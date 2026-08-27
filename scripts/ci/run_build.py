#!/usr/bin/env python3
"""Execute one trusted build command with consistent CI logging and timing."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    if not workdir.is_dir():
        print(f"ERROR: working directory does not exist: {workdir}", file=sys.stderr)
        return 2

    print("=" * 72)
    print(f"CI build directory : {workdir}")
    print(f"CI build command   : {args.command}")
    print("=" * 72)
    started = time.monotonic()

    env = os.environ.copy()
    env.setdefault("CI", "true")
    proc = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", args.command],
        cwd=workdir,
        env=env,
        check=False,
    )
    elapsed = time.monotonic() - started
    print(f"build_exit_code={proc.returncode} elapsed_seconds={elapsed:.2f}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
