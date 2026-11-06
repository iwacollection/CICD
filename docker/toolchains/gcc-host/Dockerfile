FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive

# Pin the post-USN-8996-1 snapshot and install fixed APT/OpenSSL packages.
# Retries are disabled until the fixed APT package is installed; this avoids
# the retry condition in CVE-2026-109518 during the CA bootstrap.
RUN apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends \
       apt=2.8.4ubuntu0.1 \
       libapt-pkg6.0t64=2.8.4ubuntu0.1 \
       libssl3t64=3.0.13-0ubuntu3.16 \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
       bash \
       build-essential \
       cmake \
       ninja-build \
       ccache \
       git \
    && rm -rf /var/lib/apt/lists/*

ENV CI=true \
    CCACHE_DIR=/workspace/.cache/ccache

WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD gcc-host toolchain" \
      org.opencontainers.image.description="Reproducible C/C++ CI build environment" \
      org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" \
      org.opencontainers.image.ubuntu.snapshot="20260828T000000Z"
