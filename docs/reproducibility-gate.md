# 可复现构建门禁（Reproducibility Gate）

## 1. 为什么 Artifact Contract v2 还不够

Artifact Contract v2 已保证打包层固定：

```text
文件排序
uid / gid
用户名 / 组名
tar mtime
gzip mtime
```

但这只能证明：

> 同一批输入文件可以生成相同 tar.gz。

它不能自动证明编译器生成的二进制本身是确定性的。

真实风险包括：

- `__DATE__` / `__TIME__`；
- 编译路径进入二进制；
- 随机链接顺序；
- 不稳定代码生成；
- 未固定依赖；
- 工具链漂移；
- 构建脚本读取当前时间；
- cache 隐藏了非确定性。

所以生产可复现性必须验证：

```text
same source
+ same dependency locks
+ same immutable toolchain
+ same reproducibility controls
        |
        +-- clean build #1
        |
        `-- clean build #2
                 |
                 v
          exact byte comparison
```

## 2. 当前门禁基线

当前平台用 `hello-lib / generic / linux / x86_64` 做 Hosted 可复现性基线。

它使用中央 catalog 中已经 active 的：

```text
gcc-host-container-v1
```

工具链必须是：

- container execution；
- active；
- 完整 `sha256` digest；
- Runner 必须为 `ubuntu-latest`；
- Self-hosted / SoC Runner 不能进入这个 Hosted 基线。

硬件固件的双构建验证必须等真实 SDK/Runner 存在后另行建立，当前不会伪造物理可复现性证据。

## 3. 两次构建如何隔离

`scripts/ci/reproducibility_check.py` 创建两个独立临时 workspace：

```text
run-1/source
run-1/dist

run-2/source
run-2/dist
```

复制源码时主动排除：

```text
build/
dist/
.cache/
__pycache__/
*.pyc
```

因此第二次构建不会读取第一次的 build tree。

## 4. 为什么关闭 ccache

正常 CI 使用 ccache 是合理的性能优化，但可复现性证明不能依赖：

```text
Build #1 -> 生成 object
Build #2 -> cache hit -> 直接复制 Build #1 object
```

否则“两次产物一致”不能证明第二次编译本身是确定性的。

Gate 会设置：

```text
CCACHE_DISABLE=1
```

即使命令仍经过 ccache launcher，也必须执行真实编译。

## 5. SOURCE_DATE_EPOCH

Gate 使用当前 Git commit timestamp 作为：

```text
SOURCE_DATE_EPOCH
```

并保证相同值进入两次 Toolchain Container。

`run_build.py` 只把以下确定性控制变量白名单传入容器：

```text
SOURCE_DATE_EPOCH
CCACHE_DISABLE
```

不会为了可复现构建把 Runner 的完整环境变量传入容器。

## 6. 比较什么

第一层：原始业务产物。

例如：

```text
build/libhello-lib.a
include/hello_lib.h
```

每个文件分别计算 SHA256：

```text
Build #1 SHA256
        ==
Build #2 SHA256
```

第二层：Artifact Contract v2 bundle。

```text
Build #1 tar.gz SHA256
        ==
Build #2 tar.gz SHA256
```

所以门禁同时覆盖：

```text
Compiler / linker determinism
          +
Artifact packaging determinism
```

## 7. 为什么放进 Platform Validate

`main-production-governance` 已经把：

```text
Validate CI platform
```

设为 Required Check。

因此没有另建一个未被 Ruleset 强制的“装饰性 Gate”。

现在：

```text
Reproducibility check fail
          |
          v
Validate CI platform fail
          |
          v
PR 无法进入 main
```

## 8. 证据

每次 Platform Validate 保存：

```text
reproducibility.json
reproducibility.md
```

Artifacts 保留 30 天。

Markdown 会显示：

- project / target；
- exact toolchain image digest；
- SOURCE_DATE_EPOCH；
- 两次原始产物 SHA256；
- 两次 Artifact v2 bundle SHA256；
- mismatch 类型。

## 9. 失败分类

### artifact-set

两次生成的文件集合不一样。

### artifact-bytes

同名业务产物 SHA256 不一样。

### artifact-v2-bundle

业务文件可能一样，但最终可复现 bundle bytes 不一样。

任何一类都直接失败。

## 10. 后续扩展

当前先验证平台自身的 Hosted C/C++ 基线。

真实业务推广后可以把可复现策略扩成项目 catalog 字段，例如：

```text
reproducibility:
  enabled: true
  cadence: pull_request | main | scheduled
```

大型固件通常不会对每个 PR 都完整双编一次，可以采用：

```text
PR       -> 普通 Build Gate
main     -> 正常生产 Build
nightly  -> 独立双构建 Reproducibility Audit
release  -> 强制双构建 / independent builder
```

但无论采用什么频率，不能只比较版本号或文件大小，最终证据必须落到 exact digest。
