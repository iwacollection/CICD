#!/usr/bin/env python3
"""Create and inspect canonical SDK identity files for self-hosted hardware Runners."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _identity_path(sdk_root: Path) -> Path:
    return sdk_root / ".ci" / "sdk-identity.json"


def _canonical_payload(vendor: str, sdk_id: str, version: str, source_digest: str, patchset_digest: str) -> dict:
    payload = {
        "schema_version": 1,
        "vendor": vendor,
        "sdk_id": sdk_id,
        "version": version,
        "source_digest": source_digest,
        "patchset_digest": patchset_digest,
    }
    return payload


def _validate_payload(payload: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["SDK identity root must be an object"]
    if payload.get("schema_version") != 1:
        errors.append("SDK identity schema_version must be 1")
    if payload.get("vendor") != "rockchip":
        errors.append("RK SDK identity vendor must be rockchip")
    for field in ("sdk_id", "version"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"SDK identity {field} must be a non-empty string")
    for field in ("source_digest", "patchset_digest"):
        value = payload.get(field)
        if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
            errors.append(f"SDK identity {field} must be an immutable sha256 digest")
    return errors


def create_identity(args: argparse.Namespace) -> int:
    sdk_root = Path(args.sdk_root).expanduser().resolve()
    if not sdk_root.is_dir():
        print(f"ERROR: SDK root does not exist: {sdk_root}", file=sys.stderr)
        return 2
    for value, name in ((args.source_digest, "source_digest"), (args.patchset_digest, "patchset_digest")):
        if not DIGEST_RE.fullmatch(value):
            print(f"ERROR: {name} must be sha256 followed by 64 lowercase hex characters", file=sys.stderr)
            return 2
    payload = _canonical_payload("rockchip", args.sdk_id, args.version, args.source_digest, args.patchset_digest)
    path = _identity_path(sdk_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(encoded, encoding="utf-8")
    digest = _sha256_file(path)
    print(json.dumps({"identity_file": str(path), "sdk_identity": digest, **payload}, separators=(",", ":")))
    return 0


def inspect_identity(args: argparse.Namespace) -> int:
    sdk_root = Path(args.sdk_root).expanduser().resolve()
    path = _identity_path(sdk_root)
    if not path.is_file():
        print(f"ERROR: SDK identity file is missing: {path}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read SDK identity: {exc}", file=sys.stderr)
        return 2
    errors = _validate_payload(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    digest = _sha256_file(path)
    result = {"identity_file": str(path), "sdk_identity": digest, **payload}
    print(json.dumps(result, separators=(",", ":")))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"sdk_identity={digest}\n")
            handle.write(f"sdk_id={payload['sdk_id']}\n")
            handle.write(f"sdk_version={payload['version']}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--sdk-root", required=True)
    create.add_argument("--sdk-id", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--source-digest", required=True)
    create.add_argument("--patchset-digest", required=True)
    create.set_defaults(func=create_identity)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--sdk-root", required=True)
    inspect.add_argument("--github-output", default="")
    inspect.set_defaults(func=inspect_identity)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
