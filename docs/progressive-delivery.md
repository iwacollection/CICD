# 灰度、蓝绿与多集群精准发布

这份文档是发布策略的规格。`scripts/ci/release_strategy.py`、`ci/release-strategies.json`、`ci/clusters.json` 和 `.github/workflows/release.yml` 按这里的规则实现。规则变了，要同时改策略文件、引擎和 `tests/test_progressive_delivery.py`。

## 1. 它解决什么问题

制品晋级已经保证：

```text
Build once
→ Archive
→ dev → staging → production
→ 同一个 bundle SHA256
```

环境指针只表示“这个 digest 被授权出现在该环境”。它还没有回答：

```text
请求按什么比例打到新版本？
新旧版本能不能一次切完，而不是爬权重？
这份制品到底进入哪些集群？
```

这三个问题不能再用“重新构建一次”或“把 Deployment 环境名改掉”来回答。

## 2. 方法：路由和集群管理各管一件事

| 发布方式 | 要决定的事 | 方法 | 本仓库钉死的工具 |
| --- | --- | --- | --- |
| 灰度 | 同一服务的请求如何在旧版本和新版本之间按比例分配 | 路由 | Gateway API `HTTPRoute` 后端权重 |
| 蓝绿 | 新旧版本如何各占一个槽位，并一次切完生产流量 | 路由，而且必须原子切换 | Gateway API `HTTPRoute`；预览使用独立 header |
| 多集群精准部署 | 这份制品允许进入哪些集群 | 集群管理 | Open Cluster Management `PlacementDecision` + Argo CD `ApplicationSet` |

灰度如果只靠“先发 1 个集群，再发其余集群”，控制的是集群成员，不是 1% 的请求。那是波次，不是灰度。

蓝绿如果把权重从 0 调到 100，中间会同时存在两个版本的生产流量，不再是蓝绿。

多集群如果只放一个全局 Ingress，权重变化会把新 digest 送到所有挂在这个 Ingress 后面的集群。路由不能代替名单。

因此实现是：

```text
多集群：先用集群管理得到精确 cluster id 名单
灰度 / 蓝绿：只在这份名单里调整 HTTPRoute
```

单独做灰度或蓝绿时，名单是“该环境里具备 `traffic=gateway` 的集群”。不具备流量能力的集群必须被明确排除，不能被静默跳过。

Gateway API 是路由合同，而不是某一家服务网格。能实现 HTTPRoute 的 Istio、Contour、NGINX Gateway 或 Envoy Gateway 都可以做数据面。仓库不把 VirtualService、Ingress 注解权重写成第二份事实来源，避免两套权重漂移。

集群管理钉死的是两份对象：

- `PlacementDecision.status.decisions[].clusterName` 是允许落地的集群 ID；
- `ApplicationSet` 使用 list generator，元素就是这些 ID。

不使用 ApplicationSet 的 cluster selector generator。selector 会在标签漂移时扩大范围。Karmada Placement、Rancher Fleet 属于同一类工具；接入时必须消费同一份 `ClusterPin`，不能再维护一份自己的 selector。

## 3. 和晋级、回滚的边界

```text
promotion.yml
  证明 exact artifact identity
  写入环境指针
        |
        v
release.yml / release_strategy.py
  候选 digest == 当前环境指针
  基线 digest == 现在正在接流量的版本
  产出期望状态 JSON
        |
        v
外部控制器
  按 ClusterPin 应用到真实集群
        |
        v
abort（confirm 之前）
  或 rollback.yml（confirm 之后要回到旧 digest）
```

发布引擎不做的事：

- 不执行 build command；
- 不创建 GitHub Deployment；
- 不保存 kubeconfig；
- 不把本地推演写成“已经在生产集群切流”。

`ci/clusters.json` 是参考目录，用来验证选择规则。它不代表这些集群已经接入。

## 4. 参考集群

| 集群 | 环境 | 区域 | 角色 | 流量能力 |
| --- | --- | --- | --- | --- |
| `dev-cn-east-a` | dev | cn-east | stable | gateway |
| `staging-cn-east-a` | staging | cn-east | stable | gateway |
| `prod-cn-east-canary` | production | cn-east | canary | gateway |
| `prod-cn-east-a` | production | cn-east | stable | gateway |
| `prod-cn-east-b` | production | cn-east | stable | gateway |
| `prod-cn-north-a` | production | cn-north | stable | gateway |
| `prod-edge-offline` | production | cn-edge | stable | none |

真实集群接入时，每个集群还要有标签 `cicd.platform/cluster-id=<集群 id>`。Placement 的 `matchExpressions` 用这个标签做 In 过滤。即使标签配错，控制器仍必须以 `ClusterPin.clusterIds` 和 `PlacementDecision` 的交集为准，多出来的集群直接拒绝。

## 5. 候选制品

计划必须同时带上 Artifact Contract v2 的五个身份字段：

```text
artifact_name
bundle_sha256
source_sha
source_run_id
release_tag
```

另外两个 digest：

```text
environment_pointer_digest  该环境当前 successful Deployment 的 bundle_sha256
baseline_digest             当前正在接生产流量的 digest
```

约束：

```text
environment_pointer_digest == bundle_sha256
baseline_digest != bundle_sha256
```

第一条例说明候选版本已经晋级到这个环境。第二条说明确实有一个旧版本正在服务；候选已经是基线时，没有可发布的差异。

`release.yml` 会调用 `deployment_pointer.py current`，再用 `check-pointer` 核对。操作者手填的 digest 和环境指针不一致时，Job 失败。

## 6. 灰度

策略名：`canary`。方法：`routing`。工具：`HTTPRoute`。

后端只有两个：

```text
<service>-stable   基线 digest
<service>-canary   候选 digest
```

权重步骤：

| 步骤 | canary 权重 | stable 权重 | 进入步骤前要通过的分析 |
| --- | --- | --- | --- |
| 打开波次 | 0 | 100 | 无。候选已经装到 canary 后端，但没有流量 |
| `1pct` | 1 | 99 | smoke、error_rate、latency_p95 |
| `5pct` | 5 | 95 | error_rate、latency_p95 |
| `25pct` | 25 | 75 | error_rate、latency_p95 |
| `50pct` | 50 | 50 | error_rate、latency_p95 |
| `100pct` | 先到 100，随即收口 | 100 在 stable | error_rate |

`100pct` 通过后引擎把候选 digest 写到 stable 后端，canary 权重回到 0，canary digest 清空。这样下一次发布仍有明确的基线，而不是永远挂在 canary 后端上。

阈值：

```text
error_rate <= 0.01
latency_p95_ms <= 300
requests >= 50
```

分析失败时状态不变，权重不动。不能跳步。`advance` 每次只进入下一步。

`abort` 把已开始的灰度收成 stable 权重 100、基线 digest，canary 不再接流量。完成后的发布不能 abort，要走环境 rollback。

同一环境里 `traffic` 不是 `gateway` 的集群会进入 `excluded`。只要这份列表非空，计划就失败，除非显式传入 `--accept-excluded`。确认之后，这些集群保持基线 digest，并且不会出现在 HTTPRoute 里。`prod-edge-offline` 就是这个例子。

dev 环境目前只有具备 gateway 的集群，所以 dev 灰度不需要这面确认旗标。dev 的计划也不会改 staging 或 production 的服务视图。

## 7. 蓝绿

策略名：`blue_green`。方法仍是 `routing`，切换方式是 `atomic`。

槽位固定为 `blue` 和 `green`。`--active-slot` 表示现在接流量的槽，默认 `blue`。另一个槽是 inactive。

| 步骤 | 生产流量 | 预览 | 分析 |
| --- | --- | --- | --- |
| `deploy_inactive` | 100% 仍在基线槽 | 无 | readiness=pass |
| `preview` | 100% 仍在基线槽 | header `x-release-preview: true` 打到 inactive | smoke=pass |
| `cutover` | 100% 切到候选槽 | 预览路由删除 | error_rate、latency_p95 |
| `confirm` | 保持候选槽 | 无 | 无 |

预览是另一条 HTTPRoute，不修改生产路由的权重。生产路由在 cutover 之前权重始终是 100，后端是基线槽。

cutover 不做 50/50。生产后端直接变成 `<service>-green` 或 `<service>-blue`，权重 100。

confirm 之后，原来的基线槽仍保留旧 digest，作为这次发布的回退窗口。发布状态变为 `completed`。此后 `abort` 会被拒绝，回退旧 digest 使用 `rollback.yml`。

confirm 之前 abort 会把 active 槽切回基线槽，并清掉 inactive 上的候选 digest。cutover 之后、confirm 之前 abort 同样切回基线槽。

## 8. 多集群精准部署

策略名：`multi_cluster`。方法：`cluster_management`。

默认波次，全部指向 production：

| 顺序 | 波次 | 选择器 | 波次内流量策略 | 参考目录命中的集群 |
| --- | --- | --- | --- | --- |
| 1 | `canary-clusters` | environment=production, role=canary | 灰度 | `prod-cn-east-canary` |
| 2 | `cn-east` | environment=production, region=cn-east | 蓝绿 | `prod-cn-east-a`、`prod-cn-east-b` |
| 3 | `production-gateway` | environment=production, traffic=gateway | 蓝绿 | `prod-cn-north-a` |

命中规则：

```text
选择器全部字段匹配
并且不在更早的波次里
并且不在 denylist
并且在 allowlist 中（allowlist 为空表示不额外限制）
```

一个集群只属于最早命中的波次。所以 `prod-cn-east-canary` 虽然也在 cn-east，但不会在第二波再做一次蓝绿。

`prod-edge-offline`、dev、staging 不在任何波次。它们的服务 digest 保持基线，渲染结果里不允许出现这些 ID。

波次选中的集群如果缺少该流量策略要求的 `traffic=gateway`，计划直接失败，不会静默丢掉。

空波次直接失败。只 allow `prod-cn-north-a` 时，第一波变成空波次，整次发布失败，不会跳过 canary 波次去发华北。

allowlist 里的每个 ID 都必须被某一波选中。把 `prod-edge-offline` 写进 allowlist 不会把它加进发布，只会因为“没有被任何波次选中”而失败。

denylist 去掉 `prod-cn-east-canary` 会让第一波为空，发布失败。

进行中的渲染只包含已经打开的波次。第一波还在 1% 时，华东和华北不会出现在 ApplicationSet 里。后一波要等前一波 `completed` 才打开。

打开灰度波次时，候选 digest 装到 canary 后端且权重为 0。打开蓝绿波次时，还不把候选写入槽位；下一步才是 `deploy_inactive`。

任一波次 abort，会把这次发布里所有已经打开的波次回到基线。不能留下“canary 集群已经是新版本，区域波次失败了但 canary 继续接新流量”的半截生产状态。如果只想让 canary 集群停留在新版本，应单独使用 `canary` 策略，而不是 `multi_cluster`。

## 9. 分析证据

`advance` 读取一个 JSON 对象。需要哪些字段由当前步骤决定。

```json
{
  "smoke": "pass",
  "readiness": "pass",
  "error_rate": 0.0,
  "latency_p95_ms": 20,
  "requests": 80
}
```

失败条件：

```text
要求 smoke 时，值不是 pass
要求 readiness 时，值不是 pass
要求 error_rate 或 latency 时，requests 小于 50，或字段缺失
error_rate 大于 0.01
latency_p95_ms 大于 300
```

`requests` 必须是整数。缺证据即失败，不会把缺省值当成通过。

`scenario` 子命令会自行构造一份全通过的证据，把状态机走到 `completed`，用来检查最终期望状态。workflow 里对应的句子是：

```text
synthetic analysis is not production evidence
```

生产推进只能使用 `advance`，并且证据来自真实流量或真实探活。

## 10. 渲染结果

每个已经打开的波次产出这些 JSON。Kubernetes 和 Argo CD 可以直接 apply JSON。

| 文件 | 对象 | 作用 |
| --- | --- | --- |
| `<service>-<wave>-pin.json` | `cicd.platform/v1` `ClusterPin` | 精确集群 ID、候选 digest、基线 digest |
| `<service>-<wave>-placement.json` | `Placement` | `cicd.platform/cluster-id In <这些 ID>`，`numberOfClusters` 等于名单长度 |
| `<service>-<wave>-decision.json` | `PlacementDecision` | `clusterName` 列表，必须和 ClusterPin 一致 |
| `<service>-<wave>-appset.json` | `ApplicationSet` | list generator，每个元素带该集群的 digest 和权重 |
| `<service>-<cluster>-route.json` | `HTTPRoute` | 只带这个集群的标签，权重之和为 100 |
| `<service>-<cluster>-preview.json` | `HTTPRoute` | 仅预览步骤存在 |

灰度进行中时，ApplicationSet 元素同时带着：

```text
stableDigest = 基线
canaryDigest = 候选
stableWeight / canaryWeight = 当前步骤
```

不能把单个 `image.digest` 写成候选后再靠路由“看起来像灰度”。那样 stable 后端也会变成新版本。

蓝绿元素带着 `blueDigest`、`greenDigest`、`activeSlot`。预览步骤额外带 `previewDigest`。

每份对象都有注解：

```text
cicd.platform/release-digest
cicd.platform/baseline-digest
cicd.platform/strategy
cicd.platform/clusters
cicd.platform/evidence
```

`evidence` 为 `unverified`（刚 plan）、`operator`（advance 或 abort）或 `synthetic`（scenario）。控制器可以拒绝 apply `synthetic` 和 `unverified`。

## 11. 外部控制器合同

控制器是仓库之外的组件。它要做的检查：

1. 读取 `ClusterPin.clusterIds`。
2. `PlacementDecision` 里每一个 `clusterName` 都必须在这份名单里，名单里的每一个 ID 也都必须出现。
3. 只把 ApplicationSet 的 list 元素发到同名集群。
4. HTTPRoute 只应用到 `cicd.platform/cluster` 注解指向的集群。
5. 忽略一切不在已打开波次里的集群，不要用环境级 selector 再选一次。
6. 看到 `evidence=synthetic` 或 `unverified` 时拒绝作用于生产。
7. 不在 GitHub Actions 里存放 kubeconfig。`release.yml` 只上传 `release-state.json` 和 `rendered/`。

## 12. 命令

在仓库根目录执行。

校验策略和参考目录：

```bash
python3 scripts/ci/release_strategy.py validate
```

对 dev 做一次灰度计划。dev 没有缺少流量能力的集群，所以不需要 `--accept-excluded`。下面的 digest 要换成真实晋级记录：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy canary \
  --environment dev \
  --service checkout \
  --artifact-name checkout-generic-linux-x86_64-gcc \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag artifact-v2-<archive-tag-suffix> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --out release-state.json \
  --out-dir rendered
```

推进下一步：

```bash
python3 scripts/ci/release_strategy.py advance \
  --state release-state.json \
  --analysis analysis.json \
  --out release-state.json \
  --out-dir rendered
```

confirm 之前中止：

```bash
python3 scripts/ci/release_strategy.py abort \
  --state release-state.json \
  --out release-state.json \
  --out-dir rendered
```

production 灰度必须看到排除列表。确认边缘集群保持基线后：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy canary \
  --environment production \
  --service checkout \
  --artifact-name checkout-generic-linux-x86_64-gcc \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag artifact-v2-<archive-tag-suffix> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --accept-excluded \
  --out release-state.json \
  --out-dir rendered
```

多集群计划不使用 `--accept-excluded`。未命中的集群本来就不是目标：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster \
  --environment production \
  --service checkout \
  --artifact-name checkout-generic-linux-x86_64-gcc \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag artifact-v2-<archive-tag-suffix> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --out release-state.json \
  --out-dir rendered
```

核对指针文件：

```bash
python3 scripts/ci/deployment_pointer.py current \
  --repository <owner>/<repo> \
  --environment production > pointer.json

python3 scripts/ci/release_strategy.py check-pointer \
  --pointer-json pointer.json \
  --bundle-sha256 <candidate-64-hex> \
  --environment production
```

`scenario` 只用于本地把状态机跑完并渲染最终 JSON。不要把这份渲染结果 apply 到生产。

## 13. Workflow

`.github/workflows/release.yml` 只在 `main` 上手工触发。

```text
validate 策略
→ deployment_pointer.py current
→ check-pointer
→ plan 或 scenario
→ 上传 release-state.json 与 rendered/
```

权限是 `contents: read` 和 `deployments: read`。没有 `deployments: write`，所以它不能移动环境指针，也不能代替 rollback。

`action=plan` 渲染当前步骤，证据标记为 `unverified`。`action=scenario` 使用合成证据走完步骤。生产切流用仓库外的控制器消费 `advance` 之后、证据标记为 `operator` 的渲染结果。

## 14. 已经验证的不变量

`tests/test_progressive_delivery.py` 锁定这些行为：

- 灰度必须按 1、5、25、50、100 前进，分析失败不改变状态；
- 灰度完成后 stable 后端是候选 digest，canary 权重为 0；
- abort 把已开始的流量收回到基线；
- dev 灰度不改变 staging 和 production；
- 蓝绿预览时生产流量仍是基线，cutover 的生产权重是 100；
- cutover 之后 abort 切回基线槽；
- confirm 后旧槽仍保留基线 digest，且不能再 abort；
- 多集群三个波次的集群 ID 与第 8 节表格一致；
- 第一波渲染结果只有 `prod-cn-east-canary`，ApplicationSet 只有 list generator；
- 1% 时 stable digest 仍是基线，canary 权重是 1，权重和为 100；
- 完成后边缘、dev、staging 仍是基线；
- allow 不能把名单外面的集群加进来，deny 不能把某一波删空后继续；
- 波次中途 abort 会让所有已打开集群回到基线。

平台校验 `validate.yml` 会执行 `release_strategy.py validate`。

## 15. 还没有做的事

真实集群 apply 需要集群里的 Gateway、带 `cicd.platform/cluster-id` 标签的 OCM ManagedCluster，以及消费这些 JSON 的 Argo CD。这些资源不在本仓库。控制面测试通过，不等于生产集群已经完成灰度。

把 kubeconfig 放进 GitHub Actions，或者在 Job 里直接 `kubectl apply`，都违反第 11 节的合同。
