"""WP-UW-07 (A6-07) — `gridq.tariffs.hv_tariff_model` against the DNO's own published documents.

**Acceptance 2 (a published CDCM example reproduces to the penny, each band priced as itself).**
The example is Electricity North West's own published 2026/27 CDCM charging model: its 'Net revenue
summary' sheet carries, for LV and HV Site Specific Band 1, the charging volumes, the tariff and the
revenue each charge component raises, plus the typical bill per MPAN. Every input and every
expected figure below is read from ``tests/fixtures/dno_charging/`` (cell-by-cell extracts, the cell
named beside each value; provenance in ``data/fetched/dno_charging/``). No rate is typed in.

**Acceptance 1 (a published EDCM example) is NOT claimed here.** No DNO or DCUSA publishes EDCM
inputs together with the charges they produce: the populated EDCM models are withheld ("blank to
ensure customer data is kept confidential", ENWL) and ENWL's Charge Calculator is published empty.
The EDCM tests below hold the arithmetic to ENWL's own published calculator formulas and LC14 rules,
with **constructed** quantities that are labelled as such. They test the method, not the criterion;
the criterion stays unmet and is reported as data request DR-UW07-EDCM.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gridq.schemas import BandWindows, CdcmTariff, EdcmTariff
from gridq.tariffs.hv_tariff_model import (
    CDCM_REACTIVE_THRESHOLD,
    Avoidability,
    BillingMonth,
    CdcmVolumes,
    assert_unavoidable_lines_save_nothing,
    band_windows_mask,
    billing_determinants_from_series,
    cdcm_annual_charges,
    cdcm_band_labels_from_masks,
    cdcm_charges_from_series,
    chargeable_reactive_kvarh,
    edcm_charges_from_series,
    exceeded_capacity_kva,
    network_unit_rate_series,
    to_penny,
    windows_from_statement_cell,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "dno_charging"
_PROVENANCE = Path(__file__).resolve().parents[1] / "data" / "fetched" / "dno_charging"
_CDCM = json.loads((_FIXTURES / "enwl_cdcm_2026_27_site_specific.json").read_text())
_ANNEX1 = json.loads((_FIXTURES / "enwl_schedule_2026_27_annex1_extract.json").read_text())
_ANNEX2 = json.loads((_FIXTURES / "enwl_schedule_2026_27_annex2_edcm_extract.json").read_text())

#: CDCM model 'Net revenue summary' row names -> hv_tariff_model line components.
_COMPONENT = {
    "unit_rate_1": "red_unit",
    "unit_rate_2": "amber_unit",
    "unit_rate_3": "green_unit",
    "fixed_charge": "fixed",
    "capacity_charge": "capacity",
    "exceeded_capacity_charge": "exceeded_capacity",
    "reactive_power_charge": "reactive",
}


def _d(value: float | int) -> Decimal:
    """A published cell value as `Decimal`, through its shortest repr (as Excel displays it)."""
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def _schedule_tariff(name: str, voltage: str) -> CdcmTariff:
    """A CDCM tariff built from ENWL's published Annex 1 row (columns D-J), nothing else."""
    row = _ANNEX1["tariff_rows"][name]
    r, c = row["row"], row["cells"]
    return CdcmTariff(
        dno="ENWL",
        voltage=voltage,  # type: ignore[arg-type]
        llfc=c[f"B{r}"],
        charging_year="2026/27",
        source=f"{_ANNEX1['source_url']} (Annex 1 row {r})",
        red_p_per_kwh=_d(c[f"D{r}"]),
        amber_p_per_kwh=_d(c[f"E{r}"]),
        green_p_per_kwh=_d(c[f"F{r}"]),
        fixed_p_per_day=_d(c[f"G{r}"]),
        capacity_p_per_kva_day=_d(c[f"H{r}"]),
        exceeded_capacity_p_per_kva_day=_d(c[f"I{r}"]),
        reactive_p_per_kvarh=_d(c[f"J{r}"]),
    )


def _model_volumes(name: str) -> CdcmVolumes:
    v = {k: _d(cell["value"]) for k, cell in _CDCM["tariffs"][name]["volumes"].items()}
    return CdcmVolumes(
        red_mwh=v["rate_1_units_mwh"],
        amber_mwh=v["rate_2_units_mwh"],
        green_mwh=v["rate_3_units_mwh"],
        mpans=v["mpans"],
        import_capacity_kva=v["import_capacity_kva"],
        exceeded_capacity_kva=v["exceeded_capacity_kva"],
        reactive_mvarh=v["reactive_power_units_mvarh"],
    )


_SITE_SPECIFIC = [("LV Site Specific Band 1", "LV"), ("HV Site Specific Band 1", "HV")]


# ------------------------------------------------------------------ provenance


def test_fixtures_name_their_published_source_and_hash() -> None:
    sidecars = {
        p.stem.removesuffix(".provenance"): json.loads(p.read_text())
        for p in _PROVENANCE.glob("*.provenance.json")
    }
    model = sidecars["enwl_cdcm_v11_2026_27"]
    schedule = sidecars["enwl_schedule_of_charges_2026_27_v1_1"]
    assert _CDCM["sha256"] == model["sha256"] and _CDCM["source_url"] == model["source_url"]
    for extract in (_ANNEX1, _ANNEX2):
        assert extract["sha256"] == schedule["sha256"]
        assert extract["source_url"] == schedule["source_url"]
    for name, side in sidecars.items():
        assert len(side["sha256"]) == 64, name
        assert side["size_bytes"] > 0, name
        assert side["fetched_utc"].startswith("2026-11-26"), name
        assert side["source_url"].startswith("https://"), name
        assert side["licence"], name


# ------------------------------------------------------------------ acceptance 2: to the penny


@pytest.mark.parametrize(("name", "voltage"), _SITE_SPECIFIC)
def test_schedule_rates_are_the_cdcm_models_own_rates(name: str, voltage: str) -> None:
    """The published Annex 1 row and the CDCM model's tariff rows (55-61) state the same rates."""
    tariff = _schedule_tariff(name, voltage)
    model = {k: _d(cell["value"]) for k, cell in _CDCM["tariffs"][name]["tariff"].items()}
    assert tariff.red_p_per_kwh == model["unit_rate_1_p_per_kwh"]
    assert tariff.amber_p_per_kwh == model["unit_rate_2_p_per_kwh"]
    assert tariff.green_p_per_kwh == model["unit_rate_3_p_per_kwh"]
    assert tariff.fixed_p_per_day == model["fixed_charge_p_per_mpan_day"]
    assert tariff.capacity_p_per_kva_day == model["capacity_charge_p_per_kva_day"]
    assert tariff.exceeded_capacity_p_per_kva_day == model["exceeded_capacity_charge_p_per_kva_day"]
    assert tariff.reactive_p_per_kvarh == model["reactive_power_charge_p_per_kvarh"]


@pytest.mark.parametrize(("name", "voltage"), _SITE_SPECIFIC)
def test_enwl_cdcm_site_specific_net_revenue_reproduces_to_the_penny(
    name: str, voltage: str
) -> None:
    """Every charge component, the total and the typical bill match ENWL's model to the penny."""
    published = _CDCM["tariffs"][name]
    days = _CDCM["named_constants"]["days_in_year"]["value"]
    bill = cdcm_annual_charges(_schedule_tariff(name, voltage), _model_volumes(name), days)

    assert {line.component for line in bill.lines} == set(_COMPONENT.values())
    for row, component in _COMPONENT.items():
        expected = _d(published["net_revenue_gbp"][row]["value"])
        assert to_penny(bill.line(component).amount_gbp) == to_penny(expected), (
            f"{name} {component}: {bill.line(component).amount_gbp} vs "
            f"{published['net_revenue_gbp'][row]['cell']} = {expected}"
        )
    total = _d(published["net_revenue_gbp"]["total"]["value"])
    assert to_penny(bill.total_gbp) == to_penny(total)
    typical = _d(published["typical_bill_gbp_per_mpan"]["all_the_way"]["value"])
    assert to_penny(bill.total_gbp / _model_volumes(name).mpans) == to_penny(typical)


def test_each_band_is_priced_at_its_own_rate_never_another_bands() -> None:
    """Swapping the amber and green volumes changes the bill by exactly the rate difference."""
    name, voltage = _SITE_SPECIFIC[0]
    tariff = _schedule_tariff(name, voltage)
    vol = _model_volumes(name)
    bill = cdcm_annual_charges(tariff, vol, 365)
    swapped = cdcm_annual_charges(
        tariff,
        CdcmVolumes(
            red_mwh=vol.red_mwh,
            amber_mwh=vol.green_mwh,
            green_mwh=vol.amber_mwh,
            mpans=vol.mpans,
            import_capacity_kva=vol.import_capacity_kva,
            exceeded_capacity_kva=vol.exceeded_capacity_kva,
            reactive_mvarh=vol.reactive_mvarh,
        ),
        365,
    )
    expected_shift = (
        (vol.green_mwh - vol.amber_mwh)
        * 1000
        * (tariff.amber_p_per_kwh - tariff.green_p_per_kwh)
        / 100
    )
    assert swapped.total_gbp - bill.total_gbp == expected_shift


def test_an_unstated_reactive_rate_is_not_priced_at_zero() -> None:
    name, voltage = _SITE_SPECIFIC[0]
    tariff = _schedule_tariff(name, voltage).model_copy(update={"reactive_p_per_kvarh": None})
    bill = cdcm_annual_charges(tariff, _model_volumes(name), 365)
    with pytest.raises(KeyError):
        bill.line("reactive")
    assert any("no reactive power rate" in note for note in bill.notes)


# ------------------------------------------------------------------ the half-hourly route


def _annex1_windows() -> dict[str, BandWindows]:
    """ENWL's published LV/HV red and amber bands, parsed from the Annex 1 cells themselves."""
    cells = _ANNEX1["time_band_cells"]
    return {
        "red": BandWindows(
            weekday_windows=windows_from_statement_cell(cells["B6"]),
            weekend_windows=windows_from_statement_cell(cells.get("B7")),
        ),
        "amber": BandWindows(
            weekday_windows=windows_from_statement_cell(cells["C6"]),
            weekend_windows=windows_from_statement_cell(cells["C7"]),
        ),
        "green": BandWindows(
            weekday_windows=windows_from_statement_cell(cells["E6"]),
            weekend_windows=windows_from_statement_cell(cells["E7"]),
        ),
    }


def _year() -> pd.DatetimeIndex:
    """The 2026/27 charging year on the UK clock (365 local days), indexed in UTC."""
    local = pd.date_range(
        "2026-04-01", "2027-04-01", freq="30min", tz="Europe/London", inclusive="left"
    )
    return local.tz_convert("UTC")


def _labels(idx: pd.DatetimeIndex) -> np.ndarray:
    w = _annex1_windows()
    return cdcm_band_labels_from_masks(
        band_windows_mask(w["red"], idx), band_windows_mask(w["amber"], idx)
    )


def test_published_green_cells_are_exactly_the_complement_of_red_and_amber() -> None:
    """The three published bands tile every half-hour once: green is what is left, as stated."""
    idx = _year()
    w = _annex1_windows()
    red, amber, green = (band_windows_mask(w[b], idx) for b in ("red", "amber", "green"))
    assert not (red & amber).any() and not (red & green).any() and not (amber & green).any()
    assert (red | amber | green).all()
    assert (_labels(idx) == np.where(red, "red", np.where(amber, "amber", "green"))).all()


def test_red_band_is_weekday_1600_to_1900_uk_clock_time() -> None:
    idx = pd.DatetimeIndex(
        [
            "2026-07-01 14:30",  # Wed 15:30 BST: amber
            "2026-07-01 15:00",  # Wed 16:00 BST: red
            "2026-07-01 17:30",  # Wed 18:30 BST: red
            "2026-07-01 18:00",  # Wed 19:00 BST: amber
            "2026-07-01 19:30",  # Wed 20:30 BST: green
            "2026-07-04 15:00",  # Sat 16:00 BST: amber
            "2026-12-02 16:00",  # Wed 16:00 GMT: red
        ],
        tz="UTC",
    )
    assert list(_labels(idx)) == ["amber", "red", "red", "amber", "green", "amber", "red"]


def _constructed_load(idx: pd.DatetimeIndex) -> np.ndarray:
    """CONSTRUCTED half-hourly import (kWh) for the method tests: not from any document."""
    hours = idx.tz_convert("Europe/London").hour.to_numpy()
    return 40.0 + 25.0 * ((hours >= 7) & (hours < 19)) + 5.0 * np.cos(np.arange(len(idx)) / 97)


@pytest.mark.parametrize(("name", "voltage"), _SITE_SPECIFIC)
def test_half_hourly_bill_prices_every_band_as_itself(name: str, voltage: str) -> None:
    """Over a year of half-hours (constructed load), each band's kWh meets its own band's rate.

    The two routes agree: the series route's unit lines equal the model algebra's on the same
    band volumes, so the half-hourly path is the published arithmetic, not an approximation of it.
    """
    tariff = _schedule_tariff(name, voltage)
    idx = _year()
    load = _constructed_load(idx)
    labels = _labels(idx)
    months = billing_determinants_from_series(idx, load, labels, mic_kva=500.0)
    bill = cdcm_charges_from_series(tariff, months, mic_kva=500.0)

    for band, rate in (
        ("red", tariff.red_p_per_kwh),
        ("amber", tariff.amber_p_per_kwh),
        ("green", tariff.green_p_per_kwh),
    ):
        kwh = sum((_d(float(load[i])) for i in np.flatnonzero(labels == band)), Decimal(0))
        assert abs(bill.line(f"{band}_unit").amount_gbp - kwh * rate / 100) < Decimal("1e-6")

    volumes = CdcmVolumes(
        red_mwh=bill.line("red_unit").quantity / 1000,
        amber_mwh=bill.line("amber_unit").quantity / 1000,
        green_mwh=bill.line("green_unit").quantity / 1000,
        mpans=Decimal(1),
        import_capacity_kva=Decimal(500),
        exceeded_capacity_kva=Decimal(0),
        reactive_mvarh=Decimal(0),
    )
    algebra = cdcm_annual_charges(tariff, volumes, 365)
    for component in ("red_unit", "amber_unit", "green_unit", "fixed", "capacity"):
        assert algebra.line(component).amount_gbp == bill.line(component).amount_gbp, component


def test_network_unit_rate_series_holds_only_the_published_band_rates() -> None:
    """No half-hour carries a mean of bands: the series takes exactly the three published rates."""
    name, voltage = _SITE_SPECIFIC[0]
    tariff = _schedule_tariff(name, voltage)
    idx = _year()
    labels = _labels(idx)
    series = network_unit_rate_series(tariff, idx, labels)
    published = {
        float(tariff.red_p_per_kwh / 100),
        float(tariff.amber_p_per_kwh / 100),
        float(tariff.green_p_per_kwh / 100),
    }
    assert set(np.unique(series).tolist()) == published
    mean = sum(published) / 3
    assert not np.isclose(series, mean).any()
    for band, rate in (("red", tariff.red_p_per_kwh), ("green", tariff.green_p_per_kwh)):
        assert np.all(series[labels == band] == float(rate / 100))
    with pytest.raises(ValueError, match="red/amber/green"):
        network_unit_rate_series(tariff, idx, np.full(len(idx), "day", dtype=object))
    with pytest.raises(ValueError, match="one red/amber/green label"):
        network_unit_rate_series(tariff, idx, None)


def test_statement_cell_parser_refuses_text_with_no_span() -> None:
    assert windows_from_statement_cell(None) == ()
    assert windows_from_statement_cell("") == ()
    with pytest.raises(ValueError, match="no 'HH:MM to HH:MM' span"):
        windows_from_statement_cell("All other times")


# ------------------------------------------------------------------ LC14 billing rules


def test_exceeded_capacity_formula_is_the_lc14_formula() -> None:
    """``max(2 x sqrt(AI^2 + max(RI, RE)^2) - MIC, 0)``, reactive only at times of active import."""
    ai = np.array([300.0, 300.0, 0.0, 100.0])
    ri = np.array([0.0, 400.0, 900.0, 0.0])
    re = np.array([0.0, 100.0, 0.0, 0.0])
    got = exceeded_capacity_kva(ai, ri, re, mic_kva=800.0)
    # 2*300 = 600 < 800; 2*sqrt(300^2+400^2) = 1000 -> 200; no import -> 0; 200 < 800 -> 0.
    assert got.tolist() == [0.0, 200.0, 0.0, 0.0]


def test_chargeable_reactive_uses_the_two_decimal_place_threshold() -> None:
    """``max(max(RI, RE) - 0.33 x AI, 0)`` per half-hour; 0.33 is sqrt(1/0.95^2 - 1) to 2 d.p."""
    assert round((1 / 0.95**2 - 1) ** 0.5, 2) == float(CDCM_REACTIVE_THRESHOLD)
    ai = np.array([100.0, 100.0, 0.0, 100.0])
    ri = np.array([50.0, 20.0, 40.0, 0.0])
    re = np.array([0.0, 0.0, 0.0, 34.0])
    got = chargeable_reactive_kvarh(ai, ri, re)
    assert np.allclose(got, [17.0, 0.0, 0.0, 1.0])


def test_exceeded_capacity_is_the_months_maximum_charged_for_every_day_of_that_month() -> None:
    """One breach half-hour bills the whole month of the breach, at the month's maximum (LC14)."""
    idx = pd.date_range(
        "2026-06-01", "2026-08-01", freq="30min", tz="Europe/London", inclusive="left"
    ).tz_convert("UTC")
    load = np.full(len(idx), 100.0)  # 200 kVA, inside a 300 kVA MIC
    june_breach = int(np.flatnonzero(idx == pd.Timestamp("2026-06-10 12:00", tz="UTC"))[0])
    july_small = int(np.flatnonzero(idx == pd.Timestamp("2026-07-03 12:00", tz="UTC"))[0])
    july_big = int(np.flatnonzero(idx == pd.Timestamp("2026-07-20 12:00", tz="UTC"))[0])
    load[june_breach] = 170.0  # 340 kVA: 40 kVA over
    load[july_small] = 160.0  # 320 kVA: 20 kVA over
    load[july_big] = 180.0  # 360 kVA: 60 kVA over (the month's maximum)
    labels = np.full(len(idx), "green", dtype=object)
    months = billing_determinants_from_series(idx, load, labels, mic_kva=300.0)
    assert [(m.month, m.days, m.exceeded_kva) for m in months] == [
        ("2026-06", 30, Decimal("40.0")),
        ("2026-07", 31, Decimal("60.0")),
    ]
    tariff = _schedule_tariff(*_SITE_SPECIFIC[0])
    bill = cdcm_charges_from_series(tariff, months, mic_kva=300.0)
    line = bill.line("exceeded_capacity")
    assert line.quantity == Decimal("40.0") * 30 + Decimal("60.0") * 31
    assert line.amount_gbp == line.quantity * tariff.exceeded_capacity_p_per_kva_day / 100
    assert line.avoidability is Avoidability.PEAK_KVA


def test_billing_months_follow_the_uk_clock() -> None:
    """23:30 UTC on 30 June is 00:30 BST on 1 July: it belongs to July's bill."""
    idx = pd.DatetimeIndex(["2026-06-30 22:30", "2026-06-30 23:30"], tz="UTC")
    months = billing_determinants_from_series(
        idx, np.array([1.0, 2.0]), np.array(["green", "green"], dtype=object), mic_kva=10.0
    )
    assert [(m.month, m.band_kwh["green"]) for m in months] == [
        ("2026-06", Decimal("1.0")),
        ("2026-07", Decimal("2.0")),
    ]


# ------------------------------------------------------------------ EDCM (method, not acceptance 1)


def _edcm_tariff(name: str) -> EdcmTariff:
    """An EDCM import tariff from ENWL's published Annex 2 row (columns I-L) and super red band."""
    row = _ANNEX2["annex2_tariff_rows"][name]
    r, c = row["row"], row["cells"]
    band = _ANNEX2["annex2_time_period_cells"]
    assert "Monday to Friday" in band["A6"] and "November to February" in band["A6"]
    return EdcmTariff(
        dno="ENWL",
        voltage="EHV",
        llfc=str(c[f"B{r}"]),
        charging_year="2026/27",
        source=f"{_ANNEX2['source_url']} (Annex 2 row {r})",
        super_red_p_per_kwh=_d(c[f"I{r}"]),
        fixed_p_per_day=_d(c[f"J{r}"]),
        capacity_p_per_kva_day=_d(c[f"K{r}"]),
        exceeded_capacity_p_per_kva_day=_d(c[f"L{r}"]),
        super_red_window=BandWindows(
            weekday_windows=windows_from_statement_cell(band["D6"]), months=(11, 12, 1, 2)
        ),
    )


def test_edcm_super_red_is_weekday_1600_1900_november_to_february_only() -> None:
    tariff = _edcm_tariff("Tariff 2")
    idx = pd.DatetimeIndex(
        [
            "2026-12-02 16:00",  # Wed in December, 16:00 GMT: super red
            "2026-12-05 16:00",  # Sat: not
            "2026-10-07 15:00",  # Wed in October, 16:00 BST: not (outside Nov-Feb)
            "2027-02-26 18:30",  # Fri in February, 18:30 GMT: super red
            "2027-03-03 16:00",  # Wed in March: not
        ],
        tz="UTC",
    )
    rates = network_unit_rate_series(tariff, idx)
    assert rates.tolist() == [0.00107, 0.0, 0.0, 0.00107, 0.0]


@pytest.mark.parametrize("name", ["Tariff 2", "Tariff 146"])
def test_edcm_bill_follows_enwls_published_charge_calculator(name: str) -> None:
    """ENWL's Charge Calculator (EHV half, row 17) states each component's formula:

    super red £ = rate x kWh / 100; fixed £ = p/day x days / 100; capacity £ = p/kVA/day x MIC x
    days / 100; exceeded £ = p/kVA/day x average daily exceeded kVA x days / 100. The quantities
    here are CONSTRUCTED (the calculator is published empty); the rates are Annex 2's own.
    """
    formulas = _ANNEX2["charge_calculator_formulas"]
    assert formulas["M17"] == '=IFERROR(M10*M13/100,"")'
    assert formulas["N17"] == '=IFERROR(N10*N13/100,"")'
    assert formulas["O17"] == '=IFERROR(O10*O13*N13/100,"")'
    assert formulas["P17"] == '=IFERROR(P10*P13*N13/100,"")'
    assert "Average daily exceeded capacity" in formulas["P12"]

    tariff = _edcm_tariff(name)
    months = (  # CONSTRUCTED determinants for one winter quarter
        BillingMonth("2026-12", 31, {"super_red": Decimal("41250.5")}, Decimal("120.0")),
        BillingMonth("2027-01", 31, {"super_red": Decimal("39880.0")}, Decimal(0)),
        BillingMonth("2027-02", 28, {"super_red": Decimal("36015.25")}, Decimal("45.5")),
    )
    mic = 4000.0
    bill = edcm_charges_from_series(tariff, months, mic_kva=mic)
    days = Decimal(90)
    kwh = Decimal("41250.5") + Decimal("39880.0") + Decimal("36015.25")
    average_daily_exceeded = (Decimal("120.0") * 31 + Decimal("45.5") * 28) / days
    calculator = {  # row 17 of the calculator, term for term
        "super_red_unit": tariff.super_red_p_per_kwh * kwh / 100,
        "fixed": tariff.fixed_p_per_day * days / 100,
        "capacity": tariff.capacity_p_per_kva_day * Decimal(4000) * days / 100,
        "exceeded_capacity": tariff.exceeded_capacity_p_per_kva_day
        * average_daily_exceeded
        * days
        / 100,
    }
    for component, expected in calculator.items():
        assert to_penny(bill.line(component).amount_gbp) == to_penny(expected), component
    assert {line.component for line in bill.lines} == set(calculator)
    assert "no separate reactive power charge" in " ".join(bill.notes)


# ------------------------------------------------------------------ H-02 avoidability


def test_every_line_carries_its_h02_avoidability() -> None:
    name, voltage = _SITE_SPECIFIC[0]
    bill = cdcm_annual_charges(_schedule_tariff(name, voltage), _model_volumes(name), 365)
    tags = {line.component: line.avoidability for line in bill.lines}
    assert tags == {
        "red_unit": Avoidability.HH_IMPORT,
        "amber_unit": Avoidability.HH_IMPORT,
        "green_unit": Avoidability.HH_IMPORT,
        "fixed": Avoidability.NOT_AVOIDABLE,
        "capacity": Avoidability.AGREED_CAPACITY,
        "exceeded_capacity": Avoidability.PEAK_KVA,
        "reactive": Avoidability.HH_IMPORT,
    }
    assert not bill.line("reactive").charged_on_battery_charging
    assert bill.line("red_unit").charged_on_battery_charging


def test_a_dispatch_never_saves_on_fixed_or_agreed_capacity_lines() -> None:
    """Shifting import between bands moves unit lines only; fixed and capacity save exactly £0."""
    tariff = _schedule_tariff(*_SITE_SPECIFIC[0])
    idx = _year()
    load = _constructed_load(idx)
    labels = _labels(idx)
    before = cdcm_charges_from_series(
        tariff, billing_determinants_from_series(idx, load, labels, mic_kva=500.0), mic_kva=500.0
    )
    shifted = load.copy()
    red = labels == "red"
    shifted[red] -= 10.0
    shifted[labels == "green"] += 10.0 * red.sum() / (labels == "green").sum()
    after = cdcm_charges_from_series(
        tariff,
        billing_determinants_from_series(idx, shifted, labels, mic_kva=500.0),
        mic_kva=500.0,
    )
    assert after.line("red_unit").amount_gbp < before.line("red_unit").amount_gbp
    assert_unavoidable_lines_save_nothing(before, after)

    cheaper_mic = cdcm_charges_from_series(
        tariff,
        billing_determinants_from_series(idx, shifted, labels, mic_kva=400.0),
        mic_kva=400.0,
    )
    with pytest.raises(ValueError, match="capacity"):
        assert_unavoidable_lines_save_nothing(before, cheaper_mic)


# ------------------------------------------------------------------ one calendar, read by the stack


def test_band_windows_mask_agrees_with_the_levy_calendar() -> None:
    """`band_windows_mask` bands exactly as `windowed_levy_mask` does (one set of conventions).

    The two differ by design in one place only: an excluded date falls to the WEEKEND row here (a
    statement's non-working day) and is dropped by the levy calendar. With no weekend window the
    two coincide even then, which is checked too.
    """
    from gridq.optimisation.tariff_calendar import windowed_levy_mask
    from gridq.schemas import ClockWindow, WindowedLevy

    idx = _year()
    spans = (ClockWindow(start="16:00", end="19:00"), ClockWindow(start="23:30", end="01:00"))
    weekend = (ClockWindow(start="10:00", end="12:30"),)
    for months in (None, (11, 12, 1, 2)):
        band = BandWindows(weekday_windows=spans, weekend_windows=weekend, months=months)
        levy = WindowedLevy(
            rate_gbp_per_kwh=0.01, weekday_windows=spans, weekend_windows=weekend, months=months
        )
        assert (band_windows_mask(band, idx) == windowed_levy_mask(levy, idx)).all()
    holidays = ("2026-12-25", "2026-12-28")
    band = BandWindows(weekday_windows=spans, months=(12,), excluded_dates=holidays)
    levy = WindowedLevy(
        rate_gbp_per_kwh=0.01, weekday_windows=spans, months=(12,), excluded_dates=holidays
    )
    assert (band_windows_mask(band, idx) == windowed_levy_mask(levy, idx)).all()


def _stack_with(network_tariff: CdcmTariff | EdcmTariff, **flat: float):
    from gridq.schemas import TariffBasis, TariffProvenance, TariffStack

    return TariffStack(
        basis=TariffBasis(
            vat="excluded",
            vat_attestation="stated_by_record",
            levies="itemised",
            network_charges="itemised",
        ),
        provenance=TariffProvenance(
            commodity="assumed", network="published_for_site", levies="assumed"
        ),
        import_rates={"single": 0.2},
        network_tariff=network_tariff,
        **flat,
    )


def test_a_published_cdcm_row_on_the_stack_prices_each_band_at_its_own_rate() -> None:
    """`network_rate_series_with_absences` reads the stack's published row before any flat line."""
    from gridq.optimisation.tariff_calendar import (
        duos_band_labels,
        network_rate_series_with_absences,
    )

    tariff = _schedule_tariff(*_SITE_SPECIFIC[1])
    idx = pd.date_range("2026-07-06 00:00", periods=7 * 48, freq="30min", tz="Europe/London")
    rates, absent = network_rate_series_with_absences(_stack_with(tariff), idx)
    assert absent == ()
    labels = duos_band_labels(idx)
    for band, pence in (
        ("red", tariff.red_p_per_kwh),
        ("amber", tariff.amber_p_per_kwh),
        ("green", tariff.green_p_per_kwh),
    ):
        assert set(rates[labels == band].tolist()) == {float(pence / 100)}


def test_a_published_edcm_row_on_the_stack_prices_only_its_super_red_window() -> None:
    from gridq.optimisation.tariff_calendar import network_rate_series_with_absences

    tariff = _edcm_tariff("Tariff 2")
    idx = pd.date_range("2026-12-07 00:00", periods=7 * 48, freq="30min", tz="Europe/London")
    rates, absent = network_rate_series_with_absences(_stack_with(tariff), idx)
    assert absent == ()
    local = idx.tz_convert("Europe/London")
    window = (local.dayofweek < 5) & (local.hour >= 16) & (local.hour < 19)
    assert set(rates[window].tolist()) == {float(tariff.super_red_p_per_kwh / 100)}
    assert not rates[~window].any()


def test_an_edcm_row_refuses_flat_red_amber_green_lines_and_a_disagreeing_capacity() -> None:
    tariff = _edcm_tariff("Tariff 2")
    with pytest.raises(ValueError, match="EDCM has no red/amber/green"):
        _stack_with(tariff, duos_red_rate=0.05)
    with pytest.raises(ValueError, match="disagrees with network_tariff"):
        _stack_with(
            tariff, capacity_charge_gbp_per_kva_day=float(tariff.capacity_p_per_kva_day) / 50
        )
    agreed = float(tariff.capacity_p_per_kva_day / 100)
    assert _stack_with(tariff, capacity_charge_gbp_per_kva_day=agreed).network_tariff == tariff
