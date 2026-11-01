"""Loop offer block (Play 1, organic-buyer plan 2026-11-01): every
paid /v1/grade and /v1/fix delivery carries one additive top-level
``verdict_row_loop`` field quoting the fleet's repeat products
exactly as StallRow's enforcing code states them — the Fleet Pass
covers both of this stall's routes at listed prices, so the offer
belongs on both — and error responses never carry it."""

import asyncio
import json

import app.main as main
from app import loop_offer
from app.fleet_pass import LEDGER_URL

URL = "https://93.184.216.34/v1/verdict"


def _offers_by_id(block):
    return {o["id"]: o for o in block["offers"]}


def test_block_terms_match_stallrow_enforcement():
    block = loop_offer.loop_offer()
    offers = _offers_by_id(block)
    assert block["discovery"] == "/.well-known/verdict-row.json"

    fpass = offers["fleet_pass"]
    assert fpass["url"] == f"{LEDGER_URL}/v1/fleet-pass"
    assert fpass["price_usd"] == 5.0
    assert fpass["terms"]["ttl_days"] == 90
    assert "$0.10" in fpass["what_it_is"] and "$0.50" in fpass["what_it_is"]

    assert offers["fleet_pass_20"]["price_usd"] == 20.0

    pack = offers["recheck_pack"]
    assert pack["price_usd"] == 1.0
    assert pack["terms"]["credits"] == 250
    assert pack["terms"]["ttl_days"] == 90
    assert pack["terms"]["per_check_usd"] == 1.0 / 250
    assert pack["terms"]["per_call_price_usd"] == 0.005

    hook = offers["recheck_change_webhook"]
    assert hook["price_usd"] == 0
    assert "pack_id" in hook["terms"]["gate"]

    assert offers["recheck_feed"]["price_usd"] == 0


def test_grade_delivery_carries_the_block(monkeypatch):
    async def fake(resource_url, timeout=None, sample_output=None):
        return {"grade": "A", "resource_url": resource_url}

    monkeypatch.setattr(main, "run_grade", fake)
    out = asyncio.run(main.grade(main.GradeRequest(resource_url=URL)))
    assert out["grade"] == "A"  # original fields untouched
    block = out["verdict_row_loop"]
    assert _offers_by_id(block)["fleet_pass"]["price_usd"] == 5.0


def test_fix_delivery_carries_the_block(monkeypatch):
    async def fake(resource_url, sample_output, name=None, category=None,
                   mode="full", timeout=None):
        return {"fixed": True, "resource_url": resource_url}

    monkeypatch.setattr(main, "run_fix", fake)
    req = main.FixRequest(resource_url=URL, sample_output={"ok": True})
    out = asyncio.run(main.fix(req))
    assert out["fixed"] is True
    assert "verdict_row_loop" in out


def test_failed_delivery_has_no_block(monkeypatch):
    async def fake(resource_url, timeout=None, sample_output=None):
        raise ValueError("resource_url must be an http(s) URL")

    monkeypatch.setattr(main, "run_grade", fake)
    out = asyncio.run(main.grade(main.GradeRequest(resource_url=URL)))
    assert out.status_code == 422
    assert "verdict_row_loop" not in out.body.decode()
    assert json.loads(out.body)["error"]


def test_attach_passes_non_dicts_through():
    sentinel = object()
    assert loop_offer.attach_loop_offer(sentinel) is sentinel
