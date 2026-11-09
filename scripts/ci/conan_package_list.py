#!/usr/bin/env python3
"""Classify Conan package-list origins before invoking a remote download."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LOCAL_CACHE_ORIGIN = "Local Cache"


def classify_package_list(data: object, remote: str) -> str:
    if not isinstance(data, dict) or not data:
        raise ValueError("Conan package list is empty; refusing to skip dependency resolution")

    origins = set(data)
    unexpected_origins = origins - {LOCAL_CACHE_ORIGIN, remote}
    if unexpected_origins:
        unexpected = ", ".join(sorted(str(origin) for origin in unexpected_origins))
        raise ValueError(f"Conan package list contains unexpected origins: {unexpected}")
    if origins == {LOCAL_CACHE_ORIGIN}:
        return "local-cache-only"
    if remote in origins:
        return "remote-required"

    raise ValueError(
        f"Conan package list has no expected remote '{remote}' and is not local-cache-only"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-list", required=True)
    parser.add_argument("--remote", required=True)
    args = parser.parse_args()

    try:
        data = json.loads(Path(args.package_list).read_text(encoding="utf-8"))
        print(classify_package_list(data, args.remote))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
