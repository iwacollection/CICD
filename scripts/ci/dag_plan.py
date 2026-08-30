#!/usr/bin/env python3
"""Render dependency DAG levels into executable GitHub Actions matrices."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dependency_plan import MAX_DAG_LEVELS, build_plan
from discover_matrix import build_matrix
from hardware_catalog import validate_hardware_catalog
from toolchain_catalog import load_toolchain_catalog, validate_toolchain_catalog
from validate_config import load_catalog, validate_catalog


def _dag_cache_paths(raw: str) -> str:
    """Prefix repository-relative cache paths for the DAG node's `source/` checkout."""
    paths: list[str] = []
    for line in raw.splitlines():
        path = line.strip()
        if not path:
            continue
        if path.startswith("/") or path == ".." or path.startswith("../"):
            raise ValueError(f"unsafe DAG cache path: {path}")
        paths.append(f"source/{path.removeprefix('./')}")
    return "\n".join(paths)


def render_dag(data: dict, toolchain_data: dict, selected: set[str], lane: str) -> dict:
    plan = build_plan(data, selected)
    levels: list[dict] = []
    total_targets = 0
    for index in range(MAX_DAG_LEVELS):
        project_names = set(plan["levels"][index]) if index < plan["level_count"] else set()
        matrix = build_matrix(
            data,
            toolchain_data,
            project_names=project_names,
            lane=lane,
        ) if project_names else {"include": []}
        for target in matrix["include"]:
            target["cache_paths"] = _dag_cache_paths(target.get("cache_paths", ""))
        count = len(matrix["include"])
        total_targets += count
        levels.append(
            {
                "index": index,
                "projects": sorted(project_names),
                "matrix": matrix,
                "count": count,
            }
        )
    return {
        "schema_version": 1,
        "lane": lane,
        "level_count": plan["level_count"],
        "levels": levels,
        "nodes": plan["nodes"],
        "total_targets": total_targets,
    }


def _write_outputs(rendered: dict) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"level_count={rendered['level_count']}\n")
        handle.write(f"total_targets={rendered['total_targets']}\n")
        handle.write(
            "levels="
            + json.dumps(
                [level["projects"] for level in rendered["levels"][: rendered["level_count"]]],
                separators=(",", ":"),
            )
            + "\n"
        )
        for level in rendered["levels"]:
            encoded = json.dumps(level["matrix"], separators=(",", ":"))
            handle.write(f"level_{level['index']}_matrix={encoded}\n")
            handle.write(f"level_{level['index']}_count={level['count']}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/projects.json")
    parser.add_argument("--toolchains", default="ci/toolchains.json")
    parser.add_argument("--hardware-profiles", default="ci/hardware-profiles.json")
    parser.add_argument("--projects-json", required=True)
    parser.add_argument("--lane", choices=("fast", "full", "none"), required=True)
    args = parser.parse_args()

    data = load_catalog(Path(args.catalog))
    toolchain_data = load_toolchain_catalog(Path(args.toolchains))
    hardware_data = load_catalog(Path(args.hardware_profiles))
    errors = validate_toolchain_catalog(toolchain_data)
    errors.extend(validate_hardware_catalog(hardware_data))
    errors.extend(validate_catalog(data, toolchain_data, hardware_data))
    if errors:
        raise SystemExit("invalid CI catalogs:\n" + "\n".join(errors))

    raw = json.loads(args.projects_json)
    if not isinstance(raw, list) or not all(isinstance(name, str) and name for name in raw):
        raise SystemExit("--projects-json must be a JSON array of project names")
    selected = set(raw)
    if args.lane == "none" and selected:
        raise SystemExit("none lane cannot contain selected projects")

    try:
        rendered = render_dag(data, toolchain_data, selected, args.lane)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(rendered, ensure_ascii=False, indent=2))
    _write_outputs(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
