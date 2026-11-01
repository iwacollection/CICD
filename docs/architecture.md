# CI 打包平台总体架构

## 1. 这套系统解决什么问题

这不是“写几个 GitHub Actions 文件”，而是把构建过程拆成稳定、可复用、可追溯的能力。

核心目标：

1. 普通应用和嵌入式/固件项目使用同一套治理原则。
2. RK、Qualcomm、MediaTek 通过目标矩阵接入，不复制三套流水线。
3. 一个源码提交在一个确定工具链下只产生一份可追踪制品。
4. 测试、预发、生产晋级时不重新编译，只移动/授权同一制品。
5. 缓存只用于“加速”，不能成为构建正确性的前提。
6. 内部库按依赖图编排，能并行的并行，不能并行的明确等待。
7. 失败后能知道失败在哪一层，并能从安全边界重新执行。

## 2. 分层

```text
代码仓库 / Pull Request
        |
        v
配置校验 + 依赖图检查
        |
        v
构建计划（项目 x SoC x OS x 架构 x 工具链）
        |
        +-------------------------------+
        |                               |
        v                               v
普通 Hosted Runner                专用 Self-hosted Runner
Linux/Windows/macOS               RK / 高通 / MTK SDK
        |                               |
        +---------------+---------------+
                        v
                依赖代理 / 分层缓存
                        |
                        v
                  编译 / 单测 / 扫描
                        |
                        v
        不可变制品 Bundle + Manifest + SHA256
                        |
                        v
             测试 -> 预发 -> 生产晋级
                 （不重新构建）
```

## 3. 配置面与执行面分离

`ci/projects.json` 是配置面，描述“要构建什么”。Runner 和脚本是执行面，负责“怎么构建”。

不要把厂商 SDK 路径、服务器 IP、账号密码直接写入项目配置。项目配置只描述：

- 项目名称
- 工作目录
- 内部依赖
- SoC
- 目标系统
- CPU 架构
- 工具链逻辑版本
- Runner 标签
- 构建命令
- 制品路径
- 缓存路径和锁文件

这样同一个项目迁移 Runner、升级 SDK、切换缓存后端时，不需要重写整条流水线。

## 4. 构建唯一性

一个构建实例至少由下面这些维度决定：

```text
source commit
+ project
+ soc
+ target_os
+ arch
+ toolchain version
+ dependency lock
+ build configuration
```

如果其中任何一项变化，都应视为新构建，而不是覆盖旧制品。

## 5. 多 SoC 为什么不能简单做三套 Job

RK / Qualcomm / MediaTek 最大差异通常来自：

- 厂商 SDK
- 交叉编译工具链
- Android BSP / Linux BSP
- 内核与驱动
- 打包脚本
- 签名工具
- 授权文件
- Runner 运行环境

流水线真正应该统一的是：

- 触发规则
- 配置校验
- 依赖获取
- 缓存策略
- 日志规范
- 制品命名
- 校验和
- 元数据
- 安全门禁
- 晋级流程

“平台统一，工具链隔离”是本仓库的核心原则。

## 6. 内部库依赖

20 个内部库不要靠人工决定顺序，也不要每次所有库从头重编译。

`dependency_plan.py` 会把内部依赖计算成并行层级，例如：

```text
Level 0: base-a   base-b   common-c
Level 1: codec    storage
Level 2: service-core
Level 3: product-app
```

Level 0 可以并行；Level 1 必须等待它依赖的上游完成。

更成熟的生产方案是：内部库发布版本化制品，下游通过 lock/manifest 固定版本。只有真正发生变化的库才重新构建。

## 7. 故障域

发生失败时先区分层次：

```text
配置错误
 -> 项目定义 / 参数 / 依赖环

调度失败
 -> Runner 不在线 / 标签不匹配 / 并发额度

环境失败
 -> SDK / 编译器 / 许可证 / 磁盘 / inode

依赖失败
 -> Nexus/Artifactory/镜像站 / 网络 / 缓存污染

编译失败
 -> 源码 / ABI / 头文件 / 链接

打包失败
 -> 制品路径 / 签名 / 分区大小 / 厂商脚本

发布失败
 -> 权限 / 制品不存在 / SHA256 不一致
```

先确定故障层，再深入，不要看到构建失败就直接清缓存重跑。

## 8. 仓库目录

```text
.github/workflows/     流水线入口与门禁
ci/                    项目、SoC、工具链配置
scripts/ci/            平台执行脚本
examples/              可跑通的最小示例
 tests/                 平台单元测试
 docs/                  架构、缓存、制品、安全、排障文档
```

## 10. 制品晋级与发布控制：必须区分“授权”和“上线”

当前仓库有两条不同的控制链：

**制品晋级（已实现 GitHub 侧记录）**

```text
可信 Build Run
  -> 长期归档
  -> 校验 Manifest / digest / SBOM / Attestation / Cosign
  -> 检查 dev -> staging -> production 的前置记录
  -> GitHub Environment 审批
  -> 创建 GitHub Deployment digest pointer
```

这里创建的 Deployment pointer 会被标记为 `success`，表示“这份制品已通过晋级门禁并被登记为该环境认可的 digest”。它**不表示应用已部署到 Kubernetes，也不表示业务流量已经切换或健康检查通过**。前置环境策略查找的也是同一制品身份的成功晋级 pointer，不是运行时健康证明。详见 [制品、长期归档、晋级与回滚](artifacts-promotion-and-rollback.md)。

**渐进式发布（当前实现为期望状态引擎）**

```text
环境 digest pointer
  -> release_strategy.py plan / advance / abort
  -> 输出 release-state.json
  -> 渲染 Gateway API HTTPRoute + Istio VirtualService
  -> 人工/外部控制器核对并消费
```

策略引擎已实现权重、槽位、区域波次、ClusterPin 和失败不推进等规则；`.github/workflows/release.yml` 只生成、校验并上传期望状态，不执行 `kubectl apply`。当前 `advance` 的分析 JSON 由操作者输入，程序只校验字段、类型和阈值，不验证它是否来自 Prometheus、是否新鲜或是否被篡改。因此这不是已接通 Prometheus 的自动生产发布闭环。

真实生产接入还需要：可信的分析证据采集与签名/来源验证、不可篡改的发布状态存储、Argo CD/Rollouts/流量控制器适配、运行时反馈，以及运行时回滚与 GitOps 期望状态回退。不要把清单渲染成功写成流量切换成功。

## 11. 后续生产化扩展

本仓库预留以下方向：

- Nexus / Artifactory 依赖代理
- S3 / MinIO / ACR / GHCR 不可变制品仓库
- Cosign 签名
- SBOM 软件物料清单
- SLSA provenance 构建来源证明
- OIDC 云身份
- 自托管 Runner 自动扩缩容
- 大型固件增量编译与远程编译缓存
- 许可证池与高价编译机调度
- 构建指标与队列容量监控
