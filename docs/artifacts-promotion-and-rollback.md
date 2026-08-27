# 制品、晋级与回滚

## 1. 最重要的规则

**测试、预发、生产必须使用同一个已经构建好的制品。**

错误流程：

```text
commit A
 -> 测试重新编译 -> artifact X
 -> 生产重新编译 -> artifact Y
```

即使源码 commit 一样，X 和 Y 也可能因为依赖源、时间、工具链、环境变量不同而不是同一份字节。

正确流程：

```text
commit A
 -> CI 构建一次
 -> artifact digest = abc123
 -> 测试验证 abc123
 -> 预发晋级 abc123
 -> 生产审批后晋级 abc123
```

## 2. 本仓库的制品内容

`scripts/ci/package_artifact.py` 会生成：

```text
<project>-<soc>-<os>-<arch>-<sha>.tar.gz
<project>-<soc>-<os>-<arch>-<sha>.tar.gz.sha256
<project>-<soc>-<os>-<arch>-<sha>.manifest.json
```

manifest 记录：

- 源码仓库
- Git commit SHA
- Workflow run id / attempt
- SoC
- 目标系统
- CPU 架构
- 工具链版本
- 制品 SHA256
- 制品大小
- 被打进包里的文件

## 3. 为什么要 manifest

没有 manifest 的文件名只告诉你“它叫什么”，不能证明“它从哪里来”。

事故时你需要回答：

- 这个生产包是哪次提交构建的？
- 谁的流水线构建的？
- 用的哪版 SDK？
- RK 还是高通版本？
- SHA256 是什么？
- 当前生产到底运行哪一个 digest？

manifest 就是制品身份证。

## 4. SHA256 的作用

下载、复制、跨区域同步以后都重新算 SHA256。

如果：

```text
expected = abc123
actual   = def456
```

直接停止，不允许继续发布。

这能发现：

- 网络传输损坏
- 文件被误覆盖
- 人工替换包
- 存储异常
- 发布拿错版本

## 5. 制品仓库怎么选

小规模/学习环境：

- GitHub Actions Artifact

生产环境：

- JFrog Artifactory
- Sonatype Nexus
- S3 / MinIO
- Azure Blob / ACR
- GHCR（OCI Artifact）

真正生产的制品仓库应支持：

- 不可变版本
- 保留策略
- 权限隔离
- 审计日志
- 跨区域复制
- 生命周期管理
- checksum/digest

GitHub Actions Artifact 更适合流水线中间结果，不建议把它作为唯一长期生产归档。

## 6. 环境晋级

建议：

```text
Build
  |
  v
Artifact Repository
  |
  +--> dev     自动
  |
  +--> staging 自动/审批
  |
  +--> prod    人工审批 + Environment Protection
```

所谓“晋级”不是重新打包，而是让某个环境允许使用这个 digest。

## 7. 生产审批

审批应该基于具体制品，而不是一句“发布 v1.2”。

审批页面至少展示：

- Project
- Version
- Source commit
- Digest
- Build run
- Test result
- SBOM/扫描结果
- 变更单
- 回滚目标

## 8. 回滚

回滚不应该触发重新编译。

```text
prod current = digest-B
prod previous = digest-A

回滚：
把生产引用从 digest-B 切回 digest-A
```

因此历史生产制品不能随着普通缓存清理一起被删除。

## 9. 数据库/固件为什么更复杂

应用包回滚不代表数据一定能回滚。

### 数据库

如果新版本执行了不可逆 schema migration，应用回滚可能失败。因此数据库变更需要 forward/backward compatibility 设计。

### 固件

设备升级还要考虑：

- Bootloader 是否向后兼容
- 分区布局有没有变化
- Anti-rollback
- 数据分区格式
- OTA 中断恢复
- A/B 分区

CI 能保证“包没变”，但系统级回滚能力仍要由产品架构保证。

## 10. 进一步增强

生产建议逐步补：

```text
SHA256
  + SBOM
  + Vulnerability Scan
  + License Scan
  + Cosign Signature
  + SLSA Provenance
  + Policy Gate
```

最终目标是：任何一个生产二进制都能从 digest 反查到源码、依赖、构建环境和审批记录。
