# CI 故障排查手册

排 CI 故障不要一上来“清缓存重跑”。先判断失败在哪一层。

## 1. 总体排查顺序

```text
触发没触发
 -> Job 有没有排队
 -> Runner 有没有接到任务
 -> 代码有没有检出
 -> 依赖有没有拿到
 -> 编译有没有开始
 -> 测试是否失败
 -> 打包是否失败
 -> 上传是否失败
 -> 晋级/发布是否失败
```

## 2. Job 一直排队

重点看：

- Runner 是否在线
- 标签是否匹配
- 并发额度是否用完
- Runner 是否被其他超长构建占满
- 组织/仓库 Runner Group 是否允许当前仓库使用

如果多 SoC 任务只卡某一类，例如 `soc-qualcomm`，优先查对应 Runner 池，而不是查源码。

## 3. 构建突然从 20 分钟变成 1 小时

先拆阶段耗时：

```text
排队时间
Checkout
依赖下载
Configure
Compile
Link
Test
Package
Upload
```

典型判断：

- 排队变长：Runner 容量问题。
- 依赖下载变长：代理仓库/网络/缓存失效。
- Compile 变长：ccache miss、全量重编译、CPU/IO 降速。
- Link 变长：大目标、内存不足、磁盘慢。
- Upload 变长：制品过大、跨区域网络。

不要只看总耗时。

## 4. 两个 Job 同时跑后缓存坏了

症状：

- 解压失败
- 包校验失败
- 头文件与库版本对不上
- 同一提交偶发成功/失败

先问：是不是多个 Job 共写了同一个目录？

处理：

1. 停止共享裸目录写入。
2. 按 project/soc/os/arch/toolchain/lock hash 隔离 key。
3. 依赖走 Nexus/Artifactory 这类有并发控制的服务。
4. 缓存只做加速，校验失败时允许无缓存重建。

## 5. 同一个提交有时能编过、有时不能

优先怀疑“不确定输入”：

- `latest` SDK
- 未锁定第三方版本
- PATH 顺序不同
- Runner 上残留旧文件
- 时区/时间参与生成
- 并发写共享目录
- 编译器版本漂移

把所有输入逐步固定进 manifest/lock 文件。

## 6. `No space left on device`

不仅看磁盘容量，也看 inode。

```bash
df -h
df -i
du -xhd1 /path | sort -h
```

CI 常见大户：

- workspace
- Docker layer
- Android out
- Yocto tmp/cache
- ccache
- 下载的 SDK
- 未清理 artifact

长期治理：磁盘配额、TTL、定期清理、短生命周期 Runner。

## 7. 文件删了磁盘没释放

可能进程仍然打开已删除文件。

```bash
lsof +L1
```

找到持有进程后，安全重启对应进程/容器。不要只继续 `rm`。

## 8. 编译进程被 `Killed`

优先查 OOM：

```bash
dmesg -T | grep -i -E 'oom|killed process'
```

大型 C++/Android 并行度过高会瞬间吃满内存。

止血可以降低并行：

```bash
cmake --build build --parallel 4
make -j4
```

长期应按 Runner 内存建立合理并发，而不是所有机器都 `-j$(nproc)`。

## 9. 链接报 undefined reference

常见原因：

- 库顺序
- ABI 不一致
- Debug/Release 混用
- arm64 与 x86_64 混用
- C++ 标准库不同
- 头文件版本和二进制库版本不一致

多 SoC 场景尤其要先确认你拿到的是目标平台对应的依赖包。

## 10. 厂商 SDK 在一台 Runner 能编，另一台不能

对比：

- SDK digest/version
- OS 镜像
- compiler version
- PATH
- license
- locale
- kernel capability
- disk mount
- Docker/容器 runtime

不要把“服务器名字”当成环境版本。Runner 应来自可重建的镜像或有严格基线。

## 11. 上传 Artifact 失败

检查：

- 路径是否匹配到文件
- 单文件/总大小
- 网络
- GitHub 服务状态
- retention 配置

本平台打包脚本如果没有匹配到任何制品会直接失败，避免上传一个空包后误以为构建成功。

## 12. 生产发布拿错包

如果发布流程允许手填文件路径，很容易发生。

正确做法：

- 输入 source run id / artifact id
- 校验 manifest
- 校验 SHA256
- 生产 Environment 审批
- 发布记录保存 digest

不要让发布人从桌面手工选一个 `final_v2_new.zip`。

## 13. Pipeline 中间失败怎么办

不是所有步骤都适合“从中间继续”。

可以安全复用的：

- 已发布不可变内部依赖
- 已校验的构建缓存
- 已上传并校验的最终制品

不建议复用的：

- 半写入 workspace
- 未完成的共享目录
- 没有 checksum 的中间包

如果失败点之前的结果没有不可变边界，宁可重新执行该阶段。

## 14. CI 控制面挂了怎么办

如果使用 GitHub Actions/Jenkins 等控制面，生产发布不能只依赖“控制面活着”。

关键制品必须已经落到独立制品仓库。控制面恢复后可以继续编排，但历史生产包不能跟着 Jenkins Workspace 一起丢。

## 15. 事故处理模板

```text
现象
 -> 哪个项目/平台失败？影响多少任务？

判断
 -> 调度、环境、依赖、编译、打包、上传还是发布？

证据
 -> Job log、Runner 指标、依赖仓库日志、digest

止血
 -> 停止错误发布、切备用 Runner、降低并发、临时绕过坏节点

恢复
 -> 重建安全阶段 / 复用已验证制品

验证
 -> checksum、测试、目标环境健康检查

长期治理
 -> 基线、容量、缓存、权限、监控、自动化门禁
```
