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

from jev_change_monitor.normalize import normalize_html, sha256_text
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

# Fingerprints checked for cross-split leakage: each before/after snapshot,
# raw and normalized, alone and concatenated (issue replynodes/replynodes-fetcher#487).
CONTENT_HASH_KINDS = (
    "before_raw", "after_raw", "before_normalized", "after_normalized",
    "pair_raw", "pair_normalized",
)


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


def content_hashes(case: dict) -> dict[str, str]:
    """Content fingerprints for one case.

    Returns one SHA-256 per CONTENT_HASH_KIND: the before/after snapshot raw,
    each normalized, and the raw/normalized concatenation. The normalized
    fingerprints close the loophole of raw-only checks (a tuner could memorise
    the *effectively visible* page, not just its bytes).
    """
    before = case.get("before", {}).get("content")
    after = case.get("after", {}).get("content")
    if not isinstance(before, str) or not isinstance(after, str):
        raise ValueError(f"{case.get('case_id', '<no-id>')}: snapshot content missing")
    before_norm = normalize_html(before)
    after_norm = normalize_html(after)
    return {
        "before_raw": sha256_text(before),
        "after_raw": sha256_text(after),
        "before_normalized": sha256_text(before_norm),
        "after_normalized": sha256_text(after_norm),
        "pair_raw": sha256_text(before + "\x00" + after),
        "pair_normalized": sha256_text(before_norm + "\x00" + after_norm),
    }


def cross_split_content_overlap() -> list[dict]:
    """Any shared before/after content between held_out and dev.

    #487 requires development/tuning and held-out evaluation to be separate.
    Identical page content in both splits would let a tuner memorise exact
    held-out pages, so duplicate case_ids are not enough: every snapshot is
    fingerprinted (raw and normalized, alone and concatenated) and any hash
    present in both splits is a leak. `jev-monitor validate` fails on any hit.
    """
    fingerprints: dict[str, dict] = {}
    for split in ("held_out", "dev"):
        fingerprints[split] = {}
        for case in load_cases(split, validate=False):
            try:
                hashes = content_hashes(case)
            except ValueError as exc:
                raise ValueError(f"{split}:{exc}") from exc
            for kind, digest in hashes.items():
                fingerprints[split].setdefault(kind, {}).setdefault(digest, []).append(
                    case.get("case_id", "<no-id>")
                )
    collisions: list[dict] = []
    for kind in CONTENT_HASH_KINDS:
        held = fingerprints["held_out"].get(kind, {})
        dev = fingerprints["dev"].get(kind, {})
        for digest in sorted(set(held) & set(dev)):
            collisions.append({
                "kind": kind,
                "sha256": digest,
                "held_out": sorted(held[digest]),
                "dev": sorted(dev[digest]),
            })
    return collisions


def duplicate_snapshot_groups(split: str) -> int:
    """Count content hashes shared between cases *within* one split.

    Informational only: cases in the same split legitimately share a page (the
    same site observed at different times, byte-identical no-change pairs, or
    promo add/remove pairs). Cross-split sharing is the leak; see
    `cross_split_content_overlap`.
    """
    shared: set[str] = set()
    seen: dict[str, list[str]] = {}
    for case in load_cases(split, validate=False):
        for digest in content_hashes(case).values():
            if digest in seen:
                shared.add(digest)
            else:
                seen[digest] = [case.get("case_id", "<no-id>")]
    return len(shared)