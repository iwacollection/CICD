#!/usr/bin/env bash
set -euo pipefail
: "${CI_PROJECT_ROOT:?CI_PROJECT_ROOT is required}"
: "${MTK_SDK_ROOT:?MTK_SDK_ROOT is required}"
recipe="$CI_PROJECT_ROOT/ci/vendor-mtk-build.sh"
if [[ ! -x "$recipe" ]]; then
  echo "MediaTek product recipe is missing or not executable: $recipe" >&2
  exit 2
fi
export CI_VENDOR="mediatek"
export CI_VENDOR_SDK_ROOT="$MTK_SDK_ROOT"
exec "$recipe"
