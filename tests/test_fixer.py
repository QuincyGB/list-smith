"""Offline fixer tests: every broken fixture must come back as a
package that passes BOTH x402 SDK validators, and the honest-failure
paths (unreachable, no challenge, un-inferable sample, null sample)
must return ok=False with a named reason — never a broken package.
"""

import asyncio

import jsonschema
from x402.extensions.bazaar import (
    validate_discovery_extension,
    validate_discovery_extension_spec,
)

from app.discovery import FIX_OUTPUT_SCHEMA
from app.fixer import run_fix
from tests.fixtures import (
    PLACEHOLDER,
    TARGET_URL,
    challenge,
    make_client,
    unreachable_client,
)

SAMPLE = {
    "verdict": "OK",
    "score": 12,
    "amount": "20000",
    "detail": {"flags": ["fresh"], "note": None},
    "history": [{"ts": 1, "v": 2.5}, {"ts": 2}],
}


def fix(url, client, sample=SAMPLE, **kw):
    return asyncio.run(run_fix(url, sample, client=client, **kw))


def assert_package_valid(result):
    jsonschema.validate(instance=result, schema=FIX_OUTPUT_SCHEMA)
    assert result["ok"] is True
    assert result["failure"] is None
    pkg = result["package"]
    assert pkg["inferred_from"] == "single_sample"
    ext = pkg["extension"]
    consistency = validate_discovery_extension(ext)
    assert consistency.valid, consistency.errors
    spec = validate_discovery_extension_spec(ext)
    assert spec.valid, spec.errors
    assert pkg["validation"] == {"consistency_valid": True, "spec_valid": True}
    assert len(pkg["description"]) < 500
    assert "use when" in pkg["description"].lower()
    # The sample validates against the inferred schema — and every
    # sampled field is in it (nothing dropped, nothing invented).
    out_schema = ext["info"]["output"]["schema"]
    jsonschema.validate(instance=SAMPLE, schema=out_schema)
    assert set(out_schema["properties"]) == set(SAMPLE)
    return pkg


def test_fix_hollow_listing():
    result = fix(TARGET_URL, make_client(challenge(extension="hollow")))
    pkg = assert_package_valid(result)
    assert result["score_before"] == 88  # the hollow grade (100 - 12)
    assert any(d["name"].startswith("hollow output schema") for d in result["defects"])
    # Null-in-sample note is present and the field is untyped.
    assert any("null in the sample" in n for n in result["notes"])
    note_schema = pkg["extension"]["info"]["output"]["schema"]["properties"]["detail"]
    assert note_schema["properties"]["note"] == {}
    # Numeric-looking string stayed a string.
    amount = pkg["extension"]["info"]["output"]["schema"]["properties"]["amount"]
    assert amount == {"type": "string"}


def test_fix_no_extension_listing():
    result = fix(TARGET_URL, make_client(challenge(extension=None)), name="Target Thing")
    pkg = assert_package_valid(result)
    assert result["score_before"] == 70
    assert "Target Thing" in pkg["description"]
    # No published input shape -> the fallback note must be present.
    assert any("No input shape was published" in n for n in result["notes"])


def test_fix_invalid_extension_listing():
    result = fix(TARGET_URL, make_client(challenge(extension="invalid")))
    assert_package_valid(result)
    assert result["score_before"] == 70  # consistency failure is critical (30)


def test_fix_wrong_asset_listing_warns_in_instructions():
    result = fix(TARGET_URL, make_client(challenge(asset=PLACEHOLDER)))
    pkg = assert_package_valid(result)
    assert "ASSET WARNING" in pkg["instructions"]
    # The snippet mirrors the live challenge exactly (agreement is what
    # validators check) — the warning, not a silent swap, is the repair.
    assert pkg["well_known_snippet"]["accepts"][0]["asset"] == PLACEHOLDER


def test_fix_long_description_listing_gets_short_one():
    result = fix(TARGET_URL, make_client(challenge(description="word " * 124)))
    pkg = assert_package_valid(result)
    assert len(pkg["description"]) < 500


def test_fix_healthy_listing_still_packages():
    result = fix(TARGET_URL, make_client(challenge()))
    assert_package_valid(result)
    assert result["score_before"] == 100
    assert result["defects"] == []


def test_fix_query_route_listing():
    result = fix(TARGET_URL, make_client(challenge(extension="query")),
                 sample={"temp": 62, "city": "sf"})
    jsonschema.validate(instance=result, schema=FIX_OUTPUT_SCHEMA)
    assert result["ok"] is True
    ext = result["package"]["extension"]
    assert ext["info"]["input"]["queryParams"] == {"thing_id": "abc"}
    assert validate_discovery_extension(ext).valid
    assert validate_discovery_extension_spec(ext).valid


def test_fix_snippets_are_consistent():
    result = fix(TARGET_URL, make_client(challenge(extension="hollow")))
    pkg = result["package"]
    wk = pkg["well_known_snippet"]
    assert wk["resource"] == TARGET_URL
    assert wk["method"] == "POST"
    assert wk["accepts"][0]["amount"] == "20000"
    assert wk["accepts"][0]["payTo"] == "0x1111111111111111111111111111111111111111"
    assert wk["outputSchema"] == pkg["extension"]["info"]["output"]["schema"]
    op = pkg["openapi_snippet"]["paths"]["/v1/thing"]["post"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == wk["outputSchema"]
    assert "402" in op["responses"]
    assert "extensions=" in pkg["instructions"]


def test_fix_default_mode_is_full():
    result = fix(TARGET_URL, make_client(challenge(extension="hollow")))
    assert result["mode"] == "full"
    assert "extension" in result["package"]


def test_fix_compact_mode_returns_repaired_fields_only():
    compact = fix(TARGET_URL, make_client(challenge(extension="hollow")), mode="compact")
    jsonschema.validate(instance=compact, schema=FIX_OUTPUT_SCHEMA)
    assert compact["ok"] is True
    assert compact["mode"] == "compact"
    assert compact["score_before"] == 88
    pkg = compact["package"]
    assert set(pkg) == {"inferred_from", "description", "info", "changes", "validation"}
    assert pkg["validation"] == {"consistency_valid": True, "spec_valid": True}
    # The compact info is exactly the full mode's validated extension
    # info, and the description is the same generated one — compact is
    # the same repair, minus the snippet bundle.
    full = fix(TARGET_URL, make_client(challenge(extension="hollow")))
    assert pkg["info"] == full["package"]["extension"]["info"]
    assert pkg["description"] == full["package"]["description"]
    # Complete product: the sample validates against the inferred
    # schema the compact response carries, and changes say what moved.
    jsonschema.validate(instance=SAMPLE, schema=pkg["info"]["output"]["schema"])
    assert set(pkg["info"]["output"]["schema"]["properties"]) == set(SAMPLE)
    assert any(c.startswith("description rewritten") for c in pkg["changes"])
    assert any(c.startswith("output schema inferred") for c in pkg["changes"])
    # The null-in-sample caveat survives in compact notes.
    assert any("null in the sample" in n for n in compact["notes"])


def test_fix_compact_mode_failure_is_honest():
    result = fix(TARGET_URL, unreachable_client(), mode="compact")
    jsonschema.validate(instance=result, schema=FIX_OUTPUT_SCHEMA)
    assert result["ok"] is False
    assert result["package"] is None
    assert result["mode"] == "compact"
    assert "unreachable" in result["failure"]


def test_fix_invalid_mode_rejected():
    import pytest

    with pytest.raises(ValueError):
        fix(TARGET_URL, make_client(challenge()), mode="tiny")


def test_fix_unreachable_is_honest_failure():
    result = fix(TARGET_URL, unreachable_client())
    jsonschema.validate(instance=result, schema=FIX_OUTPUT_SCHEMA)
    assert result["ok"] is False
    assert result["package"] is None
    assert "unreachable" in result["failure"]


def test_fix_free_endpoint_is_honest_failure():
    import httpx

    result = fix(TARGET_URL, make_client(httpx.Response(200, json={"hi": 1})))
    assert result["ok"] is False
    assert "no valid x402 challenge" in result["failure"]


def test_fix_null_sample_is_honest_failure():
    result = fix(TARGET_URL, make_client(challenge()), sample=None)
    assert result["ok"] is False
    assert "sample_output is required" in result["failure"]


def test_fix_overdeep_sample_is_honest_failure():
    deep = current = {}
    for _ in range(10):
        current["n"] = {}
        current = current["n"]
    result = fix(TARGET_URL, make_client(challenge()), sample=deep)
    assert result["ok"] is False
    assert "could not infer an output schema" in result["failure"]


def test_fix_oversized_sample_is_honest_failure():
    big = {"blob": "x" * 300_000}
    result = fix(TARGET_URL, make_client(challenge()), sample=big)
    assert result["ok"] is False
    assert "inference limit" in result["failure"]
