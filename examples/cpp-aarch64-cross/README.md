# Reproducible AArch64 cross-build fixture

This fixture builds a Linux AArch64 executable on an x86_64 Linux host. It deliberately distinguishes the Conan build profile (where build tools run) from the Conan host profile (where the produced library and executable run).

## Pinned target contract

- Target triple: `aarch64-linux-gnu`
- Compiler: GCC/G++ 13.4.0 (`gcc-13-aarch64-linux-gnu`, `g++-13-aarch64-linux-gnu`)
- Target ABI: Linux AArch64, `armv8`, `libstdc++11`, C++17
- Sysroot root: `/usr/aarch64-linux-gnu`
- Ubuntu base image: pinned Ubuntu 26.04 image digest
- APT snapshot: `20261107T000000Z`
- Conan CLI: 2.34.0
- Third-party dependency: `fmt/11.0.2`
- Runtime validation: `qemu-aarch64 -L /usr/aarch64-linux-gnu`

The Candidate image is built on an amd64 runner and contains the cross compiler, target headers/libraries and QEMU user-mode emulator. This is not an ARM runner and does not prove device-specific behavior.

## Host versus target

- `profiles/build` is x86_64. Conan build-context tools and build requirements run here.
- `profiles/host` is armv8. Conan host-context packages, fmt and application outputs are built for AArch64.
- `tools.build:compiler_executables` selects the AArch64 GCC frontends.
- `ci/aarch64-toolchain.cmake` configures CMake's Linux/AArch64 target and restricts system include/library lookup to the target triplet root.

## Identity and cache contract

`ci/verify.sh --bootstrap` generates `ci/target-sdk.lock` from the actual compiler, linker, cross package inventory and sysroot file tree. The file records:
- target triple and compiler version/package revisions
- linker identity
- sysroot path and tree SHA-256
- SDK APT snapshot, package inventory digest and combined SDK digest

A normal verification run refuses a missing or changed `target-sdk.lock`. The Conan `conan.lock`, both profiles, CMake toolchain file and target SDK lock are required cache-fingerprint inputs for a future DAG node. A different ABI, sysroot, SDK or dependency lock cannot be treated as the same target cache.

## Verification

The script performs Conan graph lock generation/install, CMake/Ninja configure and compile/link, ELF machine/dependency inspection, CTest using QEMU and a direct QEMU run. It creates a tar archive containing the AArch64 static library and executable and writes build evidence with timings and SHA-256.

The QEMU test is a useful Linux userspace smoke test only. Kernel/driver compatibility, hardware performance, bootloader integration and board-specific behavior still require a real ARM64 device runner/HIL setup.
