#!/usr/bin/env python3
"""Determine the smallest safe project set for a CI change."""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict, deque
from fnmatch import fnmatch
from pathlib import Path

from validate_config import load_catalog, validate_catalog

# Changes here can alter CI semantics or build environments, so Fast Lane must
# fall back to every enabled project instead of trying to guess the impact.
FULL_REBUILD_PATHS = (
    "ci/",
    "scripts/ci/",
    ".github/workflows/",
    "docker/toolchains/",
)

# These files do not affect runtime/build output. They are normally excluded by
# workflow path filters too, but keeping the rule here makes the analyzer safe
# to use from other entry points.
IGNORED_PATHS = (
    "docs/",
    ".github/ISSUE_TEMPLATE/",
)
IGNORED_FILES = {"README.md", ".gitignore"}


def _normalise(path: str) -> str:
    normalised = path.strip().replace("\\", "/")
    if normalised.startswith("./"):
        normalised = normalised[2:]
    return normalised


def _matches(path: str, pattern: str) -> bool:
    path = _normalise(path)
    pattern = _normalise(pattern)
    if not pattern:
        return False
    if any(char in pattern for char in "*?["):
        return fnmatch(path, pattern)
    prefix = pattern.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def _is_ignored(path: str) -> bool:
    path = _normalise(path)
    return path in IGNORED_FILES or any(_matches(path, prefix) for prefix in IGNORED_PATHS)


def _project_paths(project: dict) -> list[str]:
    paths = [project["path"]]
    paths.extend(project.get("impact_paths", []))
    # Preserve order but remove duplicates.
    return list(dict.fromkeys(_normalise(path) for path in paths if path))


def _expand_dependents(data: dict, roots: set[str]) -> set[str]:
    enabled = {
        project["name"]: project
        for project in data["projects"]
        if project.get("enabled", True)
    }
    dependents: dict[str, list[str]] = defaultdict(list)
    for name, project in enabled.items():
        for dependency in project.get("depends_on", []):
            if dependency in enabled:
                dependents[dependency].append(name)

    impacted = set(roots)
    queue = deque(sorted(roots))
    while queue:
        current = queue.popleft()
        for dependent in sorted(dependents[current]):
            if dependent not in impacted:
                impacted.add(dependent)
                queue.append(dependent)
    return impacted


def analyze_impact(data: dict, changed_files: list[str], force_full: bool = False) -> dict:
    enabled_projects = [
        project for project in data["projects"] if project.get("enabled", True)
    ]
    all_names = [project["name"] for project in enabled_projects]
    changed = sorted({_normalise(path) for path in changed_files if _normalise(path)})

    if force_full or not changed:
        return {
            "lane": "full",
            "projects": all_names,
            "direct_projects": all_names,
            "changed_files": changed,
            "reason": "forced full build" if force_full else "no diff available; safe full build",
        }

    global_changes = [
        path
        for path in changed
        if any(_matches(path, prefix) for prefix in FULL_REBUILD_PATHS)
    ]
    if global_changes:
        return {
            "lane": "full",
            "projects": all_names,
            "direct_projects": all_names,
            "changed_files": changed,
            "reason": f"global CI/build input changed: {global_changes[0]}",
        }

    direct: set[str] = set()
    matched_files: set[str] = set()
    for project in enabled_projects:
        patterns = _project_paths(project)
        for path in changed:
            if any(_matches(path, pattern) for pattern in patterns):
                direct.add(project["name"])
                matched_files.add(path)

    actionable_unmatched = [
        path for path in changed if path not in matched_files and not _is_ignored(path)
    ]
    if actionable_unmatched:
        # Unknown files are deliberately fail-safe. A new root-level shared
        # library must never silently bypass CI just because catalog ownership
        # has not been configured yet.
        return {
            "lane": "full",
            "projects": all_names,
            "direct_projects": sorted(direct),
            "changed_files": changed,
            "reason": f"unowned build-impacting path: {actionable_unmatched[0]}",
        }

    if not direct:
        return {
            "lane": "none",
            "projects": [],
            "direct_projects": [],
            "changed_files": changed,
            "reason": "only ignored/non-build files changed",
        }

    impacted = _expand_dependents(data, direct)
    return {
        "lane": "fast",
        "projects": [name for name in all_names if name in impacted],
        "direct_projects": sorted(direct),
        "changed_files": changed,
        "reason": "project-scoped change with dependent expansion",
    }


def _write_outputs(result: dict) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"lane={result['lane']}\n")
        handle.write(f"projects={json.dumps(result['projects'], separators=(',', ':'))}\n")
        handle.write(f"changed_count={len(result['changed_files'])}\n")
        handle.write(f"impacted_count={len(result['projects'])}\n")
        handle.write(f"reason={result['reason']}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="ci/projects.json")
    parser.add_argument("--changed-files-file")
    parser.add_argument("--force-full", action="store_true")
    args = parser.parse_args()

    data = load_catalog(Path(args.catalog))
    errors = validate_catalog(data)
    if errors:
        raise SystemExit("invalid catalog:\n" + "\n".join(errors))

    changed_files: list[str] = []
    if args.changed_files_file:
        changed_files = Path(args.changed_files_file).read_text(encoding="utf-8").splitlines()

    result = analyze_impact(data, changed_files, force_full=args.force_full)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    _write_outputs(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
