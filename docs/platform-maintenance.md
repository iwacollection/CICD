# CI 平台维护手册

这份文档回答一个问题：

> 平台已经进入稳定阶段以后，什么可以改，什么不能随手改，改完必须验证什么？

目标不是让 CI 永远不变化，而是避免“为了修一个小问题，把已经验证过的生产契约顺手改坏”。

---

## 1. 平台进入稳定阶段后的原则

当前核心链路已经真实验证：

```text
PR
→ Required Gates
→ main Build
→ Attestation
→ Archive
→ dev
→ staging
→ production
→ rollback
```

所以后续变更默认遵守：

```text
小步 PR
最小变更范围
不混无关重构
不降低 fail-closed
不重新发明已有契约
```

优先级：

```text
稳定性 > 可审计性 > 可维护性 > 新功能数量
```

---

## 2. 平台的稳定契约

下面这些属于 v1 核心契约，不能因为“YAML 更简洁”或“脚本可以少几行”就随意破坏。

### 2.1 Required Gates

`main` 必须继续强制：

```text
Validate CI platform
Build gate
Toolchain gate
```

不能把动态 DAG 中间 Job 当 Required Check，因为动态 Job 名会随 target 变化。

### 2.2 Build once

环境晋级必须继续移动同一 digest：

```text
Build once
→ Archive
→ dev
→ staging
→ production
```

禁止把 production workflow 改成重新执行 build command。

### 2.3 Artifact Contract v2

长期制品至少要继续绑定：

- project；
- source repository；
- source SHA；
- source workflow run；
- target SoC / OS / arch；
- toolchain identity；
- Runner/build metadata；
- dependency lock evidence；
- upstream artifact digest；
- member SHA256；
- bundle SHA256。

如果未来要做 v3，必须明确版本迁移，不要偷偷改变 v2 字段语义。

### 2.4 DAG handoff

下游不能通过 Runner 残留目录或缓存“碰巧找到”上游库。

必须继续：

```text
上游 Artifact v2
→ upload
→ 下游 download
→ verify
→ stage
→ build
```

下游 cache identity 必须继续绑定 upstream digest。

### 2.5 Supply-chain Gate

下面证据不能因为构建耗时而随手删除：

```text
漏洞扫描
License 检查
Secret / Misconfiguration
CycloneDX SBOM
GitHub Attestation
Cosign archive signature
```

如果扫描器更换，需要证明新实现至少保留原有安全语义。

### 2.6 Promotion Path

前向发布路径保持：

```text
dev -> staging -> production
```

`staging` 和 `production` 必须验证 exact artifact identity，不只比较版本号。

### 2.7 Rollback

Rollback 必须：

- 只能恢复目标环境自己的历史 successful deployment；
- 重新验证长期归档和 provenance；
- 新建 Deployment pointer；
- 不修改历史 Deployment；
- 不重新构建旧版本。

---

## 3. 哪些文件属于“平台控制面”

重点目录：

```text
.github/workflows/
ci/
scripts/ci/
docker/toolchains/
tests/
```

含义：

```text
.github/workflows/   -> 执行编排
ci/                  -> 平台期望状态 / catalog / policy
scripts/ci/          -> 真实规则实现
Docker toolchains    -> 不可变构建环境
tests/               -> 防止安全和契约回退
```

修改这些目录时，要默认认为自己是在改“生产平台”，不是普通业务脚本。

---

## 4. 常见变更怎么做

### 4.1 新增项目

优先只改：

```text
ci/projects.json
```

如果现有 toolchain 能满足，不需要改中央 workflow。

### 4.2 新增工具链

流程：

```text
定义 toolchain
→ 固定 immutable identity
→ toolchain smoke
→ supply-chain scan
→ catalog validate
→ 项目才能引用
```

不要让项目自己在 build command 里临时下载未知编译器。

### 4.3 升级 GitHub Action

要求：

- 仍使用完整 commit SHA；
- 先看 changelog / Node runtime 变化；
- Required Gates 全跑；
- 涉及 Artifact / Attestation / cache 行为时重点回归生产契约。

### 4.4 修改 cache

Cache 可以影响速度，不能影响正确性。

变更后检查：

```text
冷缓存能不能成功构建
删掉 cache 能不能成功构建
上游 digest 改变时下游 cache 是否失效
```

### 4.5 修改 Artifact Contract

这是高风险变更。

至少检查：

```text
package
verify
DAG handoff
Archive
Promotion
Rollback
```

任何一层读取 manifest 的逻辑都可能受影响。

### 4.6 修改 Promotion / Rollback

必须重新跑完整 lifecycle drill。

原因：PR 中的 YAML/单测通过，不能替代真实 GitHub Deployment 历史语义。

---

## 5. 每类 PR 的最低验证

### 文档 PR

```text
链接正确
命令与当前代码一致
不把 planned 写成 verified
不把历史 Run ID 当作当前状态
```

### Catalog / 项目 PR

```text
Validate CI platform
Build gate
Toolchain gate
Matrix 与影响分析符合预期
```

### Toolchain / Supply-chain PR

```text
Toolchain gate
真实 candidate build
smoke
scan
policy
SBOM
identity
```

### DAG / Artifact PR

```text
L0 -> L1 真 handoff
upstream digest 进入 manifest
upstream digest 进入 cache identity
Artifact v2 verify
```

### Archive / Promotion / Rollback PR

除 Required Gates 外，还必须做一次 main 生命周期 drill。

参考：[生产生命周期真实验收记录](production-verification.md)。

---

## 6. 不要做的事情

### 不要为了“方便”关闭安全边界

例如：

```text
把 Required Gate 去掉
把 force-push 打开
给 PR Self-hosted Runner
把 digest pin 改回 latest
scan 红了直接 ignore
production 重新 build
rollback 使用任意 Release tag
```

这些都属于架构回退，不是普通维护。

### 不要让业务仓库复制中央平台

错误：

```text
project-a/.github/workflows/ci.yml  一套
project-b/.github/workflows/ci.yml  一套
project-c/.github/workflows/ci.yml  一套
```

推荐：

```text
业务仓库
   ↓ workflow_call
中央 CICD reusable workflow
   ↓
固定 platform SHA
```

平台策略升级由中央仓库管理，消费者明确选择何时升级平台版本。

### 不要让硬件模板变成“假真机”

没有真实 Runner / SDK / 板卡时：

```text
profile = planned
hardware target = disabled
```

不能用 metadata-only success 冒充厂商构建成功。

---

## 7. 发布平台版本的建议

中央 CI 被其他仓库消费后，平台本身也需要版本治理。

推荐：

```text
v1.x
  -> 兼容 Artifact Contract v2
  -> 兼容现有 reusable inputs
  -> 安全规则可增强但不静默破坏 caller

v2
  -> 允许明确 breaking changes
```

消费者应固定：

```text
platform_ref = exact 40-char commit SHA
```

不要运行时追 `main`。

Tag/Release 是人类可读版本；真正执行仍固定 immutable SHA。

---

## 8. 事故时怎么处理

CI 平台本身出事故时，先判断属于哪一层：

```text
GitHub Actions / Hosted Runner
Toolchain registry
Cache
DAG / Artifact handoff
Supply-chain scanner
Archive
Promotion / Deployment
Self-hosted hardware
```

处理顺序：

```text
现象
→ 判断影响面
→ 找证据
→ 止血
→ 恢复
→ 验证
→ 长期治理
```

不要看到 CI 红就直接 rerun。先判断是：

```text
flaky external service
还是
真正 deterministic platform bug
```

详细案例见 [故障排查手册](troubleshooting.md)。

---

## 9. 当前硬件边界

当前 RK / Qualcomm / MediaTek 的真实物理执行仍未验收。

已准备：

- hardware profile；
- SDK identity；
- RK-first rollout policy；
- private product repo trust model；
- Runner bootstrap；
- HIL lease/broker；
- physical readiness；
- vendor adapter interface。

缺少：

- 真实构建主机；
- 厂商 SDK/BSP；
- 许可证资源；
- 板卡；
- USB/串口实验室。

因此维护文档中必须继续保持：

```text
platform-ready != hardware-verified
```

---

## 10. 什么时候才值得继续开发平台功能

不是看到一个新工具就接进去。

只有出现明确需求时再扩展，例如：

```text
真实消费者超过 GitHub Release 能承载的规模
→ 引入 Nexus/S3/MinIO/Artifactory

真实 Runner queue 持续超 SLO
→ 做 autoscaling / runner pool

真实 RK 产品接入
→ 恢复 physical bring-up

Artifact Contract v2 无法表达新交付类型
→ 设计 v3
```

在没有这些真实需求前，优先维护：

```text
文档
测试
依赖升级
安全补丁
SLO
故障演练
消费者接入
```

而不是继续增加抽象层。
