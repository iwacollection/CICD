#!/usr/bin/env python3
"""Resolve a toolchain to its centrally managed hardware execution profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hardware_catalog import index_hardware_profiles, validate_hardware_catalog
from toolchain_catalog import index_toolchains, load_toolchain_catalog, validate_toolchain_catalog
from validate_config import load_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--toolchains", default=".ci-platform/ci/toolchains.json")
    parser.add_argument("--hardware-profiles", default=".ci-platform/ci/hardware-profiles.json")
    args = parser.parse_args()

    toolchain_data = load_toolchain_catalog(Path(args.toolchains))
    hardware_data = load_catalog(Path(args.hardware_profiles))
    errors = validate_toolchain_catalog(toolchain_data)
    errors.extend(validate_hardware_catalog(hardware_data))
    if errors:
        raise SystemExit("invalid hardware/toolchain catalogs:\n" + "\n".join(errors))

    toolchain = index_toolchains(toolchain_data).get(args.toolchain)
    if not toolchain:
        raise SystemExit(f"unknown toolchain: {args.toolchain}")
    profile_id = toolchain.get("hardware_profile", "")
    profile = index_hardware_profiles(hardware_data).get(profile_id) if profile_id else None
    if profile_id and not profile:
        raise SystemExit(f"toolchain {args.toolchain} references unknown hardware profile {profile_id}")

    result = {
        "is_hardware": bool(profile_id),
        "profile": profile_id,
        "profile_status": profile.get("status", "") if profile else "",
    }
    print(json.dumps(result, separators=(",", ":")))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"is_hardware={str(result['is_hardware']).lower()}\n")
            handle.write(f"profile={profile_id}\n")
            handle.write(f"profile_status={result['profile_status']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
