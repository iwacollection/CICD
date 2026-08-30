# RK 真实物理接入手册

## 1. 最终拓扑

```text
Private RK Product Repository
        |
        | pinned reusable workflow
        v
Public CICD policy repository
        |
        | caller-owned runner scope
        v
Private Self-hosted RK Build Host (Linux x86_64)
        |
        +-- RK_SDK_ROOT -> real Rockchip SDK/BSP
        |
        +-- local HIL broker :8765
        |       |
        |       +-- exclusive lease
        |       v
        |    RK physical board
        |       +-- stable serial device (/dev/serial/by-id/...)
        |
        +-- cross compile target -> Linux arm64 firmware
```

**重要：目标架构和 Runner 架构是两件事。**

- Runner build host: Linux x86_64 (`x64` label)
- firmware target: Linux arm64

不要因为目标是 arm64 就把编译 Runner 建成 ARM64 主机，除非选定的 RK SDK 明确支持 ARM64 host。

## 2. 为什么物理 Runner 不能挂到公开 CICD 仓库

中央 `iwacollection/CICD` 是公开仓库。真实 Self-hosted Runner 拥有：

- RK SDK/BSP；
- 内网/USB/串口访问；
- HIL 真机；
- Runner 主机文件系统。

因此真实 Runner 不应进入公开 fork/PR 的授权域。

正确做法：

1. 创建/选择一个 **private RK product repository**；
2. Runner 注册到该 private repository（或只允许 private repos 的组织 Runner Group）；
3. private repo 通过 **固定 40 位 CICD commit SHA** 调用中央 reusable workflow；
4. 中央 CICD 仓库不持有物理 Runner。

## 3. 物理构建主机基线

当前 bootstrap 明确支持：

- Debian/Ubuntu Linux；
- x86_64；
- systemd；
- 真实 USB controller；
- 可访问 GitHub；
- 能访问/挂载 RK SDK；
- 建议使用独立机器或可重置 VM + USB passthrough，不与日常办公环境共用。

中央 RK profile 要求：

```text
self-hosted
linux
x64
soc-rk
```

基础工具：

```text
bash
python3
git
ccache
lsusb
```

## 4. 安装真实 RK SDK/BSP

SDK 不进入 Git。

示例：

```bash
sudo install -d -o gh-rk-runner -g gh-rk-runner /opt/rk-sdk
# 将经过审批的 Rockchip SDK/BSP 解压/挂载到 /opt/rk-sdk/<version>
export RK_SDK_ROOT=/opt/rk-sdk/<version>
```

需要记录两个不可变输入：

- `source_digest`: 原始 SDK/BSP 包 SHA256；
- `patchset_digest`: 企业补丁集/厂商补丁包 SHA256。

然后使用中央脚本创建 identity：

```bash
python3 scripts/ci/sdk_identity.py create \
  --sdk-root "$RK_SDK_ROOT" \
  --sdk-id '<real-rk-sdk-id>' \
  --version '<real-version>' \
  --source-digest 'sha256:<64hex>' \
  --patchset-digest 'sha256:<64hex>'
```

最终文件：

```text
$RK_SDK_ROOT/.ci/sdk-identity.json
```

## 5. 接入真实 RK 板卡

优先使用稳定 udev 名称，不使用 `/dev/ttyUSB0` 这种会漂移的编号。

检查：

```bash
lsusb
ls -l /dev/serial/by-id/
```

把真实设备路径写进主机本地 inventory：

```json
{
  "schema_version": 1,
  "pools": {
    "rk-linux-arm64": [
      {
        "id": "rk-board-01",
        "device_path": "/dev/serial/by-id/<real-device>",
        "env": {
          "RK_HIL_BOARD": "rk-board-01",
          "RK_HIL_SERIAL_BAUD": "1500000"
        }
      }
    ]
  }
}
```

该 inventory 属于 Lab 主机配置，不包含在产品制品中。

## 6. 安装单机 HIL Broker

生成随机 token（示例）：

```bash
export CI_RESOURCE_BROKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

准备真实 inventory 后执行：

```bash
sudo -E INVENTORY_SOURCE=/path/to/rk-hil-inventory.json \
  bash ops/rk-runner/install-hil-broker.sh
```

检查：

```bash
systemctl status rk-hil-broker.service --no-pager
curl -fsS http://127.0.0.1:8765/healthz
```

Broker 行为：

```text
POST /v1/leases
 -> pool=rk-linux-arm64
 -> 独占一块 ready board
 -> 返回 CI_HIL_DEVICE_ID / CI_HIL_DEVICE_PATH

DELETE /v1/leases/<id>
 -> 释放板卡
```

同一块板不能被两个 Job 同时租用。

## 7. 注册 GitHub Runner

**必须使用 private RK product repository 的 registration token。**

准备变量：

```bash
export GITHUB_RUNNER_REPOSITORY_URL='https://github.com/<owner>/<PRIVATE-RK-PRODUCT-REPO>'
export GITHUB_RUNNER_TOKEN='<short-lived-registration-token>'
export RK_SDK_ROOT='/opt/rk-sdk/<version>'
export CI_RESOURCE_BROKER_URL='http://127.0.0.1:8765'
export CI_RESOURCE_BROKER_TOKEN='<same-host-broker-token>'
```

执行：

```bash
sudo -E bash ops/rk-runner/bootstrap-runner.sh
```

脚本固定：

```text
actions/runner v2.337.0
linux-x64 archive SHA256:
70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613
```

下载后必须 `sha256sum --check --strict` 成功才会注册。

## 8. 私有产品仓库 Enrollment

私有产品仓库添加一个调用 workflow，固定中央 CICD 的 merged commit SHA：

```yaml
jobs:
  rk-sdk-enrollment:
    uses: iwacollection/CICD/.github/workflows/reusable-rk-enrollment.yml@<40-char-platform-sha>
    with:
      platform_ref: <same-40-char-platform-sha>
```

只能从 caller `main` 执行。

成功后得到：

```text
sdk_identity=sha256:...
```

把完全相同的 SHA256 通过 CICD PR 写入：

```text
ci/hardware-profiles.json
  rk-linux-arm64-lab.sdk.expected_sha256

ci/toolchains.json
  rk-sdk-2026.08.host_identity
```

## 9. Physical Readiness

identity 固定后，私有产品仓库调用：

```yaml
jobs:
  rk-readiness:
    uses: iwacollection/CICD/.github/workflows/reusable-rk-physical-readiness.yml@<40-char-platform-sha>
    with:
      platform_ref: <same-40-char-platform-sha>
```

Readiness 会真实验证：

```text
Runner host = x86_64
required tools present
RK_SDK_ROOT exists
sdk-identity.json valid
actual SDK identity == pinned identity (pin 后)
HIL broker reachable
real rk-linux-arm64 board can be leased
returned /dev device exists
lease can be released
```

任何一项失败均不能激活 RK target。

## 10. 产品自己的两段脚本

私有产品仓库只负责产品知识：

```text
ci/vendor-rk-build.sh
ci/vendor-rk-hil-test.sh
ci/pr-validate.sh
```

中央平台不猜测具体 BSP board/product 名。

`vendor-rk-build.sh` 应：

```text
source/init RK SDK environment
select exact board/product config
build bootloader/kernel/rootfs/application
copy only declared release artifacts to output path
```

`vendor-rk-hil-test.sh` 应使用：

```text
CI_HIL_DEVICE_ID
CI_HIL_DEVICE_PATH
RK_HIL_BOARD
```

完成产品自己的：

```text
flash/reset
boot wait
serial health check
version/digest check
smoke test
```

## 11. 激活顺序

必须严格按顺序：

```text
private product repo exists
 -> x64 physical build host
 -> real RK SDK installed
 -> canonical SDK identity
 -> local HIL broker
 -> real board visible by stable device path
 -> physical Runner registered only to private repo
 -> reusable enrollment success
 -> pin SDK identity in central catalog
 -> RK profile/toolchain active
 -> reusable physical readiness success
 -> product vendor build/HIL scripts ready
 -> enable RK target / reusable RK build
 -> first real firmware CI
```

## 12. 什么才叫“物理接入完成”

不是 Runner 显示 Online 就算完成。

最终必须保存一次真实 CI 证据：

```text
Private repo main
 -> x64 soc-rk Runner
 -> pinned RK SDK identity
 -> real vendor build
 -> firmware output
 -> exclusive HIL lease
 -> real board flash/reset/boot
 -> smoke test success
 -> Artifact Contract v2
 -> SBOM/security evidence
 -> Build gate success
```

在这条链真实出现之前，中央 catalog 中 RK target 应继续保持 disabled。
