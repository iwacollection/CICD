#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_USER="${RUNNER_USER:-gh-rk-runner}"
INVENTORY_SOURCE="${INVENTORY_SOURCE:-$SCRIPT_DIR/hil-inventory.example.json}"
: "${CI_RESOURCE_BROKER_TOKEN:?Set CI_RESOURCE_BROKER_TOKEN to a random secret with at least 32 characters}"

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run install-hil-broker.sh as root" >&2
  exit 2
fi
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  echo "ERROR: Runner user does not exist: $RUNNER_USER" >&2
  exit 2
fi
if [[ ${#CI_RESOURCE_BROKER_TOKEN} -lt 32 ]]; then
  echo "ERROR: CI_RESOURCE_BROKER_TOKEN must be at least 32 characters" >&2
  exit 2
fi
if [[ ! -f "$INVENTORY_SOURCE" ]]; then
  echo "ERROR: inventory file does not exist: $INVENTORY_SOURCE" >&2
  exit 2
fi

install -d -m 0755 /opt/rk-ci
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 /var/lib/rk-hil-broker
install -m 0755 "$SCRIPT_DIR/local_hil_broker.py" /opt/rk-ci/local_hil_broker.py
install -m 0644 "$INVENTORY_SOURCE" /etc/rk-hil-inventory.json
install -m 0644 "$SCRIPT_DIR/rk-hil-broker.service" /etc/systemd/system/rk-hil-broker.service

cat >/etc/rk-hil-broker.env <<EOF
CI_RESOURCE_BROKER_TOKEN=$CI_RESOURCE_BROKER_TOKEN
EOF
chmod 0600 /etc/rk-hil-broker.env

systemctl daemon-reload
systemctl enable --now rk-hil-broker.service

sleep 1
curl --fail --silent --show-error http://127.0.0.1:8765/healthz
printf '\nHIL broker installed. Configure the real /dev/serial/by-id path in /etc/rk-hil-inventory.json before readiness.\n'
