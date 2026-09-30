# 灰度、蓝绿与跨集群跨区域灰度

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
这份制品进入哪些集群、哪个区域先接灰度流量？
```

这三个问题不能再用“重新构建一次”或“把 Deployment 环境名改掉”来回答。候选 bundle SHA256 必须等于该环境指针，发布引擎不重新构建。

## 2. 单集群灰度为什么不够

环境内策略 `canary` 会把该环境里每一个 `traffic=gateway` 的集群放进同一次权重。参考目录的 production 因此会让 cn-east 和 cn-north 一起从 1% 走到 100%。华东加到 1% 时，华北也是 1%。这是跨集群的同一权重，不是按区域展开的灰度。

旧的 `multi_cluster` 也不是跨集群、跨区域灰度。它只在 `role=canary` 的 `prod-cn-east-canary` 上做 HTTPRoute 权重，后面的波次改成蓝绿。那是“一个灰度集群，再加上后续区域的蓝绿”，区域内的其他集群没有共享 canary 权重，后开区域也不再走 1、5、25、50、100。

多集群灰度要同时满足两件事：

```text
集群名单：精确到 cluster id，路由不能把名单外面的集群加进来
流量：名单里的集群按 canary 权重切，而不是后开区域改走蓝绿
```

因此本仓库把 `multi_cluster_canary` 做成一等策略。它按区域顺序打开，区域内所有入选集群共享同一步权重，后开区域在打开前保持 0% canary。后开区域仍然按 canary 权重推进，不会改成蓝绿。

## 3. 方法：路由和集群管理各管一件事

| 发布方式 | 要决定的事 | 方法 | 本仓库钉死的工具 |
| --- | --- | --- | --- |
| 环境内灰度 `canary` | 同一环境里已选中的 gateway 集群，如何用同一步权重分配新旧版本 | 路由 | Gateway API `HTTPRoute` 后端权重 |
| 蓝绿 `blue_green` | 新旧版本如何各占一个槽位，并一次切完生产流量 | 路由，而且必须原子切换 | Gateway API `HTTPRoute`；预览使用独立 header |
| 多集群灰度 `multi_cluster_canary` | 多个区域、每个区域内多个集群，如何按同一套灰度权重展开 | 集群管理决定名单，路由只改名单内的权重 | OCM `PlacementDecision` + Argo CD `ApplicationSet` list generator，加上 Gateway API `HTTPRoute` |

灰度如果只靠“先发 1 个集群，再发其余集群”，控制的是集群成员，不是 1% 的请求。那是波次，不是灰度。

蓝绿如果把权重从 0 调到 100，中间会同时存在两个版本的生产流量，不再是蓝绿。蓝绿保持自己的策略，不并进多集群灰度。

多集群如果只放一个全局 Ingress，权重变化会把新 digest 送到所有挂在这个 Ingress 后面的集群。路由不能代替名单。

因此 `multi_cluster_canary` 的实现是：

```text
先用集群管理得到这一区域的精确 cluster id 名单
再只在这份名单里调整 HTTPRoute 的 canary 权重
下个区域没打开时，不渲染它的路由，权重保持 0
```

Gateway API 是路由合同，而不是某一家服务网格。能实现 HTTPRoute 的 Istio、Contour、NGINX Gateway 或 Envoy Gateway 都可以做数据面。仓库不把 VirtualService、Ingress 注解权重写成第二份事实来源，避免两套权重漂移。

集群管理钉死的是两份对象：

- `PlacementDecision.status.decisions[].clusterName` 是允许落地的集群 ID；
- `ApplicationSet` 使用 list generator，元素就是这些 ID。

不使用 ApplicationSet 的 cluster selector generator。selector 会在标签漂移时扩大范围。Karmada Placement、Rancher Fleet 属于同一类工具；接入时必须消费同一份 `ClusterPin`，不能再维护一份自己的 selector。HTTPRoute 的 `cicd.platform/cluster-id` 必须落在当前已打开波次的 `ClusterPin.clusterIds` 里。名单外的集群不能出现在路由里。

## 4. 和晋级、回滚的边界

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
abort（区域灰度尚未全部完成时）
  或 rollback.yml（发布 completed 之后要回到旧 digest）
```

发布引擎不做的事：

- 不执行 build command；
- 不创建 GitHub Deployment；
- 不保存 kubeconfig；
- 不把本地推演写成“已经在生产集群切流”。

`ci/clusters.json` 是参考目录，用来验证选择规则。它不代表这些集群已经接入。

## 5. 参考集群

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

`prod-cn-east-canary` 的 `role=canary` 不再单独构成一波。多集群灰度按区域选集群，所以它和 `prod-cn-east-a`、`prod-cn-east-b` 同在 cn-east，共享同一步权重。

## 6. 候选制品

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

第一条例说明候选版本已经晋级到这个环境。第二条说明确实有一个旧版本正在服务；候选已经是基线时，没有可发布的差异。引擎不因为灰度再构建一次。

`release.yml` 会调用 `deployment_pointer.py current`，再用 `check-pointer` 核对。操作者手填的 digest 和环境指针不一致时，Job 失败。

## 7. 环境内灰度

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

这是环境内灰度。参考目录的 production 会同时选中 cn-east 和 cn-north 的全部 gateway 集群，同一步权重作用到所有这些集群。要先开华东、让华北停在 0%，用第 8 节。

## 8. 多集群灰度

策略名：`multi_cluster_canary`。这是跨集群、跨区域灰度的做法。

方法分成两层：

```text
method = cluster_management
traffic_strategy = canary
工具 = PlacementDecision + ApplicationSet list generator + HTTPRoute
```

区域顺序写在 `region_order`，参考目录是 `cn-east` 然后 `cn-north`。每一波的 `strategy` 必须是 `canary`。策略校验会拒绝把后开区域写成 `blue_green`，也会拒绝只配置一个区域。

| 顺序 | 波次 | 区域 | 选择器 | 流量策略 | 参考目录命中的集群 |
| --- | --- | --- | --- | --- | --- |
| 1 | `cn-east` | cn-east | environment=production, region=cn-east | canary | `prod-cn-east-a`、`prod-cn-east-b`、`prod-cn-east-canary` |
| 2 | `cn-north` | cn-north | environment=production, region=cn-north | canary | `prod-cn-north-a` |

命中规则：

```text
选择器全部字段匹配
并且不在更早的波次里
并且不在 denylist
并且在 allowlist 中（allowlist 为空表示不额外限制）
并且 traffic=gateway
```

一个集群只属于最早命中的波次。cn-east 里三个 gateway 集群共享这一波的 canary 权重。cn-north 是下一波，在打开之前不渲染。

`prod-edge-offline` 的区域是 cn-edge，`traffic=none`，不在任何波次。dev 和 staging 也不在 production 的波次里。它们的服务 digest 保持基线，渲染结果里不允许出现这些 ID。

不把 `prod-edge-offline` 写进 `--allow` 时，它只是未选中。把它写进 `--allow`，计划直接失败，错误说明它没有 gateway，并且不会把它放进 `ClusterPin`。这是失败关闭：明确点名也不能绕过流量能力。

空波次直接失败。只 allow `prod-cn-north-a` 时，华东波次变成空波次，整次发布失败，不会跳过华东去发华北。deny 掉华东全部集群同样失败。allow 里的环境外集群，例如对 production 传入 `dev-cn-east-a`，也会失败。

权重仍是 canary 的 1、5、25、50、100。分析阈值和失败行为与第 7 节相同。分析失败不改变任何区域的权重。

展开方式：

| 时刻 | cn-east 三个集群 | cn-north |
| --- | --- | --- |
| 计划生成，华东已打开 | canary 权重 0，候选已装上，stable 仍是基线 | 未打开。权重视为 0，保持基线，不出现在渲染结果里 |
| 华东 `1pct` | 三个集群都是 1 / 99 | 仍未打开 |
| 华东 `5pct` | 三个集群都是 5 / 95 | 仍未打开 |
| 华东 `25pct` | 三个集群都是 25 / 75 | 仍未打开 |
| 华东 `50pct` | 三个集群都是 50 / 50 | 仍未打开 |
| 华东 `100pct` | stable 收成候选，canary 权重回到 0 | 此时才打开，canary 权重 0，策略仍是 canary |
| 华北 `1pct` | stable 保持候选 | 1 / 99 |
| 华北 `5pct` | stable 保持候选 | 5 / 95 |
| 华北直到 `100pct` | stable 保持候选 | stable 收成候选，发布 `completed` |

同一区域内的集群永远使用这一步的同一个 canary 权重。不同区域在展开过程中可以处于不同权重：华东已经是候选稳定版本时，华北仍然可以停在 5%。后开区域仍然按 canary 权重推进，不会改成蓝绿。

`abort` 把每一个已经打开的区域收成 stable 权重 100、基线 digest。华东已经完成、华北停在 5% 时中止，两个区域都回到基线，不能留下“华东已经是新版本、华北失败了但华东继续接新流量”。未打开的区域本来就不在渲染结果里。`completed` 之后不能 abort，要走环境 rollback。

进行中的渲染只包含已经打开的波次。华东还在 25% 时，华北的 Placement、ApplicationSet 和 HTTPRoute 都不存在。

## 9. 蓝绿

策略名：`blue_green`。方法仍是 `routing`，切换方式是 `atomic`。它不是多集群灰度的后半段。

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

和多集群灰度一样，同一环境里没有 gateway 的集群必须 `--accept-excluded` 才会被排除，排除后保持基线。

## 10. 分析证据

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

`requests` 必须是整数。缺证据即失败，不会把缺省值当成通过。失败时当前区域和已经完成的区域都保持原权重。

`scenario` 子命令会自行构造一份全通过的证据，把状态机走到 `completed`，用来检查最终期望状态。workflow 里对应的句子是：

```text
synthetic analysis is not production evidence
```

生产推进只能使用 `advance`，并且证据来自真实流量或真实探活。

## 11. 渲染结果

每个已经打开的波次产出这些 JSON。Kubernetes 和 Argo CD 可以直接 apply JSON。

| 文件 | 对象 | 作用 |
| --- | --- | --- |
| `<service>-<wave>-pin.json` | `cicd.platform/v1` `ClusterPin` | 精确集群 ID、区域、候选 digest、基线 digest |
| `<service>-<wave>-placement.json` | `Placement` | `cicd.platform/cluster-id In <这些 ID>`，`numberOfClusters` 等于名单长度 |
| `<service>-<wave>-decision.json` | `PlacementDecision` | `clusterName` 列表，必须和 ClusterPin 一致 |
| `<service>-<wave>-appset.json` | `ApplicationSet` | list generator，每个元素带该集群的 digest 和权重 |
| `<service>-<cluster>-route.json` | `HTTPRoute` | 只带这个集群的标签，权重之和为 100 |
| `<service>-<cluster>-preview.json` | `HTTPRoute` | 仅蓝绿预览步骤存在 |

多集群灰度进行中时，ApplicationSet 元素同时带着：

```text
stableDigest = 基线（本区域尚未收口）或候选（本区域 100% 已收口）
canaryDigest = 候选；收口后清空
stableWeight / canaryWeight = 当前区域的这一步
```

同一波次里每个元素的 `canaryWeight` 相同。不能把单个 `image.digest` 写成候选后再靠路由“看起来像灰度”。那样 stable 后端也会变成新版本。

HTTPRoute、PlacementDecision 和 ApplicationSet 里的集群 ID 必须等于这一波的 `ClusterPin.clusterIds`。未打开的区域、其他环境、`prod-edge-offline` 都不会出现。

蓝绿元素带着 `blueDigest`、`greenDigest`、`activeSlot`。预览步骤额外带 `previewDigest`。

每份对象都有注解：

```text
cicd.platform/release-digest
cicd.platform/baseline-digest
cicd.platform/strategy
cicd.platform/traffic-strategy
cicd.platform/region
cicd.platform/clusters
cicd.platform/evidence
```

`evidence` 为 `unverified`（刚 plan）、`operator`（advance 或 abort）或 `synthetic`（scenario）。控制器可以拒绝 apply `synthetic` 和 `unverified`。

## 12. 外部控制器合同

控制器是仓库之外的组件。它要做的检查：

1. 读取 `ClusterPin.clusterIds`。
2. `PlacementDecision` 里每一个 `clusterName` 都必须在这份名单里，名单里的每一个 ID 也都必须出现。
3. 只把 ApplicationSet 的 list 元素发到同名集群。
4. HTTPRoute 只应用到 `cicd.platform/cluster` 注解指向的集群，并且该集群必须属于这份 pin。
5. 忽略一切不在已打开波次里的集群，不要用环境级 selector 再选一次。后开区域保持基线，直到它自己的波次出现在渲染结果里。
6. 看到 `evidence=synthetic` 或 `unverified` 时拒绝作用于生产。
7. 不在 GitHub Actions 里存放 kubeconfig。`release.yml` 只上传 `release-state.json` 和 `rendered/`。

控制面测试通过，只说明期望状态符合上面的规则。它不表示生产集群已经切了流量。

## 13. 命令

在仓库根目录执行。

校验策略和参考目录：

```bash
python3 scripts/ci/release_strategy.py validate
```

多集群灰度是 production 上跨集群、跨区域发布的命令。不需要 `--accept-excluded`。`prod-edge-offline` 默认不在名单里：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster_canary \
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

推进当前区域的下一步。证据失败时权重不变：

```bash
python3 scripts/ci/release_strategy.py advance \
  --state release-state.json \
  --analysis analysis.json \
  --out release-state.json \
  --out-dir rendered
```

任一区域尚未全部完成时中止。已打开的每个区域都回到基线：

```bash
python3 scripts/ci/release_strategy.py abort \
  --state release-state.json \
  --out release-state.json \
  --out-dir rendered
```

对 dev 做一次环境内灰度。dev 没有缺少流量能力的集群，所以不需要 `--accept-excluded`。这次会让 dev 里所有 gateway 集群使用同一步权重，没有区域顺序：

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

production 上的环境内灰度必须看到排除列表。确认边缘集群保持基线后，cn-east 和 cn-north 会使用同一步权重：

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

蓝绿仍然单独使用 `--strategy blue_green`。`--active-slot` 表示当前基线槽，默认 `blue`。

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

## 14. Workflow

`.github/workflows/release.yml` 只在 `main` 上手工触发。默认策略是 `multi_cluster_canary`。

```text
validate 策略
→ deployment_pointer.py current
→ check-pointer
→ plan 或 scenario
→ 上传 release-state.json 与 rendered/
```

权限是 `contents: read` 和 `deployments: read`。没有 `deployments: write`，所以它不能移动环境指针，也不能代替 rollback。

`action=plan` 渲染当前步骤，证据标记为 `unverified`。`action=scenario` 使用合成证据走完步骤。生产切流用仓库外的控制器消费 `advance` 之后、证据标记为 `operator` 的渲染结果。

## 15. 已经验证的不变量

`tests/test_progressive_delivery.py` 锁定这些行为：

- 环境内灰度必须按 1、5、25、50、100 前进，分析失败不改变状态；
- 环境内灰度把 cn-east 和 cn-north 放到同一步权重；
- 灰度完成后 stable 后端是候选 digest，canary 权重为 0；
- abort 把已开始的流量收回到基线；
- dev 灰度不改变 staging 和 production；
- 蓝绿预览时生产流量仍是基线，cutover 的生产权重是 100；
- cutover 之后 abort 切回基线槽；
- confirm 后旧槽仍保留基线 digest，且不能再 abort；
- `multi_cluster_canary` 的区域顺序是 cn-east 然后 cn-north，两波都是 canary；
- 华东三个 gateway 集群共享同一步权重，华北在打开前是 0% 且不出现在渲染结果里；
- 华东收口后华北打开时权重为 0，下一步才是 1%，策略仍是 canary；
- 华东已是候选、华北停在 5% 时，两个区域权重不同；
- 分析失败不改变已打开区域的权重；
- abort 把每一个已打开区域回到基线，包括已经收口的华东；
- `prod-edge-offline`、dev、staging 保持基线，且不出现在渲染结果里；
- 把 `prod-edge-offline` 写进 allow 会因缺少 gateway 失败，不会进入 pin；
- 路由里的集群 ID 不会超出该波次的 ClusterPin；
- ApplicationSet 只有 list generator；
- 候选 digest 必须等于环境指针，且不能已经是基线。

平台校验 `validate.yml` 会执行 `release_strategy.py validate`。

## 16. 还没有做的事

真实集群 apply 需要集群里的 Gateway、带 `cicd.platform/cluster-id` 标签的 OCM ManagedCluster，以及消费这些 JSON 的 Argo CD。这些资源不在本仓库。控制面测试通过，不等于生产集群已经完成灰度。

把 kubeconfig 放进 GitHub Actions，或者在 Job 里直接 `kubectl apply`，都违反第 12 节的合同。
