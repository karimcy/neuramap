"""`underwriting_basis_v1` (WP-UW-01): the evidence-gated study basis.

The three acceptance criteria, each pinned here:

1. **A stream without its required IC-4 evidence object is not priced under the basis.** Read as
   D91 reads it: such a stream contributes GBP 0 to the priced floor and never carries
   FIRM or CONTRACTED firmness. Pinned through `build_value_streams` (the valuation every sizing
   candidate and the final result go through, fed the gated registry) and through
   `generate_quote` (the production entry point, with the engine post-pass as the guard).
2. **The two bases can never share a run id.** Pinned on the canonical run input and on the
   assembled record.
3. **The academic-basis golden pins are unchanged.** Loading the plug-in and building the gated
   registry move no academic identity and change no default gate. The pinned golden files
   themselves are not edited by WP-UW-01.

**Every evidence object in this file is a SYNTHETIC SCHEMA-TEST FIXTURE** (``record_class
"synthetic_fixture"``, issuers named "SYNTHETIC-... (fixture; not a real company)"). None is
evidence, and the basis admits them only in its ``synthetic_fixtures_test_only`` mode, which labels
every artefact "SYNTHETIC TEST FIXTURES — NOT EVIDENCE".
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests.test_all_streams_study_basis import all_streams

import gridq.extensions as ext
import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.contracts.evidence import (
    CONTRACT_VERSION as IC4_VERSION,
)
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceType,
    IssuerRole,
    evidence_ref,
)
from gridq.engine.baseline import BaselineEngine
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_run_input, assemble_quote_record
from gridq.quotes.pipeline import generate_quote
from gridq.schemas import (
    ConfidenceBand,
    EvidenceLink,
    Firmness,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    RateUnit,
    SiteModel,
    ValueStreamResult,
    is_priced_floor,
)
from gridq.schemas.study import basis_class_problems
from gridq.underwriting import __version__ as UW_VERSION
from gridq.underwriting.basis import BASIS_ID, SYNTHETIC_LABEL, UnderwritingBasis
from gridq.underwriting.engine import D57_BUDGET_NOTE_PREFIX, UnderwritingEngine
from gridq.underwriting.gate import (
    DEFAULT_NOTES_DELIMITER,
    StreamVerdict,
    UnderwritingGate,
    tighten,
    underwriting_registry,
)
from gridq.underwriting.rules import (
    RULE_TABLE_HASH,
    STACKING_RULES,
    STREAM_RULES,
    RowBasis,
    rule_table_document,
)
from gridq.underwriting.sources import SOURCES
from gridq.value_stack import gb_streams
from gridq.value_stack.interface import GateResult, GateStatus
from gridq.value_stack.registry import default_registry

AS_OF = date(2026, 11, 26)
SITE_ID = "site_test"  # `tests/conftest.py::_make_site`
PLUGIN = "gridq.underwriting"
REPO = Path(__file__).resolve().parents[1]

BM = MarketStream.BALANCING_MECHANISM
DFS = MarketStream.DFS
DCDMDR = MarketStream.ANCILLARY_DC_DM_DR
FLEX = MarketStream.LOCAL_FLEX_PICLO

#: The four objects BM's row requires (registration, VLP route, customer consent, G99 approval).
BM_TYPES = (
    EvidenceType.BMU_REGISTRATION,
    EvidenceType.AGGREGATOR_AGREEMENT,
    EvidenceType.CUSTOMER_CONSENT,
    EvidenceType.G99_APPROVAL,
)
_ISSUER = {
    EvidenceType.BMU_REGISTRATION: IssuerRole.ELEXON,
    EvidenceType.AGGREGATOR_AGREEMENT: IssuerRole.AGGREGATOR,
    EvidenceType.CUSTOMER_CONSENT: IssuerRole.CUSTOMER,
    EvidenceType.G99_APPROVAL: IssuerRole.DNO,
    EvidenceType.DNO_FLEXIBILITY_CONTRACT: IssuerRole.DNO,
    EvidenceType.CAPACITY_AGREEMENT: IssuerRole.EMR_SETTLEMENT,
}


# ---------------------------------------------------------------------- fixtures (SYNTHETIC)


@pytest.fixture(autouse=True)
def _registries_as_core_ships_them() -> Iterator[None]:
    """Building an `UnderwritingEngine` registers the plug-in; put the registries back after."""
    for registry in ext._ALL:
        registry._ensure_home()
    snapshots = [r._snapshot() for r in ext._ALL]
    loaded, discovered = dict(ext._loaded), ext._discovered
    try:
        yield
    finally:
        for registry, snapshot in zip(ext._ALL, snapshots, strict=True):
            registry._restore(snapshot)
        ext._loaded.clear()
        ext._loaded.update(loaded)
        ext._discovered = discovered


@pytest.fixture(scope="module")
def lib() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def fx(
    evidence_type: EvidenceType,
    stream: MarketStream | None = BM,
    *,
    site_id: str = SITE_ID,
    verified: bool = True,
    effective: date | str = date(2026, 1, 1),
    expires: date | str | None = date(2027, 12, 31),
    record_class: str = "synthetic_fixture",
    tag: str = "",
) -> EvidenceObject:
    """A SYNTHETIC IC-4 schema-test fixture. Not evidence; names no real party."""
    label = f"{evidence_type.value}|{stream}|{site_id}|{tag}"
    payload: dict[str, Any] = {
        "contract": "IC-4",
        # The live IC-4 version, which holds every type: the G6 test makes one of each.
        "contract_version": IC4_VERSION,
        "record_class": record_class,
        "evidence_type": evidence_type.value,
        "title": f"Synthetic {evidence_type.value} (WP-UW-01 schema-test fixture) {tag}".strip(),
        "issuer": {
            "name": "SYNTHETIC-ISSUER (fixture; not a real company)",
            "role": _ISSUER.get(evidence_type, IssuerRole.AGGREGATOR).value,
            "reference": f"SYNTH-{abs(hash(label)) % 10_000:04d}",
        },
        "document_hash": "sha256:" + hashlib.sha256(label.encode()).hexdigest(),
        "issued_on": "2025-12-01",
        "effective_from": _date_or_unknown(effective),
        "expiry": (
            {"kind": "no_expiry", "reason": "synthetic fixture: no end date"}
            if expires is None
            else _date_or_unknown(expires)
            if isinstance(expires, str)
            else {"kind": "expires_on", "expires_on": expires.isoformat()}
        ),
        "scope": (
            {"kind": "site", "site_id": site_id}
            if stream is None
            else {"kind": "stream", "stream": stream.value, "site_id": site_id}
        ),
        "verification": (
            {
                "kind": "verified",
                "verifier": "SYNTHETIC-VERIFIER (fixture)",
                "verifier_role": "independent_third_party",
                "method": "document_inspection",
                "verified_on": "2025-12-02",
            }
            if verified
            else {"kind": "unverified", "reason": "synthetic fixture: not yet checked"}
        ),
    }
    # Contract models are strict in Python mode; the wire form is JSON (as IC-4's own tests).
    return EvidenceObject.model_validate_json(json.dumps(payload))


def _date_or_unknown(value: date | str) -> object:
    if value == "unknown":
        return {"kind": "unknown", "reason": "synthetic fixture: the date is not stated"}
    assert isinstance(value, date)
    return value.isoformat()


def basis(
    admission: str = "synthetic_fixtures_test_only", *, as_of: date = AS_OF
) -> UnderwritingBasis:
    return UnderwritingBasis(
        basis_id="underwriting_basis_v1",
        authorised_by="Olly",
        as_of=as_of,
        evidence_admission=admission,  # type: ignore[arg-type]
    )


def bm_evidence(**kw: Any) -> list[EvidenceObject]:
    return [fx(t, BM, **kw) for t in BM_TYPES]


def with_refs(site: SiteModel, objects: list[EvidenceObject]) -> SiteModel:
    """``site`` naming ``objects`` by content hash (what makes them assessable)."""
    links = tuple(
        EvidenceLink(
            supports=o.scope.stream if o.scope.kind == "stream" else "site",
            evidence=evidence_ref(o),
        )
        for o in objects
    )
    return SiteModel.model_validate(
        {**site.model_dump(mode="python"), "evidence_refs": links or None}
    )


def consented_bm_site(base: SiteModel, *, mec_kw: float = 4_000.0) -> SiteModel:
    """A BM-consented site with an agreed rate, as `tests/test_valuation_consent_pricing.py`."""
    return SiteModel.model_validate(
        {
            **base.model_dump(mode="python"),
            "stream_consents": (
                s.RevenueStreamConsent(
                    stream=BM,
                    consented=True,
                    agreed_rate_gbp=10_000.0,
                    agreed_rate_unit=RateUnit.GBP_PER_MW_YEAR,
                ),
            ),
            "connection": base.connection.model_copy(update={"mec_kw": mec_kw}),
        }
    )


def schedule(power_kw: float = 4_000.0) -> HalfHourlyDispatchSchedule:
    n = 48
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=np.zeros(n),
        discharge_kw=np.zeros(n),
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=np.zeros(n, dtype=bool),
        energy_price_gbp_per_kwh=None,
        deliverable_capacity_mw=2.0,
        tender_window_discharge_mwh=None,
    )


def _find(streams: list[ValueStreamResult], sid: MarketStream) -> ValueStreamResult | None:
    return next((v for v in streams if v.stream == sid), None)


def value_bm(
    site: SiteModel, lib: AssumptionLibrary, gate: UnderwritingGate | None
) -> tuple[ValueStreamResult | None, ValueStreamResult | None]:
    """``(BM in the floor list, BM in gated upside)`` from the engine valuation."""
    reg = None if gate is None else underwriting_registry(gate)
    floor, upside, _ = build_value_streams(schedule(), site, lib, registry=reg)
    return _find(floor, BM), _find(upside, BM)


# ====================================================================== ACCEPTANCE 1


def test_control_bm_is_priced_into_the_floor_on_the_default_registry(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The control the acceptance test needs: on the default path BM IS priced (CONTRACTED)."""
    bm, upside = value_bm(consented_bm_site(make_site()), lib, gate=None)
    assert bm is not None and upside is None
    assert bm.firmness is Firmness.CONTRACTED and is_priced_floor(bm)
    assert bm.annual_revenue_gbp == 40_000.0


def test_acceptance_1_bm_without_its_evidence_is_not_priced(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """ACCEPTANCE 1. Same site, same schedule, gated registry, no evidence: not in the floor."""
    gate = UnderwritingGate(basis(), evidence=())
    in_floor, upside = value_bm(consented_bm_site(make_site()), lib, gate)
    assert in_floor is None, "an un-evidenced BM must not be in the priced floor"
    assert upside is not None
    assert upside.firmness is Firmness.CONDITIONAL and not is_priced_floor(upside)
    assert upside.gate_status == "conditional"
    assert upside.unlock_condition is not None
    for t in BM_TYPES:
        assert f"provide an IC-4 {t.value}" in upside.unlock_condition
    assert upside.warnings[0].startswith("UNDERWRITING BASIS (underwriting_basis_v1): ")
    assert DEFAULT_NOTES_DELIMITER in upside.warnings


def test_positive_control_bm_with_every_required_object_is_priced(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """With every required (synthetic) object held, VALID and verified, BM prices as before."""
    objs = bm_evidence()
    gate = UnderwritingGate(basis(), evidence=objs)
    bm, upside = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert upside is None and bm is not None
    assert bm.firmness is Firmness.CONTRACTED and bm.annual_revenue_gbp == 40_000.0
    assert any(SYNTHETIC_LABEL in w for w in bm.warnings)


@pytest.mark.parametrize("missing", BM_TYPES, ids=lambda t: t.value)
def test_removing_any_one_required_object_holds_bm_out(
    missing: EvidenceType, make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    objs = [o for o in bm_evidence() if o.evidence_type is not missing]
    gate = UnderwritingGate(basis(), evidence=objs)
    in_floor, upside = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert in_floor is None and upside is not None
    assert upside.firmness is Firmness.CONDITIONAL
    assert upside.unlock_condition is not None
    assert f"provide an IC-4 {missing.value}" in upside.unlock_condition
    others = [t for t in BM_TYPES if t is not missing]
    assert not any(f"provide an IC-4 {t.value}" in upside.unlock_condition for t in others)


@pytest.mark.parametrize(
    ("kw", "clause"),
    [
        ({"expires": date(2026, 6, 30)}, "renew the bmu_registration"),
        ({"effective": date(2027, 1, 1)}, "not yet effective"),
        ({"verified": False}, "verify the bmu_registration against its issuer"),
        ({"expires": "unknown"}, "effective-from and expiry dates"),
    ],
    ids=["expired", "not_yet_effective", "unverified", "validity_unknown"],
)
def test_held_but_not_admissible_evidence_is_conditional(
    kw: dict[str, Any], clause: str, make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """D93: only VALID and verified admits. Each lesser status names its own unlock."""
    objs = [o for o in bm_evidence() if o.evidence_type is not EvidenceType.BMU_REGISTRATION]
    objs.append(fx(EvidenceType.BMU_REGISTRATION, BM, tag="variant", **kw))
    gate = UnderwritingGate(basis(), evidence=objs)
    in_floor, upside = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert in_floor is None and upside is not None
    assert upside.unlock_condition is not None and clause in upside.unlock_condition
    if kw.get("expires") == "unknown":
        assert any(w.startswith("DATA ASK: the effective-from") for w in upside.warnings)


def test_registered_only_refuses_synthetic_fixtures(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """D97: the default-safe mode refuses fixtures, as the IC-4 README requires."""
    objs = bm_evidence()
    gate = UnderwritingGate(basis("registered_only"), evidence=objs)
    in_floor, upside = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert in_floor is None and upside is not None
    assert any("synthetic schema-test fixture, refused" in w for w in upside.warnings)


def test_an_object_in_hand_but_not_referenced_by_the_site_does_nothing(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """D96: only objects the site names by hash (so inside ``input_hash``) can price."""
    gate = UnderwritingGate(basis(), evidence=bm_evidence())
    in_floor, upside = value_bm(consented_bm_site(make_site()), lib, gate)
    assert in_floor is None and upside is not None


def test_a_referenced_object_not_in_hand_is_absent_not_an_error(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    objs = bm_evidence()
    gate = UnderwritingGate(basis(), evidence=objs[1:])
    in_floor, upside = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert in_floor is None and upside is not None
    assert any("not in hand, so it is treated as ABSENT" in w for w in upside.warnings)


def test_evidence_for_another_site_does_not_cover_this_one(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    objs = bm_evidence(site_id="SYNTHETIC-OTHER-SITE")
    gate = UnderwritingGate(basis(), evidence=objs)
    in_floor, _ = value_bm(with_refs(consented_bm_site(make_site()), objs), lib, gate)
    assert in_floor is None


@pytest.mark.parametrize("stream", [DFS, DCDMDR], ids=lambda m: m.value)
def test_a_requirement_ic4_cannot_type_excludes_the_stream_at_zero(
    stream: MarketStream, make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """G6: no IC-4 type for DFS or Dynamic Response registration. UNKNOWN, GBP 0, data ask."""
    objs = [fx(t, stream) for t in EvidenceType]
    site = with_refs(consented_bm_site(make_site()), objs)
    gate = UnderwritingGate(basis(), evidence=objs)
    floor, upside, _ = build_value_streams(
        schedule(), site, lib, registry=underwriting_registry(gate)
    )
    v = _find(floor, stream) or _find(upside, stream)
    assert v is not None
    assert v.firmness is Firmness.EXCLUDED and v.annual_revenue_gbp == 0.0
    assert v.gate_status == "unknown"
    assert any(w.startswith("DATA ASK (IC-4 gap G6)") for w in v.warnings)


def test_tighten_never_loosens_any_gate() -> None:
    """D90: the result is exactly the stricter of the inner gate and the verdict."""
    rank = {GateStatus.OPEN: 0, GateStatus.CONDITIONAL: 1, GateStatus.UNKNOWN: 2}
    rank[GateStatus.CLOSED] = 3
    firm = {
        GateStatus.OPEN: Firmness.CONTRACTED,
        GateStatus.CONDITIONAL: Firmness.CONDITIONAL,
        GateStatus.UNKNOWN: Firmness.EXCLUDED,
        GateStatus.CLOSED: Firmness.EXCLUDED,
    }
    for inner_status in rank:
        inner = GateResult(
            stream=BM,
            gate_status=inner_status,
            can_be_priced_now=inner_status is GateStatus.OPEN,
            firmness=firm[inner_status],
            unlock_condition="inner unlock" if inner_status is GateStatus.CONDITIONAL else None,
            warnings=["D57 ASSUMED APPROVED: inner prose"],
        )
        for outcome in ("admitted", "conditional", "unknown"):
            verdict = StreamVerdict(
                stream=BM,
                outcome=outcome,  # type: ignore[arg-type]
                findings=(),
                missing=(),
                unlock=() if outcome == "admitted" else ("evidence unlock",),
                data_asks=(),
                relied_on=(),
            )
            out = tighten(inner, verdict)
            assert rank[out.gate_status] == max(rank[inner_status], rank[verdict.gate_status])
            assert out.can_be_priced_now == (out.gate_status is GateStatus.OPEN)
            # Inner prose is kept verbatim, scoped under the delimiter (D101).
            i = out.warnings.index(DEFAULT_NOTES_DELIMITER)
            assert out.warnings[i + 1] == "D57 ASSUMED APPROVED: inner prose"
            assert out.warnings[0].startswith("UNDERWRITING BASIS (underwriting_basis_v1)")


def test_a_stream_the_table_does_not_name_is_unknown(
    make_site: Callable[..., SiteModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(STREAM_RULES, FLEX)
    verdict = UnderwritingGate(basis()).verdict(FLEX, make_site())
    assert verdict.outcome == "unknown" and not verdict.admits


# ---------------------------------------------------------------------- size minimums


def _site_mw(make_site: Callable[..., SiteModel], mw: float) -> SiteModel:
    base = make_site()
    conn = base.connection.model_copy(update={"mic_kw": mw * 1000.0, "mec_kw": None})
    return SiteModel.model_validate({**base.model_dump(mode="python"), "connection": conn})


def test_a_sub_minimum_unit_needs_a_stream_scoped_aggregation_route(
    make_site: Callable[..., SiteModel],
) -> None:
    """D94: 0.3 MW cannot be a 1 MW BM Unit without an evidenced GSP-Group route."""
    small = _site_mw(make_site, 0.3)
    site_wide = fx(EvidenceType.AGGREGATOR_AGREEMENT, None, tag="site-wide")
    base_objs = [
        o for o in bm_evidence() if o.evidence_type is not EvidenceType.AGGREGATOR_AGREEMENT
    ]

    # A whole-site agreement satisfies the route-to-market row but is not an aggregation route.
    objs = [*base_objs, site_wide]
    v = UnderwritingGate(basis(), objs).verdict(BM, with_refs(small, objs))
    assert v.outcome == "conditional"
    assert any("below the 1 MW minimum" in u for u in v.unlock)
    assert any("The minimum size for a Balancing Mechanism Unit is 1MW." in f for f in v.findings)
    assert any(f.startswith("CONTRADICTION") for f in v.findings)

    objs = [*base_objs, fx(EvidenceType.AGGREGATOR_AGREEMENT, BM)]
    v = UnderwritingGate(basis(), objs).verdict(BM, with_refs(small, objs))
    assert v.outcome == "admitted", v.unlock
    assert any("Secondary BM Unit aggregated within one Grid Supply Point" in f for f in v.findings)


def test_a_unit_at_the_minimum_needs_no_route(make_site: Callable[..., SiteModel]) -> None:
    objs = bm_evidence()
    v = UnderwritingGate(basis(), objs).verdict(BM, with_refs(_site_mw(make_site, 1.0), objs))
    assert v.outcome == "admitted"


@pytest.mark.parametrize(("mw", "meets"), [(0.1, True), (0.15, True), (0.0999, False)])
def test_dfs_minimum_is_read_on_the_rounded_down_registered_capacity(
    mw: float, meets: bool, make_site: Callable[..., SiteModel]
) -> None:
    """NESO DFS rules 4.2: 0.1 MW, to one decimal place, rounded down (as `gb_streams` does)."""
    v = UnderwritingGate(basis()).verdict(DFS, _site_mw(make_site, mw))
    met = any("meets the 0.1 MW minimum" in f for f in v.findings)
    assert met is meets
    assert (gb_streams.dfs_registered_unit_capacity_mw(mw) is not None) is meets


def test_unarmed_minimums_do_not_block_and_carry_their_data_ask(
    make_site: Callable[..., SiteModel],
) -> None:
    objs = [
        fx(EvidenceType.DNO_FLEXIBILITY_CONTRACT, FLEX),
        fx(EvidenceType.CUSTOMER_CONSENT, FLEX),
    ]
    v = UnderwritingGate(basis(), objs).verdict(FLEX, with_refs(_site_mw(make_site, 0.05), objs))
    assert v.outcome == "admitted"
    assert any(a.startswith("DATA ASK: no retrieved published source") for a in v.data_asks)


# ---------------------------------------------------------------------- stacking


def test_dfs_is_demoted_where_the_site_holds_a_bm_unit(make_site: Callable[..., SiteModel]) -> None:
    """DFS rules 4.7.6 binds DFS, so DFS is the stream held out, whatever either is worth."""
    site = _site_mw(make_site, 2.0)
    bmu = [fx(EvidenceType.BMU_REGISTRATION, BM)]
    held = UnderwritingGate(basis(), bmu).verdict(DFS, with_refs(site, bmu))
    clear = UnderwritingGate(basis()).verdict(DFS, site)
    assert "stacking: dfs_x_bm" in held.missing
    assert "stacking: dfs_x_bm" not in clear.missing
    assert any("shall not form part of a BM Unit" in f or "4.7.6" in f for f in held.findings)
    assert any("Supplier Base BM Unit" in a for a in held.data_asks)


def test_every_stacking_rule_cites_retrieved_sources() -> None:
    for rule in STACKING_RULES:
        assert rule.source_ids and all(sid in SOURCES for sid in rule.source_ids)
        if rule.status == "exclusive_until_answered":
            assert rule.data_ask and "DATA ASK" in rule.data_ask


# ====================================================================== the post-pass, end to end


CM = MarketStream.CAPACITY_MARKET
ARB = MarketStream.TARIFF_ARBITRAGE

#: What the stub reports as priced into the floor, by stream: (GBP/yr, firmness).
_PRICES: dict[MarketStream, tuple[float, Firmness]] = {
    BM: (40_000.0, Firmness.CONTRACTED),
    FLEX: (5_000.0, Firmness.CONTRACTED),
    CM: (6_000.0, Firmness.CONTRACTED),
    ARB: (10_000.0, Firmness.FIRM),
}


class _PricedStub(BaselineEngine):
    """A `BaselineEngine` that reports the ``priced`` streams in the floor, as an engine might.

    It ignores the gated registry on purpose: it stands for an inner engine that did not read the
    gates, so these tests pin the post-pass guard alone. No MILP is solved (seconds, not minutes).
    ``notes`` names the streams that carry the D57 shared-budget note.
    """

    def __init__(
        self,
        registry: object,
        priced: tuple[MarketStream, ...] = (BM, CM, ARB),
        notes: tuple[MarketStream, ...] = (),
    ) -> None:
        self.registry_seen = registry
        self._priced = priced
        self._notes = notes

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        base = super().solve(request)
        floor = [
            ValueStreamResult(
                stream=sid,
                annual_revenue_gbp=_PRICES[sid][0],
                firmness=_PRICES[sid][1],
                gate_status="open",
                warnings=[
                    "stub: priced",
                    *(
                        [f"{D57_BUDGET_NOTE_PREFIX}: synthetic stub note"]
                        if sid in self._notes
                        else []
                    ),
                ],
            )
            for sid in self._priced
        ]
        keep = [v for v in base.value_streams if v.stream not in self._priced]
        upside = [v for v in base.gated_upside if v.stream not in self._priced]
        return base.model_copy(update={"value_streams": [*floor, *keep], "gated_upside": upside})


def _engine(objs: list[EvidenceObject], **stub: Any) -> UnderwritingEngine:
    return UnderwritingEngine(basis(), objs, engine_factory=lambda reg: _PricedStub(reg, **stub))


def _all_evidence() -> list[EvidenceObject]:
    flex = [
        fx(EvidenceType.DNO_FLEXIBILITY_CONTRACT, FLEX),
        fx(EvidenceType.CUSTOMER_CONSENT, FLEX),
    ]
    return [*bm_evidence(), *flex]


def test_acceptance_1_through_generate_quote(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """ACCEPTANCE 1 on the production entry point: no evidence, no market GBP in the floor."""
    site = consented_bm_site(make_site())
    quote = generate_quote(site, lib, engine=_engine([])).quote
    result = quote.optimisation_result
    assert result is not None
    # The stub reported BM and CM in the floor; only bill-side arbitrage survives. BM is the only
    # budget-sharing stream, so the shared-budget check cannot be what removed it.
    floor = [v for v in result.value_streams if is_priced_floor(v)]
    assert {v.stream for v in floor} == {ARB}
    bm = _find(result.gated_upside, BM)
    assert bm is not None and bm.firmness is Firmness.CONDITIONAL
    assert bm.annual_revenue_gbp == 40_000.0  # shown as upside, never as floor
    cm = _find(result.value_streams, CM)
    assert cm is not None and cm.firmness is Firmness.CONDITIONAL
    assert cm.unlock_condition is not None and "capacity_agreement" in cm.unlock_condition
    assert quote.study_basis_id == BASIS_ID
    assert quote.warnings[0].startswith(f"{UnderwritingBasis.LABEL}. ")
    assert result.evidence_refs is None


def test_the_engine_hands_the_gated_registry_to_the_inner_engine() -> None:
    eng = _engine([])
    inner = eng._inner
    assert isinstance(inner, _PricedStub) and inner.registry_seen is eng.registry
    assert len(eng.registry) == len(default_registry())


def test_the_default_engine_is_a_milp_reading_the_gated_registry_on_every_solver() -> None:
    """The production wiring, built but not solved: every MILP solver the sizing loop and the final
    dispatch use reads the gated registry, and the D46 export-in-floor study solver is not chosen
    (D90). A real solve of this wiring is the slow smoke recorded in the changelog."""
    from gridq.optimisation.engine import MilpEngine, _StudyBasisDispatchSolver

    eng = UnderwritingEngine(basis())
    inner = eng._inner
    assert isinstance(inner, MilpEngine)
    assert inner._study_basis is None
    assert not isinstance(inner._sizing_solver, _StudyBasisDispatchSolver)
    assert inner._registry is eng.registry
    assert inner._sizing_solver._registry is eng.registry
    assert inner._dispatch_solver._registry is eng.registry


def test_admitted_streams_are_stamped_with_the_evidence_they_rest_on(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    objs = bm_evidence()
    site = with_refs(_site_mw(make_site, 4.0), objs)
    eng = _engine(objs)
    result = eng.solve(eng.build_optimisation_request(site, lib))
    bm = _find(result.value_streams, BM)
    assert bm is not None and bm.firmness is Firmness.CONTRACTED
    links = result.evidence_refs or ()
    assert {link.evidence.evidence_hash for link in links if link.supports == BM.value} == {
        o.content_hash() for o in objs
    }
    assert result.bankable_status is s.BankableStatus.NOT_BANKABLE
    again = eng.apply(result, site)
    assert hash_json(again.model_dump(mode="json")) == hash_json(result.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("notes", "demoted"),
    [
        ((BM,), {BM}),
        ((BM, FLEX), {BM, FLEX}),
        ((), {BM, FLEX}),  # no recognisable note: fails closed (D100)
    ],
    ids=["bm_cut", "both_cut", "no_note_fails_closed"],
)
def test_shared_power_budget_is_held_out_of_the_floor(
    notes: tuple[MarketStream, ...],
    demoted: set[MarketStream],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    objs = _all_evidence()
    site = with_refs(_site_mw(make_site, 4.0), objs)
    eng = _engine(objs, priced=(BM, FLEX, ARB), notes=notes)
    result = eng.solve(eng.build_optimisation_request(site, lib))
    priced = {v.stream for v in result.value_streams if is_priced_floor(v)}
    assert ARB in priced
    for sid in (BM, FLEX):
        assert (sid not in priced) is (sid in demoted)
    for sid in demoted:
        v = _find([*result.value_streams, *result.gated_upside], sid)
        assert v is not None and "WP-SIZE-03" in (v.unlock_condition or "")


def test_one_budget_sharing_stream_is_not_held_for_the_budget(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    objs = bm_evidence()  # local flex unevidenced, so only BM is in the floor
    site = with_refs(_site_mw(make_site, 4.0), objs)
    eng = _engine(objs, priced=(BM, FLEX, ARB), notes=(BM,))
    result = eng.solve(eng.build_optimisation_request(site, lib))
    bm = _find(result.value_streams, BM)
    assert bm is not None and is_priced_floor(bm)


def test_building_the_engine_registers_the_plugin_once() -> None:
    """D102: the constructor loads the plug-in; a second build is a no-op."""
    assert BASIS_ID not in ext.study_bases._snapshot()
    _engine([])
    _engine([])
    reg = ext.study_bases.registration(BASIS_ID)
    assert (reg.owner, reg.version, reg.builtin) == (PLUGIN, UW_VERSION, False)
    assert sum(p.owner == PLUGIN for p in ext.loaded_plugins()) == 1


# ====================================================================== ACCEPTANCE 2


def _canonical(site: SiteModel, lib: AssumptionLibrary, b: Any) -> dict[str, object]:
    result = BaselineEngine().solve(BaselineEngine().build_optimisation_request(site, lib))
    return _canonical_run_input(site, result, None, lib, None, study_basis=b)


def test_acceptance_2_the_bases_never_share_a_run_id(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """ACCEPTANCE 2. Same site, same result, same library: the identities differ, three ways."""
    ext.load_plugin(PLUGIN)
    site = make_site()
    uw = _canonical(site, lib, basis("registered_only"))
    d55 = _canonical(site, lib, all_streams())
    assert hash_json(uw) != hash_json(d55)
    assert uw["study_basis"]["basis_id"] == BASIS_ID  # type: ignore[index]
    assert d55["study_basis"]["basis_id"] == "all_streams_enabled_study_v1"  # type: ignore[index]
    assert uw["study_basis_extension"] == {"owner": PLUGIN, "version": UW_VERSION}
    assert "study_basis_extension" not in d55

    result = BaselineEngine().solve(BaselineEngine().build_optimisation_request(site, lib))
    rec_uw = assemble_quote_record(site, result, None, lib, study_basis=basis("registered_only"))
    rec_d55 = assemble_quote_record(site, result, None, lib, study_basis=all_streams())
    assert rec_uw.input_hash != rec_d55.input_hash
    assert rec_uw.quote_id != rec_d55.quote_id
    assert (rec_uw.study_basis_id, rec_d55.study_basis_id) == (
        BASIS_ID,
        "all_streams_enabled_study_v1",
    )


def test_the_basis_id_cannot_be_the_academic_one() -> None:
    with pytest.raises(ValueError):
        UnderwritingBasis.model_validate(
            {**basis().model_dump(), "basis_id": "all_streams_enabled_study_v1"}
        )
    assert UnderwritingBasis.LABEL != s.AllStreamsEnabledStudyBasis.LABEL


@pytest.mark.parametrize(
    "change",
    [{"as_of": date(2026, 11, 27)}, {"evidence_admission": "registered_only"}],
    ids=["as_of", "evidence_admission"],
)
def test_every_basis_field_that_moves_a_number_moves_the_identity(
    change: dict[str, Any], make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    ext.load_plugin(PLUGIN)
    site = make_site()
    a = basis()
    b = UnderwritingBasis.model_validate({**a.model_dump(), **change})
    assert hash_json(_canonical(site, lib, a)) != hash_json(_canonical(site, lib, b))


def test_unregistered_the_basis_is_refused_rather_than_misidentified(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    with pytest.raises(Exception, match="not registered"):
        _canonical(make_site(), lib, basis())


# ====================================================================== ACCEPTANCE 3


def test_acceptance_3_the_plugin_moves_no_academic_identity_or_default_gate(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """ACCEPTANCE 3. Loading the plug-in and building the gated registry changes no academic run.

    The pinned goldens (`tests/test_determinism_golden.py`,
    `tests/test_study_basis_hash_identity.py`, `tests/test_all_streams_study_basis.py`) are not
    edited by WP-UW-01; this pins the mechanism by which they could have moved: the academic
    canonical inputs and the default gates.
    """
    site = consented_bm_site(make_site())
    bases = (None, all_streams())
    before = [hash_json(_canonical(site, lib, b)) for b in bases]
    gates_before = [g.check_gates(site, lib).model_dump() for g in default_registry().all()]

    ext.load_plugin(PLUGIN)
    gated = underwriting_registry(UnderwritingGate(basis(), bm_evidence()))
    _ = [g.check_gates(site, lib) for g in gated.all()]

    assert [hash_json(_canonical(site, lib, b)) for b in bases] == before
    assert [g.check_gates(site, lib).model_dump() for g in default_registry().all()] == gates_before
    assert ext.study_bases.registration("all_streams_enabled_study_v1").builtin


# ====================================================================== labelling


def test_the_label_meets_the_renderer_contract() -> None:
    label = UnderwritingBasis.LABEL
    assert label.startswith("STUDY BASIS (") and ". " not in label
    for b in (basis(), basis("registered_only")):
        assert b.note().startswith(f"{label}. ")
    assert SYNTHETIC_LABEL in basis().note()
    assert SYNTHETIC_LABEL not in basis("registered_only").note()
    assert "WP-SIZE-03" in basis().note()


def test_a_fresh_process_without_the_plugin_still_renders_the_banner() -> None:
    """The persisted note carries the label; a renderer needs no plug-in to show it."""
    code = (
        "import json, sys\n"
        "import gridq.extensions as ext\n"
        "from gridq.outputs.viewmodel import detect_study_basis\n"
        "from gridq.schemas import DataTier, QuoteMode, QuoteRecord\n"
        "rec = QuoteRecord(quote_id='q', quote_mode=QuoteMode.DRAFT, site_id='s',\n"
        "    data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,\n"
        "    study_basis_id='underwriting_basis_v1', warnings=[sys.argv[1]])\n"
        "view = detect_study_basis(rec)\n"
        "print(json.dumps([view.present, view.label, 'gridq.underwriting' in\n"
        "    [p.owner for p in ext.loaded_plugins()]]))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    out = subprocess.run(
        [sys.executable, "-c", code, basis().note()],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=REPO,
    )
    present, label, loaded = json.loads(out.stdout.strip().splitlines()[-1])
    assert present is True and loaded is False
    assert label == UnderwritingBasis.LABEL


# ====================================================================== the basis contract


def test_the_basis_class_meets_the_named_basis_contract() -> None:
    assert basis_class_problems(UnderwritingBasis, expected_id=BASIS_ID) == []
    for name in ("basis_id", "as_of", "evidence_admission", "authorised_by"):
        assert UnderwritingBasis.model_fields[name].is_required(), name


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence_ceiling": ConfidenceBand.MEDIUM},
        {"rule_table_hash": "sha256:" + "0" * 64},
        {"rule_table_version": "0.9.0"},
        {"authorised_by": "  "},
        {"evidence_admission": "everything"},
        {"invented_field": 1},
    ],
    ids=["ceiling", "table_hash", "table_version", "blank_author", "admission", "extra"],
)
def test_the_basis_refuses(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        UnderwritingBasis.model_validate({**basis().model_dump(), **bad})


# ====================================================================== the rule table


def test_every_market_stream_has_a_row() -> None:
    assert set(STREAM_RULES) == set(MarketStream)


def test_every_row_says_where_it_comes_from() -> None:
    for row in STREAM_RULES.values():
        for req in row.requirements:
            if req.row_basis is RowBasis.PUBLISHED_RULE:
                assert req.citation in SOURCES, (row.stream, req)
            else:
                assert req.citation.strip()
            if req.evidence_type is None:
                assert req.data_ask and req.data_ask.startswith("DATA ASK (IC-4 gap G6)")
        if row.size is not None:
            if row.size.min_mw is None:
                assert row.size.data_ask and row.size.source_id is None
            else:
                assert row.size.source_id in SOURCES
                assert row.size.aggregation_source_id in SOURCES


def test_the_quoted_minimums_equal_the_engine_constants() -> None:
    """The table and `gb_streams` read the same published minimums; a drift is a red test."""
    for sid in (BM, DCDMDR, MarketStream.CAPACITY_MARKET):
        size = STREAM_RULES[sid].size
        assert size is not None and size.min_mw == gb_streams.eligibility_threshold_mw(sid)
    dfs = STREAM_RULES[DFS].size
    assert dfs is not None and dfs.min_mw == gb_streams._DFS_MIN_UNIT_MW


def test_every_source_is_a_quote_from_a_retrieved_document() -> None:
    for sid, src in SOURCES.items():
        assert src.source_id == sid and src.quote.strip() and src.locator.strip()
        assert src.url.startswith("https://")


def test_the_rule_table_hash_is_the_hash_of_the_table() -> None:
    assert RULE_TABLE_HASH == hash_json(rule_table_document())
    assert basis().rule_table_hash == RULE_TABLE_HASH
