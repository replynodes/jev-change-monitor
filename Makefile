.PHONY: install check validate benchmark demo webhook-demo repro gate scan redact-check lint

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

benchmark:
	jev-monitor benchmark --split held_out --provider heuristic

benchmark-dev:
	jev-monitor benchmark --split dev --provider heuristic

demo:
	jev-monitor demo

webhook-demo:
	jev-monitor webhook-demo

repro:
	jev-monitor repro-check --split held_out

gate:
	jev-monitor gate --result results/committed/heuristic-baseline/held_out-deterministic.json

scan:
	bash scripts/secret_scan.sh

redact-check:
	jev-monitor redact-check

lint:
	@test -z "$$(python3 -m compileall -q src scripts examples && echo dirty)" || (echo "compile check failed"; exit 1)
	@echo "compileall OK"

# Full local gate suite (no live Jev needed)
all: validate benchmark benchmark-dev repro scan redact-check lint webhook-demo
	@echo "all local gates passed"