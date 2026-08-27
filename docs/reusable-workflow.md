# 业务仓库如何调用中央 CI

对于独立业务仓库，推荐使用 `.github/workflows/reusable-build.yml`，不用把业务源码搬到 CICD 仓库。

## 1. 业务仓库最小调用示例

在业务仓库创建：

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  build:
    uses: iwacollection/CICD/.github/workflows/reusable-build.yml@main
    with:
      project_name: my-cpp-service
      working_directory: .
      build_command: cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 4
      test_command: ./build/my-cpp-service --self-test
      artifact_paths_json: '["build/my-cpp-service"]'
      soc: generic
      target_os: linux
      arch: x86_64
      toolchain: gcc-14
      runner_labels_json: '["ubuntu-latest"]'
```

## 2. 生产不要长期引用 `@main`

学习阶段可以先用：

```text
@main
```

生产应该使用：

```text
@v1
```

或者更严格：

```text
@<full commit sha>
```

这样中央 CI 平台升级不会在没有审批的情况下突然改变所有业务项目。

## 3. RK 项目示例

```yaml
jobs:
  rk-build:
    uses: iwacollection/CICD/.github/workflows/reusable-build.yml@v1
    with:
      project_name: camera-firmware
      working_directory: .
      build_command: ./ci/build.sh rk linux arm64
      test_command: ./ci/test-package.sh out/rk
      artifact_paths_json: '["out/rk/**/*.img","out/rk/**/*.bin"]'
      soc: rk
      target_os: linux
      arch: arm64
      toolchain: rk-sdk-2026.08
      runner_labels_json: '["self-hosted","linux","arm64","soc-rk"]'
      cache_paths: |
        source/.cache/ccache
        source/.cache/vendor
```

## 4. 高通与 MTK

只改 target 与 Runner 能力，不复制平台逻辑：

```text
Qualcomm
soc             = qualcomm
target_os       = android
arch            = arm64
toolchain       = qcom-sdk-2026.08
runner labels   = self-hosted, linux, arm64, soc-qualcomm

MediaTek
soc             = mediatek
target_os       = android
arch            = arm64
toolchain       = mtk-sdk-2026.08
runner labels   = self-hosted, linux, arm64, soc-mediatek
```

## 5. 业务仓库负责什么

业务仓库负责：

- 自己的源码
- 自己的 build/test 脚本
- 自己的依赖 lock 文件
- 声明最终制品路径
- 声明需要什么工具链/Runner

中央平台负责：

- Runner 选择
- 缓存命名规范
- 统一执行
- 制品打包
- manifest
- SHA256
- Artifact 上传
- 晋级规范
- 安全基线

## 6. 为什么 build command 还放在业务仓库

中央平台不应该知道每个项目内部到底执行 Maven、CMake、Gradle 还是某家厂商脚本。

更合理的边界是：

```text
平台规定阶段和输入输出
项目实现自己的构建细节
```

例如固件项目统一提供：

```bash
./ci/build.sh <soc> <os> <arch>
```

中央 CI 不进入厂商脚本内部继续写几百行 if/else。

## 7. 私有业务仓库

Reusable Workflow 运行在调用方上下文，第一次 `checkout` 会检出调用它的业务仓库。

中央 CICD 仓库如果改为私有仓库，需要在 GitHub Actions 共享策略中允许其他目标仓库调用它。

## 8. Secrets

不要把生产密钥作为通用构建参数传来传去。

普通编译阶段尽量只用只读依赖凭据；签名、生产发布使用单独 workflow / Environment / KMS/HSM。

## 9. 如何升级中央流水线

推荐：

```text
CICD main
 -> 平台测试
 -> 发布 v1.1
 -> 选一个非核心业务升级
 -> 验证
 -> 批量 PR 升级其他业务引用
```

不要修改 `main` 后让所有生产仓库在下一秒同时自动吃到新逻辑。
