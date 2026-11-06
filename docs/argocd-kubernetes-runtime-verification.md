# Argo CD / Kubernetes 部署后运行验证

## 目标

CI 构建成功只证明制品构建出来了；GitOps PR 合并只证明期望配置已变更；Argo CD 显示 Synced 也只证明期望状态已同步。它们都不能单独证明 Pod 实际运行了目标镜像。

本流程把以下证据绑定到同一条记录：

- Artifact Contract manifest SHA256；
- GitOps commit revision；
- Argo CD Application 的 Synced / Healthy 状态；
- Kubernetes selector 命中的全部 Pod；
- Pod Ready 状态；
- 容器运行时 status.containerStatuses[].imageID 中的真实 sha256 digest；
- GitHub runtime Deployment ID、工作流运行记录和完整 JSON evidence artifact。

## 工作流

.github/workflows/verify-runtime-deployment.yml 只允许从 main 手动触发，并使用 runtime-dev、runtime-staging 或 runtime-production Environment。

管理员需要先创建 runtime-dev、runtime-staging、runtime-production 三个 GitHub Environment，并在 production 环境配置 Required reviewers、Prevent self-review 和只允许 main 部署。随后在 GitHub Settings → Secrets and variables → Actions 中配置：

- ARGOCD_SERVER：Argo CD HTTPS 地址，不带路径；
- ARGOCD_AUTH_TOKEN：只读 Argo CD token，至少能读取目标 Application；
- KUBECONFIG_BASE64：目标集群只读 kubeconfig 的 Base64 内容，仅授予指定 namespace 的 Pods get/list 权限。

对应 Environment 应配置允许部署的分支；生产环境应配置 Required reviewers、Prevent self-review（如果组织策略支持）和禁止绕过。不要给工作流集群管理员权限。

## 执行前准备

1. Argo CD Application 名称必须准确。
2. namespace 指向工作负载所在命名空间。
3. pod_selector 必须覆盖该 Application 的全部目标 Pod，不能只选一个健康副本。
4. expected_image 必须是 registry/repository@sha256:...，禁止只填 tag。
5. artifact_name 填构建工作流登记的 Artifact Contract 名称。
6. bundle_sha256 填归档记录中的 bundle SHA256。
7. manifest_sha256 填归档记录中的 manifest SHA256。
8. expected_gitops_revision 填 GitOps 仓库完整 commit SHA。
9. kubectl_version 选择与目标集群版本兼容的 kubectl（优先使用相同 minor 版本，遵守 Kubernetes 官方版本偏差策略）。

工作流先从长期归档下载精确 Artifact Contract，校验 bundle SHA256、manifest SHA256、manifest 中的 container_image，并重新验证 bundle 内容、GitHub attestations、Cosign 签名和 SBOM/扫描策略。只有归档里的镜像 digest 与运行时检查输入一致，才继续访问 Argo CD 和 Kubernetes。

工作流读取 Argo CD API 和 Kubernetes API。任何配置缺失、TLS 请求失败、Application 非 Synced/Healthy、revision 不匹配、没有 Pod、Pod 未 Ready、镜像声明不匹配或运行时 imageID digest 不匹配都会失败，不会登记成功 Deployment。

## 运行时验证逻辑

    人工/发布系统触发验证
      -> 读取 Argo CD Application
      -> 校验 Sync=Synced、Health=Healthy、Revision=预期 GitOps SHA
      -> kubectl 按 namespace + label selector 读取全部 Pod
      -> 校验 Pod Running + Ready
      -> 找到目标容器
      -> 比较 spec.containers[].image 与预期 immutable image
      -> 比较 status.containerStatuses[].imageID 中的 digest
      -> 生成 runtime-evidence.json + SHA256
      -> 上传 90 天证据附件
      -> 创建 GitHub Deployment runtime-<env>

image 是 Pod Spec 中声明的镜像；imageID 是容器运行时实际拉取/运行的镜像身份。必须检查 imageID，不能只看 Deployment YAML 或 Pod Spec。

## 边界与失败处理

- 这是只读验证，不执行 Argo CD sync、不 patch Deployment、不重启 Pod、不回滚业务。
- 当前实现验证一个应用中的一个精确业务镜像仓库 digest；如果应用包含多个需要分别追溯的业务镜像，应扩展为多镜像期望清单，不要把 sidecar digest 混同为主业务镜像 digest。
- selector 必须覆盖全部目标 Pod。若 selector 选入其他工作负载，验证会因缺少目标镜像而失败；若 selector 漏掉副本，则会造成证据不完整。
- kubectl 凭据应使用只读 RBAC；Argo CD token 应使用只读权限。
- 运行时 Deployment 是追加式证据记录。Deployment 的 ref 指向 CICD 仓库执行验证的 commit；GitOps revision 单独保存在 payload 中，避免把另一个仓库的 commit 当成本仓库 ref。它不会改变工具链 catalog，也不代表执行了发布。
- 90 天 Artifact 用于保留完整 Pod/imageID 明细；GitHub Deployment payload 保留 revision、digest、manifest、Pod 明细和证据 SHA256。

## 尚需真实环境验收

PR 测试只能验证脚本的输入输出与拒绝逻辑。要形成生产验收，还必须在真实 Argo CD / Kubernetes 环境完成一次成功运行，并确认：

1. Argo CD API 返回的 revision 与 GitOps 提交一致；
2. kubeconfig 只能读取目标 namespace；
3. selector 覆盖全部预期 Pod；
4. 每个目标 Pod 的 imageID digest 与期望值一致；
5. GitHub Deployment 记录和 90 天 evidence artifact 可查阅。
