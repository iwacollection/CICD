#!/usr/bin/env python3
"""Execute trusted self-hosted vendor build/HIL phases behind fail-closed gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hardware_catalog import index_hardware_profiles, validate_hardware_catalog
from resource_broker import BrokerError, Lease, acquire, release
from validate_config import load_catalog


def _trusted_main_execution() -> None:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref = os.environ.get("GITHUB_REF", "")
    runner_environment = os.environ.get("RUNNER_ENVIRONMENT", "")
    if event not in {"push", "workflow_dispatch"} or ref != "refs/heads/main":
        raise RuntimeError("privileged hardware execution is allowed only on main push/manual dispatch")
    if runner_environment != "self-hosted":
        raise RuntimeError("hardware execution requires a self-hosted Runner")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _profile_preflight(profile: dict, runner_labels_json: str) -> tuple[Path, Path]:
    if profile.get("status") != "active":
        raise RuntimeError(f"hardware profile {profile.get('id')} is not active")

    try:
        declared_labels = json.loads(runner_labels_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("runner labels are not valid JSON") from exc
    if not isinstance(declared_labels, list) or not all(isinstance(item, str) for item in declared_labels):
        raise RuntimeError("runner labels must be a JSON string array")
    if set(declared_labels) != set(profile["runner_labels"]):
        raise RuntimeError("matrix runner labels do not match the centrally managed hardware profile")

    missing_tools = [tool for tool in profile.get("required_tools", []) if shutil.which(tool) is None]
    if missing_tools:
        raise RuntimeError("hardware Runner is missing required tools: " + ", ".join(missing_tools))

    sdk = profile["sdk"]
    sdk_root_raw = os.environ.get(sdk["root_env"], "").strip()
    if not sdk_root_raw:
        raise RuntimeError(f"SDK root environment variable is missing: {sdk['root_env']}")
    sdk_root = Path(sdk_root_raw).resolve()
    if not sdk_root.is_dir():
        raise RuntimeError(f"SDK root does not exist: {sdk_root}")
    identity_file = (sdk_root / sdk["identity_file"]).resolve()
    try:
        identity_file.relative_to(sdk_root)
    except ValueError as exc:
        raise RuntimeError("SDK identity file escapes the SDK root") from exc
    if not identity_file.is_file():
        raise RuntimeError(f"SDK identity file is missing: {identity_file}")
    actual_identity = _sha256_file(identity_file)
    if actual_identity != sdk["expected_sha256"]:
        raise RuntimeError(
            f"SDK identity mismatch for {profile['id']}: expected {sdk['expected_sha256']}, got {actual_identity}"
        )

    for resource_name in ("license", "hil"):
        resource = profile[resource_name]
        if not resource.get("required", False):
            continue
        for key in ("broker_url_env", "token_env"):
            env_name = resource[key]
            if not os.environ.get(env_name, "").strip():
                raise RuntimeError(f"required {resource_name} broker environment variable is missing: {env_name}")

    platform_root = Path(__file__).resolve().parents[2]
    for adapter_key in ("build_adapter", "test_adapter"):
        adapter = (platform_root / profile[adapter_key]).resolve()
        try:
            adapter.relative_to(platform_root)
        except ValueError as exc:
            raise RuntimeError(f"{adapter_key} escapes the platform checkout") from exc
        if not adapter.is_file():
            raise RuntimeError(f"hardware adapter is missing: {adapter}")
    return sdk_root, platform_root


def _holder(profile: dict, phase: str) -> str:
    parts = [
        os.environ.get("GITHUB_REPOSITORY", "unknown-repo"),
        os.environ.get("GITHUB_RUN_ID", "unknown-run"),
        os.environ.get("GITHUB_JOB", "unknown-job"),
        profile["id"],
        phase,
    ]
    return ":".join(parts)


def _metadata(profile: dict, phase: str) -> dict[str, str]:
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "job": os.environ.get("GITHUB_JOB", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "profile": profile["id"],
        "phase": phase,
    }


def _run_phase(profile: dict, phase: str, workdir: Path, platform_root: Path, extra_env: dict[str, str]) -> int:
    adapter_key = "build_adapter" if phase == "build" else "test_adapter"
    adapter = (platform_root / profile[adapter_key]).resolve()
    env = os.environ.copy()
    env.update(extra_env)
    env["CI"] = "true"
    env["CI_HARDWARE_PROFILE"] = profile["id"]
    env["CI_PLATFORM_ROOT"] = str(platform_root)
    env["CI_PROJECT_ROOT"] = str(workdir)
    command = str(adapter)
    run_build = platform_root / "scripts/ci/run_build.py"
    print(
        json.dumps(
            {
                "hardware_profile": profile["id"],
                "phase": phase,
                "adapter": profile[adapter_key],
                "sdk_identity": profile["sdk"]["expected_sha256"],
            },
            separators=(",", ":"),
        )
    )
    completed = subprocess.run(
        [sys.executable, str(run_build), "--working-directory", str(workdir), "--command", command],
        env=env,
        check=False,
    )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=".ci-platform/ci/hardware-profiles.json")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--runner-labels-json", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--phase", choices=("build", "test"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    if args.check_only == (args.phase is not None):
        print("ERROR: choose exactly one of --check-only or --phase", file=sys.stderr)
        return 2

    try:
        _trusted_main_execution()
        data = load_catalog(Path(args.catalog))
        errors = validate_hardware_catalog(data)
        if errors:
            raise RuntimeError("invalid hardware catalog: " + "; ".join(errors))
        profiles = index_hardware_profiles(data)
        profile = profiles.get(args.profile)
        if not profile:
            raise RuntimeError(f"unknown hardware profile: {args.profile}")
        _, platform_root = _profile_preflight(profile, args.runner_labels_json)
        workdir = Path(args.working_directory).resolve()
        if not workdir.is_dir():
            raise RuntimeError(f"working directory does not exist: {workdir}")

        if args.check_only:
            print(json.dumps({"status": "ready", "profile": profile["id"]}, separators=(",", ":")))
            return 0

        phase = args.phase or ""
        leases: list[tuple[dict, Lease | None]] = []
        lease_env: dict[str, str] = {}
        command_rc = 2
        release_error: Exception | None = None
        try:
            if phase == "build":
                resource = profile["license"]
                lease = acquire(resource, kind="license", holder=_holder(profile, phase), metadata=_metadata(profile, phase))
                leases.append((resource, lease))
                if lease:
                    lease_env.update(lease.env)
                    print(f"acquired license lease for pool {resource['pool']}")
            else:
                resource = profile["hil"]
                lease = acquire(resource, kind="hil", holder=_holder(profile, phase), metadata=_metadata(profile, phase))
                leases.append((resource, lease))
                if lease:
                    lease_env.update(lease.env)
                    print(f"acquired HIL lease for pool {resource['pool']}")
            command_rc = _run_phase(profile, phase, workdir, platform_root, lease_env)
        finally:
            for resource, lease in reversed(leases):
                try:
                    release(resource, lease)
                except BrokerError as exc:
                    release_error = exc
                    print(f"ERROR: failed to release resource lease: {exc}", file=sys.stderr)
        if command_rc != 0:
            return command_rc
        if release_error is not None:
            return 2
        return 0
    except (RuntimeError, BrokerError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
