#!/usr/bin/env python3
"""Validate the fail-closed Argo CD / Argo Rollouts integration contract.

This validator checks repository configuration only. It never connects to Kubernetes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

OWNERS = {"none", "cicd-renderer", "argo-rollouts"}
MODES = {"observe-only", "local-lab", "non-production", "production"}
REQUIRED_DEFAULTS = {
    "mode": "observe-only",
    "traffic_owner": "none",
    "allow_promote": False,
    "allow_abort": False,
    "allow_production": False,
    "fail_closed_on_inconclusive": True,
    "require_immutable_image_digest": True,
    "require_human_approval_for_production": True,
}


def validate_contract(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["contract root must be a JSON object"]

    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        return ["defaults must be an object"]
    for key, expected in REQUIRED_DEFAULTS.items():
        if defaults.get(key) != expected:
            errors.append(f"defaults.{key} must be {expected!r}")

    if document.get("traffic_owners") != sorted(OWNERS):
        # Order is intentionally stable to make reviews and diffs predictable.
        errors.append("traffic_owners must contain exactly: argo-rollouts, cicd-renderer, none")
    if set(document.get("modes", [])) != MODES:
        errors.append("modes must contain exactly the four supported modes")

    rules = document.get("ownership_rules")
    if not isinstance(rules, dict):
        errors.append("ownership_rules must be an object")
    else:
        for key in (
            "single_writer_per_route",
            "cicd_renderer_and_argo_rollouts_must_not_both_write_weights",
            "rollouts_status_is_controller_owned",
            "gitops_declares_rollout_spec",
            "observer_is_read_only",
        ):
            if rules.get(key) is not True:
                errors.append(f"ownership_rules.{key} must be true")

    analysis = document.get("analysis_policy")
    if not isinstance(analysis, dict):
        errors.append("analysis_policy must be an object")
    else:
        for key in (
            "successful_requires_all_queries_valid",
            "inconclusive_on_missing_samples",
            "inconclusive_on_empty_or_nan_values",
            "inconclusive_on_prometheus_unavailable",
            "inconclusive_on_query_error",
            "timeout_is_not_success",
        ):
            if analysis.get(key) is not True:
                errors.append(f"analysis_policy.{key} must be true")

    environments = document.get("environments")
    if not isinstance(environments, list):
        errors.append("environments must be an array")
        return errors

    seen: set[tuple[str, str]] = set()
    for index, env in enumerate(environments):
        prefix = f"environments[{index}]"
        if not isinstance(env, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in ("environment", "cluster_id", "namespace", "mode", "traffic_owner"):
            if not isinstance(env.get(field), str) or not env[field].strip():
                errors.append(f"{prefix}.{field} must be a non-empty string")
        mode = env.get("mode")
        owner = env.get("traffic_owner")
        if mode not in MODES:
            errors.append(f"{prefix}.mode is unsupported")
        if owner not in OWNERS:
            errors.append(f"{prefix}.traffic_owner is unsupported")
        identity = (str(env.get("environment")), str(env.get("cluster_id")))
        if identity in seen:
            errors.append(f"{prefix} duplicates environment/cluster_id {identity!r}")
        seen.add(identity)

        if owner == "cicd-renderer" and env.get("rollouts_manages_traffic") is True:
            errors.append(f"{prefix} assigns traffic weights to both CICD renderer and Rollouts")
        if owner == "argo-rollouts" and env.get("cicd_renderer_manages_traffic") is True:
            errors.append(f"{prefix} assigns traffic weights to both Rollouts and CICD renderer")
        if mode == "production":
            if env.get("allow_production") is not True:
                errors.append(f"{prefix} production mode requires allow_production=true")
            if env.get("human_approval_required") is not True:
                errors.append(f"{prefix} production mode requires human_approval_required=true")
            if env.get("allow_promote") is True and env.get("promote_approval_gate") is not True:
                errors.append(f"{prefix} production Promote requires promote_approval_gate=true")
            if env.get("allow_abort") is True and env.get("abort_approval_gate") is not True:
                errors.append(f"{prefix} production Abort requires abort_approval_gate=true")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default="ci/argo-rollouts-integration.json")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    try:
        document = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load {args.policy}: {exc}", file=sys.stderr)
        return 1
    errors = validate_contract(document)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: Argo Rollouts integration contract is valid (configuration-only; no cluster access)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
