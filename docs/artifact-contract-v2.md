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

平台会记录每个 lock 文件的 digest。声明了 pattern 但实际匹配不到文件时，打包直接失败，避免 Manifest 假装记录了不存在的依赖基线。

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
