#!/usr/bin/env bash
set -euo pipefail
test "$(go env GOVERSION)" = "go1.25.14"
test "$(go env GOTOOLCHAIN)" = "local"
test "$(go env CGO_ENABLED)" = "1"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
cat > go.mod <<'EOF'
module toolchain-smoke
go 1.25.0
EOF
cat > smoke_test.go <<'EOF'
package smoke
import "testing"
func TestToolchain(t *testing.T) { if 1+1 != 2 { t.Fatal("bad arithmetic") } }
EOF
go test -v ./...
