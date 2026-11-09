#!/usr/bin/env python3
"""Classify a Conan package list before attempting a remote download."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def select_download_source(package_list: object) -> str:
    """Return remote when any locked package is absent locally; fail closed otherwise."""
    if not isinstance(package_list, dict) or not package_list:
        raise ValueError("Conan package list is empty")
    if package_list.get("conancenter"):
        return "remote"
    if package_list.get("Local Cache"):
        return "local-cache"
    raise ValueError(f"Conan package list has unrecognized origins: {sorted(package_list)}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: conan_package_list.py <package-list.json>", file=sys.stderr)
        return 2
    try:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        source = select_download_source(data)
        print(f"conan_package_list_origins={','.join(data.keys())}", file=sys.stderr)
        print(source)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
