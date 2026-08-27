#!/usr/bin/env python3
"""Validate and resolve centrally managed CI toolchains."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from validate_config import load_catalog

ALLOWED_STATUSES = {"active", "candidate", "planned", "retired"}
ALLOWED_EXECUTION_MODES = {"host", "container"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
load_toolchain_catalog = load_catalog


def validate_toolchain_catalog(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("toolchain schema_version must be 1")

    policy = data.get("registry_policy", {})
    prefixes = policy.get("allowed_image_prefixes", [])
    if not isinstance(prefixes, list) or not all(isinstance(x, str) and x for x in prefixes):
        errors.append("registry_policy.allowed_image_prefixes must be a string list")
        prefixes = []

    toolchains = data.get("toolchains")
    if not isinstance(toolchains, list) or not toolchains:
        return errors + ["toolchains must be a non-empty list"]

    seen: set[str] = set()
    for idx, item in enumerate(toolchains):
        prefix = f"toolchains[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        toolchain_id = item.get("id")
        if not isinstance(toolchain_id, str) or not toolchain_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        elif toolchain_id in seen:
            errors.append(f"duplicate toolchain id: {toolchain_id}")
        else:
            seen.add(toolchain_id)

        status = item.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ALLOWED_STATUSES)}")

        mode = item.get("execution_mode")
        if mode not in ALLOWED_EXECUTION_MODES:
            errors.append(f"{prefix}.execution_mode must be one of {sorted(ALLOWED_EXECUTION_MODES)}")
            continue

        if mode == "host":
            forbidden = [
                key
                for key in (
                    "image",
                    "digest",
                    "dockerfile",
                    "context",
                    "source_paths",
                    "build_args",
                )
                if item.get(key)
            ]
            if forbidden:
                errors.append(f"{prefix} host toolchain cannot define {', '.join(forbidden)}")
            continue

        image = item.get("image", "")
        digest = item.get("digest", "")
        if not isinstance(image, str) or not isinstance(digest, str):
            errors.append(f"{prefix}.image and .digest must be strings")
            continue
        if image and "@" in image:
            errors.append(f"{prefix}.image must not contain a digest; use the digest field")
        if image and prefixes and not any(image.startswith(allowed) for allowed in prefixes):
            errors.append(f"{prefix}.image is outside allowed registries: {image}")
        if digest and not DIGEST_RE.fullmatch(digest):
            errors.append(f"{prefix}.digest must be sha256 followed by 64 lowercase hex characters")

        if status == "active":
            if not image:
                errors.append(f"{prefix} active container toolchain requires image")
            if policy.get("require_digest_for_active_containers", True) and not DIGEST_RE.fullmatch(digest):
                errors.append(f"{prefix} active container toolchain requires immutable sha256 digest")

        dockerfile = item.get("dockerfile", "")
        context = item.get("context", "")
        smoke_command = item.get("smoke_command", "")
        source_paths = item.get("source_paths", [])
        build_args = item.get("build_args", {})
        build_fields_present = bool(dockerfile or context or smoke_command or source_paths or build_args)
        if build_fields_present:
            if not all(isinstance(value, str) for value in (dockerfile, context, smoke_command)):
                errors.append(f"{prefix}.dockerfile/context/smoke_command must be strings")
            elif not dockerfile or not context or not smoke_command:
                errors.append(
                    f"{prefix} buildable container toolchain requires dockerfile, context and smoke_command"
                )
            if not isinstance(source_paths, list) or not source_paths or not all(
                isinstance(value, str) and value for value in source_paths
            ):
                errors.append(f"{prefix}.source_paths must be a non-empty string list")
            if not isinstance(build_args, dict) or not all(
                isinstance(key, str)
                and key
                and isinstance(value, str)
                and value
                for key, value in build_args.items()
            ):
                errors.append(f"{prefix}.build_args must be a string-to-string object")

    return errors


def index_toolchains(data: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in data.get("toolchains", [])
        if isinstance(item, dict) and item.get("id")
    }


def immutable_reference(item: dict) -> str:
    if item.get("execution_mode") != "container":
        return ""
    image = item.get("image", "")
    digest = item.get("digest", "")
    if not image or not DIGEST_RE.fullmatch(digest):
        raise ValueError(f"toolchain {item.get('id')} does not have an immutable image digest")
    return f"{image}@{digest}"


def build_publish_matrix(data: dict, toolchain_names: set[str] | None = None) -> dict:
    include: list[dict] = []
    for item in data.get("toolchains", []):
        if not isinstance(item, dict):
            continue
        if item.get("execution_mode") != "container" or not item.get("dockerfile"):
            continue
        if item.get("status") not in {"candidate", "active"}:
            continue
        if toolchain_names is not None and item.get("id") not in toolchain_names:
            continue
        build_args = item.get("build_args", {})
        source_paths = item.get("source_paths", [])
        include.append(
            {
                "toolchain": item["id"],
                "image": item["image"],
                "dockerfile": item["dockerfile"],
                "context": item["context"],
                "smoke_command": item["smoke_command"],
                "platform": item.get("platforms", ["linux/amd64"])[0],
                "build_args_json": json.dumps(build_args, separators=(",", ":")),
                "build_args": "\n".join(f"{key}={value}" for key, value in build_args.items()),
                "source_paths_json": json.dumps(source_paths, separators=(",", ":")),
            }
        )
    return {"include": include}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/toolchains.json")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--toolchains-json")
    args = parser.parse_args()

    try:
        data = load_catalog(Path(args.catalog))
        errors = validate_toolchain_catalog(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not args.matrix:
        print(f"OK: {len(data['toolchains'])} toolchain definitions validated")
        return 0

    selected: set[str] | None = None
    if args.toolchains_json is not None:
        raw = json.loads(args.toolchains_json)
        if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
            raise SystemExit("--toolchains-json must be a JSON array of toolchain IDs")
        selected = set(raw)

    matrix = build_publish_matrix(data, selected)
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
