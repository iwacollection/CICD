# Toolchain Registry（工具链登记）

这里记录“逻辑工具链版本”和实际 Runner / Container Image 之间的对应关系。

生产原则已经调整为：**Container First，Host Exception**。

也就是说，能封装进镜像的 gcc / clang / CMake / JDK / Android SDK / NDK / Python / Node / Go / Rust / 厂商用户态 SDK，优先进入版本化工具链镜像；只有内核、驱动、USB 烧录、特殊硬件、许可证等确实依赖宿主机的能力保留在专用 Runner。

## 原则

项目只能引用明确版本，例如：

```text
gcc-host-container-v1
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

禁止项目引用：

```text
latest
current
new
final
```

生产项目最好最终解析到不可变 digest：

```text
ghcr.io/company/gcc-build@sha256:...
registry.company/ci/rk-build@sha256:...
registry.company/ci/qcom-build@sha256:...
registry.company/ci/mtk-build@sha256:...
```

标签方便人理解，digest 用于机器保证内容不变。

## 建议登记字段

生产中可以把下面信息维护到 CMDB / 配置中心：

```text
toolchain_name
vendor
sdk_version
compiler_version
base_os
execution_mode
container_registry
container_image_tag
container_image_digest
container_dockerfile_commit
runner_labels
runner_image_id
sdk_checksum
license_mode
required_host_drivers
required_devices
supported_soc
supported_target_os
supported_arch
owner
created_at
retire_at
```

## 当前示例

| 逻辑名称 | 执行方式 | 用途 | Runner 标签 | 镜像/备注 |
| --- | --- | --- | --- | --- |
| gcc-host-container-v1 | container | 普通 Linux C/C++ | ubuntu-latest | `docker/toolchains/gcc-host/Dockerfile`，当前仓库真实基线 |
| rk-sdk-2026.08 | container + 专用 Runner | RK Linux/Android | self-hosted, soc-rk | 需企业 SDK，生产应登记真实 digest |
| qcom-sdk-2026.08 | container + 专用 Runner | Qualcomm Android | self-hosted, soc-qualcomm | 需企业 SDK/授权，生产应登记真实 digest |
| mtk-sdk-2026.08 | container + 专用 Runner | MediaTek Android | self-hosted, soc-mediatek | 需企业 SDK/授权，生产应登记真实 digest |

## 镜像生命周期

```text
Dockerfile / SDK 定义变化
        |
        v
Toolchain Image CI
        |
        +-> Build
        +-> Smoke Test
        +-> 安全扫描（生产接入）
        +-> SBOM（生产接入）
        |
        v
镜像仓库
        |
        v
不可变 digest
        |
        v
Toolchain Registry 登记
        |
        v
代表项目双跑验证
        |
        v
项目逐步切换
```

仓库中的 `.github/workflows/toolchain-images.yml` 默认只做镜像 Build + Smoke Test；只有手工触发并明确 `publish=true` 才会发布 GHCR，避免普通代码变更意外创建工具链版本。

## SDK / 编译器升级流程

```text
新 SDK
 -> 建新逻辑版本
 -> 建新工具链镜像
 -> 镜像自测
 -> 记录 digest
 -> 选几个项目双跑对比
 -> 正式开放
 -> 项目逐步切换
 -> 旧版本进入维护期
 -> 到期下线
```

不要原地覆盖旧 SDK，也不要让 `latest` 成为生产依赖。否则历史 commit 即使源码没变，也无法复现当年的构建结果。

## Runner 和镜像的边界

```text
应该进镜像：
- 编译器
- CMake / Ninja / ccache
- JDK / Gradle
- Android SDK / NDK
- Python / Node / Go / Rust
- 用户态系统库
- 厂商可容器化 SDK

应该留在 Runner：
- Linux Kernel
- Docker Engine
- GPU / USB / PCIe 驱动
- 真机烧录器
- 串口设备
- HSM / 特殊硬件访问
- 无法容器化的许可证能力
```

详细设计见 `docs/containerized-build-environments.md`。
