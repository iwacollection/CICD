# 仓库治理基线与漂移审计

CI 代码可以全部正确，但 GitHub Repository Settings 如果被手工改坏，生产保护仍然会失效。

例如：

```text
代码没有变化
        |
        +-- Required Check 被删除
        +-- CODEOWNERS 审批被关闭
        +-- required review 数从 1 改成 0
        +-- force-push 被允许
        +-- branch deletion 被允许
        |
        v
生产治理已经变弱
```

所以仓库治理不能只靠“设置过一次”，而要有：

```text
Versioned Policy
      +
Live Ruleset Audit
```

## 1. 当前真实 main Ruleset

当前仓库使用：

```text
main-production-governance
```

它保护默认分支，并要求：

- 禁止删除默认分支；
- 禁止 non-fast-forward / force-push；
- 至少 1 个 approving review；
- 新提交后旧审批失效；
- Require review from Code Owners；
- 合并前解决 Review Thread；
- 未归属变更要求额外审批；
- Required Status Checks 使用 strict policy。

当前 Required Checks：

```text
Validate CI platform
Build gate
Toolchain gate
```

不要把 `Discover` 或动态 Matrix Job 配成 Required Check；它们成功不代表最终构建成功，而且名称会随目标变化。

## 2. 期望状态代码化

仓库治理最低安全基线保存在：

```text
ci/repository-governance-policy.json
```

当前策略要求：

```text
Ruleset name = main-production-governance
target       = branch
enforcement  = active
include      = ~DEFAULT_BRANCH

rules:
├── deletion
├── non_fast_forward
├── pull_request
└── required_status_checks
```

Pull Request 最低基线：

```text
approving reviews >= 1
dismiss stale reviews = true
CODEOWNERS review = true
review thread resolution = true
extra approval for unattributed changes = true
```

## 3. 为什么是“最低基线”而不是完全相等

安全加强不应该被误报为 drift。

例如策略要求：

```text
approvals >= 1
```

实际 Ruleset 改成：

```text
approvals = 2
```

这是更严格，不报错。

同样，策略要求三个 Required Checks，如果以后新增：

```text
Security gate
```

也不应该因为多了一个 Gate 而失败。

因此治理审计采用：

```text
minimum required subset
```

而不是对整个 Settings JSON 做脆弱的字符串完全比较。

## 4. 自动漂移审计

实现：

```text
.github/workflows/repository-governance.yml
        |
        v
scripts/ci/repository_governance.py
        |
        v
GitHub Rulesets API
        |
        v
compare live state vs versioned policy
```

`Repository Governance` 每天运行一次，也支持手工触发。

流程：

```text
Checkout
   |
Validate versioned policy
   |
Fetch live Ruleset
   |
Evaluate drift
   |
Generate repository-governance.json
   |
Generate repository-governance.md
   |
Publish Job Summary
   |
Upload evidence / 30 days
   |
Final governance gate
```

即使发现 drift，也会先尽量保存报告，再让最终 Gate 失败。

## 5. 哪些漂移会直接失败

包括但不限于：

```text
Ruleset 不存在
Ruleset enforcement != active
~DEFAULT_BRANCH selector 被移除
branch deletion 保护被删除
non_fast_forward 保护被删除
approving review 降到 0
stale review invalidation 被关闭
CODEOWNERS requirement 被关闭
review thread resolution 被关闭
strict required status checks 被关闭
Validate CI platform 被删除
Build gate 被删除
Toolchain gate 被删除
可见的 bypass actor 被增加
```

这些都属于真正的生产治理退化。

## 6. bypass actor 的 API 可见性边界

GitHub Rulesets API 有一个重要权限限制：`bypass_actors` 只有调用身份对 Ruleset 具有足够写权限时，GitHub 才保证返回。

但是日常治理审计的原则是：

```text
read-only
```

不能为了“看 bypass 配置”给 Workflow 一个能够修改 Ruleset 的管理权限。

因此默认策略：

```json
"bypass_actors": {
  "allow_when_visible": false,
  "visibility_required": false
}
```

行为是：

```text
API 返回 bypass_actors
        |
        +-- []       -> 通过
        |
        `-- 非空     -> drift

API 不返回 bypass_actors
        |
        `-- visibility warning
```

此时报告状态为：

```text
healthy-with-limited-visibility
```

它既不会误报为 drift，也不会假装“已经证明 bypass 为空”。

如果以后有专门的治理审计 GitHub App / 高权限身份，可以把：

```json
"visibility_required": true
```

此时 bypass 不可见就 fail closed。

## 7. 为什么审计 Workflow 不能拥有管理写权限

错误模型：

```text
为了审计 Ruleset
      |
给 Workflow Ruleset Admin/Write
      |
Workflow 被利用
      |
攻击者反而可以修改被审计规则
```

正确模型：

```text
日常自动审计
  -> read-only

高权限深度审计
  -> 独立身份
  -> 单独审批
  -> 不和普通构建权限混用
```

审计工具不应该天然拥有被审计对象的修改权。

## 8. CODEOWNERS 与权限模型

`.github/CODEOWNERS` 声明关键目录责任人，真正强制 CODEOWNERS 审批则由 Ruleset 的 `Require review from Code Owners` 保证。

业务 CI 默认只给最小权限；需要写入的 Job 单独提升：

- Toolchain Publish：`packages: write`、`id-token: write`、`attestations: write`；
- 业务制品来源证明：独立 Attestation Job 才拥有 `id-token: write`、`attestations: write`；
- PR 验证任务不得拥有 packages 写权限；
- 不可信 PR 不进入 Self-hosted SoC Runner；
- 云发布使用 OIDC / Federated Identity（联合身份）短期凭据，不保存长期 Access Key。

## 9. 紧急变更

紧急事故也不应该通过关闭 Ruleset 来处理。

推荐：

```text
Incident
  -> Emergency PR
  -> 最小变更
  -> Required CI
  -> 指定审批人
  -> Merge
  -> 事后 Review / RCA
```

如果企业未来确实设计 Break-glass（紧急破窗）身份，也必须：

- 独立授权；
- 有时间边界；
- 有事故编号；
- 有操作者和原因；
- 自动审计；
- 事后恢复正常策略。

## 10. 和 Platform Health 的区别

```text
Platform Health
    -> CI 运行得稳不稳？
    -> Success / Queue / Duration / Rerun

Repository Governance
    -> CI 的生产保护还在不在？
    -> Ruleset / Review / Required Check / force-push
```

一个是运行健康度，一个是控制面配置漂移；两者共同构成 CI 平台运维能力。
