# 仓库治理基线

当前代码层已经采用 PR + CI 的企业工作流，但仓库设置也必须阻止绕过。主分支应启用 GitHub Ruleset 或 Branch Protection。

## main 必须满足

- 禁止直接 push main，所有变更通过 Pull Request。
- 至少 1 名审批人；工具链、CI 平台、安全目录建议 2 名审批人。
- 新提交后旧审批失效，防止审批后偷偷追加提交。
- 合并前必须解决所有 Review Thread。
- 必须通过状态检查：`Platform Validate / Validate CI platform`、`Build Matrix / Discover impacted build matrix`，工具链相关变更还必须通过 `Toolchain Supply Chain / Verify`。
- 禁止强制推送与删除 main。
- 管理员也应遵守规则，紧急绕过必须留下审计记录。
- 建议启用签名提交或 GitHub vigilant mode；发布 tag 使用受保护规则。

## CODEOWNERS

`.github/CODEOWNERS` 负责声明关键目录责任人。真正强制 CODEOWNERS 审批需要配合 Ruleset 的“Require review from Code Owners”。

## 权限模型

业务 CI 默认只有 `contents: read`。只有确实需要写入的 Job 单独提升权限：

- Toolchain Publish：`packages: write`、`id-token: write`、`attestations: write`。
- 业务制品来源证明：`id-token: write`、`attestations: write`。
- PR 验证任务不得拥有 packages 写权限。

不要在仓库 Secrets 中保存云平台长期 Access Key；后续云发布统一使用 OIDC/Federated Identity 短期身份。

## 紧急变更

紧急事故允许走专用 Break-glass 流程，但不能通过关闭分支保护完成。推荐做法是：

```text
Incident
  -> Emergency PR
  -> 最小变更
  -> Required CI
  -> 指定审批人
  -> Merge
  -> 事后 Review / RCA
```

任何紧急绕过都必须记录事故编号、操作者、原因、开始/结束时间和后续修复项。
