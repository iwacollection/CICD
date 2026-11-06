FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ARG DEBIAN_FRONTEND=noninteractive
ARG GCC_PACKAGE_VERSION=12.4.0-2ubuntu1~24.04.1
RUN printf '%s\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=0 -o Acquire::https::Timeout=30 -o Acquire::Queue-Mode=access -o APT::Update::Error-Mode=any -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::Retries=0 -o Acquire::https::Timeout=30 -o Acquire::Queue-Mode=access -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates apt=2.8.4ubuntu0.1 gcc-12=${GCC_PACKAGE_VERSION} g++-12=${GCC_PACKAGE_VERSION} cmake ninja-build ccache git \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; test "$(gcc-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    test "$(g++-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    mkdir -p /home/ci /workspace; chown -R 10001:10001 /home/ci /workspace
ENV CC=gcc-12 CXX=g++-12 HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/gcc-toolchain-smoke
RUN chmod 0755 /usr/local/bin/gcc-toolchain-smoke
LABEL org.opencontainers.image.title="CICD GCC 12 toolchain" org.opencontainers.image.version="12" org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" org.opencontainers.image.ubuntu.snapshot="20261105T000000Z"
USER 10001:10001
