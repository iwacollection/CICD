FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive

# Freeze the entire Ubuntu archive dependency graph at one UTC snapshot.
# 2026-08-28 is deliberately after the Ubuntu 24.04 kernel security updates
# that fixed CVE-2026-53224 / CVE-2026-64531 in linux 6.8.0-138.138.
# The minimal Ubuntu base does not contain CA certificates. The first two APT
# commands therefore disable only HTTPS peer verification while APT still
# verifies the fixed Snapshot metadata and packages through Ubuntu's archive
# signing key and package hashes. Once ca-certificates is installed, all
# subsequent Snapshot access returns to normal TLS verification.
RUN printf '%s\n' 'APT::Snapshot "20260828T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
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
