#!/usr/bin/env python3
"""Execute trusted build commands with cgroup-aware parallelism and build telemetry."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from compile_telemetry import write_build_telemetry


def host_command(command: str) -> list[str]:
    return ["bash", "-euo", "pipefail", "-c", command]


def _parse_cpu_set(value: str) -> int | None:
    try:
        total = 0
        for item in value.strip().split(","):
            if not item:
                continue
            if "-" in item:
                first, last = item.split("-", 1)
                total += int(last) - int(first) + 1
            else:
                total += 1
        return total if total > 0 else None
    except ValueError:
        return None


def _read_positive_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value or value == "max":
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _detect_resource_limits(
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    *,
    cpu_count: int | None = None,
    affinity_count: int | None = None,
    memory_per_job_mb: int | None = None,
    memory_reserve_mb: int | None = None,
) -> dict[str, int | float | None]:
    """Detect effective CPU and memory limits, including cgroup v1/v2 constraints."""
    detected_cpu = max(1, cpu_count or os.cpu_count() or 1)
    if affinity_count is None and hasattr(os, "sched_getaffinity"):
        try:
            affinity_count = len(os.sched_getaffinity(0))
        except OSError:
            affinity_count = None
    if affinity_count and affinity_count > 0:
        detected_cpu = min(detected_cpu, affinity_count)

    cpuset = (
        _read_text(cgroup_root / "cpuset.cpus.effective")
        or _read_text(cgroup_root / "cpuset" / "cpuset.cpus")
    )
    cpuset_count = _parse_cpu_set(cpuset) if cpuset else None
    if cpuset_count:
        detected_cpu = min(detected_cpu, cpuset_count)

    quota_cpus: float | None = None
    cpu_max = _read_text(cgroup_root / "cpu.max")
    if cpu_max:
        fields = cpu_max.split()
        if len(fields) >= 2 and fields[0] != "max":
            try:
                quota_cpus = max(0.01, int(fields[0]) / int(fields[1]))
            except (ValueError, ZeroDivisionError):
                quota_cpus = None
    if quota_cpus is None:
        quota = _read_positive_int(cgroup_root / "cpu" / "cpu.cfs_quota_us")
        period = _read_positive_int(cgroup_root / "cpu" / "cpu.cfs_period_us")
        if quota and period:
            quota_cpus = max(0.01, quota / period)
    if quota_cpus is not None:
        detected_cpu = min(detected_cpu, max(1, int(quota_cpus)))

    memory_limit = _read_positive_int(cgroup_root / "memory.max")
    if memory_limit is None:
        memory_limit = _read_positive_int(cgroup_root / "memory" / "memory.limit_in_bytes")

    per_job_mb = memory_per_job_mb or int(os.environ.get("CI_BUILD_MEMORY_PER_JOB_MB", "2048"))
    reserve_mb = (
        memory_reserve_mb
        if memory_reserve_mb is not None
        else int(os.environ.get("CI_BUILD_MEMORY_RESERVE_MB", "1024"))
    )
    memory_jobs: int | None = None
    if memory_limit and per_job_mb > 0:
        usable_mb = max(per_job_mb, (memory_limit // (1024 * 1024)) - max(0, reserve_mb))
        memory_jobs = max(1, usable_mb // per_job_mb)

    effective_jobs = min(detected_cpu, memory_jobs) if memory_jobs else detected_cpu
    return {
        "cpu_visible": max(1, cpu_count or os.cpu_count() or 1),
        "cpu_affinity": affinity_count,
        "cpu_cpuset": cpuset_count,
        "cpu_quota": quota_cpus,
        "cpu_limit": detected_cpu,
        "memory_limit_bytes": memory_limit,
        "memory_per_job_mb": per_job_mb,
        "memory_reserve_mb": reserve_mb,
        "memory_job_limit": memory_jobs,
        "effective_jobs": max(1, int(effective_jobs)),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _resolve_build_jobs(
    value: str,
    resource_limits: dict[str, int | float | None] | None = None,
) -> str:
    """Resolve auto parallelism against CPU quota, CPU set and cgroup memory."""
    requested = value.strip() or "auto"
    if requested == "auto":
        limits = resource_limits or _detect_resource_limits()
        jobs = max(1, int(limits.get("effective_jobs") or 1))
        cap = os.environ.get("CI_BUILD_MAX_JOBS", "").strip()
        if cap:
            if not cap.isdigit() or int(cap) < 1:
                raise ValueError("CI_BUILD_MAX_JOBS must be a positive integer")
            jobs = min(jobs, int(cap))
        return str(jobs)
    if not requested.isdigit() or int(requested) < 1:
        raise ValueError("CI_BUILD_JOBS must be 'auto' or a positive integer")
    return str(int(requested))


def _configure_build_environment(workdir: Path, requested_jobs: str) -> str:
    """Align compiler cache paths and CMake/Ninja concurrency with the runner limits."""
    resource_limits = _detect_resource_limits()
    jobs = _resolve_build_jobs(requested_jobs, resource_limits)
    cache_scope = os.environ.get("CI_CCACHE_SCOPE", "project").strip() or "project"
    if cache_scope == "toolchain":
        if len(workdir.parents) < 2:
            raise RuntimeError("toolchain-shared ccache requires a source checkout root")
        ccache_dir = workdir.parents[1] / ".ci-cache" / "ccache"
    else:
        ccache_dir = workdir / ".cache" / "ccache"

    telemetry_dir = workdir / ".ci-telemetry"
    ccache_dir.mkdir(parents=True, exist_ok=True)
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("ccache.stats.log", "ccache-log-stats.txt", "ccache-global-stats.txt"):
        try:
            (telemetry_dir / filename).unlink()
        except FileNotFoundError:
            pass

    stats_log = telemetry_dir / "ccache.stats.log"
    resource_limits["effective_jobs"] = int(jobs)
    os.environ["CI_BUILD_JOBS"] = jobs
    os.environ["CMAKE_BUILD_PARALLEL_LEVEL"] = jobs
    os.environ["CCACHE_DIR"] = str(ccache_dir)
    os.environ["CCACHE_STATSLOG"] = str(stats_log)
    os.environ["CI_BUILD_RESOURCE_PROFILE_JSON"] = json.dumps(resource_limits, sort_keys=True)
    helper = Path(__file__).resolve().with_name("cmake_build.py")
    if helper.is_file():
        os.environ["CI_CMAKE_BUILD_HELPER"] = str(helper)
    return jobs


def _ensure_source_date_epoch(workdir: Path) -> str:
    """Use the checked-out source commit timestamp as the deterministic build clock."""
    existing = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if existing:
        if not existing.isdigit():
            raise RuntimeError("SOURCE_DATE_EPOCH must be an integer Unix timestamp")
        return existing

    proc = subprocess.run(
        ["git", "-C", str(workdir), "show", "-s", "--format=%ct", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "cannot derive SOURCE_DATE_EPOCH from checked-out source commit: "
            + proc.stderr.strip()
        )
    value = proc.stdout.strip()
    if not value.isdigit():
        raise RuntimeError("checked-out source commit timestamp is not numeric")
    os.environ["SOURCE_DATE_EPOCH"] = value
    return value


def _container_env_args(workdir: Path) -> list[str]:
    args: list[str] = []
    container_home = workdir / ".ci-home"
    maven_repository = workdir / ".cache" / "maven" / "repository"
    telemetry_dir = workdir / ".ci-telemetry"
    container_home.mkdir(parents=True, exist_ok=True)
    maven_repository.mkdir(parents=True, exist_ok=True)
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    cache_scope = os.environ.get("CI_CCACHE_SCOPE", "project")
    if cache_scope == "toolchain":
        container_ccache_dir = "/ccache"
    else:
        container_ccache_dir = "/workspace/.cache/ccache"
    args.extend([
        "-e", "HOME=/workspace/.ci-home",
        "-e", "MAVEN_OPTS=-Dmaven.repo.local=/workspace/.cache/maven/repository",
        "-e", f"CCACHE_DIR={container_ccache_dir}",
        "-e", "CCACHE_STATSLOG=/workspace/.ci-telemetry/ccache.stats.log",
        "-e", "CI_CMAKE_BUILD_HELPER=/ci-platform/cmake_build.py",
    ])

    upstream_root = os.environ.get("CI_UPSTREAM_ROOT", "")
    upstream_index = os.environ.get("CI_UPSTREAM_INDEX", "")
    if upstream_root:
        root = Path(upstream_root).resolve()
        try:
            relative_root = root.relative_to(workdir)
        except ValueError as exc:
            raise RuntimeError(
                "CI_UPSTREAM_ROOT must be inside the mounted working directory for container builds"
            ) from exc
        args.extend(["-e", f"CI_UPSTREAM_ROOT=/workspace/{relative_root.as_posix()}"])
    if upstream_index:
        index = Path(upstream_index).resolve()
        try:
            relative_index = index.relative_to(workdir)
        except ValueError as exc:
            raise RuntimeError(
                "CI_UPSTREAM_INDEX must be inside the mounted working directory for container builds"
            ) from exc
        args.extend(["-e", f"CI_UPSTREAM_INDEX=/workspace/{relative_index.as_posix()}"])

    for key in (
        "SOURCE_DATE_EPOCH",
        "CCACHE_DISABLE",
        "EXPECTED_JAVA_VERSION",
        "EXPECTED_MAVEN_VERSION",
        "MAVEN_ARGS",
        "CI_BUILD_JOBS",
        "CMAKE_BUILD_PARALLEL_LEVEL",
        "CI_BUILD_RESOURCE_PROFILE_JSON",
    ):
        value = os.environ.get(key, "")
        if value:
            args.extend(["-e", f"{key}={value}"])
    return args


def container_command(workdir: Path, command: str, image: str) -> list[str]:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("container execution requested but docker is not installed on this Runner")

    args = [
        docker, "run", "--rm", "--init", "-e", "CI=true",
        "-v", f"{workdir}:/workspace", "-w", "/workspace",
    ]
    cache_scope = os.environ.get("CI_CCACHE_SCOPE", "project")
    if cache_scope == "toolchain":
        cache_dir = Path(os.environ["CCACHE_DIR"]).resolve()
        args.extend(["-v", f"{cache_dir}:/ccache"])

    helper = Path(__file__).resolve().with_name("cmake_build.py")
    if helper.is_file():
        args.extend(["-v", f"{helper}:/ci-platform/cmake_build.py:ro"])

    args.extend(_container_env_args(workdir))
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    args.extend([image, "bash", "-euo", "pipefail", "-c", command])
    return args


def _with_ccache_stats(command: str) -> str:
    """Capture build-local ccache counters without resetting shared cache statistics."""
    prefix = """mkdir -p .ci-telemetry
_finish_ccache_stats() {
  rc=$?
  trap - EXIT
  if command -v ccache >/dev/null 2>&1; then
    ccache --show-log-stats > .ci-telemetry/ccache-log-stats.txt 2>&1 || true
    ccache --show-stats --verbose > .ci-telemetry/ccache-global-stats.txt 2>&1 || true
  fi
  exit "$rc"
}
trap _finish_ccache_stats EXIT
"""
    return prefix + command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument(
        "--container-image",
        default="",
        help="Optional Docker image. When set, execute the command inside the image.",
    )
    args = parser.parse_args()

    workdir = Path(args.working_directory).resolve()
    if not workdir.is_dir():
        print(f"ERROR: working directory does not exist: {workdir}", file=sys.stderr)
        return 2

    try:
        source_date_epoch = _ensure_source_date_epoch(workdir)
        jobs = _configure_build_environment(workdir, os.environ.get("CI_BUILD_JOBS", "auto"))
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    execution = "container" if args.container_image else "host"
    print("=" * 72)
    print(f"CI build directory : {workdir}")
    print(f"CI execution mode  : {execution}")
    if args.container_image:
        print(f"CI container image : {args.container_image}")
    if os.environ.get("CI_UPSTREAM_ROOT"):
        print(f"CI upstream root   : {os.environ['CI_UPSTREAM_ROOT']}")
    print(f"SOURCE_DATE_EPOCH  : {source_date_epoch}")
    print(f"CI_BUILD_JOBS      : {jobs}")
    print(f"CI_CCACHE_SCOPE    : {os.environ.get('CI_CCACHE_SCOPE', 'project')}")
    print(f"CI build command   : {args.command}")
    print("=" * 72)

    try:
        wrapped_command = _with_ccache_stats(args.command)
        command = (
            container_command(workdir, wrapped_command, args.container_image)
            if args.container_image
            else host_command(wrapped_command)
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    started = time.monotonic()
    env = os.environ.copy()
    env.setdefault("CI", "true")
    proc = subprocess.run(command, cwd=workdir, env=env, check=False)
    elapsed_ms = (time.monotonic() - started) * 1000
    print(f"build_exit_code={proc.returncode} elapsed_seconds={elapsed_ms / 1000:.2f}")

    try:
        profile = json.loads(os.environ.get("CI_BUILD_RESOURCE_PROFILE_JSON", "{}"))
        output = write_build_telemetry(
            workdir,
            build_exit_code=proc.returncode,
            build_duration_ms=elapsed_ms,
            resource_profile=profile,
        )
        print(f"build_telemetry_file={output}")
        telemetry = json.loads(output.read_text(encoding="utf-8"))
        cache = telemetry.get("ccache", {})
        if cache.get("available"):
            print(
                "ccache_build_stats "
                f"hits={cache.get('hits')} misses={cache.get('misses')} "
                f"hit_rate={cache.get('hit_rate_percent')}% "
                f"cleanup_events={cache.get('cleanup_events')}"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: could not collect build telemetry: {exc}", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
