# Benchmark methodology

Reproducible detector-level benchmark per replynodes/replynodes-fetcher #487.

## Commands

```sh
pip install -e .
python3 scripts/generate_dataset.py --check

jev-monitor validate                                  # ALL gates below
jev-monitor benchmark --split held_out --provider heuristic
jev-monitor benchmark --split dev --provider heuristic
jev-monitor benchmark --split held_out --provider heuristic --fault-injection-rate 0.09
jev-monitor blocked-live
jev-monitor repro-check --split held_out
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
- exact_price_accuracy = amount AND currency AND direction match
- latency p50/p95 (ms, measured per case)
- input volume (bytes, per detector)
- estimated inference cost: `cloud ceil(bytes/4) -> tokens` x the committed
  cost model (`benchmark/cost-model.json`); with placeholder rates the
  estimate is `null` and `estimated_cost_available=false` — this repo does not
  invent Jev/gateway pricing.
- schema_invalid_rate, provider_error_rate (per detector, over all cases)

Price also reports amount/currency/direction accuracy separately.

Scores for undecidable rows (schema-invalid / provider-error) are excluded
from precision/recall and reported in their own rates; the artifact records
the counts.

## Reproducibility

`runner.comparable_view()` strips volatile fields (timestamps, latency, run
id, environment) and `repro-check` asserts two consecutive runs produce
identical metrics, evaluations and per-case verdicts against the committed
artifact.

## Evaluation kinds and launch claim

- `synthetic-deterministic` — rule baseline; pipeline evidence only; never
  launch evidence.
- `live-jev` — only this kind can pass the gates. Requires authorized
  credentials via environment variables; raw result provenance is preserved in
  the artifact.

`launch_claim.status` is `passed` only for a live-jev run with all checks
green; otherwise `blocked` (no runtime / labels not independently reviewed) or
`failed` (live run below thresholds). Nothing in this repository claims the
launch thresholds passed.