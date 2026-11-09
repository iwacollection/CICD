#!/usr/bin/env python3
"""Validate reproducibility inputs for the C++ Conan consumer fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_contract(root: Path) -> dict:
    data = json.loads((root / "ci" / "dependencies.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("C++ dependency contract must use schema_version=1")
    return data


def validate_contract(root: Path, *, require_lock: bool = True) -> list[str]:
    errors: list[str] = []
    try:
        contract = load_contract(root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"cannot load dependency contract: {exc}"]

    if contract.get("package_manager") != "conan":
        errors.append("package_manager must be conan")
    if contract.get("package_manager_version") != "2.34.0":
        errors.append("package_manager_version must be pinned to 2.34.0")
    if contract.get("lockfile") != "conan.lock":
        errors.append("lockfile must be conan.lock")

    required = {
        "conanfile.py", "conan.lock", contract.get("profile", ""),
        "CMakeLists.txt", "libs/core/CMakeLists.txt",
        "libs/transport/CMakeLists.txt", "ci/dependencies.json",
    }
    fingerprint_files = contract.get("fingerprint_files")
    if not isinstance(fingerprint_files, list) or not all(isinstance(x, str) and x for x in fingerprint_files):
        errors.append("fingerprint_files must be a non-empty string list")
        fingerprint_files = []
    omitted = sorted(required - set(fingerprint_files))
    if omitted:
        errors.append("fingerprint_files omits required inputs: " + ", ".join(omitted))

    for relative in fingerprint_files:
        path = root / relative
        if relative == "conan.lock" and not require_lock and not path.exists():
            continue
        if not path.is_file():
            errors.append(f"fingerprint input is missing: {relative}")

    lock_path = root / "conan.lock"
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Conan lockfile is invalid JSON: {exc}")
        else:
            if not isinstance(lock, dict) or not isinstance(lock.get("version"), str):
                errors.append("Conan lockfile must contain its generated version field")
            requirements = lock.get("requires", []) if isinstance(lock, dict) else []
            if not isinstance(requirements, list) or not any(
                isinstance(ref, str) and ref.split("#", 1)[0] == "fmt/11.0.2"
                for ref in requirements
            ):
                errors.append("Conan lockfile does not lock fmt/11.0.2")
    elif require_lock:
        errors.append("conan.lock is required; generate it with Conan 2.34.0, do not hand-author it")

    return errors


def fingerprint(root: Path) -> str:
    contract = load_contract(root)
    digest = hashlib.sha256()
    for relative in sorted(contract["fingerprint_files"]):
        path = root / relative
        if not path.is_file():
            raise ValueError(f"fingerprint input is missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--allow-missing-lock", action="store_true")
    parser.add_argument("--print-fingerprint", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors = validate_contract(root, require_lock=not args.allow_missing_lock)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.print_fingerprint:
        print(fingerprint(root))
    else:
        print("cpp_dependency_contract=valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
