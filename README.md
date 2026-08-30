# Enterprise CI Build Platform

企业级 **应用构建、嵌入式/多 SoC 固件、制品、供应链与发布治理平台**。

这个仓库不是“给一个项目写一条 GitHub Actions”，而是把多个项目、内部依赖、多工具链、多 SoC、不同 Runner、不可变制品、供应链证据、环境晋级和回滚放到同一套可验证规则里管理。

平台有两条一级业务主线：

```text
Enterprise CI Platform
│
├── 普通应用 / 多项目 CI
│   ├── Linux C/C++
│   ├── Hosted / Container Toolchain
│   └── Dependency DAG
│
└── 嵌入式 / 多 SoC 固件 CI
    ├── Rockchip / RK / 瑞芯微
    ├── Qualcomm / 高通
    ├── MediaTek / MTK / 联发科
    ├── Linux / Android BSP
    ├── Vendor SDK / License
    ├── Self-hosted Runner
    └── HIL 真机实验室
```

两条主线最终共用：

```text
Artifact Contract v2
→ Supply-chain Policy
→ Attestation
→ Archive
→ dev → staging → production
→ Rollback
```

> 当前 Hosted 主链已经完成真实生产生命周期验收：`main Build -> Artifact v2 -> Attestation -> Archive -> dev -> staging -> production -> rollback`。多 SoC 管理模型和硬件执行契约已经实现，但真实 RK / Qualcomm / MediaTek 主机、厂商 SDK、License Server 和板卡仍属于外部资源边界，不会用模拟结果冒充真机验收。

---

## 1. 这个平台解决什么问题

当 CI 从“一个仓库编译一下”扩大到企业场景，真正困难的是：

```text
哪些项目真的要构建？
内部库应该按什么顺序？
上游产物怎么可靠交给下游？
工具链/SDK 到底是哪一版？
缓存会不会把旧依赖带进新构建？
测试通过的 bytes 和生产 bytes 是不是同一份？
PR 能不能碰高权限 Self-hosted Runner？
RK / 高通 / 联发科三套 SDK、License、板卡怎么隔离？
制品能不能长期保存、追溯、验签、回滚？
CI 自己慢了、排队了、治理漂移了，谁知道？
```

这个仓库围绕这些问题建立统一平台，而不是围绕某一家厂商命令写死流水线。

---

## 2. 两条业务主线怎么汇合

### 2.1 普通应用 / 多项目

已经真实验证：

```text
hello-lib (L0)
   ↓ Artifact Contract v2
hello-cpp (L1)
   ↓
Supply Chain / SBOM
   ↓
Attestation
   ↓
Archive
   ↓
Promotion / Rollback
```

### 2.2 多 SoC / 固件

平台管理模型：

```text
Product Target
ci/projects.json
      ↓
Toolchain / SDK
ci/toolchains.json
      ↓
Hardware Profile
ci/hardware-profiles.json
      ↓
Rollout Policy
ci/hardware-rollout.json
      ↓
Runner / SDK Identity / License / HIL / Vendor Adapter
      ↓
Firmware Artifact Contract v2
      ↓
同一套 Supply Chain / Archive / Promotion / Rollback
```

详细主线：**[多 SoC / 固件 CI 管理](docs/multi-soc-and-firmware.md)**

---

## 3. 当前已经真实跑通的 Hosted 生命周期

```text
Pull Request
     ↓
Validate CI platform
     ├── catalog / DAG / policy / governance
     └── reproducibility gate
     ↓
Impact Analysis
     ↓
Dependency DAG
     ↓
Build / Test
     ↓
Vulnerability / License / Secret / Misconfiguration
     ↓
CycloneDX SBOM
     ↓
Artifact Contract v2
     ↓
GitHub Attestation
     ↓
Build gate
     ↓
Archive Trusted Artifacts
     ↓
GitHub Release + Cosign
     ↓
dev → staging → production
     ↓
rollback to historical production digest
```

完整 Run / Deployment / digest 证据：

**[生产生命周期真实验收记录](docs/production-verification.md)**

---

## 4. 能力状态

### 4.1 已真实验证

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Hosted C/C++ 构建 | ✅ | CMake + ccache + immutable toolchain image |
| Fast Lane / 影响分析 | ✅ | 只构建受影响项目并补齐 prerequisite |
| 真实依赖 DAG | ✅ | L0-L7，同层并行、跨层 barrier |
| 上游制品交接 | ✅ | Artifact v2 下载、校验、staging、下游消费 |
| 不可变 Toolchain | ✅ | image digest + Ubuntu Snapshot |
| Cache Identity | ✅ | project / target / toolchain / locks / upstream digest |
| Reproducibility Gate | ✅ | 两次 clean build 比较原始产物和 bundle bytes |
| Artifact Contract v2 | ✅ | manifest + member SHA256 + bundle SHA256 |
| Supply-chain Scan | ✅ | vuln / license / secret / misconfiguration |
| CycloneDX SBOM | ✅ | 随制品长期保留 |
| GitHub Attestation | ✅ | trusted `main` provenance |
| Cosign | ✅ | Archive 签名与 Promotion/Rollback 验签 |
| 长期制品归档 | ✅ | 当前使用 GitHub Releases |
| `dev -> staging -> production` | ✅ | exact artifact identity 强制晋级 |
| Production Rollback | ✅ | `A -> B -> A`，旧版本不重新构建 |

### 4.2 平台已实现

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Repository Governance Drift | ✅ 已实现 | Ruleset 期望状态持续审计 |
| Platform Health / SLO | ✅ 已实现 | Success / Queue P95 / Duration P95 / Rerun Rate |
| 多 SoC Catalog 管理 | ✅ 已实现 | Project → Toolchain → Hardware Profile → Rollout |
| PR / Self-hosted 信任边界 | ✅ 已实现 | 不可信 PR 不进入厂商高权限 Runner |
| SDK Identity 契约 | ✅ 已实现 | `sdk-identity.json` + SHA256 pin |
| License Lease | ✅ 已实现 | Qualcomm/MTK 许可证池模型 |
| HIL Lease | ✅ 已实现 | 真机独占租约与释放语义 |
| Vendor Adapter | ✅ 已实现 | RK/Qcom/MTK build + HIL 适配层 |
| RK 物理接入准备 | ✅ Ready | x86_64 build host → arm64 target |

### 4.3 仍需真实外部资源

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| RK 真机闭环 | ⏸ | 缺真实主机、RK SDK/BSP、RK 板、USB/串口 |
| Qualcomm 真机闭环 | ⏸ | `planned`，缺真实 SDK/License/Runner/HIL |
| MediaTek/MTK 真机闭环 | ⏸ | `planned`，缺真实 SDK/License/Runner/HIL |
| 企业依赖代理 | ⏳ | Nexus/Artifactory 架构已定义，未实际部署 |
| 外部长期 Artifact Repository | ⏳ | 当前 GitHub Releases 已可用，规模化后再接 S3/MinIO/Nexus/Artifactory |
| 生产厂商签名 KMS/HSM | ⏳ | 需要真实企业签名基础设施 |

---

## 5. 多 SoC 到底怎么管理

SoC（System on Chip）是大类：

```text
SoC
├── Rockchip = 瑞芯微 = RK
├── Qualcomm = 高通
└── MediaTek = 联发科 = MTK
```

我们没有复制三套平台，而是：

```text
统一治理
├── PR / main
├── DAG
├── Artifact
├── Supply Chain
├── Archive
├── Promotion
└── Rollback

厂商隔离
├── SDK/BSP
├── Runner
├── Host requirements
├── License
├── HIL board
├── Flash mechanism
└── Product recipe
```

当前 Hardware Profile：

| SoC | Profile | Target | 当前 Runner | License | HIL | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| RK | `rk-linux-arm64-lab` | Linux arm64 | Linux x86_64 | 当前非必需 | 必需 | `planned` |
| Qualcomm | `qcom-android-arm64-lab` | Android arm64 | Catalog 当前为 Linux arm64 | 必需 | 必需 | `planned` |
| MediaTek | `mtk-android-arm64-lab` | Android arm64 | Catalog 当前为 Linux arm64 | 必需 | 必需 | `planned` |

RK 已明确：

```text
Build Host = Linux x86_64
      ↓ cross compile
Target     = Linux arm64 firmware
```

Qualcomm / MTK 的 Host 架构仍要在拿到真实 SDK 后按厂商支持矩阵验证，不能把 target arch 当作 host arch。

详见：

- **[多 SoC / 固件 CI 管理主线](docs/multi-soc-and-firmware.md)**
- **[Hardware Runner / SDK / License / HIL](docs/hardware-runner-integration.md)**
- **[RK 真实物理接入手册](docs/rk-physical-bringup.md)**

---

## 6. 最重要的设计原则

### 6.1 Build once

```text
commit
  ↓
Build once
  ↓
artifact digest A
  ↓
dev
  ↓
staging
  ↓
production
```

测试与生产不能重新构建出另一份 bytes。

### 6.2 Cache 只负责加速

Cache 可以删除、miss、失效，但不能成为：

- 唯一依赖来源；
- 上游项目交付方式；
- 长期制品仓库；
- 生产发布依据。

### 6.3 DAG 必须真正执行

```text
hello-lib
   ↓ verified Artifact v2
hello-cpp
```

下游只接受本次 Run 中刚构建、刚校验的上游 digest。

### 6.4 Toolchain / SDK 必须有身份

普通 Container Toolchain 使用完整 `@sha256:` digest；厂商 Host SDK 使用 `sdk-identity.json` 和不可变 `host_identity`。

### 6.5 PR 和高权限 Runner 必须隔离

PR 不能访问 Vendor SDK、License、USB、HIL、内网等高权限能力，只允许 Hosted-safe `pr_validation_command`。

### 6.6 Rollback 不是 checkout 老源码再 build

Rollback 恢复的是同环境历史成功的 immutable digest。

---

## 7. 仓库结构

```text
CICD/
├── .github/workflows/
│   ├── validate.yml                       # 平台自检 / reproducibility
│   ├── ci.yml                             # 主 DAG
│   ├── dag-node.yml                       # 单 DAG Node
│   ├── toolchain-images.yml               # Toolchain Supply Chain
│   ├── archive-artifacts.yml              # 长期归档
│   ├── promote.yml                        # dev/staging/production
│   ├── rollback.yml                       # 历史 digest rollback
│   ├── platform-health.yml                # Platform SLO
│   ├── repository-governance.yml          # Ruleset drift
│   ├── reusable-build.yml                 # 通用业务仓库入口
│   ├── reusable-rk-build.yml              # RK 产品构建入口
│   ├── reusable-rk-enrollment.yml         # RK SDK 入籍
│   └── reusable-rk-physical-readiness.yml # RK 物理 readiness
│
├── ci/
│   ├── projects.json                      # 项目 / target
│   ├── toolchains.json                    # Toolchain / SDK Registry
│   ├── hardware-profiles.json             # Runner/SDK/License/HIL Profile
│   ├── hardware-rollout.json              # SoC rollout policy
│   ├── supply-chain-policy.json
│   ├── promotion-policy.json
│   ├── platform-slo.json
│   └── repository-governance-policy.json
│
├── scripts/ci/                             # 平台规则实现
├── scripts/vendor/                         # RK/Qcom/MTK 稳定 Adapter
├── docker/toolchains/                      # 不可变 Toolchain Image
├── ops/rk-runner/                          # RK 物理接入准备
├── examples/                               # Hosted DAG 示例
├── tests/                                  # 契约 / 安全边界回归
└── docs/                                   # 平台文档
```

完整导航：**[docs/README.md](docs/README.md)**

---

## 8. 新项目怎么接入

### 普通项目

业务仓库固定中央平台完整 Commit SHA 调用：

```text
.github/workflows/reusable-build.yml
```

业务负责源码与 build/test recipe，中央平台负责 Runner、Toolchain、Artifact、安全与发布规则。

### RK 产品

真实 RK 产品优先通过专用受信入口接入：

```text
reusable-rk-enrollment.yml
reusable-rk-physical-readiness.yml
reusable-rk-build.yml
```

真实 Self-hosted Runner 注册到私有产品仓库/受控 Runner Group，而不是公开 CICD 仓库。

详细步骤：

- [新项目接入手册](docs/onboarding.md)
- [业务仓库调用中央 CI](docs/reusable-workflow.md)
- [RK 真实物理接入](docs/rk-physical-bringup.md)

---

## 9. 发布和回滚

```text
main Build
   ↓
Trusted Attestation
   ↓
Long-term Archive
   ↓
dev
   ↓
staging
   ↓
production
```

Promotion 重新验证：

- trusted Build；
- Artifact Contract v2；
- bundle SHA256；
- Release identity；
- Supply-chain Policy；
- GitHub Attestation；
- Cosign；
- 前置环境 successful Deployment。

Rollback 只接受同环境历史 Deployment ID，并创建新的 rollback pointer，不重新构建旧版本。

详见：**[制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)**

---

## 10. `main` 治理

当前是单维护者治理模型，但仍保持：

```text
必须 Pull Request
Required approvals = 0
Code Owner mandatory approval = false
Review threads 必须解决
禁止 force-push
禁止删除 main
无 bypass actor
```

Required Checks：

```text
Validate CI platform
Build gate
Toolchain gate
```

详见：**[仓库治理基线与漂移审计](docs/repository-governance.md)**

---

## 11. 平台自己怎么运维

当前 SLO：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

并持续审计 Ruleset / Required Checks / force-push / deletion protection。

- [CI 平台维护手册](docs/platform-maintenance.md)
- [平台健康度与 SLO](docs/platform-health-slo.md)
- [故障排查手册](docs/troubleshooting.md)

---

## 12. 推荐阅读顺序

### 普通应用 / Hosted 主线

1. [总体架构](docs/architecture.md)
2. [真实依赖 DAG](docs/dependency-dag-execution.md)
3. [构建、缓存与依赖](docs/build-cache-and-dependencies.md)
4. [Artifact Contract v2](docs/artifact-contract-v2.md)
5. [供应链策略](docs/supply-chain-policy.md)
6. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
7. [生产生命周期真实验收记录](docs/production-verification.md)

### RK / 高通 / 联发科主线

1. [多 SoC / 固件 CI 管理主线](docs/multi-soc-and-firmware.md)
2. [Hardware Runner / SDK / License / HIL](docs/hardware-runner-integration.md)
3. [RK 真实物理接入手册](docs/rk-physical-bringup.md)
4. [Runner 与供应链安全](docs/runner-security-and-supply-chain.md)
5. [Artifact Contract v2](docs/artifact-contract-v2.md)
6. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)

---

## 13. 当前阶段

核心平台已进入 **稳定 / 文档 / 真实消费者接入阶段**。

当前优先级：

```text
文档与代码保持一致
安全与依赖升级
Platform SLO
生命周期故障演练
真实业务仓库消费 reusable workflow
有硬件后恢复 RK physical bring-up
拿到真实 Qualcomm/MTK SDK 后再验证 Host/License/HIL 设计
规模需要时再引入 Nexus/S3/MinIO/Artifactory
```

对于这个项目，成熟的标志不是 Workflow 数量越来越多，而是：

> **普通应用和多 SoC 固件都能在统一治理下产生可证明的不可变制品；同一制品能够安全晋级、可追溯、可回滚，而厂商 SDK/Runner/License/HIL 又保持严格隔离。**
