"""Benchmark runner.

Produces one reproducible result artifact per run:

    results/committed/<provider>/<split>-<evaluation_kind>.json

The artifact records the provider identity, dataset hash, frozen-threshold
hash, the exact command, per-case rows, detector-level metrics, threshold
evaluation and the launch claim. Deterministic runs are reproducible: a second
run of the same provider/split yields byte-identical `metrics` and per-case
verdicts (latency and timestamps excluded — see `comparable_view`).
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from jev_change_monitor import __version__
from jev_change_monitor.benchmark import dataset
from jev_change_monitor.benchmark import metrics as metrics_mod
from jev_change_monitor.benchmark import thresholds as thresholds_mod
from jev_change_monitor.detectors import get_detector
from jev_change_monitor.normalize import sha256_text
from jev_change_monitor.redact import redact_value
from jev_change_monitor.schemas import validate_schema

REPO_ROOT = dataset.REPO_ROOT
RESULTS_DIR = REPO_ROOT / "results" / "runs"

VOLATILE_KEYS = ("generated_at", "runtime", "latency_ms", "latency_ms_p50",
                 "latency_ms_p95", "artifact_sha256")


def _request_for_case(case: dict) -> dict:
    before = case["before"]
    after = case["after"]
    return {
        "schema_version": "1.0",
        "request_id": f"req-{case['case_id']}",
        "detector": case["detector"],
        "url": case["url"],
        "detected_at": after["captured_at"],
        "before": {
            "content_type": before["content_type"],
            "content": before["content"],
            "sha256": sha256_text(before["content"]),
            "captured_at": before["captured_at"],
            "byte_count": len(before["content"].encode("utf-8")),
        },
        "after": {
            "content_type": after["content_type"],
            "content": after["content"],
            "sha256": sha256_text(after["content"]),
            "captured_at": after["captured_at"],
            "byte_count": len(after["content"].encode("utf-8")),
        },
    }


class _FaultInjector:
    """Deterministic fault injection to exercise the error-rate plumbing.

    Every `period`-th case yields a labeled fault instead of a judgement. This
    is NOT a detector result and is recorded as such in the artifact.
    """

    def __init__(self, rate: float):
        self.rate = rate
        self.period = max(2, round(1 / rate)) if rate and rate > 0 else 0
        self.fired = 0

    def should_fault(self, index: int) -> str | None:
        if not self.period:
            return None
        if (index + 1) % self.period == 0:
            self.fired += 1
            # alternate the two failure channels so both rates are non-zero
            return "provider_error" if self.fired % 2 else "schema_invalid"
        return None


def run(
    split: str,
    provider_name: str,
    provider,
    out_path: Path | None = None,
    fault_injection_rate: float = 0.0,
    command: str = "<unknown>",
    quiet: bool = False,
) -> dict:
    from jev_change_monitor.providers.base import ProviderResponse

    cases = dataset.load_cases(split)
    accounting = dataset.split_accounting(split)
    thresholds = thresholds_mod.load_thresholds()
    cost_model = thresholds_mod.load_cost_model()
    frozen_ok, frozen_reason = thresholds_mod.check_frozen()
    injector = _FaultInjector(fault_injection_rate)

    evaluation_kind = "live-jev" if provider.mode == "live" else "synthetic-deterministic"

    per_detector_rows: dict[str, list[dict]] = {d: [] for d in dataset.DETECTORS}
    case_rows: list[dict] = []

    for index, case in enumerate(cases):
        detector = get_detector(case["detector"])
        request = _request_for_case(case)
        fault = injector.should_fault(index)
        if fault == "provider_error":
            response = ProviderResponse(
                provider=provider_name, mode=provider.mode, result=None, raw=None,
                latency_ms=0.0, input_bytes=request["before"]["byte_count"] + request["after"]["byte_count"],
                output_bytes=0, schema_valid=False, error_category="provider",
                provider_error="injected fault (provider_error) — metric-plumbing check",
                retries=0, usage={"injected": True},
            )
        elif fault == "schema_invalid":
            response = ProviderResponse(
                provider=provider_name, mode=provider.mode,
                result={"invalid": "injected fault (schema_invalid) — metric-plumbing check"},
                raw=None, latency_ms=0.0,
                input_bytes=request["before"]["byte_count"] + request["after"]["byte_count"],
                output_bytes=64, schema_valid=False, error_category="schema_invalid",
                provider_error=None, retries=0, usage={"injected": True},
            )
        else:
            response = provider.judge(detector, request)

        schema_errors: list[str] = []
        if response.result is not None and response.schema_valid:
            schema_errors = validate_schema(response.result, "detector-result")
            if schema_errors:
                response.schema_valid = False
                response.error_category = "schema_invalid"

        result = response.result if response.schema_valid else None
        price_pred = {}
        if result:
            price_pred = ((result.get("details") or {}).get("extraction") or {})

        record = response.to_record()
        record["injected_fault"] = bool(fault)
        row = {
            "case_id": case["case_id"],
            "detector": case["detector"],
            "subtype": case["subtype"],
            "edge_case": bool(case.get("edge_case")),
            "provenance": case["provenance"],
            "label": {
                "meaningful": case["label"]["meaningful"],
                "should_alert": case["label"]["should_alert"],
            },
            "predicted": {
                "meaningful": (result or {}).get("meaningful"),
                "should_alert": (result or {}).get("should_alert"),
                "change_type": (result or {}).get("change_type"),
                "confidence": (result or {}).get("confidence"),
                "confidence_source": (result or {}).get("confidence_source"),
            },
            "result_record": record,
            "schema_errors": schema_errors,
        }
        case_rows.append(row)
        per_detector_rows[case["detector"]].append({
            **row,
            "result": result,
            "expected": case.get("expected", {}),
            "price_pred": price_pred,
        })

    per_detector_metrics = {
        detector: metrics_mod.detector_metrics(rows, cost_model)
        for detector, rows in per_detector_rows.items()
    }
    overall = metrics_mod.aggregate_metrics(per_detector_metrics)
    threshold_results = thresholds_mod.evaluate(per_detector_metrics, thresholds)
    launch = thresholds_mod.launch_status(threshold_results, evaluation_kind)

    blocked_reasons = list(launch["reasons"])
    if not accounting["human_labeled"]:
        blocked_reasons.append(
            "held-out labels are rubric-labeled drafts with review_status "
            "'pending-independent-review'; independent human review/adjudication of disputed "
            "cases has not been recorded"
        )
    if not frozen_ok:
        blocked_reasons.append(f"threshold immutability check failed: {frozen_reason}")

    artifact = {
        "artifact_version": "1.0.0",
        "run_id": None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "jev-change-monitor", "version": __version__},
        "provider": provider.describe(),
        "evaluation_kind": evaluation_kind,
        "command": command,
        "dataset": {
            "split": split,
            "path": accounting["path"],
            "sha256": accounting["sha256"],
            "cases_total": accounting["cases_total"],
            "per_detector": accounting["per_detector"],
            "edge_cases": accounting["edge_cases"],
            "duplicate_ids": accounting["duplicate_ids"],
            "provenance": accounting["provenance"],
            "human_labeled": accounting["human_labeled"],
            "labels_complete": not accounting["label_problems"],
            "minimums": accounting.get("minimums", {}),
            "minimums_ok": accounting.get("minimums_ok", True),
        },
        "thresholds": {
            "path": "benchmark/thresholds.json",
            "sha256": thresholds_mod.thresholds_sha256(),
            "frozen_lock_ok": frozen_ok,
            "frozen_lock_note": frozen_reason,
            "source_issues": thresholds.get("source_issues", []),
        },
        "cost_model": {
            "path": "benchmark/cost-model.json",
            "configured": bool(cost_model.get("configured")),
            "source": cost_model.get("source"),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "config": {
            "fault_injection_rate": fault_injection_rate,
            "faults_injected": injector.fired,
            "config_sha256": None,
        },
        "metrics": {"overall": overall, "per_detector": per_detector_metrics},
        "threshold_evaluation": threshold_results,
        "launch_claim": {
            "status": launch["status"],
            "reasons": blocked_reasons,
            "failed_checks": launch["failed_checks"],
            "criteria_source": "replynodes/replynodes-fetcher#487",
        },
        "cases": case_rows,
        "artifact_sha256": None,
    }

    config_blob = json.dumps(
        {"provider": artifact["provider"], "dataset_sha": accounting["sha256"],
         "thresholds_sha": artifact["thresholds"]["sha256"],
         "fault_injection_rate": fault_injection_rate},
        sort_keys=True,
    )
    artifact["config"]["config_sha256"] = sha256_text(config_blob)
    artifact["run_id"] = artifact["config"]["config_sha256"][:16]

    artifact = redact_value(artifact)
    artifact["artifact_sha256"] = _self_hash(artifact)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out_path, artifact)
        if not quiet:
            print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return artifact


def _self_hash(artifact: dict) -> str:
    clone = json.loads(json.dumps(artifact))
    clone["artifact_sha256"] = None
    return sha256_text(json.dumps(clone, sort_keys=True, ensure_ascii=False))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def comparable_view(artifact: dict) -> dict:
    """Strip volatile fields so two runs of the same provider can be compared."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in VOLATILE_KEYS}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    clone = strip(json.loads(json.dumps(artifact)))
    clone.get("cases", [])
    for case in clone.get("cases", []):
        case["result_record"].pop("latency_ms", None)
    return clone


def artifacts_equal(a: dict, b: dict) -> tuple[bool, list[str]]:
    ca, cb = comparable_view(a), comparable_view(b)
    problems: list[str] = []
    if ca["metrics"] != cb["metrics"]:
        problems.append("metrics differ between runs")
    if ca["threshold_evaluation"] != cb["threshold_evaluation"]:
        problems.append("threshold evaluation differs between runs")
    if [c.get("predicted") for c in ca["cases"]] != [c.get("predicted") for c in cb["cases"]]:
        problems.append("per-case predictions differ between runs")
    if ca["dataset"]["sha256"] != cb["dataset"]["sha256"]:
        problems.append("dataset hash differs between runs")
    return (not problems), problems