#!/usr/bin/env bash
set -euo pipefail

gcc_version="$(gcc-13 -dumpfullversion -dumpversion)"
gxx_version="$(g++-13 -dumpfullversion -dumpversion)"
[[ "$gcc_version" == "13.4.0" ]] || {
    echo "Expected GCC 13.4.0, got: $gcc_version" >&2
    exit 1
}
[[ "$gxx_version" == "13.4.0" ]] || {
    echo "Expected G++ 13.4.0, got: $gxx_version" >&2
    exit 1
}
conan --version | grep -Fx "Conan version 2.34.0"
cmake --version | head -n 1
ninja --version
ccache --version | head -n 1
python3 --version
grep -Fx 'VERSION_ID="26.04"' /etc/os-release
echo "cpp-conan-toolchain-smoke-ok"
