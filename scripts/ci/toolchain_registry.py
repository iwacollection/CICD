#!/usr/bin/env python3
"""Append-only registry for immutable toolchain image candidates and approvals.

GitHub Deployments are the server-side audit log. A candidate is registered only
after its digest, SBOM, scan, signature and provenance have been independently
verified by the publishing workflow. Approval is a separate protected workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
from pathlib import Path
from typing import Any

from artifact_archive import _request
from deployment_pointer import _list_all_pages

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TOOLCHAIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
REGISTRY_KIND = "toolchain-image-registry"
REQUIRED_HASHES = (
    "sbom_sha256", "scan_report_sha256", "dockerfile_sha256",
    "build_context_sha256", "build_definition_sha256",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_record(record: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["toolchain evidence record must be an object"]
    if record.get("schema_version") != 3 or record.get("kind") != REGISTRY_KIND:
        errors.append("toolchain evidence record must use schema_version=3 and the registry kind")
    toolchain = record.get("toolchain")
    if not isinstance(toolchain, str) or not TOOLCHAIN_RE.fullmatch(toolchain):
        errors.append("toolchain ID is invalid")
    image = record.get("image")
    digest = record.get("digest")
    if not isinstance(image, str) or not image.startswith("ghcr.io/iwacollection/"):
        errors.append("image must use the approved GHCR namespace")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        errors.append("digest must be an immutable sha256 digest")
    if not isinstance(record.get("immutable_reference"), str) or record.get("immutable_reference") != f"{image}@{digest}":
        errors.append("immutable_reference must exactly match image@digest")
    source = record.get("source_commit")
    if not isinstance(source, str) or not COMMIT_RE.fullmatch(source):
        errors.append("source_commit must be a 40-character commit SHA")
    if not str(record.get("workflow_run_id", "")).isdigit():
        errors.append("workflow_run_id must be numeric")
    for field in REQUIRED_HASHES:
        if not isinstance(record.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", record[field]):
            errors.append(f"{field} must be a 64-character SHA256")
    if record.get("signature_verified") is not True:
        errors.append("signature must be independently verified before registration")
    if record.get("sbom_attestation_verified") is not True:
        errors.append("digest-bound SBOM attestation must be independently verified")
    if record.get("provenance_verified") is not True:
        errors.append("GitHub provenance must be independently verified")
    if record.get("security_policy_passed") is not True:
        errors.append("security policy must pass before registration")
    if record.get("scan_attestation_verified") is not True:
        errors.append("digest-bound vulnerability scan attestation must be independently verified")
    if not isinstance(record.get("workflow_ref"), str) or not record["workflow_ref"]:
        errors.append("workflow_ref is required")
    return errors


def _environment(kind: str, toolchain: str) -> str:
    if kind not in {"candidate", "approved"} or not TOOLCHAIN_RE.fullmatch(toolchain):
        raise ValueError("invalid toolchain registry environment")
    return f"toolchain-{kind}/{toolchain}"


def _deployment_payload(record: dict[str, Any], *, state: str) -> dict[str, Any]:
    return {
        "kind": REGISTRY_KIND,
        "schema_version": 3,
        "state": state,
        "toolchain": record["toolchain"],
        "image": record["image"],
        "digest": record["digest"],
        "immutable_reference": record["immutable_reference"],
        "source_commit": record["source_commit"],
        "workflow_run_id": str(record["workflow_run_id"]),
        "workflow_ref": record["workflow_ref"],
        "dockerfile": record.get("dockerfile", ""),
        "dockerfile_sha256": record["dockerfile_sha256"],
        "build_context_sha256": record["build_context_sha256"],
        "build_definition_sha256": record["build_definition_sha256"],
        "sbom_sha256": record["sbom_sha256"],
        "scan_report_sha256": record["scan_report_sha256"],
        "signature_verified": True,
        "sbom_attestation_verified": True,
        "provenance_verified": True,
        "security_policy_passed": True,
        "scan_attestation_verified": True,
        "candidate_deployment_id": str(record.get("candidate_deployment_id", "")),
    }


def _create_deployment(
    *, api_url: str, repository: str, token: str, environment: str,
    ref: str, payload: dict[str, Any], description: str,
) -> str:
    url = f"{api_url.rstrip('/')}/repos/{repository}/deployments"
    deployment = _request(
        url, token, method="POST",
        payload={
            "ref": ref, "task": "toolchain-registry", "auto_merge": False,
            "required_contexts": [], "environment": environment,
            "description": description, "transient_environment": True,
            "production_environment": False, "payload": payload,
        },
    )
    if not isinstance(deployment, dict) or not str(deployment.get("id", "")).isdigit():
        raise ValueError("GitHub did not return a valid toolchain registry deployment")
    deployment_id = str(deployment["id"])
    _request(
        f"{url}/{deployment_id}/statuses", token, method="POST",
        payload={
            "state": "success", "environment": environment,
            "description": description, "auto_inactive": False,
        },
    )
    return deployment_id


def register_candidate(
    *, api_url: str, repository: str, token: str, record: dict[str, Any]
) -> dict[str, Any]:
    errors = validate_record(record)
    if errors:
        raise ValueError("; ".join(errors))
    environment = _environment("candidate", record["toolchain"])
    deployments = _list_all_pages(
        f"{api_url.rstrip('/')}/repos/{repository}/deployments?environment={environment}", token
    )
    for deployment in deployments:
        payload = deployment.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or payload.get("kind") != REGISTRY_KIND:
            continue
        if payload.get("digest") == record["digest"] and payload.get("source_commit") == record["source_commit"]:
            return {"status": "already-registered", "deployment_id": str(deployment["id"]), **payload}
    payload = _deployment_payload(record, state="candidate")
    deployment_id = _create_deployment(
        api_url=api_url, repository=repository, token=token, environment=environment,
        ref=record["source_commit"], payload=payload,
        description=f"Candidate {record['toolchain']} @ {record['digest']}",
    )
    return {"status": "registered", "deployment_id": deployment_id, **payload}


def inspect_candidate(
    *, api_url: str, repository: str, token: str, toolchain: str, digest: str
) -> dict[str, Any]:
    if not TOOLCHAIN_RE.fullmatch(toolchain) or not SHA256_RE.fullmatch(digest):
        raise ValueError("toolchain and exact sha256 digest are required")
    environment = _environment("candidate", toolchain)
    deployments = _list_all_pages(
        f"{api_url.rstrip('/')}/repos/{repository}/deployments?environment={environment}", token
    )
    for deployment in deployments:
        deployment_id = str(deployment.get("id", ""))
        payload = deployment.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not deployment_id.isdigit() or not isinstance(payload, dict):
            continue
        if payload.get("kind") != REGISTRY_KIND or payload.get("digest") != digest:
            continue
        if payload.get("toolchain") != toolchain or payload.get("state") != "candidate":
            continue
        statuses = _list_all_pages(
            f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses", token
        )
        if not any(item.get("state") == "success" for item in statuses):
            raise ValueError(f"candidate registry entry {deployment_id} has no successful status")
        return {"deployment_id": deployment_id, **payload}
    raise ValueError(f"candidate digest is not registered: {toolchain}@{digest}")


def approve_candidate(
    *, api_url: str, repository: str, token: str, toolchain: str, digest: str
) -> dict[str, Any]:
    candidate = inspect_candidate(
        api_url=api_url, repository=repository, token=token,
        toolchain=toolchain, digest=digest,
    )
    payload = {key: value for key, value in candidate.items() if key != "deployment_id"}
    payload["state"] = "approved"
    payload["candidate_deployment_id"] = candidate["deployment_id"]
    environment = _environment("approved", toolchain)
    deployment_id = _create_deployment(
        api_url=api_url, repository=repository, token=token, environment=environment,
        ref=payload["source_commit"], payload=payload,
        description=f"Approved {toolchain} @ {digest}",
    )
    return {"status": "approved", "deployment_id": deployment_id, **payload}


def _emit(result: dict[str, Any]) -> None:
    output = os.getenv("GITHUB_OUTPUT", "")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                if isinstance(value, (str, int)):
                    handle.write(f"{key}={value}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--record", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--toolchain", required=True)
    inspect.add_argument("--digest", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("--toolchain", required=True)
    approve.add_argument("--digest", required=True)
    args = parser.parse_args()
    token = os.getenv("GH_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise SystemExit("GH_TOKEN and GITHUB_REPOSITORY are required")
    try:
        if args.command == "register":
            record = json.loads(Path(args.record).read_text(encoding="utf-8"))
            result = register_candidate(api_url=args.api_url, repository=repository, token=token, record=record)
        elif args.command == "inspect":
            result = inspect_candidate(api_url=args.api_url, repository=repository, token=token,
                                       toolchain=args.toolchain, digest=args.digest)
        else:
            result = approve_candidate(api_url=args.api_url, repository=repository, token=token,
                                       toolchain=args.toolchain, digest=args.digest)
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
