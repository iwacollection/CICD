#!/usr/bin/env python3
"""Use GitHub Deployments as an auditable environment -> artifact digest pointer."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
from typing import Any

from artifact_archive import _request

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("deployment payload schema_version must be 1")
    if payload.get("artifact_contract") != 2:
        raise ValueError("deployment payload must reference Artifact Contract v2")
    if not isinstance(payload.get("artifact_name"), str) or not payload["artifact_name"]:
        raise ValueError("deployment payload artifact_name is invalid")
    if not SHA256_RE.fullmatch(str(payload.get("bundle_sha256", ""))):
        raise ValueError("deployment payload bundle_sha256 is invalid")
    if not COMMIT_RE.fullmatch(str(payload.get("source_sha", ""))):
        raise ValueError("deployment payload source_sha is invalid")
    if not str(payload.get("source_run_id", "")).isdigit():
        raise ValueError("deployment payload source_run_id is invalid")
    if not isinstance(payload.get("release_tag"), str) or not payload["release_tag"].startswith("artifact-v2-"):
        raise ValueError("deployment payload release_tag is invalid")


def create_pointer(
    *,
    api_url: str,
    repository: str,
    token: str,
    environment: str,
    artifact_name: str,
    bundle_sha256: str,
    source_sha: str,
    source_run_id: str,
    release_tag: str,
    reason: str,
    restored_from_deployment_id: str = "",
) -> dict:
    if not ENV_RE.fullmatch(environment):
        raise ValueError("environment contains unsupported characters")
    if not SHA256_RE.fullmatch(bundle_sha256):
        raise ValueError("bundle_sha256 must be 64 lowercase hexadecimal characters")
    if not COMMIT_RE.fullmatch(source_sha):
        raise ValueError("source_sha must be a 40-character commit SHA")
    if not source_run_id.isdigit():
        raise ValueError("source_run_id must be numeric")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_contract": 2,
        "artifact_name": artifact_name,
        "bundle_sha256": bundle_sha256,
        "source_sha": source_sha,
        "source_run_id": source_run_id,
        "release_tag": release_tag,
        "reason": reason,
    }
    if restored_from_deployment_id:
        if not restored_from_deployment_id.isdigit():
            raise ValueError("restored_from_deployment_id must be numeric")
        payload["restored_from_deployment_id"] = restored_from_deployment_id
    _validate_payload(payload)

    deployment_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments"
    deployment = _request(
        deployment_url,
        token,
        method="POST",
        payload={
            "ref": source_sha,
            "task": "deploy",
            "auto_merge": False,
            "required_contexts": [],
            "environment": environment,
            "description": f"Artifact pointer: {artifact_name}",
            "transient_environment": False,
            "production_environment": environment == "production",
            "payload": payload,
        },
    )
    assert isinstance(deployment, dict)
    deployment_id = str(deployment["id"])
    status_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses"
    _request(
        status_url,
        token,
        method="POST",
        payload={
            "state": "success",
            "environment": environment,
            "description": f"{reason}: {artifact_name} @ {bundle_sha256[:12]}",
            "auto_inactive": True,
        },
    )
    return {
        "status": "success",
        "deployment_id": deployment_id,
        "environment": environment,
        **payload,
    }


def inspect_pointer(*, api_url: str, repository: str, token: str, deployment_id: str) -> dict:
    if not deployment_id.isdigit():
        raise ValueError("deployment_id must be numeric")
    url = f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}"
    deployment = _request(url, token)
    assert isinstance(deployment, dict)
    payload = deployment.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("deployment payload is not an object")
    _validate_payload(payload)
    return {
        "deployment_id": str(deployment["id"]),
        "environment": deployment.get("environment", ""),
        **payload,
    }


def current_pointer(*, api_url: str, repository: str, token: str, environment: str) -> dict:
    if not ENV_RE.fullmatch(environment):
        raise ValueError("environment contains unsupported characters")
    url = f"{api_url.rstrip('/')}/repos/{repository}/deployments?environment={environment}&per_page=100"
    deployments = _request(url, token)
    if not isinstance(deployments, list):
        raise ValueError("deployments API returned an invalid response")
    for deployment in deployments:
        deployment_id = str(deployment.get("id", ""))
        if not deployment_id.isdigit():
            continue
        statuses_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses?per_page=20"
        statuses = _request(statuses_url, token)
        if not isinstance(statuses, list) or not any(status.get("state") == "success" for status in statuses):
            continue
        try:
            return inspect_pointer(
                api_url=api_url,
                repository=repository,
                token=token,
                deployment_id=deployment_id,
            )
        except (ValueError, json.JSONDecodeError):
            continue
    raise ValueError(f"no successful artifact deployment pointer found for environment {environment}")


def _emit(result: dict) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                if isinstance(value, (str, int)):
                    handle.write(f"{key}={value}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--repository", required=True)
    create_parser.add_argument("--environment", required=True)
    create_parser.add_argument("--artifact-name", required=True)
    create_parser.add_argument("--bundle-sha256", required=True)
    create_parser.add_argument("--source-sha", required=True)
    create_parser.add_argument("--source-run-id", required=True)
    create_parser.add_argument("--release-tag", required=True)
    create_parser.add_argument("--reason", choices=("promotion", "rollback"), required=True)
    create_parser.add_argument("--restored-from-deployment-id", default="")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--repository", required=True)
    inspect_parser.add_argument("--deployment-id", required=True)

    current_parser = subparsers.add_parser("current")
    current_parser.add_argument("--repository", required=True)
    current_parser.add_argument("--environment", required=True)

    args = parser.parse_args()
    token = os.getenv("GH_TOKEN", "")
    if not token:
        raise SystemExit("GH_TOKEN is required")
    try:
        if args.command == "create":
            result = create_pointer(
                api_url=args.api_url,
                repository=args.repository,
                token=token,
                environment=args.environment,
                artifact_name=args.artifact_name,
                bundle_sha256=args.bundle_sha256,
                source_sha=args.source_sha,
                source_run_id=args.source_run_id,
                release_tag=args.release_tag,
                reason=args.reason,
                restored_from_deployment_id=args.restored_from_deployment_id,
            )
        elif args.command == "inspect":
            result = inspect_pointer(
                api_url=args.api_url,
                repository=args.repository,
                token=token,
                deployment_id=args.deployment_id,
            )
        else:
            result = current_pointer(
                api_url=args.api_url,
                repository=args.repository,
                token=token,
                environment=args.environment,
            )
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
