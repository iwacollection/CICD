#!/usr/bin/env python3
"""Collect runner and compiler/tool versions from the actual build execution environment."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

# Argument vectors, not shell fragments: all probes run in one shell/container
# invocation to avoid starting a separate Docker container for every tool.
PROBES: dict[str, tuple[str, ...]] = {
    "cc": ("cc", "--version"),
    "cxx": ("c++", "--version"),
    "gcc": ("gcc", "--version"),
    "g++": ("g++", "--version"),
    "clang": ("clang", "--version"),
    "clang++": ("clang++", "--version"),
    "cmake": ("cmake", "--version"),
    "ninja": ("ninja", "--version"),
    "make": ("make", "--version"),
    "java": ("java", "-version"),
    "javac": ("javac", "-version"),
    "maven": ("mvn", "--version"),
    "gradle": ("gradle", "--version"),
}


def _probe_script() -> str:
    lines = [
        "set -uo pipefail",
        "probe() {",
        '  local name="$1"; shift',
        '  local binary="$1"',
        '  if ! command -v "$binary" >/dev/null 2>&1; then return 0; fi',
        "  local output",
        '  if output="$("$@" 2>&1)"; then',
        '    local first_line',
        '    first_line="$(printf "%s\\n" "$output" | awk \'NF { print; exit }\')"',
        '    if [[ -n "$first_line" ]]; then printf "%s\\t%s\\n" "$name" "$first_line"; fi',
        "  fi",
        "}",
    ]
    for name, argv in PROBES.items():
        rendered = " ".join(shlex.quote(value) for value in (name, *argv))
        lines.append(f"probe {rendered}")
    return "\n".join(lines) + "\n"


def _run_probes(workdir: Path, container_image: str) -> dict[str, str]:
    script = _probe_script()
    if container_image:
        docker = shutil.which("docker")
        if not docker:
            return {}
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
            script,
        ]
    else:
        args = ["bash", "-euo", "pipefail", "-c", script]

    proc = subprocess.run(
        args,
        cwd=workdir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        return {}

    tools: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        name, separator, version = line.partition("\t")
        if separator and name in PROBES and version.strip():
            tools[name] = version.strip()
    return tools


def collect_metadata(workdir: Path, container_image: str = "") -> dict:
    tools = _run_probes(workdir, container_image)
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
        "probe_invocations": 1,
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
