.PHONY: install check validate benchmark benchmark-dev benchmark-fault-injection demo webhook-demo repro gate scan redact-check lint

install:
	pip install -e .

# Regenerate the committed fixtures (deterministic) and verify equality
generate:
	python3 scripts/generate_dataset.py
	python3 scripts/generate_dataset.py --check

check:
	python3 scripts/generate_dataset.py --check

validate: check
	jev-monitor validate

# Run-local output goes to the gitignored results/runs/ dir. Committed
# artifacts under results/committed/ are only ever read (or gated) here —
# never overwritten by make targets, so `git status` stays clean.
benchmark:
	jev-monitor benchmark --split held_out --provider heuristic --out results/runs/held_out-deterministic.json

benchmark-dev:
	jev-monitor benchmark --split dev --provider heuristic --out results/runs/dev-deterministic.json

benchmark-fault-injection:
	jev-monitor benchmark --split held_out --provider heuristic --fault-injection-rate 0.09 --out results/runs/held_out-deterministic-fault-injection.json

demo:
	jev-monitor demo

webhook-demo:
	jev-monitor webhook-demo

repro:
	jev-monitor repro-check --split held_out

# Read-only: evaluates the committed artifact against frozen thresholds.
gate:
	jev-monitor gate --result results/committed/heuristic-baseline/held_out-deterministic.json

scan:
	bash scripts/secret_scan.sh

redact-check:
	jev-monitor redact-check

lint:
	@python3 -m compileall -q src scripts examples || { echo "compile check failed"; exit 1; }
	@echo "compileall OK"

# Full local gate suite (no live Jev needed)
all: validate benchmark benchmark-dev benchmark-fault-injection repro scan redact-check lint webhook-demo
	@echo "all local gates passed"
