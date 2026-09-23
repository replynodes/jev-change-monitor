"""Release thresholds — frozen before held-out evaluation.

`benchmark/thresholds.json` is the source of truth and mirrors
replynodes/replynodes-fetcher#487. `benchmark/thresholds.lock.json` records the
SHA-256 of the thresholds file as committed before the first held-out run; any
edit after that invalidates the held-out evaluation and must be an explicit
issue update.

This module never writes thresholds. It only reads, verifies immutability and
evaluates observed metrics against them.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
THRESHOLDS_PATH = REPO_ROOT / "benchmark" / "thresholds.json"
LOCK_PATH = REPO_ROOT / "benchmark" / "thresholds.lock.json"
COST_MODEL_PATH = REPO_ROOT / "benchmark" / "cost-model.json"


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_thresholds() -> dict:
    return _load_json(THRESHOLDS_PATH)


def load_lock() -> dict:
    return _load_json(LOCK_PATH)


def load_cost_model() -> dict:
    return _load_json(COST_MODEL_PATH)


def thresholds_sha256() -> str:
    from jev_change_monitor.normalize import sha256_text

    return sha256_text(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def check_frozen() -> tuple[bool, str]:
    """Verify the committed thresholds file matches the pre-run lock."""
    lock = load_lock()
    expected = lock.get("thresholds_sha256")
    actual = thresholds_sha256()
    if expected != actual:
        return False, (
            f"thresholds.json changed after the pre-run lock "
            f"(locked {expected[:12]}…, actual {actual[:12]}…). "
            "A threshold change requires an explicit issue update before the next held-out run."
        )
    return True, "thresholds match the pre-run lock"


def evaluate(metrics_per_detector: dict, thresholds: dict) -> list[dict]:
    """Return one entry per threshold check, with observed value and pass/fail."""
    results: list[dict] = []
    for detector, spec in thresholds["detectors"].items():
        observed = metrics_per_detector.get(detector, {})
        for metric, rule in spec["metrics"].items():
            value = observed.get(metric)
            minimum = rule.get("min")
            maximum = rule.get("max")
            passed = None
            if value is None:
                passed = False
            elif minimum is not None:
                passed = value >= minimum
            elif maximum is not None:
                passed = value <= maximum
            results.append({
                "detector": detector,
                "metric": metric,
                "min": minimum,
                "max": maximum,
                "observed": value,
                "passed": passed,
            })
    return results


def launch_status(threshold_results: list[dict], evaluation_kind: str) -> dict:
    """Launch claim is only 'passed' for live Jev evaluation with all checks green."""
    failed = [r for r in threshold_results if not r["passed"]]
    if evaluation_kind != "live-jev":
        return {
            "status": "blocked",
            "reasons": [
                "no authorized live Jev runtime configured for this run; "
                "semantic thresholds are unverified",
                "deterministic-baseline numbers are pipeline evidence only and "
                "must not be presented as launch evidence",
            ],
            "failed_checks": len(failed),
        }
    if failed:
        return {"status": "failed", "reasons": [
            f"{r['detector']}.{r['metric']} observed {r['observed']} vs "
            f"{'min ' + str(r['min']) if r['min'] is not None else 'max ' + str(r['max'])}"
            for r in failed], "failed_checks": len(failed)}
    return {"status": "passed", "reasons": [], "failed_checks": 0}