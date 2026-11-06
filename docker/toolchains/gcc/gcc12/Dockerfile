FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ARG DEBIAN_FRONTEND=noninteractive
ARG GCC_PACKAGE_VERSION=12.5.0-9ubuntu1
RUN printf '%s\n' 'APT::Snapshot "20260828T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc-12=${GCC_PACKAGE_VERSION} g++-12=${GCC_PACKAGE_VERSION} cmake ninja-build ccache git \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; test "$(gcc-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    test "$(g++-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"; \
    mkdir -p /home/ci /workspace; chown -R 10001:10001 /home/ci /workspace
ENV CC=gcc-12 CXX=g++-12 HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/gcc-toolchain-smoke
RUN chmod 0755 /usr/local/bin/gcc-toolchain-smoke
LABEL org.opencontainers.image.title="CICD GCC 12 toolchain" org.opencontainers.image.version="12" org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" org.opencontainers.image.ubuntu.snapshot="20260828T000000Z"
USER 10001:10001
