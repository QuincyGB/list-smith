"""Offline grader tests — one fixture per defect class in the spec.

A MockTransport plays the target; the grade must catch each defect,
grade the healthy listing clean, and never include repaired content.
"""

import asyncio

import jsonschema

from app.discovery import GRADE_OUTPUT_SCHEMA
from app.grader import run_grade
from tests.fixtures import (
    PLACEHOLDER,
    TARGET_URL,
    challenge,
    make_client,
    unreachable_client,
)


def grade(url, client, **kw):
    return asyncio.run(run_grade(url, client=client, **kw))


def defect_names(result):
    return [d["name"] for d in result["defects"]]


def test_healthy_listing_grades_clean():
    result = grade(TARGET_URL, make_client(challenge()))
    jsonschema.validate(instance=result, schema=GRADE_OUTPUT_SCHEMA)
    assert result["defects"] == []
    assert result["score"] == 100
    assert result["reachable"] is True
    assert result["http_status"] == 402
    assert result["x402_valid"] is True
    assert result["extension_present"] is True
    assert result["sdk_validation"] == {
        "ran": True, "consistency_valid": True, "spec_valid": True, "errors": [],
    }
    assert result["schema_quality"]["score"] == 100
    assert result["schema_quality"]["hollow"] is False
    assert result["terms"]["price_usdc"] == "$0.02"
    assert result["fix_available"] is True


def test_grade_never_contains_repaired_content():
    result = grade(TARGET_URL, make_client(challenge(extension="hollow")))
    assert "package" not in result
    assert "extension" not in result  # the repaired extension is the fix product
    assert result["fix_available"] is True


def test_hollow_schema_caught():
    result = grade(TARGET_URL, make_client(challenge(extension="hollow")))
    jsonschema.validate(instance=result, schema=GRADE_OUTPUT_SCHEMA)
    assert result["schema_quality"]["hollow"] is True
    assert "hollow output schema (example only, no typed schema)" in defect_names(result)
    severities = {d["name"]: d["severity"] for d in result["defects"]}
    assert severities["hollow output schema (example only, no typed schema)"] == "major"
    assert result["score"] == 100 - 12


def test_long_description_caught():
    long_desc = "word " * 124  # 620 chars
    assert len(long_desc) == 620
    result = grade(TARGET_URL, make_client(challenge(description=long_desc)))
    names = defect_names(result)
    assert "description over 500 characters" in names
    # The use-when minor rides along (the filler has none) — both are real.
    assert 'no "use when" phrasing in description' in names
    assert result["score"] == 100 - 12 - 4


def test_wrong_asset_placeholder_caught():
    result = grade(TARGET_URL, make_client(challenge(asset=PLACEHOLDER)))
    names = defect_names(result)
    assert "wrong asset address (known placeholder)" in names
    severities = {d["name"]: d["severity"] for d in result["defects"]}
    assert severities["wrong asset address (known placeholder)"] == "critical"
    assert result["terms"]["asset"] == PLACEHOLDER


def test_other_asset_is_major_not_critical():
    other = "0x2222222222222222222222222222222222222222"
    result = grade(TARGET_URL, make_client(challenge(asset=other)))
    severities = {d["name"]: d["severity"] for d in result["defects"]}
    assert severities["asset is not Base USDC"] == "major"


def test_extension_failing_validation_caught():
    result = grade(TARGET_URL, make_client(challenge(extension="invalid")))
    assert result["sdk_validation"]["ran"] is True
    assert result["sdk_validation"]["consistency_valid"] is False
    assert result["sdk_validation"]["errors"]
    assert "extension fails SDK consistency validation" in defect_names(result)
    severities = {d["name"]: d["severity"] for d in result["defects"]}
    assert severities["extension fails SDK consistency validation"] == "critical"
    assert result["score"] == 100 - 30


def test_no_extension_caught():
    result = grade(TARGET_URL, make_client(challenge(extension=None)))
    jsonschema.validate(instance=result, schema=GRADE_OUTPUT_SCHEMA)
    assert result["extension_present"] is False
    assert result["sdk_validation"]["ran"] is False
    assert result["schema_quality"]["score"] is None
    assert "no Bazaar discovery extension" in defect_names(result)
    assert result["score"] == 100 - 30
    assert result["fix_available"] is True  # a challenge exists to repair against


def test_query_route_grades_clean():
    result = grade(TARGET_URL, make_client(challenge(extension="query")))
    assert result["defects"] == []
    assert result["score"] == 100


def test_unreachable_target():
    result = grade(TARGET_URL, unreachable_client())
    jsonschema.validate(instance=result, schema=GRADE_OUTPUT_SCHEMA)
    assert result["reachable"] is False
    assert result["http_status"] is None
    assert result["score"] == 0
    assert defect_names(result) == ["endpoint unreachable"]
    assert result["fix_available"] is False


def test_free_200_endpoint_is_not_an_x402_listing():
    import httpx

    result = grade(TARGET_URL, make_client(httpx.Response(200, json={"hello": "world"})))
    assert result["x402_valid"] is False
    assert "no valid x402 challenge" in defect_names(result)
    assert result["score"] <= 25
    assert result["fix_available"] is False


def test_sample_matching_published_schema_is_fine():
    result = grade(
        TARGET_URL, make_client(challenge()),
        sample_output={"verdict": "OK", "score": 5},
    )
    assert result["defects"] == []


def test_sample_mismatching_published_schema_caught():
    result = grade(
        TARGET_URL, make_client(challenge()),
        sample_output={"verdict": "OK", "score": "not-a-number"},
    )
    assert "sample output does not match published schema" in defect_names(result)


def test_missing_use_when_is_minor():
    desc = "Target Thing returns a verdict and a score for a thing."
    result = grade(TARGET_URL, make_client(challenge(description=desc)))
    assert defect_names(result) == ['no "use when" phrasing in description']
    assert result["score"] == 100 - 4


def test_bad_url_raises():
    try:
        grade("ftp://nope", make_client(challenge()))
    except ValueError as e:
        assert "http(s)" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
