# Argo CD / Argo Rollouts 渐进式交付接入规范

> 状态：第一阶段接入规范（observe-only，默认不执行集群变更）
>
> 本文定义如何把现有 CICD 发布控制面接入 Argo CD 与 Argo Rollouts。它不代表仓库已经连接真实 Kubernetes 集群，也不代表任何生产流量已经由 Rollouts 接管。

## 1. 目标与非目标

目标是复用现有 `scripts/ci/release_strategy.py` 的制品身份、环境晋级、区域顺序和发布状态规则，再由 Argo CD 同步声明式资源、由 Argo Rollouts 执行单个 Kubernetes 集群内的渐进式发布，并把真实 Rollout / AnalysisRun 状态反馈给发布控制面。

本阶段不做以下事情：

- 不新增第二套 Canary / Blue-Green 状态机。
- 不让 GitHub Actions、Jenkins 或本地脚本直接持有生产 kubeconfig。
- 不把 `scenario` 产生的 synthetic analysis 当成生产证据。
- 不自动启用生产集群写权限、自动 Promote 或自动回滚。
- 不宣称 Argo CD、Rollouts、Istio、Prometheus 已部署或已连通。

## 2. 目标架构与唯一职责

```text
业务仓库提交
    |
    v
CICD Build / Test / Scan
    |
    v
Artifact Contract v2 + SBOM + Attestation + immutable image digest
    |
    v
现有 Promotion / Release Strategy
    |  校验制品身份、环境指针、审批、区域/集群顺序
    |  不直接操作 Kubernetes
    v
GitOps desired state（版本化配置仓库）
    |
    v
Argo CD
    |  同步声明式对象；管理应用期望状态
    v
Kubernetes + Argo Rollouts
    |  创建 ReplicaSet、推进步骤、暂停/中止
    |  只在选定 traffic integration 下管理流量权重
    v
AnalysisRun
    |  Prometheus / 业务探测 / 可观测性证据
    v
只读状态适配器
    |  读取 Rollout、AnalysisRun、ReplicaSet、路由状态
    v
现有 Release Strategy 状态聚合与审计
```

### 2.1 组件职责

| 组件 | 唯一职责 | 明确禁止 |
| --- | --- | --- |
| `release_strategy.py` | 制品身份、环境晋级、发布策略、集群名单和区域顺序、发布意图 | 直接调用 Kubernetes API 或自己改真实流量 |
| GitOps 仓库 | 保存可审查、可回滚的声明式期望状态 | 保存明文 kubeconfig / Token |
| Argo CD | 将 Git 期望状态同步到目标集群 | 与 Rollouts 同时管理同一个动态流量字段 |
| Argo Rollouts | 管理 Rollout 生命周期、ReplicaSet、步骤与暂停/中止 | 决定跨区域晋级顺序或替代制品晋级校验 |
| AnalysisTemplate / AnalysisRun | 对指标与业务探测做成功、失败或不确定判断 | 把无样本、查询错误或监控不可用当成功 |
| 状态适配器 | 只读采集真实控制器状态并形成证据 | 直接 Promote、Abort 或修改路由 |
| 审批入口 | 对生产 Promote / Abort 等动作进行身份和权限校验 | 复用 CI 构建身份作为生产集群管理员 |

## 3. 最重要的流量所有权规则

现有发布引擎会渲染 Gateway API `HTTPRoute` 和 Istio `VirtualService`。Argo Rollouts 也能通过流量路由集成管理权重。两者不能在同一应用、同一集群、同一发布期间同时写同一条生产路由。

必须在每个应用环境配置中明确一个流量所有者：

- `cicd-renderer`：现有发布引擎负责渲染路由期望状态。Rollouts 不得再管理同一条路由的权重。
- `argo-rollouts`：Rollouts 负责该应用的动态流量权重。现有引擎只负责制品、发布意图、区域/集群顺序和状态聚合；不得再向该应用渲染竞争性的权重字段。
- `none`：只部署工作负载，不切生产流量，适用于本地演练或尚未接入流量治理的环境。

任何配置如果没有明确 `traffic_owner`，或同时声明两个写入者，必须失败关闭。不得通过“最后一次 apply 获胜”解决冲突。

### 3.1 Argo CD 字段所有权

对每个 Kubernetes 字段明确唯一写入者：

- Git 管理：Rollout spec、Pod template、镜像 digest、AnalysisTemplate、Service 静态端口/选择器、非动态路由规则。
- Rollouts 管理：Rollout status、ReplicaSet 状态、步骤推进状态，以及所选 traffic integration 明确交给 Rollouts 管理的动态权重。
- Argo CD：同步 Git 声明式对象与报告健康/同步状态；不得通过自动同步反复覆盖 Rollouts 动态管理字段。
- Kubernetes 控制器：status 子资源与系统生成字段。

生产启用前必须对照所选 Argo Rollouts 版本和 traffic router 的官方字段管理方式验证差异。不能仅凭关闭 Argo CD self-heal 就假设冲突已经解决。

## 4. 制品身份契约

Rollout Pod Template 必须使用不可变镜像 digest，例如：

```yaml
image: registry.example.invalid/team/service@sha256:<64-hex-digest>
```

禁止使用 `latest`、可变 tag 或在部署阶段重新构建镜像。

进入 Rollout 前，发布控制面至少应验证：

1. Artifact Contract v2 格式与成员 SHA256 正确。
2. bundle SHA256 与当前环境晋级指针一致。
3. 镜像 digest 与已验证制品清单中的 digest 一致。
4. source commit、CI run、attestation 和 SBOM 可追溯。
5. 目标环境、集群 ID、应用名与审批记录一致。
6. 生产发布必须具备明确的人工审批策略。

镜像 digest 是运行时部署身份；不能用 Git commit SHA、tag 或 bundle SHA256 冒充镜像 digest。

## 5. Analysis 判定规则

Analysis 的结果至少区分：

- `Successful`：所有必需指标查询成功、样本量达到门槛、指标满足阈值。
- `Failed`：有明确指标越界或业务探测失败。
- `Inconclusive`：样本不足、Prometheus 不可用、查询错误、返回空值、数据类型错误或证据窗口未完成。

生产策略必须失败关闭：

| 情况 | 处理 |
| --- | --- |
| 错误率超过阈值 | Failed，停止扩大流量 |
| P95/P99 超过阈值 | Failed，停止扩大流量 |
| 请求量不足 | Inconclusive，暂停等待或人工处理 |
| Prometheus 无法访问 | Inconclusive / Error，禁止 Promote |
| 查询返回空值或 NaN | Inconclusive，禁止 Promote |
| Pod 未 Ready / CrashLoopBackOff | Failed 或阻断发布 |
| AnalysisRun 超时 | 不得视为 Successful |
| 指标正常但业务合成探测失败 | Failed |

不得把“没有观察到错误”解释成“证明发布成功”。必须同时验证观测窗口、请求量和数据源健康。

## 6. 多集群与多区域

Argo Rollouts 的一次 Rollout 只负责一个目标集群内的应用发布。跨集群顺序仍由现有发布控制面管理：

1. 从 `ci/clusters.json` 和发布策略解析目标集群。
2. 固化本次发布的 ClusterPin，不允许发布中途动态扩大名单。
3. 只在当前允许的集群创建/更新 GitOps 发布意图。
4. 读取该集群 Rollout 与 AnalysisRun 的真实状态。
5. 只有 Rollout 完成且 Analysis 证据有效，才允许发布控制面确认该集群成功。
6. 区域内所有必需集群成功后，才允许打开下一区域。
7. 任一集群失败或状态未知时，停止推进后续区域，并保留部分成功记录。

不能把 Git commit 已合并、Argo CD 显示 Synced 或 Rollout 对象已创建当成发布成功。

## 7. 回滚语义

必须区分三个动作：

1. **停止推进**：暂停 Rollout，不再扩大流量。
2. **运行时回切**：由 Rollouts / traffic router 按已验证的回退流程将流量切回稳定版本。
3. **GitOps 期望状态回退**：把 Git 中的镜像 digest 恢复到历史已晋级版本，避免 Argo CD 后续同步再次部署失败版本。

运行时回切成功，不代表 Git 期望状态已恢复；Git 回退成功，也不代表真实流量已切回。两者都必须独立验证。

应用回滚不等于数据库回滚。数据库变更必须采用兼容性优先的 Expand / Migrate / Contract 策略；在新旧应用版本并存期间，Schema 和消息格式必须向后兼容。

## 8. 身份、权限与审计

建议拆分以下身份：

| 身份 | 权限边界 |
| --- | --- |
| CI Build | 构建、测试、扫描、推送制品；无集群写权限 |
| GitOps Writer | 只允许更新指定 GitOps 路径/分支；无集群管理员权限 |
| Argo CD Controller | 仅对授权 Project / Namespace / Resource 执行同步 |
| Rollouts Controller | 仅管理授权命名空间内的 Rollout 与配置的流量资源 |
| Read-only Observer | 读取 Rollout、AnalysisRun、ReplicaSet、路由与事件 |
| Release Approver | 通过受审计入口批准生产 Promote / Abort |
| Break-glass Operator | 限时、审批、强审计的紧急操作身份 |

不得把 GitHub Actions 默认令牌、Jenkins 凭据、Argo CD 管理员 Token 和 Kubernetes 管理员凭据混用。优先采用 OIDC / 短期凭据；禁止把 kubeconfig、Token 或密码写入仓库。

每次发布记录至少关联：应用、环境、集群、发布 ID、source commit、镜像 digest、bundle SHA256、Rollout 名称、AnalysisRun 名称、操作者、审批人、开始/结束时间、最终状态和回滚目标。

## 9. 推荐实施顺序

### 阶段 A：只读接入（本规范对应阶段）

- 固化 traffic ownership 配置。
- 增加配置 Schema 和静态校验。
- 增加只读 Rollout / AnalysisRun 状态采集接口。
- 将真实状态映射为发布证据，不执行 Promote / Abort。
- 用伪造 API 响应做单元测试，但明确标注为模拟测试。

### 阶段 B：本地 Kubernetes 演练

- 使用隔离的 Kind 集群。
- 安装固定版本的 Argo CD、Argo Rollouts、流量路由插件和 Prometheus。
- 部署演示应用与稳定/候选版本。
- 验证正常灰度、指标失败、样本不足、监控不可用、人工暂停和中止。
- 验证 GitOps 与 Rollouts 字段所有权，不允许双写。

### 阶段 C：测试集群

- 由集群管理员提供 OIDC、命名空间、路由和监控接入。
- 先只读观察，再启用非生产 Promote / Abort。
- 验证控制器重启、Argo CD 重新同步、路由漂移和回滚。
- 形成可追溯的真实集群验收记录。

### 阶段 D：生产启用

- 逐环境、逐集群启用，不一次性扩大权限。
- 强制审批、最小权限、固定集群名单和失败关闭。
- 先人工 Promote，再评估有限范围自动推进。
- 通过故障演练和回滚验收后，才允许扩大范围。

## 10. 验收清单

- [ ] 未配置 traffic owner 时校验失败。
- [ ] CICD renderer 与 Argo Rollouts 同时声明路由写权限时校验失败。
- [ ] Rollout 使用镜像 digest，而不是 tag。
- [ ] Analysis 无样本、空值、查询错误和超时均不能成功。
- [ ] Rollout Failed / Degraded 不得被聚合为发布成功。
- [ ] 目标集群名单在一次发布期间不可漂移。
- [ ] 前一集群未成功时，后续区域不能推进。
- [ ] 运行时回切和 GitOps digest 回退分别验证。
- [ ] CI 身份不能直接写生产集群。
- [ ] 所有生产 Promote / Abort 都能追溯到操作者和审批记录。
- [ ] 本地模拟测试与真实集群验收分别记录，不混淆。

## 11. 当前能力边界

当前 CICD 仓库已经具备发布策略状态机、制品晋级/回滚、集群名单约束，以及 Gateway API / Istio 路由期望状态渲染。以上是既有控制面能力。

本规范新增的是 Argo CD / Argo Rollouts 接入边界和实施顺序。只有在后续代码、测试、集群接入和验收证据完成后，才能把对应能力标记为“已实现”或“已真实验证”。
