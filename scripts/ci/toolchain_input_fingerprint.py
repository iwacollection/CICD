#!/usr/bin/env python3
"""Compute a deterministic SHA256 fingerprint for one toolchain build input set."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from toolchain_catalog import index_toolchains, load_toolchain_catalog, validate_toolchain_catalog


def compute_fingerprint(data: dict, toolchain_id: str, root: Path) -> tuple[str, list[str]]:
    toolchains = index_toolchains(data)
    item = toolchains.get(toolchain_id)
    if item is None:
        raise ValueError(f"unknown toolchain: {toolchain_id}")
    if item.get("execution_mode") != "container" or not item.get("dockerfile"):
        raise ValueError(f"toolchain is not a buildable container: {toolchain_id}")

    files: set[Path] = set()
    for pattern in item.get("source_paths", []):
        matches = [path for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise ValueError(f"source path pattern matched no files: {pattern}")
        files.update(matches)

    relative_files = sorted(path.relative_to(root).as_posix() for path in files)
    spec = {
        "toolchain": toolchain_id,
        "dockerfile": item.get("dockerfile"),
        "context": item.get("context"),
        "platforms": item.get("platforms", []),
        "build_args": item.get("build_args", {}),
        "source_paths": item.get("source_paths", []),
    }

    digest = hashlib.sha256()
    digest.update(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\0")
    for relative in relative_files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with (root / relative).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest(), relative_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/toolchains.json")
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = load_toolchain_catalog(Path(args.catalog))
    errors = validate_toolchain_catalog(data)
    if errors:
        raise SystemExit("invalid toolchain catalog:\n" + "\n".join(errors))

    fingerprint, files = compute_fingerprint(data, args.toolchain, Path(args.root).resolve())
    if args.json:
        print(json.dumps({"fingerprint": fingerprint, "files": files}, separators=(",", ":")))
    else:
        print(fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
