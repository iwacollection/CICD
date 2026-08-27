#!/usr/bin/env python3
"""Validate that a promotion references an eligible, successful build run."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def validate_dispatch_inputs(run_id: str, artifact_name: str, expected_sha256: str) -> list[str]:
    errors: list[str] = []
    if not run_id.isdigit() or int(run_id) <= 0:
        errors.append("source_run_id must be a positive integer")
    if not ARTIFACT_RE.fullmatch(artifact_name):
        errors.append("artifact_name contains unsupported characters")
    if not SHA256_RE.fullmatch(expected_sha256):
        errors.append("expected_sha256 must be 64 lowercase hexadecimal characters")
    return errors


def validate_run_metadata(run: dict, repository: str, run_id: str) -> list[str]:
    errors: list[str] = []
    if str(run.get("id", "")) != run_id:
        errors.append("workflow run ID does not match the requested source_run_id")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        errors.append("source workflow run must be completed successfully")
    if run.get("event") != "push":
        errors.append("source workflow run must be produced by a push event")
    if run.get("head_branch") != "main":
        errors.append("source workflow run must build the main branch")
    if run.get("path") != ".github/workflows/ci.yml":
        errors.append("source workflow run must come from .github/workflows/ci.yml")
    if run.get("repository", {}).get("full_name") != repository:
        errors.append("source workflow run belongs to a different repository")
    if not COMMIT_RE.fullmatch(str(run.get("head_sha", ""))):
        errors.append("source workflow run has an invalid head SHA")
    return errors


def fetch_run(api_url: str, repository: str, run_id: str, token: str) -> dict:
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/runs/{run_id}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    errors = validate_dispatch_inputs(args.run_id, args.artifact_name, args.expected_sha256)
    if errors:
        raise SystemExit("invalid promotion inputs:\n" + "\n".join(errors))

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise SystemExit("GH_TOKEN is required to inspect the source workflow run")

    try:
        run = fetch_run(args.api_url, args.repository, args.run_id, token)
    except Exception as exc:  # Network/API errors must fail closed.
        raise SystemExit(f"unable to inspect source workflow run: {exc}") from exc

    errors = validate_run_metadata(run, args.repository, args.run_id)
    if errors:
        raise SystemExit("ineligible promotion source:\n" + "\n".join(errors))

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"source_sha={run['head_sha']}\n")
    print(
        json.dumps(
            {
                "status": "eligible",
                "run_id": args.run_id,
                "repository": args.repository,
                "source_sha": run["head_sha"],
                "workflow": run["path"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
