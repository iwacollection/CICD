# Go 与 C/C++ 工具链构建（Jenkins）

本文说明 Jenkins 项目怎样用平台统一的 Go、C/C++ 工具链镜像构建，以及新工具链怎样从 Candidate 变成可被业务使用的 Active。

## 为什么需要

- 不同机器上的 Go、gcc 版本不一致，同一份源码得到不同二进制。
- 构建时临时安装依赖，会受到软件包仓库可用性和版本变化影响。
- Alpine（musl）编译的 cgo/C++ 程序，放到 glibc 的线上机器上常常报库找不到。

## 解决什么问题 / 不解决什么

解决：构建环境固定到镜像 digest，构建命令在容器里跑，产物按 Artifact Contract v2 打包校验。

不解决（本版本明确没做）：

- 私有 Go 模块（\`GOPRIVATE\`、netrc 凭据）。\`scripts/ci/run_build.py\` 只向容器转发白名单环境变量，要支持需要先扩白名单并补测试。
- \`golangci-lint\`。它版本变化快、依赖项目配置，没有核实到可固定的校验和，所以没放进镜像。
- Pull Request / fork 构建。和 \`javaToolchainBuild\` 一样只允许可信的 \`main\` 分支，PR 要走不拿凭据的低信任验证通道。

## 实际怎么运行

1. 项目在仓库里放 \`ci/toolchain.json\`，只写工具链 ID 和命令，不写镜像。
2. Jenkinsfile 调用 \`goToolchainBuild(...)\` 或 \`cppToolchainBuild(...)\`。
3. 步骤检出平台仓库的固定 commit（由控制器凭据 \`cicd-platform-sha\` 提供），用中央解析器把工具链 ID 解析成 \`image@sha256:...\`。
4. \`cosign verify\` 校验签名，\`docker pull\` 后核对 digest。
5. 在容器内依次跑 \`build_command\`、\`test_command\`、\`verify_command\`。
6. 打包、校验制品，归档；测试报告用 \`junit\` 发布（失败时也发布）。

每一步的输出是下一步的输入：ID → 镜像 digest → 容器内构建 → 制品 → 校验。

### Go 项目示例

\`ci/toolchain.json\`：

\`\`\`json
{
  "schema_version": 1,
  "language": "go",
  "project_name": "my-go-service",
  "toolchain": "go126",
  "build_command": "go build -o bin/app ./cmd/app",
  "test_command": "gotestsum --junitfile reports/junit.xml -- ./...",
  "artifact_globs": ["bin/app"],
  "dependency_lock_files": ["go.sum"],
  "test_report_globs": ["reports/*.xml"]
}
\`\`\`

\`Jenkinsfile\`：

\`\`\`groovy
@Library('cicd-platform') _
goToolchainBuild(projectDir: '.', registryCredentialsId: 'ghcr-readonly')
\`\`\`

### C/C++ 项目示例

用已经是 Active 的 \`gcc-host-container-v1\`。CMake 3.21 以上的 \`ctest --output-junit\` 可直接产出 JUnit。

\`\`\`json
{
  "schema_version": 1,
  "project_name": "my-cpp-service",
  "toolchain": "gcc-host-container-v1",
  "build_command": "cmake -S . -B build -G Ninja && cmake --build build",
  "test_command": "ctest --test-dir build --output-junit ../reports/ctest.xml",
  "artifact_globs": ["build/my-cpp-service"],
  "dependency_lock_files": ["CMakeLists.txt"],
  "test_report_globs": ["reports/*.xml"]
}
\`\`\`

\`\`\`groovy
@Library('cicd-platform') _
cppToolchainBuild(projectDir: '.', registryCredentialsId: 'ghcr-readonly')
\`\`\`

## Go 镜像里有什么

| 项 | 值 |
|---|---|
| 基础镜像 | 官方 \`golang:1.26.10-bookworm\`，固定多架构 index digest：\`sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb\` |
| 操作系统 | Debian 12 Bookworm |
| Go | 1.26.10 linux/amd64；Dockerfile 核对镜像内 Go 版本，并下载 Go 官方发布包再次校验 SHA256：\`ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5\` |
| C 工具 | 官方基础镜像已提供 gcc、libc6-dev、make；Smoke Test 验证编译器、头文件和真实 cgo 编译 |
| 常用工具 | git、curl、CA 证书由官方 buildpack-deps 构建环境提供，并由 Smoke Test 检查 |
| gotestsum | v1.13.0，固定版本安装；Go Module checksum database 校验依赖内容 |
| 环境变量 | \`GOTOOLCHAIN=local\`、\`GOFLAGS="-trimpath -buildvcs=false"\`，缓存放在 \`/workspace/.cache\` 下 |

这次改动不再在 Go Dockerfile 中执行 \`apt-get\`。之前的 Ubuntu Snapshot 下载发生 HTTP 502/503，增加重试仍不能保证上游服务可用。现在直接使用官方 Go 构建镜像，并通过 digest 固定其 Go、Debian 和预装构建依赖，避免每次构建临时访问 Ubuntu Snapshot。

\`GOTOOLCHAIN=local\` 防止 go 命令偷偷下载别的 Go 版本；\`-buildvcs=false\` 避免项目子目录挂载时没有上层 \`.git\` 导致 \`error obtaining VCS status\`。

## 工具链怎样变成可用（Candidate → Active）

\`go126\` 合并时是 Candidate，没有发布 digest，业务项目此时还不能使用（中央解析器只放行 Active）。

1. 合并到 \`main\`，\`Toolchain Supply Chain\` 工作流构建、冒烟测试、扫描漏洞和许可证、生成 SBOM、推送并 cosign 签名。
2. 在工作流 Summary 和 \`promotion-go126-*.json\` 里拿到 digest。
3. 另开评审 PR，在 \`ci/toolchains.json\` 填 \`digest\`、\`build_context_sha256\`、\`build_definition_sha256\`（计算函数在 \`scripts/ci/toolchain_catalog.py\`）。
4. 业务验证通过后，把 \`status\` 改为 \`active\`。

## 失败怎么办

| 现象 | 判断 | 处理 |
|---|---|---|
| \`Central resolver rejected Toolchain ID\` | 工具链不是 Active，或执行模式不是容器 | 查 \`ci/toolchains.json\` 里该 ID 的 \`status\` |
| \`... is not a go toolchain\` | Go 步骤里写了 gcc 的工具链 ID | 改用 \`cppToolchainBuild\`，或改 \`ci/toolchain.json\` |
| \`Pulled image does not match central Active digest\` | 镜像被替换或拉到别的内容 | 停止构建，联系平台维护者 |
| Go 版本或 SHA256 检查失败 | 基础镜像内版本与声明不一致，或 Go 官方发布包内容不匹配 | 停止构建，核对基础镜像 digest 和 Go 官方发布校验和 |
| 缺少 gcc / 头文件 / make | 基础镜像依赖不满足 | 停止构建，检查固定的官方 Go 镜像，不要在构建时临时 apt install |
| 构建时 \`error obtaining VCS status\` | 没用本镜像的 \`GOFLAGS\` | 确认用的是 \`go126\` 镜像，不要在命令里覆盖 \`GOFLAGS\` |
| 构建失败但没有测试报告 | 失败发生在测试之前 | 正常；\`junit\` 允许空结果，看控制台日志 |
