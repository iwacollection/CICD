#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
if [[ "$mode" != "" && "$mode" != "--bootstrap" ]]; then
  echo "usage: $0 [--bootstrap]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
readonly TARGET_TRIPLE="aarch64-linux-gnu"
readonly SYSROOT="/usr/aarch64-linux-gnu"
readonly APT_SNAPSHOT="20261107T000000Z"
readonly EXPECTED_COMPILER="13.4.0"
readonly CONAN_VERSION="2.34.0"
mkdir -p .ci-telemetry .ci-cache
: > .ci-telemetry/stages.tsv

run_timed() {
  local stage="$1"
  shift
  local start end elapsed
  start="$(date +%s%N)"
  "$@"
  end="$(date +%s%N)"
  elapsed="$(( (end - start) / 1000000 ))"
  printf '%s\t%s\n' "$stage" "$elapsed" >> .ci-telemetry/stages.tsv
  printf 'aarch64_stage=%s elapsed_ms=%s\n' "$stage" "$elapsed"
}

test "$(conan --version)" = "Conan version ${CONAN_VERSION}"
test "$(aarch64-linux-gnu-g++-13 -dumpmachine)" = "$TARGET_TRIPLE"
test "$(aarch64-linux-gnu-g++-13 -dumpfullversion -dumpversion)" = "$EXPECTED_COMPILER"
test -d "$SYSROOT"
test -s "$SYSROOT/include/features.h"

compiler_package="$(dpkg-query -W -f='${Version}' g++-13-aarch64-linux-gnu)"
gcc_package="$(dpkg-query -W -f='${Version}' gcc-13-aarch64-linux-gnu)"
libc_package="$(dpkg-query -W -f='${Version}' libc6-dev-arm64-cross)"
linker_version="$(aarch64-linux-gnu-ld --version | head -n 1)"
qemu_version="$(qemu-aarch64 --version | head -n 1)"

sysroot_file_hash="$(
  find "$SYSROOT" -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum \
    | LC_ALL=C sort \
    | sha256sum | awk '{print $1}'
)"
sysroot_link_hash="$(
  find "$SYSROOT" -type l -printf '%P -> %l\n' \
    | LC_ALL=C sort \
    | sha256sum | awk '{print $1}'
)"
sysroot_digest="$(
  printf '%s\n%s\n' "$sysroot_file_hash" "$sysroot_link_hash" \
    | sha256sum | awk '{print $1}'
)"
package_inventory="$(
  dpkg-query -W -f='${binary:Package}=${Version}\n' \
    g++-13-aarch64-linux-gnu gcc-13-aarch64-linux-gnu \
    libc6-dev-arm64-cross binutils-aarch64-linux-gnu qemu-user \
    | LC_ALL=C sort
)"
package_inventory_sha256="$(printf '%s\n' "$package_inventory" | sha256sum | awk '{print $1}')"
sdk_digest="$(
  printf '%s\n' \
    "$TARGET_TRIPLE" "$EXPECTED_COMPILER" "$compiler_package" "$gcc_package" \
    "$libc_package" "$linker_version" "$sysroot_digest" \
    "$package_inventory_sha256" "$APT_SNAPSHOT" \
    | sha256sum | awk '{print $1}'
)"

python3 - "$mode" "$TARGET_TRIPLE" "$EXPECTED_COMPILER" "$compiler_package" "$gcc_package" \
  "$libc_package" "$linker_version" "$SYSROOT" "$sysroot_digest" \
  "$package_inventory_sha256" "$sdk_digest" "$APT_SNAPSHOT" "$qemu_version" <<'PY'
import json
import pathlib
import sys

(
    mode, triple, compiler_version, gxx_package, gcc_package, libc_package,
    linker, sysroot, sysroot_digest, package_digest, sdk_digest, snapshot, qemu,
) = sys.argv[1:]
identity = {
    "schema_version": 1,
    "target_triple": triple,
    "compiler": {
        "family": "GCC",
        "version": compiler_version,
        "gxx_package": gxx_package,
        "gcc_package": gcc_package,
    },
    "linker": linker,
    "sysroot": {"path": sysroot, "sha256": sysroot_digest},
    "sdk": {
        "version": "ubuntu-resolute-20261107-gcc13-cross",
        "apt_snapshot": snapshot,
        "package_inventory_sha256": package_digest,
        "sha256": sdk_digest,
    },
    "emulator": qemu,
}
lock_path = pathlib.Path("ci/target-sdk.lock")
encoded = json.dumps(identity, indent=2, sort_keys=True) + "\n"
if not lock_path.is_file():
    if mode != "--bootstrap":
        raise SystemExit("required ci/target-sdk.lock is missing; bootstrap is allowed only in Candidate verification")
    lock_path.write_text(encoded, encoding="utf-8")
else:
    expected = json.loads(lock_path.read_text(encoding="utf-8"))
    if expected != identity:
        raise SystemExit("target SDK identity mismatch; sysroot/compiler/linker/SDK changed")
pathlib.Path(".ci-telemetry/target-sdk-identity.json").write_text(encoded, encoding="utf-8")
print(f"target_triple={triple}")
print(f"target_compiler=GCC {compiler_version}")
print(f"target_sysroot_sha256={sysroot_digest}")
print(f"target_sdk_sha256={sdk_digest}")
PY

LOCK_FILE="conan.lock"
if [[ ! -s "$LOCK_FILE" ]]; then
  if [[ "$mode" != "--bootstrap" ]]; then
    echo "required Conan lockfile is missing; bootstrap is allowed only in Candidate verification" >&2
    exit 1
  fi
  run_timed dependency_resolution \
    conan lock create . \
      --profile:host=profiles/host \
      --profile:build=profiles/build \
      --remote=conancenter \
      --lockfile-out="$LOCK_FILE"
fi

# Cache namespaces are target identities, not merely architecture labels.
# The SDK digest includes the actual cross sysroot file tree, package
# inventory, compiler and linker identity from the locked target SDK.
readonly TARGET_CXX_ABI="libstdc++11"
readonly TARGET_CXX_STANDARD="17"
readonly CACHE_NAMESPACE="${TARGET_TRIPLE}-gcc${EXPECTED_COMPILER}-${TARGET_CXX_ABI}-cxx${TARGET_CXX_STANDARD}-${sdk_digest}"
export CCACHE_DIR="$PWD/.ci-cache/ccache/$CACHE_NAMESPACE"
export CONAN_HOME="$PWD/.ci-cache/conan/$CACHE_NAMESPACE"
mkdir -p "$CCACHE_DIR" "$CONAN_HOME"

cache_fingerprint="$(python3 - <<'PY'
import glob
import hashlib
import json
from pathlib import Path

root = Path.cwd().resolve()
patterns = json.loads(Path("ci/cache-key-files.json").read_text(encoding="utf-8"))
files = set()
for pattern in patterns:
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SystemExit(f"unsafe cache-key pattern: {pattern}")
    matches = [
        Path(item).resolve()
        for item in glob.glob(str(root / pattern), recursive=True)
        if Path(item).is_file()
    ]
    if not matches:
        raise SystemExit(f"required cache-key input matched no files: {pattern}")
    for item in matches:
        item.relative_to(root)
        files.add(item)

digest = hashlib.sha256()
for item in sorted(files):
    relative = item.relative_to(root).as_posix()
    digest.update(relative.encode("utf-8"))
    digest.update(b"\\0")
    digest.update(item.read_bytes())
    digest.update(b"\\0")
fingerprint = digest.hexdigest()
Path(".ci-telemetry/cache-fingerprint.txt").write_text(fingerprint + "\\n", encoding="utf-8")
print(fingerprint)
PY
)"
printf 'target_cache_namespace=%s\\n' "$CACHE_NAMESPACE"
printf 'target_cache_fingerprint=%s\\n' "$cache_fingerprint"

run_timed dependency_download_and_install \
  conan install . \
    --output-folder=build/conan \
    --profile:host=profiles/host \
    --profile:build=profiles/build \
    --lockfile="$LOCK_FILE" \
    --remote=conancenter \
    --build=missing

run_timed configure \
  cmake -S . -B build/arm64 -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/build/conan/conan_toolchain.cmake" \
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \\\n    '-DCMAKE_CROSSCOMPILING_EMULATOR=qemu-aarch64;-L;/usr/aarch64-linux-gnu'

run_timed compile_link \
  cmake --build build/arm64 --parallel "${CI_BUILD_JOBS:-2}"

file build/arm64/aarch64-cross-demo | tee .ci-telemetry/elf-file.txt
aarch64-linux-gnu-readelf -h build/arm64/aarch64-cross-demo > .ci-telemetry/elf-header.txt
aarch64-linux-gnu-readelf -d build/arm64/aarch64-cross-demo > .ci-telemetry/elf-dynamic.txt
grep -F "AArch64" .ci-telemetry/elf-header.txt
grep -F "Machine:" .ci-telemetry/elf-header.txt
grep -F "NEEDED" .ci-telemetry/elf-dynamic.txt
grep -F "ARM aarch64" .ci-telemetry/elf-file.txt

run_timed test_qemu_ctest \
  ctest --test-dir build/arm64 --output-on-failure

run_timed test_qemu_direct \
  qemu-aarch64 -L "$SYSROOT" ./build/arm64/aarch64-cross-demo

grep -Fx "aarch64-cross-core-ok:qemu-runtime-ok" < <(qemu-aarch64 -L "$SYSROOT" ./build/arm64/aarch64-cross-demo)

run_timed package \
  tar -czf build/arm64/aarch64-cross-artifact.tar.gz \
    -C build/arm64 libcross-core.a aarch64-cross-demo

python3 - <<'PY'
import hashlib
import json
import pathlib

identity = json.loads(pathlib.Path(".ci-telemetry/target-sdk-identity.json").read_text(encoding="utf-8"))
artifact = pathlib.Path("build/arm64/aarch64-cross-artifact.tar.gz")
timings = {}
for line in pathlib.Path(".ci-telemetry/stages.tsv").read_text(encoding="utf-8").splitlines():
    stage, elapsed = line.split("\t", 1)
    timings[stage + "_ms"] = int(elapsed)
cache_stats = pathlib.Path(".ci-telemetry/ccache-stats.txt")
import subprocess
try:
    subprocess.run(["ccache", "--show-stats", "--verbose"], check=False, stdout=cache_stats.open("w"), stderr=subprocess.STDOUT)
except OSError:
    cache_stats.write_text("ccache statistics unavailable\\n", encoding="utf-8")
evidence = {
    "schema_version": 1,
    "cache": {
        "fingerprint": pathlib.Path(".ci-telemetry/cache-fingerprint.txt").read_text(encoding="utf-8").strip(),
        "namespace": "${CACHE_NAMESPACE}",
        "conan_home": "${CONAN_HOME}",
        "ccache_dir": "${CCACHE_DIR}",
        "ccache_stats": cache_stats.read_text(encoding="utf-8").splitlines(),
    },
    "target": identity,
    "artifact": {
        "path": str(artifact),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size_bytes": artifact.stat().st_size,
    },
    "elf": {
        "file": pathlib.Path(".ci-telemetry/elf-file.txt").read_text(encoding="utf-8").strip(),
        "header": pathlib.Path(".ci-telemetry/elf-header.txt").read_text(encoding="utf-8").splitlines(),
        "dynamic": pathlib.Path(".ci-telemetry/elf-dynamic.txt").read_text(encoding="utf-8").splitlines(),
    },
    "timings": timings,
    "runtime_test": "passed under qemu-aarch64 user-mode emulation; no HIL claim",
}
pathlib.Path("build/arm64/build-evidence.json").write_text(
    json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(evidence, indent=2))
PY
