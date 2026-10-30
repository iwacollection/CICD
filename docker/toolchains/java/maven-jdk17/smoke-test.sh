#!/usr/bin/env bash
set -euo pipefail

EXPECTED_JDK_VERSION="17.0.21"
EXPECTED_MAVEN_VERSION="3.10.0"

if [[ "$(id -u)" != "10001" ]]; then
  echo "Expected non-root UID 10001, got $(id -u)" >&2
  exit 1
fi

actual_jdk_version="$(java -version 2>&1 | sed -n '1s/.*version "\([^"]*\)".*/\1/p')"
actual_jdk_spec="$(java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/^[[:space:]]*java.specification.version =/ { print $2; exit }')"
actual_maven_version="$(mvn --version 2>&1 | sed -n '1s/^Apache Maven \([^ ]*\).*/\1/p')"

test "$actual_jdk_version" = "$EXPECTED_JDK_VERSION" || {
  echo "JDK mismatch: expected $EXPECTED_JDK_VERSION, got $actual_jdk_version" >&2
  exit 1
}
test "$actual_jdk_spec" = "17" || {
  echo "Java specification mismatch: expected 17, got $actual_jdk_spec" >&2
  exit 1
}
test "$actual_maven_version" = "$EXPECTED_MAVEN_VERSION" || {
  echo "Maven mismatch: expected $EXPECTED_MAVEN_VERSION, got $actual_maven_version" >&2
  exit 1
}

guava_jars=(/opt/maven/lib/guava-*.jar)
if [[ "${#guava_jars[@]}" -ne 1 || "$(basename "${guava_jars[0]}")" != "guava-33.7.2-jre.jar" ]]; then
  printf 'Expected exactly one patched Guava 33.7.2 jar in Maven lib; found: %s\\n' "${guava_jars[*]}" >&2
  exit 1
fi

test -w "$HOME/.m2"
test -w "$PWD"

smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
mkdir -p "$smoke_dir/src/main/java/com/example"

cat > "$smoke_dir/src/main/java/com/example/ToolchainSmoke.java" <<'JAVA'
package com.example;

public final class ToolchainSmoke {
    private ToolchainSmoke() {}

    public static void main(String[] args) {
        String version = System.getProperty("java.specification.version");
        if (!"17".equals(version)) {
            throw new IllegalStateException("Expected Java 17, got " + version);
        }
        System.out.println("java-toolchain-smoke:" + version);
    }
}
JAVA

cat > "$smoke_dir/pom.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>java-toolchain-smoke</artifactId>
  <version>1.0.0</version>
  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <maven.compiler.release>17</maven.compiler.release>
  </properties>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-clean-plugin</artifactId>
        <version>3.4.1</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-resources-plugin</artifactId>
        <version>3.3.1</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>3.14.1</version>
        <configuration>
          <release>17</release>
        </configuration>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.5.4</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-jar-plugin</artifactId>
        <version>3.4.2</version>
        <configuration>
          <archive>
            <manifest>
              <mainClass>com.example.ToolchainSmoke</mainClass>
            </manifest>
          </archive>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
XML

mvn --batch-mode --no-transfer-progress \
  --file "$smoke_dir/pom.xml" \
  clean package

smoke_output="$(java -jar "$smoke_dir/target/java-toolchain-smoke-1.0.0.jar")"
printf '%s\n' "$smoke_output"
grep -Fxq "java-toolchain-smoke:17" <<< "$smoke_output"

echo "PASS: JDK $actual_jdk_version + Maven $actual_maven_version built and executed a Java 17 JAR as UID 10001."
