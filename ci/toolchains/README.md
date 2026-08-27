# Toolchain Registry（工具链登记）

这里记录“逻辑工具链版本”和实际 Runner/镜像之间的对应关系。

## 原则

项目只能引用明确版本，例如：

```text
gcc-host
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

禁止项目引用：

```text
latest
current
new
final
```

## 建议登记字段

生产中可以把下面信息维护到 CMDB/配置中心：

```text
toolchain_name
vendor
sdk_version
compiler_version
base_os
container_image_digest / runner_image_id
sdk_checksum
license_mode
supported_soc
supported_target_os
supported_arch
owner
created_at
retire_at
```

## 示例

| 逻辑名称 | 用途 | Runner 标签 | 备注 |
| --- | --- | --- | --- |
| gcc-host | 普通 Linux C/C++ | ubuntu-latest | 仓库示例使用 |
| rk-sdk-2026.08 | RK Linux/Android | self-hosted, soc-rk | 示例登记位，需企业 SDK |
| qcom-sdk-2026.08 | Qualcomm Android | self-hosted, soc-qualcomm | 示例登记位，需企业 SDK/授权 |
| mtk-sdk-2026.08 | MediaTek Android | self-hosted, soc-mediatek | 示例登记位，需企业 SDK/授权 |

## 升级流程

```text
新 SDK
 -> 建新版本
 -> 冒烟构建
 -> 选几个项目双跑对比
 -> 正式开放
 -> 项目逐步切换
 -> 旧版本进入维护期
 -> 到期下线
```

不要原地覆盖旧 SDK。否则历史 commit 即使源码没变，也无法复现当年的构建结果。
