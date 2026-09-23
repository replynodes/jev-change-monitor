# Examples

One runnable case per detector, plus the signed-webhook sender/receiver pair.

Detector examples (static fixtures — authored snapshots for the fictional
"Acme Analytics" product, never real customer content, never Jev outputs):

- `price/case.json` — price increase $29 → $39 (held-price-002)
- `saas_pricing/case.json` — usage limit change 10,000 → 50,000 API calls (held-saas-003)
- `product_change/case.json` — "Bulk export" feature added (held-product-001)

Run them exactly as the benchmark does:

```sh
pip install -e .
jev-monitor demo --detector price
jev-monitor demo --detector saas_pricing
jev-monitor demo --detector product_change
```

Each `case.json` is a byte-for-byte copy of the named held-out fixture,
extracted deterministically from `datasets/held_out/held-out-cases.jsonl`
(`python3 -c "..."` extraction used `case_id` selection — see the generator
`scripts/generate_dataset.py` for the single source of truth).

## Signed webhook example (HMAC-SHA256)

- `fixture_sender.py` — builds a canonical change event from a fixture case,
  signs `timestamp + "." + raw_body`, sends headers
  `X-ReplyNodes-Event-Id`, `X-ReplyNodes-Timestamp`, `X-ReplyNodes-Signature`.
- `fixture_receiver.py` — verifies the signature with constant-time comparison,
  a configurable replay tolerance (default 300 s) and event_id
  header/body consistency; returns HTTP 200 only on success.

Terminal A:

```sh
python3 examples/webhook/fixture_receiver.py --port 8765
```

Terminal B:

```sh
python3 examples/webhook/fixture_sender.py \
  --url http://127.0.0.1:8765/webhook \
  --event examples/price/case.json
```

The secret default `fixture:recipe-demo-change-me-2026` is a **labeled static
fixture**, not a real credential. Production secrets are hosted-side only and
never appear in this repository. There is no Slack/Telegram delivery here —
webhooks are the only P0 channel (replynodes/replynodes-fetcher#485).
The canonical event schema is `schemas/change-event.schema.json`
(`event_version`, `event_id`, `event_type`, detector fields, evidence refs;
no tenant routing or workspace metadata).