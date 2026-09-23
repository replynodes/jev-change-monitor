"""Live Jev provider via an OpenAI-compatible HTTP endpoint.

Configuration (environment only, never read from files):
- `JEV_ENDPOINT`  — chat-completions-style URL (required)
- `JEV_API_KEY`   — bearer token (required; never logged or printed; passed
  only inside the Authorization header)
- `JEV_MODEL`     — model id (optional; recorded in results as reported)

The repository cannot fabricate live Jev results. When `JEV_ENDPOINT` is
unset, this provider reports itself as not-configured and the benchmark emits
a machine-readable BLOCKED result for semantic thresholds.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from jev_change_monitor.detectors import DetectorSpec
from jev_change_monitor.providers.base import ProviderResponse
from jev_change_monitor.redact import env_flag


def _unwrap(payload: dict) -> dict:
    if "choices" in payload and isinstance(payload["choices"], list) and payload["choices"]:
        content = payload["choices"][0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return json.loads(content)
    return payload


class JevHttpProvider:
    name = "jev-http"
    mode = "live"

    def __init__(self, timeout_s: float = 60.0, retries: int = 2):
        self.timeout_s = timeout_s
        self.retries = retries
        self.endpoint = os.environ.get("JEV_ENDPOINT", "").strip()
        self.api_key = os.environ.get("JEV_API_KEY", "").strip()
        self.model = os.environ.get("JEV_MODEL", "").strip() or None

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def _request_body(self, detector: DetectorSpec, request: dict) -> dict:
        return {
            "model": self.model or detector.id,
            "messages": [
                {"role": "system", "content": detector.prompt_system},
                {
                    "role": "user",
                    "content": detector.prompt_user
                    .replace("<before>", request["before"]["content"])
                    .replace("<after>", request["after"]["content"]),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def judge(self, detector: DetectorSpec, request: dict) -> ProviderResponse:
        if not self.configured:
            return ProviderResponse(
                provider=self.name, mode=self.mode, result=None, raw=None,
                latency_ms=0.0, input_bytes=0, output_bytes=0, schema_valid=False,
                error_category="provider", provider_error="JEV_ENDPOINT/JEV_API_KEY not configured",
            )
        body = json.dumps(self._request_body(detector, request), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        attempt = 0
        last_error = None
        while attempt <= self.retries:
            attempt += 1
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    latency_ms = (time.perf_counter() - start) * 1000.0
                    payload = json.loads(resp.read().decode("utf-8"))
                result = _unwrap(payload)
                return ProviderResponse(
                    provider=self.name, mode=self.mode, result=result, raw=None,
                    latency_ms=latency_ms,
                    input_bytes=len(body),
                    output_bytes=len(json.dumps(result).encode("utf-8")),
                    schema_valid=True, error_category="none", retries=attempt - 1,
                    usage={"model": self.model, "endpoint": "configured (not logged)"},
                )
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                last_error = f"URLError: {exc.reason}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
        body_bytes = len(body)
        return ProviderResponse(
            provider=self.name, mode=self.mode, result=None, raw=None,
            latency_ms=0.0, input_bytes=body_bytes, output_bytes=0,
            schema_valid=False, error_category="provider", provider_error=last_error,
            retries=attempt - 1,
        )

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "model": self.model,
            "endpoint_configured": env_flag("JEV_ENDPOINT"),
            "api_key_configured": env_flag("JEV_API_KEY"),
            "timeout_s": self.timeout_s,
            "retries": self.retries,
        }


def available_live_providers() -> list[dict]:
    out = []
    for cls in (JevCommandProvider, JevHttpProvider):
        p = cls()
        out.append({"name": p.name, "configured": p.configured, "describe": p.describe()})
    return out