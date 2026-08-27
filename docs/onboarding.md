# 新项目接入手册

目标：一个新项目接进来时，研发不需要复制粘贴一整套 Workflow，只需要声明“我是什么项目、在哪个平台构建、产物在哪”。

## 1. 接入前准备

先回答 8 个问题：

1. 项目名是什么？
2. 源码目录在哪里？
3. 是否依赖内部库？
4. 目标 SoC 是 generic / RK / Qualcomm / MediaTek 哪类？
5. 目标系统是 Linux 还是 Android？
6. CPU 架构是什么？
7. 使用哪一版工具链/SDK？
8. 最终需要保存哪些文件？

## 2. 在 `ci/projects.json` 增加项目

普通 C++ 示例：

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
      "toolchain": "gcc-14",
      "runner_labels": ["ubuntu-latest"],
      "build_command": "cmake -S . -B build && cmake --build build --parallel 8",
      "artifact_paths": ["build/camera-service"],
      "cache_paths": [".cache/ccache"],
      "cache_key_files": ["CMakeLists.txt", "deps.lock"]
    }
  ]
}
```

## 3. 多 SoC 项目

同一个项目下面挂多个 target：

```text
camera-firmware
  ├─ rk / linux / arm64 / rk-sdk-x
  ├─ qualcomm / android / arm64 / qcom-sdk-y
  └─ mediatek / android / arm64 / mtk-sdk-z
```

不要复制成三个项目，除非它们本身已经是三套完全不同的源码仓库。

## 4. 配 Runner

如果 `runner_labels` 使用：

```json
["self-hosted", "linux", "arm64", "soc-rk"]
```

就必须至少有一台满足这些标签的 Runner 在线。

Runner 上要提前准备：

- 对应 SDK
- 对应编译器
- 必要许可证
- 依赖代理地址
- 足够磁盘/内存
- 清理策略

不要让项目脚本自己安装完整厂商 SDK。

## 5. 本地校验

```bash
python3 scripts/ci/validate_config.py
python3 scripts/ci/dependency_plan.py
python3 scripts/ci/discover_matrix.py
```

这三步分别检查：

- 配置结构是否正确
- 内部依赖是否有环
- 实际会生成哪些构建任务

## 6. 构建命令要求

`build_command` 应该做到：

- 非交互式
- 失败返回非 0
- 不依赖当前用户 HOME 里偷偷存在的文件
- 不写生产环境
- 不直接发布制品
- 尽量可在干净 Runner 重现

构建和发布一定分开。

## 7. 制品路径要求

写真正最终要保留的文件：

```json
"artifact_paths": [
  "out/firmware.img",
  "out/boot.img",
  "out/update.zip"
]
```

不要为了图省事写：

```json
"artifact_paths": ["**/*"]
```

否则可能把源码、缓存、私钥、临时文件一起打包。

## 8. 缓存路径

缓存只填“丢了也能重新生成”的内容。

可以：

- ccache
- sccache
- package manager download cache

不要：

- 最终制品
- 签名私钥
- 工作目录完整快照
- 生产配置

## 9. 新工具链接入

工具链版本必须先有基线，再允许项目引用。

推荐流程：

```text
SDK 下载/接收
 -> 校验 checksum
 -> 安全扫描
 -> 固定版本号
 -> 制作 Runner 镜像/容器
 -> 冒烟编译
 -> 登记 toolchain 名称
 -> 项目配置引用
```

## 10. PR 验收

新增项目的 PR 至少检查：

- catalog validate
- dependency DAG
- 构建矩阵是否符合预期
- 至少一个目标能真实编译
- artifact 打包成功
- manifest 正确
- SHA256 正确

如果是 RK/高通/MTK 专用 Runner，先在受控分支/受信代码上验证，不要让未知 PR 直接拿高权限 Runner。

## 11. 上线前

要补齐：

- 生产制品仓库
- Environment 审批
- 签名/校验
- 保留策略
- 回滚目标
- 发布后验证

CI 构建通过只说明“包被正确构建”，不等于“业务可以安全上线”。
