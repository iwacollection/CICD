# 多 SoC 与固件构建设计

## 1. 目标

同一套 CI 要能覆盖：

- 普通 Linux C/C++ 应用
- Android Native / HAL / Vendor 组件
- Linux BSP / Buildroot / Yocto 类固件
- 瑞芯微 RK
- Qualcomm 高通
- MediaTek 联发科
- 后续新增其他 SoC

重点不是“把三家厂商命令写在一个脚本里”，而是建立统一治理层。

## 2. 什么统一，什么隔离

统一：

- 触发方式
- 代码审查
- 依赖策略
- 构建元数据
- 日志格式
- 制品命名
- SHA256
- 安全门禁
- 晋级与回滚

隔离：

- 厂商 SDK
- 编译器版本
- BSP
- Android/Kernel 源码树
- 签名工具
- 许可证
- USB/烧录设备
- Runner

## 3. Runner 标签

示例：

```text
self-hosted + linux + arm64 + soc-rk
self-hosted + linux + arm64 + soc-qualcomm
self-hosted + linux + arm64 + soc-mediatek
```

工作流根据配置选择 Runner，而不是根据服务器名选择。

错误做法：

```text
runs-on: build-server-03
```

正确思路：

```text
runs-on: [self-hosted, linux, arm64, soc-rk]
```

服务器可以替换，只要重新挂相同能力标签即可。

## 4. SDK 版本必须固定

不要使用：

```text
/opt/rk-sdk/latest
```

应该使用逻辑版本：

```text
rk-sdk-2026.08
qcom-sdk-2026.08
mtk-sdk-2026.08
```

工具链版本进入构建唯一键和 manifest。以后发现某个编译器有问题，可以反查所有受影响制品。

## 5. SDK 放哪里

按优先级：

1. 版本化构建容器镜像：SDK 能容器化时优先。
2. 版本化 Runner 镜像/磁盘快照：SDK 太大或依赖宿主机能力时使用。
3. 只读共享 SDK：需要严格版本目录、校验和和权限控制。

不要每个 Job 从公网/网盘重新下载几十 GB SDK。

## 6. C++ 能不能跑容器

可以。C++ 编译器、CMake、Ninja、Conan、ccache 都很适合放进容器。

但嵌入式构建不一定全部适合容器化：

- 厂商许可证绑定宿主机
- 需要 USB 烧录
- 需要特殊内核模块
- SDK 本身假定固定发行版/目录结构
- 构建依赖特权操作

这种情况不要为了“容器化”硬套容器，使用隔离好的 Self-hosted Runner 更实际。

## 7. Android 与 Linux 的差别

### Android

可能涉及：

- Android 构建系统
- Vendor 分区
- Boot / Vendor Boot / Super Image
- APK/APEX/Native 库
- 平台签名
- OTA 包

### Linux 固件

可能涉及：

- Bootloader
- Kernel
- Device Tree
- RootFS
- Buildroot / Yocto
- 分区镜像
- recovery/升级包

CI 平台不需要理解每个厂商脚本内部细节，但必须知道最终输出是什么，并统一收口为不可变制品。

## 8. 一个固件制品应该带什么

至少：

```text
firmware.tar.gz
firmware.tar.gz.sha256
firmware.manifest.json
```

生产进一步建议：

```text
SBOM
签名
构建来源证明
版本说明
分区清单
刷写说明
兼容硬件型号
```

## 9. 签名不要放在普通编译环境

私钥不应该长期放在 Runner 文件系统。

更合理：

```text
普通构建
  -> 生成 unsigned artifact
  -> 安全签名阶段
  -> HSM/KMS/签名服务
  -> signed artifact
```

签名阶段独立权限、独立审批、独立审计。

## 10. 真机测试

固件 CI 的最后一公里经常不是“编译通过”，而是“设备能不能起来”。

可以建立设备实验室：

```text
Artifact
  -> 设备预约
  -> 烧录
  -> 上电
  -> 串口日志
  -> 健康检查
  -> 网络测试
  -> 功能测试
  -> 结果回传
```

设备应该按型号/SoC/板卡版本打标签，并做独占锁，避免两个任务同时烧同一块板。

## 11. 弱网/海外工厂场景

不要让海外 Runner 每次跨国拉完整 SDK 和所有依赖。

采用：

- 区域依赖代理
- 区域制品缓存
- 断点续传
- SHA256 校验
- 先传 manifest，再按缺失对象拉取
- 最终制品内容寻址

核心思想是“存储转发 + 校验”，不是依赖一次长连接把所有东西推过去。
