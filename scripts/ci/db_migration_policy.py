#!/usr/bin/env python3
"""Static safety gate for SQL migration files.

This intentionally catches only high-confidence destructive patterns. It does not
pretend to replace database-specific online-schema-change review.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("database migration policy root must be an object")
    return data


def validate_policy(policy: dict) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != 1:
        errors.append("db migration policy schema_version must be 1")
    if policy.get("engine") != "postgresql":
        errors.append("db migration policy engine must be postgresql")
    rules = policy.get("forbidden_sql")
    if not isinstance(rules, list) or not rules:
        errors.append("forbidden_sql must be a non-empty list")
        return errors
    ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            errors.append("each forbidden_sql rule must be an object")
            continue
        rule_id = rule.get("id")
        pattern = rule.get("pattern")
        reason = rule.get("reason")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append("forbidden_sql rule id is required")
        elif rule_id in ids:
            errors.append(f"duplicate forbidden_sql rule id: {rule_id}")
        else:
            ids.add(rule_id)
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"rule {rule_id!r} pattern is required")
        else:
            try:
                re.compile(pattern, re.IGNORECASE | re.DOTALL)
            except re.error as exc:
                errors.append(f"rule {rule_id!r} invalid regex: {exc}")
        if not isinstance(reason, str) or not reason:
            errors.append(f"rule {rule_id!r} reason is required")
        must_not = rule.get("must_not_contain")
        if must_not is not None:
            if not isinstance(must_not, str) or not must_not:
                errors.append(f"rule {rule_id!r} must_not_contain must be a non-empty string")
            else:
                try:
                    re.compile(must_not, re.IGNORECASE | re.DOTALL)
                except re.error as exc:
                    errors.append(f"rule {rule_id!r} invalid must_not_contain regex: {exc}")
    return errors


def strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    return text


def scan_text(text: str, policy: dict, source: str) -> list[str]:
    clean = strip_sql_comments(text)
    findings: list[str] = []
    for rule in policy.get("forbidden_sql", []):
        pattern = re.compile(rule["pattern"], re.IGNORECASE | re.DOTALL)
        excluded = re.compile(rule["must_not_contain"], re.IGNORECASE | re.DOTALL) if rule.get("must_not_contain") else None
        for match in pattern.finditer(clean):
            statement = match.group(0)
            if excluded is not None and excluded.search(statement):
                continue
            line = clean.count("\n", 0, match.start()) + 1
            findings.append(f"{source}:{line}: {rule['id']}: {rule['reason']}")
    return findings


def expand_patterns(root: Path, patterns: list[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        for raw in glob.glob(str(root / pattern), recursive=True):
            path = Path(raw)
            if path.is_file():
                found[path.resolve().as_posix()] = path.resolve()
    return [found[key] for key in sorted(found)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/db-migration-policy.json")
    parser.add_argument("--root", default=".")
    parser.add_argument("--patterns-json", default='["migrations/**/*.sql","db/**/*.sql"]')
    parser.add_argument("--validate-policy", action="store_true")
    args = parser.parse_args()

    try:
        policy = load_json(Path(args.policy))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    errors = validate_policy(policy)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.validate_policy:
        print(json.dumps({"status": "db-migration-policy-passed"}))
        return 0

    try:
        patterns = json.loads(args.patterns_json)
    except json.JSONDecodeError as exc:
        print(f"ERROR: patterns-json is invalid: {exc}", file=sys.stderr)
        return 2
    if not isinstance(patterns, list) or not all(isinstance(v, str) and v for v in patterns):
        print("ERROR: patterns-json must be a JSON string array", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    files = expand_patterns(root, patterns)
    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(f"{path}: cannot read migration: {exc}")
            continue
        findings.extend(scan_text(text, policy, path.relative_to(root).as_posix()))

    if findings:
        for finding in findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "sql-policy-passed", "files_scanned": len(files)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
