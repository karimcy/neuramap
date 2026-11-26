"""Render a contract's human-readable specification from its code and its frozen JSON Schemas.

The specification is generated, never written by hand, so it cannot drift from the contract: it is
the module's docstring, an identity table (id, version, versions read, git tag, each schema file
with its SHA-256), a field table per schema object (from the committed schema text), and the
public readers with their signatures and first paragraphs. ``python -m gridq.contracts --check``
fails when a committed specification differs from a fresh render; ``--write`` regenerates it.

Standard library only, as the rest of the package (pydantic is reached through the models).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

SPEC_DIR = Path(__file__).resolve().parent / "specs"

__all__ = ["SPEC_DIR", "render_spec", "spec_path"]


def spec_path(stem: str, version: str, spec_dir: Path | None = None) -> Path:
    return (SPEC_DIR if spec_dir is None else spec_dir) / f"{stem}.v{version}.md"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _type_of(node: dict[str, Any]) -> str:
    """A compact, deterministic reading of one JSON Schema node."""
    if "$ref" in node:
        return f"`{_ref_name(node['$ref'])}`"
    if "const" in node:
        return f"`{json.dumps(node['const'])}`"
    if "enum" in node:
        return "one of " + ", ".join(f"`{json.dumps(v)}`" for v in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            return " or ".join(_type_of(n) for n in node[key])
    kind = node.get("type", "any")
    if kind == "array":
        items = node.get("items", {})
        text = f"array of {_type_of(items)}" if items else "array"
        if "prefixItems" in node:
            text = "tuple of (" + ", ".join(_type_of(n) for n in node["prefixItems"]) + ")"
    else:
        text = str(kind)
        if node.get("format"):
            text += f" ({node['format']})"
    bounds = []
    for key, label in (
        ("minimum", ">="),
        ("exclusiveMinimum", ">"),
        ("maximum", "<="),
        ("exclusiveMaximum", "<"),
        ("minLength", "length >="),
        ("maxLength", "length <="),
        ("minItems", "items >="),
        ("maxItems", "items <="),
    ):
        if key in node:
            bounds.append(f"{label} {node[key]}")
    if "pattern" in node:
        bounds.append(f"pattern `{node['pattern']}`")
    return text + (f"; {', '.join(bounds)}" if bounds else "")


def _object_table(name: str, node: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}`", ""]
    if node.get("description"):
        lines += [_cell(node["description"]), ""]
    if "enum" in node:
        lines += ["Values: " + ", ".join(f"`{v}`" for v in node["enum"]), ""]
        return lines
    props: dict[str, Any] = node.get("properties", {})
    if not props:
        lines += [f"Type: {_type_of(node)}", ""]
        return lines
    required = set(node.get("required", ()))
    described = any(spec.get("description") for spec in props.values())
    head = "| field | type | required | default |" + (" description |" if described else "")
    lines += [head, "|---" * head.count(" |") + "|"]
    for field, spec in props.items():
        default = f"`{json.dumps(spec['default'])}`" if "default" in spec else ""
        row = (
            f"| `{field}` | {_cell(_type_of(spec))} | {'yes' if field in required else 'no'} "
            f"| {default} |"
        )
        lines.append(row + (f" {_cell(spec.get('description', ''))} |" if described else ""))
    lines.append("")
    return lines


def _schema_section(stem: str, text: str) -> list[str]:
    schema: dict[str, Any] = json.loads(text)
    lines = [f"## Schema `{stem}`", ""]
    lines += _object_table(schema.get("title", stem), schema)
    for name, node in sorted(schema.get("$defs", {}).items()):
        lines += _object_table(name, node)
    return lines


def _first_paragraph(obj: object) -> str:
    doc = inspect.getdoc(obj) or ""
    return _cell(doc.split("\n\n", 1)[0])


def _readers(module: ModuleType, names: Sequence[str]) -> list[str]:
    lines = ["## Readers", ""]
    lines += [
        "The contract's functions, each the one way to answer its question. Signatures are the "
        "module's own annotations.",
        "",
    ]
    for name in names:
        fn: Callable[..., Any] = getattr(module, name)
        lines += [f"### `{name}{inspect.signature(fn)}`", "", _first_paragraph(fn), ""]
    return lines


def render_spec(module: ModuleType, schemas: Sequence[tuple[str, str]], schema_dir: Path) -> str:
    """The specification of ``module`` whose frozen schemas are ``(stem, version)`` pairs, read as
    committed in ``schema_dir`` (the file's bytes are what the SHA-256 column pins)."""
    contract_id = module.CONTRACT_ID
    version = module.CONTRACT_VERSION
    texts = []
    for stem, ver in schemas:
        path = schema_dir / f"{stem}.v{ver}.schema.json"
        data = path.read_bytes()
        texts.append((stem, path.name, data.decode("utf-8"), hashlib.sha256(data).hexdigest()))
    lines = [
        f"# {contract_id} specification, version {version}",
        "",
        "<!-- Generated by gridq.contracts._specdoc from the contract module and its committed "
        "JSON Schemas. Do not edit: run `python -m gridq.contracts --write`. -->",
        "",
        "| item | value |",
        "|---|---|",
        f"| contract | `{contract_id}` |",
        f"| version | `{version}` |",
        f"| versions read | {', '.join(f'`{v}`' for v in module.SUPPORTED_VERSIONS)} |",
        f"| git tag | `{module.GIT_TAG}` |",
        f"| module | `{module.__name__}` |",
    ]
    for _, name, _, digest in texts:
        lines.append(f"| schema | `schemas/{name}` (sha256 `{digest}`) |")
    lines += ["", "## Overview", "", inspect.getdoc(module) or "", ""]
    for stem, _, text, _ in texts:
        lines += _schema_section(stem, text)
    readers = [
        n
        for n in module.__all__
        if inspect.isfunction(getattr(module, n)) and not n.startswith("_")
    ]
    lines += _readers(module, sorted(readers))
    return "\n".join(lines).rstrip("\n") + "\n"
