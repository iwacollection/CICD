# 多语言工具链版本管理

## 两层注册表

- `ci/toolchains.json`：镜像执行身份、Dockerfile、构建上下文、digest 与工具链元数据。
- `ci/toolchain-versions.json`：语言版本与构建工具版本的精确组合。JDK 与 Maven 分开 pin；Go 选择完整 patch 版本；C/C++ 区分 GCC 与 Clang。

## 版本晋级规则

1. **planned**：已登记构建定义，但不允许项目使用，也不允许发布。
2. **PR verification**：`python3 scripts/ci/toolchain_catalog.py --verify-matrix` 会构建 planned/candidate 镜像，执行 smoke、语言消费样例测试；Trivy 扫描漏洞、许可证、Secret 和镜像配置，并由策略脚本阻断 HIGH/CRITICAL 漏洞与配置问题、禁用许可证及任意 Secret 命中；生成并校验 CycloneDX SBOM。证据作为 Actions artifact 保存。
3. **candidate 发布**：合并到 main 后，发布工作流推送 GHCR 镜像，对准确 digest 扫描漏洞/许可证/Secret/配置，生成 CycloneDX SBOM 和漏洞扫描 attestation，再做 Cosign 签名、GitHub provenance 与独立验证，最后自动登记 GitHub Deployment candidate record。
4. **审核晋级**：手动运行 Promote Toolchain Candidate，经 toolchain-production Protected Environment 审批后，重新验证 digest、签名、SBOM、漏洞扫描报告、provenance 并重新扫描；工作流自动打开 catalog Promotion PR，不自动合并。
5. **active 消费**：正常代码审查并合并 Promotion PR 后，catalog 才写入真实 digest、build context/build definition SHA256 并标记 active；业务项目只允许解析 active 工具链。

`--matrix` 只包含可发布的 candidate；`--verify-matrix` 包含 planned 与 candidate，专用于 PR 验证。planned 镜像不会被推送到 GHCR。

## 当前版本

| 语言 | 组合 | 状态 | 固定输入 |
| --- | --- | --- | --- |
| Java | Temurin 17.0.21+9 + Maven 3.10.0 | candidate | 安全修复后清空旧 digest，等待新镜像发布与证据登记 |
| Java | Temurin 21.0.12.1+1 + Maven 3.10.0 + Guava 33.7.2-jre | planned | Temurin SHA256；Maven SHA512；Guava SHA256；Ubuntu base digest；APT snapshot 20261105 |
| Go | 1.26.10 | candidate | 官方 Go release SHA256；官方 golang base digest |
| Go | 1.25.14 | retired | 上游该版本存在未修复 HIGH 漏洞，保留历史定义，不允许消费或发布 |
| C/C++ | GCC 13 | active | 当前 GCC 容器工具链 |
| C/C++ | GCC/G++ 12.4.0-2ubuntu1~24.04.1 | planned | Ubuntu base digest、APT snapshot 20261105、精确 APT 包版本；smoke 强制检查完整编译器版本 |
| C/C++ | Clang 18 | planned | 尚无独立镜像 |

## 镜像输入说明

- 所有新发布的 Ubuntu 镜像使用固定 base image digest；APT 软件包从固定 snapshot 安装。CA bootstrap 阶段使用零重试，避免 CVE-2026-109518 的重试利用条件；由于最小基础镜像尚无 CA 根证书，bootstrap 暂时关闭 TLS peer 验证，但 APT 仍验证 Ubuntu archive 签名元数据和软件包哈希。安装 CA 后恢复正常 HTTPS 证书验证，再安装修复版本 apt/libapt-pkg 2.8.4ubuntu0.1 和 OpenSSL libssl3t64 3.0.13-0ubuntu3.16。
- JDK 21 使用 Temurin 官方发行归档 SHA256；Maven 使用 Apache 官方 SHA512；Maven 3.10.0 自带的 Guava 从受影响的 33.7.1-jre 替换为修复 CVE-2026-102554 的 33.7.2-jre，并校验 SHA256。
- Go 1.25.14 已退役；Go 1.26.10 是当前 candidate，仍需经过 main 发布、精确 digest 消费验证和人工晋级。
- GCC 12 使用 Ubuntu 24.04 固定 APT snapshot，精确安装 gcc-12/g++-12 12.4.0-2ubuntu1~24.04.1。APT 根据 Ubuntu 签名仓库元数据校验每个 .deb 的 SHA256，smoke test 同时断言 dpkg 包版本和编译器完整版本。

## 验证边界

PR 阶段通过 docker/build-push-action 接入 GitHub Actions BuildKit 缓存，按工具链独立保存和复用构建层；基础镜像使用固定 digest，不再每次强制 pull。Dockerfile 依赖层未变化时可直接命中缓存；仅变更的工具链需要重建。随后执行镜像 smoke 与独立消费样例项目构建/测试，再做漏洞、许可证、Secret、镜像配置扫描，并校验 CycloneDX SBOM；这不等于已发布镜像。只有 main 上的发布工作流才能产生 GHCR digest、签名和 provenance；消费项目必须在发布后使用该 digest 测试。Java 17 与 Go 1.26 当前为 candidate；Java 21 与 GCC 12 仍为 planned。所有 candidate 都不能被普通项目解析器选用，必须先完成 digest 登记和人工审核晋级。
