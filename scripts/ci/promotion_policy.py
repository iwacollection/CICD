#!/usr/bin/env python3
"""Enforce ordered environment promotion using successful GitHub Deployment pointers."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Any

from artifact_archive import _request
from deployment_pointer import inspect_pointer

SCHEMA_VERSION = 1
IDENTITY_FIELDS = (
    "artifact_name",
    "bundle_sha256",
    "source_sha",
    "source_run_id",
    "release_tag",
)


def validate_policy(policy: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["promotion policy root must be an object"]
    if policy.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    environments = policy.get("environments")
    if not isinstance(environments, list) or not environments:
        errors.append("environments must be a non-empty array")
        environments = []
    elif not all(isinstance(item, str) and item.strip() for item in environments):
        errors.append("every environment must be a non-empty string")
        environments = []
    elif len(set(environments)) != len(environments):
        errors.append("environments must be unique")

    prerequisites = policy.get("prerequisites")
    if not isinstance(prerequisites, dict):
        errors.append("prerequisites must be an object")
        prerequisites = {}

    environment_set = set(environments)
    if environment_set and set(prerequisites) != environment_set:
        errors.append("prerequisites must define exactly every environment")
    for environment, prerequisite in prerequisites.items():
        if prerequisite is not None and prerequisite not in environment_set:
            errors.append(f"prerequisites.{environment} references unknown environment {prerequisite}")
        if prerequisite == environment:
            errors.append(f"prerequisites.{environment} cannot reference itself")

    if environments:
        first = environments[0]
        if prerequisites.get(first) is not None:
            errors.append("first environment must not require a prerequisite")
        for index, environment in enumerate(environments[1:], start=1):
            expected = environments[index - 1]
            if prerequisites.get(environment) != expected:
                errors.append(f"{environment} must require immediately previous environment {expected}")

    if policy.get("require_exact_artifact_identity") is not True:
        errors.append("require_exact_artifact_identity must be true")
    return errors


def load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read promotion policy: {exc}") from exc
    errors = validate_policy(payload)
    if errors:
        raise ValueError("invalid promotion policy: " + "; ".join(errors))
    return payload


def required_prerequisite(policy: dict[str, Any], target_environment: str) -> str | None:
    if target_environment not in policy["environments"]:
        raise ValueError(f"target environment is not allowed by promotion policy: {target_environment}")
    prerequisite = policy["prerequisites"][target_environment]
    if prerequisite is not None and not isinstance(prerequisite, str):
        raise ValueError("promotion prerequisite must be a string or null")
    return prerequisite


def normalize_identity(
    *,
    artifact_name: str,
    bundle_sha256: str,
    source_sha: str,
    source_run_id: str,
    release_tag: str,
) -> dict[str, str]:
    identity = {
        "artifact_name": artifact_name,
        "bundle_sha256": bundle_sha256,
        "source_sha": source_sha,
        "source_run_id": source_run_id,
        "release_tag": release_tag,
    }
    if not artifact_name:
        raise ValueError("artifact_name is required")
    if len(bundle_sha256) != 64 or any(character not in "0123456789abcdef" for character in bundle_sha256):
        raise ValueError("bundle_sha256 must be 64 lowercase hexadecimal characters")
    if len(source_sha) != 40 or any(character not in "0123456789abcdef" for character in source_sha):
        raise ValueError("source_sha must be a 40-character lowercase commit SHA")
    if not source_run_id.isdigit():
        raise ValueError("source_run_id must be numeric")
    if not release_tag.startswith("artifact-v2-"):
        raise ValueError("release_tag must reference an Artifact Contract v2 archive")
    return identity


def pointer_matches(pointer: dict[str, Any], identity: dict[str, str]) -> bool:
    return all(str(pointer.get(field, "")) == identity[field] for field in IDENTITY_FIELDS)


def find_successful_prerequisite(
    *,
    api_url: str,
    repository: str,
    token: str,
    prerequisite_environment: str,
    identity: dict[str, str],
) -> dict[str, str]:
    deployments_url = (
        f"{api_url.rstrip('/')}/repos/{repository}/deployments"
        f"?environment={prerequisite_environment}&per_page=100"
    )
    deployments = _request(deployments_url, token)
    if not isinstance(deployments, list):
        raise ValueError("deployments API returned an invalid response")

    for deployment in deployments:
        if not isinstance(deployment, dict):
            continue
        deployment_id = str(deployment.get("id", ""))
        if not deployment_id.isdigit():
            continue
        statuses_url = (
            f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses?per_page=20"
        )
        statuses = _request(statuses_url, token)
        if not isinstance(statuses, list) or not any(
            isinstance(status, dict) and status.get("state") == "success" for status in statuses
        ):
            continue
        try:
            pointer = inspect_pointer(
                api_url=api_url,
                repository=repository,
                token=token,
                deployment_id=deployment_id,
            )
        except (ValueError, json.JSONDecodeError, urllib.error.URLError, OSError):
            continue
        if pointer.get("environment") != prerequisite_environment:
            continue
        if pointer_matches(pointer, identity):
            return {
                "prerequisite_environment": prerequisite_environment,
                "prerequisite_deployment_id": deployment_id,
            }

    raise ValueError(
        "promotion blocked: exact artifact identity has no successful deployment in prerequisite "
        f"environment {prerequisite_environment}"
    )


def verify_promotion(
    *,
    policy: dict[str, Any],
    api_url: str,
    repository: str,
    token: str,
    target_environment: str,
    identity: dict[str, str],
) -> dict[str, str]:
    prerequisite = required_prerequisite(policy, target_environment)
    if prerequisite is None:
        return {
            "status": "allowed",
            "target_environment": target_environment,
            "prerequisite_environment": "",
            "prerequisite_deployment_id": "",
        }
    result = find_successful_prerequisite(
        api_url=api_url,
        repository=repository,
        token=token,
        prerequisite_environment=prerequisite,
        identity=identity,
    )
    return {
        "status": "allowed",
        "target_environment": target_environment,
        **result,
    }


def _emit(result: dict[str, str]) -> None:
    github_output = os.getenv("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/promotion-policy.json")
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--target-environment", required=True)
    verify.add_argument("--artifact-name", required=True)
    verify.add_argument("--bundle-sha256", required=True)
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--source-run-id", required=True)
    verify.add_argument("--release-tag", required=True)

    args = parser.parse_args()
    try:
        policy = load_policy(Path(args.policy))
        if args.command == "validate":
            print(f"OK: promotion path validated: {' -> '.join(policy['environments'])}")
            return 0

        token = os.getenv("GH_TOKEN", "").strip()
        if not token:
            raise ValueError("GH_TOKEN is required")
        identity = normalize_identity(
            artifact_name=args.artifact_name,
            bundle_sha256=args.bundle_sha256,
            source_sha=args.source_sha,
            source_run_id=args.source_run_id,
            release_tag=args.release_tag,
        )
        result = verify_promotion(
            policy=policy,
            api_url=args.api_url,
            repository=args.repository,
            token=token,
            target_environment=args.target_environment,
            identity=identity,
        )
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
