# CI 平台文档导航

这套文档按“理解平台 → 接入平台 → 发布制品 → 运维平台 → 硬件边界”的顺序组织，而不是按 GitHub Actions YAML 文件名组织。

如果第一次阅读，优先走 **快速主线**；需要深入某一主题时再进入专题文档。

---

## 快速主线

1. [总体架构](architecture.md)
   - 平台为什么拆成配置面、执行面、制品面和治理面
   - 多项目、多工具链、多 SoC 如何统一管理

2. [真实依赖 DAG](dependency-dag-execution.md)
   - L0-L7 分层
   - 同层并行、跨层 barrier
   - 上游 Artifact v2 如何真正传给下游

3. [Artifact Contract v2](artifact-contract-v2.md)
   - 制品如何绑定 source、toolchain、Runner、依赖和上游 digest
   - 为什么 bundle 和每个成员都要有 SHA256

4. [供应链策略](supply-chain-policy.md)
   - immutable Actions / container digest
   - Ubuntu Snapshot
   - Trivy / SBOM / Attestation / Cosign

5. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
   - Build once
   - GitHub Release 长期归档
   - `dev -> staging -> production`
   - rollback 恢复历史 digest，不重新构建

6. [生产生命周期真实验收记录](production-verification.md)
   - 2026-08-30 实际跑过的 main Build / Archive / Promotion / Rollback
   - v1 -> v2 -> rollback v1 的真实 Deployment / digest 证据

7. [平台维护手册](platform-maintenance.md)
   - 平台稳定以后什么能改、什么不能随手改
   - 哪类变更必须重新做生命周期 drill

---

## A. 架构与构建

### [总体架构](architecture.md)

重点：

- CI 为什么不是一条 Workflow；
- 配置面与执行面；
- 多 SoC 哪些统一、哪些隔离；
- 内部库 DAG；
- Build once。

### [容器化构建环境](containerized-build-environments.md)

重点：

- Runner 与 Toolchain Image 的边界；
- Container First、Host Only as Exception；
- Docker 能解决什么、不能解决什么；
- USB / 驱动 / License / SDK 为什么仍可能需要 Self-hosted Runner。

### [构建、缓存与依赖](build-cache-and-dependencies.md)

重点：

- Cache、依赖代理、Artifact Repository 的区别；
- 多 Job 并发缓存；
- 内部库与第三方依赖；
- 长构建如何拆分。

### [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)

重点：

- 只构建真正受影响项目；
- 为什么下游改动仍要补齐 prerequisite；
- 全局 CI 变化为什么 fail-safe 到 full lane。

### [真实依赖 DAG](dependency-dag-execution.md)

重点：

- DAG 不只是打印顺序；
- same-run upstream artifact；
- target-aware resolver；
- upstream digest 绑定 downstream cache。

### [多语言构建策略](language-build-strategies.md)

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

## B. 制品与供应链

### [Artifact Contract v2](artifact-contract-v2.md)

重点：

- source identity；
- toolchain identity；
- Runner / build metadata；
- dependency locks；
- upstream artifact digest；
- reproducible tar bundle。

### [供应链策略](supply-chain-policy.md)

重点：

- Action SHA 固定；
- Docker digest 固定；
- Ubuntu Snapshot；
- Vulnerability / License / Secret / Misconfiguration；
- CycloneDX SBOM；
- GitHub Attestation；
- Cosign。

### [Runner 与供应链安全](runner-security-and-supply-chain.md)

重点：

- Hosted / Self-hosted 信任边界；
- Docker Socket 风险；
- 不可信 PR；
- OIDC / KMS / HSM；
- 依赖混淆与签名。

### [制品、晋级与回滚](artifacts-promotion-and-rollback.md)

重点：

- Actions Artifact 只是短期运输；
- GitHub Release 是当前长期对象库；
- exact artifact identity；
- Deployment lineage；
- rollback 不 rebuild。

### [生产生命周期真实验收记录](production-verification.md)

这份文档记录平台已经实际验证过什么，避免把“设计存在”误写成“生产已验证”。

---

## C. 业务仓库接入

### [业务仓库调用中央 CI](reusable-workflow.md)

重点：

- `workflow_call`；
- caller 和中央平台职责分离；
- `platform_ref` 必须固定 exact 40-char SHA；
- 不要在业务仓库复制中央平台。

### [新项目接入手册](onboarding.md)

重点：

- 项目声明；
- toolchain；
- DAG；
- artifact paths；
- cache identity；
- PR 验收；
- 外部业务仓库接入。

---

## D. 平台运维与治理

### [CI 平台运维、可观测性与容量](operations-observability-and-capacity.md)

重点：

- Queue Time；
- Build Duration P95/P99；
- Runner 容量；
- RTO/RPO；
- 成本与故障演练。

### [CI 平台健康度与 SLO](platform-health-slo.md)

当前指标：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

### [仓库治理基线与漂移审计](repository-governance.md)

重点：

- 单维护者 Ruleset；
- Required Checks；
- 禁止 force-push / deletion；
- Review Thread；
- Settings drift；
- bypass actor 可见性边界。

### [平台维护手册](platform-maintenance.md)

重点：

- v1 稳定契约；
- 变更分级；
- 最低验证；
- 哪些改动必须重新做 main lifecycle；
- 什么时候才值得继续开发新能力。

### [故障排查手册](troubleshooting.md)

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

## E. 多 SoC / 硬件边界

### [多 SoC 与固件](multi-soc-and-firmware.md)

重点：

- RK / Qualcomm / MediaTek；
- Android / Linux BSP；
- SDK、Runner、签名、真机实验室；
- 哪些能力统一治理，哪些必须隔离。

### [Hardware Runner Integration](hardware-runner-integration.md)

重点：

- hardware profile；
- SDK identity；
- License / HIL lease；
- PR 与 Self-hosted Runner 信任边界。

### [RK Physical Bring-up](rk-physical-bringup.md)

重点：

- build host `x86_64` 与 target `arm64` 分离；
- 私有产品仓库拥有物理 Runner 授权域；
- Runner bootstrap；
- HIL broker；
- SDK enrollment / physical readiness。

当前没有真实 RK 主机、SDK 和板卡，因此硬件执行保持 planned。平台不会伪造物理验收结果。

---

## F. 配置与代码对应关系

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
Hardware Execution        -> scripts/ci/hardware_execute.py
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
RK Caller                 -> .github/workflows/reusable-rk-build.yml
```

工具链登记规范：

- [Toolchain Registry 规范](../ci/toolchains/README.md)

---

## G. 面试或架构说明时怎么讲

不要只说：

```text
“我会 GitHub Actions、Matrix、Cache。”
```

更完整的描述是：

```text
我把 CI 分成影响分析、依赖 DAG、构建调度、Runner 信任边界、
不可变工具链、缓存、供应链策略、Artifact Contract、长期归档、
环境 Promotion、Rollback、Platform SLO 和 Repository Governance。

内部依赖按 DAG 真正执行，上游 Artifact v2 传给下游；
测试到生产始终移动同一个 digest，不重新构建；
Self-hosted Runner 只接受受信 main；
最终制品具备 SBOM、Attestation、Cosign 和长期 Archive；
生产可以恢复同环境历史 digest，并保留完整 Deployment lineage。
```

这比背 Workflow 语法更接近真实生产 DevOps / SRE 平台设计。
