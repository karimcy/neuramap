"""GridQ Wave-5 product surface — FastAPI + Jinja2, server-rendered, self-contained.

Two explicit run modes, chosen up front and never auto-selected:

* **Real site** (primary): the visitor enters their own site (+ optional half-hourly meter file); we
  run the live path (ingestion → grading → real `MilpEngine` → finance → the three artifacts) and,
  when inputs are missing, surface the ranked data-asks and degrade honestly, never inventing.
* **Demo** (opt-in): a clearly-labelled illustrative dataset run through the real engine with
  illustrative debt, so the full flow (3D sizing surface, firm-vs-gated split, proposal) is visible.

**The interface computes nothing.** Every figure shown is read off the stamped `QuoteRecord` via
`gridq.outputs.build_view_model` + `gridq.outputs.formatting`; charts read a data island of record
values. Pricing/finance/sizing all live in `gridq.{finance,quotes,optimisation,sizing,outputs}`.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile as StarletteUploadFile

from gridq.app import ephemeral, store, view
from gridq.governance.dpa_guard import (
    DEMO_SITE_ID,
    DpaEvidenceMissing,
    DpaPermit,
    EvidenceRegistryError,
    TransientWorkspace,
    authorise_store,
)
from gridq.schemas import (
    ConsentStatus,
    CreditStatus,
    DataTier,
    ExportRoute,
    QuoteMode,
    SelectionStatus,
    VoltageLevel,
)

#: Explicit assumption-release pin for the app, or ``None`` (the default) to resolve the NEWEST
#: release FROM DISK at run time — the same rule the CLI follows (`gridq.cli`, `cli.py:248-265`).
#:
#: This constant used to read ``"gb_v1_4"``, which is MD-F-15 through a second door: the CLI fix
#: made `gridq run` resolve `latest_version()` and warn on a superseded pin, and nobody checked the
#: web app, so the product surface sat five releases behind the library it ships with. That is not
#: cosmetic. gb_v1_7 alone re-derived `battery_opex.degradation_marginal_cost_gbp_per_mwh` from an
#: unsourced illustrative 5.0 to the levelised 21.3 GBP/MWh discharged, either side of a real
#: site's cycle break-even, so a web run and a CLI run of the SAME site could return opposite
#: viability verdicts with nothing on either record to show why.
#:
#: Set this only to reproduce an older run. A superseded pin is allowed, but it says so loudly on
#: the run record (`_resolve_assumption_version`) instead of passing as the current library.
ASSUMPTION_VERSION_PIN: str | None = None

#: Shown atop the real-mode form when it is pre-filled from the (private, gitignored) real-prospect
#: example. Makes the provenance honest: the metadata is real, the half-hourly series is the
#: visitor's own upload, and unknown fields stay blank rather than invented.
_EXAMPLE_NOTE = (
    "Pre-filled from a real prospect - Robert Thomas Farms (SmartestEnergy account EVY220LUQ), "
    "transcribed verbatim from their invoices. The tariff, authorised capacity, connection "
    "region, the grid consents (G98/G99/G100, DNO and supplier state) and the capacities "
    "(MEC/ASC) are now declared; the only honest blanks left are the DUoS bands and the company "
    "number, kept empty as ranked data-asks. Attach the site's half-hourly meter CSV below to run "
    "the real dispatch MILP and the 3D sizing surface - nothing is invented to fill the gap."
)

_APP_DIR = Path(__file__).resolve().parent

#: Paths that must never sit behind the optional gate: the health probe (so the host's
#: load-balancer check works without credentials) and the self-contained static assets.
_AUTH_EXEMPT_PREFIXES = ("/healthz", "/static")


def _basic_auth_credentials() -> tuple[bytes, bytes] | None:
    """The (user, pass) the optional public-URL gate expects, or None when the gate is off.

    Driven entirely by the ``GRIDQ_BASIC_AUTH="user:pass"`` env var (read once when the app is
    built) — **no credential ever lives in the repo** (CLAUDE.md §2). Unset/blank/`:`-less ⟹ the
    gate is dormant and every route is open, so the default posture and all tests are unchanged.
    Returned as UTF-8 bytes so the constant-time compare is locale-independent.
    """
    raw = os.environ.get("GRIDQ_BASIC_AUTH")
    if not raw or ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return user.encode("utf-8"), password.encode("utf-8")


# ---- the DPA guard at the web intake (WP-GRC-05) ----------------------------
# Owner decision (a), 2026-11-23: a public intake without a DPA is shown once and nothing is
# stored. The upload and any modelled profile live in a `TransientWorkspace` (never under the run
# store) and are deleted when the run ends; the result is held in process memory for
# `ephemeral.TTL_SECONDS` and then dropped. A run is stored only for a signed-in visitor whose site
# has valid, verified DPA evidence, and the permit is recorded beside it.
def _storage_permit(site_id: str, owner_id: str | None) -> DpaPermit | None:
    """Permission to store a visitor's run, or ``None`` (shown once, nothing stored).

    An anonymous visitor never stores. A signed-in visitor stores only under a valid, verified DPA
    for the site. The site id is typed by the visitor, so it never reaches the demo or
    ``SYNTHETIC-`` exemptions (``allow_exempt=False``). An unreadable registry stores nothing.
    """
    if not owner_id or owner_id.startswith("anon:"):
        return None
    try:
        return authorise_store(site_id, allow_exempt=False)
    except (DpaEvidenceMissing, EvidenceRegistryError):
        return None


def _keep_run(run: Any, *, owner_id: str | None) -> str:
    """Store the run under a DPA permit, or hold it in memory to be shown once. Returns its id."""
    permit = _storage_permit(run.quote.site_id, owner_id)
    if permit is not None:
        store.save_run(run.quote, run.site, run.telemetry, dpa=permit, owner_id=owner_id)
    else:
        ephemeral.hold(run.quote, run.site, run.telemetry, owner_id=owner_id)
    return str(run.quote.quote_id)


# ---- form helpers --------------------------------------------------------
def _s(form: Any, key: str) -> str | None:
    """A trimmed string form field, or None when blank."""
    value = str(form.get(key) or "").strip()
    return value or None


def _f(form: Any, key: str) -> float | None:
    """A float form field, or None when blank/unparseable (the caller validates required-ness)."""
    raw = _s(form, key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _rates(raw: str | None) -> dict[str, float]:
    """Parse a ``label:rate, label:rate`` free-text tariff field into a {label: £/kWh} dict."""
    out: dict[str, float] = {}
    if not raw:
        return out
    for part in raw.split(","):
        if ":" not in part:
            continue
        label, _, value = part.partition(":")
        try:
            out[label.strip()] = float(value.strip())
        except ValueError:
            continue
    return out


#: Form value -> (vat, vat_attestation). "not_stated" derives the basis under H-01 and records it.
_VAT_CHOICES = {
    "excluded": ("excluded", "stated_by_record"),
    "included": ("included", "stated_by_record"),
    "not_stated": ("excluded", "derived"),
}
_PROVENANCE_CHOICES = frozenset(
    {"own_invoice", "published_for_site", "by_analogy", "assumed", "not_charged"}
)


def _tariff_basis_from_form(form: Any) -> tuple[Any, Any, list[Any]]:
    """The stack's `TariffBasis`, `TariffProvenance` and any record the basis needs (WP-UW-07).

    The intake form asks each question with no option preselected. A field that arrives blank
    (an API post, an example file written before the question existed) is **derived and
    recorded, never silently filled** (D44): VAT is read as excluded under H-01 with a
    `DerivedAssumption` on ``tariff_stack.basis.vat``; levies and network charges are read as
    itemised, which is what the form's own separate levy and DUoS fields mean; and every
    component's provenance is ``assumed``, so no report sentence can call the stack the site's own
    until someone says it is.
    """
    import gridq.schemas as s
    from gridq.ingestion.validators import SiteModelLoadError
    from gridq.tariffs.basis import h01_vat_basis_record

    records: list[Any] = []
    vat_choice = _s(form, "vat_basis") or "not_stated"
    if vat_choice not in _VAT_CHOICES:
        raise SiteModelLoadError(f"Unknown VAT basis {vat_choice!r}.")
    vat, attestation = _VAT_CHOICES[vat_choice]
    vat_rate = _f(form, "vat_rate_percent")
    # A re-run (`_improve.html`) carries the recorded fraction itself, never a template-computed
    # percent (no arithmetic in templates), so the stated rate survives the round trip exactly.
    recorded_fraction = _f(form, "vat_rate_fraction")
    if vat_rate is None and recorded_fraction is not None:
        vat_rate = recorded_fraction * 100.0
    if vat == "included" and vat_rate is None:
        raise SiteModelLoadError(
            "The tariff rates are stated as including VAT: give the VAT rate the invoice shows, "
            "so the net figure can be stated."
        )
    if attestation == "derived":
        records.append(h01_vat_basis_record(subject="the tariff entered on the intake form"))
    # The form's own levy and DUoS fields are separate lines, so an absent select (a re-run posted
    # without it; the browser requires it) reads them as itemised, which is what those fields are.
    basis = s.TariffBasis.model_validate(
        {
            "vat": vat,
            "vat_rate_fraction": (vat_rate / 100.0) if vat == "included" and vat_rate else None,
            "vat_attestation": attestation,
            "levies": _s(form, "levy_basis") or "itemised",
            "network_charges": _s(form, "network_basis") or "itemised",
        }
    )
    provenance: dict[str, str] = {}
    for component in ("commodity", "network", "levies"):
        value = _s(form, f"provenance_{component}") or "assumed"
        if value not in _PROVENANCE_CHOICES:
            raise SiteModelLoadError(f"Unknown {component} provenance {value!r}.")
        provenance[component] = value
    if basis.network_charges == "not_charged":
        provenance["network"] = "not_charged"
    return basis, s.TariffProvenance.model_validate(provenance), records


def _site_from_form(form: Any, hh_summary: Any | None) -> Any:
    """Build a validated `SiteModel` from the real-site form (+ optional HH summary).

    Raises `SiteModelLoadError`/`pydantic.ValidationError` on incoherent input — the route turns
    those into field-level messages, never a 500. No value is invented: blanks stay ``None`` and
    become ranked data-asks downstream.
    """
    # Local imports keep app import light and avoid pulling pandas on the landing page.
    import gridq.schemas as s
    from gridq.ingestion import (
        infer_data_tier_from_hh_summary,
        populate_missing_data,
        to_load_data_summary,
        validate_site_model,
    )
    from gridq.ingestion.validators import SiteModelLoadError

    # F-17: a rate is inadmissible without its unit. The form collects one number per stream;
    # the unit is a property of the STREAM, not of the user's typing, so it is resolved from
    # how the pipeline actually consumes that stream rather than guessed downstream.
    consent_rate_units = {
        s.MarketStream.CAPACITY_MARKET: s.RateUnit.GBP_PER_KW_YEAR,
        s.MarketStream.BALANCING_MECHANISM: s.RateUnit.GBP_PER_MW_YEAR,
        s.MarketStream.ANCILLARY_DC_DM_DR: s.RateUnit.GBP_PER_MW_YEAR,
        s.MarketStream.LOCAL_FLEX_PICLO: s.RateUnit.GBP_PER_MWH,
        s.MarketStream.WHOLESALE_P415: s.RateUnit.GBP_PER_KWH,
    }

    annual_kwh = _f(form, "annual_consumption_kwh")
    if hh_summary is not None:
        load_data = to_load_data_summary(hh_summary)
        data_tier = infer_data_tier_from_hh_summary(
            hh_summary, has_annual_kwh=annual_kwh is not None
        )
    else:
        load_data = s.LoadDataSummary(
            annual_kwh=annual_kwh, max_import_kw=_f(form, "max_import_kw")
        )
        data_tier = DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE

    postcode = _s(form, "postcode")
    mic_kw = _f(form, "mic_kw")
    if not postcode:
        raise SiteModelLoadError("Postcode is required.")
    if mic_kw is None:
        raise SiteModelLoadError("Connection MIC (kW) is required to bound the battery.")

    site_id = _s(form, "site_id") or f"site_{postcode.replace(' ', '').lower()}"
    tariff_basis, tariff_provenance, tariff_records = _tariff_basis_from_form(form)
    tariff = s.TariffStack(
        basis=tariff_basis,
        provenance=tariff_provenance,
        supplier=_s(form, "supplier"),
        tariff_name=_s(form, "tariff_name") or None,
        standing_charge_per_day_gbp=_f(form, "standing_charge"),
        import_rates=_rates(_s(form, "import_rates")),
        duos_red_rate=_f(form, "duos_red_rate"),
        duos_amber_rate=_f(form, "duos_amber_rate"),
        duos_green_rate=_f(form, "duos_green_rate"),
        # Levy lines (£/kWh, e.g. "ccl:0.00775") and the capacity/availability charge
        # (£/kVA/day on the ASC) — transcribed bill lines, not invented (2026-07-07: the
        # intake was blind to both; the CCL and capacity lines on a real invoice never
        # reached the model).
        levies=_rates(_s(form, "levies")),
        capacity_charge_gbp_per_kva_day=_f(form, "capacity_charge_gbp_per_kva_day"),
        source=_s(form, "tariff_source"),
    )
    connection = s.ConnectionDetails(
        mic_kw=mic_kw,
        mec_kw=_f(form, "mec_kw"),
        asc_kw=_f(form, "asc_kw"),
        voltage_level=VoltageLevel(form.get("voltage_level"))
        if _s(form, "voltage_level")
        else None,
        dno_region=_s(form, "dno_region"),
    )
    consents = s.ConsentState(
        g98=ConsentStatus(form.get("g98")) if _s(form, "g98") else ConsentStatus.UNKNOWN,
        g99=ConsentStatus(form.get("g99")) if _s(form, "g99") else ConsentStatus.UNKNOWN,
        g100=ConsentStatus(form.get("g100")) if _s(form, "g100") else ConsentStatus.UNKNOWN,
        export_mpan=_s(form, "export_mpan"),
        dno_application_state=(
            ConsentStatus(form.get("dno_application_state"))
            if _s(form, "dno_application_state")
            else ConsentStatus.NOT_STARTED
        ),
        supplier_consent_state=(
            ConsentStatus(form.get("supplier_consent"))
            if _s(form, "supplier_consent")
            else ConsentStatus.NOT_STARTED
        ),
    )
    # Supply boundary (import MPAN). Blank import_mpan == the default-factory SupplyBoundary, so an
    # old-style submission serialises identically and the input hash is byte-stable (D8/D42).
    supply_boundary = s.SupplyBoundary(import_mpan=_s(form, "import_mpan"))
    # Export / route-to-market arrangement. Built ONLY when at least one export field is declared;
    # otherwise it stays None and is POPPED from the canonical run input, so old-style submissions
    # keep an unchanged input hash. aggregator_cut_pct arrives as a UI percent (0-100) and is
    # stored as a 0-1 fraction; an out-of-range value fails the schema Field (ge=0/le=1) and the
    # route turns that into a 400, never a 500.
    export_route = _s(form, "export_route")
    export_counterparty = _s(form, "export_counterparty")
    export_tariff = _f(form, "export_tariff_gbp_per_kwh")
    aggregator_cut_raw = _f(form, "aggregator_cut_pct")
    local_flex_rate = _f(form, "local_flex_agreed_rate_gbp_per_mwh")
    export_arrangement = None
    if any(
        value is not None
        for value in (
            export_route,
            export_counterparty,
            export_tariff,
            aggregator_cut_raw,
            local_flex_rate,
        )
    ):
        export_arrangement = s.ExportArrangement(
            route=ExportRoute(export_route) if export_route else ExportRoute.UNKNOWN,
            counterparty=export_counterparty,
            aggregator_cut_pct=(
                aggregator_cut_raw / 100.0 if aggregator_cut_raw is not None else None
            ),
            export_tariff_gbp_per_kwh=export_tariff,
            local_flex_agreed_rate_gbp_per_mwh=local_flex_rate,
        )
    # Per-stream customer consent (Revenue-Data Drive / D43). Each ticked ``consent_<stream>`` box
    # authorises GridQ to pursue that revenue stream on the customer's behalf; the optional
    # ``consent_rate_<stream>`` input carries the commercially agreed rate for the three streams
    # whose rate has no dedicated ExportArrangement home (Capacity Market £/kW/yr, Balancing
    # Mechanism / Ancillary £/MW/yr). The export tariff / aggregator cut / local-flex rate keep
    # their ExportArrangement home and are NOT duplicated here. Nothing ticked -> None, which
    # `quotes.assembly._canonical_run_input` POPS from the canonical run input so a submission with
    # no per-stream consents keeps a byte-stable input hash (D42). Consent is authorisation, never a
    # value: a stream still needs its data/rate to produce a number.
    stream_consents_list: list[Any] = []
    for stream in s.MarketStream:
        if _s(form, f"consent_{stream.value.lower()}") is None:
            continue
        rate = _f(form, f"consent_rate_{stream.value.lower()}")
        stream_consents_list.append(
            s.RevenueStreamConsent(
                stream=stream,
                consented=True,
                agreed_rate_gbp=rate,
                # F-17: a rate is inadmissible without its unit. The intake form collects one
                # number per stream, and the unit it means is a property of the STREAM, not of the
                # user's typing — so it is supplied here from the unit the pipeline actually
                # consumes for that stream rather than being left for a downstream guess.
                agreed_rate_unit=None if rate is None else consent_rate_units.get(stream),
            )
        )
    stream_consents = tuple(stream_consents_list) or None
    counterparty = s.Counterparty(
        legal_name=_s(form, "legal_name") or "Unnamed counterparty",
        company_number=_s(form, "company_number"),
        sector=_s(form, "sector"),
        credit_status=(
            CreditStatus(form.get("credit_status"))
            if _s(form, "credit_status")
            else CreditStatus.UNKNOWN
        ),
    )
    site = s.SiteModel(
        site_id=site_id,
        site_name=_s(form, "site_name"),
        postcode=postcode,
        data_tier=data_tier,
        load_data=load_data,
        annual_consumption_kwh=annual_kwh,
        tariff_stack=tariff,
        connection=connection,
        consents=consents,
        supply_boundary=supply_boundary,
        physical_gates=s.PhysicalGateStatus(),
        counterparty=counterparty,
        export_arrangement=export_arrangement,
        stream_consents=stream_consents,
        derived_assumptions=tariff_records or None,
    )
    # Coherence seam: after issue 018/D8 no consent value is ERROR-severity (consents are declared
    # inputs, never a hard block), so this raise is reserved for future *structural* errors. It is
    # kept deliberately - a WARNING-only report has `report.ok == True` and the run proceeds.
    report = validate_site_model(site)
    if not report.ok:
        raise SiteModelLoadError(
            "Site is schema-valid but incoherent: " + "; ".join(report.messages())
        )
    return populate_missing_data(site)


def _mark_modelled_load(site: Any, *, constrained: bool) -> Any:
    """Stamp the MODELLED half-hourly basis onto a site whose profile we generated.

    Without this the generated series is indistinguishable from a metered one the moment it is
    written to disk (hostile review N9): the Gate-2 admission would report METERED and the
    confidence ceiling would rise from `medium` to `high` on a shape nobody measured. The tier is
    also held at T1 — a modelled profile is never validated 12-month metered data.
    """
    load = site.load_data.model_copy(
        update={
            "half_hourly_basis": "modelled",
            "half_hourly_reference_profile": (
                "app constrained-shape generator" if constrained else "app business-archetype"
            ),
            "half_hourly_scaled_to": "stated annual consumption (intake form)",
        }
    )
    return site.model_copy(update={"load_data": load, "data_tier": DataTier.T1_PARTIAL_HALF_HOURLY})


def _synthesise_profile_if_needed(
    data: Any,
    hh_summary: Any | None,
    extra_warnings: list[str],
    workspace: TransientWorkspace,
) -> tuple[Any | None, bool, bool]:
    """Model a half-hourly profile from the stated facts when no meter file was supplied.

    Returns ``(hh_summary, synthetic, constrained)``. With observed shape facts on the form
    (day/night volume split, peak timing, midday behaviour) the profile is built from those
    constraints (`generate_constrained_hh_csv`); otherwise the business-archetype generator
    runs. Both are disclosed via ``extra_warnings``, including a loud mismatch warning when the
    modelled peak misses the stated peak (2026-07-07: the archetype clamp used to model a
    421 kW site at 79.6 kW silently). Shared by the wizard job (`/api/run`) and the synchronous
    real form (`/run/real`) so no intake path can silently skip the modelling step.

    The modelled profile is built from the visitor's stated facts, so it is their data: it is
    written into the run's `TransientWorkspace` and deleted with it (WP-GRC-05).
    """
    annual_kwh = _f(data, "annual_consumption_kwh")
    if hh_summary is not None or not annual_kwh:
        return hh_summary, False, False

    from gridq.app import synthetic as syn
    from gridq.ingestion import load_half_hourly_csv, summarise_half_hourly_data

    stated_peak = _f(data, "max_import_kw")
    day_share = _f(data, "day_kwh_share")
    peak_period = _s(data, "peak_period")
    midday_low = (_s(data, "midday_low") or "").lower() == "true"
    constrained = day_share is not None or bool(peak_period) or midday_low
    if constrained:
        # Observed shape facts present (bill volumes / peak timing / midday behaviour): build
        # the modelled profile from those constraints, not a business archetype, and surface
        # every modelling note as a warning.
        path, notes = syn.generate_constrained_hh_csv(
            workspace.root,
            annual_kwh=annual_kwh,
            peak_kw=stated_peak,
            day_kwh_share=day_share,
            peak_period=peak_period or None,
            midday_low=midday_low,
        )
        extra_warnings.extend(f"Modelled load profile: {n}" for n in notes)
    else:
        path = syn.generate_hh_csv(
            workspace.root,
            business_type=_s(data, "business_type"),
            annual_kwh=annual_kwh,
            peak_kw=stated_peak,
        )
    path = workspace.adopt(path)
    hh_summary = summarise_half_hourly_data(load_half_hourly_csv(path), file_ref=str(path))
    # Honesty guard (2026-07-07): the archetype generator's clamped reshape can silently miss
    # the stated peak by a wide margin - the mismatch must be loud, whichever generator ran.
    modelled_peak = getattr(hh_summary, "max_import_kw", None)
    if stated_peak and modelled_peak and abs(modelled_peak - stated_peak) / stated_peak > 0.02:
        extra_warnings.append(
            f"Modelled peak {modelled_peak:.1f} kW does not match your stated peak "
            f"{stated_peak:g} kW: the modelled shape cannot reproduce it from the facts given. "
            "Peak-linked results are unreliable; supply a half-hourly meter file or the "
            "day/night split and peak timing."
        )
    return hh_summary, True, constrained


async def _save_uploaded_hh(form: Any, workspace: TransientWorkspace) -> Any | None:
    """Put an uploaded half-hourly CSV in the run's transient workspace and summarise it.

    Returns a `HalfHourlySummary` (so the site grades to T1/T2 and the MILP can dispatch), or None
    when no usable file was uploaded. Never raises out — a bad file becomes a warning upstream.

    The bytes are content-addressed (so ``quote_id`` stays deterministic) and live only in the
    `TransientWorkspace`, never under the run store: they are deleted when the run ends, whether or
    not the result is stored (WP-GRC-05; nothing re-reads the series once the run has priced it).
    """
    upload = form.get("hh_file")
    filename = getattr(upload, "filename", None)
    if not upload or not filename:
        return None
    data = await upload.read()
    if not data:
        return None
    from gridq.ingestion import load_half_hourly_csv, summarise_half_hourly_data

    digest = hashlib.sha256(data).hexdigest()[:16]
    path = workspace.write_bytes(f"hh_{digest}.csv", data)
    df = load_half_hourly_csv(path)
    return summarise_half_hourly_data(df, file_ref=str(path))


# ---- pricing basis (D46/D55) ---------------------------------------------
def _basis_display(basis: Any) -> dict[str, Any]:
    """A `StudyBasisView` flattened into the strings a screen prints, and nothing more.

    **Why this exists at all.** The frontend stated the pricing basis on NO screen, on either basis:
    zero mentions of ``study_basis`` in ``app.py`` or any template before 2026-08-18. A study-basis
    run prices revenue the underwriting basis holds out of the priced floor INTO P50, P90, the DSCR
    and both IRRs, so every screen showing those figures has to say so (D46, D55, CLAUDE.md 2.0
    rule 4). The four run screens, the project listing and the dashboard hero all show them.

    **It formats, it does not decide** (CLAUDE.md §8). `outputs.detect_study_basis` is the single
    detector and the record is the single source; this only picks the strings apart and rewrites em
    dashes out of them, because every `StudyBasis.LABEL` joins its phrases with em dashes and no
    rendered GridQ surface carries one. ``short`` is the leading phrase, e.g. "STUDY BASIS (D55)",
    which is what fits a badge.
    """
    from gridq.outputs import formatting as fmt

    if not basis.present:
        return {"present": False, "label": None, "short": None, "note": None, "basis_id": None}
    label = fmt.no_dashes(basis.label or "")
    return {
        "present": True,
        "label": label,
        "short": label.split(", ")[0],
        "note": fmt.no_dashes(basis.note) if basis.note is not None else None,
        "basis_id": basis.basis_id,
    }


# ---- run-screen context --------------------------------------------------
def _run_context(request: Request, quote_id: str, active: str) -> dict[str, Any] | None:
    """Load a persisted run and assemble the common screen context, or None (→ 404).

    Accounts scoping (2026-07-07): a run owned by a DIFFERENT identity is treated exactly like
    a missing run (404, never 403 — no existence leak). Legacy/shared runs (no recorded owner)
    stay visible to everyone.
    """
    from gridq.app import auth

    user = auth.current_user(request)
    # A shown-once run (no DPA, nothing stored) is held in this process's memory, bound to the
    # visitor who ran it, until it expires; then, or from another worker, it is a 404.
    held = ephemeral.get(quote_id)
    stored: store.StoredRun | None
    if held is not None:
        if held.owner_id is None or held.owner_id != user["id"]:
            return None
        stored = held
    else:
        stored = store.load_run(quote_id)
        if stored is None or not auth.visible_to(user["id"], stored.owner_id):
            return None
    from gridq.app.demo import DEMO_LABEL
    from gridq.outputs import build_view_model, detect_study_basis

    vm = build_view_model(stored.quote, stored.site)
    is_demo = any(DEMO_LABEL in w for w in stored.quote.warnings)
    return {
        "request": request,
        "stored": stored,
        "quote": stored.quote,
        "site": stored.site,
        "telemetry": stored.telemetry,
        "vm": vm,
        "is_demo": is_demo,
        # Which pricing basis produced every figure on the screen. `_statusbar.html` is included by
        # all four run screens, so one banner here reaches Results, System spec, Proposal and
        # Telemetry. Detected from the record, never from the screen.
        "basis": _basis_display(detect_study_basis(stored.quote)),
        # Shown once, not stored (WP-GRC-05): `_statusbar.html` says so on every run screen.
        "not_saved": held is not None,
        "not_saved_minutes": ephemeral.TTL_SECONDS // 60,
        "nav_quote_id": quote_id,
        "nav_active": active,
    }


# ---- run-job registry (in-process; single uvicorn worker) ----------------
# The wizard / improve flows start a run as a background thread and poll its status, so the browser
# shows a live progress UI instead of a blocked request. The completion signal is real: the bar only
# reaches done when the deterministic pipeline actually returns a quote id (or errors).
_RUN_JOBS: dict[str, dict[str, Any]] = {}
_RUN_JOBS_LOCK = threading.Lock()
_RUN_JOBS_MAX = 200


def _set_job(job_id: str, *, create: bool = True, **fields: Any) -> None:
    with _RUN_JOBS_LOCK:
        job = _RUN_JOBS.get(job_id)
        if job is None:
            # A progress emit (create=False) must never resurrect an evicted job: doing so would
            # recreate it carrying only {stage, stage_done, stage_total} and no `status`, so the
            # poller would see a status-less job forever. Lifecycle writes (running/done/error)
            # keep create=True, so a terminal write still restores an evicted job correctly.
            if not create:
                return
            job = _RUN_JOBS[job_id] = {}
        job.update(fields)
        if len(_RUN_JOBS) > _RUN_JOBS_MAX:
            for stale in list(_RUN_JOBS)[: len(_RUN_JOBS) - _RUN_JOBS_MAX]:
                _RUN_JOBS.pop(stale, None)


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _RUN_JOBS_LOCK:
        job = _RUN_JOBS.get(job_id)
        return dict(job) if job is not None else None


def _quote_complete(quote: Any) -> bool:
    """True only if the run produced a real answer, not insufficient data.

    A real answer is a solved dispatch, OR at least one candidate that produced a priceable /
    returning result, OR the explicit no-viable-configuration verdict — an evaluated "do not
    proceed" is a complete underwriting result, not a failure (2026-07-07: previously such runs
    were mis-reported as "not enough data" and never saved).
    """
    opt = getattr(quote, "optimisation_result", None)
    if getattr(opt, "selection_status", None) == SelectionStatus.NO_VIABLE_CONFIGURATION:
        return True
    if getattr(quote, "dispatch_profile", None) is not None:
        return True
    grid = getattr(quote, "sizing_grid", None)
    if grid is not None and getattr(grid, "points", None):
        return any(
            getattr(p, "priceable", False)
            or getattr(p, "levered_return_pct", None) is not None
            or getattr(p, "project_irr_pct", None) is not None
            for p in grid.points
        )
    return False


def _is_complete(stored: store.StoredRun) -> bool:
    """Listing predicate: a stored run is shown only if it actually solved a system."""
    return _quote_complete(stored.quote)


#: Run labels (surfaced as warnings) for honest provenance of estimate-grade inputs.
_SYNTHETIC_LABEL = (
    "Load profile is SYNTHETIC: modelled from your annual usage and business type, not metered "
    "data. This is an estimate, not bankable. Upload a half-hourly meter file for a bankable run."
)
_CONSTRAINED_LABEL = (
    "Load profile is MODELLED, not metered: built from your transcribed billing constraints "
    "(annual energy, day/night volume split, observed peak and its timing) rather than a "
    "business archetype. Every structural feature traces to a stated observation; the residual "
    "placement is deterministic and disclosed. This is an estimate, not bankable. Upload a "
    "half-hourly meter file for a bankable run."
)
_TARIFF_REF_LABEL = (
    "Tariff uses standard reference rates for the selected tariff type, not your confirmed rates. "
    "Confirm your actual standing charge, unit rates and DUoS bands for a bankable floor."
)

#: Standard published-style reference rates per tariff type (£/kWh; standing in £/day). These are
#: confirm-able defaults so a floor can be estimated, never the customer's confirmed tariff; their
#: use is disclosed via the tariff-reference warning and the run stays not-bankable until confirmed.
#: Time-of-use import rates use band labels the engine maps to a schedule (red/amber/green,
#: day/night, peak/off-peak), so arbitrage actually prices; a flat rate intentionally yields none.
_TARIFF_REF: dict[str, dict[str, str]] = {
    "Half-Hourly C&I (DUoS RAG)": {
        "import_rates": "red:0.382,amber:0.248,green:0.165",
        "standing_charge": "0.45",
    },  # noqa: E501
    "Flat Rate": {"import_rates": "flat:0.28", "standing_charge": "0.45"},
    "Economy 7 (Day/Night)": {"import_rates": "day:0.30,night:0.14", "standing_charge": "0.45"},
    "EV Time-of-Use": {"import_rates": "peak:0.35,offpeak:0.075", "standing_charge": "0.45"},
    "Seasonal Time-of-Use": {"import_rates": "peak:0.40,offpeak:0.22", "standing_charge": "0.45"},
    "Demand + HH Metered": {
        "import_rates": "red:0.30,amber:0.24,green:0.16",
        "standing_charge": "0.55",
    },  # noqa: E501
}


def _apply_reference_data(data: dict[str, Any]) -> bool:
    """Apply standard reference time-of-use import rates for the selected tariff type.

    Fills ONLY the tariff bands, and only when the user supplied none, so the arbitrage floor can
    be estimated; returns True when they were applied, which the caller turns into the
    `_TARIFF_REF_LABEL` disclosure on the run record. Never overwrites a value the user provided.

    **ASC IS NOT FILLED HERE, DELIBERATELY.** This used to copy MIC into `asc_kw` whenever the
    visitor left ASC blank. MIC and ASC are two DIFFERENT contractual capacities (the DNO's maximum
    import capacity vs the agreed/available supply capacity the £/kVA/day availability charge is
    billed on), so copying one into the other INVENTS a contractual fact the customer never
    supplied — with no `DerivedAssumption`, no warning and nothing on the record (D45: never invent
    silently). It also contradicts this repo's own doctrine for the same field, written into the
    ranked data ask at `ingestion/missing_data.py:139-141`: "confirm the actual MIC/ASC with the
    DNO (never auto-filled)". The invented value then read downstream as a *supplied* capacity —
    `outputs.report_charts.capacity_is_supplied` returns True the moment `connection.asc_kw` is not
    None — so every wizard site rendered a "recorded agreed capacity" in the pack, and the ASC leg
    of the `connection_mic_mec_asc` data ask vanished. Leaving the field absent costs nothing on
    the pricing path: every priced consumer already falls back to MIC
    (`optimisation/milp_solver.py:320`, `:728`, `optimisation/engine.py:2645`), so the £ is
    unchanged and only the *claim* is corrected.
    """
    bands = _TARIFF_REF.get((data.get("tariff_type") or "").strip())
    applied = False
    if bands and not data.get("import_rates") and not data.get("duos_red_rate"):
        for key, value in bands.items():
            data.setdefault(key, value)
        applied = True
    return applied


def _resolve_assumption_version() -> tuple[str, list[str]]:
    """The release this run prices against, plus any warning that must reach the run record.

    Mirrors the CLI (`gridq.cli`, `cli.py:248-265`, MD-F-15): the default is the NEWEST release
    resolved from disk, never a hard-coded version, and an explicitly pinned superseded release is
    allowed but says so loudly. The note is RETURNED rather than logged so the caller can thread it
    into ``generate_quote(extra_warnings=...)`` and it lands in ``QuoteRecord.warnings`` -- the
    layer a reader actually reads, on the page and in the pack, not a server log nobody sees.
    """
    from gridq.assumptions import available_versions, latest_version

    pin = ASSUMPTION_VERSION_PIN
    if pin is None:
        return latest_version(), []
    known = available_versions()
    if known and pin in known and pin != known[-1]:
        return pin, [
            f"PRICED AGAINST A SUPERSEDED ASSUMPTION RELEASE: {pin} (the newest release on disk "
            f"is {known[-1]}). This run is NOT comparable with a `gridq run` of the same site, "
            "which resolves the newest release. Superseded releases carry known-stale values: "
            "gb_v0_1 overstates Capacity Market de-rating ~2.3-4x, and every release before "
            "gb_v1_7 carries an unsourced illustrative degradation cost of 5.0 GBP/MWh against "
            "the levelised 21.3 GBP/MWh, which can flip a site's cycle break-even and with it "
            "the viability verdict. Clear the app's ASSUMPTION_VERSION_PIN to price against the "
            "current library."
        ]
    return pin, []


def _run_summary(stored: store.StoredRun) -> dict[str, Any]:
    """A lightweight row for the Dashboard / Projects listings (no view-model build)."""
    from gridq.app.demo import DEMO_LABEL
    from gridq.outputs import detect_study_basis

    quote = stored.quote
    name = (
        stored.site.site_name if stored.site and stored.site.site_name else None
    ) or quote.site_id
    # Which pricing basis produced the run, on the listing row. A study-basis run prices normally
    # gated revenue into the priced floor, so a list that shows it beside an underwriting run with
    # no distinction is the exact confusion D46/D55 exist to prevent - and until now the frontend
    # stated the basis on NO screen, on either basis. `detect_study_basis` is the same detector
    # `build_view_model` uses (it is cheap: an id read plus a warnings scan), so no listing can
    # disagree with the run screen it links to.
    basis = _basis_display(detect_study_basis(quote))
    return {
        "quote_id": quote.quote_id,
        "label": name or quote.quote_id,
        "site_id": quote.site_id,
        "status": "Complete",
        "is_demo": any(DEMO_LABEL in warning for warning in quote.warnings),
        "basis": basis,
    }


def create_app() -> FastAPI:
    """Build the app (a factory so tests get a fresh, isolated instance)."""
    app = FastAPI(title="GridQ", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))
    templates.env.globals.update(view.JINJA_GLOBALS)
    # Cache-bust static assets by their newest mtime, so an edited CSS/JS is refetched (not served
    # stale from the browser cache) after a deploy. Constant within a process run (deterministic).
    try:
        _newest = max(p.stat().st_mtime for p in (_APP_DIR / "static").rglob("*") if p.is_file())
        templates.env.globals["static_v"] = str(int(_newest))
    except (OSError, ValueError):
        templates.env.globals["static_v"] = "1"
    app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")

    # Compress every sizeable response (HTML, CSS, the vendored JS, data islands): the pages
    # carry inline SVG + JSON islands and the budget is measured over the wire.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # (accounts middleware + routes are installed below, once the renderer exists)
    from gridq.app import auth

    # ---- optional public-URL gate (dormant unless GRIDQ_BASIC_AUTH is set) ----
    # Real mode accepts third-party site submissions, so a public deployment may want a password
    # in front of everything except the health probe / static assets. Off by default (see
    # docs/deployment.md). HTTP Basic only protects credentials when the host terminates TLS.
    _credentials = _basic_auth_credentials()
    if _credentials is not None:
        expected_user, expected_password = _credentials

        @app.middleware("http")
        async def _gate(request: Request, call_next: Any) -> Response:
            path = request.url.path
            if path == "/healthz" or path.startswith("/static"):
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            authorised = False
            if header.startswith("Basic "):
                try:
                    user, _, password = base64.b64decode(header[6:]).partition(b":")
                except (ValueError, UnicodeDecodeError):
                    user, password = b"", b""
                # ``&`` (not ``and``) so both compares always run — no early-exit timing oracle.
                authorised = secrets.compare_digest(user, expected_user) & secrets.compare_digest(
                    password, expected_password
                )
            if not authorised:
                return Response(
                    "Authentication required.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="GridQ"'},
                )
            return await call_next(request)

    def render(request: Request, name: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
        # Every template can render the account chip / sign-in state; scoping decisions stay in
        # the handlers. current_user never raises (anonymous workspace fallback).
        ctx.setdefault("user", auth.current_user(request))
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    # ---- accounts: Google sign-in (env-activated) + per-visitor workspaces ----
    # Session middleware + /auth routes; project listings and run pages scope to the visitor's
    # identity (Google `sub` when signed in, an anonymous per-browser workspace otherwise).
    # NOTE middleware ordering: add_middleware is LIFO, so installing auth AFTER GZip (and after
    # the optional basic-auth gate) makes SessionMiddleware the OUTER layer - handlers and
    # render() can always read request.session.
    auth.install(app, render)

    # ---- health probe (cheap, no template / no pandas; host healthcheck target) ----
    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def _hero_stats(complete: list[store.StoredRun]) -> dict[str, Any] | None:
        """Real figures for the landing hero tiles, read off the newest genuinely-solved run.

        The tiles previously carried numbers transcribed from the reference-site benchmark
        capture (goal FINDINGS F-011) — figures no run of THIS engine produced. Every value
        here comes from the persisted record via the view model; when the store holds no
        solved run the caller renders the honest empty state instead. Never invented.
        """
        if not complete:
            return None
        from gridq.outputs import build_view_model, detect_study_basis
        from gridq.outputs import formatting as fmt

        stored = complete[0]
        vm = build_view_model(stored.quote, stored.site)
        if vm.spec.selected_power_kw is None or vm.spec.selected_energy_kwh is None:
            return None
        savings = (
            vm.bill_savings.annual_savings_gbp
            if vm.bill_savings.available and vm.bill_savings.annual_savings_gbp is not None
            else None
        )
        return {
            "label": _run_summary(stored)["label"],
            "quote_id": stored.quote.quote_id,
            "power_kw": vm.spec.selected_power_kw,
            "energy_kwh": vm.spec.selected_energy_kwh,
            "irr_pct": vm.returns.irr_pct,
            "irr_label": view.fmt_opt(vm.returns.irr_pct, fmt.pct),
            "payback_years": vm.returns.payback_years,
            "payback_label": view.fmt_opt(vm.returns.payback_years, fmt.years),
            "savings_gbp": savings,
            "savings_label": view.fmt_opt(savings, fmt.gbp_compact),
            # The hero headlines the IRR and payback of the newest solved run. On a study-basis run
            # those figures include revenue the underwriting basis holds out of the priced floor
            # entirely, so the tiles must say which basis produced them or the front page presents a
            # feasibility figure as an engine result (D46: no study figure may be presented as an
            # underwriting output). The value comes off the record via the view model.
            "basis": _basis_display(detect_study_basis(stored.quote)),
        }

    # ---- dashboard (front page) ------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request) -> HTMLResponse:
        from gridq.app.demo import load_haywood_oaks_prefill

        # Scoped to the visitor: their own runs + legacy/shared ones. The hero tiles and the
        # WebGL surface read the newest run VISIBLE TO THIS VISITOR - another account's run
        # numbers must never headline someone else's landing page.
        me = auth.current_user(request)["id"]
        complete = [s for s in store.list_runs(visible_to_id=me) if _is_complete(s)]
        summaries = [_run_summary(stored) for stored in complete]
        stats = {
            "projects": len(summaries),
            "completed": len(summaries),
            "in_progress": 0,
            "markets": 1,
        }
        # The WebGL hero draws the newest solved run's REAL sizing surface (same island shape
        # the results page uses, projected down); no solved run = the static SVG fallback.
        hero_surface = (
            view.hero_surface_island(complete[0].quote.sizing_grid)
            if complete
            else {"available": False}
        )
        return render(
            request,
            "landing.html",
            {
                "has_example": load_haywood_oaks_prefill() is not None,
                "stats": stats,
                "recent": summaries[:5],
                "hero": _hero_stats(complete),
                "hero_surface": hero_surface,
            },
        )

    # ---- projects (only completed runs; half-finished quotes are not shown) ----
    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request) -> HTMLResponse:
        # Per-account scoping (2026-07-07): the tab previously listed EVERYONE's projects.
        # Now: the visitor's own runs plus legacy/shared runs (no recorded owner), marked so.
        me = auth.current_user(request)["id"]
        scoped = [s for s in store.list_runs(visible_to_id=me) if _is_complete(s)]
        runs = []
        for stored in scoped:
            row = _run_summary(stored)
            row["shared"] = stored.owner_id is None
            runs.append(row)
        return render(request, "projects.html", {"runs": runs})

    # ---- equipment catalogue ---------------------------------------------
    @app.get("/equipment", response_class=HTMLResponse)
    def equipment_page(request: Request) -> HTMLResponse:
        from gridq.app import equipment

        added = request.query_params.get("added") is not None
        return render(request, "equipment.html", {"eq": equipment.catalogue(), "added": added})

    @app.get("/equipment/upload", response_class=HTMLResponse)
    def equipment_upload_form(request: Request) -> HTMLResponse:
        from gridq.app import equipment

        return render(
            request,
            "equipment_upload.html",
            {
                "specs": equipment.CATEGORY_SPECS,
                "labels": equipment.CATEGORY_LABELS,
                "category": "solar",
                "form": {},
            },
        )

    @app.post("/equipment/upload")
    async def equipment_upload_submit(request: Request) -> Response:
        """Validate a user-entered product and add it to the (gitignored) catalogue, then redirect.

        Honesty invariant: the human types every figure; an optional datasheet is stored only as
        provenance, never parsed for numbers. A bad submission re-renders the form with a message
        (never a 500), mirroring the real-site form.
        """
        from gridq.app import equipment

        form = await request.form()
        category = _s(form, "category") or ""
        specs = equipment.CATEGORY_SPECS
        ctx: dict[str, Any] = {
            "specs": specs,
            "labels": equipment.CATEGORY_LABELS,
            "category": category if category in specs else "solar",
            # Echo submitted text values back so a failed submit is not retyped (files excluded).
            "form": {k: v for k, v in form.items() if isinstance(v, str)},
        }

        def fail(message: str) -> HTMLResponse:
            return render(request, "equipment_upload.html", {**ctx, "error": message})

        if category not in specs:
            return fail("Choose an equipment category.")
        maker = _s(form, "maker")
        model = _s(form, "model")
        if not maker or not model:
            return fail("Manufacturer and model are both required.")

        # Per-category fields are namespaced ``{category}__{key}`` in the form so the three category
        # field groups (all present for the no-JS path) never collide on a shared name like "price".
        entry: dict[str, Any] = {"maker": maker, "model": model}
        spec = specs[category]
        prefix = f"{category}__"
        for field in spec["numbers"]:
            value = _f(form, prefix + field["key"])
            if value is None:
                return fail(f"{field['label']} is required and must be a number.")
            if not (field["min"] < value <= field["max"]):
                return fail(
                    f"{field['label']} must be greater than {field['min']:g} "
                    f"and no more than {field['max']:g}."
                )
            entry[field["key"]] = value
        for field in spec["texts"]:
            text = _s(form, prefix + field["key"])
            if field.get("required") and not text:
                return fail(f"{field['label']} is required.")
            entry[field["key"]] = text or ""
        for field in spec["bools"]:
            entry[field["key"]] = _s(form, prefix + field["key"]) is not None

        datasheet: tuple[bytes, str] | None = None
        upload = form.get("datasheet")
        if isinstance(upload, StarletteUploadFile) and upload.filename:
            data = await upload.read()
            if data:
                ext = Path(upload.filename).suffix.lower()
                if ext not in equipment.ALLOWED_DATASHEET_EXT:
                    return fail("Datasheet must be a PDF, PNG or JPG file.")
                if len(data) > equipment.MAX_DATASHEET_BYTES:
                    return fail("Datasheet must be 10 MB or smaller.")
                datasheet = (data, ext)

        equipment.add_equipment(category, entry, datasheet=datasheet)
        return RedirectResponse("/equipment?added=1", status_code=303)

    @app.get("/equipment/datasheet/{name}")
    def equipment_datasheet(name: str) -> Response:
        """Serve a user-attached datasheet from the gitignored store (safe-id checked)."""
        from fastapi.responses import FileResponse

        from gridq.app import equipment

        path = equipment.datasheet_path(name)
        if path is None:
            return Response("Not found.", status_code=404)
        media = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        return FileResponse(
            str(path), media_type=media.get(path.suffix.lower(), "application/octet-stream")
        )

    # ---- review: red-team check of a run (every issue + fix + owner) ------
    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request) -> HTMLResponse:
        from gridq.app import redteam

        me = auth.current_user(request)["id"]
        completed = [s for s in store.list_runs(visible_to_id=me) if _is_complete(s)]
        by_id = {s.quote.quote_id: s for s in completed}
        requested = request.query_params.get("run")
        selected = by_id.get(requested) if requested else None
        if selected is None and completed:
            selected = completed[0]
        return render(
            request,
            "review.html",
            {
                "runs": [_run_summary(s) for s in completed],
                "selected_id": selected.quote.quote_id if selected else None,
                "review": redteam.red_team(selected) if selected is not None else None,
                "severity_label": redteam.SEVERITY_LABEL,
            },
        )

    # ---- new quote: the 5-stage guided wizard ----------------------------
    @app.get("/new-quote", response_class=HTMLResponse)
    def new_quote(request: Request) -> HTMLResponse:
        from gridq.app import equipment

        return render(request, "new_quote.html", {"eq": equipment.catalogue()})

    @app.post("/api/run")
    async def api_run_start(request: Request) -> JSONResponse:
        """Start the real pipeline as a background job; return a job id the client polls.

        The wizard and the results-page improve panel submit the site fields here, then poll
        ``/api/run/{job_id}`` and navigate to ``/run/{quote_id}`` when the job reports done. The
        browser is never blocked. Field mapping only: the grid-connection field is in kVA (the site
        builder reads ``mic_kw``); business type, tariff type, coordinates, archetype and equipment
        choice are UI context, not consumed by the deterministic engine.
        """
        form = await request.form()
        data = dict(form)

        from gridq.finance.lender_terms import lender_terms_from_fields
        from gridq.finance.lifecycle import lifecycle_inputs_from_fields
        from gridq.ingestion.validators import IngestionError

        # The wizard's demo path posts demo=1: run the curated illustrative dataset through this
        # same streaming job, so the demo walks the wizard, shows the run animation, and lands on
        # the rich demo result (DemoEngine + illustrative debt), not a separate synchronous path.
        is_demo = bool(data.get("demo"))
        extra_warnings: list[str] = []
        debt_terms = None
        # Where this run's upload and modelled profile live while it is processed: deleted when
        # `_work` ends (or here, if the request fails first). Never under the run store.
        workspace = TransientWorkspace()
        lender_terms = None
        lifecycle = None

        if is_demo:
            from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, load_demo_site

            site = load_demo_site()
            debt_terms = DEMO_DEBT_TERMS
            extra_warnings = [DEMO_LABEL]
        else:
            if data.get("grid_connection_kva") and not data.get("mic_kw"):
                data["mic_kw"] = data["grid_connection_kva"]
            if _apply_reference_data(data):
                extra_warnings.append(_TARIFF_REF_LABEL)

            # Build the site (and any meter file) here so input errors are a synchronous 400. With
            # no uploaded meter file but a stated annual usage, synthesise a half-hourly profile so
            # the run solves (graded T1, modelled not metered) instead of returning insufficient
            # data; the SYNTHETIC label keeps it honest and not bankable.
            try:
                # WP-UW-03 (B001): the lender-terms block, parsed FIRST so a half-entered term sheet
                # is a 400 before any upload or solve (LenderTermsError is a ValueError). All blank
                # means no terms: the run then states why it has no DSCR.
                lender_terms = lender_terms_from_fields(data)
                # WP-UW-17: the lifecycle_json field, parsed first for the same reason
                # (LifecycleInputsError is a ValueError). Blank means none stated.
                lifecycle = lifecycle_inputs_from_fields(data)
                hh_summary = await _save_uploaded_hh(form, workspace)
                hh_summary, synthetic, constrained = _synthesise_profile_if_needed(
                    data, hh_summary, extra_warnings, workspace
                )
                site = _site_from_form(data, hh_summary)
            except (IngestionError, ValueError) as exc:
                workspace.close()
                return JSONResponse({"error": "invalid", "message": str(exc)}, status_code=400)
            except BaseException:
                workspace.close()
                raise

            if synthetic:
                site = _mark_modelled_load(site, constrained=constrained)
                extra_warnings.append(_CONSTRAINED_LABEL if constrained else _SYNTHETIC_LABEL)

        job_id = secrets.token_hex(8)
        _set_job(
            job_id,
            status="running",
            stage="optimise",
            stage_done=None,
            stage_total=None,
            quote_id=None,
            message=None,
        )
        # Ownership is captured HERE, on the request thread - the worker thread has no request
        # (and touching the session off-request would be wrong anyway). The curated demo is ONE
        # deterministic shared record every visitor lands on - it stays unowned on purpose
        # (owning it would hide it from, or reassign it between, later visitors).
        owner_id = None if is_demo else auth.current_user(request)["id"]

        def _work() -> None:
            from gridq.assumptions import load_assumptions
            from gridq.quotes import generate_quote

            try:
                if is_demo:
                    from gridq.app.demo import DemoEngine

                    engine: Any = DemoEngine()
                else:
                    from gridq.optimisation import MilpEngine

                    engine = MilpEngine()

                # Real, forward-only progress: the deterministic pipeline pushes phase facts
                # (phase, done, total) into this job's dict via _set_job; GET /api/run/{job_id}
                # returns them verbatim and the client renders the bar. Side channel only — it
                # writes no computed number, so the solve stays byte-identical (C035).
                def _progress(phase: str, done: int | None, total: int | None) -> None:
                    _set_job(job_id, create=False, stage=phase, stage_done=done, stage_total=total)

                # The release is resolved FROM DISK per run (never a stale module constant), and a
                # deliberately pinned superseded release rides onto the record as a warning.
                version, release_notes = _resolve_assumption_version()
                run = generate_quote(
                    site,
                    load_assumptions(version),
                    engine=engine,
                    debt_terms=debt_terms,
                    lender_terms=lender_terms,
                    lifecycle=lifecycle,
                    quote_mode=QuoteMode.INDICATIVE,
                    extra_warnings=[*extra_warnings, *release_notes] or None,
                    progress=_progress,
                )
                if not _quote_complete(run.quote):
                    _set_job(
                        job_id,
                        status="error",
                        message=(
                            "Not enough data to produce a result. Enter annual usage so we can "
                            "model a load profile, or upload a half-hourly meter file."
                        ),
                    )
                    return
                if is_demo:
                    # The curated demo is server-chosen and attested by hash (never visitor input).
                    # The permit names the demo site, so `save_run` refuses any other record.
                    store.save_run(
                        run.quote, run.site, run.telemetry, dpa=authorise_store(DEMO_SITE_ID)
                    )
                else:
                    _keep_run(run, owner_id=owner_id)
                _set_job(job_id, status="done", stage="done", quote_id=run.quote.quote_id)
            except Exception as exc:  # noqa: BLE001 — report any engine failure to the client honestly
                _set_job(job_id, status="error", message=str(exc) or "the run failed")
            finally:
                # The raw upload and modelled profile are deleted when the job ends (WP-GRC-05).
                workspace.close()

        threading.Thread(target=_work, daemon=True).start()
        return JSONResponse({"job_id": job_id})

    @app.get("/api/run/{job_id}")
    def api_run_status(job_id: str) -> JSONResponse:
        """Poll a run job's status: ``running`` / ``done`` (with ``quote_id``) / ``error``."""
        job = _get_job(job_id)
        if job is None:
            return JSONResponse({"status": "unknown"}, status_code=404)
        return JSONResponse(job)

    # ---- real-site mode: unified with the wizard (the single guided intake) ----
    @app.get("/real")
    def real_form(request: Request) -> Response:
        return RedirectResponse("/new-quote", status_code=307)

    @app.get("/real/haywood-oaks", response_class=HTMLResponse)
    def real_form_example(request: Request) -> HTMLResponse:
        """The real-mode form, pre-filled from the private real-prospect example when installed.

        Falls back to a blank real form (never a 404/500) when the gitignored facts file is absent,
        so a clean public checkout simply shows the normal intake form.
        """
        from gridq.app.demo import load_haywood_oaks_prefill

        prefill = load_haywood_oaks_prefill()
        ctx: dict[str, Any] = {"nav_active": "real", "error": None, "form": prefill or {}}
        if prefill is not None:
            ctx["example_note"] = _EXAMPLE_NOTE
        return render(request, "real_form.html", ctx)

    async def _run_real_in(request: Request, form: Any, workspace: TransientWorkspace) -> Response:
        """The body of `run_real`, inside the request's transient workspace."""
        from gridq.finance.lender_terms import lender_terms_from_fields
        from gridq.finance.lifecycle import lifecycle_inputs_from_fields
        from gridq.ingestion.validators import IngestionError

        real_warnings: list[str] = []
        try:
            lender_terms = lender_terms_from_fields(form)
            lifecycle = lifecycle_inputs_from_fields(form)
            hh_summary = await _save_uploaded_hh(form, workspace)
            # Validation Gate 2: every underwritten site has half-hourly data, and where no meter
            # export exists, MODELLING the shape is standard practice rather than a degradation.
            # This route previously skipped modelling ("profile modelling lives on the job path
            # only"), which under the mandate turns a graceful degrade into a hard refusal. The
            # generation itself is cheap — it is the MILP search that is slow, and that runs on
            # this route either way — so the stated reason did not apply to this step.
            hh_summary, synthetic, constrained = _synthesise_profile_if_needed(
                form, hh_summary, real_warnings, workspace
            )
            site = _site_from_form(form, hh_summary)
            if synthetic:
                site = _mark_modelled_load(site, constrained=constrained)
                real_warnings.append(_CONSTRAINED_LABEL if constrained else _SYNTHETIC_LABEL)
        except (IngestionError, ValueError) as exc:
            return render(
                request,
                "real_form.html",
                {"nav_active": "real", "error": str(exc), "form": dict(form)},
                status=400,
            )

        from gridq.assumptions import load_assumptions
        from gridq.optimisation import MilpEngine
        from gridq.quotes import generate_quote

        # Same release resolution as the job path and the CLI (never a stale constant). The
        # accumulated `real_warnings` are threaded in too: they were built above and then DROPPED,
        # so a no-JS run whose load profile was modelled carried no MODELLED/SYNTHETIC label on its
        # own record. They are disclosure only and move no number.
        version, release_notes = _resolve_assumption_version()
        run = generate_quote(
            site,
            load_assumptions(version),
            engine=MilpEngine(),
            quote_mode=QuoteMode.INDICATIVE,
            extra_warnings=[*real_warnings, *release_notes] or None,
            lender_terms=lender_terms,
            lifecycle=lifecycle,
        )
        quote_id = _keep_run(run, owner_id=auth.current_user(request)["id"])
        return RedirectResponse(f"/run/{quote_id}", status_code=303)

    @app.post("/run/real")
    async def run_real(request: Request) -> Response:
        """Synchronous no-JS fallback for the real form: honest degrade, NO profile modelling.

        The real form submits via JS to the streaming job (``/api/run``, same machinery as the
        wizard), which models a disclosed profile from stated billing facts and runs the full
        sizing search without blocking the browser. This synchronous route stays for no-JS
        clients and the smoke-test contract: it runs whatever the form carries as-is (a T0 run
        when no meter file is supplied) - a multi-minute MILP search inside one blocking
        request is not a user experience, so profile modelling lives on the job path only.
        """
        form = await request.form()
        # The upload and any modelled profile are deleted when this request ends (WP-GRC-05).
        workspace = TransientWorkspace()
        try:
            return await _run_real_in(request, form, workspace)
        finally:
            workspace.close()

    # ---- demo mode -------------------------------------------------------
    @app.get("/demo")
    def demo_page(request: Request) -> Response:
        # The demo now walks the wizard with the dataset prefilled (and the run animation), instead
        # of a separate synchronous path. The curated dataset still runs via /api/run (demo=1).
        return RedirectResponse("/new-quote?demo=1", status_code=307)

    @app.post("/run/demo")
    def run_demo(request: Request) -> Response:
        from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, DemoEngine, load_demo_site
        from gridq.assumptions import load_assumptions
        from gridq.quotes import generate_quote

        site = load_demo_site()
        version, release_notes = _resolve_assumption_version()
        run = generate_quote(
            site,
            load_assumptions(version),
            engine=DemoEngine(),
            debt_terms=DEMO_DEBT_TERMS,
            quote_mode=QuoteMode.INDICATIVE,
            extra_warnings=[DEMO_LABEL, *release_notes],
        )
        # The curated demo is exempt only while its files match the attested hashes (WP-GRC-05).
        store.save_run(run.quote, run.site, run.telemetry, dpa=authorise_store(DEMO_SITE_ID))
        return RedirectResponse(f"/run/{run.quote.quote_id}", status_code=303)

    # ---- run screens -----------------------------------------------------
    @app.get("/run/{quote_id}", response_class=HTMLResponse)
    def inspect(request: Request, quote_id: str) -> HTMLResponse:
        ctx = _run_context(request, quote_id, "inspect")
        if ctx is None:
            return render(request, "not_found.html", {"quote_id": quote_id}, status=404)
        ctx["surface"] = view.surface_island(ctx["quote"].sizing_grid)
        ctx["finance"] = view.finance_island(ctx["vm"])
        ctx["dispatch"] = view.dispatch_island(ctx["quote"].dispatch_profile)
        ctx["utilisation"] = view.utilisation_island(ctx["quote"].dispatch_profile)
        ctx["value_stack"] = view.value_stack_island(ctx["vm"])
        from gridq.app import upside

        if any(upside.UPSIDE_LABEL in warning for warning in ctx["quote"].warnings):
            power_kw = ctx["vm"].spec.selected_power_kw
            ctx["upside_streams"] = upside.illustrative_stream_gbp(power_kw)
            ctx["upside"] = upside.portfolio_view(
                power_kw, ctx["vm"].split.floor_total_gbp, ctx["vm"].split.upside_total_gbp
            )
        if ctx["site"] is not None:
            from gridq.ingestion import build_ranked_data_asks

            ctx["asks"] = build_ranked_data_asks(ctx["site"])
        else:
            ctx["asks"] = []
        return render(request, "inspect.html", ctx)

    @app.get("/run/{quote_id}/spec", response_class=HTMLResponse)
    def spec(request: Request, quote_id: str) -> HTMLResponse:
        ctx = _run_context(request, quote_id, "spec")
        if ctx is None:
            return render(request, "not_found.html", {"quote_id": quote_id}, status=404)
        return render(request, "spec.html", ctx)

    @app.get("/run/{quote_id}/artifacts", response_class=HTMLResponse)
    def artifacts(request: Request, quote_id: str) -> HTMLResponse:
        ctx = _run_context(request, quote_id, "artifacts")
        if ctx is None:
            return render(request, "not_found.html", {"quote_id": quote_id}, status=404)
        from gridq.outputs import generate_artifacts

        arts = generate_artifacts(ctx["quote"], ctx["site"])
        ctx["report_html"] = arts.report_html
        return render(request, "artifacts.html", ctx)

    @app.get("/run/{quote_id}/telemetry", response_class=HTMLResponse)
    def telemetry(request: Request, quote_id: str) -> HTMLResponse:
        ctx = _run_context(request, quote_id, "telemetry")
        if ctx is None:
            return render(request, "not_found.html", {"quote_id": quote_id}, status=404)
        return render(request, "telemetry.html", ctx)

    return app


app = create_app()


def main() -> None:
    """Console entry: run the app with uvicorn (``python -m gridq.app``)."""
    import uvicorn

    host = os.environ.get("GRIDQ_APP_HOST", "127.0.0.1")
    port = int(os.environ.get("GRIDQ_APP_PORT", "8000"))
    uvicorn.run("gridq.app.app:app", host=host, port=port, reload=False)
