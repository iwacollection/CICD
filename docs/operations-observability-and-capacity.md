# CI 平台运维、可观测性与容量

CI 自己也是生产系统。业务发布依赖它，因此不能只关心“流水线能不能写出来”。

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
