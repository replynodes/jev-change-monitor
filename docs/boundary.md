# Boundary — what this repository is and is not

This repository is the **OSS companion** for ReplyNodes Monitors
(`replynodes/replynodes-fetcher` #480, #482, #485, #487, and this repo's
tracking issue #493). It demonstrates and benchmarks typed semantic judgement
over before/after website changes for exactly three detectors:

- `price`
- `saas_pricing`
- `product_change`

## In scope (OSS)

- detector contracts and prompts for the three P0 detectors
- before/after normalization and bounded evidence
- canonical change-event schema
- fixture datasets with provenance and labels
- reproducible detector-level benchmark runner + committed result artifacts
- signed webhook sender/receiver example with HMAC-SHA256 verification
- Docker/local setup, contribution guide, examples

## Not in scope (hosted/private, stays in ReplyNodes)

Per #480/#493, the hosted product owns:

- production scheduler and lease management
- tenant/workspace auth and API-key verification
- credit ledger, holds/reservations (1/1/10 monitor-run model)
- production history, durable evidence repository and retention
- PostgreSQL outbox, delivery retries, dead-letter handling
- Slack/Telegram adapters and secrets (P1, not a launch capability)
- production MCP orchestration

Nothing in this repository implements, imports or copies ReplyNodes'
production scheduler, auth, billing, outbox or MCP code, and no credentials or
secrets from the hosted product are used here. The webhook example uses a
clearly labeled **fixture** secret only.

## Not a changedetection replacement

This project is **not** a changedetection.io replacement and does not claim to
be one. `changedetection.io` remains the hosted execution/snapshot/diff
runtime (boundary per #480: "changedetection.io is execution/snapshot/diff
only"). This repo demonstrates what happens *above* raw diffs: typed semantic
judgements, canonical events and detector-level evaluation.

## Position

Turn raw website diffs into typed, actionable events with Jev.