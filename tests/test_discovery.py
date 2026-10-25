"""ListSmith's own cataloging wiring — the showroom has to be cleaner
than anything it grades:

  * facilitator selection is config-driven (PayAI by URL alone, CDP only
    with URL + both keys, keyless default otherwise), prices are the
    decided $0.10 / $0.50;
  * every description stays UNDER 500 chars and carries a use-when line;
  * BOTH routes' bazaar extensions carry full typed output schemas, pass
    the x402 SDK validators, and their shipped examples are real outputs
    that validate against those schemas.
"""

import os

import jsonschema
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, load_settings
from app.discovery import (
    FIX_COMPACT_INPUT_EXAMPLE,
    FIX_COMPACT_OUTPUT_EXAMPLE,
    FIX_INPUT_SCHEMA,
    FIX_OUTPUT_EXAMPLE,
    FIX_OUTPUT_SCHEMA,
    GRADE_INPUT_SCHEMA,
    GRADE_OUTPUT_EXAMPLE,
    GRADE_OUTPUT_SCHEMA,
    fix_extension,
    grade_extension,
    x402_manifest,
)

CDP_URL = "https://api.cdp.coinbase.com/platform/v2/x402"
PAYAI_URL = "https://facilitator.payai.network"
OWNER = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"


def _settings(**env):
    keys = ["X402_FACILITATOR_URL", "CDP_API_KEY_ID", "CDP_API_KEY_SECRET"]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        return load_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --- facilitator selection & defaults -----------------------------------------

def test_payai_selected_by_url_alone():
    s = _settings(X402_FACILITATOR_URL=PAYAI_URL)
    assert s.use_payai is True
    assert s.use_cdp is False


def test_default_facilitator_is_neither():
    s = _settings()
    assert s.use_payai is False
    assert s.use_cdp is False


def test_cdp_needs_url_and_both_keys():
    assert _settings(X402_FACILITATOR_URL=CDP_URL).use_cdp is False
    assert _settings(X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id").use_cdp is False
    s = _settings(
        X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id", CDP_API_KEY_SECRET="secret"
    )
    assert s.use_cdp is True
    assert s.use_payai is False


def test_defaults_are_listsmith_terms():
    s = _settings()
    assert s.pay_to == OWNER
    assert s.grade_price_usdc == "$0.10"
    assert s.fix_price_usdc == "$0.50"
    assert s.network == "eip155:8453"
    assert s.service_name == "list-smith"


def test_bare_settings_helper_shape():
    s = Settings(
        pay_to=OWNER, grade_price_usdc="$0.10", fix_price_usdc="$0.50",
        network="eip155:8453", facilitator_url=PAYAI_URL,
        service_name="list-smith", fetch_timeout_s=10, port=3000,
    )
    assert s.use_payai is True


# --- description length ---------------------------------------------------------

def test_all_descriptions_under_500_chars():
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    descriptions = [
        main.GRADE_DESCRIPTION,
        main.FIX_DESCRIPTION,
        manifest["description"],
        *[r["description"] for r in manifest["resources"] if "description" in r],
    ]
    for d in descriptions:
        assert len(d) < 500, f"description too long ({len(d)} chars): {d[:60]}…"
    assert "Use this when" in main.GRADE_DESCRIPTION
    assert "Use this when" in main.FIX_DESCRIPTION


# --- bazaar extensions ------------------------------------------------------------

def test_route_extensions_pass_sdk_validators():
    from x402.extensions.bazaar import (
        validate_discovery_extension,
        validate_discovery_extension_spec,
    )

    for ext in (grade_extension(), fix_extension()):
        result = validate_discovery_extension(ext)
        assert result.valid, result.errors
        spec = validate_discovery_extension_spec(ext)
        assert spec.valid, spec.errors


def test_routes_carry_the_same_extensions():
    assert main.routes["POST /v1/grade"].extensions["bazaar"] == grade_extension()
    assert main.routes["POST /v1/fix"].extensions["bazaar"] == fix_extension()


def test_output_schemas_type_every_field():
    assert set(GRADE_OUTPUT_SCHEMA["required"]) == {
        "resource_url", "graded_at", "reachable", "http_status", "x402_valid",
        "terms", "extension_present", "sdk_validation", "schema_quality",
        "defects", "score", "fix_available",
    }
    defect = GRADE_OUTPUT_SCHEMA["properties"]["defects"]["items"]
    assert defect["properties"]["severity"]["enum"] == ["critical", "major", "minor"]
    assert set(FIX_OUTPUT_SCHEMA["required"]) == {
        "resource_url", "graded_at", "ok", "failure", "score_before",
        "defects", "package", "notes",
    }
    pkg = FIX_OUTPUT_SCHEMA["properties"]["package"]
    branches = {frozenset(b["required"]) for b in pkg["anyOf"] if "required" in b}
    assert {"type": "null"} in pkg["anyOf"]  # honest failures ship package: null
    assert branches == {
        frozenset({
            "inferred_from", "description", "extension", "openapi_snippet",
            "well_known_snippet", "instructions", "validation",
        }),
        frozenset({"inferred_from", "description", "info", "changes", "validation"}),
    }
    assert FIX_OUTPUT_SCHEMA["properties"]["mode"]["enum"] == ["full", "compact"]
    assert FIX_INPUT_SCHEMA["properties"]["mode"]["enum"] == ["full", "compact"]
    # Schemas are reachable from the extensions themselves, not hollow.
    assert grade_extension()["info"]["output"]["schema"] == GRADE_OUTPUT_SCHEMA
    assert fix_extension()["info"]["output"]["schema"] == FIX_OUTPUT_SCHEMA
    assert grade_extension()["schema"]["properties"]["input"]["properties"]["body"] == GRADE_INPUT_SCHEMA
    assert fix_extension()["schema"]["properties"]["input"]["properties"]["body"] == FIX_INPUT_SCHEMA


def test_examples_are_real_and_validate():
    assert GRADE_OUTPUT_EXAMPLE, "grade example must be a real grader output"
    assert FIX_OUTPUT_EXAMPLE, "fix example must be a real fixer output"
    jsonschema.validate(instance=GRADE_OUTPUT_EXAMPLE, schema=GRADE_OUTPUT_SCHEMA)
    jsonschema.validate(instance=FIX_OUTPUT_EXAMPLE, schema=FIX_OUTPUT_SCHEMA)
    jsonschema.validate(instance=FIX_COMPACT_OUTPUT_EXAMPLE, schema=FIX_OUTPUT_SCHEMA)
    assert GRADE_OUTPUT_EXAMPLE["resource_url"] == "https://target.example.com/v1/thing"
    assert FIX_OUTPUT_EXAMPLE["ok"] is True
    assert FIX_OUTPUT_EXAMPLE["mode"] == "full"
    assert FIX_OUTPUT_EXAMPLE["package"]["validation"] == {
        "consistency_valid": True, "spec_valid": True,
    }
    # The listing publishes the compact pair: a compact request and its
    # real response (see the Settled sample-size note in discovery.py).
    assert FIX_COMPACT_OUTPUT_EXAMPLE["ok"] is True
    assert FIX_COMPACT_OUTPUT_EXAMPLE["mode"] == "compact"
    assert FIX_COMPACT_INPUT_EXAMPLE["mode"] == "compact"
    assert fix_extension()["info"]["input"]["body"] == FIX_COMPACT_INPUT_EXAMPLE
    assert fix_extension()["info"]["output"]["example"] == FIX_COMPACT_OUTPUT_EXAMPLE


def test_compact_example_equals_fresh_compact_run():
    """The published compact example is not hand-maintained prose: it
    must equal what the fixer actually returns for the published
    compact input on the fixture target (timestamps aside)."""
    import asyncio

    from app.fixer import run_fix
    from tests.fixtures import challenge, make_client

    inp = FIX_COMPACT_INPUT_EXAMPLE
    result = asyncio.run(
        run_fix(
            inp["resource_url"],
            inp["sample_output"],
            name=inp["name"],
            category=inp["category"],
            mode="compact",
            client=make_client(challenge(extension="hollow")),
        )
    )
    strip = lambda d: {k: v for k, v in d.items() if k != "graded_at"}  # noqa: E731
    assert strip(result) == strip(FIX_COMPACT_OUTPUT_EXAMPLE)


def test_compact_example_under_settled_sample_limit():
    """Settled's extractor silently drops published output samples over
    ~1.5 KB serialized (measured 2026-10-25: RouteVerdict's 1,453 B
    sample kept and graded as_advertised; Token Verdict's 1,721 B and
    this route's 4,972 B full-mode sample dropped -> nothing_to_compare).
    The published compact sample must stay under the kept side."""
    import json

    assert len(json.dumps(FIX_COMPACT_OUTPUT_EXAMPLE).encode()) <= 1450


# --- discovery surfaces ------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_manifest_lists_both_routes_with_exact_prices(client):
    resp = client.get("/.well-known/x402")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "list-smith"
    grade_res, fix_res = body["resources"][0], body["resources"][1]
    assert grade_res["price"] == "$0.10"
    assert grade_res["accepts"][0]["amount"] == "100000"
    assert grade_res["accepts"][0]["payTo"] == OWNER
    assert grade_res["outputSchema"] == GRADE_OUTPUT_SCHEMA
    assert fix_res["price"] == "$0.50"
    assert fix_res["accepts"][0]["amount"] == "500000"
    assert fix_res["accepts"][0]["payTo"] == OWNER
    assert fix_res["outputSchema"] == FIX_OUTPUT_SCHEMA
    assert len(body["description"]) < 500
    assert client.get("/llms.txt").status_code == 200
    assert client.get("/skill.md").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/v1/grade" in paths and "/v1/fix" in paths
