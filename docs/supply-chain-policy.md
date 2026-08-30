# 供应链策略与证据链

## 1. 目标

本仓库的供应链策略不是“扫描一下给人看”，而是发布 Gate 的一部分。任何需要进入长期归档、晋级或回滚的生产制品，都必须能证明：

```text
固定源码
  + 固定工具链/基础镜像
  + 固定依赖快照
  + 漏洞策略通过
  + License 策略通过
  + Secret / Misconfiguration 策略通过
  + CycloneDX SBOM
  + GitHub Build Attestation
  + Cosign keyless signature
```

任意一项缺失或不一致，Promotion/Rollback 都应失败关闭（fail closed）。

## 2. 依赖固定

工具链 Dockerfile 必须：

- `FROM` 使用完整 `@sha256:<digest>`；
- 禁止 `:latest`；
- Ubuntu APT 使用固定 `APT::Snapshot` UTC 时间；
- 更新基础镜像或 Snapshot 必须走 PR 和 Toolchain gate。

当前 GCC 工具链固定 Ubuntu 24.04 image digest，并冻结到 `20260810T000000Z` APT Snapshot。

Dependabot 只负责提出受控升级 PR，不允许运行时自动漂移。

## 3. Toolchain gate

工具链候选镜像需要经过：

```text
Docker build
  -> smoke test
  -> Trivy vulnerability/license scan
  -> supply_chain_policy.py
  -> CycloneDX/BuildKit SBOM
  -> publish exact digest
  -> 再扫描 exact digest
  -> Cosign keyless sign + verify
  -> GitHub provenance attestation
```

只有 exact digest 能进入 `ci/toolchains.json` 的 active 状态。

## 4. 业务制品 Gate

中央 Matrix 与 Reusable Build 都固定使用 Trivy v0.70.0，对源码/构建目录执行：

- Vulnerability：漏洞；
- License：许可证风险；
- Secret：敏感信息；
- Misconfiguration：高风险配置；
- CycloneDX SBOM：软件物料清单。

当前策略拒绝：

- HIGH / CRITICAL 漏洞；
- HIGH / CRITICAL License finding；
- 任意 Secret finding；
- HIGH / CRITICAL Misconfiguration。

扫描工具和 Action 本身固定到完整 Git commit，不允许使用可漂移 tag。

## 5. Artifact Contract v2 与安全证据分离

可复现 bundle 不能把“每天变化的漏洞数据库结果”直接打进 tar.gz，否则同一源码和工具链可能因为扫描时间不同得到不同 artifact digest。

因此：

```text
Artifact Contract v2
  bundle.tar.gz
  manifest.json
  checksum

Security evidence sidecars
  security-scan.json
  security-sbom.cdx.json
```

二者绑定到同一次 Workflow Run，并分别生成 GitHub Attestation。

## 6. 长期归档

`archive-artifacts.yml` 只接受成功的 `main` Build Matrix Run。

归档前重新验证：

1. Artifact v2 manifest/source/run/digest；
2. bundle 内逐文件 digest；
3. supply-chain policy；
4. bundle/manifest/scan/SBOM GitHub Attestation；
5. 对 bundle 和 manifest 创建 Cosign keyless blob signature。

Release 中保存不可变证据集合：

```text
bundle.tar.gz
bundle.tar.gz.sha256
manifest.json
security-scan.json
security-sbom.cdx.json
bundle.tar.gz.sigstore.json
manifest.json.sigstore.json
```

Release metadata 同时保存每个 asset 的 SHA256。已有同名 Release 只能完全一致，禁止覆盖。

## 7. Promotion

Promotion 不重新构建。它从长期 Release 下载原字节并重新执行：

```text
Release asset digest map
 -> Artifact v2 verifier
 -> Supply-chain policy
 -> GitHub Attestation verification
 -> Cosign verify-blob
 -> Environment Deployment digest pointer
```

只有所有证据重新验证成功，才能移动 dev/staging/production 指针。

## 8. Rollback

Rollback 不是重新 build 旧 commit。

它读取历史成功 Deployment，重新下载旧 digest 的长期归档，再走和 Promotion 相同的安全验证，然后创建新的 Deployment：

```text
production digest-A
  -> digest-B
  -> rollback
  -> digest-A
```

历史记录保持 append-only，便于审计。

## 9. 关键固定版本

当前安全执行链固定：

- Trivy Action commit: `ed142fd0673e97e23eac54620cfb913e5ce36c25`
- Trivy: `v0.70.0`
- Cosign Installer commit: `6f9f17788090df1f26f669e9d70d6ae9567deba6`
- Cosign: `v3.0.6`

升级这些组件必须作为供应链变更走 PR、测试和 Required Gates。
