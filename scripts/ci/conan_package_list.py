#!/usr/bin/env python3
"""Select only remote Conan package entries for an exact locked dependency graph."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ALLOWED_ORIGINS = {"Local Cache", "conancenter"}


def prepare_download_list(
    package_list_path: Path,
    remote_list_path: Path,
    result_path: Path,
) -> str:
    """Create a remote-only Conan list or an explicit local-cache skip result."""
    try:
        data: Any = json.loads(package_list_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Conan package list: {exc}") from exc

    if not isinstance(data, dict) or not data:
        raise ValueError("Conan package list is empty; refusing to report a successful dependency stage")

    unexpected = set(data) - ALLOWED_ORIGINS
    if unexpected:
        raise ValueError(f"unexpected Conan package-list origins: {sorted(unexpected)}")

    remote_packages = data.get("conancenter")
    if remote_packages:
        remote_list_path.write_text(
            json.dumps({"conancenter": remote_packages}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if result_path.exists():
            result_path.unlink()
        return "remote"

    local_packages = data.get("Local Cache")
    if local_packages:
        if remote_list_path.exists():
            remote_list_path.unlink()
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "Local Cache",
                    "remote_download_skipped": True,
                    "reason": "the locked graph already exists in the local Conan cache",
                    "downloaded_package_count": 0,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return "local-cache"

    raise ValueError("Conan package list contains no packages in Local Cache or conancenter")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--remote-output", required=True, type=Path)
    parser.add_argument("--result-output", required=True, type=Path)
    args = parser.parse_args()
    try:
        mode = prepare_download_list(args.input, args.remote_output, args.result_output)
    except ValueError as exc:
        parser.error(str(exc))
    print(mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
