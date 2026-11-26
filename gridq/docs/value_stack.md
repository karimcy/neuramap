# Value stack & market-gating framework (M2)

**Status:** v1 framework (gating + interface) + **real GB value functions (W3-1)** · **Package:** [`src/gridq/value_stack/`](../src/gridq/value_stack/) · **Lane:** `agent/value-stack`

This is the **generic value & constraint stack**: the abstraction that lets the core price
any market's revenue streams without ever knowing which market it is in. The System Brief is
unambiguous about why it matters:

> Every value stream implements one interface, and the core consumes it generically. **The
> rule: if the core ever branches on `market == "GB"`, the abstraction has failed.** DUoS, CM,
> DC/DM/DR and Piclo never leak into the core modules.

The interface implements all six facets the brief names: *value function over a dispatch
schedule · constraints imposed (availability windows, reserved power, held state-of-energy
bands) · price & scenario inputs consumed · firmness & decay · availability gates · deliverability
profile at degraded capability.*

W3-1 implements the **real GB value functions**: each stream now values a supplied dispatch
schedule from numbers in the assumption library (`gb_v0_1`) × dispatched quantities, consuming
the scenario price multiplier, under the firmness the gate decides. The honesty line is held by
**pricing only over a real schedule**: with **no** dispatch schedule (or a closed gate) a stream
still prices nothing (`0.0` + a note) — value over a dispatch you don't have cannot be invented.
The deliverability / tender / portfolio facts the SiteModel cannot carry arrive through a generic,
market-agnostic **`GateContext`** (decision D12), so Capacity Market and local flex can open &
price when the producing lanes supply them. The true half-hourly site-derived valuation still
lands with the dispatch engine (M3) behind this same interface.

---

## 1. The value-stream interface

Every stream — import-side floor, Capacity Market, local flex, the gated wholesale/BM/ancillary
upside — implements one interface ([`interface.py`](../src/gridq/value_stack/interface.py)):

| Member | Returns | Purpose |
|--------|---------|---------|
| `stream_id` | `MarketStream` | The registry id (the frozen stream vocabulary). |
| `display_name` | `str` | Human label for outputs. |
| `required_inputs()` | `list[RequiredInput]` | The inputs the stream needs — feeds the ranked data ask. |
| `check_gates(site_model, assumptions, context=None)` | `GateResult` | Deterministic availability gate for this site (`context` carries generic run/engine facts — §3). |
| `value_function(dispatch_schedule, site_model, assumptions, scenario, context=None)` | `ValueStreamResult` | Value over a dispatch — **prices only over a real schedule, never invents revenue** (see §5). |
| `constraints(site_model, assumptions)` | `list[DispatchConstraintDescriptor]` | The dispatch constraints this stream imposes on the optimiser. |

`ValueStream` is a `@runtime_checkable` `Protocol` (the same pattern as `OptimisationEngine`).
The concrete GB streams subclass generic base classes in
[`streams.py`](../src/gridq/value_stack/streams.py) — `FloorStream`, `ContractedStream`,
`GatedUpsideStream` — which differ only in the firmness an open gate implies.

The per-stream £ result reuses the **existing frozen contract** `ValueStreamResult`
(`gridq.schemas.market`) rather than inventing a parallel shape, so the value stack stays
aligned with the `OptimisationResult` seam M5/M8 already consume.

`dispatch_schedule` (M3's half-hourly dispatch object) and `scenario` (M6's scenario) are kept
**opaque** (typed `Any`) so this interface does not bind another lane's representation. A value
function reads only the *aggregate dispatched quantities* it needs off the schedule — a
**read model**, [`dispatch.py`](../src/gridq/value_stack/dispatch.py): `DispatchScheduleLike`
(a structural Protocol — `power_mw`, `duration_h`, and optional windowed energy / engine-verified
deliverable capacity) and the concrete `DispatchSummary` carrier, normalised by `read_dispatch`.
This duck-types the schedule exactly as the finance lane duck-types `Scenario.price_multiplier`;
it is **not** the frozen `OptimisationResult` (promoting these aggregates onto that seam is M3's
job, decisions D9/D13). `scenario.price_multiplier` is likewise read by duck-typing (default 1.0).

**`GateContext`** ([`interface.py`](../src/gridq/value_stack/interface.py)) carries the three
pre-solve facts the SiteModel cannot (decision D12): `deliverability_confirmed`,
`local_flex_tender_open`, `portfolio_mw`/`portfolio_threshold_mw`. It threads into `check_gates`
and `value_function` as a trailing `context=None` — **additive and backward-compatible**: a
`None` context reproduces the data-ask-only v1 behaviour exactly.

### The stream registry

[`registry.py`](../src/gridq/value_stack/registry.py) provides `StreamRegistry` — an ordered,
id-keyed collection — and `default_registry()`, the v1 composition root that assembles the GB
value stack. The registry is market-agnostic: it holds whatever streams it is built with. This
realises the "versioned stream registry" the `OptimisationResult` contract refers to
([03_data_contracts.md](03_data_contracts.md)). The GB stack registers nine streams:

```
PPA_OR_BILL_SAVINGS · TARIFF_ARBITRAGE · DUOS_RED_BAND_AVOIDANCE · MIC_ASC_HEADROOM
CAPACITY_MARKET · LOCAL_FLEX_PICLO · WHOLESALE_P415 · BALANCING_MECHANISM · ANCILLARY_DC_DM_DR
```

---

## 2. Firm vs conditional vs excluded

A stream's standing in the priced case is summarised by a `GateStatus` and the `Firmness` it
implies. The two are kept consistent by one builder (`gates.build_gate_result`), so a stream can
never report an inconsistent pair:

| `GateStatus` | Meaning | `can_be_priced_now` | `Firmness` |
|--------------|---------|---------------------|------------|
| `open` | Every required input present; in the priced floor. | `True` | `firm` (floor) / `contracted` (contracted streams) |
| `conditional` | Unlockable but a precondition is unmet — listed as conditional upside with its unlock condition. | `False` | `conditional` |
| `closed` | A required input is wholly absent — excluded, not assessable. | `False` | `excluded` |
| `unknown` | Gate undeterminable — treated conservatively (not priced). | `False` | `excluded` |

- **Firm** — the import-side floor a partner is *not* needed for: PPA/bill savings, tariff
  arbitrage, DUoS red-band avoidance, and MIC/ASC headroom (priced only where the connection
  actually binds). Firm when their data is present.
- **Contracted** — Capacity Market and local flex become contracted revenue *when they hold*
  (deliverability verified / tender awarded). In v1 those facts aren't captured, so they sit at
  **conditional**.
- **Conditional** — gated upside (wholesale/P415, BM, ancillary) and any contracted stream whose
  unlock isn't yet satisfied. Carries an `unlock_condition` and the `missing_requirements` that
  block it. **Never priced into the floor; never firm.**
- **Excluded** — required data absent and the stream can't be assessed at all (e.g. no DUoS
  rates, a flat single-rate tariff, no DNO area for a tender).

`firmness` (will this be in the floor?) is **orthogonal** to the priced £ number (what is it
worth?). The gate sets firmness; the value function sets the number. A firm stream still reports
`0.0` when no dispatch schedule is supplied (nothing to value); a **conditional** stream may carry
a *labelled* upside £ (e.g. CM's de-rated value before deliverability is confirmed) but its
firmness keeps it **out of the priced floor** — the finance floor counts only firmness ∈
{`firm`, `contracted`} (C014).

---

## 3. How gates work

`check_gates` is a **pure, deterministic** function of the SiteModel + assumption library: no
randomness, no wall-clock, no invented value. The market-agnostic predicates live in
[`gates.py`](../src/gridq/value_stack/gates.py); the GB wiring lives in
[`gb_streams.py`](../src/gridq/value_stack/gb_streams.py). The GB gating, straight from the
System Brief's "How gating shows up in a single-site quote":

| Stream | Gate | v1 outcome |
|--------|------|------------|
| Tariff arbitrage | usable (multi-rate) import tariff — a flat single rate has no spread | **firm** floor with data; **closed** without |
| DUoS red-band avoidance | DUoS band rate data on the tariff | **firm** floor with data; **closed** without |
| MIC / ASC headroom | a connection limit (MIC/ASC) **and** an observed peak | **firm** (priced where it binds) with both; **closed** without |
| PPA / bill savings | import tariff + load data | **firm** floor with data; **closed** without |
| Capacity Market | de-rated deliverability to the final contract year at degraded capability | **conditional** by default; **contracted** when `context.deliverability_confirmed` |
| Local flexibility (Piclo) | an open tender in the site's DNO area | **conditional** (DNO known) / **excluded** (no DNO); **contracted** when `context.local_flex_tender_open` |
| Wholesale + BM via P415 | route-to-market partner + export consents (G99/G100) | **conditional** upside (gated) |
| Balancing Mechanism | route-to-market partner + minimum aggregated portfolio MW | **conditional** upside (gated) |
| Ancillary DC/DM/DR | route-to-market partner + minimum aggregated portfolio MW | **conditional** upside (gated) |

A few gate mechanics worth noting:

- **"Route-to-market available" is assumption-driven, not a magic flag.** The predicate
  `route_to_market_available` returns `True` only when the relevant gated-upside assumption is
  `confirmed` *and* positive. In the placeholder library (`gb_v0_1`) those values are zero and
  `needs_review`, so the gated streams correctly stay conditional until the RTM partner's real
  numbers replace them (BLOCKERS B002). No partner flag is fabricated anywhere.
- **Single-site runs carry no portfolio aggregate**, so the portfolio-MW gate for BM/ancillary
  is reported as a missing requirement rather than guessed.
- **Gated upside is barred from `firm` at three layers**: the `GatedUpsideStream` refuses an open
  gate, and both `GateResult` and `ValueStreamResult` validators reject a gated stream marked
  firm. It can never slip into the floor by accident.

### Dispatch constraints

Each stream also declares the dispatch constraints its mechanic imposes
(`DispatchConstraintDescriptor`) — e.g. MIC/ASC headroom carries the *connection charge limit*
(charge load counts against MIC/ASC), Capacity Market carries the *deliverability* constraint
(hold de-rated capacity to the final contract year at degraded capability). These are
**descriptors**: they name the constraint and point at the SiteModel field or assumption key that
parameterises it. They carry **no fabricated numbers** — the binding values come from the site
data / assumptions at solve time. The global physics (round-trip losses bought at the price
faced, parasitic load at the host tariff, warranty throughput caps as hard limits) are applied
engine-wide by the dispatch MILP (M3); the per-stream descriptors capture the mechanic-specific
constraints.

---

## 4. How each stream values a dispatch schedule

The value functions live as GB **content** in `gb_streams.py` (`_price` hooks); the
market-agnostic base (`streams.py`) only orchestrates — *no market number lives in the core*.
Given a dispatch schedule and an open/conditional gate, each stream computes £/yr from
assumption-library numbers × dispatched quantities × the scenario price multiplier:

| Stream | Value (numbers from `gb_v0_1`) |
|--------|--------------------------------|
| Tariff arbitrage | `tariff_arbitrage.indicative_value_gbp_per_mw_yr` × `power_mw` |
| DUoS red-band avoidance | `duos.red_band_avoidance_gbp_per_mw_yr` × `power_mw` |
| MIC / ASC headroom | `mic_asc_headroom.value_gbp_per_mw_yr` × `power_mw` (where the connection binds) |
| Capacity Market | `capacity_market.clearing_price_gbp_per_kw_yr` × de-rated **kW**, where de-rated = engine-verified `deliverable_capacity_mw`, else `power_mw` × `de_rating_factor_by_duration_h[duration]` (the **deliverability profile at degraded capability**, with `degradation.annual_capacity_fade_pct` surfaced) |
| Local flex (Piclo) | `local_flex_piclo.utilisation_price_gbp_per_mwh` × `tender_window_discharge_mwh` |
| Wholesale + BM via P415 | `wholesale_p415.gated_upside_value_gbp_per_mw_yr` × `power_mw` (= **£0** placeholder) |
| Balancing Mechanism | `balancing_mechanism.gated_upside_value_gbp_per_mw_yr` × `power_mw` (= **£0**) |
| Ancillary DC/DM/DR | `ancillary_services.dc_dm_dr_value_gbp_per_mw_yr` × `power_mw` × **decay** (`1 − fade%`) (= **£0**) |

- **PPA / bill savings** is the customer-facing aggregate of the import-side floor; it is **not**
  separately priced here (its value is itemised across the tariff-arbitrage, DUoS and MIC/ASC
  streams — summing it would double-count). The PPA £/kWh itself is the finance pricing layer's.
- Every priced number carries a **NOT-bankable warning naming the assumption key** (the `gb_v0_1`
  streams are all `placeholder`/`needs_review`), so a placeholder-derived figure can never read as
  bankable. The gated values are placeholder **zero**, so the gated streams price £0 — the decay
  machinery (ancillary) and de-rating maths are wired and activate unchanged when a confirmed
  positive value lands.

These are **indicative** annual figures (the brief's "placeholder curves, labelled upside"). The
**true site-derived** valuation — the half-hourly integral over real load + tariff spread + Modo
price curves, net of round-trip losses — lands with the dispatch engine (M3) behind this same
interface, supplying a richer `dispatch_schedule`. How real inputs slot in:

1. **Real half-hourly load + tariff** (the site's own data, via M0 ingestion → `SiteModel`) and
   M3's `dispatch_schedule` replace the indicative £/MW/yr with the windowed energy integral —
   a localised change inside `_price`; gating and firmness do not move.
2. **Real wholesale / Modo forecast files** are loaded under `data/` (never committed, never in
   the priced floor — parallel development plan §5; B002). They feed the **gated** streams' value
   *only after* the route-to-market partner is known; until then those streams stay £0 upside.
3. **Promoting a gated stream** (e.g. P415 once an RTM partner exists) happens by **confirming the
   assumption** — a version bump, not a code edit. The gate reads assumption status, so a confirmed
   positive value flips the predicate (and the £) without a core change.

The contract M5/M8 consume (`OptimisationResult.value_streams[]` / `gated_upside[]` + `firmness`)
is unchanged across that swap; only the numbers sharpen.

---

## 5. Why no missing value is guessed

This is the hard line that keeps a quote honest (SKILLS.md; parallel development plan §5):

- **No invented revenue.** A value function prices **only over a supplied dispatch schedule**, and
  only ever as an assumption-library number × a dispatched quantity. With **no** dispatch schedule
  — or a closed/excluded gate — it reports `annual_revenue_gbp = 0.0` (you cannot value dispatch
  you do not have). Every priced figure carries a NOT-bankable warning naming its placeholder key.
  The firmness/gate is real; the money is labelled, never a fabricated figure.
- **No invented data.** A gate that lacks an input returns the appropriate `closed`/`conditional`
  status with a `missing_requirements` line and a warning — never a guessed `True`, an
  interpolated load, an assumed tariff, or a fabricated RTM partner. Missing data becomes a
  **warning + a ranked data ask**, the only two permitted responses.
- **No gated revenue in the floor.** P415/BM/ancillary are structurally barred from `firm` and
  from the priced floor; they are listed as conditional upside with an unlock condition, exactly
  as the brief requires.
- **Placeholders stay labelled.** The gated-upside assumptions are placeholder **zero**; the gate
  only opens a gated stream when a *confirmed* positive value replaces them (a version bump), so
  placeholder-driven output can never masquerade as bankable.
- **Market-agnostic core.** Nothing in `interface.py`, `gates.py` or `streams.py` names a market.
  GB specifics live only in `gb_streams.py` (which streams, which fields/assumptions) and in the
  assumption library (the numbers). A second market adds its own `<market>_streams.py` and
  registry builder — never a `market == "GB"` branch.

---

## 6. Tests

- [`tests/test_value_stack_interface.py`](../tests/test_value_stack_interface.py) — Protocol
  conformance, the registry returning the expected nine streams, the revenue-free value function,
  and the `GateResult`/builder consistency invariants.
- [`tests/test_value_stack_gates.py`](../tests/test_value_stack_gates.py) — the gate logic: P415
  without an RTM partner is conditional; Piclo without tender info is conditional-or-excluded; CM
  without deliverability is not firm; tariff arbitrage / DUoS / MIC-ASC fail their gates without
  data; gated upside can never be firm; missing data produces warnings + missing requirements.
- [`tests/test_value_stack_value_functions.py`](../tests/test_value_stack_value_functions.py) — the
  value functions: each priced number traces to a `gb_v0_1` value × dispatched quantity; no
  schedule / closed gate ⇒ £0; `GateContext` opens & prices CM (deliverability) and Piclo (tender),
  and is additive (`context=None` reproduces prior behaviour); gated streams price £0 and are never
  firm; the scenario price multiplier is applied (duck-typed); outputs are deterministic.

Run: `pytest tests/test_value_stack_interface.py tests/test_value_stack_gates.py tests/test_value_stack_value_functions.py`.

---

## 7. Open items (see `docs/shared_work/OPEN_COMMENTS.md`)

- **Package location.** The framework lives in a dedicated `value_stack/` package (per the build
  task) rather than `optimisation/` as the parallel development plan sketched — flagged for
  reconciliation with the dispatch lane.
- **`GateContext` (decision D12) is now built** — the generic, market-agnostic carrier for the
  three facts the SiteModel cannot hold (CM deliverability, Piclo tender availability, portfolio
  MW). It is an **additive** trailing `context=None` on `ValueStream.check_gates`/`value_function`,
  so a no-context call is unchanged. Its *producers* are still the open question (C007): the sized
  engine M3/M4 (deliverability), a confirmed Piclo source (tender), portfolio runs (portfolio MW).
  Until a caller populates it, CM/Piclo remain conditional exactly as before. Flagged in
  OPEN_COMMENTS for the dispatch lane (the Protocol-signature change touches a shared seam).
- **Dispatch read-model.** `dispatch.py` (`DispatchScheduleLike`/`DispatchSummary`) is the
  *read model* a value function prices over — duck-typed, **not** the frozen `OptimisationResult`.
  Landing these aggregates (`annual_discharge_kwh`, etc.) on the seam is M3's job (D9/D13).
- **Assumption / data sources needing confirmation:** RTM partner / API (B002), Piclo tender
  source + price, CM de-rating + deliverability terms, ancillary forecast source. All keep the
  gated/contracted streams conditional (and the gated ones £0) until confirmed (C007).
