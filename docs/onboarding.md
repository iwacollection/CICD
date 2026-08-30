# 新项目接入手册

目标：**业务研发只声明“项目怎么构建、产物是什么”，平台负责 DAG、Runner、工具链、供应链、制品和治理。**

不要为了接入一个新仓库复制一整套中央 Workflow。

---

## 1. 先判断接入方式

平台支持两种接入模型。

### 模型 A：项目源码就在中央仓库

使用：

```text
ci/projects.json
```

适合：

- monorepo；
- 内部库 DAG；
- 平台示例；
- 多个项目需要同一次 Workflow Run 交换上游 Artifact。

### 模型 B：独立业务仓库

业务仓库调用：

```text
iwacollection/CICD/.github/workflows/reusable-build.yml
```

适合：

- 独立服务仓库；
- 独立产品仓库；
- 中央 CI 平台与业务源码分离。

业务仓库固定 exact platform commit SHA，不直接追中央 `main`。

---

## 2. 接入前回答 10 个问题

1. 项目名是什么？
2. 源码根目录在哪里？
3. 是否依赖其他内部项目？
4. 最终 target 是 generic / RK / Qualcomm / MediaTek？
5. 目标 OS 是 Linux 还是 Android？
6. 目标 CPU 架构是什么？
7. 使用哪个已登记 toolchain / SDK identity？
8. Build command 是什么？
9. Test command 是什么？
10. 最终真正需要长期保存哪些 artifact？

如果这 10 个问题回答不清楚，不要先写 Workflow。

---

## 3. 中央仓库项目：在 `ci/projects.json` 声明

当前 Hosted C++ 示例可参考：

```json
{
  "name": "camera-service",
  "enabled": true,
  "path": "services/camera",
  "depends_on": ["media-common"],
  "targets": [
    {
      "enabled": true,
      "soc": "generic",
      "target_os": "linux",
      "arch": "x86_64",
      "toolchain": "gcc-host-container-v1",
      "runner_labels": ["ubuntu-latest"],
      "build_command": "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 4",
      "test_command": "ctest --test-dir build --output-on-failure",
      "fast_test_command": "ctest --test-dir build --output-on-failure",
      "artifact_paths": ["build/camera-service"],
      "dependency_lock_files": ["deps.lock"],
      "cache_paths": ["services/camera/.cache/ccache"],
      "cache_key_files": ["CMakeLists.txt", "deps.lock"]
    }
  ]
}
```

这里不要自己发明一个不存在的 toolchain 名称。

项目引用前，toolchain 必须已经登记在：

```text
ci/toolchains.json
```

---

## 4. 内部依赖怎么声明

假设：

```text
media-common
      ↓
camera-service
      ↓
edge-app
```

配置：

```json
{
  "name": "camera-service",
  "depends_on": ["media-common"]
}
```

平台会自动做：

```text
影响分析
→ prerequisite closure
→ DAG plan
→ L0/L1/L2 barrier
→ 上游 Artifact v2 下载
→ digest 校验
→ staging
→ downstream build
```

业务项目不应该自己通过 `cp ../other-project/build/...` 偷拿上游 workspace 文件。

---

## 5. Artifact path 怎么写

只写真正需要保留的最终文件。

正确：

```json
"artifact_paths": [
  "build/camera-service"
]
```

固件示例：

```json
"artifact_paths": [
  "out/boot.img",
  "out/system.img",
  "out/update.zip"
]
```

不要写：

```json
"artifact_paths": ["**/*"]
```

否则可能把下面内容一起打包：

```text
源码
缓存
临时文件
日志
凭据
私钥
```

Artifact Contract 的目标是形成明确交付物，不是 workspace 打包。

---

## 6. Cache 怎么声明

Cache 只允许放：

```text
删掉以后仍能重新生成的东西
```

例如：

- ccache；
- sccache；
- package download cache；
- vendor download cache。

不能缓存：

- 最终交付制品；
- 签名私钥；
- 生产配置；
- 整个 workspace 快照。

`cache_key_files` 应包含真正会改变编译结果的输入，例如：

```text
CMakeLists.txt
package-lock.json
poetry.lock
go.sum
Cargo.lock
deps.lock
toolchain.lock
```

DAG 场景下平台还会把上游 artifact digest 自动加入下游 cache identity。

---

## 7. Build command 的要求

Build command 必须：

```text
非交互
失败返回非 0
不要求人工输入密码
不依赖 HOME 里的隐式文件
不直接修改生产环境
不负责发布
尽量 deterministic
```

平台会给生产构建统一提供 commit-based `SOURCE_DATE_EPOCH`，用于可复现构建基线。

不要在 build command 中：

```text
curl latest compiler | bash
apt install 未固定来源的关键工具链
kubectl apply production
上传 production release
```

构建和发布必须分离。

---

## 8. 独立业务仓库怎么调用中央 CI

业务仓库自己的 Workflow 应保持很薄。

概念示例：

```yaml
jobs:
  central-ci:
    uses: iwacollection/CICD/.github/workflows/reusable-build.yml@<PINNED_40_CHAR_SHA>
    with:
      project_name: my-service
      working_directory: .
      build_command: make build
      test_command: make test
      artifact_paths_json: '["dist/my-service"]'
      dependency_lock_files_json: '["go.sum"]'
      soc: generic
      target_os: linux
      arch: x86_64
      toolchain: gcc-host-container-v1
      runner_labels_json: '["ubuntu-latest"]'
      platform_ref: <PINNED_40_CHAR_SHA>
```

注意：

```text
workflow `uses:` ref
和
platform_ref
```

都应该固定到明确版本 / SHA，而不是运行时追 `main`。

详细参数见：[业务仓库调用中央 CI](reusable-workflow.md)。

---

## 9. Container toolchain

如果使用 container build，镜像必须固定完整 digest：

```text
registry/repository@sha256:<64 hex>
```

不能：

```text
ubuntu:latest
my-toolchain:v1
```

因为 tag 可以被重新指向其他 bytes。

中央已登记工具链优先通过 `ci/toolchains.json` 管理，不要让每个项目自己维护不同基础镜像策略。

---

## 10. Self-hosted / 硬件目标

### PR 路径

如果 target 使用 Self-hosted Runner：

```text
pull_request
    ↓
Hosted Runner
    ↓
pr_validation_command
```

不可信 PR 不执行真实厂商 build。

如果没有 `pr_validation_command`，硬件 PR 应 fail，而不是 metadata-only 假绿。

### trusted main

只有受信 `main` 才可以进入真实 Self-hosted 构建边界。

---

## 11. RK 接入特别注意

RK 当前模型不是 ARM64 构建主机：

```text
Linux x86_64 Build Host
        ↓ cross compile
RK Linux arm64 Target
```

因此 Runner labels 是：

```json
["self-hosted", "linux", "x64", "soc-rk"]
```

Target 仍然是：

```json
{
  "soc": "rk",
  "target_os": "linux",
  "arch": "arm64"
}
```

`host arch` 和 `target arch` 是两个不同概念。

当前没有真实主机 / SDK / RK 板卡，所以不要把 RK target 改成 active 来“测试 CI 是否绿色”。

详见：[RK Physical Bring-up](rk-physical-bringup.md)。

---

## 12. 接入前本地验证

中央仓库项目：

```bash
python3 scripts/ci/toolchain_catalog.py
python3 scripts/ci/hardware_catalog.py
python3 scripts/ci/validate_config.py
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/discover_matrix.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

重点看：

```text
project / toolchain / hardware 是否一致
DAG 有没有环
最终 Matrix 是否符合预期
有没有意外调度 Self-hosted
```

---

## 13. PR 验收

接入 PR 至少要求：

```text
Validate CI platform  ✅
Build gate            ✅
Toolchain gate        ✅
```

同时人工确认：

- 影响分析范围合理；
- DAG 层级符合预期；
- build/test command 不是 placeholder；
- artifact paths 明确；
- cache 只是加速；
- dependency lock evidence 完整；
- 没有凭据写入仓库；
- PR 没进入高权限硬件 Runner。

---

## 14. “CI 通过”以后还有什么

CI 通过只说明：

```text
这个 artifact 被正确构建、测试、扫描和打包
```

不代表它已经 production-ready。

如果业务要走生产发布，还需要：

```text
main trusted build
→ Attestation
→ long-term archive
→ dev
→ staging
→ production
→ rollback target
```

详细发布契约见：[制品、晋级与回滚](artifacts-promotion-and-rollback.md)。

真实平台验收示例见：[生产生命周期真实验收记录](production-verification.md)。

---

## 15. 接入完成标准

一个项目接入完成，不是“Workflow 文件提交了”。

建议至少达到：

```text
项目配置可校验
DAG 正确
真实 build/test 成功
Artifact Contract v2 成功
供应链 Gate 成功
缓存可删后重建
业务仓库能固定平台版本
生产项目有明确 Archive / Promotion / Rollback 策略
```

硬件项目还需要额外：

```text
真实 Runner
真实 SDK identity
真实 License/HIL lease
真实 flash/boot/smoke
```
