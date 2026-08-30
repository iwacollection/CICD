#!/usr/bin/env python3
"""Physical RK bring-up preflight for the real x64 build host and board pool."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--profile", default="rk-linux-arm64-lab")
    parser.add_argument("--report", default="rk-physical-preflight.json")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    sys.path.insert(0, str(root / "scripts" / "ci"))
    from hardware_catalog import index_hardware_profiles, validate_hardware_catalog  # type: ignore
    from resource_broker import acquire, release  # type: ignore
    from sdk_identity import _validate_payload  # type: ignore
    from validate_config import load_catalog  # type: ignore

    catalog = load_catalog(root / "ci" / "hardware-profiles.json")
    errors = validate_hardware_catalog(catalog)
    if errors:
        raise SystemExit("invalid hardware catalog: " + "; ".join(errors))
    profile_data = index_hardware_profiles(catalog).get(args.profile)
    if not profile_data:
        raise SystemExit(f"unknown profile: {args.profile}")
    if profile_data.get("soc") != "rk":
        raise SystemExit("physical preflight is RK-only")

    report: dict[str, object] = {
        "schema_version": 1,
        "profile": args.profile,
        "runner": {},
        "sdk": {},
        "hil": {},
    }

    machine = platform.machine().lower()
    expected_runner_arch = profile_data.get("runner_arch", "")
    if expected_runner_arch != "x64" or machine not in {"x86_64", "amd64"}:
        raise SystemExit(f"RK physical Runner must be x86_64; catalog={expected_runner_arch!r}, host={machine!r}")
    missing = [tool for tool in profile_data.get("required_tools", []) if shutil.which(tool) is None]
    if missing:
        raise SystemExit("missing Runner tools: " + ", ".join(missing))
    report["runner"] = {"machine": machine, "required_tools": profile_data.get("required_tools", [])}

    sdk = profile_data["sdk"]
    sdk_root_raw = os.environ.get(sdk["root_env"], "").strip()
    if not sdk_root_raw:
        raise SystemExit(f"missing {sdk['root_env']}")
    sdk_root = Path(sdk_root_raw).resolve()
    identity = sdk_root / sdk["identity_file"]
    if not sdk_root.is_dir() or not identity.is_file():
        raise SystemExit(f"SDK root/identity is missing: {sdk_root} / {identity}")
    payload = json.loads(identity.read_text(encoding="utf-8"))
    identity_errors = _validate_payload(payload)
    if identity_errors:
        raise SystemExit("invalid SDK identity: " + "; ".join(identity_errors))
    actual_identity = sha256_file(identity)
    expected_identity = sdk.get("expected_sha256", "")
    if expected_identity and expected_identity != actual_identity:
        raise SystemExit(f"SDK identity mismatch: expected {expected_identity}, got {actual_identity}")
    report["sdk"] = {
        "root": str(sdk_root),
        "sdk_id": payload["sdk_id"],
        "version": payload["version"],
        "identity": actual_identity,
        "pinned": bool(expected_identity),
    }

    hil = profile_data["hil"]
    lease = acquire(
        hil,
        kind="hil",
        holder="rk-physical-preflight",
        metadata={"profile": args.profile, "purpose": "physical-preflight"},
    )
    if lease is None:
        raise SystemExit("RK HIL lease unexpectedly disabled")
    try:
        device_id = lease.env.get("CI_HIL_DEVICE_ID", "")
        device_path = lease.env.get("CI_HIL_DEVICE_PATH", "")
        if not device_id:
            raise SystemExit("HIL broker did not return CI_HIL_DEVICE_ID")
        if device_path and not Path(device_path).exists():
            raise SystemExit(f"leased HIL device path is not present: {device_path}")
        report["hil"] = {"device_id": device_id, "device_path": device_path, "lease_verified": True}
    finally:
        release(hil, lease)

    path = Path(args.report)
    path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
