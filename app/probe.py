"""Target inspection — observation only, NEVER pays the target.

One unpaid probe (GET, then POST {} if the first answer was not a 402)
plus challenge parsing (parsers.py) is everything ListSmith knows about
a target. Both paid routes build on this: /v1/grade itemizes what the
inspection finds broken; /v1/fix generates a repaired discovery package
from it. We never attach a payment header to a target request, so the
target can never charge us.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from .parsers import format_price, parse_challenge

USER_AGENT = "list-smith/1.0 (x402 listing grader)"

# --- SSRF guard + probe fetch hardening (security audit 2026-10-25) -----------
# The probe fetches a caller-supplied URL from inside our network, so it
# follows the same discipline as StallRow's page fetcher: only http(s),
# the host must resolve exclusively to public addresses, every redirect
# hop is re-checked BEFORE it is fetched (client-level redirect
# following would contact the target first), and the body is read under
# a hard size cap (a 402 challenge is a few KB; an uncapped read lets a
# hostile target OOM the service with one giant response).
_MAX_PROBE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5


def ssrf_check(url: str) -> None:
    """Reject non-http(s) URLs and any host resolving to a private,
    loopback, link-local, reserved, or multicast address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"host does not resolve: {host}") from e
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"URL host resolves to a non-public address ({ip})")


class _ProbeRefused(Exception):
    """The probe refused the target (SSRF guard, redirect overflow, or
    an oversized body). Surfaced as a probe error, never a fetch."""


async def _read_capped(resp: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > _MAX_PROBE_BYTES:
            raise _ProbeRefused(
                f"target body exceeds the {_MAX_PROBE_BYTES // (1024 * 1024)} MiB probe cap"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_once(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    timeout: float,
    content: bytes | None = None,
) -> tuple[int, httpx.Headers, bytes]:
    """One probe request with manual redirect following — every hop is
    SSRF-checked before it is fetched."""
    current = url
    for hop in range(_MAX_REDIRECTS + 1):
        await asyncio.to_thread(ssrf_check, current)  # blocking DNS — off the loop
        async with client.stream(
            method,
            current,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            content=content,
        ) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    return resp.status_code, resp.headers, b""
                if hop == _MAX_REDIRECTS:
                    raise _ProbeRefused("target redirected too many times")
                current = urljoin(current, location)
                if resp.status_code == 303:
                    method = "GET"
                    content = None
                continue
            return resp.status_code, resp.headers, await _read_capped(resp)
    raise _ProbeRefused("target redirected too many times")  # pragma: no cover


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
    chosen: tuple[int, httpx.Headers, bytes] | None = None
    method = "GET"
    latency_ms: int | None = None
    try:
        start = time.monotonic()
        status, resp_headers, body = await _fetch_once(client, "GET", url, headers, timeout)
        latency_ms = int((time.monotonic() - start) * 1000)
        chosen = (status, resp_headers, body)
        if status != 402:
            start = time.monotonic()
            status2, headers2, body2 = await _fetch_once(
                client,
                "POST",
                url,
                {**headers, "content-type": "application/json"},
                timeout,
                content=b"{}",
            )
            latency2 = int((time.monotonic() - start) * 1000)
            if status2 == 402:
                chosen = (status2, headers2, body2)
                method = "POST"
                latency_ms = latency2
    except _ProbeRefused as e:
        error = f"probe refused: {e}"
    except ValueError as e:  # SSRF guard refusal (raised via to_thread)
        error = f"probe refused: {e}"
    except httpx.HTTPError as e:
        error = str(e)
    return {
        "alive": chosen is not None,
        "status": chosen[0] if chosen is not None else None,
        "headers": dict(chosen[1]) if chosen is not None else {},
        "body": chosen[2] if chosen is not None else b"",
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
