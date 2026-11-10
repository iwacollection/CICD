#!/usr/bin/env python3
"""Normalize build-local ccache, CMake, Ninja and runner resource telemetry."""
from __future__ import annotations

import json
import os
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
    build_stats_available = bool(build_stats.strip())
    global_available = bool(global_stats.strip())

    direct = _counter(build_stats, ("cache hit direct", "cache hits direct", "direct")) if build_stats_available else None
    preprocessed = _counter(build_stats, ("cache hit preprocessed", "cache hits preprocessed", "preprocessed")) if build_stats_available else None
    hits = _counter(build_stats, ("hits",)) if build_stats_available else None
    if hits is None and direct is not None and preprocessed is not None:
        hits = direct + preprocessed

    misses = _counter(build_stats, ("cache miss", "cache misses", "misses")) if build_stats_available else None
    uncacheable = _counter(build_stats, ("uncacheable calls", "uncacheable")) if build_stats_available else None
    cacheable_calls = _counter(build_stats, ("cacheable calls",)) if build_stats_available else None
    build_cleanups = _counter(build_stats, ("cleanups", "cleanup")) if build_stats_available else None
    global_cleanups = _counter(global_stats, ("cleanups", "cleanup")) if global_available else None
    files = _counter(global_stats, ("files in cache", "files")) if global_available else None
    size_gb = _first_float(global_stats, ("cache size gb", "cache size gib")) if global_available else None
    limit_gb = None
    size_line = next(
        (line for line in global_stats.splitlines() if "cache size" in line.lower() and ":" in line),
        "",
    )
    if size_line:
        numbers = re.findall(r"([0-9]+(?:\.[0-9]+)?)", size_line.partition(":")[2])
        if len(numbers) > 1:
            limit_gb = float(numbers[1])

    calls = hits + misses if hits is not None and misses is not None else None
    hit_rate = round(hits / calls * 100.0, 2) if calls else None
    cleanup_events = build_cleanups if build_cleanups is not None else global_cleanups
    cleanup_scope = (
        "build" if build_cleanups is not None
        else "global_snapshot" if global_cleanups is not None
        else "unavailable"
    )
    return {
        # "available" means the build-local hit/miss counters are complete.
        # A global snapshot alone must never be interpreted as zero build calls.
        "available": hits is not None and misses is not None,
        "build_stats_available": build_stats_available,
        "global_available": global_available,
        "cacheable_calls": cacheable_calls,
        "hits": hits,
        "hits_direct": direct,
        "hits_preprocessed": preprocessed,
        "misses": misses,
        "uncacheable_calls": uncacheable,
        "hit_rate_percent": hit_rate,
        "cache_size_gib": size_gb,
        "cache_limit_gib": limit_gb,
        "files_in_cache": files,
        # ccache exposes cleanup runs, not an exact per-object eviction count.
        "cleanup_events": cleanup_events,
        "cleanup_events_scope": cleanup_scope,
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
    try:
        conan = json.loads((telemetry_dir / "conan-build-timings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        conan = {}
    return {
        "schema_version": 1,
        "build_exit_code": int(build_exit_code),
        "build_duration_ms": round(float(build_duration_ms), 2),
        "resources": resource_profile or {},
        "ccache": parse_ccache_stats(build_stats, global_stats),
        "cmake_ninja": cmake,
        "conan": conan,
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
