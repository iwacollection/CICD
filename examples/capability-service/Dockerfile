FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive
# Use HTTPS and disable retries while updating APT. The fixed APT/OpenSSL
# package versions remediate CVE-2026-109518 and CVE-2026-84782.
RUN set -eux; \
    printf '%s\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot; \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update; \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::Retries=0 install -y --no-install-recommends ca-certificates; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update; \
    apt-get -o Acquire::Retries=0 install -y --no-install-recommends \
      apt=2.8.4ubuntu0.1 \
      libapt-pkg6.0t64=2.8.4ubuntu0.1 \
      libssl3t64=3.0.13-0ubuntu3.16; \
    rm -rf /var/lib/apt/lists/*

USER 65532:65532
CMD ["sh", "-c", "echo capability-container-smoke"]
