# 构建、缓存与依赖仓库设计

这部分专门回答几个生产里最容易出问题的问题：构建为什么一小时、缓存怎么共享、多个 Job 同时拉依赖会不会冲突、20 个内部库怎么排顺序。

## 1. 先把三种东西分开

很多 CI 事故来自把“缓存”和“制品仓库”混为一谈。

| 类型 | 作用 | 能不能作为发布依据 | 典型实现 |
| --- | --- | --- | --- |
| 依赖代理仓库 | 保存第三方/内部依赖包 | 可以，前提是版本不可变 | Nexus / Artifactory |
| 编译缓存 | 避免重复编译同样的源文件 | 不可以 | ccache / sccache / Bazel Remote Cache |
| 最终制品仓库 | 保存真正要测试和发布的包 | 必须可以 | Artifactory / Nexus / S3 / MinIO / ACR / GHCR |

一句话：**缓存丢了只能变慢，不能导致构建结果变错。**

## 2. 为什么多个 Job 不能共写一个裸目录

错误做法：

```text
/mnt/ci-cache/npm
/mnt/ci-cache/maven
/mnt/ci-cache/ccache
```

所有 Runner 都直接读写同一个目录。

风险包括：

- 一个 Job 正在写，另一个 Job 读到半包。
- 同名版本实际内容不同，旧文件覆盖新文件。
- 清理任务把正在使用的文件删掉。
- SDK/架构不同却命中同一个缓存。
- NFS 锁、inode、元数据性能成为瓶颈。

生产里优先使用有并发语义的缓存系统或代理仓库，不要自己拿共享目录冒充缓存服务。

## 3. 本平台的缓存键

缓存至少按以下维度隔离：

```text
project
+ soc
+ target_os
+ arch
+ toolchain
+ dependency lock hash
```

例如：

```text
hello-cpp-rk-linux-arm64-rk-sdk-2026.08-<lock hash>
```

因此 RK 与高通、Linux 与 Android、旧 SDK 与新 SDK 不会互相污染。

## 4. 50 个第三方库怎么处理

不要每次去公网重新下载。

推荐链路：

```text
构建任务
  |
  v
企业依赖代理（Nexus / Artifactory）
  |
  +-- Maven Central
  +-- npm registry
  +-- PyPI
  +-- Go proxy
  +-- Conan / vcpkg 源
  +-- 厂商 SDK 归档
```

好处：

1. 外网抖动不直接打断构建。
2. 同一个包只从公网下载一次。
3. 能做恶意包、许可证、版本白名单。
4. 能保留已经下架的历史依赖，方便旧版本重构建。
5. 防止 dependency confusion（依赖混淆）。

## 5. C/C++ 项目怎么加速

### 第一层：依赖预编译

稳定的第三方库不要每次源码重编译。通过 Conan/vcpkg/内部包仓库发布按平台区分的二进制包。

### 第二层：ccache / sccache

缓存单个编译单元。源码、编译参数、编译器相同就可以复用对象文件。

### 第三层：拆分内部库

不要让应用每次把 20 个内部库全部重新编译。每个内部库独立版本化发布，应用通过 lock 文件引用确定版本。

### 第四层：只构建变化范围

单仓库项目可以根据 Git diff 判断受影响模块。多仓库项目则由上游库新版本事件触发真正需要的下游构建。

### 第五层：远程构建缓存

构建规模足够大时使用 sccache remote、Bazel Remote Cache 或类似对象缓存，让不同 Runner 复用结果。

## 6. 20 个内部库怎么排编译顺序

例如：

```text
base-a ----> codec -----+
                       +--> app
base-b ----> storage ---+
```

平台把它转成：

```text
第 0 层：base-a, base-b      # 并行
第 1 层：codec, storage      # 等自己的依赖
第 2 层：app                 # 等 codec + storage
```

`scripts/ci/dependency_plan.py` 会检查依赖环，并输出并行层级。

如果出现：

```text
a -> b -> c -> a
```

则直接失败，因为这是无法正确排序的循环依赖。

## 7. 两个 Job 同时构建同一版本怎么办

分两层控制：

### 构建层

Pull Request 的旧提交可以被新提交取消，避免浪费 Runner。

### 发布层

最终制品必须使用唯一键：

```text
source SHA + project + target + toolchain
```

相同唯一键如果已经发布成功，后来的任务应该校验 digest 后复用，而不是覆盖。

## 8. 构建一小时怎么处理

不要第一反应“换更大的机器”。先测时间花在哪里。

```text
Checkout
Dependency Download
Configure
Compile
Link
Unit Test
Package
Upload
```

常见治理顺序：

1. 看阶段耗时，找最大头。
2. 外部依赖走企业代理。
3. C/C++ 接 ccache/sccache。
4. 稳定内部库二进制化。
5. 拆 DAG，让独立模块并行。
6. 只构建变化范围。
7. 大 SDK 做版本化 Runner 镜像/磁盘快照，不要每次安装。
8. 仍然慢，再增加 CPU/RAM/IO 或远程编译。

## 9. 缓存命中率也要监控

至少关注：

- cache hit rate
- dependency download time
- compile time
- queue time
- runner utilization
- artifact upload time
- rebuild rate

如果构建突然从 20 分钟变成 60 分钟，第一时间应该能看出是缓存失效、依赖源变慢、Runner 排队还是编译本身变慢。


---

## 10. 缓存的实际运行与故障处理

缓存是可丢弃的加速层，不是构建输入的权威来源。任何缓存异常都应该允许安全地退回“重新下载/重新编译”，而不是降低校验标准。

### 10.1 一次构建如何使用缓存

以当前 DAG Node 为例，执行顺序是：

1. 先下载本次 Workflow Run 中声明的上游制品；
2. 校验上游制品的源码 SHA、仓库身份和 digest；
3. 根据项目配置中的 `cache_key_files` 计算缓存键 fingerprint（不会自动读取 `dependency_lock_files`）；
4. 将项目、SoC、系统、架构、工具链 identity、上游 fingerprint 和锁文件 fingerprint 组成 cache key；
5. 恢复缓存后执行构建；
6. 构建成功后生成独立 Artifact Contract v2，不能把缓存目录当成制品上传。

对应实现可在 `.github/workflows/dag-node.yml` 的 `Verify and stage declared upstream artifacts`、`Compute declared cache fingerprint`、`Restore build cache` 和 `Package immutable Artifact Contract v2` 步骤中逐项对照。

### 10.2 锁文件与缓存键不是同一个配置

`ci/projects.json` 中有两个容易混淆的字段：

- `dependency_lock_files`：告诉打包器哪些锁文件需要写入 Artifact Contract v2 的 `Manifest.dependencies.locks`，用于记录依赖基线；
- `cache_key_files`：告诉 `cache_fingerprint.py` 哪些文件内容变化时必须改变缓存 fingerprint。

工作流 `.github/workflows/dag-node.yml` 的 `Compute declared cache fingerprint` 步骤实际读取的是 `cache_key_files_json`，并不会自动把 `dependency_lock_files` 合并进去。因此，**如果锁文件变化必须触发缓存失效，就必须把该锁文件也显式列入同一 target 的 `cache_key_files`**。只列入 `dependency_lock_files` 只能保证 Manifest 记录它，不能保证 cache key 随它变化。

当前示例配置的实际情况：

| 项目/目标 | dependency_lock_files | cache_key_files | 当前含义 |
|---|---|---|---|
| `hello-lib` | 空数组 | `CMakeLists.txt`、头文件、源文件 | 以源代码/构建配置变化作为缓存输入；该示例没有依赖锁文件 |
| `hello-cpp` | 空数组 | `CMakeLists.txt`、`src/main.cpp` | 以源代码/构建配置变化作为缓存输入；该示例没有依赖锁文件 |
| RK/Qualcomm/MediaTek 模板 | `deps.lock`、`toolchain.lock` | 同样的两个 lock 文件 | 锁文件变化会改变 fingerprint；这些硬件 target 当前是 disabled 模板 |

新增真实项目时，建议把依赖锁文件、工具链描述文件和影响编译结果的构建配置都列入 `cache_key_files`。如果使用 glob pattern，fingerprint 工具会逐条检查 pattern；任意一条 pattern 没有匹配文件就失败，避免悄悄生成不完整的 key。

### 10.3 缓存命中不等于构建可信

当前 GitHub Actions 缓存配置使用精确 key，并提供同一项目/目标/工具链/上游 fingerprint 前缀的 restore key。restore key 可能恢复一个较旧的缓存，因此必须满足两个条件：

- 依赖下载和编译系统本身仍要校验锁文件、包摘要和编译输入；
- 最终输出仍要重新打包并执行 Artifact Contract v2 校验。

不要把“cache hit”写进制品 Manifest 当成可信证明，也不要因为缓存命中就跳过测试。

### 10.4 常见故障：证据、止血、恢复

| 故障 | 证据 | 止血 | 恢复与验证 |
|---|---|---|---|
| 缓存服务不可用/读取超时 | Actions 日志中的 cache restore/save 错误 | 允许本次构建冷启动；必要时关闭缓存步骤，不要改用不受控共享目录 | 无缓存完成 build/test/package，比较产物身份 |
| 缓存命中但编译结果异常 | 同一 commit 的有缓存/无缓存构建结果不同 | 暂停该项目缓存写入和发布 | 清空该项目对应缓存后冷构建；确认差异来源，重复构建 digest |
| 多 Runner 结果不一致 | cache key、Runner OS/arch、工具链 identity、lock hash | 隔离不匹配的 Runner/工具链缓存 | 修正 key 维度，分别冷构建并核对 Manifest |
| 缓存突然全部 miss | fingerprint 输出、锁文件 diff、工具链升级记录 | 不要为追求命中率回退工具链或锁文件 | 确认 key 变化是预期还是错误，再观察冷构建耗时 |
| 磁盘空间耗尽 | Runner 磁盘使用、inode、缓存目录大小 | 停止新任务进入故障 Runner；只清理可重建缓存 | 释放空间后检查文件系统，再跑一个冷构建 |
| 依赖代理不可用 | 代理仓库健康、上游源连接、HTTP 状态、DNS/TLS | 暂停依赖版本变更和生产晋级 | 恢复代理后按锁文件拉取，核对包摘要；不要随意切换公共源 |

### 10.5 安全清理顺序

发生磁盘压力时，不要直接执行 `rm -rf /mnt/ci-cache/*`，因为这可能删除正在使用的缓存或误删工具链/制品。

建议顺序：

1. 从调度层停止向故障 Runner 分配新任务；
2. 确认没有构建进程仍在使用待清理目录；
3. 先清理明确可再生成的临时目录和过期缓存；
4. 保留依赖代理仓库、最终制品仓库和审计证据；
5. 恢复 Runner 后执行一次冷构建和完整测试。

GitHub-hosted Runner 的缓存由 Actions 管理，不要假设可以登录宿主机手工清理；应通过仓库缓存管理策略或调整 key/缓存配置处理。

### 10.6 验收：证明缓存只是加速

至少做以下对照：

- 第一次冷构建：记录依赖下载、编译、测试、打包时间；
- 第二次热构建：确认缓存命中，但测试仍然执行；
- 修改一个 lock 文件：确认 fingerprint 改变，旧缓存不再作为精确命中；
- 修改一个上游库制品：确认 upstream fingerprint 改变；
- 关闭缓存：仍能得到完整且可校验的制品。

如果关闭缓存就无法构建，说明缓存已经变成隐式依赖，不能通过生产验收。
