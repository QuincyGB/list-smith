"""Agent-discovery surfaces: x402 manifest, skill.md, llms.txt.

Indexers (x402scan, Bazaar, agentic.market) read these alongside the
live 402 responses, so they must stay consistent with main.py pricing.
ListSmith sells listing quality, so its own metadata is the showroom:
BOTH routes carry full typed output schemas (never the hollow
{"type", "example"} shape it grades down), the shipped examples are
real outputs of the grader/fixer, and every description stays under
the 500-char CDP limit — all pinned by tests.
"""

from __future__ import annotations

from .config import Settings
from .nudge import nudge_extension

# x402 v2 "exact"-scheme asset for USDC on Base mainnet (6 decimals) — the
# real ERC-20 contract address, matching the asset the x402 SDK puts in
# the live 402 payment-required challenge. The manifest must mirror the
# challenge exactly or directory validators reject the mismatch.
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# x402scan ownership proof: an EIP-191 personal_sign of this service's bare
# origin URL ("https://list-smith-production.up.railway.app") by the payTo
# wallet, signed by the owner on 2026-10-24. Published in the manifest below
# and in the OpenAPI x-discovery / x-agentcash-provenance root keys (see
# main.py) so x402scan can verify control of the payment address. This is
# publish-and-verify: the proofs must stay published permanently — removing
# them un-verifies the x402scan listing. DO NOT REMOVE.
OWNERSHIP_PROOFS = [
    "0x2108d014fb7fe1d2309336443fd3d3b154ef1bff31320ea347f11fa816d49fb01c4809bd8dd7dbe3513772ca2578e0e753c3de3b8ad4341f84b94499fc202b8e1b"
]


def _price_to_units(price_usdc: str) -> str:
    """'$0.50' -> '500000' (USDC smallest units, 6 decimals)."""
    return str(int(round(float(price_usdc.replace("$", "").strip()) * 1_000_000)))


# --- input schemas -------------------------------------------------------------

GRADE_INPUT_SCHEMA = {
    "type": "object",
    "required": ["resource_url"],
    "properties": {
        "resource_url": {
            "type": "string",
            "description": "Full URL of the x402 resource to grade, e.g. https://service.example.com/v1/endpoint",
        },
        "sample_output": {
            "description": "Optional: one real response body from the endpoint. When supplied, the grade also checks it against the endpoint's published output schema.",
        },
    },
}

FIX_INPUT_SCHEMA = {
    "type": "object",
    "required": ["resource_url", "sample_output"],
    "properties": {
        "resource_url": {
            "type": "string",
            "description": "Full URL of the x402 resource to repair, e.g. https://service.example.com/v1/endpoint",
        },
        "sample_output": {
            "description": "One real response body from the endpoint (required). The repaired output schema is inferred from this sample only — nothing is invented.",
        },
        "name": {
            "type": "string",
            "description": "Optional display name for the generated description; derived from the URL when omitted.",
        },
        "category": {
            "type": "string",
            "description": "Optional category (e.g. token-risk, search, data, media, monitoring) used to phrase the description's use-when sentence.",
        },
        "mode": {
            "type": "string",
            "enum": ["full", "compact"],
            "description": "Optional response mode, default 'full'. 'full' returns the complete package (extension with meta-schema, OpenAPI + well-known snippets, instructions). 'compact' returns the repaired fields only (description, extension info, changes) in a smaller response; the package is validated the same way in both modes.",
        },
    },
}

# --- shared output pieces -------------------------------------------------------

_DEFECT = {
    "type": "object",
    "required": ["name", "severity", "detail"],
    "properties": {
        "name": {"type": "string"},
        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
        "detail": {"type": "string"},
    },
}

_TERMS = {
    "type": "object",
    "required": ["found", "price_usdc", "amount_units", "network", "asset", "pay_to", "scheme", "x402_version"],
    "properties": {
        "found": {"type": "boolean"},
        "price_usdc": {"type": ["string", "null"]},
        "amount_units": {"type": ["string", "null"]},
        "network": {"type": ["string", "null"]},
        "asset": {"type": ["string", "null"]},
        "pay_to": {"type": ["string", "null"]},
        "scheme": {"type": ["string", "null"]},
        "x402_version": {"type": ["integer", "null"]},
    },
}

_SDK_VALIDATION = {
    "type": "object",
    "required": ["ran", "consistency_valid", "spec_valid", "errors"],
    "properties": {
        "ran": {"type": "boolean", "description": "False when there was no extension to validate"},
        "consistency_valid": {"type": ["boolean", "null"]},
        "spec_valid": {"type": ["boolean", "null"]},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
}

_SCHEMA_QUALITY = {
    "type": "object",
    "required": ["extension_present", "hollow", "score", "deductions"],
    "properties": {
        "extension_present": {"type": "boolean"},
        "hollow": {"type": ["boolean", "null"]},
        "score": {"type": ["integer", "null"]},
        "deductions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "points", "detail"],
                "properties": {
                    "name": {"type": "string"},
                    "points": {"type": "integer", "minimum": 0},
                    "detail": {"type": "string"},
                },
            },
        },
    },
}

# --- grade output -----------------------------------------------------------------

GRADE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "resource_url", "graded_at", "reachable", "http_status", "x402_valid",
        "terms", "extension_present", "sdk_validation", "schema_quality",
        "defects", "score", "fix_available",
    ],
    "properties": {
        "resource_url": {"type": "string"},
        "graded_at": {"type": "string", "description": "ISO-8601 timestamp of the probe"},
        "reachable": {"type": "boolean"},
        "http_status": {"type": ["integer", "null"]},
        "x402_valid": {"type": "boolean"},
        "terms": _TERMS,
        "extension_present": {"type": "boolean"},
        "sdk_validation": _SDK_VALIDATION,
        "schema_quality": _SCHEMA_QUALITY,
        "defects": {"type": "array", "items": _DEFECT},
        "score": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "Listing grade: 100 (clean) to 0, from the itemized defects"},
        "fix_available": {"type": "boolean",
                          "description": "True when the endpoint presents a valid challenge /v1/fix can repair against"},
    },
}

# --- fix output -------------------------------------------------------------------

_PACKAGE = {
    "type": "object",
    "required": [
        "inferred_from", "description", "extension", "openapi_snippet",
        "well_known_snippet", "instructions", "validation",
    ],
    "properties": {
        "inferred_from": {"type": "string", "const": "single_sample"},
        "description": {"type": "string", "description": "Under 500 chars, with a use-when sentence"},
        "extension": {"type": "object", "description": "The repaired Bazaar discovery extension (passed both SDK validators)"},
        "openapi_snippet": {"type": "object"},
        "well_known_snippet": {"type": "object"},
        "instructions": {"type": "string"},
        "validation": {
            "type": "object",
            "required": ["consistency_valid", "spec_valid"],
            "properties": {
                "consistency_valid": {"type": "boolean"},
                "spec_valid": {"type": "boolean"},
            },
        },
    },
}

_VALIDATION = {
    "type": "object",
    "required": ["consistency_valid", "spec_valid"],
    "properties": {
        "consistency_valid": {"type": "boolean"},
        "spec_valid": {"type": "boolean"},
    },
}

# The compact package (mode="compact"): the repaired fields only. It is
# derived from the full package after the full extension passed both
# SDK validators, so `validation` reports that same gate.
_COMPACT_PACKAGE = {
    "type": "object",
    "required": ["inferred_from", "description", "info", "changes", "validation"],
    "properties": {
        "inferred_from": {"type": "string", "const": "single_sample"},
        "description": {"type": "string", "description": "Under 500 chars, with a use-when sentence"},
        "info": {"type": "object",
                 "description": "The repaired Bazaar extension info: input carried from the listing, output carrying the sample and the full typed schema inferred from it. Set it as your extension's info and regenerate the extension meta-schema with your x402 SDK (full mode returns it paste-ready)."},
        "changes": {"type": "array", "items": {"type": "string"},
                    "description": "What the repair changed, in plain strings"},
        "validation": _VALIDATION,
    },
}

FIX_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["resource_url", "graded_at", "ok", "failure", "score_before", "defects", "package", "notes"],
    "properties": {
        "resource_url": {"type": "string"},
        "graded_at": {"type": "string"},
        "ok": {"type": "boolean"},
        "failure": {"type": ["string", "null"],
                    "description": "Why no package was shipped, when ok is false — an unvalidatable package is never shipped broken"},
        "score_before": {"type": ["integer", "null"], "description": "The /v1/grade score of the listing as found"},
        "defects": {"type": "array", "items": _DEFECT},
        "package": {"anyOf": [{"type": "null"}, _PACKAGE, _COMPACT_PACKAGE]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string", "enum": ["full", "compact"],
                 "description": "The response mode that produced this result (echo of the request's mode, 'full' when omitted). Present on fix results; the package shape follows it."},
    },
}

GRADE_INPUT_EXAMPLE = {"resource_url": "https://target.example.com/v1/thing"}

FIX_INPUT_EXAMPLE = {
    "resource_url": "https://target.example.com/v1/thing",
    "sample_output": {"verdict": "OK", "score": 12},
    "name": "Target Thing",
    "category": "data",
}

# The listing's example pair is a COMPACT request/response: Settled's
# extractor silently drops published output samples over ~1.5 KB
# serialized (measured 2026-10-25: 1,453 B kept, 1,721 B dropped), and
# an honest full-mode sample cannot fit — its repaired package alone is
# ~4.3 KB even for a one-field sample. The compact response is a real,
# complete product (repaired fields only), so its real output is what
# the listing publishes; full mode stays the default for direct buyers.
FIX_COMPACT_INPUT_EXAMPLE = {**FIX_INPUT_EXAMPLE, "mode": "compact"}

# Real grader/fixer outputs (grade + fix of a hollow listing, generated
# by running the actual pipeline against the test fixture) — pinned by
# tests: each must validate against its output schema above, and the
# compact fix example must equal a fresh compact run on the fixture and
# stay under Settled's ~1.5 KB published-sample limit.
GRADE_OUTPUT_EXAMPLE: dict = {'resource_url': 'https://target.example.com/v1/thing',
 'graded_at': '2026-10-21T21:38:16.618483+00:00',
 'reachable': True,
 'http_status': 402,
 'x402_valid': True,
 'terms': {'found': True,
           'price_usdc': '$0.02',
           'amount_units': '20000',
           'network': 'eip155:8453',
           'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
           'pay_to': '0x1111111111111111111111111111111111111111',
           'scheme': 'exact',
           'x402_version': 2},
 'extension_present': True,
 'sdk_validation': {'ran': True, 'consistency_valid': True, 'spec_valid': True, 'errors': []},
 'schema_quality': {'extension_present': True,
                    'hollow': True,
                    'score': 55,
                    'deductions': [{'name': 'hollow output (example only, no schema)',
                                    'points': 45,
                                    'detail': 'info.output carries only type/example — the hollow '
                                              'shape 87.8% of listings ship. No field is typed, so '
                                              'a buyer cannot validate a response.'}]},
 'defects': [{'name': 'hollow output schema (example only, no typed schema)',
              'severity': 'major',
              'detail': 'info.output carries only type/example — the hollow shape 87.8% of '
                        'listings ship. No field is typed, so a buyer cannot validate a '
                        'response.'}],
 'score': 88,
 'fix_available': True}

FIX_OUTPUT_EXAMPLE: dict = {'resource_url': 'https://target.example.com/v1/thing',
 'graded_at': '2026-10-25T05:23:52.401134+00:00',
 'score_before': 88,
 'defects': [{'name': 'hollow output schema (example only, no typed schema)',
              'severity': 'major',
              'detail': 'info.output carries only type/example — the hollow shape 87.8% of '
                        'listings ship. No field is typed, so a buyer cannot validate a '
                        'response.'}],
 'package': {'inferred_from': 'single_sample',
             'description': 'Target Thing — returns verdict, score. Use when you need structured '
                            'data inside an agent workflow. $0.02 per call in USDC on Base via '
                            'x402.',
             'extension': {'info': {'input': {'type': 'http',
                                              'method': 'POST',
                                              'bodyType': 'json',
                                              'body': {'thing_id': 'abc'}},
                                    'output': {'type': 'json',
                                               'example': {'verdict': 'OK', 'score': 12},
                                               'schema': {'type': 'object',
                                                          'properties': {'verdict': {'type': 'string'},
                                                                         'score': {'type': 'integer'}},
                                                          'required': ['verdict', 'score']}}},
                           'schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                      'type': 'object',
                                      'properties': {'input': {'type': 'object',
                                                               'properties': {'type': {'type': 'string',
                                                                                       'const': 'http'},
                                                                              'method': {'type': 'string',
                                                                                         'enum': ['POST',
                                                                                                  'PUT',
                                                                                                  'PATCH']},
                                                                              'bodyType': {'type': 'string',
                                                                                           'enum': ['json',
                                                                                                    'form-data',
                                                                                                    'text']},
                                                                              'body': {'type': 'object',
                                                                                       'properties': {'thing_id': {'type': 'string'}},
                                                                                       'required': ['thing_id']}},
                                                               'required': ['type',
                                                                            'method',
                                                                            'bodyType',
                                                                            'body'],
                                                               'additionalProperties': False},
                                                     'output': {'type': 'object',
                                                                'properties': {'type': {'type': 'string'},
                                                                               'example': {'type': 'object',
                                                                                           'properties': {'verdict': {'type': 'string'},
                                                                                                          'score': {'type': 'integer'}},
                                                                                           'required': ['verdict',
                                                                                                        'score']},
                                                                               'schema': {'type': 'object'}},
                                                                'required': ['type']}},
                                      'required': ['input']}},
             'openapi_snippet': {'openapi': '3.0.3',
                                 'paths': {'/v1/thing': {'post': {'summary': 'Target Thing — '
                                                                             'returns verdict, '
                                                                             'score',
                                                                  'description': 'Target Thing — '
                                                                                 'returns verdict, '
                                                                                 'score. Use when '
                                                                                 'you need '
                                                                                 'structured data '
                                                                                 'inside an agent '
                                                                                 'workflow. $0.02 '
                                                                                 'per call in USDC '
                                                                                 'on Base via '
                                                                                 'x402.',
                                                                  'requestBody': {'required': True,
                                                                                  'content': {'application/json': {'schema': {'type': 'object',
                                                                                                                              'properties': {'thing_id': {'type': 'string'}},
                                                                                                                              'required': ['thing_id']},
                                                                                                                   'example': {'thing_id': 'abc'}}}},
                                                                  'responses': {'200': {'description': 'Success',
                                                                                        'content': {'application/json': {'schema': {'type': 'object',
                                                                                                                                    'properties': {'verdict': {'type': 'string'},
                                                                                                                                                   'score': {'type': 'integer'}},
                                                                                                                                    'required': ['verdict',
                                                                                                                                                 'score']},
                                                                                                                         'example': {'verdict': 'OK',
                                                                                                                                     'score': 12}}}},
                                                                                '402': {'description': 'Payment '
                                                                                                       'required '
                                                                                                       '— '
                                                                                                       'x402 '
                                                                                                       'challenge; '
                                                                                                       'extensions.bazaar '
                                                                                                       'carries '
                                                                                                       'the '
                                                                                                       'discovery '
                                                                                                       'extension '
                                                                                                       'from '
                                                                                                       'this '
                                                                                                       'package.'}}}}}},
             'well_known_snippet': {'resource': 'https://target.example.com/v1/thing',
                                    'method': 'POST',
                                    'price': '$0.02',
                                    'scheme': 'exact',
                                    'mimeType': 'application/json',
                                    'description': 'Target Thing — returns verdict, score. Use '
                                                   'when you need structured data inside an agent '
                                                   'workflow. $0.02 per call in USDC on Base via '
                                                   'x402.',
                                    'accepts': [{'scheme': 'exact',
                                                 'network': 'eip155:8453',
                                                 'amount': '20000',
                                                 'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
                                                 'payTo': '0x1111111111111111111111111111111111111111',
                                                 'maxTimeoutSeconds': 300}],
                                    'inputSchema': {'type': 'object',
                                                    'properties': {'thing_id': {'type': 'string'}},
                                                    'required': ['thing_id']},
                                    'outputSchema': {'type': 'object',
                                                     'properties': {'verdict': {'type': 'string'},
                                                                    'score': {'type': 'integer'}},
                                                     'required': ['verdict', 'score']}},
             'instructions': 'How to ship this package (standard x402 middleware, e.g. the '
                             'Python/TS x402 SDKs):\n'
                             '\n'
                             "1. Set your route's description to package.description (it is under "
                             '500 characters,\n'
                             '   so CDP-facilitator payments will not be rejected for length), '
                             'e.g.:\n'
                             '       RouteConfig(description=<package.description>, '
                             'extensions={"bazaar": <package.extension>})\n'
                             "2. Attach package.extension as the route's Bazaar discovery "
                             'extension:\n'
                             '       extensions={"bazaar": package["extension"]} on the same route '
                             'config.\n'
                             '   The extension passed validate_discovery_extension and\n'
                             '   validate_discovery_extension_spec at generation time.\n'
                             '3. Replace the resource entry in your /.well-known/x402 manifest '
                             'with\n'
                             '   package.well_known_snippet, and merge '
                             'package.openapi_snippet.paths into\n'
                             '   your OpenAPI document.\n'
                             '4. Keep the manifest accepts IDENTICAL to your live 402 challenge — '
                             'directory\n'
                             '   validators read the two against each other and reject '
                             'mismatches.\n'
                             '5. Metadata alone never lists you: settle one real payment through '
                             'the CDP\n'
                             '   facilitator (CDP Bazaar / Agentic.Market) or the PayAI '
                             'facilitator (PayAI\n'
                             '   index) and that index catalogs this route; re-settle at least '
                             'every ~30 days\n'
                             '   or the listing ages out.',
             'validation': {'consistency_valid': True, 'spec_valid': True}},
 'notes': ['inferred_from: single_sample — the output schema was inferred from the one sample '
           'supplied; review it before shipping, especially arrays (element schemas are merged) '
           'and any field whose real type varies.'],
 'ok': True,
 'failure': None,
 'mode': 'full'}

FIX_COMPACT_OUTPUT_EXAMPLE: dict = {'resource_url': 'https://target.example.com/v1/thing',
 'graded_at': '2026-10-25T05:23:52.455109+00:00',
 'score_before': 88,
 'defects': [{'name': 'hollow output schema (example only, no typed schema)',
              'severity': 'major',
              'detail': 'info.output carries only type/example — the hollow shape 87.8% of '
                        'listings ship. No field is typed, so a buyer cannot validate a '
                        'response.'}],
 'package': {'inferred_from': 'single_sample',
             'description': 'Target Thing — returns verdict, score. Use when you need structured '
                            'data inside an agent workflow. $0.02 per call in USDC on Base via '
                            'x402.',
             'info': {'input': {'type': 'http',
                                'method': 'POST',
                                'bodyType': 'json',
                                'body': {'thing_id': 'abc'}},
                      'output': {'type': 'json',
                                 'example': {'verdict': 'OK', 'score': 12},
                                 'schema': {'type': 'object',
                                            'properties': {'verdict': {'type': 'string'},
                                                           'score': {'type': 'integer'}},
                                            'required': ['verdict', 'score']}}},
             'changes': ['description rewritten (139 chars, sub-500, with a use-when sentence)',
                         'output schema inferred from sample (verdict, score)'],
             'validation': {'consistency_valid': True, 'spec_valid': True}},
 'notes': ['compact: fields only — set route description = package.description, extension info = '
           'package.info (meta-schema via your x402 SDK); full mode returns snippets + '
           'instructions.',
           'inferred_from: single_sample — review inferred schema before shipping.'],
 'ok': True,
 'failure': None,
 'mode': 'compact'}


def bazaar_extension(input_schema: dict, output_schema: dict,
                     input_example: dict, output_example: dict) -> dict:
    """The x402 Bazaar discovery extension for one ListSmith route, in
    the canonical v2 shape: `info` carries the values (example input AND
    a full example output with its typed schema); `schema` is the JSON
    Schema OF THE INFO OBJECT (properties input/output), which is what
    facilitator-side validators check info against."""
    return {
        "info": {
            "input": {
                "type": "http",
                "method": "POST",
                "bodyType": "json",
                "body": input_example,
            },
            "output": {
                "type": "json",
                "example": output_example,
                "schema": output_schema,
            },
        },
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "const": "http"},
                        "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
                        "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                        "body": input_schema,
                    },
                    "required": ["type", "method", "bodyType", "body"],
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
        },
    }


def grade_extension() -> dict:
    return bazaar_extension(GRADE_INPUT_SCHEMA, GRADE_OUTPUT_SCHEMA,
                            GRADE_INPUT_EXAMPLE, GRADE_OUTPUT_EXAMPLE)


def fix_extension() -> dict:
    return bazaar_extension(FIX_INPUT_SCHEMA, FIX_OUTPUT_SCHEMA,
                            FIX_COMPACT_INPUT_EXAMPLE, FIX_COMPACT_OUTPUT_EXAMPLE)


def x402_manifest(settings: Settings, base_url: str) -> dict:
    # accepts[] mirrors the live 402 challenge so directory validators
    # (Agent Bazaar, x402scan) accept the manifest.
    def accepts(price: str) -> list[dict]:
        return [
            {
                "scheme": "exact",
                "network": settings.network,
                "amount": _price_to_units(price),
                "asset": USDC_ASSET,
                "payTo": settings.pay_to,
                "maxTimeoutSeconds": 300,
            }
        ]

    return {
        "x402Version": 2,
        "service": settings.service_name,
        "description": (
            "Listing grades and repairs for x402 services. /v1/grade "
            f"({settings.grade_price_usdc}) itemizes what is broken in a "
            "route's Bazaar discovery metadata — hollow schemas, failed "
            "SDK validation, over-500-char descriptions, wrong asset "
            f"addresses. /v1/fix ({settings.fix_price_usdc}) returns a "
            "repaired, validator-passed discovery package inferred from "
            "one real sample output. Use this when you operate an x402 "
            "route and need it discoverable, valid, and honestly described."
        ),
        "network": settings.network,
        "payTo": settings.pay_to,
        "ownershipProofs": OWNERSHIP_PROOFS,
        # The nudge inbox: this stall accepts signed fleet-health
        # nudges (app/nudge.py; spec in x402-loop-kits). A top-level
        # extension, NOT a resource — the priced route count (2)
        # does not move.
        "extensions": {"nudge": nudge_extension(base_url)},
        "resources": [
            {
                "resource": f"{base_url}/v1/grade",
                "method": "POST",
                "price": settings.grade_price_usdc,
                "scheme": "exact",
                "mimeType": "application/json",
                "description": (
                    "Grade one x402 route's discovery metadata: SDK "
                    "validation verdicts, schema-quality score, itemized "
                    "defects with severities, and a 0-100 grade. Diagnosis "
                    "only — no repaired content. Use this when you want "
                    "to know what is broken before paying for the fix."
                ),
                "tags": ["x402", "bazaar", "grading", "validation", "discovery", "agents"],
                "accepts": accepts(settings.grade_price_usdc),
                "inputSchema": GRADE_INPUT_SCHEMA,
                "outputSchema": GRADE_OUTPUT_SCHEMA,
            },
            {
                "resource": f"{base_url}/v1/fix",
                "method": "POST",
                "price": settings.fix_price_usdc,
                "scheme": "exact",
                "mimeType": "application/json",
                "description": (
                    "Repair one x402 route's discovery metadata: a "
                    "Bazaar extension with a full output schema inferred "
                    "from your sample, a sub-500-char description with a "
                    "use-when sentence, OpenAPI and well-known snippets, "
                    "and paste-in instructions. Send mode 'compact' for "
                    "the repaired fields only (description, extension "
                    "info, changes) in a smaller response. Ships only if "
                    "it passes both x402 SDK validators. Use this when "
                    "your listing is hollow, invalid, or invisible."
                ),
                "tags": ["x402", "bazaar", "repair", "schema", "discovery", "agents"],
                "accepts": accepts(settings.fix_price_usdc),
                "inputSchema": FIX_INPUT_SCHEMA,
                "outputSchema": FIX_OUTPUT_SCHEMA,
            },
            {
                "resource": f"{base_url}/quote",
                "method": "GET",
                "price": "$0.00",
                "description": "Free price quote for the paid endpoints",
            },
        ],
    }



# --- agent card -------------------------------------------------------------
# The A2A-style card served at /.well-known/agent-card.json. It is
# DERIVED from the x402 manifest above — same resources, same prices,
# same payTo — so the card can never drift from the discovery doc the
# directories already read. Skills mirror the priced routes; the
# services[] entries carry each route's endpoint + atomic amount in
# the shape card verifiers (including the fleet's own
# /v1/agent-card-verify on StallRow) read. No capability is claimed
# that the service does not have: no streaming, no A2A task push
# notifications.

CARD_NAME = "ListSmith"
CARD_PROVIDER = {"organization": "Verdict Row", "url": "https://github.com/QuincyGB", "email": "patrol@verdictrow.com"}


def _skill_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()


def agent_card(settings: Settings, base_url: str) -> dict:
    manifest = x402_manifest(settings, base_url)
    skills: list[dict] = []
    services: list[dict] = []
    for r in manifest["resources"]:
        if r.get("method") != "POST":
            continue  # free reads (the quote) are not skills
        resource = r["resource"]
        path = resource[len(base_url):] if resource.startswith(base_url) else resource
        base_accept = next(
            (a for a in r.get("accepts") or [] if a.get("network") == settings.network),
            None,
        )
        skills.append(
            {
                "id": path.strip("/").replace("/", "-"),
                "name": _skill_name(path),
                "description": r["description"],
                "tags": r.get("tags") or [],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        )
        entry = {"name": path, "endpoint": resource}
        if base_accept is not None:
            entry["amount"] = str(base_accept["amount"])
        services.append(entry)
    return {
        "protocolVersion": "0.3.0",
        "name": CARD_NAME,
        "description": manifest["description"],
        "url": base_url,
        "provider": CARD_PROVIDER,
        "contactEmail": "patrol@verdictrow.com",
        "version": "1.0.0",
        "documentationUrl": f"{base_url}/llms.txt",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
        "services": services,
        "x402": {
            "version": 2,
            "scheme": "exact",
            "discovery": f"{base_url}/.well-known/x402",
            "payTo": settings.pay_to,
            "network": settings.network,
            "asset": USDC_ASSET,
        },
    }


def skill_md(settings: Settings, base_url: str) -> str:
    return f"""# {settings.service_name} — listing grades & repairs for x402 services (x402)

Your x402 route is discoverable only if its 402 carries a valid Bazaar
discovery extension. Most don't: 87.8% of listings ship a hollow
{{"type", "example"}} output, descriptions over 500 chars break CDP
payments, and a malformed extension fails facilitator-side validation
silently — the seller just never gets calls. ListSmith grades that,
and sells the fix. No API key; payment is the authentication.

## Paid endpoints

### `POST {base_url}/v1/grade` — {settings.grade_price_usdc} USDC

Body: `resource_url` (required), `sample_output` (optional — when
supplied, also checked against the published output schema).

Returns the diagnosis only: reachability and terms, whether a Bazaar
extension exists, BOTH x402 SDK validator verdicts, the schema-quality
score, itemized `defects` (name / severity / detail), a 0-100 `score`,
and `fix_available`. No repaired content — that is the other route.

### `POST {base_url}/v1/fix` — {settings.fix_price_usdc} USDC

Body: `resource_url` (required), `sample_output` (required — one real
response body), `name` / `category` (optional), `mode` (optional:
`"full"` default, or `"compact"`).

Returns the grade report plus `package`. In full mode (the default):
- `extension` — a complete Bazaar discovery extension; the output
  schema is inferred from your sample (every sampled field typed, no
  invented fields), marked `"inferred_from": "single_sample"`
- `description` — under 500 chars, with a "use when" sentence
- `openapi_snippet`, `well_known_snippet` — paste-in manifest pieces;
  the snippet's accepts mirror your live challenge exactly
- `instructions` — how to ship it with the standard x402 middleware
- `validation` — the package ships ONLY if it passed both SDK
  validators; otherwise `ok` is false and `failure` names why

In compact mode the response is smaller and `package` carries the
repaired fields only: `description`, `info` (the repaired extension
info — set it as your extension's `info` and regenerate the extension
meta-schema with your x402 SDK), `changes` (what the repair changed),
and the same `validation`. Everything needed to repair the listing is
in the compact response; full mode adds the paste-ready snippets,
the meta-schema, and long-form instructions. Same price, same
validation gate, either mode.

## Free endpoints

- `GET {base_url}/quote` — prices, network, payTo
- `GET {base_url}/health` — liveness
- `GET {base_url}/.well-known/x402` — machine-readable manifest
- `GET {base_url}/.well-known/agent-card.json` — A2A agent card (skills mirror the priced routes)
- `GET {base_url}/.well-known/stability-manifest.json` — signed pinned-route stability manifest (prices + schema fingerprints, pinned under an agent-wallet signature)

## Honest limits

ListSmith reads what your endpoint publishes unpaid; it never pays
your route and never sees the paid payload. The fix's output schema is
inferred from ONE sample: fields null in the sample are left untyped,
array element schemas are merged, numeric-looking strings stay strings.
And metadata alone never lists you — a settled payment through the CDP
or PayAI facilitator is what catalogs a route, per facilitator.
"""


def llms_txt(settings: Settings, base_url: str) -> str:
    return f"""# {settings.service_name}

Paid API: listing grades & repairs for x402 services (Bazaar discovery metadata). Network {settings.network}, x402 v2.

## Endpoints

- POST {base_url}/v1/grade — {settings.grade_price_usdc} USDC. Body: {{"resource_url": str, "sample_output"?: any}}
- POST {base_url}/v1/fix — {settings.fix_price_usdc} USDC. Body: {{"resource_url": str, "sample_output": any, "name"?: str, "category"?: str, "mode"?: "full"|"compact"}} — full (default) returns the complete snippet package; compact returns the repaired fields only (description, extension info, changes)
- GET {base_url}/quote — free price quote
- GET {base_url}/health — free liveness check
- GET {base_url}/.well-known/x402 — manifest
- GET {base_url}/.well-known/agent-card.json — A2A agent card (skills mirror the priced routes)
- GET {base_url}/.well-known/stability-manifest.json — signed pinned-route stability manifest (prices + schema fingerprints)
"""
