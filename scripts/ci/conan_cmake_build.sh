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

# Hosted runners do not promise an identical preinstalled tool set.
# Install only missing build tools; do not silently disable Ninja or ccache.
missing_packages=()
command -v ninja >/dev/null 2>&1 || missing_packages+=(ninja-build)
command -v ccache >/dev/null 2>&1 || missing_packages+=(ccache)
if (( ${#missing_packages[@]} > 0 )); then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: required build tools are missing and sudo is unavailable: ${missing_packages[*]}" >&2
    exit 2
  fi
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${missing_packages[@]}"
fi
for tool in ninja ccache; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: required build tool remains unavailable after preflight installation: $tool" >&2
    exit 2
  fi
done
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

ccache --zero-stats
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
ccache --show-stats > .ci-telemetry/ccache-first-pass.txt

# Remove CMake/Ninja outputs, but deliberately preserve the same ccache directory.
# The second full configure/build must recompile and hit the compiler cache.
warm_start="$(now_ns)"
cmake --build build --target clean
bash "$CI_CMAKE_BUILD_HELPER" \
  --source-dir . \
  --build-dir build \
  --generator Ninja \
  --build-jobs "$CI_BUILD_JOBS" \
  --define CMAKE_BUILD_TYPE=Release \
  --define CMAKE_CXX_COMPILER=g++-13 \
  --define CMAKE_CXX_COMPILER_LAUNCHER=ccache \
  --define "CMAKE_TOOLCHAIN_FILE=$PWD/build/conan/conan_toolchain.cmake"
warm_end="$(now_ns)"
warm_ms="$(elapsed_ms "$warm_start" "$warm_end")"
ccache --show-stats > .ci-telemetry/ccache-warm-pass.txt

python3 - "$resolve_ms" "$install_ms" "$build_ms" "$warm_ms" "$CONAN_VERSION" "$LOCK_FILE" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

resolve_ms, install_ms, build_ms, warm_ms = map(int, sys.argv[1:5])
version, lock_path = sys.argv[5:7]
root = pathlib.Path(".")
lock_sha = hashlib.sha256((root / lock_path).read_bytes()).hexdigest()
graph_path = root / ".ci-telemetry/conan-graph.json"
graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
install_log = (root / ".ci-telemetry/conan-install.log").read_text(errors="replace")

def parse_ccache_stats(path: str) -> tuple[int, int]:
    data = (root / path).read_text(errors="replace")
    hits = re.search(r"^\s*Hits:\s*(\d+)", data, re.MULTILINE)
    misses = re.search(r"^\s*Misses:\s*(\d+)", data, re.MULTILINE)
    if not hits or not misses:
        raise SystemExit(f"cannot parse ccache counters from {path}")
    return int(hits.group(1)), int(misses.group(1))

first_hits, first_misses = parse_ccache_stats(".ci-telemetry/ccache-first-pass.txt")
warm_hits, warm_misses = parse_ccache_stats(".ci-telemetry/ccache-warm-pass.txt")
warm_hit_delta = warm_hits - first_hits
if warm_hit_delta < 1:
    raise SystemExit(f"warm-cache validation failed: expected >=1 additional ccache hit, got {warm_hit_delta}")

telemetry = {
    "schema_version": 1,
    "conan_version": version,
    "lockfile": lock_path,
    "lockfile_sha256": lock_sha,
    "resolved_graph_sha256": graph_sha,
    "dependency_resolution_ms": resolve_ms,
    "dependency_install_fetch_build_ms": install_ms,
    "conan_download_log_lines": sum("Downloading" in line for line in install_log.splitlines()),
    "clean_configure_compile_total_ms": build_ms,
    "warm_clean_rebuild_total_ms": warm_ms,
    "ccache_first_pass_hits": first_hits,
    "ccache_first_pass_misses": first_misses,
    "ccache_warm_pass_hits": warm_hits,
    "ccache_warm_pass_misses": warm_misses,
    "ccache_warm_hit_delta": warm_hit_delta,
    "warm_cache_validation": "passed",
    "notes": "Conan package installation duration includes fetches and any --build=missing work. Warm validation cleans the CMake/Ninja outputs but preserves the same ccache directory and requires at least one additional compiler-cache hit."
}
path = root / ".ci-telemetry/conan-build-timings.json"
path.write_text(json.dumps(telemetry, indent=2) + "\n", encoding="utf-8")
print(json.dumps(telemetry, indent=2))
PY
