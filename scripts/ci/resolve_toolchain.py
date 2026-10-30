#!/usr/bin/env python3
"""Resolve a project Toolchain ID to the exact Active identity in the central catalog."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from toolchain_catalog import (
    immutable_reference,
    load_toolchain_catalog,
    validate_toolchain_catalog,
)


def resolve_toolchain(
    catalog: dict,
    toolchain_id: str,
    *,
    expected_mode: str,
    expected_image: str = "",
) -> dict:
    errors = validate_toolchain_catalog(catalog)
    if errors:
        raise ValueError("invalid central toolchain catalog: " + "; ".join(errors))

    matches = [
        item for item in catalog.get("toolchains", [])
        if isinstance(item, dict) and item.get("id") == toolchain_id
    ]
    if len(matches) != 1:
        raise ValueError(f"toolchain ID must resolve to exactly one catalog entry: {toolchain_id}")

    item = matches[0]
    if item.get("status") != "active":
        raise ValueError(
            f"toolchain {toolchain_id} is {item.get('status')!r}; only Active toolchains may be consumed"
        )
    if item.get("execution_mode") != expected_mode:
        raise ValueError(
            f"toolchain {toolchain_id} execution mode is {item.get('execution_mode')!r}, "
            f"but consumer requested {expected_mode!r}"
        )

    if expected_mode == "container":
        reference = immutable_reference(item)
        if not expected_image:
            raise ValueError("container consumer must provide the exact catalog image@sha256 reference")
        if expected_image != reference:
            raise ValueError(
                f"consumer image does not match Active registry identity: "
                f"expected={reference} actual={expected_image}"
            )
    else:
        if expected_image:
            raise ValueError("host toolchain must not receive a container image")
        reference = ""

    return {
        "id": toolchain_id,
        "status": item["status"],
        "execution_mode": item["execution_mode"],
        "image": reference,
        "identity": item.get("digest", "") if expected_mode == "container" else (item.get("host_identity") or f"host:{toolchain_id}"),
        "jdk_vendor": item.get("jdk_vendor", ""),
        "jdk_version": item.get("jdk_version", ""),
        "maven_version": item.get("maven_version", ""),
        "platforms": item.get("platforms", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/toolchains.json")
    parser.add_argument("--toolchain-id", required=True)
    parser.add_argument("--expected-mode", choices=("host", "container"), required=True)
    parser.add_argument("--expected-image", default="")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()

    try:
        catalog = load_toolchain_catalog(Path(args.catalog))
        resolved = resolve_toolchain(
            catalog,
            args.toolchain_id,
            expected_mode=args.expected_mode,
            expected_image=args.expected_image,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(resolved, ensure_ascii=False, sort_keys=True)
    print(json.dumps(resolved, ensure_ascii=False, indent=2))
    output = os.environ.get("GITHUB_OUTPUT", "")
    if args.github_output:
        if not output:
            print("ERROR: GITHUB_OUTPUT is not set", file=sys.stderr)
            return 2
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"image={resolved['image']}\n")
            handle.write(f"identity={resolved['identity']}\n")
            handle.write(f"resolved={encoded}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
