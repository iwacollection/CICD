FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ARG DEBIAN_FRONTEND=noninteractive
RUN printf '%s\n' 'APT::Snapshot "20261029T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl git build-essential \
    && rm -rf /var/lib/apt/lists/*
ARG GO_VERSION=1.25.12
ARG GO_SHA256=234828b7a89e0e303d2556310ee549fbcf253d28de937bac3da13d6294262ac1
RUN set -eux; archive="go${GO_VERSION}.linux-amd64.tar.gz"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors "https://go.dev/dl/${archive}" -o "/tmp/${archive}"; \
    printf '%s  %s\\n' "${GO_SHA256}" "/tmp/${archive}" | sha256sum --check -; \
    tar -C /usr/local -xzf "/tmp/${archive}"; rm "/tmp/${archive}"; \
    mkdir -p /home/ci /workspace; chown -R 10001:10001 /home/ci /workspace
ENV GOROOT=/usr/local/go GOPATH=/workspace/.cache/go GOCACHE=/workspace/.cache/go-build GOTOOLCHAIN=local CGO_ENABLED=1 GOFLAGS="-trimpath -buildvcs=false" HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/go-toolchain-smoke
RUN chmod 0755 /usr/local/bin/go-toolchain-smoke
LABEL org.opencontainers.image.title="CICD Go 1.25 toolchain" org.opencontainers.image.version="1.25.12" org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517"
USER 10001:10001
