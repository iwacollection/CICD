# Enterprise CI Build Platform

企业级 **应用构建、嵌入式/多 SoC 固件、制品、供应链与发布治理平台**。

这个仓库不是“给一个项目写一条 GitHub Actions”，而是把多个项目、内部依赖、多工具链、多 SoC、不同 Runner、不可变制品、供应链证据、环境晋级和回滚放到同一套可验证规则里管理。

平台有两条一级业务主线：

```text
Enterprise CI Platform
│
├── 普通应用 / 多项目 CI
│   ├── Linux C/C++
│   ├── Hosted / Container Toolchain
│   └── Dependency DAG
│
└── 嵌入式 / 多 SoC 固件 CI
    ├── Rockchip / RK / 瑞芯微
    ├── Qualcomm / 高通
    ├── MediaTek / MTK / 联发科
    ├── Linux / Android BSP
    ├── Vendor SDK / License
    ├── Self-hosted Runner
    └── HIL 真机实验室
```

两条主线最终共用：

```text
Artifact Contract v2
→ Supply-chain Policy
→ Attestation
→ Archive
→ dev → staging → production
→ Rollback
```

> 当前 Hosted 主链已经完成真实生产生命周期验收：`main Build -> Artifact v2 -> Attestation -> Archive -> dev -> staging -> production -> rollback`。多 SoC 管理模型和硬件执行契约已经实现，但真实 RK / Qualcomm / MediaTek 主机、厂商 SDK、License Server 和板卡仍属于外部资源边界，不会用模拟结果冒充真机验收。

---

## 1. 这个平台解决什么问题

当 CI 从“一个仓库编译一下”扩大到企业场景，真正困难的是：

```text
哪些项目真的要构建？
内部库应该按什么顺序？
上游产物怎么可靠交给下游？
工具链/SDK 到底是哪一版？
缓存会不会把旧依赖带进新构建？
测试通过的 bytes 和生产 bytes 是不是同一份？
PR 能不能碰高权限 Self-hosted Runner？
RK / 高通 / 联发科三套 SDK、License、板卡怎么隔离？
制品能不能长期保存、追溯、验签、回滚？
CI 自己慢了、排队了、治理漂移了，谁知道？
```

这个仓库围绕这些问题建立统一平台，而不是围绕某一家厂商命令写死流水线。

---

## 2. 两条业务主线怎么汇合

### 2.1 普通应用 / 多项目

已经真实验证：

```text
hello-lib (L0)
   ↓ Artifact Contract v2
hello-cpp (L1)
   ↓
Supply Chain / SBOM
   ↓
Attestation
   ↓
Archive
   ↓
Promotion / Rollback
```

### 2.2 多 SoC / 固件

平台管理模型：

```text
Product Target
ci/projects.json
      ↓
Toolchain / SDK
ci/toolchains.json
      ↓
Hardware Profile
ci/hardware-profiles.json
      ↓
Rollout Policy
ci/hardware-rollout.json
      ↓
Runner / SDK Identity / License / HIL / Vendor Adapter
      ↓
Firmware Artifact Contract v2
      ↓
同一套 Supply Chain / Archive / Promotion / Rollback
```

详细主线：**[多 SoC / 固件 CI 管理](docs/multi-soc-and-firmware.md)**

---

## 3. 当前已经真实跑通的 Hosted 生命周期

```text
Pull Request
     ↓
Validate CI platform
     ├── catalog / DAG / policy / governance
     └── reproducibility gate
     ↓
Impact Analysis
     ↓
Dependency DAG
     ↓
Build / Test
     ↓
Vulnerability / License / Secret / Misconfiguration
     ↓
CycloneDX SBOM
     ↓
Artifact Contract v2
     ↓
GitHub Attestation
     ↓
Build gate
     ↓
Archive Trusted Artifacts
     ↓
GitHub Release + Cosign
     ↓
dev → staging → production
     ↓
rollback to historical production digest
```

完整 Run / Deployment / digest 证据：

**[生产生命周期真实验收记录](docs/production-verification.md)**

---

## 4. 能力状态

### 4.1 已真实验证

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Hosted C/C++ 构建 | ✅ | CMake + ccache + immutable toolchain image |
| Fast Lane / 影响分析 | ✅ | 只构建受影响项目并补齐 prerequisite |
| 真实依赖 DAG | ✅ | L0-L7，同层并行、跨层 barrier |
| 上游制品交接 | ✅ | Artifact v2 下载、校验、staging、下游消费 |
| 不可变 Toolchain | ✅ | image digest + Ubuntu Snapshot |
| Cache Identity | ✅ | project / target / toolchain / locks / upstream digest |
| Reproducibility Gate | ✅ | 两次 clean build 比较原始产物和 bundle bytes |
| Artifact Contract v2 | ✅ | manifest + member SHA256 + bundle SHA256 |
| Supply-chain Scan | ✅ | vuln / license / secret / misconfiguration |
| CycloneDX SBOM | ✅ | 随制品长期保留 |
| GitHub Attestation | ✅ | trusted `main` provenance |
| Cosign | ✅ | Archive 签名与 Promotion/Rollback 验签 |
| 长期制品归档 | ✅ | 当前使用 GitHub Releases |
| `dev -> staging -> production` | ✅ | exact artifact identity 强制晋级 |
| Production Rollback | ✅ | `A -> B -> A`，旧版本不重新构建 |
| 灰度 / 蓝绿 / 跨集群跨区域发布 | ✅ 控制面 | 测试锁定同一 digest 上的路由期望状态：权重、槽位、区域顺序和精确名单。不表示真实集群已经切流。操作步骤见第 9 节 |

### 4.2 平台已实现

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Repository Governance Drift | ✅ 已实现 | Ruleset 期望状态持续审计 |
| Platform Health / SLO | ✅ 已实现 | Success / Queue P95 / Duration P95 / Rerun Rate |
| 多 SoC Catalog 管理 | ✅ 已实现 | Project → Toolchain → Hardware Profile → Rollout |
| PR / Self-hosted 信任边界 | ✅ 已实现 | 不可信 PR 不进入厂商高权限 Runner |
| SDK Identity 契约 | ✅ 已实现 | `sdk-identity.json` + SHA256 pin |
| License Lease | ✅ 已实现 | Qualcomm/MTK 许可证池模型 |
| HIL Lease | ✅ 已实现 | 真机独占租约与释放语义 |
| Vendor Adapter | ✅ 已实现 | RK/Qcom/MTK build + HIL 适配层 |
| RK 物理接入准备 | ✅ Ready | x86_64 build host → arm64 target |
| 环境内灰度 | ✅ 控制面 | Gateway API HTTPRoute 权重，逐步 1→5→25→50→100，100% 留在 canary 后端直到 `confirm` 收到 stable；同一环境的 gateway 集群同步同一步；同一次渲染写出权重相同的 Istio VirtualService |
| 多集群灰度 | ✅ 控制面 | `multi_cluster_canary`：cn-east 然后 cn-north，区域内集群同权，后开区域要等前一区域 `confirm` 才打开，名单用 PlacementDecision + ApplicationSet 钉死 |
| 蓝绿发布 | ✅ 控制面 | HTTPRoute 原子切换，预览流量不进入生产权重；同一环境的 gateway 集群在同一步切槽；VirtualService 把 100% 生产流量送到当前槽 |
| 多集群蓝绿 | ✅ 控制面 | `multi_cluster_blue_green`：cn-east 然后 cn-north，区域内集群共用槽位，后开区域打开前不渲染，预览 header 不改变生产权重，切换不是权重爬坡 |
| Istio 数据面适配 | ✅ 控制面渲染 | 同一 release state 再渲染 VirtualService。灰度权重与 HTTPRoute 相同且和为 100。这不是第二份策略 |
| GitHub Actions 灰度 / 蓝绿 | ✅ 控制面 | `release.yml` 的 `workflow_dispatch`：`plan`、`advance`、`abort`、`scenario`。先核对环境指针，再调用同一 CLI，然后同时渲染 HTTPRoute 和 VirtualService。`advance` 读取操作者提供的分析 JSON，不用合成通过证据 |
| Jenkins 调用方 | ✅ 可选 | `ops/Jenkinsfile` 可选，检出固定平台 SHA 后只调用同一条 CLI；不写权重、不保存 kubeconfig、不执行 kubectl apply |

### 4.3 仍需真实外部资源

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| RK 真机闭环 | ⏸ | 缺真实主机、RK SDK/BSP、RK 板、USB/串口 |
| Qualcomm 真机闭环 | ⏸ | `planned`，缺真实 SDK/License/Runner/HIL |
| MediaTek/MTK 真机闭环 | ⏸ | `planned`，缺真实 SDK/License/Runner/HIL |
| 企业依赖代理 | ⏳ | Nexus/Artifactory 架构已定义，未实际部署 |
| 外部长期 Artifact Repository | ⏳ | 当前 GitHub Releases 已可用，规模化后再接 S3/MinIO/Nexus/Artifactory |
| 生产厂商签名 KMS/HSM | ⏳ | 需要真实企业签名基础设施 |
| 真实集群 apply | ⏸ | 控制面已产出钉死的名单、HTTPRoute 和 Istio VirtualService；下发需要外部 OCM / Argo / Gateway 或 Istio，仓库不把本地推演当成已上线 |

---

## 5. 多 SoC 到底怎么管理

SoC（System on Chip）是大类：

```text
SoC
├── Rockchip = 瑞芯微 = RK
├── Qualcomm = 高通
└── MediaTek = 联发科 = MTK
```

我们没有复制三套平台，而是：

```text
统一治理
├── PR / main
├── DAG
├── Artifact
├── Supply Chain
├── Archive
├── Promotion
└── Rollback

厂商隔离
├── SDK/BSP
├── Runner
├── Host requirements
├── License
├── HIL board
├── Flash mechanism
└── Product recipe
```

当前 Hardware Profile：

| SoC | Profile | Target | 当前 Runner | License | HIL | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| RK | `rk-linux-arm64-lab` | Linux arm64 | Linux x86_64 | 当前非必需 | 必需 | `planned` |
| Qualcomm | `qcom-android-arm64-lab` | Android arm64 | Catalog 当前为 Linux arm64 | 必需 | 必需 | `planned` |
| MediaTek | `mtk-android-arm64-lab` | Android arm64 | Catalog 当前为 Linux arm64 | 必需 | 必需 | `planned` |

RK 已明确：

```text
Build Host = Linux x86_64
      ↓ cross compile
Target     = Linux arm64 firmware
```

Qualcomm / MTK 的 Host 架构仍要在拿到真实 SDK 后按厂商支持矩阵验证，不能把 target arch 当作 host arch。

详见：

- **[多 SoC / 固件 CI 管理主线](docs/multi-soc-and-firmware.md)**
- **[Hardware Runner / SDK / License / HIL](docs/hardware-runner-integration.md)**
- **[RK 真实物理接入手册](docs/rk-physical-bringup.md)**

---

## 6. 最重要的设计原则

### 6.1 Build once

```text
commit
  ↓
Build once
  ↓
artifact digest A
  ↓
dev
  ↓
staging
  ↓
production
```

测试与生产不能重新构建出另一份 bytes。

### 6.2 Cache 只负责加速

Cache 可以删除、miss、失效，但不能成为：

- 唯一依赖来源；
- 上游项目交付方式；
- 长期制品仓库；
- 生产发布依据。

### 6.3 DAG 必须真正执行

```text
hello-lib
   ↓ verified Artifact v2
hello-cpp
```

下游只接受本次 Run 中刚构建、刚校验的上游 digest。

### 6.4 Toolchain / SDK 必须有身份

普通 Container Toolchain 使用完整 `@sha256:` digest；厂商 Host SDK 使用 `sdk-identity.json` 和不可变 `host_identity`。

### 6.5 PR 和高权限 Runner 必须隔离

PR 不能访问 Vendor SDK、License、USB、HIL、内网等高权限能力，只允许 Hosted-safe `pr_validation_command`。

### 6.6 Rollback 不是 checkout 老源码再 build

Rollback 恢复的是同环境历史成功的 immutable digest。

---

## 7. 仓库结构

```text
CICD/
├── .github/workflows/
│   ├── validate.yml                       # 平台自检 / reproducibility
│   ├── ci.yml                             # 主 DAG
│   ├── dag-node.yml                       # 单 DAG Node
│   ├── toolchain-images.yml               # Toolchain Supply Chain
│   ├── archive-artifacts.yml              # 长期归档
│   ├── promote.yml                        # dev/staging/production
│   ├── release.yml                        # 指针核对后 plan / advance / abort / scenario，同时渲染 HTTPRoute 与 VirtualService
│   ├── rollback.yml                       # 历史 digest rollback
│   ├── platform-health.yml                # Platform SLO
│   ├── repository-governance.yml          # Ruleset drift
│   ├── reusable-build.yml                 # 通用业务仓库入口
│   ├── reusable-rk-build.yml              # RK 产品构建入口
│   ├── reusable-rk-enrollment.yml         # RK SDK 入籍
│   └── reusable-rk-physical-readiness.yml # RK 物理 readiness
│
├── ci/
│   ├── projects.json                      # 项目 / target
│   ├── toolchains.json                    # Toolchain / SDK Registry
│   ├── hardware-profiles.json             # Runner/SDK/License/HIL Profile
│   ├── hardware-rollout.json              # SoC rollout policy
│   ├── supply-chain-policy.json
│   ├── promotion-policy.json
│   ├── release-strategies.json            # 环境内灰度 / 蓝绿 / 跨区域灰度 / 跨区域蓝绿
│   ├── clusters.json                      # 集群目录与流量能力
│   ├── platform-slo.json
│   └── repository-governance-policy.json
│
├── scripts/ci/                             # 平台规则实现
├── scripts/vendor/                         # RK/Qcom/MTK 稳定 Adapter
├── docker/toolchains/                      # 不可变 Toolchain Image
├── ops/Jenkinsfile                         # 可选调用方，只调用 release_strategy.py
├── ops/rk-runner/                          # RK 物理接入准备
├── examples/                               # Hosted DAG 示例
├── tests/                                  # 契约 / 安全边界回归
└── docs/                                   # 平台文档
```

完整导航：**[docs/README.md](docs/README.md)**

---

## 8. 新项目怎么接入

### 普通项目

业务仓库固定中央平台完整 Commit SHA 调用：

```text
.github/workflows/reusable-build.yml
```

业务负责源码与 build/test recipe，中央平台负责 Runner、Toolchain、Artifact、安全与发布规则。

### RK 产品

真实 RK 产品优先通过专用受信入口接入：

```text
reusable-rk-enrollment.yml
reusable-rk-physical-readiness.yml
reusable-rk-build.yml
```

真实 Self-hosted Runner 注册到私有产品仓库/受控 Runner Group，而不是公开 CICD 仓库。

详细步骤：

- [新项目接入手册](docs/onboarding.md)
- [业务仓库调用中央 CI](docs/reusable-workflow.md)
- [RK 真实物理接入](docs/rk-physical-bringup.md)

---

## 9. 发布和回滚

```text
main Build
   ↓
Trusted Attestation
   ↓
Long-term Archive
   ↓
dev
   ↓
staging
   ↓
production
```

Promotion 重新验证：

- trusted Build；
- Artifact Contract v2；
- bundle SHA256；
- Release identity；
- Supply-chain Policy；
- GitHub Attestation；
- Cosign；
- 前置环境 successful Deployment。

Rollback 只接受同环境历史 Deployment ID，并创建新的 rollback pointer，不重新构建旧版本。

详见：**[制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)**

环境指针只表示“这个 digest 可以出现在该环境”。指针本身不决定流量百分比，也不决定哪些集群收到新版本。这两件事由发布策略控制面完成，而且仍然使用同一份已晋级制品，不重新构建。下面是操作所需的规则。逐步命令、每一步要贴的分析 JSON、渲染文件名和失败句子在 **[灰度、蓝绿与跨集群跨区域发布操作手册](docs/progressive-delivery.md)**。按那份手册执行，不必先读 `scripts/ci/release_strategy.py`。

仓库产出的是期望状态 JSON。它不保存 kubeconfig，不执行 `kubectl apply`，也不把这次渲染写成“生产集群已经切流”。`ci/clusters.json` 是参考目录，用来校验选择规则，不代表这些集群已经接入。

### 9.1 先选方法

| 你要做的事 | `--strategy` | 方法 | 流量怎么变 |
| --- | --- | --- | --- |
| 同一环境里所有 `traffic=gateway` 的集群用同一步权重 | `canary` | 路由 | Gateway API `HTTPRoute` 权重 1、5、25、50、100。参考目录的 production 会让 cn-east 和 cn-north 一起变 |
| 同一环境里所有 gateway 集群各占一个槽，验证后一次切完 | `blue_green` | 路由，而且必须原子切换 | 生产权重始终 100。预览 header `x-release-preview: true` 不改变生产权重。cn-east 和 cn-north 同一步切槽 |
| 多个区域按同一套灰度权重展开，后开区域先保持 0% | `multi_cluster_canary` | 先钉死集群名单，再只改名单内的权重 | 参考目录先打开 cn-east，cn-east `confirm` 之后才打开 cn-north。区域内集群同权。后开区域打开前不出现在渲染结果里，打开后仍然按 1、5、25、50、100，不会改成蓝绿 |
| 多个区域共用槽位并一次切完，后开区域先保持基线 | `multi_cluster_blue_green` | 先钉死集群名单，再只在名单内做原子切换 | 同一区域顺序。不是权重爬坡。预览 header 不改变生产权重 |

只给 `role=canary` 的 `prod-cn-east-canary` 做权重，再把后面的区域改成蓝绿，得到的不是跨区域灰度。环境内 `canary` 也做不到“当前区域加权重，后开区域保持 0%”。

名单和路由是两件事。OCM `PlacementDecision` 加上 Argo CD `ApplicationSet` list generator 钉死 cluster id。HTTPRoute 和 Istio `VirtualService` 只能写出这份名单里的集群。不使用 ApplicationSet 的 cluster selector generator。

### 9.2 权重、槽位、分析、中止

灰度步骤名是 `1pct`、`5pct`、`25pct`、`50pct`、`100pct`、`confirm`。不能跳步。分析失败时进程退出，`release-state.json` 不改写，权重不动。`100pct` 成功之后，流量 100% 在 canary 后端，stable 权重是 0，stable digest 仍是基线，状态保持 `in_progress`。这时 abort 回到基线并清空 canary digest。下一步 `confirm` 通过 `error_rate` 之后，候选 digest 才写到 stable 后端，canary 权重回到 0，这一波 `completed`。`confirm` 之后 abort 被拒绝。`multi_cluster_canary` 里 cn-north 要等 cn-east 的 `confirm`，不是只等到 100% canary 权重。

`abort` 把每一个已经打开的区域收回基线：灰度是 stable 权重 100、基线 digest；蓝绿是切回基线槽，并清掉 inactive 上的候选。未打开的区域本来就不在渲染结果里。

蓝绿步骤是 `deploy_inactive`、`preview`、`cutover`、`confirm`。`--active-slot` 是现在接流量的槽，默认 `blue`，另一个槽是 inactive。`preview` 是另一条路由，只匹配 header `x-release-preview: true`。生产 HTTPRoute 和生产 VirtualService 在 cutover 之前后端仍是基线槽，权重 100，没有这条 header。cutover 把生产后端一次换成候选槽，权重仍是 100，不做 50/50，也不出现 1、5、25、50、100。`confirm` 保持候选槽，原来的基线槽仍保留旧 digest。`confirm` 之前 abort 会回到基线槽。

`status=completed` 之后 `abort` 被拒绝，句子是 `completed release cannot be aborted; use environment rollback`。这时用 `.github/workflows/rollback.yml`：输入 `target_environment` 和 `restore_deployment_id`，只恢复同环境历史 Deployment 指针，不重新构建。`rollback.yml` 不渲染 HTTPRoute，也不对集群执行 `kubectl apply`。发布引擎的 abort 和这次环境 rollback 不是同一个动作。

分析 JSON 由当前步骤决定要哪些字段。阈值是 `error_rate` 不超过 0.01、`latency_p95_ms` 不超过 300、`requests` 至少 50。`requests` 必须是整数，布尔值不算通过。`advance` 读操作者提供的 `analysis.json`。`scenario` 会自己造一份全通过的证据把状态机走到 `completed`，只用来检查最终 JSON。**synthetic analysis is not production evidence**。

`prod-edge-offline` 的 `traffic=none`。多集群策略默认不选它。用 `--allow` 点名它时计划失败关闭，不会把它写进 `ClusterPin`。环境内策略在 production 上必须看到排除列表，不传 `--accept-excluded` 就失败；确认之后它保持基线，并且不出现在路由里。

候选 `bundle_sha256` 必须等于该环境当前指针，并且是 64 位小写十六进制。不相等时 CLI 拒绝计划，句子是 `candidate digest must match the environment pointer`。`baseline_digest` 必须是另一份正在接流量的 digest。发布不重新构建。

### 9.3 同一份状态上的 HTTPRoute 和 VirtualService

同一次渲染写出 Gateway API `HTTPRoute` 和 Istio `VirtualService`。VirtualService 是数据面适配，不是第二份策略。灰度两边的权重相同，并且和为 100。蓝绿生产流量 100% 在当前槽。预览 VirtualService 只有 header `x-release-preview` 的 match，不修改生产 route。每份路由的注解 `cicd.platform/cluster` 必须落在这一波 `ClusterPin.clusterIds` 里。

本地先核对策略，再生成多集群灰度的期望状态：

```bash
python3 scripts/ci/release_strategy.py validate

python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster_canary \
  --environment production \
  --service checkout \
  --artifact-name <artifact-name> \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag artifact-v2-<suffix> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --out release-state.json \
  --out-dir rendered
```

把 `--strategy` 换成 `multi_cluster_blue_green` 就是跨区域蓝绿，并加上 `--active-slot blue` 或 `green`。推进、中止和只重渲染分别是：

```bash
python3 scripts/ci/release_strategy.py advance \
  --state release-state.json \
  --analysis analysis.json \
  --out release-state.json \
  --out-dir rendered

python3 scripts/ci/release_strategy.py abort \
  --state release-state.json \
  --out release-state.json \
  --out-dir rendered
```

`service=checkout` 只是命令里的 DNS-1123 例子，不是一条已经在生产切流的服务。

### 9.4 GitHub Actions 输入

`.github/workflows/release.yml` 只在 `main` 上 `workflow_dispatch`。选别的分支时 Job 条件不成立。它不是第二份策略引擎。顺序是：

```text
validate
→ deployment_pointer.py current
→ check-pointer
→ plan、advance、abort 或 scenario
→ 同一份 release state 渲染 Gateway API HTTPRoute 和 Istio VirtualService
→ 缺任何一种 kind 则 Job 失败
→ 上传 release-state.json 与 rendered/
```

权限是 `contents: read` 和 `deployments: read`。没有 `deployments: write`，所以这次运行不能移动环境指针，也不能代替 rollback。Job 不读取 kubeconfig，也不执行 `kubectl apply`。并发组是 `release-<environment>-<service>`，不会取消已经开始的同名运行。

| 输入 | 类型 | 作用 |
| --- | --- | --- |
| `action` | `plan`、`advance`、`abort`、`scenario` | 调用 `scripts/ci/release_strategy.py` 的同名子命令。默认 `plan` |
| `strategy` | `canary`、`blue_green`、`multi_cluster_canary`、`multi_cluster_blue_green` | 默认 `multi_cluster_canary` |
| `environment` | `dev`、`staging`、`production` | 默认 `production` |
| `service` | string | DNS-1123 服务名 |
| `artifact_name` | string | Artifact Contract v2 的 artifact name |
| `bundle_sha256` | string | 必须等于 `deployment_pointer.py current` 读到的指针 |
| `source_sha` | string | 40 位小写源提交 |
| `source_run_id` | string | 数字形式的可信构建 run id |
| `release_tag` | string | 以 `artifact-v2-` 开头 |
| `baseline_digest` | string | 当前正在接流量的 digest，不能等于候选 |
| `accept_excluded` | boolean | 默认 `false`。环境内策略在 production 上要确认为 `true` |
| `allow_clusters` | string | 可选，逗号分隔。每个 id 都必须入选。点名 `traffic=none` 会失败关闭 |
| `deny_clusters` | string | 可选，逗号分隔，从名单里去掉。把某一区域全部去掉会因空波次失败 |
| `active_slot` | `blue`、`green` | 蓝绿基线槽，默认 `blue`。灰度会收下这个值，但不按槽位切流 |
| `analysis` | string | 仅 `advance`。JSON 对象，字段是 `smoke`、`readiness`、`error_rate`、`latency_p95_ms`、`requests` |
| `state` | string | 仅 `advance` 和 `abort`。上一次 `release-state.json` 压成一行后的全文 |

`plan` 和 `scenario` 不读 `state`。`check-pointer` 仍先跑。下一次 `workflow_dispatch` 不能把上一次的 artifact 重新上传进来。操作者从 artifact 取出 `release-state.json`，压成一行贴进 `state`。Job 把它写到工作区再校验：`candidate_digest` 必须等于刚刚核对过的指针，`environment` 必须等于本次环境。

`advance` 把 `analysis` 原样写入 `analysis.json`。Workflow 不填 `"smoke": "pass"` 这类合成通过结果。`scenario` 才会在摘要里打印 `synthetic analysis is not production evidence`。`plan` 的证据注解是 `unverified`，`advance` 和 `abort` 是 `operator`，`scenario` 是 `synthetic`。交给集群的只应是 `operator`。

### 9.5 Jenkins 参数

`ops/Jenkinsfile` 可选，不是第二份策略。它检出 `PLATFORM_SHA` 指向的平台提交，然后只调用 `scripts/ci/release_strategy.py` 的 `validate`、`plan`、`advance` 或 `render`。文件里没有 canary 权重，没有集群名单，没有 kubeconfig，也不执行 `kubectl apply`。`abort` 和 `scenario` 不在 Jenkins 的 `COMMAND` 里；中止用上面的 CLI 或 Actions 的 `action=abort`。

| 参数 | 传给 CLI |
| --- | --- |
| `PLATFORM_SHA` | 要检出的平台提交，必填 |
| `COMMAND` | `validate`、`plan`、`advance`、`render` |
| `STRATEGY` | `plan --strategy` |
| `ENVIRONMENT` | `plan --environment` |
| `SERVICE` | `plan --service` |
| `ARTIFACT_NAME` | `plan --artifact-name` |
| `BUNDLE_SHA256` | `plan --bundle-sha256` |
| `SOURCE_SHA` | `plan --source-sha` |
| `SOURCE_RUN_ID` | `plan --source-run-id` |
| `RELEASE_TAG` | `plan --release-tag` |
| `BASELINE_DIGEST` | `plan --baseline-digest` |
| `ENVIRONMENT_POINTER_DIGEST` | `plan --environment-pointer-digest`，必须等于候选 |
| `ACCEPT_EXCLUDED` | 为 true 时追加 `--accept-excluded` |
| `ALLOW` / `DENY` | `plan --allow` / `--deny`，原样传递 |
| `ACTIVE_SLOT` | `plan --active-slot`，`blue` 或 `green` |
| `STATE` | `advance` 和 `render` 读取的状态文件，默认 `release-state.json` |
| `ANALYSIS` | `advance` 读取的分析证据，默认 `analysis.json`。Jenkins 不合成通过结果 |
| `OUT` | `plan` 和 `advance` 写出的状态文件 |
| `OUT_DIR` | 渲染目录，默认 `rendered` |
| `EVIDENCE_MODE` | `render --evidence-mode`，默认 `operator`。取值还有 `unverified`、`synthetic` |

流水线跑完，只说明工作区里有期望状态。

### 9.6 本仓库不声称的事

- 不声称参考目录里的集群已经接入，或某次本地 `plan` 已经在生产切了流量。
- 不在 GitHub Actions 或 Jenkins 里存放 kubeconfig，也不执行 `kubectl apply`。
- 不因为灰度或蓝绿重新构建制品。候选 digest 对不上当前环境指针就失败。
- 不把 `scenario` 的合成分析当成生产证据。
- 不把 VirtualService 做成第二套权重。网格如果另写一份权重，就不再是这次渲染。
- `completed` 之后的环境回滚由 `rollback.yml` 改指针。它不代替进行中的 abort，也不会自动改写集群路由。

---

## 10. `main` 治理

当前是单维护者治理模型，但仍保持：

```text
必须 Pull Request
Required approvals = 0
Code Owner mandatory approval = false
Review threads 必须解决
禁止 force-push
禁止删除 main
无 bypass actor
```

Required Checks：

```text
Validate CI platform
Build gate
Toolchain gate
```

详见：**[仓库治理基线与漂移审计](docs/repository-governance.md)**

---

## 11. 平台自己怎么运维

当前 SLO：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

并持续审计 Ruleset / Required Checks / force-push / deletion protection。

- [CI 平台维护手册](docs/platform-maintenance.md)
- [平台健康度与 SLO](docs/platform-health-slo.md)
- [故障排查手册](docs/troubleshooting.md)

---

## 12. 推荐阅读顺序

### 普通应用 / Hosted 主线

1. [总体架构](docs/architecture.md)
2. [真实依赖 DAG](docs/dependency-dag-execution.md)
3. [构建、缓存与依赖](docs/build-cache-and-dependencies.md)
4. [Artifact Contract v2](docs/artifact-contract-v2.md)
5. [供应链策略](docs/supply-chain-policy.md)
6. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
7. [灰度、蓝绿与跨集群跨区域发布操作手册](docs/progressive-delivery.md)
8. [生产生命周期真实验收记录](docs/production-verification.md)

### RK / 高通 / 联发科主线

1. [多 SoC / 固件 CI 管理主线](docs/multi-soc-and-firmware.md)
2. [Hardware Runner / SDK / License / HIL](docs/hardware-runner-integration.md)
3. [RK 真实物理接入手册](docs/rk-physical-bringup.md)
4. [Runner 与供应链安全](docs/runner-security-and-supply-chain.md)
5. [Artifact Contract v2](docs/artifact-contract-v2.md)
6. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
7. [灰度、蓝绿与跨集群跨区域发布操作手册](docs/progressive-delivery.md)

---

## 13. 当前阶段

核心平台已进入 **稳定 / 文档 / 真实消费者接入阶段**。

当前优先级：

```text
文档与代码保持一致
安全与依赖升级
Platform SLO
生命周期故障演练
真实业务仓库消费 reusable workflow
有硬件后恢复 RK physical bring-up
拿到真实 Qualcomm/MTK SDK 后再验证 Host/License/HIL 设计
规模需要时再引入 Nexus/S3/MinIO/Artifactory
```

对于这个项目，成熟的标志不是 Workflow 数量越来越多，而是：

> **普通应用和多 SoC 固件都能在统一治理下产生可证明的不可变制品；同一制品能够安全晋级、可追溯、可回滚，而厂商 SDK/Runner/License/HIL 又保持严格隔离。**
