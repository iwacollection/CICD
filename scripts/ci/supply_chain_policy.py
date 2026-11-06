#!/usr/bin/env python3
"""Validate immutable dependency policy and Trivy security evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
SNAPSHOT_RE = re.compile(r"(?:APT::Snapshot|--snapshot)[=\s\"']+([0-9]{8}T[0-9]{6}Z)")
VALID_SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _validate_severity_section(policy: dict, section: str, errors: list[str]) -> None:
    item = policy.get(section)
    if not isinstance(item, dict):
        errors.append(f"policy.{section} must be an object")
        return
    severities = item.get("deny_severities")
    if not isinstance(severities, list) or not all(
        isinstance(value, str) and value in VALID_SEVERITIES for value in severities
    ):
        errors.append(f"policy.{section}.deny_severities is invalid")


def validate_policy(policy: dict) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != 1:
        errors.append("supply-chain policy schema_version must be 1")
    scanner = policy.get("scanner")
    if not isinstance(scanner, dict) or scanner.get("name") != "trivy":
        errors.append("policy scanner.name must be trivy")
    elif not isinstance(scanner.get("version"), str) or not scanner.get("version"):
        errors.append("policy scanner.version must be pinned")
    elif scanner.get("ignore_unfixed_vulnerabilities") is not False:
        errors.append("policy must scan unfixed vulnerabilities; exceptions require explicit approval")

    _validate_severity_section(policy, "vulnerability", errors)
    _validate_severity_section(policy, "misconfiguration", errors)

    license_policy = policy.get("license")
    if not isinstance(license_policy, dict):
        errors.append("policy.license must be an object")
    else:
        denied = license_policy.get("deny_licenses")
        if not isinstance(denied, list) or not all(
            isinstance(value, str) and value.strip() for value in denied
        ):
            errors.append("policy.license.deny_licenses must be a string list")
        elif len(denied) != len(set(denied)):
            errors.append("policy.license.deny_licenses must not contain duplicates")

    exceptions = policy.get("vulnerability_exceptions", [])
    if not isinstance(exceptions, list):
        errors.append("policy.vulnerability_exceptions must be a list")
        exceptions = []
    required_exception_fields = {
        "id", "toolchain", "image_digest", "vulnerability_id", "package",
        "severity", "justification", "owner", "approved_by", "ticket",
        "approved_at", "expires_at", "compensating_controls",
    }
    seen_exceptions: set[str] = set()
    for index, exception in enumerate(exceptions):
        prefix = f"policy.vulnerability_exceptions[{index}]"
        if not isinstance(exception, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = required_exception_fields - set(exception)
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
            continue
        exception_id = exception.get("id")
        if not isinstance(exception_id, str) or not exception_id or exception_id in seen_exceptions:
            errors.append(f"{prefix}.id must be unique and non-empty")
        else:
            seen_exceptions.add(exception_id)
        for field in ("toolchain", "package", "justification", "owner", "approved_by", "ticket", "compensating_controls"):
            if not isinstance(exception.get(field), str) or not exception[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        if not isinstance(exception.get("image_digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", exception["image_digest"]):
            errors.append(f"{prefix}.image_digest must be an exact sha256 digest")
        if not isinstance(exception.get("vulnerability_id"), str) or not re.fullmatch(r"(?:CVE-[0-9]{4}-[0-9]+|GHSA-[0-9a-z-]+)", exception["vulnerability_id"]):
            errors.append(f"{prefix}.vulnerability_id must be an exact CVE/GHSA identifier")
        if exception.get("severity") not in {"HIGH", "CRITICAL"}:
            errors.append(f"{prefix}.severity must be HIGH or CRITICAL")
        try:
            approved_at = datetime.fromisoformat(str(exception.get("approved_at", "")).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(exception.get("expires_at", "")).replace("Z", "+00:00"))
            if approved_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= approved_at:
                raise ValueError("timestamps must be timezone-aware and expires_at must be later")
            if (expires_at - approved_at).days > 90:
                errors.append(f"{prefix} exception lifetime cannot exceed 90 days")
        except ValueError:
            errors.append(f"{prefix} approved_at/expires_at must be valid timezone-aware ISO-8601 timestamps")

    artifact = policy.get("artifact")
    if not isinstance(artifact, dict):
        errors.append("policy.artifact must be an object")
    else:
        for field in ("require_sbom", "require_scan_report", "require_cosign_bundle_for_archive"):
            if not isinstance(artifact.get(field), bool):
                errors.append(f"policy.artifact.{field} must be boolean")
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


def validate_trivy_report(
    report: dict,
    policy: dict,
    *,
    toolchain: str = "",
    image_digest: str = "",
    now: datetime | None = None,
) -> tuple[list[str], dict]:
    errors: list[str] = []
    summary = {
        "vulnerabilities": 0,
        "licenses": 0,
        "denied_licenses": 0,
        "secrets": 0,
        "misconfigurations": 0,
        "vulnerability_exceptions_applied": 0,
    }
    vuln_denied = set(policy.get("vulnerability", {}).get("deny_severities", []))
    denied_licenses = set(policy.get("license", {}).get("deny_licenses", []))
    misconfig_denied = set(policy.get("misconfiguration", {}).get("deny_severities", []))
    deny_secrets = bool(policy.get("secret", {}).get("deny_any_finding", True))
    now = now or datetime.now(timezone.utc)
    exceptions = policy.get("vulnerability_exceptions", [])

    def exception_for(finding: dict) -> dict | None:
        for exception in exceptions:
            if (
                exception.get("toolchain") != toolchain
                or exception.get("image_digest") != image_digest
                or exception.get("vulnerability_id") != finding.get("VulnerabilityID")
                or exception.get("package") != finding.get("PkgName")
                or exception.get("severity") != _severity(finding)
            ):
                continue
            try:
                expiry = datetime.fromisoformat(str(exception["expires_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if expiry.tzinfo is not None and expiry > now:
                return exception
        return None

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
                exception = exception_for(finding)
                if exception is not None:
                    summary["vulnerability_exceptions_applied"] += 1
                    continue
                errors.append(
                    f"vulnerability blocked: {finding.get('VulnerabilityID', 'unknown')} "
                    f"severity={_severity(finding)} target={target} package={finding.get('PkgName', '')}"
                )
        for finding in result.get("Licenses") or []:
            if not isinstance(finding, dict):
                continue
            summary["licenses"] += 1
            name = str(finding.get("Name", finding.get("Category", "unknown")))
            if name in denied_licenses:
                summary["denied_licenses"] += 1
                errors.append(f"license blocked: {name} severity={_severity(finding)} target={target}")
        for finding in result.get("Secrets") or []:
            if not isinstance(finding, dict):
                continue
            summary["secrets"] += 1
            if deny_secrets:
                errors.append(f"secret finding blocked: {finding.get('RuleID', 'unknown')} target={target}")
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


def validate_sbom(sbom: dict) -> list[str]:
    errors: list[str] = []
    if sbom.get("bomFormat") != "CycloneDX":
        errors.append("SBOM must use CycloneDX format")
    spec_version = sbom.get("specVersion")
    if not isinstance(spec_version, str) or not spec_version:
        errors.append("SBOM specVersion is required")
    components = sbom.get("components", [])
    if not isinstance(components, list):
        errors.append("SBOM components must be a list")
    metadata = sbom.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append("SBOM metadata must be an object when present")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/supply-chain-policy.json")
    parser.add_argument("--report")
    parser.add_argument("--sbom")
    parser.add_argument("--toolchain", default="")
    parser.add_argument("--image-digest", default="")
    parser.add_argument("--dockerfiles", nargs="*")
    parser.add_argument(
        "--skip-dockerfiles",
        action="store_true",
        help="Validate report/SBOM evidence without re-checking repository Dockerfiles.",
    )
    args = parser.parse_args()

    try:
        policy = load_json(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    errors = validate_policy(policy)

    if not args.skip_dockerfiles:
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
            report_errors, summary = validate_trivy_report(
                report, policy, toolchain=args.toolchain, image_digest=args.image_digest
            )
            errors.extend(report_errors)

    if args.sbom:
        try:
            sbom = load_json(Path(args.sbom))
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(validate_sbom(sbom))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "policy-passed", "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
