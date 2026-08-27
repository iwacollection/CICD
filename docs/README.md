# CI 平台文档导航

这套文档不是按“GitHub Actions 语法”组织，而是按真实生产问题组织。建议第一次学习按顺序读。

## 第一层：先理解整体

1. [总体架构](architecture.md)
   - CI 为什么要分配置面与执行面
   - 构建唯一性
   - 多 SoC 哪些统一、哪些隔离
   - 内部库 DAG

2. [容器化构建环境](containerized-build-environments.md)
   - Docker 到底能解决哪些环境依赖问题
   - Runner 和 Toolchain Image 如何拆分
   - 为什么默认 Container First、Host 只做例外
   - RK / Qualcomm / MediaTek SDK 怎么容器化
   - 驱动、USB 烧录、许可证、真机为什么仍需要专用 Runner
   - 工具链镜像如何版本化、锁 digest、升级和回滚

3. [构建、缓存与依赖](build-cache-and-dependencies.md)
   - 缓存、依赖代理、制品仓库的区别
   - Docker 镜像和构建缓存为什么不是一回事
   - 多 Job 并发缓存为什么会坏
   - 20 个内部库 + 50 个第三方库怎么处理
   - 构建一小时怎么拆

4. [多 SoC 与固件](multi-soc-and-firmware.md)
   - RK / Qualcomm / MediaTek
   - Android / Linux BSP
   - SDK、Runner、签名、真机实验室
   - 弱网/海外工厂

## 第二层：把发布链路理解清楚

5. [制品、晋级与回滚](artifacts-promotion-and-rollback.md)
   - 为什么测试到生产不能重新编译
   - manifest / SHA256
   - Artifact Repository
   - 回滚为什么是切 digest，而不是重新打包

6. [Runner 与供应链安全](runner-security-and-supply-chain.md)
   - Self-hosted Runner 风险
   - Docker Socket 为什么仍然是高权限边界
   - 不可信 PR
   - OIDC / KMS / HSM
   - SBOM / 签名 / 依赖混淆

## 第三层：平台怎么真正落地

7. [业务仓库调用中央 CI](reusable-workflow.md)
   - Reusable Workflow
   - 业务仓库和平台仓库如何分工
   - 中央 CI 如何版本化
   - 外部仓库如何传入锁定的 Toolchain Image

8. [多语言构建策略](language-build-strategies.md)
   - C/C++
   - Java/Kotlin
   - Node.js
   - Python
   - Go
   - Rust
   - Android
   - Container Image

9. [新项目接入](onboarding.md)
   - 项目声明
   - Runner 准备
   - 工具链接入
   - PR 验收

## 第四层：把 CI 当生产系统运维

10. [CI 平台运维、可观测性与容量](operations-observability-and-capacity.md)
   - Queue Time
   - Build Duration P95/P99
   - Runner 容量
   - Jenkins Controller 故障
   - RTO/RPO
   - 成本和故障演练

11. [故障排查手册](troubleshooting.md)
    - Job 排队
    - 构建突然变慢
    - 缓存污染
    - 磁盘/inode
    - OOM
    - SDK 漂移
    - 制品拿错

## 工具链登记

- [Toolchain Registry 规范](../ci/toolchains/README.md)

## 代码对应关系

```text
想看项目声明          -> ci/projects.json
想看容器工具链        -> docker/toolchains/
想看配置校验          -> scripts/ci/validate_config.py
想看依赖 DAG          -> scripts/ci/dependency_plan.py
想看动态矩阵          -> scripts/ci/discover_matrix.py
想看 Host/Container执行 -> scripts/ci/run_build.py
想看制品打包          -> scripts/ci/package_artifact.py
想看 digest 校验      -> scripts/ci/verify_artifact.py
想看平台自检          -> .github/workflows/validate.yml
想看主构建流水线      -> .github/workflows/ci.yml
想看工具链镜像自测    -> .github/workflows/toolchain-images.yml
想看外部仓库接入      -> .github/workflows/reusable-build.yml
想看制品晋级          -> .github/workflows/promote.yml
```

## 面试时怎么理解这套系统

不要回答“我会写 GitHub Actions”。更完整的表达是：

```text
我会把 CI 拆成代码触发、依赖治理、构建调度、Runner 隔离、工具链镜像、缓存、质量门禁、不可变制品、供应链安全和环境晋级。

编译环境优先放到版本化容器镜像里，Runner 只提供内核、CPU、Docker 和必须的硬件/驱动能力；碰到 USB 烧录、厂商许可证、GPU/特殊驱动，再使用隔离的专用 Runner。

多平台场景下，流水线治理统一，但 SDK/工具链/Runner 隔离；同一个制品从测试晋级到生产，不在生产阶段重新构建。

如果构建变慢，我会拆 Queue、Image Pull、Dependency、Compile、Test、Package、Upload 的耗时，而不是直接加机器或清缓存。
```

能把这些说清楚，比背 `matrix`、`needs`、`cache` 语法更接近生产 DevOps/SRE。
