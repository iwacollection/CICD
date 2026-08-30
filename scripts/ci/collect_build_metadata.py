#!/usr/bin/env python3
"""Collect runner and compiler/tool versions from the actual build execution environment."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

PROBES = {
    "cc": "cc --version",
    "cxx": "c++ --version",
    "gcc": "gcc --version",
    "g++": "g++ --version",
    "clang": "clang --version",
    "clang++": "clang++ --version",
    "cmake": "cmake --version",
    "ninja": "ninja --version",
    "make": "make --version",
    "java": "java -version 2>&1",
    "javac": "javac -version 2>&1",
    "gradle": "gradle --version",
}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        value = line.strip()
        if value:
            return value
    return ""


def _run_probe(command: str, *, workdir: Path, container_image: str) -> str:
    probe = f"command -v {command.split()[0]} >/dev/null 2>&1 || exit 127; {command}"
    if container_image:
        docker = shutil.which("docker")
        if not docker:
            return ""
        args = [
            docker,
            "run",
            "--rm",
            "--init",
            "-v",
            f"{workdir}:/workspace:ro",
            "-w",
            "/workspace",
            container_image,
            "bash",
            "-euo",
            "pipefail",
            "-c",
            probe,
        ]
    else:
        args = ["bash", "-euo", "pipefail", "-c", probe]
    proc = subprocess.run(
        args,
        cwd=workdir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return _first_line(proc.stdout)


def collect_metadata(workdir: Path, container_image: str = "") -> dict:
    tools = {
        name: version
        for name, command in PROBES.items()
        if (version := _run_probe(command, workdir=workdir, container_image=container_image))
    }
    return {
        "runner": {
            "name": os.getenv("RUNNER_NAME", "local"),
            "os": os.getenv("RUNNER_OS", os.name),
            "arch": os.getenv("RUNNER_ARCH", "unknown"),
            "environment": os.getenv("RUNNER_ENVIRONMENT", "unknown"),
            "image_os": os.getenv("ImageOS", ""),
            "image_version": os.getenv("ImageVersion", ""),
        },
        "tools": tools,
        "execution_mode": "container" if container_image else "host",
        "container_image": container_image,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--container-image", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    if not workdir.is_dir():
        raise SystemExit(f"working directory does not exist: {workdir}")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = collect_metadata(workdir, args.container_image)
    output.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
