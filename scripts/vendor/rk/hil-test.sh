#!/usr/bin/env bash
set -euo pipefail
: "${CI_PROJECT_ROOT:?CI_PROJECT_ROOT is required}"
: "${RK_SDK_ROOT:?RK_SDK_ROOT is required}"
: "${CI_HIL_DEVICE_ID:?HIL broker must provide CI_HIL_DEVICE_ID}"
recipe="$CI_PROJECT_ROOT/ci/vendor-rk-hil-test.sh"
if [[ ! -x "$recipe" ]]; then
  echo "Rockchip HIL recipe is missing or not executable: $recipe" >&2
  exit 2
fi
export CI_VENDOR="rockchip"
export CI_VENDOR_SDK_ROOT="$RK_SDK_ROOT"
exec "$recipe"
