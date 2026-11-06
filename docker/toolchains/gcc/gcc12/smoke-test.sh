#!/usr/bin/env bash
set -euo pipefail
test "$(gcc-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"
test "$(g++-12 -dumpfullversion -dumpversion | cut -d. -f1)" = "12"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/main.cpp" <<'CPP'
#include <iostream>
int main() { std::cout << "gcc12-cxx-smoke-ok\\n"; }
CPP
g++-12 -std=c++20 -Wall -Wextra -Werror "$tmp/main.cpp" -o "$tmp/smoke"
"$tmp/smoke" | grep -Fx gcc12-cxx-smoke-ok
