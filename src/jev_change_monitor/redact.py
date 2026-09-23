"""Redaction helpers.

Providers and the benchmark result artifact must never contain credential
values. Everything written to `results/` passes through `redact_value()` so
that tokens, keys and secrets are replaced with a literal placeholder even if
a provider accidentally echoes them.

Redaction is **field-aware**:

- Values under integrity fields (any key matching `sha256`, `hash`, `digest`
  or `checksum` — including `config_sha256`, `dataset.sha256`,
  `thresholds.sha256` and `artifact_sha256`) are never redacted: a recorded
  SHA-256 must stay byte-exact so downstream integrity checks see the genuine
  digest.
- Everywhere else, only specific, known credential shapes are redacted
  (`sk-...`, `gh[pousr]_...`, `rn_live_/rn_test_...`, AWS-style `AKIA...`
  and `Bearer <token>`). A broad catch-all such as "any 21+ alphanumeric
  token starting with e/ei" is deliberately NOT used: it corrupted valid
  e-prefixed SHA-256 digests while adding no real coverage beyond the
  explicit patterns.

The repository additionally never prints values of environment variables; the
only allowed signal is a boolean "configured / not configured" flag.
"""

import re

_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b"),          # OpenAI-style
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),      # GitHub tokens
    re.compile(r"\b(rn_(live|test)_[A-Za-z0-9]{16,})\b"), # ReplyNodes API keys
    re.compile(r"\b(ak?ia[0-9A-Za-z]{16,})\b", re.IGNORECASE),  # AWS-style (AKIA... keys are uppercase)
    re.compile(r"\b(Bearer\s+)[A-Za-z0-9._\-]{12,}\b", re.IGNORECASE),
]

_PLACEHOLDER = "[REDACTED]"

# Integrity fields must survive byte-exact (never redacted).
_HASH_KEY_RE = re.compile(r"(sha256|sha-?256|hash|digest|checksum)", re.IGNORECASE)


def is_hash_field(key) -> bool:
    """True when a dict key names a recorded hash/integrity field."""
    return isinstance(key, str) and bool(_HASH_KEY_RE.search(key))


def redact_string(value: str) -> str:
    out = value
    for pat in _PATTERNS:
        out = pat.sub(lambda m: f"{m.group(1)}{_PLACEHOLDER}" if m.lastindex else _PLACEHOLDER, out)
    return out


def redact_value(value, key=None):
    """Recursively redact strings inside JSON-able structures, field-aware.

    Values under hash/integrity keys are passed through unchanged so recorded
    SHA-256 digests (including e-prefixed ones) never get corrupted.
    """
    if isinstance(value, str):
        if is_hash_field(key):
            return value
        return redact_string(value)
    if isinstance(value, dict):
        return {k: redact_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v, key=key) for v in value]
    return value


def env_bool(name: str) -> bool:
    """Boolean presence flag only; never the value."""
    import os

    return bool(os.environ.get(name))


def env_flag(name: str) -> str:
    """'configured' | 'not-configured' — never the value."""
    import os

    return "configured" if os.environ.get(name) else "not-configured"