"""Detector-level metrics.

Primary decision = `should_alert` (the actionable event). The `meaningful`
judgement confusion matrix is reported alongside. Every metric definition is
spelled out in docs/benchmark-methodology.md; the benchmark artifact records
the definitions it used.

Metric definitions
------------------
- precision = TP / (TP + FP) over the primary decision
- recall = TP / (TP + FN)
- f1 = harmonic mean of precision and recall
- false_alert_rate = alerts raised on label-noise cases / label-noise cases
  (label.should_alert == False)
- false_negative_rate = missed label-alerts / label-alerts
- false_change_rate (price) = predicted meaningful change on label-unchanged
  cases / label-unchanged cases
- exact_price_accuracy = cases where amount AND currency AND direction match
- latency p50/p95 = observed provider latency per case (milliseconds)
- input_volume = normalized request bytes sent to the provider
- estimated_inference_cost = token estimate x configured cost model
  (placeholder rates; see benchmark/cost-model.json)
- schema_invalid_rate = provider results failing the result schema / cases
- provider_error_rate = provider errors / cases
"""

from __future__ import annotations

import math
from statistics import median


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def confusion(labels: list[bool], predictions: list[bool]) -> dict:
    tp = fp = fn = tn = 0
    for label, pred in zip(labels, predictions):
        if label and pred:
            tp += 1
        elif label and not pred:
            fn += 1
        elif not label and pred:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def detector_metrics(rows: list[dict], cost_model: dict) -> dict:
    """Compute metrics for one detector from per-case rows.

    Each row: {case_id, label, result_record, result, expected, price_pred}
    """
    evaluable = [r for r in rows if r["result_record"]["schema_valid"]
                 and r["result_record"]["error_category"] == "none"]
    labels_primary = [bool(r["label"]["should_alert"]) for r in evaluable]
    preds_primary = [bool((r["result"] or {}).get("should_alert")) for r in evaluable]
    cm_primary = confusion(labels_primary, preds_primary)

    labels_meaningful = [bool(r["label"]["meaningful"]) for r in evaluable]
    preds_meaningful = [bool((r["result"] or {}).get("meaningful")) for r in evaluable]
    cm_meaningful = confusion(labels_meaningful, preds_meaningful)

    noise_rows = [r for r in evaluable if not r["label"]["should_alert"]]
    alert_positive_rows = [r for r in evaluable if r["label"]["should_alert"]]
    false_alerts = sum(1 for r in noise_rows if (r["result"] or {}).get("should_alert"))
    false_negatives = sum(1 for r in alert_positive_rows if not (r["result"] or {}).get("should_alert"))

    precision = _safe_div(cm_primary["tp"], cm_primary["tp"] + cm_primary["fp"])
    recall = _safe_div(cm_primary["tp"], cm_primary["tp"] + cm_primary["fn"])
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)

    latencies = [r["result_record"]["latency_ms"] for r in rows]
    input_bytes = sum(r["result_record"]["input_bytes"] for r in rows)
    output_bytes = sum(r["result_record"]["output_bytes"] for r in rows)

    tokens_in = int(sum(math.ceil(r["result_record"]["input_bytes"] / 4) for r in rows))
    tokens_out = int(sum(math.ceil(r["result_record"]["output_bytes"] / 4) for r in rows))
    rates_configured = bool(cost_model.get("configured"))
    cost = None
    if rates_configured:
        cost = (tokens_in / 1_000_000) * cost_model["input_per_million_tokens"] + (
            tokens_out / 1_000_000) * cost_model["output_per_million_tokens"]

    invalid = sum(1 for r in rows if not r["result_record"]["schema_valid"])
    errors = sum(1 for r in rows if r["result_record"]["error_category"] != "none")

    metrics = {
        "cases": len(rows),
        "evaluable_cases": len(evaluable),
        "primary_decision": "should_alert",
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alert_rate": _safe_div(false_alerts, len(noise_rows)),
        "false_negative_rate": _safe_div(false_negatives, len(alert_positive_rows)),
        "false_alerts": false_alerts,
        "false_negatives": false_negatives,
        "confusion_primary": cm_primary,
        "confusion_meaningful": cm_meaningful,
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "input_volume_bytes": input_bytes,
        "output_volume_bytes": output_bytes,
        "estimated_input_tokens": tokens_in,
        "estimated_output_tokens": tokens_out,
        "estimated_inference_cost_usd": cost,
        "estimated_cost_available": rates_configured,
        "schema_invalid_rate": _safe_div(invalid, len(rows)),
        "provider_error_rate": _safe_div(errors, len(rows)),
        "schema_invalid_cases": invalid,
        "provider_error_cases": errors,
    }

    # price-specific extraction accuracy
    price_rows = [r for r in evaluable if r.get("expected", {}).get("price")]
    if price_rows:
        amount_ok = sum(1 for r in price_rows
                        if r["price_pred"].get("amount") == r["expected"]["price"]["amount"])
        currency_ok = sum(1 for r in price_rows
                          if (r["price_pred"].get("currency") or "").upper()
                          == (r["expected"]["price"].get("currency") or "").upper())
        direction_ok = sum(1 for r in price_rows
                           if r["price_pred"].get("direction") == r["expected"]["price"].get("direction"))
        exact = sum(1 for r in price_rows
                    if r["price_pred"].get("amount") == r["expected"]["price"]["amount"]
                    and (r["price_pred"].get("currency") or "").upper()
                    == (r["expected"]["price"].get("currency") or "").upper()
                    and r["price_pred"].get("direction") == r["expected"]["price"].get("direction"))
        metrics.update({
            "price_cases_with_expectation": len(price_rows),
            "price_amount_accuracy": _safe_div(amount_ok, len(price_rows)),
            "price_currency_accuracy": _safe_div(currency_ok, len(price_rows)),
            "price_direction_accuracy": _safe_div(direction_ok, len(price_rows)),
            "exact_price_accuracy": _safe_div(exact, len(price_rows)),
        })

    unchanged_rows = [r for r in evaluable if not r["label"]["meaningful"]]
    false_change = sum(1 for r in unchanged_rows if (r["result"] or {}).get("meaningful"))
    metrics["false_change_rate"] = _safe_div(false_change, len(unchanged_rows))
    metrics["label_unchanged_cases"] = len(unchanged_rows)
    metrics["label_alert_cases"] = len(alert_positive_rows)
    metrics["label_noise_cases"] = len(noise_rows)
    return metrics


def aggregate_metrics(per_detector: dict[str, dict]) -> dict:
    """Overall row: aggregate confusion matrices, weighted rates, total volume."""
    total = {
        "cases": 0, "evaluable_cases": 0, "false_alerts": 0, "false_negatives": 0,
        "input_volume_bytes": 0, "output_volume_bytes": 0,
        "estimated_input_tokens": 0, "estimated_output_tokens": 0,
        "schema_invalid_cases": 0, "provider_error_cases": 0,
        "confusion_primary": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        "confusion_meaningful": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        "label_alert_cases": 0, "label_noise_cases": 0, "label_unchanged_cases": 0,
    }
    latencies: list[float] = []
    for metrics in per_detector.values():
        for key in ("cases", "evaluable_cases", "false_alerts", "false_negatives",
                    "input_volume_bytes", "output_volume_bytes", "estimated_input_tokens",
                    "estimated_output_tokens", "schema_invalid_cases", "provider_error_cases",
                    "label_alert_cases", "label_noise_cases", "label_unchanged_cases"):
            total[key] += metrics[key]
        for conf in ("confusion_primary", "confusion_meaningful"):
            for k, v in metrics[conf].items():
                total[conf][k] += v
        if metrics.get("latency_ms_p50") is not None:
            latencies.append(metrics["latency_ms_p50"])

    cm = total["confusion_primary"]
    precision = _safe_div(cm["tp"], cm["tp"] + cm["fp"])
    recall = _safe_div(cm["tp"], cm["tp"] + cm["fn"])
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)

    # overall false-change = predicted-meaningful on label-unchanged, summed
    false_change_count = 0
    for metrics in per_detector.values():
        rate = metrics.get("false_change_rate")
        count = metrics.get("label_unchanged_cases", 0)
        if rate is not None and count:
            false_change_count += round(rate * count)

    total.update({
        "primary_decision": "should_alert",
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alert_rate": _safe_div(total["false_alerts"], total["label_noise_cases"]),
        "false_negative_rate": _safe_div(total["false_negatives"], total["label_alert_cases"]),
        "false_change_rate": _safe_div(false_change_count, total["label_unchanged_cases"]),
        "false_change_cases": false_change_count,
        "schema_invalid_rate": _safe_div(total["schema_invalid_cases"], total["cases"]),
        "provider_error_rate": _safe_div(total["provider_error_cases"], total["cases"]),
        "estimated_inference_cost_usd": None,
        "estimated_cost_available": all(m.get("estimated_cost_available", False)
                                        for m in per_detector.values()),
        "latency_ms_p50": median(latencies) if latencies else None,
        "latency_ms_p95": max(latencies) if latencies else None,
        "note": "Overall latency aggregates per-detector percentiles; per-detector values are authoritative.",
    })
    if total["estimated_cost_available"]:
        costs = [m.get("estimated_inference_cost_usd") for m in per_detector.values()]
        if all(c is not None for c in costs):
            total["estimated_inference_cost_usd"] = sum(costs)
    return total