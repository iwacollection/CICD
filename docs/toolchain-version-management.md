# 多语言工具链版本管理

## 两层注册表

ci/toolchains.json 记录工具链实际执行身份：镜像、digest、Dockerfile、构建上下文和 Runner 模式。ci/toolchain-versions.json 记录语言版本与构建工具版本的组合。Java 的 JDK 与 Maven 是两个独立版本字段；Go 使用精确 patch 版本；C/C++ 区分 GCC 与 Clang。

## 当前组合与状态

| 语言 | 组合 | 状态 | 说明 |
| --- | --- | --- | --- |
| Java | Temurin 17.0.21+9 + Maven 3.10.0 | candidate | 已有固定 digest 的候选镜像 |
| Java | Temurin 21.0.12.1+1 + Maven 3.10.0 | planned | 需单独构建和验证 JDK 21 镜像 |
| Go | Go 1.26.10 | candidate | 已登记构建定义，待镜像构建/发布闭环 |
| Go | Go 1.25 | planned | 需选定具体补丁版本并固定上游校验和 |
| C/C++ | GCC 13 | active | 当前 GCC 容器工具链；GCC 13 主版本来自 Ubuntu 24.04 构建环境，需在后续 smoke 中加入强制断言 |
| C/C++ | GCC 12 | planned | 需独立镜像，不能复用 GCC 13 镜像 |
| C/C++ | Clang 18 | planned | 需独立镜像及链接器、运行时兼容性测试 |

planned 只代表已登记的版本目标，不代表仓库已有镜像，也不允许项目引用或进入可执行矩阵。

## 解析规则

项目构建只允许 active。候选版本只能由专用验证任务通过 --for-verification 选择；planned、deprecated、retired 均不可执行。解析器要求语言版本、构建工具及构建工具版本精确匹配唯一组合。镜像只能从中央工具链目录解析，项目不能覆盖镜像地址或 digest。

## 矩阵规则

运行：

    python3 scripts/ci/toolchain_versions.py --matrix

矩阵只包含 active 与 candidate 组合，并携带组合 ID、语言版本、构建工具版本、工具链 ID、状态、镜像 digest、Dockerfile、构建上下文和 smoke 命令。工具链镜像工作流也监听版本注册表变更，以便候选镜像进入构建/扫描流程。

- 有 digest 的组合：拉取并验证固定镜像。
- 没有 digest 的候选组合：先构建候选镜像，再执行 smoke、安全扫描和 SBOM；发布后登记 digest。
- planned 组合：只做注册表校验，不进入运行矩阵。
- 不允许候选版本自动晋级 active；晋级需要镜像验证、代表项目消费测试和显式审核。

## 本轮范围与后续

本轮建立版本组合注册、严格解析、状态门禁和矩阵输出。Java 21、Go 1.25、GCC 12、Clang 18 先登记为 planned；它们需要各自的构建定义、上游校验值、镜像 digest 和真实消费测试后，才能改为 candidate。不会用并不存在或未经验证的镜像冒充多版本支持。
