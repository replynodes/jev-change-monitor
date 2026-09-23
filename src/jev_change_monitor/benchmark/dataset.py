"""Fixture dataset loading, validation and split accounting.

Held-out minimums (issue replynodes/replynodes-fetcher#487, frozen in
`benchmark/thresholds.json`):
- >= 100 held-out cases total
- >= 30 per detector (price, saas_pricing, product_change)
- >= 10 edge cases
- development/tuning fixtures live in a separate split and never count.
"""

from __future__ import annotations

import json
from pathlib import Path

from jev_change_monitor.normalize import sha256_text
from jev_change_monitor.schemas import validate_schema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"

SPLIT_FILES = {
    "held_out": DATASETS_DIR / "held_out" / "held-out-cases.jsonl",
    "dev": DATASETS_DIR / "dev" / "dev-cases.jsonl",
}

MIN_HELD_OUT_TOTAL = 100
MIN_PER_DETECTOR = 30
MIN_EDGE_CASES = 10
DETECTORS = ("price", "saas_pricing", "product_change")


def split_path(split: str) -> Path:
    try:
        return SPLIT_FILES[split]
    except KeyError as exc:
        raise ValueError(f"unknown split {split!r}; choose from {sorted(SPLIT_FILES)}") from exc


def dataset_sha256(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def load_cases(split: str, validate: bool = True) -> list[dict]:
    path = split_path(split)
    cases: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if validate:
                errors = validate_schema(case, "fixture-case")
                if errors:
                    raise ValueError(f"{path}:{lineno} ({case.get('case_id')}): {'; '.join(errors)}")
            cases.append(case)
    return cases


def duplicate_ids(cases: list[dict]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for case in cases:
        cid = case.get("case_id", "")
        if cid in seen:
            dups.append(cid)
        seen.add(cid)
    return dups


def provenance_summary(cases: list[dict]) -> dict:
    out: dict[str, int] = {}
    missing = 0
    for case in cases:
        prov = case.get("provenance")
        if not prov:
            missing += 1
            continue
        out[prov] = out.get(prov, 0) + 1
    return {"counts": dict(sorted(out.items())), "missing": missing}


def label_completeness(cases: list[dict]) -> list[str]:
    """Every case must carry the label fields required by #487."""
    problems: list[str] = []
    for case in cases:
        cid = case.get("case_id", "<no-id>")
        label = case.get("label") or {}
        for field in ("meaningful", "should_alert", "rationale", "labeling"):
            if field not in label:
                problems.append(f"{cid}: label.{field} missing")
        labeling = label.get("labeling") or {}
        for field in ("method", "labeler", "review_status", "rubric_version"):
            if not labeling.get(field):
                problems.append(f"{cid}: label.labeling.{field} missing")
        detail = case.get("provenance_detail") or {}
        for field in ("kind", "license", "permission"):
            if not detail.get(field):
                problems.append(f"{cid}: provenance_detail.{field} missing")
        if case.get("detector") == "price" and label.get("meaningful") is True:
            exp = (case.get("expected") or {}).get("price")
            if not exp:
                problems.append(f"{cid}: price expectation missing for a meaningful price change")
    return problems


def split_accounting(split: str) -> dict:
    """Programmatic accounting for one split (used by `validate` and the runner)."""
    cases = load_cases(split, validate=True)
    per_detector = {d: 0 for d in DETECTORS}
    edge = 0
    for case in cases:
        det = case["detector"]
        per_detector[det] = per_detector.get(det, 0) + 1
        if case.get("edge_case"):
            edge += 1
    report = {
        "split": split,
        "path": str(split_path(split).relative_to(REPO_ROOT)),
        "sha256": dataset_sha256(split_path(split)),
        "cases_total": len(cases),
        "per_detector": per_detector,
        "edge_cases": edge,
        "duplicate_ids": duplicate_ids(cases),
        "provenance": provenance_summary(cases),
        "label_problems": label_completeness(cases),
        "human_labeled": all(
            (case.get("label", {}).get("labeling", {}).get("review_status") == "independent-review-complete")
            for case in cases
        ),
    }
    if split == "held_out":
        minimums = {
            "cases_total": len(cases) >= MIN_HELD_OUT_TOTAL,
            **{f"detector_{d}": per_detector.get(d, 0) >= MIN_PER_DETECTOR for d in DETECTORS},
            "edge_cases": edge >= MIN_EDGE_CASES,
            "no_duplicate_ids": not report["duplicate_ids"],
            "provenance_complete": report["provenance"]["missing"] == 0,
            "labels_complete": not report["label_problems"],
        }
        report["minimums"] = minimums
        report["minimums_ok"] = all(minimums.values())
    return report