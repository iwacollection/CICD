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
| 灰度 / 蓝绿 / 跨集群跨区域发布 | ✅ | 同一 digest 上做路由；多集群灰度和多集群蓝绿都按区域展开并钉死集群名单；Istio VirtualService 从同一状态渲染，不是第二份策略；见下文 |

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
| 环境内灰度 | ✅ 控制面 | Gateway API HTTPRoute 权重，逐步 1→5→25→50→100；同一环境的 gateway 集群同步同一步；同一次渲染写出权重相同的 Istio VirtualService |
| 多集群灰度 | ✅ 控制面 | `multi_cluster_canary`：cn-east 然后 cn-north，区域内集群同权，后开区域打开前保持 0%，名单用 PlacementDecision + ApplicationSet 钉死 |
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

环境指针只表示“这个 digest 可以出现在该环境”。指针本身不决定流量百分比，也不决定哪些集群收到新版本。这两件事由发布策略控制面完成，而且仍然使用同一份已晋级制品，不重新构建。

### 9.1 多集群灰度：跨集群、跨区域

生产上要做多集群灰度，用 `multi_cluster_canary`。参考目录先打开 cn-east，再打开 cn-north。cn-east 里的 `prod-cn-east-a`、`prod-cn-east-b`、`prod-cn-east-canary` 共享同一步 HTTPRoute 权重。cn-north 在自己的区域打开之前保持 0% canary，不出现在渲染结果里。华北打开之后仍然按 1、5、25、50、100 推进，不会改成蓝绿。

| 问题 | 方法 | 本仓库钉死的工具 |
| --- | --- | --- |
| 多个区域里的多个集群如何按灰度比例展开 | 先钉死集群名单，再只改名单内的权重 | OCM `PlacementDecision` + Argo CD `ApplicationSet` list generator，加上 Gateway API `HTTPRoute` |
| 同一环境里所有 gateway 集群如何用同一步权重分配请求 | 路由 | Gateway API `HTTPRoute` 后端权重。策略名是 `canary`。cn-east 和 cn-north 会一起变化 |
| 同一环境里所有 gateway 集群如何各占一个槽位，验证后一次切完生产流量 | 路由，而且必须是原子切换 | Gateway API `HTTPRoute`，预览走独立 header。策略名是 `blue_green`。cn-east 和 cn-north 会一起切换 |
| 多个区域里的多个集群如何共用槽位并一次切完，后开区域在打开前保持基线 | 先钉死集群名单，再只在名单内做原子切换 | OCM `PlacementDecision` + Argo CD `ApplicationSet` list generator，加上 Gateway API `HTTPRoute`。策略名是 `multi_cluster_blue_green` |
| 同一份期望状态如何交给 Istio | 数据面适配，不是第二份策略 | 同一次渲染写出 `VirtualService`。灰度权重与 HTTPRoute 相同且和为 100；蓝绿生产流量 100% 在当前槽；预览是 header `x-release-preview` 的独立 match |

只给 `role=canary` 的 `prod-cn-east-canary` 做权重，再把后面的区域改成蓝绿，得到的不是跨区域灰度。环境内 `canary` 会让华东和华北使用同一步权重，也做不到“当前区域加权重，后开区域保持 0%”。路由如果没有精确名单，未选中的集群仍会接到新 digest。所以多集群灰度同时要名单和权重，而且路由不能扩大名单。

`prod-edge-offline` 没有 Gateway。多集群灰度默认不选它，它保持基线 digest，也不出现在渲染结果里。若用 `--allow` 明确点名它，计划失败关闭，不会把它写进 `ClusterPin`。分析失败不改变权重。`abort` 把每一个已经打开的区域收回基线。

候选 bundle SHA256 必须等于该环境当前指针，发布不重新构建。GitHub Actions 的顺序见 9.3：先核对环境指针，再 `plan` 或 `advance`，然后从同一份状态渲染 HTTPRoute 和 VirtualService。`scenario` 会用合成分析证据走完所有步骤，只用来渲染最终期望状态；**synthetic analysis is not production evidence**。真实推进用 `advance`，并提交错误率和延迟证据。这个 Job 没有集群凭据，也不会写入 Deployment。

Jenkins 可选。要用的话走 `ops/Jenkinsfile`，它只调用同一条 CLI，不是第二份策略。

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster_canary \
  --environment production \
  --service checkout \
  --artifact-name <artifact-name> \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag <artifact-v2-tag> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --out release-state.json \
  --out-dir rendered
```

### 9.2 多集群蓝绿：跨集群、跨区域

生产上要按区域做蓝绿，用 `multi_cluster_blue_green`。它不是把 `multi_cluster_canary` 的后半段改成蓝绿，也不会把权重从 1% 爬到 100%。参考目录先打开 cn-east，再打开 cn-north。cn-east 里的 `prod-cn-east-a`、`prod-cn-east-b`、`prod-cn-east-canary` 共用同一个槽位。cn-north 在华东 `confirm` 之前保持基线 digest，不出现在渲染结果里。

预览使用 header `x-release-preview: true`，生产 HTTPRoute 的权重保持 100，后端仍是基线槽。同一集群的生产 VirtualService 也是权重 100，没有 header match。预览 VirtualService 只有这条 header match，不改变生产 route。cutover 把生产后端一次换成候选槽。`abort` 把每一个已经打开的区域切回基线槽。`completed` 之后不能 abort，要走环境 rollback。

`prod-edge-offline` 没有 Gateway。多集群蓝绿默认不选它。若用 `--allow` 明确点名它，计划失败关闭，不会把它写进 `ClusterPin`。分析失败不改变槽位。环境指针必须等于候选 digest，而且必须是 64 位小写十六进制。

```bash
python3 scripts/ci/release_strategy.py plan \
  --strategy multi_cluster_blue_green \
  --environment production \
  --service checkout \
  --artifact-name <artifact-name> \
  --bundle-sha256 <candidate-64-hex> \
  --source-sha <source-40-hex> \
  --source-run-id <run-id> \
  --release-tag <artifact-v2-tag> \
  --baseline-digest <serving-64-hex> \
  --environment-pointer-digest <candidate-64-hex> \
  --out release-state.json \
  --out-dir rendered
```

### 9.3 GitHub Actions：同一条 CLI

`.github/workflows/release.yml` 在 `main` 上手工触发。它不是第二份策略引擎。灰度和蓝绿都走下面的顺序：

```text
validate
→ deployment_pointer.py current
→ check-pointer
→ plan、advance、abort 或 scenario
→ 同一份 release state 渲染 Gateway API HTTPRoute 和 Istio VirtualService
→ 缺任何一种 kind 则 Job 失败
→ 上传 release-state.json 与 rendered/
```

权限是 `contents: read` 和 `deployments: read`。没有 `deployments: write`。Job 不读取 kubeconfig，也不执行 `kubectl apply`。

`workflow_dispatch` 输入：

| 输入 | 类型 | 作用 |
| --- | --- | --- |
| `action` | `plan`、`advance`、`abort`、`scenario` | 调用 `scripts/ci/release_strategy.py` 的同名子命令。默认 `plan` |
| `strategy` | `canary`、`blue_green`、`multi_cluster_canary`、`multi_cluster_blue_green` | 默认 `multi_cluster_canary` |
| `environment` | `dev`、`staging`、`production` | 默认 `production` |
| `service` | string | DNS-1123 服务名 |
| `artifact_name` | string | Artifact Contract v2 的 artifact name |
| `bundle_sha256` | string | 必须等于当前环境指针 |
| `source_sha` | string | 制品上的源提交 |
| `source_run_id` | string | 可信构建 run id |
| `release_tag` | string | `artifact-v2` 归档标签 |
| `baseline_digest` | string | 当前正在接流量的 digest |
| `accept_excluded` | boolean | 默认 `false`。确认同一环境里不能接流量的集群 |
| `allow_clusters` | string | 可选，逗号分隔，每个 id 都必须入选 |
| `deny_clusters` | string | 可选，逗号分隔，从名单里去掉 |
| `active_slot` | `blue`、`green` | 蓝绿基线槽，默认 `blue` |
| `analysis` | string | 仅 `advance`。JSON 对象，字段是 `smoke`、`readiness`、`error_rate`、`latency_p95_ms`、`requests` |
| `state` | string | 仅 `advance` 和 `abort`。上一次 `release-state.json` 的全文 |

`plan` 和 `scenario` 不读 `state`，用上面的制品字段生成状态。`check-pointer` 在这四者之前执行。

下一次 `workflow_dispatch` 不能把上一次的 artifact 重新上传进来，表单也没有文件输入。`advance` 和 `abort` 因此让操作者把 `release-state.json` 压成一行贴进 `state`。Job 把字符串写到工作区的 `release-state.json`（不提交），再交给现有引擎校验。状态里的 `candidate_digest` 必须等于刚刚核对过的指针，`environment` 必须等于本次环境。参考目录的状态大约 3KB。

`advance` 把 `analysis` 原样写入 `analysis.json`。Workflow 不填合成的通过结果。`scenario` 才会在摘要里打印 `synthetic analysis is not production evidence`。

`ops/Jenkinsfile` 只是这条 CLI 的可选调用方。它不决定权重，也不代替上面的 Actions 输入。

操作手册：**[灰度、蓝绿与跨集群跨区域灰度](docs/progressive-delivery.md)**

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
7. [灰度、蓝绿与跨集群跨区域灰度](docs/progressive-delivery.md)
8. [生产生命周期真实验收记录](docs/production-verification.md)

### RK / 高通 / 联发科主线

1. [多 SoC / 固件 CI 管理主线](docs/multi-soc-and-firmware.md)
2. [Hardware Runner / SDK / License / HIL](docs/hardware-runner-integration.md)
3. [RK 真实物理接入手册](docs/rk-physical-bringup.md)
4. [Runner 与供应链安全](docs/runner-security-and-supply-chain.md)
5. [Artifact Contract v2](docs/artifact-contract-v2.md)
6. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
7. [灰度、蓝绿与跨集群跨区域灰度](docs/progressive-delivery.md)

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
