#!/usr/bin/env python3
"""Bind independently produced Trivy evidence to one exact Artifact Contract v2 object."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return value


def create_manifest(
    output: Path,
    *,
    artifact_name: str,
    project: str,
    soc: str,
    target_os: str,
    arch: str,
    toolchain: str,
    toolchain_identity: str,
    source_sha: str,
    source_repository: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", artifact_name):
        raise ValueError("artifact_name contains unsupported characters")
    value = {
        "schema_version": 1,
        "artifact_name": artifact_name,
        "project": project,
        "source_sha": source_sha,
        "source_repository": source_repository,
        "workflow_run_id": str(workflow_run_id),
        "workflow_run_attempt": str(workflow_run_attempt),
        "target": {
            "soc": soc,
            "target_os": target_os,
            "arch": arch,
        },
        "toolchain": {
            "id": toolchain,
            "identity": toolchain_identity,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


def _expected_identity(build_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_name": build_manifest.get("artifact_name"),
        "project": build_manifest.get("project"),
        "source_sha": build_manifest.get("source_sha"),
        "source_repository": build_manifest.get("source_repository"),
        "workflow_run_id": str(build_manifest.get("workflow_run_id", "")),
        "workflow_run_attempt": str(build_manifest.get("workflow_run_attempt", "")),
        "target": {
            key: build_manifest.get("target", {}).get(key)
            for key in ("soc", "target_os", "arch")
        },
        "toolchain": {
            "id": build_manifest.get("toolchain", {}).get("id"),
            "identity": build_manifest.get("toolchain", {}).get("identity"),
        },
    }


def merge_security_evidence(
    build_artifact_dir: Path,
    evidence_root: Path,
    *,
    expected_source_sha: str,
    expected_repository: str,
    expected_run_id: str,
) -> dict[str, str]:
    manifests = sorted(build_artifact_dir.glob("*.manifest.json"))
    if len(manifests) != 1:
        raise ValueError(
            f"expected exactly one Artifact Contract manifest in {build_artifact_dir}, found {len(manifests)}"
        )
    build_manifest = _read_json(manifests[0], "Artifact Contract manifest")
    artifact_name = build_manifest.get("artifact_name", "")
    if not isinstance(artifact_name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", artifact_name):
        raise ValueError("Artifact Contract has an invalid artifact_name")

    if build_manifest.get("source_sha") != expected_source_sha:
        raise ValueError("Artifact Contract source_sha does not match the trusted workflow run")
    if build_manifest.get("source_repository") != expected_repository:
        raise ValueError("Artifact Contract source_repository does not match the current repository")
    if str(build_manifest.get("workflow_run_id", "")) != str(expected_run_id):
        raise ValueError("Artifact Contract workflow_run_id does not match the trusted workflow run")

    evidence_dir = evidence_root / f"security-{artifact_name}"
    if not evidence_dir.is_dir():
        raise ValueError(f"missing security evidence artifact directory: {evidence_dir}")
    security_manifest_path = evidence_dir / "security-evidence.json"
    security_manifest = _read_json(security_manifest_path, "security evidence manifest")
    if security_manifest.get("schema_version") != 1:
        raise ValueError("unsupported security evidence manifest schema_version")

    expected = _expected_identity(build_manifest)
    actual = {key: security_manifest.get(key) for key in expected}
    if actual != expected:
        mismatched = [key for key in expected if actual.get(key) != expected[key]]
        raise ValueError("security evidence identity mismatch: " + ", ".join(mismatched))

    scan = evidence_dir / "security-scan.json"
    sbom = evidence_dir / "security-sbom.cdx.json"
    if not scan.is_file() or not sbom.is_file():
        raise ValueError("security evidence artifact must contain both scan report and CycloneDX SBOM")
    _read_json(scan, "Trivy scan report")
    _read_json(sbom, "CycloneDX SBOM")

    destination_scan = build_artifact_dir / "security-scan.json"
    destination_sbom = build_artifact_dir / "security-sbom.cdx.json"
    shutil.copy2(scan, destination_scan)
    shutil.copy2(sbom, destination_sbom)
    return {
        "artifact_name": artifact_name,
        "scan": str(destination_scan),
        "sbom": str(destination_sbom),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--output", required=True)
    for name in (
        "artifact-name",
        "project",
        "soc",
        "target-os",
        "arch",
        "toolchain",
        "toolchain-identity",
        "source-sha",
        "source-repository",
        "workflow-run-id",
        "workflow-run-attempt",
    ):
        create.add_argument("--" + name, required=True)

    merge = sub.add_parser("merge")
    merge.add_argument("--build-artifact-dir", required=True)
    merge.add_argument("--evidence-root", required=True)
    merge.add_argument("--expected-source-sha", required=True)
    merge.add_argument("--expected-repository", required=True)
    merge.add_argument("--expected-run-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_manifest(
                Path(args.output),
                artifact_name=args.artifact_name,
                project=args.project,
                soc=args.soc,
                target_os=args.target_os,
                arch=args.arch,
                toolchain=args.toolchain,
                toolchain_identity=args.toolchain_identity,
                source_sha=args.source_sha,
                source_repository=args.source_repository,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
            )
        else:
            result = merge_security_evidence(
                Path(args.build_artifact_dir),
                Path(args.evidence_root),
                expected_source_sha=args.expected_source_sha,
                expected_repository=args.expected_repository,
                expected_run_id=args.expected_run_id,
            )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
