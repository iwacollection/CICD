# Runner、安全与供应链治理

## 1. Self-hosted Runner 是生产资产，不是一台随便的构建机

普通 GitHub Hosted Runner 是一次性环境；Self-hosted Runner 往往长期存在，并且可能拥有：

- 内网访问能力
- 私有依赖仓库权限
- 厂商 SDK
- 编译许可证
- 云身份
- 签名服务访问权
- 真机/烧录设备

因此安全边界必须比普通 CI 更严格。

## 2. 不要让不可信 PR 直接跑在高权限 Self-hosted Runner

这是最重要的规则之一。

如果外部 PR 可以修改脚本，然后直接在带内网权限的 Runner 上执行：

```bash
curl ...
cat $SECRET
ssh ...
```

那 CI 就变成了远程代码执行入口。

推荐：

```text
不可信 PR
 -> Hosted / Sandbox Runner
 -> 静态检查、单元测试

合并后的受信代码
 -> Self-hosted SoC Runner
 -> 厂商 SDK / 内部资源
```

如果必须 PR 阶段使用 Self-hosted Runner，应配合审批、只读凭据、网络隔离和短生命周期 Runner。

本仓库当前执行边界是：PR 的 Build Matrix 与 Reusable Build 均强制使用 GitHub Hosted Runner，并且 PR Build Job 不拥有 OIDC/Attestation 写权限；目录中的 Self-hosted 标签只在受信任的 main/手工执行中生效。仓库或组织侧仍应保持“公共仓库不可访问 Self-hosted Runner Group”的默认限制。

## 3. Runner 最好是短生命周期

优先级：

1. 每个 Job 创建临时 Runner，结束后销毁。
2. 使用虚拟机/容器快照恢复干净状态。
3. 长期 Runner 至少每次清理 workspace、临时凭据和挂载。

长期复用同一工作目录最容易出现：

- 上一个项目文件残留
- 错误命中旧构建结果
- 凭据残留
- 磁盘慢慢打满
- 权限互相污染

## 4. Runner 池按能力隔离

推荐池：

```text
hosted-general
linux-cpp
android-general
soc-rk
soc-qualcomm
soc-mediatek
signing
hardware-lab
```

签名 Runner 不应同时承担普通编译；真机实验室也不应拥有生产签名私钥。

## 5. 凭据放哪里

不要：

- 写进仓库
- 写进 Dockerfile
- 放在共享目录明文文件
- 写进构建日志
- 长期保存云 Access Key

优先：

- GitHub OIDC -> 云短期身份
- GitHub Environment Secrets
- Vault / KMS / HSM
- GitHub App Installation Token
- 厂商许可证服务

## 6. 权限最小化

每个 workflow 顶层显式写 `permissions`。

构建一般只需要：

```yaml
permissions:
  contents: read
```

不要默认给 `write-all`。

只有真正上传 Release、写包仓库、创建 deployment 的 Job 才单独增加权限。

## 7. 生产 Environment

GitHub Environment 可用来做：

- production 审批
- 指定 reviewer
- 环境级 secrets
- 分支限制

因此构建 Job 不应该拿生产凭据。只有 promotion/deploy Job 进入 `production` Environment 后才拿到生产权限。

## 8. 第三方 Action 供应链

`uses: vendor/action@v4` 很方便，但 tag 理论上可以移动。

更严格的生产环境应该把关键 Action pin 到完整 commit SHA，并通过 Dependabot/Renovate 受控升级。

同时建立 allowlist：只允许经过审查的 Action。

## 9. 依赖混淆

如果公司内部包叫：

```text
company-common
```

而公网有人发布了同名更高版本，构建工具配置不当可能去公网下载恶意包。

治理：

- 私有 namespace/scope
- 企业代理仓库
- 明确 repository 优先级
- lock 文件
- 禁止未审计的新源

## 10. 制品签名

SHA256 只能说明“文件没变”，不能证明“谁构建的”。

生产建议增加数字签名：

```text
artifact digest
 + build identity
 + signature
```

验证方既检查 digest，也检查签名来源。

## 11. SBOM

SBOM（软件物料清单）就是“这个制品里到底用了哪些组件”的清单。

发生 Log4j/OpenSSL 类漏洞时，不用挨个问项目组，而是查：

```text
哪些生产制品包含 vulnerable component X?
```

## 12. 网络策略

Runner 不需要访问整个公司网络。

按用途放行：

```text
代码平台
依赖仓库
制品仓库
许可证服务器
必要的测试环境
```

普通编译 Runner 不应默认能 SSH 到生产主机。

## 13. 审计

至少保留：

- 谁触发
- 哪个 commit
- 哪个 Runner
- 用了哪个工具链
- 下载了什么依赖
- 生成什么 digest
- 谁审批生产
- 哪个 digest 被发布
- 是否回滚

这也是为什么本平台把 manifest 当成一等公民。
