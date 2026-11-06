FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive

RUN printf '%s\n' 'APT::Snapshot "20261029T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       bash curl git gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*

ARG GO_VERSION=1.26.10
ARG GO_SHA256=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5
ARG GOTESTSUM_VERSION=v1.13.0

RUN set -eux; \
    go_archive="go${GO_VERSION}.linux-amd64.tar.gz"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 900 \
      "https://go.dev/dl/${go_archive}" --output "/tmp/${go_archive}"; \
    printf '%s  %s\n' "${GO_SHA256}" "/tmp/${go_archive}" | sha256sum --check -; \
    tar -xzf "/tmp/${go_archive}" --directory /usr/local --no-same-owner; \
    rm -f "/tmp/${go_archive}"; \
    GOTOOLCHAIN=local GOPATH=/tmp/gopath GOBIN=/usr/local/bin GOCACHE=/tmp/gocache \
      /usr/local/go/bin/go install "gotest.tools/gotestsum@${GOTESTSUM_VERSION}"; \
    chmod -R u+w /tmp/gopath; \
    rm -rf /tmp/gopath /tmp/gocache; \
    git config --system --add safe.directory /workspace; \
    mkdir -p /home/ci /workspace; \
    chown -R 10001:10001 /home/ci /workspace

ENV GO_TOOLCHAIN_VERSION=${GO_VERSION} \
    GOTESTSUM_TOOLCHAIN_VERSION=${GOTESTSUM_VERSION} \
    GOROOT=/usr/local/go \
    GOPATH=/workspace/.cache/go \
    GOCACHE=/workspace/.cache/go-build \
    GOTOOLCHAIN=local \
    CGO_ENABLED=1 \
    GOFLAGS="-trimpath -buildvcs=false" \
    HOME=/home/ci \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/go-toolchain-smoke
RUN chmod 0755 /usr/local/bin/go-toolchain-smoke

LABEL org.opencontainers.image.title="CICD Go 1.26 toolchain" \
      org.opencontainers.image.description="Pinned Go toolchain with cgo (gcc), git and gotestsum" \
      org.opencontainers.image.base.name="ubuntu:24.04" \
      org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" \
      org.opencontainers.image.version="1.26.10" \
      org.opencontainers.image.vendor="The Go Authors / gotestsum"

USER 10001:10001
