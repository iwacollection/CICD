# Reproducible AArch64 cross-build fixture

This fixture builds a Linux AArch64 executable on an x86_64 Linux host. It deliberately distinguishes the Conan build profile (where build tools run) from the Conan host profile (where the produced library and executable run).

## Pinned target contract

- Target triple: `aarch64-linux-gnu`
- Build-host compiler: native x86_64 GCC/G++ 13.4.0 (`gcc-13`, `g++-13`)
- Target compiler: AArch64 GCC/G++ 13.4.0 (`gcc-13-aarch64-linux-gnu`, `g++-13-aarch64-linux-gnu`)
- Target ABI: Linux AArch64, `armv8`, `libstdc++11`, C++17
- Sysroot root: `/usr/aarch64-linux-gnu` (cross glibc 2.43; Linux UAPI headers 7.0.0-13.13cross1)
- Base image: pinned official Ubuntu 26.04 LTS (Resolute) OCI index digest `sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c`
- APT snapshot: `20261109T000000Z` (Ubuntu native snapshot configuration)
- Conan CLI: 2.34.0, installed from the upstream Linux AMD64 release `.deb` after verifying SHA-256 `94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1`
- Third-party dependency: `fmt/11.0.2`
- Runtime validation: `qemu-aarch64 -L /usr/aarch64-linux-gnu`

The Candidate image is built on an amd64 runner and contains the cross compiler, target headers/libraries and QEMU user-mode emulator. This is not an ARM runner and does not prove device-specific behavior.

## Host versus target

- `profiles/build` is x86_64 and explicitly selects native `/usr/bin/gcc-13` and `/usr/bin/g++-13`. Conan build-context tools and build requirements run here.
- `profiles/host` is armv8. Conan host-context packages, fmt and application outputs are built for AArch64.
- `tools.build:compiler_executables` in the host profile selects the AArch64 GCC frontends; the separate build profile selects the native x86_64 GCC 13 binaries.
- Both Ninja and GNU Make are installed: the top-level fixture uses Ninja, while some Conan package recipes still configure their own source builds with the `Unix Makefiles` generator.
- `ci/aarch64-toolchain.cmake` configures CMake's Linux/AArch64 target and restricts system include/library lookup to the target triplet root.

## Identity and cache contract

The Candidate bootstrap must produce `ci/target-sdk.lock` from the actual Ubuntu image. Commit that exact generated identity before switching CI from bootstrap to strict lock verification. The Conan dependency lock remains the separately reviewed fixed recipe revision. Normal verification runs `ci/verify.sh` without bootstrap and fail closed if either lock is missing or the observed identity differs. The SDK lock records:
- target triple, cross compiler version/package revisions, and native build-host compiler version/package revisions
- linker identity
- sysroot path and tree SHA-256
- SDK APT snapshot, package inventory digest and combined SDK digest

A normal verification run refuses a missing or changed `target-sdk.lock`. The Conan `conan.lock`, both profiles, CMake toolchain file and generated target SDK lock are required cache-fingerprint inputs for a future DAG node. A different ABI, sysroot, SDK or dependency lock cannot be treated as the same target cache.

## Target cache isolation

\`ci/cache-key-files.json\` is the fixture's cache-fingerprint contract. The verifier requires every listed input to exist and hashes the relative path and content for:
- Conan recipe and generated lockfile
- Host and build profiles
- CMake AArch64 toolchain definition
- Actual target SDK/sysroot identity lock
- CMake project and all public headers/source files

The computed fingerprint is recorded in \`.ci-telemetry/cache-fingerprint.txt\` and copied into \`build/arm64/build-evidence.json\`. A missing input fails the build rather than falling back to a partial key.

The disposable Conan package cache and ccache are separate directories. Their namespace contains the AArch64 target triple, exact GCC version, standard library ABI, C++ standard and target SDK digest. A change in sysroot/compiler/ABI gets a different namespace. Ccache also performs its own compiler/input hashing; the namespace is an additional safety boundary, not a replacement for ccache's key calculation.

This fixture proves target-aware fingerprint calculation and local cache-directory isolation. It does not yet claim that GitHub Actions persistent cache restore/save is wired into the production DAG; that integration requires an Active immutable toolchain and the corresponding target project node.

## Verification

The script consumes the committed Conan dependency lock and SDK identity lock, performs Conan dependency installation, CMake/Ninja configure and compile/link, ELF machine/dependency and ARM attributes inspection, GLIBC/GLIBCXX/CXXABI symbol-version compatibility against the target sysroot, no-RPATH/RUNPATH validation, CTest using QEMU and a direct QEMU run. It never resolves a new dependency graph during normal CI verification. It creates a tar archive containing the AArch64 static library and executable and writes build evidence with timings and SHA-256.

The QEMU test is a useful Linux userspace smoke test only. Kernel/driver compatibility, hardware performance, bootloader integration and board-specific behavior still require a real ARM64 device runner/HIL setup.
