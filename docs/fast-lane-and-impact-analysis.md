# 变更影响分析与 Fast Lane

这套能力解决的是高频提交场景中的核心浪费：**一次很小的代码变更，不应该触发整个项目矩阵、所有 SoC、所有测试和所有制品处理。**

## 1. 流程

```text
Git push / Pull Request
        │
        ▼
Git diff 获取本次变更文件
        │
        ▼
impact_analysis.py
        │
        ├─ CI / 工具链 / catalog 变化 ──> Full Lane：全部启用项目
        │
        ├─ 项目目录变化 ───────────────> Fast Lane：直接项目
        │                                  │
        │                                  └─ depends_on 反向展开下游项目
        │
        ├─ impact_paths 命中 ───────────> Fast Lane：声明受影响项目
        │
        ├─ 未归属但可能影响构建的路径 ─> Full Lane：安全回退
        │
        └─ 纯文档等忽略路径 ───────────> None：0 个 Build Job
        │
        ▼
discover_matrix.py
        │
        ▼
只生成受影响 Target 的 Matrix
```

## 2. Fast Lane 是什么

Fast Lane 不是“跳过质量检查”，而是把反馈范围缩小到本次变更真正影响的项目。

当前 Fast Lane：

1. 只构建受影响项目及其下游依赖项目。
2. Target 如果配置 `fast_test_command`，Fast Lane 使用快速测试命令；没有配置则继续使用原 `test_command`，不会默认少测。
3. Pull Request 只做 Build + Test，不生成和上传不可变发布制品。
4. main push 仍会生成、校验并上传不可变制品，为后续部署保留同一制品链路。
5. 新提交到来时继续使用 `cancel-in-progress: true`，旧的过期流水线让位给最新提交。

## 3. Full Lane 什么时候自动触发

以下变更不会冒险使用局部构建：

```text
ci/**
scripts/ci/**
.github/workflows/**
docker/toolchains/**
```

这些目录会改变项目 catalog、CI 执行语义、构建脚本或工具链环境，因此直接回退到所有启用项目。

如果出现一个没有任何项目声明归属的新路径，例如：

```text
shared/new-library/**
```

分析器同样会安全回退到 Full Lane，而不是错误地认为“没有项目受影响”。这条规则用于防止新增公共库时漏测。

## 4. 项目路径如何归属

每个项目天然拥有自己的 `path`：

```json
{
  "name": "camera-service",
  "path": "services/camera"
}
```

那么：

```text
services/camera/src/camera.cpp
```

会直接命中 `camera-service`。

如果项目还依赖仓库里的公共路径，可以增加 `impact_paths`：

```json
{
  "name": "camera-service",
  "path": "services/camera",
  "impact_paths": [
    "shared/protos/**",
    "shared/camera-sdk/**"
  ]
}
```

这样修改公共协议或共享 SDK 时，该项目也会进入 Fast Lane。

## 5. depends_on 如何扩大影响范围

假设 catalog：

```text
base-lib
   │
   ▼
camera-service
   │
   ▼
vehicle-app
```

配置：

```json
{
  "name": "camera-service",
  "depends_on": ["base-lib"]
}
```

```json
{
  "name": "vehicle-app",
  "depends_on": ["camera-service"]
}
```

如果只修改 `base-lib`：

```text
直接影响：base-lib

反向依赖展开：
base-lib
camera-service
vehicle-app
```

因此 Fast Lane 会构建这三个项目，而不会构建仓库里其他无关项目。

## 6. 快速测试

Target 可以同时配置完整测试和快速测试：

```json
{
  "test_command": "./ci/test.sh --full",
  "fast_test_command": "./ci/test.sh --smoke"
}
```

语义：

```text
Fast Lane
→ fast_test_command

Full Lane
→ test_command
```

如果没有 `fast_test_command`：

```text
Fast Lane
→ test_command
```

也就是说，默认策略是安全的：**没有显式配置快速测试，就不自动减少测试。**

## 7. 高频变更下的预期效果

原来：

```text
修改 A
→ A + B + C + D + 所有 SoC 全量 Matrix
→ 完整测试
→ PR 也打包上传制品
```

现在：

```text
修改 A
→ Git diff
→ 只找到 A 与依赖 A 的项目
→ Fast Lane
→ PR：Build + 快速测试
→ main：Build + 测试 + 不可变制品
```

项目数量越多、SoC Target 越多，这个收益越明显。

## 8. 设计原则

这套机制遵循两个原则：

```text
能证明影响范围很小
→ Fast Lane

无法证明影响范围很小
→ Full Lane
```

因此它不是“为了速度赌不会出问题”，而是通过明确的项目所有权、依赖关系和安全回退，在保持正确性的前提下减少无效工作。
