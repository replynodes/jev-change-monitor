"""Deterministic rule-based baseline provider (label-blind).

This is NOT Jev and must never be presented as launch evidence. It exists so
the benchmark pipeline, metrics and artifact machinery run end-to-end without
any network/model dependency, and so the CI gate validates the pipeline with
real (if weak) scores.

Rules are documented in docs/detector-contracts.md. The provider never reads
the fixture label; it only sees the normalized before/after content.
"""

from __future__ import annotations

import re
import time

from jev_change_monitor.detectors import DetectorSpec
from jev_change_monitor.normalize import (
    diff_lines,
    extract_price_tokens,
    normalize_html,
    pick_price,
    sha256_text,
)
from jev_change_monitor.providers.base import ProviderResponse

_INJECTION_RE = re.compile(
    r"(ignore (all |any )?(previous|prior|above)|system prompt|you are now|"
    r"disregard (earlier|previous)|new instructions|override your)",
    re.IGNORECASE,
)

_SAAS_TRIGGERS = (
    "price", "plan", "limit", "seat", "entitlement", "add-on", "addon",
    "trial", "billing period", "monthly", "annual", "usage", "overage", "API calls",
)
_SAAS_TRIGGER_RE = re.compile("|".join(re.escape(t) for t in _SAAS_TRIGGERS), re.IGNORECASE)

_PRODUCT_TRIGGERS = (
    "added", "removed", "deprecated", "deprecation", "launch", "now supports",
    "integration", "support for", "new feature", "breaking change", "api version",
    "export", "import", "coming soon", "beta",
)
_PRODUCT_TRIGGER_RE = re.compile("|".join(re.escape(t) for t in _PRODUCT_TRIGGERS), re.IGNORECASE)

_COSMETIC_RE = re.compile(
    r"(testimonial|footer|copyright|color|layout|css|typo|hero|nav|logo|"
    r"cookie|spacing|style=|font)",
    re.IGNORECASE,
)


def _injection_free_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if not _INJECTION_RE.search(ln)]


def _extraction(before: dict | None, after: dict | None, direction: str) -> dict:
    """Documented price-extraction contract (see docs/detector-contracts.md)."""
    return {
        "amount": after["amount"] if after else None,
        "currency": after["currency"] if after else None,
        "period": after["period"] if after else None,
        "amount_before": before["amount"] if before else None,
        "currency_before": before["currency"] if before else None,
        "direction": direction,
    }


def _judge_price(request: dict) -> dict:
    before = request["before"]["content"]
    after = request["after"]["content"]
    before_text = normalize_html(before)
    after_text = normalize_html(after)
    injection = bool(_INJECTION_RE.search(before_text + after_text))
    bt = extract_price_tokens(before_text)
    at = extract_price_tokens(after_text)
    picked = pick_price(bt, at)

    details = {"injection_suspected": injection, "before_candidates": len(bt),
               "after_candidates": len(at), "picked": None}
    if not picked:
        details["extraction"] = _extraction(None, None, "unknown")
        return {
            "valid": True, "meaningful": False, "change_type": "none",
            "importance": "low", "should_alert": False,
            "confidence": 0.0, "confidence_source": "heuristic-rule-score",
            "summary": "No price tokens found in either snapshot.",
            "details": details, "signals": {"prompt_injection_suspected": injection},
        }
    b, a = picked
    details["picked"] = {"before": b, "after": a}
    same_currency = (b["currency"] or "").lower() == (a["currency"] or "").lower()
    same_amount = b["amount"] == a["amount"]
    if same_amount and same_currency:
        details["extraction"] = _extraction(b, a, "unchanged")
        return {
            "valid": True, "meaningful": False, "change_type": "none",
            "importance": "low", "should_alert": False,
            "confidence": 0.75, "confidence_source": "heuristic-rule-score",
            "summary": "Price unchanged.",
            "details": details, "signals": {"prompt_injection_suspected": injection},
        }
    direction = "unknown"
    if same_currency and not same_amount:
        direction = "up" if a["amount"] > b["amount"] else "down"
    details["extraction"] = _extraction(b, a, direction)
    meaningful = True
    change_type = "price_change"
    if not same_amount and same_currency:
        change_type = "price_increase" if a["amount"] > b["amount"] else "price_drop"
    elif not same_currency:
        change_type = "currency_change"
    return {
        "valid": True, "meaningful": meaningful, "change_type": change_type,
        "importance": "high", "should_alert": True,
        "confidence": 0.6, "confidence_source": "heuristic-rule-score",
        "summary": f"Price changed {b['amount']} {b['currency']} -> {a['amount']} {a['currency']}.",
        "details": details, "signals": {"prompt_injection_suspected": injection},
    }


def _judge_saas(request: dict) -> dict:
    before_text = normalize_html(request["before"]["content"])
    after_text = normalize_html(request["after"]["content"])
    injection = bool(_INJECTION_RE.search(before_text + after_text))
    diff = diff_lines(before_text, after_text)
    changed = diff["added"] + diff["removed"]
    # remove injection-looking lines from evidence
    changed_clean = _injection_free_lines(changed)
    triggers = [ln for ln in changed_clean if _SAAS_TRIGGER_RE.search(ln)]
    cosmetic_only = all(_COSMETIC_RE.search(ln) for ln in changed_clean) if changed_clean else False
    meaningful = bool(triggers) and not cosmetic_only
    alert = meaningful
    change_type = "other_meaningful" if meaningful else "none"
    importance = "high" if meaningful else "low"
    summary = "No SaaS pricing-relevant change." if not meaningful else (
        f"{len(triggers)} pricing-relevant line(s) changed; heuristic trigger match.")
    return {
        "valid": True, "meaningful": meaningful, "change_type": change_type,
        "importance": importance, "should_alert": alert,
        "confidence": 0.5 if meaningful else 0.3,
        "confidence_source": "heuristic-rule-score",
        "summary": summary,
        "details": {"trigger_lines": triggers[:10], "cosmetic_only": cosmetic_only,
                    "injection_suspected": injection},
        "signals": {"prompt_injection_suspected": injection},
    }


def _judge_product(request: dict) -> dict:
    before_text = normalize_html(request["before"]["content"])
    after_text = normalize_html(request["after"]["content"])
    injection = bool(_INJECTION_RE.search(before_text + after_text))
    diff = diff_lines(before_text, after_text)
    changed = _injection_free_lines(diff["added"] + diff["removed"])
    product_hits = [ln for ln in changed if _PRODUCT_TRIGGER_RE.search(ln)]
    cosmetic_hits = [ln for ln in changed if _COSMETIC_RE.search(ln)]
    # a change that is purely cosmetic is noise, a change with feature language is meaningful
    meaningful = bool(product_hits) and not (changed and set(cosmetic_hits) == set(changed))
    alert = meaningful
    change_type = "none"
    if meaningful:
        lower = " ".join(product_hits).lower()
        if re.search(r"deprecat", lower):
            change_type = "deprecation"
        elif re.search(r"removed|dropping|end of", lower):
            change_type = "feature_removed"
        elif re.search(r"added|now supports|launch|new|beta|coming soon", lower):
            change_type = "feature_added"
        else:
            change_type = "capability_change"
    summary = "No meaningful product change detected." if not meaningful else (
        f"Product change signalled by {len(product_hits)} feature-language line(s).")
    return {
        "valid": True, "meaningful": meaningful, "change_type": change_type,
        "importance": "high" if meaningful else "low", "should_alert": alert,
        "confidence": 0.55 if meaningful else 0.3,
        "confidence_source": "heuristic-rule-score",
        "summary": summary,
        "details": {"product_lines": product_hits[:10], "cosmetic_lines": cosmetic_hits[:5],
                    "injection_suspected": injection},
        "signals": {"prompt_injection_suspected": injection},
    }


_judgers = {
    "price": _judge_price,
    "saas_pricing": _judge_saas,
    "product_change": _judge_product,
}


class HeuristicBaselineProvider:
    """Deterministic, label-blind rule baseline. Mode: deterministic."""

    name = "heuristic-baseline"
    mode = "deterministic"

    def judge(self, detector: DetectorSpec, request: dict) -> ProviderResponse:
        start = time.perf_counter()
        raw_result = _judgers[detector.id](request)
        latency_ms = (time.perf_counter() - start) * 1000.0
        content = request["before"]["content"] + "\n" + request["after"]["content"]
        return ProviderResponse(
            provider=self.name,
            mode=self.mode,
            result=raw_result,
            raw=None,
            latency_ms=latency_ms,
            input_bytes=len(request["before"]["content"].encode("utf-8"))
            + len(request["after"]["content"].encode("utf-8")),
            output_bytes=len(str(raw_result).encode("utf-8")),
            schema_valid=True,
            error_category="none",
            usage={"input_chars": len(content), "rules": "documented in docs/detector-contracts.md"},
        )

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "model": None,
            "endpoint_configured": False,
            "notes": "Local deterministic baseline. Not Jev; never launch evidence.",
        }