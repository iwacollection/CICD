# Jenkins Java Toolchain Shared Library

本文说明 CICD 仓库内置的 Jenkins Shared Library 如何把 Java 项目接入中央 Toolchain Registry，并使用固定的 JDK/Maven 容器构建。

> **当前验收边界（2026-10-30）**：PR #47 已合并；Java 17 Candidate 镜像和 Maven 消费示例已在 GitHub Actions 实际通过。本文新增 Shared Library 实现和 Mock Pipeline 测试，但当前没有已连接的企业 Jenkins Controller、Agent、Credentials 或全局 Shared Library 配置，因此不能声称已在真实 Jenkins 执行成功。java17-maven 仍是 Candidate，正式 Shared Library 会 fail closed，不会用 Candidate 冒充 Active。

## 1. 实际代码位置

Jenkins Global Pipeline Library 使用仓库根目录约定：

~~~text
CICD/
├── src/com/iwacollection/cicd/JavaToolchainSpec.groovy
├── vars/javaToolchainBuild.groovy
├── scripts/ci/
│   ├── resolve_toolchain.py
│   ├── run_build.py
│   ├── collect_build_metadata.py
│   ├── package_artifact.py
│   └── verify_artifact.py
├── examples/java17-maven-service/
│   ├── ci/toolchain.json
│   ├── pom.xml
│   └── Jenkinsfile
└── jenkins-shared-library-tests/
    ├── pom.xml
    └── src/test/java/.../JavaToolchainSharedLibraryTest.java
~~~

| 文件 | 职责 |
|---|---|
| vars/javaToolchainBuild.groovy | Jenkins Pipeline 入口，完成信任检查、中央平台检出、镜像验签、拉取、构建和制品归档 |
| src/.../JavaToolchainSpec.groovy | 纯 Groovy 输入校验；不直接访问 Jenkins API |
| resolve_toolchain.py | 唯一权威解析器；校验 Registry、状态、执行模式和 digest |
| run_build.py | 在容器中执行命令，映射 Runner UID，固定 HOME 与 Maven 缓存位置 |
| collect_build_metadata.py | 从实际容器采集 JDK/Maven 版本 |
| package_artifact.py / verify_artifact.py | Artifact Contract v2 生成与校验 |
| jenkins-shared-library-tests | 在 Java 17 构建容器内编译 Groovy 库并模拟 Jenkins Pipeline DSL |

## 2. Jenkins Controller 全局配置

在 Manage Jenkins → System → Global Pipeline Libraries 配置：

| 配置项 | 值 |
|---|---|
| Name | iwacollection-cicd |
| Default version | 经过审核的 CICD 40 位 commit SHA |
| Retrieval method | Modern SCM / Git |
| Project Repository | https://github.com/iwacollection/CICD.git |
| Credentials | 仅仓库为私有时配置只读 checkout 凭据 |
| Load implicitly | 关闭，业务 Jenkinsfile 显式声明 |
| Allow default version to be overridden | 关闭 |

生产环境不要把 Default version 设置成可移动的 main、master 或 latest。升级 Shared Library 应走单独评审，将默认 SHA 更新到经过平台验证的提交。

业务 Jenkinsfile 使用：

~~~groovy
@Library('iwacollection-cicd') _
~~~

Jenkinsfile 不提供任意 Library 版本覆盖权。平台管理员在 Jenkins 全局配置中控制库的版本。

## 3. Agent 前置条件

分配给 Java 项目构建的 Agent 需要：

- Linux x86_64；
- Jenkins Agent 与可用的 Docker CLI / Docker Daemon；
- Git；
- Python 3；
- Cosign（版本由 Agent 镜像或配置管理固定）；
- 能拉取 GHCR Active 工具链镜像的网络；
- 能访问 Maven Central 或企业 Maven 私服；
- 足够的磁盘、内存和并发容量。

建议 Label：

~~~text
linux
docker
java-project-builder
~~~

Jenkins Agent 宿主机不安装多套 JDK。实际 JDK 与 Maven 来自中央工具链镜像。

**安全注意：** Docker Socket 基本等同宿主机高权限能力。不要把不可信 PR/Fork PR 调度到这个 Agent，也不要向其暴露生产发布凭据。本文的 javaToolchainBuild 会拒绝 CHANGE_ID 构建及非受信分支，但真正的安全边界仍须由 Jenkins Job/Folder 权限、可信 Pipeline 定义和 Agent 凭据隔离保证，不能只依赖 Jenkinsfile 自我声明。

## 4. Jenkins Credentials

建议在 Jenkins Credentials 中预先创建：

| Credential ID 示例 | 类型 | 用途 |
|---|---|---|
| cicd-platform-sha | Secret text | Controller 管理的 40 位 CICD commit SHA；必须与 Global Pipeline Library 的 Default version 完全一致，项目 Jenkinsfile 不得设置或覆盖 |
| ghcr-readonly | Username with password | GHCR 只读拉取；用户名为 GitHub 用户/机器人，密码使用仅含 read:packages 的 PAT |
| maven-settings-readonly | Secret file | Maven 私服 settings.xml，只有项目需要内部依赖时才传入 |

不要把 PAT、Maven 密码、settings.xml 或私钥写入 Git、Jenkinsfile 或镜像。

mavenSettingsCredentialsId 是可选参数。Shared Library 会把 Secret File 临时复制到项目工作目录，设置容器内的 MAVEN_ARGS=-s /workspace/.ci-maven-settings.xml，构建完成后删除临时文件。Maven settings 文件不得出现在 Artifact Contract 的 artifact_globs 中。

## 5. 项目仓库声明

项目只声明 Toolchain ID 和构建输入，不写镜像地址或 digest：

~~~json
{
  "schema_version": 1,
  "project_name": "java17-maven-service",
  "toolchain": "java17-maven",
  "build_command": "mvn -B -ntp clean verify",
  "test_command": "",
  "verify_command": "java -jar target/java17-maven-service-1.0.0.jar",
  "artifact_globs": [
    "target/java17-maven-service-1.0.0.jar"
  ],
  "dependency_lock_files": [
    "pom.xml"
  ]
}
~~~

项目声明的命令来自可信分支。不要允许 PR 作者通过可变参数覆盖容器镜像、Registry、平台 SHA、凭据 ID 或 Agent Label。

Maven 构建优先使用 Wrapper 或中央镜像内固定的 Maven 版本，但两者必须明确选择。当前示例项目使用镜像内 Maven 3.10.0。

## 6. Jenkinsfile 调用方式

仓库中的参考文件：examples/java17-maven-service/Jenkinsfile。

最小调用形态：

~~~groovy
@Library('iwacollection-cicd') _

pipeline {
    agent { label 'linux && docker && java-project-builder' }
    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
    }
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        stage('Build') {
            steps {
                script {
                    javaToolchainBuild(
                        projectDir: 'examples/java17-maven-service',
                        toolchainConfig: 'ci/toolchain.json',
                        registryCredentialsId: 'ghcr-readonly'
                    )
                }
            }
        }
    }
}
~~~

单独业务仓库通常把 projectDir 设置为 .，并让自己的仓库根目录包含 ci/toolchain.json。示例 Jenkinsfile 仍位于 CICD monorepo，因此路径指向 examples/java17-maven-service。

CICD 平台 SHA 不由业务 Jenkinsfile 提供。Controller 上的 Secret Text credential `cicd-platform-sha` 必须与 Global Pipeline Library 的 Default version 指向同一个 40 位 commit SHA。Shared Library 先拒绝 PR/非 main 分支，再从该 Controller 管理的凭据取 SHA 并校验格式；业务项目不能选择历史 SHA 回退中央 Registry 状态。中央 Resolver、策略和工具链注册表都从该 SHA 检出。

## 7. 完整调用顺序

~~~text
Trusted Jenkins main job
        |
        v
Checkout business source
        |
        v
Check CHANGE_ID / branch / source commit
        |
        v
Read ci/toolchain.json
  -> validate schema
  -> extract Toolchain ID only
        |
        v
Read controller-managed cicd-platform-sha
  -> exact 40-character commit SHA
  -> checkout CICD at that exact SHA
  -> verify checked-out commit SHA
        |
        v
scripts/ci/resolve_toolchain.py
  -> status must be Active
  -> execution_mode must be container
  -> registry prefix allowlist
  -> image@sha256
        |
        v
Cosign verify exact image digest
        |
        v
docker pull image@sha256
  -> docker inspect RepoDigests exact-match
        |
        v
run_build.py
  -> Docker container
  -> host UID/GID mapping
  -> HOME=/workspace/.ci-home
  -> Maven local repo=/workspace/.cache/maven/repository
  -> optional secret Maven settings
        |
        v
Build / unit tests / runtime smoke
        |
        v
collect_build_metadata.py
        |
        v
package_artifact.py
        |
        v
verify_artifact.py
        |
        v
JUnit reports (finally path, also on failed Maven build)
  -> archiveArtifacts (successful Artifact Contract only)
        |
        v
Cleanup Maven settings + docker logout
~~~

失败关闭条件包括：平台 SHA 不正确、非可信分支、无项目 Toolchain ID、Registry 解析失败、Candidate/Retired 状态、digest 缺失或不匹配、签名验证失败、Docker Daemon 不可用、构建/测试失败、元数据缺失、Artifact Contract 校验失败。

## 8. 当前为什么还不能在 Jenkins 上成功执行生产消费

中央 Registry 中 java17-maven 当前状态仍为：

~~~text
status = candidate
~~~

所以 Shared Library 调用中央 Resolver 时会被拒绝。**这是预期的安全行为，不是 Jenkins 集成故障。** Candidate 目前已通过 GHCR 镜像签名校验、真实 Maven 构建、JUnit、JAR 运行和 Artifact Contract v2 验收，但当前只有平台示例消费者，没有你的真实 Java 业务仓库兼容性证据。

不能为了让 Jenkinsfile 变绿而：
- 在 Jenkinsfile 里硬编码 Candidate 镜像；
- 关闭 Active 检查；
- 让业务项目传入任意 digest；
- 把 Candidate 直接改成 Active。

真实项目验证并经过单独 Promotion PR 后，才能把工具链改成 Active，再运行这条生产消费者流水线。

## 9. 本地/CI 自动化测试

在 Java 17 工具链环境中运行：

~~~bash
mvn --batch-mode --no-transfer-progress \
  -f jenkins-shared-library-tests/pom.xml clean test
~~~

测试包括：

- Groovy Shared Library 源码语法编译；
- 项目声明只含 Toolchain ID；
- Active digest 与 identity 一致性；
- Candidate 被拒绝；
- 模拟完整 Jenkins DSL 调用序列：SCM 固定 SHA、中央 Resolver、Cosign、Docker pull、容器构建、元数据、Artifact Contract、JUnit、制品归档和凭据清理；
- PR 构建在调用 Registry 或 Docker 前被拒绝。

这是 Jenkins Pipeline DSL 的 Mock 测试，不等于真实 Controller/Agent 集成测试。真实集成验收还必须从 Jenkins Controller 触发 Job，确认 Library 加载、SCM checkout、凭据绑定、Docker Agent、Cosign、Maven settings、JUnit 和 Jenkins Artifact Archive 都在真实环境执行成功。

## 10. 当前未完成项

| 项目 | 状态 |
|---|---|
| Shared Library 代码与 Groovy 辅助类 | 已实现，待本 PR CI 验证 |
| Mock Pipeline 调用测试 | 已实现，待本 PR CI 验证 |
| Java 17 容器内运行 Shared Library 测试 | 已加入 Candidate consumer workflow，待执行 |
| Jenkins 全局 Library 配置 | 需要真实 Controller 管理权限 |
| Jenkins Agent Label / Docker / Cosign | 需要真实 Agent |
| Jenkins Credentials | 需要由平台管理员配置 |
| 用户真实 Java 业务仓库接入 | 当前可访问仓库中没有发现真实 Java 服务，仍需仓库地址 |
| Java 17 Candidate → Active | 未执行，等待真实业务项目兼容性验收与 Promotion PR |
