FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
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
      org.opencontainers.image.description="Reproducible C/C++ CI build environment"
