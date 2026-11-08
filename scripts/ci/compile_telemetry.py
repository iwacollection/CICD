#!/usr/bin/env python3
"""Normalize build-local ccache, CMake, Ninja and runner resource telemetry."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _counter(text: str, labels: tuple[str, ...]) -> int | None:
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
        if not any(label in normalized for label in labels):
            continue
        match = re.search(r"([0-9][0-9,]*)", value)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _first_float(text: str, labels: tuple[str, ...]) -> float | None:
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
        if not any(label in normalized for label in labels):
            continue
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
        if match:
            return float(match.group(1))
    return None


def parse_ccache_stats(build_stats: str, global_stats: str) -> dict[str, Any]:
    direct = _counter(build_stats, ("cache hit direct", "cache hits direct", "direct"))
    preprocessed = _counter(build_stats, ("cache hit preprocessed", "cache hits preprocessed", "preprocessed"))
    hits = _counter(build_stats, ("hits",))
    if direct is None and preprocessed is None and hits is None:
        direct = 0
        preprocessed = 0
        hits = 0
    else:
        direct = direct or 0
        preprocessed = preprocessed or 0
        hits = hits if hits is not None else direct + preprocessed

    misses = _counter(build_stats, ("cache miss", "cache misses", "misses"))
    uncacheable = _counter(build_stats, ("uncacheable calls", "uncacheable"))
    cacheable_calls = _counter(build_stats, ("cacheable calls",))
    build_cleanups = _counter(build_stats, ("cleanups", "cleanup"))
    global_cleanups = _counter(global_stats, ("cleanups", "cleanup"))
    files = _counter(global_stats, ("files in cache", "files"))
    size_gb = _first_float(global_stats, ("cache size gb", "cache size gib"))
    limit_gb = None
    size_line = next(
        (line for line in global_stats.splitlines() if "cache size" in line.lower() and ":" in line),
        "",
    )
    if size_line:
        numbers = re.findall(r"([0-9]+(?:\.[0-9]+)?)", size_line.partition(":")[2])
        if len(numbers) > 1:
            limit_gb = float(numbers[1])

    misses = misses or 0
    hits = hits or 0
    calls = hits + misses
    hit_rate = round(hits / calls * 100.0, 2) if calls else None
    return {
        "available": bool(build_stats.strip() or global_stats.strip()),
        "cacheable_calls": cacheable_calls,
        "hits": hits,
        "hits_direct": direct,
        "hits_preprocessed": preprocessed,
        "misses": misses,
        "uncacheable_calls": uncacheable or 0,
        "hit_rate_percent": hit_rate,
        "cache_size_gib": size_gb,
        "cache_limit_gib": limit_gb,
        "files_in_cache": files,
        # ccache exposes cleanup runs, not an exact per-object eviction count.
        "cleanup_events": (build_cleanups if build_cleanups is not None else (global_cleanups or 0)),
        "cleanup_events_scope": "build" if build_cleanups is not None else "global_snapshot",
    }


def collect_build_telemetry(
    workdir: Path,
    *,
    build_exit_code: int,
    build_duration_ms: float,
    resource_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    telemetry_dir = workdir / ".ci-telemetry"
    try:
        build_stats = (telemetry_dir / "ccache-log-stats.txt").read_text(encoding="utf-8")
    except OSError:
        build_stats = ""
    try:
        global_stats = (telemetry_dir / "ccache-global-stats.txt").read_text(encoding="utf-8")
    except OSError:
        global_stats = ""
    try:
        cmake = json.loads((telemetry_dir / "cmake-build.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cmake = {}
    return {
        "schema_version": 1,
        "build_exit_code": int(build_exit_code),
        "build_duration_ms": round(float(build_duration_ms), 2),
        "resources": resource_profile or {},
        "ccache": parse_ccache_stats(build_stats, global_stats),
        "cmake_ninja": cmake,
    }


def write_build_telemetry(workdir: Path, **kwargs: Any) -> Path:
    telemetry_dir = workdir / ".ci-telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    phase = os.environ.get("CI_BUILD_PHASE", "build").strip().lower() or "build"
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in phase):
        raise ValueError(f"invalid CI_BUILD_PHASE: {phase}")
    telemetry = collect_build_telemetry(workdir, **kwargs)
    telemetry["phase"] = phase
    output = telemetry_dir / f"build-telemetry-{phase}.json"
    output.write_text(
        json.dumps(telemetry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
