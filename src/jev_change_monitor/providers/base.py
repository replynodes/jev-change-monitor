"""Provider runtime interface.

A provider turns a DetectorRequest into a typed result envelope. Two modes
exist:

- `deterministic`: local rule-based baselines. Environment-independent,
  reproducible, label-blind. Used for CI gates and as a baseline, never as
  launch evidence; results are tagged `synthetic-deterministic`.
- `live`: an explicitly configured Jev runtime (HTTP endpoint or command).
  Requires `JEV_ENDPOINT`+`JEV_API_KEY` (+optional `JEV_MODEL`) or
  `JEV_COMMAND`. When unconfigured, the benchmark reports a machine-readable
  BLOCKED result instead of fabricating semantic scores.

Provider implementations must never read, log or print credential *values*;
they only report configuration flags (see `redact.env_flag`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from jev_change_monitor.detectors import DetectorSpec


@dataclass
class ProviderResponse:
    provider: str
    mode: str  # "deterministic" | "live"
    result: dict | None
    raw: str | None
    latency_ms: float
    input_bytes: int
    output_bytes: int
    schema_valid: bool
    schema_errors: list[str] = field(default_factory=list)
    provider_error: str | None = None
    error_category: str = "none"  # none|timeout|http|malformed|schema_invalid|provider
    retries: int = 0
    usage: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        """Compact per-case record for result artifacts (values only; no secrets)."""
        return {
            "provider": self.provider,
            "mode": self.mode,
            "schema_valid": self.schema_valid,
            "error_category": self.error_category,
            "provider_error": self.provider_error,
            "latency_ms": round(self.latency_ms, 2),
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
            "retries": self.retries,
        }


class JudgementProvider(Protocol):
    name: str
    mode: str

    def judge(self, detector: DetectorSpec, request: dict) -> ProviderResponse:
        ...

    def describe(self) -> dict:
        ...