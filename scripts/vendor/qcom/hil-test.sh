#!/usr/bin/env bash
set -euo pipefail
: "${CI_PROJECT_ROOT:?CI_PROJECT_ROOT is required}"
: "${QCOM_SDK_ROOT:?QCOM_SDK_ROOT is required}"
: "${CI_HIL_DEVICE_ID:?HIL broker must provide CI_HIL_DEVICE_ID}"
recipe="$CI_PROJECT_ROOT/ci/vendor-qcom-hil-test.sh"
if [[ ! -x "$recipe" ]]; then
  echo "Qualcomm HIL recipe is missing or not executable: $recipe" >&2
  exit 2
fi
export CI_VENDOR="qualcomm"
export CI_VENDOR_SDK_ROOT="$QCOM_SDK_ROOT"
exec "$recipe"
