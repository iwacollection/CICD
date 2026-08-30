#!/usr/bin/env python3
"""Minimal fail-closed HIL broker for one RK lab host.

This is intentionally a single-host reference implementation. It persists leases to a
local JSON state file, prunes expired leases, authenticates every lease operation with a
Bearer token, and never executes commands from the inventory.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

LOCK = threading.Lock()


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def validate_inventory(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("inventory schema_version must be 1")
    pools = data.get("pools")
    if not isinstance(pools, dict) or not pools:
        raise ValueError("inventory pools must be a non-empty object")
    for pool, devices in pools.items():
        if not isinstance(pool, str) or not pool:
            raise ValueError("pool names must be non-empty strings")
        if not isinstance(devices, list) or not devices:
            raise ValueError(f"pool {pool} must contain at least one device")
        seen: set[str] = set()
        for device in devices:
            if not isinstance(device, dict):
                raise ValueError(f"pool {pool} device entries must be objects")
            device_id = device.get("id")
            env = device.get("env", {})
            if not isinstance(device_id, str) or not device_id or device_id in seen:
                raise ValueError(f"pool {pool} has invalid/duplicate device id")
            seen.add(device_id)
            if not isinstance(env, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in env.items()):
                raise ValueError(f"device {device_id} env must be string-to-string")
            path = device.get("device_path", "")
            if path and (not isinstance(path, str) or not path.startswith("/dev/")):
                raise ValueError(f"device {device_id} device_path must be under /dev when set")


def prune(state: dict, now: int) -> None:
    leases = state.setdefault("leases", {})
    expired = [lease_id for lease_id, lease in leases.items() if int(lease.get("expires_at", 0)) <= now]
    for lease_id in expired:
        leases.pop(lease_id, None)


def device_ready(device: dict) -> bool:
    path = device.get("device_path", "")
    return not path or Path(path).exists()


class Broker:
    def __init__(self, inventory: Path, state: Path, token: str) -> None:
        self.inventory_path = inventory
        self.state_path = state
        self.token = token
        inventory_data = load_json(inventory, {})
        validate_inventory(inventory_data)

    def health(self) -> dict:
        inventory = load_json(self.inventory_path, {})
        validate_inventory(inventory)
        ready = 0
        total = 0
        for devices in inventory["pools"].values():
            for device in devices:
                total += 1
                ready += int(device_ready(device))
        return {"status": "ok", "devices_total": total, "devices_ready": ready}

    def acquire(self, payload: dict) -> dict:
        if payload.get("kind") != "hil":
            raise ValueError("this broker serves only kind=hil")
        pool = payload.get("pool")
        holder = payload.get("holder")
        ttl = payload.get("ttl_seconds")
        if not isinstance(pool, str) or not pool:
            raise ValueError("pool is required")
        if not isinstance(holder, str) or not holder:
            raise ValueError("holder is required")
        if not isinstance(ttl, int) or not 60 <= ttl <= 43200:
            raise ValueError("ttl_seconds must be between 60 and 43200")

        now = int(time.time())
        inventory = load_json(self.inventory_path, {})
        validate_inventory(inventory)
        devices = inventory["pools"].get(pool)
        if not isinstance(devices, list):
            raise LookupError(f"unknown HIL pool: {pool}")

        with LOCK:
            state = load_json(self.state_path, {"schema_version": 1, "leases": {}})
            if state.get("schema_version") != 1:
                raise ValueError("lease state schema_version must be 1")
            prune(state, now)
            leased_ids = {lease.get("device_id") for lease in state["leases"].values()}
            selected = next((d for d in devices if d["id"] not in leased_ids and device_ready(d)), None)
            if selected is None:
                raise RuntimeError(f"no ready RK HIL device is available in pool {pool}")

            lease_id = "hil_" + secrets.token_hex(16)
            expires_at = now + ttl
            state["leases"][lease_id] = {
                "kind": "hil",
                "pool": pool,
                "device_id": selected["id"],
                "holder": holder,
                "created_at": now,
                "expires_at": expires_at,
                "metadata": payload.get("metadata", {}),
            }
            write_json(self.state_path, state)

        env = dict(selected.get("env", {}))
        env["CI_HIL_DEVICE_ID"] = selected["id"]
        if selected.get("device_path"):
            env["CI_HIL_DEVICE_PATH"] = selected["device_path"]
        return {"lease_id": lease_id, "expires_at": expires_at, "env": env}

    def release(self, lease_id: str) -> dict:
        with LOCK:
            state = load_json(self.state_path, {"schema_version": 1, "leases": {}})
            leases = state.setdefault("leases", {})
            if lease_id not in leases:
                raise LookupError("unknown lease")
            lease = leases.pop(lease_id)
            write_json(self.state_path, state)
        return {"released": True, "device_id": lease.get("device_id", "")}


def make_handler(broker: Broker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "rk-hil-broker/1"

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def send_json(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self) -> bool:
            expected = f"Bearer {broker.token}"
            return secrets.compare_digest(self.headers.get("Authorization", ""), expected)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/healthz":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                self.send_json(HTTPStatus.OK, broker.health())
            except Exception as exc:  # fail closed at health surface
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/leases":
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not self.authorized():
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be an object")
                self.send_json(HTTPStatus.CREATED, broker.acquire(payload))
            except LookupError as exc:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except RuntimeError as exc:
                self.send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_DELETE(self) -> None:  # noqa: N802
            prefix = "/v1/leases/"
            if not self.path.startswith(prefix):
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not self.authorized():
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            lease_id = unquote(self.path[len(prefix) :])
            if not lease_id:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "lease id is required"})
                return
            try:
                self.send_json(HTTPStatus.OK, broker.release(lease_id))
            except LookupError as exc:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--state", default="/var/lib/rk-hil-broker/state.json")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    token = os.environ.get("CI_RESOURCE_BROKER_TOKEN", "").strip()
    if len(token) < 32:
        raise SystemExit("CI_RESOURCE_BROKER_TOKEN must contain at least 32 characters")
    broker = Broker(Path(args.inventory), Path(args.state), token)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(broker))
    print(f"RK HIL broker listening on http://{args.bind}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
