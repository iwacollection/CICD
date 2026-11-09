FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_SNAPSHOT=20261109T000000Z
ARG CROSS_GCC_VERSION=13.4.0-10ubuntu1cross1
ARG HOST_GCC_PACKAGE_VERSION=13.4.0-10ubuntu1
ARG CROSS_LIBC_VERSION=2.43-2ubuntu2cross1
ARG CROSS_LINUX_HEADERS_VERSION=7.0.0-13.13cross1
ARG BINUTILS_VERSION=2.46-3ubuntu2
ARG QEMU_VERSION=1:10.2.1+ds-1ubuntu3.3
ARG APT_VERSION=3.2.1ubuntu0.1
ARG CONAN_VERSION=2.34.0
ARG CONAN_DEB_SHA256=94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1

# Ubuntu's native snapshot integration pins the complete archive state,
# including updates and security pockets. APT retries remain disabled as
# defense in depth for CVE-2026-109518 while installing the fixed APT build.
RUN printf 'APT::Snapshot "%s";\n' "$APT_SNAPSHOT" > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends "apt=$APT_VERSION" \
    && apt-get -o Acquire::Retries=0 full-upgrade -y \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends \
       gcc-13="$HOST_GCC_PACKAGE_VERSION" \
       g++-13="$HOST_GCC_PACKAGE_VERSION" \
       gcc-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       g++-13-aarch64-linux-gnu="$CROSS_GCC_VERSION" \
       libc6-dev-arm64-cross="$CROSS_LIBC_VERSION" \
       linux-libc-dev-arm64-cross="$CROSS_LINUX_HEADERS_VERSION" \
       libgcc-13-dev-arm64-cross="$CROSS_GCC_VERSION" \
       libstdc++-13-dev-arm64-cross="$CROSS_GCC_VERSION" \
       binutils-aarch64-linux-gnu="$BINUTILS_VERSION" \
       qemu-user="$QEMU_VERSION" \
       cmake ninja-build make ccache file binutils curl python3 ca-certificates \
    && test "$(apt-get --version | head -n 1 | awk '{print $2}')" = "$APT_VERSION" \
    && test "$(gcc-13 -dumpfullversion -dumpversion)" = "13.4.0" \
    && test "$(g++-13 -dumpfullversion -dumpversion)" = "13.4.0" \
    && test "$(aarch64-linux-gnu-gcc-13 -dumpfullversion -dumpversion)" = "13.4.0" \
    && test "$(aarch64-linux-gnu-g++-13 -dumpfullversion -dumpversion)" = "13.4.0" \
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
      org.opencontainers.image.description="Candidate Ubuntu 26.04 LTS AArch64 cross compiler with a pinned sysroot identity and QEMU user-mode execution" \
      org.opencontainers.image.base.digest="sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c" \
      org.opencontainers.image.base.name="ubuntu:26.04" \
      org.opencontainers.image.ubuntu.snapshot="20261109T000000Z" \
      org.opencontainers.image.target.triple="aarch64-linux-gnu" \
      org.opencontainers.image.target.compiler.version="13.4.0" \
      org.opencontainers.image.target.sysroot="/usr/aarch64-linux-gnu" \
      org.opencontainers.image.conan.version="2.34.0" \
      org.opencontainers.image.conan.deb.sha256="94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1"
