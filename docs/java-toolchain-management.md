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
ENTRYPOINT ["/bin/sh"]
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

以下展示 Declarative Pipeline 调用结构。生产接入时，resolveToolchain、verifyImageSignature 应由受保护的 Jenkins Shared Library 或平台 CLI 实现，并固定到经过评审的版本；不能由业务仓库自行实现一个永远返回通过的函数。中央注册表解析应读取固定 CICD commit 的 ci/toolchains.json，而不是从可变 main 分支临时下载。

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
        HARBOR_CREDENTIALS = 'harbor-ci-pull'
        MAVEN_SETTINGS_ID = 'maven-settings-readonly'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
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

                    env.TOOLCHAIN_ID = tc.id
                    env.TOOLCHAIN_IMAGE = tc.image + '@' + tc.digest
                    env.EXPECTED_JDK = tc.jdkVersion
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
                            'https://harbor.example.com',
                            env.HARBOR_CREDENTIALS
                        ) {
                            docker.image(env.TOOLCHAIN_IMAGE).inside {
                                sh '''
                                    set -eu
                                    java -version
                                    javac -version
                                    mvn -version
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
                              'jdk=' + env.EXPECTED_JDK + '\n' +
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

状态：Planned 仅登记计划、不允许消费；Candidate 允许平台构建/验证、不允许普通项目消费；Active 正式批准且必须有完整 digest；Retired 停止新项目引用但历史构建仍可追溯；Blocked 用于紧急安全风险，拒绝新构建消费，已有运行制品由应用风险流程处置。

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
