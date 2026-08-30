#!/usr/bin/env python3
"""Validate centrally managed self-hosted hardware execution profiles."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from validate_config import load_catalog

ALLOWED_STATUSES = {"planned", "active", "retired"}
ALLOWED_SOCS = {"rk", "qualcomm", "mediatek"}
ALLOWED_OSES = {"linux", "android"}
ALLOWED_ARCHES = {"arm64", "armhf", "x86_64"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def validate_hardware_catalog(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("hardware profile schema_version must be 1")

    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return errors + ["hardware profiles must be a non-empty list"]

    seen: set[str] = set()
    for idx, profile in enumerate(profiles):
        prefix = f"profiles[{idx}]"
        if not isinstance(profile, dict):
            errors.append(f"{prefix} must be an object")
            continue

        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        elif profile_id in seen:
            errors.append(f"duplicate hardware profile id: {profile_id}")
        else:
            seen.add(profile_id)

        status = profile.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ALLOWED_STATUSES)}")

        soc = profile.get("soc")
        target_os = profile.get("target_os")
        arch = profile.get("arch")
        if soc not in ALLOWED_SOCS:
            errors.append(f"{prefix}.soc must be one of {sorted(ALLOWED_SOCS)}")
        if target_os not in ALLOWED_OSES:
            errors.append(f"{prefix}.target_os must be one of {sorted(ALLOWED_OSES)}")
        if arch not in ALLOWED_ARCHES:
            errors.append(f"{prefix}.arch must be one of {sorted(ALLOWED_ARCHES)}")

        runner_labels = profile.get("runner_labels")
        if not _string_list(runner_labels) or not runner_labels:
            errors.append(f"{prefix}.runner_labels must be a non-empty string list")
            runner_labels = []
        else:
            required = {"self-hosted", "linux", f"soc-{soc}"}
            missing = sorted(required.difference(runner_labels))
            if missing:
                errors.append(f"{prefix}.runner_labels missing required labels: {', '.join(missing)}")

        required_tools = profile.get("required_tools", [])
        if not _string_list(required_tools):
            errors.append(f"{prefix}.required_tools must be a string list")

        sdk = profile.get("sdk")
        if not isinstance(sdk, dict):
            errors.append(f"{prefix}.sdk must be an object")
            sdk = {}
        root_env = sdk.get("root_env")
        if not isinstance(root_env, str) or not ENV_RE.fullmatch(root_env):
            errors.append(f"{prefix}.sdk.root_env must be an uppercase environment variable name")
        if not _safe_relative_path(sdk.get("identity_file")):
            errors.append(f"{prefix}.sdk.identity_file must be a safe path relative to the SDK root")
        expected_sha = sdk.get("expected_sha256", "")
        if not isinstance(expected_sha, str):
            errors.append(f"{prefix}.sdk.expected_sha256 must be a string")
        elif expected_sha and not DIGEST_RE.fullmatch(expected_sha):
            errors.append(f"{prefix}.sdk.expected_sha256 must be sha256 followed by 64 lowercase hex characters")
        if status == "active" and not DIGEST_RE.fullmatch(expected_sha):
            errors.append(f"{prefix} active profile requires immutable sdk.expected_sha256")

        for resource_name in ("license", "hil"):
            resource = profile.get(resource_name)
            rprefix = f"{prefix}.{resource_name}"
            if not isinstance(resource, dict):
                errors.append(f"{rprefix} must be an object")
                continue
            required = resource.get("required")
            if not isinstance(required, bool):
                errors.append(f"{rprefix}.required must be boolean")
                continue
            pool = resource.get("pool", "")
            if not isinstance(pool, str):
                errors.append(f"{rprefix}.pool must be a string")
            if required and not pool:
                errors.append(f"{rprefix}.pool is required when the resource is required")
            for field in ("broker_url_env", "token_env"):
                value = resource.get(field)
                if not isinstance(value, str) or not ENV_RE.fullmatch(value):
                    errors.append(f"{rprefix}.{field} must be an uppercase environment variable name")
            ttl = resource.get("ttl_seconds")
            if not isinstance(ttl, int) or not 60 <= ttl <= 43200:
                errors.append(f"{rprefix}.ttl_seconds must be between 60 and 43200")

        build_adapter = profile.get("build_adapter")
        test_adapter = profile.get("test_adapter")
        if not _safe_relative_path(build_adapter):
            errors.append(f"{prefix}.build_adapter must be a safe repository-relative path")
        if not _safe_relative_path(test_adapter):
            errors.append(f"{prefix}.test_adapter must be a safe repository-relative path")

    return errors


def index_hardware_profiles(data: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in data.get("profiles", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }


def build_matrix(data: dict, status: str = "active") -> dict:
    include: list[dict] = []
    for item in data.get("profiles", []):
        if not isinstance(item, dict) or item.get("status") != status:
            continue
        include.append(
            {
                "profile": item["id"],
                "soc": item["soc"],
                "target_os": item["target_os"],
                "arch": item["arch"],
                "runner_labels": json.dumps(item["runner_labels"], separators=(",", ":")),
            }
        )
    return {"include": include}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/hardware-profiles.json")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--status", choices=sorted(ALLOWED_STATUSES), default="active")
    args = parser.parse_args()

    try:
        data = load_catalog(Path(args.catalog))
        errors = validate_hardware_catalog(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not args.matrix:
        print(f"OK: {len(data['profiles'])} hardware profiles validated")
        return 0

    matrix = build_matrix(data, args.status)
    encoded = json.dumps(matrix, separators=(",", ":"))
    print(encoded)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={encoded}\n")
            handle.write(f"count={len(matrix['include'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
