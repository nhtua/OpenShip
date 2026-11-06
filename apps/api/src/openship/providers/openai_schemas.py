"""Select faithful OpenAI function schemas without closing dynamic maps."""

import copy


def unique_json_object(pairs):
    """Reject duplicate keys at any depth in model-produced JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def normalize_arguments(schema: dict, arguments: dict, *, strict: bool) -> dict:
    """Preserve non-strict JSON values; restore strict wire-only null defaults."""
    if strict:
        from .openai import _normalize_arguments

        return _normalize_arguments(schema, arguments)
    if schema.get("additionalProperties") is False:
        unknown = set(arguments) - set(schema.get("properties", {}))
        if unknown:
            raise ValueError("tool arguments contain undeclared properties")
    return copy.deepcopy(arguments)


def wire_schema(schema: dict) -> tuple[dict, bool]:
    """Use strict mode only when every reachable object has fixed properties."""
    dynamic = False
    visited = set()
    active = set()

    def visit(node):
        nonlocal dynamic
        if not isinstance(node, dict):
            raise TypeError("invalid schema node")
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise ValueError("unsupported schema reference")
            if reference in active:
                raise ValueError("recursive schema reference is unsupported")
            if reference not in visited:
                active.add(reference)
                target = schema
                try:
                    for part in reference[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise ValueError("unresolved schema reference") from None
                visit(target)
                active.remove(reference)
                visited.add(reference)
        if node.get("type") == "object" and node.get("additionalProperties", True) is not False:
            dynamic = True
        for child in node.get("properties", {}).values():
            visit(child)
        for key in ("anyOf", "oneOf", "allOf"):
            for child in node.get(key, []):
                visit(child)
        if isinstance(node.get("items"), dict):
            visit(node["items"])
        if isinstance(node.get("additionalProperties"), dict):
            visit(node["additionalProperties"])

    visit(schema)
    if dynamic:
        return copy.deepcopy(schema), False
    from .openai import _strict_schema

    return _strict_schema(schema), True
