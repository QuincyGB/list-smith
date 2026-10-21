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

    own_client = client is None
    http = client or httpx.AsyncClient(follow_redirects=True)
    try:
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
    finally:
        if own_client:
            await http.aclose()
