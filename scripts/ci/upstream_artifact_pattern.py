#!/usr/bin/env python3
"""Build a minimatch pattern for only the declared direct DAG dependencies."""
from __future__ import annotations

import argparse
import json
import re
import sys

from package_artifact import _safe_slug


def build_pattern(dependencies: object) -> str:
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("dependencies must be a non-empty JSON array")
    if not all(isinstance(item, str) and item for item in dependencies):
        raise ValueError("dependencies must contain non-empty project names")
    if len(dependencies) != len(set(dependencies)):
        raise ValueError("dependencies must not contain duplicates")

    slugs = [_safe_slug(item) for item in dependencies]
    if len(slugs) != len(set(slugs)):
        raise ValueError("dependencies map to ambiguous Artifact Contract prefixes")

    ordered = sorted(slugs)
    if len(ordered) == 1:
        return f"dag-{ordered[0]}-*"
    return "dag-{" + ",".join(ordered) + "}-*"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dependencies-json", required=True)
    args = parser.parse_args()
    try:
        dependencies = json.loads(args.dependencies_json)
        pattern = build_pattern(dependencies)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(pattern)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
