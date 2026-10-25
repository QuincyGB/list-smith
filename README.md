# ListSmith — listing grades & repairs for x402 services

**Your x402 route is discoverable only if its 402 response carries a
valid Bazaar discovery extension — and most are broken.** The Oct 2026
catalog data: 87.8% of listings ship a hollow `{"type", "example"}`
output with no typed schema, descriptions over 500 characters make the
CDP facilitator reject payments, and a malformed extension fails
facilitator-side validation in silence (we know — our own Token
Verdict's first extension failed `validate_discovery_extension`, and
nobody told us). The seller just never gets calls. Nobody sold the
fix. ListSmith does: it grades a listing's discovery metadata, and
sells back a repaired package that provably validates.

Sibling services: [RouteVerdict](https://github.com/QuincyGB/route-verdict)
(trust & liveness checks — it *finds* the hollow listings) and
[Token Verdict](https://github.com/QuincyGB/token-verdict) (risk
verdicts for token contracts — the first patient). Same stack, same
patterns, same payout wallet.

## The two routes

| Route | Price | Purpose |
|---|---|---|
| `POST /v1/grade` | **$0.10 USDC** | Diagnosis only: what is broken, itemized |
| `POST /v1/fix` | **$0.50 USDC** | The repaired discovery package |
| `GET /quote` | free | Prices, asset, network, pay-to wallet |
| `GET /.well-known/x402` | free | Machine-readable manifest (full schemas) |
| `GET /skill.md` | free | Documentation written for agents |
| `GET /llms.txt` | free | Compact agent index |
| `GET /health` | free | Liveness |

**Why these prices:** the grade is deliberately priced at impulse
level — $0.10, ten times the Bazaar's $0.01 median call — because it
is the top of the funnel, its marginal cost is one unpaid probe, and
every grade report ends with the exact list of defects the fix
removes; the fix is where the value (and the price) sits: $0.50 is a
one-time seller-tooling purchase, not a per-call commodity, priced
against the cost of the alternative — debugging facilitator validation
blind, or staying invisible in the catalog — and against what
expertise endpoints already command in this economy (the leading
security-analysis route prices at a premium; the ai_search pocket
averages ~$0.06/call against a $0.01 catalog median). A seller who
fixes their listing once amortizes $0.50 over every future call the
listing earns; a grade that comes back clean costs the seller a dime
and buys certainty. Neither price chases the $0.01 commodity floor —
ListSmith sells seller tooling, not calls.

## What /v1/grade checks (diagnosis only — no repaired content)

Observation-only (the target is probed unpaid, never charged):

- **Reachability & challenge validity** — does the route answer with a
  parseable x402 challenge at all?
- **Both real SDK validators** — `validate_discovery_extension`
  (info-vs-schema consistency; the one that fails facilitator-side)
  and `validate_discovery_extension_spec`, run against the published
  extension, verdicts and error strings included.
- **Schema quality** — RouteVerdict's scorer, reused verbatim: hollow
  `{type, example}` detection, untyped properties, missing input /
  output pieces, scored 0–100 with named deductions.
- **Description traps** — over 500 chars (CDP rejects the payments),
  missing, or lacking the "use when" phrasing the Bazaar ranks on.
- **Terms sanity** — payTo / asset / network present; on Base, the
  asset is the real USDC contract, not the known placeholder address
  (the exact bug our own Token Verdict manifest once shipped).
- **Sample-vs-schema** — when the buyer supplies a `sample_output`,
  it is validated against the endpoint's published output schema.
- Returns itemized `defects` (name / severity / detail), a 0–100
  `score`, and `fix_available`.

## What /v1/fix returns

The grade report, plus a `package`:

1. **`extension`** — a complete Bazaar discovery extension. The output
   schema is inferred from the supplied `sample_output` (infer.py:
   every sampled field typed, **no invented fields**; numeric-looking
   strings stay strings; fields null in the sample stay untyped;
   array element schemas merge). Marked
   `"inferred_from": "single_sample"` so the buyer knows to review.
2. **`description`** — composed and *programmatically counted* under
   500 chars, with a "use when" sentence.
3. **`openapi_snippet`** and **`well_known_snippet`** — paste-in
   manifest pieces. The snippet's accepts mirror the **live challenge
   exactly** (manifest/challenge agreement is what directory
   validators check); if the challenge itself publishes the
   placeholder asset, the instructions say so loudly instead of
   silently diverging.
4. **`instructions`** — how to ship it with the standard x402
   middleware (`extensions={"bazaar": ...}`), plus the cataloging
   truth: metadata alone never lists you — a settlement through the
   CDP or PayAI facilitator does, per facilitator, and listings age
   out after ~30 days without one.

**Hard gate:** the generated extension must pass **both** SDK
validators before it is returned. If a package cannot be made to
validate — unreachable target, no valid challenge, a sample too deep
or too large to infer from — the route returns `ok: false` with
`failure` naming why. A broken package is never shipped.

**Modes:** send `"mode": "compact"` for a smaller response whose
`package` carries the repaired fields only — `description`, `info`
(the repaired extension info, output schema included), `changes`,
and the same `validation` — instead of the snippet bundle. It is a
complete repair product (everything needed to fix the listing is in
it); full mode, the default, adds the paste-ready meta-schema,
OpenAPI / well-known snippets, and long-form instructions. Compact
exists because Settled's discovery extractor silently drops
published output samples over ~1.5 KB and an honest full-mode
response (~5 KB) cannot fit under that — so the listing's published
example is a real compact request/response pair, verified
byte-for-byte against a fresh fixer run by the test suite.

## How cataloging works (why the facilitator env matters)

There is no submission form for the x402 indexes. **A listing is a
side-effect of a settled payment**: each facilitator catalogs only
settlements through *its own* facilitator, in its own index. So:

- `X402_FACILITATOR_URL` unset → keyless ultravioletadao facilitator:
  settles on Base, feeds no major index.
- `X402_FACILITATOR_URL=https://facilitator.payai.network` →
  settlements catalog ListSmith in the **PayAI index**. No API key.
- `X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402`
  plus `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` → settlements catalog
  ListSmith in the **CDP Bazaar / Agentic.Market**.

One deployment settles through one facilitator at a time; appearing
in both indexes means at least one settled call under each
configuration, and at least one settled call every ~30 days per index
or the listing ages out. ListSmith's own discovery metadata is built
to catalog from birth: full typed output schemas on both routes
(never the hollow norm), descriptions under 500 chars, real shipped
examples validated against the schemas — all pinned by tests.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m pytest tests/ -q
```

Key env (see `.env.example`): `X402_PAY_TO` (payout wallet),
`X402_GRADE_PRICE_USDC` (default `$0.10`), `X402_FIX_PRICE_USDC`
(default `$0.50`), `X402_FACILITATOR_URL`, `CDP_API_KEY_ID` /
`CDP_API_KEY_SECRET`, `LISTSMITH_FETCH_TIMEOUT_S`.

Production runs on Railway from the included `Dockerfile`
(healthcheck: `/health`). ListSmith is stateless — grades and fixes
keep no per-target history (that memory is RouteVerdict's product).

## Honest limits

ListSmith reads what an endpoint publishes unpaid; it never pays the
target and never sees the paid payload, so it grades the *listing*,
not the service behind it. The fix's output schema is inferred from
ONE sample — a field that appears in other responses but not the
sample will be missing until the seller adds it, and fields null in
the sample are left untyped rather than guessed. The grade's catalog
claims are about the discovery metadata only; presence in an index
additionally requires a settlement through that index's facilitator,
which no metadata can substitute for.
