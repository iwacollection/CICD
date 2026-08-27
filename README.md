# Enterprise CI Build Platform

面向 SRE / DevOps / 嵌入式研发团队的企业级 CI 打包平台骨架。

它不是某个项目的一条流水线，而是用一套统一规则管理：

- 普通 Linux C/C++ 项目
- Android / Linux 固件
- 瑞芯微 RK
- Qualcomm 高通
- MediaTek 联发科
- 后续新增 SoC / CPU 架构 / 工具链
- 内部库依赖、第三方依赖、缓存、制品、晋级、回滚和 Runner 安全

当前仓库已经有一条真实可运行的 C++ 基线：动态发现构建矩阵 -> 编译 -> 打包 -> 生成 manifest -> SHA256 校验 -> 上传 Artifact。

---

## 1. 核心思想

这套 CI 最重要的不是 YAML，而是下面几条规则。

### 规则一：平台统一，工具链隔离

RK、高通、MTK 的 SDK/BSP/编译器可以完全不同，但触发、缓存、制品命名、校验、审批、晋级和审计必须统一。

### 规则二：缓存只负责加速

缓存坏了最多应该让构建变慢，不能让构建结果错误。第三方依赖、编译缓存和最终制品仓库是三种不同能力，不能混在一起。

### 规则三：一个源码提交只构建一次可发布制品

测试、预发、生产使用同一份已校验制品，生产不能再重新编译一遍。

### 规则四：构建环境必须能被识别

制品至少绑定：

```text
source commit
+ project
+ soc
+ target OS
+ CPU arch
+ toolchain version
+ dependency lock
```

### 规则五：内部依赖按 DAG 管理

20 个内部库不是所有 Job 乱跑，也不是靠人记编译顺序。先建立依赖图，能并行的并行，有依赖的明确等待；成熟后上游库发布不可变二进制包，下游消费锁定版本。

---

## 2. 整体流程

```text
                  Pull Request / Push
                          |
                          v
                配置校验 / 依赖图检查
                          |
                          v
                  动态生成 Build Matrix
                          |
        +-----------------+-----------------+
        |                 |                 |
        v                 v                 v
   Generic Linux       RK Runner      Qualcomm / MTK Runner
   Hosted Runner       Self-hosted       Self-hosted
        |                 |                 |
        +-----------------+-----------------+
                          |
                          v
               依赖代理 + 分层构建缓存
                          |
                          v
                    Build / Test
                          |
                          v
             Immutable Artifact Bundle
              + manifest + SHA256
                          |
                          v
                     Verify Digest
                          |
                          v
                    Artifact Store
                          |
             +------------+------------+
             |            |            |
             v            v            v
            dev        staging     production
             |            |            |
             +-------- same bytes ------+
```

---

## 3. 仓库结构

```text
CICD/
├── .github/
│   └── workflows/
│       ├── validate.yml       # 平台配置/脚本自检
│       ├── ci.yml             # 动态多目标构建
│       └── promote.yml        # 同一制品晋级，不重编译
│
├── ci/
│   ├── projects.json          # 项目、SoC、Runner、工具链、制品声明
│   └── toolchains/
│       └── README.md          # 工具链登记规范
│
├── scripts/ci/
│   ├── validate_config.py     # 项目配置校验
│   ├── dependency_plan.py     # 内部依赖 DAG / 并行层级
│   ├── discover_matrix.py     # 动态生成 Actions Matrix
│   ├── run_build.py           # 统一执行构建命令
│   ├── package_artifact.py    # 不可变制品 + manifest + SHA256
│   └── verify_artifact.py     # 制品完整性校验
│
├── examples/
│   └── cpp-app/               # 真正能跑通的最小 C++ 示例
│
├── tests/
│   └── test_ci_scripts.py     # 平台标准库单元测试
│
└── docs/
    ├── architecture.md
    ├── build-cache-and-dependencies.md
    ├── multi-soc-and-firmware.md
    ├── artifacts-promotion-and-rollback.md
    ├── runner-security-and-supply-chain.md
    ├── troubleshooting.md
    └── onboarding.md
```

---

## 4. 当前支持范围

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| C/C++ Hosted Runner 示例 | 已可运行 | CMake 构建、打包、校验、上传 |
| 动态 Build Matrix | 已实现 | 从 `ci/projects.json` 生成 |
| 内部依赖 DAG 校验 | 已实现 | 检测循环依赖并输出并行层级 |
| 分维度缓存 | 已实现基线 | project / soc / os / arch / toolchain 隔离 |
| 不可变制品 | 已实现 | bundle + manifest + SHA256 |
| 制品完整性验证 | 已实现 | 发布前重新计算 digest |
| 同制品晋级 | 已实现基线 | 从指定 Build Run 下载旧制品，验证后晋级 |
| 生产 Environment 审批 | Workflow 已预留 | 需要在 GitHub Repository Settings 配置 reviewers |
| RK | 接入模板已提供 | 需要企业自己的 SDK 与 Self-hosted Runner |
| Qualcomm | 接入模板已提供 | 需要企业 SDK/授权与 Self-hosted Runner |
| MediaTek | 接入模板已提供 | 需要企业 SDK/授权与 Self-hosted Runner |
| Nexus / Artifactory | 架构已设计 | 生产接入时配置真实地址/身份 |
| S3 / MinIO / ACR / GHCR 长期制品库 | 架构已设计 | Actions Artifact 目前用于可运行基线 |
| SBOM / 签名 / SLSA | 下一生产阶段 | 文档已说明边界与接入位置 |
| 真机烧录实验室 | 架构预留 | 后续接设备锁、串口、烧录和健康检查 |

说明：仓库不会伪造 RK/Qualcomm/MTK 厂商 SDK。三类配置默认关闭，等真实 Runner/SDK 就绪后再启用。

---

## 5. 先跑通本地平台校验

仓库脚本只依赖 Python 标准库。

```bash
python3 scripts/ci/validate_config.py
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/discover_matrix.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

你会分别看到：

1. 项目配置是否合法。
2. 内部库依赖有没有循环。
3. 当前真正会生成哪些构建任务。
4. 平台脚本的基础测试结果。

---

## 6. 新项目怎么接入

正常研发不需要复制 Workflow。

在 `ci/projects.json` 增加项目声明：

```json
{
  "name": "camera-service",
  "enabled": true,
  "path": "services/camera",
  "depends_on": ["media-common"],
  "targets": [
    {
      "enabled": true,
      "soc": "rk",
      "target_os": "linux",
      "arch": "arm64",
      "toolchain": "rk-sdk-2026.08",
      "runner_labels": ["self-hosted", "linux", "arm64", "soc-rk"],
      "build_command": "./ci/build.sh rk linux arm64",
      "artifact_paths": ["out/**/*.img"],
      "cache_paths": [".cache/ccache"],
      "cache_key_files": ["deps.lock", "toolchain.lock"]
    }
  ]
}
```

完整接入步骤见 [docs/onboarding.md](docs/onboarding.md)。

---

## 7. 多 SoC 怎么扩

不要新增 `rk.yml`、`qcom.yml`、`mtk.yml` 三条互相复制的流水线。

正确方式：

```text
同一个项目
  |
  +-- target: rk / linux / arm64 / rk-sdk-x
  +-- target: qualcomm / android / arm64 / qcom-sdk-y
  +-- target: mediatek / android / arm64 / mtk-sdk-z
```

Matrix 根据 target 自动分发到不同 Runner。

详细原理见 [docs/multi-soc-and-firmware.md](docs/multi-soc-and-firmware.md)。

---

## 8. 缓存怎么设计

不要让多个 Job 直接共写 `/mnt/cache` 这种裸目录。

本平台缓存隔离思路：

```text
project
+ soc
+ target_os
+ arch
+ toolchain
+ lock hash
```

更完整的生产设计是：

```text
第三方依赖 -> Nexus / Artifactory Proxy
C/C++ 对象文件 -> ccache / sccache / remote cache
最终制品 -> 独立不可变 Artifact Repository
```

详见 [docs/build-cache-and-dependencies.md](docs/build-cache-and-dependencies.md)。

---

## 9. 制品为什么不能重新打

CI 成功后会生成：

```text
hello-cpp-generic-linux-x86_64-<gitsha>.tar.gz
hello-cpp-generic-linux-x86_64-<gitsha>.tar.gz.sha256
hello-cpp-generic-linux-x86_64-<gitsha>.manifest.json
```

后续 staging / production 通过 `promote.yml` 获取这份已有制品并重新校验 SHA256。

```text
Build once
 -> Verify
 -> Test same digest
 -> Promote same digest
 -> Production same digest
```

详见 [docs/artifacts-promotion-and-rollback.md](docs/artifacts-promotion-and-rollback.md)。

---

## 10. Self-hosted Runner 最重要的安全规则

**不可信 PR 不要直接跑在拥有内网、SDK、许可证、云权限或签名能力的 Self-hosted Runner 上。**

推荐分层：

```text
未知 PR
 -> Hosted / Sandbox Runner

受信代码
 -> SoC Self-hosted Runner

签名
 -> 独立 Signing Runner / KMS / HSM
```

详细安全设计见 [docs/runner-security-and-supply-chain.md](docs/runner-security-and-supply-chain.md)。

---

## 11. 出故障怎么排

先判断是哪一层：

```text
触发
 -> 调度
 -> Runner
 -> Checkout
 -> Dependency
 -> Compile
 -> Test
 -> Package
 -> Upload
 -> Promotion
```

不要所有失败都靠“清缓存再跑一次”。

构建一小时、缓存并发、磁盘/inode、OOM、SDK 漂移、制品拿错等场景见 [docs/troubleshooting.md](docs/troubleshooting.md)。

---

## 12. 生产化路线

### Phase 1：当前基线

- 平台配置
- 动态 Matrix
- C++ 真构建
- 缓存隔离
- manifest
- SHA256
- Artifact
- 同制品晋级
- 文档与排障

### Phase 2：企业依赖与长期制品

- Nexus / Artifactory
- Conan / Maven / npm / PyPI / Go Proxy
- S3 / MinIO / ACR / GHCR
- 不可变保留策略
- 跨区域同步

### Phase 3：多 SoC 真实 Runner

- RK Runner Pool
- Qualcomm Runner Pool
- MediaTek Runner Pool
- SDK 镜像/快照
- 许可证池
- Runner 自动扩缩容

### Phase 4：供应链安全

- Action SHA Pinning
- SBOM
- 漏洞/License 扫描
- Cosign
- SLSA provenance
- OIDC
- Policy Gate

### Phase 5：固件设备实验室

- 设备预约与锁
- 自动烧录
- 串口日志
- 上电测试
- OTA/A-B 验证
- 弱网/断点续传验证

---

## 13. 推荐学习顺序

如果是为了真正理解生产 CI，不建议先背 Actions YAML。

按这个顺序看：

1. [总体架构](docs/architecture.md)
2. [构建、缓存与依赖](docs/build-cache-and-dependencies.md)
3. [多 SoC 与固件](docs/multi-soc-and-firmware.md)
4. [制品、晋级与回滚](docs/artifacts-promotion-and-rollback.md)
5. [Runner 与供应链安全](docs/runner-security-and-supply-chain.md)
6. [故障排查](docs/troubleshooting.md)
7. [新项目接入](docs/onboarding.md)

理解完这几部分，再看 `.github/workflows/`，会比直接背 CI 配置容易得多。
