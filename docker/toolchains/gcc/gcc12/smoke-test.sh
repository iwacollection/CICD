#!/usr/bin/env bash
set -euo pipefail
test "$(dpkg-query -W -f='${Version}' gcc-12)" = "12.4.0-2ubuntu1~24.04.1"
test "$(dpkg-query -W -f='${Version}' g++-12)" = "12.4.0-2ubuntu1~24.04.1"
test "$(gcc-12 -dumpfullversion -dumpversion)" = "12.4.0"
test "$(g++-12 -dumpfullversion -dumpversion)" = "12.4.0"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/main.cpp" <<'CPP'
#include <iostream>
int main() { std::cout << "gcc12-cxx-smoke-ok\\n"; }
CPP
g++-12 -std=c++20 -Wall -Wextra -Werror "$tmp/main.cpp" -o "$tmp/smoke"
"$tmp/smoke" | grep -Fx gcc12-cxx-smoke-ok
