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
    # Per-build hit/miss counters are the only valid basis for a hit rate.
    # A global snapshot alone must never be converted into zero build hits.
    hits = _counter(build_stats, ("hits",))
    misses = _counter(build_stats, ("cache miss", "cache misses", "misses"))
    build_available = hits is not None and misses is not None
    direct = _counter(build_stats, ("cache hit direct", "cache hits direct", "direct")) if build_available else None
    preprocessed = _counter(build_stats, ("cache hit preprocessed", "cache hits preprocessed", "preprocessed")) if build_available else None
    uncacheable = _counter(build_stats, ("uncacheable calls", "uncacheable")) if build_available else None
    cacheable_calls = _counter(build_stats, ("cacheable calls",)) if build_available else None
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

    calls = hits + misses if build_available else None
    hit_rate = round(hits / calls * 100.0, 2) if calls else None
    cleanup_events = build_cleanups if build_cleanups is not None else global_cleanups
    cleanup_scope = (
        "build" if build_cleanups is not None
        else "global_snapshot" if global_cleanups is not None
        else "unavailable"
    )
    return {
        "available": build_available,
        "reason": None if build_available else "per-build ccache hit/miss counters are missing",
        "global_snapshot_available": bool(global_stats.strip()),
        "cacheable_calls": cacheable_calls,
        "hits": hits if build_available else None,
        "hits_direct": direct,
        "hits_preprocessed": preprocessed,
        "misses": misses if build_available else None,
        "uncacheable_calls": uncacheable,
        "hit_rate_percent": hit_rate,
        "cache_size_gib": size_gb,
        "cache_limit_gib": limit_gb,
        "files_in_cache": files,
        # ccache exposes cleanup runs, not an exact per-object eviction count.
        "cleanup_events": cleanup_events,
        "cleanup_events_scope": cleanup_scope,
    }


def _read_json_telemetry(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, {"status": "unavailable", "source": path.name, "reason": "file_missing"}
    except UnicodeDecodeError:
        return {}, {"status": "unavailable", "source": path.name, "reason": "invalid_utf8"}
    except OSError as exc:
        return {}, {"status": "unavailable", "source": path.name, "reason": f"read_error: {type(exc).__name__}"}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}, {"status": "unavailable", "source": path.name, "reason": "invalid_json"}
    if not isinstance(value, dict) or not value:
        return {}, {"status": "unavailable", "source": path.name, "reason": "empty_or_non_object"}
    phase_values = [
        item for key, item in value.items()
        if key.endswith("_ms") and isinstance(item, (int, float)) and not isinstance(item, bool)
    ]
    if not phase_values:
        return {}, {"status": "unavailable", "source": path.name, "reason": "no_numeric_phase_measurements"}
    return value, {"status": "available", "source": path.name, "reason": None}


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
    ccache = parse_ccache_stats(build_stats, global_stats)
    cmake, cmake_availability = _read_json_telemetry(telemetry_dir / "cmake-build.json")
    conan, conan_availability = _read_json_telemetry(telemetry_dir / "conan-build-timings.json")
    ccache_availability = {
        "status": "available" if ccache["available"] else "unavailable",
        "source": "ccache-log-stats.txt",
        "reason": ccache["reason"],
    }
    return {
        "schema_version": 1,
        "build_exit_code": int(build_exit_code),
        "build_duration_ms": round(float(build_duration_ms), 2),
        "resources": resource_profile or {},
        "ccache": ccache,
        "cmake_ninja": cmake,
        "conan": conan,
        "telemetry_availability": {
            "ccache": ccache_availability,
            "cmake_ninja": cmake_availability,
            "conan": conan_availability,
        },
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
