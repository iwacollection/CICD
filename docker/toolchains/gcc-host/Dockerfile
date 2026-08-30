FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive

# Freeze the entire Ubuntu archive dependency graph at one UTC snapshot.
# Updating this timestamp is an explicit supply-chain change reviewed through PR + Toolchain gate.
RUN printf '%s\n' 'APT::Snapshot "20260810T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       bash \
       ca-certificates \
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
      org.opencontainers.image.ubuntu.snapshot="20260810T000000Z"
