# 03 · Data Contracts — GridQ v1

**Status:** Ratified (frozen v1) · **Date:** 2026-06-22 · **Owners:** Olly
**Reads with:** [docs/02_architecture.md](02_architecture.md) (§4 contract overview), [docs/01_project_spec.md](01_project_spec.md) (module map), [docs/issues/005-mock-optimisation-contract.md](issues/005-mock-optimisation-contract.md) & [014](issues/014-engine-integration-contract.md), [docs/decisions.md](decisions.md) (D8 ratification).

This document is the **human-readable mirror of the implemented Pydantic v1 contracts** in [`src/gridq/schemas/`](../src/gridq/schemas/). The schema layer is the **source of truth**: this doc tracks it field-for-field. Where the two ever disagree, the code wins and this doc is a bug. Every model inherits `GridQModel` ([provenance.py](../src/gridq/schemas/provenance.py)), which forbids unknown fields (`extra="forbid"`) and carries a `schema_version` string (default `SCHEMA_VERSION = "0.1.0"`).

> **Ratification (D8 · resolves [OPEN_COMMENTS](shared_work/OPEN_COMMENTS.md) C002 & C004).** The shapes below are the **frozen v1 contracts**. They intentionally diverge from this document's earlier draft (which described an older `import_capacity_kva` / `chosen_spec` / `value_by_stream` / `tag` / `meta` / `lifecycle_status` / `versions{}` shape and a `total_value` field that the code never had). The implemented schemas — `mic_kw`/`mec_kw`/`asc_kw`, flat `selected_*`, `value_streams[]` with a `firmness` enum, `gated_upside[]`, `solver_status`/`solver_metadata`, `quote_mode`, flat hashes — are now authoritative.

### Which contracts couple whom

- **Frozen platform↔engine coupling contracts (the two seams):** `SiteModel` (input) and `OptimisationResult` (output, with its stream registry). Changing either is a deliberate, versioned event.
- **`OptimisationRequest`** — the input *envelope* the engine consumes (bundles the `SiteModel` + requested streams + scenario ref + assumption version + solve mode). Agreed alongside the two, but not a third coupling contract.
- **`FinancialCase`, `QuoteRecord`, `TelemetryEvent`** — Oli-side outputs/records; Olly does not produce them. Listed here so the whole pipeline's shapes are in one place.

### Conventions (apply to every contract)

- **Base model:** every contract is a `GridQModel` — `extra="forbid"` (an unknown/typo field is a validation error, not a pass-through) and a `schema_version: str` field defaulting to `"0.1.0"`. There is **one shared `SCHEMA_VERSION`**, not a per-contract semver tag.
- **Units:** power `kW`; energy `kWh`; money `GBP` (whole £ unless a field is named `*_gbp_per_kwh` or `*_per_day_gbp`); rates/levies are `£/kWh`; time in hours; dates/datetimes are ISO-8601. Half-hourly load is 48 periods/day (≈17 520 rows/yr).
- **Connection capacity — kW, not kVA (v1 convention).** `mic_kw`/`mec_kw`/`asc_kw` are named and treated as **kW** in v1. MIC/MEC/ASC are *conventionally* apparent power (kVA); v1 deliberately collapses the kW-vs-kVA distinction and records them as kW. The reconciliation note lives in [connection.py](../src/gridq/schemas/connection.py); the glossary keeps the real-world kVA definition of the terms.
- **Non-negative by default, with two documented exceptions.** Most numeric fields are `ge=0`. The exceptions: `ConnectionDetails.import_headroom_kw` / `export_headroom_kw` may be **negative** (over-subscribed connection), and `FinancialCase.p50_cashflows` / `p90_cashflows` may be **negative** (year-0 capex). Both are noted in their schemas.
- **`Optional` / `None` means "not supplied", never zero (D45, [decisions.md](decisions.md)).** Optionality is a *schema* permission — the contract accepts a site whose `duos_red_rate` or `annual_kwh` is absent — not a licence for a consumer to read that absence as a value. **On the pricing path a consumer must never coerce `None → 0.0`** (nor rely on falsy truthiness, which swallows a genuine `0.0` too): either the value is derived-and-declared upstream so the pipeline reads a real recorded number, or the affected £ is **refused and named**. An absent rate silently read as £0 is not "unknown" — it is a confidently wrong number, and it enters P50, P90 and the DSCR indistinguishable from a measured one.
- **Run identity / determinism pin:** an `OptimisationResult` carries its run metadata on `solver_metadata` (`seed`, `assumption_version`, `code_version`, `model_version`, `solve_seconds`, `reproduction_env`). A `QuoteRecord` carries flat `input_hash` / `assumption_version` / `code_version`. `Provenance` ([provenance.py](../src/gridq/schemas/provenance.py)) is a **reusable** traceability block (`input_hash`, `assumption_version`, `code_version`, `model_version`, `seed`, `output_hash`, `reproduction_env`) — available for embedding, but **not currently embedded** in any contract below.
- **Annual basis:** `ValueStreamResult.annual_revenue_gbp` / `annual_cost_gbp` are year-1 nameplate figures. **M5 owns the multi-year extension** across the projection horizon (degradation, throughput decay, indexation), reading the relevant X.1 keys. The horizon length is `DEFAULT_HORIZON_YEARS` — a labelled structural model parameter, not an assumption-library £-value ([decisions.md](decisions.md) D9).
- **Stream registry & firmness:** stream ids come from the **versioned `MarketStream` registry** (part of the frozen `OptimisationResult` contract). Each `ValueStreamResult` carries a `firmness` ∈ `firm | contracted | conditional | excluded`. The three **gated-upside** streams (`WHOLESALE_P415`, `BALANCING_MECHANISM`, `ANCILLARY_DC_DM_DR`) live in `gated_upside[]` and can **never** be `firm`. Adding/retagging a stream bumps the contract version.
- **Per-stream consent (D44):** an explicit customer consent (`SiteModel.stream_consents`) is *authorisation*, not value — it admits the **CONTRACTED** case, never FIRM. The gated-upside never-OPEN rule relaxes to **never-OPEN-without-an-explicit-consent-basis**: a consented gated stream may open to `contracted` (wholesale/P415 also needs `export_dispatch_enabled`; CM keeps its deliverability physics — consent cannot bypass `deliverability_confirmed`). The orthogonal `"consent given"` label is carried by the additive optional `consent_status` on both `GateResult` and `ValueStreamResult`; it never changes firmness or priceability.
- **No `archetype` field** anywhere in `SiteModel` — the engine reasons from the site's own data (and `extra="forbid"` rejects one if added).
- **Enums** are closed `StrEnum` sets; an unknown value is a validation error. The full enum vocabularies are in [enums.py](../src/gridq/schemas/enums.py) and listed in [§7](#7-enums-closed-vocabularies).
- **Illustrative numbers only.** All values in the JSON blocks are illustrative placeholders, not assumptions; real numbers live only in the versioned assumption library.

---

## 1. `SiteModel` — *input contract (M1); frozen, shared*

The canonical, validated representation of one behind-the-meter site. Everything downstream reads it; ingestion (M0) produces it. Composed of `LoadDataSummary`, `TariffStack`, `ConnectionDetails`, `ConsentState`, `PhysicalGateStatus` and `Counterparty`. Source: [site.py](../src/gridq/schemas/site.py).

```jsonc
{
  "schema_version": "0.1.0",
  "site_id": "site_000123",
  "site_name": "Acme Cold Store, Unit 4",            // optional human label
  "postcode": "LE17 4XN",
  "country": "GB",                                    // default "GB"
  "data_tier": "T2_VALIDATED_12_MONTH_HALF_HOURLY",   // DataTier enum
  "load_data": { "...": "LoadDataSummary, see §1.1" },
  "annual_consumption_kwh": null,                     // top-level annual figure (T0 path)
  "tariff_stack": { "...": "TariffStack, see §1.2" },
  "connection": { "...": "ConnectionDetails, see §1.3" },
  "consents": { "...": "ConsentState, see §1.4" },
  "physical_gates": { "...": "PhysicalGateStatus, see §1.5" },
  "counterparty": { "...": "Counterparty, see §1.6" },
  "source_references": ["installer_email_2026-06-10.pdf"],  // list[str]
  "missing_data": ["export_capacity"],                       // list[str], ranked data-asks
  "created_at": "2026-06-16T14:05:00Z"
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | from `GridQModel` |
| `site_id` | `str` | **yes** | – | stable internal id |
| `site_name` | `str \| None` | no | `None` | human label |
| `postcode` | `str` | **yes** | – | no format validator in v1 |
| `country` | `str` | no | `"GB"` | |
| `data_tier` | `DataTier` | **yes** | – | closed enum (§7) |
| `load_data` | `LoadDataSummary` | **yes** | – | §1.1 |
| `annual_consumption_kwh` | `float \| None` | no | `None` | `ge=0` |
| `tariff_stack` | `TariffStack` | **yes** | – | §1.2 |
| `connection` | `ConnectionDetails` | **yes** | – | §1.3 |
| `consents` | `ConsentState` | **yes** | – | §1.4 |
| `physical_gates` | `PhysicalGateStatus` | **yes** | – | §1.5 |
| `counterparty` | `Counterparty` | **yes** | – | §1.6 |
| `source_references` | `list[str]` | no | `[]` | provenance refs |
| `missing_data` | `list[str]` | no | `[]` | ranked data-asks |
| `evidence_refs` | `tuple[EvidenceLink, ...] \| None` | no | `None` | IC-12 2.0.0: IC-4 evidence links (`supports` + `EvidenceRef`), ordered, no repeats; absent from the dump while `None` |
| `created_at` | `datetime` | no | `now()` | |

**Validation rules (model validators actually enforced):**
- **T2** (`T2_VALIDATED_12_MONTH_HALF_HOURLY`) requires `load_data.has_hh_coverage` — i.e. `load_data.half_hourly_rows`, `start_date` and `end_date` all present.
- **T0** (`T0_ANNUAL_KWH_TARIFF_POSTCODE`) requires `annual_consumption_kwh` **or** `load_data.annual_kwh` to be set.
- `extra="forbid"`: any unknown field (including `archetype`) is rejected.

> Richer tier grading (period-count floors, completeness threshold, leap-year/DST tolerance) is **not** in this schema — it is enforced in the M0 ingestion layer via labelled structural constants in `ingestion/data_tier.py` (`T2_MIN_PERIODS = 17520`, `T2_MIN_COMPLETENESS_PCT = 90.0`, `T1_MIN_PERIODS`, tolerance), per [decisions.md](decisions.md) D10. The 17 520 T2 bar that the ingestion layer enforces is the same one the schema's T2 coverage check assumes.

### 1.1 `LoadDataSummary`

Summary of a site's load data (annual figure and/or half-hourly coverage). Source: [site.py](../src/gridq/schemas/site.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `annual_kwh` | `float \| None` | no | `None` | `ge=0` |
| `half_hourly_file_ref` | `str \| None` | no | `None` | reference to the HH series |
| `half_hourly_rows` | `int \| None` | no | `None` | `ge=0` |
| `start_date` | `date \| None` | no | `None` | |
| `end_date` | `date \| None` | no | `None` | |
| `missing_intervals` | `int \| None` | no | `None` | `ge=0` |
| `max_import_kw` | `float \| None` | no | `None` | `ge=0` |
| `max_export_kw` | `float \| None` | no | `None` | `ge=0` |
| `validation_warnings` | `list[str]` | no | `[]` | |

Computed property `has_hh_coverage` → `True` iff `half_hourly_rows`, `start_date` and `end_date` are all present (drives the T2 check above).

### 1.2 `TariffStack`

Import/export rates, DUoS bands and levy lines. Rate/levy dicts map a label (e.g. `"red"`, `"RO"`) to a `£/kWh` value. Source: [tariff.py](../src/gridq/schemas/tariff.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `supplier` | `str \| None` | no | `None` | |
| `tariff_name` | `str \| None` | no | `None` | |
| `standing_charge_per_day_gbp` | `float \| None` | no | `None` | `ge=0` |
| `import_rates` | `dict[str, float]` | no | `{}` | `£/kWh`; non-negative |
| `export_rates` | `dict[str, float]` | no | `{}` | `£/kWh`; non-negative |
| `duos_red_rate` | `float \| None` | no | `None` | `ge=0` |
| `duos_amber_rate` | `float \| None` | no | `None` | `ge=0` |
| `duos_green_rate` | `float \| None` | no | `None` | `ge=0` |
| `levies` | `dict[str, float]` | no | `{}` | `£/kWh`; non-negative |
| `source` | `str \| None` | no | `None` | |
| `confidence` | `Confidence \| None` | no | `None` | enum (§7) |

**Validator:** `import_rates`, `export_rates` and `levies` values must all be `≥ 0` (a negative rate/levy is rejected).

### 1.3 `ConnectionDetails`

The connection's capacities, voltage, DNO region and headroom. Source: [connection.py](../src/gridq/schemas/connection.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `mic_kw` | `float` | **yes** | – | `ge=0`; Maximum Import Capacity (kW in v1) |
| `mec_kw` | `float \| None` | no | `None` | `ge=0`; Maximum Export Capacity |
| `asc_kw` | `float \| None` | no | `None` | `ge=0`; Available Supply Capacity |
| `voltage_level` | `VoltageLevel \| None` | no | `None` | `LV \| HV \| EHV` |
| `dno_region` | `str \| None` | no | `None` | |
| `import_headroom_kw` | `float \| None` | no | `None` | MIC − peak import; **may be negative** |
| `export_headroom_kw` | `float \| None` | no | `None` | MEC − peak export; **may be negative** |
| `anm_or_curtailment_notes` | `str \| None` | no | `None` | commentary only since IC-12 2.0.0; no term is read from it |
| `agreement` | `ConnectionAgreementRef \| None` | no | `None` | IC-12 2.0.0: the IC-1 agreement, by content hash; absent from the dump while `None` |

### 1.4 `ConsentState`

Grid and supplier consent state. The per-consent value type is the `ConsentStatus` enum (§7). Source: [consents.py](../src/gridq/schemas/consents.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `g98` | `ConsentStatus` | no | `unknown` | |
| `g99` | `ConsentStatus` | no | `unknown` | export-side consent |
| `g100` | `ConsentStatus` | no | `unknown` | export-limitation scheme |
| `export_mpan` | `str \| None` | no | `None` | |
| `dno_application_state` | `ConsentStatus` | no | `not_started` | |
| `supplier_consent_state` | `ConsentStatus` | no | `not_started` | host supply-contract interaction (lives **inside** `consents`) |
| `notes` | `str \| None` | no | `None` | |

### 1.5 `PhysicalGateStatus`

Pass/fail status of the physical install gates (tri-state booleans + notes; thresholds are not in the schema). Source: [site.py](../src/gridq/schemas/site.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `footprint_available` | `bool \| None` | no | `None` | |
| `fire_separation_ok` | `bool \| None` | no | `None` | |
| `access_ok` | `bool \| None` | no | `None` | |
| `planning_or_landlord_issue` | `bool \| None` | no | `None` | |
| `notes` | `str \| None` | no | `None` | |

### 1.6 `Counterparty`

Identity and coarse credit standing. The deterministic credit-score → rate/tenor mapping lives in the finance layer (M9); this only records identity and a coarse status. Source: [counterparty.py](../src/gridq/schemas/counterparty.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `legal_name` | `str` | **yes** | – | |
| `company_number` | `str \| None` | no | `None` | **optional** in v1 (not required) |
| `sector` | `str \| None` | no | `None` | |
| `credit_status` | `CreditStatus` | no | `unknown` | enum (§7) |
| `expected_tenure_years` | `float \| None` | no | `None` | `ge=0` |
| `notes` | `str \| None` | no | `None` | |

**Open questions (still open):**
- **HH formats (spec §10 Q2):** which installer/meter formats normalise into `load_data`. Shapes the M0 parsers.
- **Tariff source (Q3):** where `tariff_stack` (levy lines, DUoS bands) is sourced and kept current.
- **Postcode granularity / privacy / NDA state:** full vs outward postcode; HH data is personal data for sole-trader sites — does `SiteModel` need to carry NDA state? (Not in v1.)
- **DNO region derivation:** derive `connection.dno_region` from postcode, or require it explicitly?
- ~~**Gate inputs not on `SiteModel`** (CM de-rated deliverability, Piclo tender availability, aggregated-portfolio MW)~~ — **resolved** (decision **D12**, OPEN_COMMENTS C006): none goes on `SiteModel` (none is the site's own data — deliverability is engine/sizing-derived, tender availability is time-varying market state, portfolio MW is fleet-level); v1 stays **data-ask-only** (the gates already report them missing); the agreed promotion path is a generic, market-agnostic `GateContext` threaded into the value-stack `check_gates(..., context=None)` when the producing lanes land — never on `SiteModel` and never in the versioned assumption library.

---

## 2. `OptimisationRequest` — *engine input envelope*

The clean object Oli hands the optimiser (baseline/mock now, the real engine later). It carries the validated `SiteModel`, the assumption version, the requested streams and the target solve mode — and nothing else. Source: [optimisation.py](../src/gridq/schemas/optimisation.py).

```jsonc
{
  "schema_version": "0.1.0",
  "request_id": "req_000123_a1",
  "site_model": { "...": "an embedded, validated SiteModel (§1)" },
  "assumption_version": "assumptions/2026-06.3",
  "scenario_set_id": "scenarios/2026-06#central_set",  // optional
  "requested_streams": ["TARIFF_ARBITRAGE", "CAPACITY_MARKET"],  // list[MarketStream]
  "solve_mode": "baseline",                            // SolveMode enum; default "baseline"
  "created_at": "2026-06-16T14:05:00Z"
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | |
| `request_id` | `str` | **yes** | – | |
| `site_model` | `SiteModel` | **yes** | – | embedded, not by-reference, in v1 |
| `assumption_version` | `str` | **yes** | – | |
| `scenario_set_id` | `str \| None` | no | `None` | resolves in M6 / X.1 |
| `requested_streams` | `list[MarketStream]` | no | `[]` | empty ⇒ registry default downstream |
| `solve_mode` | `SolveMode` | no | `baseline` | `baseline \| mock \| production` |
| `created_at` | `datetime` | no | `now()` | |

**Validation rules:** none beyond field types + the embedded `SiteModel` validating. (There is no `sizing_space` / `objective` / `seed` / `horizon_years` envelope in v1 — sizing search space and objective live in the engine; `seed` is stamped on the result's `solver_metadata`; the projection horizon is the finance layer's `DEFAULT_HORIZON_YEARS`, see D9.)

---

## 3. `OptimisationResult` — *engine output; frozen, shared (with the stream registry)*

What the optimisation engine (and the baseline/mock) returns — the seam M5/M8 consume without importing the engine. Carries a flat selected spec, the per-stream `value_streams[]`, the `gated_upside[]` register, the binding constraints and the solver outcome/metadata. Source: [optimisation.py](../src/gridq/schemas/optimisation.py); `ValueStreamResult` and `GATED_UPSIDE_STREAMS` from [market.py](../src/gridq/schemas/market.py).

```jsonc
{
  "schema_version": "0.1.0",
  "result_id": "res_000123_a1",
  "request_id": "req_000123_a1",                       // echoes the request
  "implementation_level": "baseline",                  // ImplementationLevel enum
  "bankable_status": "not_bankable",                   // BankableStatus enum
  "selected_power_kw": 500,                            // flat spec (was chosen_spec.*)
  "selected_energy_kwh": 1000,
  "duration_hours": 2,
  "dispatch_schedule_ref": "s3://gridq-data/site_000123/dispatch_req_a1.parquet",
  "value_streams": [ { "...": "ValueStreamResult, see §3.1" } ],
  "constraints_binding": ["mic_charge_limit"],         // list[str]
  "gated_upside": [ { "...": "ValueStreamResult (gated-upside streams only)" } ],
  "solver_status": "baseline",                         // SolverStatus enum
  "solver_metadata": { "...": "SolverMetadata, see §3.2" },
  "warnings": [],
  "created_at": "2026-06-16T14:05:00Z"
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | |
| `result_id` | `str` | **yes** | – | |
| `request_id` | `str` | **yes** | – | echoes the request |
| `implementation_level` | `ImplementationLevel` | **yes** | – | `baseline \| in_progress \| production` |
| `bankable_status` | `BankableStatus` | **yes** | – | `not_bankable \| review_required \| bankable_candidate` |
| `selected_power_kw` | `float \| None` | no | `None` | `ge=0` |
| `selected_energy_kwh` | `float \| None` | no | `None` | `ge=0` |
| `duration_hours` | `float \| None` | no | `None` | `ge=0` |
| `dispatch_schedule_ref` | `str \| None` | no | `None` | reference to the dispatch series |
| `value_streams` | `list[ValueStreamResult]` | no | `[]` | §3.1 |
| `constraints_binding` | `list[str]` | no | `[]` | string ids of binding constraints |
| `gated_upside` | `list[ValueStreamResult]` | no | `[]` | gated-upside streams only (validated) |
| `solver_status` | `SolverStatus` | **yes** | – | §7; includes `insufficient_data` (C004) |
| `solver_metadata` | `SolverMetadata \| None` | no | `None` | §3.2 |
| `connection_agreement` | `ConnectionAgreementRef \| None` | no | `None` | IC-12 2.0.0; no engine sets it yet; absent from the dump while `None` |
| `evidence_refs` | `tuple[EvidenceLink, ...] \| None` | no | `None` | IC-12 2.0.0; no engine sets it yet; absent from the dump while `None` |
| `warnings` | `list[str]` | no | `[]` | |
| `created_at` | `datetime` | no | `now()` | |

**Validation rules (model validators actually enforced):**
- A **baseline** result cannot be marked bankable: `implementation_level == baseline` **and** `bankable_status == bankable_candidate` is rejected.
- `gated_upside[]` may contain **only** gated-upside streams (`WHOLESALE_P415`, `BALANCING_MECHANISM`, `ANCILLARY_DC_DM_DR` — the `GATED_UPSIDE_STREAMS` frozenset); any other stream there is rejected.

> There is **no** `total_value` / `total_value_gbp_per_yr` field, no `energy_accounting`, no `basis_year`, no `cost_by_stream`, and no `meta`/`exposure`/`tag` in the implemented contract. The priced-vs-gated split is carried by each stream's `firmness` and by membership of `gated_upside[]`. The finance "priced floor" counts firmness ∈ {`firm`, `contracted`}; `conditional` and gated-upside streams are upside, held out of the floor (finance `_is_priced`, on both the revenue and dispatch-cost sides). This matches the value-stack (`FloorStream` ⇒ `firm`, `ContractedStream` ⇒ `contracted`, `GatedUpsideStream` ⇒ at most `conditional`) — resolved in OPEN_COMMENTS C014.

> **Dispatch-energy seam (decision D13, OPEN_COMMENTS C010).** Annual charge/discharge volume is **not** on this contract today; finance reads it out-of-band via the `DispatchEnergy` hook (`finance/cashflows.py`) and the levy-on-charge / price-to-target lines stay *unavailable* until supplied. The **agreed eventual home** is two **additive, Optional** fields — `annual_charge_kwh` and `annual_discharge_kwh` (`float | None`, `default=None`, `ge=0`) — to be landed **with the M3 dispatch producer**, jointly by Olly (producer) and Oli (schema owner), as a deliberate, versioned, conformance-tested event; they are deferred (not added now) because the producer does not yet exist. The `Scenario` multiplier seam (`Scenario.price_multiplier`/`cost_multiplier`) is already first-class in `scenarios/models.py` and needs no change here — finance keeps reading it by duck-typing.

> **Landed (W5-0, decision D23) — sizing evaluation grid for the 3D surface.** The frozen `OptimisationResult` still carries only the **selected** candidate's spec (unchanged — D13 honoured). The full power×duration grid the sizer retains (`SizingRun.candidates_evaluated`) is now exposed **off** this contract: `SizingRun.sizing_grid()` projects it into a serializable `gridq.schemas.sizing.SizingGrid` (each `SizingGridPoint`: `power_kw`, `duration_hours`, `energy_kwh`, `levered_return_pct`, `project_irr_pct`, `min_p90_dscr`, `passes_covenant`, `solver_status`, plus the chosen optimum + run covenant); `MilpEngine.last_sizing_run()` (a duck-typed hook mirroring `selected_dispatch_energy()`) exposes it at the engine seam; and `generate_quote` persists it onto the **additive, Optional `QuoteRecord.sizing_grid`** field (and `QuoteRunResult.sizing_grid`). The field is **excluded from the canonical hash input** (`assembly._canonical_run_input`), so it does **not** change `input_hash`/`quote_id` and `SCHEMA_VERSION` is **not** bumped — existing records/fixtures without it validate unchanged. The 3D sizing surface (power × duration × levered return, P90-DSCR-feasible region highlighted, optimum marked) now plots from this persisted data. See [wave5_scope.md](wave5_scope.md) §4. (The `OptimisationResult` field promotion remains a separate, deferred deliberate seam event — not this change.)

### 3.1 `ValueStreamResult`

The annual revenue/cost and firmness of one market stream for a site. Source: [market.py](../src/gridq/schemas/market.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `stream` | `MarketStream` | **yes** | – | registry id (§7) |
| `annual_revenue_gbp` | `float` | no | `0.0` | `ge=0`; year-1 nameplate |
| `annual_cost_gbp` | `float` | no | `0.0` | `ge=0`; year-1 nameplate |
| `firmness` | `Firmness` | **yes** | – | `firm \| contracted \| conditional \| excluded` |
| `gate_status` | `str \| None` | no | `None` | |
| `unlock_condition` | `str \| None` | no | `None` | what would unlock a gated stream |
| `assumptions_used` | `list[str]` | no | `[]` | |
| `warnings` | `list[str]` | no | `[]` | |
| `consent_status` | `str \| None` | no | `None` | additive (D44); `"given"` when the customer explicitly consented to this stream at intake, else `None`. Orthogonal to `firmness` — the "consent given" label, copied from the gate; it never relaxes the never-FIRM bar |

**Validator:** a stream in `GATED_UPSIDE_STREAMS` cannot have `firmness == firm` (allowed: `contracted`/`conditional`/`excluded`) — unchanged; `consent_status` does not touch this.

### 3.2 `SolverMetadata`

Run metadata stamped onto a result. Source: [optimisation.py](../src/gridq/schemas/optimisation.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `seed` | `int \| None` | no | `None` | |
| `assumption_version` | `str \| None` | no | `None` | |
| `code_version` | `str \| None` | no | `None` | git SHA/build id |
| `model_version` | `str \| None` | no | `None` | e.g. `mock`, `highs-milp-v1.2` |
| `solve_seconds` | `float \| None` | no | `None` | `ge=0` |
| `reproduction_env` | `str \| None` | no | `None` | platform/arch + solver-mode tag |

**Open questions (still open):**
- **Dispatch: reference vs embedded** — v1 stores by reference (`dispatch_schedule_ref`); canonical store ties to the storage decision (D4).
- **`constraints_binding` representation** — string ids vs structured `{constraint, shadow_price}`; the latter is more useful for the real MILP.
- **`solver_status` taxonomy** for the real engine (`optimal` vs time-limited `feasible`) — firmed when the solve-time benchmark runs (Q11).
- **Stream registry governance** — Olly (M2) owns adding `MarketStream` ids; the registry version is part of this contract.

---

## 4. `FinancialCase` — *Oli's underwriting output (M5)*

The bankable result around an `OptimisationResult`. P50/P90 cashflows and DSCR are mandatory; point estimates are not deliverables. Composed of `DscrSummary`, `DebtAssumptions`, `PriceRecommendation` and `CovenantStatus`. Source: [finance.py](../src/gridq/schemas/finance.py).

```jsonc
{
  "schema_version": "0.1.0",
  "case_id": "case_000123_a1",
  "p50_cashflows": [-250000, 48000, 49000],            // GBP; year 0 typically negative
  "p90_cashflows": [-250000, 41000, 42000],            // GBP; same length as p50
  "dscr_summary": { "min_dscr": 1.33, "by_year": [1.45, 1.42], "basis": "P90" },
  "irr": 14.2,                                         // single float (project/unlevered IRR %)
  "equity_irr": 18.6,                                  // levered (equity) IRR % on P50; null when no debt (W4-2, D21/C022)
  "payback_years": 6.4,
  "debt_assumptions": { "gearing_pct": 60, "rate_pct": 7.5, "tenor_years": 8, "sculpting_basis": "P90_DSCR" },
  "price_recommendation": { "structure": "fixed_gbp_per_kwh", "value_gbp_per_kwh": 0.118, "hits_target_return": true },
  "covenant_status": { "covenant": 1.30, "min_p90_dscr": 1.33, "passes": true },
  "warnings": []
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | |
| `case_id` | `str` | **yes** | – | (not `request_id`) |
| `p50_cashflows` | `list[float]` | **yes\*** | `[]` | GBP; **may be negative**; see validator |
| `p90_cashflows` | `list[float]` | **yes\*** | `[]` | GBP; **may be negative**; see validator |
| `dscr_summary` | `DscrSummary` | **yes** | – | §4.1 |
| `irr` | `float \| None` | no | `None` | **project (unlevered)** IRR (%); a single float — equity IRR is the separate `equity_irr` field below |
| `equity_irr` | `float \| None` | no | `None` | levered (equity) IRR (%) on the **P50** base case (the **P90** case governs the DSCR covenant); `None` — never guessed — with no debt, an empty debt schedule, a non-positive equity outlay, or a non-conventional series. **Additive optional field, W4-2 (D21); flagged to Olly in C022; no `SCHEMA_VERSION` bump.** |
| `payback_years` | `float \| None` | no | `None` | `ge=0` |
| `debt_assumptions` | `DebtAssumptions` | **yes** | – | §4.2 |
| `price_recommendation` | `PriceRecommendation` | **yes** | – | §4.3 |
| `covenant_status` | `CovenantStatus` | **yes** | – | §4.4 |
| `lender_terms` | `LenderTerms \| None` | no | `None` | §4.5. The lender terms the DSCR was sized and measured on, as supplied. **Additive optional, WP-UW-03 (D75).** Enters the quote hash when set; popped when `None` (D78) |
| `warnings` | `list[str]` | no | `[]` | |

**Validator:** \*both `p50_cashflows` and `p90_cashflows` must be **non-empty** and the **same length** — a single point estimate (or one series only) is invalid.

> There is **no** `credit` block, `contract_structure` block, `cost_lines_gbp_per_yr`, `contracted_vs_merchant`, or embedded `provenance` in the v1 `FinancialCase`. Credit (rate/tenor) feeds in via `debt_assumptions`; the contracted-vs-merchant split (which depended on a `value_by_stream.exposure` field that no longer exists) is not part of the v1 shape. Equity (levered) IRR **is** modelled — as the flat top-level `equity_irr` scalar above, **not** a nested `irr.equity_pct` (additive optional, W4-2; D21, C022).

### 4.1 `DscrSummary`

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `min_dscr` | `float \| None` | no | `None` | computed on the P90 case unless the lender terms name another |
| `by_year` | `list[float]` | no | `[]` | |
| `basis` | `str` | no | `"P90"` | |
| `status` | `DscrStatus \| None` | no | `None` | why the DSCR is or is not a number: `measured`, `no_debt_terms`, `all_equity`, `tenor_beyond_horizon`, `period_not_modelled`, `no_service_years`. Set on every run since D75 |
| `unavailable_reason` | `str \| None` | no | `None` | the cause in words, set whenever `min_dscr` is `None` (D75) |
| `cashflow_case` | `CashflowCase \| None` | no | `None` | `P50`, `P90` or `LENDER`: the series measured (D77) |

`status`, `unavailable_reason` and `cashflow_case` are additive (WP-UW-03) and derived from inputs the quote
identity already pins, so they are excluded from the canonical hash input (D78). `None` only on records
written before D75.

### 4.2 `DebtAssumptions`

Per-site debt assumptions (placeholder in v1; portfolio finance is Sim's). DSCR maths activates the moment real `DebtTerms` are injected — it is **not** guessed (OPEN_COMMENTS C008, open).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `gearing_pct` | `float \| None` | no | `None` | `ge=0, le=100` |
| `rate_pct` | `float \| None` | no | `None` | `ge=0`; from M9 mapping |
| `tenor_years` | `float \| None` | no | `None` | `ge=0` |
| `sculpting_basis` | `str \| None` | no | `None` | |
| `principal_gbp` | `float \| None` | no | `None` | `ge=0`; the loan actually sized. Additive (D77); excluded from the hash (D78) |
| `sized_by` | `str \| None` | no | `None` | `gearing` or `dscr_target`: which bound set the loan. Additive (D77); excluded from the hash (D78) |

### 4.3 `PriceRecommendation`

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `structure` | `PriceStructure` | no | `fixed_gbp_per_kwh` | `fixed_gbp_per_kwh \| savings_share \| capacity_fee` |
| `value_gbp_per_kwh` | `float \| None` | no | `None` | `ge=0` |
| `hits_target_return` | `bool \| None` | no | `None` | |

### 4.4 `CovenantStatus`

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `covenant` | `float \| None` | no | `None` | the default (event-of-default) DSCR threshold |
| `min_p90_dscr` | `float \| None` | no | `None` | the minimum DSCR on the measured case (name kept for compatibility) |
| `passes` | `bool \| None` | no | `None` | |
| `source` | `str \| None` | no | `None` | `lender_terms` or `assumption_library` (D76) |
| `lock_up_dscr` | `float \| None` | no | `None` | the distribution lock-up threshold, lender terms only (D76) |
| `lock_up_passes` | `bool \| None` | no | `None` | |
| `not_evaluated_reason` | `str \| None` | no | `None` | why `passes` is `None` (D76) |

The four D76 fields are additive and excluded from the hash (D78). With lender terms the thresholds come only from
the terms; terms that state no covenant leave the verdict not evaluated, never tested against the library placeholder.

### 4.5 `LenderTerms`

A lender's debt terms for one site, as stated (WP-UW-03, D75). Frozen; own module
([lender_terms.py](../src/gridq/schemas/lender_terms.py)); exported from `gridq.schemas`. **No field has a default
figure.** Supplied by `gridq run --lender-terms FILE`, the app's lender-terms block, or `generate_quote(lender_terms=)`.

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `provenance` | `LenderTermsProvenance` | **yes** | – | `source` (`term_sheet` \| `lender_indication` \| `synthetic`), `reference` (non-empty), `as_of` (date, optional), `evidence_sha256` (optional) |
| `tenor_years` | `int` | **yes** | – | `gt=0` |
| `interest_rate_pct` | `float` | **yes** | – | all-in, percent, `ge=0`; a value in (0, 1) is refused as a fraction |
| `gearing_pct` | `float` | **yes** | – | maximum debt as percent of capex, `gt=0, le=100`; fraction guard |
| `amortisation` | `AmortisationProfile` | **yes** | – | `annuity` \| `straight_line` \| `sculpted` |
| `cashflow_case` | `CashflowCase` | **yes** | – | `P50` \| `P90` \| `LENDER` |
| `calculation_period` | `CalculationPeriod` | **yes** | – | `annual` \| `semi_annual` \| `quarterly`; only `annual` is measured (D76) |
| `sculpting_target_dscr` | `float \| None` | no | `None` | `gt=0`; `sculpted` only; sizes the loan as min(cap, PV(CFADS ÷ target)) (D77) |
| `covenant` | `DscrCovenantTerms \| None` | no | `None` | `default_dscr` and/or `lock_up_dscr` (`gt=0`); at least one; lock-up ≥ default |

**Open questions (still open):** target-return value & owner (X.1; portfolio finance is Sim's); per-site `debt_assumptions` vs portfolio finance (D-keys gated on C008); IRR/payback definitions (pin in X.1); business-rates treatment (Q10).

---

## 5. `QuoteRecord` — *a quote at a point in its lifecycle*

Binds a run (site + optimisation + finance) into a versioned quote, traceable via input/version hashes, with an append-only lifecycle history. Source: [quote.py](../src/gridq/schemas/quote.py).

```jsonc
{
  "schema_version": "0.1.0",
  "quote_id": "quote_000123",
  "quote_mode": "underwritten",                        // QuoteMode enum (was lifecycle_status)
  "site_id": "site_000123",                            // flat id (was site_ref)
  "input_hash": "sha256:5e7a…",                        // flat (was versions{}/input_hash)
  "assumption_version": "assumptions/2026-06.3",
  "code_version": "git:8a1c…",
  "optimisation_result": { "...": "embedded OptimisationResult (§3), or null" },
  "financial_case": { "...": "embedded FinancialCase (§4), or null" },
  "data_tier": "T2_VALIDATED_12_MONTH_HALF_HOURLY",
  "expiry_date": "2026-07-16",                         // date (for indicative/banded quotes)
  "status_history": [ { "quote_mode": "indicative", "timestamp": "2026-06-12T…", "note": null } ],
  "warnings": [],
  "created_at": "2026-06-16T14:05:00Z"
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | |
| `quote_id` | `str` | **yes** | – | |
| `quote_mode` | `QuoteMode` | **yes** | – | `draft \| indicative \| underwritten \| contracted \| operating \| lapsed` |
| `site_id` | `str` | **yes** | – | |
| `input_hash` | `str \| None` | no | `None` | run-level determinism pin |
| `assumption_version` | `str \| None` | no | `None` | |
| `code_version` | `str \| None` | no | `None` | |
| `optimisation_result` | `OptimisationResult \| None` | no | `None` | embedded (§3) |
| `financial_case` | `FinancialCase \| None` | no | `None` | embedded (§4) |
| `data_tier` | `DataTier` | **yes** | – | enum (§7) |
| `connection_agreement` | `ConnectionAgreementRef \| None` | no | `None` | IC-12 2.0.0: copied from `site.connection.agreement` at assembly; absent from the dump while `None` |
| `evidence_refs` | `tuple[EvidenceLink, ...] \| None` | no | `None` | IC-12 2.0.0: the site's and the result's links, merged at assembly; absent from the dump while `None` |
| `expiry_date` | `date \| None` | no | `None` | |
| `status_history` | `list[StatusHistoryEntry]` | no | `[]` | §5.1 |
| `warnings` | `list[str]` | no | `[]` | |
| `created_at` | `datetime` | no | `now()` | |

**Validator:** an **underwritten** quote (`quote_mode == underwritten`) from a **T0** site (`data_tier == T0_ANNUAL_KWH_TARIFF_POSTCODE`) **must** carry at least one entry in `warnings` (T0 data is insufficient to underwrite firmly); otherwise rejected.

> There is **no** `output_hash`, `reproduction_env`, `optimisation_request_ref`, `status_flags`, `human_minutes`, separate `timestamp`, or `model_version`/`seed` on `QuoteRecord` (run metadata lives on the embedded result's `solver_metadata`; human-minutes live on `TelemetryEvent`). Lifecycle ordering, reprice-on-assumption-change, and "indicative must not cite gated streams" are **process conventions** (architecture §6/§7), not schema validators in v1.

### 5.1 `StatusHistoryEntry`

One prior lifecycle state, for the append-only history trail. Source: [quote.py](../src/gridq/schemas/quote.py).

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `quote_mode` | `QuoteMode` | **yes** | – | |
| `timestamp` | `datetime` | no | `now()` | |
| `note` | `str \| None` | no | `None` | |

---

## 6. `TelemetryEvent` — *the cost-compression dataset (X.3)*

One logged event in a quote's life; append-only. Time-to-quote and human-minutes derive from a quote's events. Ships with the **first** quote, not retrofitted. Source: [telemetry.py](../src/gridq/schemas/telemetry.py).

```jsonc
{
  "schema_version": "0.1.0",
  "event_id": "evt_000123_007",
  "quote_id": "quote_000123",                          // flat id (was quote_ref)
  "event_type": "underwritten",                        // free-form str (see marker note below)
  "data_tier": "T2_VALIDATED_12_MONTH_HALF_HOURLY",    // optional
  "human_minutes": 12,                                 // optional
  "contributor": "Olly",                               // optional (was actor)
  "installer_iterations": 1,                           // optional (was iterations)
  "missing_data_count": 0,                             // optional
  "notes": null,
  "created_at": "2026-06-16T14:05:00Z"                 // (was timestamp)
}
```

| Field | Type | Req? | Default | Notes |
|-------|------|------|---------|-------|
| `schema_version` | `str` | – | `"0.1.0"` | |
| `event_id` | `str` | **yes** | – | |
| `quote_id` | `str` | **yes** | – | |
| `event_type` | `str` | **yes** | – | **free-form string**, not an enum |
| `data_tier` | `DataTier \| None` | no | `None` | recorded on the intake event |
| `human_minutes` | `float \| None` | no | `None` | `ge=0` |
| `contributor` | `str \| None` | no | `None` | person/system that did the work |
| `installer_iterations` | `int \| None` | no | `None` | `ge=0`; back-and-forth count |
| `missing_data_count` | `int \| None` | no | `None` | `ge=0` |
| `notes` | `str \| None` | no | `None` | |
| `created_at` | `datetime` | no | `now()` | |

> **Manual benchmark = an `event_type` string marker, not a field** ([decisions.md](decisions.md) D11, resolves the immediate part of OPEN_COMMENTS C011). A hand-built control-group quote is emitted as `TelemetryEvent(event_type="manual_benchmark")` (the constant `MANUAL_BENCHMARK_EVENT_TYPE` in [telemetry/manual_benchmark.py](../src/gridq/telemetry/manual_benchmark.py)). The earlier draft's boolean `manual_benchmark` flag was **not** added to the frozen `TelemetryEvent` schema. `event_type` is intentionally a free-form string in v1 (no enum constraint), so the marker needs no schema change.

---

## 7. Enums (closed vocabularies)

All from [enums.py](../src/gridq/schemas/enums.py); every one is a `StrEnum` (JSON serialises as the string value; an unknown value is a validation error). Where the member name differs from its string value, the **value** (what appears in JSON) is shown.

| Enum | Values (JSON strings) |
|------|------------------------|
| `DataTier` | `T0_ANNUAL_KWH_TARIFF_POSTCODE`, `T1_PARTIAL_HALF_HOURLY`, `T2_VALIDATED_12_MONTH_HALF_HOURLY` |
| `MarketStream` | `PPA_OR_BILL_SAVINGS`, `TARIFF_ARBITRAGE`, `DUOS_RED_BAND_AVOIDANCE`, `MIC_ASC_HEADROOM`, `CAPACITY_MARKET`, `LOCAL_FLEX_PICLO`, `WHOLESALE_P415`, `BALANCING_MECHANISM`, `ANCILLARY_DC_DM_DR` |
| `Firmness` | `firm`, `contracted`, `conditional`, `excluded` |
| `QuoteMode` | `draft`, `indicative`, `underwritten`, `contracted`, `operating`, `lapsed` |
| `ImplementationLevel` | `baseline`, `in_progress`, `production` |
| `BankableStatus` | `not_bankable`, `review_required`, `bankable_candidate` |
| `SolverStatus` | `baseline`, `mock`, `optimal`, `feasible`, `infeasible`, `insufficient_data` |
| `SolveMode` | `baseline`, `mock`, `production` |
| `ConsentStatus` | `not_started`, `applied`, `approved`, `rejected`, `not_required`, `unknown` |
| `VoltageLevel` | `LV`, `HV`, `EHV` |
| `CreditStatus` | `unknown`, `not_assessed`, `strong`, `adequate`, `weak` |
| `Confidence` | `low`, `medium`, `high` |
| `PriceStructure` | `fixed_gbp_per_kwh`, `savings_share`, `capacity_fee` |

**Lender-terms vocabularies** (WP-UW-03, in [lender_terms.py](../src/gridq/schemas/lender_terms.py), exported from `gridq.schemas`): `CashflowCase` (`P50`, `P90`, `LENDER`); `AmortisationProfile` (`annuity`, `straight_line`, `sculpted`); `CalculationPeriod` (`annual`, `semi_annual`, `quarterly`); `LenderTermsSource` (`term_sheet`, `lender_indication`, `synthetic`); `DscrStatus` (`measured`, `no_debt_terms`, `all_equity`, `tenor_beyond_horizon`, `period_not_modelled`, `no_service_years`).

**Gated-upside register** (`GATED_UPSIDE_STREAMS`, [market.py](../src/gridq/schemas/market.py)): `WHOLESALE_P415`, `BALANCING_MECHANISM`, `ANCILLARY_DC_DM_DR`. These can never be `firm` and are the only streams allowed in `OptimisationResult.gated_upside[]`.

> **`QuoteMode` vs `ManualQuoteType`.** `QuoteMode` (above) is the **lifecycle state** of a system `QuoteRecord`. The separate `ManualQuoteType` (`indicative`/`underwritten`/`proposal`/`other`, in [telemetry/manual_benchmark.py](../src/gridq/telemetry/manual_benchmark.py)) is the **artifact type** of a hand-built benchmark quote — not a lifecycle. They share the `indicative`/`underwritten` spellings but mean different things. See also the glossary.

---

## 8. The reusable `Provenance` / `GridQModel` base

- **`GridQModel`** ([provenance.py](../src/gridq/schemas/provenance.py)) — the base every contract inherits: `model_config = ConfigDict(extra="forbid")` and `schema_version: str = SCHEMA_VERSION` (`"0.1.0"`).
- **`Provenance`** — a reusable traceability block pinning a computation to its inputs and versions: `input_hash`, `assumption_version`, `code_version`, `model_version`, `seed`, `output_hash`, `reproduction_env` (all optional, so it can be attached incrementally). It is **available** but **not currently embedded** in any contract above; today the run-metadata role is filled by `OptimisationResult.solver_metadata` and the flat `QuoteRecord` hash fields.

---

## Change control

> **IC-12 (2026-11-26, D71).** `SiteModel`, `OptimisationResult` and `QuoteRecord` are the contract IC-12, version
> **2.0.0** (`gridq/schemas/chassis.py`), with committed JSON Schemas under `src/gridq/contracts/schemas/ic12_*`.
> `python -m gridq.contracts --check` fails if a model drifts from its schema. The contract version is separate
> from `schema_version` below, which did not move. New load archetypes, study bases, resources and constraints
> are added through `gridq.extensions` (D73), not by editing these models.

These contracts are **versioned** via the shared `schema_version` (`SCHEMA_VERSION = "0.1.0"`). `SiteModel` and `OptimisationResult` are the frozen platform↔engine seams: a change is deliberate, bumps the version, and trips the conformance tests (issue 014). The `MarketStream` registry is part of the `OptimisationResult` contract and versions with it. **The Pydantic models in [`src/gridq/schemas/`](../src/gridq/schemas/) are the source of truth; this document mirrors them.** Any field-level edit lands in code first and this doc is updated in the same change.
