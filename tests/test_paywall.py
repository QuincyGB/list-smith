"""The paywall contract: unpaid calls to either route get a 402 whose
accepts carry ListSmith's exact terms ($0.10 grade / $0.50 fix, USDC on
Base, owner payTo) and the full Bazaar discovery extension; free routes
stay open.

Offline: the middleware builds the challenge locally; no facilitator
settlement happens for an unpaid call.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
OWNER = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _challenge(response):
    raw = response.headers.get("payment-required")
    assert raw, "402 response must carry a payment-required header"
    return json.loads(base64.b64decode(raw))


def _assert_accepts(challenge, amount):
    assert challenge["x402Version"] == 2
    accepts = challenge["accepts"]
    assert len(accepts) == 1
    a = accepts[0]
    assert a["scheme"] == "exact"
    assert a["network"] == "eip155:8453"
    assert a["asset"] == USDC
    assert a["amount"] == amount
    assert a["payTo"] == main.settings.pay_to == OWNER


def test_unpaid_grade_is_paywalled_at_10_cents(client):
    r = client.post("/v1/grade", json={"resource_url": "https://target.example.com/v1/thing"})
    assert r.status_code == 402
    _assert_accepts(_challenge(r), "100000")  # $0.10 in USDC smallest units


def test_unpaid_fix_is_paywalled_at_50_cents(client):
    r = client.post(
        "/v1/fix",
        json={"resource_url": "https://target.example.com/v1/thing", "sample_output": {"a": 1}},
    )
    assert r.status_code == 402
    _assert_accepts(_challenge(r), "500000")  # $0.50 in USDC smallest units


def test_402s_carry_full_bazaar_extensions(client):
    grade_r = client.post("/v1/grade", json={"resource_url": "https://target.example.com/v1/thing"})
    grade_out = _challenge(grade_r)["extensions"]["bazaar"]["info"]["output"]
    assert "schema" in grade_out
    assert "defects" in grade_out["schema"]["properties"]
    assert "score" in grade_out["schema"]["properties"]

    fix_r = client.post(
        "/v1/fix",
        json={"resource_url": "https://target.example.com/v1/thing", "sample_output": {"a": 1}},
    )
    fix_out = _challenge(fix_r)["extensions"]["bazaar"]["info"]["output"]
    assert "schema" in fix_out
    assert "package" in fix_out["schema"]["properties"]
    assert "ok" in fix_out["schema"]["properties"]


def test_free_routes_stay_open(client):
    assert client.get("/health").status_code == 200
    quote = client.get("/quote")
    assert quote.status_code == 200
    body = quote.json()
    assert body["endpoints"]["/v1/grade"]["price"] == "$0.10"
    assert body["endpoints"]["/v1/fix"]["price"] == "$0.50"
    assert body["payTo"] == OWNER
    assert client.get("/").status_code == 200
