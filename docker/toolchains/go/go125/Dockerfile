FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ARG DEBIAN_FRONTEND=noninteractive
RUN printf '%s\\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \\
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=60 -o APT::Update::Error-Mode=any update \\
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=60 install -y --no-install-recommends ca-certificates apt=2.8.4ubuntu0.1 curl git build-essential \\
    && rm -rf /var/lib/apt/lists/*
ARG GO_VERSION=1.25.14
ARG GO_SHA256=a21ae5633a269bcd7e90cf767e48225633795e99d831742cbf3397064fee7712
RUN set -eux; archive="go${GO_VERSION}.linux-amd64.tar.gz"; \\
    curl --fail --show-error --location --retry 3 --retry-all-errors "https://go.dev/dl/${archive}" -o "/tmp/${archive}"; \\
    printf '%s  %s\\n' "${GO_SHA256}" "/tmp/${archive}" | sha256sum --check -; \\
    tar -C /usr/local -xzf "/tmp/${archive}"; rm "/tmp/${archive}"; \\
    mkdir -p /home/ci /workspace; chown -R 10001:10001 /home/ci /workspace
ENV GOROOT=/usr/local/go GOPATH=/workspace/.cache/go GOCACHE=/workspace/.cache/go-build GOTOOLCHAIN=local CGO_ENABLED=1 GOFLAGS="-trimpath -buildvcs=false" HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/go-toolchain-smoke
RUN chmod 0755 /usr/local/bin/go-toolchain-smoke
LABEL org.opencontainers.image.title="CICD Go 1.25 toolchain" org.opencontainers.image.version="1.25.14" org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517"
USER 10001:10001
