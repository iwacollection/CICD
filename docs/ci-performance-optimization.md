# CI 构建性能优化与阶段观测

本文描述 Build Matrix 对构建耗时、依赖变更范围、工具链执行和安全扫描的优化方式。目标不是跳过质量门禁，而是让互不依赖的工作并行执行，并用数据定位真实瓶颈。

## 1. 新的执行边界

构建 DAG 只负责生成可消费的构建制品：

```text
Discover / Impact Analysis
        |
        v
  Dependency DAG
  L0 -> L1 -> L2 -> ...
   |     |     |
   v     v     v
 Build Artifact Contract v2
```

每个 DAG 节点依旧执行上游制品下载、摘要校验、缓存恢复、构建、测试、制品打包和 Artifact Contract v2 校验。下游节点只等待它真正依赖的上游制品，不等待上游 Trivy 扫描完成。

安全工作由独立的 `.github/workflows/dag-security.yml` 执行：

```text
L0 build succeeds ------> L0 Trivy + SBOM
       |                        |
       v                        v
L1 build succeeds ------> L1 Trivy + SBOM
       |                        |
       v                        v
L2 build succeeds ------> L2 Trivy + SBOM
                                |
                                v
                  Detached Security Barrier
                                |
                                v
                   Final Build / Attestation Gate
```

安全扫描不再处于编译 DAG 的串行等待链，但不是可选任务。每个计划中的目标必须有成功的安全任务；`security_complete` 失败时，最终 Build Gate 失败，可信分支的制品证明也不会生成。

安全证据独立上传，带有 Artifact Contract 名称、项目、源码 SHA、仓库、Run ID/Attempt、目标架构和工具链身份。主分支归档工作流按 Artifact Contract 名称重新绑定证据，并重新执行原有扫描策略、attestation 校验和签名步骤。安全证据缺失、来源不匹配或 SBOM/扫描文件不完整时，归档失败关闭。

## 2. 变更范围与构建 Lane

`scripts/ci/impact_analysis.py` 采用以下规则：

| 变更范围 | Lane | 执行范围 |
|---|---|---|
| `ci/`、`scripts/ci/`、`docker/toolchains/` | full | 全部启用项目 |
| `.github/workflows/` 且声明了 smoke 项目 | smoke | `ci_smoke: true` 的项目及其必要前置依赖；使用 fast test |
| 已登记项目目录或 `impact_paths` | fast | 变更项目、所有下游消费者及它们的前置依赖 |
| 仅文档、Issue 模板等忽略路径 | none | 不启动项目构建 |
| 未归属的其他构建相关路径 | full | 安全回退到全部启用项目 |
| 无法获取可靠 diff、首次 push 或手动全量派发 | full | 安全回退到全部启用项目 |

只有 CI 编排文件变化时，smoke 根节点不会扩展到其所有下游消费者；它只构建 smoke 项目及构建 smoke 项目所需的前置依赖。这是为了验证工作流执行能力而不是重新编译整个产品图。

新增 smoke 项目时，在 `ci/projects.json` 对应 project 上设置：

```json
{
  "name": "small-ci-fixture",
  "ci_smoke": true
}
```

前提是该项目本身具有真实、可执行的构建和测试定义。若没有任何 smoke 项目，工作流变更会安全回退到 full。

## 3. Ninja 与编译并行度

当前 GCC Host 工具链镜像已包含 Ninja，无需另建基础镜像。启用的 C/C++ 示例改为：

```bash
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache \
  -DCMAKE_CXX_COMPILER_LAUNCHER=ccache

cmake --build build --parallel "$CI_BUILD_JOBS"
```

在 `ci/projects.json` 的 target 上配置 `build_parallelism`：

- `"auto"`：使用当前 Runner 可见的 CPU 数量，最少为 1。
- 正整数：为大型工程、内存敏感工程显式限制并行任务数。

并行度会通过 `run_build.py` 的受控环境变量传入容器。它不会改变工具链 digest、缓存身份或 Artifact Contract 的可信边界。ccache 仍只作为可丢弃缓存；缓存丢失时应冷编译，而不是改变测试和制品校验标准。

自动 CPU 数量只是默认值。大型 C++ 工程需要结合每个编译进程内存、链接峰值、Runner CPU/内存限制和实际编译时间调优，不能假定 CPU 数越大越快。

## 4. 阶段耗时指标

`scripts/ci/ci_metrics.py` 为每个 Job 生成 `ci-stage-metrics.json`，并在 Job Summary 显示阶段耗时。指标按 Run ID、Attempt、Job、项目、目标和 Runner 元数据区分。

目前采集的阶段包括：

| Job 类型 | 阶段 |
|---|---|
| Discover | source checkout、catalog validation、change detection、impact analysis、DAG planning |
| Build | source/platform checkout、upstream artifact download/resolve、cache fingerprint/restore、toolchain image pull、build、test、build metadata、artifact package/verify/upload |
| Security | source/platform checkout、Trivy scan、SBOM generation、supply-chain policy、security evidence upload |

每个构建或扫描 Job 上传一个短期保留的 `ci-metrics-*` 工件。Build Matrix 的 `Aggregate CI stage timings` 汇总为：

- `ci-performance-report.json`：原始 Job 和阶段数据；
- `ci-performance-report.md`：阶段样本数、Median、P95、Max 和逐 Job 耗时。

汇总器不会把并行 Job 的耗时相加冒充端到端耗时。排队时间和 Workflow 端到端时长仍应从 GitHub Actions run/job 时间戳单独计算；本报告中的阶段耗时是 Job 内显式埋点的时间。

## 5. 验收方式

每次优化按同一 commit、相同 Runner 类型和相同工具链比较冷/热构建，并保留以下证据：

1. Build Matrix 的 Run 总耗时和各 DAG 节点 Job 时间；
2. Build 与 Security 是否已并行，而不是互相成为 DAG 的等待条件；
3. 每个 planned target 是否都有对应 security evidence，并通过最终门禁；
4. Artifact Contract、扫描报告、SBOM 的身份是否匹配；归档后原有 attestation 和签名验证是否通过；
5. Ninja/并行度变化后的实际 compile、test、package 耗时；
6. ccache 命中率、缓存恢复时间和冷构建 fallback 是否正常。

不要仅凭 Job 变多、构建日志里出现 cache hit 或局部编译时间下降就宣称性能提升。应观察至少数次正常 PR/Push 的端到端耗时分布，区分排队、Runner 准备、编译、扫描和制品处理各自的变化。
