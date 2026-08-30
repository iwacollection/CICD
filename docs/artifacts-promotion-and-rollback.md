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
 -> dev 指针      -> abc123...
 -> staging 指针  -> abc123...
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

详细字段见 `docs/artifact-contract-v2.md`。

## 3. Actions Artifact 只做流水线短期运输

GitHub Actions Artifact 继续保留，但它只是：

```text
Build Job
  -> Attestation Job
  -> Archive Workflow
```

之间的短期传输介质。

它不再承担：

- 长期生产归档
- 环境当前版本指针
- 回滚历史

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
GitHub Attestation verify
  +-- bundle
  +-- manifest
  |
  v
GitHub Release archive
```

Release tag 不是人工版本号，而是：

```text
artifact-v2-<sha256(artifact_name)>
```

Release body 绑定：

- Artifact Contract 版本
- artifact name
- bundle SHA256
- source repository
- source SHA
- source run ID
- toolchain identity

如果同一个 release tag 已存在，归档逻辑只允许完全一致的 metadata + 三个完全一致的资产名；任何冲突都会失败，不自动覆盖。

## 5. 为什么现在选择 GitHub Release

这一步的重点是先把生命周期从 Actions 临时缓存里解耦出来，并形成真实可运行闭环。

GitHub Release 具备：

- 不受 Actions Artifact 14/90 天保留期影响
- 任意二进制文件
- API 下载
- 仓库权限控制
- 审计历史
- 无需额外 Nexus/S3 账号即可验证整套流程

后续可把 `artifact_archive.py` 的后端扩展为：

```text
Nexus Raw
S3 / MinIO
Azure Blob
ACR / OCI Artifact
JFrog Artifactory
```

Promotion/Rollback 上层契约不需要变化，只替换长期对象存储后端。

## 6. Promotion：移动环境 digest 指针

`promote.yml` 不再从 Actions Artifact 下载。

现在流程是：

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
重新验证 bundle + manifest + attestations
  |
  v
进入 GitHub Environment 审批
  |
  v
创建 GitHub Deployment
```

Deployment payload 就是环境当前制品指针：

```text
environment
 -> artifact_name
 -> bundle_sha256
 -> source_sha
 -> source_run_id
 -> release_tag
```

因此：

```text
dev       -> digest-A
staging   -> digest-A
production-> digest-A
```

是可查询、可审计的真实状态，而不是 README 里的说明。

## 7. 为什么用 GitHub Deployment 记录环境指针

环境指针必须同时满足：

- 可以变化
- 每次变化必须有历史
- 能知道谁触发
- 能知道何时发生
- 能知道指向哪个 digest
- 不能为了更新指针去修改/重建制品

GitHub Deployment 正好是追加式部署记录。

`deployment_pointer.py` 创建的新 Deployment 成功后，GitHub 会自动把同环境旧 Deployment 标记为 inactive；最新 successful deployment 就是当前环境指针。

## 8. Rollback：不是重新 Build

`rollback.yml` 输入：

```text
target_environment
restore_deployment_id
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
重新验证 digest / manifest / provenance
  |
  v
创建新的 rollback Deployment
```

例如：

```text
Deployment 101 -> production -> digest-A
Deployment 102 -> production -> digest-B

发生事故

Rollback restore_deployment_id=101

Deployment 103 -> production -> digest-A
reason=rollback
restored_from_deployment_id=101
```

所以完整历史仍然存在：A -> B -> A，而不是把 102 偷偷改掉。

## 9. 生产审批

`promote.yml` 和 `rollback.yml` 都进入目标 GitHub Environment。

因此 production 应配置：

- Required reviewers
- Allowed branches = main
- 环境级 secrets
- 必要等待时间/变更窗口

审批页面关注的是**具体 digest**，不是一句“发 v1.2”。

## 10. 故障恢复边界

CI 的 Rollback 指针只能保证“重新选择旧二进制”。

数据库、固件仍有自己的系统级兼容约束。

### 数据库

如果执行了不可逆 migration，应用包回滚不代表数据库能回退。

### 固件

还要考虑：

- Bootloader 向后兼容
- 分区表变化
- Anti-rollback fuse/policy
- 数据分区格式
- OTA 中断恢复
- A/B 分区

所以“制品可回滚”与“整个业务/设备可回滚”必须分开评估。

## 11. 当前闭环

```text
Source
  |
Build once
  |
Artifact Contract v2
  |
GitHub Attestation
  |
Actions Artifact (short-lived transport)
  |
Archive Trusted Artifacts
  |
GitHub Release (long-term object)
  |
Promotion
  |
GitHub Deployment environment digest pointer
  |
Rollback
  `-> historical Deployment -> same old Release bytes
```

下一层是供应链 Policy Gate：SBOM、漏洞、License、签名和依赖固定必须成为 Promotion 前置条件，而不是仅记录文档。
