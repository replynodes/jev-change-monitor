"""jev-change-monitor: typed website-change events + reproducible detector benchmarks.

Boundary: this package is the OSS companion for the ReplyNodes hosted monitor.
It implements detector contracts, normalization, canonical events, a webhook
HMAC example, fixtures and a benchmark runner. It does NOT implement the hosted
scheduler, tenant auth, billing, production outbox/retries or MCP orchestration.
"""

__version__ = "0.1.0"

SCHEMA_VERSION = "1.0"