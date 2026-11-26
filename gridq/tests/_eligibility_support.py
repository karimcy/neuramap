"""SYNTHETIC TEST FIXTURES for the site eligibility engine (WP-UW-02, D134).

`atlas_tree` writes a stand-in Atlas checkout under ``tmp_path``:

- ``register.py`` and ``load_control/rule.py`` are byte copies of the Atlas files pinned in
  ``tests/fixtures/atlas_load_control/PIN.json`` (the real gate and the real loader);
- ``regulatory_register.json``, ``load_control/segment_rule.yaml`` and
  ``load_control/facts_brief.yaml`` are SYNTHETIC TEST FIXTURES written here. Every string in them
  says so. No figure in them is a regulatory value: the two P511 "thresholds" are deliberately not
  the real ones and their quotes say they are not regulatory values.

Row states mirror the real register's verification statuses (REG-13 and REG-27 changed, REG-15 and
REG-38 unverified); only the sign-offs are synthetic, by the fictitious person "Pat Fixture". The
final-rule tree (`FINAL_STATES`) is a hypothetical future register in which the load-control rows
were re-verified and signed off, for the WP-GRC-07 swap test; it is labelled as such.

Nothing here describes a real customer, site, document or regulatory position.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import textwrap
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from tests.ops._dossier_support import (
    entry,
    evidence,
    full_synthetic_inputs,
    register,
    write_registry,
)

from gridq.contracts.evidence import EvidenceObject, evidence_ref
from gridq.ops.dossier import build_dossier
from gridq.ops.dossier_model import CandidateSiteDossier
from gridq.underwriting.eligibility_model import (
    ControlledAssetsDeclaration,
    EligibilityFacts,
    FactInput,
)
from gridq.underwriting.eligibility_rules import FactField

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "tests" / "fixtures" / "atlas_load_control"
PIN: dict[str, Any] = json.loads((SNAPSHOTS / "PIN.json").read_text(encoding="utf-8"))
SIBLING_ATLAS = REPO.parent / "atlas"
REGULATORY = Path("roadmap") / "governance" / "regulatory"

LABEL = "SYNTHETIC TEST FIXTURE"
AS_OF = date(2026, 11, 26)
VERIFIED_ON = "2026-11-17"
SIGNED_ON = "2026-11-20"
RELEASED_ON = "2026-11-23"
SYN_SITE = "SYNTHETIC-UW02-ALPHA"
SIGNER = "Pat Fixture"

#: signed: verified and signed off; not_signed: verified, no sign-off; changed; unverified.
DEFAULT_STATES: dict[str, str] = {
    "REG-01": "signed",
    "REG-02": "signed",
    "REG-04": "signed",
    "REG-07": "signed",
    "REG-13": "changed",
    "REG-15": "unverified",
    "REG-16": "signed",
    "REG-17": "signed",
    "REG-19": "signed",
    "REG-26": "not_signed",
    "REG-27": "changed",
    "REG-38": "unverified",
    "REG-41": "signed",
    "REG-42": "not_signed",
}
#: The hypothetical future register the final-rule swap test runs against (labelled).
FINAL_STATES: dict[str, str] = {
    **DEFAULT_STATES,
    "REG-26": "signed",
    "REG-27": "signed",
    "REG-42": "signed",
}

#: Deliberately not the real P511 figures: a fixture threshold is never a regulatory value.
LOWER_QUOTE = f"{LABEL}, not a regulatory value: the lower fixture band ends at 3 MW."
UPPER_QUOTE = f"{LABEL}, not a regulatory value: the upper fixture band starts above 7 MW."
THRESHOLDS: dict[str, Any] = {
    "lower_trading_eligibility_threshold": {
        "value": 3,
        "unit": "MW",
        "comparator": "gt",
        "evidence_index": 0,
        "quote": LOWER_QUOTE,
    },
    "upper_trading_eligibility_threshold": {
        "value": 7,
        "unit": "MW",
        "comparator": "gt",
        "evidence_index": 0,
        "quote": UPPER_QUOTE,
    },
}


def _evidence_hash(obj: dict[str, Any]) -> str:
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def register_row(
    row_id: str,
    state: str,
    *,
    thresholds: Mapping[str, Any] | None = None,
    quotes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One synthetic register row that passes the real gate's integrity check."""
    status = {"signed": "verified", "not_signed": "verified"}.get(state, state)
    row: dict[str, Any] = {
        "id": row_id,
        "spec_section": "fixture",
        "spec_row": f"{LABEL} row",
        "claim": f"{LABEL} claim for {row_id}; it states no regulatory position.",
        "spec_value": f"{LABEL} spec value",
        "verification_status": status,
        "source_value": f"{LABEL} source value",
        "note": LABEL,
        "absent_reason": None,
        "evidence": [],
    }
    if status == "unverified":
        row["absent_reason"] = f"{LABEL}: no source was read for this row"
        return row
    doc = hashlib.sha256(f"{LABEL} {row_id}".encode()).hexdigest()
    eo = {
        "contract": "IC-4",
        "contract_version": "1.0.0",
        "record_class": "registered",
        "document_hash": "sha256:" + doc,
        "evidence_type": "regulatory_publication",
        "title": f"{LABEL} publication for {row_id}",
        "issuer": {"name": f"{LABEL} issuer", "role": "regulator", "reference": LABEL},
        "issued_on": "2026-08-01",
        "effective_from": {"kind": "unknown", "reason": LABEL},
        "expiry": {"kind": "no_expiry", "reason": LABEL},
        "scope": {"kind": "stream", "site_id": "ALL_SITES", "stream": "FIXTURE"},
        "verification": {
            "kind": "verified",
            "method": "retrieved_from_issuer_publication",
            "verified_on": VERIFIED_ON,
            "verifier": f"{LABEL} verifier",
            "verifier_role": "neura_internal",
        },
    }
    row["evidence"] = [
        {
            "evidence_hash": _evidence_hash(eo),
            "evidence_object": eo,
            "source": {
                "sha256": doc,
                "quotes": list(quotes) or [f"{LABEL} quote for {row_id}."],
            },
        }
    ]
    if thresholds is not None:
        row["thresholds"] = dict(thresholds)
    if state == "signed":
        row["sign_off"] = {"by": SIGNER, "role": f"{LABEL} compliance lead", "on": SIGNED_ON}
    return row


def write_register(
    root: Path,
    states: Mapping[str, str] = DEFAULT_STATES,
    *,
    thresholds: Mapping[str, Any] | None = THRESHOLDS,
    reg01_quotes: tuple[str, ...] = (LOWER_QUOTE, UPPER_QUOTE),
) -> Path:
    rows = []
    for row_id, state in sorted(states.items()):
        if row_id == "REG-01":
            rows.append(register_row(row_id, state, thresholds=thresholds, quotes=reg01_quotes))
        else:
            rows.append(register_row(row_id, state))
    path = root / REGULATORY / "regulatory_register.json"
    path.write_text(json.dumps({"register": LABEL, "rows": rows}, indent=1), encoding="utf-8")
    return path


def segment_rule(*, final: bool = False, released_on: str = RELEASED_ON) -> dict[str, Any]:
    """A synthetic segment rule shaped like WP-GRC-16's, passing the real loader's checks."""
    reason = f"{LABEL} reason; it states no position."
    if final:
        rests = [
            {"row": r, "reliance": "verified", "used_for": f"{LABEL} use"}
            for r in ("REG-26", "REG-27", "REG-42")
        ]
        outcomes = {
            "refuse": {
                "engine_status": "ineligible",
                "admitted_for_controlled_dispatch": False,
                "meaning": f"{LABEL} refusal",
            },
            "admit": {
                "engine_status": "eligible",
                "admitted_for_controlled_dispatch": True,
                "meaning": f"{LABEL} admission under a final rule",
            },
        }
        passing, held = "admit", "admit"
    else:
        rests = [
            {
                "row": r,
                "reliance": "provisional",
                "used_for": f"{LABEL} use",
                "provisional_reason": f"{LABEL}: the row is not yet relied on as verified",
                "expected_refusal": refusal,
            }
            for r, refusal in (
                ("REG-26", "RowNotSignedOff"),
                ("REG-27", "RowChanged"),
                ("REG-42", "RowNotSignedOff"),
            )
        ]
        outcomes = {
            "refuse": {
                "engine_status": "ineligible",
                "admitted_for_controlled_dispatch": False,
                "meaning": f"{LABEL} refusal",
            },
            "pass_provisional": {
                "engine_status": "undetermined",
                "admitted_for_controlled_dispatch": False,
                "meaning": f"{LABEL} provisional pass",
            },
            "hold": {
                "engine_status": "undetermined",
                "admitted_for_controlled_dispatch": False,
                "meaning": f"{LABEL} hold pending counsel",
            },
        }
        passing, held = "pass_provisional", "hold"

    def consumer(cid: str, outcome: str, code: str) -> dict[str, Any]:
        e: dict[str, Any] = {
            "id": cid,
            "label": f"{LABEL} {cid}",
            "outcome": outcome,
            "reason": reason,
            "reason_code": code,
            "rows": ["REG-27"],
        }
        if outcome in ("pass_provisional", "admit_provisional"):
            e["proposed_class"] = "B"
        return e

    def asset(aid: str, outcome: str, code: str) -> dict[str, Any]:
        return {
            "id": aid,
            "label": f"{LABEL} {aid}",
            "outcome": outcome,
            "reason": reason,
            "reason_code": code,
            "rows": ["REG-42"],
        }

    return {
        "rule_id": "load_control_segment_rule",
        "version": "9.0.0" if final else "0.9.0",
        "status": "final" if final else "provisional",
        "released_on": released_on,
        "legal_position": "none",
        "work_package": f"{LABEL} for WP-UW-02",
        "final_rule_from": "WP-GRC-07",
        "scope": f"{LABEL}: a stand-in segment rule for the eligibility engine tests.",
        "facts_brief": "facts_brief.yaml",
        "missing_input": {
            "engine_status": "undetermined",
            "reason_code": "LCL-MISSING-INPUT",
            "reason": f"{LABEL}: an input is missing, so the answer is undetermined.",
        },
        "rests_on": rests,
        "outcomes": outcomes,
        "consumer_classes": [
            consumer("domestic", "refuse", "LCL-CONSUMER-GUARD"),
            consumer("small_business", "refuse", "LCL-CONSUMER-GUARD"),
            consumer("non_domestic_not_small_business", passing, "LCL-CONSUMER-PASS-PROVISIONAL"),
        ],
        "asset_classes": [
            asset("bess", held, "LCL-HELD-COUNSEL"),
            asset("cooling_plant", held, "LCL-HELD-COUNSEL"),
            asset("ups", "refuse", "LCL-ASSET-READ-ONLY"),
        ],
        "portfolio_conditions": [
            {
                "id": "PC-FIXTURE",
                "applies_to": passing,
                "reason_code": "LCL-PORTFOLIO",
                "condition": f"{LABEL}: every customer of the contracting entity is read.",
                "rows": ["REG-27"],
            }
        ],
    }


def write_rule(root: Path, data: Mapping[str, Any]) -> Path:
    """JSON is YAML, so the real loader's ``yaml.safe_load`` reads it unchanged."""
    lc = root / REGULATORY / "load_control"
    (lc / "segment_rule.yaml").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (lc / "facts_brief.yaml").write_text(
        json.dumps({"brief": LABEL, "facts": [{"id": "F-FIXTURE"}]}), encoding="utf-8"
    )
    return lc / "segment_rule.yaml"


def atlas_tree(
    root: Path,
    *,
    states: Mapping[str, str] = DEFAULT_STATES,
    final: bool = False,
    loader: str | None = None,
    **register_kw: Any,
) -> Path:
    """A SYNTHETIC TEST FIXTURE Atlas checkout. ``loader`` replaces ``rule.py`` (stand-ins)."""
    lc = root / REGULATORY / "load_control"
    lc.mkdir(parents=True, exist_ok=True)
    (root / "SYNTHETIC_TEST_FIXTURE.txt").write_text(LABEL + "\n", encoding="utf-8")
    shutil.copyfile(SNAPSHOTS / "register.py.snapshot", root / REGULATORY / "register.py")
    if loader is None:
        shutil.copyfile(SNAPSHOTS / "rule.py.snapshot", lc / "rule.py")
    else:
        shutil.copyfile(SNAPSHOTS / "rule.py.snapshot", lc / "_wp_grc16_rule.py")
        (lc / "rule.py").write_text(loader, encoding="utf-8")
    write_register(root, states, **register_kw)
    write_rule(root, segment_rule(final=final))
    return root


_BASE = textwrap.dedent("""
    import importlib.util
    import sys
    from pathlib import Path

    _HERE = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location("_wp_grc16_rule", _HERE / "_wp_grc16_rule.py")
    base = importlib.util.module_from_spec(_spec)
    sys.modules["_wp_grc16_rule"] = base
    _spec.loader.exec_module(base)
    register = base.register
    RuleRefused = base.RuleRefused
    RULE = base.RULE
""")

#: A stand-in for the final-rule loader WP-GRC-07 will release: same signatures as WP-GRC-16's;
#: it reuses that loader and lifts only LC-4 (a final rule is not loadable). Not the real loader.
FINAL_LOADER = (
    f'"""{LABEL}: a stand-in for the WP-GRC-07 final-rule loader (same signatures)."""\n'
    + _BASE
    + textwrap.dedent("""
    import yaml

    evaluate = base.evaluate
    LC4 = "a final rule is not loadable by this loader"


    def load_rule(path=RULE, *, as_of, register_path=None):
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        errs = [e for e in base.structure_problems(data, path) if LC4 not in e]
        reliance, gate_errs = base.gate(data, as_of, register_path)
        errs += gate_errs
        released = base._released(data)
        if released is not None and as_of < released:
            errs.append(f"as_of {as_of} is before the rule's released_on {released}")
        if errs:
            raise RuleRefused("; ".join(errs))
        return base.LoadedRule(data=data, as_of=as_of, reliance=reliance, path=path)
""")
)

#: A broken provisional loader whose evaluator says eligible: the engine's overlay must hold.
ELIGIBLE_PROVISIONAL_LOADER = (
    f'"""{LABEL}: a broken provisional evaluator that returns eligible."""\n'
    + _BASE
    + textwrap.dedent("""
    load_rule = base.load_rule


    def evaluate(rule, site):
        out = base.evaluate(rule, {**site, "customer": None})
        out.update(status="eligible", admitted_for_controlled_dispatch=["bess"])
        out["reasons"].append({"code": "FIXTURE-BROKEN", "text": "broken fixture", "rows": []})
        return out
""")
)


def raising_loader(body: str) -> str:
    """A loader whose ``load_rule`` or ``evaluate`` misbehaves as ``body`` says."""
    return f'"""{LABEL}: a misbehaving loader."""\n' + _BASE + textwrap.dedent(body)


# --------------------------------------------------------------------------------------------
# Sites, facts and declarations
# --------------------------------------------------------------------------------------------


def synthetic_dossier(
    ev_root: Path, site_id: str = SYN_SITE, *, as_of: date = AS_OF
) -> CandidateSiteDossier:
    """An OPS-01 dossier with every field of ``site_id`` evidenced by fixture documents."""
    full_synthetic_inputs(ev_root, site_id)
    return build_dossier(register(entry(site_id)), as_of=as_of, evidence_root=ev_root)


def declared(field: FactField, value: dict[str, Any]) -> FactInput:
    return FactInput.model_validate_json(
        json.dumps(
            {
                "field": field.value,
                "basis": "declaration",
                "value": {"kind": field.value, **value},
                "evidence": [],
                "declared_by": f"{SIGNER} ({LABEL})",
            }
        )
    )


def evidenced(
    ev_root: Path,
    site_id: str,
    field: FactField,
    value: dict[str, Any],
    *,
    evidence_type: str,
    role: str,
    **kw: Any,
) -> FactInput:
    obj: EvidenceObject = evidence(site_id, evidence_type, role, tag=f"uw02-{field.value}", **kw)
    write_registry(ev_root, obj)
    return FactInput.model_validate_json(
        json.dumps(
            {
                "field": field.value,
                "basis": "evidence",
                "value": {"kind": field.value, **value},
                "evidence": [evidence_ref(obj).model_dump(mode="json")],
                "declared_by": {"kind": "not_applicable", "reason": LABEL},
            }
        )
    )


def facts(site_id: str, *items: FactInput, as_of: date = AS_OF) -> EligibilityFacts:
    return EligibilityFacts(
        record="gridq.eligibility_facts",
        record_version="1.0.0",
        record_class="synthetic_fixture" if site_id.startswith("SYNTHETIC-") else "registered",
        site_id=site_id,
        as_of=as_of,
        facts=tuple(items),
    )


def assets(*classes: str, record_class: str = "synthetic_fixture") -> ControlledAssetsDeclaration:
    return ControlledAssetsDeclaration.model_validate_json(
        json.dumps(
            {
                "record": "gridq.controlled_assets",
                "record_version": "1.0.0",
                "record_class": record_class,
                "product": f"{LABEL} product",
                "declared_by": f"{SIGNER} ({LABEL})",
                "asset_classes": list(classes or ("bess", "cooling_plant")),
            }
        )
    )


def customer_facts(
    ev_root: Path, site_id: str, consumer_class: str, *, portfolio: tuple[str, ...] | None = None
) -> list[FactInput]:
    """The two consumer-class facts, evidenced by synthetic fixtures (synthetic mode only)."""
    out = [
        evidenced(
            ev_root,
            site_id,
            FactField.CUSTOMER_CONSUMER_CLASS,
            {"consumer_class": consumer_class},
            evidence_type="technical_report",
            role="supplier",
        )
    ]
    if portfolio is not None:
        out.append(
            evidenced(
                ev_root,
                site_id,
                FactField.CONTRACTING_ENTITY_CUSTOMERS,
                {"consumer_classes": list(portfolio)},
                evidence_type="technical_report",
                role="supplier",
            )
        )
    return out
