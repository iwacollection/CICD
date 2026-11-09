#!/usr/bin/env bash
set -euo pipefail

start_ns="$(date +%s%N)"
ctest --test-dir build --output-on-failure
end_ns="$(date +%s%N)"
test_ms=$(( (end_ns - start_ns) / 1000000 ))

python3 - "$test_ms" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(".ci-telemetry/conan-build-timings.json")
data = json.loads(path.read_text(encoding="utf-8"))
data["test_ms"] = int(sys.argv[1])
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"conan_ctest_ms={data['test_ms']}")
PY
