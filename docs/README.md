# CI/CD 平台文档导航

> **第一次学习请先看：[第一次看这个 CI/CD 项目：从这里开始](START-HERE.md)。**
>
> 不需要从头读完所有文档。先理解主流程，再按实际问题查阅专题。

## 一、核心学习路线

按顺序读下面 4 份文档，就能建立平台的基本认识：

1. [总体架构](architecture.md)：平台由哪些部分组成，代码如何进入平台。
2. [真实依赖 DAG](dependency-dag-execution.md)：多个项目之间有依赖时，构建顺序如何安排。
3. [Artifact Contract v2](artifact-contract-v2.md)：构建结果如何记录来源、依赖和校验值。
4. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)：同一个制品如何经过测试、预发布、生产，以及出问题如何回退。

完整主流程：

`代码提交 → 影响分析 → 依赖构建 → 测试 → 制品打包 → 安全检查 → 长期归档 → 环境发布 → 验证 / 回滚`

## 二、按问题查文档

### 构建和工具链

- [容器化构建环境](containerized-build-environments.md)
- [构建、缓存与依赖](build-cache-and-dependencies.md)
- [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)
- [多语言构建策略](language-build-strategies.md)
- [Java 工具链管理](java-toolchain-management.md)
- [Jenkins Java Toolchain Shared Library](jenkins-shared-library.md)

### 制品和安全

- [Artifact Contract v2](artifact-contract-v2.md)
- [供应链策略](supply-chain-policy.md)
- [Runner 与供应链安全](runner-security-and-supply-chain.md)
- [制品、晋级与回滚](artifacts-promotion-and-rollback.md)

### 发布和部署

- [灰度、蓝绿与跨集群发布](progressive-delivery.md)
- [CI/CD 完整闭环架构](cicd-closed-loop-architecture.md)
- [Argo CD / Argo Rollouts 接入规范](argocd-argo-rollouts-integration.md)
- [生产生命周期验收记录](production-verification.md)

### 嵌入式和多 SoC 固件

- [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)
- [Hardware Runner / SDK / License / HIL](hardware-runner-integration.md)
- [RK 真实物理接入手册](rk-physical-bringup.md)

### 平台运维和治理

- [新项目接入手册](onboarding.md)
- [业务仓库调用中央 CI](reusable-workflow.md)
- [CI 平台运维、可观测性与容量](operations-observability-and-capacity.md)
- [平台健康度与 SLO](platform-health-slo.md)
- [仓库治理基线与漂移审计](repository-governance.md)
- [平台维护手册](platform-maintenance.md)
- [故障排查手册](troubleshooting.md)

## 三、配置和代码从哪里找

| 内容 | 位置 |
|---|---|
| 项目和构建目标 | `ci/projects.json` |
| 工具链登记 | `ci/toolchains.json` |
| 硬件规格 | `ci/hardware-profiles.json` |
| 供应链规则 | `ci/supply-chain-policy.json` |
| 发布策略 | `ci/release-strategies.json` |
| 依赖分析和构建计划 | `scripts/ci/impact_analysis.py`、`scripts/ci/dependency_plan.py` |
| 构建执行 | `scripts/ci/run_build.py` |
| 制品打包和校验 | `scripts/ci/package_artifact.py`、`scripts/ci/verify_artifact.py` |
| 发布状态管理 | `scripts/ci/release_strategy.py` |

## 四、GitHub Actions 工作流分别负责什么

这些工作流不是重复的“多套 CI”。它们承担不同职责：

| 工作流 | 作用 |
|---|---|
| `validate.yml` | 检查平台配置、策略和自动化测试 |
| `ci.yml` | 分析改动并按依赖顺序构建项目 |
| `dag-node.yml` | 执行一个具体的依赖构建节点 |
| `toolchain-images.yml` | 构建、检查和发布中央工具链镜像 |
| `archive-artifacts.yml` | 长期保存构建制品 |
| `promote.yml` | 将已验证制品晋级到目标环境 |
| `release.yml` | 管理灰度、蓝绿等渐进式发布 |
| `rollback.yml` | 将环境恢复到之前的制品版本 |
| `platform-health.yml` | 收集平台健康度和运行指标 |
| `repository-governance.yml` | 检查仓库规则是否发生漂移 |
| `reusable-build.yml` | 供普通业务仓库调用的构建入口 |
| `reusable-rk-build.yml` | 供 RK 固件项目调用的构建入口 |
| `reusable-rk-enrollment.yml` | 登记真实 RK SDK 身份 |
| `reusable-rk-physical-readiness.yml` | 检查真实 RK Runner、SDK 和硬件实验室是否就绪 |

## 五、设计、代码和生产验收要分开看

- 文档说明设计目标，不自动代表功能已经上线。
- 代码存在，不自动代表真实外部系统已经接通。
- GitHub Actions 测试通过，只能证明对应自动化测试通过。
- 真实 Jenkins、Harbor、Kubernetes 集群、RK 板卡等外部环境，需要各自的接入和验收证据。

具体证据以[生产生命周期验收记录](production-verification.md)和各专题中的“当前状态 / 未完成边界”为准。
