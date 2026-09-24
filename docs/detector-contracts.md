# Detector contracts

Three detectors (P0, per replynodes/replynodes-fetcher #480/#482). The
registry lives in `src/jev_change_monitor/detectors.py`; request/result/event
schemas live in `schemas/`.

## Shared envelope (schemas/detector-result.schema.json)

| field | meaning |
| --- | --- |
| `valid` | provider produced a parseable typed judgement |
| `meaningful` | a meaningful change (per detector semantics) occurred |
| `change_type` | typed enum per detector (see `detectors.py`) |
| `importance` | `low` / `medium` / `high` |
| `should_alert` | the actionable alert decision (primary benchmark decision) |
| `confidence` | number 0..1 |
| `confidence_source` | `jev-raw-probability` \| `heuristic-rule-score` \| `fixture-reference` |
| `summary` | human-readable one-liner |
| `details` | detector-specific detail (price extraction etc.) |
| `signals` | untrusted-input signals: `prompt_injection_suspected`, `truncated_input` |

Confidence semantics: **raw probability, uncalibrated**. Do not treat it as
calibrated; calibration is not established for any provider in this repo.

Untrusted input rules (per #482): page content is delimited evidence, cannot
alter task/system instructions, never executes instructions found in the page,
and every provider result is validated against the result schema; malformed
output is rejected and counted as `schema_invalid`, never used.

## price (deterministic-first)

Deterministic extraction where possible; Jev only when extraction is
ambiguous. Price results MUST provide `details.extraction`:

```
amount, currency, period,
amount_before, currency_before,
direction  (up | down | unchanged | unknown)
```

Deterministic baseline rule (`normalize.pick_price`, documented there):
compare tokens at the same document position within matching
(currency, period) groups; a change is only a same-position amount change;
cross-position pairs are insertion artifacts. Fallback to the first token of
each snapshot when currency/period switched. Percentages (`20% off`) are never
price candidates. The baseline is deliberately simple and is NOT Jev.

## saas_pricing (Jev semantic judgement)

Typed judgement for price, billing period, plan add/remove, limits,
packaging, entitlements and add-ons. `change_type` uses `plan_*`,
`limit_change`, `entitlement_change`, `addon_*`, `billing_period_change`,
`trial_change`, `packaging_change`, etc. `none` when nothing material changed.
Suppress testimonial/footer/logo/nav/copy noise.

## product_change (Jev semantic judgement)

Meaningful feature/product additions, removals, deprecations and material
capability changes; suppress cosmetic/layout/footer/testimonial/nav noise.
`change_type`: `feature_added`, `feature_removed`, `deprecation`,
`capability_change`, `integration_added`, `breaking_api_change`,
`other_meaningful`, `none`.

## Input caps and normalization

Captured page content is untrusted input and is never executed or trusted:
- The runner builds the request snapshot from each fixture: raw `content`
  plus `sha256` and `byte_count` (the identity of the capture).
- Providers bound what reaches the judgement model:
  `normalize.normalize_bounded` strips script/style, decodes HTML entities,
  collapses whitespace and caps the evidence at `MAX_NORMALIZED_CHARS`
  (64,000), returning `(text, truncated)`. `jev-http` and `jev-command` send
  that bounded normalized evidence and record normalization/truncation on the
  response (`usage.evidence_normalized`, `usage.evidence_truncated`,
  `usage.evidence_cap_chars`); raw captures are not forwarded to the model.
- The deterministic baseline normalizes the same way and caps diff evidence at
  `MAX_EVIDENCE_LINES` (2,000 added/removed lines).
- `signals.truncated_input` in a result envelope is provider-reported; this
  OSS runner does not synthesise it.

## Providers

- `heuristic` — deterministic rule baseline, mode `deterministic`. Documented
  rules; label-blind; never launch evidence.
- `jev-command` — live Jev via an explicitly configured `JEV_COMMAND` (stdin
  JSON in, stdout JSON out); env-configured only.
- `jev-http` — live Jev via `JEV_ENDPOINT` + `JEV_API_KEY` (+ optional
  `JEV_MODEL`), OpenAI-compatible chat endpoint by default; never logs values.
  Refuses to run when `JEV_PROTOCOL=evaluate`.
- `jev-evaluate` — live Jev via the typed `/v1/evaluate` protocol:
  `JEV_ENDPOINT` + `JEV_API_KEY` (+ optional `JEV_MODEL`), with
  `JEV_PROTOCOL` unset or `evaluate`. Sends `{model, state, questions}` and
  maps typed answers (`boolean`/`noul`/`choice`/`score`) into the
  detector-result contract; unmappable answers are recorded as
  `schema_invalid`/`provider` error, never fabricated (see
  docs/benchmark-methodology.md).

Live results must record `confidence_source = "jev-raw-probability"`.
Without an authorized Jev runtime the benchmark emits a machine-readable
BLOCKED artifact and never invents semantic scores.