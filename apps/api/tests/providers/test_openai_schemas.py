"""OpenAI wire schemas must retain the canonical tool argument semantics."""

import pytest

from src.openship.providers.openai import _normalize_arguments
from src.openship.sandboxes.python_tool import PythonArguments


def test_python_dynamic_maps_are_not_closed_on_the_wire():
    from src.openship.providers.openai_schemas import wire_schema

    schema = PythonArguments.model_json_schema()
    wire, strict = wire_schema(schema)
    assert strict is False
    assert wire["properties"]["output_media"]["additionalProperties"] == {"type": "string"}


def test_python_dynamic_output_alias_survives_normalization():
    schema = PythonArguments.model_json_schema()
    arguments = {"source": "print('ok')", "output_media": {"chart.png": "image/png"}}
    assert _normalize_arguments(schema, arguments)["output_media"] == {"chart.png": "image/png"}


def test_dynamic_map_does_not_accept_unknown_root_property():
    with pytest.raises(ValueError):
        _normalize_arguments(PythonArguments.model_json_schema(), {"unknown": True})


@pytest.mark.parametrize("container", ["array", "union", "ref"])
def test_nested_dynamic_maps_disable_strict_mode(container):
    from src.openship.providers.openai_schemas import wire_schema

    dynamic = {"type": "object", "additionalProperties": {"type": "string"}}
    nested = {
        "array": {"type": "array", "items": dynamic},
        "union": {"anyOf": [dynamic, {"type": "null"}]},
        "ref": {"$ref": "#/$defs/Map"},
    }[container]
    schema = {"type": "object", "properties": {"field": nested},
              "additionalProperties": False, "$defs": {"Map": dynamic}}
    assert wire_schema(schema)[1] is False


def test_unresolved_reference_fails_closed():
    from src.openship.providers.openai_schemas import wire_schema

    with pytest.raises(ValueError):
        wire_schema({"type": "object", "properties": {"x": {"$ref": "#/$defs/missing"}},
                     "additionalProperties": False})


def test_fixed_schema_remains_strict():
    from src.openship.providers.openai_schemas import wire_schema

    schema = {"type": "object", "properties": {"value": {"type": "string"}},
              "required": ["value"], "additionalProperties": False}
    assert wire_schema(schema)[1] is True


def test_non_strict_arguments_preserve_explicit_null():
    from src.openship.providers.openai_schemas import normalize_arguments

    schema = PythonArguments.model_json_schema()
    args = {"source": "print('ok')", "output_media": None}
    assert normalize_arguments(schema, args, strict=False)["output_media"] is None


def test_recursive_reference_is_rejected_instead_of_silently_assumed_safe():
    from src.openship.providers.openai_schemas import wire_schema

    schema = {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}},
              "additionalProperties": False, "$defs": {"Node": {"type": "object",
              "properties": {"child": {"$ref": "#/$defs/Node"}}, "additionalProperties": False}}}
    with pytest.raises(ValueError, match="recursive"):
        wire_schema(schema)
