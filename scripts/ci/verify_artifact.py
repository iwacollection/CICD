#!/usr/bin/env python3
"""Verify a packaged artifact against its manifest and SHA256 sidecar."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--expected-sha256", default="")
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    manifests = list(directory.rglob("*.manifest.json"))
    if len(manifests) != 1:
        raise SystemExit(f"expected exactly one manifest, found {len(manifests)}")

    manifest_path = manifests[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_name = manifest["bundle"]["file"]
    expected = manifest["bundle"]["sha256"]
    bundle_candidates = list(directory.rglob(bundle_name))
    if len(bundle_candidates) != 1:
        raise SystemExit(f"expected exactly one bundle {bundle_name}, found {len(bundle_candidates)}")

    bundle = bundle_candidates[0]
    actual = sha256(bundle)
    if actual != expected:
        raise SystemExit(f"manifest digest mismatch: expected={expected} actual={actual}")
    if args.expected_sha256 and actual != args.expected_sha256:
        raise SystemExit(
            f"operator-provided digest mismatch: expected={args.expected_sha256} actual={actual}"
        )

    sidecars = list(directory.rglob(f"{bundle_name}.sha256"))
    if len(sidecars) != 1:
        raise SystemExit(f"expected exactly one checksum sidecar, found {len(sidecars)}")
    sidecar_digest = sidecars[0].read_text(encoding="utf-8").strip().split()[0]
    if sidecar_digest != actual:
        raise SystemExit(f"checksum sidecar mismatch: expected={sidecar_digest} actual={actual}")

    print(
        json.dumps(
            {
                "status": "verified",
                "project": manifest["project"],
                "source_sha": manifest["source_sha"],
                "bundle": bundle.name,
                "sha256": actual,
                "target": manifest["target"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
