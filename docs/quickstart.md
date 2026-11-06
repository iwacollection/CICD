# 开箱即用：本地验证与完整 DAG 示例

本仓库提供可运行的合成项目，不需要先接入真实业务代码。样例覆盖 Go、Java、C/C++ 工具链，以及四层 C++ 依赖交付链。

## 一、准备环境

- Git
- Python 3.10+
- Docker Engine（需要实际运行容器构建时）
- 可访问 GHCR 的网络

## 二、先验证平台配置和自动化测试

在仓库根目录执行：

```bash
python3 scripts/ci/toolchain_catalog.py
python3 scripts/ci/toolchain_versions.py
python3 scripts/ci/validate_config.py
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/supply_chain_policy.py
python3 -m unittest discover -s tests -v
```

这一步验证工具链目录、版本选择规则、项目依赖图、供应链策略和平台单元测试；它不会伪装成容器镜像构建或真实硬件验收。

## 三、查看完整四层构建计划

```bash
python3 scripts/ci/dag_plan.py \
  --projects-json '["hello-lib","hello-cpp","hello-service","hello-release"]' \
  --lane full
```

预期依赖层级：

```text
L0  hello-lib       构建静态库
       |
L1  hello-cpp       接收并链接 L0 的静态库，生成可执行文件
       |
L2  hello-service   接收并运行 L1 的可执行文件，生成服务验证制品
       |
L3  hello-release   接收 L2 的制品，生成最终发布制品
```

每一层只消费前序任务上传的 Artifact Contract v2 制品。平台会校验来源提交、仓库身份、目标平台、清单和 SHA256；缺少依赖或校验失败时，下游构建不会继续。

## 四、运行 GitHub Actions 完整构建

推送分支或创建 PR 后，Build Matrix 会自动分析改动并构建受影响项目。要主动验证全部启用项目，可在 GitHub Actions 页面手动运行 **Build Matrix** 的 `workflow_dispatch`（仅允许 main 分支），它会执行 full lane。

工具链镜像的构建、漏洞/许可证扫描和 SBOM 由 **Toolchain Supply Chain** 工作流负责。planned 镜像只在 PR 验证，不会发布；candidate 镜像发布仍受分支、权限和审核策略约束。

## 五、硬件项目说明

Rockchip、Qualcomm、MediaTek 示例属于硬件接入模板。没有真实 self-hosted Runner、SDK、许可证和设备时，只能运行 hosted PR 预检；不能把模拟构建说成真实 SDK/HIL（硬件在环）验收通过。
