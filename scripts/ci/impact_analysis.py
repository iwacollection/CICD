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
    "docker/toolchains/",
)

# Workflow orchestration changes still need an executable smoke path, but do
# not inherently change every project's compiled output.
WORKFLOW_SMOKE_PATHS = (
    ".github/workflows/",
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
    return list(dict.fromkeys(_normalise(path) for path in paths if path))


def _enabled_index(data: dict) -> dict[str, dict]:
    return {
        project["name"]: project
        for project in data["projects"]
        if project.get("enabled", True)
    }


def _expand_dependents(data: dict, roots: set[str]) -> set[str]:
    enabled = _enabled_index(data)
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


def _expand_dependencies(data: dict, roots: set[str]) -> set[str]:
    """Include every enabled prerequisite needed to build the selected roots."""
    enabled = _enabled_index(data)
    expanded = set(roots)
    queue = deque(sorted(roots))
    while queue:
        current = queue.popleft()
        project = enabled.get(current)
        if not project:
            continue
        for dependency in sorted(project.get("depends_on", [])):
            if dependency in enabled and dependency not in expanded:
                expanded.add(dependency)
                queue.append(dependency)
    return expanded


def analyze_impact(data: dict, changed_files: list[str], force_full: bool = False) -> dict:
    catalog_projects = data["projects"]
    enabled_projects = [
        project for project in catalog_projects if project.get("enabled", True)
    ]
    all_names = [project["name"] for project in enabled_projects]
    enabled_by_name = {project["name"]: project for project in enabled_projects}
    changed = sorted({_normalise(path) for path in changed_files if _normalise(path)})
    reasons: dict[str, set[str]] = {
        project["name"]: set() for project in catalog_projects
    }

    def build_result(
        lane: str,
        selected_names: set[str],
        direct_names: set[str],
        reason: str,
        dependency_names: set[str] | None = None,
        dependent_names: set[str] | None = None,
    ) -> dict:
        selected = selected_names & set(all_names)
        project_reasons: dict[str, dict] = {}
        for project in catalog_projects:
            name = project["name"]
            if not project.get("enabled", True):
                project_reasons[name] = {
                    "included": False,
                    "reasons": ["excluded: project is disabled in ci/projects.json"],
                }
                continue
            project_reason_set = set(reasons.get(name, set()))
            if name not in selected:
                project_reason_set.add(
                    "excluded: no changed path maps to this project and it is not "
                    "in the dependency/dependent closure"
                )
            project_reasons[name] = {
                "included": name in selected,
                "reasons": sorted(project_reason_set),
            }

        excluded_names = [
            project["name"]
            for project in catalog_projects
            if project["name"] not in selected
        ]
        return {
            "lane": lane,
            "projects": [name for name in all_names if name in selected],
            "direct_projects": [name for name in all_names if name in direct_names],
            "dependency_closure": [
                name for name in all_names if name in (dependency_names or set())
            ],
            "dependent_closure": [
                name for name in all_names if name in (dependent_names or set())
            ],
            "excluded_projects": excluded_names,
            "changed_files": changed,
            "reason": reason,
            "project_reasons": project_reasons,
        }

    def full_result(reason: str, direct_names: set[str] | None = None) -> dict:
        for name in all_names:
            reasons[name].add(f"selected by Full Lane: {reason}")
        return build_result(
            "full",
            set(all_names),
            set(all_names) if direct_names is None else direct_names,
            reason,
        )

    if force_full or not changed:
        reason = "forced full build" if force_full else "no diff available; safe full build"
        return full_result(reason)

    global_changes = [
        path
        for path in changed
        if any(_matches(path, prefix) for prefix in FULL_REBUILD_PATHS)
    ]
    if global_changes:
        return full_result(f"global CI/build input changed: {global_changes[0]}")

    direct_paths: dict[str, set[str]] = defaultdict(set)
    matched_files: set[str] = set()
    for project in enabled_projects:
        for path in changed:
            if any(_matches(path, pattern) for pattern in _project_paths(project)):
                direct_paths[project["name"]].add(path)
                matched_files.add(path)

    direct = set(direct_paths)
    for name, paths in direct_paths.items():
        for path in sorted(paths):
            reasons[name].add(f"direct path match: {path}")

    workflow_changes = [
        path
        for path in changed
        if any(_matches(path, prefix) for prefix in WORKFLOW_SMOKE_PATHS)
    ]
    actionable_unmatched = [
        path
        for path in changed
        if path not in matched_files
        and not _is_ignored(path)
        and path not in workflow_changes
    ]
    if actionable_unmatched:
        return full_result(
            f"unowned build-impacting path: {actionable_unmatched[0]}",
            direct_names=direct,
        )

    smoke_roots: set[str] = set()
    if workflow_changes:
        smoke_roots = {
            project["name"]
            for project in enabled_projects
            if project.get("ci_smoke", False)
        }
        if not smoke_roots:
            return full_result(
                "workflow changed but no ci_smoke project is declared; safe full build",
                direct_names=direct,
            )
        for name in smoke_roots:
            for path in workflow_changes:
                reasons[name].add(f"declared ci_smoke project for workflow change: {path}")

    if not direct and not smoke_roots:
        reason = "only ignored/non-build files changed"
        for name in all_names:
            reasons[name].add(f"excluded: {reason}")
        return build_result("none", set(), set(), reason)

    dependents: dict[str, list[str]] = defaultdict(list)
    for name, project in enabled_by_name.items():
        for dependency in project.get("depends_on", []):
            if dependency in enabled_by_name:
                dependents[dependency].append(name)

    impacted = set(direct) | set(smoke_roots)
    dependent_closure: set[str] = set()
    queue = deque(sorted(direct))
    visited = set(direct)
    while queue:
        current = queue.popleft()
        for dependent in sorted(dependents[current]):
            reasons[dependent].add(f"downstream consumer of {current}")
            if dependent not in visited:
                visited.add(dependent)
                dependent_closure.add(dependent)
                impacted.add(dependent)
                queue.append(dependent)

    dependency_closure: set[str] = set()
    queue = deque(sorted(impacted))
    visited = set(impacted)
    while queue:
        current = queue.popleft()
        project = enabled_by_name.get(current)
        if not project:
            continue
        for dependency in sorted(project.get("depends_on", [])):
            if dependency not in enabled_by_name:
                continue
            reasons[dependency].add(f"required prerequisite of {current}")
            if dependency not in visited:
                visited.add(dependency)
                dependency_closure.add(dependency)
                impacted.add(dependency)
                queue.append(dependency)

    if workflow_changes and direct:
        reason = (
            "workflow orchestration and project changes; declared smoke projects "
            "plus project dependency/dependent closure"
        )
    elif workflow_changes:
        reason = "workflow orchestration change; declared smoke projects and dependency closure"
    else:
        reason = "project-scoped change with dependency and dependent closure"

    lane = "smoke" if workflow_changes else "fast"
    return build_result(
        lane,
        impacted,
        direct,
        reason,
        dependency_names=dependency_closure,
        dependent_names=dependent_closure,
    )
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
        handle.write(
            "dependency_closure="
            + json.dumps(result["dependency_closure"], separators=(",", ":"))
            + "\n"
        )
        handle.write(
            "dependent_closure="
            + json.dumps(result["dependent_closure"], separators=(",", ":"))
            + "\n"
        )
        handle.write(
            "project_reasons_json="
            + json.dumps(result["project_reasons"], separators=(",", ":"))
            + "\n"
        )

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
    output_json = os.environ.get("CI_IMPACT_PLAN_FILE")
    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _write_outputs(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
