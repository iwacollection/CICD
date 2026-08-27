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
