# CI/CD 学习与工作文档导航

文档按实际交付工作流程组织。建议先读[第一次学习入口](START-HERE.md)，再根据当前工作环节查阅专题；不需要按目录顺序读完所有文档。

## 一、先理解整条交付流程

```text
项目接入
  → 代码变更与构建范围分析
  → 工具链准备、依赖构建与测试
  → 制品生成、校验和安全检查
  → 制品归档与环境发布
  → 发布验证、回滚
  → 平台日常运维与持续治理
```

## 二、按工作流程学习

### 1. 接入一个新项目

先弄清楚项目需要提供什么，平台负责什么，以及怎样安全地接入中央构建流程。

- [新项目接入手册](onboarding.md)：从项目登记到首次构建的完整接入步骤。
- [业务仓库调用中央 CI](reusable-workflow.md)：业务仓库如何调用中央构建流程。
- [总体架构](architecture.md)：了解平台各环节如何衔接。

### 2. 提交代码后，平台如何决定构建什么

理解一次代码变更怎样影响项目，以及多个项目之间有依赖时如何安排先后顺序。

- [Fast Lane 与影响分析](fast-lane-and-impact-analysis.md)：识别本次变更影响的项目，避免无关构建。
- [真实依赖 DAG](dependency-dag-execution.md)：按依赖关系安排构建，支持安全并行。
- [构建、缓存与依赖](build-cache-and-dependencies.md)：理解依赖下载、缓存命中和缓存失效时的处理。

### 3. 平台怎样准备环境并完成构建、测试

不同项目需要不同版本的 JDK、编译器或厂商 SDK；本环节重点是环境一致、任务隔离和失败可定位。

- [多语言构建策略](language-build-strategies.md)：不同语言项目如何共用平台规则。
- [容器化构建环境](containerized-build-environments.md)：如何固定构建环境，减少机器差异。
- [Java 工具链管理](java-toolchain-management.md)：管理不同版本 JDK 和 Java 构建工具。
- [Jenkins Java Toolchain Shared Library](jenkins-shared-library.md)：Jenkins 项目如何调用统一 Java 工具链。
- [Jenkins Go / C++ 端到端验收](native-jenkins-e2e-acceptance.md)：Go Candidate 晋级、Go/C++ 示例项目接入、Jenkins 凭据与真实验收证据。
- [平台运维、可观测性与容量](operations-observability-and-capacity.md)：排查排队、执行时间和 Runner 容量问题。

### 4. 构建完成后，怎样证明制品可信

制品是最终要交付的软件文件或固件。本环节解决来源不清、文件被替换、依赖不明和安全风险不可追溯的问题。

- [Artifact Contract v2](artifact-contract-v2.md)：记录制品来源、构建环境、依赖和文件校验值。
- [供应链策略](supply-chain-policy.md)：检查漏洞、许可证、密钥和配置风险。
- [Runner 与供应链安全](runner-security-and-supply-chain.md)：隔离不可信代码与有权限的构建机器。

### 5. 怎样保存、发布和回滚

先理解制品归档，再理解同一份制品如何进入不同环境，以及发布失败时如何恢复。

- [制品、晋级与回滚](artifacts-promotion-and-rollback.md)：制品如何归档、进入环境以及恢复历史版本。
- [灰度、蓝绿与跨集群发布](progressive-delivery.md)：逐步放量、切换版本和跨集群发布的操作流程。
- [CI/CD 完整闭环架构](cicd-closed-loop-architecture.md)：查看从制品到部署、验证和回滚的整体设计。
- [Argo CD / Argo Rollouts 接入规范](argocd-argo-rollouts-integration.md)：了解与 Kubernetes 发布系统的集成边界。

### 6. 平台运行中，怎样发现和处理问题

- [故障排查手册](troubleshooting.md)：按现象定位构建、制品、发布和权限问题。
- [平台健康度与 SLO](platform-health-slo.md)：了解成功率、排队时间和执行时间等平台指标。
- [平台维护手册](platform-maintenance.md)：日常维护、变更和重新验收要求。
- [仓库治理基线与漂移审计](repository-governance.md)：检查仓库保护规则是否被意外修改。
- [生产生命周期验收记录](production-verification.md)：查看真实执行记录和验证边界。

## 三、两类需要额外学习的项目场景

这两组专题是主流程在特定项目上的扩展，不是另一套独立交付流程。

### 嵌入式与多 SoC 固件

- [多 SoC / 固件 CI 管理主线](multi-soc-and-firmware.md)：不同芯片平台如何共用管理方式、隔离工具链。
- [Hardware Runner / SDK / License / HIL](hardware-runner-integration.md)：专用构建机器、厂商 SDK、许可证和硬件实验室如何协作。
- [RK 真实物理接入手册](rk-physical-bringup.md)：瑞芯微真实设备接入前后的准备和验收。

### Jenkins 与 Java 项目

- [Java 工具链管理](java-toolchain-management.md)
- [Jenkins Java Toolchain Shared Library](jenkins-shared-library.md)

## 四、每份技术专题应该回答什么

阅读或维护专题时，围绕实际工作问题展开，不以罗列名词或代码文件为目标：

1. **为什么需要**：生产中遇到什么问题，不做会有什么影响？
2. **解决什么问题**：本方案负责什么，不负责什么？
3. **实际怎么运行**：从输入、执行过程到输出，按真实顺序说明。
4. **失败怎么办**：常见失败现象、判断依据、处理步骤和恢复验证是什么？

如果某项内容不适用，应明确说明原因，不为满足格式而硬加内容。

## 五、如何判断一项能力已经完成

必须把以下四层分开记录，不能互相替代：

| 层次 | 能证明什么 | 不能证明什么 |
|---|---|---|
| 设计目标 | 说明计划解决的问题和预期行为 | 不能证明代码已经实现 |
| 代码实现 | 仓库中存在相应逻辑、配置或集成入口 | 不能证明逻辑正确，也不能证明外部系统已接通 |
| 自动化验证 | 测试或工作流在规定条件下通过 | 不能自动证明真实生产环境运行正常 |
| 真实生产验收 | 在明确的真实环境中完成端到端验证，并保留记录 | 不能自动推广为所有项目、硬件和环境均已验收 |

当前真实验证范围和未完成边界，以[生产生命周期验收记录](production-verification.md)为准。不要把模拟场景、示例项目或控制面渲染结果写成真实生产部署。

## 六、跨流程专题：按需要深入

下面几份文档解决的是贯穿多个交付环节的问题，不属于单一阶段：

- [业务 CI 能力扩展](business-ci-capabilities.md)：质量检查、自动化测试、容器构建和数据库迁移等能力怎样以可复用方式接入，避免每个项目重复造流水线。
- [工具链供应链管理](toolchain-supply-chain.md)：JDK、编译器和厂商 SDK 如何登记、构建、扫描、批准、固定 digest，并在升级失败时回退。
- [工具链候选登记、审核晋级与部署追溯](toolchain-promotion-runbook.md)：发布后的 digest 自动登记、SBOM/签名/provenance 独立验证、漏洞例外审批，以及业务制品到环境部署的追溯。
- [可复现构建门禁](reproducibility-gate.md)：怎样通过隔离的两次构建和文件摘要比较，判断同一份源码是否得到一致产物。
- [业务仓库调用中央 CI](reusable-workflow.md)：中央平台升级后，业务仓库如何固定平台版本、分批接入和验收。

这些专题是主流程的补充。第一次学习时不必全部阅读；遇到对应问题再查阅。

## 七、遇到问题时的阅读方式

不要先从脚本或工作流文件名开始找。先确定问题发生在哪个工作环节，再读对应专题中的运行流程和失败处理；需要核对实现时，再沿专题中的配置和代码引用进入仓库实现。
