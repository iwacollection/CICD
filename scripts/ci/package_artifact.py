#!/usr/bin/env python3
"""Create Artifact Contract v2: reproducible bundle, checksums and provenance-rich manifest."""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = slug.strip("-._")
    return slug or "unknown"


def _load_json_list(raw: str, field: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SystemExit(f"{field} must be a JSON array of non-empty strings")
    return value


def _expand_files(workdir: Path, patterns: list[str], *, field: str, required: bool) -> list[Path]:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(Path(p).resolve() for p in glob.glob(str(workdir / pattern), recursive=True))
    files = sorted({path for path in matches if path.is_file()})
    if required and patterns and not files:
        raise SystemExit(f"{field} matched no files: {patterns}")
    return files


def _identity_suffix(identity: str) -> str:
    if identity.startswith("sha256:") and len(identity) >= 19:
        return identity.split(":", 1)[1][:12]
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _resolve_toolchain_identity(toolchain: str, explicit: str, container_image: str) -> str:
    if explicit:
        return explicit
    if "@sha256:" in container_image:
        return container_image.rsplit("@", 1)[1]
    return f"host:{toolchain}"


def _file_record(path: Path, workdir: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.relative_to(workdir)),
        "sha256": sha256(path),
        "size_bytes": stat.st_size,
        "mode": format(stat.st_mode & 0o777, "04o"),
    }


def _load_build_metadata(path: str) -> dict:
    if not path:
        return {
            "runner": {
                "name": os.getenv("RUNNER_NAME", "local"),
                "os": os.getenv("RUNNER_OS", os.name),
                "arch": os.getenv("RUNNER_ARCH", "unknown"),
                "environment": os.getenv("RUNNER_ENVIRONMENT", "unknown"),
                "image_os": os.getenv("ImageOS", ""),
                "image_version": os.getenv("ImageVersion", ""),
            },
            "tools": {},
        }
    metadata_path = Path(path).resolve()
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load build metadata {metadata_path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SystemExit("build metadata root must be a JSON object")
    if not isinstance(metadata.get("runner"), dict) or not isinstance(metadata.get("tools"), dict):
        raise SystemExit("build metadata must contain runner and tools objects")
    return metadata


def _write_reproducible_tar_gz(bundle: Path, files: list[Path], workdir: Path, source_date_epoch: int) -> None:
    with bundle.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=source_date_epoch) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
                for path in files:
                    arcname = str(path.relative_to(workdir))
                    info = tf.gettarinfo(str(path), arcname=arcname)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = source_date_epoch
                    info.pax_headers = {}
                    with path.open("rb") as fh:
                        tf.addfile(info, fh)


def build_manifest(
    *,
    args: argparse.Namespace,
    workdir: Path,
    files: list[Path],
    dependency_locks: list[Path],
    bundle: Path,
    bundle_digest: str,
    base: str,
    toolchain_identity: str,
    build_metadata: dict,
    source_date_epoch: int,
) -> dict:
    git_sha = os.getenv("GITHUB_SHA", "local")
    repository = os.getenv("GITHUB_REPOSITORY", "local")
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "local")
    runner = dict(build_metadata.get("runner", {}))
    runner["labels"] = _load_json_list(args.runner_labels_json, "runner_labels_json")
    return {
        "schema_version": 2,
        "artifact_name": base,
        "project": args.project,
        "source_sha": git_sha,
        "source_repository": repository,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repository": repository,
            "commit_sha": git_sha,
            "workflow_run_id": run_id,
            "workflow_run_attempt": run_attempt,
            "workflow_ref": os.getenv("GITHUB_WORKFLOW_REF", ""),
        },
        "target": {
            "soc": args.soc,
            "target_os": args.target_os,
            "arch": args.arch,
            "toolchain": args.toolchain,
        },
        "toolchain": {
            "id": args.toolchain,
            "identity": toolchain_identity,
            "execution_mode": args.execution_mode,
            "container_image": args.container_image,
        },
        "runner": runner,
        "compiler_versions": build_metadata.get("tools", {}),
        "dependencies": {
            "locks": [_file_record(path, workdir) for path in dependency_locks],
        },
        "bundle": {
            "file": bundle.name,
            "sha256": bundle_digest,
            "size_bytes": bundle.stat().st_size,
            "format": "tar.gz",
            "reproducible": True,
            "source_date_epoch": source_date_epoch,
        },
        "files": [_file_record(path, workdir) for path in files],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--artifacts-json", required=True)
    parser.add_argument("--dependency-locks-json", default="[]")
    parser.add_argument("--soc", required=True)
    parser.add_argument("--target-os", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--toolchain-identity", default="")
    parser.add_argument("--execution-mode", default="host", choices=("host", "container"))
    parser.add_argument("--container-image", default="")
    parser.add_argument("--runner-labels-json", default="[]")
    parser.add_argument("--build-metadata-file", default="")
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    if not workdir.is_dir():
        raise SystemExit(f"working directory does not exist: {workdir}")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_patterns = _load_json_list(args.artifacts_json, "artifacts_json")
    dependency_patterns = _load_json_list(args.dependency_locks_json, "dependency_locks_json")
    files = _expand_files(workdir, artifact_patterns, field="artifacts_json", required=True)
    dependency_locks = _expand_files(
        workdir,
        dependency_patterns,
        field="dependency_locks_json",
        required=True,
    )

    git_sha = os.getenv("GITHUB_SHA", "local")
    short_sha = _safe_slug(git_sha[:12])
    toolchain_identity = _resolve_toolchain_identity(
        args.toolchain,
        args.toolchain_identity,
        args.container_image,
    )
    identity_suffix = _identity_suffix(toolchain_identity)
    toolchain_slug = _safe_slug(args.toolchain)
    base = "-".join(
        [
            _safe_slug(args.project),
            _safe_slug(args.soc),
            _safe_slug(args.target_os),
            _safe_slug(args.arch),
            toolchain_slug,
            identity_suffix,
            short_sha,
        ]
    )

    try:
        source_date_epoch = int(os.getenv("SOURCE_DATE_EPOCH", "0"))
    except ValueError as exc:
        raise SystemExit("SOURCE_DATE_EPOCH must be an integer") from exc
    if source_date_epoch < 0:
        raise SystemExit("SOURCE_DATE_EPOCH must be >= 0")

    bundle = output_dir / f"{base}.tar.gz"
    _write_reproducible_tar_gz(bundle, files, workdir, source_date_epoch)
    digest = sha256(bundle)
    checksum = output_dir / f"{bundle.name}.sha256"
    checksum.write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")

    build_metadata = _load_build_metadata(args.build_metadata_file)
    manifest = build_manifest(
        args=args,
        workdir=workdir,
        files=files,
        dependency_locks=dependency_locks,
        bundle=bundle,
        bundle_digest=digest,
        base=base,
        toolchain_identity=toolchain_identity,
        build_metadata=build_metadata,
        source_date_epoch=source_date_epoch,
    )
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
            fh.write("artifact_schema_version=2\n")
            fh.write(f"toolchain_identity={toolchain_identity}\n")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
