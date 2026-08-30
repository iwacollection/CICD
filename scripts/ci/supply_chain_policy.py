#!/usr/bin/env python3
"""Validate immutable dependency policy and Trivy security reports."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
SNAPSHOT_RE = re.compile(r"(?:APT::Snapshot|--snapshot)[=\s\"']+([0-9]{8}T[0-9]{6}Z)")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def validate_policy(policy: dict) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != 1:
        errors.append("supply-chain policy schema_version must be 1")
    scanner = policy.get("scanner")
    if not isinstance(scanner, dict) or scanner.get("name") != "trivy":
        errors.append("policy scanner.name must be trivy")
    elif not isinstance(scanner.get("version"), str) or not scanner.get("version"):
        errors.append("policy scanner.version must be pinned")
    for section in ("vulnerability", "license", "misconfiguration"):
        item = policy.get(section)
        if not isinstance(item, dict):
            errors.append(f"policy.{section} must be an object")
            continue
        severities = item.get("deny_severities")
        if not isinstance(severities, list) or not all(
            isinstance(value, str) and value in {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
            for value in severities
        ):
            errors.append(f"policy.{section}.deny_severities is invalid")
    return errors


def validate_dockerfile(path: Path, policy: dict) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    container = policy.get("container", {})
    from_lines = [line.strip() for line in text.splitlines() if line.strip().upper().startswith("FROM ")]
    if not from_lines:
        return [f"{path}: Dockerfile has no FROM instruction"]
    if container.get("require_base_image_digest", True):
        for line in from_lines:
            if not DIGEST_RE.search(line + " "):
                errors.append(f"{path}: base image must be pinned by full sha256 digest: {line}")
    if container.get("forbid_latest_tag", True):
        for line in from_lines:
            if ":latest" in line:
                errors.append(f"{path}: latest tag is forbidden: {line}")
    if "apt-get" in text or re.search(r"\bapt\s+(?:install|update)\b", text):
        if container.get("require_apt_snapshot", True) and not SNAPSHOT_RE.search(text):
            errors.append(f"{path}: apt usage must be bound to an immutable Ubuntu Snapshot ID")
    return errors


def _severity(item: dict) -> str:
    return str(item.get("Severity", "UNKNOWN")).upper()


def validate_trivy_report(report: dict, policy: dict) -> tuple[list[str], dict]:
    errors: list[str] = []
    summary = {
        "vulnerabilities": 0,
        "licenses": 0,
        "secrets": 0,
        "misconfigurations": 0,
    }
    vuln_denied = set(policy.get("vulnerability", {}).get("deny_severities", []))
    license_denied = set(policy.get("license", {}).get("deny_severities", []))
    misconfig_denied = set(policy.get("misconfiguration", {}).get("deny_severities", []))
    deny_secrets = bool(policy.get("secret", {}).get("deny_any_finding", True))

    results = report.get("Results", [])
    if not isinstance(results, list):
        return ["Trivy report Results must be a list"], summary
    for result in results:
        if not isinstance(result, dict):
            continue
        target = str(result.get("Target", "unknown"))
        for finding in result.get("Vulnerabilities") or []:
            if not isinstance(finding, dict):
                continue
            summary["vulnerabilities"] += 1
            if _severity(finding) in vuln_denied:
                errors.append(
                    f"vulnerability blocked: {finding.get('VulnerabilityID', 'unknown')} "
                    f"severity={_severity(finding)} target={target} package={finding.get('PkgName', '')}"
                )
        for finding in result.get("Licenses") or []:
            if not isinstance(finding, dict):
                continue
            summary["licenses"] += 1
            if _severity(finding) in license_denied:
                errors.append(
                    f"license blocked: {finding.get('Name', finding.get('Category', 'unknown'))} "
                    f"severity={_severity(finding)} target={target}"
                )
        for finding in result.get("Secrets") or []:
            if not isinstance(finding, dict):
                continue
            summary["secrets"] += 1
            if deny_secrets:
                errors.append(
                    f"secret finding blocked: {finding.get('RuleID', 'unknown')} target={target}"
                )
        for finding in result.get("Misconfigurations") or []:
            if not isinstance(finding, dict):
                continue
            summary["misconfigurations"] += 1
            if _severity(finding) in misconfig_denied:
                errors.append(
                    f"misconfiguration blocked: {finding.get('ID', 'unknown')} "
                    f"severity={_severity(finding)} target={target}"
                )
    return errors, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/supply-chain-policy.json")
    parser.add_argument("--report")
    parser.add_argument("--dockerfiles", nargs="*")
    args = parser.parse_args()

    try:
        policy = load_json(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    errors = validate_policy(policy)
    dockerfiles = [Path(path) for path in (args.dockerfiles or [])]
    if not dockerfiles:
        dockerfiles = sorted(Path("docker").rglob("Dockerfile")) if Path("docker").exists() else []
    for path in dockerfiles:
        try:
            errors.extend(validate_dockerfile(path, policy))
        except OSError as exc:
            errors.append(f"cannot read Dockerfile {path}: {exc}")

    summary: dict = {}
    if args.report:
        try:
            report = load_json(Path(args.report))
        except ValueError as exc:
            errors.append(str(exc))
        else:
            report_errors, summary = validate_trivy_report(report, policy)
            errors.extend(report_errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "policy-passed", "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
