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

并行度会通过 run_build.py 的受控环境变量传入容器，并同时设置 CMAKE_BUILD_PARALLEL_LEVEL，使没有显式 --parallel 的 cmake --build 也遵循同一并行度。CCACHE_DIR 统一指向当前项目的 .cache/ccache；矩阵缓存路径相应以 source/ 为前缀，确保 actions/cache 恢复的位置与容器挂载的 /workspace/.cache/ccache 完全一致。硬件 SDK 构建也经过同一个 run_build.py，因此可复用同一缓存目录约定。它不会改变工具链 digest、缓存身份或 Artifact Contract 的可信边界。ccache 仍只作为可丢弃缓存；缓存丢失时应冷编译，而不是改变测试和制品校验标准。

自动 CPU 数量只是默认值。大型 C++ 工程需要结合每个编译进程内存、链接峰值、Runner CPU/内存限制和实际编译时间调优，不能假定 CPU 数越大越快。

## 4. 阶段耗时指标

`scripts/ci/ci_metrics.py` 为每个 Job 生成 `ci-stage-metrics.json`，并在 Job Summary 显示阶段耗时。指标按 Run ID、Attempt、Job、项目、目标和 Runner 元数据区分。

目前采集的阶段包括：

| Job 类型 | 阶段 |
|---|---|
| Discover | source checkout、catalog validation、change detection、impact analysis、DAG planning |
| Build | source/platform checkout、trust lane、hardware binding/preflight、upstream artifact download/resolve、cache fingerprint/restore、toolchain image pull、software build/test、hardware build/HIL test、build metadata、artifact package/verify/upload |
| Security | source/platform checkout、Trivy scan、SBOM generation、supply-chain policy、security manifest、security evidence upload |

每个阶段的结束步骤使用 `if: always()` 记录退出结果。失败阶段会保留实际耗时并标记 `failure`；Job 在阶段中断时由 finalizer 标记为 `interrupted`，不会把失败伪装成成功。记录指标的步骤使用 `continue-on-error`，避免统计工具故障覆盖真正的构建/安全失败。

Trivy Action 默认启用其数据库和检查规则缓存。本工作流在同一个 Security Job 的第二次 Trivy 调用（SBOM 生成）使用 `skip-setup-trivy: true`，复用前一步已经安装的同版本二进制。没有设置 `TRIVY_SKIP_DB_UPDATE`，因此不会为了节省下载时间而主动跳过漏洞数据库更新。参考 [Trivy Action 缓存和重复 Setup 说明](https://github.com/aquasecurity/trivy-action#cache)。

构建环境元数据采集也已批处理：以前每探测一个编译器/构建工具就启动一次 Docker；现在把 13 个工具的版本探测命令放入同一个 Bash 容器调用中，再逐工具解析结果。缺失的工具仍然跳过，不会因某个工具不存在而让其他探测中断。

每个构建或扫描 Job 上传一个短期保留的 `ci-metrics-*` 工件。Build Matrix 的 `Aggregate CI stage timings` 汇总为：

- `ci-performance-report.json`：原始 Job、阶段结果、任务类型分布和时间戳；
- `ci-performance-report.md`：Job 类型的 Median/P95/Max、阶段样本数及成功/失败/中断数量；
- `instrumented_workflow_span_ms`：从最早 Job 计时标记到最晚 Job 计时完成的墙钟跨度，用来观察并行工作的整体时间范围。

注意，`instrumented_workflow_span_ms` 不是 GitHub Actions 的完整 Run Duration：它不包含首个计时标记前的排队/Runner 初始化，也不包含最后一个计时 Job 之后的 post-job cleanup。它不会把并行 Job 的耗时相加。正式复盘时仍应同时记录 GitHub Run 的开始/结束时间，区分排队、Runner 启动、任务依赖等待和 Job 内阶段耗时。

## 5. 验收方式

每次优化按同一 commit、相同 Runner 类型和相同工具链比较冷/热构建，并保留以下证据：

1. Build Matrix 的 Run 总耗时和各 DAG 节点 Job 时间；
2. Build 与 Security 是否已并行，而不是互相成为 DAG 的等待条件；
3. 每个 planned target 是否都有对应 security evidence，并通过最终门禁；
4. Artifact Contract、扫描报告、SBOM 的身份是否匹配；归档后原有 attestation 和签名验证是否通过；
5. Ninja/并行度变化后的实际 compile、test、package 耗时；
6. ccache 命中率、缓存恢复时间和冷构建 fallback 是否正常。

不要仅凭 Job 变多、构建日志里出现 cache hit 或局部编译时间下降就宣称性能提升。应观察至少数次正常 PR/Push 的端到端耗时分布，区分排队、Runner 准备、编译、扫描和制品处理各自的变化。


## 6. 主干构建性能回归监控

主干 push 和主干 workflow_dispatch 会额外运行 Mainline CI performance regression monitor。它不对 PR 建立基线，避免把 PR 的临时环境和主干数据混在一起；并按 Full/Fast/Smoke Lane 分别比较。

每次主干构建会检查：

| 指标 | 计算口径 | 回归阈值 |
|---|---|---:|
| Build-stage P95 | 当前 Run 同一 Lane 所有 Build Job 的 build 阶段 P95；与最近最多 20 个主干 Run 的 per-run P95 中位数比较 | 高于基线 15% |
| Trivy scan P95 | 当前 Run 同一 Lane 所有 Security Job 的 trivy_scan 阶段 P95；与历史 per-run P95 中位数比较 | 高于基线 20% |
| 功能构建失败率 | 最近最多 20 次已完成的主干 Build Matrix，加上当前 Build Gate 结果；失败、取消、超时计为失败 | 高于 10% |

编译和扫描指标至少需要 5 份同 Lane 历史报告才开始判定；失败率至少需要 10 次已完成构建才开始判定。每次执行都会生成 ci-performance-regression.json/.md，作为 Actions Job Summary 和 30 天工件保存。超过阈值时发出 GitHub Actions warning 注解。

性能告警当前采用非阻断模式：它提示 P95、扫描时间或失败率回归，但不改变 Build gate 的 Artifact Contract、安全扫描、attestation 或签名结果。这样可以先积累真实主干基线，不会因为首批样本不足或性能噪声阻断制品交付。指标报告缺失或 GitHub 历史 API 不可读时，Monitor 会明确显示 monitor_unavailable 警告，不会伪造健康结果。

Build-stage P95 代表目标 Job 内实际执行构建命令的阶段，不是 GitHub Run 的完整排队时间。instrumented_workflow_span_ms 仍用于观察 DAG 并行后的流程跨度；两者不要相加，也不要把多个并行 Job 的时长当成墙钟时间。


## 7. Cache identity and mixed-lane monitoring

Compiler/vendor cache keys are scoped to project, target, immutable toolchain identity and declared source/lock-file fingerprint. They intentionally do not include upstream artifact fingerprints: an upstream artifact can change without invalidating this project's compiler object cache. DAG artifact download and identity verification remain independent and mandatory. The cache namespace was versioned to v2 so old cache archives cannot be mistaken for the revised key contract.

The mainline performance monitor evaluates every Lane present in the current run independently. A mixed Full/Fast/Smoke run no longer collapses into an unknown lane: each lane gets its own Build P95 and Trivy P95 comparison against same-lane historical reports. Functional failure rate remains workflow-wide.
