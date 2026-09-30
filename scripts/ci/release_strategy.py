#!/usr/bin/env python3
"""Plan canary, blue-green, and cross-region multi-cluster releases.

Environment canary and blue-green change traffic on one chosen cluster set
(Gateway API HTTPRoute). Cross-region canary (`multi_cluster_canary`) keeps
that same weight schedule, but opens one region at a time. Clusters in the
open region share the step weight. Later regions stay at 0% canary until
they open, and they stay on canary weights instead of switching to blue-green.

Cross-region blue-green (`multi_cluster_blue_green`) uses the same region
order and exact cluster pin, but never ramps weights. Clusters in the open
region share one blue/green slot. Later regions stay on the baseline slot
and are omitted from the render until they open. A preview header does not
change the production route weight. Cutover is atomic.

Cluster membership is an exact pin (Open Cluster Management PlacementDecision
plus an Argo CD ApplicationSet list generator). A route must never add a
cluster that the pin omitted.

The engine does not build artifacts and does not apply manifests to a live
cluster. Callers pass an environment pointer digest that promotion already
recorded.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from promotion_policy import load_policy as load_promotion_policy
from promotion_policy import normalize_identity

SCHEMA_VERSION = 1
DNS1123_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOP_LEVEL_CLUSTER_FIELDS = ("environment", "region", "role")
ANALYSIS_CHECKS = ("smoke", "readiness", "error_rate", "latency_p95")
ROLES = ("canary", "stable")
TRAFFIC_LABELS = ("gateway", "none")
BLUE_GREEN_STEPS = (
    ("deploy_inactive", "hold"),
    ("preview", "preview"),
    ("cutover", "switch"),
    ("confirm", "active"),
)
METHOD_DECISION = {
    "canary": {
        "question": "同一服务的请求如何在旧版本和新版本之间按比例分配",
        "method": "routing",
        "tool": "Gateway API HTTPRoute",
    },
    "blue_green": {
        "question": "新旧版本如何各占一个槽位并一次切完生产流量",
        "method": "routing",
        "tool": "Gateway API HTTPRoute",
    },
    "multi_cluster_canary": {
        "question": "多个区域里的多个集群如何按同一套灰度权重推进，后开区域在打开前保持 0%",
        "method": "cluster_management",
        "tool": "PlacementDecision + ApplicationSet + HTTPRoute",
    },
    "multi_cluster_blue_green": {
        "question": "多个区域里的多个集群如何共用蓝绿槽位并一次切完，后开区域在打开前保持基线",
        "method": "cluster_management",
        "tool": "PlacementDecision + ApplicationSet + HTTPRoute",
    },
}

MEMBERSHIP_STRATEGIES = frozenset({"multi_cluster_canary", "multi_cluster_blue_green"})
MEMBERSHIP_TRAFFIC = {
    "multi_cluster_canary": "canary",
    "multi_cluster_blue_green": "blue_green",
}
CLUSTER_MANAGEMENT = {
    "placement_api": "cluster.open-cluster-management.io/v1beta1",
    "placement_kind": "Placement",
    "decision_kind": "PlacementDecision",
    "delivery_api": "argoproj.io/v1alpha1",
    "delivery_kind": "ApplicationSet",
}


def _reject_unknown(payload: dict, allowed: set[str], prefix: str) -> list[str]:
    unknown = sorted(set(payload) - allowed)
    if not unknown:
        return []
    return [f"{prefix} has unknown fields: {', '.join(unknown)}"]


def _dns1123(value: object, prefix: str) -> str | None:
    if not isinstance(value, str) or not DNS1123_RE.fullmatch(value):
        return f"{prefix} must be a DNS-1123 label"
    return None


def _sha256(value: object, prefix: str) -> str | None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        return f"{prefix} must be 64 lowercase hexadecimal characters"
    return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be an object")
    return payload


def _cluster_value(cluster: dict[str, Any], key: str) -> str | None:
    if key in TOP_LEVEL_CLUSTER_FIELDS:
        value = cluster.get(key)
        return value if isinstance(value, str) else None
    labels = cluster.get("labels")
    if not isinstance(labels, dict):
        return None
    value = labels.get(key)
    return value if isinstance(value, str) else None


def _matches(cluster: dict[str, Any], selector: dict[str, str]) -> bool:
    return all(_cluster_value(cluster, key) == expected for key, expected in selector.items())


def _has_labels(cluster: dict[str, Any], required: dict[str, str]) -> bool:
    labels = cluster.get("labels")
    if not isinstance(labels, dict):
        return False
    return all(labels.get(key) == expected for key, expected in required.items())


def _inactive_slot(slot: str) -> str:
    if slot == "blue":
        return "green"
    if slot == "green":
        return "blue"
    raise ValueError("active slot must be blue or green")


def validate_release_policy(
    strategies: dict[str, Any],
    clusters: dict[str, Any],
    promotion: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    environments = promotion.get("environments")
    if not isinstance(environments, list):
        return ["promotion environments are unavailable"]
    environment_set = set(environments)

    errors.extend(
        _reject_unknown(
            clusters,
            {"schema_version", "description", "clusters"},
            "clusters",
        )
    )
    if clusters.get("schema_version") != SCHEMA_VERSION:
        errors.append("clusters schema_version must be 1")
    items = clusters.get("clusters")
    if not isinstance(items, list) or not items:
        errors.append("clusters must be a non-empty array")
        items = []

    seen_ids: set[str] = set()
    for index, cluster in enumerate(items):
        prefix = f"clusters[{index}]"
        if not isinstance(cluster, dict):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(
            _reject_unknown(cluster, {"id", "environment", "region", "role", "labels"}, prefix)
        )
        name_error = _dns1123(cluster.get("id"), f"{prefix}.id")
        if name_error:
            errors.append(name_error)
        elif cluster["id"] in seen_ids:
            errors.append(f"duplicate cluster id: {cluster['id']}")
        else:
            seen_ids.add(cluster["id"])
        if cluster.get("environment") not in environment_set:
            errors.append(f"{prefix}.environment is not a promotion environment")
        region_error = _dns1123(cluster.get("region"), f"{prefix}.region")
        if region_error:
            errors.append(region_error)
        if cluster.get("role") not in ROLES:
            errors.append(f"{prefix}.role must be one of {list(ROLES)}")
        labels = cluster.get("labels")
        if not isinstance(labels, dict) or not labels:
            errors.append(f"{prefix}.labels must be a non-empty object")
            continue
        if any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in labels.items()):
            errors.append(f"{prefix}.labels must be string key/value pairs")
        if labels.get("traffic") not in TRAFFIC_LABELS:
            errors.append(f"{prefix}.labels.traffic must be gateway or none")

    errors.extend(
        _reject_unknown(
            strategies,
            {
                "schema_version",
                "description",
                "artifact_contract",
                "namespace",
                "gateway_name",
                "service_port",
                "preview_header",
                "preview_header_value",
                "strategies",
            },
            "release strategies",
        )
    )
    if strategies.get("schema_version") != SCHEMA_VERSION:
        errors.append("release strategies schema_version must be 1")
    if strategies.get("artifact_contract") != 2:
        errors.append("release strategies artifact_contract must be 2")
    for field in ("namespace", "gateway_name"):
        name_error = _dns1123(strategies.get(field), field)
        if name_error:
            errors.append(name_error)
    port = strategies.get("service_port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        errors.append("service_port must be an integer from 1 to 65535")
    header = strategies.get("preview_header")
    if not isinstance(header, str) or not header or any(character.isspace() for character in header):
        errors.append("preview_header must be a header name without spaces")
    if strategies.get("preview_header_value") != "true":
        errors.append("preview_header_value must be true")

    defined = strategies.get("strategies")
    if not isinstance(defined, dict):
        errors.append("strategies must be an object")
        return errors
    errors.extend(
        _reject_unknown(
            defined,
            {"canary", "blue_green", "multi_cluster_canary", "multi_cluster_blue_green"},
            "strategies",
        )
    )
    canary = defined.get("canary")
    blue = defined.get("blue_green")
    multi = defined.get("multi_cluster_canary")
    multi_blue = defined.get("multi_cluster_blue_green")
    if isinstance(canary, dict):
        errors.extend(_validate_canary(canary))
    else:
        errors.append("strategies.canary must be an object")
    if isinstance(blue, dict):
        errors.extend(_validate_blue_green(blue))
    else:
        errors.append("strategies.blue_green must be an object")
    if isinstance(multi, dict):
        errors.extend(_validate_multi_region(multi, "multi_cluster_canary", "canary", environment_set, items))
    else:
        errors.append("strategies.multi_cluster_canary must be an object")
    if isinstance(multi_blue, dict):
        errors.extend(
            _validate_multi_region(
                multi_blue,
                "multi_cluster_blue_green",
                "blue_green",
                environment_set,
                items,
            )
        )
    else:
        errors.append("strategies.multi_cluster_blue_green must be an object")
    for name, decision in METHOD_DECISION.items():
        body = defined.get(name)
        if isinstance(body, dict) and body.get("method") != decision["method"]:
            errors.append(f"strategies.{name}.method must be {decision['method']}")
    return errors


def _validate_thresholds(payload: object, prefix: str) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{prefix} must be an object"]
    errors = _reject_unknown(
        payload,
        {"error_rate_max", "latency_p95_max_ms", "min_requests"},
        prefix,
    )
    rate = payload.get("error_rate_max")
    latency = payload.get("latency_p95_max_ms")
    minimum = payload.get("min_requests")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < float(rate) <= 1:
        errors.append(f"{prefix}.error_rate_max must be greater than 0 and at most 1")
    if isinstance(latency, bool) or not isinstance(latency, int) or latency <= 0:
        errors.append(f"{prefix}.latency_p95_max_ms must be a positive integer")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        errors.append(f"{prefix}.min_requests must be a positive integer")
    return errors


def _validate_analysis_list(payload: object, prefix: str, allow_empty: bool) -> list[str]:
    if not isinstance(payload, list):
        return [f"{prefix} must be an array"]
    if not payload and not allow_empty:
        return [f"{prefix} must not be empty"]
    errors: list[str] = []
    if any(item not in ANALYSIS_CHECKS for item in payload):
        errors.append(f"{prefix} contains an unknown analysis check")
    if len(payload) != len(set(payload)):
        errors.append(f"{prefix} contains duplicate checks")
    return errors


def _validate_canary(canary: dict[str, Any]) -> list[str]:
    errors = _reject_unknown(
        canary,
        {"kind", "method", "routing", "required_cluster_labels", "steps", "analysis_thresholds", "abort"},
        "strategies.canary",
    )
    if canary.get("kind") != "traffic":
        errors.append("strategies.canary.kind must be traffic")
    if canary.get("abort") != "shift_all_weight_to_baseline":
        errors.append("strategies.canary.abort must shift_all_weight_to_baseline")
    routing = canary.get("routing")
    if not isinstance(routing, dict):
        errors.append("strategies.canary.routing must be an object")
    else:
        errors.extend(
            _reject_unknown(routing, {"api", "kind", "backends"}, "strategies.canary.routing")
        )
        if routing.get("api") != "gateway.networking.k8s.io/v1" or routing.get("kind") != "HTTPRoute":
            errors.append("strategies.canary.routing must be Gateway API HTTPRoute")
        if routing.get("backends") != ["stable", "canary"]:
            errors.append("strategies.canary.routing.backends must be stable then canary")
    labels = canary.get("required_cluster_labels")
    if labels != {"traffic": "gateway"}:
        errors.append("strategies.canary.required_cluster_labels must require traffic=gateway")
    errors.extend(_validate_thresholds(canary.get("analysis_thresholds"), "strategies.canary.analysis_thresholds"))
    steps = canary.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["strategies.canary.steps must be a non-empty array"]
    weights: list[int] = []
    names: set[str] = set()
    for index, step in enumerate(steps):
        prefix = f"strategies.canary.steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(_reject_unknown(step, {"name", "canary_weight", "analysis"}, prefix))
        name_error = _dns1123(step.get("name"), f"{prefix}.name")
        if name_error:
            errors.append(name_error)
        elif step["name"] in names:
            errors.append(f"duplicate canary step name: {step['name']}")
        else:
            names.add(step["name"])
        weight = step.get("canary_weight")
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 100:
            errors.append(f"{prefix}.canary_weight must be an integer from 1 to 100")
        else:
            weights.append(weight)
        errors.extend(_validate_analysis_list(step.get("analysis"), f"{prefix}.analysis", allow_empty=False))
    if weights and weights != sorted(set(weights)):
        errors.append("canary weights must be strictly increasing")
    if weights and weights[-1] != 100:
        errors.append("the last canary step must send 100 percent")
    return errors


def _validate_blue_green(blue: dict[str, Any]) -> list[str]:
    errors = _reject_unknown(
        blue,
        {"kind", "method", "routing", "required_cluster_labels", "steps", "analysis_thresholds", "abort"},
        "strategies.blue_green",
    )
    if blue.get("kind") != "traffic":
        errors.append("strategies.blue_green.kind must be traffic")
    if blue.get("abort") != "restore_baseline_slot":
        errors.append("strategies.blue_green.abort must restore_baseline_slot")
    routing = blue.get("routing")
    if not isinstance(routing, dict):
        errors.append("strategies.blue_green.routing must be an object")
    else:
        errors.extend(
            _reject_unknown(
                routing,
                {"api", "kind", "switch", "slots"},
                "strategies.blue_green.routing",
            )
        )
        if routing.get("api") != "gateway.networking.k8s.io/v1" or routing.get("kind") != "HTTPRoute":
            errors.append("strategies.blue_green.routing must be Gateway API HTTPRoute")
        if routing.get("switch") != "atomic":
            errors.append("strategies.blue_green.routing.switch must be atomic")
        if routing.get("slots") != ["blue", "green"]:
            errors.append("strategies.blue_green.routing.slots must be blue then green")
    if blue.get("required_cluster_labels") != {"traffic": "gateway"}:
        errors.append("strategies.blue_green.required_cluster_labels must require traffic=gateway")
    errors.extend(
        _validate_thresholds(blue.get("analysis_thresholds"), "strategies.blue_green.analysis_thresholds")
    )
    steps = blue.get("steps")
    expected = [{"name": name, "traffic": traffic} for name, traffic in BLUE_GREEN_STEPS]
    if not isinstance(steps, list) or [
        {"name": step.get("name"), "traffic": step.get("traffic")} for step in steps if isinstance(step, dict)
    ] != expected:
        errors.append("strategies.blue_green.steps must be deploy_inactive, preview, cutover, confirm")
        return errors
    for index, step in enumerate(steps):
        prefix = f"strategies.blue_green.steps[{index}]"
        errors.extend(_reject_unknown(step, {"name", "traffic", "analysis"}, prefix))
        errors.extend(
            _validate_analysis_list(
                step.get("analysis"),
                f"{prefix}.analysis",
                allow_empty=step.get("name") == "confirm",
            )
        )
    return errors


def _validate_multi_region(
    multi: dict[str, Any],
    strategy_name: str,
    traffic_strategy: str,
    environments: set[str],
    clusters: list[Any],
) -> list[str]:
    prefix_root = f"strategies.{strategy_name}"
    expected_abort = (
        "shift_all_weight_to_baseline" if traffic_strategy == "canary" else "restore_baseline_slot"
    )
    errors = _reject_unknown(
        multi,
        {
            "kind",
            "method",
            "traffic_strategy",
            "region_order",
            "cluster_management",
            "waves",
            "deny_unmatched",
            "pin_exact_cluster_ids",
            "required_cluster_labels",
            "abort",
        },
        prefix_root,
    )
    if multi.get("kind") != "placement":
        errors.append(f"{prefix_root}.kind must be placement")
    if multi.get("traffic_strategy") != traffic_strategy:
        errors.append(f"{prefix_root}.traffic_strategy must be {traffic_strategy}")
    if multi.get("abort") != expected_abort:
        errors.append(f"{prefix_root}.abort must be {expected_abort}")
    if multi.get("deny_unmatched") is not True:
        errors.append(f"{prefix_root}.deny_unmatched must be true")
    if multi.get("pin_exact_cluster_ids") is not True:
        errors.append(f"{prefix_root}.pin_exact_cluster_ids must be true")
    if multi.get("required_cluster_labels") != {"traffic": "gateway"}:
        errors.append(f"{prefix_root}.required_cluster_labels must require traffic=gateway")
    if multi.get("cluster_management") != CLUSTER_MANAGEMENT:
        errors.append(f"{prefix_root}.cluster_management does not match the pinned tools")
    region_order = multi.get("region_order")
    if (
        not isinstance(region_order, list)
        or len(region_order) < 2
        or any(not isinstance(region, str) for region in region_order)
    ):
        errors.append(f"{prefix_root}.region_order must list at least two regions")
        region_order = []
    elif len(region_order) != len(set(region_order)):
        errors.append(f"{prefix_root}.region_order contains duplicate regions")
    else:
        for index, region in enumerate(region_order):
            region_error = _dns1123(region, f"{prefix_root}.region_order[{index}]")
            if region_error:
                errors.append(region_error)
    waves = multi.get("waves")
    if not isinstance(waves, list) or not waves:
        return errors + [f"{prefix_root}.waves must be a non-empty array"]
    names: set[str] = set()
    wave_environments: set[str] = set()
    wave_regions: list[str] = []
    catalog = [cluster for cluster in clusters if isinstance(cluster, dict)]
    for index, wave in enumerate(waves):
        prefix = f"{prefix_root}.waves[{index}]"
        if not isinstance(wave, dict):
            errors.append(f"{prefix} must be an object")
            continue
        errors.extend(_reject_unknown(wave, {"name", "selector", "strategy"}, prefix))
        name_error = _dns1123(wave.get("name"), f"{prefix}.name")
        if name_error:
            errors.append(name_error)
        elif wave["name"] in names:
            errors.append(f"duplicate wave name: {wave['name']}")
        else:
            names.add(wave["name"])
        if wave.get("strategy") != traffic_strategy:
            errors.append(f"{prefix}.strategy must be {traffic_strategy}")
        selector = wave.get("selector")
        if not isinstance(selector, dict) or not selector:
            errors.append(f"{prefix}.selector must be a non-empty object")
            continue
        if any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in selector.items()):
            errors.append(f"{prefix}.selector values must be non-empty strings")
            continue
        if "environment" not in selector:
            errors.append(f"{prefix}.selector must include environment")
        elif selector["environment"] not in environments:
            errors.append(f"{prefix}.selector.environment is not a promotion environment")
        else:
            wave_environments.add(selector["environment"])
        if "region" not in selector:
            errors.append(f"{prefix}.selector must include region")
        else:
            wave_regions.append(selector["region"])
            gateway = [
                cluster
                for cluster in catalog
                if _matches(cluster, selector) and _has_labels(cluster, {"traffic": "gateway"})
            ]
            if not gateway:
                errors.append(f"{prefix} selects no gateway clusters")
    if len(wave_environments) > 1:
        errors.append(f"{strategy_name} waves must target one environment")
    if region_order and wave_regions != region_order:
        errors.append(f"{strategy_name} waves must follow region_order")
    if len(set(wave_regions)) < 2:
        errors.append(f"{strategy_name} must span more than one region")
    return errors


def load_release_policy(
    strategy_path: Path,
    cluster_path: Path,
    promotion_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    strategies = _load_json(strategy_path)
    clusters = _load_json(cluster_path)
    promotion = load_promotion_policy(promotion_path)
    errors = validate_release_policy(strategies, clusters, promotion)
    if errors:
        raise ValueError("invalid progressive release policy: " + "; ".join(errors))
    return strategies, clusters, promotion


def _parse_ids(value: list[str] | str | None, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = list(value)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{label} must be cluster ids")
    if len(items) != len(set(items)):
        raise ValueError(f"{label} contains duplicate cluster ids")
    for item in items:
        if _dns1123(item, label):
            raise ValueError(f"{label} contains an invalid cluster id: {item}")
    return items


def _index_clusters(clusters: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cluster["id"]: cluster for cluster in clusters["clusters"]}


def _require_known(ids: list[str], indexed: dict[str, dict[str, Any]], label: str) -> None:
    unknown = [item for item in ids if item not in indexed]
    if unknown:
        raise ValueError(f"{label} references unknown clusters: {', '.join(unknown)}")


def _k8s_name(*parts: str) -> str:
    name = "-".join(parts)
    if _dns1123(name, "kubernetes name") or len(name) > 63:
        raise ValueError(f"kubernetes name is invalid: {name}")
    return name


def _new_canary_wave(
    *,
    name: str,
    cluster_ids: list[str],
    baseline: str,
    candidate: str,
    started: bool,
    region: str = "",
) -> dict[str, Any]:
    wave = {
        "name": name,
        "strategy": "canary",
        "method": "routing",
        "cluster_ids": cluster_ids,
        "started": started,
        "status": "planned",
        "step_index": -1,
        "step_name": "",
        "baseline_digest": baseline,
        "candidate_digest": candidate,
        "stable_digest": baseline,
        "canary_digest": candidate if started else "",
        "stable_weight": 100,
        "canary_weight": 0,
        "active_slot": "",
        "baseline_slot": "",
        "slot_digests": {"blue": "", "green": ""},
        "preview": False,
        "region": region,
    }
    return wave


def _new_blue_wave(
    *,
    name: str,
    cluster_ids: list[str],
    baseline: str,
    candidate: str,
    active_slot: str,
    region: str = "",
) -> dict[str, Any]:
    inactive = _inactive_slot(active_slot)
    slots = {"blue": "", "green": ""}
    slots[active_slot] = baseline
    slots[inactive] = ""
    return {
        "name": name,
        "strategy": "blue_green",
        "method": "routing",
        "cluster_ids": cluster_ids,
        "started": False,
        "status": "planned",
        "step_index": -1,
        "step_name": "",
        "baseline_digest": baseline,
        "candidate_digest": candidate,
        "stable_digest": baseline,
        "canary_digest": "",
        "stable_weight": 100,
        "canary_weight": 0,
        "active_slot": active_slot,
        "baseline_slot": active_slot,
        "slot_digests": slots,
        "preview": False,
        "region": region,
    }


def _open_wave(wave: dict[str, Any]) -> None:
    wave["started"] = True
    wave["step_index"] = -1
    wave["step_name"] = ""
    wave["status"] = "planned"
    wave["preview"] = False
    if wave["strategy"] == "canary":
        wave["stable_digest"] = wave["baseline_digest"]
        wave["canary_digest"] = wave["candidate_digest"]
        wave["stable_weight"] = 100
        wave["canary_weight"] = 0
        return
    if wave["strategy"] == "blue_green":
        inactive = _inactive_slot(wave["baseline_slot"])
        wave["active_slot"] = wave["baseline_slot"]
        wave["slot_digests"][wave["baseline_slot"]] = wave["baseline_digest"]
        wave["slot_digests"][inactive] = ""
        wave["stable_digest"] = wave["baseline_digest"]
        wave["canary_digest"] = ""
        wave["stable_weight"] = 100
        wave["canary_weight"] = 0


def plan_release(
    *,
    strategies: dict[str, Any],
    clusters: dict[str, Any],
    promotion: dict[str, Any],
    strategy: str,
    environment: str,
    service: str,
    identity: dict[str, str],
    baseline_digest: str,
    environment_pointer_digest: str,
    accept_excluded: bool = False,
    allow: list[str] | str | None = None,
    deny: list[str] | str | None = None,
    active_slot: str = "blue",
) -> dict[str, Any]:
    if strategy not in METHOD_DECISION:
        raise ValueError(f"unknown release strategy: {strategy}")
    if environment not in promotion["environments"]:
        raise ValueError(f"environment is not a promotion environment: {environment}")
    service_error = _dns1123(service, "service")
    if service_error:
        raise ValueError(service_error)
    baseline_error = _sha256(baseline_digest, "baseline_digest")
    if baseline_error:
        raise ValueError(baseline_error)
    pointer_error = _sha256(environment_pointer_digest, "environment_pointer_digest")
    if pointer_error:
        raise ValueError(pointer_error)
    if environment_pointer_digest != identity["bundle_sha256"]:
        raise ValueError("candidate digest must match the environment pointer")
    if baseline_digest == identity["bundle_sha256"]:
        raise ValueError("candidate digest is already the baseline serving digest")
    if active_slot not in ("blue", "green"):
        raise ValueError("active slot must be blue or green")

    indexed = _index_clusters(clusters)
    allow_ids = _parse_ids(allow, "allow")
    deny_ids = _parse_ids(deny, "deny")
    _require_known(allow_ids, indexed, "allow")
    _require_known(deny_ids, indexed, "deny")
    outside_allow = [item for item in allow_ids if indexed[item]["environment"] != environment]
    if outside_allow:
        raise ValueError(
            "allowlist includes clusters outside the target environment: " + ", ".join(outside_allow)
        )
    outside_deny = [item for item in deny_ids if indexed[item]["environment"] != environment]
    if outside_deny:
        raise ValueError(
            "denylist includes clusters outside the target environment: " + ", ".join(outside_deny)
        )

    candidate = identity["bundle_sha256"]
    region_order: list[str] = []
    if strategy in MEMBERSHIP_STRATEGIES:
        waves, unselected = _plan_region_placement(
            strategies=strategies,
            indexed=indexed,
            strategy=strategy,
            environment=environment,
            allow_ids=allow_ids,
            deny_ids=deny_ids,
            baseline=baseline_digest,
            candidate=candidate,
            active_slot=active_slot,
        )
        region_order = [wave["region"] for wave in waves]
        excluded: list[dict[str, str]] = []
    else:
        waves, excluded = _plan_traffic(
            strategies=strategies,
            indexed=indexed,
            strategy=strategy,
            environment=environment,
            allow_ids=allow_ids,
            deny_ids=deny_ids,
            baseline=baseline_digest,
            candidate=candidate,
            active_slot=active_slot,
            accept_excluded=accept_excluded,
        )
        unselected = [
            {"id": cluster["id"], "reason": "outside_environment"}
            for cluster in clusters["clusters"]
            if cluster["environment"] != environment
        ]

    for wave in waves:
        _k8s_name(service, wave["name"])
        for cluster_id in wave["cluster_ids"]:
            _k8s_name(service, cluster_id, "route")

    if waves:
        _open_wave(waves[0])

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy,
        "method": METHOD_DECISION[strategy]["method"],
        "tool": METHOD_DECISION[strategy]["tool"],
        "environment": environment,
        "service": service,
        "status": "planned",
        "identity": dict(identity),
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate,
        "accept_excluded": accept_excluded,
        "allow": allow_ids,
        "deny": deny_ids,
        "excluded": excluded,
        "unselected": unselected,
        "region_order": region_order,
        "active_slot": active_slot,
        "waves": waves,
        "history": [{"action": "plan", "strategy": strategy}],
    }


def _plan_traffic(
    *,
    strategies: dict[str, Any],
    indexed: dict[str, dict[str, Any]],
    strategy: str,
    environment: str,
    allow_ids: list[str],
    deny_ids: list[str],
    baseline: str,
    candidate: str,
    active_slot: str,
    accept_excluded: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    required = strategies["strategies"][strategy]["required_cluster_labels"]
    selected: list[str] = []
    excluded: list[dict[str, str]] = []
    allow_set = set(allow_ids)
    deny_set = set(deny_ids)
    for cluster_id in sorted(indexed):
        cluster = indexed[cluster_id]
        if cluster["environment"] != environment:
            continue
        if cluster_id in deny_set:
            excluded.append({"id": cluster_id, "reason": "denied"})
            continue
        if allow_ids and cluster_id not in allow_set:
            excluded.append({"id": cluster_id, "reason": "not_in_allow"})
            continue
        if not _has_labels(cluster, required):
            excluded.append({"id": cluster_id, "reason": "missing_traffic_capability"})
            continue
        selected.append(cluster_id)
    if allow_ids:
        missing = [item for item in allow_ids if item not in selected]
        if missing:
            raise ValueError("allowlist clusters were not selected: " + ", ".join(missing))
    if excluded and not accept_excluded:
        rendered = ", ".join(f"{item['id']} ({item['reason']})" for item in excluded)
        raise ValueError("excluded clusters require accept_excluded: " + rendered)
    if not selected:
        raise ValueError("traffic strategy selected no clusters")
    if strategy == "canary":
        wave = _new_canary_wave(
            name="environment-traffic",
            cluster_ids=selected,
            baseline=baseline,
            candidate=candidate,
            started=False,
        )
    else:
        wave = _new_blue_wave(
            name="environment-traffic",
            cluster_ids=selected,
            baseline=baseline,
            candidate=candidate,
            active_slot=active_slot,
        )
    return [wave], excluded


def _explicit_non_gateway(
    allow_ids: list[str],
    indexed: dict[str, dict[str, Any]],
    environment: str,
) -> None:
    lacking = [
        cluster_id
        for cluster_id in allow_ids
        if indexed[cluster_id]["environment"] == environment
        and not _has_labels(indexed[cluster_id], {"traffic": "gateway"})
    ]
    if lacking:
        raise ValueError(
            "explicitly targeted clusters lack gateway traffic and stay off the pin: "
            + ", ".join(lacking)
        )


def _plan_region_placement(
    *,
    strategies: dict[str, Any],
    indexed: dict[str, dict[str, Any]],
    strategy: str,
    environment: str,
    allow_ids: list[str],
    deny_ids: list[str],
    baseline: str,
    candidate: str,
    active_slot: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    traffic = MEMBERSHIP_TRAFFIC[strategy]
    multi = strategies["strategies"][strategy]
    if multi.get("traffic_strategy") != traffic:
        raise ValueError(f"{strategy} traffic strategy must be {traffic}")
    _explicit_non_gateway(allow_ids, indexed, environment)
    wave_environments = {wave["selector"]["environment"] for wave in multi["waves"]}
    if wave_environments != {environment}:
        raise ValueError(
            f"{strategy} waves target "
            + ", ".join(sorted(wave_environments))
            + f", release environment is {environment}"
        )
    assigned: set[str] = set()
    waves: list[dict[str, Any]] = []
    allow_set = set(allow_ids)
    deny_set = set(deny_ids)
    required = strategies["strategies"][traffic]["required_cluster_labels"]
    for wave in multi["waves"]:
        if wave["strategy"] != traffic:
            raise ValueError(f"wave {wave['name']} must use {traffic}, not {wave['strategy']}")
        region = wave["selector"].get("region")
        if not isinstance(region, str) or not region:
            raise ValueError(f"wave {wave['name']} must select one region")
        matched = [
            cluster_id
            for cluster_id, cluster in sorted(indexed.items())
            if _matches(cluster, wave["selector"]) and cluster_id not in assigned and cluster_id not in deny_set
        ]
        if allow_ids:
            matched = [cluster_id for cluster_id in matched if cluster_id in allow_set]
        incapable = [
            cluster_id
            for cluster_id in matched
            if not _has_labels(indexed[cluster_id], required)
        ]
        if incapable:
            raise ValueError(
                f"wave {wave['name']} includes clusters without gateway traffic capability: "
                + ", ".join(incapable)
            )
        if not matched:
            raise ValueError(f"empty wave: {wave['name']}")
        assigned.update(matched)
        if traffic == "canary":
            built = _new_canary_wave(
                name=wave["name"],
                cluster_ids=matched,
                baseline=baseline,
                candidate=candidate,
                started=False,
                region=region,
            )
        else:
            built = _new_blue_wave(
                name=wave["name"],
                cluster_ids=matched,
                baseline=baseline,
                candidate=candidate,
                active_slot=active_slot,
                region=region,
            )
        waves.append(built)
    regions = [wave["region"] for wave in waves]
    if regions != list(multi["region_order"]) or len(set(regions)) < 2:
        raise ValueError(f"{strategy} must select gateway clusters in more than one region")
    if allow_ids:
        missing = [item for item in allow_ids if item not in assigned]
        if missing:
            raise ValueError("allowlist clusters were not selected by any wave: " + ", ".join(missing))
    unselected = [
        {"id": cluster_id, "reason": "not_in_wave"}
        for cluster_id in sorted(indexed)
        if cluster_id not in assigned
    ]
    return waves, unselected


def evaluate_analysis(required: list[str], evidence: dict[str, Any], thresholds: dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("analysis evidence must be an object")
    for check in required:
        if check == "smoke":
            if evidence.get("smoke") != "pass":
                raise ValueError("analysis smoke must be pass")
        elif check == "readiness":
            if evidence.get("readiness") != "pass":
                raise ValueError("analysis readiness must be pass")
        elif check in ("error_rate", "latency_p95"):
            requests = evidence.get("requests")
            if isinstance(requests, bool) or not isinstance(requests, int) or requests < thresholds["min_requests"]:
                raise ValueError("analysis requests below min_requests")
            if check == "error_rate":
                rate = evidence.get("error_rate")
                if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                    raise ValueError("analysis error_rate is missing")
                if float(rate) > float(thresholds["error_rate_max"]):
                    raise ValueError("analysis error_rate exceeds threshold")
            else:
                latency = evidence.get("latency_p95_ms")
                if isinstance(latency, bool) or not isinstance(latency, (int, float)):
                    raise ValueError("analysis latency_p95_ms is missing")
                if float(latency) > float(thresholds["latency_p95_max_ms"]):
                    raise ValueError("analysis latency_p95 exceeds threshold")
        else:
            raise ValueError(f"unknown analysis check: {check}")


def _current_wave(state: dict[str, Any]) -> dict[str, Any] | None:
    for wave in state["waves"]:
        if wave["status"] != "completed":
            return wave
    return None


def _apply_next_step(wave: dict[str, Any], evidence: dict[str, Any], strategies: dict[str, Any]) -> None:
    if not wave["started"]:
        raise ValueError(f"wave {wave['name']} is not open")
    body = strategies["strategies"][wave["strategy"]]
    steps = body["steps"]
    next_index = wave["step_index"] + 1
    if next_index >= len(steps):
        raise ValueError(f"wave {wave['name']} has no further steps")
    step = steps[next_index]
    evaluate_analysis(step["analysis"], evidence, body["analysis_thresholds"])
    wave["step_index"] = next_index
    wave["step_name"] = step["name"]
    wave["status"] = "in_progress"
    if wave["strategy"] == "canary":
        weight = step["canary_weight"]
        wave["canary_weight"] = weight
        wave["stable_weight"] = 100 - weight
        wave["canary_digest"] = wave["candidate_digest"]
        wave["stable_digest"] = wave["baseline_digest"]
        if next_index == len(steps) - 1:
            wave["stable_digest"] = wave["candidate_digest"]
            wave["stable_weight"] = 100
            wave["canary_weight"] = 0
            wave["canary_digest"] = ""
            wave["status"] = "completed"
        return

    traffic = step["traffic"]
    inactive = _inactive_slot(wave["baseline_slot"])
    if traffic == "hold":
        wave["slot_digests"][inactive] = wave["candidate_digest"]
        wave["active_slot"] = wave["baseline_slot"]
        wave["preview"] = False
    elif traffic == "preview":
        wave["preview"] = True
        wave["active_slot"] = wave["baseline_slot"]
    elif traffic == "switch":
        wave["preview"] = False
        wave["active_slot"] = inactive
    elif traffic == "active":
        wave["preview"] = False
        wave["active_slot"] = inactive
        wave["status"] = "completed"
    else:
        raise ValueError(f"unknown blue-green traffic action: {traffic}")
    serving = wave["slot_digests"][wave["active_slot"]]
    wave["stable_digest"] = serving
    wave["stable_weight"] = 100
    wave["canary_weight"] = 0
    wave["canary_digest"] = ""


def advance_release(
    state: dict[str, Any],
    evidence: dict[str, Any],
    strategies: dict[str, Any],
) -> dict[str, Any]:
    if state["status"] == "completed":
        raise ValueError("completed release has no further steps")
    if state["status"] == "aborted":
        raise ValueError("aborted release cannot advance")
    nxt = copy.deepcopy(state)
    wave = _current_wave(nxt)
    if wave is None:
        raise ValueError("release has no open wave")
    if not wave["started"]:
        _open_wave(wave)
    _apply_next_step(wave, evidence, strategies)
    nxt["history"].append(
        {
            "action": "advance",
            "wave": wave["name"],
            "step": wave["step_name"],
            "status": wave["status"],
        }
    )
    if wave["status"] == "completed":
        opened = False
        for candidate in nxt["waves"]:
            if candidate["status"] != "completed" and not candidate["started"]:
                _open_wave(candidate)
                nxt["history"].append({"action": "open_wave", "wave": candidate["name"]})
                opened = True
                break
        nxt["status"] = "in_progress" if opened else "completed"
    else:
        nxt["status"] = "in_progress"
    return nxt


def _reset_wave(wave: dict[str, Any]) -> None:
    baseline = wave["baseline_digest"]
    wave["stable_digest"] = baseline
    wave["canary_digest"] = ""
    wave["stable_weight"] = 100
    wave["canary_weight"] = 0
    wave["preview"] = False
    if wave["strategy"] == "blue_green":
        inactive = _inactive_slot(wave["baseline_slot"])
        wave["active_slot"] = wave["baseline_slot"]
        wave["slot_digests"][wave["baseline_slot"]] = baseline
        wave["slot_digests"][inactive] = ""
    wave["status"] = "aborted"


def abort_release(state: dict[str, Any]) -> dict[str, Any]:
    if state["status"] == "completed":
        raise ValueError("completed release cannot be aborted; use environment rollback")
    if state["status"] == "aborted":
        raise ValueError("release is already aborted")
    nxt = copy.deepcopy(state)
    for wave in nxt["waves"]:
        if wave["started"]:
            _reset_wave(wave)
    nxt["status"] = "aborted"
    nxt["history"].append({"action": "abort"})
    return nxt


def passing_analysis(strategies: dict[str, Any]) -> dict[str, Any]:
    minimum = 1
    for name in ("canary", "blue_green"):
        threshold = strategies["strategies"][name]["analysis_thresholds"]["min_requests"]
        minimum = max(minimum, int(threshold))
    return {
        "smoke": "pass",
        "readiness": "pass",
        "error_rate": 0.0,
        "latency_p95_ms": 1,
        "requests": minimum,
    }


def run_scenario(
    *,
    strategies: dict[str, Any],
    clusters: dict[str, Any],
    promotion: dict[str, Any],
    strategy: str,
    environment: str,
    service: str,
    identity: dict[str, str],
    baseline_digest: str,
    environment_pointer_digest: str,
    accept_excluded: bool = False,
    allow: list[str] | str | None = None,
    deny: list[str] | str | None = None,
    active_slot: str = "blue",
) -> dict[str, Any]:
    state = plan_release(
        strategies=strategies,
        clusters=clusters,
        promotion=promotion,
        strategy=strategy,
        environment=environment,
        service=service,
        identity=identity,
        baseline_digest=baseline_digest,
        environment_pointer_digest=environment_pointer_digest,
        accept_excluded=accept_excluded,
        allow=allow,
        deny=deny,
        active_slot=active_slot,
    )
    evidence = passing_analysis(strategies)
    for _ in range(100):
        if state["status"] == "completed":
            return state
        state = advance_release(state, evidence, strategies)
    raise ValueError("scenario did not complete")


def project_release(state: dict[str, Any], clusters: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_wave: dict[str, dict[str, Any]] = {}
    for wave in state["waves"]:
        if not wave["started"]:
            continue
        for cluster_id in wave["cluster_ids"]:
            by_wave[cluster_id] = wave
    projected: dict[str, dict[str, Any]] = {}
    for cluster in clusters["clusters"]:
        cluster_id = cluster["id"]
        wave = by_wave.get(cluster_id)
        if wave is None:
            projected[cluster_id] = _baseline_view(state["baseline_digest"])
            continue
        projected[cluster_id] = _wave_view(wave)
    return projected


def _baseline_view(baseline: str) -> dict[str, Any]:
    return {
        "targeted": False,
        "wave": "",
        "serving_digests": [baseline],
        "stable_weight": 100,
        "canary_weight": 0,
        "stable_digest": baseline,
        "canary_digest": "",
        "active_slot": "",
        "preview_digest": "",
    }


def _wave_view(wave: dict[str, Any]) -> dict[str, Any]:
    if wave["strategy"] == "canary":
        serving: list[str] = []
        if wave["stable_weight"] > 0 and wave["stable_digest"]:
            serving.append(wave["stable_digest"])
        if wave["canary_weight"] > 0 and wave["canary_digest"]:
            serving.append(wave["canary_digest"])
        return {
            "targeted": True,
            "wave": wave["name"],
            "serving_digests": serving,
            "stable_weight": wave["stable_weight"],
            "canary_weight": wave["canary_weight"],
            "stable_digest": wave["stable_digest"],
            "canary_digest": wave["canary_digest"],
            "active_slot": "",
            "preview_digest": "",
        }
    active = wave["active_slot"]
    serving_digest = wave["slot_digests"][active]
    inactive = _inactive_slot(wave["baseline_slot"])
    preview = wave["slot_digests"][inactive] if wave["preview"] else ""
    return {
        "targeted": True,
        "wave": wave["name"],
        "serving_digests": [serving_digest] if serving_digest else [],
        "stable_weight": 100,
        "canary_weight": 0,
        "stable_digest": serving_digest,
        "canary_digest": "",
        "active_slot": active,
        "preview_digest": preview,
    }


def _annotations(state: dict[str, Any], wave: dict[str, Any], evidence_mode: str) -> dict[str, str]:
    return {
        "cicd.platform/release-digest": state["candidate_digest"],
        "cicd.platform/baseline-digest": state["baseline_digest"],
        "cicd.platform/strategy": state["strategy"],
        "cicd.platform/method": "cluster_management" if state["strategy"] in MEMBERSHIP_STRATEGIES else wave["method"],
        "cicd.platform/traffic-strategy": wave["strategy"],
        "cicd.platform/wave": wave["name"],
        "cicd.platform/region": wave.get("region", ""),
        "cicd.platform/clusters": ",".join(wave["cluster_ids"]),
        "cicd.platform/evidence": evidence_mode,
    }


def render_documents(state: dict[str, Any], strategies: dict[str, Any], evidence_mode: str) -> list[tuple[str, dict[str, Any]]]:
    if evidence_mode not in ("unverified", "operator", "synthetic"):
        raise ValueError("evidence_mode must be unverified, operator, or synthetic")
    namespace = strategies["namespace"]
    documents: list[tuple[str, dict[str, Any]]] = []
    for wave in state["waves"]:
        if not wave["started"]:
            continue
        annotations = _annotations(state, wave, evidence_mode)
        pin_name = _k8s_name(state["service"], wave["name"], "pin")
        documents.append(
            (
                f"{pin_name}.json",
                {
                    "apiVersion": "cicd.platform/v1",
                    "kind": "ClusterPin",
                    "metadata": {"name": pin_name, "namespace": namespace, "annotations": annotations},
                    "spec": {
                        "service": state["service"],
                        "environment": state["environment"],
                        "wave": wave["name"],
                        "region": wave.get("region", ""),
                        "strategy": state["strategy"],
                        "trafficStrategy": wave["strategy"],
                        "method": "cluster_management" if state["strategy"] in MEMBERSHIP_STRATEGIES else "routing",
                        "clusterIds": list(wave["cluster_ids"]),
                        "candidateDigest": state["candidate_digest"],
                        "baselineDigest": state["baseline_digest"],
                    },
                },
            )
        )
        placement_name = _k8s_name(state["service"], wave["name"], "placement")
        documents.append(
            (
                f"{placement_name}.json",
                {
                    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                    "kind": "Placement",
                    "metadata": {"name": placement_name, "namespace": namespace, "annotations": annotations},
                    "spec": {
                        "numberOfClusters": len(wave["cluster_ids"]),
                        "predicates": [
                            {
                                "requiredClusterSelector": {
                                    "labelSelector": {
                                        "matchExpressions": [
                                            {
                                                "key": "cicd.platform/cluster-id",
                                                "operator": "In",
                                                "values": list(wave["cluster_ids"]),
                                            }
                                        ]
                                    }
                                }
                            }
                        ],
                    },
                },
            )
        )
        decision_name = _k8s_name(state["service"], wave["name"], "decision")
        documents.append(
            (
                f"{decision_name}.json",
                {
                    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                    "kind": "PlacementDecision",
                    "metadata": {
                        "name": decision_name,
                        "namespace": namespace,
                        "annotations": annotations,
                        "labels": {"cluster.open-cluster-management.io/placement": placement_name},
                    },
                    "status": {"decisions": [{"clusterName": cluster_id} for cluster_id in wave["cluster_ids"]]},
                },
            )
        )
        app_name = _k8s_name(state["service"], wave["name"], "appset")
        elements = [_application_element(state, wave, cluster_id) for cluster_id in wave["cluster_ids"]]
        documents.append(
            (
                f"{app_name}.json",
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": app_name, "namespace": namespace, "annotations": annotations},
                    "spec": {
                        "generators": [{"list": {"elements": elements}}],
                        "template": {
                            "metadata": {"name": state["service"] + "-{{cluster}}"},
                            "spec": {
                                "project": "default",
                                "destination": {"name": "{{cluster}}", "namespace": namespace},
                                "source": {
                                    "plugin": {
                                        "name": "cicd-release-v1",
                                        "parameters": [
                                            {"name": "stableDigest", "string": "{{stableDigest}}"},
                                            {"name": "canaryDigest", "string": "{{canaryDigest}}"},
                                            {"name": "stableWeight", "string": "{{stableWeight}}"},
                                            {"name": "canaryWeight", "string": "{{canaryWeight}}"},
                                            {"name": "activeSlot", "string": "{{activeSlot}}"},
                                            {"name": "blueDigest", "string": "{{blueDigest}}"},
                                            {"name": "greenDigest", "string": "{{greenDigest}}"},
                                            {"name": "previewDigest", "string": "{{previewDigest}}"},
                                        ],
                                    }
                                },
                            },
                        },
                    },
                },
            )
        )
        for cluster_id in wave["cluster_ids"]:
            route_name = _k8s_name(state["service"], cluster_id, "route")
            documents.append((f"{route_name}.json", _http_route(state, strategies, wave, cluster_id, route_name, annotations)))
            if wave["preview"]:
                preview_name = _k8s_name(state["service"], cluster_id, "preview")
                documents.append(
                    (
                        f"{preview_name}.json",
                        _preview_route(state, strategies, wave, cluster_id, preview_name, annotations),
                    )
                )
    return documents


def _application_element(state: dict[str, Any], wave: dict[str, Any], cluster_id: str) -> dict[str, str]:
    if wave["strategy"] == "canary":
        return {
            "cluster": cluster_id,
            "stableDigest": wave["stable_digest"],
            "canaryDigest": wave["canary_digest"],
            "stableWeight": str(wave["stable_weight"]),
            "canaryWeight": str(wave["canary_weight"]),
            "activeSlot": "",
            "blueDigest": "",
            "greenDigest": "",
            "previewDigest": "",
        }
    inactive = _inactive_slot(wave["baseline_slot"])
    preview = wave["slot_digests"][inactive] if wave["preview"] else ""
    return {
        "cluster": cluster_id,
        "stableDigest": wave["slot_digests"][wave["active_slot"]],
        "canaryDigest": "",
        "stableWeight": "100",
        "canaryWeight": "0",
        "activeSlot": wave["active_slot"],
        "blueDigest": wave["slot_digests"]["blue"],
        "greenDigest": wave["slot_digests"]["green"],
        "previewDigest": preview,
    }


def _http_route(
    state: dict[str, Any],
    strategies: dict[str, Any],
    wave: dict[str, Any],
    cluster_id: str,
    route_name: str,
    annotations: dict[str, str],
) -> dict[str, Any]:
    port = strategies["service_port"]
    if wave["strategy"] == "canary":
        backends = [
            {"name": f"{state['service']}-stable", "port": port, "weight": wave["stable_weight"]},
        ]
        if wave["canary_digest"] or wave["canary_weight"] > 0:
            backends.append(
                {"name": f"{state['service']}-canary", "port": port, "weight": wave["canary_weight"]}
            )
    else:
        backends = [
            {
                "name": f"{state['service']}-{wave['active_slot']}",
                "port": port,
                "weight": 100,
            }
        ]
    route_annotations = dict(annotations)
    route_annotations["cicd.platform/cluster"] = cluster_id
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": route_name,
            "namespace": strategies["namespace"],
            "annotations": route_annotations,
            "labels": {"cicd.platform/cluster-id": cluster_id},
        },
        "spec": {
            "parentRefs": [{"name": strategies["gateway_name"], "namespace": strategies["namespace"]}],
            "rules": [{"backendRefs": backends}],
        },
    }


def _preview_route(
    state: dict[str, Any],
    strategies: dict[str, Any],
    wave: dict[str, Any],
    cluster_id: str,
    route_name: str,
    annotations: dict[str, str],
) -> dict[str, Any]:
    inactive = _inactive_slot(wave["baseline_slot"])
    route_annotations = dict(annotations)
    route_annotations["cicd.platform/cluster"] = cluster_id
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": route_name,
            "namespace": strategies["namespace"],
            "annotations": route_annotations,
            "labels": {"cicd.platform/cluster-id": cluster_id},
        },
        "spec": {
            "parentRefs": [{"name": strategies["gateway_name"], "namespace": strategies["namespace"]}],
            "rules": [
                {
                    "matches": [
                        {
                            "headers": [
                                {
                                    "name": strategies["preview_header"],
                                    "value": strategies["preview_header_value"],
                                }
                            ]
                        }
                    ],
                    "backendRefs": [
                        {
                            "name": f"{state['service']}-{inactive}",
                            "port": strategies["service_port"],
                            "weight": 100,
                        }
                    ],
                }
            ],
        },
    }


def check_pointer(pointer: dict[str, Any], *, bundle_sha256: str, environment: str) -> None:
    if not isinstance(pointer, dict):
        raise ValueError("environment pointer must be an object")
    digest_error = _sha256(bundle_sha256, "bundle_sha256")
    if digest_error:
        raise ValueError(digest_error)
    pointer_error = _sha256(pointer.get("bundle_sha256"), "pointer bundle_sha256")
    if pointer_error:
        raise ValueError(pointer_error)
    if not isinstance(environment, str) or not environment:
        raise ValueError("environment is required")
    if pointer.get("environment") != environment:
        raise ValueError("pointer environment does not match the release environment")
    if pointer.get("bundle_sha256") != bundle_sha256:
        raise ValueError("environment pointer digest does not match release candidate")


def _write_documents(documents: list[tuple[str, dict[str, Any]]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in documents:
        (out_dir / filename).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _emit(result: dict[str, Any]) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            status = result.get("status")
            if isinstance(status, str):
                handle.write(f"status={status}\n")
            strategy = result.get("strategy")
            if isinstance(strategy, str):
                handle.write(f"strategy={strategy}\n")
    print(json.dumps({key: result[key] for key in ("status", "strategy", "method", "environment", "service") if key in result}, indent=2))


def _identity_from_args(args: argparse.Namespace) -> dict[str, str]:
    return normalize_identity(
        artifact_name=args.artifact_name,
        bundle_sha256=args.bundle_sha256,
        source_sha=args.source_sha,
        source_run_id=args.source_run_id,
        release_tag=args.release_tag,
    )


def _add_release_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", required=True, choices=tuple(METHOD_DECISION))
    parser.add_argument("--environment", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--baseline-digest", required=True)
    parser.add_argument("--environment-pointer-digest", required=True)
    parser.add_argument("--accept-excluded", action="store_true")
    parser.add_argument("--allow", default="")
    parser.add_argument("--deny", default="")
    parser.add_argument("--active-slot", default="blue", choices=("blue", "green"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/release-strategies.json")
    parser.add_argument("--clusters", default="ci/clusters.json")
    parser.add_argument("--promotion-policy", default="ci/promotion-policy.json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")

    plan_parser = subparsers.add_parser("plan")
    _add_release_args(plan_parser)
    plan_parser.add_argument("--out", required=True)
    plan_parser.add_argument("--out-dir", default="")

    scenario_parser = subparsers.add_parser("scenario")
    _add_release_args(scenario_parser)
    scenario_parser.add_argument("--out", required=True)
    scenario_parser.add_argument("--out-dir", required=True)

    advance_parser = subparsers.add_parser("advance")
    advance_parser.add_argument("--state", required=True)
    advance_parser.add_argument("--analysis", required=True)
    advance_parser.add_argument("--out", required=True)
    advance_parser.add_argument("--out-dir", default="")

    abort_parser = subparsers.add_parser("abort")
    abort_parser.add_argument("--state", required=True)
    abort_parser.add_argument("--out", required=True)
    abort_parser.add_argument("--out-dir", default="")

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--state", required=True)
    render_parser.add_argument("--out-dir", required=True)
    render_parser.add_argument("--evidence-mode", default="operator", choices=("unverified", "operator", "synthetic"))

    pointer_parser = subparsers.add_parser("check-pointer")
    pointer_parser.add_argument("--pointer-json", required=True)
    pointer_parser.add_argument("--bundle-sha256", required=True)
    pointer_parser.add_argument("--environment", required=True)

    args = parser.parse_args()
    try:
        strategies, clusters, promotion = load_release_policy(
            Path(args.policy),
            Path(args.clusters),
            Path(args.promotion_policy),
        )
        if args.command == "validate":
            print(
                "OK: progressive release policy validated "
                f"({len(clusters['clusters'])} clusters, strategies {'/'.join(METHOD_DECISION)})"
            )
            return 0
        if args.command == "check-pointer":
            pointer = _load_json(Path(args.pointer_json))
            check_pointer(pointer, bundle_sha256=args.bundle_sha256, environment=args.environment)
            print("OK: environment pointer matches release candidate")
            return 0
        if args.command in ("plan", "scenario"):
            common = dict(
                strategies=strategies,
                clusters=clusters,
                promotion=promotion,
                strategy=args.strategy,
                environment=args.environment,
                service=args.service,
                identity=_identity_from_args(args),
                baseline_digest=args.baseline_digest,
                environment_pointer_digest=args.environment_pointer_digest,
                accept_excluded=args.accept_excluded,
                allow=args.allow,
                deny=args.deny,
                active_slot=args.active_slot,
            )
            if args.command == "plan":
                state = plan_release(**common)
                evidence_mode = "unverified"
            else:
                state = run_scenario(**common)
                evidence_mode = "synthetic"
        else:
            state = _load_json(Path(args.state))
            if args.command == "advance":
                evidence = _load_json(Path(args.analysis))
                state = advance_release(state, evidence, strategies)
                evidence_mode = "operator"
            elif args.command == "abort":
                state = abort_release(state)
                evidence_mode = "operator"
            else:
                evidence_mode = args.evidence_mode
        if args.command != "render":
            Path(args.out).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out_dir = getattr(args, "out_dir", "")
        if out_dir:
            _write_documents(render_documents(state, strategies, evidence_mode), Path(out_dir))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.command != "render":
        _emit(state)
    else:
        print(f"OK: rendered release manifests to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
