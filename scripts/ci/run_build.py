#!/usr/bin/env python3
"""Execute one trusted build command with consistent CI logging and timing.

The command can run directly on the Runner host or inside a Docker image.
Container mode bind-mounts the working directory to /workspace so build outputs
and verified DAG upstream artifacts remain available to build commands.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def host_command(command: str) -> list[str]:
    return ["bash", "-euo", "pipefail", "-c", command]


def _container_env_args(workdir: Path) -> list[str]:
    args: list[str] = []
    upstream_root = os.environ.get("CI_UPSTREAM_ROOT", "")
    upstream_index = os.environ.get("CI_UPSTREAM_INDEX", "")
    if upstream_root:
        root = Path(upstream_root).resolve()
        try:
            relative_root = root.relative_to(workdir)
        except ValueError as exc:
            raise RuntimeError(
                "CI_UPSTREAM_ROOT must be inside the mounted working directory for container builds"
            ) from exc
        container_root = "/workspace/" + relative_root.as_posix()
        args.extend(["-e", f"CI_UPSTREAM_ROOT={container_root}"])
    if upstream_index:
        index = Path(upstream_index).resolve()
        try:
            relative_index = index.relative_to(workdir)
        except ValueError as exc:
            raise RuntimeError(
                "CI_UPSTREAM_INDEX must be inside the mounted working directory for container builds"
            ) from exc
        container_index = "/workspace/" + relative_index.as_posix()
        args.extend(["-e", f"CI_UPSTREAM_INDEX={container_index}"])
    return args


def container_command(workdir: Path, command: str, image: str) -> list[str]:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("container execution requested but docker is not installed on this Runner")

    args = [
        docker,
        "run",
        "--rm",
        "--init",
        "-e",
        "CI=true",
        "-v",
        f"{workdir}:/workspace",
        "-w",
        "/workspace",
    ]
    args.extend(_container_env_args(workdir))

    # On Linux, keep generated files owned by the Runner user instead of root.
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

    args.extend([image, "bash", "-euo", "pipefail", "-c", command])
    return args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument(
        "--container-image",
        default="",
        help="Optional Docker image. When set, execute the command inside the image.",
    )
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    if not workdir.is_dir():
        print(f"ERROR: working directory does not exist: {workdir}", file=sys.stderr)
        return 2

    execution = "container" if args.container_image else "host"
    print("=" * 72)
    print(f"CI build directory : {workdir}")
    print(f"CI execution mode  : {execution}")
    if args.container_image:
        print(f"CI container image : {args.container_image}")
    if os.environ.get("CI_UPSTREAM_ROOT"):
        print(f"CI upstream root   : {os.environ['CI_UPSTREAM_ROOT']}")
    print(f"CI build command   : {args.command}")
    print("=" * 72)

    try:
        command = (
            container_command(workdir, args.command, args.container_image)
            if args.container_image
            else host_command(args.command)
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    started = time.monotonic()
    env = os.environ.copy()
    env.setdefault("CI", "true")
    proc = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        check=False,
    )
    elapsed = time.monotonic() - started
    print(f"build_exit_code={proc.returncode} elapsed_seconds={elapsed:.2f}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
