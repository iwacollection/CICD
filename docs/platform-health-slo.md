# CI 平台健康度与 SLO

CI 本身也是生产系统。业务是否能构建、测试、归档和晋级，都依赖 CI 控制面与 Runner 调度，因此不能只观察“某个 Job 红不红”。

本仓库现在把平台健康度作为可执行能力，而不只是文档建议。

## 1. 当前自动采集的四个指标

`Platform Health` Workflow 每 6 小时执行一次，也可以手工触发。它只读取 GitHub Actions 历史，不需要云权限、物理 Runner 或厂商 SDK。

当前从 `main` 分支的 `push` 构建采集：

- `Success Rate`：成功率；
- `Queue P95`：任务从创建到真正开始执行的 P95 等待时间；
- `Duration P95`：任务开始执行到结束的 P95 时长；
- `Rerun Rate`：需要重新运行的比例，用来发现偶发失败或不稳定任务。

默认观察三个平台级 Workflow：

```text
Platform Validate
Build Matrix
Toolchain Supply Chain
```

策略统一定义在：

```text
ci/platform-slo.json
```

业务项目不能通过自己的 Workflow 输入关闭这些平台指标。

## 2. 默认 SLO

当前基线为：

```text
成功率              >= 95%
Queue P95          <= 300 秒
Duration P95       <= 1800 秒
Rerun Rate         <= 10%
```

这是一套平台基线，不代表所有业务都必须使用同样的构建时长目标。以后 RK / Qualcomm / MTK 真正接入后，应继续按 Runner Pool、SoC 和构建类型拆分容量 SLO。

## 3. 为什么只统计 main push

PR 阶段天然会包含研发代码错误，如果把所有 PR 编译失败直接计入 CI 平台成功率，会把“代码写错”和“平台故障”混在一起。

当前平台 SLO 先观察受信 `main` 变更：

```text
branch = main
event  = push
status = completed
```

这样成功率更接近“平台有没有稳定执行已经合入的生产 CI”。

如果未来要统计 PR 体验，应单独增加 PR 指标，而不是和 main SLO 混算。

## 4. Queue Time 怎么理解

```text
Workflow Run Created
        |
        |  Queue Time
        v
Workflow Run Started
        |
        |  Duration
        v
Workflow Run Completed
```

如果：

```text
Queue P95 上升
Duration P95 正常
```

优先怀疑：

- Hosted Runner 调度拥堵；
- Self-hosted Runner 数量不足；
- 某个 Runner Pool 被长任务占满；
- 并发限制过严。

如果：

```text
Queue P95 正常
Duration P95 上升
```

则继续拆：

- 依赖下载；
- 缓存命中；
- 编译；
- 测试；
- 安全扫描；
- SBOM；
- 制品打包与上传。

两类问题不能通过“多加 Runner”统一解决。

## 5. 为什么记录 Rerun Rate

仅看最后一次成功会掩盖不稳定性。

例如：

```text
第一次：failure
重新运行：success
```

最终页面可能是绿色，但平台实际已经出现 Flaky CI（偶发失败）。

`run_attempt > 1` 会被计入 Rerun Rate。超过策略阈值后，`Platform Health` 会产生 SLO breach。

## 6. 样本不足时不误报

新仓库或刚启用某个 Workflow 时，不能因为只有一两次运行就直接判定平台违反 SLO。

`ci/platform-slo.json` 当前要求每个 Workflow 至少：

```text
5 个 completed runs
```

不足时状态是：

```text
insufficient-data
```

不会被当成 breach。

当一部分 Workflow 样本足够、一部分不足时，总体状态为：

```text
partial-data
```

## 7. 失败与证据保留

`Platform Health` 的执行顺序是：

```text
读取策略
   |
读取 GitHub Actions 历史
   |
计算 SLO
   |
生成 platform-health.json
   |
生成 platform-health.md
   |
写入 Job Summary
   |
上传 evidence Artifact（30 天）
   |
最后执行 Health Gate
```

即使 SLO 已经违反，也先尽量保存报告，再让最后的 Gate 失败。

这样不会出现：

```text
健康检查红了
但没有证据说明为什么红
```

## 8. 权限边界

该 Workflow 只需要：

```yaml
permissions:
  contents: read
  actions: read
```

它不拥有：

- `contents: write`；
- `packages: write`；
- `id-token: write`；
- Deployment 写权限；
- Environment 发布权限；
- Self-hosted Runner 厂商 SDK 权限。

因此平台观测能力不会顺便获得发布或供应链写权限。

## 9. 本地离线回放

`platform_health.py` 支持直接读取 GitHub Actions API 保存下来的 JSON：

```bash
python3 scripts/ci/platform_health.py \
  --policy ci/platform-slo.json \
  --input workflow-runs.json \
  --json-out platform-health.json \
  --markdown-out platform-health.md
```

这可以用于：

- 调整阈值前做历史回放；
- 故障复盘；
- 验证新的 SLO 不会产生大量误报；
- 保存 Benchmark 数据。

## 10. 后续生产扩展

真实 SoC Runner 上线后再继续增加：

```text
Platform Health
├── Hosted Runner
│   ├── Queue P95
│   └── Duration P95
├── RK Pool
│   ├── Online / Busy
│   ├── Queue P95
│   ├── Build P95
│   └── HIL wait time
├── Qualcomm Pool
├── MTK Pool
├── Artifact Archive
│   ├── archive latency
│   └── checksum failures
└── Promotion
    ├── success rate
    └── approval-to-deploy latency
```

这一阶段不需要伪造硬件指标；等真实主机和板卡存在以后，再把 Runner Pool 和 HIL 指标接进同一个健康模型。
