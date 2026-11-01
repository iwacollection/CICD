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


---

## 11. 把“语言支持”落到一次真实构建

前面的表格告诉你选什么工具；本节说明项目接入时，流水线实际要完成什么。平台不会替项目猜测构建命令，项目必须把构建、测试和最终输出目录明确声明在项目配置中。

### 11.1 每种语言都遵守同一个外层流程

```text
检出固定 commit
  -> 解析工具链身份（版本/镜像 digest）
  -> 恢复隔离的依赖缓存
  -> 下载锁文件指定的依赖
  -> 执行项目 build 命令
  -> 执行 test 命令
  -> 检查声明的输出文件确实存在
  -> 打包为 Artifact Contract v2
  -> 校验 Manifest、文件清单和 SHA256
  -> 上传制品与安全证据
```

平台统一的是输入、证据和制品规则；语言构建工具仍由项目选择。**只执行了编译命令但没有测试、没有明确产物路径，不算完成一次可交付构建。**

### 11.2 常见语言的实际命令与关键输入

以下是可作为项目 build/test 命令起点的示例。路径和任务名必须按实际项目调整，不能把示例直接当成所有项目的固定配置。

| 类型 | Build 示例 | Test 示例 | 必须固定的关键输入 |
|---|---|---|---|
| C/C++ | `cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 4` | `ctest --test-dir build --output-on-failure` | 编译器、CMake/Ninja、系统头文件、目标三元组、依赖锁 |
| Java/Maven | `mvn -B -ntp -DskipTests package` | `mvn -B -ntp test` | JDK vendor/版本、Maven Wrapper/版本、pom、settings 来源、依赖版本 |
| Java/Gradle | `./gradlew --no-daemon assemble` | `./gradlew --no-daemon test` | JDK、Gradle Wrapper、插件版本、依赖锁、构建缓存配置 |
| Node.js | `npm ci` 后执行项目 build script | `npm test` | Node 版本、包管理器版本、lock 文件、操作系统/架构 |
| Python | `uv sync --frozen` 后执行项目打包命令 | `uv run pytest` | Python 版本、uv/Poetry 版本、锁文件、系统 wheel 兼容性 |
| Go | `go build ./...` | `go test ./...` | Go 版本、go.mod/go.sum、GOOS/GOARCH、CGO 和 C 编译器 |
| Rust | `cargo build --locked --release` | `cargo test --locked` | rust-toolchain、Cargo.lock、target triple、features |
| Android App | `./gradlew --no-daemon assembleRelease` | `./gradlew --no-daemon test` | JDK、Gradle/AGP、SDK/NDK、ABI、签名身份 |
| Android BSP | 项目提供的 Soong/Make build target | 项目提供的单元/集成/HIL 测试 | BSP commit、厂商 SDK、内核配置、设备目标、专用 Runner |

注意：Maven 示例中的 `-DskipTests package` 只用于把打包和测试阶段拆开观察；正式流水线必须在同一次受控构建中执行测试门禁，不能把跳过测试的命令作为最终成功条件。若项目需要一次命令完成，可使用 `mvn -B -ntp verify`。

### 11.3 C/C++ 示例：从源码到可校验制品

以仓库中的 `examples/cpp-app` 为例：

```bash
cmake -S examples/cpp-app -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

真实接入时还要确认应用使用的是 DAG 交接的上游库，而不是本机残留文件。当前示例通过 `CI_UPSTREAM_ROOT` 查找 `hello-lib` 的头文件和静态库；缺少上游文件时 CMake 应直接失败。

输出目录应与项目配置中的 artifact paths 一致。不要把整个工作目录、缓存目录或临时文件夹打进发布包。

### 11.4 失败时先判断哪一层出错

| 现象 | 优先证据 | 处理方式 | 恢复验证 |
|---|---|---|---|
| 依赖下载失败 | 包管理器日志、代理仓库状态、DNS/TLS | 检查依赖代理和凭据；不要临时改成公网未锁定版本 | 使用锁文件重新下载，核对依赖版本/摘要 |
| 编译器版本不一致 | 构建环境元数据、工具链镜像 digest | 停止发布，修正 Toolchain ID 或镜像引用 | Manifest 中工具链 identity 与批准版本一致 |
| 单元测试失败 | JUnit/测试日志、失败用例 | 修复代码或测试环境，不得仅删除失败测试 | 同一 commit 重新执行完整测试 |
| 找不到输出文件 | 项目 artifact paths、构建目录 | 修正 build 输出路径或制品声明 | 打包器找到预期文件，Manifest.files 包含它 |
| 不同 Runner 结果不同 | Runner OS/arch、编译器、依赖锁、环境变量 | 固定容器镜像和依赖；排除宿主机隐式依赖 | 两次独立构建比较产物 digest |
| 签名失败 | 签名任务身份、密钥服务审计、证书有效期 | 隔离签名权限，禁止把密钥写入仓库或日志 | 验证签名主体、制品 digest 和证书链 |

### 11.5 项目接入的完成定义

一个语言模板只有同时满足以下条件，才能标记为“已验证”：

1. 固定 commit 和工具链版本可被记录；
2. 依赖锁文件存在且进入 Manifest；
3. build 命令真实执行并产生声明的文件；
4. test 命令真实执行，失败会让 Job 失败；
5. Artifact Contract v2 校验通过；
6. 缓存关闭或清空后仍能完成构建；
7. 负向测试证明错误工具链、缺失产物或错误 digest 会被拒绝。

仅有配置样例、Dockerfile 或模板 workflow，只能称为“已定义/待接入”，不能称为真实项目已通过。
