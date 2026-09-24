"""Deterministic integrity checks exposed as a CLI gate (`jev-monitor redact-check`).

These are integration/repro checks over the *committed* tree, not unit tests:

1. Redaction probe — a fixed JSON-able structure containing known credential
   shapes *and* valid SHA-256 digests (including e-prefixed ones, which the
   old over-broad pattern corrupted) is pushed through `redact.redact_value`.
   Every hash field must survive byte-exact and render as a valid 64-hex
   digest; every secret-shaped value must have its FULL token absent from the
   output — only the safe, non-secret prefix plus the literal `[REDACTED]`
   placeholder may survive (e.g. `sk-[REDACTED]`, `AKIA[REDACTED]`,
   `Bearer [REDACTED]`); non-secret plain text must be untouched.
2. Committed benchmark artifacts — every hash-labeled field (`*sha256*`,
   `*hash*`, `*digest*`, `*checksum*`) must be null or a valid 64-hex digest,
   the artifact self-hash must recompute to the recorded `artifact_sha256`,
   the (non-null) `config.config_sha256` must recompute from the artifact's
   provider/dataset/thresholds/fault settings, and no artifact may contain
   the `[REDACTED]` placeholder at all (committed artifacts carry no secrets).
3. Rubric citations — every fixture and example must cite the existing
   `docs/provenance-and-labeling.md` rubric path and may only reference
   `docs/...md` files that actually exist (no broken rubric paths).
4. JEV_COMMAND value hygiene — a poisoned `JEV_COMMAND` (arbitrary command
   text and credential-shaped tokens) is pushed through a fully configured
   `JevCommandProvider`: `describe()` and a complete in-memory benchmark
   artifact must never contain the raw command value, and any recorded
   `command_label` must be a strict-allowlist basename. This is a
   deterministic CLI/artifact inspection, not a unit test.
5. JEV_HTTP endpoint hygiene — a poisoned `JEV_ENDPOINT` (URL carrying
   credential-shaped tokens) and `JEV_API_KEY` are pushed through a fully
   configured `JevHttpProvider`: `describe()` must never contain the endpoint
   or key, and a real (connection-refused) `judge()` call must record only a
   bounded, withheld `provider_error` category — never the endpoint URL,
   exception detail, or credential text — while keeping
   `error_category = \"provider\"` so provider-error metrics stay wired.
   Loopback port 1 keeps the probe deterministic and network-free.

`jev-monitor validate` runs the same checks; `jev-monitor redact-check` is the
standalone deterministic entry point.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from jev_change_monitor.normalize import sha256_text
from jev_change_monitor.redact import is_hash_field, redact_value

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = REPO_ROOT / "docs"
RESULTS_COMMITTED = REPO_ROOT / "results" / "committed"
DATASETS_DIR = REPO_ROOT / "datasets"
EXAMPLES_DIR = REPO_ROOT / "examples"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DOCS_TOKEN_RE = re.compile(r"docs/[A-Za-z0-9_./\-]+\.md")
RUBRIC_CITATION = "docs/provenance-and-labeling.md"

# Fixed probe: hash digests (including the e-prefixed `config_sha256` that the
# old `ei?...` pattern corrupted) plus secret-shaped values and plain text.
# The secret-shaped values are assembled from parts so no credential-shaped
# literal exists in the source tree (scripts/secret_scan.sh must stay green);
# at runtime they are the exact shapes redaction is expected to mask.
HASH_64_E = "e50bfb663d600bad1c5b26cb0eeae7f3d75f90b4617f53c93278f16739feb580"
HASH_64_F = "3f54b0a124a07c2a8a1415e1d0b0e8422ff12eaeefb28012170accf8fb66f671"
HASH_64_T = "7ccba976e24012cda11761aba8720743a2f3c01d5dafb2f545205e49338e09a0"
HASH_64_ARTIFACT = "e0" * 32

_OPENAI_SHAPE = "sk-" + "abcdefghijklmn1234567890abcdefgh"
_GITHUB_SHAPE = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
_RN_LIVE_SHAPE = "rn_live_" + "abcdefghijklmnopqrstuv"
_RN_TEST_SHAPE = "rn_test_" + "abcdefghijklmnopqrstuv"
_AWS_SHAPE = "AKIA" + "IOSFODNN7EXAMPLE12345"
_BEARER_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"
_BEARER_SHAPE = "Authorization: Bearer " + _BEARER_TOKEN

REDACTION_PROBE = {
    "secret": _OPENAI_SHAPE,
    "token": _GITHUB_SHAPE,
    "api_key": _RN_LIVE_SHAPE,
    "aws": _AWS_SHAPE,
    "bearer": _BEARER_SHAPE,
    "plain": "hello world",
    "run_id": "e50bfb663d600bad",
    "config_sha256": HASH_64_E,
    "artifact_sha256": HASH_64_ARTIFACT,
    "dataset": {"sha256": HASH_64_F},
    "thresholds": {"sha256": HASH_64_T},
    "digest_list": [HASH_64_E, HASH_64_F],
    "history": [
        {"config_sha256": HASH_64_E, "note": "sk-" + "another-secret-abcdefghijklmnop"},
        {"config_sha256": HASH_64_F, "note": _RN_TEST_SHAPE},
    ],
}

SECRET_KEYS = ("secret", "token", "api_key", "aws", "bearer")
# Exact safe output for every secret-shaped probe entry: the FULL token must
# be gone and only the non-secret prefix plus `[REDACTED]` may remain.
SECRET_EXPECTATIONS = {
    "secret": "sk-[REDACTED]",
    "token": "ghp_[REDACTED]",
    "api_key": "rn_live_[REDACTED]",
    "aws": "AKIA[REDACTED]",
    "bearer": "Authorization: Bearer [REDACTED]",
}
HISTORY_NOTE_EXPECTATIONS = ("sk-[REDACTED]", "rn_test_[REDACTED]")
HASH_PROBE_KEYS = ("config_sha256", "artifact_sha256", "digest_list")
HASH_PROBE_PATH_SEGMENTS = (
    ("dataset", "sha256"),
    ("thresholds", "sha256"),
)

# JEV_COMMAND poison values: arbitrary command text plus credential-shaped
# tokens. Assembled from parts so no credential-shaped literal exists in the
# source tree (scripts/secret_scan.sh must stay green). Every poison's
# executable does not exist, so execution fails instantly and deterministically
# — the provider still records `provider.describe()` into the artifact, which
# is exactly what the probe inspects.
_CMD_POISON_BIN1 = "/opt/evil/runner_2026 --token sk-" + "abcdef0123456789abcdef0123456789ab"
_CMD_POISON_BIN2 = "/usr/bin/tool --api-key rn_live_" + "abcdef0123456789abcdef0123"
_CMD_POISON_BIN3 = "/tmp/no-such-binary --flag ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
_CMD_POISON_BIN4 = "/opt/evil/aws_tool --access AKIA" + "IOSFODNN7EXAMPLE12345"
_CMD_POISON_BIN5 = "/opt/evil/http_tool --auth Bearer " + "abcdefghijklmnopqrstuvwxyz0123456789"
_CMD_POISON_EXE = "sk-" + "abcdefghijklmn1234567890abcdefgh"
_CMD_POISON_AKIA_EXE = "AKIA" + "IOSFODNN7EXAMPLE12345"
_CMD_POISONS = [
    _CMD_POISON_BIN1, _CMD_POISON_BIN2, _CMD_POISON_BIN3, _CMD_POISON_BIN4,
    _CMD_POISON_BIN5, _CMD_POISON_EXE, _CMD_POISON_AKIA_EXE,
]
_CMD_CREDENTIALS = [
    "sk-" + "abcdef0123456789abcdef0123456789ab",
    "rn_live_" + "abcdef0123456789abcdef0123",
    "ghp_" + "abcdefghijklmnopqrstuvwxyz123456",
    "sk-" + "abcdefghijklmn1234567890abcdefgh",
    "rn_test_" + "abcdef0123456789abcdef0123",
    "AKIA" + "IOSFODNN7EXAMPLE12345",
    "Bearer " + "abcdefghijklmnopqrstuvwxyz0123456789",
]
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# JEV_HTTP poison values: a URL that carries credential-shaped tokens in its
# query and a bearer token shaped like a real API key. Assembled from parts so
# no credential-shaped literal exists in the source tree. Loopback port 1 is
# closed on every local host, so the connection is refused immediately and
# deterministically — the probe never touches the external network. If the
# provider ever echoed the endpoint or exception detail, these exact strings
# would appear in describe()/provider_error and fail the gate.
_HTTP_SK_TOKEN = "sk-" + "abcdef0123456789abcdef0123456789ab"
_HTTP_RN_TOKEN = "rn_live_" + "abcdef0123456789abcdef0123"
_HTTP_AKIA_TOKEN = "AKIA" + "IOSFODNN7EXAMPLE12345"
_HTTP_ENDPOINT_POISON = (
    "http://127.0.0.1:1/chat/completions?token=" + _HTTP_SK_TOKEN
    + "&key=" + _HTTP_RN_TOKEN
    + "&aws=" + _HTTP_AKIA_TOKEN
)
_HTTP_API_KEY_POISON = _HTTP_SK_TOKEN
_HTTP_ENDPOINT_FRAGMENTS = ("http://", "127.0.0.1", "/chat/completions")
_HTTP_BEARER_HEADER_SHAPE = "Bearer " + _HTTP_SK_TOKEN


def _iter_hash_strings(node, key=None, path=()):
    """Yield (path, key, value) for string values under hash-labeled keys."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_hash_strings(v, key=k, path=path + (str(k),))
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            yield from _iter_hash_strings(item, key=key, path=path + (f"[{index}]",))
    elif isinstance(node, str) and is_hash_field(key):
        yield path, key, node


def redaction_probe() -> list[str]:
    """Run the fixed probe and report any integrity problems."""
    problems: list[str] = []
    result = cast(dict, redact_value(REDACTION_PROBE))

    for key in HASH_PROBE_KEYS:
        if result[key] != REDACTION_PROBE[key]:
            problems.append(f"probe: hash field {key!r} was altered by redaction")
        value = result[key]
        if isinstance(value, str) and not HEX64_RE.match(value):
            problems.append(f"probe: hash field {key!r} is not a valid 64-hex digest: {value[:32]}…")
    for key in HASH_PROBE_PATH_SEGMENTS:
        value = result[key[0]][key[1]]
        if value != REDACTION_PROBE[key[0]][key[1]]:
            problems.append(
                f"probe: hash field {'.'.join(key)!r} was altered by redaction"
            )
    for index, entry in enumerate(result["history"]):
        expected_hash = REDACTION_PROBE["history"][index]["config_sha256"]
        if entry.get("config_sha256") != expected_hash:
            problems.append("probe: nested config_sha256 was altered by redaction")

    for key in SECRET_KEYS:
        original = REDACTION_PROBE[key]
        value = result[key]
        if original in value:
            problems.append(f"probe: full secret-shaped value under {key!r} STILL PRESENT after redaction")
        if "[REDACTED]" not in value:
            problems.append(f"probe: secret-shaped value under {key!r} was NOT redacted")
        if value != SECRET_EXPECTATIONS[key]:
            problems.append(
                f"probe: secret-shaped value under {key!r} does not match the safe "
                f"form {SECRET_EXPECTATIONS[key]!r}: got {value!r}"
            )
    for index, entry in enumerate(result["history"]):
        original = REDACTION_PROBE["history"][index].get("note", "")
        note = entry.get("note", "")
        if original in note:
            problems.append(f"probe: full secret token still present in nested history[{index}]")
        if "[REDACTED]" not in note:
            problems.append(f"probe: secret-shaped value in nested history[{index}] was NOT redacted")
        if note != HISTORY_NOTE_EXPECTATIONS[index]:
            problems.append(
                f"probe: nested history[{index}] note does not match the safe form "
                f"{HISTORY_NOTE_EXPECTATIONS[index]!r}: got {note!r}"
            )
    if result["plain"] != "hello world":
        problems.append("probe: plain non-secret text was altered by redaction")
    if result["run_id"] != "e50bfb663d600bad":
        problems.append("probe: non-hash run_id (16-hex) was altered by redaction")
    return problems


def committed_artifact_checks(results_committed: Path = RESULTS_COMMITTED) -> list[str]:
    """Validate hash integrity of every committed benchmark artifact."""
    from jev_change_monitor.benchmark.runner import _self_hash  # noqa: SLF001 - shared integrity helper

    problems: list[str] = []
    artifacts = sorted(results_committed.rglob("*.json"))
    if not artifacts:
        problems.append("no committed result artifacts found under results/committed/")
    for path in artifacts:
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: invalid JSON: {exc}")
            continue
        text = path.read_text(encoding="utf-8")
        if "[REDACTED]" in text:
            problems.append(f"{path.name}: artifact contains the redaction placeholder "
                            "(a committed artifact must never carry secrets at all)")
        for field_path, key, value in _iter_hash_strings(artifact):
            where = ".".join(field_path) or key
            if not HEX64_RE.match(value):
                problems.append(
                    f"{path.name}: hash-labeled field {where!r} is not a valid "
                    f"64-hex digest ({value[:40]}…) — recorded digests must stay byte-exact"
                )
        recomputed = _self_hash(artifact)
        if recomputed != artifact.get("artifact_sha256"):
            problems.append(f"{path.name}: artifact self-hash mismatch")
        config_hash = (artifact.get("config") or {}).get("config_sha256")
        if config_hash:
            blob = json.dumps(
                {
                    "provider": artifact.get("provider"),
                    "dataset_sha": (artifact.get("dataset") or {}).get("sha256"),
                    "thresholds_sha": (artifact.get("thresholds") or {}).get("sha256"),
                    "fault_injection_rate": (artifact.get("config") or {}).get("fault_injection_rate"),
                },
                sort_keys=True,
            )
            if sha256_text(blob) != config_hash:
                problems.append(f"{path.name}: config.config_sha256 does not recompute")
    return problems


def rubric_citations(datasets_dir: Path = DATASETS_DIR,
                     examples_dir: Path = EXAMPLES_DIR,
                     docs_dir: Path = DOCS_DIR) -> list[str]:
    """Every fixture/example must cite the existing rubric doc; no broken docs paths."""
    problems: list[str] = []
    files = sorted(datasets_dir.rglob("*.jsonl")) + sorted(examples_dir.rglob("case.json"))
    for path in files:
        try:
            if path.suffix == ".jsonl":
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                docs = [json.loads(line) for line in lines]
            else:
                docs = [json.loads(path.read_text(encoding="utf-8"))]
        except json.JSONDecodeError as exc:
            problems.append(f"{path}: invalid JSON: {exc}")
            continue
        for doc in docs:
            labeling = (doc.get("label") or {}).get("labeling") or {}
            method = labeling.get("method") or ""
            notes = labeling.get("notes") or ""
            if RUBRIC_CITATION not in method:
                problems.append(
                    f"{path} ({doc.get('case_id')}): labeling.method does not cite "
                    f"the rubric at {RUBRIC_CITATION}"
                )
            for token in set(DOCS_TOKEN_RE.findall(method + " " + notes)):
                if not (REPO_ROOT / token).exists():
                    problems.append(
                        f"{path} ({doc.get('case_id')}): labeling cites missing doc {token!r}"
                    )
    return problems


def command_value_probe() -> list[str]:
    """Poison JEV_COMMAND; prove the raw value never reaches results.

    Deterministic CLI/artifact inspection (not a unit test): with a fully
    configured `JevCommandProvider`, `describe()` is written into every
    benchmark artifact, so the probe asserts that (a) `describe()` contains
    neither the raw command text nor credential tokens and only emits a
    strict-allowlist basename at most, and (b) a complete in-memory benchmark
    artifact recorded under the poisoned environment stays free of every
    poison/credential string, remains schema-valid with the new provider
    keys, and keeps an honest blocked launch claim. Every poison executable
    does not exist, so all provider executions fail instantly and
    deterministically (their withheld error rows are inspected too).
    """
    import os

    from jev_change_monitor.benchmark import runner
    from jev_change_monitor.providers.jev_command import JevCommandProvider
    from jev_change_monitor.schemas import validate_schema

    problems: list[str] = []

    def label_ok(label) -> bool:
        return label is None or bool(_LABEL_RE.match(label))

    original = os.environ.get("JEV_COMMAND")
    try:
        for poison in _CMD_POISONS:
            os.environ["JEV_COMMAND"] = poison
            describe = JevCommandProvider().describe()
            blob = json.dumps(describe, sort_keys=True)
            if poison in blob:
                problems.append("command probe: raw JEV_COMMAND text reached describe()")
            if describe.get("command") not in (None,):
                problems.append("command probe: describe() exposes a command value")
            label = describe.get("command_label")
            if not label_ok(label):
                problems.append(f"command probe: unsafe command_label {label!r}")
            if poison.startswith(("sk-", "AKIA")) and label is not None:
                problems.append("command probe: credential-shaped executable leaked as label")

        # Full-artifact inspection: run the benchmark with the first poison so
        # provider.describe() AND per-case error rows are persisted, then prove
        # no poison/credential text entered the artifact.
        poison = _CMD_POISONS[0]
        os.environ["JEV_COMMAND"] = poison
        provider = JevCommandProvider()
        artifact = runner.run(
            split="held_out",
            provider_name=provider.name,
            provider=provider,
            out_path=None,
            fault_injection_rate=0.0,
            command="jev-monitor redact-check (command-poison probe)",
        )
        artifact_blob = json.dumps(artifact, sort_keys=True, ensure_ascii=False)
        for token in _CMD_POISONS + _CMD_CREDENTIALS:
            if token in artifact_blob:
                problems.append("command probe: poison/credential text reached the result artifact")
        if artifact.get("provider", {}).get("command") not in (None,):
            problems.append("command probe: artifact provider exposes a command value")
        artifact_label = artifact.get("provider", {}).get("command_label")
        if not label_ok(artifact_label):
            problems.append("command probe: artifact command_label is unsafe")
        if artifact.get("provider", {}).get("command_label") != "runner_2026":
            problems.append("command probe: expected safe basename label 'runner_2026'")
        errors = validate_schema(artifact, "benchmark-result")
        if errors:
            problems.append(f"command probe: artifact schema-invalid: {'; '.join(errors)}")
        if artifact.get("launch_claim", {}).get("status") not in ("blocked", "failed"):
            problems.append("command probe: artifact launch claim must stay blocked/failed "
                            "(never passed)")
        for row in artifact.get("cases", []):
            provider_error = (row.get("result_record") or {}).get("provider_error") or ""
            if poison in provider_error or "runner_2026 --token" in provider_error:
                problems.append("command probe: provider_error echoed the command value")
    finally:
        if original is None:
            os.environ.pop("JEV_COMMAND", None)
        else:
            os.environ["JEV_COMMAND"] = original
    return problems


def http_provider_probe() -> list[str]:
    """Poison JEV_ENDPOINT/JEV_API_KEY; prove the endpoint never leaks.

    Deterministic CLI/artifact inspection (not a unit test): with a fully
    configured `JevHttpProvider`, `describe()` must never contain the endpoint
    URL or the API key, and a real connection-refused `judge()` call must
    record `provider_error` as a bounded, withheld category — never the
    endpoint URL, exception detail, or credential text — while preserving
    `error_category = "provider"` so provider-error metrics stay wired. The
    endpoint points at loopback port 1 (immediate connection refusal), so the
    probe never touches the external network.
    """
    import os

    from jev_change_monitor.benchmark import runner as runner_mod
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.providers.jev_http import JevHttpProvider

    problems: list[str] = []
    tokens = (_HTTP_ENDPOINT_POISON, _HTTP_API_KEY_POISON,
              _HTTP_SK_TOKEN, _HTTP_RN_TOKEN, _HTTP_AKIA_TOKEN,
              _HTTP_BEARER_HEADER_SHAPE)

    original = {name: os.environ.get(name)
                for name in ("JEV_ENDPOINT", "JEV_API_KEY", "JEV_MODEL")}
    try:
        os.environ["JEV_ENDPOINT"] = _HTTP_ENDPOINT_POISON
        os.environ["JEV_API_KEY"] = _HTTP_API_KEY_POISON
        os.environ["JEV_MODEL"] = "jev-probe-model"

        provider = JevHttpProvider()
        if not provider.configured:
            problems.append("http probe: poison env did not configure the provider")

        describe_blob = json.dumps(provider.describe(), sort_keys=True)
        for token in tokens:
            if token in describe_blob:
                problems.append("http probe: raw endpoint/api-key text reached describe()")

        case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
        request = runner_mod._request_for_case(case)  # noqa: SLF001 - shared request builder
        response = provider.judge(get_detector("price"), request)
        if response.error_category != "provider":
            problems.append(
                "http probe: provider failures must keep error_category='provider' "
                "(provider-error metrics stay wired)"
            )
        provider_error = response.provider_error or ""
        for token in tokens:
            if token in provider_error:
                problems.append("http probe: endpoint/credential text reached provider_error")
        for fragment in _HTTP_ENDPOINT_FRAGMENTS:
            if fragment in provider_error:
                problems.append("http probe: provider_error exposes endpoint URL fragments")
        if "details withheld" not in provider_error:
            problems.append(
                f"http probe: provider_error must be a bounded withheld category, "
                f"got {provider_error!r}"
            )
        if ":" in provider_error and provider_error.startswith("URLError"):
            problems.append(
                f"http probe: provider_error must not carry URLError reason detail, "
                f"got {provider_error!r}"
            )
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return problems


def evaluate_provider_probe() -> list[str]:
    """Poison JEV_ENDPOINT/JEV_API_KEY/JEV_PROTOCOL and check typed-answer mapping.

    Deterministic CLI/artifact inspection (not a unit test), mirroring
    `http_provider_probe` and `command_value_probe`:

    1. Poison env — a fully configured `JevEvaluateProvider` must never echo
       the endpoint URL or API key from `describe()`, and a real
       connection-refused `judge()` over loopback port 1 must record only a
       bounded, withheld `provider_error` (never endpoint fragments, never
       credential text, `error_category = "provider"`).
    2. Protocol gates — `JEV_PROTOCOL=evaluate` with a chat-completions-style
       URL must be refused with a bounded message that never echoes the URL;
       `JEV_PROTOCOL=<other>` must report the provider as unconfigured.
    3. Typed-answer mapping contract (pure `map_answers`, no network) — fully
       typed payloads map to a schema-valid detector result with
       `confidence_source = "jev-raw-probability"`; boolean answers carrying
       only a probability map via the documented >= 0.5 rule; exact usage/cost
       numbers pass through; unmappable answers (missing answer, `noul`
       abstention, choice outside the allowed set, score outside [0,1],
       missing answers envelope) are recorded as
       `schema_invalid`/`provider` errors with result=None — fields are never
       fabricated.
    """
    import os

    from jev_change_monitor.benchmark import runner as runner_mod
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.providers.jev_evaluate import (
        JevEvaluateProvider,
        map_answers,
    )

    problems: list[str] = []
    tokens = (_HTTP_ENDPOINT_POISON, _HTTP_API_KEY_POISON,
              _HTTP_SK_TOKEN, _HTTP_RN_TOKEN, _HTTP_AKIA_TOKEN,
              _HTTP_BEARER_HEADER_SHAPE)
    _EVAL_ENDPOINT_POISON = (
        "http://127.0.0.1:1/v1/evaluate?token=" + _HTTP_SK_TOKEN
        + "&key=" + _HTTP_RN_TOKEN
        + "&aws=" + _HTTP_AKIA_TOKEN
    )
    _EVAL_ENDPOINT_FRAGMENTS = ("http://127.0.0.1", "/v1/evaluate")

    original = {name: os.environ.get(name)
                for name in ("JEV_ENDPOINT", "JEV_API_KEY", "JEV_MODEL", "JEV_PROTOCOL")}
    try:
        # --- poisoned env: endpoint/key must never leak; judge() over closed
        # loopback port 1 records a bounded withheld error ---
        os.environ["JEV_ENDPOINT"] = _EVAL_ENDPOINT_POISON
        os.environ["JEV_API_KEY"] = _HTTP_API_KEY_POISON
        os.environ["JEV_MODEL"] = "jev-probe-model"
        os.environ["JEV_PROTOCOL"] = "evaluate"

        provider = JevEvaluateProvider()
        if not provider.configured:
            problems.append("evaluate probe: poison env did not configure the provider")

        describe_blob = json.dumps(provider.describe(), sort_keys=True)
        for token in tokens:
            if token in describe_blob:
                problems.append("evaluate probe: raw endpoint/api-key text reached describe()")

        case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
        request = runner_mod._request_for_case(case)  # noqa: SLF001 - shared request builder
        response = provider.judge(get_detector("price"), request)
        if response.error_category != "provider":
            problems.append(
                "evaluate probe: provider failures must keep error_category='provider' "
                "(provider-error metrics stay wired)"
            )
        provider_error = response.provider_error or ""
        for token in tokens:
            if token in provider_error:
                problems.append("evaluate probe: endpoint/credential text reached provider_error")
        for fragment in _EVAL_ENDPOINT_FRAGMENTS:
            if fragment in provider_error:
                problems.append("evaluate probe: provider_error exposes endpoint URL fragments")
        if "details withheld" not in provider_error:
            problems.append(
                f"evaluate probe: provider_error must be a bounded withheld category, "
                f"got {provider_error!r}"
            )

        # --- protocol gate: chat-completions URL must be refused, bounded ---
        os.environ["JEV_ENDPOINT"] = _HTTP_ENDPOINT_POISON  # /chat/completions + tokens
        chat_guard = JevEvaluateProvider().judge(get_detector("price"), request)
        chat_error = chat_guard.provider_error or ""
        if "chat-completions" not in chat_error:
            problems.append(
                f"evaluate probe: chat-completions URL must be refused with a bounded "
                f"message, got {chat_error!r}"
            )
        # The guard must never echo the configured endpoint: no credential
        # tokens, no scheme/host fragments. (The literal protocol path
        # "/v1/evaluate" is allowed as a protocol name in the message, so it
        # is not treated as a leak.)
        for token in tokens + ("http://", "127.0.0.1", "/chat/completions"):
            if token in chat_error:
                problems.append("evaluate probe: chat-guard message echoed the endpoint")

        # --- protocol gate: non-evaluate JEV_PROTOCOL reports unconfigured ---
        os.environ["JEV_ENDPOINT"] = _EVAL_ENDPOINT_POISON
        os.environ["JEV_PROTOCOL"] = "chat"
        if JevEvaluateProvider().configured:
            problems.append("evaluate probe: JEV_PROTOCOL != evaluate must not configure the provider")

        # --- typed-answer mapping contract (pure, no network) ---
        os.environ["JEV_PROTOCOL"] = "evaluate"
        meta = {"evidence_normalized": True, "evidence_truncated": True,
                "evidence_cap_chars": 64000}
        from jev_change_monitor.schemas import validate_schema

        # Mixed native + gateway answer shapes: noul (native), boolean with
        # probability (verified gateway example), choice with `choice` key,
        # score with `confidence`.
        payload_ok = {
            "model": "typesafe-ai/jev",
            "usage": {"input_tokens": 1200, "output_tokens": 90, "total_cost_usd": 0.0042},
            "answers": {
                "valid": {"type": "noul", "noul": 0.99},
                "meaningful": {"type": "boolean", "probability": 0.85},
                "change_type": {"type": "choice", "choice": "price_increase",
                                "probabilities": {"price_increase": 1.0}},
                "importance": {"type": "choice", "value": "high"},
                "should_alert": {"type": "boolean", "probability": 0.8},
                "confidence": {"type": "score", "score": 3.0, "confidence": 0.75},
            },
        }
        ok = map_answers(payload_ok, get_detector("price"), meta)
        if not ok.schema_valid or ok.error_category != "none":
            problems.append(f"evaluate probe: fully-typed payload must map cleanly: "
                            f"{ok.provider_error!r}")
        if ok.result is None:
            problems.append("evaluate probe: fully-typed payload produced no result")
        else:
            result = ok.result
            if not (result["valid"] is True and result["meaningful"] is True
                    and result["should_alert"] is True):
                problems.append("evaluate probe: boolean/noul mapping wrong for fully-typed payload")
            if result["change_type"] != "price_increase" or result["importance"] != "high":
                problems.append("evaluate probe: choice mapping wrong for fully-typed payload")
            if result["confidence"] != 0.8 or result["confidence_source"] != "jev-raw-probability":
                problems.append("evaluate probe: raw-probability confidence mapping wrong "
                                "(expected should_alert probability 0.8)")
            schema_errors = validate_schema(result, "detector-result")
            if schema_errors:
                problems.append(f"evaluate probe: mapped result schema-invalid: "
                                f"{'; '.join(schema_errors)}")
            usage = ok.usage.get("provider_usage", {})
            if usage.get("input_tokens") != 1200 or usage.get("total_cost_usd") != 0.0042:
                problems.append("evaluate probe: exact usage/cost numbers must pass through")

        # boolean-family answer with only a probability: documented >= 0.5 rule
        payload_prob = {
            "answers": {
                "valid": {"type": "boolean", "value": True},
                "meaningful": {"type": "noul", "noul": 0.3},
                "change_type": {"type": "choice", "value": "none"},
                "importance": {"type": "choice", "value": "low"},
                "should_alert": {"type": "boolean", "probability": 0.7},
            },
        }
        prob = map_answers(payload_prob, get_detector("price"), meta)
        if not prob.schema_valid or prob.result is None:
            problems.append("evaluate probe: probability-only boolean payload failed to map")
        else:
            if prob.result["meaningful"] is not False:
                problems.append("evaluate probe: probability-only boolean must map via >= 0.5 rule")
            if prob.result["should_alert"] is not True:
                problems.append("evaluate probe: probability-only boolean must map via >= 0.5 rule")

        # unmappable cases must never fabricate fields
        unmappable_cases = [
            ("missing should_alert",
             {"answers": {k: v for k, v in payload_ok["answers"].items()
                          if k != "should_alert"}},
             "schema_invalid", "should_alert"),
            ("unknown answer type on meaningful",
             {"answers": {**payload_ok["answers"],
                          "meaningful": {"type": "text", "value": "probably"}}},
             "schema_invalid", "meaningful"),
            ("choice outside allowed set",
             {"answers": {**payload_ok["answers"],
                          "change_type": {"type": "choice", "value": "price_removed_typo"}}},
             "schema_invalid", "change_type"),
            ("score answer without usable confidence",
             {"answers": {**payload_ok["answers"],
                          "confidence": {"type": "score", "score": 3.0},
                          "should_alert": {"type": "boolean", "value": True},
                          "meaningful": {"type": "boolean", "value": True}}},
             "schema_invalid", "confidence"),
            ("missing answers envelope",
             {"result": "text"},
             "provider", "answers"),
        ]
        for name, payload, expected_category, expected_token in unmappable_cases:
            got = map_answers(payload, get_detector("price"), meta)
            if got.schema_valid is not False or got.error_category != expected_category:
                problems.append(
                    f"evaluate probe: {name} must record error_category={expected_category}, "
                    f"got schema_valid={got.schema_valid} category={got.error_category!r}"
                )
            if got.result is not None:
                problems.append(f"evaluate probe: {name} must NOT fabricate a result")
            if expected_token not in (got.provider_error or ""):
                problems.append(
                    f"evaluate probe: {name} provider_error must name {expected_token!r}, "
                    f"got {got.provider_error!r}"
                )
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return problems


def run_redact_check(results_committed: Path = RESULTS_COMMITTED) -> list[str]:
    """All deterministic integrity checks; one problem per list entry."""
    problems: list[str] = []
    problems.extend(redaction_probe())
    problems.extend(committed_artifact_checks(results_committed))
    problems.extend(rubric_citations())
    problems.extend(command_value_probe())
    problems.extend(http_provider_probe())
    problems.extend(evaluate_provider_probe())
    return problems
