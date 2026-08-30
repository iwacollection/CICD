#!/usr/bin/env python3
"""Verify Artifact Contract v1/v2 bundles, manifests and checksum sidecars."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_manifest_identity(
    manifest: dict,
    *,
    source_sha: str = "",
    run_id: str = "",
    repository: str = "",
) -> list[str]:
    errors: list[str] = []
    expectations = {
        "source_sha": source_sha,
        "workflow_run_id": run_id,
        "source_repository": repository,
    }
    for field, expected in expectations.items():
        if expected and str(manifest.get(field, "")) != expected:
            errors.append(
                f"manifest {field} mismatch: expected={expected} actual={manifest.get(field, '')}"
            )
    return errors


def validate_manifest_contract(manifest: dict) -> list[str]:
    version = manifest.get("schema_version")
    if version == 1:
        return []
    if version != 2:
        return [f"unsupported artifact schema_version: {version}"]

    errors: list[str] = []
    required_objects = ("source", "target", "toolchain", "runner", "dependencies", "bundle")
    for field in required_objects:
        if not isinstance(manifest.get(field), dict):
            errors.append(f"manifest.{field} must be an object")

    artifact_name = manifest.get("artifact_name")
    if not isinstance(artifact_name, str) or not artifact_name:
        errors.append("manifest.artifact_name must be a non-empty string")

    bundle = manifest.get("bundle", {})
    if isinstance(bundle, dict) and isinstance(artifact_name, str) and artifact_name:
        if bundle.get("file") != f"{artifact_name}.tar.gz":
            errors.append("manifest bundle.file must equal artifact_name + .tar.gz")
        if bundle.get("format") != "tar.gz":
            errors.append("manifest bundle.format must be tar.gz")
        if bundle.get("reproducible") is not True:
            errors.append("manifest bundle.reproducible must be true")
        if not isinstance(bundle.get("source_date_epoch"), int) or bundle.get("source_date_epoch", -1) < 0:
            errors.append("manifest bundle.source_date_epoch must be a non-negative integer")

    target = manifest.get("target", {})
    toolchain = manifest.get("toolchain", {})
    if isinstance(target, dict) and isinstance(toolchain, dict):
        if not isinstance(toolchain.get("id"), str) or not toolchain.get("id"):
            errors.append("manifest toolchain.id must be a non-empty string")
        if target.get("toolchain") != toolchain.get("id"):
            errors.append("manifest target.toolchain must match toolchain.id")
        identity = toolchain.get("identity")
        if not isinstance(identity, str) or not identity:
            errors.append("manifest toolchain.identity must be a non-empty string")
        execution_mode = toolchain.get("execution_mode")
        if execution_mode not in {"host", "container"}:
            errors.append("manifest toolchain.execution_mode must be host or container")
        if execution_mode == "container":
            image = toolchain.get("container_image")
            if not isinstance(image, str) or "@sha256:" not in image:
                errors.append("container toolchain image must be pinned by @sha256 digest")
            elif isinstance(identity, str) and image.rsplit("@", 1)[1] != identity:
                errors.append("container toolchain identity must match container image digest")

    runner = manifest.get("runner", {})
    if isinstance(runner, dict):
        labels = runner.get("labels")
        if not isinstance(labels, list) or not all(isinstance(item, str) and item for item in labels):
            errors.append("manifest runner.labels must be a string list")

    compiler_versions = manifest.get("compiler_versions")
    if not isinstance(compiler_versions, dict):
        errors.append("manifest.compiler_versions must be an object")

    dependencies = manifest.get("dependencies", {})
    if isinstance(dependencies, dict):
        locks = dependencies.get("locks")
        if not isinstance(locks, list):
            errors.append("manifest dependencies.locks must be a list")
        elif any(not _valid_file_record(item) for item in locks):
            errors.append("manifest dependencies.locks contains an invalid file record")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("manifest.files must be a non-empty list")
    elif any(not _valid_file_record(item) for item in files):
        errors.append("manifest.files contains an invalid file record")
    elif len({item["path"] for item in files}) != len(files):
        errors.append("manifest.files contains duplicate paths")

    return errors


def _valid_file_record(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    path = item.get("path")
    digest = item.get("sha256")
    size = item.get("size_bytes")
    return (
        isinstance(path, str)
        and bool(path)
        and isinstance(digest, str)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
        and isinstance(size, int)
        and size >= 0
    )


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def verify_bundle_contents(bundle: Path, manifest: dict) -> list[str]:
    if manifest.get("schema_version") != 2:
        return []
    expected_records = {item["path"]: item for item in manifest["files"]}
    actual_records: dict[str, dict] = {}
    errors: list[str] = []
    try:
        with tarfile.open(bundle, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    errors.append(f"bundle contains non-regular member: {member.name}")
                    continue
                if not _safe_member_name(member.name):
                    errors.append(f"bundle contains unsafe path: {member.name}")
                    continue
                if member.name in actual_records:
                    errors.append(f"bundle contains duplicate path: {member.name}")
                    continue
                extracted = tf.extractfile(member)
                if extracted is None:
                    errors.append(f"cannot read bundle member: {member.name}")
                    continue
                h = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                    h.update(chunk)
                    size += len(chunk)
                actual_records[member.name] = {"sha256": h.hexdigest(), "size_bytes": size}
    except (OSError, tarfile.TarError) as exc:
        return [f"cannot inspect bundle: {exc}"]

    if set(actual_records) != set(expected_records):
        missing = sorted(set(expected_records) - set(actual_records))
        extra = sorted(set(actual_records) - set(expected_records))
        if missing:
            errors.append(f"bundle missing manifest files: {missing}")
        if extra:
            errors.append(f"bundle has files absent from manifest: {extra}")
    for path, expected in expected_records.items():
        actual = actual_records.get(path)
        if not actual:
            continue
        if actual["sha256"] != expected["sha256"]:
            errors.append(f"bundle member digest mismatch for {path}")
        if actual["size_bytes"] != expected["size_bytes"]:
            errors.append(f"bundle member size mismatch for {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--expected-source-sha", default="")
    parser.add_argument("--expected-run-id", default="")
    parser.add_argument("--expected-repository", default="")
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    manifests = list(directory.rglob("*.manifest.json"))
    if len(manifests) != 1:
        raise SystemExit(f"expected exactly one manifest, found {len(manifests)}")

    manifest_path = manifests[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract_errors = validate_manifest_contract(manifest)
    if contract_errors:
        raise SystemExit("\n".join(contract_errors))
    identity_errors = validate_manifest_identity(
        manifest,
        source_sha=args.expected_source_sha,
        run_id=args.expected_run_id,
        repository=args.expected_repository,
    )
    if identity_errors:
        raise SystemExit("\n".join(identity_errors))

    bundle_name = manifest["bundle"]["file"]
    expected = manifest["bundle"]["sha256"]
    bundle_candidates = list(directory.rglob(bundle_name))
    if len(bundle_candidates) != 1:
        raise SystemExit(f"expected exactly one bundle {bundle_name}, found {len(bundle_candidates)}")

    bundle = bundle_candidates[0]
    actual = sha256(bundle)
    if actual != expected:
        raise SystemExit(f"manifest digest mismatch: expected={expected} actual={actual}")
    if args.expected_sha256 and actual != args.expected_sha256:
        raise SystemExit(
            f"operator-provided digest mismatch: expected={args.expected_sha256} actual={actual}"
        )

    content_errors = verify_bundle_contents(bundle, manifest)
    if content_errors:
        raise SystemExit("\n".join(content_errors))

    sidecars = list(directory.rglob(f"{bundle_name}.sha256"))
    if len(sidecars) != 1:
        raise SystemExit(f"expected exactly one checksum sidecar, found {len(sidecars)}")
    sidecar_digest = sidecars[0].read_text(encoding="utf-8").strip().split()[0]
    if sidecar_digest != actual:
        raise SystemExit(f"checksum sidecar mismatch: expected={sidecar_digest} actual={actual}")

    print(
        json.dumps(
            {
                "status": "verified",
                "schema_version": manifest.get("schema_version"),
                "project": manifest["project"],
                "source_sha": manifest["source_sha"],
                "bundle": bundle.name,
                "sha256": actual,
                "target": manifest["target"],
                "toolchain": manifest.get("toolchain", {}),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
