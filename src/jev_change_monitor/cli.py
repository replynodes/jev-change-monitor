"""Command-line interface.

Commands
--------
validate     schemas + fixtures + counts + threshold immutability + result artifacts
benchmark    run the detector benchmark for a split with a chosen provider
repro-check  run the deterministic benchmark twice and compare (excluding latency)
gate         evaluate a committed result artifact against the frozen thresholds
redact-check deterministic redaction-integrity + rubric-citation check
demo         run one example case per detector through a provider
webhook-demo fixture sender -> fixture receiver roundtrip with HMAC verification
providers    show which live Jev providers are configured (never values)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jev_change_monitor import __version__
from jev_change_monitor.benchmark import dataset, runner, thresholds as thresholds_mod
from jev_change_monitor.detectors import VALID_DETECTORS, get_detector
from jev_change_monitor.integrity import run_redact_check
from jev_change_monitor.providers import get_provider
from jev_change_monitor.redact import env_flag, redact_value
from jev_change_monitor.schemas import list_schemas, validate_all_schemas, validate_schema
from jev_change_monitor.benchmark.runner import display_path

REPO_ROOT = dataset.REPO_ROOT
RESULTS_COMMITTED = REPO_ROOT / "results" / "committed"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _fail(message: str, code: int = 1) -> int:
    print(f"FAIL: {message}")
    return code


def cmd_validate(args: argparse.Namespace) -> int:
    problems: list[str] = []
    print("== schemas ==")
    for name in list_schemas():
        print(f"  ok  {name}")
    schema_problems = validate_all_schemas()
    problems.extend(f"{name}: {p}" for name, p in schema_problems.items())

    print("== dataset ==")
    for split in ("held_out", "dev"):
        try:
            report = dataset.split_accounting(split)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        print(f"  {split}: {report['cases_total']} cases "
              f"{report['per_detector']} edge={report['edge_cases']} "
              f"provenance={report['provenance']['counts']}")
        print(f"    dataset sha256 {report['sha256']}")
        if report["duplicate_ids"]:
            problems.append(f"{split}: duplicate case_ids {report['duplicate_ids']}")
        if report["provenance"]["missing"]:
            problems.append(f"{split}: {report['provenance']['missing']} cases without provenance")
        if report["label_problems"]:
            problems.extend(f"{split}: {p}" for p in report["label_problems"])
        if report["human_labeled"] is not True:
            print("    note: labels are rubric drafts pending independent human review "
                  "(recorded as a blocked launch reason, not a validation failure)")
        if split == "held_out":
            for key, ok in report.get("minimums", {}).items():
                print(f"    minimum {key}: {'ok' if ok else 'FAIL'}")
            if not report.get("minimums_ok", False):
                problems.append("held_out minimums not met")

    print("== fixtures validate against fixture-case schema ==")
    for split in ("held_out", "dev"):
        cases = dataset.load_cases(split, validate=True)
        print(f"  {split}: {len(cases)} cases schema-valid")

    print("== constructed requests and change events conform to committed schemas ==")
    from jev_change_monitor.events import build_change_event

    heuristic_provider = get_provider("heuristic")
    constructed = {"requests": 0, "events": 0}
    for split in ("held_out", "dev"):
        for case in dataset.load_cases(split, validate=False):
            request = runner._request_for_case(case)  # noqa: SLF001 - shared request builder
            request_errors = validate_schema(request, "detector-request")
            if request_errors:
                problems.append(f"{split}/{case['case_id']}: detector request "
                                f"schema-invalid: {'; '.join(request_errors)}")
            constructed["requests"] += 1
            response = heuristic_provider.judge(get_detector(case["detector"]), request)
            if response.result is not None and response.schema_valid:
                event = build_change_event(response.result, request,
                                           {"case_id": case["case_id"]})
                event_errors = validate_schema(event, "change-event")
                if event_errors:
                    problems.append(f"{split}/{case['case_id']}: change event "
                                    f"schema-invalid: {'; '.join(event_errors)}")
                constructed["events"] += 1
    print(f"  requests checked: {constructed['requests']}, events checked: "
          f"{constructed['events']}")
    if constructed["events"] == 0:
        problems.append("no change events could be constructed for schema checks")

    print("== split separation (held_out vs dev content, #487) ==")
    try:
        overlaps = dataset.cross_split_content_overlap()
    except ValueError as exc:
        problems.append(str(exc))
        overlaps = []
    if overlaps:
        print(f"  FAIL {len(overlaps)} shared content hash(es) across splits:")
        for item in overlaps:
            print(f"    {item['kind']}: {item['sha256'][:16]} held_out={item['held_out']} "
                  f"dev={item['dev']}")
        problems.append(
            f"cross-split content overlap: {len(overlaps)} shared hash(es); "
            "dev/tuning and held-out must not share before/after page content (#487)"
        )
    else:
        print("  ok  zero shared before/after content hashes across held_out and dev "
              f"({'/'.join(dataset.CONTENT_HASH_KINDS)})")
    for split in ("held_out", "dev"):
        groups = dataset.duplicate_snapshot_groups(split)
        print(f"  note {split}: {groups} hash(es) shared between cases within the split "
              "(allowed — same page observed at different times; not cross-split leakage)")

    print("== thresholds ==")
    ok, reason = thresholds_mod.check_frozen()
    print(f"  frozen: {'ok' if ok else 'FAIL'} ({reason})")
    if not ok:
        problems.append(reason)
    cost_model = thresholds_mod.load_cost_model()
    print(f"  cost model configured: {cost_model.get('configured')} "
          f"(source: {cost_model.get('source')})")

    print("== committed result artifacts ==")
    artifacts = sorted(RESULTS_COMMITTED.rglob("*.json"))
    if not artifacts:
        problems.append("no committed result artifacts found under results/committed/")
    for path in artifacts:
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_schema(payload, "benchmark-result")
        status = "ok" if not errors else "FAIL"
        print(f"  {status} {path.relative_to(REPO_ROOT)} "
              f"({payload.get('evaluation_kind')}, launch={payload.get('launch_claim', {}).get('status')})")
        problems.extend(f"{path.name}: {e}" for e in errors)
        if payload.get("artifact_sha256"):
            recomputed = runner._self_hash(payload)  # noqa: SLF001 - artifact integrity check
            if recomputed != payload["artifact_sha256"]:
                problems.append(f"{path.name}: artifact self-hash mismatch")

    print("== secret hygiene ==")
    print(f"  JEV_ENDPOINT {env_flag('JEV_ENDPOINT')}, JEV_API_KEY {env_flag('JEV_API_KEY')}, "
          f"JEV_COMMAND {env_flag('JEV_COMMAND')}, JEV_PROTOCOL {env_flag('JEV_PROTOCOL')}")

    print("== redaction integrity (hashes survive, secrets redacted) ==")
    integrity_problems = run_redact_check()
    for problem in integrity_problems:
        print(f"  FAIL {problem}")
    problems.extend(f"redact-check: {p}" for p in integrity_problems)
    if not integrity_problems:
        print("  ok  hash fields byte-exact; full secret tokens absent (safe prefix + "
              "[REDACTED] only); rubric paths resolve; JEV_COMMAND values and JEV_HTTP / "
              "JEV_EVALUATE endpoint detail never persist; price-extraction mapping, "
              "socket/read-timeout classification, retry-semantics and jev-http "
              "protocol-refusal probes pass")

    if problems:
        print("\n".join(f"  - {p}" for p in problems))
        return _fail(f"{len(problems)} validation problem(s)")
    print("validate: PASS")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    provider = get_provider(args.provider)
    if args.provider in ("jev-http", "jev-command", "jev-evaluate") and not getattr(provider, "configured", False):
        print(f"BLOCKED: provider {args.provider} is not configured in this environment.")
        print("No live Jev credential/runtime is available, so semantic thresholds cannot be "
              "evaluated. Recording a machine-readable BLOCKED result instead of fabricating "
              "live numbers.")
        print("(provider jev-evaluate requires JEV_ENDPOINT + JEV_API_KEY with "
              "JEV_PROTOCOL unset or 'evaluate'; jev-http is the chat-completions protocol.)")
        if not args.allow_unconfigured:
            return 2
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO_ROOT / out
    else:
        name = f"{args.split}-{('live-jev' if provider.mode == 'live' else 'deterministic')}"
        if args.fault_injection_rate:
            name += "-fault-injection"
        out = RESULTS_COMMITTED / provider.name / f"{name}.json"
    try:
        artifact = runner.run(
            split=args.split,
            provider_name=provider.name,
            provider=provider,
            out_path=out,
            fault_injection_rate=args.fault_injection_rate,
            command=" ".join(["jev-monitor", "benchmark", *sys.argv[2:]]),
        )
    except OSError as exc:
        return _fail(f"cannot write {display_path(out)}: {exc}")
    print(json.dumps({
        "evaluation_kind": artifact["evaluation_kind"],
        "evaluation_kind_note": {
            "synthetic-deterministic": "synthetic-deterministic runs use the local rule "
                                       "baseline and are NOT Jev results",
            "live-jev": "live Jev provider",
            "live-jev-blocked": "blocked: no authorized live Jev runtime was available; "
                                "zero metrics recorded",
        }.get(artifact["evaluation_kind"], artifact["evaluation_kind"]),
        "provider": artifact["provider"]["provider"],
        "cases": artifact["dataset"]["cases_total"],
        "per_detector": artifact["dataset"]["per_detector"],
        "metrics": {
            d: {
                "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
                "false_alert_rate": m["false_alert_rate"],
                "false_negative_rate": m["false_negative_rate"],
                "false_change_rate": m["false_change_rate"],
                "exact_price_accuracy": m.get("exact_price_accuracy"),
                "schema_invalid_rate": m["schema_invalid_rate"],
                "provider_error_rate": m["provider_error_rate"],
            }
            for d, m in artifact["metrics"]["per_detector"].items()
        },
        "failed_threshold_checks": [t for t in artifact["threshold_evaluation"] if not t["passed"]],
        "launch_claim": artifact["launch_claim"],
    }, indent=2, sort_keys=True))
    return 0


def cmd_repro_check(args: argparse.Namespace) -> int:
    provider = get_provider("heuristic")
    first = runner.run(split=args.split, provider_name=provider.name, provider=provider,
                       command="jev-monitor repro-check (run 1)", quiet=True)
    second = runner.run(split=args.split, provider_name=provider.name, provider=provider,
                        command="jev-monitor repro-check (run 2)", quiet=True)
    equal, problems = runner.artifacts_equal(first, second)
    print(f"repro-check ({args.split}, {provider.name}): {'PASS' if equal else 'FAIL'}")
    for problem in problems:
        print(f"  - {problem}")
    committed = RESULTS_COMMITTED / provider.name / f"{args.split}-deterministic.json"
    if committed.exists():
        stored = json.loads(committed.read_text(encoding="utf-8"))
        same_as_committed, diffs = runner.artifacts_equal(stored, first)
        print(f"matches committed artifact {display_path(committed)}: "
              f"{'PASS' if same_as_committed else 'FAIL'}")
        for d in diffs:
            print(f"  - {d}")
        equal = equal and same_as_committed
    else:
        print(f"note: no committed artifact at {committed.relative_to(REPO_ROOT)}; "
              "run `jev-monitor benchmark` to create it")
    return 0 if equal else 1


def cmd_gate(args: argparse.Namespace) -> int:
    """Honest gate: PASS requires status=passed, empty reasons AND labels done.

    A blocked launch claim, a non-empty `launch_claim.reasons` list, or
    `dataset.human_labeled != true` (labels still pending independent review)
    always refuses PASS — the gate never reports green while a launch blocker
    is recorded (#487). Deterministic over the artifact.
    """
    path = Path(args.result)
    if not path.is_absolute():
        path = REPO_ROOT / path
    artifact = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_schema(artifact, "benchmark-result")
    if errors:
        return _fail(f"result artifact invalid: {'; '.join(errors)}")
    print(f"artifact: {display_path(path)}")
    print(f"provider: {artifact['provider']['provider']} ({artifact['evaluation_kind']})")
    human_labeled = artifact.get("dataset", {}).get("human_labeled") is True
    print(f"label review: {('independent-review-complete' if human_labeled else 'pending-independent-review')}")
    print(f"launch claim: {artifact['launch_claim']['status']}")
    for reason in artifact["launch_claim"]["reasons"]:
        print(f"  reason: {reason}")
    failed = [t for t in artifact["threshold_evaluation"] if not t["passed"]]
    for check in artifact["threshold_evaluation"]:
        observed = check["observed"]
        observed = "n/a" if observed is None else f"{observed:.4f}"
        bound = f">= {check['min']}" if check["min"] is not None else f"<= {check['max']}"
        print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['detector']}.{check['metric']} "
              f"{observed} ({bound})")
    claim_status = artifact["launch_claim"]["status"]
    if claim_status == "failed" and failed:
        return _fail(f"{len(failed)} threshold check(s) failed")
    if claim_status != "passed" or not human_labeled or artifact["launch_claim"]["reasons"]:
        print("gate: BLOCKED — launch thresholds unverified (live Jev absent, labels pending "
              "independent review, or a launch blocker is recorded)")
        return 2
    if failed:
        return _fail(f"{len(failed)} threshold check(s) failed")
    print("gate: PASS")
    return 0


def cmd_redact_check(args: argparse.Namespace) -> int:
    """Deterministic redaction-integrity + rubric-citation check."""
    problems = run_redact_check()
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return _fail(f"{len(problems)} integrity problem(s)")
    print("redact-check: PASS (hash fields byte-exact; full secret tokens absent — safe "
          "prefix + [REDACTED] only; rubric paths resolve; JEV_COMMAND values and "
          "JEV_HTTP / JEV_EVALUATE endpoint detail never persist; price-extraction "
          "mapping, socket/read-timeout classification, retry-semantics and jev-http "
          "protocol-refusal probes pass)")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    provider = get_provider(args.provider)
    detectors = [args.detector] if args.detector else list(VALID_DETECTORS)
    for detector in detectors:
        case_path = EXAMPLES_DIR / detector / "case.json"
        case = json.loads(case_path.read_text(encoding="utf-8"))
        errors = validate_schema(case, "fixture-case")
        if errors:
            return _fail(f"{case_path}: {'; '.join(errors)}")
        request = runner._request_for_case(case)  # noqa: SLF001 - shared request builder
        response = provider.judge(get_detector(detector), request)
        from jev_change_monitor.events import build_change_event

        result = response.result or {}
        print(f"== {detector} ({case_path.relative_to(REPO_ROOT)}) ==")
        print(json.dumps({"label": case["label"], "expected": case.get("expected"),
                          "provider": provider.describe(),
                          "result": result}, indent=2, sort_keys=True))
        if result and response.schema_valid:
            event = build_change_event(result, request, {"case_id": case["case_id"]})
            event_errors = validate_schema(event, "change-event")
            if event_errors:
                return _fail(f"{case_path}: change event schema-invalid: "
                             f"{'; '.join(event_errors)}")
            print("canonical event:")
            print(json.dumps(event, indent=2, sort_keys=True))
    return 0


def cmd_webhook_demo(args: argparse.Namespace) -> int:
    """Real fixture sender -> fixture receiver roundtrip, plus a tamper check."""
    import http.server
    import threading
    import urllib.error
    import urllib.request

    from jev_change_monitor import webhook
    from jev_change_monitor.events import build_change_event

    case = json.loads((EXAMPLES_DIR / "price" / "case.json").read_text(encoding="utf-8"))
    provider = get_provider("heuristic")
    request = runner._request_for_case(case)
    response = provider.judge(get_detector("price"), request)
    event = build_change_event(response.result or {}, request, {"case_id": case["case_id"]})
    event_errors = validate_schema(event, "change-event")
    if event_errors:
        return _fail(f"change event schema-invalid: {'; '.join(event_errors)}")
    secret = webhook.get_webhook_secret()
    headers, raw_body = webhook.build_signed_request(event, secret)

    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = webhook.verify_request(
                {k: v for k, v in self.headers.items()},
                body,
                webhook.get_webhook_secret(),
                tolerance_seconds=args.tolerance,
            )
            received["ok"] = payload.ok
            received["reason"] = payload.reason
            received["event_id"] = (payload.event or {}).get("event_id")
            received["event"] = payload.event
            self.send_response(200 if payload.ok else 401)
            self.end_headers()
            self.wfile.write(b'{"received":true}')

        def log_message(self, *a):  # silence the default stderr access log
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    req = urllib.request.Request(f"http://127.0.0.1:{port}/webhook", data=raw_body,
                                headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"fixture sender -> fixture receiver: HTTP {resp.status}")

    print(f"headers sent: {sorted(headers)}")
    print(f"signed message: 'timestamp + \".\" + raw_body' ({len(raw_body)} body bytes)")
    print(f"receiver verdict: ok={received.get('ok')} reason={received.get('reason')} "
          f"event_id={received.get('event_id')}")

    # tamper + replay checks exercise the receiver contract for real
    tampered_headers, _ = webhook.build_signed_request(event, secret)
    tampered = webhook.verify_request(tampered_headers, raw_body + b" ", secret, args.tolerance)
    stale_headers, _ = webhook.build_signed_request(event, secret, timestamp="1000")
    stale = webhook.verify_request(stale_headers, raw_body, secret, args.tolerance)
    print(f"tampered body verdict: ok={tampered.ok} reason={tampered.reason}")
    print(f"stale timestamp verdict: ok={stale.ok} reason={stale.reason}")
    received_event = received.get("event")
    received_errors = (validate_schema(received_event, "change-event")
                       if isinstance(received_event, dict)
                       else ["no canonical event received"])
    print(f"received event schema: {'ok' if not received_errors else '; '.join(received_errors)}")
    server.server_close()
    ok = (received.get("ok") is True and not tampered.ok and not stale.ok
          and not received_errors)
    print("webhook-demo: PASS" if ok else "webhook-demo: FAIL")
    return 0 if ok else 1


def cmd_providers(args: argparse.Namespace) -> int:
    from jev_change_monitor.providers.jev_command import JevCommandProvider
    from jev_change_monitor.providers.jev_evaluate import JevEvaluateProvider
    from jev_change_monitor.providers.jev_http import JevHttpProvider

    listed = [JevHttpProvider(), JevEvaluateProvider(), JevCommandProvider()]
    for provider in listed:
        describe = provider.describe()
        print(json.dumps({"name": provider.name, "configured": provider.configured,
                          "config": describe}, indent=2, sort_keys=True))
    if not any(p.configured for p in listed):
        print("No live Jev provider is configured. Semantic thresholds are BLOCKED; only "
              "deterministic schema/fixture validation can run.")
    return 0


def cmd_blocked_live(args: argparse.Namespace) -> int:
    """Write a machine-readable BLOCKED artifact for the live Jev path.

    Used when no authorized Jev credential/runtime is available: the semantic
    launch thresholds cannot be evaluated, so the artifact records `blocked`
    with explicit reasons instead of fabricated live numbers.
    """
    import platform
    import sys
    from datetime import datetime, timezone

    from jev_change_monitor import __version__
    from jev_change_monitor.providers.jev_command import JevCommandProvider
    from jev_change_monitor.providers.jev_evaluate import JevEvaluateProvider
    from jev_change_monitor.providers.jev_http import JevHttpProvider

    accounting = dataset.split_accounting("held_out")
    frozen_ok, frozen_reason = thresholds_mod.check_frozen()
    providers = [JevHttpProvider(), JevEvaluateProvider(), JevCommandProvider()]
    reasons = [
        "no authorized live Jev runtime configured (JEV_ENDPOINT/JEV_API_KEY/JEV_COMMAND/JEV_PROTOCOL "
        "all unset); semantic thresholds (replynodes/replynodes-fetcher#487) are unverified",
        "deterministic-baseline numbers are pipeline evidence only and must not be presented as "
        "launch evidence",
        "held-out labels are rubric drafts with review_status 'pending-independent-review'; "
        "independent human review/adjudication of disputed cases has not been recorded",
    ]
    if not frozen_ok:
        reasons.append(f"threshold immutability check failed: {frozen_reason}")
    thresholds = thresholds_mod.load_thresholds()
    threshold_evaluation = []
    for detector, spec in thresholds["detectors"].items():
        for metric, rule in spec["metrics"].items():
            threshold_evaluation.append({
                "detector": detector, "metric": metric,
                "min": rule.get("min"), "max": rule.get("max"),
                "observed": None, "passed": False,
            })
    zero = {"cases": 0, "evaluable_cases": 0, "precision": None, "recall": None, "f1": None,
            "false_alert_rate": None, "false_negative_rate": None, "false_change_rate": None,
            "schema_invalid_rate": None, "provider_error_rate": None}
    artifact = {
        "artifact_version": "1.0.0",
        "run_id": f"blocked-{thresholds_mod.thresholds_sha256()[:12]}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "jev-change-monitor", "version": __version__},
        "provider": {
            "provider": "jev-unavailable",
            "mode": "live",
            "model": None,
            "endpoint_configured": providers[0].describe()["endpoint_configured"],
            "api_key_configured": providers[0].describe()["api_key_configured"],
            "command_configured": providers[2].describe()["command_configured"],
            "evaluate_protocol_configured": providers[1].describe()["protocol_env"],
            "notes": "No authorized Jev runtime was available when this artifact was written.",
        },
        "evaluation_kind": "live-jev-blocked",
        "command": " ".join(["jev-monitor", "blocked-live", *sys.argv[2:]]),
        "dataset": {
            "split": "held_out",
            "path": accounting["path"],
            "sha256": accounting["sha256"],
            "cases_total": accounting["cases_total"],
            "per_detector": accounting["per_detector"],
            "edge_cases": accounting["edge_cases"],
            "duplicate_ids": accounting["duplicate_ids"],
            "human_labeled": accounting["human_labeled"],
            "minimums_ok": accounting.get("minimums_ok", True),
        },
        "thresholds": {
            "path": "benchmark/thresholds.json",
            "sha256": thresholds_mod.thresholds_sha256(),
            "frozen_lock_ok": frozen_ok,
            "frozen_lock_note": frozen_reason,
            "source_issues": thresholds.get("source_issues", []),
        },
        "cost_model": {"path": "benchmark/cost-model.json",
                       "configured": False,
                       "source": "placeholder rates are not real Jev pricing"},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "config": {"fault_injection_rate": 0.0, "faults_injected": 0,
                   "config_sha256": None},
        "metrics": {
            "overall": {**zero, "note": "no live provider configured; zero metrics recorded, "
                                        "semantic thresholds unverified"},
            "per_detector": {d: {**zero, "note": "unverified"} for d in dataset.DETECTORS},
        },
        "threshold_evaluation": threshold_evaluation,
        "launch_claim": {
            "status": "blocked",
            "reasons": reasons,
            "failed_checks": len(threshold_evaluation),
            "criteria_source": "replynodes/replynodes-fetcher#487",
        },
        "cases": [],
        "artifact_sha256": None,
    }
    artifact = redact_value(artifact)
    artifact["artifact_sha256"] = runner._self_hash(artifact)
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    committed_root = RESULTS_COMMITTED
    is_committed_target = out == committed_root or committed_root in out.parents
    if is_committed_target and not args.committed:
        return _fail(
            f"refusing to overwrite committed artifact {display_path(out)}; "
            "run-local output defaults to the gitignored "
            "results/runs/live-jev-blocked.json — pass --committed with an "
            "explicit --out under results/committed/ only to deliberately "
            "regenerate a committed BLOCKED artifact"
        )
    if args.committed and not is_committed_target:
        return _fail(
            "--committed requires an explicit --out under results/committed/ "
            f"(got {display_path(out)})"
        )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        runner._write_json(out, artifact)  # noqa: SLF001 - shared writer
    except OSError as exc:
        return _fail(f"cannot write {display_path(out)}: {exc}")
    print(f"wrote {display_path(out)}")
    print(f"launch claim: {artifact['launch_claim']['status']}")
    for reason in reasons:
        print(f"  blocked: {reason}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev-monitor",
                                     description="Typed website-change events and detector benchmarks.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate schemas, fixtures, counts and thresholds")
    p_validate.set_defaults(func=cmd_validate)

    p_bench = sub.add_parser("benchmark", help="run the detector benchmark")
    p_bench.add_argument("--split", choices=["held_out", "dev"], default="held_out")
    p_bench.add_argument("--provider", choices=["heuristic", "jev-command", "jev-http",
                                                "jev-evaluate"],
                         default="heuristic")
    p_bench.add_argument("--out", default=None)
    p_bench.add_argument("--fault-injection-rate", type=float, default=0.0,
                         help="inject labeled provider faults at this rate to exercise error metrics")
    p_bench.add_argument("--allow-unconfigured", action="store_true",
                         help="still run and record provider errors when a live provider is unset")
    p_bench.set_defaults(func=cmd_benchmark)

    p_repro = sub.add_parser("repro-check", help="re-run the deterministic benchmark and compare")
    p_repro.add_argument("--split", choices=["held_out", "dev"], default="held_out")
    p_repro.set_defaults(func=cmd_repro_check)

    p_gate = sub.add_parser("gate", help="evaluate a result artifact against frozen thresholds")
    p_gate.add_argument("--result", required=True)
    p_gate.set_defaults(func=cmd_gate)

    p_redact = sub.add_parser(
        "redact-check",
        help="deterministic redaction-integrity + rubric-citation check",
    )
    p_redact.set_defaults(func=cmd_redact_check)

    p_demo = sub.add_parser("demo", help="run an example case through a provider")
    p_demo.add_argument("--detector", choices=list(VALID_DETECTORS), default=None)
    p_demo.add_argument("--provider", choices=["heuristic", "jev-command", "jev-http",
                                               "jev-evaluate"],
                        default="heuristic")
    p_demo.set_defaults(func=cmd_demo)

    p_webhook = sub.add_parser("webhook-demo", help="fixture sender/receiver HMAC roundtrip")
    p_webhook.add_argument("--tolerance", type=int, default=300)
    p_webhook.set_defaults(func=cmd_webhook_demo)

    p_providers = sub.add_parser("providers", help="show configured live providers (no values)")
    p_providers.set_defaults(func=cmd_providers)

    p_blocked = sub.add_parser(
        "blocked-live",
        help="write a machine-readable BLOCKED artifact for the live Jev path "
             "(safe default: gitignored results/runs/; committed copy requires --committed)",
    )
    p_blocked.add_argument(
        "--out",
        default="results/runs/live-jev-blocked.json",
        help=("output path; the safe default is the gitignored run-local copy "
              "results/runs/live-jev-blocked.json; writing under "
              "results/committed/ requires --committed"),
    )
    p_blocked.add_argument(
        "--committed",
        action="store_true",
        help="deliberately regenerate the committed BLOCKED artifact "
             "(requires an explicit --out under results/committed/)",
    )
    p_blocked.set_defaults(func=cmd_blocked_live)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())