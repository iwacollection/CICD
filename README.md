# Enterprise CI Build Platform

面向 SRE / DevOps / 嵌入式研发团队的企业级 CI 构建与制品平台。

它不是“某个项目的一条 GitHub Actions”，而是一套统一管理下面这些问题的平台规则：

- 普通 Linux C/C++ 构建；
- 多项目内部依赖 DAG；
- Android / Linux 固件；
- Rockchip RK / Qualcomm / MediaTek 等多 SoC；
- Hosted / Self-hosted Runner 信任边界；
- 工具链与 SDK 身份；
- 缓存与依赖代理；
- Artifact Contract v2；
- SBOM / 漏洞 / License / Secret / Misconfiguration 扫描；
- 长期制品归档；
- Promotion / Rollback；
- CI 平台自身的 SLO 与健康度。

> 当前没有真实 RK / Qualcomm / MediaTek 主机、厂商 SDK 和板卡，因此物理硬件执行保持 `planned`。仓库已经准备好接入路径，但不会伪造“真机已通过”的结果。

---

## 1. 当前平台状态

| 能力 | 状态 | 当前实现 |
| --- | --- | --- |
| Hosted C/C++ 真构建 | ✅ 已运行 | CMake + ccache + immutable toolchain image |
| 动态 Build Matrix | ✅ 已实现 | `ci/projects.json` -> matrix |
| 影响分析 / Fast Lane | ✅ 已实现 | 只构建受影响项目并补齐依赖前置 |
| 真实依赖 DAG | ✅ 已实现 | L0-L7、同层并行、跨层 barrier |
| 上游制品交接 | ✅ 已实现 | 下载、校验 Artifact v2、再交给下游 |
| 工具链注册中心 | ✅ 已实现 | `ci/toolchains.json` |
| 工具链镜像不可变 | ✅ 已实现 | full image digest + Ubuntu Snapshot |
| 构建缓存隔离 | ✅ 已实现 | project / target / toolchain / lock / upstream digest |
| Artifact Contract v2 | ✅ 已实现 | 可复现 bundle + manifest + member SHA256 |
| 长期制品归档 | ✅ 已实现 | GitHub Release Archive，Actions Artifact 只做短期传输 |
| Promotion | ✅ 已实现 | 从长期归档晋级同一 digest，不重新构建 |
| Rollback | ✅ 已实现 | 恢复历史 digest pointer，不重新构建 |
| SBOM | ✅ 已实现 | CycloneDX |
| 漏洞 / License / Secret / Misconfig | ✅ 已实现 | Trivy + central policy gate |
| Cosign / GitHub Attestation | ✅ 已实现 | 归档与工具链信任链 |
| Self-hosted PR 隔离 | ✅ 已实现 | 不可信 PR 强制 Hosted-safe validation |
| RK 平台接入骨架 | ✅ 已实现 | x86_64 build host / arm64 target / SDK identity / HIL lease |
| RK 真机执行 | ⏸ 外部阻塞 | 缺真实主机、SDK、板卡 |
| Qualcomm / MTK 真机执行 | ⏸ 后续 | 当前仅 planned profile |
| CI Platform Health | ✅ 已实现 | Success / Queue P95 / Duration P95 / Rerun Rate |

---

## 2. 最重要的设计原则

### 2.1 Build once，后面只移动同一份制品

```text
Source Commit
     |
     v
Build + Test + Scan
     |
     v
Artifact Contract v2
     |
     v
Long-term Archive
     |
     +------> dev
     |
     +------> staging
     |
     +------> production
```

生产晋级不能重新编译，否则：

```text
测试过的 bytes != 生产真正运行的 bytes
```

本平台的 Promotion / Rollback 都围绕 immutable digest pointer 工作。

### 2.2 Cache 只负责加速，不负责正确性

缓存可以删除，可以 miss，可以失效；最多导致构建变慢。

不能把缓存当成：

- 唯一依赖源；
- 最终制品仓库；
- 上游项目交付方式；
- 生产发布依据。

缓存 fingerprint 会绑定项目、target、toolchain、lock 和上游 artifact digest，避免不同构建互相污染。

### 2.3 内部项目按 DAG 执行，不靠人记顺序

当前真实基线：

```text
hello-lib                 L0
   |
   | verified Artifact Contract v2
   v
hello-cpp                 L1
```

下游必须拿到并验证本次 Workflow Run 的上游制品才能构建成功。

### 2.4 Runner 主机架构和目标 CPU 架构是两个概念

RK 当前模型：

```text
Linux x86_64 Build Host
        |
        | cross compile
        v
RK Linux arm64 Firmware
```

所以：

```text
Runner labels = self-hosted / linux / x64 / soc-rk
Target arch   = arm64
```

不能因为固件目标是 ARM64，就直接假设 CI 构建机也必须是 ARM64。

### 2.5 公开中央仓库不拥有真实硬件 Runner

真实物理 Runner 的最终信任模型：

```text
Public Central CICD
        |
        | reusable workflow pinned by exact SHA
        v
Private Product Repository
        |
        | owns Runner authorization
        v
Physical Build Host + SDK + HIL Board
```

不可信 fork / PR 不应该进入拥有厂商 SDK、USB、许可证、内网或签名能力的物理 Runner 授权域。

---

## 3. 真实构建主链路

```text
Pull Request / Push
        |
        v
Platform Validate
        |
        +-- catalog validation
        +-- DAG validation
        +-- toolchain / hardware policy
        +-- supply-chain policy
        +-- platform SLO policy
        |
        v
Impact Analysis
        |
        v
Dependency Closure
        |
        v
DAG Plan L0-L7
        |
        +-------------------------------+
        |                               |
        v                               v
Hosted Generic Build             Trusted Hardware Build
        |                               |
        |                               +-- SDK identity
        |                               +-- resource lease
        |                               +-- vendor adapter
        |                               +-- HIL lease/test
        |                               |
        +---------------+---------------+
                        |
                        v
                 Build / Test
                        |
                        v
         Vulnerability / License / Secret
                / Misconfig Scan
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
                  Build gate
                        |
                        v
              Attestation / Archive
                        |
                        v
              Promotion / Rollback
```

---

## 4. 仓库结构

```text
CICD/
├── .github/
│   └── workflows/
│       ├── validate.yml
│       ├── ci.yml
│       ├── dag-node.yml
│       ├── reusable-build.yml
│       ├── toolchain-images.yml
│       ├── archive-artifacts.yml
│       ├── promote.yml
│       ├── rollback.yml
│       ├── platform-health.yml
│       ├── reusable-rk-build.yml
│       ├── reusable-rk-enrollment.yml
│       └── reusable-rk-physical-readiness.yml
│
├── ci/
│   ├── projects.json
│   ├── toolchains.json
│   ├── hardware-profiles.json
│   ├── hardware-rollout.json
│   ├── supply-chain-policy.json
│   └── platform-slo.json
│
├── scripts/ci/
│   ├── validate_config.py
│   ├── impact_analysis.py
│   ├── dependency_plan.py
│   ├── dag_plan.py
│   ├── discover_matrix.py
│   ├── run_build.py
│   ├── cache_fingerprint.py
│   ├── resolve_upstream_artifacts.py
│   ├── package_artifact.py
│   ├── verify_artifact.py
│   ├── artifact_archive.py
│   ├── deployment_pointer.py
│   ├── supply_chain_policy.py
│   ├── platform_health.py
│   ├── hardware_catalog.py
│   └── hardware_execute.py
│
├── docker/toolchains/
├── ops/rk-runner/
├── examples/
├── firmware/
├── tests/
└── docs/
```

完整文档导航见 [docs/README.md](docs/README.md)。

---

## 5. 本地平台校验

核心平台脚本只依赖 Python 标准库：

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

这不是只检查 YAML 语法，而是验证：

- 项目、toolchain、hardware profile 是否互相一致；
- DAG 是否有环；
- Runner / SDK / target binding 是否可信；
- 供应链规则是否合法；
- 平台 SLO 配置是否合法；
- 当前真正会调度哪些构建目标。

---

## 6. 多 SoC 管理方式

不要复制成：

```text
rk.yml
qcom.yml
mtk.yml
```

统一用项目 target + 中央 profile：

```text
project
├── rk        / linux   / arm64 / rk-sdk-x
├── qualcomm  / android / arm64 / qcom-sdk-y
└── mediatek  / android / arm64 / mtk-sdk-z
```

统一的部分：

- PR policy；
- DAG；
- cache identity；
- Artifact Contract；
- SBOM/security gate；
- archive；
- promotion/rollback；
- audit evidence。

隔离的部分：

- SDK/BSP；
- host requirements；
- license pool；
- build adapter；
- HIL board pool；
- flash/test recipe。

当前只允许 RK-first rollout；Qualcomm / MTK 不会被误激活。

---

## 7. RK 物理接入当前边界

GitHub 侧已经准备好：

```text
x86_64 Runner bootstrap
        |
SDK identity
        |
HIL resource broker
        |
physical preflight
        |
private product repo reusable workflow
```

但真实验收需要外部实体：

```text
1. 私有 RK 产品仓库
2. Debian/Ubuntu x86_64 构建主机
3. 真实 Rockchip SDK/BSP
4. 真实 RK arm64 板卡
5. 稳定 USB/串口路径
```

在这些设备不存在之前，RK profile 保持 planned，不把模拟测试冒充真机成功。

详见 [docs/rk-physical-bringup.md](docs/rk-physical-bringup.md)。

---

## 8. 平台自身怎么监控

CI 不是“写完就不管”的脚本，它自己也是生产系统。

`Platform Health` 当前自动观察：

```text
Success Rate
Queue P95
Duration P95
Rerun Rate
```

策略位于：

```text
ci/platform-slo.json
```

默认每 6 小时生成：

```text
platform-health.json
platform-health.md
```

如果样本足够且违反 SLO，Health Gate 会失败；样本不足则标记 `insufficient-data`，不产生假告警。

详见 [docs/platform-health-slo.md](docs/platform-health-slo.md)。

---

## 9. 当前生产边界

已经可以在纯 Hosted 环境真实验证的能力：

```text
Impact Analysis
Dependency DAG
Immutable Toolchain
Cache Fingerprint
Build/Test
Supply-chain Scan
SBOM
Artifact Contract v2
Verified Upstream Handoff
Long-term Archive Logic
Promotion/Rollback Logic
Platform Health/SLO
```

需要外部系统后再继续真实落地的部分：

```text
Physical SoC Runner
Vendor SDK/BSP
License Server
HIL Device Lab
External Nexus/Artifactory
External S3/MinIO/ACR（如果企业不用 GitHub Release Archive）
KMS/HSM production signing identity
```

这些边界会明确保留，不使用 mock 结果替代生产验收。

---

## 10. 推荐学习顺序

1. [总体架构](docs/architecture.md)
2. [容器化构建环境](docs/containerized-build-environments.md)
3. [构建缓存与依赖](docs/build-cache-and-dependencies.md)
4. [Fast Lane 与影响分析](docs/fast-lane-and-impact-analysis.md)
5. [真实依赖 DAG](docs/dependency-dag-execution.md)
6. [Artifact Contract v2](docs/artifact-contract-v2.md)
7. [制品晋级与回滚](docs/artifacts-promotion-and-rollback.md)
8. [供应链策略](docs/supply-chain-policy.md)
9. [Runner 安全](docs/runner-security-and-supply-chain.md)
10. [多 SoC 与固件](docs/multi-soc-and-firmware.md)
11. [CI 平台健康度与 SLO](docs/platform-health-slo.md)
12. [故障排查](docs/troubleshooting.md)
13. [新项目接入](docs/onboarding.md)

重点不是背 YAML，而是理解：

```text
代码怎么进入 CI
-> 哪些项目真的受影响
-> 依赖顺序怎么保证
-> 哪个 Runner 有资格执行
-> 工具链是否不可变
-> 缓存是否只影响速度
-> 构建出了什么 bytes
-> bytes 有没有被扫描/证明/归档
-> 如何把同一 digest 晋级和回滚
-> CI 自己是不是健康
```
