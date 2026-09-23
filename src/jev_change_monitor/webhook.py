"""HMAC-SHA256 webhook signing and verification (canonical contract).

Canonical headers (per replynodes/replynodes-fetcher #485):
- `X-ReplyNodes-Event-Id`
- `X-ReplyNodes-Timestamp`
- `X-ReplyNodes-Signature`

Signature: HMAC-SHA256 over the exact bytes `timestamp + "." + raw_body`,
hex-encoded. Replay protection: receiver enforces a configurable timestamp
tolerance (default 300 s). Verification uses constant-time comparison.

All secrets in this module are labeled FIXTURE secrets for the demo
sender/receiver. Production secrets live in the hosted secrets manager and
never appear in this repository.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

FIXTURE_SECRET = "fixture:recipe-demo-change-me-2026"  # clearly labeled fixture, NOT a secret
X_EVENT_ID = "X-ReplyNodes-Event-Id"
X_TIMESTAMP = "X-ReplyNodes-Timestamp"
X_SIGNATURE = "X-ReplyNodes-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


def get_webhook_secret() -> bytes:
    """Fixture secret from env (defaults to the labeled fixture value)."""
    value = os.environ.get("WEBHOOK_FIXTURE_SECRET", FIXTURE_SECRET)
    return value.encode("utf-8")


def sign(secret: bytes, timestamp: str, raw_body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + raw_body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify(
    secret: bytes,
    timestamp: str,
    raw_body: bytes,
    signature: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason). Constant-time comparison; replay tolerance."""
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False, "invalid timestamp format"
    now = now if now is not None else time.time()
    if abs(now - ts) > tolerance_seconds:
        return False, f"timestamp outside tolerance window ({tolerance_seconds}s)"
    expected = sign(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature):
        return False, "signature mismatch"
    return True, "ok"


def build_signed_request(
    event: dict,
    secret: bytes,
    timestamp: str | None = None,
) -> tuple[dict, dict]:
    """Build (headers, raw_body) for a canonical event (fixture sender helper)."""
    raw_body = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
    timestamp = timestamp or str(int(time.time()))
    headers = {
        X_EVENT_ID: event["event_id"],
        X_TIMESTAMP: timestamp,
        X_SIGNATURE: sign(secret, timestamp, raw_body),
        "Content-Type": "application/json",
        "User-Agent": "jev-change-monitor-fixture-sender/0.1",
    }
    return headers, raw_body


class VerifiedPayload:
    def __init__(self, ok: bool, reason: str, event: dict | None, headers: dict):
        self.ok = ok
        self.reason = reason
        self.event = event
        self.headers = headers


def _header(headers: dict, name: str) -> str | None:
    """Case-insensitive header lookup (urllib lowercases sent names)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name.lower())


def verify_request(
    headers: dict,
    raw_body: bytes,
    secret: bytes,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> VerifiedPayload:
    """Verify an incoming webhook request (fixture receiver helper)."""
    event_id = _header(headers, X_EVENT_ID)
    timestamp = _header(headers, X_TIMESTAMP)
    signature = _header(headers, X_SIGNATURE)
    if not (event_id and timestamp and signature):
        return VerifiedPayload(False, "missing required headers", None, headers)
    ok, reason = verify(secret, timestamp, raw_body, signature, tolerance_seconds, now)
    if not ok:
        return VerifiedPayload(False, reason, None, headers)
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return VerifiedPayload(False, f"invalid JSON body: {exc}", None, headers)
    if event.get("event_id") != event_id:
        return VerifiedPayload(False, "event_id header does not match body", None, headers)
    return VerifiedPayload(True, "ok", event, headers)