# Go / C++ Jenkins Shared Library 端到端验收

本文定义两个可重复执行的 Jenkins 消费项目，以及从 Go Candidate 发布到 Jenkins 真正消费的验收顺序。**Mock 测试、GitHub Actions 镜像验证、真实 Jenkins 执行是三种不同证据，不能互相替代。**

## 1. 两个真实消费样例

| 语言 | 工程目录 | Jenkins Shared Library 入口 | 中央工具链 |
|---|---|---|---|
| Go | `examples/go-toolchain-consumer` | `goToolchainBuild` | `go126` |
| C++ | `examples/cpp-toolchain-consumer` | `cppToolchainBuild` | `gcc-host-container-v1` |

Go 样例不依赖第三方 Go Module，包含 CGO 能力由工具链 Smoke Test 验证；项目本身验证 go build、gotestsum、JUnit、二进制运行和 Artifact Contract v2。

C++ 样例使用 CMake + Ninja + CTest，验证编译、单元测试、JUnit、可执行文件运行和 Artifact Contract v2。它是独立消费样例，不会改动现有 `hello-lib -> hello-cpp` DAG 示例。

## 2. Go Candidate 发布

Go 工具链当前状态是 Candidate，Shared Library 的中央解析器只允许 Active 工具链进入正式业务构建。因此不能拿 Candidate 直接执行 Jenkins 正式消费流程。

顺序如下：

1. 将本次修复 PR 合并到 `main`。
2. `main` 的 push 自动触发 `Toolchain Supply Chain`。
3. `Verify / go126` 必须全部通过：镜像构建、Go 版本、CGO 构建、纯 Go 构建、go vet、Go 单元测试、JUnit、Trivy 漏洞/许可证策略和 SBOM。
4. `Publish / go126` 必须成功：推送镜像、对精确 digest 做 Smoke Test、扫描、Cosign 签名和签名验证、GitHub provenance。
5. 下载 `toolchain-promotion-go126-<commit>.zip` 中的 `promotion-go126.json`，记录 immutable reference 和摘要字段。

**不要**手工把 tag 当成 digest，也不要在未完成精确 digest 验证时改为 Active。

## 3. Candidate 晋级 Active

Candidate 发布后，另开一个评审 PR 修改 `ci/toolchains.json` 中的 `go126` 项：

- `digest`：填写发布记录里的 sha256 digest。
- `build_context_sha256`：填写该次发布记录中的构建上下文摘要。
- `build_definition_sha256`：填写该次发布记录中的构建定义摘要。
- `status`：在 Candidate 验收证据齐全后改为 `active`。

晋级 PR 必须重新运行工具链目录校验。工具链镜像一经 Active，业务项目只能引用中央目录解析出的 `image@sha256:...`，不能自行传入镜像地址或 tag。

C++ 的 `gcc-host-container-v1` 已是 Active，不需要重新发布或改变其 digest。

## 4. Jenkins 控制器配置

先配置 Jenkins Global Pipeline Libraries：

| 配置项 | 值 |
|---|---|
| Name | `iwacollection-cicd` |
| Default version | 已批准的 CICD `main` 40 位 commit SHA |
| Load implicitly | 关闭 |
| Allow default version to be overridden | 关闭 |
| Retrieval | Git SCM，仓库 `https://github.com/iwacollection/CICD.git` |

控制器 Credentials：

| Credential ID | 类型 | 内容 |
|---|---|---|
| `cicd-platform-sha` | Secret Text | 与 Global Shared Library 完全相同的 40 位 commit SHA |
| `ghcr-readonly` | Username / Password | 只读 GHCR 凭据；不得使用有写权限的发布身份 |

Runner 要求：

- Linux Jenkins Agent，安装 Docker CLI 和可用 Docker Engine/socket。
- Agent 执行用户有受控的 Docker socket 使用权限。
- Go Job 标签：`linux && docker && go-project-builder`。
- C++ Job 标签：`linux && docker && cpp-project-builder`。
- 不要让不可信 PR/fork Job 获得上述凭据或运行正式 Shared Library 步骤。

## 5. 建立两个 Jenkins Job

创建两个 Multibranch Pipeline 或普通 Pipeline Job，均只在可信 `main` 分支运行。

| Job | Script Path |
|---|---|
| Go consumer | `examples/go-toolchain-consumer/Jenkinsfile` |
| C++ consumer | `examples/cpp-toolchain-consumer/Jenkinsfile` |

两个 Jenkinsfile 已经调用对应的 Shared Library 方法。不要在业务 Jenkinsfile 中写镜像 tag、digest、Docker 登录密码或平台 commit SHA。

Go Job 必须等 `go126` 晋级 Active 后执行；C++ Job 可在现有 Active GCC 工具链上先执行。

## 6. 每次真实验收必须保留的证据

每个 Job 至少保存：

1. Jenkins Job URL、Build Number、构建时间和结果。
2. SCM repository、branch、source commit SHA。
3. Shared Library 使用的 CICD commit SHA。
4. 中央 Resolver 解析出的 Toolchain ID、Active 状态和 immutable image digest。
5. Cosign 验证输出、Docker pull digest 校验结果。
6. 容器内编译器版本和实际 build/test/verify 命令。
7. JUnit 测试报告：Go 的 gotestsum XML、C++ 的 CTest XML。
8. Artifact Contract v2 bundle、manifest、SHA256 和 verify_artifact 结果。
9. Jenkins archived artifact / fingerprint。

验收判断：

| 检查 | Go | C++ |
|---|---|---|
| Resolver | go126 / Active | gcc-host-container-v1 / Active |
| 编译 | go build 成功 | CMake/Ninja 成功 |
| 测试 | gotestsum 全绿 | CTest 全绿 |
| 运行 | 输出 go-toolchain-consumer-ok | 输出 cpp-toolchain-consumer-ok |
| JUnit | reports/junit.xml | reports/ctest.xml |
| 制品 | Artifact Contract v2 验证通过 | Artifact Contract v2 验证通过 |

## 7. 当前验收边界

仓库中已准备好两个真实项目样例、Jenkinsfile、项目工具链声明和 Shared Library 配置测试。C++ 样例已在本地 Linux 环境完成 CMake/Ninja/CTest 编译测试；Go 的 CGO 模式回归已用本地 Go 1.23 做了等价验证。

这不代表已经完成真实 Jenkins 验收：当前会话没有连接你的 Jenkins Controller、Agent 或 Jenkins Credentials，也没有实际 Jenkins Build URL。因此在拿到真实 Jenkins 构建记录前，不会把这两条链路标记为“真实 Jenkins 已通过”。

## 8. 故障处理

- Resolver 拒绝 Go：先检查 `go126` 是否已 Active，不能绕过中央状态。
- Cosign identity 错误：检查镜像是否由 `main` 的 `toolchain-images.yml` 发布，不要关闭签名校验。
- Docker digest 不匹配：立即停止构建，不要回退到 tag。
- 没有 JUnit：检查是否在项目声明的 test command 中生成报告；构建在测试前失败时允许空报告，但必须保留失败日志。
- 制品验证失败：不归档为成功制品；检查 source SHA、repository、manifest 和 bundle SHA256。
