# CI/CD 学习与工作文档导航

文档按真实交付工作组织。第一次接触平台，先读[第一次学习入口](START-HERE.md)；遇到具体工作，再从本页进入对应的操作手册。不要把所有专题当作一条必须从头读完的教程。

## 先看懂整条交付主线

```text
项目接入
  → 代码变更与构建范围分析
  → 工具链准备、依赖构建与测试
  → 制品生成、校验和安全检查
  → 制品归档与环境晋级
  → 部署后运行验证 / 渐进式发布
  → 回滚、运维与持续治理
```

## 首先区分三种不同的生命周期

这是阅读本仓库最重要的边界。它们可能都出现 Candidate、Production、Deployment 等词，但不是同一条流水线。

| 生命周期 | 管理对象 | 入口工作流 | 完成后能说明什么 |
|---|---|---|---|
| 工具链晋级 | JDK、GCC、Go 等构建环境镜像 | `.github/workflows/promote-toolchain.yml` | 精确工具链镜像通过证据校验、独立审批，并通过目录 PR 成为 Active |
| 业务制品晋级 | Artifact Contract v2 Bundle | `.github/workflows/promote.yml` | 同一个业务制品 digest 按 dev → staging → production 顺序登记环境指针 |
| Kubernetes 运行时验证 | GitOps revision、Pod 状态、实际 imageID digest | `.github/workflows/verify-runtime-deployment.yml` | 只读确认集群实际运行版本与预期一致，不执行部署 |

另有一条**渐进式发布状态引擎**：`.github/workflows/release.yml` 调用发布策略脚本，生成和验证期望的权重/槽位状态。没有真实控制器执行和运行时反馈证据时，不能把渲染成功写成已切流。

## 按实际工作流程学习

### 1. 接入一个新项目

先确认项目怎么构建、依赖哪些内部组件、需要什么工具链以及最终交付什么文件。

- [新项目接入手册](onboarding.md)：从项目登记到首次构建的步骤和检查项。
- [业务仓库调用中央 CI](reusable-workflow.md)：独立业务仓库如何固定中央平台版本并调用共享工作流。
- [总体架构](architecture.md)：平台各层的职责与边界。
- [开箱即用指南](quickstart.md)：本地检查、测试和运行四层示例 DAG。

### 2. 提交代码后，平台决定构建什么

- [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)：根据变更范围决定构建任务。
- [真实依赖 DAG](dependency-dag-execution.md)：上游任务、下游任务、等待关系和 Artifact 交接。
- [构建、缓存与依赖](build-cache-and-dependencies.md)：依赖代理、编译缓存、缓存隔离和故障恢复。

### 3. 准备构建环境并完成构建、测试

- [多语言构建策略](language-build-strategies.md)：Java、Go、C/C++ 的统一接入原则。
- [容器化构建环境](containerized-build-environments.md)：容器工具链、宿主机例外和执行隔离。
- [Java 工具链管理](java-toolchain-management.md)：JDK、Maven/Gradle、镜像规格和兼容性治理。
- [Jenkins Java Toolchain Shared Library](jenkins-shared-library.md)：Jenkins Controller/Agent 配置、凭据和项目调用。
- [Jenkins Go / C++ 端到端验收](native-jenkins-e2e-acceptance.md)：Mock、GitHub Actions 与真实 Jenkins 验收边界。
- [平台运维、可观测性与容量](operations-observability-and-capacity.md)：队列、成功率、执行耗时和容量治理。

### 4. 证明构建结果可信

- [Artifact Contract v2](artifact-contract-v2.md)：制品身份、Manifest、来源和文件 SHA256。
- [供应链策略](supply-chain-policy.md)：漏洞、许可证、Secret、配置风险和失败关闭规则。
- [Runner 与供应链安全](runner-security-and-supply-chain.md)：不可信 PR、Self-hosted Runner、凭据和签名边界。
- [可复现构建门禁](reproducibility-gate.md)：隔离构建和输出一致性验证。

### 5. 归档、晋级、发布和回滚

**业务制品：**

- [制品、长期归档、晋级与回滚](artifacts-promotion-and-rollback.md)：Bundle 如何归档、环境指针如何推进以及历史版本如何恢复。

**工具链镜像：**

- [工具链供应链管理](toolchain-supply-chain.md)：Candidate、Active、Retired 状态及其与业务构建的关系。
- [工具链 Candidate 晋级操作手册](toolchain-promotion-runbook.md)：审批前的签名、SBOM、来源证明和安全扫描验证，独立审批、审批后复扫以及目录 PR。

**Kubernetes 和渐进式发布：**

- [灰度、蓝绿与跨集群发布](progressive-delivery.md)：策略状态机、步骤推进、中止和区域顺序。
- [CI/CD 完整闭环架构](cicd-closed-loop-architecture.md)：制品治理、GitOps、Argo CD、Rollouts 和指标驱动决策的目标设计。
- [Argo CD / Argo Rollouts 接入规范](argocd-argo-rollouts-integration.md)：组件职责、接入阶段和安全边界。
- [Argo CD / Kubernetes 部署后运行验证](argocd-kubernetes-runtime-verification.md)：只读读取 GitOps revision、Pod Ready 和实际镜像 digest。

### 6. 平台运行、排障和治理

- [故障排查手册](troubleshooting.md)：按现象定位触发、排队、Runner、构建、制品和发布问题。
- [平台健康度与 SLO](platform-health-slo.md)：自动采集指标、阈值和样本不足处理。
- [平台维护手册](platform-maintenance.md)：变更边界、最低验证和事故处理。
- [仓库治理基线与漂移审计](repository-governance.md)：GitHub Ruleset 期望状态和漂移检测。
- [生产生命周期验收记录](production-verification.md)：保留已经完成的历史真实运行证据；它不是实时状态看板。

## 特定项目场景

### 嵌入式与多 SoC 固件

- [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)：RK、Qualcomm、MediaTek 的统一治理和隔离策略。
- [Hardware Runner / SDK / License / HIL](hardware-runner-integration.md)：专用机器、SDK 身份、许可证与真机实验室。
- [RK 真实物理接入手册](rk-physical-bringup.md)：真实板卡接入前的准备、预检和验收。

### Java 与 Jenkins

- [Java 工具链管理](java-toolchain-management.md)
- [Jenkins Java Toolchain Shared Library](jenkins-shared-library.md)
- [Go / C++ 工具链构建](go-cpp-toolchain.md)

## 每份技术专题应该回答什么

每份操作或设计文档都应回答：

1. **为什么需要**：生产中遇到什么问题，不做会有什么影响？
2. **负责什么、不负责什么**：明确能力边界和外部前提。
3. **实际怎么运行**：按真实顺序说明输入、执行、输出和状态变化。
4. **失败怎么办**：给出日志/指标证据、止血动作、恢复步骤和恢复验证。
5. **怎样证明完成**：列出自动化证据和真实环境验收证据，不用“应该可以”代替结果。

## 如何判断一项能力已经完成

| 证据层 | 能证明什么 | 不能证明什么 |
|---|---|---|
| 设计目标 | 说明预期行为和组件职责 | 不能证明代码实现 |
| 仓库实现 | 存在配置、脚本、工作流或集成入口 | 不能证明行为已经正确运行 |
| 自动化验证 | 测试或 Workflow 在对应 SHA 和条件下通过 | 不能证明外部系统已接通 |
| 真实环境验收 | 指定外部环境完成端到端验证并保留证据 | 不能自动推广到其他环境、硬件或版本 |

当前文档的审核范围、文档职责和关键状态快照见[技术文档审核记录](documentation-review.md)。历史生产生命周期证据见[生产生命周期验收记录](production-verification.md)。

## 跨流程专题：遇到问题再深入

- [业务 CI 能力扩展](business-ci-capabilities.md)：质量检查、自动化测试、容器构建和数据库迁移。
- [平台完整闭环架构](cicd-closed-loop-architecture.md)：从制品到 GitOps、运行反馈和恢复的目标架构。
- [业务仓库调用中央 CI](reusable-workflow.md)：中央平台升级后，业务仓库如何固定版本并分批接入。

不要在多份文档里复制同一套阈值、命令或状态表。配置规则优先以中央 JSON 和实际脚本为准；操作文档解释怎样使用和验证它们。
