"""Schema-inference unit tests: every sampled field typed, nothing
invented, numeric strings stay strings, nulls stay untyped, arrays
merge honestly, and over-deep samples are refused."""

import jsonschema
import pytest

from app.infer import MAX_DEPTH, InferenceError, infer_schema


def test_scalars():
    assert infer_schema(True) == {"type": "boolean"}
    assert infer_schema(7) == {"type": "integer"}
    assert infer_schema(7.5) == {"type": "number"}
    assert infer_schema(35.0) == {"type": "number"}
    assert infer_schema("x") == {"type": "string"}
    assert infer_schema(None) == {}


def test_numeric_strings_stay_strings():
    # Amounts ship as strings all over this ecosystem; the inference
    # must NOT "helpfully" coerce them.
    schema = infer_schema({"amount": "20000", "rate": "0.05", "count": 3})
    assert schema["properties"]["amount"] == {"type": "string"}
    assert schema["properties"]["rate"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer"}


def test_object_fields_and_required():
    sample = {"a": 1, "b": {"c": "x"}}
    schema = infer_schema(sample)
    jsonschema.validate(instance=sample, schema=schema)
    assert set(schema["properties"]) == {"a", "b"}  # nothing invented
    assert schema["required"] == ["a", "b"]
    assert schema["properties"]["b"]["properties"]["c"] == {"type": "string"}


def test_null_field_left_untyped():
    schema = infer_schema({"maybe": None, "sure": 1})
    assert schema["properties"]["maybe"] == {}
    assert "maybe" in schema["required"]  # the FIELD is real; its type is not guessed


def test_array_of_objects_merges():
    sample = {"rows": [{"a": 1, "b": "x"}, {"a": 2}]}
    schema = infer_schema(sample)
    jsonschema.validate(instance=sample, schema=schema)
    items = schema["properties"]["rows"]["items"]
    assert items["properties"]["a"] == {"type": "integer"}
    assert items["properties"]["b"] == {"type": "string"}
    # b is absent from one element, so it cannot be required.
    assert items["required"] == ["a"]


def test_array_scalar_merges():
    assert infer_schema([1, 2, 3])["items"] == {"type": "integer"}
    # integer + number unify to number (every integer is a valid number)
    assert infer_schema([1, 2.5])["items"] == {"type": "number"}
    # a null element makes the honest item schema unconstrained
    assert infer_schema([1, None])["items"] == {}
    assert infer_schema([]) == {"type": "array", "items": {}}


def test_nested_objects_and_arrays_validate():
    sample = {
        "token": {"address": "0xabc", "risk": {"score": 88.5, "flags": ["new", "thin"]}},
        "history": [{"ts": 1, "v": None}, {"ts": 2, "v": 3.25}],
    }
    schema = infer_schema(sample)
    jsonschema.validate(instance=sample, schema=schema)
    flags = schema["properties"]["token"]["properties"]["risk"]["properties"]["flags"]
    assert flags == {"type": "array", "items": {"type": "string"}}
    v = schema["properties"]["history"]["items"]["properties"]["v"]
    assert v == {}  # None merged with number -> untyped, not a guess


def test_depth_cap_refuses_honestly():
    deep = current = {}
    for _ in range(MAX_DEPTH + 3):
        current["n"] = {}
        current = current["n"]
    with pytest.raises(InferenceError):
        infer_schema(deep)
