#!/usr/bin/env python3
"""Create a deterministic cache-key fingerprint from catalog-declared files."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path


def fingerprint_files(working_directory: Path, patterns: list[str]) -> str:
    root = working_directory.resolve()
    files: set[Path] = set()

    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("cache key patterns must be non-empty strings")
        candidate = Path(pattern)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"cache key pattern must stay inside the project: {pattern}")

        matches = [
            Path(path).resolve()
            for path in glob.glob(str(root / pattern), recursive=True)
            if Path(path).is_file()
        ]
        if not matches:
            raise ValueError(f"cache key pattern matched no files: {pattern}")
        for path in matches:
            path.relative_to(root)
            files.add(path)

    if not files:
        raise ValueError("at least one cache key file is required")

    digest = hashlib.sha256()
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--patterns-json", required=True)
    args = parser.parse_args()

    patterns = json.loads(args.patterns_json)
    if not isinstance(patterns, list):
        raise SystemExit("--patterns-json must be a JSON array")

    try:
        fingerprint = fingerprint_files(Path(args.working_directory), patterns)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid cache fingerprint input: {exc}") from exc

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"fingerprint={fingerprint}\n")
    print(fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
