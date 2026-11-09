#!/usr/bin/env bash
set -euo pipefail

mode="\${1:-}"
if [[ "$mode" != "" && "$mode" != "--bootstrap" ]]; then
  echo "usage: $0 [--bootstrap]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
expected_version="Conan version 2.34.0"
actual_version="$(conan --version)"
if [[ "$actual_version" != "$expected_version" ]]; then
  echo "expected $expected_version, got $actual_version" >&2
  exit 1
fi

profile="$PWD/ci/profile"
lockfile="$PWD/conan.lock"
mkdir -p build
: > build/stage-timings.tsv

run_timed() {
  local stage="$1"
  shift
  local started finished elapsed
  started="$(date +%s%N)"
  "$@"
  finished="$(date +%s%N)"
  elapsed="$(( (finished - started) / 1000000 ))"
  printf '%s\t%s\n' "$stage" "$elapsed" >> build/stage-timings.tsv
  printf 'conan_fixture_timing stage=%s elapsed_ms=%s\n' "$stage" "$elapsed"
}

if [[ ! -s "$lockfile" ]]; then
  if [[ "$mode" != "--bootstrap" ]]; then
    echo "required committed conan.lock is missing; use --bootstrap only to generate it in the candidate verification lane" >&2
    exit 1
  fi
  run_timed dependency_resolution \
    conan lock create . \
      --profile:host="$profile" \
      --profile:build="$profile" \
      --lockfile-out="$lockfile" \
      --lockfile-clean
  test -s "$lockfile"
else
  echo "Using committed Conan lockfile: $lockfile"
fi

export CONAN_HOME="\${CONAN_HOME:-$PWD/.ci-conan-home}"
rm -rf "$CONAN_HOME"
mkdir -p "$CONAN_HOME"

run_timed dependency_download_install_cold \
  conan install . \
    --profile:host="$profile" \
    --profile:build="$profile" \
    --lockfile="$lockfile" \
    --build=missing \
    --output-folder=build/conan \
    --generator=CMakeDeps \
    --generator=CMakeToolchain

run_timed dependency_install_warm \
  conan install . \
    --profile:host="$profile" \
    --profile:build="$profile" \
    --lockfile="$lockfile" \
    --build=missing \
    --output-folder=build/conan-warm \
    --generator=CMakeDeps \
    --generator=CMakeToolchain

run_timed configure \
  cmake -S . -B build/native -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/build/conan/conan_toolchain.cmake"

run_timed compile_link \
  cmake --build build/native --parallel "\${CI_BUILD_JOBS:-2}"

run_timed test \
  ctest --test-dir build/native --output-on-failure

output="$(./build/native/cpp-conan-multimodule)"
printf '%s\n' "$output"
[[ "$output" == "conan-multimodule-ok" ]]
