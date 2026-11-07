# 技术文档审核记录与维护基线

> 审核日期：2026-11-07  
> 审核基线：`main`，提交 `2dcb37ee6ff142c6fe8cb21c9becf1429caa63d0`  
> 审核目标：让技术文档与仓库实现一致，读者能区分设计、代码、自动化验证和真实外部环境验收，并能按步骤执行关键操作。

## 1. 本次审核结论

仓库已有较完整的技术专题，但主要问题不是“文档数量不足”，而是少数关键操作文档存在状态过期或流程顺序与实现不一致。此次重点修正了入口文档、工具链晋级手册、Java 当前状态和历史验收说明。

本次未将文档范围扩大成无依据的“生产能力已完成”声明，也没有修改工作流代码或安全门禁。

## 2. 发现的问题与处理

| 位置 | 发现 | 风险 | 本次处理 |
|---|---|---|---|
| 根目录 `README.md` | 快速开始标题前存在被写成字面字符的 `\\n` | 首页末尾显示异常，入口不自然 | 改为正常 Markdown 标题和段落 |
| `docs/toolchain-promotion-runbook.md` | 旧顺序将 Environment 审批放在 SBOM、Provenance 和安全扫描前 | 操作者可能先触发审批，再发现证据根本不完整 | 按当前工作流重写：审批前 Pre-flight，审批后复扫，最后创建 catalog PR |
| `docs/toolchain-promotion-runbook.md` | 将独立 Environment 审批与仓库 PR Code Owner 审查混为一谈 | 与仓库单维护者 Ruleset 现状冲突 | 明确区分两个控制点及其职责 |
| `docs/java-toolchain-management.md` | 现状表仍沿用较早的 Java 17 Candidate 已固定 digest 状态 | 与当前 `ci/toolchains.json` 中 digest 为空的状态不一致 | 更新当前状态表，保留并标记历史记录 |
| `docs/production-verification.md` | 文档记录的是 2026-08-30 生命周期验收，容易被误读为当前实时状态 | 历史验收不能代表当前每条工作流都通过 | 明确标记历史基线，不覆盖历史运行记录 |
| `docs/README.md` / `docs/START-HERE.md` | 主流程导航未把工具链、业务制品、Kubernetes 运行时验证三条生命周期充分区分 | 容易把“晋级”“部署”“流量切换”混为一个成功状态 | 补充独立生命周期说明和当前状态入口 |

## 3. 本次对照的实现范围

重点核对以下配置与实现路径：

| 主题 | 对照的仓库事实 |
|---|---|
| 工具链目录 | `ci/toolchains.json`、`ci/toolchain-versions.json`、`ci/supply-chain-policy.json` |
| Candidate 发布与晋级 | `.github/workflows/toolchain-images.yml`、`.github/workflows/promote-toolchain.yml`、`scripts/ci/toolchain_registry.py` |
| 业务制品生命周期 | `.github/workflows/archive-artifacts.yml`、`.github/workflows/promote.yml`、`.github/workflows/rollback.yml` |
| 渐进式发布 | `.github/workflows/release.yml`、`scripts/ci/release_strategy.py`、`ci/release-strategies.json` |
| 运行时验证 | `.github/workflows/verify-runtime-deployment.yml`、`scripts/ci/verify_runtime_deployment.py` |
| 项目和 DAG 构建 | `ci/projects.json`、`.github/workflows/ci.yml`、`.github/workflows/dag-node.yml` |
| Jenkins 集成 | `vars/javaToolchainBuild.groovy`、`src/com/iwacollection/cicd/`、`docs/jenkins-shared-library.md` |
| 硬件执行面 | `ci/hardware-profiles.json`、`ci/hardware-rollout.json`、`ops/rk-runner/` |

## 4. 必须始终区分的四层证据

| 证据层 | 能证明 | 不能证明 |
|---|---|---|
| 设计文档 | 目标架构、组件职责、预期流程 | 代码已经实现 |
| 仓库实现 | 有配置、脚本、Workflow 或集成入口 | 行为已正确运行 |
| 自动化验证 | 对应 SHA 的测试/Workflow 在特定条件下通过 | 外部 Jenkins、Harbor、Kubernetes 或真实硬件已接通 |
| 真实环境验收 | 指定外部环境、版本、账号和资源完成端到端验证 | 其他环境或其他版本自动通过 |

示例项目、Mock Pipeline、synthetic analysis、期望状态渲染和只读验证，不应写成真实业务发布或真实生产流量切换。

## 5. 当前关键状态快照

以下状态来自审核基线提交和对应运行记录。状态变化后，应更新权威配置或新增验收记录，而不是只修改文档中的口头描述。

| 能力 | 2026-11-07 可确认状态 |
|---|---|
| GCC 13 容器工具链 | `gcc-host-container-v1` 为 Active，目录固定 digest |
| Java 17 | `java17-maven` 仍为 Candidate；当前目录 digest 为空，不是 Active |
| Java 21 | `java21-maven` 为 Candidate；本次看到的 Candidate digest 为 `sha256:8f771c4a767d85a9500543a601f36ef94140b34325a0ff2c778d9dd34c2805d6`；最新晋级运行未通过 evidence hash 校验 |
| Go 1.26 | `go126` 为 Candidate，是否可被业务消费仍以中央目录为准 |
| GCC 12 | `gcc12` 为 Candidate，不应写成 Active |
| 企业 Harbor | 当前已验证发布链路使用 GHCR；未据此认定企业 Harbor API、HA 和灾备已验收 |
| 企业 Jenkins | Shared Library 代码和 Mock Pipeline 验证不等于真实 Jenkins Controller/Agent 接入 |
| Argo CD / Kubernetes | 仓库有接入和只读运行验证工作流；没有据此声称真实生产集群已经接入 |
| 多 SoC 真机 | RK / Qualcomm / MediaTek 的配置与接入设计不等于真实 SDK、License、Runner 和 HIL 验收 |

### Java 21 最新失败记录

- 工作流：[Promote Toolchain Candidate #42336243998](https://github.com/iwacollection/CICD/actions/runs/42336243998)
- 工作流代码提交：`2dcb37ee6ff142c6fe8cb21c9becf1429caa63d0`
- 镜像签名验证成功；SBOM 和漏洞扫描 Attestation 的 subject digest 比对已通过。
- 失败点：`verified-scan.json digest does not match registered candidate evidence`。
- SLSA Provenance、晋级前的新 Trivy 扫描和安全策略复核均未执行（skipped）；独立审批 Job 也未进入。
- 该结果表示证据摘要交叉校验失败，不表示 Trivy 已报告漏洞，也不表示后续门禁通过。

## 6. 文档职责与唯一事实来源

| 文档 | 主要职责 | 不重复维护 |
|---|---|---|
| `README.md` | 平台一句话定位、范围、起点 | 详细架构和执行步骤 |
| `docs/README.md` | 按工作流程导航 | 每个专题的完整说明 |
| `docs/START-HERE.md` | 初学者学习顺序和基本概念 | 运维操作细节 |
| `docs/architecture.md` | 总体分层、配置面/执行面、控制链边界 | Jenkins、Toolchain、Argo 的完整实施手册 |
| `docs/toolchain-supply-chain.md` | 工具链目录、构建与 Candidate/Active 生命周期 | 逐步执行晋级审批 |
| `docs/toolchain-promotion-runbook.md` | Candidate 晋级的实际操作和失败处理 | 业务 Artifact 晋级 |
| `docs/java-toolchain-management.md` | Java 工具链规格、JDK/Maven、Java 项目接入 | Shared Library 每个参数的操作说明 |
| `docs/jenkins-shared-library.md` | Jenkins Controller/Agent、凭据、调用和验收 | 企业 Harbor 部署方案 |
| `docs/artifacts-promotion-and-rollback.md` | 业务制品归档、环境指针和回滚 | Kubernetes 流量切换 |
| `docs/progressive-delivery.md` | release strategy、权重/槽位、区域顺序和状态操作 | 工具链 Candidate 晋级 |
| `docs/argocd-kubernetes-runtime-verification.md` | GitOps revision、Pod Ready 和运行镜像 digest 的只读验证 | 执行 Argo CD Sync 或业务部署 |
| `docs/production-verification.md` | 保留已完成的历史生命周期验收证据 | 实时状态看板 |

规则、阈值、工具链版本和环境清单应优先引用对应的 JSON 配置或脚本；文档只解释其含义和操作方法，避免出现第二套可漂移的数据源。

## 7. 其他文档中必须保留的边界

以下既有技术专题应继续遵守各自已经说明的限制，不能为了让描述更“完整”而扩大能力范围：

- `progressive-delivery.md`：策略引擎生成并校验期望状态；synthetic scenario 不是 Prometheus 生产证据；没有外部控制器执行证据时，不得写成已切流。
- `argocd-argo-rollouts-integration.md`：接入规范不代表 Argo CD、Rollouts、Gateway API 或 Prometheus 已真实连通。
- `argocd-kubernetes-runtime-verification.md`：当前工作流是只读验证，不执行 Sync、Patch、重启或回滚。
- `hardware-runner-integration.md` / `multi-soc-and-firmware.md`：硬件 Profile 和示例 Inventory 不等于实际 Runner、SDK、License 和 HIL 资源已上线。
- `jenkins-shared-library.md`：Mock Pipeline 测试不等于真实企业 Jenkins Controller/Agent 验收。

## 8. 后续文档变更检查清单

新增或修改技术文档时，逐项检查：

- [ ] 说明面向谁、解决什么问题，以及明确的非目标。
- [ ] 按实际运行顺序解释输入、执行、输出和关键状态。
- [ ] 所有命令、文件路径、Workflow 名称和参数都能在当前仓库找到。
- [ ] 配置的示例值明确标注为真实值、示例值或占位值。
- [ ] 失败场景写明日志/指标证据、止血动作、恢复步骤和恢复验证。
- [ ] 标注外部依赖、凭据权限、网络前提和人工审批点。
- [ ] 区分代码实现、自动化测试和真实环境验收。
- [ ] 每种流程只保留一份主操作手册，其他文档使用链接。
- [ ] 状态、阈值和版本优先引用中央 JSON / 实际脚本，不复制成第二套事实源。
- [ ] 文档变更经过 Markdown 链接、路径、命令和对应自动化检查。

**审核原则：文档写得全面，不等于把规划写成已实现；文档写得专业，也不等于堆砌术语。每个操作都必须能找到真实入口、可信证据和失败后的安全停止点。**
