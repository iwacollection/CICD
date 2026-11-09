FROM ghcr.io/iwacollection/cicd-toolchain-gcc-host@sha256:3caf311bd69a8faf218417ec729acdcfb0cd6f164a0fe55a6c041ac20e4fe012

ARG DEBIAN_FRONTEND=noninteractive

# Move inherited Ubuntu packages onto the reviewed 2026-11-05 snapshot.
# Pin packages that the previous Trivy report identified as HIGH/CRITICAL.
RUN echo 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get update \
    && apt-get --allow-downgrades install -y --no-install-recommends \
       apt=2.8.4ubuntu0.1 \
       libapt-pkg6.0t64=2.8.4ubuntu0.1 \
       linux-libc-dev=6.8.0-147.147 \
       libbrotli1=1.1.0-2ubuntu0.1 \
       libssl3t64=3.0.13-0ubuntu3.16 \
       openssl=3.0.13-0ubuntu3.16 \
       python3 \
       python3-venv \
    && python3 -m venv /opt/conan-venv \
    && /opt/conan-venv/bin/pip install --no-cache-dir "conan==2.34.0" \
    && ln -s /opt/conan-venv/bin/conan /usr/local/bin/conan \
    && rm -rf /var/lib/apt/lists/*

COPY smoke-test.sh /usr/local/bin/cpp-conan-smoke
RUN chmod 0755 /usr/local/bin/cpp-conan-smoke

ENV CONAN_HOME=/workspace/.cache/conan
WORKDIR /workspace

LABEL org.opencontainers.image.title="CICD GCC 13 + Conan 2.34.0 toolchain" \
      org.opencontainers.image.description="Candidate reproducible C++ dependency build environment" \
      org.opencontainers.image.base.digest="sha256:3caf311bd69a8faf218417ec729acdcfb0cd6f164a0fe55a6c041ac20e4fe012" \
      org.opencontainers.image.ubuntu.snapshot="20261105T000000Z"
