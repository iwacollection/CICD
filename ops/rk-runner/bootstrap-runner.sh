#!/usr/bin/env bash
set -euo pipefail

# Physical RK build-host bootstrap.
# This script intentionally registers the Runner to a PRIVATE RK product repository,
# not to the public central CICD repository.

RUNNER_VERSION="2.337.0"
RUNNER_ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
RUNNER_SHA256="70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_ARCHIVE}"
RUNNER_HOME="${RUNNER_HOME:-/opt/actions-runner-rk}"
RUNNER_USER="${RUNNER_USER:-gh-rk-runner}"
RUNNER_NAME="${RUNNER_NAME:-rk-builder-$(hostname -s)}"
RUNNER_LABELS="linux,x64,soc-rk"

: "${GITHUB_RUNNER_REPOSITORY_URL:?Set GITHUB_RUNNER_REPOSITORY_URL to the PRIVATE RK product repository URL}"
: "${GITHUB_RUNNER_TOKEN:?Set GITHUB_RUNNER_TOKEN to a short-lived registration token}"
: "${RK_SDK_ROOT:?Set RK_SDK_ROOT to the real Rockchip SDK/BSP root}"
: "${CI_RESOURCE_BROKER_URL:?Set CI_RESOURCE_BROKER_URL, normally http://127.0.0.1:8765}"
: "${CI_RESOURCE_BROKER_TOKEN:?Set CI_RESOURCE_BROKER_TOKEN for the local/central HIL broker}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: RK build Runner must be Linux" >&2
  exit 2
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "ERROR: RK build host must be x86_64. Target arm64 is cross-compiled and is not the Runner CPU architecture." >&2
  exit 2
fi
if [[ ! -d "$RK_SDK_ROOT" ]]; then
  echo "ERROR: RK_SDK_ROOT does not exist: $RK_SDK_ROOT" >&2
  exit 2
fi
if [[ "$GITHUB_RUNNER_REPOSITORY_URL" == "https://github.com/iwacollection/CICD"* ]]; then
  echo "ERROR: do not register the physical Self-hosted Runner to the public central CICD repository" >&2
  exit 2
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl tar git python3 ccache usbutils jq
else
  echo "ERROR: current bootstrap supports Debian/Ubuntu x86_64 hosts with apt-get" >&2
  exit 2
fi

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$RUNNER_USER"
fi

install -d -o "$RUNNER_USER" -g "$RUNNER_USER" "$RUNNER_HOME"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

curl --fail --location --proto '=https' --tlsv1.2 "$RUNNER_URL" -o "$tmp_dir/$RUNNER_ARCHIVE"
echo "$RUNNER_SHA256  $tmp_dir/$RUNNER_ARCHIVE" | sha256sum --check --strict

tar -xzf "$tmp_dir/$RUNNER_ARCHIVE" -C "$RUNNER_HOME"
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME"

# Runner process environment. Credentials live only on the host and are chmod 0600.
cat >"$RUNNER_HOME/.env" <<EOF
RK_SDK_ROOT=$RK_SDK_ROOT
CI_RESOURCE_BROKER_URL=$CI_RESOURCE_BROKER_URL
CI_RESOURCE_BROKER_TOKEN=$CI_RESOURCE_BROKER_TOKEN
EOF
chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.env"
chmod 0600 "$RUNNER_HOME/.env"

sudo -u "$RUNNER_USER" bash -lc "cd '$RUNNER_HOME' && ./config.sh --unattended --replace --url '$GITHUB_RUNNER_REPOSITORY_URL' --token '$GITHUB_RUNNER_TOKEN' --name '$RUNNER_NAME' --labels '$RUNNER_LABELS' --work _work"

cd "$RUNNER_HOME"
./svc.sh install "$RUNNER_USER"
./svc.sh start

printf '\nRK physical Runner bootstrap completed.\n'
printf 'Runner version: %s\n' "$RUNNER_VERSION"
printf 'Runner name: %s\n' "$RUNNER_NAME"
printf 'Repository: %s\n' "$GITHUB_RUNNER_REPOSITORY_URL"
printf 'Labels: self-hosted, linux, x64, soc-rk\n'
printf 'Target architecture remains arm64.\n'
printf 'Next: create SDK identity, start HIL broker, then run RK SDK Enrollment / RK Hardware Runner Readiness.\n'
