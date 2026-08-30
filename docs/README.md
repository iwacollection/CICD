# CI 平台文档导航

这套文档不是按“GitHub Actions 语法”组织，而是按真实生产问题组织。建议第一次学习按顺序读。

## 第一层：先理解整体

1. [总体架构](architecture.md)
   - CI 为什么要分配置面与执行面
   - 构建唯一性
   - 多 SoC 哪些统一、哪些隔离
   - 内部库 DAG

2. [容器化构建环境](containerized-build-environments.md)
   - Docker 到底能解决哪些环境依赖问题
   - Runner 和 Toolchain Image 如何拆分
   - 为什么默认 Container First、Host 只做例外
   - RK / Qualcomm / MediaTek SDK 怎么容器化
   - 驱动、USB 烧录、许可证、真机为什么仍需要专用 Runner
   - 工具链镜像如何版本化、锁 digest、升级和回滚

3. [构建、缓存与依赖](build-cache-and-dependencies.md)
   - 缓存、依赖代理、制品仓库的区别
   - Docker 镜像和构建缓存为什么不是一回事
   - 多 Job 并发缓存为什么会坏
   - 20 个内部库 + 50 个第三方库怎么处理
   - 构建一小时怎么拆

4. [多 SoC 与固件](multi-soc-and-firmware.md)
   - RK / Qualcomm / MediaTek
   - Android / Linux BSP
   - SDK、Runner、签名、真机实验室
   - 弱网/海外工厂

## 第二层：把发布链路理解清楚

5. [Artifact Contract v2](artifact-contract-v2.md)
   - 制品身份如何绑定源码、工具链、Runner、依赖锁和上游制品
   - 为什么包内每个成员都要有 digest
   - 为什么相同输入必须可复现出相同 bundle

6. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
   - 为什么测试到生产不能重新编译
   - 长期 Release Archive
   - `dev -> staging -> production` Promotion Path Policy
   - GitHub Deployment 作为环境 digest pointer
   - `promoted_from_deployment_id` 晋级审计链
   - rollback 为什么只恢复同环境历史 digest，不重新构建

7. [供应链策略](supply-chain-policy.md)
   - Action / Docker digest 固定
   - Ubuntu Snapshot
   - 漏洞、License、Secret、Misconfiguration 扫描
   - CycloneDX SBOM
   - Cosign / GitHub Attestation

8. [Runner 与供应链安全](runner-security-and-supply-chain.md)
   - Self-hosted Runner 风险
   - Docker Socket 为什么仍然是高权限边界
   - 不可信 PR
   - OIDC / KMS / HSM
   - SBOM / 签名 / 依赖混淆

## 第三层：平台怎么真正落地

9. [业务仓库调用中央 CI](reusable-workflow.md)
   - Reusable Workflow
   - 业务仓库和平台仓库如何分工
   - 中央 CI 如何版本化
   - 外部仓库如何固定 platform SHA

10. [真实依赖 DAG 执行](dependency-dag-execution.md)
    - L0-L7 分层执行
    - 同层并行、跨层 barrier
    - 上游 Artifact Contract v2 下载与校验
    - 下游缓存如何绑定上游 digest

11. [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)
    - 只构建真正受影响项目
    - 下游变更为什么仍要补齐上游 prerequisite
    - 全局 CI 变更为什么 fail-safe 到 full lane

12. [多语言构建策略](language-build-strategies.md)
    - C/C++
    - Java/Kotlin
    - Node.js
    - Python
    - Go
    - Rust
    - Android
    - Container Image

13. [新项目接入](onboarding.md)
    - 项目声明
    - Runner 准备
    - 工具链接入
    - PR 验收

## 第四层：把 CI 当生产系统运维

14. [CI 平台运维、可观测性与容量](operations-observability-and-capacity.md)
    - Queue Time
    - Build Duration P95/P99
    - Runner 容量
    - RTO/RPO
    - 成本和故障演练

15. [CI 平台健康度与 SLO](platform-health-slo.md)
    - Success Rate
    - Queue P95
    - Duration P95
    - Rerun Rate
    - `healthy / breached / insufficient-data`
    - 定时健康报告与证据保留

16. [仓库治理基线与漂移审计](repository-governance.md)
    - `main-production-governance` Ruleset
    - PR / CODEOWNERS / Review Thread
    - Required Checks
    - 禁止 force-push / deletion
    - Settings 漂移自动检测
    - bypass actor API 可见性与最小权限边界

17. [故障排查手册](troubleshooting.md)
    - Job 排队
    - 构建突然变慢
    - 缓存污染
    - 磁盘/inode
    - OOM
    - SDK 漂移
    - 制品拿错

## 第五层：硬件接入边界

18. [Hardware Runner Integration](hardware-runner-integration.md)
    - SoC profile
    - SDK identity
    - HIL lease
    - PR 与 Self-hosted Runner 信任边界

19. [RK Physical Bring-up](rk-physical-bringup.md)
    - x86_64 构建主机与 arm64 目标架构分离
    - 私有产品仓库拥有真实 Runner 授权域
    - Runner bootstrap
    - 本地 HIL broker
    - SDK enrollment / physical readiness

当前没有真实 RK 主机、SDK 和板卡，因此物理执行保持 planned；平台不会伪造物理验收结果。

## 工具链登记

- [Toolchain Registry 规范](../ci/toolchains/README.md)

## 代码对应关系

```text
项目声明 / target       -> ci/projects.json
工具链注册              -> ci/toolchains.json
硬件 Profile            -> ci/hardware-profiles.json
平台 SLO                -> ci/platform-slo.json
晋级路径策略            -> ci/promotion-policy.json
仓库治理策略            -> ci/repository-governance-policy.json
供应链策略              -> ci/supply-chain-policy.json

依赖 DAG                -> scripts/ci/dependency_plan.py / dag_plan.py
影响分析                -> scripts/ci/impact_analysis.py
动态矩阵                -> scripts/ci/discover_matrix.py
构建执行                -> scripts/ci/run_build.py
Artifact v2 打包         -> scripts/ci/package_artifact.py
Artifact 校验            -> scripts/ci/verify_artifact.py
长期归档                 -> scripts/ci/artifact_archive.py
环境指针 / rollback      -> scripts/ci/deployment_pointer.py
Promotion 路径校验       -> scripts/ci/promotion_policy.py
上游制品解析             -> scripts/ci/resolve_upstream_artifacts.py
供应链 Gate              -> scripts/ci/supply_chain_policy.py
平台健康度               -> scripts/ci/platform_health.py
仓库治理漂移             -> scripts/ci/repository_governance.py
硬件执行                 -> scripts/ci/hardware_execute.py

平台自检                 -> .github/workflows/validate.yml
主 DAG 构建              -> .github/workflows/ci.yml
单 DAG Node              -> .github/workflows/dag-node.yml
工具链供应链             -> .github/workflows/toolchain-images.yml
长期归档                 -> .github/workflows/archive-artifacts.yml
Promotion                -> .github/workflows/promote.yml
Rollback                 -> .github/workflows/rollback.yml
平台健康                 -> .github/workflows/platform-health.yml
仓库治理审计             -> .github/workflows/repository-governance.yml
外部仓库通用接入         -> .github/workflows/reusable-build.yml
RK 产品接入              -> .github/workflows/reusable-rk-build.yml
```

## 面试时怎么理解这套系统

不要回答“我会写 GitHub Actions”。更完整的表达是：

```text
我会把 CI 拆成代码触发、影响分析、依赖 DAG、构建调度、Runner 隔离、工具链、缓存、质量门禁、供应链安全、不可变制品、长期归档、分环境晋级、回滚、平台 SLO 和仓库治理漂移。

普通构建环境优先容器化；必须依赖厂商 SDK、USB、许可证或真机时，再进入受信 Self-hosted Runner。目标固件是 arm64，不代表构建主机也必须是 arm64。

多平台场景下治理统一，但 SDK/工具链/Runner 隔离；同一个 Artifact Contract v2 制品严格按 dev -> staging -> production 晋级，不在生产阶段重新构建。

如果构建变慢，我先拆 Queue、Dependency、Compile、Test、Scan、Package、Upload；如果 Queue 高而执行时间正常，则优先看 Runner 容量。仓库 Ruleset 本身也做持续漂移检测，避免 Settings 被手工改弱却无人发现。
```

能把这些说清楚，比背 `matrix`、`needs`、`cache` 语法更接近生产 DevOps/SRE。
