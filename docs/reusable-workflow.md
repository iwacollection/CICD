# 业务仓库如何调用中央 CI

独立业务仓库不需要把源码搬到 CICD 仓库，也不应该复制一整套中央 Workflow。

推荐模型：

```text
Business Repository
        ↓ workflow_call
Central CICD Platform
        ↓
shared build / security / artifact policy
```

业务仓库负责自己的 build/test recipe；中央平台负责 Runner 信任边界、供应链、Artifact Contract 和通用执行规则。

---

## 1. 通用业务仓库最小示例

在业务仓库创建一个很薄的 Workflow：

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  build:
    uses: iwacollection/CICD/.github/workflows/reusable-build.yml@<CICD_FULL_COMMIT_SHA>
    with:
      platform_ref: <CICD_FULL_COMMIT_SHA>
      project_name: my-cpp-service
      working_directory: .
      build_command: cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 4
      test_command: ./build/my-cpp-service --self-test
      artifact_paths_json: '["build/my-cpp-service"]'
      dependency_lock_files_json: '[]'
      soc: generic
      target_os: linux
      arch: x86_64
      toolchain: gcc-host-container-v1
      runner_labels_json: '["ubuntu-latest"]'
```

实际项目如果使用中央登记的 container toolchain，应同时提供对应 immutable container image digest。

不要在文档或业务仓库里自创一个中央 Registry 不存在的 toolchain 名称。

---

## 2. 为什么必须同时固定两个 SHA

调用时有两个位置：

```text
uses: ...reusable-build.yml@<CICD_FULL_COMMIT_SHA>

platform_ref: <CICD_FULL_COMMIT_SHA>
```

二者必须使用同一个审核过的 40 位 commit SHA。

含义：

```text
uses ref
  -> 固定 Workflow 本身

platform_ref
  -> 固定 Workflow checkout 的中央 policy/scripts
```

不能出现：

```text
Workflow 固定到旧版本
中央 scripts 却运行时 checkout main
```

否则消费者以为自己固定了平台版本，实际规则仍会漂移。

Tag / Release 可以作为人类可读版本，但生产执行推荐继续固定 exact commit SHA。

---

## 3. 通用 Reusable Workflow 的职责

`.github/workflows/reusable-build.yml` 当前统一处理：

```text
caller source checkout
        ↓
platform_ref 校验
        ↓
central policy checkout
        ↓
Self-hosted trust lane 判断
        ↓
immutable toolchain image
        ↓
cache fingerprint
        ↓
build / test
        ↓
Trivy scan
        ↓
CycloneDX SBOM
        ↓
Supply-chain Policy
        ↓
build metadata
        ↓
Artifact Contract v2
        ↓
verify
        ↓
Actions Artifact upload
```

因此业务仓库不应该再次复制：

- Trivy Gate；
- SBOM 规则；
- Artifact manifest；
- SHA256 打包；
- Self-hosted PR 隔离逻辑。

否则中央规则升级后会形成两套标准。

---

## 4. 业务仓库负责什么

业务仓库负责：

```text
源码
build command / build script
test command
Hosted-safe PR validation（硬件目标）
依赖 lock 文件
最终 artifact path
业务特有配置
```

中央平台负责：

```text
执行阶段
Runner 信任边界
cache identity
Supply-chain Policy
SBOM
Artifact Contract
统一校验
平台版本治理
```

最重要的边界：

> 中央 CI 定义“阶段和契约”，业务项目定义“自己到底怎么构建”。

---

## 5. 为什么 build command 仍属于业务仓库

平台不应该知道所有项目内部细节。

例如：

```text
C++     -> CMake
Java    -> Gradle / Maven
Go      -> go build
Android -> Gradle / vendor build system
Firmware -> BSP/vendor scripts
```

如果中央 Workflow 开始出现：

```text
if project == A
if project == B
if soc == xxx
```

几百行以后，中央平台会变成所有业务逻辑的耦合点。

推荐业务仓库提供稳定入口：

```bash
./ci/build.sh
./ci/test.sh
```

或者厂商项目：

```bash
./ci/vendor-rk-build.sh
./ci/vendor-rk-hil-test.sh
```

---

## 6. Self-hosted 目标的 PR 信任边界

如果：

```json
"runner_labels_json": "[\"self-hosted\", ...]"
```

Pull Request 不会直接进入真实 Self-hosted Runner。

执行模型：

```text
pull_request
      ↓
ubuntu-latest
      ↓
pr_validation_command
```

`pr_validation_command` 必须能够在 Hosted Runner 上执行，不能依赖：

- 厂商 SDK；
- USB/串口；
- HIL 板卡；
- License server；
- 企业私网中的高权限资源。

适合执行：

```text
脚本语法检查
配置 / manifest 校验
格式检查
静态分析
Hosted-safe 单元测试
lock 文件完整性
```

如果 Self-hosted target 没有 `pr_validation_command`：

```text
PR = FAIL
```

不会采用：

```text
硬件构建跳过
只输出 metadata
PR 绿色
```

这种“假绿色”模式。

---

## 7. RK 项目推荐使用专用入口

真实 RK 项目优先使用：

```text
.github/workflows/reusable-rk-build.yml
```

而不是让业务仓库自由传入 RK Runner / SDK 绑定。

原因：RK 平台希望中央控制：

```text
soc
hardware profile
SDK identity
Runner labels
HIL lease
vendor adapter contract
```

真实 RK 架构是：

```text
Private RK Product Repository
        ↓ pinned reusable-rk-build
Linux x86_64 Self-hosted Build Host
        ↓ cross compile
RK Linux arm64 Target
        ↓
HIL Board
```

所以 RK build host labels 是：

```text
self-hosted
linux
x64
soc-rk
```

目标制品仍然是：

```text
rk / linux / arm64
```

**build host 架构和 target 架构不能混为一谈。**

当前没有真实 RK Runner / SDK / 板卡，所以 RK physical execution 仍保持 planned。

---

## 8. Qualcomm / MediaTek 当前状态

中央平台保留 profile / adapter 设计，但当前 rollout 明确是 RK-first。

因此 Qualcomm / MediaTek：

```text
不主动激活
不使用模板绿色冒充真实 build
等 RK 真实链路和实际业务资源存在后再恢复
```

如果未来恢复，需要各自准备：

```text
真实 build host
SDK identity
License pool
vendor build recipe
HIL/flash/test
```

而不是仅修改 `soc` 字符串。

---

## 9. Cache

业务仓库可以声明 cache，但 cache 永远只是加速层。

例如：

```yaml
      cache_key_files_json: '["deps.lock","toolchain.lock"]'
      cache_paths: |
        source/.cache/ccache
        source/.cache/vendor
```

要求：

```text
删除 cache 后仍能正确构建
```

不要缓存：

- 最终 artifact；
- 私钥；
- 整个 workspace；
- 生产配置。

---

## 10. Secrets

不要把生产凭据做成通用 reusable build 参数。

普通 CI：

```text
尽量无 Secret
或只使用只读 dependency registry credential
```

生产签名 / 云发布：

```text
独立 Workflow
GitHub Environment
OIDC / Federated Identity
KMS / HSM
```

不要把长期 Access Key 放进 build job。

---

## 11. 中央平台怎么升级

正确方式：

```text
CICD platform change
      ↓
Required Gates
      ↓
main verification
      ↓
必要时 lifecycle drill
      ↓
发布稳定版本 / 确认固定 SHA
      ↓
选择一个非核心 consumer 升级
      ↓
验证
      ↓
再批量升级其他 caller
```

不要让所有消费者：

```text
@main
```

因为中央 `main` 一变化，所有业务仓库会同时吃到新行为。

详细维护规则见：[平台维护手册](platform-maintenance.md)。

---

## 12. Consumer 接入验收

一个独立业务仓库真正接入中央 CI，至少要证明：

```text
PR 能调用 pinned reusable workflow
main 能真实 build/test
Artifact Contract v2 正确
Supply-chain Gate 正确
平台 SHA 不会漂移
如果使用 Self-hosted，PR 不会进入高权限 Runner
```

如果该业务还使用中央发布链，再额外验证：

```text
Archive
Promotion
Rollback
```

中央平台自身的参考验收记录见：[生产生命周期真实验收记录](production-verification.md)。
