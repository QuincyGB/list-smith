"""ListSmith — x402 listing grades & repairs for x402 services.

Paid:   POST /v1/grade   (diagnosis only, USDC via x402 v2 on Base)
        POST /v1/fix     (the repaired discovery package)
Free:   GET  /, /health, /quote, /.well-known/x402, /skill.md, /llms.txt

Both paid routes are observation-only toward the target: ListSmith
probes it WITHOUT paying it and works from what it publishes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Some shells/hosts export a no_proxy list containing bracketed IPv6
# entries ("[::1]"). The x402 SDK's httpx-based facilitator client parses
# that list as URLs and dies with "Invalid port: ':1]'", which turns every
# unpaid call into a 500 instead of a 402. Scrub just the bracketed
# entries before any SDK client is constructed. (Carried over from
# RouteVerdict, which fixed in-process what Token Verdict only patched
# in its test conftest.)
for _var in ("no_proxy", "NO_PROXY"):
    _val = os.environ.get(_var)
    if _val:
        _kept = [e for e in _val.split(",") if "[" not in e and "]" not in e]
        os.environ[_var] = ",".join(_kept)

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption, RouteConfig
from x402.http.middleware import fastapi_payment_middleware
from x402.mechanisms.evm.exact.server import ExactEvmScheme
from x402.server import x402ResourceServer

from .config import load_settings
from .discovery import (
    OWNERSHIP_PROOFS,
    fix_extension,
    grade_extension,
    llms_txt,
    skill_md,
    x402_manifest,
)
from .fixer import run_fix
from .grader import run_grade

log = logging.getLogger("list-smith")
logging.basicConfig(level=logging.INFO)

settings = load_settings()  # raises if payout wallet is a placeholder

# --- x402 wiring -------------------------------------------------------------
# The facilitator is chosen by X402_FACILITATOR_URL alone (plus CDP keys for
# the CDP branch). Cataloging is settlement-driven per facilitator — each
# index lists only settlements through its OWN facilitator — so the env
# choice decides which catalog a settled payment feeds:
#   * CDP (api.cdp.coinbase.com, needs CDP_API_KEY_ID/SECRET): CDP Bazaar.
#   * PayAI (facilitator.payai.network, no key for exact): PayAI index.
#   * default keyless ultravioletadao: settles Base, feeds no major index.
# One deployment settles through one facilitator at a time; to appear in
# both indexes, settle at least one call under each configuration.
if settings.use_cdp:
    from .cdp_auth import cdp_facilitator_config

    assert settings.cdp_api_key_id and settings.cdp_api_key_secret
    facilitator = HTTPFacilitatorClient(
        cdp_facilitator_config(settings.cdp_api_key_id, settings.cdp_api_key_secret)
    )
    log.info("facilitator: CDP (Bazaar cataloging path)")
elif settings.use_payai:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: PayAI (PayAI index cataloging path)")
else:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: %s (%s)", settings.facilitator_url, settings.network)

resource_server = x402ResourceServer(facilitator)
resource_server.register(settings.network, ExactEvmScheme())

GRADE_DESCRIPTION = (
    "Grade one x402 route's Bazaar discovery metadata. Returns the "
    "diagnosis only: both x402 SDK validator verdicts, the schema-"
    "quality score, itemized defects with severities (hollow output "
    "schemas, over-500-char descriptions, wrong asset addresses, failed "
    "validation), and a 0-100 grade. Use this when you operate an x402 "
    "route and want to know exactly what is broken before paying for "
    "the repair."
)

FIX_DESCRIPTION = (
    "Repair one x402 route's Bazaar discovery metadata. Send a "
    "resource URL and one real sample; get the defect report plus "
    "a package that ships only after passing both x402 SDK "
    "validators: full output schema inferred from the sample, a "
    "sub-500-char description with a use-when sentence, OpenAPI "
    "and well-known snippets, instructions. Mode 'compact' returns "
    "the repaired fields only (description, extension info, "
    "changes). Use this when your listing is hollow, invalid, or "
    "invisible in the Bazaar."
)

_TAGS = ["x402", "bazaar", "discovery", "validation", "schema", "agents"]

routes = {
    "POST /v1/grade": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=settings.pay_to,
                price=settings.grade_price_usdc,
                network=settings.network,
            )
        ],
        description=GRADE_DESCRIPTION,
        mime_type="application/json",
        service_name=settings.service_name,  # ≤32 chars, forwarded to Bazaar
        tags=_TAGS,
        extensions={"bazaar": grade_extension()},
    ),
    "POST /v1/fix": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=settings.pay_to,
                price=settings.fix_price_usdc,
                network=settings.network,
            )
        ],
        description=FIX_DESCRIPTION,
        mime_type="application/json",
        service_name=settings.service_name,
        tags=_TAGS,
        extensions={"bazaar": fix_extension()},
    ),
}

# --- app ---------------------------------------------------------------------
app = FastAPI(title="list-smith", version="1.0.0")


def _openapi_with_ownership_proofs():
    """Default OpenAPI document plus the x402scan ownership-proof root
    keys (x-discovery / x-agentcash-provenance). x402scan reads these to
    verify control of the payTo address — see OWNERSHIP_PROOFS in
    discovery.py; removing them un-verifies the listing."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema["x-discovery"] = {"ownershipProofs": OWNERSHIP_PROOFS}
    schema["x-agentcash-provenance"] = {"ownershipProofs": OWNERSHIP_PROOFS}
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_ownership_proofs

# Build the payment middleware ONCE. Constructing it runs the SDK's
# initialize(), which synchronously fetches the facilitator's /supported
# document over the network — building it per request (as this module did
# originally) added a full external round-trip, on the event loop, to
# EVERY request including /health and the unpaid 402 challenges. That was
# the fleet's latency-variance problem (2026-10-24): Settled's latency
# component whipsawed on services that hadn't changed. (Same fix as
# token-verdict ff05d1c and stall-row 59e14b3.)
_payment_middleware = fastapi_payment_middleware(routes, resource_server)


_PASS_PATHS = {"/v1/grade", "/v1/fix"}


@app.middleware("http")
async def x402_middleware(request: Request, call_next):
    # A call presenting a Fleet Pass id is paid from the pass ledger
    # in StallRow: debit before the handler, refund on failure.
    from . import fleet_pass as _fleet_pass

    debit = await _fleet_pass.debit_for(request, settings.service_name, _PASS_PATHS)
    if isinstance(debit, JSONResponse):
        return debit
    if debit:
        response = await call_next(request)
        if response.status_code >= 400:
            await _fleet_pass.refund(debit)
        return response
    return await _payment_middleware(request, call_next)


# Request-body cap (security audit 2026-10-25): every legitimate body in
# this API is a small JSON document; an uncapped body lets a caller
# stream unbounded bytes at the parser. Chunked bodies without a
# Content-Length are the residual gap (terminated at the edge).
_MAX_BODY_BYTES = 1024 * 1024


@app.middleware("http")
async def _body_cap(request: Request, call_next):
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _MAX_BODY_BYTES:
        return JSONResponse({"error": "request body too large"}, status_code=413)
    return await call_next(request)


class GradeRequest(BaseModel):
    resource_url: str = Field(description="Full URL of the x402 resource to grade")
    sample_output: Any = Field(
        default=None,
        description="Optional real response body — also checked against the published output schema",
    )


class FixRequest(BaseModel):
    resource_url: str = Field(description="Full URL of the x402 resource to repair")
    sample_output: Any = Field(description="One real response body from the endpoint (required)")
    name: str | None = Field(default=None, description="Display name for the generated description")
    category: str | None = Field(default=None, description="Category for the use-when sentence")
    mode: str = Field(
        default="full",
        description="'full' (default) returns the complete package; 'compact' returns the repaired fields only (description, extension info, changes)",
    )


def _public_base(request: Request) -> str:
    """Public base URL of this service. Railway terminates TLS at the edge,
    so the request scheme arrives as http — indexers and agents need https."""
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ListSmith — listing grades &amp; repairs for x402 services</title>
<meta name="description" content="Your x402 route is discoverable only if its 402 carries a valid Bazaar discovery extension. Most are hollow, over-length, or invalid — and the seller just never gets calls. ListSmith grades it for $0.10 and sells the validated fix for $0.50.">
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 20px 60px;
         line-height: 1.55; color: #16202a; background: #fbfcfd; }
  h1 { font-size: 2rem; margin-bottom: .2em; }
  .tag { font-size: 1.1rem; color: #33475b; margin-top: 0; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre { background: #0f1b26; color: #dcebf5; padding: 14px 16px;
        border-radius: 8px; overflow-x: auto; font-size: .85rem; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; }
  td, th { border-bottom: 1px solid #dde5ec; padding: 6px 8px;
           text-align: left; vertical-align: top; }
  .pill { display: inline-block; background: #e7f0e9; color: #1d5c33;
          border-radius: 999px; padding: 2px 10px; font-size: .85rem; }
  a { color: #0b5cad; }
  .limits { background: #fdf6e5; border: 1px solid #ecd9a0;
            border-radius: 8px; padding: 10px 14px; }
</style>
</head>
<body>
<h1>ListSmith</h1>
<p class="tag">The listing doctor for the x402 Bazaar. A route is
discoverable only if its 402 response carries a valid Bazaar discovery
extension — and most are broken: <strong>87.8% ship a hollow
<code>{"type", "example"}</code> output</strong>, descriptions over 500
characters make the CDP facilitator reject payments, and a malformed
extension fails facilitator-side validation in silence. The seller just
never gets calls. ListSmith grades the damage — and sells the fix.
No accounts, no API keys: <em>payment is the authentication.</em></p>

<p><span class="pill">Live on Base mainnet</span>
<span class="pill">x402 v2 · exact scheme</span>
<span class="pill">Grade $0.10 · Fix $0.50</span>
<span class="pill">Observation only — never pays the target</span></p>

<h2>The two routes</h2>
<table>
  <tr><th>Route</th><th>Price</th><th>What you get</th></tr>
  <tr><td><code>POST /v1/grade</code></td><td>$0.10</td>
      <td>The diagnosis: both x402 SDK validator verdicts, schema-quality
      score, itemized defects with severities, a 0-100 grade. No repaired
      content.</td></tr>
  <tr><td><code>POST /v1/fix</code></td><td>$0.50</td>
      <td>The repair: a complete discovery package — full output schema
      inferred from one real sample output, a sub-500-char description
      with a use-when sentence, OpenAPI + well-known snippets, paste-in
      instructions. Ships only if it passes BOTH SDK validators.</td></tr>
</table>

<h2>How to call it</h2>
<pre>curl -X POST https://list-smith-production.up.railway.app/v1/fix \\
  -H 'content-type: application/json' \\
  -d '{"resource_url":"https://your-service.example.com/v1/route",
       "sample_output":{"your":"real response body"}}'</pre>
<p>Unpaid calls return an HTTP 402 x402 challenge. Pay with any x402 v2
client, retry with the payment header, and the report (or the package)
comes back. The target endpoint is probed unpaid and is never charged.</p>

<h2>Endpoints</h2>
<table>
  <tr><th>Route</th><th>Price</th><th>Purpose</th></tr>
  <tr><td><code>POST /v1/grade</code></td><td>$0.10</td><td>Grade one route's discovery metadata (paid, x402)</td></tr>
  <tr><td><code>POST /v1/fix</code></td><td>$0.50</td><td>Repair one route's discovery metadata (paid, x402)</td></tr>
  <tr><td><code>GET /quote</code></td><td>free</td><td>Prices, asset, network, pay-to wallet</td></tr>
  <tr><td><code>GET /.well-known/x402</code></td><td>free</td><td>Machine-readable service manifest</td></tr>
  <tr><td><code>GET /skill.md</code></td><td>free</td><td>Documentation written for agents</td></tr>
  <tr><td><code>GET /llms.txt</code></td><td>free</td><td>Compact agent index</td></tr>
  <tr><td><code>GET /health</code></td><td>free</td><td>Liveness</td></tr>
</table>
<p>Source code:
<a href="https://github.com/QuincyGB/list-smith">github.com/QuincyGB/list-smith</a>
(public source — the grading is deterministic and fully inspectable, and
the test suite ships with it). Sibling services:
<a href="https://github.com/QuincyGB/route-verdict">RouteVerdict</a>
(trust &amp; liveness checks — it finds the hollow listings, ListSmith
fixes them) and
<a href="https://github.com/QuincyGB/token-verdict">Token Verdict</a>
(risk verdicts for token contracts — whose own broken extension was the
first patient).</p>

<div class="limits">
<strong>Honest limits:</strong> ListSmith reads what an endpoint
publishes unpaid; it never pays the target and never sees the paid
payload. The fix's output schema is inferred from ONE sample: fields
null in the sample stay untyped, array element schemas are merged,
numeric-looking strings stay strings. Metadata alone never lists you —
a settled payment through the CDP or PayAI facilitator is what
catalogs a route, per facilitator.
</div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_HTML


@app.get("/health")
async def health():
    return {"ok": True, "service": settings.service_name}


@app.get("/quote")
async def quote():
    """Free: what the paid endpoints cost and where they settle."""
    return {
        "service": settings.service_name,
        "endpoints": {
            "/v1/grade": {"price": settings.grade_price_usdc, "purpose": "diagnosis only"},
            "/v1/fix": {"price": settings.fix_price_usdc, "purpose": "repaired discovery package"},
        },
        "asset": "USDC",
        "network": settings.network,
        "payTo": settings.pay_to,
        "protocol": "x402 v2 (exact scheme)",
    }


@app.get("/.well-known/x402")
async def well_known(request: Request):
    return JSONResponse(x402_manifest(settings, _public_base(request)))


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill(request: Request):
    return skill_md(settings, _public_base(request))


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms(request: Request):
    return llms_txt(settings, _public_base(request))


@app.post("/v1/grade")
async def grade(req: GradeRequest):
    """Paid endpoint — unreachable until the x402 middleware verifies payment."""
    resource_url = req.resource_url.strip()
    try:
        if req.sample_output is None:
            return await run_grade(resource_url, timeout=settings.fetch_timeout_s)
        return await run_grade(
            resource_url, timeout=settings.fetch_timeout_s, sample_output=req.sample_output
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception:  # noqa: BLE001 — never leak tracebacks to callers
        log.exception("grade failed for %s", resource_url)
        return JSONResponse({"error": "grade failed"}, status_code=502)


@app.post("/v1/fix")
async def fix(req: FixRequest):
    """Paid endpoint — unreachable until the x402 middleware verifies payment."""
    resource_url = req.resource_url.strip()
    try:
        return await run_fix(
            resource_url,
            req.sample_output,
            name=req.name,
            category=req.category,
            mode=req.mode,
            timeout=settings.fetch_timeout_s,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception:  # noqa: BLE001 — never leak tracebacks to callers
        log.exception("fix failed for %s", resource_url)
        return JSONResponse({"error": "fix failed"}, status_code=502)


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
