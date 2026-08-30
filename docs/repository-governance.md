# 仓库治理基线与漂移审计

CI 代码正确并不代表 GitHub Repository Settings 一定正确。Required Check、PR 规则、force-push 和 deletion 都属于生产控制面，所以需要：

```text
Versioned Policy
      +
Live Ruleset Audit
```

## 1. 当前仓库采用单维护者治理模式

当前仓库只有一个长期维护者，因此不使用“作者必须找另一位 Code Owner 审批自己 PR”的多人治理规则。

目标 Ruleset：

```text
main-production-governance
```

单维护者模式要求：

```text
main 必须通过 PR                 = true
required approving reviews      = 0
require Code Owner review       = false
resolve review threads          = true
dismiss stale reviews on push   = true
extra approval for unattributed = true
branch deletion                 = forbidden
non-fast-forward / force-push   = forbidden
bypass actors                   = none
```

Required Checks 继续强制：

```text
Validate CI platform
Build gate
Toolchain gate
```

所以取消人工 Approval 并不等于允许直接修改 `main`。正常路径仍然是：

```text
feature branch
      ↓
Pull Request
      ↓
Validate CI platform
      ↓
Build gate
      ↓
Toolchain gate
      ↓
Review threads resolved
      ↓
Merge
```

## 2. 为什么不是 approvals=1 + CODEOWNERS=true

当前 `.github/CODEOWNERS` 的 owner 是仓库维护者本人。如果：

```text
PR 作者 = 唯一 Code Owner
```

同时 Ruleset 又要求：

```text
approval >= 1
require Code Owner review = true
```

GitHub 不允许 PR 作者批准自己的 PR，于是会形成结构性死锁：CI 全绿也无法合并。

因此当前阶段采用单维护者策略。未来如果仓库变成多人维护，可以再通过单独治理 PR 升级为：

```text
approvals >= 1
require Code Owner review = true
```

但必须先确保存在至少两个真实维护身份。

## 3. 期望状态代码化

仓库治理策略保存在：

```text
ci/repository-governance-policy.json
```

当前 schema v2 对 Pull Request 关键参数采用**显式期望值**，不是简单的“越严格越好”。原因是审批数量和 Code Owner Review 在单维护者仓库里过度收紧会让仓库不可操作。

当前 Pull Request 期望：

```json
{
  "required_approving_review_count": 0,
  "dismiss_stale_reviews_on_push": true,
  "require_code_owner_review": false,
  "required_review_thread_resolution": true,
  "require_extra_approval_for_unattributed_changes": true
}
```

Required Status Checks 仍按最小集合验证：以后可以新增 `Security gate`，但以下三个不能删除：

```text
Validate CI platform
Build gate
Toolchain gate
```

## 4. 自动漂移审计

实现：

```text
.github/workflows/repository-governance.yml
        ↓
scripts/ci/repository_governance.py
        ↓
GitHub Rulesets API
        ↓
compare live state vs versioned policy
```

`Repository Governance` 定时运行，也支持手工触发。

它会生成：

```text
repository-governance.json
repository-governance.md
```

并保留审计证据。

## 5. 当前哪些漂移会失败

包括：

```text
Ruleset 缺失或 inactive
~DEFAULT_BRANCH selector 被移除
branch deletion 保护被删除
non-fast-forward 保护被删除
approval count != 0
require_code_owner_review != false
review thread resolution 被关闭
stale review invalidation 被关闭
extra approval for unattributed changes 被关闭
strict required status checks 被关闭
Validate CI platform 被删除
Build gate 被删除
Toolchain gate 被删除
可见的 bypass actor 被增加
```

这里需要特别理解：

```text
approval 从 0 改成 1
```

在团队仓库可能叫“加强”，但在当前单维护者仓库会重新制造合并死锁，所以被视为 governance drift。

## 6. CODEOWNERS 仍然保留

`.github/CODEOWNERS` 仍用于声明关键目录的责任人，例如：

```text
/.github/workflows/
/ci/
/scripts/ci/
/tests/
/docker/toolchains/
```

只是当前 Ruleset 不要求 Code Owner 必须执行 Approval。

这样保留了：

```text
ownership metadata
```

但不会产生单人 Self-Approval 死锁。

未来多人维护时可以重新启用强制 Code Owner Review。

## 7. bypass actor 的可见性边界

GitHub Rulesets API 并不保证普通只读审计身份一定能看到 `bypass_actors`。

日常治理 Workflow 保持只读：

```text
contents: read
```

不会为了审计 Ruleset 而给自己管理写权限。

如果 API 能返回 bypass actors：

```text
[]      -> 通过
非空    -> drift
```

如果字段因为权限不可见：

```text
healthy-with-limited-visibility
```

报告会明确提示可见性不足，不会假装已经证明 bypass 为空。

## 8. 最小权限仍保持

单维护者模式只取消“无意义的人工自审批”，不会放宽流水线执行权限。

仍要求：

- PR 任务不拥有 package write；
- 不可信 PR 不进入 Self-hosted SoC Runner；
- Attestation 使用独立最小权限 Job；
- Toolchain Publish 才拥有必要的 packages/id-token/attestations write；
- 云发布使用 OIDC / Federated Identity，不保存长期 Access Key；
- 无 Ruleset bypass actor；
- 禁止 force-push 和 main deletion。

## 9. 紧急变更

紧急事故也不通过关闭 Ruleset 或 force-push 处理。

推荐：

```text
Incident
  ↓
Emergency PR
  ↓
最小变更
  ↓
Required CI
  ↓
Merge
  ↓
事后 Review / RCA
```

如果未来建立多人团队和 Break-glass 身份，需要独立授权、时间边界、事故编号和自动审计。

## 10. 与 Platform Health 的区别

```text
Platform Health
    -> CI 运行得稳不稳
    -> Success / Queue / Duration / Rerun

Repository Governance
    -> 生产控制面有没有被改坏
    -> Ruleset / PR / Required Check / force-push / deletion
```

两个能力共同构成 CI 平台的生产运维面。
