#!/usr/bin/env bash
# Resolve the locked Conan graph, install third-party packages and use its CMake toolchain.
set -euo pipefail

readonly CONAN_VERSION="2.34.0"
readonly VENV_DIR=".ci-tools/conan-${CONAN_VERSION}"
readonly LOCK_FILE="conan.lock"
readonly CONAN_PROFILE_HOST="profiles/host"
readonly CONAN_PROFILE_BUILD="profiles/build"

if [[ ! -s "$LOCK_FILE" ]]; then
  echo "ERROR: required Conan lockfile is missing: $LOCK_FILE" >&2
  exit 2
fi

# The hosted ubuntu-latest Runner does not guarantee Ninja is preinstalled.
# Keep the declared Ninja generator reproducible instead of silently falling
# back to a different build backend.
if ! command -v ninja >/dev/null 2>&1; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: Ninja is required but neither ninja nor sudo is available" >&2
    exit 2
  fi
  sudo apt-get update -qq
  sudo apt-get install -y -qq ninja-build
fi
if ! command -v ninja >/dev/null 2>&1; then
  echo "ERROR: Ninja installation completed but the executable is still unavailable" >&2
  exit 2
fi
for input in conanfile.txt "$CONAN_PROFILE_HOST" "$CONAN_PROFILE_BUILD"; do
  if [[ ! -s "$input" ]]; then
    echo "ERROR: required Conan input is missing or empty: $input" >&2
    exit 2
  fi
done

mkdir -p .ci-tools .ci-cache .ci-telemetry
if [[ ! -x "$VENV_DIR/bin/conan" ]]; then
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check "conan==${CONAN_VERSION}"
fi

export CONAN_HOME="$PWD/.ci-cache/conan-home"
mkdir -p "$CONAN_HOME"
CONAN="$VENV_DIR/bin/conan"
"$CONAN" --version | grep -Fx "Conan version ${CONAN_VERSION}"

now_ns() { date +%s%N; }
elapsed_ms() { echo $(( ( $2 - $1 ) / 1000000 )); }

resolve_start="$(now_ns)"
"$CONAN" graph info . \
  --profile:host="$CONAN_PROFILE_HOST" \
  --profile:build="$CONAN_PROFILE_BUILD" \
  --lockfile="$LOCK_FILE" \
  --remote=conancenter \
  --format=json \
  --out-file=.ci-telemetry/conan-graph.json \
  > .ci-telemetry/conan-graph-info.log
resolve_end="$(now_ns)"
resolve_ms="$(elapsed_ms "$resolve_start" "$resolve_end")"

install_start="$(now_ns)"
"$CONAN" install . \
  --output-folder=build/conan \
  --profile:host="$CONAN_PROFILE_HOST" \
  --profile:build="$CONAN_PROFILE_BUILD" \
  --lockfile="$LOCK_FILE" \
  --remote=conancenter \
  --build=missing \
  2>&1 | tee .ci-telemetry/conan-install.log
install_end="$(now_ns)"
install_ms="$(elapsed_ms "$install_start" "$install_end")"

build_start="$(now_ns)"
bash "$CI_CMAKE_BUILD_HELPER" \
  --source-dir . \
  --build-dir build \
  --generator Ninja \
  --build-jobs "$CI_BUILD_JOBS" \
  --define CMAKE_BUILD_TYPE=Release \
  --define CMAKE_CXX_COMPILER=g++-13 \
  --define CMAKE_CXX_COMPILER_LAUNCHER=ccache \
  --define "CMAKE_TOOLCHAIN_FILE=$PWD/build/conan/conan_toolchain.cmake"
build_end="$(now_ns)"
build_ms="$(elapsed_ms "$build_start" "$build_end")"

python3 - "$resolve_ms" "$install_ms" "$build_ms" "$CONAN_VERSION" "$LOCK_FILE" <<'PY'
import hashlib
import json
import pathlib
import sys

resolve_ms, install_ms, build_ms = map(int, sys.argv[1:4])
version, lock_path = sys.argv[4:6]
root = pathlib.Path(".")
lock_sha = hashlib.sha256((root / lock_path).read_bytes()).hexdigest()
graph_path = root / ".ci-telemetry/conan-graph.json"
graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
install_log = (root / ".ci-telemetry/conan-install.log").read_text(errors="replace")
download_lines = sum("Downloading" in line for line in install_log.splitlines())
telemetry = {
    "schema_version": 1,
    "conan_version": version,
    "lockfile": lock_path,
    "lockfile_sha256": lock_sha,
    "resolved_graph_sha256": graph_sha,
    "dependency_resolution_ms": resolve_ms,
    "dependency_install_fetch_build_ms": install_ms,
    "conan_download_log_lines": download_lines,
    "cmake_configure_compile_total_ms": build_ms,
    "notes": "Conan graph resolution and package installation are measured separately. Install time includes package fetches and any --build=missing work; download-only duration is not inferred from mixed install output."
}
path = root / ".ci-telemetry/conan-build-timings.json"
path.write_text(json.dumps(telemetry, indent=2) + "\n", encoding="utf-8")
print(json.dumps(telemetry, indent=2))
PY
