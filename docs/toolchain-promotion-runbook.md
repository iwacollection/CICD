# 工具链 Candidate 晋级操作手册

> 本文以仓库当前实现为准，说明工具链镜像如何从 Candidate 经过独立验证和人工审批，最终通过目录 PR 变成 Active。  
> 最近核对的实现：`.github/workflows/promote-toolchain.yml`，主分支提交 `2dcb37ee6ff142c6fe8cb21c9becf1429caa63d0`。  
> **重要：验证失败不会进入人工审批；人工审批通过也不会直接修改工具链目录。**

## 1. 先区分三种不同的交付对象

本仓库中有三条容易混淆、但互不替代的生命周期：

| 生命周期 | 对象 | 主要入口 | 成功代表什么 |
|---|---|---|---|
| 工具链晋级 | JDK、编译器等构建镜像 | `.github/workflows/promote-toolchain.yml` | 一个精确镜像 digest 通过证据校验和独立审批，随后产生目录 PR |
| 业务制品晋级 | JAR、二进制、固件等 Artifact Contract v2 Bundle | `.github/workflows/promote.yml` | 同一份业务制品被登记到 dev / staging / production 环境指针 |
| 运行时验证 | 已部署应用的 GitOps revision、Pod 和镜像 digest | `.github/workflows/verify-runtime-deployment.yml` | 只读确认集群实际运行内容与预期一致，不执行部署 |

工具链镜像不是业务应用镜像；GitHub Deployment 指针也不等于 Kubernetes 已经部署或流量已经切换。灰度和蓝绿策略另见 [渐进式发布操作手册](progressive-delivery.md)。

## 2. 权威配置与实现位置

| 路径 | 用途 | 操作者应如何使用 |
|---|---|---|
| `ci/toolchains.json` | 工具链唯一目录，记录 ID、状态、镜像和 digest | 判断项目是否允许消费；不要在这里手工激活 Candidate |
| `ci/toolchain-versions.json` | Java、Go、C/C++ 版本组合与选择策略 | 核对语言版本和对应 Toolchain ID |
| `ci/supply-chain-policy.json` | 漏洞、许可证、Secret、配置和证据策略 | 确认当前阻断规则和精确例外 |
| `.github/workflows/toolchain-images.yml` | 构建、发布 Candidate 并生成供应链证据 | 观察 Candidate 的生产过程 |
| `.github/workflows/promote-toolchain.yml` | 晋级前验证、独立审批、审批后复扫和创建目录 PR | **Candidate 晋级的唯一工作流入口** |
| `scripts/ci/toolchain_registry.py` | 检查 Candidate、记录审批结果 | 工作流调用，不建议人工绕过工作流直接写记录 |
| `scripts/ci/toolchain_catalog.py` | 校验目录和生成构建/消费矩阵 | 本地排查目录状态 |

业务项目只声明 Toolchain ID，不应自行填写任意镜像地址、tag 或 digest。

## 3. 完整流程：先验证，再审批，最后合并 PR

```text
Toolchain Dockerfile / 目录变更
          |
          v
Pull Request 检查
          |
          v
合并 main
          |
          v
Toolchain Supply Chain
  - 构建并发布到 GHCR
  - 得到不可变 image@sha256
  - Smoke / 消费样例
  - Trivy 扫描
  - SBOM / Cosign 签名与 Attestation
  - GitHub SLSA Provenance
          |
          v
Candidate Deployment 记录
          |
          v
手动启动 Promote Toolchain Candidate（main）
          |
          v
PRE-FLIGHT：无生产审批环境
  1. 精确读取 Candidate 和 digest
  2. 验证镜像签名
  3. 验证 SBOM Attestation 和 subject digest
  4. 验证漏洞扫描 Attestation 和 subject digest
  5. 重新计算证据 SHA256，与 Candidate 记录比对
  6. 验证 GitHub SLSA Provenance / signer workflow
  7. 对精确 digest 重新运行 Trivy
  8. 执行当前安全策略
          |
      任一失败
          +------> 结束；审批 Job 不运行
          |
       全部成功
          v
进入 toolchain-production Environment
          |
          v
独立审批人批准（Prevent self-review）
          |
          v
审批后再次读取精确 Candidate
          |
          v
Trivy 对同一 digest 再扫描 + 当前策略复核
          |
          v
写入 human-approved Candidate 记录
          |
          v
自动创建 catalog Promotion PR（不自动合并）
          |
          v
PR 正常检查与治理
          |
          v
合并后 ci/toolchains.json 才变为 active
          |
          v
业务构建才能通过 Toolchain ID 消费
```

这里有两个不同的安全时点：

- **审批前验证**：确认这份 Candidate 的来源、签名、SBOM、扫描证据和当前安全状态都可信。没有通过时，不应该把审批请求发给审批人。
- **审批后复核**：等待审批期间，安全状态可能变化，因此工作流在审批后对相同 digest 做一次新的 Trivy 扫描和策略复核。它不能替代审批前的 SBOM、Attestation 和 Provenance 校验。

## 4. 首次使用前检查 GitHub 配置

工作流依赖以下仓库配置。它不会自动替管理员创建或修改这些设置。

### 4.1 Environment：`toolchain-production`

在 GitHub 仓库 Settings → Environments 中确认：

- Environment 名称准确为 `toolchain-production`。
- 配置至少一名真正独立的 Required reviewer。
- 启用 Prevent self-review。
- 限制允许部署的分支为 `main`。
- 不配置绕过审批的例外路径。

工作流会通过 GitHub API 检查 Required reviewer 和 Prevent self-review 是否存在；读取不到或配置不满足要求时会失败关闭。GitHub Environment 的审批发生在 `promote` Job 开始执行前。

### 4.2 Environment Secret：`TOOLCHAIN_PROMOTION_TOKEN`

将专用 Fine-grained PAT 配置为 `toolchain-production` Environment Secret，名称必须是：

```text
TOOLCHAIN_PROMOTION_TOKEN
```

该 Token 用于读取 Environment 保护规则、提交审批记录以及创建目录 PR。按当前工作流调用范围授予本仓库所需的最小权限：Contents 读写、Pull requests 读写，以及读取 Environment 保护配置所需的 Administration 只读权限。不要使用个人全权限 Token，也不要把 Token 写进仓库文件或日志。

工作流其他只读步骤使用受限的 `GITHUB_TOKEN`。不要为了让某一步通过而扩大所有 Job 的权限。

### 4.3 与仓库 PR 审查规则的区别

`toolchain-production` 的 Required reviewer 是工具链晋级所需的**独立人工审批**。

自动创建的 catalog PR 则走仓库当前 Ruleset：Required Checks、PR 流程和适用的代码治理规则。当前仓库治理采用单维护者配置，不要在本手册里再要求与之冲突的“所有 PR 必须另有一名 Code Owner 审批”。这两种控制解决的问题不同：

- Environment 审批：是否允许这个工具链候选进入人工晋级阶段。
- catalog PR：是否允许把经批准的精确 digest 写成 Active 配置。

## 5. 操作者执行步骤

### 第一步：确认 Candidate 身份

从 Toolchain Supply Chain 运行记录及 `toolchain-candidate/<toolchain>` Deployment 记录中取得：

- 精确 Toolchain ID，例如 `java21-maven`；
- 完整镜像引用，例如 `ghcr.io/iwacollection/cicd-toolchain-java21-maven@sha256:...`；
- 完整 digest，格式必须为 `sha256:` 加 64 位十六进制摘要；
- 对应的 Candidate 来源提交和发布运行记录。

不要从 tag 推测 digest，不要把另一个 Toolchain ID 的 digest 复制过来。目录状态、Candidate Deployment 记录和镜像地址必须指向同一个对象。

### 第二步：从 main 手动运行工作流

打开：

[GitHub Actions：Promote Toolchain Candidate](https://github.com/iwacollection/CICD/actions/workflows/promote-toolchain.yml)

点击 **Run workflow**，分支选择 `main`，填写两个输入：

| 输入 | 示例 |
|---|---|
| `toolchain` | `java21-maven` |
| `digest` | `sha256:8f771c4a767d85a9500543a601f36ef94140b34325a0ff2c778d9dd34c2805d6` |

以上 Java 21 digest 是 2026-11-07 本次排查中看到的 Candidate 示例，不代表它已经 Active。正式执行前，仍须以最新 Candidate Deployment 记录为准。

工作流只允许从 `refs/heads/main` 运行。不要在功能分支上启动有晋级权限的工作流。

### 第三步：先看 `Verify candidate before production approval`

此 Job 不绑定 `toolchain-production` Environment，因此先执行全部审批前验证。重点看以下步骤是否全部是 `success`：

| 步骤 | 它在确认什么 |
|---|---|
| Load exact registered candidate | Toolchain ID、Candidate 状态、输入 digest 和不可变镜像引用一致 |
| Independently verify image signature | 镜像签名由指定仓库发布工作流身份签发 |
| Independently verify digest-bound CycloneDX SBOM | SBOM Attestation 可验证，subject 指向请求的镜像 digest |
| Vulnerability Attestation subject / evidence hash | 扫描证明绑定同一 digest，报告内容摘要与 Candidate 记录一致 |
| Independently verify GitHub SLSA provenance | 来源证明来自指定仓库、指定发布工作流和 main 来源 |
| Re-scan exact digest at promotion time | 对不可变镜像引用运行当前版本 Trivy |
| Enforce current scan and verified SBOM policy | 用仓库当前策略检查 SBOM、漏洞、许可证、Secret 和配置风险 |

**只要这个 Job 失败，审批 Job 就应该是 `skipped`。** `skipped` 不代表验证通过；它表示前置条件没有满足，后续步骤没有执行。

### 第四步：只有 Pre-flight 全部成功后才做独立审批

Pre-flight 成功后，GitHub 才会把工作流推进到 `Approve and open promotion PR` Job。该 Job 使用 `toolchain-production` Environment，等待 Required reviewer 独立审批。

审批人应核对 Toolchain ID、完整 digest、来源记录和验证运行结果。审批人不是在批准“一个 Java 版本”的抽象概念，而是在批准**这一份精确镜像 digest**。

### 第五步：审批后复核并创建目录 PR

审批完成后，工作流会：

1. 再次读取 Candidate，确认 Toolchain ID 和 digest 没有变化；
2. 对同一个 immutable digest 运行新的 Trivy 扫描；
3. 使用当前安全策略检查新扫描报告；
4. 记录 human-approved Candidate；
5. 创建 catalog Promotion PR。

该工作流**不会自动合并**目录 PR。检查 PR 中的 Toolchain ID、digest、build context 指纹、build definition 指纹和 approval Deployment ID。PR 的必需检查通过后，按仓库正常治理流程审查和合并。

只有合并后的 `ci/toolchains.json` 状态为 `active`，且容器工具链 digest 完整固定，业务项目才可以正式消费。

## 6. 失败时如何排查

| 失败位置 | 代表什么 | 应如何处理 |
|---|---|---|
| Candidate inspect | ID、状态、登记记录或 digest 不匹配 | 核对 Candidate Deployment 和目录；不要换用 tag 或其他 digest |
| Image signature | 镜像签名身份、签名内容或证书校验失败 | 检查发布工作流身份、镜像 digest 和 OCI 签名引用 |
| SBOM / vulnerability Attestation | Attestation 缺失、签名不可信或 subject 不匹配 | 检查发布产物和 Attestation；修复后生成新的可信证据 |
| Evidence SHA256 mismatch | 解出的 predicate 经规范化后，其内容摘要与 Candidate 登记值不一致 | 对照原始 Attestation predicate、登记字段和 canonical JSON 规则；不要跳过摘要检查 |
| SLSA provenance | 来源仓库、分支、签发工作流或制品身份不匹配 | 核对来源运行及 signer workflow，不能把其他工作流的证明替代进来 |
| Trivy 扫描失败 | 扫描器无法扫描或精确镜像无法读取 | 检查 GHCR 拉取、扫描器执行和镜像可用性 |
| Security policy failure | 当前漏洞、许可证、Secret 或配置策略不允许通过 | 修复镜像或走精确、限期、有审批的例外流程；不要全局忽略 |
| Environment approval | Required reviewer 或 Prevent self-review 配置不满足 | 修正 Environment 设置和只读检查凭据，再重新运行 |
| Post-approval scan | 审批等待期间安全状态变化或扫描失败 | 停止晋级，重新评估候选；不能因为人工已批准就继续放行 |

**不要把不同失败类型混为一谈。** 例如“SBOM subject digest 已匹配”不代表“SBOM 内容哈希已匹配”；“Attestation 存在”也不代表“来源证明通过”；“Trivy 扫描步骤执行”不代表“当前策略允许发布”。

## 7. 2026-11-07 Java 21 当前验收状态

以下是可追溯的真实运行结果，不是设计目标：

| 项目 | 当前结果 |
|---|---|
| Toolchain ID | `java21-maven` |
| Candidate 镜像 digest | `sha256:8f771c4a767d85a9500543a601f36ef94140b34325a0ff2c778d9dd34c2805d6` |
| 运行 | [Promote Toolchain Candidate #42336243998](https://github.com/iwacollection/CICD/actions/runs/42336243998) |
| 工作流提交 | `2dcb37ee6ff142c6fe8cb21c9becf1429caa63d0` |
| 结果 | `failure`，未进入独立审批 |

该次运行确认：

- Candidate 身份检查成功；
- 镜像签名验证成功；
- SBOM 和漏洞扫描 Attestation 的 subject digest 比对已通过；
- 随后的 `verified-scan.json` canonical SHA256 与 Candidate 登记的扫描报告摘要不一致，Pre-flight 因此失败；
- SLSA Provenance 步骤、晋级时的新 Trivy 扫描、安全策略复核均为 `skipped`；
- `Approve and open promotion PR` Job 为 `skipped`，没有发生独立审批，也没有产生 Active 目录 PR。

因此当前准确结论是：**Java 21 Candidate 已有精确镜像和签名/Attestation 证据，但晋级验证未通过；不能称为 SBOM、Provenance 和安全扫描全部通过，更不能称为 Active。** 本次失败不是 Trivy 报告发现了 HIGH/CRITICAL 漏洞，而是候选登记的扫描报告摘要与取回的 Attestation predicate 内容摘要不一致。差异根因尚需单独定位，本文不推测原因。

此前 PR #59 修复的是 `sha256:<hex>` 与 in-toto subject `<hex>` 的格式比较问题，并已合并到上述工作流提交。它解决了 subject 格式比较，但没有自动证明后续 evidence hash、SLSA 和新扫描步骤成功。

当前目录状态仍以 `ci/toolchains.json` 为准：`java21-maven` 为 `candidate`，不是 `active`。

## 8. 例外与回滚原则

漏洞例外只能精确绑定 Toolchain ID、镜像 digest、漏洞 ID、包名、严重性、责任人、审批工单和有效期。修改例外必须走受保护 PR，并遵循 [供应链策略](supply-chain-policy.md)。不要为了让一次晋级通过而：

- 关闭 SBOM / Attestation / Provenance 检查；
- 跳过 digest 或 evidence hash 比对；
- 允许 `latest` 或可变 tag；
- 把 Candidate 直接改成 Active；
- 取消 Environment 独立审批；
- 对失败的扫描报告使用全局忽略；
- 自动合并 catalog PR。

工具链升级应产生新的 Candidate digest。发生问题时，恢复到上一个已批准的 Active digest，并通过 PR 修改中央目录；不要覆盖旧镜像或重新构建历史镜像冒充旧版本。

## 9. 验收完成标准

一项工具链只有同时满足以下条件，才可以写成“已晋级”：

- [ ] Candidate 记录与不可变镜像 digest 一致；
- [ ] 镜像签名验证通过；
- [ ] SBOM Attestation 的签名、subject 和内容摘要全部通过；
- [ ] 漏洞扫描 Attestation 的签名、subject 和内容摘要全部通过；
- [ ] GitHub SLSA Provenance 来源验证通过；
- [ ] 晋级前的精确 digest Trivy 扫描通过当前安全策略；
- [ ] 独立 Environment 审批已经完成；
- [ ] 审批后的精确 digest 新扫描和策略复核通过；
- [ ] human-approved Candidate 记录成功；
- [ ] catalog Promotion PR 正常通过检查并合并；
- [ ] `ci/toolchains.json` 中状态为 `active`，digest 与批准记录一致；
- [ ] 代表性业务消费者使用该 Toolchain ID 构建成功。

任何一项未完成，都应标注为 Candidate、验证中或失败，不能用“工作流启动成功”“PR 已合并”或“审批已点击”代替整个晋级验收。
