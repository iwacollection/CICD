# 工具链制品登记、审核晋级与部署追溯

本文描述工具链镜像从构建到业务使用的闭环。工具链镜像与业务应用制品是两类对象，但两者通过不可变 digest 和 Artifact Contract manifest 关联。

## 1. 工具链镜像生命周期

```text
main 构建
  │
  ├─ Buildx push -> ghcr.io/...@sha256:D
  ├─ Smoke + 语言消费样例
  ├─ Trivy 全量扫描（包括 unfixed）
  ├─ CycloneDX SBOM
  ├─ Cosign image signature
  ├─ Cosign CycloneDX attestation（subject = digest D）
  ├─ Cosign Trivy vulnerability attestation（subject = digest D）
  └─ GitHub SLSA provenance（subject = digest D）
         │
         ▼
独立验证以上证据 + 重新校验摘要
         │
         ▼
GitHub Deployment registry：toolchain-candidate/<toolchain>
         │
         ▼
workflow_dispatch: Promote Toolchain Candidate
         │
         ├─ Protected Environment: toolchain-production 人工审批
         ├─ 重新验证 image signature
         ├─ 从 OCI referrer 取回并验证 CycloneDX / vuln attestation
         ├─ 检查 predicate 的 subject digest 与登记 digest 一致
         ├─ 对取回的 SBOM / scan report 重新计算 canonical SHA256
         ├─ gh attestation verify：验证 GitHub SLSA provenance 和 signer workflow
         └─ 对精确 digest 再扫描一次，执行当前漏洞策略
                  │
                  ▼
        GitHub Deployment registry：toolchain-approved/<toolchain>
                  │
                  ▼
        自动创建 catalog Promotion PR（不自动合并）
                  │
                  ▼
        正常代码审查、合并后 status=candidate -> active
                  │
                  ▼
        业务项目只能通过 toolchain ID 消费 active digest
```

GitHub Deployments 是追加式登记记录，保留候选 digest、源码 commit、workflow run、workflow identity、Dockerfile/context/build-definition SHA256、SBOM/scan SHA256 和验证结果。相同 digest + source commit 重复登记是幂等的。

## 2. 首次启用前必须配置 GitHub Environment

仓库管理员需要在 **Settings → Environments** 创建：

- `toolchain-production`
- 配置 Required reviewers（至少一名安全/平台审批人）。
- 添加 Environment secret `TOOLCHAIN_PROMOTION_TOKEN`：使用专用 fine-grained PAT，授予本仓库 Contents（读写）、Pull requests（读写）和 Administration（只读，用于检查环境保护规则）；不要使用个人全权限 Token。
- 禁止部署分支绕过审批；仅允许 `main`。
- 如组织支持，启用 Prevent self-review。

如果没有配置 Required reviewers，GitHub Environment 不会自动产生人工审批；工作流虽然能运行，但不能据此声称具备人工晋级控制。该配置属于仓库管理权限，不由 PR 代码自动修改。

## 3. 执行候选晋级

1. 在 Toolchain Supply Chain 的 main 发布成功后，查看 `toolchain-candidate/<toolchain>` Deployment。
2. 复制登记记录中的完整 `sha256:...`，不能使用 tag。
3. 从 main 手动运行 **Promote Toolchain Candidate**，填写 toolchain ID 和完整 digest。
4. 等待 `toolchain-production` Environment 审批。
5. 工作流重新验证签名、SBOM、漏洞报告、provenance 和当前漏洞策略。
6. 成功后自动打开 catalog Promotion PR。
7. 审查 PR 中的 digest、build_context_sha256、build_definition_sha256 和 promotion_deployment_id；正常合并 PR 后，业务解析器才会把该工具链视为 active。

工作流不会自动合并 PR。失败时不会更新 catalog 状态，也不会开放业务消费。

## 4. 漏洞例外治理

默认策略扫描 HIGH / CRITICAL，包含尚无修复版本的漏洞。不要全局忽略 unfixed finding。

仅当业务确实无法立即升级，且安全负责人批准时，才可在 `ci/supply-chain-policy.json` 的 `vulnerability_exceptions` 中增加例外。每条例外必须精确绑定：

- toolchain ID；
- 镜像完整 digest；
- CVE/GHSA；
- 精确 package 名和 severity；
- 业务理由、责任人、审批人、工单；
- 已采取的补偿控制；
- 带时区的 approved_at / expires_at。

例外最长 90 天，不支持通配符，不允许只按 CVE 全局豁免。toolchain 或 digest 一变，例外立即不匹配。到期后自动失效，finding 重新阻断；需要续期必须重新审批并走 PR。Secret、许可证和镜像配置问题不受漏洞例外影响。

示意结构（字段必须填写真实信息，不能直接复制占位值）：

```json
{
  "id": "SEC-EXCEPTION-2026-0001",
  "toolchain": "go126",
  "image_digest": "sha256:<64位真实摘要>",
  "vulnerability_id": "CVE-2099-12345",
  "package": "example-package",
  "severity": "HIGH",
  "justification": "说明为何暂时不能升级",
  "owner": "真实责任团队",
  "approved_by": "真实安全审批人",
  "ticket": "SEC-1234",
  "approved_at": "2026-11-07T00:00:00Z",
  "expires_at": "2026-12-07T00:00:00Z",
  "compensating_controls": "说明隔离、访问限制和监控措施"
}
```

## 5. 业务制品从构建到部署的追溯

业务构建生成 Artifact Contract v2 manifest，记录：

- source repository / commit / workflow run；
- 工具链 ID、工具链 identity 和实际 container image digest；
- 依赖锁文件及文件 SHA256；
- bundle digest 和 bundle 内文件 SHA256；
- 构建器/Runner 元数据。

长期归档把 manifest、bundle、扫描报告、SBOM、签名 bundle 和每个 Release asset 的 SHA256 固定在同一个不可变 Release 记录中。Promotion / Rollback 下载归档原字节，重新验证 manifest、bundle、SBOM、扫描、GitHub attestations 和 Cosign blob signature，不重新构建。

环境 Deployment pointer 继续追加记录，并携带：

- bundle SHA256；
- manifest SHA256；
- source commit 和 source run；
- toolchain identity；
- container image 的不可变 digest reference；
- promotion / rollback 来源 Deployment ID。

因此可以从生产 Deployment ID 反查到 manifest，再反查源代码 commit、构建 workflow、工具链镜像 digest、依赖锁文件、SBOM、漏洞扫描、签名和 provenance。Rollback 复制历史身份，不重新生成制品。

## 6. 验证与边界

本仓库的单元测试覆盖例外的 digest/包/CVE/过期匹配、候选登记幂等性、候选身份检查和工作流门禁。GitHub Actions 会验证 YAML、脚本和 PR 可运行路径。

以下仍需要仓库管理员/真实环境完成，不能用单元测试代替：

- 在 GitHub Settings 中设置 `toolchain-production` Required reviewers；
- main 上真实推送 GHCR 镜像并验证 OCI referrers 可读；
- 执行一次人工批准的 Promotion workflow；
- 合并自动生成的 catalog PR 后验证消费者只解析 active digest；
- 在真实部署平台接入 Admission/Deploy gate，并把运行时镜像 digest 回写到部署记录。

部署平台必须拒绝 tag-only image、未登记 digest、非 active toolchain 和缺失/不匹配的证据。仅有 GitHub Deployment 记录还不等于 Kubernetes Admission 已强制执行。
