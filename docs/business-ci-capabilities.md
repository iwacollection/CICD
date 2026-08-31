# 业务 CI Capability 管理

这份文档说明中央 `CICD` 如何在不破坏现有 Core 主链的前提下，逐步补齐业务 CI 能力。

核心原则只有一句：

> **Capability 负责证明业务代码是否满足某类质量要求；Core 负责产生可信生产制品并管理制品生命周期。**

因此新能力不能重新发明第二套 Build、Artifact、Archive、Promotion 或 Rollback。

---

## 1. 平台分层

```text
Enterprise CI Platform
│
├── Core Stable Contract
│   ├── Impact Analysis
│   ├── Dependency DAG
│   ├── Trusted Build
│   ├── Runner Trust Boundary
│   ├── Toolchain Identity
│   ├── Cache Identity
│   ├── Artifact Contract v2
│   ├── Artifact Supply Chain
│   ├── Attestation
│   ├── Archive
│   ├── Promotion
│   └── Rollback
│
└── Business CI Capabilities
    ├── Quality
    ├── Test
    ├── Container
    └── DB Migration
```

Core 已经过真实 `main -> Archive -> dev -> staging -> production -> rollback` 生命周期验证，因此 Capability v1 默认不能修改这些稳定契约。

---

## 2. Capability Ownership Matrix

机器可读的职责声明保存在：

```text
ci/capabilities.json
```

当前 v1：

| Capability | 负责 | 不负责 |
| --- | --- | --- |
| Quality | format / lint / static analysis / coverage command / evidence | 最终 Build、Artifact、Release、Deploy |
| Test | unit / integration / E2E / evidence | 最终 Artifact、生产发布 |
| Container | 临时 image build、Dockerfile policy、image scan、image SBOM、smoke | push 生产 Registry、Archive、Promotion |
| DB Migration | SQL policy、临时 PostgreSQL、migration/rollback/compatibility、schema evidence | 生产 DB apply、生产凭据、生产 DB rollback |

所有 Phase 1 Capability 固定：

```text
runner_class = hosted-only
may_use_self_hosted = false
may_publish_release = false
may_deploy = false
```

这意味着它们不能因为业务仓库传入参数就获得 RK/Qcom/MTK Runner、内网、USB、License 或生产环境权限。

---

## 3. CI Profile

Profile 不是另一套流水线，而是“某类业务通常需要哪些 Capability”的标准组合。

当前：

```text
backend-service
├── quality
├── test
├── container
└── db_migration

container-service
├── quality
├── test
└── container

native-library
├── quality
└── test

firmware
├── quality
└── test
```

Firmware 的 SoC/DAG/SDK/HIL 仍由原来的硬件/Core 主线管理，不能被业务 Capability 取代。

---

## 4. Quality CI

入口：

```text
.github/workflows/reusable-quality.yml
```

业务仓库可以提供：

```text
format_command
lint_command
static_analysis_command
coverage_command
```

例如 Python：

```yaml
jobs:
  quality:
    uses: iwacollection/CICD/.github/workflows/reusable-quality.yml@<40-char-platform-sha>
    with:
      project_name: api
      platform_ref: <same-40-char-platform-sha>
      format_command: uv run ruff format --check .
      lint_command: uv run ruff check .
      static_analysis_command: uv run mypy .
      coverage_command: uv run pytest --cov --cov-report=xml:$CI_EVIDENCE_DIR/coverage.xml
```

中央 Workflow 负责：

- Hosted Runner；
- exact platform SHA；
- 命令失败即失败；
- 标准 `CI_EVIDENCE_DIR`；
- 30 天 Evidence Artifact。

它不会生成生产 Artifact。

---

## 5. Test CI

入口：

```text
.github/workflows/reusable-test.yml
```

阶段：

```text
setup
  ↓
unit
  ↓
integration（可选）
  ↓
E2E（可选）
```

业务命令可以把 JUnit、coverage、日志等写入：

```text
$CI_EVIDENCE_DIR
```

Capability 中为了测试而编译临时 test binary 是允许的，但这些 bytes **不能成为 production Artifact**。最终生产制品仍然只能由 Core Build 产生。

---

## 6. Container CI

入口：

```text
.github/workflows/reusable-container.yml
```

流程：

```text
Dockerfile
   ↓
中央 Dockerfile Policy
   ↓
Ephemeral docker build
   ↓
Non-root policy
   ↓
Trivy image scan
   ↓
CycloneDX image SBOM
   ↓
中央 Supply-chain Policy
   ↓
业务 smoke_command
   ↓
Image evidence
```

重要边界：

```text
NO docker login
NO docker push
NO package write
NO id-token write
NO Release
NO Promotion
```

Container Capability 只证明“这个业务镜像能安全构建并启动”。如果以后真正发布 OCI Image，仍需接入 Core 的不可变 digest、Attestation、Archive/Registry 与 Promotion 契约，而不是在这里偷偷增加一条发布通道。

基础镜像继续受中央 `ci/supply-chain-policy.json` 管理，必须固定完整 `@sha256:` digest。

---

## 7. DB Migration CI

入口：

```text
.github/workflows/reusable-db-migration.yml
```

当前数据库基线：

```text
PostgreSQL 16
官方镜像
完整 sha256 digest 固定
临时 ci_test database
固定测试账号/密码
不读取生产 Secret
```

流程：

```text
Migration files
   ↓
Static destructive-SQL policy
   ↓
Ephemeral PostgreSQL
   ↓
migration_command
   ↓
Schema evidence
   ↓
compatibility_command（可选）
   ↓
rollback_command（可选）
   ↓
Rollback schema evidence
```

中央策略当前直接拒绝高置信度危险操作：

```text
DROP DATABASE
DROP SCHEMA
TRUNCATE
DELETE without WHERE
UPDATE without WHERE
```

策略文件：

```text
ci/db-migration-policy.json
```

这不是说所有 `ALTER TABLE` 都安全。大表 DDL、在线变更、锁表时间、数据回填等仍需要真实数据库规模和生产 Change Policy；v1 只先阻止最明显的灾难性 SQL，并验证 migration 能在干净 PostgreSQL 上执行。

---

## 8. Evidence，不是第二套 Artifact

每个 Capability 都会保存：

```text
capability.json
+ 该能力自己的报告/日志/SBOM/schema
```

这些叫：

```text
Evidence
```

不是：

```text
Production Artifact
```

区别：

```text
Evidence
  -> 证明检查执行过、结果是什么

Artifact Contract v2
  -> 可以长期归档、晋级和回滚的生产制品
```

不要把测试报告拿去 Promotion，也不要让 Capability 自己 Archive。

---

## 9. 和多 SoC / RK / 高通 / MTK 的关系

不会冲突。

```text
Firmware Product
│
├── Quality Capability       Hosted
├── Test Capability          Hosted-safe tests
│
└── Existing SoC Core
    ├── projects.json
    ├── toolchains.json
    ├── hardware-profiles.json
    ├── hardware-rollout.json
    ├── Vendor SDK
    ├── License
    ├── HIL
    └── Artifact Contract v2
```

不可信 PR 的 Quality/Test 仍然只跑 Hosted Runner。

真实 RK/Qcom/MTK SDK/HIL 继续遵守原有 Self-hosted 信任边界，Capability v1 没有权限进入硬件执行面。

---

## 10. Capability Smoke

为了避免“YAML 写了但没人真正调用”，仓库增加：

```text
.github/workflows/capability-smoke.yml
```

只有 Capability 自身代码变化时运行，真实调用：

```text
Reusable Quality
Reusable Test
Reusable Container
Reusable DB Migration
```

使用仓库内小型 fixture 验证执行契约。

它不产生长期 Release，也不进入任何环境。

---

## 11. 下一阶段如何扩展

等 Phase 1 稳定后，再按同样 Ownership 模型增加：

```text
API Contract CI
Performance Regression CI
Native Safety / ASan / UBSan / Fuzz CI
Docs CI
Consumer Compatibility CI
```

每加一个能力，都必须先回答：

1. 它属于 Core 还是 Capability？
2. 是否重复已有职责？
3. 是否需要 Self-hosted？如果不需要就禁止。
4. 输出是 Evidence 还是 Production Artifact？
5. 是否真的需要发布/部署权限？默认答案应为“不需要”。

这样 Capability 可以持续增加，而不会把已经验证过的 Core 重新变成一套混乱的大流水线。
