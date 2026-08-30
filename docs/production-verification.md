# 生产生命周期真实验收记录

本文不是设计说明，而是 **CICD 平台在 2026-08-30 实际执行过的生产链路验收证据**。

目的只有一个：区分下面两句话。

```text
“代码里支持这个能力”

和

“这个能力真的在 main 上跑通过”
```

---

## 1. 验收结论

截至 2026-08-30，下面这条完整链路已经真实执行成功：

```text
Pull Request
   ↓
Required Gates
   ↓
main Build Matrix
   ↓
L0 hello-lib
   ↓ verified Artifact Contract v2
L1 hello-cpp
   ↓
GitHub Attestation
   ↓
Build gate
   ↓
Archive Trusted Artifacts
   ↓
GitHub Release + Cosign
   ↓
dev
   ↓
staging
   ↓
production v1
   ↓
production v2
   ↓
rollback
   ↓
production 恢复 v1
```

因此以下能力不是“计划中”或“只有单元测试”：

- main 上真实依赖 DAG；
- 上游 Artifact v2 交接；
- trusted main provenance / GitHub Attestation；
- 长期 Release Archive；
- Cosign archive signature；
- `dev -> staging -> production` 路径强制；
- GitHub Deployment lineage；
- production 历史 digest rollback；
- rollback 不重新构建旧版本。

---

## 2. v1 基线制品

第一次进入 production 的最终应用制品：

```text
project:
hello-cpp

source run:
33312989466

source SHA:
6d63d8fa283640e52786922ede7abaa3e8ef9bd2

artifact name:
hello-cpp-generic-linux-x86_64-gcc-host-container-v1-3caf311bd69a-6d63d8fa2836

bundle SHA256:
d862333999d64bca766ff4d65473f3f53c8f93dad6ef44289dca08b0a5bd29ba

release tag:
artifact-v2-a6d3cbdd9545d461096a83d409ef0282f0f5cfa9a1a5f5e953da794607f6a046
```

该 bundle 在 Build、Archive、Promotion 和 Rollback 中使用同一个 SHA256，没有在环境晋级阶段重新编译。

---

## 3. main Build 与 Archive

### Build Matrix

```text
Run ID:
33312989466

Branch:
main

Result:
success
```

核心执行：

```text
hello-lib (L0)
   ↓
Artifact Contract v2
   ↓
hello-cpp (L1)
   ↓
Attest trusted DAG artifacts
   ↓
Build gate
```

### Archive Trusted Artifacts

```text
Run ID:
33313097780

Result:
success
```

归档前重新验证：

- 原始 Build Run；
- bundle SHA256；
- Artifact Contract v2 manifest；
- bundle 内成员 digest；
- vulnerability / license / secret / misconfiguration policy；
- CycloneDX SBOM；
- GitHub Attestation；
- Cosign keyless archive signature。

Actions Artifact 在这里仅负责短期运输，长期资产进入 GitHub Release。

---

## 4. v1 Promotion 路径

同一 v1 digest 的环境链路：

```text
dev
Deployment 6167191032
        ↓
staging
Deployment 6167239672
        ↓
production
Deployment 6167294876
```

每一步 Promotion 都重新校验：

```text
artifact_name
bundle_sha256
source_sha
source_run_id
release_tag
```

`staging` 必须找到 successful `dev` deployment 的完全相同 artifact identity。

`production` 必须找到 successful `staging` deployment 的完全相同 artifact identity。

因此下面路径会被拒绝：

```text
Archive -> production
Archive -> staging（无 dev）
dev digest-A -> staging digest-B
staging digest-A -> production digest-B
```

---

## 5. 为什么又创建了 v2 drill 制品

如果 production 当前只有一个版本：

```text
production = v1
```

直接执行“回滚到 v1”只能证明 Workflow 能运行，不能证明 production 的版本真的发生了恢复。

所以验收专门创建了第二个可追踪版本。

v2：

```text
source run:
33315320217

source SHA:
dc5d6144ae7364158926e8ac01b5776fe1cbc06e

artifact name:
hello-cpp-generic-linux-x86_64-gcc-host-container-v1-3caf311bd69a-dc5d6144ae73

bundle SHA256:
19e257a221d960f7733a97937feab9efbdd79c275f2a7ffbb628e9feb3137892
```

v2 同样经过：

```text
main Build
→ Attestation
→ Archive
→ dev
→ staging
→ production
```

对应 Deployment：

```text
dev        6167474231
staging    6167500209
production 6167544579
```

这样 production 的状态真实变成：

```text
v1 digest d8623339...
        ↓
v2 digest 19e257a2...
```

---

## 6. production rollback 验收

Rollback Workflow Run：

```text
33316634828
```

输入：

```text
target_environment:
production

restore_deployment_id:
6167294876
```

也就是要求 production 从 v2 恢复到历史 v1 Deployment。

Rollback 执行前重新验证：

1. `6167294876` 确实属于 `production`；
2. 原始 Build Run `33312989466` 仍然可信；
3. 长期 Release 仍然存在；
4. Release metadata 与 Deployment pointer 一致；
5. Artifact Contract v2 校验通过；
6. bundle SHA256 仍是 `d8623339...29ba`；
7. supply-chain policy 通过；
8. GitHub Attestation 通过；
9. Cosign archive signature 通过。

最终创建新的 production pointer：

```text
Deployment ID:
6167602777

environment:
production

bundle SHA256:
d862333999d64bca766ff4d65473f3f53c8f93dad6ef44289dca08b0a5bd29ba

reason:
rollback

restored_from_deployment_id:
6167294876
```

Workflow Summary 明确记录：

```text
Rebuild: NO
```

最终 production 历史：

```text
v1
Deployment 6167294876
SHA d8623339...
        ↓
v2
Deployment 6167544579
SHA 19e257a2...
        ↓
rollback
Deployment 6167602777
SHA d8623339...
```

即：

```text
A -> B -> A
```

旧 Deployment 没有被篡改，而是创建新的历史记录。

---

## 7. 这次验收暴露并修掉的真实问题

真实 main 生命周期验证的价值不只是“证明成功”，还发现了 PR 阶段没有暴露的问题。

第一次 main Build 中：

```text
L0/L1 success
DAG barrier success
Attestation skipped
Build gate failure
```

根因是 GitHub Actions 的 skipped-needs 传播：空的 L2-L7 正常 skip 后，Attestation Job 没有显式 `always()`，被祖先 skip 状态短路。

修复后：

```text
empty DAG levels = skipped（正常）
        ↓
build_complete = success
        ↓
trusted main attestation = success
        ↓
Build gate = success
        ↓
Archive 才允许触发
```

这说明平台遵循的是 fail-closed：Attestation 缺失时 Build gate 失败，Archive 不会错误发布未证明制品。

---

## 8. 已验证边界与未验证边界

### 已真实验证

```text
Hosted Linux x86_64
C/C++
Dependency DAG
Immutable container toolchain
Reproducible-build gate
Artifact Contract v2
Supply-chain scan
CycloneDX SBOM
GitHub Attestation
Cosign
GitHub Release long-term archive
Promotion lineage
dev -> staging -> production
production rollback
Platform Ruleset / Required Gates
```

### 仍属于外部资源边界

```text
真实 RK / Qualcomm / MediaTek 主机
真实厂商 SDK/BSP
License server / pool
真实 HIL 板卡实验室
企业外部 Nexus / Artifactory / S3 / MinIO / ACR
企业 KMS/HSM 生产签名身份
```

这些能力有平台接口和接入设计，但没有真实资源就不会标记为“生产验证完成”。

---

## 9. 验收标准

以后平台重大版本升级，不应只要求“PR 三个 Gate 都绿”。

至少重新证明：

```text
1. Validate CI platform
2. Build gate
3. Toolchain gate
4. main Build
5. trusted Attestation
6. Archive
7. 一个 digest 走 dev -> staging -> production
8. rollback 恢复历史 production digest
```

如果长期制品后端、签名方式、Promotion Policy 或 Artifact Contract 发生不兼容变化，应重新做完整生命周期验收。
