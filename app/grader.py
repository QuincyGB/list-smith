"""The /v1/grade diagnosis — what is broken in a listing's discovery
metadata, itemized, with the real x402 SDK validators as the referee.

Checks (all observation-only; the target is never paid):
  reachability and challenge validity; the primary accept's terms
  (payTo / asset / network present; the asset is the real Base USDC
  contract, not the placeholder our own Token Verdict manifest once
  shipped); Bazaar extension presence; BOTH SDK validators
  (validate_discovery_extension — info-vs-schema consistency, the one
  that fails facilitator-side — and validate_discovery_extension_spec);
  schema quality via RouteVerdict's scorer (schema_quality.py, reused
  verbatim — the hollow {type, example} detector); description length
  (over 500 chars breaks CDP payments) and the "use when" phrasing the
  Bazaar's ranking weighs; and, when the buyer supplies a sample output,
  whether it actually matches the published output schema.

NO repaired content is produced here — that is what /v1/fix sells.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import jsonschema

from .probe import inspect_target
from .schema_quality import evaluate_schema_quality

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# The placeholder Token Verdict's manifest once advertised for Base —
# the canonical wrong-asset defect (~/workspace/platform-signups/
# x402-bazaar.md, gap #1).
KNOWN_PLACEHOLDER_ASSETS = {"0x3600000000000000000000000000000000000000"}

_WEIGHTS = {"critical": 30, "major": 12, "minor": 4}

_MISSING = object()  # sample_output not supplied (distinct from JSON null)

# schema_quality deductions ListSmith re-reports as its own defects.
# Description deductions are skipped — grade checks descriptions itself.
_QUALITY_DEFECTS = {
    "output section missing": ("missing output section", "critical"),
    "hollow output (example only, no schema)": (
        "hollow output schema (example only, no typed schema)",
        "major",
    ),
    "output schema types no real fields": (
        "output schema types no real fields",
        "major",
    ),
    "untyped output properties": ("untyped output properties", "minor"),
    "no output example": ("no output example", "minor"),
    "input section missing": ("missing input section (no example, no schema)", "major"),
    "no example input": ("no example input", "minor"),
}


def _defect(name: str, severity: str, detail: str) -> dict:
    return {"name": name, "severity": severity, "detail": detail}


def grade_inspection(inspection: dict, sample_output: object = _MISSING) -> dict:
    """Grade one inspected target. Pure given the inspection (plus the
    SDK validators) — the network work lives in probe.inspect_target."""
    probe = inspection["probe"]
    challenge = inspection["challenge"]
    primary = inspection["primary"]
    terms = inspection["terms"]
    bazaar = challenge.bazaar

    defects: list[dict] = []
    sdk_validation = {"ran": False, "consistency_valid": None, "spec_valid": None, "errors": []}

    if not probe["alive"]:
        defects.append(
            _defect(
                "endpoint unreachable",
                "critical",
                f"No HTTP response at all — probe failed: {probe['error']}.",
            )
        )
        score = 0
    elif not challenge.valid or primary is None:
        defects.append(
            _defect(
                "no valid x402 challenge",
                "critical",
                challenge.error
                or "The response is not a parseable x402 payment challenge.",
            )
        )
        score = 10  # it answered; that is all that can be said for it
    else:
        # --- terms ---------------------------------------------------
        if primary.pay_to is None:
            defects.append(
                _defect("no payTo in challenge", "critical",
                        "The accepts entry names no payTo wallet — money cannot be routed to the seller.")
            )
        if primary.asset is None:
            defects.append(
                _defect("no asset in challenge", "major",
                        "The accepts entry names no asset contract.")
            )
        if primary.network is None:
            defects.append(
                _defect("no network in challenge", "major",
                        "The accepts entry names no network.")
            )
        if primary.network == "eip155:8453" and primary.asset:
            if primary.asset.lower() in KNOWN_PLACEHOLDER_ASSETS:
                defects.append(
                    _defect(
                        "wrong asset address (known placeholder)",
                        "critical",
                        f"The Base accept names asset {primary.asset} — the known placeholder "
                        f"address, not USDC ({BASE_USDC}). Directory validators read the "
                        "challenge against the manifest and buyers cannot settle USDC against it.",
                    )
                )
            elif primary.asset.lower() != BASE_USDC.lower():
                defects.append(
                    _defect(
                        "asset is not Base USDC",
                        "major",
                        f"The Base accept names asset {primary.asset}, not the USDC contract "
                        f"({BASE_USDC}). Bazaar pricing assumes USDC — if another token is "
                        "intentional, the listing must say so.",
                    )
                )

        # --- discovery extension --------------------------------------
        schema_quality = evaluate_schema_quality(bazaar, challenge.resource_description)
        if bazaar is None:
            defects.append(
                _defect(
                    "no Bazaar discovery extension",
                    "critical",
                    "The 402 challenge carries no extensions.bazaar — the listing has no "
                    "catalog card, so the Bazaar and the indexes fed by it cannot describe "
                    "or rank this route.",
                )
            )
        else:
            from x402.extensions.bazaar import (
                validate_discovery_extension,
                validate_discovery_extension_spec,
            )

            consistency = validate_discovery_extension(bazaar)
            spec = validate_discovery_extension_spec(bazaar)
            errors = list(consistency.errors or []) + list(spec.errors or [])
            sdk_validation = {
                "ran": True,
                "consistency_valid": bool(consistency.valid),
                "spec_valid": bool(spec.valid),
                "errors": errors,
            }
            if not consistency.valid:
                defects.append(
                    _defect(
                        "extension fails SDK consistency validation",
                        "critical",
                        "validate_discovery_extension rejects the extension (info does not "
                        "match its own schema): "
                        + "; ".join(consistency.errors or ["unspecified"]),
                    )
                )
            if not spec.valid:
                defects.append(
                    _defect(
                        "extension fails SDK spec validation",
                        "major",
                        "validate_discovery_extension_spec rejects the extension: "
                        + "; ".join(spec.errors or ["unspecified"]),
                    )
                )
            meta_schema = bazaar.get("schema")
            if not isinstance(meta_schema, dict) or not meta_schema:
                defects.append(
                    _defect(
                        "extension has no meta-schema",
                        "major",
                        "The extension carries no extension-level schema — facilitators "
                        "validate info against it, and its absence leaves the listing "
                        "unverifiable.",
                    )
                )
            for d in schema_quality["deductions"]:
                mapped = _QUALITY_DEFECTS.get(d["name"])
                if mapped is not None:
                    defects.append(_defect(mapped[0], mapped[1], d["detail"]))
            # Example fields the schema never mentions — schema pieces
            # missing in the other direction.
            info = bazaar.get("info") if isinstance(bazaar.get("info"), dict) else {}
            output = info.get("output") if isinstance(info.get("output"), dict) else {}
            out_schema = output.get("schema") if isinstance(output, dict) else None
            example = output.get("example") if isinstance(output, dict) else None
            if isinstance(out_schema, dict) and isinstance(example, dict):
                props = out_schema.get("properties")
                if isinstance(props, dict):
                    uncovered = sorted(k for k in example if k not in props)
                    if uncovered:
                        defects.append(
                            _defect(
                                "example fields missing from output schema",
                                "minor",
                                "The output example carries field(s) the schema never "
                                "declares: " + ", ".join(uncovered) + ".",
                            )
                        )
            # A supplied sample that the published schema rejects.
            if sample_output is not _MISSING and isinstance(out_schema, dict):
                try:
                    jsonschema.validate(instance=sample_output, schema=out_schema)
                except jsonschema.ValidationError as e:
                    defects.append(
                        _defect(
                            "sample output does not match published schema",
                            "major",
                            "The sample output supplied with this grade fails the "
                            f"endpoint's own published output schema: {e.message}",
                        )
                    )
                except jsonschema.SchemaError:
                    pass  # a broken schema is already reported above

        # --- resource description --------------------------------------
        description = challenge.resource_description
        if description is None:
            defects.append(
                _defect("no resource description", "minor",
                        "The 402 resource object has no description — the text a buying "
                        "agent reads to decide.")
            )
        else:
            if len(description) > 500:
                defects.append(
                    _defect(
                        "description over 500 characters",
                        "major",
                        f"Description is {len(description)} chars; the CDP facilitator "
                        "rejects payments for resources described in over 500.",
                    )
                )
            lowered = description.lower()
            if "use when" not in lowered and "use this when" not in lowered:
                defects.append(
                    _defect(
                        'no "use when" phrasing in description',
                        "minor",
                        "The description never says when to use the route — the quality "
                        "signal the Bazaar's ranking weighs and 94% of listings miss.",
                    )
                )

        score = max(0, 100 - sum(_WEIGHTS[d["severity"]] for d in defects))
    schema_quality_out = (
        evaluate_schema_quality(bazaar, challenge.resource_description)
        if challenge.valid and primary is not None
        else {"extension_present": bool(bazaar), "hollow": None, "score": None, "deductions": []}
    )

    return {
        "resource_url": inspection["resource_url"],
        "graded_at": datetime.now(timezone.utc).isoformat(),
        "reachable": bool(probe["alive"]),
        "http_status": probe["status"],
        "x402_valid": bool(challenge.valid),
        "terms": terms,
        "extension_present": bazaar is not None,
        "sdk_validation": sdk_validation,
        "schema_quality": schema_quality_out,
        "defects": defects,
        "score": score if probe["alive"] else 0,
        "fix_available": bool(challenge.valid and primary is not None),
    }


async def run_grade(
    resource_url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
    sample_output: object = _MISSING,
) -> dict:
    inspection = await inspect_target(resource_url, client=client, timeout=timeout)
    return grade_inspection(inspection, sample_output=sample_output)
