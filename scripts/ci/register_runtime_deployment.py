#!/usr/bin/env python3
"""Append a GitHub Deployment record only after runtime evidence was verified."""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.error
from pathlib import Path
from typing import Any
from artifact_archive import _request

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{7,40}$")

def register_runtime(*, api_url: str, repository: str, token: str,
                     evidence: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version": 1, "kind": "argocd-kubernetes-runtime-verification",
                "argocd_sync": "Synced", "argocd_health": "Healthy"}
    for key, expected in required.items():
        if evidence.get(key) != expected:
            raise ValueError(f"runtime evidence {key} must equal {expected}")
    for field in ("image_digest", "manifest_sha256"):
        value = str(evidence.get(field, ""))
        if not SHA256_RE.fullmatch(value if field == "image_digest" else "sha256:" + value):
            raise ValueError(f"runtime evidence {field} is invalid")
    revision = str(evidence.get("gitops_revision", ""))
    if not REVISION_RE.fullmatch(revision):
        raise ValueError("runtime evidence GitOps revision is invalid")
    if not evidence.get("application") or not evidence.get("namespace"):
        raise ValueError("runtime evidence application and namespace are required")
    pods = evidence.get("verified_pods")
    if not isinstance(pods, list) or not pods or evidence.get("pod_count") != len(pods):
        raise ValueError("runtime evidence must contain a matching non-empty Pod list")
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("evidence_sha256", ""))):
        raise ValueError("runtime evidence evidence_sha256 is invalid")
    environment_name = evidence.get("environment")
    if environment_name not in {"dev", "staging", "production"}:
        raise ValueError("runtime evidence environment is invalid")

    environment = f"runtime-{environment_name}"
    deployments_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments"
    payload = {key: evidence[key] for key in (
        "schema_version", "kind", "application", "namespace", "pod_selector",
        "image_digest", "expected_image", "gitops_revision", "manifest_sha256",
        "argocd_sync", "argocd_health", "pod_count", "verified_pods",
        "verified_at", "evidence_sha256")}
    deployment = _request(deployments_url, token, method="POST", payload={
        "ref": revision, "task": "runtime-verification", "auto_merge": False,
        "required_contexts": [], "environment": environment,
        "description": f"Verified {evidence['application']} @ {evidence['image_digest']}",
        "transient_environment": False, "production_environment": environment_name == "production",
        "payload": payload})
    deployment_id = str(deployment.get("id", "")) if isinstance(deployment, dict) else ""
    if not deployment_id.isdigit():
        raise ValueError("GitHub did not return a runtime Deployment ID")
    status = _request(f"{deployments_url}/{deployment_id}/statuses", token, method="POST", payload={
        "state": "success", "environment": environment,
        "description": f"Argo CD Healthy; {evidence['pod_count']} Pods use exact image digest",
        "auto_inactive": False})
    return {"deployment_id": deployment_id, "environment": environment,
            "image_digest": evidence["image_digest"], "gitops_revision": revision,
            "evidence_sha256": evidence["evidence_sha256"],
            "status": status.get("state", "success") if isinstance(status, dict) else "success"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="runtime-evidence.json")
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()
    token, repository = os.getenv("GH_TOKEN", "").strip(), os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise SystemExit("GH_TOKEN and GITHUB_REPOSITORY are required")
    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ValueError("runtime evidence must be a JSON object")
        result = register_runtime(api_url=args.api_url, repository=repository, token=token, evidence=evidence)
    except (OSError, json.JSONDecodeError, ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    output = os.getenv("GITHUB_OUTPUT", "")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
