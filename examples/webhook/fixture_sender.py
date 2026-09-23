"""Fixture webhook sender (HMAC-SHA256).

Usage (fixture ONLY — the secret below is a labeled fixture, not a secret):

    python3 examples/webhook/fixture_sender.py \
        --url http://127.0.0.1:8765/webhook \
        --event examples/price/case.json \
        --secret "fixture:recipe-demo-change-me-2026"

Signs `timestamp + "." + raw_body` with HMAC-SHA256 and sends the canonical
headers:

    X-ReplyNodes-Event-Id
    X-ReplyNodes-Timestamp
    X-ReplyNodes-Signature

The secret value is never printed or logged. Production secrets come from a
secrets manager and never from this repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jev_change_monitor import webhook
from jev_change_monitor.events import build_change_event


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixture webhook sender (HMAC-SHA256).")
    parser.add_argument("--url", required=True, help="receiver URL")
    parser.add_argument("--event", default="examples/price/case.json",
                        help="fixture case (uses a heuristic judgement to build the event)")
    parser.add_argument("--secret", default=webhook.FIXTURE_SECRET,
                        help="fixture secret; default is the labeled fixture value")
    args = parser.parse_args()

    case = json.loads(Path(args.event).read_text(encoding="utf-8"))
    from jev_change_monitor.benchmark.runner import _request_for_case
    from jev_change_monitor.detectors import get_detector
    from jev_change_monitor.providers import get_provider

    response = get_provider("heuristic").judge(get_detector(case["detector"]),
                                                _request_for_case(case))
    if not response.result:
        print("no judgement produced; cannot build event", file=sys.stderr)
        return 1
    event = build_change_event(response.result, _request_for_case(case), {"case_id": case["case_id"]})
    headers, raw_body = webhook.build_signed_request(event, args.secret.encode("utf-8"))
    req = urllib.request.Request(args.url, data=raw_body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"delivered event_id={event['event_id']} -> HTTP {resp.status}")
            print(f"headers: {sorted(headers)}")
            print(f"signed content: timestamp + '.' + raw_body ({len(raw_body)} bytes)")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"receiver rejected: HTTP {exc.code}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"delivery failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())