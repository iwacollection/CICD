#!/usr/bin/env python3
"""Determine which centrally managed toolchains require verify or publish work."""
from __future__ import annotations

import argparse
import json
import os
from fnmatch import fnmatch
from pathlib import Path

from toolchain_catalog import index_toolchains, load_toolchain_catalog, validate_toolchain_catalog

CONTROL_PLANE_PATHS = {
    "scripts/ci/toolchain_catalog.py",
    "scripts/ci/toolchain_change_analysis.py",
    ".github/workflows/toolchain-images.yml",
}
BUILD_SPEC_FIELDS = (
    "execution_mode",
    "image",
    "dockerfile",
    "context",
    "platforms",
    "smoke_command",
    "source_paths",
    "build_args",
)


def _normalise(path: str) -> str:
    value = path.strip().replace("\\", "/")
    return value[2:] if value.startswith("./") else value


def _buildable(item: dict) -> bool:
    return (
        item.get("execution_mode") == "container"
        and bool(item.get("dockerfile"))
        and item.get("status") in {"candidate", "active"}
    )


def _build_spec(item: dict) -> dict:
    return {field: item.get(field) for field in BUILD_SPEC_FIELDS}


def _matches_any(path: str, patterns: list[str]) -> bool:
    path = _normalise(path)
    return any(fnmatch(path, _normalise(pattern)) for pattern in patterns)


def analyze_changes(
    current: dict,
    base: dict | None,
    changed_files: list[str],
    *,
    force_verify_all: bool = False,
    force_publish_all: bool = False,
) -> dict:
    current_index = index_toolchains(current)
    base_index = index_toolchains(base or {})
    buildable = [
        item["id"]
        for item in current.get("toolchains", [])
        if isinstance(item, dict) and _buildable(item)
    ]
    changed = sorted({_normalise(path) for path in changed_files if _normalise(path)})

    if force_publish_all:
        return {
            "verify_toolchains": buildable,
            "publish_toolchains": buildable,
            "changed_files": changed,
            "reason": "forced verify and publish of all buildable toolchains",
        }

    publish: set[str] = set()
    verify: set[str] = set()

    if base is None:
        publish.update(buildable)
    else:
        for toolchain_id in buildable:
            current_item = current_index[toolchain_id]
            previous = base_index.get(toolchain_id)
            if previous is None or _build_spec(current_item) != _build_spec(previous):
                publish.add(toolchain_id)
                continue
            source_paths = current_item.get("source_paths", [])
            if any(_matches_any(path, source_paths) for path in changed):
                publish.add(toolchain_id)

    if force_verify_all or any(path in CONTROL_PLANE_PATHS for path in changed):
        verify.update(buildable)

    verify.update(publish)

    if publish:
        reason = "toolchain build inputs changed"
    elif verify:
        reason = "toolchain control plane changed; verify only"
    elif "ci/toolchains.json" in changed:
        reason = "promotion/catalog metadata changed; no image rebuild required"
    else:
        reason = "no toolchain build inputs changed"

    return {
        "verify_toolchains": [name for name in buildable if name in verify],
        "publish_toolchains": [name for name in buildable if name in publish],
        "changed_files": changed,
        "reason": reason,
    }


def _write_outputs(result: dict) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(
            "verify_toolchains="
            + json.dumps(result["verify_toolchains"], separators=(",", ":"))
            + "\n"
        )
        handle.write(
            "publish_toolchains="
            + json.dumps(result["publish_toolchains"], separators=(",", ":"))
            + "\n"
        )
        handle.write(f"verify_count={len(result['verify_toolchains'])}\n")
        handle.write(f"publish_count={len(result['publish_toolchains'])}\n")
        handle.write(f"reason={result['reason']}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/toolchains.json")
    parser.add_argument("--base-catalog")
    parser.add_argument("--changed-files-file")
    parser.add_argument("--force-verify-all", action="store_true")
    parser.add_argument("--force-publish-all", action="store_true")
    args = parser.parse_args()

    current = load_toolchain_catalog(Path(args.catalog))
    errors = validate_toolchain_catalog(current)
    if errors:
        raise SystemExit("invalid current toolchain catalog:\n" + "\n".join(errors))

    base = None
    if args.base_catalog and Path(args.base_catalog).exists():
        base = load_toolchain_catalog(Path(args.base_catalog))

    changed_files: list[str] = []
    if args.changed_files_file and Path(args.changed_files_file).exists():
        changed_files = Path(args.changed_files_file).read_text(encoding="utf-8").splitlines()

    result = analyze_changes(
        current,
        base,
        changed_files,
        force_verify_all=args.force_verify_all,
        force_publish_all=args.force_publish_all,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    _write_outputs(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
