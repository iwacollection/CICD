#!/usr/bin/env python3
"""Resolve exact upstream Artifact Contract v2 objects for one DAG node."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from verify_artifact import (
    sha256,
    validate_manifest_contract,
    validate_manifest_identity,
    verify_bundle_contents,
)


def _target_rank(manifest: dict, soc: str, target_os: str, arch: str) -> int | None:
    target = manifest.get("target", {})
    if target.get("target_os") != target_os or target.get("arch") != arch:
        return None
    upstream_soc = target.get("soc")
    if upstream_soc == soc:
        return 0
    if upstream_soc == "generic" and soc != "generic":
        return 1
    return None


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe tar member path: {name}")
    return path


def _extract_verified(bundle: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError(f"upstream bundle contains non-regular member: {member.name}")
            relative = _safe_member_path(member.name)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read upstream member: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def _load_candidates(directory: Path) -> list[tuple[Path, dict]]:
    candidates: list[tuple[Path, dict]] = []
    for manifest_path in sorted(directory.rglob("*.manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid upstream manifest JSON: {manifest_path}: {exc}") from exc
        candidates.append((manifest_path, manifest))
    return candidates


def _fingerprint(records: list[dict]) -> str:
    canonical = [
        {
            "project": item["project"],
            "artifact_name": item["artifact_name"],
            "bundle_sha256": item["bundle_sha256"],
            "target": item["target"],
        }
        for item in sorted(records, key=lambda record: record["project"])
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve(
    directory: Path,
    dependencies: list[str],
    *,
    soc: str,
    target_os: str,
    arch: str,
    destination: Path,
    expected_source_sha: str = "",
    expected_repository: str = "",
) -> dict:
    if len(dependencies) != len(set(dependencies)):
        raise ValueError("dependencies-json contains duplicates")
    candidates = _load_candidates(directory)
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    for dependency in dependencies:
        ranked: list[tuple[int, Path, dict]] = []
        for manifest_path, manifest in candidates:
            if manifest.get("project") != dependency:
                continue
            rank = _target_rank(manifest, soc, target_os, arch)
            if rank is not None:
                ranked.append((rank, manifest_path, manifest))
        if not ranked:
            raise ValueError(
                f"no compatible upstream artifact for dependency={dependency} target={soc}/{target_os}/{arch}"
            )
        best_rank = min(item[0] for item in ranked)
        best = [item for item in ranked if item[0] == best_rank]
        if len(best) != 1:
            names = [item[2].get("artifact_name", item[1].name) for item in best]
            raise ValueError(
                f"ambiguous upstream artifacts for {dependency}: {', '.join(sorted(map(str, names)))}"
            )
        _, manifest_path, manifest = best[0]

        errors = validate_manifest_contract(manifest)
        errors.extend(
            validate_manifest_identity(
                manifest,
                source_sha=expected_source_sha,
                repository=expected_repository,
            )
        )
        if manifest.get("schema_version") != 2:
            errors.append("DAG handoff requires Artifact Contract v2")
        if errors:
            raise ValueError(f"invalid upstream artifact {dependency}: {'; '.join(errors)}")

        bundle_name = manifest["bundle"]["file"]
        bundle_matches = list(manifest_path.parent.rglob(bundle_name))
        if len(bundle_matches) != 1:
            artifact_root = manifest_path.parent
            while artifact_root != directory and not artifact_root.name.startswith("dag-"):
                artifact_root = artifact_root.parent
            bundle_matches = list(artifact_root.rglob(bundle_name))
        if len(bundle_matches) != 1:
            raise ValueError(f"expected exactly one upstream bundle {bundle_name}, found {len(bundle_matches)}")
        bundle = bundle_matches[0]
        actual_digest = sha256(bundle)
        if actual_digest != manifest["bundle"]["sha256"]:
            raise ValueError(f"upstream bundle digest mismatch for {dependency}")
        content_errors = verify_bundle_contents(bundle, manifest)
        if content_errors:
            raise ValueError(f"invalid upstream bundle {dependency}: {'; '.join(content_errors)}")

        project_destination = destination / dependency
        if project_destination.exists():
            shutil.rmtree(project_destination)
        _extract_verified(bundle, project_destination)
        records.append(
            {
                "project": dependency,
                "artifact_name": manifest["artifact_name"],
                "bundle_sha256": actual_digest,
                "source_sha": manifest["source_sha"],
                "source_repository": manifest["source_repository"],
                "target": manifest["target"],
                "resolved_rank": best_rank,
                "path": str(project_destination.resolve()),
            }
        )

    fingerprint = _fingerprint(records)
    index = {
        "schema_version": 1,
        "consumer_target": {"soc": soc, "target_os": target_os, "arch": arch},
        "upstream_fingerprint": fingerprint,
        "dependencies": records,
    }
    index_path = destination / "upstream-index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "root": str(destination.resolve()),
        "index": str(index_path.resolve()),
        "count": len(records),
        "fingerprint": fingerprint,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--dependencies-json", required=True)
    parser.add_argument("--soc", required=True)
    parser.add_argument("--target-os", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--expected-source-sha", default=os.getenv("GITHUB_SHA", ""))
    parser.add_argument("--expected-repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()

    dependencies = json.loads(args.dependencies_json)
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and item for item in dependencies
    ):
        raise SystemExit("--dependencies-json must be a JSON string array")
    try:
        result = resolve(
            Path(args.directory).resolve(),
            dependencies,
            soc=args.soc,
            target_os=args.target_os,
            arch=args.arch,
            destination=Path(args.destination).resolve(),
            expected_source_sha=args.expected_source_sha,
            expected_repository=args.expected_repository,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"root={result['root']}\n")
            handle.write(f"index={result['index']}\n")
            handle.write(f"count={result['count']}\n")
            handle.write(f"fingerprint={result['fingerprint']}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
