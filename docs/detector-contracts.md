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

`normalize.normalize_html` strips script/style, converts HTML entities,
collapses whitespace and caps at 64,000 chars / 2,000 evidence lines.
Requests carry `sha256` + `byte_count` per snapshot, verified at benchmark
time.

## Providers

- `heuristic` — deterministic rule baseline, mode `deterministic`. Documented
  rules; label-blind; never launch evidence.
- `jev-command` — live Jev via an explicitly configured `JEV_COMMAND` (stdin
  JSON in, stdout JSON out); env-configured only.
- `jev-http` — live Jev via `JEV_ENDPOINT` + `JEV_API_KEY` (+ optional
  `JEV_MODEL`), OpenAI-compatible chat endpoint by default; never logs values.

Live results must record `confidence_source = "jev-raw-probability"`.
Without an authorized Jev runtime the benchmark emits a machine-readable
BLOCKED artifact and never invents semantic scores.