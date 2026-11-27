"""WP-SITE-06: the cited standards, the release record and its refusals (D203, -6).

All inputs are SYNTHETIC (``tests/_ramp_ridethrough_support.py``). Offline: the source table is
pinned here; no network fetch.
"""

from __future__ import annotations

import json
import re
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from _ramp_ridethrough_support import (
    AS_OF,
    agreement,
    all_study_evidence,
    declaration,
    evidence,
    study,
)
from gridq.contracts.evidence import EvidenceNotFound, EvidenceRefused, evidence_ref
from gridq.ops.dossier_model import AskHolder
from gridq.ramp_ridethrough import (
    CONSTANTS,
    PERSON_ONLY_ASKS,
    SOURCES,
    RampRidethroughScreen,
    render_summary,
    screen_ramp_ridethrough,
)
from gridq.ramp_ridethrough.sources import CURRENCY

#: (key, source, clause, value): a silent edit of the transcribed table fails here.
GOLDEN = [
    ("g99.type_b.min_capacity_w", "G99_ISSUE_2", "4 (Type B)", Fraction(1_000_000)),
    ("g99.type_d.min_connection_kv", "G99_ISSUE_2", "4 (Type D)", Fraction(110)),
    ("g99.grid_code_large.england_wales_w", "G99_ISSUE_2", "6.1.3.1", Fraction(100_000_000)),
    ("g99.grid_code_large.south_scotland_w", "G99_ISSUE_2", "6.1.3.1", Fraction(30_000_000)),
    ("g99.grid_code_large.north_scotland_w", "G99_ISSUE_2", "6.1.3.1", Fraction(10_000_000)),
    ("g99.t12_2.u_ret_pu", "G99_ISSUE_2", "12.3.1.6 Table 12.2", Fraction(1, 10)),
    ("g99.t12_2.t_rec3_s", "G99_ISSUE_2", "12.3.1.6 Table 12.2", Fraction(22, 10)),
    ("g99.t13_6.u_ret_pu", "G99_ISSUE_2", "13.3.1.10 Table 13.6", Fraction(0)),
    ("g99.frt.recovery_fraction", "G99_ISSUE_2", "12.3.1.7(e)", Fraction(9, 10)),
    ("g99.b4_1.ppm_retained_voltage", "G99_ISSUE_2", "B.4.4.2 Table B.4.1", Fraction(1, 10)),
    ("g99.study.generic_min_short_circuit_va", "G99_ISSUE_2", "B.4.4.3 / C.7.5.3", Fraction(5 * 10**7)),  # noqa: E501
    ("p28.steady_state_change_limit_pct", "P28_ISSUE_2", "5.3.2 Table 4; 6.2; footnote 14", Fraction(3)),  # noqa: E501
    ("p28.cat2.max_events_per_month", "P28_ISSUE_2", "5.3.2 Table 4", Fraction(4)),
    ("p28.cat3.vmax_decrease_pct", "P28_ISSUE_2", "5.3.2 Table 4 NOTE 5", Fraction(12)),
    ("p28.cat23.min_separation_s", "P28_ISSUE_2", "5.3.2", Fraction(600)),
    ("p28.rvc.remanent_flux_pu", "P28_ISSUE_2", "6.4.1(b); footnote 35", Fraction(1, 2)),
]  # fmt: skip


@pytest.mark.parametrize(("key", "source", "clause", "value"), GOLDEN)
def test_transcribed_constants_are_pinned(
    key: str, source: str, clause: str, value: Fraction
) -> None:
    c = CONSTANTS[key]
    assert (c.source_id, c.clause_id, c.value) == (source, clause, value)


def test_constant_table_size_is_pinned() -> None:
    assert len(CONSTANTS) == 50


def test_sources_are_https_with_sha256_and_unconfirmed_currency() -> None:
    assert set(SOURCES) == {"G99_ISSUE_2", "P28_ISSUE_2", "P28_DCRP_REPORT", "GRID_CODE_I6R33"}
    for s in SOURCES.values():
        assert s.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", s.sha256)
        assert s.retrieved_on == "2026-11-26"
    assert set(CURRENCY.values()) == {"unconfirmed"}
    assert all(c.source_id in SOURCES for c in CONSTANTS.values())


def test_person_only_asks_go_to_the_named_owner() -> None:
    assert len(PERSON_ONLY_ASKS) == 4
    for a in PERSON_ONLY_ASKS:
        assert a.holder is AskHolder.NEURA_OWNER and str(a.holder_name) == "Olly"
        assert a.scope == "dossier"


def _screen(**kw: Any) -> RampRidethroughScreen:
    args: dict[str, Any] = {
        "evidence": all_study_evidence(),
        "as_of": AS_OF,
        "record_class": "synthetic_fixture",
    }
    args.update(kw)
    a = args.pop("a", agreement())
    b = args.pop("bess", declaration())
    s = args.pop("s", study())
    return screen_ramp_ridethrough(a, b, s, **args)


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    else:
        yield node


def test_record_is_deterministic_float_free_and_tamper_evident() -> None:
    one, two = _screen(), _screen()
    assert one.screen_hash == two.screen_hash
    assert one.schema_name == "gridq.ramp_ridethrough_screen"
    assert RampRidethroughScreen.model_validate_json(one.model_dump_json()) == one
    data = one.model_dump(mode="json")
    assert not [v for v in _walk(data) if isinstance(v, float)]
    data["ridethrough"]["state"] = "required_evidenced"
    with pytest.raises(ValidationError):
        RampRidethroughScreen.model_validate_json(json.dumps(data))


def test_every_cite_in_the_record_resolves_and_carries_currency() -> None:
    cites = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if {"source_id", "clause_id", "url", "sha256", "currency"} <= node.keys():
                cites.append(node)
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect(_screen().model_dump(mode="json"))
    assert len(cites) > 50
    for c in cites:
        src = SOURCES[c["source_id"]]
        assert (c["url"], c["sha256"], c["currency"]) == (src.url, src.sha256, "unconfirmed")


def test_summary_leads_with_the_screen_only_caveat() -> None:
    text = render_summary(_screen())
    first = text.splitlines()[0]
    assert first.startswith("SCREEN ONLY, standards currency unconfirmed")
    assert "Neura does not write protection-grade controls" in first
    assert "at nominal V0" in text


def test_synthetic_archetype_is_refused_in_a_registered_run() -> None:
    with pytest.raises(EvidenceRefused, match="synthetic archetype"):
        _screen(record_class="registered")


def test_a_synthetic_run_needs_a_synthetic_site_id() -> None:
    a = agreement(site_id="SITE-X")
    bess = declaration().model_copy(update={"site_id": "SITE-X"})
    with pytest.raises(ValueError, match="SYNTHETIC-"):
        _screen(a=a, bess=bess)


def test_inputs_naming_different_sites_are_refused() -> None:
    other = study().model_copy(update={"site_id": "SYNTHETIC-OTHER"})
    with pytest.raises(ValueError, match="names site"):
        _screen(s=other)


def test_an_unresolvable_study_reference_is_refused() -> None:
    with pytest.raises(EvidenceNotFound):
        _screen(evidence=all_study_evidence()[1:])


def test_a_synthetic_study_object_is_refused_in_a_registered_resolution() -> None:
    from gridq.contracts.evidence import SyntheticEvidenceRefused, resolve_ref

    ev = evidence("technical_report", "oem", "rms model for refusal")
    with pytest.raises(SyntheticEvidenceRefused):
        resolve_ref(evidence_ref(ev), (ev,))
