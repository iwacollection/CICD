#!/usr/bin/env python3
"""Build the GitHub Actions matrix from centrally managed CI catalogs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from toolchain_catalog import (
    immutable_reference,
    index_toolchains,
    load_toolchain_catalog,
    validate_toolchain_catalog,
)
from validate_config import load_catalog, validate_catalog


def build_matrix(
    data: dict,
    toolchain_data: dict,
    project_filter: str | None = None,
    project_names: set[str] | None = None,
    lane: str = "full",
) -> dict:
    toolchains = index_toolchains(toolchain_data)
    include: list[dict] = []
    for project in data["projects"]:
        if not project.get("enabled", True):
            continue
        if project_filter and project["name"] != project_filter:
            continue
        if project_names is not None and project["name"] not in project_names:
            continue
        for target in project["targets"]:
            if not target.get("enabled", True):
                continue
            definition = toolchains[target["toolchain"]]
            execution_mode = definition["execution_mode"]
            container_image = immutable_reference(definition) if execution_mode == "container" else ""
            toolchain_identity = definition.get("digest") or f"host:{definition['id']}"
            test_command = target.get("test_command", "")
            if lane == "fast":
                test_command = target.get("fast_test_command", test_command)
            include.append(
                {
                    "project": project["name"],
                    "path": project["path"],
                    "soc": target["soc"],
                    "target_os": target["target_os"],
                    "arch": target["arch"],
                    "toolchain": target["toolchain"],
                    "toolchain_identity": toolchain_identity,
                    "toolchain_status": definition["status"],
                    "runner_labels": json.dumps(target["runner_labels"], separators=(",", ":")),
                    "execution_mode": execution_mode,
                    "container_image": container_image,
                    "build_command": target["build_command"],
                    "test_command": test_command,
                    "artifact_paths": json.dumps(target["artifact_paths"], separators=(",", ":")),
                    "cache_paths": "\n".join(target.get("cache_paths", [])),
                    "cache_key_files": "\n".join(target.get("cache_key_files", [])),
                    "lane": lane,
                }
            )
    return {"include": include}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/projects.json")
    parser.add_argument("--toolchains", default="ci/toolchains.json")
    parser.add_argument("--project")
    parser.add_argument("--projects-json")
    parser.add_argument("--lane", choices=("fast", "full", "none"), default="full")
    args = parser.parse_args()

    data = load_catalog(Path(args.catalog))
    toolchain_data = load_toolchain_catalog(Path(args.toolchains))
    errors = validate_toolchain_catalog(toolchain_data)
    errors.extend(validate_catalog(data, toolchain_data))
    if errors:
        raise SystemExit("invalid CI catalogs:\n" + "\n".join(errors))

    selected_projects: set[str] | None = None
    if args.projects_json is not None:
        raw = json.loads(args.projects_json)
        if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
            raise SystemExit("--projects-json must be a JSON array of project names")
        selected_projects = set(raw)

    matrix = build_matrix(data, toolchain_data, args.project, selected_projects, lane=args.lane)
    encoded = json.dumps(matrix, separators=(",", ":"))
    print(encoded)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"matrix={encoded}\n")
            fh.write(f"count={len(matrix['include'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
