#!/usr/bin/env python3
"""Build one hosted-safe target twice in clean workspaces and compare exact bytes."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from toolchain_catalog import immutable_reference, index_toolchains, toolchain_identity, validate_toolchain_catalog
from validate_config import load_catalog, validate_catalog


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_project_target(
    projects: dict[str, Any],
    *,
    project_name: str,
    soc: str,
    target_os: str,
    arch: str,
    expected_toolchain: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = next(
        (
            item
            for item in projects.get("projects", [])
            if isinstance(item, dict) and item.get("name") == project_name
        ),
        None,
    )
    if project is None:
        raise ValueError(f"project not found: {project_name}")
    if project.get("enabled", True) is not True:
        raise ValueError(f"project is not enabled: {project_name}")
    target = next(
        (
            item
            for item in project.get("targets", [])
            if isinstance(item, dict)
            and item.get("soc") == soc
            and item.get("target_os") == target_os
            and item.get("arch") == arch
            and item.get("toolchain") == expected_toolchain
        ),
        None,
    )
    if target is None:
        raise ValueError(
            "reproducibility target not found: "
            f"{project_name}/{soc}/{target_os}/{arch}/{expected_toolchain}"
        )
    if target.get("enabled", True) is not True:
        raise ValueError("reproducibility target must be enabled")
    return project, target


def _source_date_epoch(repository_root: Path) -> int:
    proc = subprocess.run(
        ["git", "show", "-s", "--format=%ct", "HEAD"],
        cwd=repository_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot resolve SOURCE_DATE_EPOCH from git: {proc.stderr.strip()}")
    value = proc.stdout.strip()
    if not value.isdigit():
        raise RuntimeError("git commit timestamp is not numeric")
    return int(value)


def _clean_copy(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns("build", "dist", ".cache", "__pycache__", "*.pyc")
    shutil.copytree(source, destination, ignore=ignored)


def _run_command(
    *,
    repository_root: Path,
    working_directory: Path,
    command: str,
    container_image: str,
) -> None:
    wrapped = f"export CCACHE_DISABLE=1; export SOURCE_DATE_EPOCH=${{SOURCE_DATE_EPOCH}}; {command}"
    args = [
        sys.executable,
        str(repository_root / "scripts" / "ci" / "run_build.py"),
        "--working-directory",
        str(working_directory),
        "--command",
        wrapped,
    ]
    if container_image:
        args.extend(["--container-image", container_image])
    proc = subprocess.run(args, cwd=repository_root, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with exit code {proc.returncode}: {command}")


def _expand_artifacts(workdir: Path, patterns: list[str]) -> dict[str, dict[str, Any]]:
    files: dict[str, Path] = {}
    for pattern in patterns:
        for raw in glob.glob(str(workdir / pattern), recursive=True):
            path = Path(raw).resolve()
            if not path.is_file():
                continue
            relative = path.relative_to(workdir).as_posix()
            files[relative] = path
    if not files:
        raise RuntimeError(f"artifact patterns matched no files: {patterns}")
    return {
        relative: {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        for relative, path in sorted(files.items())
    }


def _package_bundle(
    *,
    repository_root: Path,
    working_directory: Path,
    output_directory: Path,
    project: dict[str, Any],
    target: dict[str, Any],
    toolchain: dict[str, Any],
    container_image: str,
) -> dict[str, str]:
    args = [
        sys.executable,
        str(repository_root / "scripts" / "ci" / "package_artifact.py"),
        "--project",
        str(project["name"]),
        "--working-directory",
        str(working_directory),
        "--artifacts-json",
        json.dumps(target.get("artifact_paths", []), separators=(",", ":")),
        "--dependency-locks-json",
        json.dumps(target.get("dependency_lock_files", []), separators=(",", ":")),
        "--soc",
        str(target["soc"]),
        "--target-os",
        str(target["target_os"]),
        "--arch",
        str(target["arch"]),
        "--toolchain",
        str(target["toolchain"]),
        "--toolchain-identity",
        toolchain_identity(toolchain),
        "--execution-mode",
        str(toolchain["execution_mode"]),
        "--container-image",
        container_image,
        "--runner-labels-json",
        json.dumps(target.get("runner_labels", []), separators=(",", ":")),
        "--output-dir",
        str(output_directory),
    ]
    proc = subprocess.run(
        args,
        cwd=repository_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Artifact Contract v2 packaging failed: {proc.stderr.strip()}")
    bundles = sorted(output_directory.glob("*.tar.gz"))
    if len(bundles) != 1:
        raise RuntimeError(f"expected exactly one reproducibility bundle, found {len(bundles)}")
    bundle = bundles[0]
    return {"file": bundle.name, "sha256": sha256(bundle)}


def _iteration(
    *,
    iteration: int,
    repository_root: Path,
    project: dict[str, Any],
    target: dict[str, Any],
    toolchain: dict[str, Any],
    container_image: str,
    root: Path,
) -> dict[str, Any]:
    source = root / f"run-{iteration}" / "source"
    output = root / f"run-{iteration}" / "dist"
    source.parent.mkdir(parents=True, exist_ok=True)
    _clean_copy((repository_root / str(project["path"])).resolve(), source)

    _run_command(
        repository_root=repository_root,
        working_directory=source,
        command=str(target["build_command"]),
        container_image=container_image,
    )
    test_command = str(target.get("test_command", "")).strip()
    if test_command:
        _run_command(
            repository_root=repository_root,
            working_directory=source,
            command=test_command,
            container_image=container_image,
        )

    artifacts = _expand_artifacts(source, list(target.get("artifact_paths", [])))
    bundle = _package_bundle(
        repository_root=repository_root,
        working_directory=source,
        output_directory=output,
        project=project,
        target=target,
        toolchain=toolchain,
        container_image=container_image,
    )
    return {"iteration": iteration, "artifacts": artifacts, "bundle": bundle}


def compare_iterations(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    first_artifacts = first["artifacts"]
    second_artifacts = second["artifacts"]
    for path in sorted(set(first_artifacts) | set(second_artifacts)):
        left = first_artifacts.get(path)
        right = second_artifacts.get(path)
        if left is None or right is None:
            mismatches.append(
                {
                    "kind": "artifact-set",
                    "path": path,
                    "first": "missing" if left is None else str(left["sha256"]),
                    "second": "missing" if right is None else str(right["sha256"]),
                }
            )
            continue
        if left["sha256"] != right["sha256"]:
            mismatches.append(
                {
                    "kind": "artifact-bytes",
                    "path": path,
                    "first": str(left["sha256"]),
                    "second": str(right["sha256"]),
                }
            )
    if first["bundle"]["sha256"] != second["bundle"]["sha256"]:
        mismatches.append(
            {
                "kind": "artifact-v2-bundle",
                "path": str(first["bundle"]["file"]),
                "first": str(first["bundle"]["sha256"]),
                "second": str(second["bundle"]["sha256"]),
            }
        )
    return mismatches


def render_markdown(report: dict[str, Any]) -> str:
    if report["status"] == "error":
        return "\n".join(
            [
                "# Reproducibility Gate",
                "",
                "Status: **error**",
                f"Project: `{report['project']}`",
                f"Target: `{report['target']}`",
                f"Toolchain: `{report['toolchain']}`",
                "",
                "## Failure",
                "",
                f"- {report['error']}",
                "",
            ]
        )

    lines = [
        "# Reproducibility Gate",
        "",
        f"Status: **{report['status']}**",
        f"Project: `{report['project']}`",
        f"Target: `{report['target']}`",
        f"Toolchain: `{report['toolchain']}`",
        f"Immutable image: `{report['container_image']}`",
        f"SOURCE_DATE_EPOCH: `{report['source_date_epoch']}`",
        "",
        "| Item | Run 1 SHA256 | Run 2 SHA256 |",
        "| --- | --- | --- |",
    ]
    first = report["iterations"][0]
    second = report["iterations"][1]
    for path in sorted(first["artifacts"]):
        lines.append(
            f"| `{path}` | `{first['artifacts'][path]['sha256']}` | `{second['artifacts'].get(path, {}).get('sha256', 'missing')}` |"
        )
    lines.append(
        f"| Artifact v2 bundle | `{first['bundle']['sha256']}` | `{second['bundle']['sha256']}` |"
    )
    lines.extend(["", "## Mismatches", ""])
    if report["mismatches"]:
        for mismatch in report["mismatches"]:
            lines.append(f"- `{mismatch['kind']}` `{mismatch['path']}`")
    else:
        lines.append("- None. Both isolated builds produced identical artifact bytes and bundle bytes.")
    lines.append("")
    return "\n".join(lines)


def _write_outputs(args: argparse.Namespace, report: dict[str, Any]) -> None:
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_out:
        output = Path(args.markdown_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", default="ci/projects.json")
    parser.add_argument("--toolchains", default="ci/toolchains.json")
    parser.add_argument("--hardware", default="ci/hardware-profiles.json")
    parser.add_argument("--project", default="hello-lib")
    parser.add_argument("--soc", default="generic")
    parser.add_argument("--target-os", default="linux")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--toolchain", default="gcc-host-container-v1")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    args = parser.parse_args()

    repository_root = Path.cwd().resolve()
    try:
        projects = load_catalog(Path(args.projects))
        toolchains = load_catalog(Path(args.toolchains))
        hardware = load_catalog(Path(args.hardware))
        toolchain_errors = validate_toolchain_catalog(toolchains)
        if toolchain_errors:
            raise ValueError("invalid toolchain catalog: " + "; ".join(toolchain_errors))
        project_errors = validate_catalog(projects, toolchains, hardware)
        if project_errors:
            raise ValueError("invalid project catalog: " + "; ".join(project_errors))
        project, target = _find_project_target(
            projects,
            project_name=args.project,
            soc=args.soc,
            target_os=args.target_os,
            arch=args.arch,
            expected_toolchain=args.toolchain,
        )
        indexed_toolchains = index_toolchains(toolchains)
        toolchain = indexed_toolchains.get(args.toolchain)
        if toolchain is None:
            raise ValueError(f"unknown toolchain: {args.toolchain}")
        if toolchain.get("status") != "active":
            raise ValueError("reproducibility target requires active toolchain")
        if toolchain.get("execution_mode") != "container":
            raise ValueError("host toolchains are not accepted by the hosted reproducibility baseline")
        runner_labels = target.get("runner_labels", [])
        if runner_labels != ["ubuntu-latest"]:
            raise ValueError("reproducibility baseline is hosted-only and cannot consume self-hosted Runner labels")
        container_image = immutable_reference(toolchain)
        source_date_epoch = _source_date_epoch(repository_root)

        env_before = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
        try:
            with tempfile.TemporaryDirectory(prefix="ci-reproducibility-") as temporary:
                root = Path(temporary)
                first = _iteration(
                    iteration=1,
                    repository_root=repository_root,
                    project=project,
                    target=target,
                    toolchain=toolchain,
                    container_image=container_image,
                    root=root,
                )
                second = _iteration(
                    iteration=2,
                    repository_root=repository_root,
                    project=project,
                    target=target,
                    toolchain=toolchain,
                    container_image=container_image,
                    root=root,
                )
        finally:
            if env_before is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = env_before

        mismatches = compare_iterations(first, second)
        report = {
            "schema_version": 1,
            "status": "reproducible" if not mismatches else "non-reproducible",
            "project": project["name"],
            "target": f"{target['soc']}/{target['target_os']}/{target['arch']}",
            "toolchain": args.toolchain,
            "toolchain_identity": toolchain_identity(toolchain),
            "container_image": container_image,
            "source_date_epoch": source_date_epoch,
            "iterations": [first, second],
            "mismatches": mismatches,
        }
    except (ValueError, RuntimeError, OSError) as exc:
        report = {
            "schema_version": 1,
            "status": "error",
            "project": args.project,
            "target": f"{args.soc}/{args.target_os}/{args.arch}",
            "toolchain": args.toolchain,
            "error": str(exc),
            "iterations": [],
            "mismatches": [],
        }
        _write_outputs(args, report)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _write_outputs(args, report)
    print(json.dumps({"status": report["status"], "mismatches": len(mismatches)}, separators=(",", ":")))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
