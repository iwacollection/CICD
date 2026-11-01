#!/usr/bin/env python3
"""Render controller-driven progressive delivery for Argo Rollouts.

`release_strategy.py` is an operator-driven engine: a person (or a caller)
feeds analysis evidence into `advance` one step at a time. This module is the
controller-driven counterpart. It reads the same `ci/release-strategies.json`
(weights, checks, thresholds, region order) and renders the manifests that let
Argo Rollouts walk those steps by itself:

* canary  -> Rollout `strategy.canary`: setWeight 1, 5, 25, 50, 100, each
  followed by an inline analysis step, then the confirm analysis. A failed
  AnalysisRun aborts the rollout and the router goes back to the stable
  backend. Weights are moved by the Gateway API plugin (or Istio).
* blue_green -> Rollout `strategy.blueGreen`: smoke runs as
  `prePromotionAnalysis` against the preview Service, error_rate and
  latency_p95 run as `postPromotionAnalysis` against the active Service, and
  `scaleDownDelaySeconds` keeps the previous ReplicaSet alive long enough for
  an abort to switch back.
* multi_cluster_* -> the same Rollout in every pinned cluster, plus one
  ApplicationSet with `RollingSync`. Argo CD only opens the next region after
  every Application of the previous region is Healthy, and a Rollout is only
  Healthy after its last analysis passed. An aborted or paused rollout keeps
  the later regions closed.

Policy stays single-sourced: thresholds in every success/failure condition come
from `ci/release-strategies.json`. `ci/argo-rollouts.json` only says how a check
is measured and how long it runs.

This module renders desired state. It does not build artifacts, does not apply
manifests, and never holds a kubeconfig. The image must be pinned by digest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MANIFEST_NAME = "argo-render-manifest.json"
ROLLBACK_MARGIN_SECONDS = 60
HOST_ARG = "{{args.host}}"
GATEWAY_PLUGIN = "argoproj-labs/gatewayAPI"
ISTIO_API = "networking.istio.io/v1"
GATEWAY_API = "gateway.networking.k8s.io/v1"

DNS1123_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
MAX_UPDATE_RE = re.compile(r"^(100|[1-9][0-9]?)%$|^[0-9]+$")
IMAGE_DIGEST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(:[0-9]{1,5})?(/[a-z0-9]+([._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$"
)

TRAFFIC_ROUTERS = ("gateway_api", "istio")
ANALYSIS_PROVIDER = {
    "smoke": "web",
    "readiness": "native",
    "error_rate": "prometheus",
    "latency_p95": "prometheus",
}
TRAFFIC_STRATEGY = {
    "canary": "canary",
    "blue_green": "blue_green",
    "multi_cluster_canary": "canary",
    "multi_cluster_blue_green": "blue_green",
}
MULTI_CLUSTER = frozenset({"multi_cluster_canary", "multi_cluster_blue_green"})
TEMPLATE_SUFFIX = {
    "smoke": "smoke",
    "error_rate": "error-rate",
    "latency_p95": "latency-p95",
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "description",
    "controller",
    "workload",
    "metrics",
    "analysis",
    "blue_green",
    "progressive_sync",
    "gitops",
}


def _reject_unknown(payload: dict, allowed: set[str], prefix: str) -> list[str]:
    unknown = sorted(set(payload) - allowed)
    if not unknown:
        return []
    return [f"{prefix} has unknown fields: {', '.join(unknown)}"]


def _is_int(value: object, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _dns1123(value: object) -> bool:
    return isinstance(value, str) and bool(DNS1123_RE.fullmatch(value))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} root must be an object")
    return payload


def bake_seconds(timing: dict[str, Any]) -> int:
    """Upper bound for how long one metric keeps measuring."""
    return int(timing.get("initial_delay_seconds", 0)) + int(timing["interval_seconds"]) * int(timing["count"])


def _section(payload: dict, key: str, errors: list[str]) -> dict | None:
    value = payload.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object")
        return None
    return value


def _validate_timing(timing: object, prefix: str, *, extra: set[str], with_failure_limit: bool) -> list[str]:
    if not isinstance(timing, dict):
        return [f"{prefix} must be an object"]
    allowed = {"initial_delay_seconds", "interval_seconds", "count"} | extra
    if with_failure_limit:
        allowed.add("failure_limit")
    errors = _reject_unknown(timing, allowed, prefix)
    if "initial_delay_seconds" in timing and not _is_int(timing["initial_delay_seconds"], 0):
        errors.append(f"{prefix}.initial_delay_seconds must be an integer of at least 0")
    if not _is_int(timing.get("interval_seconds"), 1):
        errors.append(f"{prefix}.interval_seconds must be an integer of at least 1")
    if not _is_int(timing.get("count"), 1):
        errors.append(f"{prefix}.count must be an integer of at least 1")
    if with_failure_limit:
        limit = timing.get("failure_limit")
        if not _is_int(limit, 0):
            errors.append(f"{prefix}.failure_limit must be an integer of at least 0")
        elif _is_int(timing.get("count"), 1) and limit >= timing["count"]:
            errors.append(f"{prefix}.failure_limit must be lower than count, otherwise the check can never fail")
    return errors


def validate_argo_policy(argo: dict[str, Any], strategies: dict[str, Any]) -> list[str]:
    errors = _reject_unknown(argo, TOP_LEVEL_KEYS, "argo rollouts policy")
    if argo.get("schema_version") != SCHEMA_VERSION:
        errors.append("argo rollouts schema_version must be 1")

    controller = _section(argo, "controller", errors)
    if controller is not None:
        errors.extend(
            _reject_unknown(
                controller,
                {"traffic_router", "abort_scale_down_delay_seconds", "revision_history_limit"},
                "controller",
            )
        )
        if controller.get("traffic_router") not in TRAFFIC_ROUTERS:
            errors.append(f"controller.traffic_router must be one of {list(TRAFFIC_ROUTERS)}")
        if not _is_int(controller.get("abort_scale_down_delay_seconds"), 0):
            errors.append("controller.abort_scale_down_delay_seconds must be an integer of at least 0")
        if not _is_int(controller.get("revision_history_limit"), 1):
            errors.append("controller.revision_history_limit must be an integer of at least 1")

    workload = _section(argo, "workload", errors)
    if workload is not None:
        errors.extend(
            _reject_unknown(workload, {"replicas", "container_port", "port_name", "readiness_path"}, "workload")
        )
        if not _is_int(workload.get("replicas"), 1):
            errors.append("workload.replicas must be an integer of at least 1")
        port = workload.get("container_port")
        if not _is_int(port, 1) or port > 65535:
            errors.append("workload.container_port must be an integer from 1 to 65535")
        name = workload.get("port_name")
        if not _dns1123(name) or len(name) > 15:
            errors.append("workload.port_name must be a DNS-1123 label of at most 15 characters")
        path = workload.get("readiness_path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append("workload.readiness_path must start with /")

    metrics = _section(argo, "metrics", errors)
    if metrics is not None:
        errors.extend(_reject_unknown(metrics, {"prometheus_address", "cluster_domain", "queries"}, "metrics"))
        address = metrics.get("prometheus_address")
        if not isinstance(address, str) or not re.fullmatch(r"https?://[^\s/]+(/\S*)?", address):
            errors.append("metrics.prometheus_address must be an http(s) URL")
        domain = metrics.get("cluster_domain")
        if not isinstance(domain, str) or not DOMAIN_RE.fullmatch(domain):
            errors.append("metrics.cluster_domain must be a DNS domain")
        queries = metrics.get("queries")
        if not isinstance(queries, dict) or set(queries) != {"error_rate", "latency_p95", "request_volume"}:
            errors.append("metrics.queries must define exactly error_rate, latency_p95 and request_volume")
        else:
            for key, query in queries.items():
                if not isinstance(query, str) or HOST_ARG not in query:
                    errors.append(f"metrics.queries.{key} must be scoped with {HOST_ARG}")

    analysis = _section(argo, "analysis", errors)
    if analysis is not None:
        errors.extend(
            _reject_unknown(
                analysis,
                {
                    "consecutive_error_limit",
                    "smoke",
                    "error_rate",
                    "latency_p95",
                    "request_volume",
                    "confirm_error_rate",
                },
                "analysis",
            )
        )
        if not _is_int(analysis.get("consecutive_error_limit"), 1):
            errors.append("analysis.consecutive_error_limit must be an integer of at least 1")
        smoke = analysis.get("smoke")
        errors.extend(
            _validate_timing(
                smoke,
                "analysis.smoke",
                extra={"path", "json_path", "expected", "timeout_seconds"},
                with_failure_limit=True,
            )
        )
        if isinstance(smoke, dict):
            if not isinstance(smoke.get("path"), str) or not smoke["path"].startswith("/"):
                errors.append("analysis.smoke.path must start with /")
            for key in ("json_path", "expected"):
                if not isinstance(smoke.get(key), str) or not smoke[key]:
                    errors.append(f"analysis.smoke.{key} must be a non-empty string")
            if not _is_int(smoke.get("timeout_seconds"), 1):
                errors.append("analysis.smoke.timeout_seconds must be an integer of at least 1")
        for key in ("error_rate", "latency_p95", "confirm_error_rate"):
            errors.extend(_validate_timing(analysis.get(key), f"analysis.{key}", extra=set(), with_failure_limit=True))
        volume = analysis.get("request_volume")
        errors.extend(
            _validate_timing(volume, "analysis.request_volume", extra={"inconclusive_limit"}, with_failure_limit=False)
        )
        if isinstance(volume, dict):
            limit = volume.get("inconclusive_limit")
            if not _is_int(limit, 0):
                errors.append("analysis.request_volume.inconclusive_limit must be an integer of at least 0")
            elif _is_int(volume.get("count"), 1) and limit >= volume["count"]:
                errors.append("analysis.request_volume.inconclusive_limit must be lower than count")

    blue = _section(argo, "blue_green", errors)
    if blue is not None:
        errors.extend(_reject_unknown(blue, {"auto_promotion_enabled", "scale_down_delay_seconds"}, "blue_green"))
        if not isinstance(blue.get("auto_promotion_enabled"), bool):
            errors.append("blue_green.auto_promotion_enabled must be a boolean")
        delay = blue.get("scale_down_delay_seconds")
        if not _is_int(delay, 1):
            errors.append("blue_green.scale_down_delay_seconds must be an integer of at least 1")
        elif analysis is not None and not errors:
            window = max(bake_seconds(analysis[key]) for key in ("error_rate", "latency_p95", "request_volume"))
            if delay < window + ROLLBACK_MARGIN_SECONDS:
                errors.append(
                    "blue_green.scale_down_delay_seconds must outlive post-promotion analysis "
                    f"({window}s) plus {ROLLBACK_MARGIN_SECONDS}s, otherwise abort cannot switch back"
                )

    sync = _section(argo, "progressive_sync", errors)
    if sync is not None:
        errors.extend(_reject_unknown(sync, {"label_key", "max_update"}, "progressive_sync"))
        if not isinstance(sync.get("label_key"), str) or not DNS1123_RE.fullmatch(sync["label_key"]):
            errors.append("progressive_sync.label_key must be a DNS-1123 label")
        if not isinstance(sync.get("max_update"), str) or not MAX_UPDATE_RE.fullmatch(sync["max_update"]):
            errors.append("progressive_sync.max_update must be a count or a percentage like 100%")

    gitops = _section(argo, "gitops", errors)
    if gitops is not None:
        errors.extend(
            _reject_unknown(
                gitops,
                {"repo_url", "revision", "base_path", "project", "argocd_namespace"},
                "gitops",
            )
        )
        errors.extend(_validate_gitops(gitops, require_repo=False))

    for name in ("namespace", "gateway_name"):
        if not _dns1123(strategies.get(name)):
            errors.append(f"release strategies {name} must be a DNS-1123 label")
    for strategy_name in ("canary", "blue_green"):
        body = strategies.get("strategies", {}).get(strategy_name)
        if not isinstance(body, dict) or not isinstance(body.get("analysis_thresholds"), dict):
            errors.append(f"release strategies {strategy_name} must define analysis_thresholds")
            continue
        for step in body.get("steps", []):
            for check in step.get("analysis", []):
                if check not in ANALYSIS_PROVIDER:
                    errors.append(f"release strategies use check {check} that has no Argo Rollouts mapping")
    return errors


def _validate_gitops(gitops: dict[str, Any], *, require_repo: bool) -> list[str]:
    errors: list[str] = []
    repo = gitops.get("repo_url")
    if not isinstance(repo, str):
        errors.append("gitops.repo_url must be a string")
    elif repo and not re.fullmatch(r"(https://\S+|ssh://\S+|git@\S+:\S+)", repo):
        errors.append("gitops.repo_url must be an https, ssh or scp-style git URL")
    elif require_repo and not repo:
        errors.append("gitops.repo_url is empty")
    if not isinstance(gitops.get("revision"), str) or not gitops["revision"]:
        errors.append("gitops.revision must be a non-empty string")
    base = gitops.get("base_path")
    if (
        not isinstance(base, str)
        or not base
        or base.startswith("/")
        or any(part in ("", "..") for part in base.split("/"))
    ):
        errors.append("gitops.base_path must be a relative path without .. segments")
    for key in ("project", "argocd_namespace"):
        if not _dns1123(gitops.get(key)):
            errors.append(f"gitops.{key} must be a DNS-1123 label")
    return errors


def load_policies(
    strategy_path: Path,
    cluster_path: Path,
    promotion_path: Path,
    argo_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Imported lazily so the Argo module stays importable without the manual engine.
    from release_strategy import load_release_policy

    strategies, _clusters, _promotion = load_release_policy(strategy_path, cluster_path, promotion_path)
    argo = _load_json(argo_path)
    errors = validate_argo_policy(argo, strategies)
    if errors:
        raise ValueError("invalid argo rollouts policy: " + "; ".join(errors))
    return strategies, argo


def _k8s_name(*parts: str) -> str:
    name = "-".join(parts)
    if not _dns1123(name):
        raise ValueError(f"kubernetes name is invalid: {name}")
    return name


def _number(value: float | int) -> str:
    return json.dumps(value)


def _template_name(service: str, check: str, *, confirm: bool = False) -> str:
    suffix = TEMPLATE_SUFFIX[check]
    return _k8s_name(service, suffix, "confirm") if confirm else _k8s_name(service, suffix)


def _volume_template_name(service: str) -> str:
    return _k8s_name(service, "request-volume")


def _hosts(service: str, namespace: str, traffic: str, domain: str) -> dict[str, str]:
    suffix = f"{namespace}.svc.{domain}"
    if traffic == "canary":
        canary = f"{service}-canary.{suffix}"
        return {"smoke": canary, "error_rate": canary, "latency_p95": canary, "request_volume": canary}
    active = f"{service}-active.{suffix}"
    return {
        "smoke": f"{service}-preview.{suffix}",
        "error_rate": active,
        "latency_p95": active,
        "request_volume": active,
    }


def _templates_for(service: str, checks: list[str], *, confirm: bool = False) -> list[str]:
    """Template names for one analysis step. Native checks (readiness) need no template."""
    names = [
        _template_name(service, check, confirm=confirm and check == "error_rate")
        for check in checks
        if ANALYSIS_PROVIDER[check] != "native"
    ]
    if any(check in ("error_rate", "latency_p95") for check in checks):
        names.append(_volume_template_name(service))
    return names


def _metric_base(name: str, timing: dict[str, Any], consecutive: int) -> dict[str, Any]:
    metric: dict[str, Any] = {"name": name}
    delay = int(timing.get("initial_delay_seconds", 0))
    if delay:
        metric["initialDelay"] = f"{delay}s"
    metric["interval"] = f"{int(timing['interval_seconds'])}s"
    metric["count"] = int(timing["count"])
    if "failure_limit" in timing:
        metric["failureLimit"] = int(timing["failure_limit"])
    if "inconclusive_limit" in timing:
        metric["inconclusiveLimit"] = int(timing["inconclusive_limit"])
    metric["consecutiveErrorLimit"] = consecutive
    return metric


def _prometheus(address: str, query: str) -> dict[str, Any]:
    return {"prometheus": {"address": address, "query": query}}


def _error_rate_metric(argo: dict, thresholds: dict, timing: dict) -> dict[str, Any]:
    limit = _number(thresholds["error_rate_max"])
    metric = _metric_base("error-rate", timing, argo["analysis"]["consecutive_error_limit"])
    # No 5xx series (or no traffic at all) is not an error. Missing traffic is
    # judged by request-volume, so this metric never aborts on silence.
    metric["successCondition"] = f"len(result) == 0 || isNaN(result[0]) || result[0] <= {limit}"
    metric["failureCondition"] = f"len(result) > 0 && !isNaN(result[0]) && result[0] > {limit}"
    metric["provider"] = _prometheus(argo["metrics"]["prometheus_address"], argo["metrics"]["queries"]["error_rate"])
    return metric


def _latency_metric(argo: dict, thresholds: dict) -> dict[str, Any]:
    limit = _number(thresholds["latency_p95_max_ms"])
    metric = _metric_base("latency-p95", argo["analysis"]["latency_p95"], argo["analysis"]["consecutive_error_limit"])
    metric["successCondition"] = f"len(result) == 0 || isNaN(result[0]) || result[0] <= {limit}"
    metric["failureCondition"] = f"len(result) > 0 && !isNaN(result[0]) && result[0] > {limit}"
    metric["provider"] = _prometheus(argo["metrics"]["prometheus_address"], argo["metrics"]["queries"]["latency_p95"])
    return metric


def _volume_metric(argo: dict, thresholds: dict) -> dict[str, Any]:
    minimum = _number(thresholds["min_requests"])
    metric = _metric_base("request-volume", argo["analysis"]["request_volume"], argo["analysis"]["consecutive_error_limit"])
    # Too little traffic is neither pass nor fail: the measurement is
    # inconclusive and Argo Rollouts pauses for a person instead of guessing.
    metric["successCondition"] = f"len(result) == 1 && !isNaN(result[0]) && result[0] >= {minimum}"
    metric["failureCondition"] = "len(result) > 1"
    metric["provider"] = _prometheus(
        argo["metrics"]["prometheus_address"], argo["metrics"]["queries"]["request_volume"]
    )
    return metric


def _smoke_metric(argo: dict, service_port: int) -> dict[str, Any]:
    smoke = argo["analysis"]["smoke"]
    metric = _metric_base("smoke", smoke, argo["analysis"]["consecutive_error_limit"])
    metric["successCondition"] = f"result == {json.dumps(smoke['expected'])}"
    metric["provider"] = {
        "web": {
            "url": f"http://{HOST_ARG}:{service_port}{smoke['path']}",
            "timeoutSeconds": int(smoke["timeout_seconds"]),
            "jsonPath": smoke["json_path"],
        }
    }
    return metric


def _meta(name: str, namespace: str, labels: dict[str, str], annotations: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "namespace": namespace, "labels": dict(labels), "annotations": dict(annotations)}


def build_analysis_templates(
    *,
    service: str,
    namespace: str,
    traffic: str,
    strategies: dict[str, Any],
    argo: dict[str, Any],
    labels: dict[str, str],
    annotations: dict[str, str],
) -> list[tuple[str, dict[str, Any]]]:
    body = strategies["strategies"][traffic]
    thresholds = body["analysis_thresholds"]
    hosts = _hosts(service, namespace, traffic, argo["metrics"]["cluster_domain"])
    used: set[str] = set()
    confirm_error_rate = False
    for step in body["steps"]:
        for check in step["analysis"]:
            if ANALYSIS_PROVIDER[check] == "native":
                continue
            if step["name"] == "confirm" and check == "error_rate":
                confirm_error_rate = True
            else:
                used.add(check)
    needs_volume = bool(used & {"error_rate", "latency_p95"}) or confirm_error_rate

    wanted: list[tuple[str, str, dict[str, Any]]] = []
    if "smoke" in used:
        wanted.append((_template_name(service, "smoke"), hosts["smoke"], _smoke_metric(argo, strategies["service_port"])))
    if "error_rate" in used:
        wanted.append(
            (
                _template_name(service, "error_rate"),
                hosts["error_rate"],
                _error_rate_metric(argo, thresholds, argo["analysis"]["error_rate"]),
            )
        )
    if confirm_error_rate:
        wanted.append(
            (
                _template_name(service, "error_rate", confirm=True),
                hosts["error_rate"],
                _error_rate_metric(argo, thresholds, argo["analysis"]["confirm_error_rate"]),
            )
        )
    if "latency_p95" in used:
        wanted.append((_template_name(service, "latency_p95"), hosts["latency_p95"], _latency_metric(argo, thresholds)))
    if needs_volume:
        wanted.append((_volume_template_name(service), hosts["request_volume"], _volume_metric(argo, thresholds)))

    documents: list[tuple[str, dict[str, Any]]] = []
    for name, host, metric in wanted:
        documents.append(
            (
                f"{name}-analysis.json",
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "AnalysisTemplate",
                    "metadata": _meta(name, namespace, labels, annotations),
                    "spec": {"args": [{"name": "host", "value": host}], "metrics": [metric]},
                },
            )
        )
    return documents


def _analysis_ref(names: list[str]) -> dict[str, Any]:
    return {"templates": [{"templateName": name} for name in names]}


def compile_canary_steps(service: str, canary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    readable: list[dict[str, Any]] = []
    for step in canary["steps"]:
        confirm = step["name"] == "confirm"
        templates = _templates_for(service, step["analysis"], confirm=confirm)
        entry: dict[str, Any] = {"step": step["name"], "checks": list(step["analysis"]), "templates": templates}
        if "canary_weight" in step:
            steps.append({"setWeight": step["canary_weight"]})
            entry["setWeight"] = step["canary_weight"]
        if templates:
            steps.append({"analysis": _analysis_ref(templates)})
        readable.append(entry)
    return steps, readable


def compile_blue_green(
    service: str, blue: dict[str, Any]
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    pre: list[str] = []
    post: list[str] = []
    readable: list[dict[str, Any]] = []
    for step in blue["steps"]:
        templates = _templates_for(service, step["analysis"])
        mapped = {"deploy_inactive": "native readiness", "preview": "prePromotionAnalysis", "cutover": "postPromotionAnalysis"}
        if step["name"] == "preview":
            pre = templates
        elif step["name"] == "cutover":
            post = templates
        readable.append(
            {
                "step": step["name"],
                "traffic": step["traffic"],
                "checks": list(step["analysis"]),
                "templates": templates,
                "argo": mapped.get(step["name"], "scaleDownDelaySeconds"),
            }
        )
    return pre, post, readable


def _pod_template(service: str, image: str, workload: dict[str, Any]) -> dict[str, Any]:
    port_name = workload["port_name"]
    return {
        "metadata": {"labels": {"app": service}},
        "spec": {
            "containers": [
                {
                    "name": service,
                    "image": image,
                    "ports": [{"name": port_name, "containerPort": workload["container_port"]}],
                    "readinessProbe": {
                        "httpGet": {"path": workload["readiness_path"], "port": port_name},
                        "periodSeconds": 5,
                        "failureThreshold": 3,
                    },
                }
            ]
        },
    }


def _service(name: str, namespace: str, service: str, strategies: dict, argo: dict, labels, annotations) -> dict[str, Any]:
    port_name = argo["workload"]["port_name"]
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(name, namespace, labels, annotations),
        "spec": {
            "selector": {"app": service},
            "ports": [
                {"name": port_name, "port": strategies["service_port"], "targetPort": port_name, "protocol": "TCP"}
            ],
        },
    }


def _backend(name: str, port: int, weight: int) -> dict[str, Any]:
    return {"name": name, "port": port, "weight": weight}


def _gateway_route(
    name: str,
    namespace: str,
    strategies: dict,
    rules: list[dict[str, Any]],
    labels,
    annotations,
) -> dict[str, Any]:
    return {
        "apiVersion": GATEWAY_API,
        "kind": "HTTPRoute",
        "metadata": _meta(name, namespace, labels, annotations),
        "spec": {
            "parentRefs": [{"name": strategies["gateway_name"], "namespace": namespace}],
            "rules": rules,
        },
    }


def _istio_route(
    name: str,
    namespace: str,
    service: str,
    strategies: dict,
    http: list[dict[str, Any]],
    labels,
    annotations,
) -> dict[str, Any]:
    return {
        "apiVersion": ISTIO_API,
        "kind": "VirtualService",
        "metadata": _meta(name, namespace, labels, annotations),
        "spec": {"hosts": [service], "gateways": [strategies["gateway_name"]], "http": http},
    }


def _istio_destinations(backends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"destination": {"host": item["name"], "port": {"number": item["port"]}}, "weight": item["weight"]}
        for item in backends
    ]


def build_router(
    *,
    service: str,
    namespace: str,
    traffic: str,
    strategies: dict[str, Any],
    argo: dict[str, Any],
    labels: dict[str, str],
    annotations: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    port = strategies["service_port"]
    istio = argo["controller"]["traffic_router"] == "istio"
    if traffic == "canary":
        backends = [_backend(f"{service}-stable", port, 100), _backend(f"{service}-canary", port, 0)]
    else:
        backends = [_backend(f"{service}-active", port, 100)]
    if not istio:
        name = _k8s_name(service, "route")
        rules: list[dict[str, Any]] = []
        if traffic == "blue_green":
            rules.append(
                {
                    "matches": [
                        {
                            "headers": [
                                {"name": strategies["preview_header"], "value": strategies["preview_header_value"]}
                            ]
                        }
                    ],
                    "backendRefs": [_backend(f"{service}-preview", port, 100)],
                }
            )
        rules.append({"backendRefs": backends})
        return f"{name}.json", _gateway_route(name, namespace, strategies, rules, labels, annotations)

    name = _k8s_name(service, "vs")
    http: list[dict[str, Any]] = []
    if traffic == "blue_green":
        http.append(
            {
                "name": "preview",
                "match": [
                    {"headers": {strategies["preview_header"]: {"exact": strategies["preview_header_value"]}}}
                ],
                "route": _istio_destinations([_backend(f"{service}-preview", port, 100)]),
            }
        )
    http.append({"name": "primary", "route": _istio_destinations(backends)})
    return f"{name}.json", _istio_route(name, namespace, service, strategies, http, labels, annotations)


def build_rollout(
    *,
    service: str,
    namespace: str,
    traffic: str,
    image: str,
    strategies: dict[str, Any],
    argo: dict[str, Any],
    labels: dict[str, str],
    annotations: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    controller = argo["controller"]
    body = strategies["strategies"][traffic]
    if traffic == "canary":
        steps, readable = compile_canary_steps(service, body)
        router = controller["traffic_router"]
        if router == "gateway_api":
            routing: dict[str, Any] = {
                "plugins": {GATEWAY_PLUGIN: {"httpRoute": _k8s_name(service, "route"), "namespace": namespace}}
            }
        else:
            routing = {"istio": {"virtualService": {"name": _k8s_name(service, "vs"), "routes": ["primary"]}}}
        strategy_block: dict[str, Any] = {
            "canary": {
                "stableService": f"{service}-stable",
                "canaryService": f"{service}-canary",
                "trafficRouting": routing,
                "abortScaleDownDelaySeconds": controller["abort_scale_down_delay_seconds"],
                "steps": steps,
            }
        }
    else:
        pre, post, readable = compile_blue_green(service, body)
        block: dict[str, Any] = {
            "activeService": f"{service}-active",
            "previewService": f"{service}-preview",
            "autoPromotionEnabled": argo["blue_green"]["auto_promotion_enabled"],
            "scaleDownDelaySeconds": argo["blue_green"]["scale_down_delay_seconds"],
            "abortScaleDownDelaySeconds": controller["abort_scale_down_delay_seconds"],
        }
        if pre:
            block["prePromotionAnalysis"] = _analysis_ref(pre)
        if post:
            block["postPromotionAnalysis"] = _analysis_ref(post)
        strategy_block = {"blueGreen": block}
    rollout = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Rollout",
        "metadata": _meta(service, namespace, labels, annotations),
        "spec": {
            "replicas": argo["workload"]["replicas"],
            "revisionHistoryLimit": controller["revision_history_limit"],
            "selector": {"matchLabels": {"app": service}},
            "template": _pod_template(service, image, argo["workload"]),
            "strategy": strategy_block,
        },
    }
    return rollout, readable


def _ignore_differences(argo: dict[str, Any]) -> list[dict[str, Any]]:
    # Argo Rollouts rewrites router weights at runtime. Without this, Argo CD
    # sees drift and puts the 100/0 baseline weights back in the middle of a rollout.
    if argo["controller"]["traffic_router"] == "istio":
        return [
            {
                "group": "networking.istio.io",
                "kind": "VirtualService",
                "jqPathExpressions": [".spec.http[].route[].weight"],
            }
        ]
    return [
        {
            "group": "gateway.networking.k8s.io",
            "kind": "HTTPRoute",
            "jqPathExpressions": [".spec.rules[].backendRefs[].weight"],
        }
    ]


def build_applicationset(
    *,
    state: dict[str, Any],
    namespace: str,
    argo: dict[str, Any],
    gitops: dict[str, Any],
    labels: dict[str, str],
    annotations: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    service = state["service"]
    multi = state["strategy"] in MULTI_CLUSTER
    sync = argo["progressive_sync"]
    label_key = sync["label_key"]
    elements: list[dict[str, str]] = []
    for wave in state["waves"]:
        for cluster_id in wave["cluster_ids"]:
            element = {"cluster": cluster_id}
            if multi:
                element["region"] = wave["region"]
            elements.append(element)
    app_labels = {"cicd.platform/service": service}
    if multi:
        app_labels[label_key] = "{{region}}"
    spec: dict[str, Any] = {
        "generators": [{"list": {"elements": elements}}],
        "template": {
            "metadata": {"name": f"{service}-{{{{cluster}}}}", "labels": app_labels},
            "spec": {
                "project": gitops["project"],
                "source": {
                    "repoURL": gitops["repo_url"],
                    "targetRevision": gitops["revision"],
                    "path": f"{gitops['base_path']}/{service}",
                },
                "destination": {"name": "{{cluster}}", "namespace": namespace},
                "ignoreDifferences": _ignore_differences(argo),
                "syncPolicy": {"syncOptions": ["RespectIgnoreDifferences=true"]},
            },
        },
    }
    if multi:
        spec["strategy"] = {
            "type": "RollingSync",
            "rollingSync": {
                "steps": [
                    {
                        "matchExpressions": [{"key": label_key, "operator": "In", "values": [region]}],
                        "maxUpdate": sync["max_update"],
                    }
                    for region in state["region_order"]
                ]
            },
        }
    else:
        # RollingSync forces auto-sync off. Without it, the usual GitOps auto-sync applies.
        spec["template"]["spec"]["syncPolicy"]["automated"] = {"prune": True, "selfHeal": True}
    name = _k8s_name(service, "appset")
    return (
        f"{name}.json",
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": _meta(name, gitops["argocd_namespace"], labels, annotations),
            "spec": spec,
        },
    )


def _check_state(state: dict[str, Any]) -> None:
    strategy = state.get("strategy")
    if strategy not in TRAFFIC_STRATEGY:
        raise ValueError(f"unknown release strategy: {strategy}")
    history = [item.get("action") for item in state.get("history", []) if isinstance(item, dict)]
    if state.get("status") != "planned" or history != ["plan"]:
        raise ValueError(
            "argo mode renders from a fresh plan; after the first sync Argo Rollouts owns progression, "
            "so an advanced or aborted release state must not be rendered"
        )
    waves = state.get("waves")
    if not isinstance(waves, list) or not waves:
        raise ValueError("release state has no waves")
    for wave in waves:
        if wave.get("strategy") != TRAFFIC_STRATEGY[strategy]:
            raise ValueError(f"wave {wave.get('name')} does not match strategy {strategy}")
        if not wave.get("cluster_ids"):
            raise ValueError(f"wave {wave.get('name')} has no clusters")
    if strategy in MULTI_CLUSTER:
        regions = [wave.get("region") for wave in waves]
        if regions != state.get("region_order") or len(set(regions)) < 2:
            raise ValueError("multi-cluster state must list one wave per region in region_order")
    for field in ("candidate_digest", "baseline_digest"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(state.get(field, ""))):
            raise ValueError(f"{field} must be 64 lowercase hexadecimal characters")


def render_documents(
    state: dict[str, Any],
    strategies: dict[str, Any],
    argo: dict[str, Any],
    *,
    image: str,
    gitops: dict[str, Any] | None = None,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    _check_state(state)
    if not IMAGE_DIGEST_RE.fullmatch(image or ""):
        raise ValueError("image must be pinned by digest, like registry/name@sha256:<64 hex>; tags are not accepted")
    strategy = state["strategy"]
    traffic = TRAFFIC_STRATEGY[strategy]
    service = state["service"]
    if not _dns1123(service):
        raise ValueError("service must be a DNS-1123 label")
    namespace = strategies["namespace"]
    gitops = dict(argo["gitops"] if gitops is None else gitops)
    identity = state["identity"]
    labels = {"app": service, "app.kubernetes.io/managed-by": "cicd-platform"}
    annotations = {
        "cicd.platform/controller": "argo-rollouts",
        "cicd.platform/strategy": strategy,
        "cicd.platform/release-digest": state["candidate_digest"],
        "cicd.platform/baseline-digest": state["baseline_digest"],
        "cicd.platform/image": image,
        "cicd.platform/source-sha": identity["source_sha"],
        "cicd.platform/release-tag": identity["release_tag"],
        "cicd.platform/evidence": "analysis-run",
    }

    rollout, readable = build_rollout(
        service=service,
        namespace=namespace,
        traffic=traffic,
        image=image,
        strategies=strategies,
        argo=argo,
        labels=labels,
        annotations=annotations,
    )
    documents: list[tuple[str, dict[str, Any]]] = [(f"{_k8s_name(service, 'rollout')}.json", rollout)]
    service_names = ("stable", "canary") if traffic == "canary" else ("active", "preview")
    for suffix in service_names:
        name = _k8s_name(service, suffix)
        documents.append(
            (f"{name}-svc.json", _service(name, namespace, service, strategies, argo, labels, annotations))
        )
    documents.append(
        build_router(
            service=service,
            namespace=namespace,
            traffic=traffic,
            strategies=strategies,
            argo=argo,
            labels=labels,
            annotations=annotations,
        )
    )
    documents.extend(
        build_analysis_templates(
            service=service,
            namespace=namespace,
            traffic=traffic,
            strategies=strategies,
            argo=argo,
            labels=labels,
            annotations=annotations,
        )
    )

    appset_note = "rendered"
    if gitops.get("repo_url"):
        errors = _validate_gitops(gitops, require_repo=True)
        if errors:
            raise ValueError("; ".join(errors))
        documents.append(
            build_applicationset(
                state=state,
                namespace=namespace,
                argo=argo,
                gitops=gitops,
                labels=labels,
                annotations=annotations,
            )
        )
    elif strategy in MULTI_CLUSTER:
        raise ValueError(
            "multi-cluster strategies need gitops.repo_url (or --gitops-repo-url): "
            "region order is enforced by the ApplicationSet RollingSync, so it cannot be skipped"
        )
    else:
        appset_note = "skipped: gitops.repo_url is empty"

    _self_check(documents, state, traffic)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "engine": "argo-rollouts",
        "strategy": strategy,
        "traffic_strategy": traffic,
        "service": service,
        "environment": state["environment"],
        "image": image,
        "router": argo["controller"]["traffic_router"],
        "region_order": list(state.get("region_order", [])),
        "clusters": [cluster for wave in state["waves"] for cluster in wave["cluster_ids"]],
        "steps": readable,
        "applicationset": appset_note,
        "files": [name for name, _ in documents],
    }
    return documents, summary


def _self_check(documents: list[tuple[str, dict[str, Any]]], state: dict[str, Any], traffic: str) -> None:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for _, doc in documents:
        by_kind.setdefault(doc["kind"], []).append(doc)
    templates = {doc["metadata"]["name"] for doc in by_kind.get("AnalysisTemplate", [])}
    rollout = by_kind["Rollout"][0]
    block = rollout["spec"]["strategy"]
    referenced: list[str] = []
    if "canary" in block:
        for step in block["canary"]["steps"]:
            if "analysis" in step:
                referenced.extend(item["templateName"] for item in step["analysis"]["templates"])
    else:
        for key in ("prePromotionAnalysis", "postPromotionAnalysis"):
            if key in block["blueGreen"]:
                referenced.extend(item["templateName"] for item in block["blueGreen"][key]["templates"])
    missing = sorted(set(referenced) - templates)
    if missing:
        raise ValueError("internal render check failed, missing AnalysisTemplate: " + ", ".join(missing))
    if not IMAGE_DIGEST_RE.fullmatch(rollout["spec"]["template"]["spec"]["containers"][0]["image"]):
        raise ValueError("internal render check failed: rollout image is not digest-pinned")
    for doc in by_kind.get("HTTPRoute", []):
        for rule in doc["spec"]["rules"]:
            weights = [item["weight"] for item in rule["backendRefs"]]
            if sum(weights) != 100 and traffic == "canary":
                raise ValueError("internal render check failed: route weights must sum to 100")
    for doc in by_kind.get("ApplicationSet", []):
        rendered = [item["cluster"] for item in doc["spec"]["generators"][0]["list"]["elements"]]
        pinned = [cluster for wave in state["waves"] for cluster in wave["cluster_ids"]]
        if rendered != pinned:
            raise ValueError("internal render check failed: ApplicationSet clusters differ from the cluster pin")


def _write_documents(
    documents: list[tuple[str, dict[str, Any]]],
    summary: dict[str, Any],
    out_dir: Path,
) -> None:
    """Write this render and drop only files an earlier Argo render wrote.

    The manifest records what this module wrote, so a reused out dir never
    deletes files that belong to another tool.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    previous: set[str] = set()
    if manifest_path.is_file():
        try:
            previous = set(json.loads(manifest_path.read_text(encoding="utf-8")).get("files", []))
        except (OSError, json.JSONDecodeError):
            previous = set()
    written: set[str] = set()
    for filename, document in documents:
        (out_dir / filename).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.add(filename)
    for stale in previous - written:
        path = out_dir / stale
        if path.is_file() and path.parent == out_dir:
            path.unlink()
    manifest_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ci/release-strategies.json")
    parser.add_argument("--clusters", default="ci/clusters.json")
    parser.add_argument("--promotion-policy", default="ci/promotion-policy.json")
    parser.add_argument("--argo-policy", default="ci/argo-rollouts.json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--state", required=True, help="release-state.json written by release_strategy.py plan")
    render_parser.add_argument("--image", required=True, help="registry/name@sha256:<digest> of the candidate")
    render_parser.add_argument("--out-dir", required=True)
    render_parser.add_argument("--gitops-repo-url", default="")
    render_parser.add_argument("--gitops-revision", default="")
    args = parser.parse_args()
    try:
        strategies, argo = load_policies(
            Path(args.policy), Path(args.clusters), Path(args.promotion_policy), Path(args.argo_policy)
        )
        if args.command == "validate":
            print(
                "OK: argo rollouts policy validated "
                f"(router {argo['controller']['traffic_router']}, strategies {'/'.join(TRAFFIC_STRATEGY)})"
            )
            return 0
        state = _load_json(Path(args.state))
        gitops = dict(argo["gitops"])
        if args.gitops_repo_url:
            gitops["repo_url"] = args.gitops_repo_url
        if args.gitops_revision:
            gitops["revision"] = args.gitops_revision
        documents, summary = render_documents(state, strategies, argo, image=args.image, gitops=gitops)
        _write_documents(documents, summary, Path(args.out_dir))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({key: summary[key] for key in ("strategy", "service", "router", "applicationset", "files")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
