#!/usr/bin/env python3
"""Build the GitHub Actions matrix from ci/projects.json."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from validate_config import load_catalog, validate_catalog


def build_matrix(data: dict, project_filter: str | None = None) -> dict:
    include: list[dict] = []
    for project in data["projects"]:
        if not project.get("enabled", True):
            continue
        if project_filter and project["name"] != project_filter:
            continue
        for target in project["targets"]:
            if not target.get("enabled", True):
                continue
            include.append(
                {
                    "project": project["name"],
                    "path": project["path"],
                    "soc": target["soc"],
                    "target_os": target["target_os"],
                    "arch": target["arch"],
                    "toolchain": target["toolchain"],
                    "runner_labels": json.dumps(target["runner_labels"], separators=(",", ":")),
                    "execution_mode": target.get("execution_mode", "host"),
                    "container_image": target.get("container_image", ""),
                    "container_dockerfile": target.get("container_dockerfile", ""),
                    "build_command": target["build_command"],
                    "test_command": target.get("test_command", ""),
                    "artifact_paths": json.dumps(target["artifact_paths"], separators=(",", ":")),
                    "cache_paths": "\n".join(target.get("cache_paths", [])),
                    "cache_key_files": "\n".join(target.get("cache_key_files", [])),
                }
            )
    return {"include": include}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/projects.json")
    parser.add_argument("--project")
    args = parser.parse_args()

    data = load_catalog(Path(args.catalog))
    errors = validate_catalog(data)
    if errors:
        raise SystemExit("invalid catalog:\n" + "\n".join(errors))

    matrix = build_matrix(data, args.project)
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
