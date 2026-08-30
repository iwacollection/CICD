# CI 平台文档导航

这套文档围绕两条**一级业务主线**组织：

```text
Enterprise CI Platform
│
├── 主线 A：普通应用 / 多项目构建
│   ├── Linux C/C++
│   ├── 依赖 DAG
│   ├── Hosted / Container Toolchain
│   └── Artifact / Supply Chain / Release
│
└── 主线 B：嵌入式 / 多 SoC 固件 CI
    ├── RK / Rockchip / 瑞芯微
    ├── Qualcomm / 高通
    ├── MediaTek / MTK / 联发科
    ├── Android / Linux BSP
    ├── Vendor SDK / License
    ├── Self-hosted Runner
    └── HIL 真机实验室
```

两条主线最终共用同一套制品与发布治理：

```text
Artifact Contract v2
→ Supply-chain Policy
→ Attestation
→ Archive
→ dev → staging → production
→ Rollback
```

所以多 SoC 不是平台末尾的“附加硬件章节”，而是和普通应用 CI 并列的一条核心业务场景。

---

## 1. 第一次阅读：先选你的主线

### 如果你主要看普通应用 CI

推荐：

1. [总体架构](architecture.md)
2. [真实依赖 DAG](dependency-dag-execution.md)
3. [构建、缓存与依赖](build-cache-and-dependencies.md)
4. [Artifact Contract v2](artifact-contract-v2.md)
5. [供应链策略](supply-chain-policy.md)
6. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
7. [生产生命周期真实验收记录](production-verification.md)

### 如果你主要看 RK / 高通 / 联发科固件 CI

推荐：

1. [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)
2. [Hardware Runner / SDK / License / HIL](hardware-runner-integration.md)
3. [RK 真实物理接入手册](rk-physical-bringup.md)
4. [Runner 与供应链安全](runner-security-and-supply-chain.md)
5. [Artifact Contract v2](artifact-contract-v2.md)
6. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)

---

## 2. 平台快速主线

不区分业务类型时，先理解这 8 个核心概念：

1. [总体架构](architecture.md)
   - 配置面、执行面、制品面、治理面；
   - 普通应用和多 SoC 如何共用一个平台。

2. [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)
   - RK / Qualcomm / MediaTek 怎么统一管理；
   - `projects -> toolchain -> hardware profile -> rollout`；
   - SDK / License / HIL 为什么必须隔离。

3. [真实依赖 DAG](dependency-dag-execution.md)
   - L0-L7 分层；
   - 同层并行、跨层 barrier；
   - 上游 Artifact v2 如何真正传给下游。

4. [Artifact Contract v2](artifact-contract-v2.md)
   - source、toolchain、Runner、依赖锁、上游 digest；
   - bundle/member SHA256。

5. [供应链策略](supply-chain-policy.md)
   - immutable Action/container；
   - Ubuntu Snapshot；
   - Trivy / SBOM / Attestation / Cosign。

6. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
   - Build once；
   - GitHub Release 长期归档；
   - `dev -> staging -> production`；
   - rollback 恢复历史 digest，不重新构建。

7. [生产生命周期真实验收记录](production-verification.md)
   - 2026-08-30 实际跑过的 Build / Archive / Promotion / Rollback；
   - v1 -> v2 -> rollback v1 的真实证据。

8. [平台维护手册](platform-maintenance.md)
   - 稳定以后什么能改；
   - 哪类变更必须重新做 lifecycle drill。

---

# A. 平台总体架构与普通应用构建

## [总体架构](architecture.md)

重点：

- CI 为什么不是一条 Workflow；
- 配置面 / 执行面 / 制品面 / 治理面；
- 普通应用与多 SoC 的公共底座；
- Build once；
- 内部依赖 DAG。

## [容器化构建环境](containerized-build-environments.md)

重点：

- Runner 与 Toolchain Image 的边界；
- Container First、Host Only as Exception；
- Docker 能解决什么、不能解决什么；
- USB / 驱动 / License / 大型 Vendor SDK 为什么仍需要 Self-hosted Runner。

## [构建、缓存与依赖](build-cache-and-dependencies.md)

重点：

- Cache、依赖代理、Artifact Repository 的区别；
- 多 Job 并发缓存；
- 20 个内部库 + 50 个第三方库；
- 长构建怎么拆；
- 为什么 Cache 只能加速，不能做发布依据。

## [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)

重点：

- 只构建真正受影响项目；
- 为什么下游改动仍补齐 prerequisite；
- 全局 CI 变化为什么 fail-safe 到 full lane。

## [真实依赖 DAG](dependency-dag-execution.md)

重点：

- DAG 不只是打印顺序；
- same-run upstream Artifact；
- target-aware resolver；
- upstream digest 绑定 downstream cache。

## [多语言构建策略](language-build-strategies.md)

覆盖：

- C/C++；
- Java/Kotlin；
- Node.js；
- Python；
- Go；
- Rust；
- Android；
- Container Image。

---

# B. 多 SoC / 嵌入式固件 CI

## [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)

这是 RK / 高通 / 联发科的**总入口**。

重点：

```text
SoC
├── RK / Rockchip / 瑞芯微
├── Qualcomm / 高通
└── MediaTek / MTK / 联发科
```

以及中央管理关系：

```text
ci/projects.json
      ↓
ci/toolchains.json
      ↓
ci/hardware-profiles.json
      ↓
ci/hardware-rollout.json
      ↓
Runner / SDK / License / HIL / Vendor Adapter
      ↓
Artifact Contract v2
```

这份文档还解释：

- 哪些能力三家统一；
- 哪些能力必须隔离；
- Android BSP 与 Linux BSP 的不同；
- 为什么不复制 `rk-ci.yml / qcom-ci.yml / mtk-ci.yml`；
- 当前三家真实状态。

## [Hardware Runner / SDK / License / HIL](hardware-runner-integration.md)

重点：

- Self-hosted Runner 信任边界；
- SDK identity；
- License Pool；
- HIL Device lease；
- Vendor Adapter；
- 每个 SoC 如何从 `planned` 到 `active`。

## [RK 真实物理接入手册](rk-physical-bringup.md)

RK-first 的最后一公里：

```text
Private RK Product Repo
      ↓ pinned CICD SHA
Linux x86_64 Build Host
      ↓ RK SDK/BSP
Linux arm64 Firmware
      ↓ HIL Broker
RK Real Board
```

重点：

- **Build Host x86_64 != Target arm64**；
- 真实 Runner 归属私有产品仓库；
- Runner bootstrap；
- SDK enrollment；
- HIL broker；
- Physical Readiness；
- 什么证据才算“真机接入完成”。

### 当前硬件状态

```text
多 SoC 管理模型        ✅ 已实现
RK 平台执行面          ✅ Ready
RK 真机                ⏸ 缺主机 / SDK / 板卡
Qualcomm                ⏸ planned
MediaTek / MTK          ⏸ planned
```

平台不会用模拟数据冒充物理验收。

---

# C. 制品与供应链

## [Artifact Contract v2](artifact-contract-v2.md)

重点：

- source identity；
- toolchain/SDK identity；
- Runner/build metadata；
- dependency locks；
- upstream Artifact digest；
- reproducible bundle；
- bundle/member SHA256。

## [供应链策略](supply-chain-policy.md)

重点：

- Action SHA 固定；
- Docker digest 固定；
- Ubuntu Snapshot；
- Vulnerability / License / Secret / Misconfiguration；
- CycloneDX SBOM；
- GitHub Attestation；
- Cosign。

## [Runner 与供应链安全](runner-security-and-supply-chain.md)

重点：

- Hosted / Self-hosted 信任边界；
- Docker Socket 风险；
- 不可信 PR；
- Vendor SDK / License / HIL 权限；
- OIDC / KMS / HSM；
- 依赖混淆与签名。

## [制品、晋级与回滚](artifacts-promotion-and-rollback.md)

重点：

- Actions Artifact 只是短期运输；
- GitHub Release 是当前长期对象库；
- exact Artifact identity；
- Deployment lineage；
- rollback 不 rebuild。

## [生产生命周期真实验收记录](production-verification.md)

用于区分：

```text
代码支持
vs
main 上真的跑通过
```

---

# D. 业务仓库接入

## [业务仓库调用中央 CI](reusable-workflow.md)

重点：

- `workflow_call`；
- caller 与中央平台职责分离；
- `platform_ref` 固定 exact 40-char SHA；
- 普通项目用 `reusable-build.yml`；
- RK 产品优先使用 RK 专用受信入口；
- 不在业务仓库复制中央平台。

## [新项目接入手册](onboarding.md)

重点：

- project/target；
- Toolchain；
- DAG；
- Artifact path；
- Cache identity；
- PR validation；
- 外部业务仓库接入。

---

# E. 平台运维与治理

## [CI 平台运维、可观测性与容量](operations-observability-and-capacity.md)

重点：

- Queue Time；
- Build Duration P95/P99；
- Runner 容量；
- RTO/RPO；
- 成本；
- 故障演练；
- 未来硬件 Runner/HIL 容量管理。

## [CI 平台健康度与 SLO](platform-health-slo.md)

当前指标：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

## [仓库治理基线与漂移审计](repository-governance.md)

重点：

- 单维护者 Ruleset；
- Required Checks；
- 禁止 force-push / deletion；
- Review Thread；
- Settings drift；
- bypass actor 可见性边界。

## [平台维护手册](platform-maintenance.md)

重点：

- v1 稳定契约；
- 变更分级；
- 最低验证；
- 哪些改动需要重新做 main lifecycle；
- 什么时候才值得继续增加平台能力。

## [故障排查手册](troubleshooting.md)

重点：

- Job 排队；
- 构建突然变慢；
- Cache 污染；
- 磁盘 / inode；
- OOM；
- SDK 漂移；
- Artifact 拿错；
- Archive / Promotion 故障。

---

# F. 配置与代码对应关系

```text
项目 / target             -> ci/projects.json
工具链 Registry           -> ci/toolchains.json
Hardware Profile          -> ci/hardware-profiles.json
Hardware Rollout          -> ci/hardware-rollout.json
Supply-chain Policy       -> ci/supply-chain-policy.json
Promotion Policy          -> ci/promotion-policy.json
Platform SLO              -> ci/platform-slo.json
Repository Governance     -> ci/repository-governance-policy.json

影响分析                  -> scripts/ci/impact_analysis.py
依赖 DAG                  -> scripts/ci/dependency_plan.py / dag_plan.py
动态 Matrix               -> scripts/ci/discover_matrix.py
构建执行                  -> scripts/ci/run_build.py
Cache Identity            -> scripts/ci/cache_fingerprint.py
上游制品解析              -> scripts/ci/resolve_upstream_artifacts.py
Artifact v2 打包          -> scripts/ci/package_artifact.py
Artifact 校验             -> scripts/ci/verify_artifact.py
长期归档                  -> scripts/ci/artifact_archive.py
Promotion Path            -> scripts/ci/promotion_policy.py
Environment Pointer       -> scripts/ci/deployment_pointer.py
Supply-chain Gate         -> scripts/ci/supply_chain_policy.py
Reproducibility           -> scripts/ci/reproducibility_check.py
Platform Health           -> scripts/ci/platform_health.py
Governance Drift          -> scripts/ci/repository_governance.py
Hardware Catalog          -> scripts/ci/hardware_catalog.py
Hardware Execution        -> scripts/ci/hardware_execute.py
Resource Lease            -> scripts/ci/resource_lease.py
SDK Identity              -> scripts/ci/sdk_identity.py
```

Workflow：

```text
平台自检                  -> .github/workflows/validate.yml
主 DAG                    -> .github/workflows/ci.yml
单 DAG Node               -> .github/workflows/dag-node.yml
Toolchain Supply Chain    -> .github/workflows/toolchain-images.yml
长期归档                  -> .github/workflows/archive-artifacts.yml
Promotion                 -> .github/workflows/promote.yml
Rollback                  -> .github/workflows/rollback.yml
Platform Health           -> .github/workflows/platform-health.yml
Governance Audit          -> .github/workflows/repository-governance.yml
External Generic Caller   -> .github/workflows/reusable-build.yml
RK Build Caller           -> .github/workflows/reusable-rk-build.yml
RK SDK Enrollment         -> .github/workflows/reusable-rk-enrollment.yml
RK Physical Readiness     -> .github/workflows/reusable-rk-physical-readiness.yml
```

工具链登记规范：

- [Toolchain Registry 规范](../ci/toolchains/README.md)

---

# G. 面试或架构说明怎么讲

普通应用主线：

```text
我把 CI 拆成影响分析、依赖 DAG、构建调度、不可变工具链、缓存、
Artifact Contract、供应链、长期归档、环境 Promotion、Rollback、
Platform SLO 和 Repository Governance。
```

多 SoC 主线：

```text
对于 RK、高通和联发科，我没有复制三套流水线，而是用
Project Target -> Toolchain -> Hardware Profile -> Rollout 四层模型管理。
平台统一制品、安全和发布规则；厂商 SDK、License、Runner、HIL 和
产品 Recipe 隔离。PR 永远不进入高权限 Self-hosted Runner，只有
受信 main 才能进入完整 Vendor Build/HIL 链路。
```

这比只说“我会 GitHub Actions Matrix”更接近真实生产 CI 平台设计。
