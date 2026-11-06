#!/usr/bin/env bash
# Smoke test for the Go toolchain image. Runs offline: the fixture has no
# third-party dependencies. Executed by the Toolchain Supply Chain workflow
# before the image is published, and again against the exact published digest.
set -euo pipefail

expected_go="go${GO_TOOLCHAIN_VERSION:?GO_TOOLCHAIN_VERSION is not set in the image}"
expected_gotestsum="${GOTESTSUM_TOOLCHAIN_VERSION:?GOTESTSUM_TOOLCHAIN_VERSION is not set in the image}"

actual_go="$(go env GOVERSION)"
if [[ "$actual_go" != "$expected_go" ]]; then
  echo "Go version mismatch: expected $expected_go, got $actual_go" >&2
  exit 1
fi

for pair in GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local; do
  key="${pair%%=*}"
  want="${pair#*=}"
  got="$(go env "$key")"
  if [[ "$got" != "$want" ]]; then
    echo "go env $key mismatch: expected $want, got $got" >&2
    exit 1
  fi
done

gotestsum --version | grep -F "${expected_gotestsum#v}" >/dev/null || {
  echo "gotestsum version mismatch: expected $expected_gotestsum" >&2
  gotestsum --version >&2 || true
  exit 1
}

gcc --version | head -n 1
git --version

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

cat > go.mod <<'GOMOD'
module example.com/toolchain-smoke

go 1.26
GOMOD

# cgo: proves gcc and glibc headers are usable, not just present.
cat > main.go <<'GOMAIN'
package main

/*
static int add(int a, int b) { return a + b; }
*/
import "C"

import "fmt"

func main() {
	fmt.Println("go-toolchain-smoke-ok", C.add(20, 22))
}
GOMAIN

cat > main_test.go <<'GOTEST'
package main

import "testing"

func TestSmoke(t *testing.T) {
	if got := 20 + 22; got != 42 {
		t.Fatalf("unexpected result: %d", got)
	}
}
GOTEST

CGO_ENABLED=1 go build -o smoke-cgo .
./smoke-cgo | grep -Fx "go-toolchain-smoke-ok 42"

CGO_ENABLED=0 go build -o smoke-static .

go vet ./...
gotestsum --junitfile smoke-junit.xml --format testname -- ./...
test -s smoke-junit.xml
grep -q "<testsuite" smoke-junit.xml

echo "go-toolchain-smoke: all checks passed"
