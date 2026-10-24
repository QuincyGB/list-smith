"""Target inspection — observation only, NEVER pays the target.

One unpaid probe (GET, then POST {} if the first answer was not a 402)
plus challenge parsing (parsers.py) is everything ListSmith knows about
a target. Both paid routes build on this: /v1/grade itemizes what the
inspection finds broken; /v1/fix generates a repaired discovery package
from it. We never attach a payment header to a target request, so the
target can never charge us.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from .parsers import format_price, parse_challenge

USER_AGENT = "list-smith/1.0 (x402 listing grader)"


_shared: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    """One process-wide client for target probes. Building a fresh
    AsyncClient per probe (the original shape) threw away the
    connection pool every call, so each grade/fix paid a new
    DNS + TCP + TLS handshake to the target host. The shared client
    keeps connections alive between calls. Timeouts are unchanged:
    _probe passes its explicit per-request timeout on every call.
    Callers must NOT close it. (Same fix as token-verdict's shared
    DEXScreener client, ff05d1c.)"""
    global _shared
    if _shared is None or _shared.is_closed:
        _shared = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=90.0,
            ),
        )
    return _shared


async def _probe(client: httpx.AsyncClient, url: str, timeout: float) -> dict:
    """Unauthenticated probe. Tries GET, then POST {} — whichever answer
    carries a 402 is the one we parse."""
    headers = {"user-agent": USER_AGENT}
    error: str | None = None
    chosen = None
    method = "GET"
    latency_ms: int | None = None
    try:
        start = time.monotonic()
        resp = await client.get(url, headers=headers, timeout=timeout)
        latency_ms = int((time.monotonic() - start) * 1000)
        chosen = resp
        if resp.status_code != 402:
            start = time.monotonic()
            resp2 = await client.post(
                url, json={}, headers={**headers, "content-type": "application/json"},
                timeout=timeout,
            )
            latency2 = int((time.monotonic() - start) * 1000)
            if resp2.status_code == 402:
                chosen = resp2
                method = "POST"
                latency_ms = latency2
    except httpx.HTTPError as e:
        error = str(e)
    return {
        "alive": chosen is not None,
        "status": chosen.status_code if chosen is not None else None,
        "headers": dict(chosen.headers) if chosen is not None else {},
        "body": chosen.content if chosen is not None else b"",
        "method": method,
        "latency_ms": latency_ms,
        "error": error,
    }


async def inspect_target(
    resource_url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> dict:
    """Probe + parse a target. Returns probe facts, the parsed challenge,
    the primary (Base-preferred) accept, and formatted terms."""
    parsed = urlparse(resource_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("resource_url must be an http(s) URL")

    # Default to the shared process-wide client; a caller-injected
    # client (tests) is used as-is. Either way the client outlives this
    # call — the old per-request client was closed in a finally block,
    # which is precisely the pool-churn this fix removes.
    http = client if client is not None else _shared_client()
    probe = await _probe(http, resource_url, timeout)
    challenge = parse_challenge(probe["status"] or 0, probe["headers"], probe["body"])
    primary = challenge.primary_accept() if challenge.valid else None
    terms = {
        "found": bool(primary),
        "price_usdc": format_price(
            primary.amount if primary else None,
            primary.network if primary else None,
            primary.asset if primary else None,
        ),
        "amount_units": primary.amount if primary else None,
        "network": primary.network if primary else None,
        "asset": primary.asset if primary else None,
        "pay_to": primary.pay_to if primary else None,
        "scheme": primary.scheme if primary else None,
        "x402_version": challenge.x402_version,
    }
    return {
        "resource_url": resource_url,
        "probe": probe,
        "challenge": challenge,
        "primary": primary,
        "terms": terms,
    }
