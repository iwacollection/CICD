# The pinned GCC base image configures APT::Snapshot "20260828T000000Z".
# Keep this image separate from Active GCC: promotion is controlled by the
# repository's normal image verification, SBOM, scan and provenance workflow.
FROM ghcr.io/iwacollection/cicd-toolchain-gcc-host@sha256:3caf311bd69a8faf218417ec729acdcfb0cd6f164a0fe55a6c041ac20e4fe012

ARG DEBIAN_FRONTEND=noninteractive
ARG CONAN_VERSION=2.34.0
ARG CONAN_DEB_SHA256=94d34280e3915535bd73ac9e6896b11a5d0dea31b6c04f8c1c1acf9bf61013a1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl; \
    curl --fail --location --retry 3 \
      "https://github.com/conan-io/conan/releases/download/\${CONAN_VERSION}/conan-\${CONAN_VERSION}-amd64.deb" \
      --output /tmp/conan.deb; \
    echo "\${CONAN_DEB_SHA256}  /tmp/conan.deb" | sha256sum --check --status; \
    apt-get install -y --no-install-recommends /tmp/conan.deb; \
    test "$(conan --version)" = "Conan version \${CONAN_VERSION}"; \
    rm -f /tmp/conan.deb; \
    rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="CICD GCC 13 + Conan 2.34.0 candidate" \
      org.opencontainers.image.description="Candidate C++ build image; not Active until independently verified and promoted" \
      org.opencontainers.image.base.name="ghcr.io/iwacollection/cicd-toolchain-gcc-host" \
      org.opencontainers.image.base.digest="sha256:3caf311bd69a8faf218417ec729acdcfb0cd6f164a0fe55a6c041ac20e4fe012" \
      org.opencontainers.image.conan.version="2.34.0"
