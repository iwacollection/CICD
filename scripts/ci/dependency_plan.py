#!/usr/bin/env python3
"""Topologically validate project dependencies and produce parallel build levels."""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict, deque
from pathlib import Path

from validate_config import load_catalog, validate_catalog

MAX_DAG_LEVELS = 8


def enabled_projects(data: dict) -> dict[str, dict]:
    return {
        project["name"]: project
        for project in data["projects"]
        if project.get("enabled", True)
    }


def dependency_closure(data: dict, selected: set[str]) -> set[str]:
    """Return selected projects plus every enabled transitive prerequisite."""
    projects = enabled_projects(data)
    unknown = sorted(selected - set(projects))
    if unknown:
        raise ValueError(f"selected projects are not enabled/known: {', '.join(unknown)}")
    closure = set(selected)
    queue = deque(sorted(selected))
    while queue:
        current = queue.popleft()
        for dependency in sorted(projects[current].get("depends_on", [])):
            if dependency not in projects:
                raise ValueError(f"enabled project {current} depends on disabled/unknown project {dependency}")
            if dependency not in closure:
                closure.add(dependency)
                queue.append(dependency)
    return closure


def build_levels(data: dict, selected_projects: set[str] | None = None) -> list[list[str]]:
    projects = enabled_projects(data)
    if selected_projects is None:
        selected = set(projects)
    else:
        selected = dependency_closure(data, set(selected_projects))

    indegree = {name: 0 for name in selected}
    children: dict[str, list[str]] = defaultdict(list)

    for name in sorted(selected):
        project = projects[name]
        for dep in project.get("depends_on", []):
            if dep not in selected:
                # dependency_closure should prevent this, but fail closed if the
                # planner is ever called with inconsistent data.
                raise ValueError(f"DAG selection omitted prerequisite {dep} required by {name}")
            indegree[name] += 1
            children[dep].append(name)

    ready = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    levels: list[list[str]] = []
    processed = 0
    while ready:
        current = list(ready)
        ready.clear()
        levels.append(current)
        processed += len(current)
        next_ready: list[str] = []
        for node in current:
            for child in sorted(children[node]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_ready.append(child)
        ready.extend(sorted(next_ready))

    if processed != len(selected):
        cyclic = sorted(name for name, degree in indegree.items() if degree > 0)
        raise ValueError(f"dependency cycle detected among: {', '.join(cyclic)}")
    if len(levels) > MAX_DAG_LEVELS:
        raise ValueError(
            f"dependency DAG requires {len(levels)} levels, platform maximum is {MAX_DAG_LEVELS}; "
            "increase the explicit workflow level budget before onboarding this graph"
        )
    return levels


def build_plan(data: dict, selected_projects: set[str] | None = None) -> dict:
    levels = build_levels(data, selected_projects)
    projects = enabled_projects(data)
    nodes: dict[str, dict] = {}
    for level_index, level in enumerate(levels):
        for project_name in level:
            nodes[project_name] = {
                "level": level_index,
                "depends_on": list(projects[project_name].get("depends_on", [])),
            }
    return {
        "schema_version": 1,
        "max_levels": MAX_DAG_LEVELS,
        "level_count": len(levels),
        "levels": levels,
        "nodes": nodes,
    }


def _write_outputs(plan: dict) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"level_count={plan['level_count']}\n")
        handle.write(f"levels={json.dumps(plan['levels'], separators=(',', ':'))}\n")
        for index in range(MAX_DAG_LEVELS):
            projects = plan["levels"][index] if index < len(plan["levels"]) else []
            handle.write(f"level_{index}_projects={json.dumps(projects, separators=(',', ':'))}\n")
            handle.write(f"level_{index}_project_count={len(projects)}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", nargs="?", default="ci/projects.json")
    parser.add_argument("--projects-json")
    args = parser.parse_args()
    data = load_catalog(Path(args.catalog))
    errors = validate_catalog(data)
    if errors:
        raise SystemExit("invalid catalog:\n" + "\n".join(errors))

    selected: set[str] | None = None
    if args.projects_json is not None:
        raw = json.loads(args.projects_json)
        if not isinstance(raw, list) or not all(isinstance(name, str) and name for name in raw):
            raise SystemExit("--projects-json must be a JSON array of project names")
        selected = set(raw)

    try:
        plan = build_plan(data, selected)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    _write_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
