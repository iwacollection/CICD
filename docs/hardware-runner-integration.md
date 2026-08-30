# 多 SoC 硬件执行面：Runner / SDK / License / HIL

这份文档解释 **RK（瑞芯微）/ Qualcomm（高通）/ MediaTek（联发科、MTK）如何从普通 DAG 节点进入受控的硬件执行面**。

如果要先理解整体关系，先读：

- [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)

本篇重点只讲执行面：Runner、SDK 身份、License、HIL 真机和 Vendor Adapter。

---

## 1. 为什么需要独立“硬件执行面”

普通应用构建通常可以：

```text
GitHub Hosted Runner
      ↓
Container Toolchain
      ↓
Build / Test
```

厂商固件构建可能额外依赖：

```text
几十 GB 的 SDK/BSP
固定 Host OS / 目录结构
USB / Serial
ADB / Fastboot
厂商 License
特殊驱动
板卡 / 电源控制器
内网资源
```

所以不能把所有目标都强行塞进 Hosted Runner 或普通 Docker。

中央 DAG 不需要改成第二套。节点只是在执行前解析：

```text
普通 Container Toolchain
        或
Hardware Profile
```

---

## 2. 信任边界

最重要的安全规则：

> **Pull Request 永远不能因为目标是 RK / 高通 / MTK，就直接运行厂商代码到高权限 Self-hosted Runner。**

PR 路径：

```text
Pull Request
     ↓
target 包含 self-hosted
     ↓
强制 GitHub Hosted Runner
     ↓
只执行 pr_validation_command
```

PR 阶段不能访问：

- Vendor SDK；
- License Server；
- HIL 真机；
- USB/串口；
- 企业内网高权限资源；
- 生产签名材料。

完整硬件构建只允许受信 `main` 进入。

---

## 3. 中央 ownership：谁管什么

### `ci/projects.json`

负责产品视角：

- 项目依赖；
- SoC / OS / arch target；
- Toolchain；
- Artifact path；
- Build/Test command；
- Hosted-safe PR validation。

### `ci/toolchains.json`

负责工具链/SDK 视角：

- Toolchain ID；
- 生命周期；
- `execution_mode`；
- `hardware_profile`；
- 激活后的不可变 `host_identity`。

### `ci/hardware-profiles.json`

负责执行资源视角：

- Runner labels；
- SDK root env；
- SDK identity file；
- required tools；
- License Pool；
- HIL Pool；
- Vendor Build/HIL Adapter。

### `ci/hardware-rollout.json`

负责上线节奏：

- 哪个 SoC 当前允许激活；
- 最大 active profile 数；
- 当前 rollout phase。

项目本身不能覆盖 Hardware Profile；Toolchain → Hardware Profile 是中央唯一绑定来源。

---

## 4. 当前三家 Hardware Profile

| SoC | Profile | Target | Runner labels | License | HIL | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| RK / Rockchip | `rk-linux-arm64-lab` | Linux arm64 | `self-hosted, linux, x64, soc-rk` | 当前非必需 | `rk-linux-arm64` | `planned` |
| Qualcomm | `qcom-android-arm64-lab` | Android arm64 | `self-hosted, linux, arm64, soc-qualcomm` | `qcom-build` | `qcom-android-arm64` | `planned` |
| MediaTek | `mtk-android-arm64-lab` | Android arm64 | `self-hosted, linux, arm64, soc-mediatek` | `mtk-build` | `mtk-android-arm64` | `planned` |

注意：Qualcomm/MTK 的 Runner 架构目前只是 Catalog 中的 planned 值，真实 SDK 接入前必须按厂商 Host 支持矩阵重新确认。

RK 已经过专门纠正：

```text
Runner Host = Linux x86_64
Target      = Linux arm64 firmware
```

目标架构不能反推构建主机架构。

---

## 5. SDK Identity：为什么“Runner 上装了 SDK”还不够

错误认知：

```text
Runner 标签 = soc-rk
所以 SDK 一定正确
```

这是不成立的。

同一台机器可能被手工升级、补丁漂移、SDK 被替换。

因此每个厂商 SDK 根目录必须存在：

```text
.ci/sdk-identity.json
```

对应环境变量：

```text
RK      -> RK_SDK_ROOT
Qcom    -> QCOM_SDK_ROOT
MTK     -> MTK_SDK_ROOT
```

Identity 应描述：

- vendor / sdk_id；
- version；
- 原始 SDK/BSP package digest；
- 企业/厂商 patchset digest。

平台计算：

```text
sha256(.ci/sdk-identity.json)
```

并要求：

```text
ci/hardware-profiles.json
sdk.expected_sha256
        ==
ci/toolchains.json
host_identity
        ==
Runner 实际 SDK identity
```

只有三者一致，才允许完整构建。

SDK 内容本身不提交到 Git，只提交 identity digest。

---

## 6. License Pool

当前模型：

```text
RK         -> license.required = false
Qualcomm   -> license.required = true
MediaTek   -> license.required = true
```

Runner 服务在 Git 外部提供：

```text
CI_RESOURCE_BROKER_URL
CI_RESOURCE_BROKER_TOKEN
```

申请流程：

```text
Build Job
   ↓
POST /v1/leases
kind=license
pool=qcom-build / mtk-build
   ↓
Broker 返回 lease_id + 临时 env
   ↓
Vendor Build
   ↓
finally
   ↓
DELETE /v1/leases/<lease_id>
```

License 地址和 token：

- 不写入 Git；
- 不打印；
- 不进入 Artifact；
- 只注入实际 Vendor Build 进程。

如果 License lease 或释放失败，任务 fail closed。

---

## 7. HIL 真机租约

HIL（Hardware-in-the-Loop，硬件在环）解决的是：

> 编译成功以后，固件在真实板子上到底能不能烧、能不能启动、能不能工作。

HIL 也通过 Resource Broker 管理：

```text
POST /v1/leases
kind=hil
pool=<hardware profile configured pool>
```

至少返回：

```text
CI_HIL_DEVICE_ID
```

可以同时返回：

- stable serial path；
- USB serial number；
- ADB serial；
- Lab endpoint；
- Power controller；
- board revision。

同一个 HIL Device 在 lease 有效期间不能被第二个 Job 同时占用。

---

## 8. Vendor Adapter 和产品 Recipe

中央平台拥有稳定 Adapter：

```text
scripts/vendor/rk/build.sh
scripts/vendor/rk/hil-test.sh
scripts/vendor/qcom/build.sh
scripts/vendor/qcom/hil-test.sh
scripts/vendor/mtk/build.sh
scripts/vendor/mtk/hil-test.sh
```

它们负责统一：

- 环境变量契约；
- License/HIL lease；
- 失败语义；
- 日志；
- Artifact 路径交接。

实际产品仓库拥有：

```text
ci/vendor-rk-build.sh
ci/vendor-rk-hil-test.sh
ci/vendor-qcom-build.sh
ci/vendor-qcom-hil-test.sh
ci/vendor-mtk-build.sh
ci/vendor-mtk-hil-test.sh
```

产品 Recipe 才知道：

- 具体 BSP board/product；
- `lunch`/make target；
- kernel/bootloader/rootfs 构建细节；
- flash 分区；
- board-specific smoke test。

因此中央 CI 不需要变成几百行 `if soc == ...`。

---

## 9. 完整硬件 Job 生命周期

```text
Trusted main
   ↓
Resolve Toolchain
   ↓
Resolve Hardware Profile
   ↓
Self-hosted Runner
   ↓
Host/required tools preflight
   ↓
SDK identity verify
   ↓
License lease（如 required）
   ↓
Vendor Build Adapter
   ↓
Firmware bytes
   ↓
HIL lease
   ↓
Vendor HIL Adapter
   ↓
flash / boot / smoke
   ↓
release License/HIL
   ↓
Artifact Contract v2
   ↓
Supply-chain evidence
   ↓
Archive / Promotion
```

任何资源申请、SDK 校验、设备释放失败都不能降级成绿色。

---

## 10. 激活一个 SoC 的顺序

每个 SoC 独立激活，不能“三家一起开”。

严格顺序：

1. 准备真实 Self-hosted Runner；
2. 按 Hardware Profile 设置能力标签；
3. 安装真实 Vendor SDK/BSP；
4. 生成 `.ci/sdk-identity.json`；
5. 获取并固定 SDK SHA256；
6. 安装 required tools；
7. 配置 Resource Broker；
8. 注册 License Pool（需要时）；
9. 注册真实 HIL Device；
10. 产品仓库提供 Vendor build/HIL recipe；
11. profile/toolchain 从 `planned` 改为 `active`；
12. Readiness 全绿；
13. 最后才 enable target。

这个顺序防止：

- Runner Online 但 SDK 错；
- SDK 有但 License 不可用；
- 编译通过但没有板；
- 板存在但无法释放租约；
- 配置写完就被当作生产 Ready。

---

## 11. 为什么物理 Runner 不放在公开 CICD 仓库

真实硬件 Runner 可能拥有：

- 商业 SDK；
- USB/串口；
- HIL 板；
- 内网；
- License；
- 主机文件系统。

因此推荐：

```text
Private Product Repository
       ↓ pinned reusable workflow SHA
Public CICD Platform Repository
       ↓ policy / reusable workflow
Private Product-owned Self-hosted Runner
```

真实 Runner 注册到私有产品仓库或受控组织 Runner Group，而不是公开 CICD 仓库。

详见：

- [RK 真实物理接入手册](rk-physical-bringup.md)

---

## 12. 当前状态

当前代码已经具备：

- 三家 Hardware Profile；
- Toolchain → Hardware Profile 绑定；
- SDK identity contract；
- License lease；
- HIL lease；
- Vendor Adapter；
- PR / Self-hosted 信任边界；
- RK enrollment/readiness/bootstrap；
- fail-closed Gate。

但真实状态仍是：

```text
RK          planned
Qualcomm    planned
MediaTek    planned
```

原因是目前没有真实 Runner / Vendor SDK / License Server / HIL 设备证据。

所以正确描述：

```text
硬件 CI 执行契约     ✅
RK 平台执行面        ✅ Ready
RK 物理真机闭环      ⏸
Qcom/MTK 物理闭环    ⏸
```

平台不会把“代码已经支持”冒充“真机已经验证”。
