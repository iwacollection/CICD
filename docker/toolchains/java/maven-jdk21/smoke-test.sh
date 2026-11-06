#!/usr/bin/env bash
set -euo pipefail
test "$(java -version 2>&1 | sed -n '1s/.*version "\([^"]*\)".*/\1/p')" = "21.0.12.1"
test "$(mvn --version 2>&1 | sed -n '1s/^Apache Maven \([^ ]*\).*/\1/p')" = "3.10.0"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/Smoke.java" <<'JAVA'
public class Smoke { public static void main(String[] args) { System.out.println("jdk21-maven-smoke-ok"); } }
JAVA
javac "$tmp/Smoke.java"
java -cp "$tmp" Smoke | grep -Fx jdk21-maven-smoke-ok
