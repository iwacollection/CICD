#!/usr/bin/env bash
set -euo pipefail
: "${CI_PROJECT_ROOT:?CI_PROJECT_ROOT is required}"
: "${QCOM_SDK_ROOT:?QCOM_SDK_ROOT is required}"
recipe="$CI_PROJECT_ROOT/ci/vendor-qcom-build.sh"
if [[ ! -x "$recipe" ]]; then
  echo "Qualcomm product recipe is missing or not executable: $recipe" >&2
  exit 2
fi
export CI_VENDOR="qualcomm"
export CI_VENDOR_SDK_ROOT="$QCOM_SDK_ROOT"
exec "$recipe"
