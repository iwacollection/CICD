# 依赖 DAG 真正执行与上游制品交接

## 1. 这次解决的是什么问题

以前 `dependency_plan.py` 只能做两件事：

```text
检查有没有循环依赖
打印理论上的构建层级
```

但 GitHub Actions 的 Matrix 仍然一起启动，所以：

- 下游不会真的等待上游；
- 上游制品不会传给下游；
- 下游可能偷偷使用 Runner 残留、旧缓存或外部旧包；
- “DAG 正确”只存在于日志里，不存在于执行链路里。

现在中央 CI 把依赖关系变成真正的执行 barrier（屏障）。

## 2. 当前执行模型

```text
Change Impact
   │
   ├─ 向下：加入所有受影响消费者
   │
   └─ 向上：加入这些项目的全部前置依赖
   │
   ▼
DAG Planner
   │
   ├─ L0：无前置依赖，层内并行
   ├─ L1：等待 L0 全部成功
   ├─ L2：等待 L1 全部成功
   └─ ...
   │
   ▼
DAG Node
   │
   ├─ 下载本次 Workflow Run 的 dag-* 上游制品
   ├─ 验证 Artifact Contract v2 / source SHA / repository / digest
   ├─ 解包到 .ci-upstream/<project>
   ├─ 计算 upstream fingerprint
   ├─ upstream fingerprint 进入下游 cache key
   ├─ build/test 通过 CI_UPSTREAM_ROOT 使用上游制品
   ├─ 下游 manifest 记录 upstream artifact digest
   └─ 生成自己的 Artifact v2 + SBOM + 安全扫描
```

## 3. 为什么下游改动也要重新构建上游

假设：

```text
lib-a -> service -> ui
```

只修改 `ui` 时，如果只构建 `ui`，它必须从某个外部位置寻找 `service/lib-a`。
这容易把“上一次流水线的包”误当成本次构建依赖。

当前 Fast Lane 会把前置依赖闭包也加入：

```text
修改 ui
 -> lib-a
 -> service
 -> ui
```

因此下游拿到的是**同一 commit、同一 Workflow Run 刚刚验证过的上游制品**。

## 4. 上游制品不是 workspace 共享

不同 Job/Runner 之间不共享工作目录。

上游通过 Actions Artifact 交接：

```text
L0 project
 -> Artifact Contract v2
 -> upload dag-<artifact_name>

L1 project
 -> download dag-*
 -> resolve_upstream_artifacts.py
 -> verify
 -> extract
```

这意味着即使 Self-hosted Runner 上残留旧文件，下游也不能把它当成声明的上游依赖。

## 5. 目标匹配规则

对每个 `depends_on`，resolver 根据下游 target 找唯一兼容上游制品：

优先级：

1. 同 SoC + 同 OS + 同 CPU 架构；
2. `generic` SoC + 同 OS + 同 CPU 架构，作为平台通用库 fallback。

找不到：失败。

同优先级找到多个：失败。

不会“随便挑一个最近的包”。

## 6. 缓存为什么必须绑定上游 digest

错误：

```text
cache key = project + toolchain + own source
```

如果上游 `lib-a` 变化而下游源码没变化，下游可能命中旧 cache。

现在：

```text
cache key
 = project
 + target
 + toolchain identity
 + upstream fingerprint
 + own cache inputs
```

`upstream fingerprint` 由所有声明上游的 artifact name、bundle SHA256、target 计算。
任何上游制品发生变化，下游缓存自动失效。

## 7. Manifest 如何记录真实依赖

Artifact Contract v2 的 `dependencies` 现在包含两类：

```json
{
  "locks": [],
  "upstream_artifacts": [
    {
      "project": "hello-lib",
      "artifact_name": "...",
      "bundle_sha256": "...",
      "source_sha": "...",
      "target": {
        "soc": "generic",
        "target_os": "linux",
        "arch": "x86_64"
      }
    }
  ]
}
```

因此任何下游生产制品都能反查“构建时到底链接了哪个上游 digest”。

## 8. 真实验证示例

仓库现在启用两层示例：

```text
hello-lib
  │
  │  libhello-lib.a + hello_lib.h
  ▼
hello-cpp
```

`hello-cpp` 的 CMake 明确要求：

```text
CI_UPSTREAM_ROOT/hello-lib/build/libhello-lib.a
CI_UPSTREAM_ROOT/hello-lib/include/hello_lib.h
```

没有 DAG 传下来的 verified artifact，CMake 直接失败。
所以流水线成功能证明“上游制品交接确实执行了”，不是只证明 planner 打印了两层。

## 9. 最大 DAG 深度

中央 workflow 当前显式提供 L0～L7，共 8 层。

这是 GitHub Actions 静态 workflow 的明确平台预算，不允许静默截断：

```text
DAG <= 8 层 -> 正常规划
DAG > 8 层  -> dependency_plan.py 直接失败
```

如果未来业务真的需要超过 8 层，应通过平台 PR 扩展 workflow 层级并补容量/排队评估，而不是让第 9 层“看起来成功但根本没跑”。

## 10. 与 Self-hosted SoC Runner 的边界

DAG 不改变已经建立的信任规则：

```text
PR
 -> Hosted Runner
 -> hardware target 只允许 pr_validation_command

trusted main
 -> 才允许调度 Self-hosted SoC Runner
```

DAG Node 自身没有 OIDC/Attestation 写权限；构建完成后由单独的 Hosted attestation job 统一对可信 main 制品生成 provenance。

## 11. 当前与未来 SoC 接入

DAG 引擎已经支持 target-aware 上游匹配，但真实 RK/Qcom/MTK 仍要求外部基础设施准备好后才能启用：

- 对应 Self-hosted Runner；
- 固定 SDK/toolchain identity；
- License server/pool；
- 真机/HIL 实验室；
- 项目真实 build/test/pr-validation 脚本。

这些条件没有满足前，`embedded-firmware-template` 保持 disabled，不能用模板绿色冒充真实硬件 CI。


---

## 12. DAG 执行失败时，系统应该怎样表现

DAG 的关键不是“画出依赖图”，而是确保下游只消费本次运行中、同一源码提交产生并验证过的上游制品。

### 12.1 失败传播规则

当前中央 Workflow 使用静态的 L0～L7 Job，并通过 `needs` 建立层级屏障。下游层依赖上一层成功；上一层失败或被跳过时，后续层不会作为正常成功路径继续交付。Matrix 的 `fail-fast: false` 只表示同一层一个目标失败时，不立即取消同层其他目标；它**不表示允许下一层忽略失败继续构建**。

```text
L0: lib-a 成功 ─┐
    lib-b 失败 ─┴─> L1 不应正常启动
                         |
                         v
                    整次 DAG 失败
                         |
                         v
              修复失败节点或输入后重跑
```

### 12.2 下游节点如何拒绝错误上游

在 `.github/workflows/dag-node.yml` 中，执行顺序是：

1. 下载本次 Run 的 `dag-*` artifacts；
2. `resolve_upstream_artifacts.py` 根据依赖声明、目标平台、`GITHUB_SHA` 和 `GITHUB_REPOSITORY` 解析唯一上游；
3. 校验 Manifest 和 bundle digest；
4. 解压到当前项目的 `.ci-upstream/<project>`；
5. 将上游 fingerprint 放入下游 cache key；
6. 将上游 artifact digest 写入下游 Manifest。

因此，找不到上游、同优先级匹配多个、源码 SHA 不一致或仓库身份不一致，都应该失败关闭。不能从旧 Workflow Run、其他分支或 Runner 工作目录中“补一个看起来差不多的包”。

### 12.3 操作者排查顺序

当下游项目失败时，不要先重跑整个矩阵。按以下顺序定位：

1. 打开失败的 DAG Node Job，确认失败发生在 checkout、上游下载、resolver、build/test 还是 package/verify；
2. 若失败在 resolver，检查本次 Run 是否上传了预期的 `dag-<artifact>`，以及 Manifest 中的 source SHA、repository、target 和 digest；
3. 若失败在 build，检查项目是否按 `CI_UPSTREAM_ROOT` 引用上游输出，而不是引用本机绝对路径；
4. 若失败在 cache fingerprint，检查锁文件 pattern 是否拼错、文件是否确实存在；
5. 若失败在 verify，保留 bundle、manifest、checksum 和完整日志，禁止手工修改 Manifest 让校验通过。

### 12.4 重跑与恢复边界

- **代码或构建脚本错误：**修复代码后产生新 commit，再从新 commit 运行 DAG；不要把旧 commit 的上游包混入新 commit。
- **Runner 临时故障：**可重跑失败的 Workflow Job，但必须确认 GitHub Actions 重跑仍属于同一个 Workflow Run/Attempt 语义，并观察上游 artifact 是否仍可获取。若无法确认，重跑整个 Workflow 更安全。
- **上游制品缺失或 digest 不匹配：**不要只重跑下游；应从失败的上游层重新生成制品，再让下游消费同一 Run 中的验证结果。
- **依赖图或目标配置错误：**先修复 catalog，再运行配置校验和 DAG 规划；不要用手工指定的层级绕过 Planner。
- **超过 8 层：**当前 Workflow 只显式实现 L0～L7，Planner 应拒绝超出预算的 DAG。必须通过代码变更增加层级并补测试，不能删掉深层依赖。

### 12.5 本地复现 Planner

从仓库根目录运行：

```bash
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/dag_plan.py --projects-json '["hello-cpp"]' --lane fast
```

第一条检查 catalog 中依赖关系；第二条根据选定项目和 lane 输出可执行层级。它们验证的是规划逻辑，不会在本地执行 GitHub Actions Job，也不会生成真实 DAG artifacts。

### 12.6 恢复完成的判定

一次恢复必须同时满足：

- 本次运行所有必需层成功；
- 下游 Manifest 的 `dependencies.upstream_artifacts` 指向本次运行上游 bundle digest；
- 上游与下游 source SHA、repository、target 匹配；
- 下游 build/test 成功；
- 最终 bundle、Manifest、checksum 再次校验通过。

只看到最后一个 Job 绿色，不能单独证明依赖交接正确。
