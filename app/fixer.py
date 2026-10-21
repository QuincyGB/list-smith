"""The /v1/fix repair package — a listing's discovery metadata, rebuilt
to validate, from one real sample output.

Pipeline: grade first (grader.py — the response carries the defect
report and the before-score), then generate:

  1. a complete Bazaar discovery extension — input schema (theirs when
     published, inferred from their example otherwise) and a FULL output
     schema inferred from the supplied sample_output (infer.py: every
     sampled field typed, no invented fields), marked
     "inferred_from": "single_sample";
  2. a description, programmatically counted under 500 chars (longer
     breaks CDP payments), containing a "use when" sentence;
  3. an OpenAPI paths snippet and a /.well-known/x402 resource snippet
     (the snippet's accepts mirror the LIVE challenge exactly —
     manifest/challenge agreement is what directory validators check);
  4. paste-in instructions for the standard x402 middleware pattern.

HARD GATE: the generated extension must pass BOTH x402 SDK validators
(validate_discovery_extension and validate_discovery_extension_spec)
before it is returned. A package that cannot be made to validate is
returned as an honest failure naming why — never shipped broken.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx
import jsonschema

from .grader import BASE_USDC, KNOWN_PLACEHOLDER_ASSETS, _MISSING, grade_inspection
from .infer import InferenceError, infer_schema
from .probe import inspect_target

MAX_SAMPLE_BYTES = 262_144  # 256 KiB of sample is plenty to infer from

_BODY_METHODS = ("POST", "PUT", "PATCH")
_QUERY_METHODS = ("GET", "HEAD", "DELETE")

_USE_CASES = {
    "token-risk": "you need a risk verdict on a token or contract before trading or paying",
    "risk": "you need a risk verdict before committing funds",
    "search": "you need web results inside an agent task loop",
    "data": "you need structured data inside an agent workflow",
    "media": "you need generated media inside an agent workflow",
    "monitoring": "you need a scheduled check on a route or asset you rely on",
}


def _contains_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_null(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_null(v) for v in value)
    return False


def _display_name(resource_url: str, name: str | None) -> str:
    if name and name.strip():
        return name.strip()[:60]
    parsed = urlparse(resource_url)
    host = parsed.netloc.split(":")[0]
    first_label = host.split(".")[0].replace("-", " ").replace("_", " ").strip()
    segments = [
        s for s in parsed.path.split("/")
        if s and not (s.lower().startswith("v") and s[1:].isdigit())
    ]
    tail = segments[-1].replace("-", " ").replace("_", " ").strip() if segments else ""
    parts = [p for p in (first_label, tail) if p and p.lower() not in (first_label or "").lower()]
    display = " ".join(parts).strip() or host
    return display.title()[:60]


def _returns_clause(sample_output: object) -> str:
    if isinstance(sample_output, dict):
        keys = list(sample_output.keys())
        if not keys:
            return "returns an empty JSON object"
        shown = ", ".join(keys[:5])
        more = f" (+{len(keys) - 5} more)" if len(keys) > 5 else ""
        return f"returns {shown}{more}"
    if isinstance(sample_output, list):
        return "returns a JSON array"
    return f"returns a JSON {type(sample_output).__name__}"


def _build_description(
    resource_url: str,
    sample_output: object,
    terms: dict,
    name: str | None,
    category: str | None,
) -> str:
    """Compose a <500-char description with a mandatory use-when clause.
    The length is counted, not hoped for: clauses are shed in order
    (field list first) until it fits."""
    display = _display_name(resource_url, name)
    use_case = _USE_CASES.get(
        (category or "").strip().lower(),
        "you need this route's result inside an agent workflow, purchase flow, or monitoring job",
    )
    price = terms.get("price_usdc")
    price_clause = f" {price} per call in USDC on Base via x402." if price else ""

    def compose(returns: str) -> str:
        return f"{display} — {returns}. Use when {use_case}.{price_clause}"

    text = compose(_returns_clause(sample_output))
    if len(text) >= 500 and isinstance(sample_output, dict):
        keys = list(sample_output.keys())
        text = compose("returns " + ", ".join(keys[:2]) + (f" (+{len(keys) - 2} more)" if len(keys) > 2 else ""))
    if len(text) >= 500:
        text = f"{display}. Use when {use_case}.{price_clause}"
    if len(text) >= 500:  # pathological name/use-case — hard word-boundary cut
        cut = text[:495].rsplit(" ", 1)[0]
        text = cut if cut.endswith(".") else cut + "."
    assert len(text) < 500, f"description still {len(text)} chars"
    return text


def _input_material(bazaar: dict | None) -> tuple[str, str, object, dict, str | None]:
    """(route_kind, method, example_input, input_schema, note) from the
    target's published extension — theirs when usable, honest defaults
    otherwise. route_kind is "body" or "query"."""
    info = bazaar.get("info") if isinstance(bazaar, dict) and isinstance(bazaar.get("info"), dict) else {}
    input_info = info.get("input") if isinstance(info.get("input"), dict) else {}
    meta_input = {}
    if isinstance(bazaar, dict) and isinstance(bazaar.get("schema"), dict):
        props = bazaar["schema"].get("properties")
        if isinstance(props, dict) and isinstance(props.get("input"), dict):
            meta_input = props["input"].get("properties") or {}

    if "queryParams" in input_info:
        method = input_info.get("method")
        method = method if method in _QUERY_METHODS else "GET"
        example = input_info.get("queryParams")
        example = example if isinstance(example, dict) else {}
        schema = meta_input.get("queryParams")
        if not (isinstance(schema, dict) and schema.get("properties")):
            schema = infer_schema(example) if example else {"type": "object"}
        return "query", method, example, schema, None
    if "body" in input_info or "bodyType" in input_info:
        method = input_info.get("method")
        method = method if method in _BODY_METHODS else "POST"
        example = input_info.get("body")
        example = example if isinstance(example, dict) else {}
        schema = meta_input.get("body")
        if not (isinstance(schema, dict) and schema.get("properties")):
            schema = infer_schema(example) if example else {"type": "object"}
        return "body", method, example, schema, None
    return (
        "body",
        "POST",
        {},
        {"type": "object"},
        "No input shape was published by the endpoint — the package assumes a POST "
        "JSON body route with an empty example input; replace the example body and "
        "input schema with your real request shape before shipping.",
    )


def _meta_schema(kind: str, input_schema: dict, output_schema: dict) -> dict:
    if kind == "query":
        input_props = {
            "type": {"type": "string", "const": "http"},
            "method": {"type": "string", "enum": list(_QUERY_METHODS)},
            "queryParams": input_schema,
        }
        input_required = ["type", "method"]
    else:
        input_props = {
            "type": {"type": "string", "const": "http"},
            "method": {"type": "string", "enum": list(_BODY_METHODS)},
            "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
            "body": input_schema,
        }
        input_required = ["type", "method", "bodyType", "body"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": input_props,
                "required": input_required,
                "additionalProperties": False,
            },
            "output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "example": output_schema,
                    "schema": {"type": "object"},
                },
                "required": ["type"],
            },
        },
        "required": ["input"],
    }


def _build_extension(
    kind: str, method: str, example_input: object, input_schema: dict,
    sample_output: object, output_schema: dict,
) -> dict:
    if kind == "query":
        input_info = {"type": "http", "method": method, "queryParams": example_input}
    else:
        input_info = {"type": "http", "method": method, "bodyType": "json", "body": example_input}
    return {
        "info": {
            "input": input_info,
            "output": {"type": "json", "example": sample_output, "schema": output_schema},
        },
        "schema": _meta_schema(kind, input_schema, output_schema),
    }


def _openapi_snippet(
    resource_url: str, kind: str, method: str, description: str,
    example_input: object, input_schema: dict,
    sample_output: object, output_schema: dict,
) -> dict:
    path = urlparse(resource_url).path or "/"
    operation: dict = {"summary": description.split(".")[0], "description": description}
    if kind == "query":
        params = []
        props = input_schema.get("properties", {}) if isinstance(input_schema, dict) else {}
        required = set(input_schema.get("required", [])) if isinstance(input_schema, dict) else set()
        for pname, pschema in props.items():
            params.append(
                {"name": pname, "in": "query", "required": pname in required, "schema": pschema}
            )
        if params:
            operation["parameters"] = params
    else:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": input_schema, "example": example_input}},
        }
    operation["responses"] = {
        "200": {
            "description": "Success",
            "content": {"application/json": {"schema": output_schema, "example": sample_output}},
        },
        "402": {
            "description": "Payment required — x402 challenge; extensions.bazaar carries the discovery extension from this package."
        },
    }
    return {"openapi": "3.0.3", "paths": {path: {method.lower(): operation}}}


def _instructions(terms: dict, notes_context: dict) -> str:
    lines = [
        "How to ship this package (standard x402 middleware, e.g. the Python/TS x402 SDKs):",
        "",
        "1. Set your route's description to package.description (it is under 500 characters,",
        "   so CDP-facilitator payments will not be rejected for length), e.g.:",
        '       RouteConfig(description=<package.description>, extensions={"bazaar": <package.extension>})',
        "2. Attach package.extension as the route's Bazaar discovery extension:",
        '       extensions={"bazaar": package["extension"]} on the same route config.',
        "   The extension passed validate_discovery_extension and",
        "   validate_discovery_extension_spec at generation time.",
        "3. Replace the resource entry in your /.well-known/x402 manifest with",
        "   package.well_known_snippet, and merge package.openapi_snippet.paths into",
        "   your OpenAPI document.",
        "4. Keep the manifest accepts IDENTICAL to your live 402 challenge — directory",
        "   validators read the two against each other and reject mismatches.",
        "5. Metadata alone never lists you: settle one real payment through the CDP",
        "   facilitator (CDP Bazaar / Agentic.Market) or the PayAI facilitator (PayAI",
        "   index) and that index catalogs this route; re-settle at least every ~30 days",
        "   or the listing ages out.",
    ]
    if notes_context.get("placeholder_asset"):
        lines += [
            "",
            f"ASSET WARNING: your live challenge publishes asset {notes_context['placeholder_asset']}",
            f"on Base — the known placeholder, not USDC ({BASE_USDC}). Fix the asset in your",
            "payment configuration first, then update accepts.asset in the snippet to match",
            "the corrected challenge; snippet and challenge must agree (see step 4).",
        ]
    return "\n".join(lines)


def build_fix(inspection: dict, sample_output: object, name: str | None, category: str | None) -> dict:
    """Grade, then generate + validate the package. Pure given the
    inspection — run_fix does the network work."""
    resource_url = inspection["resource_url"]
    grade = grade_inspection(inspection, sample_output=sample_output)
    base = {
        "resource_url": resource_url,
        "graded_at": grade["graded_at"],
        "score_before": grade["score"] if grade["reachable"] else None,
        "defects": grade["defects"],
        "package": None,
        "notes": [],
    }

    def fail(reason: str) -> dict:
        return {**base, "ok": False, "failure": reason}

    if sample_output is None:
        return fail("sample_output is required for /v1/fix and must be one real response body (got null).")
    try:
        serialized = json.dumps(sample_output)
    except (TypeError, ValueError):
        return fail("sample_output is not JSON-serializable.")
    if len(serialized.encode()) > MAX_SAMPLE_BYTES:
        return fail(f"sample_output is {len(serialized.encode())} bytes — over the {MAX_SAMPLE_BYTES}-byte inference limit.")
    if not grade["reachable"]:
        detail = inspection["probe"]["error"] or "no HTTP response"
        return fail(f"endpoint unreachable ({detail}) — there is no live challenge to repair against.")
    if not grade["fix_available"]:
        err = inspection["challenge"].error or "no parseable x402 challenge"
        return fail(f"no valid x402 challenge to repair against: {err}.")

    challenge = inspection["challenge"]
    terms = inspection["terms"]
    try:
        output_schema = infer_schema(sample_output)
    except InferenceError as e:
        return fail(f"could not infer an output schema: {e}")
    try:
        jsonschema.validate(instance=sample_output, schema=output_schema)
    except jsonschema.ValidationError as e:  # defensive — inference guarantees this
        return fail(f"inferred schema does not validate its own sample: {e.message}")

    kind, method, example_input, input_schema, input_note = _input_material(challenge.bazaar)
    extension = _build_extension(kind, method, example_input, input_schema, sample_output, output_schema)

    from x402.extensions.bazaar import (
        validate_discovery_extension,
        validate_discovery_extension_spec,
    )

    consistency = validate_discovery_extension(extension)
    spec = validate_discovery_extension_spec(extension)
    if not (consistency.valid and spec.valid):
        problems = "; ".join(list(consistency.errors or []) + list(spec.errors or []))
        return fail(f"the generated extension failed SDK validation and was NOT shipped: {problems}")

    description = _build_description(resource_url, sample_output, terms, name, category)
    primary = inspection["primary"]
    accept = {
        "scheme": primary.scheme,
        "network": primary.network,
        "amount": primary.amount,
        "asset": primary.asset,
        "payTo": primary.pay_to,
        "maxTimeoutSeconds": primary.max_timeout_seconds,
    }
    well_known = {
        "resource": resource_url,
        "method": method,
        "price": terms["price_usdc"],
        "scheme": primary.scheme or "exact",
        "mimeType": "application/json",
        "description": description,
        "accepts": [accept],
        "inputSchema": input_schema,
        "outputSchema": output_schema,
    }
    placeholder = (
        primary.asset
        if primary.network == "eip155:8453"
        and primary.asset
        and primary.asset.lower() in KNOWN_PLACEHOLDER_ASSETS
        else None
    )
    package = {
        "inferred_from": "single_sample",
        "description": description,
        "extension": extension,
        "openapi_snippet": _openapi_snippet(
            resource_url, kind, method, description, example_input, input_schema,
            sample_output, output_schema,
        ),
        "well_known_snippet": well_known,
        "instructions": _instructions(terms, {"placeholder_asset": placeholder}),
        "validation": {"consistency_valid": True, "spec_valid": True},
    }
    notes = [
        "inferred_from: single_sample — the output schema was inferred from the one sample "
        "supplied; review it before shipping, especially arrays (element schemas are merged) "
        "and any field whose real type varies.",
    ]
    if _contains_null(sample_output):
        notes.append(
            "Fields null in the sample were left untyped ({}) — one sample says nothing "
            "honest about their real type; set the type yourself if you know it."
        )
    if input_note:
        notes.append(input_note)
    return {**base, "ok": True, "failure": None, "package": package, "notes": notes}


async def run_fix(
    resource_url: str,
    sample_output: object,
    name: str | None = None,
    category: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> dict:
    inspection = await inspect_target(resource_url, client=client, timeout=timeout)
    return build_fix(inspection, sample_output, name, category)
