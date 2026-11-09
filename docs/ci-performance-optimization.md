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

Security Job 在 Trivy 安装和数据库缓存恢复之前启动 `trivy_scan` 计时，以保持其与改造前“Trivy Action + 数据库缓存恢复 + 扫描”的计时边界一致；随后显式安装固定版本 Trivy v0.70.0，并分别运行漏洞扫描与 CycloneDX SBOM 生成两个 Trivy 进程。两个进程在同一个 Runner、同一个 source checkout 中并行执行，不额外创建两个 GitHub Actions Job，因此不会增加 Runner 启动和跨 Job 工件传输。扫描进程启用 `vuln,license,secret,misconfig` 四类扫描，保留 HIGH/CRITICAL 严重级别和 JSON 报告；扫描使用每日更新的 Trivy DB 缓存（缓存键按日期轮转，恢复使用 `cache-trivy-` 前缀），不跳过漏洞数据库更新。SBOM 进程单独使用临时缓存目录，并设置 `--skip-db-update`、`--skip-java-db-update`、`--skip-check-update`，因为它只生成组件清单，不负责漏洞判定。两个输出仍为 `security-scan.json` 和 `security-sbom.cdx.json`，完成后继续执行原有 supply-chain policy、security identity manifest、单个 detached evidence artifact 上传和最终 security barrier。任一进程启动失败或退出码非零都会使 Security Job 失败；阶段计时分别记录实际进程生命周期。

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
| 工作流失败结论率 | 最近最多 20 次已完成的主干 Build Matrix，加上当前 Build Gate 结果；只按 GitHub 的 `failure / (success + failure)` 计算 | 高于 10% |
| 取消 / 超时 / action_required | 单独统计，不计入失败结论率分母；`cancelled` 可能是 `cancel-in-progress: true` 导致新提交替代旧运行 | 观察项，不触发失败率告警 |

编译和扫描指标至少需要 5 份同 Lane 历史报告才开始判定；工作流失败结论率至少需要 10 个 `success` / `failure` 结论才开始判定。取消、超时和 `action_required` 会展示在报告中，但不参与该比率。每次执行都会生成 ci-performance-regression.json/.md，作为 Actions Job Summary 和 30 天工件保存。超过阈值时发出 GitHub Actions warning 注解。

性能告警当前采用非阻断模式：它提示 P95、扫描时间或工作流失败结论率回归，但不改变 Build gate 的 Artifact Contract、安全扫描、attestation 或签名结果。这样可以先积累真实主干基线，不会因为首批样本不足或性能噪声阻断制品交付。指标报告缺失或 GitHub 历史 API 不可读时，Monitor 会明确显示 monitor_unavailable 警告，不会伪造健康结果。

Build-stage P95 代表目标 Job 内实际执行构建命令的阶段，不是 GitHub Run 的完整排队时间。instrumented_workflow_span_ms 仍用于观察 DAG 并行后的流程跨度；两者不要相加，也不要把多个并行 Job 的时长当成墙钟时间。


## 7. Cache identity and mixed-lane monitoring

Compiler/vendor cache keys use the v4 namespace and are scoped by cache scope, project, SoC, target OS, architecture, immutable toolchain identity and the project's declared source/lock-file fingerprint. The exact key deliberately excludes upstream artifact fingerprints and run-specific identifiers (`github.run_id` / `github.run_attempt`): an upstream artifact can change without invalidating this project's compiler object cache, and stable keys are required for cross-run reuse. DAG artifact download and identity verification remain independent and mandatory. Restore prefixes are restricted to the same cache scope, project, target and immutable toolchain identity; a changed source fingerprint can restore an older same-project snapshot, but that is a prefix fallback, not an exact hit. ccache still hashes the actual compiler arguments and preprocessed inputs, and every run still performs its normal build, tests and Artifact Contract v2 verification.

Cache restore and save are separate actions. Pull-request runs may restore trusted base-branch caches but never save to the trusted main namespace. Only a successful push to `main`, after build/test, artifact verification and artifact upload succeed, may save—and only when the exact key was not restored. GitHub Actions cache snapshots are immutable; parallel jobs do not merge their cache additions, so a shared restore prefix is not a cross-project cache aggregation mechanism.

### P0 runtime verification (2026-11-09)

The following three mainline Build Matrix runs exercised the same `hello-lib` input fingerprint, target (`generic/linux/x86_64`), runner class (`ubuntu-latest`) and immutable toolchain identity. The exact key ended in `9c7ebc67...` in all three runs.

| Mainline run | Cache result | Build command | ccache | Cache restore stage |
|---|---|---:|---:|---:|
| [#42552650550](https://github.com/iwacollection/CICD/actions/runs/42552650550) | Exact miss, then save | 2.393 s | 0 hits / 1 miss | 461.53 ms |
| [#42554712027](https://github.com/iwacollection/CICD/actions/runs/42554712027) | Exact-key hit | 3.708 s | 1 hit / 0 misses | 456.13 ms |
| [#42569606008](https://github.com/iwacollection/CICD/actions/runs/42569606008) | Exact-key hit | 1.577 s | 1 hit / 0 misses | 728.86 ms |

The first run saved a 2,776-byte cache; the two subsequent runs restored that exact key. The first save took approximately 0.76 s from the Actions log. The warm ccache hits prove the cache lifecycle works, but the build-duration spread (2.39 s, 3.71 s, 1.58 s) and tiny one-translation-unit fixture do **not** establish an end-to-end performance improvement. No percentage speedup is claimed.

A changed-input verification PR [#88](https://github.com/iwacollection/CICD/pull/88) changed the declared `hello-lib` header/source fingerprint from `9c7ebc67...` to `bd8c070f...`. Its [Build Matrix run #42613185213](https://github.com/iwacollection/CICD/actions/runs/42613185213) restored the old key only through the same-project restore prefix, recorded 0 ccache hits / 1 miss, rebuilt and tested successfully, uploaded and verified Artifact Contract v2, and skipped `Save build cache` because it was a PR. The [Platform Validate run #42613185248](https://github.com/iwacollection/CICD/actions/runs/42613185248) also passed. This verifies changed-input fallback without permitting a PR cache write.

The same mainline run [#42569606008](https://github.com/iwacollection/CICD/actions/runs/42569606008) exposed a path mismatch in the Conan fixture projects: the cache action pointed at repository-root `source/.cache/ccache` and `source/.ci-cache/conan-home`, while the build scripts create those directories under `source/examples/<project>/`. [PR #89](https://github.com/iwacollection/CICD/pull/89) corrected the catalog paths and added regression coverage; it merged as `b4d5cec52227a3630d4cce0c73a54dbf38d63d69`. The post-merge trusted mainline [Build Matrix #42614568676](https://github.com/iwacollection/CICD/actions/runs/42614568676) succeeded. Its `conan-format-lib` and `conan-compression-lib` L0 jobs used the corrected `source/examples/<project>/.cache/ccache` and `.ci-cache/conan-home` paths, completed their internal warm-cache validation, and successfully saved their project-scoped v4 keys. The exact cache keys are `dag-ci-v4-project-conan-format-lib-generic-linux-x86_64-host:gcc-host-v1-conan-format-lib-820bb07947c0d9174c0e9568585d005ee5c0912c49d233975f3c3e214b7ea8b7` and `dag-ci-v4-project-conan-compression-lib-generic-linux-x86_64-host:gcc-host-v1-conan-compression-lib-2a1fe7128ee566959992b69c1bed08046eb52f510342a93d60ae110b473a2f46`. A subsequent unchanged mainline run is still needed to verify exact-key restore for these two project-scoped caches. The first post-fix PR warm-restore attempt [#42619117425](https://github.com/iwacollection/CICD/actions/runs/42619117425) did restore the exact `conan-format-lib` cache key, but exposed a separate warm-Conan-home defect: when every locked package is already local, Conan's package-list JSON contains only `Local Cache`; the helper then passed that local-only list to `conan download --remote=conancenter`, which failed with `'conancenter' doesn't exist in package list`. This was not a cache miss or stale artifact. The follow-up in this PR classifies package-list origins: local-only skips the unnecessary remote download, a listed `conancenter` origin performs the download, and any unknown origin fails closed. The subsequent `conan install --no-remote`, dependency identity comparison and compiler warm-cache validation remain mandatory. The README is intentionally outside the declared cache fingerprints.

The mainline performance monitor evaluates every Lane present in the current run independently. A mixed Full/Fast/Smoke run no longer collapses into an unknown lane: each lane gets its own Build P95 and Trivy P95 comparison against same-lane historical reports. The workflow failure-conclusion rate remains workflow-wide; it counts only GitHub `failure` against `success + failure`. Cancelled, timed_out and action_required conclusions are surfaced separately and excluded from that rate. A run-level `failure` is not enough evidence to label the root cause a functional/code defect; failed job/step evidence is required for root-cause attribution. A `cancelled` conclusion also does not reveal whether cancellation was caused by concurrency supersession or a manual action.


## 8. Compile performance diagnostics and resource controls

### ccache: per-build hit/miss and global retention

Every compiler invocation receives a per-build `CCACHE_STATSLOG` file. The build wrapper collects `ccache --show-log-stats` after the command, even if the build fails, and separately captures `ccache --show-stats --verbose` for cache size, file count and cleanup counters. It does not reset the shared cache's lifetime statistics.

The report records:
- Direct hits, preprocessed hits, misses, uncacheable calls and the per-build hit rate.
- Current cache size, cache file count, configured size limit and cumulative cleanup events.
- A default `CCACHE_MAXSIZE=2G` for each shared toolchain cache. It can be overridden in the runner environment for a larger project.

**Cleanup events are not an exact eviction count.** ccache reports how many cleanup operations ran; each cleanup can remove a variable number of entries to return below configured limits. The cache file count and size are snapshots, not a per-file deletion audit. For misses, direct/preprocessed ratio and uncacheable reasons should be inspected together. If misses remain unexpectedly high, run a diagnostic build with ccache debug enabled and compare its per-object input and debug logs before changing sloppiness or hash settings.

The stats log is intentionally separate per build, so concurrent jobs sharing one `CCACHE_DIR` do not need to reset global counters and do not contaminate one another's per-build hit rate.



### Trivy/SBOM parallel experiment baseline

The pre-change PR #65 Build Matrix artifact contained four Security Job samples. Trivy scan duration was median 12.71s / P95 14.37s; CycloneDX SBOM generation was median 4.62s / P95 5.24s; total Security Job duration was median 21.73s / P95 24.17s. These four targets are a small CI fixture sample, not a production-wide performance claim. The parallel implementation is an experiment: compare the same target set and runner class after the change, confirm policy/evidence gates remain green, and retain it only if Security Job wall-clock duration decreases without increasing failure or resource contention. The `trivy_scan` timer deliberately includes Trivy setup and database-cache restore as well as the scan, matching the old measurement boundary. `sbom_generation` measures only the SBOM child process. These overlapping per-stage durations must not be added to estimate the job wall time.

### Shared compiler cache and isolation

The C++ example projects use `cache_scope=toolchain` and a common `source/.ci-cache/ccache` directory. Cache restore keys can reuse snapshots across projects only when these dimensions match:

1. Cache scope.
2. SoC, target OS and architecture.
3. Immutable toolchain digest/identity.

Each project still has its own exact save key and declared source/lock fingerprint. The cache is a performance optimization, not a source of truth: the compiler's ccache hash validates the actual compiler arguments, source and headers. Upstream Artifact Contract identity and download verification remain independent of ccache and are not relaxed.

The current example workload is too small to claim large cross-project hit-rate gains. It establishes a safe sharing boundary and records per-project/per-build evidence. Promote wider sharing only when measured hit rate improves without cache restore time or eviction/cleanup rising enough to erase the gain.

### CMake and Ninja diagnostics

C++ build commands use `scripts/ci/cmake_build.sh` to record:
- `configure_generate_ms`: CMake configure plus generation in one CMake invocation.
- `compile_link_ms`: the Ninja-backed build phase.
- CMake Google Trace event count and trace file for deeper profiling.
- Ninja graph node/edge counts, target count, command count and repeated command lines.
- Actual entries added to `.ninja_log`, object compile edges and duplicate object output paths.
- A post-build `ninja -n` check. A zero-action dry run confirms that the just-built tree is incrementally clean.

CMake exposes configure and generation as one `cmake -S/-B` operation; the report deliberately does not invent a separately measured generation duration. Use the Google Trace to inspect individual configure/generate events. Ninja's graph and command tools are diagnostic evidence: repeated command text is a signal to inspect, not proof of duplicate execution. Duplicate output paths in the incremental log are counted separately.

### Resource-aware concurrency

When `build_parallelism=auto`, `run_build.py` computes an effective job count from visible CPUs, process affinity/cpuset, cgroup CPU quota and memory limit. The default memory model reserves 1 GiB for the OS/tools and budgets 2 GiB per compile job; both can be changed with `CI_BUILD_MEMORY_RESERVE_MB` and `CI_BUILD_MEMORY_PER_JOB_MB`. `CI_BUILD_MAX_JOBS` optionally caps the CPU-derived value. An explicitly configured positive `build_parallelism` remains an intentional override.

Ninja also receives a load-average guard. At the matrix level, each DAG layer is limited to four concurrent Build Jobs. This avoids multiplying full-runner CPU concurrency by an unbounded number of simultaneous projects. Increase these limits only after comparing throughput, memory high-water mark, I/O wait and OOM/failure rates on representative builds.

### Toolchain image cold starts

Only nodes that need an actual compiler/toolchain container should pull it. The artifact-only `hello-service` and `hello-release` nodes now use the hosted runner for artifact verification, copying and packaging rather than pulling the GCC image just to copy files. Compiler nodes retain immutable digest-pinned images.

The immutable image is reused if the current Runner already has the exact digest. GitHub-hosted runners are usually ephemeral, so local Docker layers are not assumed to persist between separate jobs. We deliberately did not tar/export the image into Actions Cache: that can add another large compression, upload, download and unpack cycle. Persistent self-hosted runners can benefit from the digest-verified local-image reuse path; hosted runners should be optimized based on measured image pull time and layer sizes.

### Acceptance evidence for this pass

A successful PR check is necessary but not enough to claim build speed improvement. The Build Matrix artifact should show:
- Per-project ccache hits/misses and cache size/cleanup events.
- CMake configure/generate versus compile/link distributions.
- Ninja graph and incremental no-op result.
- Effective CPU/memory parallelism per build.
- Build-node count and whether non-compiling nodes skipped Docker image pulls.

Compare at least one cold run and several warm runs on the same Runner class and toolchain identity before publishing a percentage improvement.


Resource diagnostics additionally capture before/after cgroup v2 counters around each build: CPU usage and throttling, memory peak/current and OOM events, per-device block I/O bytes/ops, and Linux CPU/memory/I/O pressure-stall information (PSI). The report aggregates throttled time, OOM kill events, peak memory and I/O deltas. On runners without cgroup v2 or PSI, unavailable counters remain absent rather than being guessed.


Build and test commands can both run through `run_build.py`. To avoid one overwriting the other's counters, the wrapper writes `build-telemetry-build.json` and `build-telemetry-test.json` as separate phase records. The per-job metrics finalizer attaches every phase record; ccache/CMake/Ninja statistics are calculated only from `phase=build`, while CPU, memory and I/O resource deltas may include both build and test phases.

## 9. End-to-end workflow and job queue telemetry

The separate `CI End-to-End Observability` workflow runs after a `Build Matrix` run on `main` has completed. It reads the parent run and its exact run-attempt jobs through the GitHub Actions API, then joins those timestamps with the parent's `ci-performance-report` artifact when available. The collector checks out the trusted default branch; it does not execute code from an untrusted pull-request head. The resulting JSON and Markdown reports are uploaded for 90 days.

| Metric | Calculation | What it tells us |
|---|---|---|
| Workflow queue | `run_started_at - created_at` | Delay before the workflow run starts |
| Workflow end-to-end | `updated_at - created_at`, only after run status is `completed` | Overall GitHub run elapsed time |
| Workflow active time | `updated_at - run_started_at`, only after run completion | Time from run start to its final update |
| Job queue wait | `job.started_at - job.created_at` | Per-job delay before Runner execution |
| Job runtime | `job.completed_at - job.started_at` | Actual elapsed time for an individual Job |
| Job execution window | Earliest Job start to latest Job completion | Wall-clock window occupied by parallel DAG jobs |
| Instrumented workflow span | Earliest in-job timing marker to latest instrumented Job completion | Internal execution span already collected by `ci_metrics.py` |

**Important interpretation boundaries:**

- GitHub's workflow-run REST object does not expose a dedicated `completed_at` timestamp. The collector fetches the run only after `status=completed` and uses its `updated_at` as the end-time proxy. Job objects have their own `completed_at` timestamps.
- Job queue wait is `started_at - created_at`. It is the job-level scheduling/Runner wait, not the time spent waiting for upstream DAG dependencies before the Job is created.
- The Job execution window is a wall-clock interval. It is not the sum of parallel Job runtimes and is not, by itself, the DAG critical-path duration.
- The instrumented span excludes queue time before the first marker and post-job cleanup. Compare it with the GitHub run and Job timestamps; do not add the two windows together.
- If the parent performance artifact is unavailable, the observer still reports run and Job timing. Stage-level details are omitted rather than fabricated.
- The observer is triggered only for completed `Build Matrix` runs on `main`; its token is read-only for Actions and Contents.

The first report should be read as an attribution tool: use run queue and Job queue to identify scheduling delays, Job runtime to identify slow DAG nodes, and stage timings to identify the work inside the slow Job. This separates GitHub scheduling, dependency topology, Runner execution, and the actual build/security stages instead of collapsing them into one undifferentiated duration.

