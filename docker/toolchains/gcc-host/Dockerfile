ARG BASE_IMAGE=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG UBUNTU_SNAPSHOT=20260810T000000Z
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get -S "${UBUNTU_SNAPSHOT}" update \
    && apt-get -S "${UBUNTU_SNAPSHOT}" install -y --no-install-recommends \
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
      org.opencontainers.image.base.name="${BASE_IMAGE}" \
      io.iwacollection.ubuntu.snapshot="${UBUNTU_SNAPSHOT}"
