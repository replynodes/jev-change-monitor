"""Fixture webhook receiver (HMAC-SHA256 verification).

Usage (fixture ONLY):

    python3 examples/webhook/fixture_receiver.py --port 8765 \
        --secret "fixture:recipe-demo-change-me-2026" --tolerance 300

Verifies every POST against:
  - HMAC-SHA256 over `timestamp + "." + raw_body`
  - replay tolerance: |now - X-ReplyNodes-Timestamp| <= tolerance seconds
  - header/body `event_id` consistency
  - constant-time signature comparison

HTTP 200 only when all checks pass, otherwise HTTP 401 with the reason in
`X-ReplyNodes-Reason`. This is a static fixture; production receivers keep
secrets in a secrets manager and add delivery idempotency via event_id.
"""

from __future__ import annotations

import argparse
import http.server
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jev_change_monitor import webhook


def serve(port: int, secret: str, tolerance: int) -> None:
    secret_bytes = secret.encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = webhook.verify_request(
                {k: v for k, v in self.headers.items()}, body, secret_bytes,
                tolerance_seconds=tolerance,
            )
            status = 200 if payload.ok else 401
            self.send_response(status)
            self.send_header("X-ReplyNodes-Reason", payload.reason.encode("ascii", "replace"))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"verified":' + (b"true" if payload.ok else b"false") + b"}")
            event_id = webhook._header(payload.headers, webhook.X_EVENT_ID)  # noqa: SLF001
            print(f"event_id={event_id} ok={payload.ok} reason={payload.reason}")

        def log_message(self, *args):  # silence stderr access logs
            return

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"fixture receiver listening on 127.0.0.1:{port} "
          f"(tolerance {tolerance}s, secret={'<set>' if secret else 'unset'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixture webhook receiver.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--secret", default=webhook.FIXTURE_SECRET)
    parser.add_argument("--tolerance", type=int, default=webhook.DEFAULT_TOLERANCE_SECONDS)
    args = parser.parse_args()
    serve(args.port, args.secret, args.tolerance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())