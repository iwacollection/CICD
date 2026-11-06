#!/usr/bin/env bash
# Smoke test for the Go toolchain image. Runs offline: the fixture has no
# third-party dependencies. Executed before publication and against the exact
# published digest.
set -euo pipefail

expected_go="go${GO_TOOLCHAIN_VERSION:?GO_TOOLCHAIN_VERSION is not set in the image}"
expected_gotestsum="${GOTESTSUM_TOOLCHAIN_VERSION:?GOTESTSUM_TOOLCHAIN_VERSION is not set in the image}"
expected_x_mod="${GOTESTSUM_X_MOD_VERSION:?GOTESTSUM_X_MOD_VERSION is not set in the image}"
expected_x_text="${GOTESTSUM_X_TEXT_VERSION:?GOTESTSUM_X_TEXT_VERSION is not set in the image}"

actual_go="$(go env GOVERSION)"
if [[ "$actual_go" != "$expected_go" ]]; then
  echo "Go version mismatch: expected $expected_go, got $actual_go" >&2
  exit 1
fi

for pair in GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local CGO_ENABLED=1; do
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

module_info="$(go version -m "$(command -v gotestsum)")"
grep -F "golang.org/x/mod $expected_x_mod" <<<"$module_info" >/dev/null
grep -F "golang.org/x/text $expected_x_text" <<<"$module_info" >/dev/null
if command -v apt-get >/dev/null 2>&1; then
  echo "apt-get must not be present in the Go build-only image" >&2
  exit 1
fi

gcc --version | sed -n '1p'
make --version | sed -n '1p'
git --version
curl --version | sed -n '1p'
test -r /usr/include/stdio.h

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

# A package that imports "C" is excluded when CGO_ENABLED=0, so the static
# build and test checks use a separate pure-Go package.
mkdir static
cat > static/main.go <<'GOSTATIC'
package main

import "fmt"

func main() {
	fmt.Println("go-toolchain-smoke-static-ok")
}
GOSTATIC

cat > static/main_test.go <<'GOSTATICTEST'
package main

import "testing"

func TestStaticSmoke(t *testing.T) {
	if got := 20 + 22; got != 42 {
		t.Fatalf("unexpected result: %d", got)
	}
}
GOSTATICTEST

CGO_ENABLED=0 go build -o smoke-static ./static
./smoke-static | grep -Fx "go-toolchain-smoke-static-ok"

# Keep the build mode explicit. CGO_ENABLED is process-scoped in the earlier
# build command; without this prefix, vet/test could see a different package
# file set and report "build constraints exclude all Go files".
CGO_ENABLED=1 go vet ./...
CGO_ENABLED=1 gotestsum --junitfile smoke-junit.xml --format testname -- .
test -s smoke-junit.xml
grep -q "<testsuite" smoke-junit.xml

CGO_ENABLED=0 gotestsum --junitfile static-junit.xml --format testname -- ./static
test -s static-junit.xml
grep -q "<testsuite" static-junit.xml

echo "go-toolchain-smoke: all checks passed"
