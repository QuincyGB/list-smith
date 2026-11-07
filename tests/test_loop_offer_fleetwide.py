"""Loop block delivery layer + per-wallet loop memory (Next-20
#1 + #5, 2026-11-03): the verdict_row_loop block now attaches at
the x402 middleware delivery layer, so EVERY successful paid
delivery on every priced ListSmith route carries it — per-call
and Fleet-Pass funded alike — and a returning payer's block is
personalised from the nudge store's payer_history (their own
calls / distinct days / total spent, stall-wide and per-route,
plus an adaptive Recheck pack offer with identity-bound deep
links on StallRow). Error responses and unpaid 402 challenges
never carry it and are never recorded; 402 amounts are unchanged.

The per-call tests bypass the payment middleware in-process with
a passthrough that stamps the verified payment payload on
request.state exactly as the real middleware does (the paywall
contract itself is pinned by test_paywall.py and the unpaid test
below, which runs against the real middleware)."""

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.main as main
from app import delivery_ledger as delivery_ledger_svc
from app.fleet_pass import LEDGER_URL
from app.nudge import NudgeStore

WALLET = "0x1111111111111111111111111111111111111111"
WALLET2 = "0x2222222222222222222222222222222222222222"
OWNER = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
URL = "https://93.184.216.34/v1/verdict"
GRADE_UNITS = main.PRICE_UNITS["/v1/grade"]
FIX_UNITS = main.PRICE_UNITS["/v1/fix"]


def _payload(wallet):
    return SimpleNamespace(
        payload=SimpleNamespace(
            authorization=SimpleNamespace(**{"from": wallet}),
            signature="0xdeadbeef",
        )
    )


async def _fake_grade(resource_url, timeout=None, sample_output=None):
    return {"grade": "A", "resource_url": resource_url}


async def _fake_fix(resource_url, sample_output, name=None, category=None,
                    mode="full", timeout=None):
    return {"fixed": True, "resource_url": resource_url}


def _settled_client(monkeypatch, tmp_path, wallet=WALLET):
    """TestClient on a fresh store whose payment middleware is a
    passthrough stamping `wallet`'s verified payload — a settled
    per-call delivery, in-process."""
    fresh = NudgeStore(str(tmp_path / "smith.db"))
    monkeypatch.setattr(main, "_nudge_store", fresh)
    monkeypatch.setattr(main, "run_grade", _fake_grade)
    monkeypatch.setattr(main, "run_fix", _fake_fix)

    # Fleet delivery-ledger recording (2026-11-07) is stubbed like
    # the other delivery side effects in this harness; its own
    # behavior is pinned by tests/test_delivery_ledger_recording.py.
    async def _fake_record_delivery(payload):
        return {"recorded": True}

    monkeypatch.setattr(
        delivery_ledger_svc, "record_delivery", _fake_record_delivery
    )

    async def passthrough(request, call_next):
        request.state.payment_payload = _payload(wallet)
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough)
    return TestClient(main.app, raise_server_exceptions=False), fresh


def _grade(client):
    return client.post("/v1/grade", json={"resource_url": URL})


def _fix(client):
    return client.post(
        "/v1/fix", json={"resource_url": URL, "sample_output": {"a": 1}}
    )


# --- (a) settled deliveries on every priced route carry the block --------

def test_per_call_fix_delivery_carries_block(monkeypatch, tmp_path):
    client, _store = _settled_client(monkeypatch, tmp_path)
    r = _fix(client)
    assert r.status_code == 200, r.text
    block = r.json()["verdict_row_loop"]
    offers = {o["id"]: o for o in block["offers"]}
    assert offers["recheck_pack"]["price_usd"] == 1.0
    assert offers["fleet_pass"]["url"] == f"{LEDGER_URL}/v1/fleet-pass"
    # First-time payer: the static block, no personalised section.
    assert "returning_payer" not in block


def test_per_call_grade_delivery_carries_block(monkeypatch, tmp_path):
    client, _store = _settled_client(monkeypatch, tmp_path)
    r = _grade(client)
    assert r.status_code == 200, r.text
    assert "verdict_row_loop" in r.json()
    assert "returning_payer" not in r.json()["verdict_row_loop"]


def _install_ledger(monkeypatch):
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path.endswith("/debit"):
            return httpx.Response(200, json={"debit_id": "fpd_abc"})
        return httpx.Response(200, json={"ok": True})

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=10.0)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_fleet_pass_funded_delivery_carries_block(monkeypatch, tmp_path):
    fresh = NudgeStore(str(tmp_path / "smith.db"))
    monkeypatch.setattr(main, "_nudge_store", fresh)
    monkeypatch.setattr(main, "run_grade", _fake_grade)
    _install_ledger(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)

    r = client.post(
        "/v1/grade", json={"resource_url": URL},
        headers={"X-Fleet-Pass": "fpass_ok"},
    )
    assert r.status_code == 200, r.text
    assert "verdict_row_loop" in r.json()
    assert "returning_payer" not in r.json()["verdict_row_loop"]


# --- (b) a returning payer's delivery is personalised --------------------

def test_second_delivery_same_route_is_personalised(monkeypatch, tmp_path):
    client, store = _settled_client(monkeypatch, tmp_path)

    first = _grade(client)
    assert "returning_payer" not in first.json()["verdict_row_loop"]

    second = _grade(client)
    assert second.status_code == 200, second.text
    rp = second.json()["verdict_row_loop"]["returning_payer"]
    assert rp["identity"] == WALLET
    assert rp["identity_kind"] == "evm_wallet"
    assert rp["calls"] == 2
    assert rp["calls_this_route"] == 2
    assert rp["distinct_days"] == 1
    assert rp["total_spent_usd"] == 2 * GRADE_UNITS / 1_000_000

    offer = rp["adaptive_offer"]
    assert offer["credits"] == 250
    assert offer["price_usd"] == 1.0
    assert offer["deep_link"] == f"{LEDGER_URL}/v1/recheck/pack?payer={WALLET}"
    assert offer["webhook_deep_link"] == (
        f"{LEDGER_URL}/v1/recheck/webhooks?payer={WALLET}"
    )

    history = store.payer_history(WALLET)
    assert history["calls"] == 2
    assert history["total_units"] == 2 * GRADE_UNITS
    assert history["routes"]["/v1/grade"]["calls"] == 2


def test_returning_payer_across_routes_is_personalised(
    monkeypatch, tmp_path
):
    """Stall-wide memory: a grade then a fix — the fix delivery is
    personalised with calls=2 but calls_this_route=1, and the
    total spend sums both routes' enforced prices."""
    client, store = _settled_client(monkeypatch, tmp_path)
    _grade(client)

    second = _fix(client)
    assert second.status_code == 200, second.text
    rp = second.json()["verdict_row_loop"]["returning_payer"]
    assert rp["calls"] == 2
    assert rp["calls_this_route"] == 1
    assert rp["total_spent_usd"] == (GRADE_UNITS + FIX_UNITS) / 1_000_000
    assert store.payer_history(WALLET)["routes"]["/v1/fix"]["calls"] == 1


def test_other_wallet_still_gets_the_static_block(monkeypatch, tmp_path):
    client, _store = _settled_client(monkeypatch, tmp_path)
    _grade(client)
    _grade(client)

    async def passthrough2(request, call_next):
        request.state.payment_payload = _payload(WALLET2)
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough2)
    r = _grade(client)
    assert r.status_code == 200, r.text
    assert "returning_payer" not in r.json()["verdict_row_loop"]


def test_fleet_pass_repeat_delivery_is_personalised(monkeypatch, tmp_path):
    fresh = NudgeStore(str(tmp_path / "smith.db"))
    monkeypatch.setattr(main, "_nudge_store", fresh)
    monkeypatch.setattr(main, "run_grade", _fake_grade)
    _install_ledger(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    headers = {"X-Fleet-Pass": "fpass_repeat"}

    client.post("/v1/grade", json={"resource_url": URL}, headers=headers)
    second = client.post(
        "/v1/grade", json={"resource_url": URL}, headers=headers
    )
    assert second.status_code == 200, second.text
    rp = second.json()["verdict_row_loop"]["returning_payer"]
    # The pass ledger records no buyer wallet: the credential id is
    # the payer identity available, and the deep link pre-binds it.
    assert rp["identity"] == "fleet-pass:fpass_repeat"
    assert rp["identity_kind"] == "funding_credential"
    assert rp["calls"] == 2 and rp["calls_this_route"] == 2


# --- (c) 402 challenges and error responses carry no block ---------------

def test_unpaid_challenges_carry_no_block_and_record_nothing(
    monkeypatch, tmp_path
):
    fresh = NudgeStore(str(tmp_path / "smith.db"))
    monkeypatch.setattr(main, "_nudge_store", fresh)
    client = TestClient(main.app, raise_server_exceptions=False)

    for path, body, units in (
        ("/v1/grade", {"resource_url": URL}, GRADE_UNITS),
        (
            "/v1/fix",
            {"resource_url": URL, "sample_output": {"a": 1}},
            FIX_UNITS,
        ),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 402, (path, r.text)
        assert "verdict_row_loop" not in r.text
        challenge = json.loads(base64.b64decode(r.headers["payment-required"]))
        accept = challenge["accepts"][0]
        assert accept["amount"] == str(units), path
        assert accept["payTo"] == OWNER, path
    assert GRADE_UNITS == 100000 and FIX_UNITS == 500000
    assert fresh.payer_history(WALLET) is None


def test_error_responses_carry_no_block(monkeypatch, tmp_path):
    client, store = _settled_client(monkeypatch, tmp_path)

    # Validation error on a priced route (settled passthrough).
    r = client.post("/v1/grade", json={})
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text

    # Handler failure (bad target): 422, nothing delivered.
    async def failing(resource_url, timeout=None, sample_output=None):
        raise ValueError("resource_url must be an http(s) URL")

    monkeypatch.setattr(main, "run_grade", failing)
    r = _grade(client)
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text

    assert store.payer_history(WALLET) is None


def test_oversized_delivery_is_not_rewritten_but_is_recorded(
    monkeypatch, tmp_path
):
    """The streaming guard: a JSON response advertising a body over
    the injection cap passes through untouched — no buffering, no
    block — while the settled delivery still lands in payer
    memory."""
    fresh = NudgeStore(str(tmp_path / "smith.db"))
    monkeypatch.setattr(main, "_nudge_store", fresh)
    big = JSONResponse(
        {"dataset": "x"},
        headers={"content-length": str(main._LOOP_MAX_JSON_BYTES + 1)},
    )
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/v1/grade"),
        state=SimpleNamespace(payment_payload=_payload(WALLET)),
    )
    out = asyncio.run(main._attach_loop_offer(request, big))
    assert out is big
    assert fresh.payer_history(WALLET)["calls"] == 1


# --- payer memory store semantics -----------------------------------------

def test_store_counts_calls_days_and_spend_across_routes(tmp_path):
    store = NudgeStore(str(tmp_path / "smith.db"))
    day0 = 1_700_000_000.0

    snap = store.record_payer_delivery(WALLET, "/v1/grade", GRADE_UNITS, day0)
    assert snap["prior_calls"] == 0
    assert snap["calls"] == 1 and snap["distinct_days"] == 1

    # Same day, another route: stall-wide days stay 1, spend sums.
    snap = store.record_payer_delivery(
        WALLET, "/v1/fix", FIX_UNITS, day0 + 60
    )
    assert snap["prior_calls"] == 1
    assert snap["calls"] == 2 and snap["distinct_days"] == 1
    assert snap["total_units"] == GRADE_UNITS + FIX_UNITS

    # Next UTC day: a new distinct day, per-route and stall-wide.
    snap = store.record_payer_delivery(
        WALLET, "/v1/grade", GRADE_UNITS, day0 + 86_400
    )
    assert snap["calls"] == 3 and snap["distinct_days"] == 2
    assert snap["routes"]["/v1/grade"]["calls"] == 2
    assert store.payer_history(WALLET2) is None


def test_price_units_derived_from_settings():
    assert main._price_units(main.settings.grade_price_usdc) == GRADE_UNITS
    assert main._price_units(main.settings.fix_price_usdc) == FIX_UNITS
