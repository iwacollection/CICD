FROM golang:1.26.10-alpine3.23@sha256:264964005eb4582789b23e5c578b6d46096287ed7c6441b163b1d27e82dac041

ARG GO_VERSION=1.26.10
ARG GO_SHA256=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5
ARG GOTESTSUM_VERSION=v1.13.0
ARG X_MOD_VERSION=v0.40.0
ARG X_TEXT_VERSION=v0.41.0

# Alpine avoids carrying Debian's vulnerable kernel-header and general-purpose
# package set. build-base provides GCC, musl-dev headers and make for CGO.
RUN set -eux; \
    apk add --no-cache build-base ca-certificates curl git; \
    test "$(go env GOVERSION)" = "go${GO_VERSION}"; \
    command -v gcc; \
    command -v git; \
    command -v make; \
    command -v curl; \
    test -r /usr/include/stdio.h; \
    go_archive="go${GO_VERSION}.linux-amd64.tar.gz"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 900 \
      "https://go.dev/dl/${go_archive}" --output "/tmp/${go_archive}"; \
    printf '%s  %s\n' "${GO_SHA256}" "/tmp/${go_archive}" | sha256sum -c -; \
    rm -f "/tmp/${go_archive}"; \
    tooling_dir="$(mktemp -d)"; \
    mkdir -p /tmp/gopath /tmp/gocache; \
    cd "$tooling_dir"; \
    GOTOOLCHAIN=local GOPATH=/tmp/gopath GOCACHE=/tmp/gocache go mod init cicd-toolchain-tools; \
    GOTOOLCHAIN=local GOPATH=/tmp/gopath GOCACHE=/tmp/gocache go get "gotest.tools/gotestsum@${GOTESTSUM_VERSION}"; \
    GOTOOLCHAIN=local GOPATH=/tmp/gopath GOCACHE=/tmp/gocache go get "golang.org/x/mod@${X_MOD_VERSION}" "golang.org/x/text@${X_TEXT_VERSION}"; \
    GOTOOLCHAIN=local GOPATH=/tmp/gopath GOBIN=/usr/local/bin GOCACHE=/tmp/gocache \
      go install gotest.tools/gotestsum; \
    cd /; \
    rm -rf "$tooling_dir" /tmp/gopath /tmp/gocache /root/go/pkg/mod /root/.cache/go-build; \
    rm -f /usr/local/go/src/crypto/internal/boring/Dockerfile /usr/local/go/src/crypto/internal/fips140/nistec/fiat/Dockerfile; \
    git config --system --add safe.directory /workspace; \
    mkdir -p /home/ci /workspace; \
    chown -R 10001:10001 /home/ci /workspace

ENV GO_TOOLCHAIN_VERSION=${GO_VERSION} \
    GOTESTSUM_TOOLCHAIN_VERSION=${GOTESTSUM_VERSION} \
    GOTESTSUM_X_MOD_VERSION=${X_MOD_VERSION} \
    GOTESTSUM_X_TEXT_VERSION=${X_TEXT_VERSION} \
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
      org.opencontainers.image.description="Pinned official Go build image with cgo (GCC/musl), git, make and gotestsum" \
      org.opencontainers.image.base.name="golang:1.26.10-alpine3.23" \
      org.opencontainers.image.base.digest="sha256:264964005eb4582789b23e5c578b6d46096287ed7c6441b163b1d27e82dac041" \
      org.opencontainers.image.version="1.26.10" \
      org.opencontainers.image.vendor="The Go Authors / gotestsum"

USER 10001:10001
