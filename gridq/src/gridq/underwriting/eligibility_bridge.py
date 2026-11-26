"""The Atlas bridge for the site eligibility engine (WP-UW-02, D133).

Run only as a script, in a fresh interpreter (``python -I eligibility_bridge.py``), once per
engine run. It imports nothing from GridQ and holds no rule logic. It:

1. hashes the four Atlas files it will read (the register module, the register file, the
   load-control loader and the segment rule) before and after every call;
2. imports the loader (``load_control/rule.py``) by file path. The loader imports the register
   module by file path itself, and this process is new, so the register file is read afresh: no
   ``lru_cache`` or ``sys.modules`` entry from an earlier run can exist;
3. calls ``load_rule(<segment rule>, as_of=<the run's as_of>)`` once and ``evaluate(rule, site)``
   for each site, and ``require_verified(row, as_of=<the run's as_of>)`` for every register row the
   engine's rule table cites, each in its own ``try``, so one refusal never masks another;
4. writes one JSON document to stdout.

Input (stdin, JSON): ``{"atlas_root": str, "as_of": "YYYY-MM-DD", "rows": [row id, ...],
"sites": [{"site_id", "customer"?, "contracting_entity_customers"?, "assets"}, ...]}``.

Every exception is reported by class name and message; the engine turns each into
"undetermined" with that reason. Nothing here decides eligibility.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any

REGULATORY = Path("roadmap") / "governance" / "regulatory"
FILES = {
    "register_module": REGULATORY / "register.py",
    "register_file": REGULATORY / "regulatory_register.json",
    "loader": REGULATORY / "load_control" / "rule.py",
    "segment_rule": REGULATORY / "load_control" / "segment_rule.yaml",
}


def _sha256(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


def _hashes(root: Path) -> dict[str, str]:
    return {k: _sha256(root / rel) for k, rel in FILES.items()}


def _error(exc: BaseException) -> dict[str, str]:
    return {"class": type(exc).__name__, "message": " ".join(str(exc).split()) or repr(exc)}


def _import(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _row_view(row: dict[str, Any]) -> dict[str, Any]:
    """What the engine needs from a row the gate passed: its thresholds and its quotes."""
    quotes = [
        list(((e.get("source") or {}).get("quotes")) or []) for e in row.get("evidence") or []
    ]
    return {"thresholds": row.get("thresholds"), "evidence_quotes": quotes}


def run(request: dict[str, Any]) -> dict[str, Any]:
    root = Path(request["atlas_root"])
    as_of = date.fromisoformat(request["as_of"])
    before = _hashes(root)
    out: dict[str, Any] = {"bridge": "gridq.underwriting.eligibility_bridge", "as_of": str(as_of)}

    loader: Any = None
    register: Any = None
    try:
        loader = _import("gridq_eligibility_atlas_rule", root / FILES["loader"])
        register = loader.register
        out["loader_import"] = {"outcome": "imported"}
    except BaseException as exc:  # noqa: BLE001 - every failure is reported, none is a pass
        out["loader_import"] = {"outcome": "error", "error": _error(exc)}
    if register is None:
        try:
            register = _import("neura_regulatory_register", root / FILES["register_module"])
        except BaseException as exc:  # noqa: BLE001
            out["register_import"] = {"outcome": "error", "error": _error(exc)}

    loaded: Any = None
    if loader is not None:
        try:
            loaded = loader.load_rule(root / FILES["segment_rule"], as_of=as_of)
            out["load_rule"] = {
                "outcome": "loaded",
                "rule_id": str(loaded.data.get("rule_id")),
                "version": str(loaded.data.get("version")),
                "status": str(loaded.data.get("status")),
                "reported_as_of": loaded.as_of.isoformat(),
                "rests_on": sorted(str(r.get("row")) for r in loaded.data.get("rests_on") or []),
            }
        except BaseException as exc:  # noqa: BLE001
            out["load_rule"] = {"outcome": "error", "error": _error(exc)}
    else:
        out["load_rule"] = {"outcome": "not_called", "why": "the loader did not import"}

    rows: dict[str, Any] = {}
    for row_id in request["rows"]:
        if register is None:
            rows[row_id] = {"verdict": "not_read", "message": "the register module did not import"}
            continue
        try:
            row = register.require_verified(row_id, as_of=as_of)
            rows[row_id] = {"verdict": "passes", "message": "require_verified passes"}
            rows[row_id].update(_row_view(row))
        except BaseException as exc:  # noqa: BLE001
            err = _error(exc)
            rows[row_id] = {"verdict": err["class"], "message": err["message"]}
    out["rows"] = rows

    sites: dict[str, Any] = {}
    for site in request["sites"]:
        if loaded is None:
            continue
        try:
            sites[site["site_id"]] = {
                "outcome": "evaluated",
                "result": loader.evaluate(loaded, site),
            }
        except BaseException as exc:  # noqa: BLE001
            sites[site["site_id"]] = {"outcome": "error", "error": _error(exc)}
    out["evaluations"] = sites

    after = _hashes(root)
    out["files"] = before
    out["files_changed_during_read"] = sorted(k for k in before if before[k] != after[k])
    return out


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        result = run(request)
    except BaseException:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
