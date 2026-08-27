#!/usr/bin/env python3
"""Create an immutable tar.gz bundle, SHA256 file and machine-readable manifest."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--artifacts-json", required=True)
    parser.add_argument("--soc", required=True)
    parser.add_argument("--target-os", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    patterns = json.loads(args.artifacts_json)
    files: list[Path] = []
    for pattern in patterns:
        files.extend(Path(p).resolve() for p in glob.glob(str(workdir / pattern), recursive=True))
    files = sorted({p for p in files if p.is_file()})
    if not files:
        raise SystemExit(f"no build artifacts matched: {patterns}")

    git_sha = os.getenv("GITHUB_SHA", "local")
    short_sha = git_sha[:12]
    base = f"{args.project}-{args.soc}-{args.target_os}-{args.arch}-{short_sha}"
    bundle = output_dir / f"{base}.tar.gz"
    with tarfile.open(bundle, "w:gz") as tf:
        for path in files:
            tf.add(path, arcname=str(path.relative_to(workdir)))

    digest = sha256(bundle)
    checksum = output_dir / f"{bundle.name}.sha256"
    checksum.write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "project": args.project,
        "source_sha": git_sha,
        "source_repository": os.getenv("GITHUB_REPOSITORY", "local"),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "local"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": {
            "soc": args.soc,
            "target_os": args.target_os,
            "arch": args.arch,
            "toolchain": args.toolchain,
        },
        "bundle": {"file": bundle.name, "sha256": digest, "size_bytes": bundle.stat().st_size},
        "inputs": [str(p.relative_to(workdir)) for p in files],
    }
    manifest_path = output_dir / f"{base}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"bundle={bundle}\n")
            fh.write(f"manifest={manifest_path}\n")
            fh.write(f"checksum={checksum}\n")
            fh.write(f"digest={digest}\n")
            fh.write(f"artifact_name={base}\n")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
