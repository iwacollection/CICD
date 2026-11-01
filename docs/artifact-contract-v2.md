# Artifact Contract v2：可复现制品与可审计 Manifest

## 目标

Artifact Contract v2 解决四个生产问题：

1. 同一个项目的不同工具链不再生成同名制品。
2. 相同输入文件在相同 `SOURCE_DATE_EPOCH` 下生成相同的 `tar.gz` digest。
3. Manifest 不只记录“产出了什么”，还记录“用什么工具链、Runner、编译器和依赖锁产出”。
4. Promotion/回滚后续只围绕同一个 digest 流转，不重新编译。

## 制品身份

v1：

```text
project-soc-os-arch-sourceSha
```

v2：

```text
project-soc-os-arch-toolchain-toolchainIdentity12-sourceSha12
```

例如：

```text
hello-cpp-generic-linux-x86_64-gcc-host-container-v1-3caf311bd69a-abcdef123456
```

即使项目、SoC、系统、架构、源码 SHA 一样，只要工具链 identity 不一样，制品身份就不会碰撞。

## Manifest v2

关键字段：

```text
schema_version = 2
artifact_name
project

source
├── repository
├── commit_sha
├── workflow_run_id
├── workflow_run_attempt
└── workflow_ref

target
├── soc
├── target_os
├── arch
└── toolchain

toolchain
├── id
├── identity
├── execution_mode
└── container_image

runner
├── name
├── os
├── arch
├── environment
├── image_os
├── image_version
└── labels

compiler_versions
├── gcc / g++
├── clang / clang++
├── cmake
├── ninja / make
└── Java/Gradle（存在时）

dependencies
└── locks[]
    ├── path
    ├── sha256
    ├── size_bytes
    └── mode

bundle
├── file
├── sha256
├── size_bytes
├── format = tar.gz
├── reproducible = true
└── source_date_epoch

files[]
├── path
├── sha256
├── size_bytes
└── mode
```

## 可复现打包

普通 `tar.gz` 会把文件时间、用户名、uid/gid 等宿主机信息写进去，所以同一批文件重新压缩可能得到不同 digest。

v2 固定：

```text
文件顺序       = 按路径排序
uid/gid        = 0
uname/gname    = 空
mtime          = SOURCE_DATE_EPOCH（默认 0）
gzip mtime     = SOURCE_DATE_EPOCH
gzip filename  = 空
```

因此文件内容和权限不变时，bundle 字节可稳定复现。

注意：这保证的是“平台打包层可复现”。如果编译器本身把时间戳、随机数或绝对路径写进二进制，业务构建还需要额外设置 deterministic/reproducible build 参数。

## 依赖锁

业务目标可以声明：

```json
"dependency_lock_files": ["deps.lock", "toolchain.lock"]
```

平台会逐条展开每一个 lock pattern，并记录匹配到的每个 lock 文件 digest。**只要任意一条已声明 pattern 没有匹配到文件，打包就立即失败**；不能用“其他 pattern 匹配到了文件”来掩盖缺失的锁文件。pattern 还必须解析到 working directory 内的普通文件，不能通过绝对路径或 `..` 越界读取仓库外文件。

## Runner 与编译器版本

`collect_build_metadata.py` 会在真实构建执行环境中探测版本。容器工具链会重新进入同一个不可变 `image@sha256` 探测，因此记录的是容器内工具版本，而不是 GitHub Runner 宿主机版本。

## 验证

`verify_artifact.py` 对 v2 做以下检查：

```text
Manifest schema
  ↓
Toolchain identity / image digest 一致
  ↓
Runner labels / lock records / file records 合法
  ↓
Bundle SHA256 与 Manifest 一致
  ↓
Checksum sidecar 一致
  ↓
逐个读取 tar 成员
  ↓
成员路径、size、SHA256 与 Manifest.files 完全一致
```

同时拒绝：

- `../` 等路径穿越成员
- 非普通文件成员
- 重复路径
- Manifest 声明但 bundle 缺失的文件
- bundle 多出 Manifest 未声明的文件
- container image digest 与 toolchain identity 不一致

## 兼容性

Verifier 暂时继续接受 v1 Manifest，原因是历史 Actions Artifact 仍可能需要被验证或晋级。

新构建统一生成 v2；后续长期制品库、环境 digest 指针、Promotion/Rollback 会以 v2 为主契约。


---

## 10. 操作者完整验收流程

Artifact Contract 的目的不是“多生成一个 JSON”，而是让接手制品的人能够回答：它来自哪个仓库和 commit、使用什么工具链、依赖了什么、包内到底有哪些文件、下载后有没有被替换。

### 10.1 构建阶段生成什么

当前 `scripts/ci/package_artifact.py` 负责生成：

- `*.tar.gz`：真正交付的 bundle；
- `*.manifest.json`：源码、工具链、Runner、依赖锁、上游制品和文件清单；
- `*.sha256`：bundle 校验摘要。

在工作流里，它由 DAG Node 或 reusable build 调用。项目必须声明真实存在的 artifact paths；声明了依赖锁 pattern 却找不到文件时，打包必须失败。

### 10.2 本地执行打包与校验

以下命令展示打包器的真实参数结构。假设项目工作目录是 `examples/cpp-app`，构建产物已放在 `build/`，请按实际编译结果调整 artifact 路径：

```bash
python3 scripts/ci/package_artifact.py \
  --project hello-cpp \
  --working-directory examples/cpp-app \
  --artifacts-json '["build/hello-ci"]' \
  --dependency-locks-json '[]' \
  --soc generic \
  --target-os linux \
  --arch x86_64 \
  --toolchain gcc-host \
  --execution-mode host \
  --runner-labels-json '["ubuntu-latest"]' \
  --output-dir dist
```

注意：当前 `examples/cpp-app/CMakeLists.txt` 的可执行目标名是 `hello-ci`，所以正确产物路径为 `build/hello-ci`，不是 `build/hello-cpp`。运行打包命令前，必须先按“多语言项目的 CI 构建策略”第 11.3 节构建并暂存 `hello-lib` 上游制品，再构建 `hello-cpp`。生产容器工具链还必须传入不可变的 `--container-image image@sha256:...` 和对应身份，不能把这个 host 示例当作生产容器构建证明。

打包后执行：

```bash
python3 scripts/ci/verify_artifact.py --directory dist
```

CI 工作流还会把打包器输出的 digest 作为 `--expected-sha256` 再校验一次，避免只相信目录内 Manifest 自己声明的摘要。

### 10.3 每次交接必须核对的身份

至少核对以下值：

| 检查项 | 应从哪里取 | 不一致意味着什么 |
|---|---|---|
| repository | Manifest.source.repository | 制品不是来自预期仓库 |
| commit SHA | Manifest.source.commit_sha | 制品不是本次提交构建 |
| workflow run ID/attempt | Manifest.source | 制品可能来自其他运行或重试 |
| target | Manifest.target | OS/架构/SoC 不匹配 |
| toolchain identity | Manifest.toolchain.identity | 工具链版本或镜像身份不同 |
| dependency locks | Manifest.dependencies.locks | 依赖基线不完整或发生变化 |
| upstream artifacts | Manifest.dependencies.upstream_artifacts | 下游引用的库版本不正确 |
| bundle digest | Manifest.bundle.sha256 + sidecar | 文件传输或存储内容被替换 |

### 10.4 校验失败时的处理

| 错误 | 可能原因 | 正确处理 |
|---|---|---|
| 找不到或找到多个 Manifest | artifact 上传不完整、目录混入多个制品 | 隔离目录，重新下载对应 Run 的完整 artifact |
| Manifest schema 错误 | 字段缺失或版本不兼容 | 修复打包器/调用参数，重新生成；不要手工补字段 |
| source SHA/repository 不匹配 | 取错分支、Run 或制品 | 丢弃候选制品，从正确 commit 重新构建 |
| bundle digest 不匹配 | 传输损坏、覆盖或篡改 | 隔离制品，重新从可信归档下载并复核 |
| bundle 成员与 files 清单不一致 | 文件在打包后被替换、Manifest 不完整 | 重新打包并校验；保留失败样本用于调查 |
| toolchain identity 不匹配 | 使用了可变 tag 或错误镜像 | 重新解析已批准的 digest，重新构建 |

任何校验失败都必须 fail closed：制品不得进入 Promotion。禁止通过删除 Manifest 字段、跳过校验或手工改 digest 来“修绿”。

### 10.5 恢复后如何证明没有拿错包

1. 从可信的 Workflow Run 或长期归档重新下载 bundle、Manifest 和 checksum；
2. 使用独立记录的预期 source SHA、repository 和 bundle digest 执行 verifier；
3. 确认 Manifest 中工具链 identity、target、依赖锁和上游 digest 与本次构建记录一致；
4. 对照制品归档记录中的 digest；
5. 再允许 Promotion 读取该制品。

本地目录自带的 checksum 只能证明 bundle 与该目录中的声明相符；如果 Manifest 和 bundle 一起被替换，必须再与可信 Workflow Run/归档记录中的预期 digest 比较。
