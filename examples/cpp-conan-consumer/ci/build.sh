#!/usr/bin/env bash
set -euo pipefail

EXPECTED_CONAN_VERSION="2.34.0"
PROFILE="profiles/gcc13-linux-x86_64"

actual_conan="$(conan --version)"
[[ "$actual_conan" == "Conan version $EXPECTED_CONAN_VERSION" ]] || {
    echo "Expected Conan $EXPECTED_CONAN_VERSION, got: $actual_conan" >&2
    exit 1
}
compiler_major="$(g++ -dumpfullversion -dumpversion | cut -d. -f1)"
[[ "$compiler_major" == "13" ]] || {
    echo "Expected GCC 13, got: $(g++ --version | head -n 1)" >&2
    exit 1
}

if [[ ! -f conan.lock ]]; then
    if [[ "${CONAN_LOCK_BOOTSTRAP:-0}" != "1" ]]; then
        echo "conan.lock is missing; only the explicit bootstrap lane may generate it" >&2
        exit 1
    fi
    conan lock create . -pr:h="$PROFILE" -pr:b="$PROFILE" --lockfile-out=conan.lock
fi

python3 ci/dependency_contract.py --root . --print-fingerprint

conan install . \
    --output-folder=build/conan \
    -pr:h="$PROFILE" \
    -pr:b="$PROFILE" \
    --lockfile=conan.lock \
    --build=missing

cmake -S . -B build -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=build/conan/conan_toolchain.cmake \
    -DCMAKE_BUILD_TYPE=Release

cmake --build build --parallel "${CI_BUILD_JOBS:-2}"
ctest --test-dir build --output-on-failure
test "$(./build/cpp-platform-demo)" = "cpp-platform-core:ready"
echo "cpp-conan-consumer-ok"
