# 容器化构建环境：解决 CI 环境依赖问题

这套平台默认采用 **Container First（容器优先）**，但不是“所有 Job 都必须跑 Docker”。

核心目标是把 Runner 和编译环境拆开：

```text
Runner
├── 操作系统内核
├── CPU / 内存 / 磁盘
├── Docker
├── 必要硬件（USB、GPU、烧录器、加密卡）
└── 少量宿主机级驱动

Toolchain Image
├── gcc / clang
├── CMake / Ninja / ccache
├── JDK / Gradle
├── Android SDK / NDK
├── Python / Node / Go / Rust
├── RK / Qualcomm / MTK 用户态 SDK
└── 构建需要的系统库

Project
├── 源码
├── deps.lock / toolchain.lock
├── 构建命令
├── 测试命令
└── 制品声明
```

这样一台新 Runner 接入时，不需要人工重新安装几十个编译环境。只要 Runner 能运行指定容器，项目就能获得同一套用户态构建环境。

---

## 1. Docker 能解决什么

### 1.1 编译器版本漂移

传统做法：

```text
runner-a: gcc 11 + cmake 3.22
runner-b: gcc 13 + cmake 3.28
runner-c: 某个人昨天手工升级过
```

同一 commit 调度到不同 Runner，可能得到不同结果。

容器化后：

```text
project -> gcc-toolchain@sha256:abc...
```

调度到哪个兼容 Runner 都使用同一个镜像。

### 1.2 系统依赖冲突

例如项目 A 要 libssl 1.x，项目 B 要另一版本；Android 项目和普通 Linux C++ 又需要完全不同的依赖。

不要继续往 Runner 上叠软件包。不同构建镜像独立维护即可。

### 1.3 新 Runner 扩容慢

传统 Runner 扩容往往需要：装 JDK、装 gcc、装 CMake、解 SDK、改 PATH、装 Python 包、再验证。

容器优先后，Runner 的标准可以收敛成：

```text
OS + Docker + Runner Agent + 必要驱动
```

### 1.4 环境污染

Job A `pip install`、`npm install -g`、修改 `/usr/local`，可能影响后面的 Job。

容器退出后用户态环境直接销毁，污染面显著降低。

### 1.5 历史构建复现

只保存源码 commit 不够。

至少应保存：

```text
source commit
+ dependency lock
+ toolchain logical version
+ container image digest
+ build command
```

镜像 digest 比 `latest`、`v1` 这种标签可靠，因为 digest 指向确定的镜像内容。

---

## 2. Docker 不能解决什么

Docker 不是虚拟机，也不是所有硬件问题的答案。

下面这些通常仍然依赖 Runner 宿主机：

### 2.1 内核和驱动

容器共享宿主机内核。

GPU Driver、某些 USB Driver、特殊 PCIe 驱动、内核模块不能简单靠普通 Dockerfile 完全隔离。

### 2.2 真机烧录

```text
编译固件       -> 很适合容器
签名           -> 可容器化，但密钥必须外置
USB 烧录       -> 通常需要专用 Runner / Device Lab
串口测试       -> 通常需要专用 Runner / Device Lab
重启设备       -> 真实硬件控制
健康检查       -> 真实硬件控制
```

不要为了“全 Docker”把设备权限粗暴开放给所有构建容器。

### 2.3 厂商许可证

Qualcomm / MediaTek 或某些商业编译器可能需要许可证服务器、USB Dongle 或受限制 SDK。

镜像可以封装 SDK 的用户态部分，但授权信息不能直接烘焙进镜像。

### 2.4 CPU 架构

x86_64 Runner 不能天然高效执行所有 arm64 用户态构建镜像。

可以用 QEMU 做部分跨架构任务，但大型 Android/BSP 构建通常更适合对应架构或厂商验证过的 Runner。

---

## 3. 本平台的执行模型

`ci/projects.json` 中每个 target 可以选择：

```json
{
  "execution_mode": "container",
  "container_image": "registry.example.com/ci/rk-sdk@sha256:..."
}
```

或者由平台仓库里的 Dockerfile构建：

```json
{
  "execution_mode": "container",
  "container_dockerfile": "docker/toolchains/gcc-host/Dockerfile",
  "container_image": ""
}
```

无法容器化的特殊任务才使用：

```json
{
  "execution_mode": "host"
}
```

原则：**host 是例外，不是默认。**

---

## 4. 推荐的生产结构

```text
                         Toolchain Image Pipeline
                                  |
               Dockerfile / SDK Definition Change
                                  |
                                  v
                         Build Toolchain Image
                                  |
                     Test / Scan / Generate SBOM
                                  |
                                  v
                          Push Image Registry
                                  |
                                  v
                       immutable image digest
                                  |
                +-----------------+-----------------+
                |                 |                 |
                v                 v                 v
              RK CI          Qualcomm CI          MTK CI
                |                 |                 |
        Self-hosted Runner Self-hosted Runner Self-hosted Runner
                |                 |                 |
                +-------- Docker Build/Test -------+
                                  |
                                  v
                          Immutable Artifact
```

项目流水线不应该每次都重新安装工具链。

更成熟的方式是：

```text
工具链镜像：低频构建
项目源码：高频构建
```

例如：

```text
rk-sdk-2026.08
 -> ghcr.io/company/rk-build@sha256:111...

qcom-sdk-2026.08
 -> registry.company/ci/qcom-build@sha256:222...

mtk-sdk-2026.08
 -> registry.company/ci/mtk-build@sha256:333...
```

项目只引用 digest。

---

## 5. 镜像为什么不能使用 latest

错误：

```text
company/rk-sdk:latest
```

今天和下周的 `latest` 可以不是同一份镜像。

更好的方式：

```text
company/rk-sdk:2026.08
```

生产发布最稳妥：

```text
company/rk-sdk@sha256:<digest>
```

逻辑版本方便人理解，digest 用于机器保证不可变。

Toolchain Registry 应同时保存两者。

---

## 6. 镜像本身怎么升级

不要直接覆盖旧工具链。

```text
旧版本
rk-sdk-2026.08
        |
        | 新 SDK / 编译器
        v
新版本
rk-sdk-2026.09-rc1
        |
        +-> 工具链镜像自测
        +-> 代表项目双跑
        +-> 比较编译结果 / 测试结果 / 大小 / 性能
        +-> 安全扫描
        +-> 生成 SBOM
        |
        v
rk-sdk-2026.09
        |
        +-> 新项目默认
        +-> 老项目逐步迁移
        |
        v
旧版本维护期 -> 到期下线
```

历史版本在保留期内不能被原地覆盖。

---

## 7. 缓存和 Docker 的关系

Docker 镜像解决“环境一致性”，缓存解决“速度”。

两者不要混为一谈。

```text
Toolchain Image
 -> 固定编译环境

Dependency Cache
 -> Maven/npm/pip/Gradle/第三方源码下载加速

Compile Cache
 -> ccache/sccache 编译结果复用

Artifact Repository
 -> 保存最终可发布制品
```

当前 C++ 示例已经把 `ccache` 放进工具链镜像，并把 cache 目录挂在源码工作目录下，由 Actions Cache 恢复和保存。

镜像损坏不应该静默退回另一套宿主机环境；缓存损坏则最多应该导致构建变慢，而不能改变正确性。

---

## 8. 大型 SoC SDK 镜像怎么处理

RK/Qualcomm/MTK 的 SDK 很大，可能几十 GB。

不要简单做成一个巨型层。

建议分层：

```text
Base OS
  |
  +-- common-build-base
         gcc / python / java / repo / git / cmake
  |
  +-- android-common
         Android 通用依赖
  |
  +-- rk-sdk
  +-- qcom-sdk
  +-- mtk-sdk
```

同时配合：

- Runner 本地镜像缓存
- 内网 Registry
- Registry Proxy / Mirror
- 分层稳定的 Dockerfile
- SDK 大文件尽量放靠前且低频变化的层
- 不把源码 COPY 到工具链镜像

这样项目源码变化不会导致几十 GB SDK 层失效。

---

## 9. 安全边界

### 禁止把秘密写进镜像

不要：

```dockerfile
ENV SIGN_KEY=...
COPY production-key.pem /root/key.pem
```

签名密钥应来自 KMS/HSM/Vault/CI Secret，并且只在真正签名阶段短时获得。

### Self-hosted Runner 上的 Docker Socket 是高权限资源

能够访问 `/var/run/docker.sock` 的不可信代码，通常可以进一步控制宿主机。

因此：

```text
外部 PR / Fork PR
    -> GitHub-hosted 或强隔离 Runner

受信任主分支固件构建
    -> 专用 Self-hosted Runner

生产签名 / 真机发布
    -> 更高隔离等级的专用 Runner
```

不能因为“用了 Docker”就认为 Self-hosted Runner 自动安全。

---

## 10. 推荐最终形态

```text
研发提交代码
     |
     v
中央 CI 读取项目声明
     |
     v
选择 Runner 能力
     |
     v
拉取锁定 digest 的 Toolchain Image
     |
     v
恢复依赖/编译缓存
     |
     v
Docker 内 Build + Test
     |
     v
Runner 外 Package + Manifest + SHA256
     |
     v
Artifact Repository
     |
     v
后续测试 / staging / production 使用同一制品
```

这套设计的核心不是“Docker 化率 100%”，而是：

> **能固定在镜像里的环境全部固定进镜像；必须依赖内核、驱动、许可证或真实硬件的能力留给专用 Runner。**

这样既能解决环境依赖漂移，也不会为了容器化破坏真实固件生产链路。
