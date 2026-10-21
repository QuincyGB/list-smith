"""JSON Schema inference from a single sample output.

The /v1/fix package's output schema is inferred from ONE real response
the buyer supplies. The rules are deliberately conservative:

- every property in the sample appears in the schema, and NO property
  is invented — the schema describes exactly the sample's fields;
- types come from the sample's JSON types only. A string stays a string
  even when it looks numeric ("20000" is how many APIs ship amounts);
  the service never guesses that a string is "really" a number;
- a field that is null in the sample is left UNTYPED ({}) — the field
  exists, but one sample says nothing honest about its real type;
- arrays merge their element schemas: shared object keys merge,
  properties missing from some elements drop out of `required`, and
  integer+number unify to number.
"""

from __future__ import annotations

MAX_DEPTH = 6


class InferenceError(ValueError):
    """The sample cannot be turned into an honest schema."""


def _merge(a: dict, b: dict) -> dict:
    """Merge two inferred schemas (array elements) into one that still
    validates both."""
    if a == b:
        return a
    if a == {} or b == {}:
        # Unconstrained (e.g. a null element) — the honest union is
        # "anything", not a guess at the other element's type.
        return {}
    type_a, type_b = a.get("type"), b.get("type")
    if type_a == "object" and type_b == "object":
        props: dict = {}
        for key in a.get("properties", {}) | b.get("properties", {}):
            if key in a.get("properties", {}) and key in b.get("properties", {}):
                props[key] = _merge(a["properties"][key], b["properties"][key])
            elif key in a.get("properties", {}):
                props[key] = a["properties"][key]
            else:
                props[key] = b["properties"][key]
        merged: dict = {"type": "object", "properties": props}
        required = sorted(set(a.get("required", [])) & set(b.get("required", [])))
        if required:
            merged["required"] = required
        return merged
    if type_a == "array" and type_b == "array":
        return {"type": "array", "items": _merge(a.get("items", {}), b.get("items", {}))}
    if isinstance(type_a, str) and isinstance(type_b, str):
        if {type_a, type_b} == {"integer", "number"}:
            return {"type": "number"}
        if type_a != type_b:
            return {"type": sorted([type_a, type_b])}
        return {"type": type_a}
    return {}


def infer_schema(value: object, depth: int = 0) -> dict:
    """Infer a JSON Schema for one JSON value."""
    if depth > MAX_DEPTH:
        raise InferenceError(
            f"sample is nested deeper than {MAX_DEPTH} levels — too deep to infer a schema honestly"
        )
    if value is None:
        return {}
    if isinstance(value, bool):  # before int: bool is an int subclass
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        items: dict | None = None
        for element in value:
            element_schema = infer_schema(element, depth + 1)
            items = element_schema if items is None else _merge(items, element_schema)
        return {"type": "array", "items": items if items is not None else {}}
    if isinstance(value, dict):
        schema: dict = {"type": "object", "properties": {}}
        for key, sub in value.items():
            schema["properties"][str(key)] = infer_schema(sub, depth + 1)
        if value:
            schema["required"] = list(value.keys())
        return schema
    raise InferenceError(f"unsupported sample value type: {type(value).__name__}")
