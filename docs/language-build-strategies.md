# 多语言项目的 CI 构建策略

这套平台不是只给 C++ 用。核心治理规则一致，但不同语言的“依赖缓存”和“最终制品”要分清楚。

## 1. C / C++

常见工具：

- CMake / Ninja / Make
- Conan / vcpkg
- ccache / sccache

推荐：

```text
第三方依赖 -> Conan/vcpkg + Nexus/Artifactory
对象文件缓存 -> ccache/sccache
最终制品 -> tar/zip/rpm/deb/firmware bundle
```

缓存键要包含编译器和工具链版本。GCC 13 与 GCC 14 不应共用一个不区分版本的对象缓存。

## 2. Java / Kotlin

常见工具：

- Maven
- Gradle

缓存：

- Maven local repository download cache
- Gradle dependency/cache

生产依赖应通过 Nexus/Artifactory 代理 Maven Central 和内部 Maven Repository。

最终制品通常是：

- JAR
- WAR
- distribution tar/zip
- OCI container image

不要把整个 `~/.m2` 当最终制品保存，也不要让 SNAPSHOT 成为生产发布的唯一版本标识。

## 3. Node.js

常见工具：

- npm
- pnpm
- yarn

优先缓存“下载内容”，不要把跨平台的 `node_modules` 随便在不同 Runner 之间共享。

锁文件：

```text
package-lock.json
pnpm-lock.yaml
yarn.lock
```

这些文件变化必须进入缓存失效条件。

内部 npm 包应走企业 Registry，并使用 scope，例如：

```text
@company/common
```

避免依赖混淆。

## 4. Python

常见工具：

- pip
- uv
- Poetry

推荐锁定依赖：

```text
uv.lock
poetry.lock
requirements.txt + hashes
```

缓存 wheel/download，最终发布的是：

- wheel
- sdist
- 应用 bundle
- container image

不要把某台 Runner 的整个 `.venv` 当跨机器制品复用，尤其不同 Python 小版本、OS、CPU 架构之间可能不兼容。

## 5. Go

主要缓存：

- module download cache
- `GOCACHE` 编译缓存

关键输入：

```text
go.mod
go.sum
Go version
GOOS
GOARCH
CGO settings
```

Go 很适合做多平台 Matrix，但只要开启 CGO，就需要把 C 工具链/系统库也纳入构建环境版本。

## 6. Rust

常见：

- Cargo registry/git cache
- target 编译目录
- sccache

关键输入：

```text
Cargo.lock
rust-toolchain.toml
Target triple
Features
```

最终制品仍应脱离 `target/` 临时目录，单独打包并记录 digest。

## 7. Android 应用

常见：

- Gradle
- Android SDK
- NDK

缓存需要区分：

- JDK
- Gradle
- Android Gradle Plugin
- SDK/NDK
- ABI

最终制品：

- APK
- AAB
- mapping 文件
- native symbols

签名应与普通编译权限分离。

## 8. Android 系统 / BSP

这和普通 Android App 不是一个量级。

可能使用：

- Soong
- Make
- 厂商 BSP
- Kernel build
- Vendor build scripts

缓存和 Runner 通常更重，SDK/源码树也更大。适合版本化 Runner 镜像、磁盘快照、远程对象缓存和独立 SoC Runner Pool。

## 9. Container Image

容器镜像本身也是制品。

推荐：

```text
source commit
 -> build image once
 -> image digest sha256:...
 -> scan
 -> sign
 -> dev/staging/prod 使用同一 digest
```

生产不要只引用：

```text
my-app:latest
```

应该最终落到不可变 digest。

## 10. 不同语言共用一条平台的关键

平台不需要知道每个语言所有细节，只需要强制统一边界：

```text
输入固定
 -> Build
 -> Test
 -> Package
 -> Manifest
 -> Digest
 -> Store
 -> Promote same bytes
```

语言自己的包管理器、编译器和测试框架放在 Build/Test 阶段内部。
