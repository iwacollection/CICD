#!/usr/bin/env python3
"""Archive and retrieve Artifact Contract v2 objects from GitHub Releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from verify_artifact import (
    sha256,
    validate_manifest_contract,
    validate_manifest_identity,
    verify_bundle_contents,
)


def release_tag(artifact_name: str) -> str:
    digest = hashlib.sha256(artifact_name.encode("utf-8")).hexdigest()
    return f"artifact-v2-{digest}"


def _request(url: str, token: str, *, method: str = "GET", payload: dict | None = None, data: bytes | None = None, content_type: str = "application/vnd.github+json") -> dict | bytes:
    body = data
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        if response.headers.get_content_type() == "application/json" or raw.startswith((b"{", b"[")):
            return json.loads(raw.decode("utf-8"))
        return raw


def _get_release(api_url: str, repository: str, tag: str, token: str) -> dict | None:
    url = f"{api_url.rstrip('/')}/repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        result = _request(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    assert isinstance(result, dict)
    return result


def _load_one_manifest(directory: Path) -> tuple[Path, dict]:
    manifests = list(directory.rglob("*.manifest.json"))
    if len(manifests) != 1:
        raise ValueError(f"expected exactly one manifest, found {len(manifests)}")
    path = manifests[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _local_files(directory: Path, manifest: dict) -> tuple[Path, Path, Path]:
    bundle_name = manifest["bundle"]["file"]
    bundles = list(directory.rglob(bundle_name))
    manifests = list(directory.rglob("*.manifest.json"))
    checksums = list(directory.rglob(f"{bundle_name}.sha256"))
    if len(bundles) != 1 or len(manifests) != 1 or len(checksums) != 1:
        raise ValueError("archive requires exactly one bundle, manifest and checksum sidecar")
    return bundles[0], manifests[0], checksums[0]


def _validate_local(directory: Path, manifest: dict, *, repository: str, source_sha: str, run_id: str) -> tuple[Path, Path, Path]:
    errors = validate_manifest_contract(manifest)
    errors.extend(
        validate_manifest_identity(
            manifest,
            source_sha=source_sha,
            run_id=run_id,
            repository=repository,
        )
    )
    if manifest.get("schema_version") != 2:
        errors.append("long-term archive only accepts Artifact Contract v2")
    if errors:
        raise ValueError("; ".join(errors))
    bundle, manifest_path, checksum = _local_files(directory, manifest)
    actual = sha256(bundle)
    if actual != manifest["bundle"]["sha256"]:
        raise ValueError("bundle digest does not match manifest")
    content_errors = verify_bundle_contents(bundle, manifest)
    if content_errors:
        raise ValueError("; ".join(content_errors))
    sidecar_digest = checksum.read_text(encoding="utf-8").strip().split()[0]
    if sidecar_digest != actual:
        raise ValueError("checksum sidecar does not match bundle")
    return bundle, manifest_path, checksum


def _release_record(manifest: dict) -> dict:
    return {
        "schema_version": 1,
        "artifact_contract": 2,
        "artifact_name": manifest["artifact_name"],
        "bundle_sha256": manifest["bundle"]["sha256"],
        "source_repository": manifest["source_repository"],
        "source_sha": manifest["source_sha"],
        "workflow_run_id": str(manifest["workflow_run_id"]),
        "toolchain_identity": manifest["toolchain"]["identity"],
    }


def archive(directory: Path, *, repository: str, source_sha: str, run_id: str, api_url: str, token: str) -> dict:
    _, manifest = _load_one_manifest(directory)
    bundle, manifest_path, checksum = _validate_local(
        directory,
        manifest,
        repository=repository,
        source_sha=source_sha,
        run_id=run_id,
    )
    tag = release_tag(manifest["artifact_name"])
    expected_record = _release_record(manifest)
    existing = _get_release(api_url, repository, tag, token)
    expected_assets = {bundle.name, manifest_path.name, checksum.name}
    if existing is not None:
        try:
            record = json.loads(existing.get("body") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"existing release {tag} has non-JSON metadata") from exc
        assets = {item.get("name") for item in existing.get("assets", [])}
        if record != expected_record or assets != expected_assets:
            raise ValueError(f"existing release {tag} does not match immutable archive contract")
        return {"status": "already-archived", "release_tag": tag, **expected_record}

    create_url = f"{api_url.rstrip('/')}/repos/{repository}/releases"
    created = _request(
        create_url,
        token,
        method="POST",
        payload={
            "tag_name": tag,
            "target_commitish": source_sha,
            "name": f"Artifact {manifest['artifact_name']}",
            "body": json.dumps(expected_record, sort_keys=True),
            "draft": False,
            "prerelease": True,
            "make_latest": "false",
        },
    )
    assert isinstance(created, dict)
    upload_template = created["upload_url"].split("{", 1)[0]
    for path in (bundle, manifest_path, checksum):
        upload_url = f"{upload_template}?name={urllib.parse.quote(path.name)}"
        _request(
            upload_url,
            token,
            method="POST",
            data=path.read_bytes(),
            content_type="application/octet-stream",
        )
    return {"status": "archived", "release_tag": tag, **expected_record}


def download(directory: Path, *, repository: str, artifact_name: str, expected_sha256: str, api_url: str, token: str) -> dict:
    tag = release_tag(artifact_name)
    release = _get_release(api_url, repository, tag, token)
    if release is None:
        raise ValueError(f"long-term artifact release not found: {tag}")
    try:
        record = json.loads(release.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("release metadata is not valid JSON") from exc
    if record.get("artifact_name") != artifact_name:
        raise ValueError("release artifact name does not match requested artifact")
    if record.get("bundle_sha256") != expected_sha256:
        raise ValueError("release digest does not match requested digest")

    directory.mkdir(parents=True, exist_ok=True)
    assets = release.get("assets", [])
    if len(assets) != 3:
        raise ValueError(f"expected three immutable release assets, found {len(assets)}")
    for asset in assets:
        name = asset.get("name", "")
        if not name or Path(name).name != name:
            raise ValueError(f"unsafe release asset name: {name}")
        url = asset.get("url")
        if not url:
            raise ValueError(f"release asset missing API URL: {name}")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            (directory / name).write_bytes(response.read())
    return {"status": "downloaded", "release_tag": tag, **record}


def _emit(result: dict) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key in ("release_tag", "artifact_name", "bundle_sha256", "source_sha", "workflow_run_id"):
                if key in result:
                    handle.write(f"{key}={result[key]}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("--directory", required=True)
    archive_parser.add_argument("--repository", required=True)
    archive_parser.add_argument("--source-sha", required=True)
    archive_parser.add_argument("--run-id", required=True)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--directory", required=True)
    download_parser.add_argument("--repository", required=True)
    download_parser.add_argument("--artifact-name", required=True)
    download_parser.add_argument("--expected-sha256", required=True)

    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()
    token = os.getenv("GH_TOKEN", "")
    if not token:
        raise SystemExit("GH_TOKEN is required")

    try:
        if args.command == "archive":
            result = archive(
                Path(args.directory).resolve(),
                repository=args.repository,
                source_sha=args.source_sha,
                run_id=args.run_id,
                api_url=args.api_url,
                token=token,
            )
        else:
            result = download(
                Path(args.directory).resolve(),
                repository=args.repository,
                artifact_name=args.artifact_name,
                expected_sha256=args.expected_sha256,
                api_url=args.api_url,
                token=token,
            )
    except (ValueError, OSError, urllib.error.URLError) as exc:
        raise SystemExit(str(exc)) from exc
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
