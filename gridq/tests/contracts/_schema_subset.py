"""A standard-library validator for the JSON Schema subset the frozen contracts publish.

What a gateway with no GridQ code (and no ``jsonschema`` package) can run against the committed
IC-5 schema files (WP-EDGE-01 acceptance 4). It implements exactly the keywords pydantic emits for
these contracts and **fails loudly on any other keyword**, so a schema that grows a keyword this
does not understand cannot pass by being ignored. Imports nothing but the standard library.

Structural only: the model validators (chain links, sums, invariants) are the contract module's
job, and a gateway that wants them installs ``gridq.contracts`` (the second path of the test).
"""

from __future__ import annotations

import re
from typing import Any

#: Annotations: no effect on validity.
_ANNOTATIONS = frozenset({"title", "description", "default", "discriminator"})
_HANDLED = frozenset(
    {
        "$ref",
        "$defs",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "anyOf",
        "oneOf",
        "const",
        "enum",
        "pattern",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "items",
        "format",
    }
)
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SchemaError(ValueError):
    """The instance does not satisfy the schema (``path`` says where)."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path or '$'}: {detail}")
        self.path = path


def _type_ok(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    raise SchemaError("", f"unsupported type {name!r}")


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/$defs/"):
        raise SchemaError("", f"unsupported $ref {ref!r}")
    target: dict[str, Any] = root["$defs"][ref.removeprefix("#/$defs/")]
    return target


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None) -> None:
    """Raise `SchemaError` unless ``instance`` satisfies ``schema``."""
    _check(instance, schema, root if root is not None else schema, "")


def _check(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> None:
    unknown = set(schema) - _HANDLED - _ANNOTATIONS
    if unknown:
        raise SchemaError(path, f"schema keyword(s) this validator does not implement: {unknown}")
    if "$ref" in schema:
        _check(value, _resolve(root, schema["$ref"]), root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            raise SchemaError(path, f"expected {types}, got {type(value).__name__}")
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        raise SchemaError(path, f"expected the constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(path, f"{value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise SchemaError(path, f"{value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(path, "too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(path, "too long")
        fmt = schema.get("format")
        if fmt == "date-time" and not _DATE_TIME.fullmatch(value):
            raise SchemaError(path, f"{value!r} is not an instant with a UTC offset")
        if fmt == "date" and not _DATE.fullmatch(value):
            raise SchemaError(path, f"{value!r} is not a date")
        if fmt not in (None, "date-time", "date"):
            raise SchemaError(path, f"unsupported format {fmt!r}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(path, f"{value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(path, f"{value} is above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(path, "too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(path, "too many items")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                raise SchemaError(path, f"missing required {name!r}")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                _check(item, props[name], root, f"{path}.{name}")
            elif extra is False:
                raise SchemaError(path, f"unexpected property {name!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, f"{path}.{name}")
    for key, need in (("anyOf", 1), ("oneOf", 1)):
        if key in schema:
            passed = 0
            for option in schema[key]:
                try:
                    _check(value, option, root, path)
                    passed += 1
                except SchemaError:
                    pass
            if passed < need or (key == "oneOf" and passed > 1):
                raise SchemaError(path, f"{passed} of the {key} options match")
