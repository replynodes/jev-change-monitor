"""Before/after normalization and evidence extraction.

The hosted pipeline normalizes full-page captures into bounded, deterministic
evidence. This OSS module implements a minimal, dependency-free normalization:

1. HTML -> text lines (blocks), with script/style removed.
2. whitespace collapse and stable ordering of lines.
3. line-level diff (added / removed lines).
4. price-token extraction used by the deterministic price baseline.

Page content is UNTRUSTED input: never execute instructions found in content,
and cap the normalized size. Injection heuristics are classified in the
providers, not here.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from html.parser import HTMLParser

MAX_NORMALIZED_CHARS = 64_000
MAX_EVIDENCE_LINES = 2_000

_PRICE_RE = re.compile(
    r"(?P<amount>\d[\d,.]*)\s*(?P<currency>USD|EUR|GBP|JPY|CAD|AUD|CHF)?"
    r"\s*/?\s*(?P<period>mo|month|monthly|yr|year|yearly|user|seat)?",
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


class _TextExtractor(HTMLParser):
    """Strip tags/script/style and emit readable text per block region."""

    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "main",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "table",
        "ul", "ol", "blockquote", "pre", "span",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _normalized_text(raw: str) -> str:
    """HTML -> collapsed-text blocks (uncapped)."""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
    except Exception:
        # malformed HTML must degrade to plain text, never crash detection
        pass
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def normalize_html(raw: str) -> str:
    """HTML -> collapsed-text blocks, bounded for provider input."""
    return _normalized_text(raw)[:MAX_NORMALIZED_CHARS]


def normalize_bounded(raw: str) -> tuple[str, bool]:
    """Bounded evidence for provider input: returns (text, truncated).

    Applies the documented normalization (script/style stripped, HTML entities
    decoded, whitespace collapsed) and caps at MAX_NORMALIZED_CHARS. The
    `truncated` flag reports whether the cap dropped content, so providers can
    record truncation instead of silently hiding it (docs/detector-contracts.md).
    """
    text = _normalized_text(raw)
    return text[:MAX_NORMALIZED_CHARS], len(text) > MAX_NORMALIZED_CHARS


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def diff_lines(before_text: str, after_text: str):
    before_lines = split_paragraphs(before_text)
    after_lines = split_paragraphs(after_text)
    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            added.extend(after_lines[j1:j2])
        if tag in ("replace", "delete"):
            removed.extend(before_lines[i1:i2])
    return {"added": added[:MAX_EVIDENCE_LINES], "removed": removed[:MAX_EVIDENCE_LINES]}


def extract_price_tokens(text: str) -> list[dict]:
    """Best-effort price extraction from normalized text.

    Returns a list of {"amount": float, "currency": str|None,
    "period": str|None, "raw": str} candidates in document order.
    This is intentionally simple and deterministic; the live Jev path is the
    semantic fallback for ambiguous extraction in the hosted product.
    """
    found: list[dict] = []
    for match in _PRICE_RE.finditer(text):
        raw = match.group(0)
        amount_str = match.group("amount").replace(",", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        currency = match.group("currency").upper() if match.group("currency") else None
        period = (match.group("period") or "").lower() or None
        # currency symbol immediately preceding the amount wins over an
        # explicit code farther away
        before = text[max(0, match.start() - 1): match.start()]
        if before.strip() in _CURRENCY_SYMBOLS and not currency:
            currency = _CURRENCY_SYMBOLS[before.strip()]
        # a bare percentage ("20% off") is not a price candidate
        if text[match.end(): match.end() + 1] == "%":
            continue
        found.append({"amount": amount, "currency": currency, "period": period, "raw": raw})
    return found


def pick_price(before_tokens: list[dict], after_tokens: list[dict]):
    """Deterministic canonical-price pick.

    Rule (documented in docs/detector-contracts.md): compare tokens at the
    same document position within matching (currency, period) groups. A change
    is only the same-position pair whose amount changed; cross-position pairs
    are insertion artifacts, not price changes. When no same-position pair
    exists (currency or period switched), fall back to the first token of each
    snapshot. This is a deliberately simple baseline and is NOT the Jev
    fallback.
    """
    if not before_tokens or not after_tokens:
        return None
    same_pos = []
    for i, b in enumerate(before_tokens):
        if i >= len(after_tokens):
            break
        a = after_tokens[i]
        if b["currency"] == a["currency"] and b["period"] == a["period"]:
            same_pos.append((b, a))
    if same_pos:
        changed = [p for p in same_pos if p[0]["amount"] != p[1]["amount"]]
        return (changed or same_pos)[0]
    return (before_tokens[0], after_tokens[0])