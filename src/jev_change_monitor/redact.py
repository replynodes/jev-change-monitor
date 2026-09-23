"""Redaction helpers.

Providers and the benchmark result artifact must never contain credential
values. Everything written to `results/` passes through `redact_strings()`
so that tokens, keys and secrets are replaced with a literal placeholder even
if a provider accidentally echoes them.

The repository additionally never prints values of environment variables; the
only allowed signal is a boolean "configured / not configured" flag.
"""

import re

_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b"),          # OpenAI-style
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),      # GitHub tokens
    re.compile(r"\b(rn_(live|test)_[A-Za-z0-9]{16,})\b"), # ReplyNodes API keys
    re.compile(r"\b(ak?ia[0-9A-Za-z]{16,})\b"),           # AWS-style
    re.compile(r"\b(Bearer\s+)[A-Za-z0-9._\-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(ei?[0-9a-zA-Z]{20,})\b", re.IGNORECASE),
]

_PLACEHOLDER = "[REDACTED]"


def redact_string(value: str) -> str:
    out = value
    for pat in _PATTERNS:
        out = pat.sub(lambda m: f"{m.group(1)}{_PLACEHOLDER}" if m.lastindex else _PLACEHOLDER, out)
    return out


def redact_value(value):
    """Recursively redact strings inside JSON-able structures."""
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


def env_bool(name: str) -> bool:
    """Boolean presence flag only; never the value."""
    import os

    return bool(os.environ.get(name))


def env_flag(name: str) -> str:
    """'configured' | 'not-configured' — never the value."""
    import os

    return "configured" if os.environ.get(name) else "not-configured"