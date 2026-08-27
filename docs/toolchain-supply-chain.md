# 企业级工具链供应链

## 目标

业务项目不得现场构建 SDK/编译器镜像，也不得自行填写容器镜像地址。项目只声明 `toolchain` ID；中央 `ci/toolchains.json` 决定工具链状态、执行方式、镜像仓库与不可变 digest。

核心原则：

1. **构建环境与业务代码解耦**：工具链由独立流水线生产，业务流水线只消费。
2. **不可变引用**：Active 容器工具链必须使用 `image@sha256:<64位摘要>`。
3. **先验证后发布**：PR 只构建和冒烟；合并 main 后才允许发布 GHCR。
4. **发布不等于可用**：新镜像先作为 Candidate 发布，拿到真实 digest 后再通过 PR 晋升为 Active。
5. **最小权限**：验证任务只有 contents:read；只有 main 的 Publish Job 获得 packages/id-token/attestations 写权限。
6. **可追溯**：每个发布镜像必须有源提交、Dockerfile SHA256、镜像 digest、SBOM、构建来源证明和 promotion record。
7. **缓存绑定工具链身份**：业务缓存键必须包含 toolchain digest，SDK 变化自动隔离旧缓存。

## 状态机

```text
Planned
   |
   | 工具链定义/SDK准备
   v
Candidate
   |
   | PR Verify: build + smoke test
   | main Publish: push + SBOM + provenance
   v
Promotion Record
   |
   | 人工/自动审核真实 digest
   | 修改 ci/toolchains.json
   v
Active
   |
   | 业务 Matrix 才允许消费
   v
Retired
```

`planned`：仅规划，不允许启用项目消费。

`candidate`：可以由工具链流水线构建/验证/发布，但业务项目仍禁止消费。

`active`：生产可用。容器模式必须存在完整 sha256 digest。

`retired`：停止新构建使用；历史制品仍通过 manifest/digest 保持可追溯。

## 正常变更流程

```text
修改 docker/toolchains/<name>/Dockerfile 或 ci/toolchains.json
        |
        v
Pull Request
        |
        +--> Toolchain Verify
        |      - catalog 校验
        |      - Buildx 构建
        |      - smoke test
        |      - Dockerfile 身份校验
        |
        +--> Platform Validate
               - 项目目录校验
               - 工具链引用校验
               - Matrix 渲染
               - 单元测试
        |
        v
Merge main
        |
        v
Toolchain Publish
        - GHCR push
        - pull 并冒烟测试刚发布的精确 image@sha256
        - OCI SBOM
        - GitHub provenance attestation
        - promotion-<toolchain>.json
        |
        v
取得真实 image@sha256
        |
        v
Promotion PR
        - candidate -> active
        - 写入 digest
        |
        v
业务 CI 才可引用
```

## 多 SoC 管理

RK、Qualcomm、MediaTek 不把几十 GB SDK 直接塞进业务仓库。每一种 SDK 都作为独立 toolchain ID 管理，例如：

```text
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

项目只声明：

```json
"toolchain": "rk-sdk-2026.08"
```

不允许项目出现：

```json
"container_image": "...",
"container_dockerfile": "...",
"execution_mode": "container"
```

这样升级 SDK 时只改中央工具链目录，通过影响分析触发安全的 Full Lane；不会出现 A 项目用旧 SDK、B 项目偷偷用新 SDK、C 项目使用 latest 的配置漂移。

## Runner 与大 SDK

真正的 RK/QCOM/MTK SDK 很大时，下一层优化不是取消 digest，而是让受控 Self-hosted Runner 预热 Active digest：

```text
中央 Toolchain Catalog
        |
        v
Runner Image Prewarm
        |
        +-- runner-rk-*   预拉 RK active digest
        +-- runner-qcom-* 预拉 QCOM active digest
        +-- runner-mtk-*  预拉 MTK active digest
        |
        v
业务 Fast Lane
基本不再现场下载/构建 SDK
```

预热只是缓存优化，业务仍必须引用 `image@sha256`，不能退回 tag/latest。

## 回滚

工具链回滚不重新构建旧 SDK。直接把中央目录中的 Active digest 回退到上一个已验证 digest，并重新走 PR + Full Lane。

因此回滚对象是：

```text
Toolchain ID + immutable digest
```

而不是“重新打一个相同 tag”。
