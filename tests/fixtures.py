"""Deliberately broken (and one healthy) listing fixtures.

A MockTransport plays the target host, so grade/fix run with zero
network. Each broken fixture isolates one defect class the spec names:
hollow schema, >500-char description, wrong asset address, an extension
that fails SDK validation, and no extension at all.
"""

import httpx

TARGET_URL = "https://target.example.com/v1/thing"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PLACEHOLDER = "0x3600000000000000000000000000000000000000"
PAY_TO = "0x1111111111111111111111111111111111111111"

HEALTHY_DESCRIPTION = (
    "Target Thing returns a verdict and a score for a thing. "
    "Use when you need a thing checked before acting on it."
)

FULL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["verdict", "score"],
}
FULL_EXAMPLE = {"verdict": "OK", "score": 12}
FULL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"thing_id": {"type": "string"}},
    "required": ["thing_id"],
}
FULL_INPUT_BODY = {"thing_id": "abc"}


def make_extension(
    *,
    output_example=FULL_EXAMPLE,
    output_schema=FULL_OUTPUT_SCHEMA,
    input_body=FULL_INPUT_BODY,
    input_schema=FULL_INPUT_SCHEMA,
    method="POST",
    kind="body",
    include_output_schema=True,
):
    """A canonical-shape extension (mirrors the SDK/route-verdict
    pattern). Keyword switches produce the broken variants."""
    output = {"type": "json", "example": output_example}
    if include_output_schema and output_schema is not None:
        output["schema"] = output_schema
    if kind == "query":
        input_info = {"type": "http", "method": method, "queryParams": input_body}
        meta_input = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "const": "http"},
                "method": {"type": "string", "enum": ["GET", "HEAD", "DELETE"]},
                "queryParams": input_schema or {"type": "object"},
            },
            "required": ["type", "method"],
            "additionalProperties": False,
        }
    else:
        input_info = {
            "type": "http",
            "method": method,
            "bodyType": "json",
            "body": input_body,
        }
        meta_input = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "const": "http"},
                "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
                "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                "body": input_schema or {"type": "object"},
            },
            "required": ["type", "method", "bodyType", "body"],
            "additionalProperties": False,
        }
    return {
        "info": {"input": input_info, "output": output},
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "input": meta_input,
                "output": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "example": output_schema or {"type": "object"},
                        "schema": {"type": "object"},
                    },
                    "required": ["type"],
                },
            },
            "required": ["input"],
        },
    }


def challenge(
    *,
    extension="healthy",
    description=HEALTHY_DESCRIPTION,
    asset=USDC,
    amount="20000",
    pay_to=PAY_TO,
):
    """A v2 challenge dict. `extension`: "healthy" | "hollow" |
    "invalid" | "query" | None (no extensions at all)."""
    body = {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": TARGET_URL, "description": description},
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "asset": asset,
                "amount": amount,
                "payTo": pay_to,
                "maxTimeoutSeconds": 300,
            }
        ],
    }
    if extension == "healthy":
        body["extensions"] = {"bazaar": make_extension()}
    elif extension == "hollow":
        body["extensions"] = {"bazaar": make_extension(include_output_schema=False)}
    elif extension == "invalid":
        # The old Token Verdict shape: the extension-level "schema" is
        # the raw INPUT schema, so info (input/output) fails validation
        # against it — facilitator-side consistency failure.
        ext = make_extension()
        ext["schema"] = {
            "type": "object",
            "properties": {"address": {"type": "string"}},
            "required": ["address"],
        }
        body["extensions"] = {"bazaar": ext}
    elif extension == "query":
        body["extensions"] = {
            "bazaar": make_extension(kind="query", method="GET",
                                     input_body={"thing_id": "abc"})
        }
    elif extension is None:
        pass
    else:  # a raw extension dict passed through
        body["extensions"] = {"bazaar": extension}
    return body


def make_client(target):
    """target: a challenge dict, an httpx.Response, or a callable."""

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(target, httpx.Response):
            return target
        if callable(target):
            return target(request)
        return httpx.Response(402, json=target)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def unreachable_client():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
