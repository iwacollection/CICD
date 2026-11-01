# Argo Rollouts：让灰度自己跑完

`release_strategy.py` 是人推：每过一档，调用方把 `analysis.json` 喂给 `advance`。这一层是机器推：还是同一份 `ci/release-strategies.json`，渲染成 Argo Rollouts 和 AnalysisTemplate，由 AnalysisRun 自己去 Prometheus 取数。过了就进下一档，没过就自动退回稳定版，不用人盯。

它不是第二份策略。台阶 1、5、25、50、100，每一档查哪些项，阈值是多少，区域顺序，全部只读 `ci/release-strategies.json`。`ci/argo-rollouts.json` 只管两件事：每一项怎么量，每一项量多久。测试会检查这个文件里不出现任何阈值。

## 一条主线

每一步只回答一个问题，上一步的输出就是下一步的输入。

| 步骤 | 回答什么问题 | 谁来做 | 输出 |
| --- | --- | --- | --- |
| 1. plan | 发哪个版本、发到哪些集群 | `release_strategy.py plan` | `release-state.json` |
| 2. render | 这个策略在 Argo 里长什么样 | `argo_rollouts.py render` | Rollout、AnalysisTemplate、Service、HTTPRoute、ApplicationSet |
| 3. sync | 谁把它放进集群 | Git 加 Argo CD | 集群里的期望状态 |
| 4. 推进 | 谁一档一档往前走 | Argo Rollouts controller | setWeight，然后 AnalysisRun，再下一档 |
| 5. 兜底 | 出问题谁拦住 | 同一个 controller 和 ApplicationSet | 回到 stable，后面的区域不开 |

本仓库只做到第 2 步。第 3 步起都在集群侧，本仓库不执行 `kubectl apply`，也不持有 kubeconfig。

```
python3 scripts/ci/release_strategy.py plan --strategy multi_cluster_canary \
  --environment production --service checkout ... --out release-state.json

python3 scripts/ci/argo_rollouts.py render --state release-state.json \
  --image ghcr.io/<org>/checkout@sha256:<digest> \
  --gitops-repo-url <你们的 GitOps 仓库> --out-dir rendered-argo
```

`--image` 必须带 digest，用 tag 会直接报错。`render` 只接受刚 `plan` 出来的状态，已经 `advance` 或 `abort` 过的状态会被拒绝，避免人推和机器推混在一起。

渲染结果的放法：除 `*-appset.json` 以外的文件提交到 GitOps 仓库的 `rollouts/<service>/`，`*-appset.json` 放在 Argo CD 管理的位置。多次渲染用同一个 `--out-dir` 是安全的，只会清理上一次渲染写出的文件，目录里别的文件不动。

## 策略怎么映射

| 策略里的东西 | Argo 里的样子 |
| --- | --- |
| canary 每一档 | `setWeight` 后面紧跟一个 `analysis` 步骤，没判完不会走下一档 |
| canary 的 confirm | 最后一个 `analysis` 步骤，用更长的 `*-error-rate-confirm` 模板。它通过后 Rollouts 才把新版本升为 stable |
| canary 的 abort | AnalysisRun 失败，路由权重回 stable，canary 在 `abortScaleDownDelaySeconds` 后缩容 |
| blue_green 的 readiness | Rollouts 自带：新副本集全部 Available 才会往下走，不需要模板 |
| blue_green 的 preview 冒烟 | `prePromotionAnalysis`，对 preview Service 做 smoke |
| blue_green 的 cutover | `postPromotionAnalysis`，对 active Service 查 error_rate 和 latency_p95 |
| blue_green 的 abort | 分析失败后切回旧副本集。`scaleDownDelaySeconds` 必须比事后分析更长，校验会检查这一点 |
| multi_cluster_* 的区域顺序 | ApplicationSet 的 `RollingSync`，按 `region` 标签分组，上一组全部 Healthy 才开下一组 |

最后一行是区域门禁能成立的关键：Argo CD 对 Rollout 的内置健康检查，进行中是 Progressing，暂停是 Suspended，中止是 Degraded，晋升完成才是 Healthy。所以 cn-east 里只要有一个集群的灰度没跑完、被暂停或被中止，cn-north 就不会被打开。

Gateway API 插件只支持 canary，所以 blue_green 不靠权重，靠 `activeService` 和 `previewService` 两个 Service 切换。带 `x-release-preview: true` 的请求走 preview，生产流量始终只走 active。

## 分析出各种结果时会发生什么

| 情况 | AnalysisRun 结果 | Rollout 怎么办 |
| --- | --- | --- |
| 5xx 比例超过策略里的 `error_rate_max`，次数超过 `failure_limit` | Failed | 自动 abort，流量回 stable |
| p95 延迟超过 `latency_p95_max_ms` | Failed | 自动 abort |
| `/healthz` 返回的不是 ok | Failed（冒烟 `failure_limit` 是 0） | 自动 abort |
| 请求量不到 `min_requests`（流量太小） | request-volume 是 inconclusive，超过 `inconclusive_limit` 后整体 Inconclusive | 暂停，等人 `kubectl argo rollouts promote` 或 `abort`。Argo CD 显示 Suspended，下一个区域不会开 |
| Prometheus 连不上，连续 4 次取数报错 | Error | 按失败处理，自动 abort（宁可回退，不放行） |
| 完全没有 5xx 序列，或者零流量 | error-rate 不判失败，沉默不等于出错 | 是否放行交给 request-volume 判断 |

"量不够"故意做成暂停而不是失败也不是通过：夜里低峰发布，不应该因为没人访问就把好版本回滚，也不应该因为没数据就放行。

## 集群侧要先准备好

这些本仓库管不到，渲染出来的东西依赖它们：

1. **Argo Rollouts 1.5 以上。** 在 `argo-rollouts` 命名空间的 `argo-rollouts-config` ConfigMap 里登记 `argoproj-labs/gatewayAPI` 插件，建议写上 `sha256`。再给 controller 的 ServiceAccount 授予 HTTPRoute 的读写权限，否则它改不了权重。
2. **Argo CD 打开 progressive syncs。** 在 `argocd-cmd-params-cm` 里设 `applicationsetcontroller.enable.progressive.syncs: "true"`，它在 Argo CD 3.3 起是 Beta，2.x 里是 Alpha。`RollingSync` 会强制关掉生成的 Application 的自动同步，所以多集群的 ApplicationSet 模板里没有 `automated`，区域内由 ApplicationSet 触发同步。
3. **每个集群里有 Prometheus。** 地址写在 `metrics.prometheus_address`。默认的 PromQL 按 Istio 指标写（`istio_requests_total`、`destination_service`）。如果你们的网关是 Envoy Gateway 之类，三条查询都要改，而且都必须带 `{{args.host}}`，不带的话校验会不通过，避免查成全局数据。
4. **服务提供 `GET /healthz`，返回 `{"status":"ok"}`。** 冒烟和就绪探针都用它，路径和期望值在 `analysis.smoke` 与 `workload.readiness_path` 里改。
5. **Argo CD 里注册的集群名，和 `ci/clusters.json` 的 id 一致。**

## 为什么渲染的 Application 带了 ignoreDifferences

Rollouts 会在运行时改 HTTPRoute（或 VirtualService）的权重。Git 里写的是 100/0 基线，如果 Argo CD 把它当成漂移改回去，灰度会被一次次打断。所以 Application 模板里带了 `ignoreDifferences` 指向权重字段，并开了 `RespectIgnoreDifferences=true`。换 Istio 路由时会自动换成 VirtualService 的权重路径。

## 调参在哪

`ci/argo-rollouts.json`：

- `analysis.*`：每一项的间隔、次数、`failure_limit`、`initial_delay_seconds`。校验会拒绝 `failure_limit >= count`，那样这一项永远不会失败。
- `blue_green.scale_down_delay_seconds`：必须大于事后分析的时长再加 60 秒。
- `controller.traffic_router`：`gateway_api` 或 `istio`。
- `progressive_sync.max_update`：区域内同时同步多少个集群，默认全部一起。

默认参数下一个区域大约 38 分钟：5 档，每档 5.5 分钟，再加 10 分钟的 confirm。两个区域顺序跑，大约一个多小时。想更快，先降 `count` 或 `interval_seconds`，不要动阈值。

## 没做的，以及已知限制

- 没有渲染 Rollouts controller、插件和 RBAC 的安装，那是集群级的，只在上面写了前置条件。
- 没有接进 GitHub Actions 或 Jenkins。`render` 目前是命令行，可以由现有的发布流水线在 `plan` 之后调用。
- 工作负载只是最小骨架：镜像、端口、就绪探针、副本数。需要环境变量、资源限制、挂载的服务，在 GitOps 仓库里用 kustomize 覆盖这个 Rollout，或者以后改成 `workloadRef`。
- 镜像 digest 和制品包的 digest 是什么关系，这一层不校验，只把两者都写进 annotation（`cicd.platform/image`、`cicd.platform/release-digest`）。容器构建那一环要保证镜像确实是用这份制品构出来的。
- 只有每档的内联分析，没有贯穿全程的后台分析。
- 默认查询没在真实集群上跑过，上线前请在预发环境用一个故意报错的版本演练一次，确认 abort 和暂停都按上表发生。
