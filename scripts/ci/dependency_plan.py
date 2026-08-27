#!/usr/bin/env python3
"""Topologically validate internal project dependencies and print parallel build levels."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path

from validate_config import load_catalog, validate_catalog


def build_levels(data: dict) -> list[list[str]]:
    projects = {p["name"]: p for p in data["projects"] if p.get("enabled", True)}
    indegree = {name: 0 for name in projects}
    children: dict[str, list[str]] = defaultdict(list)

    for name, project in projects.items():
        for dep in project.get("depends_on", []):
            if dep not in projects:
                continue
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
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_ready.append(child)
        ready.extend(sorted(next_ready))

    if processed != len(projects):
        cyclic = sorted(name for name, degree in indegree.items() if degree > 0)
        raise ValueError(f"dependency cycle detected among: {', '.join(cyclic)}")
    return levels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", nargs="?", default="ci/projects.json")
    args = parser.parse_args()
    data = load_catalog(Path(args.catalog))
    errors = validate_catalog(data)
    if errors:
        raise SystemExit("invalid catalog:\n" + "\n".join(errors))
    levels = build_levels(data)
    print(json.dumps({"levels": levels}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
