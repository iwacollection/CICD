# 灰度、蓝绿与跨集群跨区域发布操作手册

这份文档按操作顺序写。执行发布时读这里即可，不必打开 `scripts/ci/release_strategy.py`。策略数字、区域顺序和集群名单以 `ci/release-strategies.json`、`ci/clusters.json` 和这条 CLI 的输出为准。规则变了，要同时改策略文件、引擎、本手册和 `tests/test_progressive_delivery.py`。

相关文件：

| 文件 | 操作者要把它当成什么 |
| --- | --- |
| `scripts/ci/release_strategy.py` | 唯一策略引擎。子命令是 `validate`、`plan`、`advance`、`abort`、`render`、`scenario`、`check-pointer` |
| `ci/release-strategies.json` | 权重、槽位、区域顺序、分析阈值、预览 header |
| `ci/clusters.json` | 参考集群目录。用来校验选择规则，不代表集群已经接入 |
| `.github/workflows/release.yml` | `main` 上的 `workflow_dispatch`。先核对环境指针，再调用同一条 CLI |
| `ops/Jenkinsfile` | 可选调用方。只调用 `validate`、`plan`、`advance`、`render` |
| `.github/workflows/rollback.yml` | 发布 `completed` 之后恢复同环境历史 Deployment 指针 |

## 1. 先记住三条边界

1. 候选 bundle SHA256 必须等于该环境当前指针。发布不重新构建。
2. 本仓库写出期望状态 JSON。它不保存 kubeconfig，不执行 `kubectl apply`，也不创建 GitHub Deployment。
3. 控制面测试通过，只说明期望状态符合本手册。它不表示生产集群已经切了流量。`ci/clusters.json` 里的集群名是参考目录。

环境指针回答“这个 digest 可以出现在该环境”。它不回答流量百分比，也不回答哪些集群收到新版本。那两件事由下面的策略完成，而且仍然使用同一份已晋级制品。

## 2. 怎么选方法

一次发布只选一个 `--strategy`。

| 你要做的事 | 策略名 | 方法 | 钉死的工具 | 不要选它的情况 |
| --- | --- | --- | --- | --- |
| 同一环境里每个 `traffic=gateway` 的集群用同一步权重 | `canary` | `routing` | Gateway API `HTTPRoute` 后端权重 | 你想先开 cn-east、让 cn-north 停在 0%。环境内灰度会让两地一起变 |
| 同一环境里每个 gateway 集群各占一个槽，验证后一次切完 | `blue_green` | `routing`，切换方式 `atomic` | Gateway API `HTTPRoute`。预览走独立 header | 你想把权重从 1% 爬到 100%，或者只切一个区域 |
| 多个区域按同一套灰度权重展开 | `multi_cluster_canary` | `cluster_management`，流量策略仍是 `canary` | OCM `PlacementDecision` + Argo CD `ApplicationSet` list generator + `HTTPRoute` | 你想让后开区域改走蓝绿 |
| 多个区域共用槽位并一次切完 | `multi_cluster_blue_green` | `cluster_management`，流量策略仍是 `blue_green` | 同一套名单工具 + 原子 `HTTPRoute` | 你想在区域之间做 1、5、25、50、100 |

判断顺序：

```text
要不要按区域先后打开？
  否 → 环境内 canary 或 blue_green
  是 → 流量是按比例分配，还是一次切完？
        按比例 → multi_cluster_canary
        一次切完 → multi_cluster_blue_green
```

只给 `role=canary` 的 `prod-cn-east-canary` 做权重，再把后面的区域改成蓝绿，得到的不是跨区域灰度。那是旧的“一个灰度集群，再加上后续区域的蓝绿”。本仓库没有名为 `multi_cluster` 的策略。

灰度如果只靠“先发 1 个集群，再发其余集群”，控制的是集群成员，不是 1% 的请求。蓝绿如果把权重从 0 调到 100，中间会同时存在两个版本的生产流量，不再是蓝绿。多集群如果只放一个全局 Ingress，权重变化会把新 digest 送到这个 Ingress 后面的所有集群。所以跨区域发布要同时有精确名单和对应的流量策略。

Istio `VirtualService` 不参与上面的选择。它从同一份 release state 再渲染一次，不是第二份策略。Jenkins 和 GitHub Actions 都不另写权重。

## 3. 发布前要备齐的身份

计划必须同时带上 Artifact Contract v2 的五个字段：

| 字段 | CLI | 格式 |
| --- | --- | --- |
| `artifact_name` | `--artifact-name` | 非空。与归档制品名一致 |
| `bundle_sha256` | `--bundle-sha256` | 64 位小写十六进制。这是候选 |
| `source_sha` | `--source-sha` | 40 位小写提交 SHA |
| `source_run_id` | `--source-run-id` | 十进制数字，可信构建 run id |
| `release_tag` | `--release-tag` | 以 `artifact-v2-` 开头 |

另外两个 digest：

| 字段 | CLI | 含义 |
| --- | --- | --- |
| `environment_pointer_digest` | `--environment-pointer-digest` | 该环境当前 successful Deployment 的 `bundle_sha256` |
| `baseline_digest` | `--baseline-digest` | 现在正在接流量的 digest |

必须同时成立：

```text
environment_pointer_digest == bundle_sha256
baseline_digest != bundle_sha256
```

第一条说明候选已经晋级到这个环境。第二条说明确实有一个旧版本正在服务。候选已经是基线时，没有可发布的差异。引擎不因为灰度或蓝绿再构建一次。

`release.yml` 不让操作者自己填指针。它调用 `deployment_pointer.py current`，再用 `check-pointer` 核对。手填的 `bundle_sha256` 和指针不一致时 Job 失败。指针里的 `bundle_sha256` 也必须是 64 位小写十六进制；格式不对时先失败，不会把两份非法字符串的相等当成通过。指针的 `environment` 必须等于这次发布的环境。

本地核对指针：

```bash
python3 scripts/ci/deployment_pointer.py current \
  --repository <owner>/<repo> \
  --environment production > pointer.json

python3 scripts/ci/release_strategy.py check-pointer \
  --pointer-json pointer.json \
  --bundle-sha256 <candidate-64-hex> \
  --environment production
```

`deployment_pointer.py current` 需要环境变量 `GH_TOKEN`。成功时打印的 JSON 里至少有 `environment` 和 `bundle_sha256`。把这份 `bundle_sha256` 原样用作 `--bundle-sha256` 和 `--environment-pointer-digest`。

服务名 `--service` 是 DNS-1123 标签：小写字母、数字和连字符，不能以连字符开头或结尾。下文命令里的 `checkout` 是例子，不是一条已经在生产切流的服务。

## 4. 参考集群和区域顺序

| 集群 | 环境 | 区域 | 角色 | 流量能力 |
| --- | --- | --- | --- | --- |
| `dev-cn-east-a` | dev | cn-east | stable | gateway |
| `staging-cn-east-a` | staging | cn-east | stable | gateway |
| `prod-cn-east-canary` | production | cn-east | canary | gateway |
| `prod-cn-east-a` | production | cn-east | stable | gateway |
| `prod-cn-east-b` | production | cn-east | stable | gateway |
| `prod-cn-north-a` | production | cn-north | stable | gateway |
| `prod-edge-offline` | production | cn-edge | stable | none |

`region_order` 在两份多集群策略里都是 `cn-east` 然后 `cn-north`。策略校验拒绝只配置一个区域，也拒绝把后开区域改成另一种流量策略。

| 顺序 | 波次名 | 选择器 | `multi_cluster_canary` | `multi_cluster_blue_green` | 参考目录命中 |
| --- | --- | --- | --- | --- | --- |
| 1 | `cn-east` | environment=production, region=cn-east | canary | blue_green | `prod-cn-east-a`、`prod-cn-east-b`、`prod-cn-east-canary` |
| 2 | `cn-north` | environment=production, region=cn-north | canary | blue_green | `prod-cn-north-a` |

命中规则：

```text
选择器全部字段匹配
并且不在更早的波次里
并且不在 denylist
并且在 allowlist 中（allowlist 为空表示不额外限制）
并且 traffic=gateway
```

一个集群只属于最早命中的波次。同一区域内的集群共享这一步：灰度共享同一个 canary 权重，蓝绿共享同一个槽位、同一对 slot digest 和同一个预览标记。

`prod-cn-east-canary` 的 `role=canary` 不再单独构成一波。它和另外两个华东 gateway 集群共享 cn-east 这一步。

`prod-edge-offline` 的区域是 cn-edge，`traffic=none`，不在任何波次。dev 和 staging 也不在 production 的波次里。它们的服务 digest 保持基线，渲染结果里不允许出现这些 ID。

不把 `prod-edge-offline` 写进 `--allow` 时，它只是未选中。把它写进 `--allow`，计划直接失败，错误说明它没有 gateway，并且不会把它放进 `ClusterPin`。这是失败关闭：明确点名也不能绕过流量能力。句子是：

```text
explicitly targeted clusters lack gateway traffic and stay off the pin: prod-edge-offline
```

空波次直接失败。只 allow `prod-cn-north-a` 时，华东波次变成空波次，整次发布失败，不会跳过华东去发华北。deny 掉华东全部集群同样失败。allow 里的环境外集群，例如对 production 传入 `dev-cn-east-a`，也会失败。对 dev 使用这份 production 波次会失败，因为波次环境与发布环境不一致。

环境内策略的选择规则不同。它选出该环境里每一个 `traffic=gateway` 的集群，放进名为 `environment-traffic` 的唯一一波。production 上 `prod-edge-offline` 会进入 `excluded`。这份列表非空时，计划失败，除非显式传入 `--accept-excluded`。确认之后，这些集群保持基线 digest，并且不会出现在 HTTPRoute 里。dev 目前只有具备 gateway 的集群，所以 dev 灰度不需要这面旗标，也不会改 staging 或 production 的服务视图。

真实集群接入时，每个集群还要有标签 `cicd.platform/cluster-id=<集群 id>`。Placement 的 `matchExpressions` 用这个标签做 In 过滤。即使标签配错，控制器仍必须以 `ClusterPin.clusterIds` 和 `PlacementDecision` 的交集为准，多出来的集群直接拒绝。

## 5. 分析证据

`advance` 读取一个 JSON 对象。需要哪些字段由当前步骤决定。多出来的字段会被忽略。缺字段、类型不对或超过阈值时，这一次 advance 失败，状态文件不改写，权重和槽位都保持上一次成功的值。

通过样例，灰度的 `1pct` 和蓝绿的 `cutover` 都能用：

```json
{
  "smoke": "pass",
  "readiness": "pass",
  "error_rate": 0.0,
  "latency_p95_ms": 20,
  "requests": 80
}
```

阈值写在策略里：

```text
error_rate <= 0.01
latency_p95_ms <= 300
requests >= 50
```

等于阈值可以通过。大于阈值失败。

| 步骤要求 | 失败条件 |
| --- | --- |
| `smoke` | 值不是 `pass` |
| `readiness` | 值不是 `pass` |
| `error_rate` 或 `latency_p95` | `requests` 缺失、不是整数、或小于 50；对应指标缺失或超过阈值 |
| 无（仅蓝绿 `confirm`） | 不要求分析。JSON 对象 `{}` 可以通过 |

`requests` 必须是整数。布尔值在 Python 里是 `int` 的子类，引擎仍把它当成缺证据，不会当成通过。分析根节点如果不是 JSON 对象，也会失败。

失败时标准错误是 `ERROR:` 加下面之一：

```text
analysis evidence must be an object
analysis smoke must be pass
analysis readiness must be pass
analysis requests below min_requests
analysis error_rate is missing
analysis error_rate exceeds threshold
analysis latency_p95_ms is missing
analysis latency_p95 exceeds threshold
```

灰度每一步要准备的最小 JSON：

| 步骤 | 最小证据 |
| --- | --- |
| `1pct` | `smoke=pass`，以及 `error_rate`、`latency_p95_ms`、`requests` |
| `5pct`、`25pct`、`50pct` | `error_rate`、`latency_p95_ms`、`requests`。不看 smoke |
| `100pct` | `error_rate` 和 `requests`。不看延迟，也不看 smoke。通过后流量留在 canary 后端 |
| `confirm` | `error_rate` 和 `requests`。通过后才把候选写到 stable |

蓝绿每一步：

| 步骤 | 最小证据 |
| --- | --- |
| `deploy_inactive` | `{"readiness":"pass"}` |
| `preview` | `{"smoke":"pass"}` |
| `cutover` | `error_rate`、`latency_p95_ms`、`requests` |
| `confirm` | `{}` |

不能跳步。`advance` 每次只进入当前区域的下一步。灰度的 `100pct` 只把 canary 权重放到 100，状态保持 `in_progress`，不会打开下一区域。当前区域的 `confirm` 成功后，引擎在同一次 advance 里打开下一个未开始的区域。后开区域打开时不消耗分析。蓝绿的最后一步也叫 `confirm`，它不要求分析，行为见第 8 节。

`scenario` 子命令会自行构造一份全通过的证据，把状态机走到 `completed`，用来检查最终期望状态。它在 workflow 摘要里对应的句子是：

```text
synthetic analysis is not production evidence
```

生产推进只能使用 `advance`，并且证据来自真实流量或真实探活。GitHub Actions 的 `analysis` 输入就是这份 JSON 字符串。Job 把它写入 `analysis.json` 后交给 `release_strategy.py advance`，不会在 workflow 里填一份合成的通过结果。

## 6. 环境内灰度

策略名：`canary`。方法：`routing`。工具：`HTTPRoute`。

后端只有两个：

```text
<service>-stable   基线 digest，直到 confirm 才换成候选
<service>-canary   候选 digest，confirm 之后清空
```

策略文件里的权重：

| 步骤 | 读到的 canary 权重 | stable 权重 | 进入这一步前要通过的分析 |
| --- | --- | --- | --- |
| 打开波次，还没有 advance | 0 | 100 | 无。候选已经装到 canary 后端，但权重是 0 |
| `1pct` | 1 | 99 | smoke、error_rate、latency_p95 |
| `5pct` | 5 | 95 | error_rate、latency_p95 |
| `25pct` | 25 | 75 | error_rate、latency_p95 |
| `50pct` | 50 | 50 | error_rate、latency_p95 |
| `100pct` | 100 | 0 | error_rate。流量全在 canary 后端，stable digest 仍是基线，状态保持 `in_progress` |
| `confirm` | 0 | 100 | error_rate。候选写到 stable，canary digest 清空，这一波 `completed` |

`100pct` 成功之后，HTTPRoute 和 VirtualService 都是 canary 权重 100、stable 权重 0。候选 digest 仍在 canary 后端，stable digest 保持基线。发布和这一波的状态都是 `in_progress`。这时 `abort` 把流量收回基线，stable 权重回到 100，并清空 canary digest。

下一次 `advance` 的步骤名是 `confirm`，分析仍是 `error_rate`。通过之后，候选 digest 写到 stable 后端，canary 权重回到 0，canary digest 清空，这一波变为 `completed`。环境内灰度这时整个发布也是 `completed`。此后 `abort` 被拒绝，句子是 `completed release cannot be aborted; use environment rollback`。

权重梯子仍是 1、5、25、50、100。`confirm` 把已经承接全部流量的候选收成下一次发布的基线。这一步没有蓝绿槽位。

分析失败不改变权重。`100pct` 之前的 `abort`，以及停在 `100pct` 时的 `abort`，都把已开始的灰度收成 stable 权重 100、基线 digest。`confirm` 之后要走环境 rollback，见第 11 节。

production 上的环境内灰度必须看到排除列表。确认边缘集群保持基线后，cn-east 和 cn-north 的全部 gateway 集群使用同一步权重：

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

不传 `--accept-excluded` 时失败，句子里会带 `prod-edge-offline` 和 `excluded clusters require accept_excluded`。

dev 没有缺少流量能力的集群，所以不需要 `--accept-excluded`。这次会让 dev 里所有 gateway 集群使用同一步权重，没有区域顺序。把上面的 `--environment` 换成 `dev`，并去掉 `--accept-excluded`。

这是环境内灰度。要先开华东、让华北停在 0%，用第 7 节。

## 7. 多集群灰度

策略名：`multi_cluster_canary`。这是跨集群、跨区域灰度的做法。

```text
method = cluster_management
traffic_strategy = canary
工具 = PlacementDecision + ApplicationSet list generator + HTTPRoute
abort = shift_all_weight_to_baseline
```

权重仍是 1、5、25、50、100，每区域最后多一步 `confirm`。分析阈值和失败行为与第 5、6 节相同。分析失败不改变任何区域的权重。后开区域仍然按 canary 权重推进，不会改成蓝绿。

展开方式。下表是每一次成功 advance 之后读到的状态：

| 时刻 | cn-east 三个集群 | cn-north |
| --- | --- | --- |
| `plan` 刚完成，华东已打开 | canary 权重 0，候选已装上，stable 仍是基线 | 未打开。保持基线，不出现在渲染结果里 |
| 华东 `1pct` | 三个集群都是 1 / 99 | 仍未打开 |
| 华东 `5pct` | 三个集群都是 5 / 95 | 仍未打开 |
| 华东 `25pct` | 三个集群都是 25 / 75 | 仍未打开 |
| 华东 `50pct` | 三个集群都是 50 / 50 | 仍未打开 |
| 华东 `100pct` | canary 权重 100、stable 权重 0，stable digest 仍是基线，状态 `in_progress` | 仍未打开 |
| 华东 `confirm` | stable 收成候选，canary 权重回到 0，这一波 `completed` | 同一次 advance 才打开，canary 权重 0，策略仍是 canary |
| 华北 `1pct` | stable 保持候选 | 1 / 99 |
| 华北 `5pct` | stable 保持候选 | 5 / 95 |
| 华北 `25pct`、`50pct` | stable 保持候选 | 分别是 25 / 75、50 / 50 |
| 华北 `100pct` | stable 保持候选 | canary 权重 100，stable digest 仍是基线，发布仍是 `in_progress` |
| 华北 `confirm` | stable 保持候选 | stable 收成候选，发布 `completed` |

同一区域内的集群永远使用这一步的同一个 canary 权重。不同区域在展开过程中可以处于不同权重：华东已经 confirm、stable 是候选时，华北仍然可以停在 5%。华东停在 `100pct` 时华北不会打开。从 `plan` 到 `completed` 一共 12 次成功的 advance：华东 6 次，华北 6 次。

`abort` 把每一个已经打开的区域收成 stable 权重 100、基线 digest，并清空 canary digest。华东已经 confirm、华北停在 5% 时中止，两个区域都回到基线，不能留下“华东已经是新版本、华北失败了但华东继续接新流量”。华东还停在 `100pct`、华北未打开时中止，只把华东收回基线。未打开的区域本来就不在渲染结果里。整个发布 `completed` 之后不能 abort。

进行中的渲染只包含已经打开的波次。华东还在 25% 时，华北的 Placement、ApplicationSet、HTTPRoute 和 VirtualService 都不存在。同一个 `--out-dir` 再次渲染时，这次没有写出的清单会被删掉，所以目录里也不会留下上一次的预览文件或尚未打开的区域。

不需要 `--accept-excluded`。命令：

```bash
python3 scripts/ci/release_strategy.py validate

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

`plan` 成功后，`rendered/` 里应有华东的 pin、placement、decision、appset，以及三个集群各自的 `HTTPRoute` 和 `VirtualService`。文件名里不应出现 `prod-cn-north-a` 或 `prod-edge-offline`。

推进当前区域。把第 5 节里对应当前步骤的 JSON 写成 `analysis.json`：

```bash
python3 scripts/ci/release_strategy.py advance \
  --state release-state.json \
  --analysis analysis.json \
  --out release-state.json \
  --out-dir rendered
```

退出码 0 才表示权重变了。退出码 1 时原来的 `release-state.json` 保持不变。

## 8. 环境内蓝绿

策略名：`blue_green`。方法仍是 `routing`，切换方式是 `atomic`。它不是多集群灰度的后半段。

槽位固定为 `blue` 和 `green`。`--active-slot` 表示现在接流量的槽，默认 `blue`。另一个槽是 inactive。基线在 green 上时传 `--active-slot green`，cutover 会把生产切到 blue。

| 步骤 | 生产流量 | 预览 | 分析 |
| --- | --- | --- | --- |
| `plan` 刚完成 | 100% 仍在基线槽，inactive digest 为空 | 无预览文件 | 不消耗分析 |
| `deploy_inactive` | 100% 仍在基线槽 | 无 | readiness=pass。候选写入 inactive |
| `preview` | 100% 仍在基线槽 | header `x-release-preview: true` 打到 inactive | smoke=pass |
| `cutover` | 100% 切到候选槽 | 预览路由删除 | error_rate、latency_p95 |
| `confirm` | 保持候选槽 | 无 | 无。基线槽仍保留旧 digest，发布可以在这一步 `completed` |

预览是另一条 HTTPRoute，不修改生产路由的权重。生产路由在 cutover 之前权重始终是 100，后端是基线槽，规则里没有 header 匹配。同一集群的生产 VirtualService 也是权重 100，没有 header match。预览 VirtualService 只有这条 header match，不改变生产 route。

cutover 不做 50/50，也不出现 canary 后端。生产后端直接变成 `<service>-green` 或 `<service>-blue`，权重 100。

confirm 之后，原来的基线槽仍保留旧 digest，作为这次发布的回退窗口。发布状态变为 `completed`。此后 `abort` 会被拒绝。回退旧 digest 使用 `rollback.yml`，见第 11 节。

confirm 之前 abort，包括 cutover 之后、confirm 之前，会把 active 槽切回基线槽，并清掉 inactive 上的候选 digest。

和多集群灰度一样，同一环境里没有 gateway 的集群必须 `--accept-excluded` 才会被排除，排除后保持基线。参考目录的 production 会把 cn-east 和 cn-north 放进同一波，两地在同一步切槽。要先切华东、让华北保持基线且不出现在渲染结果里，用第 9 节。

production 环境内蓝绿：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy blue_green \
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
  --active-slot blue \
  --out release-state.json \
  --out-dir rendered
```

`plan` 的证据标记为 `unverified`。此时生产路由权重是 100，后端是基线槽，没有预览路由。`deploy_inactive` 才在 readiness 通过后把候选写入 inactive。

## 9. 跨集群、跨区域蓝绿

策略名：`multi_cluster_blue_green`。这是跨集群、跨区域蓝绿的做法，不是多集群灰度的后半段，也不是把权重从 0 调到 100。

```text
method = cluster_management
traffic_strategy = blue_green
工具 = PlacementDecision + ApplicationSet list generator + HTTPRoute
abort = restore_baseline_slot
region_order = cn-east 然后 cn-north
```

命中规则与第 4 节相同。cn-east 里三个 gateway 集群共用这一波的同一个槽位。cn-north 在打开之前不渲染。

步骤仍是蓝绿的四步，不是 1、5、25、50、100：

| 时刻 | cn-east 三个集群 | cn-north |
| --- | --- | --- |
| `plan`，华东已打开 | 生产 100% 在基线槽，inactive 还是空的，没有预览路由 | 未打开。保持基线，不出现在渲染结果里 |
| `deploy_inactive` | 候选写入 inactive，生产仍是基线槽，权重 100 | 仍未打开 |
| `preview` | 生产仍是基线槽，权重 100。header `x-release-preview: true` 打到 inactive | 仍未打开 |
| `cutover` | 生产一次切到候选槽，权重 100，预览路由删除 | 仍未打开 |
| `confirm` | 保持候选槽，基线槽仍保留旧 digest | 同一次 advance 才打开。生产 100% 在基线槽，inactive 为空 |
| 华北走完四步 | 保持候选槽 | `confirm` 后发布 `completed`，基线槽仍保留旧 digest |

预览 header 不改变生产权重。生产 HTTPRoute 在 cutover 之前后端一直是基线槽，权重 100。预览是另一条 HTTPRoute，预览 VirtualService 是另一份对象。cutover 不做 50/50，也不出现 canary 后端。ApplicationSet 元素的 `canaryWeight` 保持 `0`。

分析阈值和失败行为与第 5、8 节相同。分析失败不改变任何区域的槽位。缺证据、`requests` 不是整数（包括布尔值）或超过阈值，都不会切槽。

`abort` 把每一个已经打开的区域切回基线槽，并清掉 inactive 上的候选 digest。华东已经 confirm、华北还停在 preview 时中止，两个区域都回到基线，不能留下“华东已经是新版本、华北失败了但华东继续接新流量”。未打开的区域本来就不在渲染结果里。`completed` 之后不能 abort，要走环境 rollback。abort 不是 rollback：rollback 只在发布完成后恢复历史 digest。

进行中的渲染只包含已经打开的波次。华东还在 preview 时，华北的 Placement、ApplicationSet、HTTPRoute 和 VirtualService 都不存在。同一个 `--out-dir` 再次渲染时，cutover 会删掉上一次留下的预览文件。

从 `plan` 到 `completed` 一共 8 次成功的 advance：华东 4 次，华北 4 次。华东 `confirm` 那一次会同时打开华北。

不需要 `--accept-excluded`：

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster_blue_green \
  --environment production \
  --service checkout \
  --artifact-name checkout-generic-linux-x86_64-gcc \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag artifact-v2-<archive-tag-suffix> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --active-slot blue \
  --out release-state.json \
  --out-dir rendered
```

## 10. 渲染结果怎么读

每个已经打开的波次产出这些 JSON。文件名里的 `<wave>` 在多集群策略中是 `cn-east` 或 `cn-north`，在环境内策略中是 `environment-traffic`。

| 文件 | 对象 | 操作者要核对的内容 |
| --- | --- | --- |
| `<service>-<wave>-pin.json` | `cicd.platform/v1` `ClusterPin` | `spec.clusterIds` 是这一波的精确 ID。未打开的区域没有这份文件 |
| `<service>-<wave>-placement.json` | `Placement` | `cicd.platform/cluster-id In <这些 ID>`，`numberOfClusters` 等于名单长度 |
| `<service>-<wave>-decision.json` | `PlacementDecision` | `status.decisions[].clusterName` 与 ClusterPin 一致 |
| `<service>-<wave>-appset.json` | `ApplicationSet` | `spec.generators` 只有一个 `list`。没有 cluster selector generator |
| `<service>-<cluster>-route.json` | `HTTPRoute` | 只带这个集群。权重之和为 100 |
| `<service>-<cluster>-vs.json` | `VirtualService` | 与同一集群 HTTPRoute 的权重或槽位相同 |
| `<service>-<cluster>-preview.json` | `HTTPRoute` | 仅蓝绿 `preview` 步骤存在 |
| `<service>-<cluster>-preview-vs.json` | `VirtualService` | 仅蓝绿 `preview` 步骤存在 |

多集群灰度在 1% 时，同一个集群的两份生产路由应是：

```text
HTTPRoute backendRefs
  <service>-stable   weight 99
  <service>-canary   weight 1

VirtualService http[0].route
  host <service>-stable    weight 99
  host <service>-canary    weight 1
```

两边权重相同，并且和为 100。生产 VirtualService 没有 `match`。命名空间是 `apps`，Gateway 名是 `platform-gateway`，端口是 80。这些值来自 `ci/release-strategies.json`。

蓝绿生产路由只有一个后端，权重 100，名字是 `<service>-<activeSlot>`。预览路由和预览 VirtualService 的后端是 inactive 槽，权重 100，并且只匹配 header `x-release-preview` 等于 `true`。预览对象不修改生产 route。

多集群灰度进行中时，ApplicationSet 元素同时带着：

```text
stableDigest = 基线（本区域尚未 confirm）或候选（本区域 confirm 之后）
canaryDigest = 候选；confirm 后清空。100pct 时 canaryWeight 是 100，stableDigest 仍是基线
stableWeight / canaryWeight = 当前区域的这一步
```

同一波次里每个元素的 `canaryWeight` 相同。不能把单个 `image.digest` 写成候选后再靠路由“看起来像灰度”。那样 stable 后端也会变成新版本。

蓝绿元素带着 `blueDigest`、`greenDigest`、`activeSlot`。预览步骤额外带 `previewDigest`。多集群蓝绿在同一波次里这些字段完全相同，`canaryWeight` 保持 `0`。

HTTPRoute、VirtualService、PlacementDecision 和 ApplicationSet 里的集群 ID 必须等于这一波的 `ClusterPin.clusterIds`。未打开的区域、其他环境、`prod-edge-offline` 都不会出现。路由注解 `cicd.platform/cluster` 和标签 `cicd.platform/cluster-id` 是同一个集群，而且必须落在注解 `cicd.platform/clusters` 列出的 pin 里。名单外的集群不能出现在这两份清单里。

每份对象都有注解：

```text
cicd.platform/release-digest      候选 digest
cicd.platform/baseline-digest     基线 digest
cicd.platform/strategy            顶层策略名
cicd.platform/traffic-strategy    canary 或 blue_green
cicd.platform/region              多集群波次的区域；环境内波次为空字符串
cicd.platform/clusters            这一波的 cluster id，逗号分隔
cicd.platform/evidence            unverified、operator 或 synthetic
cicd.platform/cluster             仅路由对象。这一份清单属于哪个集群
cicd.platform/adapter             仅 VirtualService，值为 istio
```

`evidence` 的来源：

| 值 | 谁写的 | 能不能交给生产控制器 |
| --- | --- | --- |
| `unverified` | `plan` | 不能。候选可能已经装上，但还没有分析 |
| `operator` | `advance`、`abort`，或 `render --evidence-mode operator` | 可以交给仓库外的控制器。控制器仍要做第 14 节的检查 |
| `synthetic` | `scenario`，或 `render --evidence-mode synthetic` | 不能。synthetic analysis is not production evidence |

非法的 `evidence` 模式会被拒绝，不会写出文档。

Contour、NGINX Gateway、Envoy Gateway 仍然可以在集群里实现 HTTPRoute。本仓库没有为它们再写一份权重。Ingress 注解权重也不是事实来源。Karmada Placement、Rancher Fleet 属于和 OCM 同一类的工具；接入时必须消费同一份 `ClusterPin`，不能再维护一份自己的 selector。

不连集群，也可以在本机核对一次渲染。在 `rendered/` 目录执行：

```bash
python3 - <<'PY'
import json
from pathlib import Path

docs = [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in sorted(Path("rendered").glob("*.json"))]
kinds = {doc["kind"] for _, doc in docs}
missing = {"HTTPRoute", "VirtualService"} - kinds
if missing:
    raise SystemExit("missing " + ", ".join(sorted(missing)))
for name, doc in docs:
    if doc["kind"] not in ("HTTPRoute", "VirtualService"):
        continue
    ann = doc["metadata"]["annotations"]
    cluster = ann["cicd.platform/cluster"]
    pin = ann["cicd.platform/clusters"].split(",")
    if cluster not in pin:
        raise SystemExit(f"{name} names {cluster} outside the pin")
print("kinds", ", ".join(sorted(kinds)))
print("route files", sum(1 for _, doc in docs if doc["kind"] in ("HTTPRoute", "VirtualService")))
PY
```

这段检查只读本地 JSON。它不是 `kubectl apply`，也不证明集群里已经有这些对象。

## 11. 中止还是环境回滚

先看 `release-state.json` 里的 `status`。

| 状态 | 要收回流量或槽位时做什么 | 不要做什么 |
| --- | --- | --- |
| `planned` 或 `in_progress` | `abort`。已打开的每个区域回到基线。灰度是 stable 权重 100、基线 digest。蓝绿是基线槽，inactive 候选被清空 | 不要走 `rollback.yml` 来代替这次 abort。abort 改的是发布期望状态，不是环境指针 |
| `aborted` | 已经收过。再 abort 会失败，句子是 `release is already aborted` | 不要对这份状态 advance |
| `completed` | `.github/workflows/rollback.yml` | `abort` 会失败。句子是 `completed release cannot be aborted; use environment rollback` |

abort 命令：

```bash
python3 scripts/ci/release_strategy.py abort \
  --state release-state.json \
  --out release-state.json \
  --out-dir rendered
```

abort 成功后证据标记是 `operator`。渲染里仍然有已经打开过的区域，路由回到基线。从未打开的区域继续不出现。

`completed` 之后使用 `rollback.yml` 的 `workflow_dispatch`，而且必须在 `main` 上运行。输入只有两个：

| 输入 | 作用 |
| --- | --- |
| `target_environment` | `dev`、`staging` 或 `production` |
| `restore_deployment_id` | 要恢复的同环境历史 successful Deployment ID |

它重新验证那次历史制品，然后创建一条新的 rollback Deployment，指针回到那个旧 digest。旧 Deployment 记录不被修改。它不重新构建，不渲染 HTTPRoute 或 VirtualService，也不对集群执行 `kubectl apply`。因此指针恢复之后，集群里已经切过去的路由不会自动改回去。本仓库没有把 rollback 和集群切流接成一条自动流水线。

`release.yml` 的权限没有 `deployments: write`，所以发布 Job 自己不能移动环境指针，也不能代替 rollback。

## 12. 本地命令一览

在仓库根目录执行。先校验策略和参考目录：

```bash
python3 scripts/ci/release_strategy.py validate
```

成功时打印 `progressive release policy validated`。

`plan` 和 `scenario` 的参数相同：

| 参数 | 必填 | 作用 |
| --- | --- | --- |
| `--strategy` | 是 | `canary`、`blue_green`、`multi_cluster_canary`、`multi_cluster_blue_green` |
| `--environment` | 是 | `dev`、`staging`、`production` |
| `--service` | 是 | DNS-1123 服务名 |
| `--artifact-name` | 是 | 见第 3 节 |
| `--bundle-sha256` | 是 | 候选，64 位小写十六进制 |
| `--source-sha` | 是 | 40 位小写 SHA |
| `--source-run-id` | 是 | 数字 |
| `--release-tag` | 是 | `artifact-v2-` 开头 |
| `--baseline-digest` | 是 | 正在接流量的 digest |
| `--environment-pointer-digest` | 是 | 必须等于候选 |
| `--accept-excluded` | 否 | 环境内策略在 production 上需要。多集群策略不要靠它把 `traffic=none` 放进名单 |
| `--allow` | 否 | 逗号分隔的 cluster id。每个 id 都必须入选 |
| `--deny` | 否 | 逗号分隔的 cluster id |
| `--active-slot` | 否 | `blue` 或 `green`，默认 `blue` |
| `--out` | 是 | 写出的 `release-state.json` |
| `--out-dir` | `scenario` 必填，`plan` 可选 | 渲染目录 |

`scenario` 会把状态机跑完。不要把这份 `evidence=synthetic` 的渲染结果交给生产控制器。

`advance`：

```bash
python3 scripts/ci/release_strategy.py advance \
  --state release-state.json \
  --analysis analysis.json \
  --out release-state.json \
  --out-dir rendered
```

`render` 不推进状态，只按已有 JSON 再写清单。默认证据模式是 `operator`。检查一份 `plan` 结果时应显式传 `unverified`，避免把未分析的状态标成操作者已确认：

```bash
python3 scripts/ci/release_strategy.py render \
  --state release-state.json \
  --out-dir rendered \
  --evidence-mode unverified
```

`check-pointer` 见第 3 节。

常见失败句子：

| 标准错误里的句子 | 含义 | 状态文件 |
| --- | --- | --- |
| `candidate digest must match the environment pointer` | 两个 digest 不相等，或格式在比较前已经不合法 | 不写出 |
| `candidate digest is already the baseline serving digest` | 没有可发布的差异 | 不写出 |
| `bundle_sha256 must be 64 lowercase hexadecimal characters` | 候选不是 64 位小写十六进制 | 不写出 |
| `environment pointer digest does not match release candidate` | `check-pointer` 发现指针和候选不同 | 不写出 |
| `pointer environment does not match the release environment` | 指针属于另一个环境 | 不写出 |
| `pointer bundle_sha256 must be 64 lowercase hexadecimal characters` | 指针 digest 格式非法 | 不写出 |
| `excluded clusters require accept_excluded` | 环境内策略遇到不能接流量的集群 | 不写出 |
| `explicitly targeted clusters lack gateway traffic and stay off the pin` | `--allow` 点名了 `traffic=none` | 不写出，不会进入 ClusterPin |
| `empty wave` | allow 或 deny 把某一区域清空了 | 不写出 |
| `allowlist clusters were not selected` | allow 里的 id 没有入选 | 不写出 |
| `analysis ...` | 证据不够或超过阈值 | 不改写。权重或槽位不动 |
| `completed release cannot be aborted; use environment rollback` | 发布已经完成 | 不改写 |
| `release is already aborted` | 重复 abort | 不改写 |
| `aborted release cannot advance` | 对已中止的状态继续推进 | 不改写 |
| `completed release has no further steps` | 对已完成的状态继续推进 | 不改写 |

## 13. GitHub Actions

`.github/workflows/release.yml` 的名字是 Progressive Release。只在 `main` 上 `workflow_dispatch`。在别的分支上手动触发时，Job 的 `if: github.ref == 'refs/heads/main'` 不成立，不会执行策略。它不是第二份策略引擎。`plan`、`advance`、`abort`、`scenario` 都调用 `scripts/ci/release_strategy.py`。

权限是 `contents: read` 和 `deployments: read`。没有 `deployments: write`。Job 不读取 kubeconfig，也不执行 `kubectl apply`。并发组是 `release-<environment>-<service>`，`cancel-in-progress` 为 false：同名发布不会互相取消，后一次会等待。

每次运行的顺序是：

```text
validate
→ deployment_pointer.py current
→ check-pointer
→ plan、advance、abort 或 scenario
→ 从同一份 release state 渲染 Gateway API HTTPRoute 和 Istio VirtualService
→ rendered/ 里缺少 HTTPRoute 或 VirtualService 则 Job 失败
→ 上传 release-state.json 与 rendered/
```

最后一步还会检查：路由上的 `cicd.platform/cluster` 落在 `cicd.platform/clusters` 里。`advance` 和 `abort` 的渲染如果标成 `synthetic`，Job 失败。摘要里写明这次 Job 不把清单应用到真实集群。

`workflow_dispatch` 输入：

| 输入 | 类型 | 何时使用 |
| --- | --- | --- |
| `action` | choice：`plan`、`advance`、`abort`、`scenario` | 必填，默认 `plan` |
| `strategy` | choice：`canary`、`blue_green`、`multi_cluster_canary`、`multi_cluster_blue_green` | 必填，默认 `multi_cluster_canary` |
| `environment` | choice：`dev`、`staging`、`production` | 必填，默认 `production` |
| `service` | string | 必填 |
| `artifact_name` | string | 必填 |
| `bundle_sha256` | string | 必填，必须等于当前环境指针 |
| `source_sha` | string | 必填 |
| `source_run_id` | string | 必填 |
| `release_tag` | string | 必填 |
| `baseline_digest` | string | 必填 |
| `accept_excluded` | boolean | 必填，默认 `false` |
| `allow_clusters` | string | 可选，逗号分隔 |
| `deny_clusters` | string | 可选，逗号分隔 |
| `active_slot` | choice：`blue`、`green` | 必填，默认 `blue` |
| `analysis` | string | 仅 `advance`。当前步骤需要的 JSON 对象 |
| `state` | string | 仅 `advance` 和 `abort`。上一次 `release-state.json` 的全文 |

`plan` 和 `scenario` 用制品字段现场生成状态，不读 `state`。`check-pointer` 仍然先跑。

### 13.1 第一次：plan

在 GitHub 的 Actions 页面选择 Progressive Release，分支选 `main`，`action=plan`，填第 3 节的身份。`bundle_sha256` 用该环境当前指针。`baseline_digest` 用现在正在接流量的 digest。多集群策略保持 `accept_excluded=false`。环境内 production 策略把它设为 true。

跑完后下载 artifact `progressive-release-<run-id>`。里面有 `release-state.json` 和 `rendered/`。先按第 10 节核对：华东已打开，华北不在文件名里，同时存在 HTTPRoute 和 VirtualService，`cicd.platform/evidence` 是 `unverified`。这份渲染还没有分析，不要交给生产控制器。

### 13.2 之后每一次：advance

`workflow_dispatch` 没有文件输入，下一次运行也不会把上一次的 artifact 重新上传进来。操作者把上一次 artifact 里的 `release-state.json` 压成一行，贴进 `state`。在已经下载的目录里：

```bash
python3 -c 'import json,pathlib; print(json.dumps(json.loads(pathlib.Path("release-state.json").read_text(encoding="utf-8")), separators=(",", ":")))'
```

把这一行贴进 `state`。按第 5 节为当前步骤准备 `analysis`，也压成一行贴进去。其余身份字段保持和这次发布相同，尤其是 `bundle_sha256` 和 `environment`。Job 在调用引擎之前核对两件事：状态里的 `candidate_digest` 等于刚刚通过 `check-pointer` 的指针，状态里的 `environment` 等于本次 `environment`。对不上就失败，不会推进另一份发布。

参考目录上走完一步之后的状态大约 3KB，放得进这个字符串。不要把 `state` 提交进仓库。

`analysis` 原样写入 `analysis.json`。需要哪些字段仍由当前步骤决定。Workflow 不生成合成通过证据。成功后重新下载 artifact。证据标记应为 `operator`。按第 7 节或第 9 节的表核对权重或槽位，再决定下一次 advance 还是 abort。

### 13.3 中止

`action=abort`。`state` 同样贴上一份进行中的 `release-state.json`。`analysis` 留空。成功后已打开区域回到基线，证据标记是 `operator`。`completed` 的状态不要走这个动作。

### 13.4 scenario

`action=scenario` 使用合成证据走完步骤，并在摘要里打印：

```text
synthetic analysis is not production evidence
```

它用来渲染最终期望状态，供人对照第 7 节或第 9 节的最后一行。不要把 artifact 交给生产控制器。生产切流只消费 `advance` 或 `abort` 之后、证据标记为 `operator` 的渲染结果。

## 14. 外部控制器合同

控制器是仓库之外的组件。它要做的检查：

1. 读取 `ClusterPin.clusterIds`。
2. `PlacementDecision` 里每一个 `clusterName` 都必须在这份名单里，名单里的每一个 ID 也都必须出现。
3. 只把 ApplicationSet 的 list 元素发到同名集群。
4. HTTPRoute 和 VirtualService 只应用到 `cicd.platform/cluster` 注解指向的集群，并且该集群必须属于这份 pin。VirtualService 的权重从同一份渲染结果来，控制器不能再改一版。
5. 忽略一切不在已打开波次里的集群，不要用环境级 selector 再选一次。后开区域保持基线，直到它自己的波次出现在渲染结果里。
6. 看到 `evidence=synthetic` 或 `unverified` 时拒绝作用于生产。
7. 不在 GitHub Actions 或 Jenkins 里存放 kubeconfig。`release.yml` 只上传 `release-state.json` 和 `rendered/`，不执行 `kubectl apply`。`ops/Jenkinsfile` 是可选调用方，只调用同一条 CLI。

控制面测试通过，只说明期望状态符合上面的规则。它不表示生产集群已经切了流量。

## 15. Jenkins 是可选调用方

`ops/Jenkinsfile` 可选。它不是第二份策略，只调用同一条 CLI。它做两件事：

```text
检出参数 PLATFORM_SHA 指向的平台提交
按 COMMAND 调用 scripts/ci/release_strategy.py 的 validate、plan、advance 或 render
```

`abort` 和 `scenario` 不在 `COMMAND` 里。要中止，用第 11 节的 CLI，或 GitHub Actions 的 `action=abort`。Jenkinsfile 里不写 canary 权重，不写集群名单，不写 `kubectl apply`，也不保存 kubeconfig。权重、区域顺序和 ClusterPin 仍由检出的那次提交里的 Python 策略引擎决定。改 Jenkins 参数不会变成另一套灰度或蓝绿。

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `PLATFORM_SHA` | 空，必填 | 要检出的平台提交 SHA。空字符串会让流水线失败 |
| `COMMAND` | 无默认，选项为 `validate`、`plan`、`advance`、`render` | 传给 CLI 的子命令 |
| `STRATEGY` | 空 | `plan --strategy`，原样传递 |
| `ENVIRONMENT` | 空 | `plan --environment` |
| `SERVICE` | 空 | `plan --service` |
| `ARTIFACT_NAME` | 空 | `plan --artifact-name` |
| `BUNDLE_SHA256` | 空 | `plan --bundle-sha256` |
| `SOURCE_SHA` | 空 | `plan --source-sha` |
| `SOURCE_RUN_ID` | 空 | `plan --source-run-id` |
| `RELEASE_TAG` | 空 | `plan --release-tag` |
| `BASELINE_DIGEST` | 空 | `plan --baseline-digest` |
| `ENVIRONMENT_POINTER_DIGEST` | 空 | `plan --environment-pointer-digest`。必须等于候选 |
| `ACCEPT_EXCLUDED` | false | 为 true 时 `plan` 追加 `--accept-excluded` |
| `ALLOW` | 空 | `plan --allow` |
| `DENY` | 空 | `plan --deny` |
| `ACTIVE_SLOT` | 选项 `blue`、`green` | `plan --active-slot` |
| `STATE` | `release-state.json` | `advance` 和 `render` 读取的状态文件 |
| `ANALYSIS` | `analysis.json` | `advance` 读取的分析证据。调用方自己放入真实 JSON。Jenkins 不合成通过结果 |
| `OUT` | `release-state.json` | `plan` 和 `advance` 写出的状态文件 |
| `OUT_DIR` | `rendered` | 渲染目录 |
| `EVIDENCE_MODE` | `operator` | 只给 `render`。还可以是 `unverified` 或 `synthetic` |

`validate` 不使用制品参数，只校验检出的那次提交里的策略和参考目录。`advance` 不重新 plan。`render` 只把已有状态再写成 JSON，包括 HTTPRoute 和 VirtualService。

这条流水线跑完，只说明工作区里有期望状态。它不表示生产集群已经切了流量。

## 16. 已经验证的不变量

`tests/test_progressive_delivery.py` 锁定这些行为：

- 环境内灰度必须按 1、5、25、50、100 前进，然后 `confirm`，分析失败不改变状态；
- `100pct` 之后流量 100% 在 canary 后端，stable digest 仍是基线，状态保持 `in_progress`，这时 abort 回到基线；
- `confirm` 通过 `error_rate` 后才把候选写到 stable，canary 权重回到 0，并完成这一波；
- 环境内灰度把 cn-east 和 cn-north 放到同一步权重；
- 灰度 `confirm` 之后 stable 后端是候选 digest，canary 权重为 0，再 abort 会被拒绝；
- abort 把已开始的流量收回到基线；
- dev 灰度不改变 staging 和 production；
- 蓝绿预览时生产流量仍是基线，cutover 的生产权重是 100；
- cutover 之后 abort 切回基线槽；
- confirm 后旧槽仍保留基线 digest，且不能再 abort；
- `multi_cluster_canary` 的区域顺序是 cn-east 然后 cn-north，两波都是 canary；
- 华东三个 gateway 集群共享同一步权重，华北在打开前是 0% 且不出现在渲染结果里；
- 华东 `100pct` 时华北仍未打开；华东 `confirm` 后华北才打开，打开时权重为 0，下一步才是 1%，策略仍是 canary；
- 华东已是候选、华北停在 5% 时，两个区域权重不同；
- 分析失败不改变已打开区域的权重；
- abort 把每一个已打开区域回到基线，包括已经收口的华东；
- `prod-edge-offline`、dev、staging 保持基线，且不出现在渲染结果里；
- 把 `prod-edge-offline` 写进 allow 会因缺少 gateway 失败，不会进入 pin；
- 路由里的集群 ID 不会超出该波次的 ClusterPin；
- 同一个 `--out-dir` 再次渲染时，会删掉这次不再写出的清单，包括已经结束的预览路由和尚未打开区域的文件；
- 同一集群的 VirtualService 权重与 HTTPRoute 相同，且和为 100；
- 蓝绿生产 VirtualService 把 100% 流量送到当前槽，没有权重爬坡；
- 预览 VirtualService 只有 header `x-release-preview` 的 match，不改变生产 route；
- VirtualService 的 `cicd.platform/cluster` 落在 ClusterPin 内，名单外集群不会出现；
- ApplicationSet 只有 list generator；
- 候选 digest 必须等于环境指针，且不能已经是基线；
- `check-pointer` 拒绝非 SHA256 的指针 digest，也拒绝环境和 digest 不相等；
- 环境内蓝绿的 `plan` 会渲染基线槽，生产权重 100，没有预览路由；cn-east 和 cn-north 在同一步切槽；
- `multi_cluster_blue_green` 的区域顺序是 cn-east 然后 cn-north，两波都是 blue_green；
- 华东三个集群共用同一个槽位，华北在打开前保持基线且不出现在渲染结果里；
- 预览 header 不改变生产权重，cutover 是权重 100 的原子切换，不会出现 1、5、25、50、100；
- 华东 confirm 后华北才打开，打开时 inactive 仍为空，策略仍是 blue_green；
- 分析失败不改变已打开区域的槽位；`requests` 为布尔值时失败关闭；
- abort 把每一个已打开区域切回基线槽并清空 inactive，包括已经 confirm 的华东；
- `completed` 之后不能 abort，要走环境 rollback；
- 把 `prod-edge-offline` 写进多集群蓝绿的 allow 会因缺少 gateway 失败；
- `release.yml` 的 `action` 包含 `plan`、`advance`、`abort`、`scenario`；`advance` 把操作者传入的分析 JSON 交给引擎；渲染结果同时包含 HTTPRoute 和 VirtualService；workflow 里没有 kubeconfig，也不包含 `kubectl apply`。

平台校验 `validate.yml` 会执行 `release_strategy.py validate`。

## 17. 还没有做的事

真实集群 apply 需要集群里的 Gateway 或 Istio、带 `cicd.platform/cluster-id` 标签的 OCM ManagedCluster，以及消费这些 JSON 的控制器。这些资源不在本仓库。控制面测试通过，不等于生产集群已经完成灰度或蓝绿。

把 kubeconfig 放进 GitHub Actions 或 Jenkins，或者在 Job 里直接 `kubectl apply`，都违反第 14 节的合同。VirtualService 只是同一份状态的适配，不能在网格里再维护一套权重。
