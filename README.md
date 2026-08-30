# Enterprise CI Build Platform

企业级 **CI 构建、制品、供应链与发布治理平台**。

这个仓库不是“给一个项目写一条 GitHub Actions”，而是把多个项目、多个工具链、内部依赖、多 SoC、制品归档、环境晋级和回滚放到同一套可验证规则里管理。

> 当前核心 Hosted 链路已经完成真实生产生命周期验收：`main Build -> Artifact v2 -> Attestation -> Archive -> dev -> staging -> production -> rollback`。真实 RK / Qualcomm / MediaTek 物理主机、厂商 SDK 和板卡仍属于外部资源边界，不会用模拟结果冒充真机验收。

---

## 1. 这个平台解决什么问题

当 CI 从“一个仓库编译一下”扩大到企业场景，真正难的通常不是 YAML 语法，而是：

```text
哪些项目真的要构建？
内部库应该按什么顺序构建？
上游产物怎么可靠传给下游？
工具链是不是同一版？
缓存会不会把旧依赖带进新构建？
PR 能不能碰高权限 Self-hosted Runner？
测试通过的 bytes 和生产 bytes 是不是同一份？
制品能不能长期保存、追溯、验签、回滚？
CI 自己慢了、排队了、漂移了，谁知道？
```

这个仓库围绕这些问题建立一套统一平台。

---

## 2. 当前已经真实跑通的主链路

```text
Pull Request
     |
     v
Validate CI platform
     |
     +-- catalog / DAG / policy / governance
     +-- reproducibility gate
     |
     v
Impact Analysis
     |
     v
Dependency DAG
     |
     +-- L0 hello-lib
     |      |
     |      `-> Artifact Contract v2
     |
     `-- L1 hello-cpp
            |
            `-> verified upstream artifact handoff
     |
     v
Build / Test
     |
     v
Vulnerability / License / Secret / Misconfiguration
     |
     v
CycloneDX SBOM
     |
     v
Supply-chain Policy
     |
     v
Artifact Contract v2
     |
     v
GitHub Attestation
     |
     v
Build gate
     |
     v
Archive Trusted Artifacts
     |
     v
GitHub Release + Cosign
     |
     v
 dev -> staging -> production
     |
     v
rollback to historical production digest
```

完整真实验收证据见：

**[生产生命周期真实验收记录](docs/production-verification.md)**

---

## 3. 能力状态

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Hosted C/C++ 构建 | ✅ 已验证 | CMake + ccache + immutable toolchain image |
| Fast Lane / 影响分析 | ✅ 已验证 | 只构建受影响项目，并补齐 prerequisite |
| 真实依赖 DAG | ✅ 已验证 | L0-L7，同层并行，跨层 barrier |
| 上游制品交接 | ✅ 已验证 | Artifact v2 下载、校验、staging、下游消费 |
| 不可变工具链 | ✅ 已验证 | image digest + Ubuntu Snapshot |
| Cache identity | ✅ 已验证 | project / target / toolchain / locks / upstream digest |
| Reproducibility Gate | ✅ 已验证 | 两次 clean build 比较原始产物和 bundle bytes |
| Artifact Contract v2 | ✅ 已验证 | manifest + member SHA256 + bundle SHA256 |
| Supply-chain Scan | ✅ 已验证 | Trivy：vuln / license / secret / misconfig |
| CycloneDX SBOM | ✅ 已验证 | 随制品长期保留 |
| GitHub Attestation | ✅ 已验证 | trusted `main` provenance |
| Cosign | ✅ 已验证 | 长期归档签名与 Promotion/Rollback 验签 |
| 长期制品归档 | ✅ 已验证 | GitHub Releases，不依赖 Actions Artifact 生命周期 |
| `dev -> staging -> production` | ✅ 已验证 | exact artifact identity 强制晋级 |
| Production rollback | ✅ 已验证 | `A -> B -> A`，旧版本不重新构建 |
| Repository Governance Drift | ✅ 已实现 | Ruleset 期望状态持续审计 |
| Platform Health / SLO | ✅ 已实现 | Success / Queue P95 / Duration P95 / Rerun Rate |
| RK 平台接入骨架 | ✅ 平台 Ready | x86_64 build host / arm64 target / SDK identity / HIL lease |
| RK 真机 | ⏸ 外部资源阻塞 | 无真实主机、SDK、板卡 |
| Qualcomm / MTK 真机 | ⏸ 暂停 | 保持 planned，不进入当前 rollout |
| Nexus / S3 / MinIO / Artifactory | ⏳ 可选增强 | 当前长期库使用 GitHub Releases |

---

## 4. 最重要的设计原则

### 4.1 Build once

测试、预发、生产使用同一份已经构建好的 bytes：

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

不能变成：

```text
dev build A
production rebuild B
```

否则“测试通过”并不能证明生产实际运行的是同一份制品。

### 4.2 Cache 只负责加速

Cache 可以 miss、删除、失效。

它不能成为：

- 唯一依赖来源；
- 上游项目交付方式；
- 长期制品仓库；
- 生产发布依据。

### 4.3 DAG 必须真正执行

依赖关系不是打印一张图，而是：

```text
hello-lib
   ↓ verified Artifact v2
hello-cpp
```

下游只有拿到本次 Run 里刚构建、刚校验的上游 digest 才能继续。

### 4.4 工具链必须有身份

平台不接受“Runner 上应该装了差不多版本”。

Container toolchain 使用完整 `@sha256:` digest；厂商 Host SDK 使用显式 SDK identity。

### 4.5 PR 和高权限 Runner 必须隔离

不可信 PR 不能因为目标是硬件平台，就自动进入带 SDK、USB、许可证、内网权限的 Self-hosted Runner。

### 4.6 Rollback 不是重新发布旧源码

Rollback 恢复的是：

```text
某个环境曾经成功使用过的历史 immutable digest
```

不是 checkout 老 commit 再 build 一遍。

---

## 5. 仓库结构

```text
CICD/
├── .github/
│   └── workflows/
│       ├── validate.yml                 # 平台自检 / reproducibility
│       ├── ci.yml                       # 主 DAG
│       ├── dag-node.yml                 # 单 DAG 节点
│       ├── toolchain-images.yml         # 工具链供应链
│       ├── archive-artifacts.yml        # 长期归档
│       ├── promote.yml                  # dev/staging/production
│       ├── rollback.yml                 # 历史 digest rollback
│       ├── platform-health.yml          # 平台 SLO
│       ├── repository-governance.yml    # Ruleset drift
│       ├── reusable-build.yml           # 通用业务仓库入口
│       └── reusable-rk-*.yml            # RK 专用受信入口
│
├── ci/
│   ├── projects.json                    # 项目 / target catalog
│   ├── toolchains.json                  # 工具链 registry
│   ├── hardware-profiles.json           # 硬件执行 profile
│   ├── hardware-rollout.json            # 硬件 rollout policy
│   ├── promotion-policy.json            # 环境晋级路径
│   ├── supply-chain-policy.json         # 供应链策略
│   ├── platform-slo.json                # CI SLO
│   └── repository-governance-policy.json
│
├── scripts/ci/                           # 平台规则实现
├── docker/toolchains/                    # 不可变工具链镜像
├── ops/rk-runner/                        # RK 物理接入准备
├── examples/                             # 真实两层 DAG 示例
├── tests/                                # 契约 / 安全边界回归
└── docs/                                 # 平台文档
```

完整导航：**[docs/README.md](docs/README.md)**

---

## 6. 新项目怎么接入

### 方式 A：中央仓库内项目

在 `ci/projects.json` 声明：

```text
项目名
源码目录
内部依赖
目标 OS / CPU / SoC
工具链
Runner
build / test command
artifact paths
cache identity inputs
```

然后由中央 DAG 自动规划。

### 方式 B：独立业务仓库

业务仓库调用：

```text
.github/workflows/reusable-build.yml
```

关键原则：

```text
业务仓库提供源码和 build/test recipe
中央 CICD 提供平台规则
业务仓库固定 exact platform commit SHA
```

不要在每个业务仓库复制一整套中央 CI。

详细步骤：

- [新项目接入手册](docs/onboarding.md)
- [业务仓库调用中央 CI](docs/reusable-workflow.md)

---

## 7. 发布和回滚怎么工作

构建成功并不直接等于 production 发布。

```text
main Build
   ↓
trusted Attestation
   ↓
Long-term Archive
   ↓
dev
   ↓
staging
   ↓
production
```

Promotion 会重新验证：

- 原始 trusted Build；
- Artifact Contract v2；
- bundle SHA256；
- Release identity；
- Supply-chain Policy；
- GitHub Attestation；
- Cosign；
- 前置环境 successful Deployment。

Rollback 只接受同环境历史 Deployment ID，并新建 rollback pointer。

详细说明：**[制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)**

---

## 8. 平台怎么保护 `main`

当前仓库采用单维护者治理模型，但仍保持生产保护：

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

这里取消的是“唯一维护者必须找自己审批”的死锁，不是取消 CI 安全门禁。

详见：**[仓库治理基线与漂移审计](docs/repository-governance.md)**

---

## 9. 硬件 / SoC 当前边界

当前只保留 **RK-first** 物理接入设计，Qualcomm / MTK 不主动推进。

RK 模型：

```text
Private Product Repository
        ↓ pinned reusable workflow
Linux x86_64 Build Host
        ↓ cross compile
RK Linux arm64 Firmware
        ↓
HIL Board
```

已准备：

- Runner bootstrap；
- SDK identity；
- hardware profile；
- rollout policy；
- HIL lease/broker；
- physical readiness；
- vendor adapter interface。

未真实验收：

- 物理主机；
- Rockchip SDK/BSP；
- RK 板；
- USB/串口；
- 实际 flash/boot/smoke。

详见：**[RK Physical Bring-up](docs/rk-physical-bringup.md)**

---

## 10. 平台自己怎么运维

CI 自己也是生产系统。

平台当前跟踪：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

并持续审计：

```text
Ruleset
Required Checks
force-push / deletion protection
review policy
```

稳定阶段维护原则见：

**[CI 平台维护手册](docs/platform-maintenance.md)**

故障处理见：

**[故障排查手册](docs/troubleshooting.md)**

---

## 11. 本地平台校验

```bash
python3 scripts/ci/toolchain_catalog.py
python3 scripts/ci/hardware_catalog.py
python3 scripts/ci/validate_config.py
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/supply_chain_policy.py
python3 scripts/ci/platform_health.py --policy ci/platform-slo.json --validate-policy
python3 scripts/ci/discover_matrix.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

平台校验的重点不是 YAML 能不能解析，而是：

```text
catalog 是否一致
DAG 是否有环
target / toolchain / Runner binding 是否可信
供应链策略是否合法
治理策略是否合法
最终到底会调度哪些任务
```

---

## 12. 推荐阅读顺序

第一次看这个仓库，推荐：

1. [总体架构](docs/architecture.md)
2. [真实依赖 DAG](docs/dependency-dag-execution.md)
3. [Artifact Contract v2](docs/artifact-contract-v2.md)
4. [供应链策略](docs/supply-chain-policy.md)
5. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
6. [生产生命周期真实验收记录](docs/production-verification.md)
7. [业务仓库调用中央 CI](docs/reusable-workflow.md)
8. [新项目接入手册](docs/onboarding.md)
9. [平台健康度与 SLO](docs/platform-health-slo.md)
10. [仓库治理](docs/repository-governance.md)
11. [平台维护手册](docs/platform-maintenance.md)
12. [故障排查](docs/troubleshooting.md)

---

## 13. 当前阶段

核心平台已经进入**稳定 / 文档 / 消费者接入阶段**。

除非出现真实需求，否则不继续为了“功能更多”而增加抽象。

后续优先事项是：

```text
文档保持与代码一致
安全与依赖升级
Platform SLO
生命周期故障演练
真实业务仓库消费 reusable workflow
有硬件后恢复 RK physical bring-up
规模需要时再引入 Nexus/S3/MinIO/Artifactory
```

对于这个项目，成熟的标志不是 Workflow 数量越来越多，而是：

> **相同输入能稳定产生可证明的制品，同一制品能安全晋级、可追溯、可回滚，平台自身也能被治理和运维。**
