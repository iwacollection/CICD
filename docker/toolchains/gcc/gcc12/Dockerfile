FROM ubuntu:26.10@sha256:c99db7323621802852e277a697aba0a09e44b30f3258d5b51dfc97d380b65e7e
ARG DEBIAN_FRONTEND=noninteractive
ARG GCC_PACKAGE_VERSION=12.5.0-9ubuntu1
# Bootstrap CA certificates using the base image's normal archive sources.
# Enable the pinned HTTPS snapshot only after the trusted CA bundle is installed.
RUN rm -f /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates \
    && printf '%s\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=60 install -y --no-install-recommends gcc-12=${GCC_PACKAGE_VERSION} g++-12=${GCC_PACKAGE_VERSION} cmake ninja-build ccache git \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; test "$(gcc-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    test "$(g++-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    test "$(gcc-12 -dumpfullversion -dumpversion)" = "12.5.0"; \
    test "$(g++-12 -dumpfullversion -dumpversion)" = "12.5.0"; \
    mkdir -p /home/ci /workspace; chown -R 10001:10001 /home/ci /workspace
ENV CC=gcc-12 CXX=g++-12 HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/gcc-toolchain-smoke
RUN chmod 0755 /usr/local/bin/gcc-toolchain-smoke
LABEL org.opencontainers.image.title="CICD GCC 12 toolchain" org.opencontainers.image.version="12.5.0" org.opencontainers.image.base.digest="sha256:c99db7323621802852e277a697aba0a09e44b30f3258d5b51dfc97d380b65e7e" org.opencontainers.image.ubuntu.snapshot="20261105T000000Z"
USER 10001:10001
