"""Deterministic integrity checks exposed as a CLI gate (`jev-monitor redact-check`).

These are integration/repro checks over the *committed* tree, not unit tests:

1. Redaction probe — a fixed JSON-able structure containing known credential
   shapes *and* valid SHA-256 digests (including e-prefixed ones, which the
   old over-broad pattern corrupted) is pushed through `redact.redact_value`.
   Every hash field must survive byte-exact and render as a valid 64-hex
   digest; every secret-shaped value must contain the literal `[REDACTED]`
   placeholder; non-secret plain text must be untouched.
2. Committed benchmark artifacts — every hash-labeled field (`*sha256*`,
   `*hash*`, `*digest*`, `*checksum*`) must be null or a valid 64-hex digest,
   the artifact self-hash must recompute to the recorded `artifact_sha256`,
   the (non-null) `config.config_sha256` must recompute from the artifact's
   provider/dataset/thresholds/fault settings, and no artifact may contain
   the `[REDACTED]` placeholder at all (committed artifacts carry no secrets).
3. Rubric citations — every fixture and example must cite the existing
   `docs/provenance-and-labeling.md` rubric path and may only reference
   `docs/...md` files that actually exist (no broken rubric paths).

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
_AWS_SHAPE = "AKIA" + "IOSFODNN7EXAMPLE12345"
_RN_TEST_SHAPE = "rn_test_" + "abcdefghijklmnopqrstuv"

REDACTION_PROBE = {
    "secret": _OPENAI_SHAPE,
    "token": _GITHUB_SHAPE,
    "api_key": _RN_LIVE_SHAPE,
    "aws": _AWS_SHAPE,
    "bearer": "Authorization: Bearer abcdefghijklmnop123456",
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
HASH_PROBE_KEYS = ("config_sha256", "artifact_sha256", "digest_list")
HASH_PROBE_PATH_SEGMENTS = (
    ("dataset", "sha256"),
    ("thresholds", "sha256"),
)


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
        if "[REDACTED]" not in result[key]:
            problems.append(f"probe: secret-shaped value under {key!r} was NOT redacted")
    for index, entry in enumerate(result["history"]):
        if "[REDACTED]" not in entry.get("note", ""):
            problems.append(f"probe: secret-shaped value in nested history[{index}] was NOT redacted")
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


def run_redact_check(results_committed: Path = RESULTS_COMMITTED) -> list[str]:
    """All deterministic integrity checks; one problem per list entry."""
    problems: list[str] = []
    problems.extend(redaction_probe())
    problems.extend(committed_artifact_checks(results_committed))
    problems.extend(rubric_citations())
    return problems