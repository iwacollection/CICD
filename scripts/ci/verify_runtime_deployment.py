#!/usr/bin/env python3
"""Fail-closed verification of an Argo CD sync and Kubernetes image digest."""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{7,40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

def _read_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value

def verify_runtime(app: dict[str, Any], pods: dict[str, Any], *,
                   application: str, namespace: str, selector: str,
                   expected_image: str, expected_revision: str,
                   manifest_sha256: str, target_environment: str) -> dict[str, Any]:
    if not NAME_RE.fullmatch(application):
        raise ValueError("application name is invalid")
    if not NAME_RE.fullmatch(namespace):
        raise ValueError("namespace is invalid")
    if not selector or len(selector) > 256 or any(c in selector for c in "\r\n"):
        raise ValueError("pod selector is invalid")
    if not DIGEST_RE.fullmatch(manifest_sha256):
        raise ValueError("manifest SHA256 is invalid")
    if not REVISION_RE.fullmatch(expected_revision):
        raise ValueError("expected GitOps revision must be a 7-40 character commit SHA")
    if target_environment not in {"dev", "staging", "production"}:
        raise ValueError("target environment is invalid")
    if "@sha256:" not in expected_image:
        raise ValueError("expected image must use an immutable @sha256 digest")
    image_name, image_digest = expected_image.rsplit("@", 1)
    if not image_name or not DIGEST_RE.fullmatch(image_digest):
        raise ValueError("expected image reference is invalid")

    metadata, status = app.get("metadata") or {}, app.get("status") or {}
    sync, health = status.get("sync") or {}, status.get("health") or {}
    if metadata.get("name") != application:
        raise ValueError("Argo CD returned a different application")
    if sync.get("status") != "Synced":
        raise ValueError(f"Argo CD application is not Synced: {sync.get('status')}")
    if health.get("status") != "Healthy":
        raise ValueError(f"Argo CD application is not Healthy: {health.get('status')}")
    actual_revision = str(sync.get("revision") or "")
    if actual_revision != expected_revision:
        raise ValueError(f"GitOps revision mismatch: expected {expected_revision}, got {actual_revision or '<empty>'}")

    items = pods.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Kubernetes selector returned no Pods")
    verified_pods = []
    for pod in items:
        pod_meta, pod_status, pod_spec = pod.get("metadata") or {}, pod.get("status") or {}, pod.get("spec") or {}
        pod_name = pod_meta.get("name", "")
        if pod_meta.get("namespace", namespace) != namespace:
            raise ValueError(f"Pod {pod_name} is outside namespace {namespace}")
        if pod_meta.get("deletionTimestamp"):
            raise ValueError(f"Pod {pod_name} is terminating")
        if pod_status.get("phase") != "Running":
            raise ValueError(f"Pod {pod_name} is not Running")
        if not any(c.get("type") == "Ready" and c.get("status") == "True"
                   for c in (pod_status.get("conditions") or [])):
            raise ValueError(f"Pod {pod_name} is not Ready")
        expected_containers = [c for c in (pod_spec.get("containers") or [])
                               if c.get("image", "").split("@", 1)[0] == image_name]
        if not expected_containers:
            raise ValueError(f"Pod {pod_name} does not declare expected image {image_name}")
        status_by_name = {s.get("name"): s for s in (pod_status.get("containerStatuses") or [])}
        pod_images = []
        for container in expected_containers:
            cs = status_by_name.get(container.get("name"))
            if not cs or not cs.get("ready"):
                raise ValueError(f"Pod {pod_name} container {container.get('name')} is not Ready")
            image_id = str(cs.get("imageID") or "")
            if image_digest not in image_id:
                raise ValueError(f"Pod {pod_name} container {container.get('name')} runtime imageID does not match {image_digest}: {image_id or '<empty>'}")
            pod_images.append({"container": container.get("name"), "image": container.get("image"), "image_id": image_id})
        verified_pods.append({"name": pod_name, "uid": pod_meta.get("uid", ""),
                              "node": pod_spec.get("nodeName", ""), "images": pod_images})

    evidence = {
        "schema_version": 1, "kind": "argocd-kubernetes-runtime-verification",
        "environment": target_environment, "application": application, "namespace": namespace,
        "pod_selector": selector, "expected_image": expected_image, "image_digest": image_digest,
        "gitops_revision": actual_revision, "manifest_sha256": manifest_sha256,
        "argocd_sync": sync.get("status"), "argocd_health": health.get("status"),
        "pod_count": len(verified_pods), "verified_pods": sorted(verified_pods, key=lambda p: p["name"]),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    return evidence

def main() -> int:
    parser = argparse.ArgumentParser()
    for arg in ("app-json", "pods-json", "application", "namespace", "selector",
                "expected-image", "expected-revision", "manifest-sha256", "environment"):
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--output", default="runtime-evidence.json")
    args = parser.parse_args()
    try:
        evidence = verify_runtime(
            _read_json(args.app_json), _read_json(args.pods_json),
            application=args.application, namespace=args.namespace, selector=args.selector,
            expected_image=args.expected_image, expected_revision=args.expected_revision,
            manifest_sha256=args.manifest_sha256, target_environment=args.environment)
        Path(args.output).write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: evidence[k] for k in
        ("application", "environment", "gitops_revision", "image_digest", "pod_count", "evidence_sha256")}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
