"""Tests for the versioned assumption library."""

from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from gridq.assumptions import (
    SECTION_NAMES,
    Assumption,
    AssumptionError,
    AssumptionStatus,
    load_assumptions,
)
from gridq.assumptions.loader import ASSUMPTIONS_DIR
from gridq.schemas.enums import Confidence


def test_gb_v0_1_loads_with_all_sections() -> None:
    lib = load_assumptions("gb_v0_1")
    assert lib.metadata.version == "gb_v0_1"
    assert lib.metadata.market == "GB"
    # every required section is present and non-empty
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"section {section} is empty"
    assert lib.get("battery_capex", "installed_cost").status == AssumptionStatus.PLACEHOLDER


def test_missing_assumption_version_fails() -> None:
    with pytest.raises(AssumptionError):
        load_assumptions("does_not_exist_v9")


def _assumption_kwargs(**drop: object) -> dict:
    kwargs: dict = dict(
        value=500000,
        unit="GBP/MW",
        source="placeholder",
        status=AssumptionStatus.PLACEHOLDER,
        owner_or_reviewer="Oli",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 6, 17),
        notes="x",
    )
    for k in drop:
        kwargs.pop(k, None)
    return kwargs


@pytest.mark.parametrize("missing", ["source", "status", "confidence"])
def test_missing_required_field_fails(missing: str) -> None:
    kwargs = _assumption_kwargs()
    del kwargs[missing]
    with pytest.raises(ValidationError):
        Assumption(**kwargs)


def test_valid_assumption_constructs() -> None:
    a = Assumption(**_assumption_kwargs())
    assert a.status == AssumptionStatus.PLACEHOLDER
    assert a.confidence == Confidence.LOW


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Assumption(**_assumption_kwargs(), bogus=1)  # type: ignore[arg-type]


def test_invalid_status_rejected() -> None:
    kwargs = _assumption_kwargs()
    kwargs["status"] = "totally_made_up"
    with pytest.raises(ValidationError):
        Assumption(**kwargs)


# --- gb_v1_0 release (W4-3: sourced provenance pass) -----------------------
#
# gb_v1_0 is the sourced successor to gb_v0_1: values backed by a citable source carry it
# inline; everything else stays an honest placeholder/needs_review (never invented). These
# counts are locked literals so any future drift in the release is caught.

# 25 (W4-3 sourced pass) + L02 quote_validity.reprice_tolerance_pct
# + L14 battery_capex.installed_cost_benchmark_band — both placeholders.
_V1_0_TOTAL_ENTRIES = 27
_V1_0_NEEDS_REVIEW = 10
_V1_0_PLACEHOLDER = 17
_V1_0_CONFIRMED = 0

# The only values gb_v1_0 populates from a real, in-repo citation (the Neura business plan);
# everything else stays an honest placeholder/needs_review (no number invented).
_IN_REPO_SOURCED = (("battery_capex", "installed_cost"), ("financing", "target_equity_irr_pct"))


def _v1_0_entries() -> list[tuple[str, str, Assumption]]:
    lib = load_assumptions("gb_v1_0")
    return [(s, name, a) for s in SECTION_NAMES for name, a in getattr(lib, s).items()]


def test_gb_v1_0_loads_with_all_sections() -> None:
    lib = load_assumptions("gb_v1_0")
    assert lib.metadata.version == "gb_v1_0"
    assert lib.metadata.market == "GB"
    assert lib.metadata.released == date(2026, 6, 24)
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"section {section} is empty"


def test_gb_v1_0_entry_count_locked() -> None:
    # W4-3 carried the values forward; L02 adds quote_validity.reprice_tolerance_pct.
    assert len(_v1_0_entries()) == _V1_0_TOTAL_ENTRIES


def test_gb_v1_0_every_entry_has_full_provenance() -> None:
    # The library's core promise: no number is undocumented. Every entry carries a
    # non-empty source + notes and a valid confidence/status enum.
    for section, name, a in _v1_0_entries():
        where = f"{section}.{name}"
        assert a.source.strip(), f"{where} has an empty source"
        assert a.notes.strip(), f"{where} has empty notes"
        assert a.confidence in Confidence, f"{where} has an invalid confidence"
        assert a.status in AssumptionStatus, f"{where} has an invalid status"


def test_gb_v1_0_no_bare_placeholder_source_survives() -> None:
    # gb_v0_1 used the bare token "placeholder" as a source; W4-3 replaces every one with
    # either a real citation, an honest "no citable source identified" tag, or "(gated)".
    for section, name, a in _v1_0_entries():
        assert a.source.strip().lower() != "placeholder", f"{section}.{name} still bare placeholder"


def test_gb_v1_0_in_repo_sourced_values_carry_citation_and_are_promoted() -> None:
    # The values W4-3 actually populates from a citable in-repo source carry that source inline
    # (so an output/frontend can show where the number came from) and are promoted past raw
    # placeholder to needs_review with an honest confidence.
    lib = load_assumptions("gb_v1_0")
    for section, name in _IN_REPO_SOURCED:
        a = lib.get(section, name)
        assert "neura" in a.source.lower(), f"{section}.{name} should cite the in-repo Neura plan"
        assert a.confidence in Confidence
        assert a.status == AssumptionStatus.NEEDS_REVIEW, (
            f"{section}.{name} has a real citation but was left a raw placeholder"
        )


def test_gb_v1_0_placeholders_remain_flagged() -> None:
    # Nothing in this release is confirmed: every entry stays placeholder or needs_review, so no
    # quote built on gb_v1_0 can be presented as bankable.
    for section, name, a in _v1_0_entries():
        assert a.status in (AssumptionStatus.PLACEHOLDER, AssumptionStatus.NEEDS_REVIEW), (
            f"{section}.{name} is {a.status.value}, but gb_v1_0 confirms nothing"
        )


def test_gb_v1_0_status_counts_reported_and_locked() -> None:
    statuses = [a.status for _, _, a in _v1_0_entries()]
    needs_review = sum(s == AssumptionStatus.NEEDS_REVIEW for s in statuses)
    placeholder = sum(s == AssumptionStatus.PLACEHOLDER for s in statuses)
    confirmed = sum(s == AssumptionStatus.CONFIRMED for s in statuses)
    # Reported (visible in -v output) and locked so the honest mix can't silently drift.
    print(
        f"gb_v1_0 status mix: needs_review={needs_review} "
        f"placeholder={placeholder} confirmed={confirmed}"
    )
    assert needs_review == _V1_0_NEEDS_REVIEW
    assert placeholder == _V1_0_PLACEHOLDER
    assert confirmed == _V1_0_CONFIRMED


def test_gb_v1_0_gated_streams_stay_placeholder_zero() -> None:
    # Gated upside (wholesale/BM/ancillary) is never populated — placeholder zero, needs_review,
    # and the "(gated)" signal preserved in the source string.
    lib = load_assumptions("gb_v1_0")
    for section in ("wholesale_p415", "balancing_mechanism", "ancillary_services"):
        (a,) = getattr(lib, section).values()
        assert a.value == 0, f"{section} gated stream must stay zero"
        assert a.status == AssumptionStatus.NEEDS_REVIEW
        assert "gated" in a.source.lower(), f"{section} lost its '(gated)' signal"


def test_gb_v1_0_round_trip_citation_corrected() -> None:
    # The integrity fix: gb_v0_1 cited a round-trip-loss figure the source doc never states.
    # gb_v1_0 keeps 0.88 but labels it honestly as NOT sourced.
    a = load_assumptions("gb_v1_0").get("round_trip_efficiency", "ac_round_trip")
    assert a.value == 0.88
    assert a.status == AssumptionStatus.PLACEHOLDER
    assert "not sourced" in a.source.lower()


def test_gb_v0_1_round_trip_citation_left_frozen() -> None:
    # gb_v0_1 is frozen for provenance — W4-3 must NOT edit it. Prove the original (unsupported)
    # citation is still there, so the correction lives only in gb_v1_0.
    a = load_assumptions("gb_v0_1").get("round_trip_efficiency", "ac_round_trip")
    assert a.source == "system_requirements (≈12% round-trip loss)"


def test_loader_rejects_entry_missing_provenance_field(tmp_path) -> None:
    # The loader validates via AssumptionLibrary.model_validate, so a value missing a required
    # provenance field raises pydantic ValidationError (NOT AssumptionError, which covers only
    # missing-file / parse / non-dict-top-level). Feed a tmp dir via base_dir.
    raw = yaml.safe_load((ASSUMPTIONS_DIR / "gb_v1_0.yaml").read_text(encoding="utf-8"))
    del raw["battery_capex"]["installed_cost"]["source"]
    (tmp_path / "gb_v1_0.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_assumptions("gb_v1_0", base_dir=tmp_path)


def test_loader_missing_file_raises_assumption_error(tmp_path) -> None:
    # The negative twin: a genuinely-absent version is an AssumptionError, not a ValidationError —
    # documenting the two distinct failure modes so the test above can't pass for the wrong reason.
    with pytest.raises(AssumptionError):
        load_assumptions("gb_v1_0", base_dir=tmp_path)
