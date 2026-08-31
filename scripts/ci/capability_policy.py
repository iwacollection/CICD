#!/usr/bin/env python3
"""Validate business CI capability ownership without weakening Core contracts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORE_REQUIRED = {
    "trusted_build",
    "runner_trust_boundary",
    "toolchain_identity",
    "cache_identity",
    "artifact_contract_v2",
    "artifact_supply_chain",
    "attestation",
    "archive",
    "promotion",
    "rollback",
}
REQUIRED_CAPABILITIES = {"quality", "test", "container", "db_migration"}


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("capability policy root must be an object")
    return data


def validate_policy(policy: dict, repository_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != 1:
        errors.append("capability policy schema_version must be 1")

    core_owned = policy.get("core_owned")
    if not isinstance(core_owned, list) or not all(isinstance(v, str) and v for v in core_owned):
        errors.append("core_owned must be a non-empty string list")
        core_set: set[str] = set()
    else:
        core_set = set(core_owned)
        missing = sorted(CORE_REQUIRED - core_set)
        if missing:
            errors.append(f"core_owned is missing stable Core responsibilities: {missing}")
        if len(core_set) != len(core_owned):
            errors.append("core_owned must not contain duplicates")

    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("capabilities must be an object")
        capabilities = {}
    else:
        missing = sorted(REQUIRED_CAPABILITIES - set(capabilities))
        if missing:
            errors.append(f"missing phase-1 capabilities: {missing}")

    for capability_id, item in capabilities.items():
        if not isinstance(capability_id, str) or not capability_id:
            errors.append("capability id must be a non-empty string")
            continue
        if not isinstance(item, dict):
            errors.append(f"capability {capability_id} must be an object")
            continue
        workflow = item.get("workflow")
        if not isinstance(workflow, str) or not workflow.startswith(".github/workflows/reusable-"):
            errors.append(f"capability {capability_id}.workflow must reference a reusable workflow")
        elif repository_root is not None and not (repository_root / workflow).is_file():
            errors.append(f"capability {capability_id} workflow does not exist: {workflow}")
        if item.get("runner_class") != "hosted-only":
            errors.append(f"capability {capability_id} must remain hosted-only in v1")
        for field in ("may_publish_release", "may_deploy", "may_use_self_hosted"):
            if item.get(field) is not False:
                errors.append(f"capability {capability_id}.{field} must be false")
        responsibilities = item.get("responsibilities")
        if not isinstance(responsibilities, list) or not responsibilities or not all(
            isinstance(v, str) and v for v in responsibilities
        ):
            errors.append(f"capability {capability_id}.responsibilities must be a non-empty string list")
        elif set(responsibilities) & core_set:
            errors.append(
                f"capability {capability_id} overlaps Core responsibilities: "
                f"{sorted(set(responsibilities) & core_set)}"
            )

    profiles = policy.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        errors.append("profiles must be a non-empty object")
    else:
        for profile, enabled in profiles.items():
            if not isinstance(profile, str) or not profile:
                errors.append("profile name must be a non-empty string")
                continue
            if not isinstance(enabled, list) or not enabled or not all(isinstance(v, str) for v in enabled):
                errors.append(f"profile {profile} must contain at least one capability id")
                continue
            if len(enabled) != len(set(enabled)):
                errors.append(f"profile {profile} contains duplicate capabilities")
            unknown = sorted(set(enabled) - set(capabilities))
            if unknown:
                errors.append(f"profile {profile} references unknown capabilities: {unknown}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/capabilities.json")
    parser.add_argument("--repository-root", default=".")
    args = parser.parse_args()
    try:
        policy = load_json(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    errors = validate_policy(policy, Path(args.repository_root).resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "capability-policy-passed", "profiles": sorted(policy["profiles"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
