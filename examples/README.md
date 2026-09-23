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

Each `case.json` carries the *same case object* as the named held-out fixture
(same `case_id` and every field; only the file format differs — the datasets
are compact JSONL while the examples are pretty-printed with sorted keys, so
the raw bytes are not byte-for-byte identical). Cases are selected
deterministically by `case_id` from `datasets/held_out/held-out-cases.jsonl` —
see the generator `scripts/generate_dataset.py` for the single source of
truth. Verify object equality yourself:

```sh
python3 - <<'PY'
import json
for name in ("price", "saas_pricing", "product_change"):
    cases = [json.loads(l) for l in open("datasets/held_out/held-out-cases.jsonl")
             if l.strip()]
    example = json.load(open(f"examples/{name}/case.json"))
    fixture = next(c for c in cases if c["case_id"] == example["case_id"])
    assert example == fixture, name
    print(f"{name}/case.json == held-out fixture {example['case_id']}")
print("examples match their held-out fixtures (object equality)")
PY
```

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