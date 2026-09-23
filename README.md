# jev-change-monitor

Typed website-change events and reproducible detector-level benchmarks for
**price**, **SaaS pricing**, and **product-change** monitoring with Jev.

> Turn raw website diffs into typed, actionable events with Jev.

Demo/benchmark OSS repository, part of ReplyNodes Monitors
(replynodes/replynodes-fetcher #480; OSS tracking issue #493; benchmark
release gate #487). See `docs/boundary.md` for the exact hosted/OSS boundary.

## What this repository demonstrates

- Typed detector contracts for exactly three P0 detectors: `price`,
  `saas_pricing`, `product_change`.
- Before/after normalization over untrusted page content (bounded, injection
  signals, schema-validated output).
- A canonical change-event schema (`schemas/change-event.schema.json`) with
  `event_version`, `event_id`, timestamps and evidence references.
- A signed-webhook sender/receiver example: HMAC-SHA256 over
  `timestamp + "." + raw_body` with `X-ReplyNodes-Event-Id`,
  `X-ReplyNodes-Timestamp`, `X-ReplyNodes-Signature`, replay tolerance and
  constant-time verification (canonical headers per #485).
- Labeled fixture datasets with provenance (`synthetic` / `derived` /
  `real_public`) and a reproducible benchmark runner that reports
  detector-level precision/recall/F1, false-alert, false-negative, latency,
  input volume, estimated inference cost, schema-invalid and provider-error
  rates, and price amount/currency/direction accuracy.
- Committed, frozen release thresholds (fixed before any held-out run) and
  committed result artifacts — including a machine-readable **BLOCKED**
  artifact when no authorized live Jev runtime is available.

## Quick start

```sh
pip install -e .
python3 scripts/generate_dataset.py --check   # fixtures match the generator
jev-monitor validate                          # schemas, counts, thresholds, artifacts
jev-monitor demo --detector price             # run one example case
jev-monitor benchmark --split held_out --provider heuristic
jev-monitor repro-check --split held_out      # deterministic metrics are byte-stable
jev-monitor webhook-demo                      # signed sender -> receiver roundtrip
```

Docker:

```sh
docker compose run --rm validate
docker compose run --rm benchmark
```

## Repository layout

| Path | Purpose |
| --- | --- |
| `schemas/` | canonical JSON Schemas (request, result, fixture, event, benchmark artifact) |
| `src/jev_change_monitor/` | normalization, detector contracts, providers, events, webhook, benchmark runner |
| `datasets/` | `held_out/` (111 cases) and `dev/` (15 tuning cases), generated, provenance-labeled |
| `benchmark/` | frozen thresholds + lock, cost model |
| `examples/` | one case per detector + signed webhook sender/receiver |
| `results/` | committed benchmark artifacts + BLOCKED live-Jev artifact |
| `docs/` | boundary, detector contracts, benchmark methodology, provenance/labeling |

## Launch-evidence honesty

The launch thresholds from #487 are **not** passed by this repository today:

- The local `heuristic-baseline` provider is pipeline evidence only. It is
  deliberately not Jev and never counts toward launch thresholds.
- Without an authorized live Jev runtime (env-configured `JEV_ENDPOINT` +
  `JEV_API_KEY` or `JEV_COMMAND` — values are never logged), the benchmark
  writes `results/committed/blocked/live-jev-blocked.json` with
  `launch_claim.status = "blocked"` and the exact missing capabilities.
- Held-out labels are rubric drafts pending independent human
  review/adjudication, which is a separate launch blocker.

`jev-monitor gate --result <artifact>` tells you the launch claim for any
artifact.

## Notes

- Jev is the The TypeSafe AI judgement model accessed via the Vercel AI
  Gateway in the hosted product. This OSS project is not affiliated with
  TypeSafe AI; it only demonstrates and benchmarks the judgement pattern.
- Slack/Telegram are P1 and are not implemented or advertised here.
- This repository is not a changedetection.io replacement (see
  `docs/boundary.md`).

## License

Apache-2.0 — see `LICENSE`. Fixture page content is authored for this
repository; derived fixtures record their public source and pattern-only
derivation per case.