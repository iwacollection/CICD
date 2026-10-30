FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive

# Freeze Ubuntu packages to a known archive state. Ubuntu 24.04 supports
# Snapshot IDs directly through APT; the first install bootstraps CA roots.
RUN printf '%s\n' 'APT::Snapshot "20261029T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Eclipse Temurin JDK 17.0.21+9. The SHA256 is the release asset digest
# published by the adoptium/temurin17-binaries GitHub release.
ARG TEMURIN_VERSION=17.0.21
ARG TEMURIN_BUILD=9
ARG TEMURIN_SHA256=8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711

# Apache Maven 3.10.0 GA. Verify the official Apache SHA512 before extraction.
ARG MAVEN_VERSION=3.10.0
ARG MAVEN_SHA512=908b1501bfb420bf7c8affb855534a9c407fd6099367bfb9f2f2dcb8e9799102bffb84518cde74c679bd76870247c6528683abdd620581bffa90f95d92d175aa

RUN set -eux; \
    jdk_archive="OpenJDK17U-jdk_x64_linux_hotspot_${TEMURIN_VERSION}_${TEMURIN_BUILD}.tar.gz"; \
    jdk_url="https://github.com/adoptium/temurin17-binaries/releases/download/jdk-${TEMURIN_VERSION}%2B${TEMURIN_BUILD}/${jdk_archive}"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 900 \
      "${jdk_url}" --output "/tmp/${jdk_archive}"; \
    printf '%s  %s\n' "${TEMURIN_SHA256}" "/tmp/${jdk_archive}" | sha256sum --check -; \
    mkdir -p /opt/java/openjdk; \
    tar -xzf "/tmp/${jdk_archive}" --directory /opt/java/openjdk --strip-components=1 --no-same-owner; \
    rm -f "/tmp/${jdk_archive}"; \
    maven_archive="apache-maven-${MAVEN_VERSION}-bin.tar.gz"; \
    maven_url="https://dlcdn.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/${maven_archive}"; \
    maven_sha_url="https://downloads.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/${maven_archive}.sha512"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 600 \
      "${maven_url}" --output "/tmp/${maven_archive}"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 60 \
      "${maven_sha_url}" --output "/tmp/${maven_archive}.sha512"; \
    official_maven_sha512="$(awk 'NF { print $1; exit }' "/tmp/${maven_archive}.sha512")"; \
    test "${official_maven_sha512}" = "${MAVEN_SHA512}"; \
    printf '%s  %s\n' "${MAVEN_SHA512}" "/tmp/${maven_archive}" | sha512sum --check -; \
    mkdir -p /opt/maven; \
    tar -xzf "/tmp/${maven_archive}" --directory /opt/maven --strip-components=1 --no-same-owner; \
    rm -f "/tmp/${maven_archive}" "/tmp/${maven_archive}.sha512"; \
    mkdir -p /home/ci/.m2 /workspace; \
    chown -R 10001:10001 /home/ci /workspace; \
    chmod 0755 /opt/java /opt/java/openjdk /opt/maven

ENV JAVA_HOME=/opt/java/openjdk \
    MAVEN_HOME=/opt/maven \
    HOME=/home/ci \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/opt/java/openjdk/bin:/opt/maven/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace

COPY smoke-test.sh /usr/local/bin/java-toolchain-smoke
RUN chmod 0755 /usr/local/bin/java-toolchain-smoke

LABEL org.opencontainers.image.title="CICD Java 17 Maven toolchain" \
      org.opencontainers.image.description="Pinned Eclipse Temurin JDK 17 and Apache Maven build environment" \
      org.opencontainers.image.base.name="ubuntu:24.04" \
      org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517" \
      org.opencontainers.image.version="17.0.21+9-maven-3.10.0" \
      org.opencontainers.image.vendor="Eclipse Temurin / Apache Maven"

USER 10001:10001
