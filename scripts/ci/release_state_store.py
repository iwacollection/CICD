#!/usr/bin/env python3
"""Persist progressive-release state as an append-only GitHub Deployment log.

The state JSON is only a transport format. The authoritative head is the newest
successful checkpoint in the dedicated `cicd-release-state` deployment environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Any

from artifact_archive import _request
from deployment_pointer import _list_all_pages

STORE_ENVIRONMENT = "cicd-release-state"
STORE_KIND = "progressive-release-state"
TERMINAL = {"completed", "aborted"}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _clean_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "_store"}


def _state_digest(state: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(_clean_state(state))).hexdigest()


def _payload(deployment: dict[str, Any]) -> dict[str, Any]:
    value = deployment.get("payload")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or value.get("kind") != STORE_KIND:
        raise ValueError("deployment is not a progressive release state checkpoint")
    return value


def _statuses(api_url: str, repository: str, token: str, deployment_id: str) -> list[dict[str, Any]]:
    url = f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses"
    return _list_all_pages(url, token)


def _latest(
    *, api_url: str, repository: str, token: str, environment: str, service: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    url = f"{api_url.rstrip('/')}/repos/{repository}/deployments?environment={STORE_ENVIRONMENT}"
    deployments = _list_all_pages(url, token)
    matches: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for deployment in deployments:
        try:
            payload = _payload(deployment)
        except (ValueError, json.JSONDecodeError):
            continue
        if payload.get("environment") == environment and payload.get("service") == service:
            deployment_id = str(deployment.get("id", ""))
            if deployment_id.isdigit():
                matches.append((int(deployment_id), deployment, payload))
    if not matches:
        return None
    _, deployment, payload = max(matches, key=lambda item: item[0])
    deployment_id = str(deployment["id"])
    statuses = _statuses(api_url, repository, token, deployment_id)
    if not any(status.get("state") == "success" for status in statuses):
        raise ValueError(f"latest release checkpoint {deployment_id} has no successful status")
    return deployment, payload


def _verify_checkpoint(
    *, deployment: dict[str, Any], payload: dict[str, Any], environment: str,
    service: str, candidate_digest: str | None = None
) -> dict[str, Any]:
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("release checkpoint state is not an object")
    if payload.get("state_sha256") != _state_digest(state):
        raise ValueError("release checkpoint state digest mismatch")
    if payload.get("environment") != environment or payload.get("service") != service:
        raise ValueError("release checkpoint scope mismatch")
    if state.get("environment") != environment or state.get("service") != service:
        raise ValueError("release state does not match its checkpoint scope")
    if candidate_digest and state.get("candidate_digest") != candidate_digest:
        raise ValueError("release state candidate does not match the current environment pointer")
    meta = {
        "deployment_id": str(deployment["id"]),
        "revision": int(payload["revision"]),
        "state_sha256": payload["state_sha256"],
        "parent_deployment_id": str(payload.get("parent_deployment_id", "")),
        "environment": environment,
        "service": service,
        "candidate_digest": state.get("candidate_digest", ""),
    }
    return {**state, "_store": meta}


def load_state(
    *, api_url: str, repository: str, token: str, environment: str,
    service: str, candidate_digest: str
) -> dict[str, Any]:
    latest = _latest(
        api_url=api_url, repository=repository, token=token,
        environment=environment, service=service,
    )
    if latest is None:
        raise ValueError("no persisted release state exists; run action=plan first")
    deployment, payload = latest
    state = _verify_checkpoint(
        deployment=deployment, payload=payload, environment=environment,
        service=service, candidate_digest=candidate_digest,
    )
    if state.get("status") in TERMINAL:
        raise ValueError(f"release is already {state['status']}; start a new plan")
    return state


def publish_state(
    *, api_url: str, repository: str, token: str, environment: str,
    service: str, action: str, state: dict[str, Any]
) -> dict[str, Any]:
    if action not in {"plan", "advance", "abort"}:
        raise ValueError("only plan, advance, and abort can be persisted")
    clean = _clean_state(state)
    if clean.get("environment") != environment or clean.get("service") != service:
        raise ValueError("release state does not match requested environment/service")
    if not clean.get("candidate_digest"):
        raise ValueError("release state candidate_digest is required")

    latest = _latest(
        api_url=api_url, repository=repository, token=token,
        environment=environment, service=service,
    )
    parent_id = ""
    revision = 1
    if latest is not None:
        previous_deployment, previous_payload = latest
        previous_id = str(previous_deployment["id"])
        previous_revision = int(previous_payload.get("revision", 0))
        if action == "plan":
            previous_state = previous_payload.get("state", {})
            if previous_state.get("status") not in TERMINAL:
                raise ValueError("an active release already exists; advance or abort it before planning again")
            parent_id = previous_id
            revision = previous_revision + 1
        else:
            supplied_meta = state.get("_store")
            if not isinstance(supplied_meta, dict):
                raise ValueError("advance/abort requires state loaded from the latest server checkpoint")
            if str(supplied_meta.get("deployment_id", "")) != previous_id:
                raise ValueError("stale release state: checkpoint is no longer the latest revision")
            if supplied_meta.get("state_sha256") != _state_digest(previous_payload.get("state", {})):
                raise ValueError("stale or modified release state")
            if supplied_meta.get("revision") != previous_revision:
                raise ValueError("release state revision mismatch")
            parent_id = previous_id
            revision = previous_revision + 1
    elif action != "plan":
        raise ValueError("advance/abort cannot run without a persisted plan")

    digest = _state_digest(clean)
    payload = {
        "kind": STORE_KIND,
        "environment": environment,
        "service": service,
        "revision": revision,
        "parent_deployment_id": parent_id,
        "state_sha256": digest,
        "state": clean,
    }
    create_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments"
    deployment = _request(
        create_url, token, method="POST",
        payload={
            "ref": os.getenv("GITHUB_SHA", "main"),
            "task": "progressive-release-state",
            "auto_merge": False,
            "required_contexts": [],
            "environment": STORE_ENVIRONMENT,
            "description": f"Release state {environment}/{service} revision {revision}",
            "transient_environment": True,
            "production_environment": False,
            "payload": payload,
        },
    )
    if not isinstance(deployment, dict) or not str(deployment.get("id", "")).isdigit():
        raise ValueError("GitHub did not return a valid release checkpoint deployment")
    deployment_id = str(deployment["id"])
    status_url = f"{api_url.rstrip('/')}/repos/{repository}/deployments/{deployment_id}/statuses"
    _request(
        status_url, token, method="POST",
        payload={
            "state": "success",
            "environment": STORE_ENVIRONMENT,
            "description": f"Persisted release state revision {revision}",
            "auto_inactive": False,
        },
    )
    return {
        **clean,
        "_store": {
            "deployment_id": deployment_id,
            "revision": revision,
            "state_sha256": digest,
            "parent_deployment_id": parent_id,
            "environment": environment,
            "service": service,
            "candidate_digest": clean["candidate_digest"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--service", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load")
    load.add_argument("--candidate-digest", required=True)
    load.add_argument("--out", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--action", required=True, choices=("plan", "advance", "abort"))
    publish.add_argument("--state", required=True)
    args = parser.parse_args()
    token = os.getenv("GH_TOKEN", "").strip()
    if not token:
        raise SystemExit("GH_TOKEN is required")
    try:
        if args.command == "load":
            state = load_state(
                api_url=args.api_url, repository=args.repository, token=token,
                environment=args.environment, service=args.service,
                candidate_digest=args.candidate_digest,
            )
            Path(args.out).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            raw = json.loads(Path(args.state).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("release state must be a JSON object")
            state = publish_state(
                api_url=args.api_url, repository=args.repository, token=token,
                environment=args.environment, service=args.service,
                action=args.action, state=raw,
            )
            Path(args.state).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state.get("_store", {}), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
