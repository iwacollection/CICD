#!/usr/bin/env bash
set -euo pipefail
test "$(g++ -dumpfullversion -dumpversion | cut -d. -f1)" = "13"
conan --version | grep -Fx "Conan version 2.34.0"
cmake --version | head -n 1
ninja --version
ccache --version | head -n 1
python3 --version
echo "cpp-conan-toolchain-smoke-ok"
