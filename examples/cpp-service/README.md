# hello-service

这是 DAG 第三层的合成消费项目。它不重新编译上游代码，而是读取并执行第二层 `hello-cpp` 交付的、经过 Artifact Contract v2 校验的二进制，然后把运行结果打包为自己的制品。

CI 通过 `CI_UPSTREAM_ROOT/hello-cpp/build/hello-ci` 接收上游制品。缺少上游制品、执行失败或输出不匹配都会使构建失败。
