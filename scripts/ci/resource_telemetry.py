#!/usr/bin/env python3
"""Capture cgroup v2 resource pressure and compute per-build deltas."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_integer(path: Path) -> int | None:
    value = _read_text(path)
    if not value or value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _read_key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in _read_text(path).splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def _read_io(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in _read_text(path).splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, int] = {}
        for item in fields[1:]:
            key, separator, raw = item.partition("=")
            if not separator:
                continue
            try:
                values[key] = int(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def _read_pressure(proc_root: Path, name: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in _read_text(proc_root / "pressure" / name).splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for item in fields[1:]:
            key, separator, raw = item.partition("=")
            if not separator:
                continue
            try:
                values[key] = float(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def read_resource_snapshot(
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Return current cgroup counters, memory usage and Linux PSI pressure."""
    return {
        "cpu_stat": _read_key_values(cgroup_root / "cpu.stat"),
        "memory_events": _read_key_values(cgroup_root / "memory.events"),
        "memory_current_bytes": _read_integer(cgroup_root / "memory.current"),
        "memory_peak_bytes": _read_integer(cgroup_root / "memory.peak"),
        "io_stat": _read_io(cgroup_root / "io.stat"),
        "pressure": {
            name: _read_pressure(proc_root, name)
            for name in ("cpu", "memory", "io")
        },
    }


def calculate_resource_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    def counters(name: str) -> dict[str, int]:
        left = before.get(name, {})
        right = after.get(name, {})
        if not isinstance(left, dict) or not isinstance(right, dict):
            return {}
        return {
            key: max(0, int(right[key]) - int(value))
            for key, value in left.items()
            if key in right
        }

    left_io = before.get("io_stat", {})
    right_io = after.get("io_stat", {})
    totals = {key: 0 for key in ("rbytes", "wbytes", "rios", "wios", "dbytes", "dios")}
    if isinstance(left_io, dict) and isinstance(right_io, dict):
        for device, left in left_io.items():
            right = right_io.get(device, {})
            if not isinstance(left, dict) or not isinstance(right, dict):
                continue
            for key in totals:
                totals[key] += max(0, int(right.get(key, 0)) - int(left.get(key, 0)))

    psi_delta: dict[str, dict[str, float]] = {}
    left_pressure = before.get("pressure", {})
    right_pressure = after.get("pressure", {})
    if isinstance(left_pressure, dict) and isinstance(right_pressure, dict):
        for resource, records in left_pressure.items():
            right_records = right_pressure.get(resource, {})
            if not isinstance(records, dict) or not isinstance(right_records, dict):
                continue
            for kind, values in records.items():
                right_values = right_records.get(kind, {})
                if not isinstance(values, dict) or not isinstance(right_values, dict):
                    continue
                total_before = values.get("total")
                total_after = right_values.get("total")
                if isinstance(total_before, (int, float)) and isinstance(total_after, (int, float)):
                    psi_delta.setdefault(resource, {})[kind] = max(0.0, total_after - total_before)

    return {
        "cpu": counters("cpu_stat"),
        "memory_events": counters("memory_events"),
        "io": totals,
        "memory_peak_bytes": after.get("memory_peak_bytes"),
        "memory_current_bytes": after.get("memory_current_bytes"),
        "pressure_total_usec": psi_delta,
    }
