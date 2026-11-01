# CI 平台运维、可观测性与容量

CI 自己也是生产系统。业务发布依赖它，因此不能只关心“流水线能不能写出来”。

## 0. 当前仓库已经真正实现什么

本仓库现在已经把最基础的平台健康指标落成可执行能力，而不再只是本文档里的建议：

```text
.github/workflows/platform-health.yml
        |
        v
scripts/ci/platform_health.py
        |
        +-- Success Rate
        +-- Queue P95
        +-- Duration P95
        +-- Rerun Rate
        |
        v
ci/platform-slo.json
        |
        v
platform-health.json / platform-health.md
```

`Platform Health` 每 6 小时读取 `main` 分支的 GitHub Actions 历史，计算平台级 SLO；报告会写入 Job Summary，并作为 Artifact 保留 30 天。策略和完整说明见 [platform-health-slo.md](platform-health-slo.md)。

当前没有真实 RK / Qualcomm / MTK 主机，因此不会伪造 Runner CPU、磁盘、HIL 等指标。真实 Runner Pool 上线后再继续扩展对应容量指标。

## 1. CI 平台要监控什么

### 控制面

- Workflow/Jenkins 调度是否正常
- API 错误率
- 队列长度
- 队列等待时间
- 任务成功率
- 任务取消率

### Runner

- 在线 Runner 数
- Busy Runner 数
- CPU / 内存
- 磁盘空间
- inode
- IO 延迟
- 网络
- Job 启动时间

### 构建

- 总构建时间
- Compile 时间
- Test 时间
- Package 时间
- Upload 时间
- cache hit rate
- dependency download time

### 制品与依赖仓库

- 可用性
- 存储容量
- 请求延迟
- 5xx
- checksum 错误
- 清理任务
- replication lag

## 2. 最值得看的三个指标

如果只能先做三个：

```text
Queue Time
Build Duration P95/P99
Success Rate
```

Queue Time 很高但 Build Duration 正常，通常是 Runner 容量问题；Build Duration 自己上涨，则继续拆编译、依赖、IO、缓存。

当前 `Platform Health` 已经直接实现 Queue P95、Duration P95 和 Success Rate，并额外增加 Rerun Rate 用于发现“第一次失败、重跑才绿”的 Flaky CI。

## 3. Runner 容量怎么算

不要只按“有几台机器”。

先统计：

```text
每天 Job 数
峰值每小时 Job 数
平均执行时间
P95 执行时间
不同 Runner Pool 的任务占比
```

例如高通构建平均 50 分钟，峰值一小时进来 8 个任务，而只有 2 个 Qualcomm Runner，那么排队是必然的。

## 4. 不同任务不要抢同一类昂贵 Runner

例如：

```text
代码格式检查  2 min
单元测试      5 min
固件全量编译 60 min
真机测试     20 min
```

不要所有任务都占 `soc-qualcomm` Runner。

前置检查先在便宜 Runner 完成，只有已经通过基础门禁的任务才进入昂贵 SoC Runner。

## 5. Jenkins Master/Controller 挂了怎么办

如果使用 Jenkins：

- Controller 不应该保存唯一一份生产制品。
- 配置应代码化/备份。
- Plugin 版本要可恢复。
- Credentials 应进入专门凭据系统或安全备份。
- Agent 应尽量无状态。
- 最终制品在外部 Artifactory/Nexus/S3。

Controller 恢复以后重新接管调度；已经构建好的不可变制品不应丢失。

GitHub Actions 托管控制面省掉了 Controller 运维，但 Self-hosted Runner、依赖仓库、制品仓库仍然是自己的责任。

## 6. CI 的 RTO/RPO

可以给平台定义：

- RTO：CI 故障后多长时间恢复构建/发布能力。
- RPO：最多允许丢多少构建元数据/配置/制品。

生产制品通常要求非常低的 RPO，缓存则可以直接丢。

这再次说明：缓存和制品不能放在同一个生命周期里。

## 7. 成本治理

重点关注：

- Hosted Runner 分钟数
- Self-hosted 机器空闲率
- 大型 ARM Runner 成本
- 存储增长
- Artifact retention
- 重复构建比例
- Cache 存储命中率

真正的优化不是“所有东西缓存越久越好”，而是高价值、命中率高的缓存保留，低价值缓存自动淘汰。

## 8. 构建失败率突然升高

按照维度拆：

```text
按 project
按 runner pool
按 toolchain
按 soc
按 target_os
按 error class
```

如果所有 RK 项目同时失败，很可能是 RK Runner/SDK/依赖服务问题，而不是十几个项目同时写坏代码。

## 9. 告警建议

避免“每个 Job 失败都给 SRE 打电话”。

可以分级：

- 单个项目编译失败：项目负责人。
- 某 Runner Pool 大面积失败：CI 平台负责人。
- 制品仓库不可用：高优先级。
- 生产晋级失败：发布/SRE。
- digest 校验失败：安全级高优先级，停止发布。

当前 `Platform Health` 在样本达到最低数量以后会对 SLO breach 失败，使它可以作为平台级告警信号；新 Workflow 样本不足时只标记 `insufficient-data`，不会误报。

## 10. 故障演练

可以定期演练：

- 一个 Runner 下线
- 依赖代理不可用
- 缓存全部清空
- 制品仓库主节点故障
- 网络限速/断开
- 磁盘满
- SDK 镜像损坏

目标不是证明“永远不坏”，而是验证坏了以后是否能在预期时间恢复，并且不会发布错误制品。


---

## 11. Platform Health 的日常操作

当前可执行的健康度采集只覆盖 GitHub Actions 的平台级 Workflow 历史，不等于已经采集企业 Jenkins、物理 Runner、Harbor 或 HIL 设备指标。

### 11.1 手工触发与查看证据

`.github/workflows/platform-health.yml` 支持手工触发，也每 6 小时定时运行。手工触发后，查看以下三处：

1. **Job Summary**：快速看成功率、Queue P95、Duration P95、Rerun Rate 和是否触发 SLO breach；
2. **platform-health Artifact**：下载 JSON/Markdown 报告，保留具体样本和计算结果；
3. **Workflow 日志**：若没有生成报告，先检查 API 请求、Token 权限和策略文件校验步骤。

本工作流需要 `actions: read` 权限读取 Workflow 历史；不要为解决 403 而直接授予 `write-all`。

### 11.2 指标异常时如何判断

| 观测结果 | 优先怀疑 | 下一步证据 |
|---|---|---|
| Queue P95 升高，Duration P95 稳定 | Runner/并发额度/调度排队 | Workflow 创建与开始时间、Runner 池在线数 |
| Duration P95 升高，Queue P95 稳定 | 构建阶段变慢 | 按依赖下载、编译、测试、打包、上传拆耗时 |
| Success Rate 下降 | 代码变更、工具链、外部服务或 Runner 故障 | 按 Workflow、项目、工具链和失败阶段分组 |
| Rerun Rate 升高 | Flaky test、外部依赖抖动、Runner 不稳定 | 首次失败与重跑成功的 Job/step 差异 |
| insufficient-data | 观察窗口样本不足 | 检查窗口、分支、事件筛选和样本数量；不要当成 SLO 通过 |

### 11.3 SLO breach 的处置

1. 先确认报告窗口和样本数量，避免把少量任务的偶然波动当成平台事故；
2. 对比最近一次成功报告，确定哪个 Workflow/指标首次恶化；
3. 查看同一时间段的仓库变更、工具链升级和外部依赖服务事件；
4. 对影响范围进行分级：单个项目、单类工具链、Runner Pool 或全平台；
5. 止血后再次手工运行 Platform Health，保留新的报告和恢复时间。

不能通过放宽阈值或调低最低样本数来消除告警。若阈值确实需要调整，应修改 `ci/platform-slo.json`，说明业务依据并经过代码评审。

### 11.4 指标覆盖边界

| 能力 | 当前证据来源 | 尚不能证明 |
|---|---|---|
| GitHub Actions 成功率/排队/执行时间/重跑率 | Actions API 历史与 Platform Health 报告 | 企业 Jenkins 和自托管 Runner 的完整健康 |
| Runner CPU、内存、磁盘、IO | 当前未接入真实主机指标 | 不能据此推断物理 Runner 没有容量问题 |
| Harbor/制品仓库容量与延迟 | 当前没有企业 Harbor 实测指标 | 不能声称制品仓库 HA/灾备通过 |
| SoC Runner 与 HIL 等待时间 | 当前无真实硬件池 | 不能声称 RK/QCOM/MTK 物理执行面健康 |

真实基础设施接入后，应分别增加采集器和证据源，再将指标纳入平台健康模型；不能把 GitHub-hosted 样本改名成物理 Runner 指标。
