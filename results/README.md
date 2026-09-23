# results/

Reproducible benchmark artifacts. Every file under `results/committed/` is
committed and validated against
`schemas/benchmark-result.schema.json` by `jev-monitor validate`.

| Artifact | What it is | Evaluation kind |
| --- | --- | --- |
| `heuristic-baseline/held_out-deterministic.json` | held-out run with the local rule baseline, 111 cases | `synthetic-deterministic` |
| `heuristic-baseline/dev-deterministic.json` | dev/tuning split run, 15 cases | `synthetic-deterministic` |
| `heuristic-baseline/held_out-deterministic-fault-injection.json` | held-out run with labeled fault injection (9%) to exercise error-rate metrics only | `synthetic-deterministic` |
| `blocked/live-jev-blocked.json` | machine-readable BLOCKED result for the live Jev path | `live-jev` (blocked) |

## Reproducing

```sh
pip install -e .
python3 scripts/generate_dataset.py --check          # fixtures match the generator
jev-monitor validate                                 # schemas, counts, thresholds, artifacts
# Run-local output goes to the gitignored results/runs/ dir: every command in
# this block writes there, so reproducing never rewrites the committed
# artifacts under results/committed/. Those are only replaced by a deliberate
# targeted regeneration (a benchmark or `blocked-live` run without --out) in
# the change that owns them.
jev-monitor benchmark --split held_out --provider heuristic --out results/runs/held_out-deterministic.json
jev-monitor benchmark --split dev --provider heuristic --out results/runs/dev-deterministic.json
jev-monitor benchmark --split held_out --provider heuristic --fault-injection-rate 0.09 --out results/runs/held_out-deterministic-fault-injection.json
jev-monitor blocked-live --out results/runs/live-jev-blocked.json
jev-monitor repro-check --split held_out             # deterministic metrics are byte-stable
```

Artifacts exclude volatile fields (latency, timestamps, run environment) when
compared; see `runner.VOLATILE_KEYS` and `runner.comparable_view`.

Reproducing inside Docker never touches the committed artifacts: `docker
compose run --rm benchmark` writes run-local output to the ephemeral
`benchmark-runs` named volume at `/app/results/runs` (see docker-compose.yml),
and `docker compose run --rm validate` is read-only.

## Launch claim

`live-jev-blocked.json` records `launch_claim.status = "blocked"`. The
semantic thresholds from #487 are **unverified**: no authorized Jev runtime was
available, held-out labels are still pending independent human review, and the
deterministic baseline is never launch evidence. The deterministic numbers are
pipeline evidence only and must not be presented as passing the launch gates.

Run-local output not meant for commit lands in `results/runs/` (gitignored).