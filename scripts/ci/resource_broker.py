#!/usr/bin/env python3
"""Small stdlib client for centrally coordinated license/HIL resource leases."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    lease_id: str
    env: dict[str, str]


class BrokerError(RuntimeError):
    pass


def _request(base_url: str, token: str, method: str, path: str, payload: dict | None = None) -> dict:
    if not base_url.startswith(("https://", "http://")):
        raise BrokerError("resource broker URL must use http or https")
    url = base_url.rstrip("/") + path
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "cicd-hardware-resource-client/1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BrokerError(f"resource broker request failed: {exc}") from exc
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerError("resource broker returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise BrokerError("resource broker response must be a JSON object")
    return decoded


def acquire(resource: dict, *, kind: str, holder: str, metadata: dict[str, str]) -> Lease | None:
    if not resource.get("required", False):
        return None
    url_env = resource["broker_url_env"]
    token_env = resource["token_env"]
    base_url = os.environ.get(url_env, "").strip()
    token = os.environ.get(token_env, "").strip()
    if not base_url:
        raise BrokerError(f"required resource broker URL environment variable is missing: {url_env}")
    if not token:
        raise BrokerError(f"required resource broker token environment variable is missing: {token_env}")

    payload = {
        "kind": kind,
        "pool": resource["pool"],
        "holder": holder,
        "ttl_seconds": resource["ttl_seconds"],
        "metadata": metadata,
    }
    response = _request(base_url, token, "POST", "/v1/leases", payload)
    lease_id = response.get("lease_id")
    lease_env = response.get("env", {})
    if not isinstance(lease_id, str) or not lease_id:
        raise BrokerError("resource broker response is missing lease_id")
    if not isinstance(lease_env, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str) for key, value in lease_env.items()
    ):
        raise BrokerError("resource broker env must be a string-to-string object")
    return Lease(lease_id=lease_id, env=dict(lease_env))


def release(resource: dict, lease: Lease | None) -> None:
    if lease is None:
        return
    url_env = resource["broker_url_env"]
    token_env = resource["token_env"]
    base_url = os.environ.get(url_env, "").strip()
    token = os.environ.get(token_env, "").strip()
    if not base_url or not token:
        raise BrokerError("cannot release resource lease because broker credentials disappeared")
    encoded = urllib.parse.quote(lease.lease_id, safe="")
    _request(base_url, token, "DELETE", f"/v1/leases/{encoded}")
