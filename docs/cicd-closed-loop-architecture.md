# CI/CD 完整闭环架构：制品治理、Argo CD GitOps、Argo Rollouts 与 Prometheus 自动决策

> 定位：平台级架构与实施规范。本文将代码提交、可信构建、制品治理、环境晋级、GitOps 部署、渐进式发布、指标分析、人工审批、自动停止、回滚和审计串成一个可追溯闭环。
>
> 状态声明：本文描述目标闭环及其与本仓库现有能力的衔接。仓库已有 Artifact Contract v2、供应链验证、归档、Promotion、deployment pointer、release strategy 和路由期望状态渲染，不等于真实 Kubernetes 集群已经接入。Argo CD、Rollouts、Prometheus 的真实联通、权限和生产验收必须由环境接入证据证明。

---

## 1. 建设目标：从流水线自动化到发布闭环

传统 CI/CD 经常存在以下断点：

1. CI 构建成功，但无法证明部署镜像就是本次构建产物。
2. 制品上传成功，但部署使用可变 tag，无法确认实际运行内容。
3. Git 配置合并成功，但 Argo CD 可能没有同步，或同步失败。
4. Argo CD 显示 Synced，但应用可能没有 Ready，更不代表用户请求正常。
5. Rollout 显示完成，但 Analysis 可能没有样本、Prometheus 查询失败或观察窗口不足。
6. 流量已经回切，但 GitOps 仍保存失败版本；Argo CD 后续同步可能再次部署失败版本。
7. 发布失败后无法还原制品、集群、流量权重、指标窗口、审批人和回滚结果。

本架构目标：

- **Build once**：构建一次，测试、晋级、部署和回滚复用同一份不可变制品。
- **Artifact identity**：source commit、bundle digest、image digest、SBOM、签名和 provenance 可关联。
- **GitOps desired state**：部署意图以 Git commit 表达，Argo CD 同步授权目录。
- **Progressive delivery**：Argo Rollouts 负责单集群发布步骤、暂停、分析门禁和运行时流量切换。
- **Evidence-based decision**：Prometheus Analysis 必须有有效样本、正确窗口、健康数据源和明确阈值。
- **One writer per field**：同一应用、集群、发布期间，生产流量权重只有一个写入者。
- **Rollback consistency**：运行时回切与 GitOps 期望状态回退分别执行、分别验证。
- **End-to-end traceability**：每个阶段记录输入、输出、操作者、时间、证据和结果。

## 2. 总体架构与职责边界

### 2.1 端到端逻辑图

~~~text
源码与构建层
业务 Git Repo
  └─ PR / Review / Ruleset / Merge
       ↓
CI Orchestrator（GitHub Actions / Jenkins）
  ├─ 固定 Source SHA
  ├─ Dependency DAG / Toolchain / Runner
  ├─ Build / Unit Test / Integration Test
  ├─ SAST / Dependency / Secret / License / Container Scan
  └─ SBOM + Provenance + Attestation + Cosign
       ↓
制品治理层
Artifact Contract v2
  ├─ bundle SHA256 / member SHA256
  ├─ image digest
  ├─ source / run / toolchain / dependency identity
  └─ SBOM / signature / provenance
       ↓
Artifact Archive → Verification → Promotion Policy
       ↓
Environment Deployment Pointer（dev / staging / production）
       ↓
发布控制层
Release Controller / release_strategy.py
  ├─ 校验候选制品与环境指针
  ├─ 固化 release ID / cluster pin / region wave
  ├─ 生成部署意图
  ├─ 等待 Argo CD / Rollouts / Analysis 真实状态
  └─ 聚合证据、推进或停止
       ↓
GitOps Repository
  ├─ 环境配置 / Application / ApplicationSet
  ├─ Rollout / Service / AnalysisTemplate
  ├─ 固定镜像 digest
  └─ 每次部署意图对应 Git commit
       ↓
Argo CD Application / ApplicationSet
  ├─ Repo / revision / path / destination
  ├─ Sync / Health / Drift
  └─ Project RBAC / Sync Policy
       ↓
Kubernetes Runtime
Argo Rollouts Controller
  ├─ ReplicaSet / Pod / pause / promote / abort
  ├─ Canary steps / BlueGreen preview & active
  └─ 唯一选定的 traffic integration
       ↓
AnalysisRun → AnalysisTemplate → Prometheus / Metrics / Web Probe
       ↓
Successful / Failed / Error / Inconclusive
       ↓
闭环与审计层
只读 State Adapter
  ├─ Argo CD Application status
  ├─ Rollout / AnalysisRun / ReplicaSet / Pod / Route
  ├─ Prometheus query result + time window + sample count
  └─ GitOps commit / artifact / approval / event correlation
       ↓
Release Decision
  ├─ Continue / Pause / Require Approval
  ├─ Abort / Runtime Rollback
  └─ GitOps Revert / Restore previous digest
~~~

### 2.2 组件职责表

| 组件 | 负责什么 | 不负责什么 |
|---|---|---|
| GitHub / 业务 Git | 源码、PR、Review、分支保护 | 不证明运行时成功 |
| CI（Actions / Jenkins） | 构建、测试、扫描、签名、产出制品 | 不持有生产集群管理员凭据 |
| Artifact Contract v2 | 统一制品身份和构建元数据 | 不管理 Kubernetes 流量 |
| Archive | 长期保存制品及证据 | 不等于环境已部署 |
| Promotion Policy | 判断制品是否允许进入环境 | 不负责 Rollout 内部流量步骤 |
| Release Controller | 编排发布意图、集群波次、证据和决策 | 不应再造一套与 Rollouts 竞争的单集群状态机 |
| GitOps Repository | 保存声明式部署期望状态 | 不保存明文 kubeconfig / Token |
| Argo CD | 同步 Git 期望状态并报告 Sync / Health | 不判断业务指标 |
| Argo Rollouts | 单集群发布步骤、暂停、推进、中止和流量集成 | 不决定制品晋级、跨区域顺序 |
| Prometheus | 提供时间序列数据 | 不独立批准生产发布 |
| AnalysisRun | 按模板执行指标判定 | 不替代发布身份和审批审计 |
| State Adapter | 只读读取控制器状态并归一化证据 | 不绕过审批修改集群 |

### 2.3 六种状态不可混为一谈

~~~text
Artifact Promoted
  ≠ GitOps Commit Merged
  ≠ Argo CD Synced
  ≠ Rollout Healthy
  ≠ Analysis Successful
  ≠ Business Release Accepted
~~~

- Artifact Promoted：制品是否被允许进入目标环境。
- GitOps Commit Merged：部署期望状态是否写入 Git。
- Argo CD Synced：集群资源是否与目标 Git revision 同步。
- Rollout Healthy：工作负载是否达到控制器定义的健康条件。
- Analysis Successful：观测窗口内指标和探测是否满足门槛。
- Business Release Accepted：是否满足最终验收和审批要求。

不能把某一个状态冒充成整个发布成功。

## 3. 统一发布身份：所有证据必须串成一条链

### 3.1 Release Identity

一次发布必须生成唯一 release_id。建议至少包含：

| 字段 | 约束 / 示例 | 用途 |
|---|---|---|
| release_id | UUID 或全局唯一 ID | 一次发布的主关联键 |
| application | checkout | 业务应用 |
| environment | dev / staging / production | 目标环境 |
| source_repo | owner/repo | 源码仓库 |
| source_sha | 40 位 commit SHA | 精确源码版本 |
| source_run_id | CI run ID | 构建来源 |
| artifact_name | Artifact Contract v2 名称 | 制品逻辑名称 |
| bundle_sha256 | 64 位 SHA256 | 制品包身份 |
| image_digest | sha256:... | 容器运行时身份 |
| sbom_digest | 64 位 SHA256 | SBOM 身份 |
| provenance_ref | attestation 标识 | 构建来源证明 |
| signature_identity | issuer / signer | 签名主体 |
| promotion_id | 晋级记录 ID | 制品晋级链 |
| gitops_repo | owner/repo | 部署配置仓库 |
| gitops_commit | 40 位 commit SHA | 本次期望状态版本 |
| argo_application | Application 名称 | Argo CD 对象 |
| rollout_name | Rollout 名称 | 运行时发布对象 |
| cluster_id | 精确集群 ID | 发布目标 |
| analysis_run_ids | AnalysisRun 列表 | 分析证据 |
| approval_id | 审批记录 ID | 人工授权 |
| rollback_of | 原 release_id，可空 | 回滚关联 |

release_id 不替代 source SHA、bundle digest 或 image digest。它只是把这些身份关联起来的主键。

### 3.2 身份关联规则

1. 一个 release_id 只能绑定确定的 source_sha、bundle_sha256 和 image_digest 集合。
2. GitOps commit 必须引用 image digest，不得只写可变 tag。
3. Rollout Pod Template 中的 image digest 必须与已验证制品清单一致。
4. Argo CD Application 的 repo、revision、path、destination 必须属于本次发布授权范围。
5. Rollout、AnalysisRun、ReplicaSet、Pod 和流量资源必须关联应用、环境、集群及 release_id。
6. 回滚创建新的 rollback release，引用原失败 release_id 和恢复目标 digest，不能覆盖原发布证据。

建议统一 Kubernetes 标签和注解：

~~~yaml
metadata:
  labels:
    cicd.platform/application: checkout
    cicd.platform/environment: production
    cicd.platform/cluster-id: prod-cn-east-a
  annotations:
    cicd.platform/release-id: "<release-id>"
    cicd.platform/source-sha: "<40-char-sha>"
    cicd.platform/bundle-sha256: "<64-char-sha256>"
    cicd.platform/image-digest: "sha256:<64-hex>"
    cicd.platform/gitops-commit: "<40-char-sha>"
    cicd.platform/promotion-id: "<promotion-id>"
~~~

不要把 release_id、commit SHA、Pod UID 作为常规 Prometheus metric label。高基数身份应放在 annotation、日志、事件和审计记录中。

## 4. CI 构建与制品治理

### 4.1 标准构建链路

~~~text
PR
 ↓
代码评审 / Ruleset / Required Checks
 ↓
固定 source SHA
 ↓
影响分析 / 依赖 DAG / 工具链解析
 ↓
隔离 Runner 构建
 ↓
单元测试 / 集成测试 / 兼容性测试
 ↓
SAST / SCA / Secret / License / Container Scan
 ↓
生成 SBOM
 ↓
打包 Artifact Contract v2
 ↓
计算 bundle 与 member SHA256
 ↓
生成 provenance / attestation
 ↓
签名并验证签名
 ↓
长期归档
 ↓
生成可晋级制品记录
~~~

### 4.2 Build once

同一份制品在 dev、staging、production 间晋级时，不能重新编译或重新打包成新产物。否则即使 source SHA 相同，也无法证明部署的是测试过的 bytes。

~~~text
Build(source SHA X)
   ↓
Artifact bundle digest A
   ↓
Image digest I
   ↓
dev deploys I
   ↓
staging deploys I
   ↓
production deploys I
~~~

允许环境差异存在于外部配置、Secret 引用、资源规格和路由策略中；不允许通过重新构建改变应用制品本身。

### 4.3 制品准入门禁

进入 Promotion 前，至少检查：

- Artifact Contract v2 schema 正确；
- bundle SHA256 与实际文件一致；
- 每个 member digest 与清单一致；
- source SHA、CI run、toolchain、依赖锁和构建身份完整；
- SBOM 存在且与制品对应；
- provenance / attestation 的主体、issuer、source repo、source revision 和 workflow 符合信任策略；
- 签名验证成功；
- 漏洞、许可证、密钥泄漏和策略检查满足目标环境门槛；
- 目标环境允许该制品和来源进入；
- 容器场景中 image digest 与制品清单中的 digest 对应。

不能仅因镜像存在于 Registry、tag 正确或 CI job 绿色就允许生产部署。

### 4.4 Promotion 与 Deployment Pointer

环境指针表达“当前被认可的制品身份”，不等于流量权重。当前仓库的 Promotion / deployment pointer 机制应继续作为制品晋级边界。

必须区分：

- promoted：策略允许候选进入环境；
- deployment requested：已创建 GitOps 部署意图；
- deployed：Argo CD 已同步且工作负载达到最低部署条件；
- release accepted：Rollout 和 Analysis 达标，并完成规定审批。

生产指针不能在只生成渲染文件时就被写成生产部署成功。字段语义必须明确是 promotion pointer 还是 runtime deployment pointer，不能混用。

## 5. GitOps：Argo CD 如何接收发布意图

### 5.1 仓库职责划分

~~~text
业务源码仓库
  └─ 源码、测试、业务代码

CICD 平台仓库（iwacollection/CICD）
  └─ 构建策略、制品契约、供应链、Promotion、Release Orchestration

GitOps 配置仓库（以真实 Argo CD Application 为准）
  └─ 环境配置、Application/ApplicationSet、Rollout、Service、
     AnalysisTemplate、路由声明和版本化部署意图
~~~

这是职责划分，不强制三个仓库必须分开。若当前 Argo CD 已直接监听业务仓库或 CICD 仓库，必须以真实 Application 的 repoURL、targetRevision、path、destination 和 project 为准，不能假设另有一个 GitOps 仓库已经存在。

CICD 平台仓库不应因“接入 Argo CD”自动获得集群写权限。通常 CI 只需要对授权 GitOps 路径提交变更；Argo CD Controller 在集群侧完成同步。

### 5.2 Application 关键字段

以下是结构示例，仓库地址、目录和集群地址都是占位值：

~~~yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: checkout-production
  namespace: argocd
spec:
  project: production
  source:
    repoURL: https://github.com/ORG/GITOPS-REPO.git
    targetRevision: main
    path: apps/checkout/overlays/production
  destination:
    server: https://KUBERNETES-API
    namespace: checkout
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
~~~

必须明确五个事实：

1. repoURL：Argo CD 从哪个 Git 仓库读取。
2. targetRevision：跟踪哪个 branch、tag 或 commit。
3. path：仓库中哪个目录是应用期望状态根目录。
4. destination：同步到哪个集群和 namespace。
5. project：该 Application 受哪套 Argo CD Project 权限约束。

生产环境对高风险发布应采用受控 revision 和明确审批策略。automated sync、prune、selfHeal 必须按资源类型和 Rollouts 字段所有权设计。

### 5.3 ApplicationSet 与多集群

ApplicationSet 负责从批准的集群集合生成 Application，不等于发布策略本身。跨区域顺序、固定名单和波次门禁仍由发布控制面管理。

- 先计算并冻结本次发布 ClusterPin；
- ApplicationSet 只接收明确授权的集群集合；
- 禁止 selector 意外扩大目标集群；
- 每个 Application 携带应用、环境、release_id 和 cluster_id；
- Argo CD Project 限制 repo、destination namespace、cluster 和资源类型；
- 集群不在 pin 中时，不允许因为标签变化自动加入本次发布；
- 目标集群消失、身份变化或 ApplicationSet 生成数量不符时，停止发布并标记状态未知。

OCM Placement / PlacementDecision 与 Argo CD ApplicationSet 可以组成跨集群分发方案，但 PlacementDecision 结果必须与发布时冻结的 ClusterPin 做交集校验。动态匹配结果不能直接当成授权结果。

### 5.4 GitOps 更新流程

~~~text
Release Controller 校验制品
  ↓
生成部署变更（image digest + release metadata）
  ↓
GitOps Writer 使用短期身份提交到指定目录
  ↓
PR / Policy / Review（生产按策略要求）
  ↓
GitOps commit 合并
  ↓
Argo CD 发现目标 revision
  ↓
Render / Diff / Sync
  ↓
Application Synced + Health
  ↓
State Adapter 校验 revision 与目标 commit 完全一致
  ↓
进入 Rollout 运行时验证
~~~

必须记录 GitOps commit SHA。PR URL 和 branch 名都不能代替 commit SHA。

### 5.5 Argo CD 状态处理

| Argo CD 状态 | 发布控制面处理 |
|---|---|
| OutOfSync | 等待同步或受控 Sync，不能判定部署完成 |
| Synced + Progressing | 等待健康状态和 Rollout |
| Synced + Healthy | 仅说明 Argo CD 资源健康条件满足，仍需 Rollout 与 Analysis |
| SyncFailed | 停止后续步骤，记录错误和 GitOps commit |
| Degraded | 停止推进，触发调查或回滚 |
| Unknown / API unavailable | 状态未知，失败关闭 |
| Synced 但 revision 不等于本次 commit | 版本不匹配，阻断发布 |

Argo CD Healthy 不能代替业务指标分析。Deployment Ready 也不能证明请求延迟、错误率和交易成功率。

## 6. Argo Rollouts：单集群内渐进式发布

### 6.1 Rollouts 在链路中的位置

Rollouts 接收 GitOps 中声明的 Rollout 对象，负责单集群内新旧 ReplicaSet 的生命周期和渐进发布步骤。它不应重新实现 Artifact Contract、Promotion 或跨区域策略。

~~~text
GitOps commit
  ↓
Argo CD sync Rollout spec
  ↓
Argo Rollouts Controller
  ↓
创建候选 ReplicaSet / Pod
  ↓
等待 readiness / minReadySeconds
  ↓
执行 canary step 或 blue-green preview
  ↓
创建 AnalysisRun
  ↓
等待 Analysis 结果
  ↓
Successful → 下一步 / Pause / Promote
Failed → 停止推进 / abort policy
Error / Inconclusive → 暂停并等待恢复或人工决策
~~~

### 6.2 Canary 与 Blue-Green

| 维度 | Canary | Blue-Green |
|---|---|---|
| 核心机制 | 按步骤逐渐增加候选流量 | 部署 inactive，再切换 active |
| 流量变化 | 1%、5%、25%、50%、100% 等 | 通常一次切换 |
| 观察重点 | 每个权重阶段的错误率、延迟、请求量 | Preview 验证、切换前后健康 |
| 资源成本 | 新旧版本并存，按策略扩容 | 通常保留两套槽位 |
| 回退 | 流量移回 stable，停止候选 | active service / route 指回旧槽 |

### 6.3 Canary Rollout 结构示例

以下仅展示步骤组织。traffic router、Service、指标模板和 Rollouts 版本必须按实际集群适配：

~~~yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: checkout
  namespace: checkout
spec:
  replicas: 6
  selector:
    matchLabels:
      app: checkout
  template:
    metadata:
      labels:
        app: checkout
    spec:
      containers:
        - name: checkout
          image: registry.example.invalid/checkout@sha256:<64-hex-digest>
          ports:
            - name: http
              containerPort: 8080
  strategy:
    canary:
      stableService: checkout-stable
      canaryService: checkout-canary
      trafficRouting:
        # 必须配置一种真实支持的 traffic router，不能保留占位配置部署。
      steps:
        - setWeight: 1
        - pause:
            duration: 5m
        - analysis:
            templates:
              - templateName: checkout-success-rate
        - setWeight: 5
        - pause:
            duration: 10m
        - analysis:
            templates:
              - templateName: checkout-success-rate
        - setWeight: 25
        - pause:
            duration: 10m
        - analysis:
            templates:
              - templateName: checkout-success-rate
        - setWeight: 50
        - pause:
            duration: 15m
        - analysis:
            templates:
              - templateName: checkout-success-rate
        - setWeight: 100
        - pause: {}
~~~

这不是可直接 apply 的完整清单。Rollout API 字段必须按安装的 CRD 版本校验；trafficRouting 必须补齐真实 router 配置。空 trafficRouting 不代表已接入流量管理。

### 6.4 Blue-Green 的关键对象

- previewService：指向候选版本，用于预览验证；
- activeService：生产用户实际访问的服务；
- 候选 Ready 后才进入 preview；
- 预览探测通过后才允许切换 active；
- 切换后继续观察真实请求和业务指标；
- abort 时将 active 流量恢复到稳定版本；
- 旧 ReplicaSet 保留时间必须覆盖观察和回滚窗口。

Preview 验证不等于生产流量验证。只有 active 流量切换后，才能验证真实生产流量表现。

### 6.5 暂停、推进和中止

- 自动步骤：Rollouts 按声明 steps 执行。
- 自动分析：AnalysisRun 执行并产生 Successful / Failed / Error。
- 自动暂停：关键步骤等待人工批准或外部状态。
- 人工 Promote：授权人员明确批准继续。
- Abort：终止候选发布并按 traffic router 行为回到稳定版本。
- Rollback：发布完成后，按独立回滚流程恢复历史版本。

暂停不是失败；Analysis Error 也不等于业务指标失败。后者意味着证据无法可靠得出结论。

## 7. Prometheus Analysis：发布决策必须由证据驱动

### 7.1 完整指标链路

~~~text
用户请求
  ↓
Ingress / Gateway / Service Mesh
  ↓
应用实例
  ├─ 请求总量
  ├─ 成功请求量
  ├─ 5xx / 业务失败量
  ├─ 请求耗时 Histogram
  └─ 业务交易结果
  ↓
Prometheus scrape / remote write
  ↓
PromQL 查询
  ↓
AnalysisTemplate
  ↓
AnalysisRun
  ↓
Successful / Failed / Error / Inconclusive
  ↓
Rollouts step 决策
~~~

可能导致错误结论的原因：

- 应用没有埋点，查询为空；
- Prometheus scrape 失败，数据不完整；
- 查询范围选错，混入其他版本或集群流量；
- 请求量太少，百分比剧烈波动；
- 指标延迟上报，观察窗口不完整；
- 新版本标签缺失，查询拿到全量服务指标；
- 只看平均延迟，尾延迟恶化被掩盖。

### 7.2 应用埋点指标

建议统一 RED（Rate、Errors、Duration）：

| 指标 | 类型 | 用途 |
|---|---|---|
| http_requests_total | Counter | 请求总量 |
| http_request_duration_seconds_bucket | Histogram | 延迟分布，计算 P95/P99 |
| 5xx 请求计数 | Counter | 服务端错误 |
| business_transactions_total{result="success"} | Counter | 业务成功交易 |
| business_transactions_total{result="failure"} | Counter | 业务失败交易 |
| runtime metrics | Gauge / Counter | CPU、内存、GC、线程池等辅助证据 |

标签建议：service、namespace、environment、cluster、version 或 release channel。避免 user_id、order_id、request_id、trace_id、Pod UID、release_id 等高基数标签。

### 7.3 PromQL 示例

错误率：

~~~promql
sum(rate(http_requests_total{service="checkout",status=~"5.."}[5m]))
/
sum(rate(http_requests_total{service="checkout"}[5m]))
~~~

P95 延迟：

~~~promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(http_request_duration_seconds_bucket{service="checkout"}[5m])
  )
)
~~~

候选版本请求量：

~~~promql
sum(increase(http_requests_total{
  service="checkout",
  version="candidate"
}[5m]))
~~~

注意：

1. 实际 metric 名称、标签和状态码定义必须与应用埋点一致。
2. Histogram 必须有正确 bucket 才能计算 quantile。
3. P95/P99 应按集群、服务和版本隔离，避免稳定版本流量稀释候选版本坏指标。
4. 错误率分子和分母必须使用相同时间窗口、服务范围和版本过滤。
5. 错误率必须用实际错误请求速率除以实际总请求速率，不能用固定的 1 次/秒替换真实分母；零请求或无样本必须单独判为证据不足，不能算成 0% 错误率。
6. 低流量下 P95 是不稳定估计，必须有样本门槛。

### 7.4 AnalysisTemplate 结构示例

以下示例展示查询与门槛的组织方式，需按实际 Prometheus、指标标签、Rollouts 版本和阈值调整：

~~~yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: checkout-success-rate
  namespace: checkout
spec:
  args:
    - name: service
      value: checkout
    - name: candidate-version
      value: candidate
  metrics:
    - name: candidate-request-volume
      interval: 1m
      count: 5
      successCondition: result[0] >= 50
      failureLimit: 0
      provider:
        prometheus:
          address: http://prometheus-operated.monitoring.svc:9090
          query: |
            sum(increase(http_requests_total{
              service="{{args.service}}",
              version="{{args.candidate-version}}"
            }[5m]))
    - name: candidate-error-rate
      interval: 1m
      count: 5
      successCondition: result[0] <= 0.01
      failureLimit: 0
      provider:
        prometheus:
          address: http://prometheus-operated.monitoring.svc:9090
          query: |
            sum(rate(http_requests_total{
              service="{{args.service}}",
              version="{{args.candidate-version}}",
              status=~"5.."
            }[5m]))
            /
            sum(rate(http_requests_total{
              service="{{args.service}}",
              version="{{args.candidate-version}}"
            }[5m]))
    - name: candidate-p95
      interval: 1m
      count: 5
      successCondition: result[0] <= 0.3
      failureLimit: 0
      provider:
        prometheus:
          address: http://prometheus-operated.monitoring.svc:9090
          query: |
            histogram_quantile(
              0.95,
              sum by (le) (
                rate(http_request_duration_seconds_bucket{
                  service="{{args.service}}",
                  version="{{args.candidate-version}}"
                }[5m])
              )
            )
~~~

错误率查询不能用 `clamp_min(总请求速率, 1)` 作为分母。低流量时总请求速率可能小于每秒 1 次，这种写法会把真实错误率压低。例如 5 分钟内 50 个请求、3 个错误，真实错误率是 6%；若把约 0.17 次/秒的分母强行替换为 1，结果会错误地接近 1%。

因此必须把“请求量是否足够”作为独立门槛：先确认候选版本在观察窗口内有足够请求，再判断错误率。若分母为零、查询无结果或返回 NaN，Analysis 必须按证据不足/执行异常处理，不得当作成功。

生产前必须验证：

- Prometheus Provider 网络、认证和 TLS；
- 查询结果类型、空结果和 NaN 的处理；
- 候选版本标签确实由工作负载埋点产生；
- 请求量门槛与统计窗口匹配；
- Analysis 总执行时间、失败次数和超时策略；
- Rollouts 对 Error / Inconclusive 的行为符合失败关闭规则。

示例中各 metric 的独立评估和样本门槛还需按实际 Rollouts 版本验证；不能把三个查询分别成功误认为它们一定来自同一个原子时间窗口。

### 7.5 四种分析结果

| 结果 | 含义 | 发布决策 |
|---|---|---|
| Successful | 查询成功、样本充分、指标满足阈值 | 可进入下一步，但仍受审批策略约束 |
| Failed | 有有效证据证明指标越界或探测失败 | 停止扩大流量，按策略 abort / 回切 |
| Error | 查询、权限、网络、数据源或执行出错 | 不得 Promote；暂停并修复观测链路 |
| Inconclusive | 样本不足、数据不足或无法得出可信结论 | 不得 Promote；等待样本或人工处理 |

**没有数据不是零错误；Prometheus 不可用不是成功；样本不足不是健康；Analysis 超时不能自动通过。**

若 Provider 无法可靠区分空值、NaN、查询失败和超时，必须在适配层显式校验，不能只依赖一个 successCondition。

### 7.6 不只看技术指标

生产发布至少组合：

- 可用性：5xx / 请求失败率；
- 延迟：P95、P99；
- 流量：候选版本请求量；
- 资源：CPU、内存、重启、OOM、线程池、连接池；
- 业务：支付成功率、订单创建成功率、关键交易完成率；
- 探测：真实业务路径合成探测；
- 数据源健康：Prometheus scrape health、查询错误、采样延迟。

接口返回 200 但订单金额错误，仍然是发布失败。

## 8. 自动决策：从指标结果到发布动作

### 8.1 决策矩阵

| Argo CD | Rollout | Analysis | 决策 |
|---|---|---|---|
| SyncFailed | 未正常创建 | 无 | 停止发布，修复 GitOps |
| OutOfSync | 未确认 | 无 | 等待或受控同步 |
| Synced | Progressing | Running | 等待 |
| Synced | Paused | Successful | 满足本步骤证据后，按审批策略推进 |
| Synced | Paused | Failed | 停止扩大流量，触发 abort / 回切 |
| Synced | Paused | Error | 暂停，修复观测链路，不得 Promote |
| Synced | Paused | Inconclusive | 等待样本或人工处理，不得 Promote |
| Synced | Degraded | 任意 | 阻断推进，调查工作负载健康 |
| Unknown | Unknown | Unknown | 状态未知，失败关闭 |
| Synced | Completed | Successful | 技术验收完成；再检查业务验收和审批 |

### 8.2 自动推进与人工审批

推荐分环境：

- dev：可自动部署、分析、推进；保留审计。
- staging：自动部署和分析；关键步骤可要求测试负责人批准。
- production：先采用自动分析 + 人工 Promote，演练充分后再评估限定范围自动推进。
- 高风险服务：生产始终保留人工审批或双人审批。

审批对象必须绑定 release_id、application、environment、candidate image digest、GitOps commit、当前 Rollout step、AnalysisRun 结果及时间窗口、允许动作、审批人和有效期。

不能让一个脱离 digest 和发布步骤的“批准生产发布”按钮执行任意动作。

### 8.3 幂等和竞态控制

控制面必须具备：

- 幂等请求 ID；
- 每个 application + environment 的发布互斥锁；
- 同一时间只允许一个活动 release；
- 每次动作带期望状态版本 / resourceVersion；
- 先读取状态，再提交动作；
- 动作完成后重新读取状态验证；
- 重试不能重复创建 Promotion 或覆盖新状态；
- 锁超时后先核对真实 Rollout 和 GitOps 状态，不可假设上次动作失败。

不能只依赖 GitHub Actions concurrency。Jenkins、人工 kubectl、Argo CD 和其他自动化系统都可能并发改变资源。

## 9. 流量控制权：禁止双写

本仓库现有 release strategy 可渲染 Gateway API HTTPRoute 和 Istio VirtualService；Argo Rollouts 也能通过 traffic router 管理权重。这是集成时最重要的冲突点。

每个 application + environment + cluster 必须明确一个 traffic_owner：

| traffic_owner | 流量权重写入者 | 适用模式 |
|---|---|---|
| cicd-renderer | 现有 release strategy 渲染器 | 保留当前 HTTPRoute / VirtualService 状态机；Rollouts 不得写同一动态权重 |
| argo-rollouts | Argo Rollouts Controller | Rollouts 控制单集群 canary / blue-green；release controller 只编排制品、集群波次和证据 |
| none | 无 | 仅部署和健康检查，不执行流量切换 |

强制规则：

1. traffic_owner 缺失必须校验失败。
2. cicd-renderer 与 argo-rollouts 同时拥有同一路由写权限必须校验失败。
3. none 不得生成可承接生产流量的权重配置。
4. traffic owner 变更必须作为独立迁移，不能在一次发布中途切换。
5. Istio VirtualService 若只是同一状态生成的适配对象，不能被另一个控制器同时写入。
6. Argo CD selfHeal / automated sync 必须结合 Rollouts 字段管理机制验证；不能简单关闭 selfHeal 就认为冲突已解决。
7. 每个字段都要记录 Git、Argo CD、Rollouts、其他 Operator 中的唯一 writer。

现有 release_strategy.py 不能和 Rollouts 各自推进一遍 1/5/25/50/100。正确做法是保留一个跨环境 / 跨集群发布控制面，单集群流量步骤交给唯一选定的控制器，通过阶段事件和证据交接，而不是同时修改权重。

## 10. 多集群与多区域发布

### 10.1 两级状态机

~~~text
Global Release Controller
  ├─ 固定 cluster pin
  ├─ 选择区域 wave
  ├─ 判断当前区域必需集群是否成功
  ├─ 控制下一波是否开放
  └─ 聚合全局发布结果

Per-cluster Argo Rollouts
  ├─ 部署本集群候选版本
  ├─ 执行 canary / blue-green
  ├─ 执行本集群 AnalysisRun
  ├─ 暂停 / Promote / Abort
  └─ 回报真实运行状态
~~~

全局控制面不能把 ApplicationSet 已生成当成集群成功，也不能把单集群 Rollout 成功当成整个区域成功。

### 10.2 波次推进条件

一个区域只有满足以下条件才可确认：

- ClusterPin 中所有必需集群都已确认；
- 每个集群 Application 指向本次期望 revision；
- 每个集群 Rollout 对应本次 release_id 和 image digest；
- 每个集群必要 AnalysisRun 都 Successful；
- 没有未处理 Error / Inconclusive；
- 没有未解决 Degraded / Unknown；
- 所需审批完成；
- 业务验证和观测窗口完成。

任意一个必需集群失败或状态未知，后续区域都不能自动打开。

### 10.3 部分成功处理

例如 cn-east 三个集群中两个成功，一个 Analysis Error：

- 记录两个已成功集群的 Rollout / Analysis 证据；
- 记录失败集群的错误和最后已知流量状态；
- 停止打开 cn-north；
- 按服务容灾策略决定保留已成功集群还是全区域回切；
- 不能把整个区域标记成功；
- 回切必须逐集群验证路由、稳定版本和请求恢复；
- 恢复后把 GitOps desired state 对齐到最终选择的版本。

全局 rollback policy 必须预先明确是失败集群单独回切，还是整个已开始波次统一回切。

## 11. 回滚闭环：运行时回切与 GitOps 回退

### 11.1 两条回滚路径

~~~text
失败检测
  ↓
停止后续 Promote / 下一波
  ↓
A. Runtime Rollback
   ├─ Rollouts abort / traffic router 回切
   ├─ 验证 stable backend / active service
   ├─ 验证真实流量恢复
   └─ 记录运行时回切证据

B. GitOps Desired-state Rollback
   ├─ GitOps commit 恢复到历史已批准 image digest
   ├─ PR / 受控 revert / break-glass
   ├─ Argo CD sync 到恢复 revision
   ├─ 验证 Application revision / Sync / Health
   └─ 验证 Rollout template 与运行时 image digest

两条路径都完成
  ↓
发布状态标记为 rollback_verified
~~~

### 11.2 为什么不能只做其中一个

只做 Runtime Rollback：流量可能回到旧版本，但 GitOps 仍保存失败版本；Argo CD selfHeal 或下一次 Sync 可能重新部署失败版本。

只做 GitOps Revert：Git 已恢复旧 digest，但当前流量可能仍指向候选版本；Argo CD 同步成功不代表流量已经回切。

回滚成功必须分别证明：

1. 生产流量实际指向恢复版本；
2. GitOps desired state 已恢复目标 digest；
3. Argo CD 已同步目标 revision；
4. Rollout / Pod 实际 image digest 与目标一致；
5. Prometheus 和业务探测恢复；
6. 回滚事件关联原 release_id 和恢复 release_id。

应用回滚不等于数据库回滚。数据库变更采用 Expand / Migrate / Contract；新旧应用并存时 Schema 和消息格式必须向后兼容。

## 12. 安全边界与身份设计

| 身份 | 最小权限 |
|---|---|
| CI Build | 读源码、构建、测试、扫描、生成证明；无生产集群写权限 |
| Artifact Publisher | 向指定 Registry / Archive 写入制品 |
| Promotion Actor | 更新环境晋级记录；不能直接管理 Kubernetes |
| GitOps Writer | 只写授权仓库、分支和目录 |
| Argo CD Controller | 只同步授权 Project / Namespace / Resource |
| Rollouts Controller | 只管理授权 namespace 的 Rollout 和配置的流量资源 |
| Analysis Provider | 只读查询 Prometheus |
| State Observer | 只读获取 Application、Rollout、AnalysisRun、Pod 和 Route |
| Release Approver | 对绑定 release_id 和 digest 的动作审批 |
| Break-glass Operator | 限时、审批、强审计的紧急操作 |

安全要求：

- 优先 OIDC 和短期凭据；
- 禁止明文 kubeconfig、PAT、Registry 密码进入 Git；
- CI 默认令牌最小权限；
- PR 代码不得获得生产 GitOps Writer 或 Kubernetes 权限；
- 生产写权限只存在于受控控制器或审批入口；
- Argo CD Project 限制 sourceRepos、destinations、cluster 和资源类型；
- Rollouts Controller RBAC 只授予必需对象；
- Prometheus 查询身份只读；
- 所有人工 Promote / Abort / Rollback 记录操作者、原因、审批和目标 digest；
- Break-glass 使用后复盘、轮换凭据并恢复正常权限。

## 13. 发布事件模型与审计

### 13.1 统一事件类型

建议归一化以下事件：

- BuildStarted / BuildSucceeded / BuildFailed
- ArtifactPackaged / ArtifactVerified / ArtifactArchived
- PromotionRequested / PromotionApproved / PromotionRejected
- GitOpsChangeCreated / GitOpsChangeMerged
- ArgoSyncStarted / ArgoSyncSucceeded / ArgoSyncFailed
- RolloutStarted / RolloutPaused / RolloutStepChanged
- AnalysisStarted / AnalysisSucceeded / AnalysisFailed / AnalysisError / AnalysisInconclusive
- PromoteRequested / PromoteApproved / RolloutPromoted
- AbortRequested / RolloutAborted
- RuntimeRollbackStarted / RuntimeRollbackVerified
- GitOpsRollbackStarted / GitOpsRollbackVerified
- ReleaseAccepted / ReleaseRejected

每个事件至少包含：

~~~json
{
  "event_id": "globally-unique-event-id",
  "event_type": "AnalysisSucceeded",
  "occurred_at": "2026-11-01T10:00:00Z",
  "release_id": "release-uuid",
  "application": "checkout",
  "environment": "production",
  "cluster_id": "prod-cn-east-a",
  "source_sha": "<source-sha>",
  "bundle_sha256": "<bundle-sha256>",
  "image_digest": "sha256:<image-digest>",
  "gitops_commit": "<gitops-commit>",
  "rollout": "checkout",
  "analysis_run": "checkout-analysis-123",
  "actor": "system-or-user",
  "decision": "continue",
  "evidence_ref": "immutable-evidence-reference"
}
~~~

这是事件契约示例，不代表当前仓库已部署事件总线或持久化服务。落地时需定义 Schema、版本兼容、去重、保留期和访问控制。

### 13.2 证据不能只保存在 Workflow 日志

发布结束后至少归档：

- Artifact Contract v2 manifest；
- bundle / image digest；
- SBOM、signature、provenance；
- Promotion 记录和环境 pointer；
- GitOps PR / commit；
- Argo CD Application 状态快照；
- Rollout YAML / status 快照；
- AnalysisTemplate 和 AnalysisRun 结果；
- Prometheus 查询表达式、时间范围、样本量、返回值；
- 关键事件和审批记录；
- 最终运行镜像 digest；
- 回滚前后路由与流量验证；
- 最终发布结论。

敏感信息不得进入证据包。

## 14. CI/CD 平台自身的可观测性

| 指标 | 关注点 |
|---|---|
| ci_build_success_rate | 构建成功率 |
| ci_queue_duration_seconds | Runner 排队时间 |
| ci_build_duration_seconds | 构建耗时 |
| artifact_verification_failures_total | 制品验证失败 |
| promotion_duration_seconds | 环境晋级耗时 |
| gitops_sync_duration_seconds | Argo CD 同步耗时 |
| gitops_sync_failures_total | GitOps 同步失败 |
| rollout_duration_seconds | Rollout 总耗时 |
| rollout_abort_total | 发布中止次数 |
| analysis_error_total | Analysis 执行错误 |
| analysis_inconclusive_total | Analysis 证据不足 |
| release_rollback_total | 回滚次数 |
| release_change_failure_rate | 变更失败率 |
| release_lead_time_seconds | 提交到生产验收耗时 |

release_id、commit SHA、Pod UID 等高基数字段通过日志、事件或追踪系统关联，不直接无限扩张 Prometheus time series。

## 15. 故障场景与处置矩阵

| 故障 | 识别证据 | 首要动作 | 恢复验证 |
|---|---|---|---|
| 构建成功但签名失败 | Attestation / Cosign 验证失败 | 禁止归档晋级 | 重新生成可信证明 |
| image digest 与制品清单不一致 | Artifact manifest / Registry digest | 阻断 GitOps 更新 | digest 完全匹配 |
| GitOps 合并但 Argo 未同步 | GitOps commit 与 Application revision 不一致 | 停止 Rollout | revision 与目标 commit 一致 |
| Argo CD SyncFailed | Sync operation / controller error | 停止发布 | Sync 成功且资源健康 |
| Pod Ready 但 5xx 上升 | Prometheus error rate | 停止扩大流量 | 回切后错误率恢复 |
| P95 / P99 恶化 | Histogram quantile | 停止推进 | 目标窗口恢复且样本充分 |
| Prometheus 无数据 | query empty / scrape target down | Error / Inconclusive | 数据源恢复且样本重新累计 |
| 候选流量过少 | candidate request count below threshold | 等待，不得判成功 | 达到最小样本数 |
| AnalysisRun 超时 | AnalysisRun status / controller event | 不得自动 Promote | 重新分析或人工裁决 |
| Argo selfHeal 覆盖动态流量字段 | Git diff / Rollouts status / route change | 停止双写 | 单一 writer 生效 |
| 单区域部分集群失败 | 每集群 Rollout / Analysis 状态 | 阻断下一波 | 按统一策略回切或恢复 |
| Runtime 回切后 Git 仍是坏版本 | route stable 但 GitOps digest 不一致 | 执行 GitOps revert | Argo Sync 到恢复 revision |
| GitOps 回退后流量仍指向候选 | Git revision 已回退但 active route 未恢复 | 执行运行时回切 | 路由、Pod digest、业务指标一致 |
| 控制面重启 | release state 与 Kubernetes 实际状态不一致 | 只读重建状态 | 对账完成后恢复操作 |

## 16. 实施路线：按可验证阶段接入

### Phase 0：现状盘点与字段所有权

- 盘点真实 GitOps 仓库、Argo CD Application / ApplicationSet；
- 记录 repoURL、targetRevision、path、destination、project；
- 盘点集群、namespace、Gateway / Istio / Ingress；
- 明确每个应用环境 traffic_owner；
- 对照现有 release_strategy.py 和 ci/release-strategies.json；
- 输出真实状态，不以示例配置代替。

验收：每个应用环境都能说明谁写镜像、谁同步资源、谁写流量权重、谁做分析、谁批准生产。

### Phase 1：制品身份贯通

- 从 Artifact Contract v2 提取 source SHA、bundle digest、image digest；
- 验证 SBOM、attestation、signature；
- GitOps 变更引用精确 image digest；
- 生成 release_id 并关联 Promotion ID；
- 建立 GitOps commit 与 artifact identity 的审计映射。

验收：从生产 Pod 的 image digest 反查唯一制品、source SHA、构建 run 和供应链证据。

### Phase 2：Argo CD 只读对账

- 接入 Application / ApplicationSet 只读状态；
- 校验目标 repo、revision、path、destination；
- 校验实际同步 revision 等于本次 GitOps commit；
- 读取 Sync / Health / operation 状态；
- 失败、未知和版本不匹配都阻断后续步骤。

验收：无需给 CI 集群写权限，也能准确知道部署意图是否同步。

### Phase 3：Rollouts 非生产演练

- 固定 Argo Rollouts 和 CRD 版本；
- 在隔离集群部署演示应用；
- 选择唯一 traffic router；
- 部署 AnalysisTemplate；
- 验证正常推进、指标失败、查询错误、样本不足、人工暂停和 abort；
- 验证 Argo CD 不覆盖 Rollouts 动态字段。

验收：有真实集群 Rollout / AnalysisRun / route 状态证据，而不只是 YAML 静态检查。

### Phase 4：Prometheus 自动分析

- 应用完成 RED 和业务指标埋点；
- 验证 scrape target、标签和 histogram；
- 建立错误率、P95/P99、样本量和业务成功率查询；
- 验证空值、NaN、查询超时和 Prometheus 故障；
- 将 AnalysisRun 状态只读反馈给 Release Controller。

验收：指标异常能阻止扩大流量；观测系统故障不能被误判为成功。

### Phase 5：发布控制面闭环

- 保留现有制品准入和 Promotion 规则；
- Release Controller 生成 GitOps commit；
- 等待 Argo CD sync；
- 等待 Rollouts / Analysis 结果；
- 按集群波次和人工审批策略推进；
- 保存所有阶段事件和证据。

验收：一个 release_id 串起制品、GitOps commit、Application、Rollout、AnalysisRun 和审批。

### Phase 6：回滚与生产治理

- 验证 Rollouts runtime abort / rollback；
- 验证 GitOps desired-state revert；
- 验证两条路径独立失败时的恢复流程；
- 演练部分集群失败、Prometheus 不可用、Argo CD 不可用和控制面重启；
- 建立生产审批、Break-glass、RBAC 和审计。

验收：回滚后流量、GitOps revision、运行镜像 digest 和业务指标一致。

## 17. 与当前 CICD 仓库的对应关系

| 能力 | 当前仓库已有基础 | 完整闭环仍需补齐 |
|---|---|---|
| 构建与依赖 DAG | CI workflow、DAG、toolchain 管理 | 实际业务项目持续接入验收 |
| 制品身份 | Artifact Contract v2 | image digest 与 bundle member 强绑定及消费端校验 |
| 供应链 | SBOM、扫描、attestation / 签名策略 | 生产 Registry 和真实验证身份接入 |
| 长期归档 | Archive workflow / artifact archive | 外部长期存储保留、灾备和恢复验收 |
| 环境晋级 | Promotion policy / workflow | 与真实部署状态的语义拆分和对账 |
| 环境指针 | deployment pointer | 明确 promotion pointer 与 runtime deployment pointer |
| 发布状态机 | scripts/ci/release_strategy.py | 与真实 Rollout / Analysis 状态建立适配器 |
| 路由期望状态 | HTTPRoute / Istio VirtualService 渲染 | 真实 traffic owner 选择，避免与 Rollouts 双写 |
| 集群波次 | ci/clusters.json、Placement / ApplicationSet 参考渲染 | 真实集群身份、PlacementDecision 和固定 pin 验收 |
| Argo CD | 接入规范 / ApplicationSet 设计 | 真实 repoURL、path、destination、Project 和 Sync 状态接入 |
| Argo Rollouts | 接入契约与 observe-only 设计 | 固定版本、真实 CRD、traffic router、Rollout 状态反馈 |
| Prometheus Analysis | 指标阈值与证据模型设计 | 真实 AnalysisTemplate、AnalysisRun、数据源和失败语义验收 |
| 自动决策 | 现有状态机可作为全局编排基础 | 真实状态适配、审批、幂等动作和运行时反馈 |
| 回滚 | 环境历史 Deployment pointer rollback；发布中 abort | Rollouts runtime rollback 与 GitOps desired-state rollback 双闭环 |
| 审计 | CI / GitHub 运行记录和制品元数据 | 统一 release event schema、持久化证据索引 |

### 17.1 必须保留的现有架构事实

- scripts/ci/release_strategy.py 是现有发布策略引擎，不能为了接入 Rollouts 再造平行状态机。
- ci/release-strategies.json 是策略配置来源，策略数字和波次顺序不能在新文档或 Workflow 中另写不一致的规则。
- ci/clusters.json 是参考集群目录，不是实际集群接入证明。
- 当前 .github/workflows/release.yml 生成并验证期望状态，不代表已对真实 Kubernetes 执行 apply。
- 当前 advance 使用操作者提供的 analysis JSON，不等于已从 Prometheus AnalysisRun 读取真实结果。
- 当前路由输出包含 Gateway API HTTPRoute 和 Istio VirtualService；接入 Rollouts 前必须选择 traffic owner。
- scenario 的 synthetic analysis 只能用于状态机测试，不能用于生产决策。
- ApplicationSet 清单渲染成功，不代表真实 Argo CD 已连接并同步目标集群。

### 17.2 建议新增的实现模块

不要把所有逻辑塞进 release_strategy.py。建议按职责增加：

~~~text
scripts/ci/
├── release_strategy.py             # 现有全局策略状态机
├── release_identity.py              # release_id 与制品身份校验
├── gitops_change.py                 # 生成 / 校验 GitOps 变更契约
├── argocd_observer.py               # 只读 Application / Sync / Health
├── rollouts_observer.py             # 只读 Rollout / ReplicaSet / AnalysisRun
├── analysis_evidence.py             # Analysis 结果归一化与证据校验
├── release_decision.py              # 全局决策矩阵与幂等动作门禁
├── release_events.py                # 事件 Schema / 去重 / 审计输出
└── rollback_coordinator.py          # runtime 与 GitOps 双回滚编排

ci/
├── release-strategies.json          # 现有全局策略配置
├── argo-rollouts-integration.json   # 接入契约与安全默认值
├── release-evidence.schema.json     # 证据 Schema（目标）
└── release-events.schema.json       # 事件 Schema（目标）

tests/
├── test_release_identity.py
├── test_argocd_observer.py
├── test_rollouts_observer.py
├── test_analysis_evidence.py
├── test_release_decision.py
└── test_rollback_coordinator.py
~~~

Observer 默认只读。Promote / Abort / Rollback 必须经过独立授权、审批、幂等和审计门禁。

## 18. 生产验收总清单

### 制品与供应链
- [ ] source SHA、CI run、toolchain、bundle digest 可追溯。
- [ ] SBOM、provenance、signature 与制品身份一致。
- [ ] 部署镜像使用不可变 digest。
- [ ] dev / staging / production 使用同一份制品 bytes。
- [ ] 未通过准入的制品不能写入 GitOps 生产路径。

### GitOps / Argo CD
- [ ] Application 的 repoURL、targetRevision、path、destination、project 已登记。
- [ ] GitOps commit 与 release_id、image digest 关联。
- [ ] Argo CD 实际 revision 与本次 commit 一致。
- [ ] SyncFailed、Degraded、Unknown 均阻断推进。
- [ ] Project RBAC 限制仓库、集群、namespace 和资源类型。
- [ ] selfHeal / prune 与 Rollouts 字段所有权完成验证。

### Rollouts / Traffic
- [ ] 固定 Argo Rollouts 版本和 CRD。
- [ ] 每个应用明确唯一 traffic owner。
- [ ] Canary / Blue-Green 的 Service / Route 选择正确。
- [ ] Rollout Pod Template digest 与已批准制品一致。
- [ ] 暂停、推进、中止和回切均有状态验证。
- [ ] 不存在 CICD renderer 与 Rollouts 双写权重。

### Prometheus / Analysis
- [ ] 应用 RED / 业务指标埋点正确。
- [ ] 候选版本指标可独立查询。
- [ ] 请求量门槛和观察窗口有效。
- [ ] 错误率、P95/P99、业务成功率阈值经过基线验证。
- [ ] 空值、NaN、无样本、查询错误、超时均不能判成功。
- [ ] Prometheus 故障不会触发自动 Promote。
- [ ] AnalysisRun 与 release_id、cluster_id 关联。

### 自动决策 / 多集群
- [ ] 决策矩阵覆盖 Successful / Failed / Error / Inconclusive。
- [ ] 关键生产步骤有明确审批策略。
- [ ] 同一应用环境只有一个活动 release。
- [ ] 幂等、互斥锁、resourceVersion 和重试策略已验证。
- [ ] ClusterPin 固定，区域波次按顺序推进。
- [ ] 部分集群失败时不会误开下一波。

### 回滚 / 审计
- [ ] Runtime rollback 与 GitOps revert 分别验证。
- [ ] 恢复后的流量、Git revision、image digest 和业务指标一致。
- [ ] 数据库 / 消息兼容策略已评审。
- [ ] 每次发布、分析、审批和回滚均有不可变证据引用。
- [ ] 控制面重启后能从 Git 与集群真实状态对账恢复。

## 19. 最终判断标准

只有下面这条链全部成立，才能称为 CI/CD 可追溯、可闭环：

~~~text
source commit
  ↓ 可追溯
trusted build
  ↓ 可验证
immutable artifact + image digest
  ↓ 可晋级
promotion record
  ↓ 可定位
GitOps commit
  ↓ 可确认
Argo CD synced revision
  ↓ 可观测
Rollout / ReplicaSet / Pod
  ↓ 有证据
Prometheus AnalysisRun + business probe
  ↓ 有决策
Promote / Pause / Abort
  ↓ 可恢复
Runtime rollback + GitOps desired-state rollback
  ↓ 可审计
release event timeline + evidence archive
~~~

缺少其中任何一环，都只能称为部分自动化或发布流程集成，不能宣称实现端到端闭环。


---

## 20. 学习版：先用一个真实发布故事理解整条链

第一次接触这套架构，不要先背组件名称。先想象公司要把“支付服务”的新版本从测试环境发布到生产环境。

### 20.1 发布前：先确定这次到底要发布什么

开发人员提交支付服务代码。代码仓库记录一个唯一的提交编号（Source Commit SHA），它相当于“这次源代码的身份证”。

CI 流水线随后执行：

1. 拉取这个确定版本的代码，而不是随时变化的分支内容。
2. 编译程序、运行单元测试和集成测试。
3. 检查代码漏洞、第三方依赖风险、密钥泄漏和许可证。
4. 制作部署包和容器镜像。
5. 给部署包和镜像计算摘要值（Digest）。摘要可以理解为文件内容的指纹：内容变化，指纹就会变化。
6. 生成软件物料清单（SBOM），说明这个软件由哪些组件和依赖组成。
7. 对制品签名并保存构建来源证明。
8. 把制品和相关证明归档。

此时只能说“制品构建并通过了规定检查”，还不能说“生产发布成功”。

### 20.2 晋级：为什么测试过的包不能重新编译

假设测试环境使用镜像摘要 sha256:AAA...。进入预发布和生产时，必须继续使用同一个镜像摘要。

不能在生产阶段重新编译一次。因为重新编译后，即使代码提交编号相同，编译器、依赖、构建参数或时间戳都可能变化，生成的文件也可能不同。

Promotion（制品晋级）就是检查这个已经构建好的制品是否允许进入下一个环境。它不是重新构建，也不是已经部署。

### 20.3 部署：GitOps 和 Argo CD 分别做什么

GitOps 可以先理解为“把部署配置也放进 Git 管理”。

例如，GitOps 仓库里写明：
- 支付服务应该运行哪个镜像摘要；
- 需要几个副本；
- 使用哪些 Service 和路由；
- 使用哪套灰度发布规则。

发布系统提交一个配置变更。Argo CD 持续观察这个仓库：Git 中写的目标状态如果与 Kubernetes 集群实际状态不一致，它就按授权规则同步。

这里有三个不同结果：
- Git 提交成功：只是部署说明已经写进仓库；
- Argo CD Synced：集群资源已经和指定 Git 版本对齐；
- 应用可用：还要看 Pod 是否就绪、请求是否成功、业务指标是否正常。

所以 Git 合并成功不等于应用已经部署成功。

### 20.4 灰度：Argo Rollouts 为什么存在

假设支付服务生产环境有 100% 用户流量。直接把所有用户切到新版本，风险很大。

Canary（金丝雀/灰度发布）可以先让少量流量访问新版本：
1. 先放 1% 流量给新版本；
2. 观察一段时间；
3. 如果错误率、延迟和业务成功率正常，再放到 5%；
4. 继续观察，再逐步提高到 25%、50%；
5. 最后才让新版本接收全部流量。

Argo Rollouts 是 Kubernetes 中负责执行这些发布步骤的控制器。它负责管理新旧版本的副本、暂停、推进和中止。它不是构建工具，也不负责决定哪个制品有资格进入生产。

### 20.5 自动分析：Prometheus 不只是“有数据就算通过”

Prometheus 可以理解为指标数据库。应用把请求数量、错误数量、请求耗时等指标暴露出来，Prometheus 定期采集并保存。

AnalysisTemplate 是“检查规则模板”，例如：
- 新版本错误率是否低于 1%；
- P95 延迟是否低于 300 毫秒；
- 观察窗口内是否至少有 50 个请求；
- 支付成功率是否没有明显下降。

AnalysisRun 是“某一次实际执行检查的记录”。它会按模板查询指标并给出结果。

注意：如果 Prometheus 没有数据、查询报错、请求数量不足，不能把它当成“指标正常”。正确处理通常是暂停或判为证据不足，而不是继续放量。

### 20.6 最后：成功和失败分别怎样结束

成功路径：
- 制品身份核验通过；
- GitOps 配置指向正确镜像；
- Argo CD 同步到正确版本；
- Rollouts 按步骤扩大流量；
- 每个观察阶段的指标都达到门槛；
- 业务验收和必要审批完成；
- 记录最终运行版本和所有证据。

失败路径：
- 停止继续扩大流量；
- 按策略将流量切回旧版本；
- 把 GitOps 仓库中的目标镜像也恢复为旧版本；
- 等待 Argo CD 同步回退后的配置；
- 检查实际 Pod 镜像、路由、错误率和业务成功率；
- 保存失败原因和回滚证据。

这就是“闭环”：不仅能把新版本发出去，还能知道发的是什么、发到了哪里、运行得怎么样、为什么继续或停止，以及失败后是否真的恢复。

## 21. 专业词汇解释：看不懂时先查这里

| 词语 | 用容易理解的话解释 | 在本架构里的作用 |
|---|---|---|
| CI | 持续集成。代码提交后自动编译、测试和检查 | 确认代码和构建结果符合要求 |
| CD | 持续交付/部署。把通过检查的版本送到环境中 | 管理从制品到运行环境的过程 |
| Pipeline / Workflow | 流水线。按顺序自动执行的一组任务 | 把构建、测试、发布步骤串起来 |
| Runner | 真正执行流水线命令的机器或容器 | 执行编译、测试、扫描等任务 |
| DAG | 有依赖关系的任务图 | 例如先编译，再测试，最后打包 |
| Toolchain | 编译工具链，例如 JDK、GCC、CMake | 保证构建使用明确的工具版本 |
| Artifact | 制品。编译后准备交付的软件包或镜像 | 后续测试、晋级和部署的对象 |
| Digest | 摘要/指纹。根据文件内容计算出的唯一校验值 | 确认部署的内容没有被替换 |
| Immutable | 不可变。发布后不能悄悄替换内容 | 防止同一个版本名称指向不同文件 |
| SBOM | 软件物料清单 | 说明软件包含哪些开源库和组件 |
| Provenance | 构建来源证明 | 说明制品由哪个仓库、提交和流水线生成 |
| Attestation | 经过验证的声明/证明 | 把构建过程和检查结果形成可验证证据 |
| Cosign / Signature | 签名工具/数字签名 | 验证制品来自可信发布者且未被替换 |
| Promotion | 制品晋级 | 决定同一个制品能否从测试进入预发布或生产 |
| Deployment Pointer | 部署指针 | 记录某环境当前认可或部署的制品，必须明确字段语义 |
| GitOps | 用 Git 保存和管理部署目标配置 | 让部署变更可审查、可追踪、可回退 |
| Desired State | 期望状态 | 我们希望集群最终运行成什么样 |
| Argo CD | Kubernetes 的 GitOps 同步工具 | 把 Git 中的部署配置同步到集群 |
| Application | Argo CD 的应用对象 | 告诉 Argo CD 从哪个仓库、目录部署到哪里 |
| ApplicationSet | 批量生成 Application 的工具 | 按规则为多个集群创建应用对象 |
| Sync | 同步 | 让集群配置和 Git 里指定版本一致 |
| Health | 健康状态 | Argo CD 根据资源状态判断是否达到健康条件 |
| Drift | 配置漂移 | 集群实际配置与 Git 期望配置不一致 |
| RBAC | 基于角色的权限控制 | 限制谁能看、改、发布哪些资源 |
| Kubernetes Controller | 控制器 | 持续观察实际状态，并尝试调整到期望状态 |
| ReplicaSet | 副本集 | 确保某个版本的 Pod 数量符合要求 |
| Pod | Kubernetes 中运行容器的基本单元 | 真正承载应用进程 |
| Rollout | 一次发布对象/发布过程 | 描述新版本怎样逐步替换旧版本 |
| Argo Rollouts | 渐进式发布控制器 | 管理灰度、蓝绿、暂停、推进和中止 |
| Canary | 灰度发布 | 先让少量流量使用新版本，再逐步扩大 |
| Blue-Green | 蓝绿发布 | 同时准备新旧两套版本，再切换用户入口 |
| Traffic Router | 流量路由器 | 决定请求进入旧版本还是新版本 |
| Traffic Owner | 流量权重唯一管理者 | 防止两个控制器互相覆盖流量比例 |
| Prometheus | 指标采集和查询系统 | 提供错误率、延迟、请求量等运行数据 |
| Metric | 指标 | 可量化的运行数据，例如请求数和耗时 |
| RED | Rate、Errors、Duration：请求量、错误、耗时 | 判断服务是否正常的常用指标组合 |
| Histogram | 直方图指标 | 统计请求耗时分布，计算 P95/P99 等分位值 |
| P95 / P99 | 95% / 99% 请求耗时不超过的值 | 观察慢请求和尾部延迟 |
| AnalysisTemplate | 分析规则模板 | 定义灰度阶段要检查哪些指标 |
| AnalysisRun | 一次实际分析任务 | 保存某次指标检查的执行结果 |
| Successful | 检查成功 | 所有必要检查有有效数据且达到门槛 |
| Failed | 检查失败 | 有明确证据表明指标不符合要求 |
| Error | 检查执行出错 | 查询或分析本身发生错误 |
| Inconclusive | 无法得出结论 | 数据不足或不确定，不能当作成功 |
| Fail Closed | 失败关闭 | 无法确认安全时，默认不继续放量 |
| Release ID | 发布唯一编号 | 把构建、部署、分析、审批和回滚记录串起来 |
| Cluster Pin | 本次发布固定的集群名单 | 防止发布过程中目标集群意外扩大 |
| Wave | 发布波次 | 先发一组集群，确认成功后再发下一组 |
| Adapter | 适配器 | 把不同系统的状态转换成统一格式 |
| Observer | 观察器 | 读取状态和证据，不执行修改动作 |
| Idempotency | 幂等 | 同一个操作重复执行，不会造成重复或额外副作用 |
| Break-glass | 紧急授权通道 | 正常权限不可用时，经审批临时执行高风险操作 |
| Runtime Rollback | 运行时回切 | 把真实用户流量和运行版本切回稳定版本 |
| GitOps Revert | Git 配置回退 | 把部署仓库恢复到已知正确的配置版本 |
| Expand / Migrate / Contract | 数据库兼容迁移三阶段 | 先扩展兼容结构，再迁移数据，最后清理旧结构 |

## 22. 对当前 CICD 仓库“是否全面”的审查结论

### 22.1 作为学习用的整体架构，主链路已经覆盖

目前文档已经把以下内容串联起来：
- 源码提交、代码评审、构建、测试和工具链；
- 制品身份、摘要校验、SBOM、签名、来源证明和归档；
- 制品晋级、环境指针和统一发布编号；
- GitOps 配置、Argo CD 同步和多集群 ApplicationSet；
- Argo Rollouts 灰度/蓝绿、流量管理和暂停/中止；
- Prometheus 指标、AnalysisTemplate / AnalysisRun 和自动判断；
- 人工审批、权限隔离、发布事件和审计；
- 多集群发布顺序、部分失败处理；
- 运行时回切与 Git 配置回退；
- 平台自身监控、故障矩阵、分阶段实施和验收。

### 22.2 仍需要补充或真实环境验证的部分

“文档覆盖”不等于“仓库已经实现”，更不等于“生产可用”。以下工作仍然重要：

| 缺口 | 为什么重要 | 后续应补什么 |
|---|---|---|
| 数据库变更发布 | 应用回滚不一定能回滚数据结构和数据 | 数据库迁移版本、兼容窗口、备份恢复、回滚/前滚策略 |
| Feature Flag（功能开关） | 可先部署代码但暂不向用户开放新功能 | 开关审批、用户分群、紧急关闭和配置审计 |
| 配置与密钥发布 | 配置错误或 Secret 变化也会导致故障 | 配置版本、密钥引用、轮换、变更审计和回滚 |
| 测试策略 | 只跑单元测试无法覆盖真实依赖和兼容问题 | 单测、集成、契约、端到端、性能、安全和灾难演练 |
| 发布前容量检查 | 新版本可能增加 CPU、内存、连接数或下游压力 | 容量基线、资源配额、HPA、连接池和限流验证 |
| 变更窗口与业务保护 | 高峰期、结算期或活动期发布风险更高 | 变更日历、冻结窗口、业务负责人确认和熔断条件 |
| 多服务依赖发布 | 微服务之间接口不兼容会导致单服务灰度失败 | API/消息兼容检查、依赖拓扑和分批顺序 |
| 备份与灾难恢复 | Git 回退不能恢复被破坏的数据 | 数据备份、恢复演练、RPO/RTO 和灾备切换 |
| 发布后观察与自动关闭 | 发布完成后故障可能延迟出现 | 延长观察期、自动告警、自动关闭发布和责任人通知 |
| 证据存储治理 | Workflow 日志会过期或被删除 | 长期存储、保留策略、不可篡改、访问审计和恢复演练 |

### 22.3 结论：全面到什么程度

这套内容已经可以作为“现代应用 CI/CD + 制品治理 + GitOps + 渐进式发布”的总体学习蓝图，但不能称为所有企业场景都已覆盖，也不能称为真实生产闭环已经完成。

尤其要分清三个层次：
1. **架构文档覆盖**：说明应该有哪些组件、流程、边界和失败处理。
2. **仓库自动化实现**：代码、Workflow、策略和测试确实执行了这些规则。
3. **生产环境验收**：真实 Argo CD、Kubernetes、Rollouts、Prometheus、Registry 和审批系统已连通，并完成成功/失败/回滚演练。

当前 CICD 仓库已有不少基础，但 Argo CD / Rollouts / Prometheus 的真实联动仍是后续接入工作。下一阶段应先补数据库变更、配置与密钥、Feature Flag、多服务兼容和灾备专题，再按 Phase 0 到 Phase 6 逐步接真实环境。

### 22.4 推荐学习顺序

不要从头到尾死记英文术语。建议按下面顺序学习，每次只弄懂一段链路：

1. CI：代码怎样变成一个可交付的软件包；
2. 制品治理：怎样证明这个包没有被替换、来自哪里；
3. Promotion：为什么测试过的同一个包才能进入生产；
4. GitOps：部署配置为什么也要进 Git；
5. Argo CD：Git 里的配置怎样变成集群里的资源；
6. Kubernetes 基础对象：Deployment、ReplicaSet、Pod、Service 分别做什么；
7. Argo Rollouts：怎样只让一小部分用户先访问新版本；
8. Prometheus：怎样测量请求量、错误率和延迟；
9. Analysis：怎样根据指标自动继续、暂停或停止；
10. 回滚：怎样同时恢复真实流量和 Git 里的部署目标；
11. 审计：怎样从一个发布编号还原整次发布过程。
