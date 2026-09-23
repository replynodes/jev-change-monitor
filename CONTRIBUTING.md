# Contributing — adding a detector

Detector additions follow the same pattern as the three P0 detectors.

## 1. Contract

Add a `DetectorSpec` to `src/jev_change_monitor/detectors.py`:

- `id` (kebab-case), `version`, one-paragraph description
- `prompt_system` / `prompt_user` referencing `<before>` / `<after>` evidence
  and demanding a typed JSON result
- `allowed_change_types` — extend `schemas/detector-result.schema.json`'s
  `change_type` enum only in the same PR that adds the detector

## 2. Schema

The shared envelope stays identical. Extend
`schemas/fixture-case.schema.json` `expected` with detector-specific fields
(price already shows the pattern: `expected.price`).

## 3. Fixtures

Add cases to `scripts/generate_dataset.py`:

- template the page HTML for your detector (like `SAAS_BASE` /
  `PRODUCT_BASE`)
- write explicit case specs with `subtype`, edits, label, rationale
- split: `DEV_CASES` for tuning, held-out lists for evaluation
- every case must record provenance + `provenance_detail` and complete
  labeling fields, and must be static (fixtures are never Jev outputs)

Run the generator:

```sh
python3 scripts/generate_dataset.py
python3 scripts/generate_dataset.py --check
```

## 4. Deterministic baseline

Add a `_judge_<detector>` rule function in
`src/jev_change_monitor/providers/heuristic.py` so the pipeline and CI run
without a model. The baseline is label-blind and never launch evidence.

## 5. Thresholds

Add your detector's gates to `benchmark/thresholds.json` **and** update
`benchmark/thresholds.lock.json` (recompute the SHA-256) in the same commit.
Freeze applies before the first held-out run: `jev-monitor validate --strict`
will fail on any later drift. Explicitly state the threshold source (issue or
accepted spec) in `"source_issues"`.

## 6. Verify before opening a PR

```sh
pip install -e .
python3 scripts/generate_dataset.py --check
jev-monitor validate
jev-monitor benchmark --split held_out --provider heuristic
jev-monitor repro-check --split held_out
bash scripts/secret_scan.sh
git diff --check
```

Minimums are enforced programmatically (>=100 held-out total, >=30 per
detector, >=10 edge cases, no duplicate ids, provenance + label completeness).

## Rules

- No unit, white-box, test-per-function or coverage-only tests — use CLI
  validation, schema checks, the deterministic benchmark and E2E contract
  checks (webhook roundtrip).
- Keep it boring and inspectable.
- Never commit real credentials or env values; `scripts/secret_scan.sh` gates
  this.
- Never tune prompts or thresholds after a held-out evaluation starts; that
  invalidates the run.
- Slack/Telegram, scheduler, tenant auth, billing, outbox/retries and MCP
  orchestration stay in the hosted product and do not belong here.