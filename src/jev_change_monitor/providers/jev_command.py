"""Live Jev provider via an explicitly configured local command.

Contract: `JEV_COMMAND` names an executable that reads one JSON object on
stdin and writes one JSON object (the detector result, optionally wrapped in
an OpenAI-style `{"choices":[{"message":{"content":"..."}}]}` envelope) on
stdout. The input carries the detector prompt text plus bounded, normalized
evidence per snapshot (see `bounded_evidence` in providers/base.py): script/
style stripped, entities decoded, whitespace collapsed, capped at 64,000
chars, with `evidence.truncated` recording whether the cap dropped content.
Raw capture HTML is not forwarded; `sha256`/`byte_count` identify the capture
the evidence came from. Never executes page content.

Execution contract: `JEV_COMMAND` is parsed with Python's POSIX `shlex` into
an argv list and executed WITHOUT a shell (`shell=False`) — no pipes,
redirections, globbing, or environment expansion. It must name a single
executable plus literal arguments. A deliberate shell pipeline/expansion
contract is not supported.

Credential discipline: the value passed to the command is environment-only;
the command string itself must not contain secrets. The raw command value is
never persisted or printed: `describe()` reports only a configured flag plus
an executable basename when it survives a strict allowlist (credential-shaped
names yield `command_label: null`), and failure messages never echo argv. If
`JEV_COMMAND` is unset, this provider reports itself as not-configured and
never runs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

from jev_change_monitor.detectors import DetectorSpec
from jev_change_monitor.normalize import MAX_NORMALIZED_CHARS
from jev_change_monitor.providers.base import ProviderResponse, bounded_evidence
from jev_change_monitor.redact import env_flag, redact_value

# Strict allowlist for the display label: a plain portable symbol token.
# Anything with spaces, slashes, shell metacharacters or credential-shaped
# prefixes fails and yields `command_label: null` instead of exposing text.
_COMMAND_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def safe_command_label(command: str) -> str | None:
    """Executable basename of a configured command, only when it is safe.

    The raw JEV_COMMAND value must never be persisted or printed. This
    returns either a fixed, allowlisted basename (args are never included),
    or None when the executable token is missing, unsafe, or itself
    credential-shaped (so a secret string can never ride out as a label).
    """
    if not command:
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    base = os.path.basename(argv[0])
    if not base or base in (".", ".."):
        return None
    if not _COMMAND_LABEL_RE.match(base):
        return None
    if redact_value(base) != base:
        # credential-shaped executable name: redaction would mask it, so we
        # withhold the label rather than emit a redacted or leaking form.
        return None
    return base


def _unwrap(payload: dict | str) -> dict:
    """Accept an OpenAI-style envelope or a bare result object."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{"):
            return json.loads(text)
        raise ValueError("command returned non-JSON output")
    if "choices" in payload and isinstance(payload["choices"], list) and payload["choices"]:
        content = payload["choices"][0].get("message", {}).get("content")
        if content:
            return json.loads(content)
    return payload


class JevCommandProvider:
    name = "jev-command"
    mode = "live"

    def __init__(self, timeout_s: float = 60.0, retries: int = 2):
        self.timeout_s = timeout_s
        self.retries = retries
        self.command = os.environ.get("JEV_COMMAND", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.command)

    def judge(self, detector: DetectorSpec, request: dict) -> ProviderResponse:
        if not self.configured:
            return ProviderResponse(
                provider=self.name, mode=self.mode, result=None, raw=None,
                latency_ms=0.0, input_bytes=0, output_bytes=0, schema_valid=False,
                error_category="provider", provider_error="JEV_COMMAND not configured",
            )
        evidence, meta = bounded_evidence(request)
        prompt_request = {
            "detector": detector.id,
            "system": detector.prompt_system,
            "user": detector.prompt_user.replace("<before>", evidence["before"])
            .replace("<after>", evidence["after"]),
            "url": request.get("url"),
            "before": {
                "evidence": evidence["before"],
                "sha256": request["before"]["sha256"],
                "byte_count": request["before"]["byte_count"],
                "captured_at": request["before"]["captured_at"],
            },
            "after": {
                "evidence": evidence["after"],
                "sha256": request["after"]["sha256"],
                "byte_count": request["after"]["byte_count"],
                "captured_at": request["after"]["captured_at"],
            },
            "max_request_chars": detector.max_request_chars,
            "evidence": {"normalized": True, "cap_chars": MAX_NORMALIZED_CHARS,
                         "truncated": meta["evidence_truncated"]},
        }
        payload = json.dumps(prompt_request, ensure_ascii=False)
        raw = None
        attempt = 0
        last_error = None
        while attempt <= self.retries:
            attempt += 1
            start = time.perf_counter()
            try:
                argv = shlex.split(self.command)
                proc = subprocess.run(
                    argv,
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    shell=False,
                )
                latency_ms = (time.perf_counter() - start) * 1000.0
                if proc.returncode != 0:
                    last_error = f"command exited {proc.returncode}"
                    continue
                raw = proc.stdout
                result = _unwrap(raw)
                return ProviderResponse(
                    provider=self.name, mode=self.mode, result=result, raw=None,
                    latency_ms=latency_ms,
                    input_bytes=len(payload.encode("utf-8")),
                    output_bytes=len(proc.stdout.encode("utf-8")),
                    schema_valid=True, error_category="none", retries=attempt - 1,
                    usage={"command_shell": False, "timeout_s": self.timeout_s, **meta},
                )
            except subprocess.TimeoutExpired:
                last_error = "timeout"
            except Exception as exc:  # noqa: BLE001 - normalize provider failure
                # shlex/OSError/decode messages can echo the JEV_COMMAND value
                # (e.g. FileNotFoundError includes argv) or raw stdout. The
                # command value must never reach a result artifact, so the
                # exception detail is withheld.
                last_error = f"{type(exc).__name__} (details withheld)"
            finally:
                pass
        return ProviderResponse(
            provider=self.name, mode=self.mode, result=None, raw=None,
            latency_ms=0.0, input_bytes=len(payload.encode("utf-8")), output_bytes=0,
            schema_valid=False, error_category="provider", provider_error=last_error,
            retries=attempt - 1,
        )

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "model": None,
            "command_configured": env_flag("JEV_COMMAND"),
            "command_label": safe_command_label(self.command),
            "timeout_s": self.timeout_s,
            "retries": self.retries,
        }