#!/usr/bin/env python3
"""Run the Trivy filesystem report and CycloneDX SBOM generation concurrently."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ci_metrics import finish_stage, start_stage


def _command_sets(
    *,
    trivy_bin: str,
    scan_ref: str,
    scan_output: Path,
    sbom_output: Path,
    scan_cache_dir: Path,
    sbom_cache_dir: Path,
) -> dict[str, list[str]]:
    return {
        "trivy_scan": [
            trivy_bin,
            "fs",
            "--scanners",
            "vuln,license,secret,misconfig",
            "--severity",
            "HIGH,CRITICAL",
            "--format",
            "json",
            "--output",
            str(scan_output),
            "--exit-code",
            "0",
            "--cache-dir",
            str(scan_cache_dir),
            scan_ref,
        ],
        "sbom_generation": [
            trivy_bin,
            "fs",
            "--format",
            "cyclonedx",
            "--output",
            str(sbom_output),
            "--skip-db-update",
            "--skip-java-db-update",
            "--skip-check-update",
            "--cache-dir",
            str(sbom_cache_dir),
            scan_ref,
        ],
    }


def run_parallel(
    *,
    scan_ref: str,
    scan_output: Path,
    sbom_output: Path,
    metrics_file: Path,
    scan_cache_dir: Path,
    sbom_cache_dir: Path,
    trivy_bin: str = "trivy",
    trivy_stage_prestarted: bool = False,
) -> dict[str, Any]:
    """Run scan and SBOM processes together, recording independent stage timings."""
    for output in (scan_output, sbom_output):
        output.parent.mkdir(parents=True, exist_ok=True)
    for cache_dir in (scan_cache_dir, sbom_cache_dir):
        cache_dir.mkdir(parents=True, exist_ok=True)

    commands = _command_sets(
        trivy_bin=trivy_bin,
        scan_ref=scan_ref,
        scan_output=scan_output,
        sbom_output=sbom_output,
        scan_cache_dir=scan_cache_dir,
        sbom_cache_dir=sbom_cache_dir,
    )

    pending: dict[str, subprocess.Popen[bytes]] = {}
    exit_codes: dict[str, int] = {}

    # Start both timers immediately before launching their respective processes.
    # Keeping the timer file writes in this parent process avoids concurrent JSON
    # updates while still measuring the actual lifetime of each child process.
    for stage, command in commands.items():
        if not (stage == "trivy_scan" and trivy_stage_prestarted):
            start_stage(metrics_file, stage)
        try:
            pending[stage] = subprocess.Popen(command)
        except OSError as exc:
            print(f"Unable to start {stage}: {exc}", file=sys.stderr)
            finish_stage(metrics_file, stage, exit_code=127)
            exit_codes[stage] = 127

    while pending:
        for stage, process in list(pending.items()):
            return_code = process.poll()
            if return_code is None:
                continue
            finish_stage(metrics_file, stage, exit_code=return_code)
            exit_codes[stage] = return_code
            del pending[stage]
        if pending:
            time.sleep(0.02)

    for stage, return_code in exit_codes.items():
        print(f"{stage}: exit_code={return_code}")
    return {
        "exit_codes": exit_codes,
        "success": all(code == 0 for code in exit_codes.values())
        and len(exit_codes) == len(commands),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-ref", required=True)
    parser.add_argument("--scan-output", required=True)
    parser.add_argument("--sbom-output", required=True)
    parser.add_argument("--metrics-file", required=True)
    parser.add_argument("--scan-cache-dir", required=True)
    parser.add_argument("--sbom-cache-dir", required=True)
    parser.add_argument("--trivy-bin", default="trivy")
    parser.add_argument(
        "--trivy-stage-prestarted",
        action="store_true",
        help="The trivy_scan timer was started before Trivy setup/cache restore.",
    )
    args = parser.parse_args()

    result = run_parallel(
        scan_ref=args.scan_ref,
        scan_output=Path(args.scan_output),
        sbom_output=Path(args.sbom_output),
        metrics_file=Path(args.metrics_file),
        scan_cache_dir=Path(args.scan_cache_dir),
        sbom_cache_dir=Path(args.sbom_cache_dir),
        trivy_bin=args.trivy_bin,
        trivy_stage_prestarted=args.trivy_stage_prestarted,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
