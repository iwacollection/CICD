# 企业级工具链供应链

## 目标

业务项目不得现场构建 SDK/编译器镜像，也不得自行填写容器镜像地址。项目只声明 `toolchain` ID；中央 `ci/toolchains.json` 决定工具链状态、执行方式、镜像仓库与不可变 digest。

核心原则：

1. **构建环境与业务代码解耦**：工具链由独立流水线生产，业务流水线只消费。
2. **不可变引用**：Active 容器工具链必须使用 `image@sha256:<64位摘要>`。
3. **先验证后发布**：PR 只构建和冒烟；合并 main 后才允许发布 GHCR。
4. **发布不等于可用**：新镜像先作为 Candidate 发布，拿到真实 digest 后再通过 PR 晋升为 Active。
5. **最小权限**：验证任务只有 contents:read；只有 main 的 Publish Job 获得 packages/id-token/attestations 写权限。
6. **可追溯**：每个发布镜像必须有源提交、Dockerfile SHA256、镜像 digest、SBOM、构建来源证明和 promotion record。
7. **缓存绑定工具链身份**：业务缓存键必须包含 toolchain digest，SDK 变化自动隔离旧缓存。

## 状态机

```text
Planned
   |
   | 工具链定义/SDK准备
   v
Candidate
   |
   | PR Verify: build + smoke test
   | main Publish: push + SBOM + provenance
   v
Candidate 审核工作流
   |
   | Pre-flight：签名 / SBOM / 扫描证明 / 摘要 / SLSA / Trivy / 策略
   | 全部成功后才进入独立 Environment 审批
   v
审批后复扫 + human-approved 记录
   |
   | 自动创建 catalog Promotion PR（不自动合并）
   v
PR 检查与正常合并
   |
   | ci/toolchains.json 状态更新
   v
Active
   |
   | 业务 Matrix 才允许消费
   v
Retired
```

`planned`：仅规划，不允许启用项目消费。

`candidate`：可以由工具链流水线构建/验证/发布，但业务项目仍禁止消费。

`active`：生产可用。容器模式必须存在完整 sha256 digest。

`retired`：停止新构建使用；历史制品仍通过 manifest/digest 保持可追溯。

## 正常变更流程

```text
修改 docker/toolchains/<name>/Dockerfile 或 ci/toolchains.json
        |
        v
Pull Request
        |
        +--> Toolchain Verify
        |      - catalog 校验
        |      - Buildx 构建
        |      - smoke test
        |      - Dockerfile 身份校验
        |
        +--> Platform Validate
               - 项目目录校验
               - 工具链引用校验
               - Matrix 渲染
               - 单元测试
        |
        v
Merge main
        |
        v
Toolchain Publish
        - GHCR push
        - pull 并冒烟测试刚发布的精确 image@sha256
        - OCI SBOM
        - GitHub provenance attestation
        - promotion-<toolchain>.json
        |
        v
取得真实 image@sha256
        |
        v
Promotion PR
        - candidate -> active
        - 写入 digest
        |
        v
业务 CI 才可引用
```

## 多 SoC 管理

RK、Qualcomm、MediaTek 不把几十 GB SDK 直接塞进业务仓库。每一种 SDK 都作为独立 toolchain ID 管理，例如：

```text
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

项目只声明：

```json
"toolchain": "rk-sdk-2026.08"
```

不允许项目出现：

```json
"container_image": "...",
"container_dockerfile": "...",
"execution_mode": "container"
```

这样升级 SDK 时只改中央工具链目录，通过影响分析触发安全的 Full Lane；不会出现 A 项目用旧 SDK、B 项目偷偷用新 SDK、C 项目使用 latest 的配置漂移。

## Runner 与大 SDK

真正的 RK/QCOM/MTK SDK 很大时，下一层优化不是取消 digest，而是让受控 Self-hosted Runner 预热 Active digest：

```text
中央 Toolchain Catalog
        |
        v
Runner Image Prewarm
        |
        +-- runner-rk-*   预拉 RK active digest
        +-- runner-qcom-* 预拉 QCOM active digest
        +-- runner-mtk-*  预拉 MTK active digest
        |
        v
业务 Fast Lane
基本不再现场下载/构建 SDK
```

预热只是缓存优化，业务仍必须引用 `image@sha256`，不能退回 tag/latest。

## Candidate 构建矩阵与 Active 不可变性

`toolchain_catalog.py --matrix` 只输出状态为 `candidate` 的容器工具链。

- `planned`：仅登记，不构建、不消费。
- `candidate`：由 Toolchain Supply Chain 工作流构建、冒烟、扫描、生成 SBOM 和来源证明；main 发布得到真实 digest 后仍需 Promotion PR。
- `active`：已经批准的不可变镜像身份。新工具链的 PR 不能触发 Active 镜像重建或重新发布，也不能把 Active 的 digest 静默替换为新构建结果。
- `retired`：不参与构建和消费，历史 digest 保留用于审计和恢复。

这样可以避免一个新 Candidate 的提交触发全仓库 Active 镜像重建，也避免某个旧 Active 镜像的当前漏洞扫描结果阻塞无关的 Candidate 构建。Candidate 的 digest 为空表示尚待首次发布或经审批请求重建；发布成功后，流水线会自动将真实 digest 和完整 build context SHA256 登记到 GitHub Deployment registry。随后由 `.github/workflows/promote-toolchain.yml` 执行独立晋级：先在 Pre-flight 中核验镜像签名、SBOM 与漏洞扫描 Attestation、subject digest、证据内容摘要、GitHub SLSA Provenance、精确 digest 的新 Trivy 扫描和当前策略；Pre-flight 全部成功后，才进入 `toolchain-production` Environment 等待独立审批。审批后再次读取同一个 digest、重新扫描并通过当前策略，才记录 human-approved Candidate 并自动创建 catalog Promotion PR。该 PR 不会自动合并；只有 PR 按仓库规则合并后，`ci/toolchains.json` 才更新为 Active。不要把 Environment 审批当作 catalog PR 审查，也不要把 catalog PR 合并当作真实 Jenkins / Kubernetes 部署验收。后续矩阵会跳过已有 digest 的 Candidate，但在跳过之前会重新计算 Docker build context 的目录与文件内容摘要；只要 Dockerfile、上下文文件新增/删除/修改，或镜像地址、Dockerfile 路径、平台、smoke command 等目录定义字段变化，摘要/构建定义指纹就会不匹配并让校验失败，而不是静默跳过。需要重新构建时，必须通过 PR 显式清空该 Candidate digest，再更新输入并重新走构建、扫描、签名和发布流程。

Active 镜像的持续漏洞复扫属于独立的 Registry/安全运营任务，应按 Active digest 扫描并触发准入阻断或升级流程；不能通过重新构建 Active 镜像来代替复扫。

## 回滚

工具链回滚不重新构建旧 SDK。直接把中央目录中的 Active digest 回退到上一个已验证 digest，并重新走 PR + Full Lane。

因此回滚对象是：

```text
Toolchain ID + immutable digest
```

而不是“重新打一个相同 tag”。


---

## 操作者手册：一次工具链变更如何安全进入业务构建

本节把上面的状态机落到当前仓库的真实入口。工具链目录是唯一登记来源；业务项目不能绕过它直接指定镜像地址。

### 1. 先确认当前登记状态

在仓库根目录执行：

```bash
python3 scripts/ci/toolchain_catalog.py
python3 scripts/ci/supply_chain_policy.py
```

第一条检查 `ci/toolchains.json` 的结构和工具链引用；第二条检查供应链策略。任一命令失败，都先修复目录或策略问题，不要继续构建镜像。

查看待构建 Candidate 矩阵：

```bash
python3 scripts/ci/toolchain_catalog.py --matrix
```

查看已有不可变 digest、并带消费者样例的 Candidate/Active 测试矩阵：

```bash
python3 scripts/ci/toolchain_catalog.py --consumer-matrix
```

注意：`--matrix` 只选择需要构建的 Candidate；`--consumer-matrix` 是消费者测试，不是把 Candidate 自动提升为 Active。

### 2. Candidate 到 Active 的正确顺序

```text
修改 Dockerfile / build context / toolchain catalog
  -> Pull Request
  -> Toolchain Verify + Platform Validate
  -> 合并 main
  -> Toolchain Supply Chain 构建并发布 Candidate
  -> 登记精确 image@sha256、SBOM、扫描证明和来源证明
  -> 手动启动 Promote Toolchain Candidate（main）
  -> Pre-flight：验镜像签名、SBOM / vuln Attestation、证据摘要、SLSA、Trivy 和当前策略
  -> Pre-flight 全部成功后，才进入 toolchain-production 独立审批
  -> 审批后重新读取同一个 Candidate digest，再跑 Trivy 和当前策略
  -> 写入 human-approved Candidate 记录
  -> 自动创建 catalog Promotion PR（不自动合并）
  -> PR 正常检查和合并
  -> ci/toolchains.json 状态才变为 active
  -> 业务构建引用 Toolchain ID
```

**审批顺序不能反过来：Pre-flight 必须在独立人工审批之前完成。** Pre-flight 任一门禁失败时，审批 Job 应为 skipped；skipped 不代表通过。审批后还要对精确 digest 做新的 Trivy 扫描，避免审批等待期间安全状态变化。详细输入、操作、失败判读和当前 Java 21 状态以[工具链 Candidate 晋级操作手册](toolchain-promotion-runbook.md)为准。

不要在 PR 阶段给业务项目开放 Candidate 消费权限。也不要因为镜像 push 成功、签名存在或审批人点击通过，就把它称为 Active。

### 3. 发布后必须核对什么

至少保留以下证据并相互对应：

- Toolchain ID 与 catalog 中的镜像仓库；
- Dockerfile 和完整 build context 的内容指纹；
- 发布后的镜像 digest，而不是 tag；
- 镜像冒烟测试结果；
- 漏洞/许可证扫描报告；
- SBOM；
- GitHub provenance attestation；
- Promotion PR 的审批和合并记录。

如果 digest、build context 或 smoke command 不一致，停止 Promotion。不要复用旧 digest 冒充新输入的构建结果。

### 4. 常见失败与恢复

| 现象 | 证据与判断 | 处理 | 恢复验证 |
|---|---|---|---|
| catalog 校验失败 | `toolchain_catalog.py` 错误信息 | 修正 ID、状态、镜像、Dockerfile/context 路径 | catalog 和 supply-chain policy 两条命令均成功 |
| Candidate 没进入构建矩阵 | `--matrix` 输出为空或数量为 0 | 检查状态是否为 candidate、digest 是否已存在、PR 修改范围是否触发工作流 | 矩阵只包含预期的待构建 Candidate |
| 镜像构建成功但 smoke 失败 | Docker build 与 smoke step 日志 | 修复镜像内工具路径、版本或启动依赖 | 对新 Candidate digest 再次执行 smoke |
| 扫描发现阻断漏洞/许可证 | Trivy JSON 报告与策略输出 | 修复基础镜像/软件包或按组织流程处理例外 | 新镜像重新扫描并通过策略门禁 |
| 发布后 digest 未写回 catalog | GHCR 发布记录、promotion JSON、catalog diff | 通过 Promotion PR 写入实际 digest | catalog 校验通过，消费者矩阵解析到精确 digest |
| Active 工具链损坏或出现严重漏洞 | Active digest、复扫报告、受影响项目清单 | 先阻断新消费；回退到上一个已验证 digest，或按 Candidate 流程发布修复版本 | Full Lane 和消费者构建通过，Manifest 记录回退后的 identity |

### 5. 回滚不是重新构建

回滚应将 Toolchain ID 指向之前保存并验证过的 digest，再通过 PR 和完整验证流程。不要给旧版本重新打一个相同 tag，也不要在业务项目中临时覆盖镜像地址。

工具链回退后，旧、新工具链 identity 不同，缓存必须自然隔离；业务制品也必须记录实际使用的工具链身份。


## 发布后的自动登记与人工晋级

详细步骤、例外字段和 GitHub Environment 管理要求见[工具链候选登记、审核晋级与部署追溯](toolchain-promotion-runbook.md)。GitHub Deployment 记录是候选/批准审计历史；`ci/toolchains.json` 仍是项目消费状态的声明来源，只有 Promotion PR 合并后才会变为 active。
