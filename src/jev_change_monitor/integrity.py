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
6. Price extraction mapping — the `price` detector's dynamic `choice`
   extraction questions must derive their criteria from the actual bounded
   candidate price tokens of the committed example (`examples/price/case.json`),
   the three answers must map deterministically into a schema-valid
   `details.extraction`, unmappable choices / missing contexts must fail as
   `schema_invalid` with the failing answer id named (amounts never
   invented), sentinels map to null extraction fields, and non-price
   detectors must never emit `details.extraction`. Pure `map_answers`, no
   network.
7. Timeout classification — a real socket read timeout against a local
   loopback server that accepts and stalls must be classified
   `error_category = \"timeout\"` with a bounded withheld `provider_error`,
   bounded retry/backoff, and never a fabricated result; endpoint/credential
   fragments must not leak. Loopback-only and deterministic.

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


def extraction_mapping_probe() -> list[str]:
    """Deterministic price-extraction mapping probe (pure, no network).

    Mirrors `evaluate_provider_probe`'s pure `map_answers` section for the P2
    price-extraction contract: builds the bounded candidate context exactly as
    the live path does from the committed `examples/price/case.json`, then
    proves:

    1. question bank wiring — the price detector carries the three typed
       `choice` extraction questions (`price_after` / `price_before` /
       `price_direction`) and their criteria derive from the actual candidate
       price tokens in the bounded evidence plus the fixed sentinels; non-price
       detectors carry none of them;
    2. deterministic mapping — a fully-typed answers payload maps to a
       schema-valid result whose `details.extraction` carries exactly the
       chosen candidate fields plus the direction, never invented; two runs are
       byte-identical; usage records the bounded candidate counts;
    3. failure discipline — a choice outside the bounded candidate set or a
       missing `price_direction` is `schema_invalid` with the failing answer id
       named and `result=None`; sentinel choices map to null extraction fields;
       a price payload with extraction answers but no candidate context fails
       instead of silently dropping the extraction;
    4. non-price detectors never emit `details.extraction`.
    """
    from jev_change_monitor.benchmark import runner as runner_mod
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.normalize import extract_price_tokens
    from jev_change_monitor.providers.base import bounded_evidence
    from jev_change_monitor.providers.jev_evaluate import (
        _MAX_PRICE_CANDIDATES,
        _price_token_key,
        map_answers,
        price_extraction_questions,
        question_bank,
    )
    from jev_change_monitor.schemas import validate_schema

    problems: list[str] = []
    case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
    request = runner_mod._request_for_case(case)  # noqa: SLF001 - shared request builder
    evidence, _ = bounded_evidence(request)
    questions, context = price_extraction_questions(evidence)

    # --- 1. question bank wiring -----------------------------------------
    for qid in ("price_after", "price_before", "price_direction"):
        question = questions.get(qid)
        if question is None or question.get("type") != "choice":
            problems.append(f"extraction probe: price question {qid!r} must be a choice")
            continue
        criteria = question.get("criteria") or {}
        if qid != "price_direction" and not all(s in criteria for s in ("no_price_token", "unclear")):
            problems.append(f"extraction probe: {qid} criteria must include the fixed sentinels")
    non_price = question_bank(get_detector("saas_pricing"))
    if any(qid.startswith("price_") for qid in non_price):
        problems.append("extraction probe: non-price detector carries price extraction questions")

    # criteria must derive from the actual bounded candidate tokens
    for side in ("before", "after"):
        keys: set[str] = set()
        seen: set[str] = set()
        for token in extract_price_tokens(evidence[side]):
            key = _price_token_key(token)
            if key in seen:
                continue
            seen.add(key)
            if len(keys) >= _MAX_PRICE_CANDIDATES:
                break
            keys.add(key)
        qid = "price_after" if side == "after" else "price_before"
        criteria = set((questions.get(qid) or {}).get("criteria") or {})
        missing = keys - criteria
        if missing:
            problems.append(f"extraction probe: {side} candidate keys missing from criteria: {sorted(missing)[:3]}")
        context_keys = set((context.get(side) or {}).keys())
        if not keys.issubset(context_keys):
            problems.append(f"extraction probe: {side} candidate context does not cover the criteria")
    # the committed example's canonical tokens ($39 after, $29 before)
    after_criteria = set((questions.get("price_after") or {}).get("criteria") or {})
    before_criteria = set((questions.get("price_before") or {}).get("criteria") or {})
    if "amt:39|cur:USD|per:mo" not in after_criteria:
        problems.append("extraction probe: after-state $39 token missing from criteria")
    if "amt:29|cur:USD|per:mo" not in before_criteria:
        problems.append("extraction probe: before-state $29 token missing from criteria")
    direction_criteria = set((questions.get("price_direction") or {}).get("criteria") or {})
    if direction_criteria != {"up", "down", "unchanged", "unknown"}:
        problems.append("extraction probe: price_direction criteria must be exactly up/down/unchanged/unknown")

    probe_meta = {
        "evidence_normalized": True,
        "evidence_truncated": False,
        "evidence_cap_chars": 64000,
        "price_candidates_after": len(context["after"]),
        "price_candidates_before": len(context["before"]),
        "price_candidates_truncated": bool(context["truncated"]),
    }

    def base_answers(**overrides) -> dict:
        answers = {
            "valid": {"type": "boolean", "value": True},
            "meaningful": {"type": "boolean", "value": True},
            "change_type": {"type": "choice", "value": "price_increase"},
            "importance": {"type": "choice", "value": "high"},
            "should_alert": {"type": "boolean", "value": True},
            "confidence": {"type": "score", "score": 3.0, "confidence": 0.8},
        }
        answers.update(overrides)
        return answers

    ok_payload = {"answers": base_answers(
        price_after={"type": "choice", "choice": "amt:39|cur:USD|per:mo"},
        price_before={"type": "choice", "choice": "amt:29|cur:USD|per:mo"},
        price_direction={"type": "choice", "choice": "up"},
    )}

    # --- 2. deterministic mapping into details.extraction ----------------
    ok = map_answers(ok_payload, get_detector("price"), probe_meta, price_extraction=context)
    if not ok.schema_valid or ok.error_category != "none" or ok.result is None:
        problems.append(f"extraction probe: fully-typed price payload must map cleanly: {ok.provider_error!r}")
    else:
        extraction = ok.result.get("details", {}).get("extraction")
        expected = {"amount": 39.0, "currency": "USD", "period": "mo",
                    "amount_before": 29.0, "currency_before": "USD", "direction": "up"}
        if extraction != expected:
            problems.append(f"extraction probe: mapping produced {extraction!r}, expected {expected!r}")
        schema_errors = validate_schema(ok.result, "detector-result")
        if schema_errors:
            problems.append(f"extraction probe: mapped result schema-invalid: {'; '.join(schema_errors)}")
    again = map_answers(ok_payload, get_detector("price"), probe_meta, price_extraction=context)
    if json.dumps(ok.result, sort_keys=True) != json.dumps(again.result, sort_keys=True):
        problems.append("extraction probe: mapping is not deterministic")
    if ok.usage.get("price_candidates_after") != len(context["after"]):
        problems.append("extraction probe: usage must record the bounded after-candidate count")

    # --- 3. unmappable / sentinel / wiring guard -------------------------
    unmappable_cases = [
        ("choice outside the bounded candidate set",
         {"price_after": {"type": "choice", "value": "amt:999.0|cur:USD|per:none"}},
         "price_after"),
        ("missing price_direction",
         {"price_after": {"type": "choice", "value": "amt:39|cur:USD|per:mo"},
          "price_before": {"type": "choice", "value": "amt:29|cur:USD|per:mo"}},
         "price_direction"),
    ]
    for name, overrides, token in unmappable_cases:
        got = map_answers({"answers": base_answers(**overrides)}, get_detector("price"),
                          probe_meta, price_extraction=context)
        if got.schema_valid is not False or got.error_category != "schema_invalid":
            problems.append(f"extraction probe: {name} must record schema_invalid")
        if got.result is not None:
            problems.append(f"extraction probe: {name} must NOT fabricate a result")
        if token not in (got.provider_error or ""):
            problems.append(
                f"extraction probe: {name} provider_error must name {token!r}, got {got.provider_error!r}"
            )

    sentinel_payload = {"answers": base_answers(
        meaningful={"type": "boolean", "value": False},
        change_type={"type": "choice", "value": "none"},
        importance={"type": "choice", "value": "low"},
        should_alert={"type": "boolean", "value": False},
        price_after={"type": "choice", "value": "no_price_token"},
        price_before={"type": "choice", "value": "no_price_token"},
        price_direction={"type": "choice", "value": "unknown"},
    )}
    sentinel = map_answers(sentinel_payload, get_detector("price"), probe_meta, price_extraction=context)
    if not sentinel.schema_valid or sentinel.result is None:
        problems.append("extraction probe: sentinel-only payload must map cleanly")
    else:
        extraction = sentinel.result.get("details", {}).get("extraction")
        if extraction != {"amount": None, "currency": None, "period": None,
                          "amount_before": None, "currency_before": None, "direction": "unknown"}:
            problems.append(f"extraction probe: sentinel mapping unexpected: {extraction!r}")

    guard = map_answers(ok_payload, get_detector("price"), probe_meta)
    if guard.schema_valid is not False or "candidate context is unavailable" not in (guard.provider_error or ""):
        problems.append("extraction probe: price answers without a candidate context must fail")

    # --- 4. non-price detectors never emit extraction ---------------------
    saas_payload = {"answers": {
        "valid": {"type": "boolean", "value": True},
        "meaningful": {"type": "boolean", "value": False},
        "change_type": {"type": "choice", "value": "none"},
        "importance": {"type": "choice", "value": "low"},
        "should_alert": {"type": "boolean", "value": False},
        "confidence": {"type": "score", "score": 2.0, "confidence": 0.6},
    }}
    saas = map_answers(saas_payload, get_detector("saas_pricing"), probe_meta)
    if (saas.result or {}).get("details", {}).get("extraction") is not None:
        problems.append("extraction probe: non-price detectors must not emit details.extraction")
    return problems


def timeout_classification_probe() -> list[str]:
    """Deterministic socket/read-timeout classification probe (loopback only).

    A local TCP server accepts the provider's POST and then stalls (never
    responds), forcing a real socket read timeout with a short client timeout.
    `JevEvaluateProvider.judge` must classify the failure as
    `error_category = \"timeout\"`, keep a bounded withheld `provider_error`
    (no endpoint host, no credential text), never fabricate a result, and
    report the exhausted retry count. `retries=1` exercises the bounded
    retry/backoff path (two attempts, both timing out). The server is
    loopback-only, so the probe never touches the external network.
    """
    import os
    import socket
    import threading
    import time

    from jev_change_monitor.benchmark import runner as runner_mod
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.providers.jev_evaluate import JevEvaluateProvider

    problems: list[str] = []
    case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
    request = runner_mod._request_for_case(case)  # noqa: SLF001 - shared request builder

    original = {name: os.environ.get(name)
                for name in ("JEV_ENDPOINT", "JEV_API_KEY", "JEV_MODEL", "JEV_PROTOCOL")}
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind(("127.0.0.1", 0))
        listen_sock.listen(4)
        port = listen_sock.getsockname()[1]
        os.environ["JEV_ENDPOINT"] = f"http://127.0.0.1:{port}/v1/evaluate"
        os.environ["JEV_API_KEY"] = _HTTP_API_KEY_POISON
        os.environ["JEV_MODEL"] = "jev-probe-model"
        os.environ["JEV_PROTOCOL"] = "evaluate"

        def stall_accept() -> None:
            """Accept up to 3 connections; never respond to any of them."""
            for _ in range(3):
                try:
                    conn, _addr = listen_sock.accept()
                except OSError:
                    return

                def drain(c: socket.socket) -> None:
                    try:
                        c.settimeout(2.0)
                        try:
                            while c.recv(65536):
                                pass
                        except OSError:
                            pass
                        time.sleep(4.0)
                    except OSError:
                        pass
                    finally:
                        try:
                            c.close()
                        except OSError:
                            pass

                threading.Thread(target=drain, args=(conn,), daemon=True).start()

        threading.Thread(target=stall_accept, daemon=True).start()

        response = JevEvaluateProvider(timeout_s=0.4, retries=1).judge(get_detector("price"), request)
        if response.error_category != "timeout":
            problems.append(
                f"timeout probe: socket/read timeout must classify error_category='timeout', "
                f"got {response.error_category!r}"
            )
        if "timeout" not in (response.provider_error or "").lower():
            problems.append(
                f"timeout probe: provider_error must be a bounded timeout message, "
                f"got {response.provider_error!r}"
            )
        if "127.0.0.1" in (response.provider_error or ""):
            problems.append("timeout probe: provider_error leaked the endpoint host")
        if _HTTP_API_KEY_POISON in (response.provider_error or ""):
            problems.append("timeout probe: provider_error leaked credential text")
        if response.result is not None or response.schema_valid:
            problems.append("timeout probe: a timed-out judge must not fabricate a result")
        if response.retries != 1:
            problems.append(f"timeout probe: expected retries=1 for two exhausted attempts, got {response.retries}")
    finally:
        try:
            listen_sock.close()
        except OSError:
            pass
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return problems


def retry_semantics_probe() -> list[str]:
    """Deterministic retry-semantics probe (loopback only, no network).

    A scripted loopback TCP server proves the documented retry contract of
    `JevEvaluateProvider.judge` (docs/benchmark-methodology.md), covering the
    independent-review P2 findings without unit tests:

    1. HTTP 429 (rate limit) is retried with the bounded Retry-After backoff
       (here 0.05s) and a later success records `retries = attempt - 1` in
       the returned ProviderResponse and its artifact record (never 0).
    2. A socket/read timeout is retried with the bounded backoff and a later
       success likewise records `retries = attempt - 1`.
    3. A non-retryable HTTP status (503) is recorded immediately: the server
       observes exactly ONE connection despite `retries=2`, and the response
       is a bounded `provider`-category error (`HTTP 503`), `retries == 0`,
       no fabricated result.
    4. A non-timeout connection failure (connection refused) is recorded
       immediately: `retries == 0`, bounded withheld `provider_error`, no
       fabricated result.
    5. A `Retry-After` value above the 60s cap is clamped to the cap rather
       than silently reduced to the 1s default; malformed/absent values use
       the 1s default.
    """
    import os
    import socket
    import threading
    import time

    from jev_change_monitor.benchmark import runner as runner_mod
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.providers.jev_evaluate import (
        JevEvaluateProvider,
        _bounded_retry_after,
    )

    problems: list[str] = []
    case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
    request = runner_mod._request_for_case(case)  # noqa: SLF001 - shared request builder

    ok_payload = {
        "model": "jev-probe-model",
        "answers": {
            "valid": {"type": "boolean", "value": True},
            "meaningful": {"type": "boolean", "value": True},
            "change_type": {"type": "choice", "value": "price_increase"},
            "importance": {"type": "choice", "value": "high"},
            "should_alert": {"type": "boolean", "value": True},
            "confidence": {"type": "score", "score": 3.0, "confidence": 0.9},
            # judge() passes the bounded price candidate context for the price
            # detector, so the extraction answers are required (same canonical
            # candidate keys proven by extraction_mapping_probe).
            "price_after": {"type": "choice", "value": "amt:39|cur:USD|per:mo"},
            "price_before": {"type": "choice", "value": "amt:29|cur:USD|per:mo"},
            "price_direction": {"type": "choice", "value": "up"},
        },
    }

    def read_request(conn: socket.socket, timeout: float = 2.0) -> None:
        """Consume the request (headers + Content-Length body) so the server
        never races the client; small loopback bodies only."""
        conn.settimeout(timeout)
        data = b""
        while b"\r\n\r\n" not in data:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            data += chunk
        head, _, _ = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    length = 0
                break
        left = length - (len(data) - len(head) - 4)
        while left > 0:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            left -= len(chunk)

    def send_http(conn: socket.socket, status: bytes, body: bytes = b"",
                  extra: bytes = b"") -> None:
        conn.sendall(status + extra
                     + b"Content-Length: " + str(len(body)).encode("ascii")
                     + b"\r\nConnection: close\r\n\r\n" + body)

    def run_server(behaviors: list[str]) -> tuple:
        """Scripted loopback server; returns (port, connection_count, stop)."""
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("127.0.0.1", 0))
        listen.listen(8)
        port = listen.getsockname()[1]
        count: list[int] = [0]
        stopped: list[bool] = [False]

        def handle(conn: socket.socket, behavior: str) -> None:
            try:
                if behavior == "stall":
                    # Accept, never respond: forces a real client read timeout.
                    conn.settimeout(2.0)
                    try:
                        while conn.recv(65536):
                            pass
                    except OSError:
                        pass
                    time.sleep(4.0)
                elif behavior == "ok":
                    read_request(conn)
                    send_http(conn, b"HTTP/1.1 200 OK\r\n",
                              json.dumps(ok_payload).encode("utf-8"))
                elif behavior == "ratelimit":
                    read_request(conn)
                    send_http(conn, b"HTTP/1.1 429 Too Many Requests\r\n",
                              extra=b"Retry-After: 0.05\r\n")
                elif behavior == "server_error":
                    read_request(conn)
                    send_http(conn, b"HTTP/1.1 503 Service Unavailable\r\n")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        def serve() -> None:
            listen.settimeout(0.5)
            while not stopped[0]:
                try:
                    conn, _addr = listen.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                idx = count[0]
                count[0] += 1
                threading.Thread(
                    target=handle,
                    args=(conn, behaviors[min(idx, len(behaviors) - 1)]),
                    daemon=True,
                ).start()

        threading.Thread(target=serve, daemon=True).start()

        def stop() -> None:
            stopped[0] = True
            try:
                listen.close()
            except OSError:
                pass

        return port, count, stop

    original = {name: os.environ.get(name)
                for name in ("JEV_ENDPOINT", "JEV_API_KEY", "JEV_MODEL", "JEV_PROTOCOL")}
    listeners: list = []
    try:
        os.environ["JEV_API_KEY"] = _HTTP_API_KEY_POISON
        os.environ["JEV_MODEL"] = "jev-probe-model"
        os.environ["JEV_PROTOCOL"] = "evaluate"

        def endpoint_for(port: int) -> str:
            return f"http://127.0.0.1:{port}/v1/evaluate"

        # --- 1. HTTP 429 -> success: truthfully retried, retries=1 ----------
        port, count, stop = run_server(["ratelimit", "ok"])
        listeners.append(stop)
        os.environ["JEV_ENDPOINT"] = endpoint_for(port)
        after_429 = JevEvaluateProvider(timeout_s=2.0, retries=2).judge(
            get_detector("price"), request)
        stop()
        if not after_429.schema_valid or after_429.error_category != "none":
            problems.append(
                f"retry probe: 429 -> success must yield a clean result, "
                f"got {after_429.error_category!r} / {after_429.provider_error!r}"
            )
        if after_429.retries != 1:
            problems.append(
                f"retry probe: 429 -> success must record retries=1, "
                f"got {after_429.retries}"
            )
        if after_429.to_record().get("retries") != 1:
            problems.append("retry probe: artifact record must carry retries=1 after a retry")
        if count[0] != 2:
            problems.append(
                f"retry probe: 429 -> success expected exactly 2 connections, "
                f"got {count[0]}"
            )

        # --- 2. timeout -> success: truthfully retried, retries=1 ----------
        port, count, stop = run_server(["stall", "ok"])
        listeners.append(stop)
        os.environ["JEV_ENDPOINT"] = endpoint_for(port)
        after_timeout = JevEvaluateProvider(timeout_s=0.4, retries=2).judge(
            get_detector("price"), request)
        stop()
        if not after_timeout.schema_valid or after_timeout.error_category != "none":
            problems.append(
                f"retry probe: timeout -> success must yield a clean result, "
                f"got {after_timeout.error_category!r} / {after_timeout.provider_error!r}"
            )
        if after_timeout.retries != 1:
            problems.append(
                f"retry probe: timeout -> success must record retries=1, "
                f"got {after_timeout.retries}"
            )
        if count[0] != 2:
            problems.append(
                f"retry probe: timeout -> success expected exactly 2 connections, "
                f"got {count[0]}"
            )

        # --- 3. HTTP 503: NOT retried; exactly one connection --------------
        port, count, stop = run_server(["server_error"])
        listeners.append(stop)
        os.environ["JEV_ENDPOINT"] = endpoint_for(port)
        server_error = JevEvaluateProvider(timeout_s=2.0, retries=2).judge(
            get_detector("price"), request)
        stop()
        if server_error.error_category != "provider":
            problems.append(
                f"retry probe: HTTP 503 must keep error_category='provider', "
                f"got {server_error.error_category!r}"
            )
        if "HTTP 503" not in (server_error.provider_error or ""):
            problems.append(
                f"retry probe: HTTP 503 provider_error must be bounded 'HTTP 503', "
                f"got {server_error.provider_error!r}"
            )
        if "429" in (server_error.provider_error or ""):
            problems.append("retry probe: HTTP 503 error must not mention 429")
        if server_error.retries != 0:
            problems.append(
                f"retry probe: non-retryable HTTP 503 must stop immediately "
                f"(retries=0), got {server_error.retries}"
            )
        if count[0] != 1:
            problems.append(
                f"retry probe: non-retryable HTTP 503 must open exactly ONE "
                f"connection, got {count[0]}"
            )
        if server_error.result is not None or server_error.schema_valid:
            problems.append("retry probe: a 503 failure must not fabricate a result")

        # --- 4. connection refused: NOT retried ----------------------------
        os.environ["JEV_ENDPOINT"] = "http://127.0.0.1:1/v1/evaluate"
        refused = JevEvaluateProvider(timeout_s=2.0, retries=2).judge(
            get_detector("price"), request)
        if refused.error_category != "provider":
            problems.append(
                f"retry probe: connection refused must keep error_category='provider', "
                f"got {refused.error_category!r}"
            )
        if "URLError (details withheld)" not in (refused.provider_error or ""):
            problems.append(
                f"retry probe: connection-refused provider_error must be bounded "
                f"withheld, got {refused.provider_error!r}"
            )
        if refused.retries != 0:
            problems.append(
                f"retry probe: connection refused must stop immediately "
                f"(retries=0), got {refused.retries}"
            )
        if refused.result is not None or refused.schema_valid:
            problems.append("retry probe: a refused connection must not fabricate a result")
        if "127.0.0.1" in (refused.provider_error or ""):
            problems.append("retry probe: connection-refused error leaked the endpoint host")

        # --- 5. Retry-After clamping (P2-2): deterministic, network-free ---
        class _FakeExc:
            def __init__(self, headers: dict):
                self.headers = headers

        if _bounded_retry_after(_FakeExc({"Retry-After": "120"}),
                                default=1.0, cap=60.0) != 60.0:
            problems.append(
                "retry probe: Retry-After above the cap must clamp to the cap "
                "(60s), not silently fall back to the 1s default"
            )
        if _bounded_retry_after(_FakeExc({"Retry-After": "2"}),
                                default=1.0, cap=60.0) != 2.0:
            problems.append(
                "retry probe: in-range Retry-After must pass through unchanged"
            )
        if _bounded_retry_after(_FakeExc({"Retry-After": "not-a-number"}),
                                default=1.0, cap=60.0) != 1.0:
            problems.append(
                "retry probe: malformed Retry-After must use the 1s default"
            )
        if _bounded_retry_after(_FakeExc({}), default=1.0, cap=60.0) != 1.0:
            problems.append("retry probe: absent Retry-After must use the 1s default")
        if _bounded_retry_after(_FakeExc({"Retry-After": "-5"}),
                                default=1.0, cap=60.0) != 1.0:
            problems.append(
                "retry probe: non-positive Retry-After must use the 1s default"
            )
    finally:
        for stop in listeners:
            stop()
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
    problems.extend(extraction_mapping_probe())
    problems.extend(timeout_classification_probe())
    problems.extend(retry_semantics_probe())
    return problems
