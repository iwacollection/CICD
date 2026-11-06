FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517
ARG DEBIAN_FRONTEND=noninteractive
RUN printf '%s\n' 'APT::Snapshot "20261105T000000Z";' > /etc/apt/apt.conf.d/50snapshot \
    && apt-get -o Acquire::Retries=0 -o Acquire::https::Timeout=30 -o Acquire::Queue-Mode=access -o APT::Update::Error-Mode=any -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::Retries=0 -o Acquire::https::Timeout=30 -o Acquire::Queue-Mode=access -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates apt=2.8.4ubuntu0.1 curl \
    && rm -rf /var/lib/apt/lists/*
ARG TEMURIN_VERSION=21.0.12.1
ARG TEMURIN_BUILD=1
ARG TEMURIN_SHA256=ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94
ARG MAVEN_VERSION=3.10.0
ARG MAVEN_SHA512=908b1501bfb420bf7c8affb855534a9c407fd6099367bfb9f2f2dcb8e9799102bffb84518cde74c679bd76870247c6528683abdd620581bffa90f95d92d175aa
ARG GUAVA_VERSION=33.7.2-jre
ARG GUAVA_SHA256=b530942257fb935f8b2cfaa5f8eb5bd59c555fd8e8d01b8ce98912e077ea606c
RUN set -eux; \
    jdk_archive="OpenJDK21U-jdk_x64_linux_hotspot_${TEMURIN_VERSION}_${TEMURIN_BUILD}.tar.gz"; \
    jdk_url="https://github.com/adoptium/temurin21-binaries/releases/download/jdk-${TEMURIN_VERSION}%2B${TEMURIN_BUILD}/${jdk_archive}"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors "${jdk_url}" --output "/tmp/${jdk_archive}"; \
    printf '%s  %s\n' "${TEMURIN_SHA256}" "/tmp/${jdk_archive}" | sha256sum --check -; \
    mkdir -p /opt/java/openjdk; tar -xzf "/tmp/${jdk_archive}" -C /opt/java/openjdk --strip-components=1 --no-same-owner; rm "/tmp/${jdk_archive}"; \
    maven_archive="apache-maven-${MAVEN_VERSION}-bin.tar.gz"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors "https://dlcdn.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/${maven_archive}" -o "/tmp/${maven_archive}"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors "https://downloads.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/${maven_archive}.sha512" -o "/tmp/${maven_archive}.sha512"; \
    test "$(awk 'NF {print $1; exit}' "/tmp/${maven_archive}.sha512")" = "${MAVEN_SHA512}"; \
    printf '%s  %s\n' "${MAVEN_SHA512}" "/tmp/${maven_archive}" | sha512sum --check -; \
    mkdir -p /opt/maven; tar -xzf "/tmp/${maven_archive}" -C /opt/maven --strip-components=1 --no-same-owner; \
    rm -f "/tmp/${maven_archive}" "/tmp/${maven_archive}.sha512"; \
    guava_archive="guava-${GUAVA_VERSION}.jar"; \
    curl --fail --show-error --location --retry 3 --retry-all-errors "https://repo.maven.apache.org/maven2/com/google/guava/guava/${GUAVA_VERSION}/${guava_archive}" -o "/tmp/${guava_archive}"; \
    printf '%s  %s\n' "${GUAVA_SHA256}" "/tmp/${guava_archive}" | sha256sum --check -; \
    rm -f /opt/maven/lib/guava-33.7.1-jre.jar; install -m 0644 "/tmp/${guava_archive}" "/opt/maven/lib/${guava_archive}"; rm "/tmp/${guava_archive}"; \
    mkdir -p /home/ci/.m2 /workspace; chown -R 10001:10001 /home/ci /workspace; chmod 0755 /opt/java /opt/java/openjdk /opt/maven
ENV JAVA_HOME=/opt/java/openjdk MAVEN_HOME=/opt/maven HOME=/home/ci LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/opt/java/openjdk/bin:/opt/maven/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace
COPY smoke-test.sh /usr/local/bin/java-toolchain-smoke
RUN chmod 0755 /usr/local/bin/java-toolchain-smoke
LABEL org.opencontainers.image.title="CICD Java 21 Maven toolchain" org.opencontainers.image.version="21.0.12.1+1-maven-3.10.0" org.opencontainers.image.base.digest="sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517"
USER 10001:10001
