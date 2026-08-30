# 制品、长期归档、晋级与回滚

## 1. 最重要的规则

**测试、预发、生产必须使用同一个已经构建好的 Artifact Contract v2 制品。**

错误流程：

```text
commit A
 -> 测试重新编译 -> artifact X
 -> 生产重新编译 -> artifact Y
```

正确流程：

```text
commit A
 -> CI 构建一次
 -> Artifact Contract v2
 -> bundle SHA256 = abc123...
 -> 长期归档
 -> dev 指针        -> abc123...
 -> staging 指针    -> abc123...
 -> production 指针 -> abc123...
```

晋级只移动环境指针，不重新编译、不重新打包。

## 2. Artifact Contract v2

构建会生成：

```text
<artifact-name>.tar.gz
<artifact-name>.tar.gz.sha256
<artifact-name>.manifest.json
```

`artifact-name` 已包含：

```text
project
+ soc
+ os
+ arch
+ toolchain
+ toolchain identity short fingerprint
+ source SHA short fingerprint
```

详细字段见 [artifact-contract-v2.md](artifact-contract-v2.md)。

## 3. Actions Artifact 只做流水线短期运输

GitHub Actions Artifact 继续保留，但它只是：

```text
Build Job
  -> Attestation Job
  -> Archive Workflow
```

之间的短期传输介质。

它不再承担：

- 长期生产归档；
- 环境当前版本指针；
- 回滚历史。

因此即使 Actions Artifact 后续按 14 天策略被清理，已归档生产制品仍然存在。

## 4. 当前长期制品库

当前仓库首先落地一个无需额外基础设施的生产闭环：**GitHub Releases 作为长期二进制对象库**。

可信 `main` 的 `Build Matrix` 成功后，`archive-artifacts.yml` 自动触发：

```text
Successful main Build Matrix
  |
  v
Download exact Actions Artifact
  |
  v
verify_artifact.py
  |
  +-- source SHA / run ID / repository
  +-- manifest v2
  +-- bundle SHA256
  +-- tar member SHA256
  |
  v
Supply-chain policy
  |
  +-- vulnerability
  +-- license
  +-- secret
  +-- misconfiguration
  +-- CycloneDX SBOM
  |
  v
GitHub Attestation verify
  |
  v
Cosign archive signature
  |
  v
GitHub Release archive
```

Release tag 不是人工版本号，而是：

```text
artifact-v2-<sha256(artifact_name)>
```

Release metadata 绑定：

- Artifact Contract 版本；
- artifact name；
- bundle SHA256；
- source repository；
- source SHA；
- source run ID；
- toolchain identity；
- scan / SBOM / signature evidence digest。

如果同一个 release tag 已存在，归档逻辑只允许完全一致的 metadata 与资产；任何冲突都会失败，不自动覆盖。

## 5. 为什么现在选择 GitHub Release

这一步的重点是先把生命周期从 Actions 临时缓存里解耦出来，并形成真实可运行闭环。

GitHub Release 具备：

- 不受 Actions Artifact 14/90 天保留期影响；
- 任意二进制文件；
- API 下载；
- 仓库权限控制；
- 审计历史；
- 无需额外 Nexus/S3 账号即可验证整套流程。

后续可把 `artifact_archive.py` 的后端扩展为：

```text
Nexus Raw
S3 / MinIO
Azure Blob
ACR / OCI Artifact
JFrog Artifactory
```

Promotion/Rollback 上层契约不需要变化，只替换长期对象存储后端。

## 6. Promotion：不只是“选一个环境”

`promote.yml` 不再允许把任意归档制品直接跳到任意环境。

中央策略位于：

```text
ci/promotion-policy.json
```

当前固定晋级路径：

```text
Build Archive
    |
    v
   dev
    |
    v
 staging
    |
    v
production
```

对应策略：

```text
dev        -> 无前置环境
staging    -> 必须先在 dev 成功部署同一 artifact identity
production -> 必须先在 staging 成功部署同一 artifact identity
```

不能：

```text
Archive -> production        X
Archive -> staging           X（没有 dev 历史时）
dev digest-A -> staging digest-B   X
```

## 7. “同一份制品”怎么证明

晋级前不是只比较版本号或文件名，而是要求前置环境存在一个 successful GitHub Deployment，并且下面五个字段全部一致：

```text
artifact_name
bundle_sha256
source_sha
source_run_id
release_tag
```

也就是：

```text
exact artifact identity
```

只要 digest、source run 或 archive release 任意一个不同，都不能拿前置环境的成功记录给另一个制品“借通行证”。

## 8. Promotion 完整流程

当前流程是：

```text
人工输入
source_run_id
artifact_name
expected_sha256
目标环境
  |
  v
验证原始 main Build Matrix Run
  |
  v
从长期 Release 下载 exact artifact
  |
  v
Promotion Path Policy
  |
  +-- dev: root allowed
  |
  +-- staging: 查 dev successful deployment history
  |
  `-- production: 查 staging successful deployment history
  |
  v
重新验证 bundle + manifest
  |
  v
重新执行 supply-chain policy
  |
  v
验证 GitHub Attestation + Cosign
  |
  v
进入 GitHub Environment 审批
  |
  v
创建 GitHub Deployment pointer
```

Promotion Policy 查询的是**历史 successful deployment**，而不是只看前置环境当前版本。

所以即使 dev 后来已经前进到 digest-B，只要 digest-A 曾经在 dev 真实成功部署过，仍然可以按变更流程把经过验证的 digest-A 晋级到 staging。

## 9. GitHub Deployment 是环境 digest pointer + 审计链

Deployment payload 记录：

```text
environment
 -> artifact_name
 -> bundle_sha256
 -> source_sha
 -> source_run_id
 -> release_tag
 -> promoted_from_deployment_id   # promotion 时
 -> restored_from_deployment_id   # rollback 时
```

例如：

```text
Deployment 101
  environment=dev
  digest=A

Deployment 120
  environment=staging
  digest=A
  promoted_from_deployment_id=101

Deployment 140
  environment=production
  digest=A
  promoted_from_deployment_id=120
```

这使生产发布不只是：

```text
production -> digest-A
```

还可以反查：

```text
production 140
  <- staging 120
       <- dev 101
```

形成可审计晋级链。

## 10. 为什么 Environment Approval 仍然需要

Promotion Path Policy 解决的是：

> 这份 bytes 有没有经过规定的环境路径？

GitHub Environment Approval 解决的是：

> 这次变更现在是否允许进入这个环境？

二者不是一回事。

production 仍应配置：

- Required reviewers；
- Allowed branches = main；
- 环境级 secrets；
- 必要等待时间 / 变更窗口。

所以 production 发布要同时满足：

```text
可信 main Build
+ immutable archive
+ supply-chain verification
+ staging successful exact identity
+ production Environment approval
```

## 11. Rollback：不是逆向 Promotion

`rollback.yml` 输入：

```text
target_environment
restore_deployment_id
```

Rollback 不走 `dev -> staging -> production` 前向策略。

它的边界是：

```text
只能恢复目标环境自己的历史 successful deployment
```

流程：

```text
找到历史 Deployment
  |
  +-- 必须属于同一个 environment
  |
  v
读取历史 artifact_name + digest + release_tag
  |
  v
重新验证原始 main Build Run
  |
  v
从长期库下载旧制品
  |
  v
重新验证 digest / manifest / policy / provenance
  |
  v
创建新的 rollback Deployment
```

例如：

```text
Deployment 140 -> production -> digest-A
Deployment 150 -> production -> digest-B

发生事故

Rollback restore_deployment_id=140

Deployment 151 -> production -> digest-A
reason=rollback
restored_from_deployment_id=140
```

完整历史是：

```text
A -> B -> A
```

旧记录不会被修改。

## 12. 为什么 Rollback 不要求重新经过 staging

事故回滚的目标是恢复**这个环境曾经已经成功运行过的历史 bytes**。

如果强制 production rollback 再走：

```text
production -> dev -> staging -> production
```

会把应急恢复变成重新发布，失去 rollback 的意义。

所以规则分开：

```text
Forward Promotion
  -> 必须 dev -> staging -> production

Rollback
  -> 只能恢复同环境历史 successful digest
```

## 13. 故障恢复边界

CI 的 Rollback pointer 只能保证“重新选择旧二进制”。

数据库、固件仍有自己的系统级兼容约束。

### 数据库

如果执行了不可逆 migration，应用包回滚不代表数据库能回退。

### 固件

还要考虑：

- Bootloader 向后兼容；
- 分区表变化；
- Anti-rollback fuse/policy；
- 数据分区格式；
- OTA 中断恢复；
- A/B 分区。

所以“制品可回滚”与“整个业务/设备可回滚”必须分开评估。

## 14. 当前完整闭环

```text
Source
  |
Build once
  |
Artifact Contract v2
  |
Supply-chain Scan + SBOM
  |
GitHub Attestation
  |
Actions Artifact (short-lived transport)
  |
Archive Trusted Artifacts
  |
GitHub Release + Cosign (long-term object)
  |
Promotion Path Policy
  |
  +-- dev
  +-- staging requires exact successful dev identity
  `-- production requires exact successful staging identity
  |
GitHub Environment Approval
  |
GitHub Deployment pointer + promoted_from lineage
  |
Rollback
  `-> same-environment historical Deployment -> same old Release bytes
```
