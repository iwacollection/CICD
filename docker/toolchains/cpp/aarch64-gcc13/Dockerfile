FROM debian:forky-slim@sha256:27b88700aef98dd3dd0b32538f8e5d662a88237ec8fbf969254c8d3c93dfdab1

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_VERSION=3.3.6
ARG HOST_GCC_PACKAGE_VERSION=13.5.0-3
ARG CROSS_GCC_VERSION=13.5.0-1cross1
ARG CROSS_LIBC_VERSION=2.43-3cross8
ARG CROSS_LINUX_HEADERS_VERSION=7.2.15-1
ARG BINUTILS_VERSION=2.47.50.20261021-1
ARG QEMU_VERSION=1:11.1.3+ds-1
ARG CONAN_VERSION=2.34.0
ARG CONAN_DEB_SHA256=94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1

# Forky is Debian testing (not stable). This isolated image is a build-toolchain
# candidate only. Sid is indexed only for the exact static qemu-user version; all
# system/compiler/sysroot upgrades and package selection remain targeted at Forky.
# APT continues to verify signed Debian Release metadata.
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
       "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261108T000000Z forky main" \
       "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261108T000000Z sid main" \
       > /etc/apt/sources.list \
    && printf '%s\n' 'APT::Default-Release "forky";' > /etc/apt/apt.conf.d/99default-release \
    && apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends "apt=${APT_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' libapt-pkg7.0)" = "$APT_VERSION" \
    && apt-get -t forky -o Acquire::Retries=0 full-upgrade -y \
    && apt-get -t forky -o Acquire::Retries=0 install -y --no-install-recommends \
       "gcc-13=${HOST_GCC_PACKAGE_VERSION}" \
       "g++-13=${HOST_GCC_PACKAGE_VERSION}" \
       "gcc-13-aarch64-linux-gnu=${CROSS_GCC_VERSION}" \
       "g++-13-aarch64-linux-gnu=${CROSS_GCC_VERSION}" \
       "libc6-dev-arm64-cross=${CROSS_LIBC_VERSION}" \
       "linux-libc-dev-arm64-cross=${CROSS_LINUX_HEADERS_VERSION}" \
       "libgcc-13-dev-arm64-cross=${CROSS_GCC_VERSION}" \
       "libstdc++-13-dev-arm64-cross=${CROSS_GCC_VERSION}" \
       "binutils-aarch64-linux-gnu=${BINUTILS_VERSION}" \
       cmake ninja-build make ccache file python3 ca-certificates \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends "qemu-user=${QEMU_VERSION}" \
    && test "$(apt-get --version | head -n 1 | awk '{print $2}')" = "$APT_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' gcc-13)" = "$HOST_GCC_PACKAGE_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' g++-13)" = "$HOST_GCC_PACKAGE_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' gcc-13-aarch64-linux-gnu)" = "$CROSS_GCC_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' g++-13-aarch64-linux-gnu)" = "$CROSS_GCC_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' libc6-dev-arm64-cross)" = "$CROSS_LIBC_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' linux-libc-dev-arm64-cross)" = "$CROSS_LINUX_HEADERS_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' binutils-aarch64-linux-gnu)" = "$BINUTILS_VERSION" \
    && test "$(dpkg-query -W -f='${Version}' qemu-user)" = "$QEMU_VERSION" \
    && test "$(gcc-13 -dumpfullversion -dumpversion)" = "13.5.0" \
    && test "$(g++-13 -dumpfullversion -dumpversion)" = "13.5.0" \
    && test "$(aarch64-linux-gnu-gcc-13 -dumpfullversion -dumpversion)" = "13.5.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpfullversion -dumpversion)" = "13.5.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpmachine)" = "aarch64-linux-gnu" \
    && qemu-aarch64 --version | grep -F "11.1.3" \
    && test -s /usr/aarch64-linux-gnu/include/features.h \
    && python3 -c 'import hashlib,urllib.request; p="/tmp/conan.deb"; urllib.request.urlretrieve("https://github.com/conan-io/conan/releases/download/2.34.0/conan-2.34.0-amd64.deb",p); a=hashlib.sha256(open(p,"rb").read()).hexdigest(); e="94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1"; print("conan_deb_sha256="+a); assert a==e, "Conan DEB SHA-256 mismatch"' \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends /tmp/conan.deb \
    && test "$(conan --version)" = "Conan version ${CONAN_VERSION}" \
    && rm -f /tmp/conan.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/conan-venv/bin:${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD AArch64 GCC 13 cross toolchain" \
      org.opencontainers.image.description="Candidate Debian Forky AArch64 cross compiler with pinned sysroot identity and security-fixed static QEMU user-mode execution" \
      org.opencontainers.image.base.digest="sha256:27b88700aef98dd3dd0b32538f8e5d662a88237ec8fbf969254c8d3c93dfdab1" \
      org.opencontainers.image.base.name="debian:forky-slim" \
      org.opencontainers.image.debian.snapshot="20261108T000000Z" \
      org.opencontainers.image.qemu.version="1:11.1.3+ds-1" \
      org.opencontainers.image.qemu.snapshot="20261108T000000Z" \
      org.opencontainers.image.target.triple="aarch64-linux-gnu" \
      org.opencontainers.image.target.compiler.version="13.5.0" \
      org.opencontainers.image.target.sysroot="/usr/aarch64-linux-gnu" \
      org.opencontainers.image.conan.version="2.34.0" \
      org.opencontainers.image.conan.deb.sha256="94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1"
