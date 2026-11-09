FROM docker.io/library/ubuntu:26.04@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b

ARG DEBIAN_FRONTEND=noninteractive

# Freeze the Ubuntu 26.04 archive. The minimal base lacks CA roots, so bootstrap
# only ca-certificates with TLS peer verification disabled; APT still verifies
# signed Release metadata and package hashes. All subsequent traffic uses TLS.
RUN echo 'APT::Snapshot "20261107T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
       gcc-13=13.4.0-10ubuntu1 \
       g++-13=13.4.0-10ubuntu1 \
       cmake \
       ninja-build \
       ccache \
       git \
       python3 \
       python3-venv \
    && ln -sf /usr/bin/gcc-13 /usr/local/bin/gcc \
    && ln -sf /usr/bin/g++-13 /usr/local/bin/g++ \
    && python3 -m venv /opt/conan-venv \
    && /opt/conan-venv/bin/pip install --no-cache-dir "conan==2.34.0" \
    && ln -s /opt/conan-venv/bin/conan /usr/local/bin/conan \
    && rm -rf /var/lib/apt/lists/*

COPY smoke-test.sh /usr/local/bin/cpp-conan-smoke
RUN chmod 0755 /usr/local/bin/cpp-conan-smoke

ENV CONAN_HOME=/workspace/.cache/conan
ENV CC=gcc
ENV CXX=g++

WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD Ubuntu 26.04 GCC 13 + Conan 2.34.0 toolchain" \
      org.opencontainers.image.description="Candidate reproducible C++ dependency build environment" \
      org.opencontainers.image.base.digest="sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b" \
      org.opencontainers.image.ubuntu.snapshot="20261107T000000Z" \
      org.opencontainers.image.compiler.version="13.4.0-10ubuntu1"
