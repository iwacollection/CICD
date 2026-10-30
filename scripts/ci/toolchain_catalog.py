#!/usr/bin/env python3
"""Validate and resolve centrally managed CI toolchains."""
from __future__ import annotations

import argparse
import hashlib
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

        hardware_profile = item.get("hardware_profile", "")
        if not isinstance(hardware_profile, str):
            errors.append(f"{prefix}.hardware_profile must be a string")
            hardware_profile = ""
        host_identity = item.get("host_identity", "")
        if not isinstance(host_identity, str):
            errors.append(f"{prefix}.host_identity must be a string")
            host_identity = ""
        elif host_identity and not DIGEST_RE.fullmatch(host_identity):
            errors.append(f"{prefix}.host_identity must be sha256 followed by 64 lowercase hex characters")

        if mode == "host":
            forbidden = [key for key in ("image", "digest", "dockerfile", "context") if item.get(key)]
            if forbidden:
                errors.append(f"{prefix} host toolchain cannot define {', '.join(forbidden)}")
            if status == "active" and hardware_profile and not DIGEST_RE.fullmatch(host_identity):
                errors.append(f"{prefix} active hardware host toolchain requires immutable host_identity")
            continue

        if host_identity:
            errors.append(f"{prefix} container toolchain cannot define host_identity")

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
        if dockerfile or context or smoke_command:
            if not all(isinstance(value, str) for value in (dockerfile, context, smoke_command)):
                errors.append(f"{prefix}.dockerfile/context/smoke_command must be strings")
            elif not dockerfile or not context or not smoke_command:
                errors.append(f"{prefix} buildable container toolchain requires dockerfile, context and smoke_command")

    return errors


def index_toolchains(data: dict) -> dict[str, dict]:
    return {item["id"]: item for item in data.get("toolchains", []) if isinstance(item, dict) and item.get("id")}


def immutable_reference(item: dict) -> str:
    if item.get("execution_mode") != "container":
        return ""
    image = item.get("image", "")
    digest = item.get("digest", "")
    if not image or not DIGEST_RE.fullmatch(digest):
        raise ValueError(f"toolchain {item.get('id')} does not have an immutable image digest")
    return f"{image}@{digest}"


def toolchain_identity(item: dict) -> str:
    if item.get("execution_mode") == "container":
        return item.get("digest") or f"container:{item.get('id')}"
    return item.get("host_identity") or f"host:{item.get('id')}"


def build_context_sha256(repository_root: Path, context: str) -> str:
    """Hash a complete Docker build context by sorted relative path and content digest."""
    root = repository_root.resolve()
    relative_context = Path(context)
    if relative_context.is_absolute() or ".." in relative_context.parts:
        raise ValueError(f"unsafe toolchain build context path: {context}")

    context_path = (root / relative_context).resolve()
    if context_path != root and root not in context_path.parents:
        raise ValueError(f"toolchain build context escapes repository: {context}")
    if not context_path.is_dir():
        raise ValueError(f"toolchain build context does not exist: {context}")

    hasher = hashlib.sha256()
    files = sorted(context_path.rglob("*"), key=lambda path: path.relative_to(context_path).as_posix())
    for path in files:
        if path.is_symlink():
            raise ValueError(
                f"toolchain build context contains a symlink; use a pinned regular file instead: "
                f"{path.relative_to(root).as_posix()}"
            )
        if not path.is_file():
            continue
        relative_path = path.relative_to(context_path).as_posix()
        file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        hasher.update(relative_path.encode("utf-8"))
        hasher.update(b"\\0")
        hasher.update(file_sha256.encode("ascii"))
        hasher.update(b"\\n")

    return hasher.hexdigest()


def validate_published_candidate_context(item: dict, repository_root: Path) -> None:
    expected = item.get("build_context_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(
            f"published Candidate {item.get('id')} requires a 64-character build_context_sha256; "
            "do not skip validation for a pinned image"
        )

    context = item.get("context", "")
    dockerfile = item.get("dockerfile", "")
    context_path = Path(context)
    dockerfile_path = Path(dockerfile)
    if context_path.is_absolute() or dockerfile_path.is_absolute():
        raise ValueError(f"published Candidate {item.get('id')} has an absolute build path")
    if ".." in context_path.parts or ".." in dockerfile_path.parts:
        raise ValueError(f"published Candidate {item.get('id')} has an unsafe build path")

    root = repository_root.resolve()
    context_root = (root / context_path).resolve()
    dockerfile_resolved = (root / dockerfile_path).resolve()
    if context_root != root and root not in context_root.parents:
        raise ValueError(f"published Candidate {item.get('id')} context escapes repository")
    if context_root not in dockerfile_resolved.parents:
        raise ValueError(
            f"published Candidate {item.get('id')} Dockerfile must be inside its pinned context"
        )
    if not dockerfile_resolved.is_file():
        raise ValueError(f"published Candidate {item.get('id')} Dockerfile is missing: {dockerfile}")

    actual = build_context_sha256(repository_root, context)
    if actual != expected:
        raise ValueError(
            f"published Candidate {item.get('id')} build context changed: "
            f"expected {expected}, got {actual}; clear digest in a reviewed PR before rebuilding"
        )


def build_publish_matrix(data: dict, repository_root: Path | None = None) -> dict:
    repository_root = repository_root or Path.cwd()
    include: list[dict] = []
    for item in data.get("toolchains", []):
        if not isinstance(item, dict):
            continue
        if item.get("execution_mode") != "container" or not item.get("dockerfile"):
            continue
        # Only Candidate images are built and published by this workflow.
        # Active entries are immutable approved digests: rebuilding or publishing
        # them during an unrelated catalog change would create an unapproved
        # replacement and could make an existing project depend on a new image.
        # An empty digest means this Candidate still needs a first publish
        # or an explicitly requested rebuild. Once published, the registry
        # pins the digest and this workflow must not replace it implicitly.
        if item.get("status") != "candidate":
            continue
        if item.get("digest"):
            validate_published_candidate_context(item, repository_root)
            continue
        include.append(
            {
                "toolchain": item["id"],
                "image": item["image"],
                "dockerfile": item["dockerfile"],
                "context": item["context"],
                "smoke_command": item["smoke_command"],
                "platform": item.get("platforms", ["linux/amd64"])[0],
            }
        )
    return {"include": include}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/toolchains.json")
    parser.add_argument("--matrix", action="store_true")
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

    try:
        matrix = build_publish_matrix(data, Path.cwd())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

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
