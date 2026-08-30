#!/usr/bin/env python3
"""Audit GitHub repository Ruleset state against versioned CI governance policy."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


def validate_policy(policy: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["governance policy root must be an object"]
    if policy.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("ruleset_name", "target", "enforcement"):
        value = policy.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be a non-empty string")

    include_refs = policy.get("include_refs")
    if not isinstance(include_refs, list) or not include_refs or not all(
        isinstance(item, str) and item.strip() for item in include_refs
    ):
        errors.append("include_refs must be a non-empty string array")

    required_types = policy.get("required_rule_types")
    if not isinstance(required_types, list) or not required_types or not all(
        isinstance(item, str) and item.strip() for item in required_types
    ):
        errors.append("required_rule_types must be a non-empty string array")
    elif len(set(required_types)) != len(required_types):
        errors.append("required_rule_types must be unique")

    pr_policy = policy.get("pull_request")
    if not isinstance(pr_policy, dict):
        errors.append("pull_request must be an object")
    else:
        approvals = pr_policy.get("required_approving_review_count")
        if not isinstance(approvals, int) or isinstance(approvals, bool) or approvals < 0:
            errors.append("pull_request.required_approving_review_count must be >= 0")
        for key in (
            "dismiss_stale_reviews_on_push",
            "require_code_owner_review",
            "required_review_thread_resolution",
            "require_extra_approval_for_unattributed_changes",
        ):
            if not isinstance(pr_policy.get(key), bool):
                errors.append(f"pull_request.{key} must be boolean")

    status_policy = policy.get("required_status_checks")
    if not isinstance(status_policy, dict):
        errors.append("required_status_checks must be an object")
    else:
        if status_policy.get("strict_required_status_checks_policy") is not True:
            errors.append("required_status_checks.strict_required_status_checks_policy must be true")
        contexts = status_policy.get("contexts")
        if not isinstance(contexts, list) or not contexts or not all(
            isinstance(item, str) and item.strip() for item in contexts
        ):
            errors.append("required_status_checks.contexts must be a non-empty string array")
        elif len(set(contexts)) != len(contexts):
            errors.append("required_status_checks.contexts must be unique")

    bypass_policy = policy.get("bypass_actors")
    if not isinstance(bypass_policy, dict):
        errors.append("bypass_actors must be an object")
    else:
        if bypass_policy.get("allow_when_visible") is not False:
            errors.append("bypass_actors.allow_when_visible must be false")
        if not isinstance(bypass_policy.get("visibility_required"), bool):
            errors.append("bypass_actors.visibility_required must be boolean")
    return errors


def load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read repository governance policy: {exc}") from exc
    errors = validate_policy(payload)
    if errors:
        raise ValueError("invalid repository governance policy: " + "; ".join(errors))
    return payload


def _request(url: str, token: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "enterprise-ci-governance-audit/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"GitHub governance API request failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub governance API returned invalid JSON") from exc


def fetch_ruleset(*, repository: str, token: str, api_url: str, ruleset_name: str) -> dict[str, Any]:
    if repository.count("/") != 1:
        raise ValueError("repository must use owner/name form")
    encoded_repository = urllib.parse.quote(repository, safe="/")
    base = api_url.rstrip("/")
    rulesets = _request(f"{base}/repos/{encoded_repository}/rulesets", token)
    if not isinstance(rulesets, list):
        raise RuntimeError("rulesets API returned an invalid response")
    match = next(
        (
            item
            for item in rulesets
            if isinstance(item, dict) and item.get("name") == ruleset_name and str(item.get("id", "")).isdigit()
        ),
        None,
    )
    if match is None:
        raise ValueError(f"required repository ruleset is missing: {ruleset_name}")
    ruleset_id = str(match["id"])
    detail = _request(f"{base}/repos/{encoded_repository}/rulesets/{ruleset_id}", token)
    if not isinstance(detail, dict):
        raise RuntimeError("ruleset detail API returned an invalid response")
    return detail


def _rule_by_type(ruleset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules = ruleset.get("rules")
    if not isinstance(rules, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if isinstance(rule, dict) and isinstance(rule.get("type"), str):
            result[rule["type"]] = rule
    return result


def evaluate_ruleset(ruleset: dict[str, Any], policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    warnings: list[str] = []
    if ruleset.get("name") != policy["ruleset_name"]:
        violations.append(f"ruleset name mismatch: {ruleset.get('name')!r}")
    if ruleset.get("target") != policy["target"]:
        violations.append(f"ruleset target must be {policy['target']}")
    if ruleset.get("enforcement") != policy["enforcement"]:
        violations.append(f"ruleset enforcement must be {policy['enforcement']}")

    ref_name = ruleset.get("conditions", {}).get("ref_name", {}) if isinstance(ruleset.get("conditions"), dict) else {}
    includes = set(ref_name.get("include", [])) if isinstance(ref_name, dict) and isinstance(ref_name.get("include"), list) else set()
    for expected in policy["include_refs"]:
        if expected not in includes:
            violations.append(f"ruleset must include protected ref selector {expected}")

    rules = _rule_by_type(ruleset)
    for required_type in policy["required_rule_types"]:
        if required_type not in rules:
            violations.append(f"required ruleset rule is missing: {required_type}")

    pull_request_rule = rules.get("pull_request", {})
    pr_parameters = pull_request_rule.get("parameters", {}) if isinstance(pull_request_rule, dict) else {}
    expected_pr = policy["pull_request"]
    actual_approvals = pr_parameters.get("required_approving_review_count") if isinstance(pr_parameters, dict) else None
    expected_approvals = expected_pr["required_approving_review_count"]
    if not isinstance(actual_approvals, int) or actual_approvals != expected_approvals:
        violations.append(
            "required approving review count must equal policy value "
            f"{expected_approvals}"
        )
    for key in (
        "dismiss_stale_reviews_on_push",
        "require_code_owner_review",
        "required_review_thread_resolution",
        "require_extra_approval_for_unattributed_changes",
    ):
        expected_value = expected_pr[key]
        actual_value = pr_parameters.get(key) if isinstance(pr_parameters, dict) else None
        if actual_value is not expected_value:
            violations.append(f"pull request rule must keep {key}={str(expected_value).lower()}")

    status_rule = rules.get("required_status_checks", {})
    status_parameters = status_rule.get("parameters", {}) if isinstance(status_rule, dict) else {}
    if not isinstance(status_parameters, dict) or status_parameters.get("strict_required_status_checks_policy") is not True:
        violations.append("required status checks must use strict_required_status_checks_policy=true")
    actual_checks: set[str] = set()
    raw_checks = status_parameters.get("required_status_checks", []) if isinstance(status_parameters, dict) else []
    if isinstance(raw_checks, list):
        for check in raw_checks:
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                actual_checks.add(check["context"])
    for expected_context in policy["required_status_checks"]["contexts"]:
        if expected_context not in actual_checks:
            violations.append(f"required status check is missing: {expected_context}")

    bypass_policy = policy["bypass_actors"]
    bypass_actors = ruleset.get("bypass_actors")
    if isinstance(bypass_actors, list):
        if bypass_actors and bypass_policy["allow_when_visible"] is False:
            violations.append("ruleset bypass actors are not allowed")
    elif bypass_policy["visibility_required"]:
        violations.append("ruleset bypass_actors visibility is required but unavailable")
    else:
        warnings.append(
            "bypass_actors not visible to this audit identity; GitHub only guarantees this field to identities with ruleset write access"
        )
    return violations, warnings


def build_report(ruleset: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    violations, warnings = evaluate_ruleset(ruleset, policy)
    if violations:
        status = "drifted"
    elif warnings:
        status = "healthy-with-limited-visibility"
    else:
        status = "healthy"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "ruleset_id": str(ruleset.get("id", "")),
        "ruleset_name": str(ruleset.get("name", "")),
        "violations": violations,
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Repository Governance Audit",
        "",
        f"Status: **{report['status']}**",
        f"Ruleset: `{report['ruleset_name']}` (id `{report['ruleset_id']}`)",
        "",
        "## Drift findings",
        "",
    ]
    if report["violations"]:
        lines.extend(f"- {item}" for item in report["violations"])
    else:
        lines.append("- None in the observable Ruleset fields.")
    lines.extend(["", "## Visibility warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item}" for item in report["warnings"])
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/repository-governance-policy.json")
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--ruleset-json", default="")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args()

    try:
        policy = load_policy(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.validate_only:
        print(json.dumps({"status": "valid", "schema_version": SCHEMA_VERSION}, separators=(",", ":")))
        return 0

    try:
        if args.ruleset_json:
            ruleset = json.loads(Path(args.ruleset_json).read_text(encoding="utf-8"))
            if not isinstance(ruleset, dict):
                raise ValueError("ruleset JSON root must be an object")
        else:
            if not args.repository:
                raise ValueError("--repository or GITHUB_REPOSITORY is required")
            ruleset = fetch_ruleset(
                repository=args.repository,
                token=args.token,
                api_url=args.api_url,
                ruleset_name=policy["ruleset_name"],
            )
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = build_report(ruleset, policy)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_out:
        output = Path(args.markdown_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "violations": len(report["violations"]), "warnings": len(report["warnings"])}, separators=(",", ":")))
    if args.fail_on_drift and report["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
