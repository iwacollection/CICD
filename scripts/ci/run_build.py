#!/usr/bin/env python3
"""Execute one trusted build command with consistent CI logging and timing.

The command can run directly on the Runner host or inside a Docker image.
Container mode bind-mounts the working directory to /workspace so build outputs
remain available to later CI packaging steps.
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
