# jev-change-monitor

Typed website-change events and reproducible detector-level benchmarks for
**price**, **SaaS pricing**, and **product-change** monitoring with Jev.

> Turn raw website diffs into typed, actionable events with Jev.

Demo/benchmark OSS repository, part of ReplyNodes Monitors
(replynodes/replynodes-fetcher #480; OSS tracking issue #493; benchmark
release gate #487). See `docs/boundary.md` for the exact hosted/OSS boundary.

> **Hosted monitors (follow-up, not yet live)**: ReplyNodes hosted monitors
> are a hosted follow-up tracked in
> [replynodes/replynodes-fetcher#493](https://github.com/replynodes/replynodes-fetcher/issues/493)
> under the [ReplyNodes GitHub org](https://github.com/replynodes). Nothing in
> this repository is a live hosted service, and no hosted monitors signup or
> endpoint is claimed here — this OSS repo demonstrates and benchmarks the
> pattern only. See `docs/boundary.md`.

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
jev-monitor redact-check                      # hashes survive, secrets redacted, rubric paths resolve
jev-monitor demo --detector price             # run one example case
jev-monitor benchmark --split held_out --provider heuristic --out results/runs/held_out-deterministic.json
jev-monitor repro-check --split held_out      # deterministic metrics are byte-stable
jev-monitor webhook-demo                      # signed sender -> receiver roundtrip
jev-monitor blocked-live                      # BLOCKED probe -> gitignored results/runs/
```

Live Jev via the typed `/v1/evaluate` protocol (provider `jev-evaluate`):

```sh
export JEV_ENDPOINT=https://ai-gateway.vercel.sh/v1/evaluate
export JEV_API_KEY=change-me                  # env-only, never logged
export JEV_MODEL=typesafe-ai/jev
export JEV_PROTOCOL=evaluate                  # protocol gate for the evaluate provider
jev-monitor demo --provider jev-evaluate      # smoke: 3 example cases
jev-monitor benchmark --split dev --provider jev-evaluate --out results/runs/live-jev-dev.json
jev-monitor benchmark --split held_out --provider jev-evaluate --out results/runs/live-jev-held_out.json
```

The evaluate provider sends `{model, state, questions}` to `JEV_ENDPOINT`
(the `/v1/evaluate` contract — the endpoint has no chat completions surface),
with bounded normalized before/after state and detector-specific typed
questions. Typed answers (`boolean`/`noul`/`choice`/`score`) map into
`schemas/detector-result.schema.json`; an answer that cannot be mapped safely
is recorded as `schema_invalid`/`provider` error rather than fabricating
fields (see `docs/benchmark-methodology.md`). For the `price` detector the
provider adds three dynamic `choice` extraction questions whose criteria come
only from bounded candidate price tokens in the normalized evidence, and maps
them deterministically into the required `details.extraction` (`amount`,
`currency`, `period`, `amount_before`, `currency_before`, `direction`) — an
amount is never invented. Socket/read timeouts are classified separately as
`timeout` with bounded backoff; HTTP 429 stays a bounded rate-limit provider
error. `jev-http` remains the chat-completions protocol and refuses to run
when `JEV_PROTOCOL=evaluate`.

Docker:

```sh
docker compose run --rm validate   # read-only check of the committed artifacts
docker compose run --rm benchmark  # writes ONLY to an ephemeral named volume; never commits
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
  `JEV_API_KEY` or `JEV_COMMAND` — values are never logged), `jev-monitor
  blocked-live` writes a machine-readable BLOCKED artifact with
  `launch_claim.status = "blocked"` and the exact missing capabilities. The
  safe default is the gitignored run-local copy
  `results/runs/live-jev-blocked.json`; the committed copy under
  `results/committed/blocked/` is regenerated only deliberately via
  `--committed` with an explicit `--out` under `results/committed/`.
- Held-out labels are rubric drafts pending independent human
  review/adjudication, which is a separate launch blocker.
- Live `jev-evaluate` held-out runs observe real metrics but do not meet the
  frozen #487 thresholds, so `launch_claim.status` stays `failed` — never
  `passed`. The most recent run (gitignored `results/runs/live-jev-*.json`)
  measured price `exact_price_accuracy` 0.886 below the 0.95 gate over the
  exact 35-row extraction denominator, plus a price `false_change_rate` and a
  saas_pricing `false_alert_rate` above their caps. When the gateway
  rate-limits a run, the bounded HTTP 429 cases stay provider-error rows with
  capped `Retry-After` backoff — never fake semantic results — and the
  artifact records the evaluable subset explicitly.

`jev-monitor gate --result <artifact>` tells you the launch claim for any
artifact and refuses `PASS` while any launch blocker (missing live Jev run,
pending independent label review, non-empty reasons) is recorded.

`jev-monitor redact-check` proves in one deterministic pass that recorded
SHA-256 fields stay byte-exact (never corrupted by redaction), that the FULL
secret token is removed from every secret-shaped value — each credential
family (`sk-`, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`, `rn_live_`, `rn_test_`,
`AKIA`, `Bearer`) is replaced by its safe non-secret prefix plus `[REDACTED]`
(e.g. `sk-[REDACTED]`, `AKIA[REDACTED]`), so the original token text never
survives in output or artifacts — every fixture cites the existing rubric at
`docs/provenance-and-labeling.md`, and a poisoned `JEV_COMMAND` (arbitrary
command text plus credential-shaped tokens) never reaches
`provider.describe()` or any result artifact — only a configured flag and an
allowlisted executable basename may be recorded.

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