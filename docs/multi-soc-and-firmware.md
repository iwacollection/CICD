# 多 SoC / 固件 CI 管理主线

这份文档是 **RK（瑞芯微）/ Qualcomm（高通）/ MediaTek（联发科、MTK）固件 CI 的总入口**。

它回答的不是“某家厂商怎么敲一条编译命令”，而是：

> 当一个企业同时维护多款 SoC、多个 SDK/BSP、Android/Linux 固件、专用 Runner、许可证和真机实验室时，中央 CI 到底如何统一管理，又如何避免三家厂商互相污染。

---

## 1. 先把名词关系说清楚

```text
SoC
= System on Chip
= 芯片平台这个大类

SoC 厂商/平台：
├── Rockchip  = 瑞芯微 = RK
├── Qualcomm  = 高通
└── MediaTek  = 联发科 = MTK
```

所以 RK、高通、MTK 是多 SoC 管理下的三个厂商目标，不是三套完全独立的 CI 平台。

---

## 2. 这个平台同时服务两类构建

```text
Enterprise CI Platform
│
├── 普通应用 CI
│   ├── Linux C/C++
│   ├── 多项目依赖 DAG
│   └── Hosted / Container Toolchain
│
└── 嵌入式 / SoC 固件 CI
    ├── RK / Rockchip
    ├── Qualcomm
    ├── MediaTek / MTK
    ├── Linux BSP
    ├── Android BSP
    ├── Vendor SDK
    ├── License Pool
    └── HIL 真机实验室
```

两条业务线最终统一进入同一套：

```text
Artifact Contract v2
→ Supply-chain Policy
→ Attestation
→ Archive
→ Promotion
→ Rollback
```

因此多 SoC 不是附加 Demo，而是平台的一条一级业务主线。

---

## 3. 核心原则：治理统一，厂商执行隔离

### 统一管理

所有平台统一：

- PR / main 信任边界；
- 项目与依赖 DAG；
- Toolchain/SDK 身份；
- 构建元数据；
- Cache identity；
- Artifact Contract v2；
- SHA256；
- SBOM / 漏洞 / License / Secret / Misconfiguration；
- Attestation / Cosign；
- Archive；
- `dev -> staging -> production`；
- Rollback；
- Platform SLO / Repository Governance。

### 必须隔离

不同 SoC 必须隔离：

- 厂商 SDK/BSP；
- 编译器与 Host OS 要求；
- Android/Kernel 源码树；
- Vendor build target；
- License Server / License Pool；
- USB/串口/烧录工具；
- HIL 板卡池；
- Self-hosted Runner 能力；
- 产品自己的 build/flash/test recipe。

一句话：

```text
平台规则统一
厂商能力隔离
产品知识留在产品仓库
```

---

## 4. 中央管理模型：四层 Catalog

真正管理 RK / 高通 / MTK 的不是三份大 YAML，而是下面四层。

```text
产品项目
ci/projects.json
      │
      │ target = soc / os / arch / toolchain
      ▼
工具链
ci/toolchains.json
      │
      │ hardware_profile
      ▼
硬件能力
ci/hardware-profiles.json
      │
      ├── Runner labels
      ├── SDK identity
      ├── required tools
      ├── License Pool
      ├── HIL Pool
      └── Vendor Adapter
      │
      ▼
上线控制
ci/hardware-rollout.json
      │
      ▼
planned / active / enabled
```

### 4.1 `ci/projects.json`：业务要构建什么

项目只声明业务目标，例如：

```text
embedded-firmware-template
│
├── rk / linux / arm64
│   └── toolchain = rk-sdk-2026.08
│
├── qualcomm / android / arm64
│   └── toolchain = qcom-sdk-2026.08
│
└── mediatek / android / arm64
    └── toolchain = mtk-sdk-2026.08
```

项目不能自己指定“去 build-server-03”，也不能自己覆盖中央 Hardware Profile。

### 4.2 `ci/toolchains.json`：用什么工具链/SDK

工具链负责：

- 名称与生命周期；
- Container/Host 执行模式；
- 不可变身份；
- 对应 Hardware Profile。

厂商 SDK 不允许使用模糊路径：

```text
/opt/rk-sdk/latest        ❌
```

而使用受控逻辑身份：

```text
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

真实激活后还必须有 `host_identity=sha256:...`。

### 4.3 `ci/hardware-profiles.json`：真实执行能力

Hardware Profile 管的是“这类任务需要什么执行环境”，不是产品代码。

当前 Catalog：

| Profile | 目标 | 当前 Runner 架构 | License | HIL | 状态 |
| --- | --- | --- | --- | --- | --- |
| `rk-linux-arm64-lab` | Linux arm64 固件 | Linux x86_64 (`x64`) | 当前非必需 | 必需 | `planned` |
| `qcom-android-arm64-lab` | Android arm64 | 当前 Catalog 为 Linux arm64 | 必需 | 必需 | `planned` |
| `mtk-android-arm64-lab` | Android arm64 | 当前 Catalog 为 Linux arm64 | 必需 | 必需 | `planned` |

**重要：目标 CPU 与构建主机 CPU 不是一回事。**

RK 已明确采用：

```text
Build Host = Linux x86_64
      ↓ cross compile
Target     = Linux arm64 firmware
```

Qualcomm/MTK 目前仍处于 `planned`，Catalog 中的 Runner 架构只是当前计划值；接入真实厂商 SDK 前必须根据 SDK 官方 Host 支持矩阵重新确认，不能因为目标是 arm64 就默认构建主机也必须 arm64。

### 4.4 `ci/hardware-rollout.json`：谁现在允许上线

Catalog 中有配置，不代表允许生产执行。

当前 rollout 主线是：

```text
phase = rk-first
```

因此：

```text
RK          → 第一优先真实接入目标，但当前仍 planned
Qualcomm    → planned / 暂停激活
MediaTek    → planned / 暂停激活
```

这样可以避免“一次把三家 SDK、Runner、License、板卡全部打开”。

---

## 5. RK / 高通 / MTK 的统一执行路径

中央执行关系：

```text
Product Target
ci/projects.json
      ↓
Toolchain
ci/toolchains.json
      ↓
Hardware Profile
ci/hardware-profiles.json
      ↓
Trusted Self-hosted Runner
      ↓
SDK identity preflight
      ↓
License lease（需要时）
      ↓
Vendor build adapter
      ↓
Firmware output
      ↓
HIL device lease
      ↓
Vendor HIL adapter
      ↓
Artifact Contract v2
      ↓
Supply-chain / SBOM / Attestation
      ↓
Archive / Promotion / Rollback
```

这条主干三家共用。

不同的只是：

```text
SDK
Host requirements
License
Vendor build command
Flash mechanism
Board/HIL transport
```

---

## 6. 为什么不写 `rk-ci.yml / qcom-ci.yml / mtk-ci.yml`

如果三家各写一整套流水线，会迅速出现：

```text
RK 自己定义 Artifact
Qcom 自己定义 Cache
MTK 自己定义 Release
三家使用不同安全 Gate
三家 Runner 权限各自漂移
```

正确模型是：

```text
一个中央 DAG / Artifact / Supply-chain / Release 体系
                    +
三套受控 Hardware Profile / Vendor Adapter
```

这样新增第四种 SoC 时，不需要复制整个平台，只新增：

- Toolchain；
- Hardware Profile；
- Vendor Adapter；
- Product target；
- 必要的 License/HIL pool。

---

## 7. Runner 怎么管理

Runner 标签描述“能力”，不描述机器编号。

不要：

```text
runs-on: build-server-03
```

RK 当前真实设计：

```text
self-hosted
linux
x64
soc-rk
```

Qualcomm/MTK 当前 planned profile 仍分别声明自己的 SoC 标签。

机器允许替换，只要新机器经过同样的 SDK identity / readiness 验证并拥有相同能力标签。

---

## 8. SDK/BSP 怎么管理

SDK 本身通常是巨大的厂商资产，不应该提交进中央 Git 仓库。

中央仓库只保存：

```text
SDK logical id
SDK identity SHA256
Hardware Profile binding
```

真实 Runner 保存/挂载 SDK：

```text
RK_SDK_ROOT
QCOM_SDK_ROOT
MTK_SDK_ROOT
```

每个 SDK 根目录要求：

```text
.ci/sdk-identity.json
```

它至少应该能追踪：

- 厂商/SDK ID；
- 版本；
- 原始 SDK/BSP 包 digest；
- 企业补丁集 digest。

平台对 identity 文件做 SHA256，并要求同时匹配：

```text
hardware-profiles.json -> sdk.expected_sha256
            ==
toolchains.json        -> host_identity
```

没有真实 SDK 指纹，就不能从 `planned` 改为 `active`。

---

## 9. License Pool 怎么管理

当前模型：

```text
RK         → license.required = false
Qualcomm   → license.required = true, pool=qcom-build
MediaTek   → license.required = true, pool=mtk-build
```

需要 License 的 Job：

```text
Job
 ↓
Resource Broker 申请 lease
 ↓
拿到临时环境变量
 ↓
Vendor Build
 ↓
finally 释放 lease
```

许可证地址/token 不提交进 Git，也不写进 Artifact。

如果资源申请或释放失败，流水线 fail closed，不允许“没拿到 License 也绿色”。

---

## 10. HIL 真机实验室怎么管理

固件 CI 的最后一公里不是“编译成功”，而是设备真的能：

```text
烧录
→ Reset / Power Cycle
→ Boot
→ 串口/ADB/Fastboot 观察
→ 健康检查
→ 版本/digest 检查
→ Smoke / 功能测试
```

HIL 设备必须以资源池管理：

```text
rk-linux-arm64
qcom-android-arm64
mtk-android-arm64
```

每个 Job 先申请独占 lease，再获得：

```text
CI_HIL_DEVICE_ID
CI_HIL_DEVICE_PATH（如适用）
```

同一块板同一时间只能被一个 Job 使用。

---

## 11. Vendor Adapter 与产品脚本的边界

中央平台拥有稳定 Adapter：

```text
scripts/vendor/rk/build.sh
scripts/vendor/rk/hil-test.sh
scripts/vendor/qcom/build.sh
scripts/vendor/qcom/hil-test.sh
scripts/vendor/mtk/build.sh
scripts/vendor/mtk/hil-test.sh
```

真实产品仓库拥有产品知识：

```text
ci/vendor-rk-build.sh
ci/vendor-rk-hil-test.sh
ci/vendor-qcom-build.sh
ci/vendor-qcom-hil-test.sh
ci/vendor-mtk-build.sh
ci/vendor-mtk-hil-test.sh
```

中央平台不应该猜：

- RK3588/RK3568 的真实 board config；
- 某 Qualcomm BSP 的 `lunch`/target；
- 某 MTK 产品的 vendor make target；
- 实际 flash 分区和产品 smoke test。

中央平台只管：什么时候能运行、在哪运行、SDK/License/设备是否可信、最终产物如何治理。

---

## 12. PR 为什么不能直接进入厂商 Runner

Self-hosted Runner 可能拥有：

- 商业 SDK；
- 内网；
- License；
- USB/串口；
- 设备实验室；
- 主机文件系统。

所以不可信 PR：

```text
PR
 ↓
发现 target 需要 self-hosted
 ↓
强制 GitHub Hosted Runner
 ↓
只执行 pr_validation_command
```

只有受信 `main` 才允许进入完整 Vendor Build / License / HIL 链路。

这条信任边界不能为了“PR 也想编完整固件”而取消。

---

## 13. Android 与 Linux 固件差异

### Android BSP/固件可能包含

- AOSP / Android build system；
- Boot / Vendor Boot / Super Image；
- Vendor 分区；
- APK / APEX / Native 库；
- 平台签名；
- OTA 包；
- ADB / Fastboot HIL。

### Linux BSP/固件可能包含

- Bootloader；
- Kernel；
- Device Tree；
- RootFS；
- Buildroot / Yocto；
- recovery / upgrade package；
- 串口/网络 HIL。

中央 CI 不需要理解每家 BSP 内部所有细节，但必须统一识别最终产物和证据。

---

## 14. 固件最终应该沉淀什么

最终发布对象不应该只有 `firmware.img`。

建议至少包含：

```text
firmware bundle
Artifact Contract v2 manifest
bundle/member SHA256
Toolchain / SDK identity
source SHA
依赖锁
SBOM
Supply-chain scan evidence
Attestation
签名
兼容硬件型号/板卡版本
刷写/升级说明
```

这样未来才能回答：

> “这块线上设备运行的固件，到底由哪个 commit、哪版 SDK、哪套依赖、哪个 Runner 能力构建出来？”

---

## 15. 签名边界

普通 Runner 不应该长期持有生产私钥。

推荐：

```text
普通构建
  ↓
unsigned immutable artifact
  ↓
受控签名阶段
  ↓
KMS / HSM / Signing Service
  ↓
signed artifact
```

当前平台已经具备 Attestation/Cosign 制品治理；真实厂商生产密钥/KMS/HSM 属于外部生产资源，需要企业实际签名系统后再接。

---

## 16. 弱网 / 海外工厂 / 边缘设备

不要让海外 Runner 每次跨区域重新下载几十 GB SDK。

推荐：

- 区域依赖代理；
- 区域制品缓存；
- SDK 版本化镜像/快照；
- 断点续传；
- SHA256；
- Manifest first；
- 按缺失对象拉取；
- Content-addressed artifact。

核心思想：

```text
存储转发 + 不可变身份 + 完整校验
```

---

## 17. 当前真实状态

| 能力 | RK | Qualcomm | MediaTek |
| --- | --- | --- | --- |
| Catalog / Toolchain binding | ✅ | ✅ | ✅ |
| Hardware Profile | ✅ | ✅ | ✅ |
| Vendor Adapter | ✅ | ✅ | ✅ |
| PR / Self-hosted 信任边界 | ✅ | ✅ | ✅ |
| SDK identity 契约 | ✅ | ✅ | ✅ |
| License lease 模型 | 可启用 | ✅ | ✅ |
| HIL lease 模型 | ✅ | ✅ | ✅ |
| Rollout | `rk-first` | paused | paused |
| 真实 Runner | ❌ | ❌ | ❌ |
| 真实 SDK/BSP | ❌ | ❌ | ❌ |
| 真实 License Server | 当前非必需 | ❌ | ❌ |
| 真实板卡/HIL | ❌ | ❌ | ❌ |
| 真实固件 CI | ❌ | ❌ | ❌ |

因此当前正确表述是：

```text
多 SoC CI 管理模型      ✅ 已实现
RK 执行面               ✅ 平台 Ready
RK 真实物理闭环         ⏸ 等真实主机/SDK/板卡
Qualcomm / MTK          ⏸ 保持 planned
```

不能把“平台配置存在”说成“厂商真机已经跑通”。

---

## 18. 相关文档

- [Hardware Runner / SDK / License / HIL 集成](hardware-runner-integration.md)
- [RK 真实物理接入手册](rk-physical-bringup.md)
- [容器化构建环境](containerized-build-environments.md)
- [Runner 与供应链安全](runner-security-and-supply-chain.md)
- [Artifact Contract v2](artifact-contract-v2.md)
- [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
