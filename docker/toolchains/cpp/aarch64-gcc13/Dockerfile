FROM ubuntu:26.04@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_SNAPSHOT=20261107T000000Z
ARG CROSS_GCC_VERSION=13.4.0-10ubuntu1cross1
ARG CROSS_LIBC_VERSION=2.43-2ubuntu2cross1

# Bootstrap TLS trust from ordinary archive metadata, then bind the cross
# toolchain and its transitive packages to one immutable Ubuntu snapshot.
RUN rm -f /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates \
    && printf '%s\n' 'APT::Snapshot "20261107T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 install -y --no-install-recommends \
       gcc-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       g++-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       libc6-dev-arm64-cross="$CROSS_LIBC_VERSION" \
       binutils-aarch64-linux-gnu \
       qemu-user \
       cmake ninja-build make ccache file binutils \
       python3-venv \
    && test "$(aarch64-linux-gnu-gcc-13 -dumpfullversion -dumpversion)" = "13.4.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpfullversion -dumpversion)" = "13.4.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpmachine)" = "aarch64-linux-gnu" \
    && test -s /usr/aarch64-linux-gnu/include/features.h \
    && python3 -m venv /opt/conan-venv \
    && /opt/conan-venv/bin/pip install --disable-pip-version-check --no-cache-dir "conan==2.34.0" \
    && /opt/conan-venv/bin/conan --version | grep -Fx "Conan version 2.34.0" \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/conan-venv/bin:${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD AArch64 GCC 13 cross toolchain" \
      org.opencontainers.image.description="Candidate Linux AArch64 cross compiler with pinned sysroot identity and QEMU smoke execution" \
      org.opencontainers.image.base.digest="sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b" \
      org.opencontainers.image.ubuntu.snapshot="20261107T000000Z" \
      org.opencontainers.image.target.triple="aarch64-linux-gnu" \
      org.opencontainers.image.target.compiler.version="13.4.0" \
      org.opencontainers.image.target.sysroot="/usr/aarch64-linux-gnu"
