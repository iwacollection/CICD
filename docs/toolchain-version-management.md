# 多语言工具链版本管理

## 两层注册表

- `ci/toolchains.json`：镜像执行身份、Dockerfile、构建上下文、digest 与工具链元数据。
- `ci/toolchain-versions.json`：语言版本与构建工具版本的精确组合。JDK 与 Maven 分开 pin；Go 选择完整 patch 版本；C/C++ 区分 GCC 与 Clang。

## 版本晋级规则

1. **planned**：已登记构建定义，但不允许项目使用，也不允许发布。
2. **PR verification**：`python3 scripts/ci/toolchain_catalog.py --verify-matrix` 会构建 planned/candidate 镜像，执行 smoke、Trivy 漏洞/许可证扫描，并生成 CycloneDX SBOM。证据作为 Actions artifact 保存。
3. **candidate**：仅在 PR 验证通过后，经审核把 catalog 与版本注册表状态改为 candidate。合并到 main 后，发布工作流才会推送 GHCR 镜像、扫描准确 digest、生成 BuildKit SBOM、Cosign 签名和 provenance。
4. **消费验证**：镜像发布后将返回的真实 digest 写回 catalog；消费矩阵拉取 digest，验证签名并执行消费项目测试。
5. **active**：只有候选镜像具备不可变 digest、供应链证据和消费项目验证后，才由人工审核晋级。绝不自动晋级。

`--matrix` 只包含可发布的 candidate；`--verify-matrix` 包含 planned 与 candidate，专用于 PR 验证。planned 镜像不会被推送到 GHCR。

## 当前版本

| 语言 | 组合 | 状态 | 固定输入 |
| --- | --- | --- | --- |
| Java | Temurin 17.0.21+9 + Maven 3.10.0 | candidate | 已有 digest |
| Java | Temurin 21.0.12.1+1 + Maven 3.10.0 + Guava 33.7.2-jre | planned | Temurin SHA256；Maven SHA512；Guava SHA256；Ubuntu base digest；APT snapshot 20261105 |
| Go | 1.26.10 | candidate | 官方 Go release SHA256；官方 golang base digest |
| Go | 1.25.14 | planned | go.dev 官方 linux/amd64 archive SHA256；Ubuntu base digest；APT snapshot 20261105 |
| C/C++ | GCC 13 | active | 当前 GCC 容器工具链 |
| C/C++ | GCC/G++ 12.4.0-2ubuntu1~24.04.1 | planned | Ubuntu base digest、APT snapshot 20261105、精确 APT 包版本；smoke 强制检查完整编译器版本 |
| C/C++ | Clang 18 | planned | 尚无独立镜像 |

## 镜像输入说明

- 所有 Ubuntu 镜像使用固定 base image digest；APT 软件包从固定 snapshot 安装。构建以零重试模式更新索引并安装修复 CVE-2026-109518 的 apt 2.8.4ubuntu0.1，再安装工具链；包索引签名和软件包哈希仍由 APT 校验。APT 安装步骤合并为一次 update + install，避免重复刷新索引。
- JDK 21 使用 Temurin 官方发行归档 SHA256；Maven 使用 Apache 官方 SHA512；Maven 3.10.0 自带的 Guava 从受影响的 33.7.1-jre 替换为修复 CVE-2026-102554 的 33.7.2-jre，并校验 SHA256。
- Go 1.25.14 使用 go.dev 官方发布页提供的 linux/amd64 SHA256。
- GCC 12 使用 Ubuntu 24.04 固定 APT snapshot，精确安装 gcc-12/g++-12 12.4.0-2ubuntu1~24.04.1。APT 根据 Ubuntu 签名仓库元数据校验每个 .deb 的 SHA256，smoke test 同时断言 dpkg 包版本和编译器完整版本。

## 验证边界

PR 阶段通过 docker/build-push-action 接入 GitHub Actions BuildKit 缓存，按工具链独立保存和复用构建层；基础镜像使用固定 digest，不再每次强制 pull。Dockerfile 依赖层未变化时可直接命中缓存；仅变更的工具链需要重建。随后执行镜像 smoke 与独立消费样例项目构建/测试，再做漏洞/许可证扫描并生成 CycloneDX SBOM；这不等于已发布镜像。只有 main 上的发布工作流才能产生 GHCR digest、签名和 provenance；消费项目必须在发布后使用该 digest 测试。当前新增版本仍保持 planned，不能被项目解析器选用。
