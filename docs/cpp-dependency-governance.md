# C++ 依赖治理与 Conan Lockfile

## 当前阶段的目标

在现有不可变 GCC 工具链、依赖 DAG、ccache 和 Artifact Contract v2 之上，增加可重复的 C++ 第三方依赖解析。此阶段先验证 Ubuntu 26.04 LTS + Linux 7.0 headers + Linux x86_64 + GCC 13.4.0 + C++17，不宣称已经完成企业级远程依赖仓库或 ARM64 交叉编译。

## 样板工程

`examples/cpp-conan-consumer` 包含两个内部库（`platform_core`、`platform_transport`）和一个最终应用。Transport 依赖 Conan 管理的 `fmt/11.0.2`，最终应用和 CTest 测试都经过 CMake/Ninja 编译。

工具链作为独立 Candidate：`gcc13-conan234`。它基于固定 digest 的 Ubuntu 26.04 LTS 镜像，使用固定 APT Snapshot `20261107T000000Z`，安装精确 GCC/G++ 13.4.0-10ubuntu1、CMake、Ninja、ccache 和精确版本 Conan 2.34.0。Candidate 未发布或未通过晋级前，业务项目不能引用它作为 Active 工具链。

## 锁文件生成与使用

首次引导由工具链容器中的 Conan 2.34.0 生成：

```bash
cd examples/cpp-conan-consumer
CONAN_LOCK_BOOTSTRAP=1 bash ci/build.sh
```

生成后必须把真实生成的 `conan.lock` 纳入版本控制。严格构建不允许 bootstrap：

```bash
bash ci/build.sh
```

严格模式遇到锁文件缺失会直接失败。禁止手写或猜测 Conan lockfile 内容。

## 缓存边界

- Conan cache：第三方依赖 recipe/package 下载缓存；丢失只应导致重新下载或构建。
- ccache：编译器输出缓存；不能作为发布制品。
- DAG artifact：模块间经校验的构建输出。
- Release artifact：经过测试、制品合同和供应链验证的交付件。

依赖指纹至少包含 `conanfile.py`、`conan.lock`、Conan profile、CMakeLists 和依赖契约。锁文件或 profile 变化必须产生不同指纹。任何缺失输入均 fail closed。

## 本阶段验收边界

1. GCC 13 / Conan 2.34.0 / CMake / Ninja 的版本检查通过。
2. Conan 锁定的 fmt recipe reference 可解析并安装。
3. 两个内部 CMake 库和最终应用可以完成 clean build。
4. CTest 和最终应用 smoke test 通过。
5. 重复运行复用 Conan 本地缓存，但锁定依赖图不发生变化。
6. 缺少 lockfile 或依赖声明未进入 fingerprint 时测试失败。

本阶段还要根据 Trivy 结果确认 Ubuntu 26.04 镜像没有继承 Ubuntu 24.04 GA 内核头文件的未修复 CVE。后续阶段再接入受认证的不可变 Conan 远程仓库、DAG cache contract、ARM64 cross profile / sysroot，以及 GCC/Clang ABI 矩阵。
