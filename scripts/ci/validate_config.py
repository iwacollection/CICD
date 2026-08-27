#!/usr/bin/env python3
"""Validate the CI platform project catalog using only Python stdlib."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_SOCS = {"generic", "rk", "qualcomm", "mediatek"}
ALLOWED_OSES = {"linux", "android"}
ALLOWED_ARCHES = {"x86_64", "arm64", "armhf"}
ALLOWED_EXECUTION_MODES = {"host", "container"}


def load_catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load catalog {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("catalog root must be a JSON object")
    return data


def validate_catalog(data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    projects = data.get("projects")
    if not isinstance(projects, list) or not projects:
        return errors + ["projects must be a non-empty list"]

    names: set[str] = set()
    for pidx, project in enumerate(projects):
        prefix = f"projects[{pidx}]"
        if not isinstance(project, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = project.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix}.name must be a non-empty string")
        elif name in names:
            errors.append(f"duplicate project name: {name}")
        else:
            names.add(name)

        if not isinstance(project.get("path"), str) or not project.get("path"):
            errors.append(f"{prefix}.path must be a non-empty string")

        depends_on = project.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(x, str) for x in depends_on):
            errors.append(f"{prefix}.depends_on must be a list of project names")

        targets = project.get("targets")
        if not isinstance(targets, list) or not targets:
            errors.append(f"{prefix}.targets must be a non-empty list")
            continue

        seen_targets: set[tuple[str, str, str, str]] = set()
        for tidx, target in enumerate(targets):
            tprefix = f"{prefix}.targets[{tidx}]"
            if not isinstance(target, dict):
                errors.append(f"{tprefix} must be an object")
                continue
            soc = target.get("soc")
            target_os = target.get("target_os")
            arch = target.get("arch")
            toolchain = target.get("toolchain")
            if soc not in ALLOWED_SOCS:
                errors.append(f"{tprefix}.soc must be one of {sorted(ALLOWED_SOCS)}")
            if target_os not in ALLOWED_OSES:
                errors.append(f"{tprefix}.target_os must be one of {sorted(ALLOWED_OSES)}")
            if arch not in ALLOWED_ARCHES:
                errors.append(f"{tprefix}.arch must be one of {sorted(ALLOWED_ARCHES)}")
            if not isinstance(toolchain, str) or not toolchain:
                errors.append(f"{tprefix}.toolchain must be a non-empty string")
            key = (str(soc), str(target_os), str(arch), str(toolchain))
            if key in seen_targets:
                errors.append(f"{tprefix} duplicates target {key}")
            seen_targets.add(key)

            runner_labels = target.get("runner_labels")
            if not isinstance(runner_labels, list) or not runner_labels or not all(isinstance(x, str) and x for x in runner_labels):
                errors.append(f"{tprefix}.runner_labels must be a non-empty string list")

            execution_mode = target.get("execution_mode", "host")
            if execution_mode not in ALLOWED_EXECUTION_MODES:
                errors.append(f"{tprefix}.execution_mode must be one of {sorted(ALLOWED_EXECUTION_MODES)}")
            if execution_mode == "container":
                image = target.get("container_image", "")
                dockerfile = target.get("container_dockerfile", "")
                if not isinstance(image, str) or not isinstance(dockerfile, str):
                    errors.append(f"{tprefix}.container_image/container_dockerfile must be strings")
                elif not image and not dockerfile:
                    errors.append(f"{tprefix} container mode requires container_image or container_dockerfile")
                elif image and dockerfile:
                    errors.append(f"{tprefix} choose container_image or container_dockerfile, not both")

            if not isinstance(target.get("build_command"), str) or not target.get("build_command"):
                errors.append(f"{tprefix}.build_command must be a non-empty string")
            test_command = target.get("test_command", "")
            if not isinstance(test_command, str):
                errors.append(f"{tprefix}.test_command must be a string")
            artifacts = target.get("artifact_paths")
            if not isinstance(artifacts, list) or not artifacts or not all(isinstance(x, str) and x for x in artifacts):
                errors.append(f"{tprefix}.artifact_paths must be a non-empty string list")

    for project in projects:
        if isinstance(project, dict):
            for dep in project.get("depends_on", []):
                if dep not in names:
                    errors.append(f"project {project.get('name')} depends on unknown project {dep}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", nargs="?", default="ci/projects.json")
    args = parser.parse_args()
    try:
        data = load_catalog(Path(args.catalog))
        errors = validate_catalog(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {len(data['projects'])} project definitions validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
