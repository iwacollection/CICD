#!/usr/bin/env python3
"""Write small, stable metadata for business CI capability runs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = {
        "schema_version": 1,
        "capability": args.capability,
        "project": args.project,
        "status": args.status,
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "source_sha": os.environ.get("GITHUB_SHA", ""),
        "ref": os.environ.get("GITHUB_REF", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
