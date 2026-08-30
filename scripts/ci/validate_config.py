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
PROJECT_FORBIDDEN_TOOLCHAIN_FIELDS = {"execution_mode", "container_image", "container_dockerfile"}


def load_catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load catalog {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("catalog root must be a JSON object")
    return data


def _toolchain_index(toolchain_data: dict | None) -> dict[str, dict]:
    if not toolchain_data:
        return {}
    items = toolchain_data.get("toolchains", [])
    if not isinstance(items, list):
        return {}
    return {
        item["id"]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def validate_catalog(data: dict, toolchain_data: dict | None = None) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    projects = data.get("projects")
    if not isinstance(projects, list) or not projects:
        return errors + ["projects must be a non-empty list"]

    toolchains = _toolchain_index(toolchain_data)
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

        impact_paths = project.get("impact_paths", [])
        if not _string_list(impact_paths):
            errors.append(f"{prefix}.impact_paths must be a string list")

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

            forbidden = sorted(field for field in PROJECT_FORBIDDEN_TOOLCHAIN_FIELDS if field in target)
            if forbidden:
                errors.append(
                    f"{tprefix} cannot define {', '.join(forbidden)}; toolchain execution and image ownership belong to ci/toolchains.json"
                )

            runner_labels = target.get("runner_labels")
            if not _string_list(runner_labels) or not runner_labels:
                errors.append(f"{tprefix}.runner_labels must be a non-empty string list")
                runner_labels = []

            pr_validation_command = target.get("pr_validation_command", "")
            if not isinstance(pr_validation_command, str):
                errors.append(f"{tprefix}.pr_validation_command must be a string")
            elif "self-hosted" in runner_labels and not pr_validation_command.strip():
                errors.append(
                    f"{tprefix}.pr_validation_command is required for self-hosted targets so PR validation fails closed"
                )

            if toolchains and isinstance(toolchain, str):
                definition = toolchains.get(toolchain)
                if not definition:
                    errors.append(f"{tprefix}.toolchain references unknown toolchain {toolchain}")
                elif project.get("enabled", True) and target.get("enabled", True):
                    if definition.get("status") != "active":
                        errors.append(
                            f"{tprefix}.toolchain {toolchain} must be active before an enabled target can consume it"
                        )

            if not isinstance(target.get("build_command"), str) or not target.get("build_command"):
                errors.append(f"{tprefix}.build_command must be a non-empty string")
            test_command = target.get("test_command", "")
            if not isinstance(test_command, str):
                errors.append(f"{tprefix}.test_command must be a string")
            fast_test_command = target.get("fast_test_command", test_command)
            if not isinstance(fast_test_command, str):
                errors.append(f"{tprefix}.fast_test_command must be a string")
            artifacts = target.get("artifact_paths")
            if not _string_list(artifacts) or not artifacts:
                errors.append(f"{tprefix}.artifact_paths must be a non-empty string list")

            dependency_lock_files = target.get("dependency_lock_files", [])
            if not _string_list(dependency_lock_files):
                errors.append(f"{tprefix}.dependency_lock_files must be a string list")

            cache_paths = target.get("cache_paths", [])
            if not _string_list(cache_paths):
                errors.append(f"{tprefix}.cache_paths must be a string list")
                cache_paths = []
            cache_key_files = target.get("cache_key_files", [])
            if not _string_list(cache_key_files):
                errors.append(f"{tprefix}.cache_key_files must be a string list")
                cache_key_files = []
            if cache_paths and not cache_key_files:
                errors.append(f"{tprefix}.cache_key_files is required when cache_paths is configured")

    for project in projects:
        if isinstance(project, dict):
            for dep in project.get("depends_on", []):
                if dep not in names:
                    errors.append(f"project {project.get('name')} depends on unknown project {dep}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", nargs="?", default="ci/projects.json")
    parser.add_argument("--toolchains", default="ci/toolchains.json")
    args = parser.parse_args()
    try:
        data = load_catalog(Path(args.catalog))
        toolchain_data = load_catalog(Path(args.toolchains))
        errors = validate_catalog(data, toolchain_data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {len(data['projects'])} project definitions validated against central toolchains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
