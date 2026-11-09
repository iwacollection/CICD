FROM debian:trixie@sha256:bfc36aecdece0387418759dba474aa856c026ca8087da3606b41b90ab0509c99

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_SNAPSHOT=20261108T000000Z
ARG CROSS_GCC_VERSION=13.3.0-16cross1
ARG HOST_GCC_PACKAGE_VERSION=13.3.0-16
ARG CROSS_LIBC_VERSION=2.41-11cross1
ARG BINUTILS_VERSION=2.44-3
ARG QEMU_VERSION=1:10.0.13+ds-0+deb13u1
ARG APT_VERSION=3.0.4
ARG CONAN_VERSION=2.34.0
ARG CONAN_DEB_SHA256=94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1

# Pin both Debian archives to one timestamp. APT retries are disabled as
# defense in depth for CVE-2026-109518; APT 3.0.4 is the fixed Trixie build.
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
       'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261108T000000Z trixie main' \
       'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20261108T000000Z trixie-security main' \
       > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 full-upgrade -y \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends \
       apt="$APT_VERSION" \
       gcc-13="$HOST_GCC_PACKAGE_VERSION" \
       g++-13="$HOST_GCC_PACKAGE_VERSION" \
       gcc-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       g++-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       libc6-dev-arm64-cross="$CROSS_LIBC_VERSION" \
       binutils-aarch64-linux-gnu="$BINUTILS_VERSION" \
       qemu-user="$QEMU_VERSION" \
       cmake ninja-build make ccache file binutils curl python3 ca-certificates \
    && test "$(gcc-13 -dumpfullversion -dumpversion)" = "13.3.0" \
    && test "$(g++-13 -dumpfullversion -dumpversion)" = "13.3.0" \
    && test "$(aarch64-linux-gnu-gcc-13 -dumpfullversion -dumpversion)" = "13.3.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpfullversion -dumpversion)" = "13.3.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpmachine)" = "aarch64-linux-gnu" \
    && test -s /usr/aarch64-linux-gnu/include/features.h \
    && curl --fail --location --retry 3 \
       "https://github.com/conan-io/conan/releases/download/${CONAN_VERSION}/conan-${CONAN_VERSION}-amd64.deb" \
       --output /tmp/conan.deb \
    && echo "${CONAN_DEB_SHA256}  /tmp/conan.deb" | sha256sum --check --status \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends /tmp/conan.deb \
    && test "$(conan --version)" = "Conan version ${CONAN_VERSION}" \
    && rm -f /tmp/conan.deb \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/conan-venv/bin:${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD AArch64 GCC 13 cross toolchain" \
      org.opencontainers.image.description="Candidate Debian 13 AArch64 cross compiler with pinned sysroot identity and QEMU smoke execution" \
      org.opencontainers.image.base.digest="sha256:bfc36aecdece0387418759dba474aa856c026ca8087da3606b41b90ab0509c99" \
      org.opencontainers.image.debian.snapshot="20261108T000000Z" \
      org.opencontainers.image.target.triple="aarch64-linux-gnu" \
      org.opencontainers.image.target.compiler.version="13.3.0" \
      org.opencontainers.image.target.sysroot="/usr/aarch64-linux-gnu" \
      org.opencontainers.image.conan.version="2.34.0" \
      org.opencontainers.image.conan.deb.sha256="94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1"
