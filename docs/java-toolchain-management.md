# Java / JDK 标准工具链管理方案

> 本文是现有 CICD 平台工具链供应链的 Java 扩展，不建立第二套工具链注册表，也不绕过现有 Artifact Contract、Supply-chain Policy、Runner Trust Boundary 和 Promotion/Rollback 规则。

## 1. 目标与范围

为多个 Java 项目提供统一、可复现、可审计的构建环境。Jenkins Agent 宿主机只提供执行能力，不安装多个 JDK 供项目切换。项目只声明中央 Toolchain ID，由平台解析到经过验证、批准且不可变的容器镜像 digest。

覆盖 JDK 8/11/17/21 标准镜像、Maven/Gradle 版本治理、镜像构建与安全扫描、Harbor/GHCR 仓库、Jenkins 调用、项目接入、缓存、制品追溯、升级退役和故障处理。本方案不替代 Java 项目自身的依赖管理、测试策略和部署系统；构建镜像也不等同于业务运行镜像。

## 2. 与当前 CICD 平台的关系

当前仓库已经有中央注册表 ci/toolchains.json、镜像目录 docker/toolchains/、工具链流水线 .github/workflows/toolchain-images.yml、目录校验 scripts/ci/toolchain_catalog.py，以及：

- docs/toolchain-supply-chain.md：Candidate → Active → Retired 生命周期；
- docs/containerized-build-environments.md：Container First / Host Exception；
- docs/build-cache-and-dependencies.md：依赖代理、编译缓存、制品仓库边界；
- docs/runner-security-and-supply-chain.md：Runner 信任边界；
- docs/artifact-contract-v2.md：构建产物身份和校验。

Java 工具链必须复用这些规则。不得另建 Jenkins 私有镜像白名单作为权威来源；不得让业务仓库自行指定任意镜像地址；不得使用 latest 或可变 tag 作为 Active 工具链。

### 当前仓库事实与部署边界

当前 ci/toolchains.json 的允许镜像前缀是 ghcr.io/iwacollection/，现有工具链发布工作流使用 GHCR、GitHub OIDC/Attestation 与 Cosign。当前仓库没有配置企业 Harbor 地址、Harbor 机器人凭据或 Harbor API 扫描轮询适配器。

因此，本文定义 Harbor 作为企业部署时的镜像仓库方案。真实 Harbor 地址、TLS、机器人账号、网络连通性和扫描 API 适配器完成配置前，不得把示例地址写成 Active 镜像。如果继续使用 GHCR，沿用现有发布链路；如果切换 Harbor，必须同时修改注册表允许前缀、镜像登录/推送步骤、扫描状态检查、签名验证和 Runner 拉取凭据。Harbor 与 GHCR 可以做主仓库/灾备副本，但必须以 digest 对齐，不能把同名 tag 当成相同制品。

## 3. 总体架构与职责

~~~text
Java 项目仓库
  ├── pom.xml / Gradle Wrapper
  ├── ci/toolchain.json       只声明 toolchain ID
  └── Jenkinsfile             调用中央流水线/共享库
              |
              v
中央 CICD 平台
  ├── ci/toolchains.json      唯一工具链注册表
  ├── docker/toolchains/java  标准镜像源码
  ├── toolchain-images.yml    构建、验证、发布候选镜像
  └── Supply-chain Policy     漏洞、许可证、SBOM、签名
              |
              v
Harbor 或 GHCR
  ├── Candidate
  ├── Active immutable digest
  └── Retired / 历史版本
              |
              v
Jenkins Agent（只提供执行能力）
  └── 拉取 Active digest，在容器中运行构建
              |
              v
Nexus / Artifactory Maven 依赖代理
              |
              v
Artifact Contract v2 / 长期制品库
  ├── JAR/WAR、测试报告
  └── source + toolchain + artifact digest 元数据
~~~

| 组件 | 职责 | 不应该承担的职责 |
|---|---|---|
| Git 中央 CICD 仓库 | 定义工具链、策略、构建模板和批准状态 | 保存镜像二进制层 |
| 镜像构建流水线 | 构建、测试、扫描、签名、产生候选 digest | 自动把未审核候选设为 Active |
| Harbor/GHCR | 存储镜像、扫描报告、标签与访问控制 | 决定业务项目使用哪个工具链 |
| 中央 Toolchain Registry | 维护状态和精确 digest | 保存秘密或可变 tag |
| Jenkins | 调度、调用工具链、构建项目、归档证据 | 允许项目随意覆盖镜像地址 |
| Maven 私服 | 代理/缓存 Maven 依赖和内部组件 | 充当 Docker 镜像仓库 |
| Artifact Repository | 保存项目 JAR/WAR 和长期构建证据 | 充当依赖缓存或构建镜像仓库 |

## 4. 工具链建模：一个标准组合对应一个镜像

第一阶段支持四套标准 Maven 工具链：

| Toolchain ID | JDK 主版本 | Maven | 用途 |
|---|---:|---|---|
| java8-maven | 8 | 平台批准的固定版本 | 维护旧版 Java 项目 |
| java11-maven | 11 | 平台批准的固定版本 | Java 11 项目 |
| java17-maven | 17 | 平台批准的固定版本 | 新项目基线之一 |
| java21-maven | 21 | 平台批准的固定版本 | 新版项目 |

每个镜像必须进一步固定 JDK 发行版及完整补丁版本、基础操作系统及其 digest、Maven 版本、CA 证书和必要诊断工具、CPU 架构与平台清单、Dockerfile 和基础镜像来源。JDK 17 不是完整可复现的构建环境；不同补丁版本、发行版和基础 OS 都可能造成差异。

### Maven 与 Gradle 版本策略

- 镜像内 Maven：调用 mvn，镜像中固定 Maven 版本，中央平台验证版本。
- Maven Wrapper：调用 ./mvnw，项目提交 Wrapper 配置和 distribution checksum；平台验证下载来源和版本。不能误以为镜像内 Maven 就是实际 Maven。
- Gradle：优先使用项目 Gradle Wrapper，并锁定 Gradle distribution checksum；JDK 镜像提供 JDK 和必要系统工具。
- 不允许流水线一边声称使用镜像 Maven，一边不受控地调用项目下载的另一版本 Maven。

确实存在不同 Maven 版本组合时，新增独立 Toolchain ID，例如 java17-maven-3.9，不覆盖已有镜像。不要为每个小版本无限复制镜像，只在兼容性或供应链治理需要时拆分。

## 5. 仓库目录

~~~text
CICD/
├── ci/toolchains.json
├── docker/toolchains/
│   ├── gcc-host/
│   └── java/
│       ├── maven-jdk8/Dockerfile + smoke-test.sh
│       ├── maven-jdk11/
│       ├── maven-jdk17/
│       └── maven-jdk21/
├── .github/workflows/toolchain-images.yml
├── scripts/ci/toolchain_catalog.py
├── scripts/ci/harbor_scan_gate.py    # Harbor 部署时新增并测试
├── examples/java-maven-service/
│   ├── ci/toolchain.json
│   └── Jenkinsfile
└── docs/java-toolchain-management.md
~~~

Dockerfile 必须使用受控基础镜像并固定 digest。下面仅为结构示例，不能在未确认基础镜像 digest、JDK 补丁版本和企业仓库地址时作为生产版本：

~~~dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ENV LANG=C.UTF-8
WORKDIR /workspace

# 只安装构建所需且经过审核的基础工具。
# 不写入 Maven/Nexus 密码、签名密钥、云凭据或许可证文件。
COPY smoke-test.sh /usr/local/bin/smoke-test
RUN chmod 0755 /usr/local/bin/smoke-test

USER 10001
# 不设置固定 ENTRYPOINT；由受控的 smoke/build 命令显式指定 shell 和参数。
# 避免与现有工具链工作流的 docker run IMAGE bash -euo pipefail -c ... 调用冲突。
~~~

镜像构建必须在受控 pipeline 中传入经审核的 BASE_IMAGE digest。不得使用 ubuntu:latest、eclipse-temurin:17 等浮动引用。若选用官方 Maven+JDK 基础镜像，应先审核 OS、JDK、Maven 版本和来源，再固定 digest。

## 6. 中央 Toolchain Registry

现有 ci/toolchains.json 是唯一权威目录。Java 条目遵守现有字段：

- id：项目声明的逻辑名称；
- status：planned / candidate / active / retired；
- execution_mode：container；
- image：不含 digest 的仓库镜像名；
- digest：Active 必须是完整 sha256；
- dockerfile、context、platforms、smoke_command：构建定义。

可增加 language、jdk_version、jdk_vendor、maven_version、owner、support_until 等元数据，但在 catalog validator 扩展字段校验前，不要假设新增字段已被强制校验。

示例（digest 和仓库路径仅为占位，不可作为真实 Active 配置）：

~~~json
{
  "id": "java17-maven",
  "status": "candidate",
  "execution_mode": "container",
  "description": "Java 17 standard Maven build toolchain",
  "image": "ghcr.io/iwacollection/cicd-toolchain-java17-maven",
  "digest": "",
  "dockerfile": "docker/toolchains/java/maven-jdk17/Dockerfile",
  "context": "docker/toolchains/java/maven-jdk17",
  "platforms": ["linux/amd64"],
  "smoke_command": "java -version && javac -version && mvn -version"
}
~~~

真实镜像发布前，Java 工具链保持 planned。Active digest 必须由发布流水线产出并通过 Promotion PR 写入；禁止人工猜测或复制其他环境的 digest。

## 7. 镜像生命周期：构建与消费分离

~~~text
Planned
  | Dockerfile / toolchain definition PR
  v
Candidate
  | PR: catalog validate + build + smoke + security scan
  | main: publish immutable candidate + digest
  v
Published Candidate
  | exact digest scan + SBOM + signature + provenance
  | representative Java projects compatibility test
  v
Promotion PR
  | update ci/toolchains.json with exact digest
  v
Active
  | project CI may consume exact digest
  v
Deprecated / Retired
~~~

关键约束：PR 验证只使用低权限，不得获得生产 Harbor 推送凭据；只有受信任主分支发布任务能推送候选镜像；推送成功不代表 Harbor 异步扫描完成；扫描失败、超时、数据库过期或报告缺失不能晋级；发布后必须对精确 image@sha256 再做冒烟测试和扫描；Active 只能通过受保护 PR 写入中央注册表；旧 digest 保留在历史记录中，回滚通过恢复旧 digest，不重新构建旧镜像。

## 8. Harbor 项目、权限和策略

企业使用 Harbor 时，建议建立私有项目：

| Harbor Project | 内容 | 写权限 | 读权限 |
|---|---|---|---|
| ci-toolchains-candidate | 候选工具链镜像 | 仅发布流水线机器人 | 安全扫描与审核流水线 |
| ci-toolchains-release | 已批准工具链镜像 | 仅晋级流水线机器人 | Jenkins 构建节点只读 |
| ci-app-runtime | 业务运行镜像 | 应用镜像流水线 | 运行环境按需只读 |

若 Harbor 版本/部署方式不方便跨项目复制，可以先使用单一私有项目，并通过不可变 tag、机器人账号和中央 digest 注册表实现候选/正式隔离；不能为了目录好看而牺牲 digest 校验和权限隔离。

机器人账号建议：
- robot$toolchain-publisher：只对候选项目有 push/pull 权限；
- robot$toolchain-promoter：仅对正式项目有受控复制/推送权限；
- robot$jenkins-pull：正式工具链项目只读；
- robot$scanner：仅具备扫描 API 所需最小权限。

必须开启项目私有、推送后扫描、正式标签不可变、项目配额与容量告警、漏洞严重等级策略、定期全量扫描、漏洞数据库更新与新鲜度监控、保留策略和备份、机器人账号轮换与审计。

Harbor 定时全量扫描、推送后扫描、Trivy 漏洞数据库更新是三件不同的事。定时扫描成功但漏洞库长期未更新，不能视为安全门禁健康。

### Harbor 异步扫描门禁契约

Jenkins/发布流水线不能在 docker push 后立即判定成功。应实现独立适配器 scripts/ci/harbor_scan_gate.py，其输入至少包括 Harbor endpoint、project/repository、artifact digest、最大等待秒数、轮询间隔、允许的漏洞阈值和漏洞数据库最大允许年龄。

适配器必须使用 TLS 验证 Harbor；通过 Jenkins Credentials 注入机器人账号；以 digest 查询 Artifact；轮询 queued/running/success/error/timeout；仅在扫描完成且报告存在时读取漏洞报告；检查扫描器版本、数据库更新时间和报告时间；按组织策略判断 Critical/High 及有依据的 unfixed 例外；输出机器可读 JSON 和审计摘要；任何状态未知、API 失败、超时、报告缺失、数据库过期均 fail closed。不得自动删除镜像或修改 Harbor 策略。

Harbor API 路径、响应结构和认证方式必须针对企业实际 Harbor 版本验证后实现；不要把未经验证的 API 字段写死在 Jenkinsfile 中。

## 9. 安全扫描与供应链证据

工具链镜像至少要有漏洞扫描（OS packages 和支持的语言依赖）、许可证策略、CycloneDX SBOM、镜像签名、构建来源证明、镜像摘要。构建来源证明应关联 source commit、workflow identity、Dockerfile hash、base image digest。消费端验证签名和 Active digest；签名不能替代漏洞扫描，Harbor 扫描结果也不能替代签名与来源证明。

漏洞例外必须记录 CVE、受影响包、理由、风险负责人、批准人、到期日和补救计划。不得用全局忽略 HIGH/CRITICAL 的方式让流水线变绿。

本仓库现有工具链工作流已经采用 Trivy、SBOM、Cosign 和 GitHub provenance 思路。Harbor 部署时需保留这些证据能力。

## 10. Jenkins 执行架构

### Agent 分层

| Agent 池 | 用途 | 权限 |
|---|---|---|
| trusted-toolchain-builder | 构建标准工具链镜像 | 仅受信主分支任务可调度，允许候选仓库推送 |
| java-project-builder | Java 项目构建 | 正式工具链只读、Maven 私服读取 |
| untrusted-pr-builder | 外部 PR / 不可信代码 | 无生产密钥、无 Harbor push、无高权限 Docker socket |
| release-signing-agent | 签名/晋级 | 独立受控身份，短时凭据，审批后执行 |

如果使用 Docker Pipeline 并挂载宿主机 Docker Socket，容器内不可信代码可能控制宿主机 Docker Daemon。不能把“在容器中运行”误认为强隔离。优先采用隔离 VM、短生命周期 Agent 或经验证的 rootless/远程 BuildKit；不可信 PR 不得运行在具有生产凭据的共享构建节点上。

### 消费端完整阶段

~~~text
Checkout
  -> 读取项目 toolchain ID
  -> 从固定版本中央平台解析 Active Registry
  -> 校验 status=active、digest 完整、registry allowlist
  -> 验证签名/来源证明
  -> 拉取 image@sha256
  -> 校验 Java/Maven/Gradle 实际版本
  -> 配置 Maven 私服 settings（凭据临时注入）
  -> 恢复隔离缓存
  -> clean verify / test
  -> 生成 Artifact Contract metadata
  -> 上传 JAR/WAR、测试报告、SBOM、构建元数据
  -> 清理临时凭据与工作目录
~~~

### 项目仓库声明

项目只提交工具链 ID，不允许提交镜像地址：

~~~json
{
  "schema_version": 1,
  "toolchain": "java17-maven",
  "build_command": "mvn -B -ntp clean verify",
  "artifact_globs": ["target/*.jar", "target/*.war"]
}
~~~

中央平台解析后，最终执行必须是 image@sha256。企业 Harbor 环境使用实际 Harbor 地址和同样的 digest 规则。

### Jenkinsfile 示例（消费端）

以下展示 Declarative Pipeline 调用结构。此示例**仅适用于可信分支/受信发布流水线**：Jenkins Job 必须由平台管理员配置为从受保护分支或可信 Pipeline 定义加载，不能仅靠项目仓库内可被 PR 修改的 Jenkinsfile 自我声明可信。assertTrustedBuildContext、resolveToolchain、verifyImageSignature 应由受保护的 Jenkins Shared Library 或平台 CLI 实现，并固定到经过评审的版本；不能由业务仓库自行实现一个永远返回通过的函数。中央注册表解析应读取固定 CICD commit 的 ci/toolchains.json，而不是从可变 main 分支临时下载。外部 PR/Fork PR 必须走另一条由平台控制的 untrusted-pr-builder 流水线，不绑定 Maven settings、发布凭据或签名凭据。

~~~groovy
@Library('company-ci-library@v2.0.0') _

pipeline {
    agent { label 'linux && java-project-builder' }

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
        skipDefaultCheckout(true)
    }

    environment {
        CICD_PLATFORM_REF = 'REPLACE_WITH_APPROVED_40_CHAR_COMMIT_SHA'
        TOOLCHAIN_REGISTRY_URL = 'REPLACE_WITH_APPROVED_REGISTRY_URL'
        TOOLCHAIN_REGISTRY_CREDENTIALS = 'toolchain-registry-pull'
        MAVEN_SETTINGS_ID = 'maven-settings-readonly'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Enforce trusted source') {
            steps {
                script {
                    // 必须读取 Jenkins Controller / 受保护 Job 提供的可信上下文，
                    // 不能相信 PR 可以自行设置的环境变量或仓库文件。
                    assertTrustedBuildContext()
                }
            }
        }

        stage('Resolve approved toolchain') {
            steps {
                script {
                    def project = readJSON(file: 'ci/toolchain.json')
                    if (!project.toolchain) {
                        error('ci/toolchain.json must declare toolchain')
                    }

                    def tc = resolveToolchain(
                        platformRef: env.CICD_PLATFORM_REF,
                        toolchainId: project.toolchain
                    )

                    if (tc.status != 'active' ||
                        !(tc.digest ==~ /^sha256:[0-9a-f]{64}$/)) {
                        error('Toolchain is not Active or digest is invalid')
                    }

                    if (!tc.jdk?.version ||
                        !tc.jdk?.specificationVersion ||
                        !tc.maven?.version ||
                        tc.maven.mode != 'image') {
                        error('Registry must declare the exact image-managed JDK and Maven versions')
                    }

                    env.TOOLCHAIN_ID = tc.id
                    env.TOOLCHAIN_IMAGE = tc.image + '@' + tc.digest
                    env.EXPECTED_JDK_VERSION = tc.jdk.version
                    env.EXPECTED_JDK_SPECIFICATION = tc.jdk.specificationVersion
                    env.EXPECTED_MAVEN_VERSION = tc.maven.version
                }
            }
        }

        stage('Verify image trust') {
            steps {
                script {
                    verifyImageSignature(
                        image: env.TOOLCHAIN_IMAGE,
                        platformRef: env.CICD_PLATFORM_REF
                    )
                }
            }
        }

        stage('Build and test') {
            steps {
                configFileProvider([
                    configFile(
                        fileId: env.MAVEN_SETTINGS_ID,
                        variable: 'MAVEN_SETTINGS'
                    )
                ]) {
                    script {
                        docker.withRegistry(
                            env.TOOLCHAIN_REGISTRY_URL,
                            env.TOOLCHAIN_REGISTRY_CREDENTIALS
                        ) {
                            docker.image(env.TOOLCHAIN_IMAGE).inside {
                                sh '''
                                    set -eu

                                    actual_java_version="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^[[:space:]]*java.version =/ { print $2; exit }')"
                                    actual_java_spec="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^[[:space:]]*java.specification.version =/ { print $2; exit }')"
                                    actual_maven_version="$(mvn -version | awk 'NR == 1 { print $3; exit }')"

                                    test "$actual_java_version" = "$EXPECTED_JDK_VERSION" || {
                                        echo "JDK version mismatch: expected $EXPECTED_JDK_VERSION, got $actual_java_version" >&2
                                        exit 1
                                    }
                                    test "$actual_java_spec" = "$EXPECTED_JDK_SPECIFICATION" || {
                                        echo "JDK specification mismatch: expected $EXPECTED_JDK_SPECIFICATION, got $actual_java_spec" >&2
                                        exit 1
                                    }
                                    test "$actual_maven_version" = "$EXPECTED_MAVEN_VERSION" || {
                                        echo "Maven version mismatch: expected $EXPECTED_MAVEN_VERSION, got $actual_maven_version" >&2
                                        exit 1
                                    }

                                    javac -version
                                    mvn -s "$MAVEN_SETTINGS" -B -ntp clean verify
                                '''
                            }
                        }
                    }
                }
            }
        }

        stage('Package build metadata') {
            steps {
                script {
                    writeFile(
                        file: 'toolchain-build.properties',
                        text: 'toolchain=' + env.TOOLCHAIN_ID + '\n' +
                              'image=' + env.TOOLCHAIN_IMAGE + '\n' +
                              'jdk=' + env.EXPECTED_JDK_VERSION + '\n' +
                              'maven=' + env.EXPECTED_MAVEN_VERSION + '\n' +
                              'git_commit=' + (env.GIT_COMMIT ?: 'unknown') + '\n' +
                              'jenkins_job=' + env.JOB_NAME + '\n' +
                              'jenkins_build=' + env.BUILD_NUMBER + '\n'
                    )
                }
            }
        }
    }

    post {
        always {
            junit(
                testResults: '**/target/surefire-reports/TEST-*.xml',
                allowEmptyResults: false
            )
            archiveArtifacts(
                artifacts: '**/target/*.jar,**/target/*.war,toolchain-build.properties',
                allowEmptyArchive: false,
                fingerprint: true
            )
            cleanWs()
        }
    }
}
~~~

可信流水线才可执行此示例。外部 PR 的 Pipeline 定义必须由中央平台控制，使用独立隔离 Agent，不加载 Maven settings 和任何写凭据；如果外部 PR 依赖私有 Maven 组件，应由经过安全评审的依赖代理方案解决，不能把通用私服密码注入 PR-controlled Maven 插件。即使是只读密码，也可能被项目构建脚本读取和外传。可信上下文校验必须来自 Jenkins Controller / Job 配置，不得仅依据分支名、PR 环境变量或项目 JSON 自我判断。

注意：这是消费端示例，不是无需适配即可部署的完整 Shared Library 实现。Maven Wrapper 项目应调用 ./mvnw，并校验 Wrapper 版本和 checksum；docker.withRegistry 使用只读机器人账号；不把 Harbor 密码通过命令行或构建日志暴露；实际构建必须验证 JDK、Maven/Gradle 版本与中央注册表声明一致。项目构建阶段通常不必再次扫描镜像；镜像安全扫描在发布和定期复扫流程完成，消费端验证签名、digest 和 Active 状态。并发构建应隔离工作空间并使用唯一制品键。

## 11. 标准镜像生产流水线

镜像生产流水线与项目消费流水线分开：

1. PR：校验 JSON、Dockerfile、基础镜像 digest、构建参数和 smoke test；
2. PR：BuildKit 构建临时镜像，不推送正式仓库；
3. PR：Trivy vuln/license 扫描、策略检查、生成 SBOM；
4. 合并受保护分支后：受信任务构建并推送唯一候选 tag；
5. 获取 registry 返回的真实 digest；
6. 对精确 digest pull、smoke test、扫描、签名和 provenance；
7. Harbor 异步扫描时轮询结果，直到完成或超时；失败必须阻断；
8. 生成 promotion record；
9. 创建 Promotion PR，将 Toolchain 从 Candidate 更新为 Active，并写入 digest；
10. 代表性 Java 项目进行兼容性双跑；
11. 合并 Promotion PR 后，项目构建才可消费该 Active digest。

候选 tag 可用于人类识别，但不能作为 Active 消费身份。禁止覆盖历史候选或 Active tag。

## 12. Maven 依赖、缓存和并发

必须区分四类存储：

| 存储 | 用途 | 是否是发布依据 |
|---|---|---|
| Harbor/GHCR | Docker 构建镜像 | 工具链环境身份 |
| Nexus/Artifactory Maven proxy | Maven Central、内部依赖代理 | 依赖来源，需版本与 checksum 治理 |
| Jenkins cache / remote cache | 加速依赖或编译 | 否，丢失只能变慢 |
| Artifact Repository | 保存项目 JAR/WAR | 是，按不可变制品身份晋级 |

Maven 缓存键至少包含 project + JDK toolchain digest + Maven/Wrapper version + pom/dependency lock fingerprint + OS/architecture。不得让不同 JDK、不同项目或不同信任等级任务无边界共享可写缓存。Maven 依赖优先使用 Nexus/Artifactory 代理；依赖版本、校验和与仓库来源应可审计。

同一个项目并发构建时，工作空间必须隔离。发布制品使用 source commit + target + toolchain digest 等身份生成唯一键；相同身份不可覆盖不同 bytes。缓存不是制品仓库。

## 13. 升级、退役和漏洞响应

~~~text
基础镜像/JDK 安全更新
  -> 新 Toolchain Candidate
  -> 镜像构建与 smoke test
  -> 漏洞/许可证扫描
  -> SBOM / 签名 / provenance
  -> 代表项目兼容性双跑
  -> Promotion PR 更新 Active digest
  -> 项目逐步迁移
  -> 旧 digest 进入维护/退役期
~~~

状态：Planned 仅登记计划、不允许消费；Candidate 允许平台构建/验证、不允许普通项目消费；Active 正式批准且必须有完整 digest；Retired 停止新项目引用但历史构建仍可追溯。发生紧急安全风险时，由中央准入策略拒绝具体 digest；在 schema 和 catalog validator 未扩展前，不得把 blocked 当成现有 toolchains.json 的合法状态。

不得原地覆盖旧镜像，不得重新打同一 tag 冒充旧版本。回滚通过中央注册表恢复上一份已验证 digest，并重新走 PR 审核。

每个 CVE 记录 CVE、受影响镜像 digest、包名/版本、严重等级、是否有修复版本、责任人、修复期限、例外到期时间。漏洞库过期、扫描失败、扫描结果缺失应视为安全能力降级，而不是零漏洞。

## 14. 监控、审计与容量

| 指标 | 目的 |
|---|---|
| Toolchain Active digest 数量和变更 | 发现未审批漂移 |
| 镜像扫描成功率、排队时间、失败数 | 发现扫描器故障 |
| 漏洞数据库最后更新时间 | 发现情报过期 |
| Critical/High 未修复与超期数量 | 漏洞 SLA |
| 镜像仓库容量、增长率、垃圾回收结果 | 防止存储耗尽 |
| 镜像 pull 耗时、失败率 | 发现 Agent 到仓库网络问题 |
| Jenkins queue/build P95 | 发现构建资源不足 |
| Maven 下载耗时、缓存命中率 | 发现依赖源问题 |
| 构建失败率、重试率、工具链版本分布 | 发现兼容性问题 |

审计记录关联项目、源码 commit、Jenkins run、Toolchain ID、镜像 digest、签名验证结果、扫描报告身份、最终 Artifact digest 和操作者/审批人。

## 15. 备份、灾备与故障处理

- Git 中央注册表和 Dockerfile：受保护 Git 分支、远端备份；
- Harbor：按实际存储后端备份数据库、配置、镜像数据和证书；定期恢复演练；
- 镜像灾备：复制后比较 digest，确认复制对象一致；
- Jenkins：备份配置即代码、Shared Library 固定版本、凭据恢复流程；
- Maven 私服：备份元数据和必要的内部发布组件；代理缓存可重建，但内部 release 依赖不可丢；
- Agent：可重建，不把宿主机手工安装的 JDK 当成恢复依赖。

| 故障 | 行为 |
|---|---|
| Harbor 不可用 | 已缓存且摘要/签名/Active 状态仍有效的镜像可按离线应急策略使用；未缓存镜像停止构建 |
| 镜像 pull 失败 | 检查 DNS、TLS、Robot 权限、配额、网络和 digest；不回退 latest |
| 扫描超时/失败 | 阻断 Candidate 晋级 |
| 漏洞库过期 | 安全门禁降级并告警；更新数据库后重新扫描 |
| 新 JDK 不兼容 | 回退 Active digest；项目产物不重新编译来伪造回滚 |
| Maven 私服故障 | 已校验缓存可按策略短时继续；缺失依赖时失败，不从未知公网源绕过 |
| Agent 磁盘满 | 停止接新任务、清理过期工作区/缓存，不能删除运行任务数据 |

## 16. 实施顺序与验收标准

### P0：先建立可用主链路
1. 将 Java 8/11/17/21 作为 planned 条目登记；
2. 选定每套镜像的 JDK 发行版、补丁版本、OS、Maven 策略和架构；
3. 建立 Dockerfile 与 smoke test；
4. 扩展现有工具链 workflow 和 catalog，而不是创建第二条流水线；
5. 先选一个 Java 17 项目接入；
6. 项目只能声明 Toolchain ID，不能传镜像地址；
7. Active digest 固定并写入构建元数据。

验收：同一 commit 在两个干净 Agent 上使用相同 digest 构建，工具链版本一致；项目不能选择未登记镜像。

### P1：安全门禁与企业 Harbor
1. 配置 Harbor 项目、Robot、TLS、不可变 tag、配额；
2. 接入 push scan 和定时全量扫描；
3. 实现 Harbor digest 扫描状态轮询适配器；
4. 检查漏洞库新鲜度；
5. 生成 SBOM、签名、provenance；
6. Candidate 到 Active 必须通过 Promotion PR。

验收：扫描排队、失败、超时、报告缺失、数据库过期任一情况都不能晋级；未经批准的镜像无法被项目消费。

### 2026-10-30 实测进度：Java 17 Candidate 已发布

第一套 Java 17 工具链已在主分支实际完成构建、扫描、发布与证据归档。此状态只代表 **Candidate**，不是生产 Active：

| 项目 | 实际结果 |
|---|---|
| Toolchain ID | `java17-maven` |
| JDK | Eclipse Temurin `17.0.21+9` |
| Maven | Apache Maven `3.10.0` |
| Maven 内置 Guava | 替换为 `33.7.2-jre`，并固定制品 SHA256 |
| Base OS | Ubuntu 24.04，APT Snapshot `20261029T000000Z` |
| 镜像仓库 | GHCR |
| 不可变引用 | `ghcr.io/iwacollection/cicd-toolchain-java17-maven@sha256:3b3c558e4a94ab6f71b059ccd7f23ea60a6f76082844b428645015b1413ce9f7` |
| 平台提交 | `c3c386f7b5550371019619918287c1e2beb6d014` |
| Toolchain Supply Chain | Run `41002782086`，成功 |
| Platform Validate | Run `41002782062`，成功 |
| Build Matrix | Run `41002782215`，成功 |
| Promotion record | Artifact `12954466516`，保存源码提交、Dockerfile SHA256、镜像 digest 和验证结果 |

已实测通过的发布门禁：

1. Buildx 构建 Candidate 镜像；
2. 非 root UID 10001 运行；
3. JDK、Maven、Guava 版本精确校验；
4. Maven `clean package` 编译 Java 17 项目、生成 JAR 并运行；
5. 对本地候选镜像执行 Trivy 漏洞与许可证扫描；
6. 执行 HIGH/CRITICAL 供应链策略；
7. 生成 CycloneDX SBOM；
8. 对 GHCR 实际发布的精确 digest 再做 smoke test 和 Trivy 扫描；
9. Cosign keyless 签名及验证；
10. GitHub build provenance attestation；
11. 上传 promotion record。

首次扫描曾发现 Maven 3.10.0 发行包携带的 Guava 33.7.1 命中 HIGH CVE。已在镜像构建时固定替换为修复版本 33.7.2，并在 smoke test 中强制验证镜像内只有该版本。没有降低 HIGH/CRITICAL 门槛，也没有增加 CVE 忽略项。

**尚未完成的晋级条件：** 当前未接入真实 Java 业务项目，尚未执行企业 Jenkins Shared Library / 真实 Agent 项目构建及业务兼容性双跑。因此该镜像必须保持 Candidate，不能标记 Active，也不能让业务项目使用。候选 digest 应写回中央 Registry；只有代表项目兼容性验证和审批完成后，才能通过单独 Promotion PR 将状态改为 Active。

### P2：规模化治理
1. 增加 Java 8/11/21 和 Gradle 项目；
2. 建立代表项目兼容性矩阵；
3. Maven 私服和缓存隔离；
4. 仓库复制、备份恢复、容量告警；
5. 建立漏洞 SLA、退役和回滚演练。

验收：可以回答任意构建使用的源码、JDK、Maven、镜像 digest、扫描结果和最终制品 digest；可以回退到上一已验证 digest。

## 17. 上线前检查清单

- [ ] Java Toolchain ID 已在中央注册表登记；
- [ ] Active 工具链有真实镜像 digest；
- [ ] 基础镜像按 digest 固定；
- [ ] JDK 发行版、补丁版本、Maven/Gradle 策略明确；
- [ ] Dockerfile 和 smoke test 纳入 PR；
- [ ] 只有受信发布任务可 push；
- [ ] Harbor Robot 权限最小化；
- [ ] Harbor push scan 和定时扫描已开启；
- [ ] 漏洞数据库更新时间有监控；
- [ ] 扫描状态轮询与 fail-closed 门禁经过测试；
- [ ] SBOM、签名、provenance 可验证；
- [ ] Jenkins 只读拉取 Active digest；
- [ ] 不可信 PR 与高权限 Docker/发布凭据隔离；
- [ ] Maven 依赖通过企业代理，缓存身份隔离；
- [ ] 构建元数据记录 source/toolchain/artifact digest；
- [ ] Harbor、Git、Maven 私服有备份与恢复演练；
- [ ] 旧版本退役和回滚流程经过演练。

## 18. 最终原则

1. 一个受治理的工具链组合一个镜像，不是一台 Agent 安装多个 JDK 再靠 PATH 切换。
2. 中央注册表是唯一权威来源，Jenkins Shared Library 是执行入口，不复制第二份批准清单。
3. 项目声明 ID，不声明镜像地址。
4. Candidate 发布与 Active 批准分离。
5. 镜像 digest 是身份，tag 只是可读标签。
6. 扫描完成、漏洞库新鲜、签名可信、来源可追溯共同构成镜像晋级条件。
7. 缓存只加速，Artifact 才是交付对象。
8. Docker 不是安全边界替代品；不可信代码不得接触高权限 Runner。
9. 回滚旧 digest，不覆盖旧镜像、不重新构建历史工具链。
10. 所有生产能力必须有真实配置和验收证据；示例地址、占位 digest 和未部署 Harbor 适配器不能标记为已完成。


---

## 19. 生产架构边界：控制面、执行面、依赖面和制品面

前面给出的是逻辑组件。本节明确生产环境中每一层的部署位置和信任边界，避免把“容器里有 JDK”误解为所有 Jenkins 组件都应该使用同一个 JDK。

~~~text
                         ┌─────────────────────────────┐
                         │        控制面                │
                         │ Git / PR / Toolchain Registry│
                         │ Jenkins Controller           │
                         │ Shared Library / Policy      │
                         └──────────────┬──────────────┘
                                        │ 调度与签发短期身份
                                        ▼
┌───────────────────────────────────────────────────────────────────┐
│                         执行面                                     │
│                                                                   │
│  Jenkins Agent Runtime                                            │
│  - 负责 Jenkins Remoting                                           │
│  - 版本按 Jenkins LTS / Agent 要求独立固定                          │
│  - 不承担业务项目 JDK 版本选择                                      │
│                                                                   │
│  临时 Build Executor                                               │
│  - 拉取 Active Toolchain image@sha256                              │
│  - 执行 JDK / Maven / Gradle / 项目测试                             │
│  - Job 结束销毁工作目录、临时凭据和执行容器                           │
└──────────────────────────────┬────────────────────────────────────┘
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
      Harbor / GHCR       Nexus / Artifactory   Artifact Repository
      工具链镜像仓库       Maven 依赖代理          JAR/WAR 和证明材料
             │                 │                  │
             └─────────────────┼──────────────────┘
                               ▼
                     安全与审计面
             IAM / Robot / KMS-HSM / Scanner
             SBOM / Signature / Provenance
             Logs / Metrics / Audit / Alert
~~~

### 19.1 Jenkins Controller 与项目 JDK 必须分离

Jenkins Controller 和 Agent Remoting 本身需要一个满足对应 Jenkins 版本要求的 Java 运行时。这个运行时是 Jenkins 平台依赖，不应该由业务项目的 JDK 版本决定。

例如，某个旧项目需要 JDK 8 编译，并不意味着 Jenkins Controller 或 Agent Remoting 也必须降到 JDK 8。正确做法是：

1. Jenkins Controller 使用平台批准且受支持的运行时。
2. Agent Remoting 使用与当前 Jenkins LTS 兼容的运行时镜像或宿主机运行时。
3. 业务 Build Executor 单独使用 JDK 8、11、17 或 21 工具链镜像。
4. Agent Runtime 与 Build Toolchain 分开升级、分开回滚、分开做漏洞治理。

如果采用 Kubernetes 动态 Agent，可以让 Pod 中的 Agent 容器负责 Remoting，另一个构建容器提供 JDK 工具链，共享受控 workspace；如果采用 Docker Agent，则由隔离 VM 启动短生命周期 Agent/Build 容器。不能让项目通过修改 PATH、JAVA_HOME 或安装脚本改变 Agent Runtime。

### 19.2 构建 JDK 不等于 Java 字节码目标版本

必须区分：

| 概念 | 含义 | 示例 |
|---|---|---|
| Build JDK | 实际执行 javac、Maven、Gradle 的 JDK | JDK 21 |
| Source level | 项目源码语法级别 | Java 17 |
| Target bytecode | 生成的 class 文件目标版本 | Java 17 |
| Runtime JRE/JDK | 业务应用实际运行时 | 与构建 JDK 可以不同 |

新 JDK 可以在受支持条件下编译较旧目标版本，但不能仅凭设置 source/target 就断言兼容。应优先使用编译器的 release 约束，并通过代表性运行时测试验证 API、依赖和字节码兼容性。旧项目迁移必须检查注解处理器、代码生成器、Maven 插件、Gradle 插件和内部依赖，不只检查 pom.xml 中的 Java 版本。

---

## 20. 工具链标准规格：每个 ID 必须描述什么

Toolchain ID 代表一份经过验证的完整构建环境，不只是一个 JDK 主版本。每个工具链正式进入 Candidate 前，平台维护者必须完成以下规格表。

| 规格项 | 必填内容 | 设计约束 |
|---|---|---|
| Toolchain ID | 全局唯一逻辑 ID | 不重复使用已退役 ID 表示不同内容 |
| JDK Vendor | 发行商和发行版 | 记录许可证、更新来源和支持策略 |
| JDK Version | 主版本、完整补丁版本、构建号 | 不允许只写 17 或 21 |
| Base OS | 发行版、版本、包源快照 | 必须可追溯并固定基础镜像 digest |
| Architecture | linux/amd64、linux/arm64 等 | 每个架构分别 smoke test |
| Maven | 固定版本，或明确使用 Wrapper | 不可在同一 ID 下隐式切换 |
| Gradle | Wrapper 版本和 distribution checksum | JDK 镜像不能偷偷覆盖项目 Wrapper |
| Build Tools | Git、CA、zip、unzip 等 | 只安装构建必需工具 |
| Locale / Timezone | 语言、编码、时区 | 避免不同 Agent 因环境变量产生差异 |
| User | 非 root UID/GID | 默认不可写入系统目录 |
| Network | 依赖源与允许访问目标 | 不允许任意公网下载 |
| Owner | 平台责任团队 | 负责维护、漏洞修复和生命周期 |
| Support Policy | 支持截止规则、升级频率 | 根据 Vendor 生命周期和企业支持合同维护 |
| Test Projects | 代表性项目清单 | 晋级前验证真实兼容性 |

### 20.1 JDK Vendor 选择原则

不要只比较 JDK 下载包大小。采购或选择 JDK 发行版时，应比较：

- 是否有稳定的安全更新来源和明确的补丁发布机制；
- 企业是否有商业支持或长期支持合同；
- 授权条款是否允许企业构建环境分发和容器封装；
- 多架构是否都有对应发行版；
- 是否有可验证的校验和、签名和下载来源；
- 是否能在内网制品代理中保存归档版本；
- 漏洞扫描工具能否识别其组件和包元数据。

对生产工具链，平台需要保存下载来源、归档文件 checksum、解包后版本输出和许可证审查结论。不得把“从某个公共下载站下载成功”当作来源验证。

### 20.2 JDK 8 的兼容性与风险策略

JDK 8 属于兼容性工具链，不应自然成为新项目默认版本。平台需要为 JDK 8 单独登记责任项目、业务负责人、依赖插件、最低补丁要求、漏洞例外和退役日期。若供应商不再提供组织认可的安全更新，应明确采取商业支持、隔离环境、迁移计划或停止使用其中一种措施，不能用“构建仍然成功”证明风险可接受。

### 20.3 工具链镜像内容清单

镜像只包含构建需要的内容：

- 固定版本 JDK/JRE Development Kit；
- 根据 Toolchain ID 需要的 Maven 或基础系统依赖；
- CA 证书、时区、UTF-8 Locale；
- 少量诊断工具，例如 shell、tar、gzip、证书检查工具；
- 版本输出和健康检查脚本；
- OCI labels，记录 Toolchain ID、JDK Vendor/Version、Maven Version、源码 commit、Dockerfile hash、基础镜像 digest。

镜像中禁止包含：

- Maven 私服密码、Harbor Robot Secret、GitHub Token；
- SSH 私钥、生产签名密钥、云访问密钥；
- Jenkins Agent Secret；
- 业务源码、构建工作区、编译产物；
- 与构建无关的服务端口、守护进程和启动服务；
- 未审核的工具下载器或启动脚本。

容器默认以非 root 用户运行，根文件系统尽可能只读，workspace 和 Maven cache 使用明确挂载点。镜像启动时不得执行自动升级或自动下载工具的逻辑。

---

## 21. Harbor 企业部署架构：不能只部署一个 Registry 地址

本仓库当前使用 GHCR，不代表已有企业 Harbor。以下是企业采用 Harbor 时的目标逻辑架构；实际部署拓扑、组件扩缩容方式和 API 行为必须以所选 Harbor 版本的官方部署文档和测试环境为准。

~~~text
Jenkins / Build Network
       │
       │ HTTPS + Robot Pull / Push
       ▼
Corporate DNS
       │
       ▼
L4/L7 Load Balancer
       │
       ▼
Harbor Front Door / Core
       ├── Registry API / OCI Manifests
       ├── Token / Project / RBAC
       ├── Job Service
       ├── Web Portal / API
       └── Scanner Adapter
                │
                ├── Vulnerability DB / Scanner
                └── Scan Reports
       │
       ├──────────────► HA Database
       ├──────────────► HA Redis / Cache
       └──────────────► Durable Registry Storage
                              ├── Object Storage
                              └── 或受支持的共享存储方案

Backup / DR Site
       ├── Registry Artifacts / Blobs
       ├── Database Backup
       ├── Harbor Configuration / Certificates
       ├── Robot / RBAC Recovery Procedure
       └── Restore Verification
~~~

### 21.1 Harbor 可用性设计

生产 Harbor 应至少评估以下故障域：

1. 入口层：负载均衡器、DNS、TLS 证书和健康检查。
2. Harbor 服务层：Core、Registry、Job Service、Scanner 等服务的可用性与扩缩容。
3. 元数据层：数据库高可用、备份和恢复。
4. 缓存/队列层：Redis 等依赖的高可用和持久性要求。
5. 镜像数据层：Registry storage 的容量、持久性、吞吐、跨区复制和备份。
6. 扫描层：Scanner 版本、漏洞库更新、队列积压和扫描任务失败。
7. 身份层：Robot 账号、权限、密钥轮换和操作审计。

只备份 Harbor 数据库不等于镜像已经备份；只复制 registry storage 也不保证项目、权限和扫描元数据可恢复。必须按所选部署方式明确数据恢复顺序，并做完整恢复演练。

### 21.2 候选仓库与正式仓库的晋级

推荐逻辑隔离：

- Candidate：仅接收受信工具链发布流水线的写入。
- Release：仅接收经过审批的晋级动作；Jenkins 项目构建只读。
- Runtime：业务运行镜像独立管理，不与 CI Build Toolchain 混用。

晋级可以采用同一 Harbor 内跨项目复制，也可以采用受控复制任务。无论使用哪种方式，复制后都必须对目标仓库重新读取实际 manifest digest，并校验它与晋级记录一致。若 OCI media type、manifest list 或复制实现导致 digest 改变，则把目标对象视为新的内容身份，重新执行扫描和签名验证，不得用 tag 相同代替 digest 一致。

### 21.3 镜像保留与垃圾回收

保留策略必须覆盖：

- 当前 Active digest；
- 当前 Candidate digest；
- 至少一个或多个经过确认的上一稳定版本；
- 仍被历史构建或审计记录引用的镜像；
- 受法律、合规或安全调查要求保留的镜像。

删除镜像前先检查 Toolchain Registry、项目构建历史、Artifact metadata、Promotion record 和灾备副本引用。不要直接用 Harbor 的 tag 保留策略删掉仍被 digest 引用的历史 manifest。Registry garbage collection 只能在官方推荐流程和维护窗口内执行，并在执行前后验证镜像 pull。

---

## 22. 网络访问路径与安全边界

网络策略要围绕“谁主动访问谁、使用什么身份、允许做什么”设计，而不是简单放通 Jenkins 到所有内部网段。

| 来源 | 目标 | 目的 | 权限与限制 |
|---|---|---|---|
| Jenkins Controller | Git / Jenkins API | 拉取作业定义、调度 Agent | 不执行不可信构建脚本 |
| Agent Runtime | Jenkins Controller | Remoting 通信 | 仅 Agent 专用网络与身份 |
| Build Executor | Git Source | 拉取源码 | 只读、按项目授权 |
| Build Executor | Harbor/GHCR | 拉取 Active 工具链 | 正式仓库只读 |
| Toolchain Publisher | Candidate Registry | 推送候选镜像 | 仅受信发布工作流可用 |
| Promotion Job | Release Registry | 复制/晋级镜像 | 仅审批后的发布身份可用 |
| Build Executor | Nexus/Artifactory | 下载 Maven 依赖 | 只读访问，项目/仓库范围受控 |
| Artifact Publisher | Artifact Repository | 上传项目制品 | 独立写权限，不交给普通构建步骤 |
| Scanner | Vulnerability DB Source | 更新漏洞库 | 受控外连代理或离线同步 |
| Signing Job | KMS/HSM | 签名或获取身份 | 短时身份、不可导出密钥优先 |
| Monitoring | Harbor/Jenkins/Nexus | 采集指标和健康状态 | 只读监控身份 |

### 22.1 必须禁止的网络路径

- 业务构建容器不能访问 Kubernetes 管理网、生产数据库、云元数据服务或生产签名服务。
- 外部 PR 构建容器不能访问候选镜像推送接口、正式镜像仓库写接口和制品发布写接口。
- 构建容器不能随意访问公网下载 Maven 依赖或工具安装包；应通过企业依赖代理和受控出口访问。
- Harbor Robot 只读凭据不能被用来访问无关项目。
- 普通构建步骤不能直接访问 KMS/HSM 私钥操作接口。

### 22.2 DNS、TLS 和证书

Harbor、Nexus、Artifact Repository、Jenkins 和 Git 服务统一使用企业 DNS 名称和受信 TLS 证书。Agent 镜像必须包含企业根证书链，但不得通过关闭 TLS 校验解决证书问题。证书续期要有到期监控和演练；Agent 不能长期固定某台 Registry IP。

---

## 23. 完整端到端生命周期与门禁表

真正的闭环不是“镜像构建成功”，而是镜像从定义、生产、审批、消费、追踪、更新到退役，每个阶段都有输入、输出和阻断条件。

| 阶段 | 输入 | 核心动作 | 产出证据 | 失败时的行为 |
|---|---|---|---|---|
| 1. 需求登记 | JDK 版本、项目清单、负责人 | 评估 Vendor、OS、Maven、架构与支持周期 | Toolchain Spec | 规格不完整，不进入构建 |
| 2. 定义 PR | Dockerfile、元数据、测试脚本 | Schema、Dockerfile、基础镜像 digest 检查 | PR 检查记录 | PR 不合并 |
| 3. 候选构建 | 合并后的可信 commit | 固定输入、BuildKit 构建 | Candidate image digest | 不推正式仓库 |
| 4. 镜像自测 | Candidate digest | JDK、javac、Maven、Gradle、用户权限测试 | Smoke report | 不进入扫描晋级 |
| 5. 安全检测 | Candidate digest | CVE、License、SBOM、恶意内容和策略检测 | Scan report / SBOM | 不得晋级 |
| 6. 来源证明 | Candidate digest | 绑定源码、Workflow、Dockerfile、Base image | Signature / Provenance | 不得晋级 |
| 7. 兼容性验证 | Candidate digest、代表项目 | 真实项目 Build/Test 双跑与差异分析 | Compatibility report | 不得晋级 |
| 8. 晋级审批 | 全部候选证据 | 检查门禁、风险例外和责任人 | Promotion PR | 审批未通过，不改变 Active |
| 9. Active 发布 | 合并 Promotion PR | 更新 Registry 的确切 digest | Registry commit | 项目不能消费未 Active |
| 10. 项目构建 | Source commit + Toolchain ID | 解析 Active、验签、构建和测试 | Artifact Contract | 任何身份验证失败即失败 |
| 11. 制品归档 | JAR/WAR、元数据和报告 | 上传不可变制品库 | Artifact digest / archive record | 不进入后续发布 |
| 12. 持续复扫 | Active 和历史保留 digest | 新漏洞库扫描和策略复核 | Scan history / alerts | 紧急风险按准入策略阻断 |
| 13. 版本更新 | 新 JDK / OS / CVE 修复 | 构建新 Candidate、双跑、晋级 | 新 Active digest | 旧版本保持不变 |
| 14. 退役 | 迁移完成、到期或风险决策 | 禁止新引用、保留历史追溯 | Retirement record | 历史构建仍可验证 |
| 15. 恢复/回滚 | 上一稳定 digest 或旧制品 | 恢复 Registry 指针或重新发布已存制品 | Rollback record | 不重新构建历史镜像冒充旧版本 |

### 23.1 Candidate 到 Active 的不可绕过条件

一个 Candidate 必须同时满足：

- Catalog schema 校验通过，Toolchain ID 唯一；
- 基础镜像和工具下载来源可信且已固定；
- 目标架构构建和 smoke test 通过；
- 精确 digest 的安全扫描成功且漏洞库满足新鲜度要求；
- License 策略通过，例外均有负责人和到期时间；
- SBOM 可以解析，组件清单与镜像内容一致；
- 签名和 provenance 可验证，来源身份符合允许策略；
- 代表性 Java 项目构建和测试通过；
- Promotion PR 中的 digest 与所有报告对象完全一致；
- 审批人不是该候选镜像发布任务自身的自动身份。

任何条件不满足都保持 Candidate，不得自动写入 Active。

### 23.2 Active 到 Retired 的条件

退役前必须确认：

1. 所有登记项目已经迁移，或有明确的例外负责人；
2. 新项目默认值已经指向新 Toolchain ID；
3. 历史流水线是否仍需重放以及保留期限；
4. 历史镜像已复制到灾备仓库且 digest 一致；
5. 所有缓存键、文档、模板和 Jenkins Shared Library 默认值已更新；
6. 退役公告和回滚窗口已经通知项目负责人。

Retired 表示停止新消费，不代表可以立即删除镜像。删除需要独立的数据保留审批。

---

## 24. Jenkins Shared Library：中央平台应提供的稳定接口

业务 Jenkinsfile 不应该重复实现 Registry 解析、签名校验、漏洞例外判断和 Artifact metadata 生成。上述逻辑应集中在受保护的 Shared Library 或平台 CLI，并固定到审核过的版本。

建议平台接口契约如下。这里是目标接口，不表示当前仓库或 Jenkins 已经实现这些函数。

| 接口 | 输入 | 返回/副作用 | 必须拒绝的情况 |
|---|---|---|---|
| assertTrustedBuildContext | Jenkins Controller 提供的可信 Job/Revision 上下文 | 可信等级和来源身份 | 外部 PR、Fork、非受保护 Pipeline 定义 |
| resolveToolchain | platform commit SHA、Toolchain ID | image、digest、JDK/Maven 元数据、状态 | ID 不存在、非 Active、未知仓库 |
| verifyToolchain | image@digest、平台身份 | 验签和 provenance 结果 | 签名主体不可信、来源不匹配、摘要不一致 |
| assertToolchainPolicy | Toolchain ID、项目身份 | 准入结果 | Retired、紧急 deny、超出项目授权 |
| withToolchain | 解析后的工具链对象、执行命令 | 在隔离容器执行命令 | 项目试图覆盖镜像、挂载敏感目录 |
| collectBuildEvidence | source、toolchain、dependency、test | 标准 metadata / report | 必填证据缺失 |
| publishArtifact | artifact、metadata、短期发布身份 | 不可变 Artifact reference | 制品覆盖、digest 不匹配 |
| recordBuild | build identity、结果与证据引用 | 审计记录 | 身份字段缺失 |
| requestPromotion | Artifact reference、environment | Promotion request | 证据不全、Artifact 不存在 |

### 24.1 resolveToolchain 的返回对象

建议所有项目使用同一种返回结构，避免某个 Jenkinsfile 读 jdkVersion，另一个读 java_version。

~~~json
{
  "id": "java17-maven",
  "status": "active",
  "image": "registry.example.invalid/ci/java17-maven",
  "digest": "sha256:REPLACE_WITH_REAL_64_HEX_DIGEST",
  "jdk": {
    "vendor": "APPROVED_VENDOR",
    "version": "17.0.x+build",
    "specificationVersion": "17"
  },
  "maven": {
    "mode": "image",
    "version": "3.9.x"
  },
  "platforms": ["linux/amd64"],
  "registry_commit": "REPLACE_WITH_40_CHAR_COMMIT_SHA",
  "support_policy": "java17-standard"
}
~~~

上例仓库域名、digest、版本均为占位。正式接口必须返回真实字段，并由 Schema 校验；不得把占位字符串作为可消费镜像。

### 24.2 项目不能覆盖平台决策

业务项目可以声明：

- Toolchain ID；
- 构建目标、测试目标；
- 制品路径；
- 项目级非敏感构建参数；
- 需要的架构和资源规格。

业务项目不能声明或覆盖：

- 任意 registry/image/digest；
- 签名验证结果；
- 是否跳过漏洞门禁；
- Harbor Robot 凭据；
- 生产制品发布凭据；
- 受信 Runner label；
- 中央 Shared Library 版本；
- 生产网络访问范围。

中央平台若允许项目有例外参数，必须设置 Schema allowlist、默认值、范围校验和审批策略。

---

## 25. Java 项目接入与迁移流程

不能一次性把所有项目从宿主机 JDK 切到容器。应先做项目清单和差异盘点，再分批迁移。

### 25.1 接入前盘点

每个项目必须回答：

| 盘点项 | 检查内容 |
|---|---|
| Java 版本 | 当前构建 JDK、目标字节码版本、运行时版本 |
| 构建系统 | Maven、Maven Wrapper、Gradle、Gradle Wrapper |
| 插件 | 编译、打包、测试、代码生成和质量扫描插件 |
| 依赖 | 私有依赖、第三方依赖、快照依赖、动态版本 |
| 操作系统 | 是否依赖宿主机路径、系统库、字体、时区或命令行工具 |
| 架构 | 是否包含 native library 或 JNI |
| 凭据 | 是否需要 Maven 私服读取、签名、发布或部署密钥 |
| 测试 | 单元测试、集成测试、容器测试、数据库和中间件依赖 |
| 制品 | JAR/WAR、附属文件、SBOM、测试报告和归档规则 |
| 运行约束 | 是否要求特定 JVM 参数或特定 JRE/JDK |

### 25.2 迁移步骤

1. 记录现有构建主机的 JDK Vendor、完整版本、Maven/Gradle 版本和系统依赖。
2. 选择匹配的 Candidate 工具链，不要先升级 JDK 再同时迁移环境。
3. 在隔离 Agent 上运行相同 source commit，比较构建结果和测试结果。
4. 逐项处理环境差异，包括证书、时区、文件权限、系统依赖、Maven settings 和 Wrapper。
5. 对比 JAR/WAR 内容、构建元数据、测试报告和关键行为。对不具备可重复构建条件的项目，先记录已知差异。
6. 同一项目在旧环境与新镜像并行一段观察期。
7. 项目负责人确认后，将项目声明切换到 Toolchain ID。
8. 监控构建成功率、耗时、依赖下载时间和失败原因。
9. 观察期结束后关闭旧 Agent 的 JDK 安装需求，保留必要恢复路径。

迁移回退只回到已批准的旧工具链或旧执行路径，不允许 Jenkinsfile 临时写一个任意镜像地址绕过中央注册表。

### 25.3 项目验收标准

- 项目仓库只有 Toolchain ID，不出现任意工具链镜像地址；
- 同一提交在两个干净 Agent 上解析出相同 image@digest；
- JDK、Maven/Gradle 实际版本与中央声明一致；
- 依赖只能来自批准的代理仓库；
- 缓存丢失不会改变构建正确性；
- 构建产物有 SHA256 和完整元数据；
- 不可信 PR 无法获取发布凭据；
- 项目负责人能使用文档完成首次接入和回滚。

---

## 26. Maven / Gradle 依赖、缓存和并发治理细则

### 26.1 依赖源治理

Maven Central 和其他第三方仓库应通过 Nexus/Artifactory 代理。内部 release 组件发布到受控 hosted repository；snapshot 仓库应与 release 仓库分离，防止快照组件污染稳定构建。

建议策略：

- 优先使用固定依赖版本，不允许生产构建依赖无版本上限的动态版本；
- 对关键依赖维护 dependency lock 或等价的版本约束；
- 对 Maven Wrapper、Gradle Wrapper 校验 distribution URL 和 checksum；
- 对外部依赖记录仓库来源和校验信息；
- 对关键依赖保留可审计的代理缓存或内部镜像；
- 禁止在下载失败时静默切换未知公网源。

### 26.2 缓存分层

| 缓存 | 内容 | 隔离要求 |
|---|---|---|
| Maven local repository | 下载的依赖和插件 | 按项目、信任等级、Toolchain digest 分区 |
| Gradle user home | Gradle distribution 和 dependency cache | 按 Wrapper、项目和信任等级隔离 |
| Build cache | 可复用的任务输出 | 只允许受信任务写入可信共享缓存 |
| Docker layer cache | 工具链镜像层 | 由镜像构建流水线管理 |
| Workspace | 当前源码与临时构建文件 | 每次 Job 独立，不跨不可信项目复用 |

缓存必须被视为非权威、可丢弃的性能优化。缓存命中时也必须执行依赖校验和及构建输入身份校验。来自不可信 PR 的写缓存不能被受信任发布构建直接消费，否则攻击者可能通过缓存投毒影响正式构建。

### 26.3 并发与资源控制

- 同一个 Agent Executor 同一时刻只运行一个具有可写 workspace 的构建任务，除非平台已验证 workspace 隔离。
- 对 Maven/Gradle 并行度设置 CPU、内存和 I/O 上限，防止同一宿主机上多个项目相互争抢。
- 使用 Jenkins timeout 和合理的并发策略；不要用无限制重试掩盖依赖源故障。
- Maven 下载缓存与最终制品上传必须分开计量。
- 缓存回收不能删除正在运行的 Job 使用的数据。
- 大项目的 Maven/Gradle 缓存预热必须以 Toolchain digest 和依赖 lock 为身份，不以项目分支名作为唯一身份。

---

## 27. Artifact Contract：工具链身份必须进入最终制品元数据

工具链镜像是构建环境，JAR/WAR 是业务制品。两者必须建立可验证关联，但不能混为一个仓库或一个生命周期。

一个完整构建至少应记录：

~~~json
{
  "schema_version": 2,
  "project": "example-service",
  "target": "linux-amd64",
  "source": {
    "repository": "REPLACE_WITH_SOURCE_REPOSITORY",
    "commit": "REPLACE_WITH_40_CHAR_SOURCE_COMMIT",
    "ref": "refs/heads/main"
  },
  "toolchain": {
    "id": "java17-maven",
    "image": "registry.example.invalid/ci/java17-maven",
    "digest": "sha256:REPLACE_WITH_REAL_DIGEST",
    "jdk_vendor": "APPROVED_VENDOR",
    "jdk_version": "17.0.x+build",
    "maven_mode": "wrapper",
    "maven_version": "3.9.x"
  },
  "dependencies": {
    "pom_sha256": "REPLACE_WITH_SHA256",
    "wrapper_properties_sha256": "REPLACE_WITH_SHA256",
    "dependency_lock_sha256": "REPLACE_WITH_SHA256"
  },
  "execution": {
    "builder_identity": "REPLACE_WITH_TRUSTED_BUILDER_ID",
    "jenkins_job": "REPLACE_WITH_JOB",
    "build_number": "REPLACE_WITH_BUILD_NUMBER",
    "platform_commit": "REPLACE_WITH_40_CHAR_CICD_COMMIT"
  },
  "tests": {
    "unit": "passed",
    "integration": "passed",
    "report_sha256": "REPLACE_WITH_SHA256"
  },
  "outputs": [
    {
      "path": "target/example-service.jar",
      "sha256": "REPLACE_WITH_SHA256",
      "media_type": "application/java-archive"
    }
  ]
}
~~~

这个示例用于说明字段关系，不表示上述 JSON 已经是当前仓库 Artifact Contract v2 的精确 Schema。正式落地时必须复用当前仓库的 Artifact Contract validator 和既有字段名，不能创造一份与平台冲突的第二套制品契约。

### 27.1 Build once，环境晋级不重建

正确流程：

~~~text
Source Commit
   |
   v
Resolve Active Toolchain Digest
   |
   v
Build + Test
   |
   v
JAR/WAR + Artifact Metadata + SHA256
   |
   v
Immutable Artifact Repository
   |
   +--> dev
   |
   +--> staging
   |
   +--> production
~~~

同一个制品在 dev、staging、production 之间晋级。环境差异通过配置和部署参数管理，不能在每个环境重新编译出一个“相同版本”制品。需要重建时，应产生新的 Artifact identity，并重新经过构建、测试和审计链路。

---

## 28. 凭据与身份治理：不要把只读密码误当成无风险

即使 Maven 仓库只有读权限，项目构建脚本仍然可以读取构建进程可见的环境变量、settings.xml 或挂载文件，并将内容发送到其他网络目标。因此“只读凭据”仍然需要谨慎管理。

### 28.1 凭据分层

| 身份 | 使用位置 | 权限 | 禁止用途 |
|---|---|---|---|
| SCM Read Identity | Checkout | 读取授权源码 | 不得写代码或创建 Release |
| Registry Pull Robot | Build Executor | 只读 Active toolchain | 不得推送镜像 |
| Registry Publish Robot | Toolchain Publish Job | 写 Candidate | 不得访问 Release 凭据 |
| Registry Promotion Identity | Promotion Job | Candidate 到 Release 受控复制 | 不得由普通项目构建调用 |
| Maven Read Identity | 依赖下载 | 只读指定仓库 | 不得上传 release 组件 |
| Artifact Publish Identity | 制品归档阶段 | 上传指定项目的不可变制品 | 不得修改历史制品 |
| Signing Identity | 签名服务 | 对满足策略的摘要签名 | 不导出私钥，不暴露给项目脚本 |
| Harbor Scanner Identity | 扫描适配器 | 读取目标扫描状态与报告 | 不得修改项目权限和删除镜像 |

### 28.2 凭据生命周期

1. 凭据保存在 Jenkins Credentials、Vault 或云 Secret Manager，不放入 Git 和镜像。
2. 优先使用 OIDC 或短期凭据；必须使用静态 Robot Secret 时设置轮换周期和审计。
3. 凭据只能在需要的阶段注入，阶段结束后撤销或失效。
4. 不使用 echo、set -x 或异常堆栈打印秘密。
5. 不把同一个身份同时用于 Pull、Push、Sign、Deploy。
6. 轮换时先验证新凭据，再切换消费者，最后撤销旧凭据。
7. 定期检查闲置 Robot、离职人员授权、过期 Token 和仓库成员权限。

### 28.3 不可信 PR 的限制

不可信 PR 构建没有发布凭据、没有签名凭据、没有生产 Kubernetes/云权限，也不能写入可信共享缓存。若它必须下载私有依赖，采用最小范围、短时、只读身份，并限制网络出口；对高敏感内部依赖，应使用代理服务端身份或隔离执行方式，避免将通用私服密码直接交给任意构建脚本。

---

## 29. 监控、告警与容量目标

监控不能只看 Jenkins Build Success。要分别观察 Registry、Scanner、Agent、Maven 代理、Artifact Repository 和凭据服务。

| 层级 | 建议指标 | 典型告警信号 |
|---|---|---|
| Jenkins | Queue Time、Build Duration、失败率、重试率、Agent Online、Executor 使用率 | 排队持续增加、Agent 频繁掉线 |
| Toolchain | Active digest 数、工具链版本分布、Candidate 停留时间 | Active 未按期升级、Candidate 长期无人处理 |
| Harbor/GHCR | Pull latency、Push failure、Registry 5xx、容量、复制延迟 | 镜像拉取失败、存储逼近阈值 |
| Scanner | Scan queue、扫描耗时、失败率、DB 更新时间、未处理任务 | 漏洞库过期、队列积压、报告缺失 |
| Maven Proxy | 请求成功率、下载耗时、缓存命中率、上游错误率 | 依赖下载突然变慢或上游异常 |
| Artifact Repository | 上传失败、读取失败、对象校验失败、容量 | 构建完成但制品没有归档 |
| Security | Token 过期、Robot 使用异常、越权尝试、签名失败 | 非预期身份访问或签名主体变化 |
| DR | 备份成功率、复制延迟、最近恢复演练时间 | 备份任务失败、灾备 digest 不一致 |

SLO 数字应由业务构建频率、允许中断时间和现有平台基线确定，不应在没有测量数据时凭空填写。至少分别定义：

- 工具链镜像可拉取性；
- Maven 依赖服务可用性；
- 构建 Agent 调度成功率；
- Artifact 归档成功率；
- 漏洞扫描门禁可用性；
- 灾备副本可恢复性。

安全门禁故障与业务构建故障要区分统计。扫描服务不可用不代表镜像安全，应触发安全门禁告警和候选晋级暂停。

---

## 30. 生产故障处理手册

### 30.1 Jenkins 找不到或拉不到工具链镜像

**现象：** Job 在 Resolve 或 Pull 阶段失败，项目代码尚未开始编译。

**证据：**
- Toolchain ID 是否存在、状态是否 Active；
- Registry commit 是否固定到预期 SHA；
- Registry 里的 image 与 digest 是否匹配；
- Agent 是否能解析 Harbor DNS，TLS 证书是否可信；
- Pull Robot 是否有目标项目只读权限；
- Harbor 是否限流、配额耗尽或 Registry 存储异常。

**处理：**
1. 先判定是 Registry 元数据错误、网络/TLS 错误还是认证错误。
2. 只修复对应权限或网络，不改用 latest。
3. 检查同一个 digest 是否存在于灾备仓库。
4. 使用灾备副本前先比较真实 digest，并验证签名。
5. 记录故障时间、项目、Toolchain ID、digest 和处理变更。

**恢复验证：** 在干净 Agent 上用相同 image@digest pull 并运行版本 smoke test；确认 Jenkins Build metadata 仍记录预期 digest。

### 30.2 Harbor 扫描排队或超时

**现象：** 镜像已经 Push，但扫描门禁一直处于 queued/running 或无法取得报告。

**证据：**
- Harbor Artifact digest 是否正确；
- 扫描任务状态与最后更新时间；
- Scanner 健康状态、任务队列和资源利用率；
- 漏洞数据库更新时间；
- API 响应码、响应体和请求关联 ID；
- 是否扫描错了 tag 或 repository，而不是目标 digest。

**处理：**
1. 不重复推送同一镜像以“重试”扫描。
2. 根据 Harbor 版本和扫描器日志定位队列或服务故障。
3. 修复 Scanner/DB 后，对原 digest 重新触发扫描。
4. 扫描报告完整且 DB 新鲜后，再运行门禁。
5. 超时、Unknown、Error、报告缺失一律保持不可晋级。

**恢复验证：** 对原 digest 获取成功扫描报告，确认报告 digest 与 Candidate digest 一致，保存扫描器版本与漏洞库更新时间。

### 30.3 新 JDK 工具链导致项目构建失败

**现象：** 旧工具链成功，新 Candidate 失败或生成差异制品。

**证据：**
- 实际 JDK、Maven/Gradle 和 Wrapper 版本；
- 编译参数、系统属性、环境变量、Locale 和 Timezone；
- 插件版本、注解处理器、依赖锁；
- 测试报告和构建日志；
- 新旧镜像的基础 OS、CA 和系统包差异。

**处理：**
1. 不直接更新 Active digest。
2. 在 Candidate 复现并缩小差异。
3. 如果是工具链缺陷，修复后生成新 Candidate digest。
4. 如果仅个别项目不兼容，保留旧 Toolchain ID 并给出迁移计划。
5. 完成代表项目双跑和 Promotion PR 后再切换。

**恢复验证：** 项目再次使用旧 Active digest 构建成功，产物身份与此前已知版本一致。

### 30.4 Maven 私服故障或依赖被污染

**现象：** 多项目依赖下载失败、同坐标依赖 checksum 不一致、构建结果突然变化。

**证据：**
- Maven Proxy 响应码、下载 URL、checksum；
- 依赖版本是否为动态版本或 SNAPSHOT；
- 本地缓存与远程仓库是否不同；
- 受信和不可信 Job 是否共用可写缓存；
- 仓库是否允许覆盖 release 组件。

**处理：**
1. 暂停受影响依赖的发布和消费。
2. 使用 checksum 确认依赖内容，不直接清空所有缓存作为第一步。
3. 对污染缓存进行隔离和失效处理。
4. 从可信上游重新同步依赖，记录修复后的 checksum。
5. 将受污染依赖纳入供应链事件调查。

**恢复验证：** 干净 Agent 从批准代理仓库下载依赖，checksum 与批准记录一致，项目构建通过。

### 30.5 Active 工具链出现严重漏洞

**现象：** 新漏洞情报命中 Active JDK、OS 包或 Maven 插件/依赖。

**处理顺序：**
1. 获取受影响的精确 image digest 和包版本，不按 tag 猜测。
2. 评估是否存在可利用路径、运行暴露面和修复版本。
3. 对存在紧急风险的 digest 执行中央准入阻断，阻止新构建继续消费。
4. 建立修复 Candidate，重新扫描、签名、双跑并审批晋级。
5. 已构建业务制品是否需要重新构建或重新发布，由应用风险评估决定，不自动删除历史制品。
6. 记录例外负责人、期限、影响项目和完成证据。

**恢复验证：** 新 Active digest 无对应未处置风险；旧 digest 已按策略阻止新消费或完成有审批的例外；项目构建元数据可定位受影响范围。

---

## 31. 备份、灾备和恢复演练

### 31.1 需要保护的数据

| 数据 | 是否必须备份 | 恢复要求 |
|---|---|---|
| Toolchain Registry / Dockerfile / Policy | 是 | Git 版本与远端备份可恢复 |
| Harbor 数据库 | 是 | 恢复项目、Artifact 元数据、权限等 |
| Harbor Registry Storage | 是 | 镜像 manifest 和 layer 可读取 |
| Harbor 配置、TLS、Robot 恢复材料 | 是 | 权限和身份可重新建立 |
| SBOM、扫描报告、签名和 Provenance | 是 | 能重新定位并验证历史 digest |
| Maven 私有 release 组件 | 是 | 内部依赖可恢复 |
| Maven 代理缓存 | 可重建 | 不得作为唯一依赖来源 |
| Artifact Repository | 是 | 已发布业务制品可恢复 |
| Jenkins 配置即代码和 Shared Library | 是 | 可在新 Controller 恢复 Job 定义 |
| Jenkins Workspace 和临时 Cache | 通常不备份 | 可丢弃并重新构建 |

### 31.2 Harbor 恢复顺序

恢复流程应依据实际 Harbor 部署与存储后端形成经验证的 Runbook。逻辑顺序通常包括：

1. 恢复网络、DNS、TLS 和入口层。
2. 恢复数据库、Redis 等依赖服务。
3. 恢复 Harbor 配置、项目与访问控制。
4. 恢复 registry data 或连接到已恢复的对象存储。
5. 恢复 Scanner 配置和漏洞数据库更新能力。
6. 校验项目、Artifact、manifest、blob 和 robot 权限。
7. 对关键 Active digest 执行 pull、manifest digest 比对和签名验证。
8. 仅在验证通过后重新开放 Jenkins 构建流量。

### 31.3 灾备切换与回切

灾备仓库不能只依赖“同名 tag”。必须同步并验证：
- Toolchain ID 到 image@digest 的 Registry 映射；
- 候选和正式仓库的身份关系；
- OCI Manifest / Manifest List；
- SBOM、Signature、Provenance；
- 保留策略和扫描历史；
- Robot/只读凭据的恢复与轮换。

切换到灾备仓库时，项目仍然解析 Toolchain ID，由平台根据受控 Registry endpoint 映射到灾备副本。项目仓库不能自行修改镜像地址。回切主仓库前再次比较 digest，并保留故障期间产生的审计记录。

RTO/RPO 必须根据实际 Harbor 规模、存储架构和业务要求通过演练测量，不能只在文档中指定一个未验证数字。

---

## 32. 生产验收测试矩阵

验收必须同时包含正常路径和拒绝路径。只证明“一个 Java 项目构建成功”，无法证明供应链闭环有效。

| 编号 | 测试场景 | 预期结果 | 证据 |
|---|---|---|---|
| T01 | 注册表中不存在的 Toolchain ID | Resolve 失败 | Resolve log |
| T02 | Toolchain 状态为 planned | 项目构建拒绝 | Policy decision |
| T03 | Toolchain 状态为 candidate | 项目构建拒绝 | Policy decision |
| T04 | Active 没有完整 SHA256 digest | 校验失败 | Catalog validation |
| T05 | 项目提交任意 image 字段 | Schema/Policy 拒绝 | PR check |
| T06 | 镜像签名主体不是受信发布工作流 | 验签失败 | Signature verification |
| T07 | Provenance source commit 与批准记录不一致 | 晋级拒绝 | Provenance check |
| T08 | Harbor 扫描状态为 queued 且超时 | 晋级拒绝 | Scan gate report |
| T09 | Harbor 扫描 API 错误或报告缺失 | 晋级拒绝 | Scan gate report |
| T10 | 漏洞数据库超过允许最大年龄 | 晋级拒绝 | Scanner freshness evidence |
| T11 | Candidate 存在超策略 Critical/High | 晋级拒绝或要求正式例外 | Vulnerability policy report |
| T12 | Candidate 复制到 Release 后 digest 不同 | 晋级失败并重新验证 | Copy verification |
| T13 | Maven 依赖代理不可用 | 按缓存与应急策略处理，不绕过未知公网源 | Build log |
| T14 | 不可信 PR 尝试读取发布凭据 | 不存在可读凭据，访问失败 | Credential boundary test |
| T15 | 不可信 PR 写入可信共享缓存 | 权限拒绝或隔离缓存 | Cache access evidence |
| T16 | 工具链升级后代表项目失败 | Active 不变 | Compatibility report |
| T17 | Agent 本地没有镜像缓存 | 仍按 digest 从 Registry 拉取成功 | Clean Agent test |
| T18 | Registry 主站故障 | 按灾备策略恢复或停止新构建，不回退 latest | DR drill record |
| T19 | 回滚 Active 到上一验证 digest | 新 Job 使用旧 digest，历史证据保留 | Promotion/Rollback record |
| T20 | 同一源码在两个干净 Agent 构建 | 工具链身份和输入记录一致，差异有解释 | Build metadata comparison |
| T21 | 归档成功后进入 staging/production | 使用同一个 Artifact digest，不重新编译 | Deployment lineage |
| T22 | 删除旧镜像前执行引用检查 | 被历史构建引用的镜像不能误删 | Retention audit |

### 32.1 验收证据最小集合

每次工具链晋级至少归档：

1. Toolchain Definition PR 和合并 commit；
2. Dockerfile hash、基础镜像 digest、下载物 checksum；
3. 构建工作流运行记录和 Candidate digest；
4. Smoke Test 报告；
5. Vulnerability / License 扫描报告与漏洞库更新时间；
6. SBOM；
7. Signature 和 Provenance 验证结果；
8. 代表项目兼容性测试报告；
9. Promotion PR 和审批记录；
10. Active Registry commit；
11. 灾备副本 digest 校验结果。

每个 Java 项目构建至少归档：

1. Source repository + commit；
2. Toolchain ID + registry commit + image digest；
3. JDK/Maven/Gradle 实际版本；
4. Wrapper 和依赖锁 checksum；
5. Jenkins Job / Build ID / Agent 身份；
6. 测试报告；
7. JAR/WAR SHA256；
8. Artifact Repository reference；
9. 后续环境晋级记录。

---

## 33. 分阶段落地计划与退出条件

此处是建议实施路线，不代表这些能力已经在当前仓库或企业环境中运行。

### Phase 0：架构冻结与现状确认

交付：
- Java 项目清单和 JDK/Maven/Gradle 现状；
- JDK Vendor 和 OS 选择；
- 目标 Registry 决策（先沿用 GHCR，还是企业 Harbor）；
- Jenkins Controller、Agent Runtime、Build Executor 的部署模型；
- Nexus/Artifact Repository 和凭据边界确认。

退出条件：关键依赖、账号、网络和镜像来源有负责人，所有占位值有明确的待配置清单。

### Phase 1：最小可用工具链

只选择一个代表性 Java 17 项目，不同时迁移所有项目。

交付：
- 一套标准镜像 Dockerfile；
- smoke-test；
- Toolchain Spec；
- Candidate 发布和真实 digest；
- SBOM、扫描、签名与 provenance；
- Promotion PR；
- 项目只声明 Toolchain ID；
- 完整 Artifact metadata。

退出条件：同一 commit 在两个干净 Agent 上成功构建；工具链可验证、可追溯、可回滚。

### Phase 2：扩展 JDK 矩阵

逐一增加 JDK 8、11、21 和 Gradle 组合，按项目兼容性需求增加，不为凑齐版本而构建无使用场景的镜像。

退出条件：每个 Active Toolchain 都有 owner、支持策略、代表项目和定期更新计划。

### Phase 3：企业 Harbor 化与灾备

在企业实际 Harbor 中配置项目、Robot、TLS、存储、扫描器、镜像不可变策略、备份和灾备；实现匹配该 Harbor 版本的扫描状态适配器。

退出条件：主仓库与灾备仓库的精确 digest 一致；扫描失败、超时、缺报告、数据库过期等负向用例全部阻断晋级；完成一次恢复演练。

### Phase 4：规模化运营

交付：
- Shared Library 标准入口；
- 项目接入手册；
- 版本兼容矩阵；
- 缓存与 Maven Proxy 运营策略；
- 漏洞 SLA 和升级窗口；
- 监控、告警和容量基线；
- 故障与回滚演练。

退出条件：平台团队可以独立维护工具链，项目团队能按文档自助接入，构建身份和制品身份可审计。

---

## 34. 当前仓库事实、目标架构和未完成事项必须分开

| 能力 | 当前仓库可确认的状态 | 本方案目标 | 尚未完成的内容 |
|---|---|---|---|
| 中央工具链注册表 | 已有 `ci/toolchains.json`；Java 17 Candidate 已登记并固定已发布 digest | 继续复用唯一 Registry | 未完成 Candidate → Active Promotion |
| 工具链矩阵 | 只构建/发布 digest 为空的 Candidate；Active 与已发布 Candidate 不会被无关提交重建 | 保持候选构建与正式消费解耦 | Active digest 的定期远端复扫需独立安全运营任务 |
| GHCR 工具链发布 | Java 17 Candidate 已真实发布，digest 为 `sha256:3b3c558e…13ce9f7`；远端 smoke、扫描、签名、Provenance 均成功 | 复用现有供应链能力 | 无 |
| Java JDK Dockerfile | Java 17 Dockerfile 与 smoke test 已实现，JDK/Maven/Guava 和基础镜像输入固定 | 后续按项目需求增加 JDK 8/11/21、Gradle 组合 | 其他 JDK 版本尚未实施 |
| Java Toolchain Active | `java17-maven` 当前仍为 Candidate，没有业务项目引用 | 代表项目双跑、审批后晋级 | 不能把 Candidate 说成生产 Active |
| Harbor 企业服务 | 当前仓库未配置企业 Harbor endpoint/Robot | 企业部署时接入 | Harbor HA、Robot、API 扫描适配器尚未接入 |
| Harbor Scan Gate | 本方案定义适配器契约；当前实际跑通的是 GHCR + Trivy 工作流门禁 | 按所选 Harbor 版本开发和测试 | 不能声称 Harbor 异步扫描适配器已运行 |
| Jenkins Shared Library | 文档定义目标接口；当前未配置企业 Jenkins Controller / Shared Library | 受保护 Shared Library 解析并消费 Active digest | 真实 Jenkins 接入尚未完成 |
| Java 代表项目 | 当前完成的是独立 smoke Maven 项目，不是企业业务项目 | 至少一个真实项目兼容性双跑 | 真实业务项目兼容性验收未完成 |
| DR | 文档定义恢复范围和演练要求 | 实施备份、灾备和恢复演练 | 不能声称 RTO/RPO 已达成 |

**上线状态只有在运行证据存在时才能更新。** 文档合并、JSON 校验或 GitHub PR 通过，只能证明文档和仓库变更进入版本控制，不能证明 Harbor、Jenkins、Registry 或生产项目已完成部署。

---

## 35. 最终架构决策与禁止事项

最终方案应坚持以下决策：

1. **一个受治理的工具链组合对应一个不可变镜像身份。** JDK Vendor、补丁、基础 OS、Maven/Gradle 和架构都属于工具链规格。
2. **Jenkins Controller/Agent Runtime 与项目 Build JDK 分离。** 老项目 JDK 不影响 Jenkins 平台运行时。
3. **中央 Registry 是唯一批准来源。** 项目只声明 Toolchain ID，中央平台解析精确 digest。
4. **镜像生产与项目构建分离。** 项目构建不负责安装或发布标准工具链。
5. **Candidate 与 Active 分离。** 镜像 Push 成功不是晋级成功，只有所有证据通过并审批后才能 Active。
6. **Harbor/GHCR 只保存镜像和扫描数据，不负责业务项目选择工具链。**
7. **Harbor 扫描、SBOM、签名和 Provenance 是不同门禁。** 任何一项都不能代替其他项。
8. **缓存不是制品库，Artifact 才是交付身份。** 构建一次，后续环境晋级使用同一个制品 digest。
9. **不可信构建不接触发布、签名和生产凭据。** Docker Socket 不是安全沙箱。
10. **升级是新建 Candidate，回滚是恢复既有 digest。** 不覆盖 tag，不重新构建历史镜像冒充原件。
11. **Harbor 灾备要同时保护镜像数据、元数据、配置、权限和证据。** 同名 tag 不代表同一个镜像。
12. **所有未完成能力必须显式标注。** 没有真实配置、运行记录和验收报告，就不能写成生产已实现。

以下行为一律禁止：

- 在 Jenkins Agent 宿主机上为每个项目不断安装 JDK 并通过 PATH 临时切换；
- 在业务 Jenkinsfile 中填写任意容器镜像地址或 latest；
- 项目自行绕过中央 Registry 选择非 Active 工具链；
- 把 Jenkins Controller 直接作为普通项目构建节点；
- 把 Harbor 扫描任务未返回结果解释成“没有漏洞”；
- 扫描器或漏洞库异常时自动放行 Candidate；
- 将 Harbor Robot Push、Artifact 发布、Signing 凭据同时注入普通 Maven Build；
- 让不可信 PR 写入可信共享缓存；
- 把密码、私钥、许可证或业务源码打进工具链镜像；
- 只备份 Harbor 数据库而不备份 Registry Storage；
- 通过覆盖标签、重建旧版本或改写 digest 来实现回滚；
- 以文档、静态检查或 PR 合并代替真实环境验收。

生产完成的定义不是“文档写完”，而是：**同一个 Toolchain ID 能从中央 Registry 解析到 Active digest；该 digest 可验签、可扫描、可从灾备恢复；项目可在隔离 Agent 上消费；最终业务制品有完整来源和摘要；版本可升级、可退役、可回滚；每个环节都有可审计证据。**


## 25. Jenkins Shared Library 的实际实现状态

仓库内的 Shared Library 入口现位于：

- vars/javaToolchainBuild.groovy：受信分支构建入口；
- src/com/iwacollection/cicd/JavaToolchainSpec.groovy：项目声明、相对路径、Active digest 验证；
- examples/java17-maven-service/Jenkinsfile：示例消费者；
- jenkins-shared-library-tests：Groovy 源码编译和 Mock Pipeline 测试。

执行逻辑固定为：

1. 检查 CHANGE_ID、分支、source commit 和 source repository；
2. 读取项目 ci/toolchain.json，只获得 Toolchain ID 和构建命令；
3. 检出精确 platformRef 的 CICD 仓库并核对 checkout SHA；
4. 调用 scripts/ci/resolve_toolchain.py，从中央 Registry 解析 image@sha256，要求 status=active；
5. 使用 Cosign 验签、docker pull 和 RepoDigests 精确比对；
6. 通过 run_build.py 在容器内运行 build/test/verify；
7. 采集实际 JDK/Maven 元数据，生成并校验 Artifact Contract v2；
8. 发布 JUnit 测试报告和归档制品；
9. 清理 Maven settings 临时文件并退出 Registry 登录。

项目只声明 Toolchain ID，不允许把镜像地址或 digest 作为业务仓库覆盖项。Maven 私服凭据通过 Jenkins Secret File 临时注入，不能写入项目仓库。

### 25.1 当前尚未完成的企业验收

当前 Java 17 仍是 Candidate，因此正式 Shared Library 会拒绝消费。这是正确的安全门禁。当前没有已连接的企业 Jenkins Controller、Agent、全局 Shared Library 配置或企业 Credentials，也没有发现可接入的真实 Java 业务仓库。

本仓库的 Jenkins Shared Library 测试使用 Mock Pipeline DSL 验证调用顺序和失败关闭行为；GitHub Actions 会在 Java 17 容器中编译并执行测试。Mock 通过不代表真实 Jenkins 集成通过。

详见：[Jenkins Java Toolchain Shared Library 实施与接入](jenkins-shared-library.md)。
