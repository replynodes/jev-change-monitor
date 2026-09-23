"""Canonical change-event schema builder (event_version, event_id, timestamp).

The canonical event mirrors the hosted contract (replynodes/replynodes-fetcher
#485) minus internal tenant metadata: the public payload never exposes
workspace/internal fields. `monitor_id` and `run_id` are optional in the OSS
schema; the hosted pipeline populates them from its own identity store.

`event_id` is derived deterministically (UUIDv5 over case + evidence hashes)
so demo and benchmark artifacts are reproducible.
"""

from __future__ import annotations

import uuid

from jev_change_monitor import SCHEMA_VERSION

_EVENT_VERSION = "1.0"
_EVENT_TYPE = "monitor.change.v1"
_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def build_change_event(result: dict, request: dict, source: dict | None = None) -> dict:
    """Build a canonical change event from a typed detector result."""
    detector = request["detector"]
    evidence_sha = request["after"].get("sha256", "") or ""
    case_id = (source or {}).get("case_id", "demo")
    event_id = str(uuid.uuid5(_NAMESPACE, f"jev-change-monitor:{detector}:{case_id}:{evidence_sha}"))
    confidence = {
        "value": result.get("confidence"),
        "source": result.get("confidence_source"),
        "calibrated": False,
        "calibration_note": "Raw probability semantics only; calibration has not been established.",
    }
    event = {
        "event_version": _EVENT_VERSION,
        "event_id": event_id,
        "event_type": _EVENT_TYPE,
        "detector": detector,
        "url": request["url"],
        "change_type": result.get("change_type", "none"),
        "importance": result.get("importance", "low"),
        "meaningful": result.get("meaningful", False),
        "should_alert": result.get("should_alert", False),
        "summary": result.get("summary", ""),
        "confidence": confidence,
        "detected_at": request.get("detected_at"),
        "evidence": {
            "before_sha256": request["before"].get("sha256"),
            "after_sha256": evidence_sha,
            "before_captured_at": request["before"].get("captured_at"),
            "after_captured_at": request["after"].get("captured_at"),
        },
    }
    if source:
        event["monitor_id"] = f"fixture-{source['case_id']}"
        event["run_id"] = f"fixture-run-{source['case_id']}"
    return event