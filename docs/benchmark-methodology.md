# Benchmark methodology

Reproducible detector-level benchmark per replynodes/replynodes-fetcher #487.

## Commands

```sh
pip install -e .
python3 scripts/generate_dataset.py --check

jev-monitor validate                                  # ALL gates below
jev-monitor redact-check                              # redaction integrity + rubric citations
# Run-local output goes to the gitignored results/runs/ dir; committed
# artifacts under results/committed/ are never overwritten by run commands.
jev-monitor benchmark --split held_out --provider heuristic --out results/runs/held_out-deterministic.json
jev-monitor benchmark --split dev --provider heuristic --out results/runs/dev-deterministic.json
jev-monitor benchmark --split held_out --provider heuristic --fault-injection-rate 0.09 --out results/runs/held_out-deterministic-fault-injection.json
jev-monitor blocked-live                              # safe default: gitignored results/runs/live-jev-blocked.json
jev-monitor repro-check --split held_out
```

A bare `blocked-live` never writes under `results/committed/` (exit 1).
Regenerating the committed BLOCKED artifact is deliberate only:

```sh
jev-monitor blocked-live --committed --out results/committed/blocked/live-jev-blocked.json
```

`jev-monitor validate` also proves the #487 split separation: it fingerprints
every before/after snapshot (raw and normalized, alone and concatenated) and
fails on any content hash shared between `dev/` and `held_out/`.

## Fixed thresholds (committed before any held-out run)

`benchmark/thresholds.json` (SHA-256 locked in
`benchmark/thresholds.lock.json`; `jev-monitor validate` fails on mismatch):

- price: `exact_price_accuracy >= 0.95`, `false_change_rate <= 0.02`
- saas_pricing: `precision >= 0.90`, `recall >= 0.85`, `false_alert_rate <= 0.10`
- product_change: `precision >= 0.90`, `recall >= 0.80`, `false_alert_rate <= 0.10`

Thresholds are never tuned after a held-out run; a change requires an explicit
issue update before the next held-out run.

## Metric definitions

Primary decision = `should_alert` (the actionable alert). The `meaningful`
judgement confusion matrix is reported alongside.

- precision = TP/(TP+FP) over the primary decision
- recall = TP/(TP+FN)
- F1 = harmonic mean
- false_alert_rate = alerts on label-noise cases / noise cases
- false_negative_rate = missed alerts / label-alert cases
- false_change_rate (price) = predicted-meaningful on label-unchanged / unchanged
- false_change_cases = the exact integer numerator behind each false_change_rate
  (per detector and overall). The overall false_change_rate derives from the
  summed per-detector exact counts — never from rounding per-detector rates —
  so the aggregate cannot drift by per-detector rounding.
- exact_price_accuracy = amount AND currency AND direction match
- latency p50/p95 (ms, measured per case)
- input volume (bytes, per detector)
- estimated inference cost: `cloud ceil(bytes/4) -> tokens` x the committed
  cost model (`benchmark/cost-model.json`); with placeholder rates the
  estimate is `null` and `estimated_cost_available=false` — this repo does not
  invent Jev/gateway pricing.
- schema_invalid_rate, provider_error_rate (per detector, over all cases)

Price also reports amount/currency/direction accuracy separately.

### Price accuracy denominator (exact policy)

The #487 gate `exact_price_accuracy >= 0.95` is measured over **evaluable
price cases that carry an explicit extraction expectation** (`expected.price`
— amount, currency and direction to match). No-price-token and pure-noise
price cases without an extraction expectation are excluded by design: there is
nothing to compare an extraction against, so including them would either fake
a miss or silently drop them. The artifact records **both** numbers so the
denominator is auditable:

- `price_cases_total` — all evaluable price cases (the wider set).
- `price_cases_with_expectation` — the denominator actually used for
  `exact_price_accuracy` / `price_amount_accuracy` /
  `price_currency_accuracy` / `price_direction_accuracy`.

For the committed held-out run: 41 total price cases, 36 with an extraction
expectation; the 5 excluded are no-price-token/noise edge cases without
`expected.price`.

Scores for undecidable rows (schema-invalid / provider-error) are excluded
from precision/recall and reported in their own rates; the artifact records
the counts.

Detector-specific typing: each result's `change_type` is validated per
detector against its `DetectorSpec.allowed_change_types`, on top of the
global `schemas/detector-result.schema.json` union. A provider cannot emit
another detector's type and pass validation; such a row is recorded as
schema-invalid and counted in `schema_invalid_rate`, never measured as a
judgement.

## Reproducibility

`runner.comparable_view()` strips volatile fields (timestamps, latency, run
id, environment) and `repro-check` asserts two consecutive runs produce
identical metrics, evaluations and per-case verdicts against the committed
artifact.

## Evaluation kinds and launch claim

- `synthetic-deterministic` — rule baseline; pipeline evidence only; never
  launch evidence.
- `live-jev` — a real, configured live Jev runtime ran. Only this kind can
  pass the gates. Requires authorized credentials via environment variables;
  raw result provenance is preserved in the artifact.
- `live-jev-blocked` — the dedicated machine-readable BLOCKED artifact written
  when no authorized live Jev runtime is available: zero metrics, an
  unverified threshold evaluation, and `launch_claim.status = "blocked"`. It
  is deliberately distinct from `live-jev` so a downstream reader keying on
  `evaluation_kind` alone can never mistake a blocked artifact for live
  evidence. The run-local copy defaults to the gitignored `results/runs/`;
  the committed copy is regenerated only through the explicit
  `blocked-live --committed` guard.

`launch_claim.status` is `passed` **only** for a live-jev run with all checks
green **and** `dataset.human_labeled == true` (every held-out label carries
`labeling.review_status = "independent-review-complete"`); otherwise `blocked`
(no runtime / labels pending independent review / a blocker is recorded) or
`failed` (live run below thresholds). `jev-monitor gate --result <artifact>`
refuses `PASS` while `launch_claim.reasons` is non-empty or
`dataset.human_labeled` is false, so a launch blocker can never be reported as
green. Nothing in this repository claims the launch thresholds passed.

## Live Jev evaluation protocol (`jev-evaluate`)

The typed `/v1/evaluate` endpoint (e.g.
`https://ai-gateway.vercel.sh/v1/evaluate`) has **no chat completions
surface**, so provider `jev-evaluate` implements its own wire contract instead
of reusing `jev-http`:

```
POST {JEV_ENDPOINT}
Authorization: Bearer {JEV_API_KEY}
{"model": "<JEV_MODEL or detector id>",
 "state": "BEFORE (normalized):\n<bounded normalized before>\n\nAFTER (normalized):\n<bounded normalized after>",
 "questions": {                          # MAP of question id -> question
   "valid":        {"type": "boolean", "instructions": "..."},
   "change_type":  {"type": "choice", "instructions": "...",
                    "criteria": {"<option>": "<description>", ...}},
   "confidence":   {"type": "score", "instructions": "...",
                    "criteria": ["<level 1>", ..., "<level N>"]},
   ...
 }}
```

- `state` is a **string** carrying the bounded, normalized BEFORE/AFTER
  evidence (never raw page content) in a deterministic template — the
  `bounded_evidence` discipline (`normalize_bounded`, 64,000 char cap,
  truncation recorded in the artifact) reused from `jev-http` /
  `jev-command`. Verified against the live gateway: `state` as an object is
  rejected (HTTP 400) by `/v1/evaluate`.
- `questions` is a **map** of detector-specific typed questions, each with
  `type` + `instructions` (choice adds a `criteria` map of option->description,
  score adds an ordered `criteria` array of 2-10 level descriptions). The live
  gateway accepts the `boolean` question type for binary decisions (the noul
  family); `choice` and `score` complete the official type set. Base question
  content is static, built only from the committed DetectorSpec — never from
  page text. The price detector additionally carries three dynamic extraction
  `choice` questions whose criteria derive ONLY from the bounded candidate
  price tokens of the evidence (see "Price extraction" below).
- The endpoint answers with a typed answers map. The gateway renders the
  boolean/noul family as `{"type": "boolean", "probability": 0.85}` (verified
  live contract); the native System One endpoint renders it as
  `{"type": "noul", "noul": 0.8}`. Choice answers carry the chosen option in
  `choice` (or `value`);
  score answers carry the score position in `score` (or `value`) plus a
  `confidence`. The mapper accepts both renderings (`boolean` and `noul`).

Typed-answer mapping into `schemas/detector-result.schema.json`:

| answer | maps to | rule |
| --- | --- | --- |
| `valid` / `meaningful` / `should_alert` (boolean / noul) | same-named result field | explicit `value` when present; otherwise the documented rule `value = probability >= 0.5`, where probability comes from `probability` / `noul` / `confidence` in [0,1] |
| `change_type` (choice) | `change_type` | chosen option must be inside the detector's `allowed_change_types`; otherwise unmappable |
| `importance` (choice) | `importance` | chosen option must be `low`/`medium`/`high` |
| `confidence` (raw probability) | `confidence` | the raw `probability`/`noul` of the `should_alert` answer, else the `meaningful` answer; else the `confidence` reported on the score question answer — all in [0,1] |
| `price_after` / `price_before` (choice, price only) | `details.extraction` amount/currency/period per side | chosen option must be a bounded candidate token key for that side or a fixed sentinel (`no_price_token` / `unclear`); sentinels map to `null` fields; anything else is unmappable |
| `price_direction` (choice, price only) | `details.extraction.direction` | chosen option must be `up` / `down` / `unchanged` / `unknown` |

### Price extraction via typed choices

The typed `/v1/evaluate` contract has no free-form extraction answer, so the
price detector's `details.extraction` (required by the detector contract —
`docs/detector-contracts.md`) is modelled as three dynamic `choice` questions.
Their criteria are derived **only** from bounded candidate price tokens
(`normalize.extract_price_tokens` over the bounded 64,000-char normalized
BEFORE/AFTER evidence, deduplicated, capped at 8 candidates per side, plus the
fixed sentinels `no_price_token` / `unclear`). Candidate counts and a
truncation flag are recorded in the per-case `usage`
(`price_candidates_after`, `price_candidates_before`,
`price_candidates_truncated`) so the bounded-evidence limitation stays honest.

`map_answers` turns the chosen options deterministically into
`details.extraction`:

```
amount, currency, period,            (from price_after)
amount_before, currency_before,      (from price_before)
direction  (up | down | unchanged | unknown)
```

A chosen option that is **not** in the bounded candidate set for that side (an
invented amount) is unmappable and recorded as `schema_invalid` naming the
failing answer id — an amount is never computed, guessed or fabricated. The
sentinel `no_price_token` / `unclear` map to `null` amount/currency/period
(honest "no token / unclear" evidence). This preserves the exact-price
denominator (`price_cases_with_expectation` — see "Price accuracy denominator"
above): every evaluable price case with `expected.price` is now actually
compared against a real extraction instead of an empty one.

`confidence_source` is always `jev-raw-probability` for this provider (raw
uncalibrated probability semantics, docs/detector-contracts.md). The result
`summary` and `details` are built deterministically from the mapped answers —
never from raw response text. An answer that cannot be mapped safely is
recorded as `schema_invalid` (or `provider` when the response envelope is
missing) with a bounded `provider_error` naming the failing answer id/rule;
**fields are never fabricated**. Exact numeric usage/cost returned by the
gateway pass through to the per-case `usage` record (bounded numeric/bool
passthrough only).

Protocol gate: `JEV_PROTOCOL` must be unset or `evaluate` for
`jev-evaluate`; any other value reports the provider as not-configured, and
`jev-http` (chat-completions) refuses to run when `JEV_PROTOCOL=evaluate`, so
a chat-shaped configuration can never accidentally hit an evaluate endpoint.
`JEV_ENDPOINT` containing `/chat/completions` is refused by `jev-evaluate`
(the contract targets `/v1/evaluate` only). Failure messages never echo the
endpoint URL, response fragments or credential text — only bounded status
categories (e.g. `HTTP 400`) and withheld-detail type names are recorded.
HTTP 429 (rate limit) is retried with a bounded `Retry-After` backoff (header
value clamped to the 60s cap — a requested wait above the cap waits the full
60s, never silently reduced to 1s — and 1s default when the value is absent or
malformed) instead of an immediate burst
re-send; when retries are exhausted the case records `provider_error =
"HTTP 429"` only, keeping `error_category = "provider"` so rate-limit metrics
stay wired. A socket/read timeout (`URLError` wrapping `TimeoutError`, or a
bare `TimeoutError`) is classified separately as
`error_category = "timeout"` and retried with a bounded deterministic
exponential backoff (1s, 2s, 4s, capped at 8s) — never a fabricated semantic
result; endpoint/key/error detail stays withheld (`"timeout (details
withheld)"`). Both retry loops are capped by the provider's `retries` setting
and record the exhausted `retries` count truthfully. Any other HTTP status
(4xx/5xx) and any non-timeout connection/other error is **not** retried: the
first failure is recorded immediately as a bounded `provider`-category error
(status code or withheld detail only), so a permanent 400/503 or a refused
connection is never re-sent uselessly. A retry that eventually succeeds
records `retries = attempt - 1` in the per-case result/artifact, matching
`jev-http`.

Run-local live runs write to the gitignored `results/runs/` (e.g.
`results/runs/live-jev-dev.json`); committed artifacts are never overwritten.

## Redaction integrity

`jev-monitor redact-check` (also run inside `validate`) proves, over a fixed
probe and every committed artifact, that recorded SHA-256/hash fields survive
byte-exact and render as valid 64-hex digests (including e-prefixed hashes),
that every secret-shaped value has its FULL token removed — only the safe
non-secret prefix plus `[REDACTED]` survives (e.g. `sk-[REDACTED]`,
`AKIA[REDACTED]`, `Bearer [REDACTED]`), so the original credential text is
absent from the output — that `config.config_sha256` recomputes from the
artifact, and that all rubric citations resolve to
`docs/provenance-and-labeling.md`.

## Docker

`docker compose run --rm validate` validates the committed artifacts baked
into the image (read-only). `docker compose run --rm benchmark` writes
run-local output to the ephemeral `benchmark-runs` named volume at
`/app/results/runs` — it never overwrites committed result artifacts or dirties
the working tree (see `docker-compose.yml`).