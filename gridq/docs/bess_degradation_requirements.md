# BESS Degradation, Lifetime and Warranty — Modelling Requirements

**Scope:** degradation as it bears on **project economics** for a behind-the-meter lithium-ion BESS
in Great Britain, 0.1–5 MW, 1–4 h duration, LFP or NMC.

**Method:** repo-blind. Every requirement below is derived from published literature, vendor
warranty reporting, or national-lab cost-and-performance assessments. Nothing here was taken from
or validated against any implementation.

**How to read a requirement.** Each item gives: the obligation (MUST / SHOULD / CONSIDER), *what*
the model has to do, *why*, the **error if omitted** with direction and size, and *how* to
implement it — with the governing equation wherever the literature supplies one.

**Verification status.** Sources fetched and read in-session are marked ✅. A small number of
parameter sets are quoted from the standard literature but could not be re-fetched in-session
(paywalled); these are marked ⚠️ **verify before use**. Do not hard-code a ⚠️ value without
opening the source.

---

## Part 1 — Ageing physics and functional form (C-01 … C-07)

### C-01 — MUST decompose fade into an additive calendar term and a cycle term

**What.** Total capacity fade must be computed as two separately-parameterised terms, not one
lumped "% per cycle" or "% per year":

```
Q_loss(t)  =  Q_cal( t, T, SoC_mean )  +  Q_cyc( Q_throughput, DoD, C-rate, T )
SoH(t)     =  1 − Q_loss(t)
```

**Why.** The two mechanisms have different drivers and different functional forms. Calendar ageing
(SEI growth) runs on wall-clock time and is driven by temperature and mean SoC; cycle ageing runs
on charge throughput and is driven by depth, rate and temperature. Every credible semi-empirical
model in the literature is built this way — Schmalstieg et al. (2014) for NMC, Naumann et al.
(2018, 2020) for LFP, Xu et al. (2018) for the grid-optimisation formulation. ✅

**Error if omitted.** A pure cycle model applied to a lightly-cycled asset **understates** fade and
**overstates** life. This is not a small correction: Engels, Claessens & Deconinck (2019), studying
grid-battery investment for German frequency control, conclude that *"calendar ageing drives
battery degradation, whereas cycle ageing has less impact"* for that duty. ✅ A GB BTM asset doing
peak-shaving plus modest arbitrage sits in the same regime — a few hundred EFC/year against a
15–20 year holding period. Conversely a pure calendar model applied to a heavily-cycled merchant
asset **overstates** life and **overstates** NPV.

**How.** Two independent state variables, both accumulated per timestep, summed to give SoH. Do not
let one be a scalar multiple of the other.

---

### C-02 — MUST use a sub-linear time exponent for calendar fade

**What.** Calendar fade is a power law in time with an exponent below 1:

```
Q_cal(t) = α(T, SoC_mean) · t^p        p ≈ 0.75 (NMC, Schmalstieg)   p ≈ 0.5 (LFP, Naumann)
```

The form was verified in-session via Reniers et al. (2018), which reproduces the Schmalstieg
formulation verbatim as ✅

```
E_lost,Wh = ( α(V_mean, T) · T_end^0.75  +  β(V(t), z(t)) · √( ∫|I(t)| dt ) ) · E_Ah
```

**Why.** SEI growth is diffusion-limited: the layer itself is the diffusion barrier, so the rate
falls as the layer thickens. A √t or t^0.75 law is the standard closure.

**Error if omitted (sized).** Fit a **linear** calendar model to year-1 data and project to year 10
and you overstate ten-year calendar fade by:

- t^0.75 law: 10 / 10^0.75 = **1.78×** (78% too much fade)
- t^0.5 law: 10 / 10^0.5 = **3.16×** (216% too much fade)

Calibrate the linear model at year 10 instead and you **understate** early fade by the same
factors — which matters because early fade is what a 5-year warranty and a 3rd/5th-year
augmentation decision turn on.

**How.** Store elapsed time; evaluate `α` from the *time-weighted mean* temperature and SoC over the
interval, then accumulate `Δ Q_cal = α · (t₂^p − t₁^p)`. Accumulating `α·Δt^p` per timestep is
wrong — it does not telescope.

⚠️ Schmalstieg's numeric coefficients for `α(V,T)` and `β(V,z)` are in J. Power Sources 257
(2014) 325–334; the paper is **not** open access (confirmed via Unpaywall in-session) and the
coefficients could not be re-read. Verify before use.

---

### C-03 — MUST use a sub-linear throughput exponent for cycle fade

**What.**

```
Q_cyc = β(DoD, V_mean, T) · Q_throughput^q          q ≈ 0.5  (both Schmalstieg NMC and Naumann LFP)
```

where `Q_throughput` is accumulated charge (Ah or MWh), not cycle count. ✅ (form verified via
Reniers et al. 2018)

**Why.** Same diffusion-limited mechanism as C-02, driven by throughput rather than time.

**Error if omitted (sized).** A model linear in throughput, calibrated at N equivalent full cycles,
**overstates** fade at 4N by **2×** and at 9N by **3×**. For a merchant GB asset the difference
between 1.5 and 4 cycles/day is exactly this regime — a linear model makes aggressive cycling look
~1.6× more damaging than the √ law implies, and will bias the optimiser toward under-trading.

**Caveat — this is the pre-knee law only.** See C-05.

**How.** Accumulate throughput as a state; evaluate `Δ Q_cyc = β · (Q₂^q − Q₁^q)`. Same telescoping
requirement as C-02.

---

### C-04 — MUST model the initial fast-fade phase separately from the long-run law

**What.** Represent the first ~1–3% of capacity loss (roughly the first few hundred EFC / first
6–12 months) with its own faster rate, rather than back-fitting a single power law through it.

**Why.** Formation-phase SEI growth is much faster than steady-state. Fitting one power law across
the whole dataset either over-weights the early points (inflating long-run fade) or under-weights
them (making the asset look better than it will be at first capacity test).

**Error if omitted.** Direction depends on fit region, magnitude ~1–2 percentage points of SoH at
year 1. That is material because the year-1 and year-2 **capacity tests** are what warranty claims
and augmentation triggers are measured against, and a 1–2 pp miss at year 2 can flip an
augmentation decision by several years.

**How.** Two-segment fit, or an offset term `Q_loss = Q₀ + α t^p + β Q^q` with `Q₀` fitted to the
formation phase. State which convention the published curve you are calibrating to already
includes.

---

### C-05 — MUST NOT extrapolate the sub-linear law past the knee; set EOL at or above it

**What.** The √-law is valid only above the "knee". Below roughly 80% SoH, fade accelerates.
Energy-Storage.news' technical review of LFP cells in BESS (Critchley, 6 May 2026) reports that
**capacity fade roughly doubles below 80% SoH**. ✅

**Why.** Below the knee the dominant mechanism switches (lithium inventory exhaustion, plating,
particle cracking) and the diffusion-limited closure no longer holds. O'Kane et al. (2022) show
formally that coupling degradation mechanisms produces qualitatively different end-of-life paths
depending on usage. ✅

**Error if omitted (sized).** Extrapolating √-behaviour from 80% down to 70% SoH **overstates** the
time spent in the 70–80% band by roughly a **factor of 2**, which directly inflates modelled life
and NPV. A 15-year model can easily be a 12-year asset.

**How.** Either (a) set the EOL threshold at 80% and refuse to model below it, or (b) apply an
explicit acceleration factor ≥2 to the fade rate below 80% SoH, and flag any run whose SoH
trajectory enters that band as low-confidence.

---

### C-06 — SHOULD model resistance growth and RTE fade separately from capacity fade

**What.** Round-trip efficiency declines over life independently of capacity. PNNL's grid-scale
testing found AC–AC RTE of **83–87% over 1.5 years of testing**, and **81% for a battery >5 years
old** ✅ (Mongird et al. 2020) — a loss of roughly **2–5 percentage points** of RTE over 5 years.

**Why.** Impedance growth is a separate degradation mode from lithium inventory loss. It hits
arbitrage margin directly and continuously, and it is not captured by SoH at all.

**Error if omitted (sized).** Holding RTE fixed at BoL over a 15-year model **overstates** arbitrage
gross margin. At a 3 pp RTE loss and a £70/MWh average charging price, the additional round-trip
loss is ≈ £2.2/MWh discharged — on a 400 EFC/yr, 2 MWh asset that is ~£1.8k/yr, growing over life.
Small per-MWh, but it compounds against a thin merchant spread and it also **understates**
auxiliary/charging import volume, which for a BTM site is billed at retail (see C-39).

**How.** A second state variable `η_RT(t)`, linear or √-law in time and throughput, floored at the
warranty's guaranteed efficiency if one exists.

---

### C-07 — SHOULD model usable energy separately from SoH (cell imbalance derate)

**What.** Deliverable energy at nameplate power can be below `SoH × E_rated` because of cell-to-cell
imbalance, not chemistry. Energy-Storage.news' April 2026 review of US operational BESS reports
that the practical bottleneck is often that systems cannot *"hit their nameplate discharge because
a handful of cells are out of sync"* ✅ (Bonner, 15 April 2026).

**Why.** SoH is a fleet-average chemistry metric; the pack is limited by its worst cells and by BMS
protection thresholds.

**Error if omitted.** **Overstates** deliverable energy — and therefore overstates every
energy-limited revenue stream — by an amount that grows with age. Treat as a few percent early,
larger late.

**How.** `E_usable(t) = SoH(t) · E_rated · (1 − k_imbalance(t)) · DoD_window`. Keep
`k_imbalance` an explicit, auditable assumption; do not bury it inside SoH.

---

## Part 2 — Stress-factor dependence (C-08 … C-13)

### C-08 — MUST make cycle fade a function of cycle **depth**, not throughput alone

**What.** Life loss per cycle follows a convex stress function of depth. The canonical
grid-optimisation form (Xu et al.; Shi et al.) is a power law: ✅

```
Φ(δ) = k₄ · δ^k₅
```

with the widely-used NMC 18650 parameterisation **Φ(δ) = 5.24 × 10⁻⁴ · δ^2.03** ✅ (verified
in-session in both Xu et al. 2017/2018 and Shi et al. 2017/2018), and an alternative
**Φ(δ) = 4.5 × 10⁻⁴ · δ^1.3** used in Shi et al.'s planning case study. ✅

Cycles to end-of-life at constant depth δ is `N(δ) = 1/Φ(δ)`.

**Self-consistency check (do this in your test suite).** With k₄ = 5.24e-4, k₅ = 2.03:
Φ(0.8) = 3.33e-4 → N = **3,002 cycles at 80% depth** — exactly the "3,000 cycles @ 80% depth"
quoted in the Xu et al. case study. ✅ The parameter set is internally consistent.

**Why.** Depth is the single strongest controllable driver of cycle life, and it is what separates
a shallow frequency-response duty from a deep arbitrage duty.

**Error if omitted (sized).** This is the largest single error in the section. Converting the
stress function to lifetime *throughput* (EFC = N(δ) × δ):

| Cycle depth δ | Φ(δ) | Cycles to EOL, N(δ) | Lifetime EFC = N·δ |
|---|---|---|---|
| 0.05 | 1.20e-6 | 835,141 | **41,757** |
| 0.10 | 4.89e-6 | 204,488 | **20,449** |
| 0.20 | 2.00e-5 | 50,070 | **10,014** |
| 0.50 | 1.28e-4 | 7,794 | **3,897** |
| 0.80 | 3.33e-4 | 3,002 | **2,402** |
| 1.00 | 5.24e-4 | 1,908 | **1,908** |

Shallow cycling at 10% depth delivers **8.5× more lifetime throughput** than cycling at 80% depth.
A throughput-only model — "X MWh of throughput costs Y% of life regardless of depth" — therefore
misprices shallow duty by up to an order of magnitude, and will systematically reject profitable
shallow frequency-response cycling. Preger et al. (2020) confirm the direction empirically: LFP
cells cycled 40–60% SoC reached ~4,000–5,500 EFC to 80%, versus ~2,500–4,000 EFC at 0–100% SoC. ✅

**How.** See C-16 for the MILP-tractable representation.

---

### C-09 — MUST include temperature with the **chemistry-correct sign**

**What.** Temperature dependence is Arrhenius-like *in general* —

```
S_T(T) = exp( k_T · (T − T_ref) · T_ref / T )        T in K, T_ref = 298.15 K
```

— but the sign of the observed net effect over the practical 15–35 °C band is **chemistry
dependent**. Preger et al. (2020), testing commercial A123 LFP and LG Chem NMC 18650s, found
*"capacity fade rate increased with increasing temperature for LFP cells but decreased for NMC
cells"* over 15–35 °C. ✅ Their approximate EFC-to-80% figures: ✅

| | 15 °C | 25 °C | 35 °C |
|---|---|---|---|
| LFP (0–100% SoC, 0.5C/1C) | ~2,500–4,000 | ~2,000–3,500 | ~3,000–5,000 |
| NMC (0–100% SoC, 0.5C/1C) | ~800–1,200 | ~600–1,000 | ~400–800 |

*(read from figures; treat as ranges, not point estimates)*

**Why.** At the cold end, NMC suffers lithium plating during charge, which outweighs the Arrhenius
slowdown of SEI growth. A monotone Arrhenius term gets NMC's low-temperature behaviour backwards.

**Error if omitted (sized).** For a GB site — annual mean ambient ~10 °C, poorly-heated container —
a monotone Arrhenius model applied to NMC predicts **slower** fade in winter when the cells are
actually plating. Direction: **understates** NMC fade. Preger's own numbers put the 15 °C-vs-35 °C
NMC life ratio at roughly **1.5–2×** *in favour of the cold* — so getting the sign wrong is a
factor-of-2 error in the wrong direction, not a rounding issue. For LFP the conventional Arrhenius
sign holds and warm operation is the risk.

**How.** LFP: Arrhenius on the calendar and cycle terms. NMC: either a U-shaped fit with a minimum
near 25–35 °C, or an explicit low-temperature plating penalty on the *charge* branch only (C-13).
Do not share one temperature kernel across chemistries.

---

### C-10 — MUST include mean SoC / dwell-at-high-SoC in the calendar term

**What.**

```
S_σ(σ) = exp( k_σ · (σ − σ_ref) )        σ = mean SoC, σ_ref = 0.5
```

**Why.** Calendar fade rises steeply with storage SoC (higher cathode potential ⇒ faster
electrolyte oxidation and SEI growth). Critchley (2026) states plainly that holding above 80% or
below 20% SoC for extended periods accelerates degradation. ✅

**Error if omitted (sized).** This is a **GB-specific trap**. An asset holding high SoC to stay
ready for a low-frequency response service, or parked full overnight ahead of a morning peak-shave,
accrues calendar fade at a materially higher rate than the 50%-SoC laboratory baseline the vendor
quoted. A model that ignores mean SoC **understates** calendar fade for any readiness-driven duty
and therefore **overstates** the net value of holding a high-SoC standby posture. Because the
optimiser only minimises terms that are explicitly present (Reniers et al. 2018, verbatim
conclusion ✅), omitting this term makes the optimiser *actively choose* the high-SoC posture.

**How.** Accumulate time-weighted mean SoC per interval and feed it to `α` in C-02. If the
optimiser is allowed to choose idle SoC, the term must be inside the objective, not applied
post-hoc — otherwise it changes nothing about dispatch.

---

### C-11 — MUST include C-rate dependence for LFP; MAY omit it for NMC

**What.** Preger et al. (2020), LFP at 25 °C, 0–100% SoC: ✅

| C-rate | EFC to 80% |
|---|---|
| 0.5C | ~3,500 |
| 1C | ~3,000 |
| 2C | ~2,500 |
| 3C | ~2,000 |

For NMC over the same tested range they report **no systematic C-rate dependence**. ✅

**Why.** The 0.5C → 3C span costs LFP roughly **43% of its cycle life**. This is directly
load-bearing for duration selection: a **1-hour LFP system cycling at 1C cannot inherit the cycle
life quoted for a 2-hour system at 0.5C.** Critchley (2026) notes this is precisely why *"a lot of
BESS are run at 0.5C or less."* ✅

**Error if omitted (sized).** Applying a 0.5C cycle-life figure to a 1C duty **overstates** LFP life
by ~15–17%; applying it to a 2C duty overstates by ~40%. In a 1 MW / 1 MWh BTM configuration doing
peak-shaving at full power, this is the difference between clearing and missing the warranty's SoH
guarantee.

**How.** Multiply `β` in C-03 by a rate stress factor calibrated to the table above, keyed on the
*actual* dispatched C-rate per timestep, not on nameplate.

---

### C-12 — SHOULD model cell temperature endogenously, not from ambient

**What.** Cell temperature is ambient plus self-heating minus HVAC, not ambient. Schimpe et al.
(2018) build exactly this coupling for a 192 kWh / 248 kW LFP container and show HVAC power is a
first-order term in the energy balance. ✅

**Why.** C-09's temperature kernel is only as good as its input. Feeding it ambient temperature
ignores I²R self-heating, which is largest exactly when cycling is deepest and fastest.

**Error if omitted.** **Understates** cell temperature during high-throughput periods, therefore
**understates** LFP fade in summer and during aggressive merchant weeks. Also decouples degradation
from HVAC parasitic load (C-38), which is physically the same phenomenon.

**How.** A lumped thermal model: `C_th dT/dt = I²R + P_HVAC_cooling(T) + UA(T_amb − T)`. The
optimiser does not need this inside the MILP — a post-solve thermal pass feeding the degradation
accumulator is adequate for underwriting.

**Practitioner note.** The Enspired CEO's framing is worth quoting because it explains why warranties
are written the way they are: *"when you look at what really needs to be protected it is really
around temperature. All the models try to limit throughput over time but that's really a proxy for
temperature."* ✅ (Energy-Storage.news, 5 Aug 2025)

---

### C-13 — CONSIDER an explicit low-temperature charge-rate limit (lithium plating)

**What.** Cap charge C-rate below ~10 °C cell temperature.

**Why.** Plating is the mechanism behind NMC's inverted temperature response in C-09, and it is
irreversible and safety-relevant.

**Error if omitted.** For a GB unheated or lightly-heated enclosure in winter, **understates**
degradation on the charge branch and — more seriously — models a charging capability the BMS will
refuse to deliver, **overstating** winter revenue from services that require fast charge.

**How.** A temperature-dependent upper bound on the charge variable. Cheap to add, and it is a real
operational constraint, not just a degradation term.

---

## Part 3 — Cycle counting and MILP tractability (C-14 … C-19)

### C-14 — MUST define the equivalent-full-cycle metric unambiguously and match it to the warranty

**What.** State explicitly which of these the model means, and use one consistently:

```
EFC_discharge = Σ_t E_discharge,t / E_rated                      (discharge-only)
EFC_symmetric = Σ_t ( E_charge,t + E_discharge,t ) / (2 · E_rated)   (half-sum)
```

and whether energy is metered **AC** (at the grid meter) or **DC** (at the battery terminals).

**Why.** Warranties count throughput on a specific basis. Supplier throughput warranties are
normally written against **discharged** energy; a model counting charge + discharge without halving
will report double.

**Error if omitted (sized).** Up to a **factor of 2** on reported cycles, and a further **~10–15%**
from the AC-vs-DC choice at typical RTE. A factor-of-2 error in the cycle count against a
1.5-cycle/day warranty cap is a project-killing modelling defect — the model will either report a
breach that isn't there or clear a cap it has actually blown.

**How.** One named constant, one function, unit-tested against a hand-computed square-wave profile.
Record the basis alongside the number everywhere it is reported.

---

### C-15 — MUST rainflow-count the optimised SoC profile ex post and report realised fade

**What.** After the dispatch optimisation resolves, run rainflow cycle counting on the resulting
SoC time series, extract the multiset of cycle depths, and evaluate total life loss:

```
L = Σ_i Φ(v_i)  +  ½ · Σ_j Φ(w_j)
```

where `v_i` are full cycles and `w_j` are the residual half-cycles. ✅ (Shi et al. 2017/2018 —
verified form)

Rainflow itself: reduce the SoC history to its turning points; extract cycles from the extrema
inward; cycle depth is *"the absolute difference between the cycle starting and ending SoC."* ✅

**Why.** Rainflow is the reference method. Whatever surrogate is used inside the optimiser (C-16),
the *reported* degradation must come from the reference method applied to the actual schedule.

**Error if omitted (sized).** Butt & Verba (2026), running a UK residential MILP on the REFIT
dataset with Octopus Agile prices and rainflow post-processing against the Naumann LFP stress
model, found that an energy-cost-only objective incurred hidden degradation costs of **£125.36
against £13.72 of energy cost — 914%** in one configuration, and **up to 1,060%** across the
sensitivity study. ✅ Direction: an energy-only optimiser **understates true cost** and reports
near-grid-neutrality on projects that are in fact loss-making.

**How.** A standard rainflow implementation (ASTM E1049-85 style) over the half-hourly SoC series.
It is O(n) and trivially cheap relative to the MILP; there is no performance excuse for skipping it.

---

### C-16 — SHOULD embed a depth-aware degradation cost in the MILP objective

**What.** Rainflow is non-causal (a cycle's depth is not known until it closes) so it cannot go
into a MILP directly. Use the piecewise-linear SoC-segment construction from Xu et al.: divide the
SoC range into J segments and charge a per-segment marginal cost. ✅ Verified form:

```
c_j  =  ( R / (η_dis · E_rated) ) · J · [ Φ(j/J) − Φ((j−1)/J) ]        j = 1 … J
```

where `R` is the prorated cell/pack replacement cost, `η_dis` the discharge efficiency, and `E_rated`
the rated energy. Energy moved while the SoC sits in segment *j* is charged at `c_j`. Because Φ is
convex, `c_j` increases with *j* and the piecewise-linear cost is LP-representable **without
binaries** — the optimiser will fill cheap (shallow, mid-SoC) segments first of its own accord.

**Why.** Costing depth inside the objective is what actually changes dispatch. A post-hoc penalty
reports the problem; only an in-objective term fixes it.

**Error if omitted (sized).** Reniers et al. (2018) quantify this directly on Belgian day-ahead
arbitrage with a Kokam 16 Ah NMC pack. Revenue-maximising (degradation ignored) versus
profit-maximising (degradation co-optimised), same physics model: ✅

| Model | Objective | Revenue | Degradation cost | **Profit** | Capacity lost |
|---|---|---|---|---|---|
| Bucket (linear) | revenue-max | €75.93 | €49.60 | €26.32 | 2.04% |
| Single-particle | revenue-max | €108.35 | €66.95 | €41.40 | 2.75% |
| Bucket (linear) | profit-max | €59.77 | €20.14 | €39.63 | 0.83% |
| **Single-particle** | **profit-max** | €85.82 | €13.42 | **€72.41** | **0.55%** |

Co-optimising degradation rather than ignoring it cut capacity loss by roughly **80%** while
*raising* profit; the best case beat the naive baseline by **175%**. ✅ The direction is
unambiguous: ignoring degradation in the objective destroys both life and money.

**How.** J = 5–10 segments is normally enough. Calibrate `c_j` from your chosen Φ (C-08) and `R`
(C-34). Report the per-segment costs in the model output so they can be sanity-checked against the
flat equivalent.

---

### C-17 — MUST exploit the convexity of the rainflow cost when choosing the solver formulation

**What.** Shi, Xu, Tan & Zhang prove: **the rainflow cycle-based degradation cost is convex in the
SoC trajectory whenever the stress function Φ is convex.** ✅ (Theorem 1, arXiv:1703.07968; ACC
2018)

**Why.** This is the licence to use a piecewise-linear convex surrogate (C-16) with no integer
variables and still land at a global optimum. Without knowing this result, implementers reach for
binaries to encode cycle detection and pay an enormous branch-and-bound cost.

**Error if omitted.** No accuracy error — a **tractability** error. Teams that miss this either
solve a needlessly large MILP or abandon depth-awareness entirely and fall back to C-08's
throughput-only approximation, importing that requirement's order-of-magnitude mispricing.

**How.** Check Φ is convex (for `Φ = k₄δ^k₅`, convex iff k₅ ≥ 1 — both published parameterisations,
k₅ = 2.03 and k₅ = 1.3, satisfy this ✅). Then keep the degradation cost as a convex
piecewise-linear term and let the LP relaxation do the work. Note the authors themselves solved it
with a subgradient method rather than reformulating; the piecewise-linear route (C-16) is the
MILP-friendly equivalent.

---

### C-18 — SHOULD iterate MILP ↔ rainflow to a fixed point

**What.** Solve the MILP with the current degradation cost; rainflow-count the result (C-15);
compare realised fade against the fade implied by the surrogate; if they differ by more than a
stated tolerance, recalibrate the surrogate and re-solve. Iterate to convergence.

**Why.** The SoC-segment surrogate is an approximation of a non-causal quantity. The fixed point is
where the surrogate's implied cost equals the reference method's realised cost for the schedule the
surrogate actually produced.

**Error if omitted (sized).** Reports a fade number the dispatch does not produce. Set the
acceptance tolerance explicitly — **realised vs modelled fade within 10%** is a reasonable bar; if
the gap is wider, the surrogate is not fit for the duty and the run should be flagged, not silently
reported.

**How.** Two or three iterations normally suffice. Log the convergence path; a non-converging case
is diagnostic information, not a failure to hide.

---

### C-19 — CONSIDER in-MILP real-time cycle counting

**What.** Nebuloni et al. (2025) present a MILP-compatible **logarithmic** degradation model with
real-time cycle-counting computed inside the optimisation. ✅ (IEEE Trans. Ind. Appl. 61(6)
8297–8306; DOI 10.1109/TIA.2025.3571359; green OA at hdl.handle.net/11311/1300195)

**Why.** It removes the surrogate/reference gap that C-18 iterates away.

**Error if omitted.** None, if C-16 + C-18 are implemented. This is an accuracy-vs-solve-time
upgrade, worth evaluating only once the simpler route is in place and its convergence behaviour is
understood.

**How.** Evaluate against the C-18 fixed point on a representative GB week before adopting; the
binary count matters at half-hourly resolution over a multi-year horizon.

---

## Part 4 — Capacity-fade curves and observed rates (C-20 … C-23)

### C-20 — MUST use chemistry-specific cycle-life curves, never a single blended number

**What.** PNNL's 2020 assessment assumes, at 80% DoD: **LFP 2,000 cycles, NMC 1,200 cycles**, with a
**10-year calendar life** for both and a 5% increase in cycle count projected for 2030. ✅ (Mongird
et al. 2020, DOE/PA-0204)

These are deliberately conservative national-lab planning figures. The literature spread is wide —
the same report cites **~8,000 cycles at 80% DoD to a 70% end-of-life capacity** from Wright &
Motloch (2001) ✅ — and modern LFP cells are quoted far higher still (C-23).

**Why.** LFP and NMC differ by a factor approaching 2 in PNNL's own assumptions, and they respond to
temperature in **opposite directions** (C-09). No blended number is defensible.

**Error if omitted.** Using an NMC cycle life for an LFP asset **understates** life by ~40% and
kills otherwise-viable projects; the reverse **overstates** life by ~67%.

**How.** Chemistry is a required, not optional, model input. Refuse to run without it.

---

### C-21 — MUST calibrate against a published EFC-to-EOL-versus-depth curve, not a point

**What.** Cycle life must be a *curve* in depth (C-08's table), anchored to a citable source, with
the anchor point's DoD, C-rate, temperature and EOL threshold recorded alongside it.

**Why.** "3,000 cycles" is meaningless without "at 80% DoD, 25 °C, 0.5C/1C, to 80% SoH". Preger et
al. (2020) is the cleanest open dataset for this because it varies one factor at a time across both
chemistries. ✅

**Error if omitted.** Silent, unbounded. Two teams quoting "3,000 cycles" from different conditions
can differ by 2–3× in lifetime throughput.

**How.** Store cycle life as `(N, δ, C-rate, T, EOL_threshold, source, year)` — never as a bare
integer. This is the single highest-leverage data-hygiene requirement in this document.

---

### C-22 — SHOULD bound modelled annual fade to the observed field envelope

**What.** Two independent 2026 industry reviews converge on the same envelope: a typical grid-scale
battery **loses 20–30% of capacity in its first decade** ✅ (Critchley, 6 May 2026; Bonner, 15 April
2026) — an average of roughly **2–3% per year**, front-loaded.

**Why.** This is the reality check on any bottom-up model. A model producing 0.8%/yr or 5%/yr for a
conventional GB duty is telling you its parameters are wrong.

**Error if omitted.** Bottom-up semi-empirical models are easy to mis-parameterise in either
direction; without an envelope check, the error propagates silently into a 15-year NPV. Bonner
notes the stakes: on merchant assets with aggressive cycling, degradation is *"a financial liability
that can cost tens of millions of dollars of a project's valuation."* ✅

**How.** An automated assertion on the SoH trajectory: cumulative fade at year 10 within 15–35%.
Outside that band, flag the run rather than reporting it.

---

### C-23 — SHOULD treat vendor "zero-degradation" and headline-cycle claims as strictly conditional

**What.** Record the *conditions* attached to any vendor claim, and derate the claim when the
modelled duty exceeds them.

Worked example, verified in-session: CATL's Tener product (6.25 MWh per 20-ft container) is marketed
as **5 years zero degradation**, with **>15,000 cycles** demonstrated in trials. ✅ But the baseline
condition is **one cycle per day**, and CATL's own representative confirmed that at **two cycles per
day the zero-degradation window shortens to 6–7 months** rather than a full year. ✅
(Energy-Storage.news, 5 Nov 2024)

Separately, Intertek CEA notes headline figures that *"exceed 10,000 cycles"* are routinely quoted
against 20–25 year project horizons in a way that, combined with disclaimers, *"can reduce the
guarantee to a marketing claim."* ✅ (Huang, 3 June 2026)

**Why.** The headline number and the contractually-binding number are different quantities.

**Error if omitted (sized).** Taking "5 years zero degradation" at face value for a 2-cycle/day
merchant duty **overstates** the zero-fade period by roughly **2×** and understates ten-year fade
correspondingly.

**How.** Every vendor performance claim stored as `(claim, cycles/day condition, temperature
condition, SoC-window condition, source, date)`. Scale the claim by actual modelled cycles/day.

---

## Part 5 — End of life, augmentation and capex (C-24 … C-28)

### C-24 — MUST define end-of-life explicitly and state what happens there

**What.** Pick and record an EOL SoH threshold, and define the event: augment, repower, or
decommission.

The market has moved. Intertek CEA's June 2026 review reports guaranteed end-of-life SoH thresholds
of **70% in older contracts, 65% in current contracts, and 60% in some cases** ✅ — against ACCURE's
January 2025 report of performance warranties typically guaranteeing **70–80% SoH** at the end of a
**15–20 year** term. ✅ The academic and national-lab convention remains **80%** (Preger et al.
2020; the majority of cycle-life literature) with **70%** used where deeper utilisation is assumed
(Wright & Motloch 2001, via PNNL). ✅

**Why.** EOL threshold is the denominator of the entire lifetime calculation. Moving from 80% to
70% roughly *doubles* the time spent degrading if you (wrongly) extrapolate the √-law — or extends
life by a smaller, knee-limited amount if you correctly apply C-05.

**Error if omitted (sized).** Mixing an 80%-EOL cycle-life figure with a 70%-EOL project assumption
**overstates** life. Conversely, underwriting to 80% when the warranty guarantees 65% leaves
contractual value unclaimed.

**How.** `EOL_SoH` is a named, single-sourced constant. Assert that the cycle-life curve in C-21 was
measured to the *same* threshold; if not, convert explicitly and document the conversion.

---

### C-25 — MUST propagate SoH into every energy-limited revenue stream

**What.** As SoH falls, deliverable energy falls, and so does every revenue stream that is paid on
energy or on sustained duration. Revenue in year *t* must scale with `E_usable(t)` from C-07, not
with nameplate.

**Why.** Otherwise degradation appears in the model as a cost with no revenue consequence — which is
exactly backwards for a BTM asset whose peak-shaving value depends on being able to cover the full
peak window in year 15, not just year 1.

**Error if omitted (sized).** **Overstates** lifetime revenue by roughly the average fade over the
holding period — **10–15%** on a 20–30%/decade trajectory over 15 years, and more for
duration-sensitive streams (below).

**How.** Recompute the energy constraint `E_usable(t)` annually and re-run (or at minimum re-scale)
the dispatch. A single BoL dispatch scaled by a flat availability factor does not capture this.

---

### C-26 — MUST model augmentation or oversizing explicitly, with capex and timing

**What.** Choose and model one of the two strategies the industry actually uses:

1. **Upfront oversizing.** Install **15–20% extra capacity** at COD, which also reduces DoD and
   flattens the fade curve — a double benefit. ✅ (Bonner, 15 April 2026)
2. **Periodic augmentation.** Add capacity mid-life. CATL cites *"typical market needs for
   supplements at the 3rd and 5th years."* ✅ (5 Nov 2024)

**Why.** Neither is free, and the choice materially changes the capex profile and the shape of the
cash flows. Oversizing is capex at t=0 (worst for IRR, best for degradation); augmentation is capex
at t=3/5/7 (better for IRR, and buys cells at a lower future price).

**Error if omitted (sized).** Modelling a flat nameplate for 15 years with no augmentation capex and
no oversizing is **doubly wrong**: it **overstates** deliverable energy late in life (C-25) *and*
**omits** a capex line item worth roughly 15–20% of initial battery capex. On a BTM project with
thin margins this alone can flip the investment decision.

**How.** An explicit augmentation schedule `[(year, MWh_added, £/kWh_at_that_year)]`. Note that
augmentation is normally done at rack or container granularity, not cell-by-cell, so `MWh_added` is
lumpy — model it as such. Also note that mixing new and aged cells on a shared DC bus creates its
own imbalance penalty (C-07).

---

### C-27 — SHOULD price augmentation at forward cell prices, not today's

**What.** Augmentation capex in year *n* must use a projected £/kWh for year *n*.

**Why.** Cell prices have fallen substantially and are projected to continue falling; PNNL's own
2020→2030 projection embeds continued improvement (5% more cycles, RTE 86% → 88%). ✅ Pricing a
year-7 augmentation at year-0 prices **overstates** the cost.

**Error if omitted.** **Overstates** augmentation capex, understating NPV — the conservative
direction, but a real distortion of the oversizing-vs-augmentation choice in C-26, biasing toward
oversizing.

**How.** A price-decline curve as an explicit, sourced assumption with its own sensitivity band. Do
not let a single point estimate drive the strategy choice; run both C-26 strategies and compare.

---

### C-28 — SHOULD model the duration-class cliff as capacity fades

**What.** Where a revenue stream pays on *sustained duration* — GB Capacity Market de-rating by
duration class, and ancillary services with minimum-duration requirements — capacity fade
eventually pushes the asset below its declared duration band. That is a **step change** in revenue,
not a smooth taper.

**Why.** A 2-hour system at 100% SoH is a 2-hour system. At 80% SoH it is a 1.6-hour system, and
whatever duration class it was accredited in no longer describes it.

**Error if omitted (sized).** **Overstates** later-life capacity and ancillary revenue by the full
size of the derating step at the year the asset crosses the boundary. Because it is a cliff rather
than a taper, a smooth-degradation model will miss it entirely.

**How.** Compute effective duration `E_usable(t) / P_rated` each year and re-derate against the
applicable band. Two ways to manage it commercially: declare a lower duration class from the outset,
or size augmentation (C-26) specifically to hold the class boundary — model both.

⚠️ **Citation gap.** The current GB de-rating factors by duration class must be taken from the
live NESO/DESNZ Capacity Market de-rating factor publication for the relevant delivery year. That
document was **not** retrieved in-session; do not hard-code de-rating factors from memory.

---

## Part 6 — Warranties as suppliers actually write them (C-29 … C-33)

### C-29 — MUST implement the warranty operating envelope as hard dispatch constraints

**What.** A supplier warranty is two things bolted together: an **SoH guarantee curve** (a table of
guaranteed retained capacity by year), and an **operating envelope** that the buyer must stay inside
for the guarantee to hold. The envelope, as actually written, constrains:

- **Cycles per day.** ACCURE report that *"most warranty conditions cap daily charge-discharge
  cycles to one or two."* ✅ (Lorscheid & Kairies, 7 Jan 2025). Enspired report the observed spread
  in the field: some assets have warranties permitting **4 cycles/day**, others have *"very
  unsophisticated warranties with 1.5 or 1.6 cycles a day."* ✅ (5 Aug 2025)
- **Annual / cumulative throughput** in MWh.
- **Temperature range**, **C-rate ceiling**, and **SoC dwell limits**.

Encode as:

```
Σ_{t ∈ day d}  E_discharge,t  ≤  n_cycles_max · E_rated          ∀ d
Σ_{t ∈ year y} E_discharge,t  ≤  Throughput_cap_y                 ∀ y
```

**Why.** These are contractual, not advisory. ACCURE are explicit that *"exceeding these conditions,
even due to issues such as sensor errors or control system malfunctions, can void the warranty."* ✅

**Error if omitted (sized).** This is the single most quantified item in this document. Enspired put
the revenue difference between a 1.5–1.6 cycle/day warranty and a 4 cycle/day warranty at
**plus-minus 10 or 20%**. ✅ A model that ignores the cap **overstates** revenue by that margin —
and, worse, produces a dispatch plan that would void the SoH guarantee, converting a contingent
asset (C-32) into zero.

**How.** Hard constraints in the MILP, parameterised from the actual contract. Where the contract is
not yet signed, run the underwriting at **both** ends of the observed 1.5–4 cycles/day range and
report the spread as a risk band rather than picking a midpoint.

---

### C-30 — MUST report the shadow price of the cycle and throughput caps

**What.** Extract and report the dual variable on each warranty constraint in C-29: the £/year the
project would gain from one additional permitted cycle per day.

**Why.** It converts a contractual term into a negotiable number. Given Enspired's observation that
some OEMs will negotiate and others will not, knowing the cap is worth £X/MW/yr tells the developer
exactly how hard to push, or how much to pay for a better warranty, or whether to switch supplier.

**Error if omitted.** No modelling error — a **decision-support** omission. The model silently
accepts a constraint that may be cheap to relax, leaving the quantified 10–20% revenue delta
(C-29) unexamined.

**How.** If the cap is enforced via a binary-free linear constraint, the LP dual is available
directly. Otherwise, re-solve at cap ± 0.5 cycles/day and report the finite difference. Present it
as £/MW/yr per additional daily cycle.

---

### C-31 — MUST match the warranty's throughput metering basis exactly

**What.** Determine, from the contract, whether the throughput cap is measured on discharged energy
or charge+discharge; AC or DC; and gross or net of auxiliary load. Then use *that* definition — not
the model's internal convention — for the constraint in C-29.

**Why.** See C-14. The definitional choices compound: charge+discharge vs discharge-only is 2×; AC
vs DC is a further ~10–15% at typical RTE.

**Error if omitted (sized).** Up to **2.3×** in the worst combination. In one direction the model
reports a warranty breach that does not exist and the optimiser needlessly throttles; in the other
it clears a cap the asset has actually blown, and the SoH guarantee is void at the first claim.

**How.** The warranty metering basis is a required contract field. If it is unknown, the run must
be flagged as unwarrantied rather than defaulted.

---

### C-32 — SHOULD model the SoH guarantee as a contingent asset, not a certainty

**What.** The guarantee has value only if (a) the asset stayed inside the envelope, (b) the capacity
test substantiates the claim, and (c) the counterparty is solvent and the remedy is enforceable.

**Why.** Intertek CEA's assessment is that at a 60% threshold over a 25-year horizon, disclaimers
*"can reduce the guarantee to a marketing claim."* ✅ Extended terms (20–25 years) magnify
counterparty risk.

**Error if omitted.** **Overstates** downside protection. A model that books the guaranteed SoH
floor as a certain outcome has removed the degradation risk it was built to measure.

**How.** Model the guaranteed floor as a recovery in the downside case with an explicit probability
or haircut, not as a hard floor on the SoH trajectory. Keep the modelled (physics) SoH and the
guaranteed (contractual) SoH as two separate series and report both.

---

### C-33 — SHOULD budget capacity-test outages and long-term service agreement cost

**What.** Warranty claims are substantiated by periodic capacity tests, which require taking the
asset offline; and most OEM warranties are conditioned on an LTSA/O&M agreement with the supplier.

**Why.** Both are real, recurring, and routinely omitted. Energy-Storage.news' April 2026 review
emphasises LTSAs as the industry's actual risk-mitigation instrument. ✅

**Error if omitted.** **Overstates** availability (test outages are additional to forced outages,
C-40) and **understates** opex.

**How.** An explicit annual outage allowance for capacity testing, plus LTSA cost as a distinct opex
line. Note that the test outage is schedulable — place it in a low-value period, and let the model
demonstrate the value of doing so.

---

## Part 7 — The marginal cost of a cycle in the objective function (C-34 … C-37)

### C-34 — MUST include a degradation cost term in the dispatch objective

**What.** The objective must be profit, not revenue:

```
max  Σ_t [ p_t · E_dis,t − p_t · E_chg,t ]  −  C_deg( SoC trajectory )
```

**Why.** Reniers et al. state the mechanism in one sentence that should govern the whole design:
the optimiser *"only minimises the terms explicitly present"* — omitting a degradation mechanism
causes *algorithmic blindness* to that failure mode. ✅ Degradation left out of the objective is not
merely unmeasured; it is actively maximised, because cycling is free.

**Error if omitted (sized).** Two independent quantifications, both verified in-session:
- Reniers et al. (2018): co-optimising degradation rather than ignoring it cut capacity loss by
  **~80%** while raising profit; best case **+175%** versus the naive baseline. ✅
- Butt & Verba (2026), UK residential, Octopus Agile: hidden degradation cost reached **914–1,060%
  of energy cost** under an energy-cost-only objective. ✅

**How.** The simple, defensible **levelised** form for a first-pass GB underwriting model:

```
c_deg  =  C_repl / ( N_EFC,EOL · η_dis )                    [£ / MWh discharged, AC]
```

where `C_repl` is replacement cost in £/MWh of rated energy, `N_EFC,EOL` is equivalent full cycles
to the EOL threshold **at the modelled depth and C-rate** (C-21), and `η_dis` is discharge
efficiency.

*Worked GB example:* `C_repl` = £120,000/MWh, `N_EFC,EOL` = 6,000 EFC, `η_dis` = 0.94 →
**c_deg ≈ £21.3/MWh discharged.**

This is the number that belongs in the bid. It is a floor on the spread the asset should accept.

---

### C-35 — MUST use a depth-dependent marginal cost, not a flat £/MWh

**What.** The exact marginal cycle-ageing cost, differentiating the stress function:

```
c_deg(δ)  =  R · Φ'(δ) / η_dis          with   Φ'(δ) = k₄ · k₅ · δ^(k₅ − 1)
```

`R` in £/MWh of rated energy. This is Xu et al.'s marginal aging cost, whose verified form is ✅

```
∂Φ(δ_i)/∂g_t  =  ( 1 / (η_dis · E_rated) ) · dΦ(δ_i)/dδ_i
```

with the piecewise-linear discretisation given in C-16.

**Why.** Φ is convex (k₅ = 2.03), so the marginal cost of the *next* MWh depends on how deep the
current half-cycle already is. A flat adder cannot represent that.

**Error if omitted (sized).** With k₅ = 2.03, the marginal cost at δ = 0.8 is
`(0.8/0.1)^1.03 = 8.55×` the marginal cost at δ = 0.1. A flat `c_deg` calibrated to one deep cycle
per day therefore **overcharges shallow cycling by roughly 8×** — suppressing exactly the
shallow, high-value frequency-response duty that is most profitable per MWh of wear — while
**undercharging** the deep tail. Both errors push the same way: toward a dispatch that is
simultaneously too timid on shallow cycles and too aggressive on deep ones.

**How.** C-16's segment costs `c_j`. If a flat number is unavoidable (e.g. for a headline
sensitivity), state the depth it was calibrated at, and never reuse it at a different depth.

---

### C-36 — MUST express the break-even spread condition including RTE *and* degradation

**What.** The condition for a round trip to be worth doing:

```
p_discharge  ≥  p_charge / η_RT  +  c_deg(δ)
```

so the **required spread** is

```
Δp_min  =  p_charge · ( 1/η_RT − 1 )  +  c_deg(δ)
```

**Why.** Both terms are needed, and BTM projects routinely lose money because only the first is
modelled.

**Error if omitted (sized).** *Worked GB example.* `p_charge` = £70/MWh, `η_RT` = 0.86 (PNNL's
AC–AC figure ✅), `c_deg` = £21.3/MWh (C-34):

```
Δp_min  =  70 × (1/0.86 − 1)  +  21.3  =  11.4  +  21.3  =  £32.7/MWh
```

An efficiency-only model would set the bar at **£11.4/MWh** — **65% too low**. Every trade in the
£11.4–£32.7/MWh band is booked as profitable and is in fact loss-making once wear is paid for. On a
GB day-ahead shape where a large share of intraday spreads sit in exactly that band, this is the
difference between a modelled positive-NPV project and a real negative-NPV one.

**How.** Assert this inequality on every dispatch decision in post-solve validation. If the
optimiser is charging trades below `Δp_min`, either `c_deg` is not actually in the objective
(C-34) or the degradation term is being applied to the wrong variable.

---

### C-37 — SHOULD set the replacement-cost basis consistently with the augmentation strategy

**What.** `R` / `C_repl` in C-34 and C-35 must mean the same thing as the capex in C-26.

- If the strategy is **augmentation**, `C_repl` is the cost of *added cells/racks*, at forward
  prices (C-27) — not full-system replacement.
- If the strategy is **run-to-EOL then decommission**, `C_repl` is the amortised residual value of
  the original system.

For reference, published values span an enormous range and are strongly vintage-dependent: Xu et
al. used **$300,000/MWh (= $300/kWh)** in the ISO-NE case study ✅; Shi et al. used **$0.6/Wh (=
$600/kWh)** ✅; Butt & Verba used **£500/kWh** for UK residential ✅ (citing Uddin et al. 2017).
These are cell/pack costs from 2017–2021 vintages and are far above current grid-scale LFP pricing.

**Why.** `c_deg` scales linearly in `C_repl`. Using a $600/kWh 2017 figure where £100/kWh is
appropriate inflates the degradation adder by **6×**, which will suppress virtually all arbitrage.

**Error if omitted (sized).** Directly proportional. This is the most likely single source of a
wildly wrong `c_deg`, and the published range spans **6×**.

**How.** One sourced, dated `C_repl` constant with a stated basis (cell / module / full system;
delivered / installed) and its own sensitivity band. Never inherit it silently from a paper's case
study.

---

## Part 8 — Auxiliary load and availability (C-38 … C-40)

### C-38 — MUST model auxiliary load as time-based, not proportional to throughput

**What.** HVAC, BMS, controls and PCS standby draw power *whenever the system is energised*, largely
independent of throughput. Model as a near-constant kW plus a temperature-dependent HVAC term —
then derive the throughput share, rather than assuming one.

Schimpe et al. (2018) measured this on a 192 kWh / 248 kW LFP container: ✅

- **Control and monitoring:** 789 W standby / 830 W in operation (battery 287 W, power electronics
  422–463 W, system 81 W).
- **System consumption as a share of total energy input**, by duty:

| Duty | Full equivalent cycles/yr | System consumption (aux) | of which thermal mgmt | Conversion RTE | **Total RTE** |
|---|---|---|---|---|---|
| Primary control reserve | 409 | **10.7%** | 3.7% | 80.2% | **71.6%** |
| PV–battery | 223 | **17.3%** | 5.8% | 72.0% | **59.5%** |
| Secondary control reserve | 18 | **67.5%** | 7.2% | 69.6% | **22.6%** |

**Why.** Because aux is time-based and throughput is not, aux as a share of throughput scales
approximately as **1/EFC**. That is the whole point. A low-utilisation asset can spend most of its
energy on its own life support: at 18 EFC/yr, total round-trip efficiency collapsed to **22.6%** —
**47 percentage points** below the conversion efficiency. ✅

**Error if omitted (sized).** Modelling aux as a fixed percentage of throughput **understates** it
badly for low-utilisation duties and overstates it for high-utilisation ones. For a GB BTM asset in
the 200–400 EFC/yr range, expect aux in the **10–20%** band on this vintage of hardware. Omitting
aux entirely overstates net exported energy by that same 10–20%.

**⚠️ Vintage caveat.** This is a 2015–2017 prototype 192 kWh container. Modern MWh-scale containers
with liquid cooling and consolidated BMS are materially better, and the absolute watts do not scale
up linearly with capacity — the *share* falls substantially at MWh scale. Use Schimpe for the
**structure** (time-based, 1/EFC scaling, thermal coupling) and source the **magnitude** from the
actual product datasheet. Note also that PNNL's headline 86% AC–AC RTE is explicitly defined *net
of auxiliary load consumption* ✅ — check before double-counting.

**How.** `P_aux(t) = P_base + P_HVAC(T_amb(t), T_cell(t))`, integrated over all energised hours
including idle. Couple `P_HVAC` to the thermal model in C-12 — they are the same physics.

---

### C-39 — MUST charge auxiliary and round-trip losses at the correct behind-the-meter tariff

**What.** For a BTM asset, auxiliary consumption and charging losses are **site import**. They are
billed at the site's retail import rate — commodity plus network, policy and levy charges — not at
the wholesale price the arbitrage was valued against.

**Why.** This is the defining economic feature of behind-the-meter and the most common way a BTM
model flatters itself. Wholesale-priced losses can understate the true cost of those losses by a
large multiple once non-commodity charges are included.

**Error if omitted (sized).** Directly proportional to the gap between the site's all-in import rate
and the wholesale reference. Applied to the 10–20% aux share from C-38 plus the ~14% round-trip
loss at 86% RTE, this is a substantial and *permanent* drag — it does not decline with degradation,
it grows relative to a shrinking energy output.

**How.** Two separate price series: a wholesale/market series for the revenue side, and the site's
all-in import tariff for aux and charging losses. Assert they are different; a model in which they
are the same object is misconfigured for BTM.

---

### C-40 — MUST apply availability as an effect on both energy and payment streams

**What.** Model forced and planned unavailability explicitly, and apply it to *all* affected
streams, not as a single revenue haircut.

Verified reference points:
- PNNL assume **5% downtime** in their duration-corresponding-to-cycle-life calculation. ✅
  (Mongird et al. 2020)
- Intertek CEA report that state-of-the-art battery containers achieve **around 98%** availability,
  and quantify the sensitivity: moving from 98% to 97% takes allowed downtime from **~175 hours per
  year to ~263 hours**. ✅ (Huang, 3 June 2026)

**Why.** Availability has three distinct economic effects, and a flat revenue multiplier captures
only the first: (1) lost energy revenue; (2) lost *availability* payments on capacity-based
services, which are paid for being ready rather than for delivering; (3) **penalties** — GB
ancillary and capacity obligations penalise non-delivery, so unavailability during a contracted
window is worse than merely unpaid.

**Error if omitted (sized).** At 95–98% availability the first-order energy effect is **2–5%** of
revenue. But the asymmetry matters more than the magnitude: unavailability is correlated with high
temperature and high utilisation — precisely the high-price hours — so a uniform-random outage model
**understates** the revenue impact. And treating availability as a pure haircut misses the penalty
leg entirely.

**How.** An hourly availability state, with (a) planned outages scheduled into low-value periods
(including the capacity tests from C-33), and (b) forced outages drawn with a hazard rate that
increases with temperature and utilisation. Apply to energy, availability payments and penalty
exposure separately. Distinguish clearly between **availability** (the asset is capable) and
**performance** (the asset delivered to instruction) — GB service contracts test the second.

---

## Constants table

Every value below carries its conditions, its vintage and its source. Values marked ⚠️ could not be
re-fetched in-session and **must be opened at source before use**. Do not lift any row into a model
without carrying its conditions with it (C-21).

### Ageing model form and parameters

| Symbol | Value | Units | Conditions | Vintage | Source |
|---|---|---|---|---|---|
| `p` (calendar time exponent, NMC) | 0.75 | – | Li(NiMnCo)O₂ 18650 | 2014 | Schmalstieg et al. 2014, via Reniers et al. 2018 ✅ |
| `p` (calendar time exponent, LFP) | ~0.5 | – | LFP/graphite commercial cell | 2018 | Naumann et al. 2018 ⚠️ |
| `q` (cycle throughput exponent) | 0.5 | – | both NMC and LFP semi-empirical models | 2014–2020 | Schmalstieg 2014 / Naumann 2020, via Reniers 2018 ✅ |
| `α(V,T)`, `β(V,z)` | — | – | NMC calendar/cycle coefficients | 2014 | Schmalstieg et al. 2014 ⚠️ **not open access** |
| `k₄`, `k₅` (DoD stress, NMC) | 5.24e-4, 2.03 | – | Li(NiMnCo)O₂ 18650, lab tests | 2017–2018 | Xu et al. 2017; Shi et al. 2017 ✅ |
| `k₄`, `k₅` (DoD stress, alt.) | 4.5e-4, 1.3 | – | planning case study | 2017 | Shi et al. 2017 ✅ |
| Naumann LFP stress coeffs `δ`, `c` | 4.0253, 1.0923 | – | LFP/graphite, DoD-dependent | 2020 (used 2026) | Naumann et al. 2020, via Butt & Verba 2026 ✅ |
| `N_nom` (LFP nominal cycle life) | 5,600 | cycles | LFP, stand-alone PV context | 2021 | Dufo-López et al. 2021, via Butt & Verba 2026 ✅ |
| `S_T` form | `exp(k_T·(T−T_ref)·T_ref/T)` | – | T in K, T_ref = 298.15 K | 2018 | Xu et al. 2018 ⚠️ (params) |
| `S_σ` form | `exp(k_σ·(σ−σ_ref))` | – | σ = mean SoC, σ_ref = 0.5 | 2018 | Xu et al. 2018 ⚠️ (params) |

### Cycle life and capacity fade

| Quantity | Value | Units | Conditions | Vintage | Source |
|---|---|---|---|---|---|
| Cycle life, LFP | 2,000 | cycles | 80% DoD | 2020 (proj. 2,100 by 2030) | Mongird et al. 2020 ✅ |
| Cycle life, NMC | 1,200 | cycles | 80% DoD | 2020 (proj. 1,260 by 2030) | Mongird et al. 2020 ✅ |
| Cycle life, Li-ion | ~8,000 | cycles | 80% DoD, to **70%** capacity | 2001 | Wright & Motloch 2001, via Mongird 2020 ✅ |
| Calendar life | 10 | years | 80% DoD; lit. range 5–20 yr | 2020 | Mongird et al. 2020 ✅ |
| EFC to 80%, LFP | ~3,500 / 3,000 / 2,500 / 2,000 | EFC | 0.5C / 1C / 2C / 3C, 25 °C, 0–100% SoC | 2020 | Preger et al. 2020 ✅ |
| EFC to 80%, LFP by SoC window | ~4,000–5,500 / 3,500–5,000 / 2,500–4,000 | EFC | 40–60% / 20–80% / 0–100% SoC, 25 °C | 2020 | Preger et al. 2020 ✅ |
| EFC to 80%, NMC by SoC window | ~1,200–1,800 / 800–1,200 / 400–800 | EFC | 40–60% / 20–80% / 0–100% SoC, 25 °C | 2020 | Preger et al. 2020 ✅ |
| NMC C-rate sensitivity | none observed | – | over tested range | 2020 | Preger et al. 2020 ✅ |
| Temperature sign, LFP | fade **increases** with T | – | 15–35 °C | 2020 | Preger et al. 2020 ✅ |
| Temperature sign, NMC | fade **decreases** with T | – | 15–35 °C | 2020 | Preger et al. 2020 ✅ |
| Observed field fade | 20–30% | % capacity per first decade | grid-scale, typical | 2026 | Critchley 2026; Bonner 2026 ✅ |
| Fade acceleration below knee | ~2× | – | below 80% SoH | 2026 | Critchley 2026 ✅ |
| Vendor claim (LFP, CATL Tener) | 0% fade for 5 yr | – | **at 1 cycle/day**; 6–7 months at 2 cycles/day | 2024 | Energy-Storage.news 2024 ✅ |
| Vendor claim (cycles, demo) | >15,000 | cycles | demonstration projects | 2024 | Energy-Storage.news 2024 ✅ |

### End of life, warranty and availability

| Quantity | Value | Units | Conditions | Vintage | Source |
|---|---|---|---|---|---|
| EOL SoH, academic convention | 80 | % | most cycle-life literature | 2020 | Preger et al. 2020 ✅ |
| EOL SoH, warranty (older) | 70 | % | end of 15–20 yr term | 2025 | Lorscheid & Kairies 2025 ✅ |
| EOL SoH, warranty (current) | 65 | % | some contracts 60% | 2026 | Huang 2026 ✅ |
| Warranty term | 15–25 | years | extending over time | 2025–2026 | Lorscheid & Kairies 2025; Huang 2026 ✅ |
| Warranty cycle cap, typical | 1–2 | cycles/day | most warranties | 2025 | Lorscheid & Kairies 2025 ✅ |
| Warranty cycle cap, observed range | 1.5–4 | cycles/day | field spread across OEMs | 2025 | Enspired via Energy-Storage.news 2025 ✅ |
| **Revenue impact of cycle cap** | **±10–20** | **%** | 1.5–1.6/day vs 4/day | 2025 | Enspired via Energy-Storage.news 2025 ✅ |
| Headline warranty cycles | >10,000 | cycles | marketing figure, heavily disclaimed | 2026 | Huang 2026 ✅ |
| Availability, state-of-the-art | ~98 | % | modern battery containers | 2026 | Huang 2026 ✅ |
| Downtime at 98% / 97% | 175 / 263 | hours/yr | – | 2026 | Huang 2026 ✅ |
| Downtime assumption | 5 | % | national-lab planning basis | 2020 | Mongird et al. 2020 ✅ |
| Upfront oversizing | 15–20 | % extra capacity | degradation mitigation strategy | 2026 | Bonner 2026 ✅ |
| Augmentation timing, typical | 3rd and 5th | year | stated market expectation | 2024 | Energy-Storage.news 2024 ✅ |

### Efficiency and auxiliary load

| Quantity | Value | Units | Conditions | Vintage | Source |
|---|---|---|---|---|---|
| RTE, AC–AC | 86 | % | net of aux load; 88% projected 2030 | 2020 | Mongird et al. 2020 ✅ |
| RTE, measured, new | 83–87 | % | over 1.5 yr of testing | 2020 | Mongird et al. 2020 ✅ |
| RTE, measured, >5 yr old | 81 | % | same test programme | 2020 | Mongird et al. 2020 ✅ |
| Usable DoD, planning | 80 | % | LFP and NMC, all durations | 2020 | Mongird et al. 2020 ✅ |
| SoC window, optimisation | 15–95 | % | case-study assumption | 2017 | Xu et al. 2017 ✅ |
| Aux (control + monitoring) | 789 / 830 | W standby / operation | 192 kWh, 248 kW LFP container | 2015–2017 | Schimpe et al. 2018 ✅ |
| Aux share of energy input | 10.7 / 17.3 / 67.5 | % | at 409 / 223 / 18 EFC per year | 2015–2017 | Schimpe et al. 2018 ✅ |
| Thermal management share | 3.7 / 5.8 / 7.2 | % | same three duties | 2015–2017 | Schimpe et al. 2018 ✅ |
| Conversion RTE | 80.2 / 72.0 / 69.6 | % | PCR / PV-B / SCR | 2015–2017 | Schimpe et al. 2018 ✅ |
| **Total RTE (incl. aux)** | **71.6 / 59.5 / 22.6** | **%** | PCR / PV-B / SCR | 2015–2017 | Schimpe et al. 2018 ✅ |
| Peak conversion efficiency | 87 / 85 | % | at 41% / 100% charge-based utilisation | 2015–2017 | Schimpe et al. 2018 ✅ |
| Peak total efficiency | 81 | % | full nominal power, constant cycling | 2015–2017 | Schimpe et al. 2018 ✅ |
| Inverter efficiency | 98.5 (SiC ~99) | % | component level | 2026 | Huang 2026 ✅ |
| Transformer / cabling | 99 / 99.9 | % | component level | 2026 | Huang 2026 ✅ |

### Cost basis for the degradation term

| Quantity | Value | Units | Conditions | Vintage | Source |
|---|---|---|---|---|---|
| `R` (replacement cost) | 300,000 | $/MWh ($300/kWh) | NMC 18650, ISO-NE case study | 2017 | Xu et al. 2017 ✅ |
| Cell replacement price | 0.6 | $/Wh ($600/kWh) | planning case study | 2017 | Shi et al. 2017 ✅ |
| `C_cell` | 500 | £/kWh | UK residential Li-ion | 2017 (used 2026) | Uddin et al. 2017, via Butt & Verba 2026 ✅ |
| Derived `c_deg` (worked example) | ~21.3 | £/MWh discharged | £120k/MWh, 6,000 EFC, η_dis 0.94 | — | derived, C-34 |
| Derived `Δp_min` (worked example) | ~32.7 | £/MWh | p_chg £70/MWh, η_RT 0.86, c_deg £21.3 | — | derived, C-36 |

### Derived cycle-depth economics (from Φ(δ) = 5.24e-4·δ^2.03)

| δ | Φ(δ) | Cycles to EOL | Lifetime EFC | Relative lifetime throughput |
|---|---|---|---|---|
| 0.05 | 1.20e-6 | 835,141 | 41,757 | 17.4× |
| 0.10 | 4.89e-6 | 204,488 | 20,449 | 8.5× |
| 0.20 | 2.00e-5 | 50,070 | 10,014 | 4.2× |
| 0.50 | 1.28e-4 | 7,794 | 3,897 | 1.6× |
| 0.80 | 3.33e-4 | 3,002 | 2,402 | 1.0× (reference) |
| 1.00 | 5.24e-4 | 1,908 | 1,908 | 0.8× |

*Derived in this document from the verified Xu/Shi parameterisation; the δ = 0.8 row reproduces the
"3,000 cycles at 80% depth" figure stated in Xu et al.'s own case study, confirming internal
consistency.* ✅

---

## Bibliography

**Peer-reviewed literature**

1. Preger, Y., Barkholtz, H. M., Fresquez, A., Campbell, D. L., Juba, B. W., Romàn-Kustas, J.,
   Ferreira, S. R., Chalamala, B. (2020). *Degradation of Commercial Lithium-Ion Cells as a Function
   of Chemistry and Cycling Conditions.* Journal of The Electrochemical Society **167**(12), 120532.
   DOI: 10.1149/1945-7111/abae37 ✅
2. Xu, B., Zhao, J., Zheng, T., Litvinov, E., Kirschen, D. S. (2018). *Factoring the Cycle Aging
   Cost of Batteries Participating in Electricity Markets.* IEEE Transactions on Power Systems
   **33**(2), 2248–2259. DOI: 10.1109/TPWRS.2017.2733339. Preprint: arXiv:1707.04567 —
   https://arxiv.org/abs/1707.04567 ✅
3. Xu, B., Oudalov, A., Ulbig, A., Andersson, G., Kirschen, D. S. (2018). *Modeling of Lithium-Ion
   Battery Degradation for Cell Life Assessment.* IEEE Transactions on Smart Grid **9**(2),
   1131–1140. DOI: 10.1109/TSG.2016.2578950 ⚠️ (closed access; parameters not re-verified)
4. Shi, Y., Xu, B., Tan, Y., Zhang, B. (2018). *A Convex Cycle-based Degradation Model for Battery
   Energy Storage Planning and Operation.* American Control Conference. arXiv:1703.07968 —
   https://arxiv.org/abs/1703.07968 ✅
5. Shi, Y., Xu, B., Tan, Y., Kirschen, D. S., Zhang, B. (2019). *Optimal Battery Control Under Cycle
   Aging Mechanisms in Pay for Performance Settings.* IEEE Transactions on Automatic Control.
   arXiv:1709.05715 — https://arxiv.org/abs/1709.05715 ✅
6. Reniers, J. M., Mulder, G., Ober-Blöbaum, S., Howey, D. A. (2018). *Improving optimal control of
   grid-connected lithium-ion batteries through more accurate battery and degradation modelling.*
   Journal of Power Sources **379**, 91–102. DOI: 10.1016/j.jpowsour.2018.01.004. Open access:
   arXiv:1710.04552 — https://arxiv.org/abs/1710.04552 ✅
7. Schmalstieg, J., Käbitz, S., Ecker, M., Sauer, D. U. (2014). *A holistic aging model for
   Li(NiMnCo)O₂ based 18650 lithium-ion batteries.* Journal of Power Sources **257**, 325–334.
   DOI: 10.1016/j.jpowsour.2014.02.012 ⚠️ **not open access** (confirmed via Unpaywall)
8. Naumann, M., Schimpe, M., Keil, P., Hesse, H. C., Jossen, A. (2018). *Analysis and modeling of
   calendar aging of a commercial LiFePO₄/graphite cell.* Journal of Energy Storage **17**, 153–169.
   DOI: 10.1016/j.est.2018.01.019. Green OA: https://mediatum.ub.tum.de/1435450 ✅ (record verified)
9. Naumann, M., Spingler, F., Jossen, A. (2020). *Analysis and modeling of cycle aging of a
   commercial LiFePO₄/graphite cell.* Journal of Power Sources **451**, 227666.
   DOI: 10.1016/j.jpowsour.2019.227666 ✅ (citation verified)
10. Schimpe, M., Naumann, M., Truong, N., Hesse, H. C., Santhanagopalan, S., Saxon, A., Jossen, A.
    (2018). *Energy efficiency evaluation of a stationary lithium-ion battery container storage
    system via electro-thermal modeling and detailed component analysis.* Applied Energy **210**,
    211–229. DOI: 10.1016/j.apenergy.2017.10.129. Open access:
    https://www.osti.gov/servlets/purl/1409737 ✅ **(full text read in-session)**
11. Collath, N., Tepe, B., Englberger, S., Jossen, A., Hesse, H. (2022). *Aging aware operation of
    lithium-ion battery energy storage systems: A review.* Journal of Energy Storage **55**, 105634.
    DOI: 10.1016/j.est.2022.105634 ✅ (citation verified; full text 403)
12. Collath, N., Cornejo, M., Engwerth, V., Hesse, H., Jossen, A. (2023). *Increasing the lifetime
    profitability of battery energy storage systems through aging aware operation.* Applied Energy
    **348**, 121531. DOI: 10.1016/j.apenergy.2023.121531 (CC-BY) ✅ (citation verified)
13. O'Kane, S. E. J., Ai, W., Madabattula, G., Alonso Alvarez, D., Timms, R., Sulzer, V., Edge,
    J. S., Wu, B., Offer, G. J., Marinescu, M. (2022). *Lithium-ion battery degradation: how to
    model it.* Physical Chemistry Chemical Physics. arXiv:2112.02037 —
    https://arxiv.org/abs/2112.02037 ✅
14. Nebuloni, R., Ilea, V., Berizzi, A., Bovo, C., Meraldi, L., Moretti, L., Raboni, P. (2025).
    *A MILP Battery Degradation Logarithmic Model With Real-Time Cycle Counting Calculation.*
    IEEE Transactions on Industry Applications **61**(6), 8297–8306.
    DOI: 10.1109/TIA.2025.3571359. Green OA: https://hdl.handle.net/11311/1300195 ✅
15. Engels, J., Claessens, B., Deconinck, G. (2019). *Techno-Economic Analysis and Optimal Control
    of Battery Storage for Frequency Control Services, Applied to the German Market.*
    arXiv:1903.04251 — https://arxiv.org/abs/1903.04251 ✅
16. Butt, D., Verba, N. (2026). *Hidden Degradation Costs in Energy-Cost-Only HEMS Optimisation:
    Study on Battery and PV Sensitivity.* arXiv:2606.16051 — https://arxiv.org/abs/2606.16051 ✅
    **(full text read in-session)**
17. Wang, J., Liu, P., Hicks-Garner, J., Sherman, E., Soukiazian, S., Verbrugge, M., Tataria, H.,
    Musser, J., Finamore, P. (2011). *Cycle-life model for graphite-LiFePO₄ cells.* Journal of Power
    Sources **196**(8), 3942–3948. DOI: 10.1016/j.jpowsour.2010.11.134 ⚠️ **not verified
    in-session**
18. Uddin, K., Gough, R., Radcliffe, J., Marco, J., Jennings, P. (2017). *Techno-economic analysis
    of the viability of residential photovoltaic systems using lithium-ion batteries for energy
    storage in the United Kingdom.* Applied Energy **206**, 12–21.
    DOI: 10.1016/j.apenergy.2017.08.170 ✅ (via Butt & Verba 2026)
19. Dufo-López, R., Cortés-Arcos, T., Artal-Sevil, J. S., Bernal-Agustín, J. L. (2021). *Comparison
    of Lead-Acid and Li-Ion Batteries Lifetime Prediction Models in Stand-Alone Photovoltaic
    Systems.* Applied Sciences **11**(3), 1099. DOI: 10.3390/app11031099 ✅ (via Butt & Verba 2026)

**National laboratory and government**

20. Mongird, K., Viswanathan, V., Alam, J., Vartanian, C., Sprenkle, V., Baxter, R. (2020). *2020
    Grid Energy Storage Technology Cost and Performance Assessment.* Pacific Northwest National
    Laboratory / U.S. Department of Energy, Publication DOE/PA-0204, December 2020.
    https://www.pnnl.gov/sites/default/files/media/file/Final%20-%20ESGC%20Cost%20Performance%20Report%2012-11-2020.pdf
    ✅ **(full text read in-session)**
21. Curtis, T. (2024). *Battery Energy Storage System (BESS) End-of-Performance and Decommissioning
    Considerations.* National Renewable Energy Laboratory. DOI: 10.66816/pr4195955. OSTI ID 3377661
    — https://www.osti.gov/biblio/3377661 ✅ (record verified; full text not retrieved)
22. Wright, R. B., Motloch, C. G. (2001). Cycle-life testing, cited in Mongird et al. (2020) for
    ~8,000 cycles at 80% DoD to 70% capacity. ✅ (via PNNL) ⚠️ primary source not retrieved

**Industry and trade press** *(used for warranty structure and field-observed performance, which
the peer-reviewed literature does not cover)*

23. Huang, Y. (Intertek CEA) (2026). *Why BESS performance guarantees are more complex than they
    seem.* Energy-Storage.news, 3 June 2026.
    https://www.energy-storage.news/why-bess-performance-guarantees-are-more-complex-than-they-seem/ ✅
24. Lorscheid, V., Kairies, K.-P. (ACCURE Battery Intelligence) (2025). *Navigating the rigid world
    of energy storage warranties.* Energy-Storage.news, 7 January 2025.
    https://www.energy-storage.news/navigating-the-rigid-world-of-energy-storage-warranties/ ✅
25. Murray, C. (2025). *The role of optimisers in BESS tolls and performance warranties* (interview
    with J. Mayerhofer, CEO, Enspired). Energy-Storage.news, 5 August 2025.
    https://www.energy-storage.news/the-role-of-optimisers-in-bess-tolls-and-performance-warranties/ ✅
26. Murray, C. (2024). *'We can go further than five years': CATL on Tener BESS and its
    'zero-degradation'.* Energy-Storage.news, 5 November 2024.
    https://www.energy-storage.news/we-can-go-further-than-five-years-catl-on-tener-bess-and-its-zero-degradation/ ✅
27. Critchley, L. (2026). *The degradation of LFP cells in BESS.* Energy-Storage.news, 6 May 2026.
    https://www.energy-storage.news/the-degradation-of-lfp-cells-in-bess/ ✅
28. Bonner, A. (2026). *'Unfettered optimism' on US BESS degradation hits wall of operational
    reality.* Energy-Storage.news, 15 April 2026.
    https://www.energy-storage.news/unfettered-optimism-on-us-bess-degradation-hits-wall-of-operational-reality/ ✅

---

## Known gaps in this survey

Stated explicitly so they are not mistaken for completeness:

1. **GB Capacity Market de-rating factors by duration class** (C-28) — not retrieved. Must be taken
   from the live NESO/DESNZ publication for the relevant delivery year.
2. **Schmalstieg (2014) numeric coefficients** — paywalled, confirmed not open access. The
   functional form is verified; the coefficients are not.
3. **Naumann (2018/2020) full parameter sets** — only the two cycle-stress coefficients used by
   Butt & Verba were recovered. The calendar-ageing parameters were not.
4. **Xu et al. (2018) TSG stress-function parameters** (`k_δ1…3`, `k_σ`, `k_T`, `k_t`) — closed
   access; only the functional forms are given here.
5. **Augmentation capex in £/kWh** — no directly citable figure was obtained. Only the strategy
   (15–20% oversizing; 3rd/5th-year supplements) is sourced. NREL ATB and NREL publication servers
   were unreachable during this survey.
6. **GB-specific operational degradation and availability data** (e.g. Modo Energy fleet analysis) —
   not retrieved. The availability figures used are a national-lab planning assumption (5% downtime)
   and an industry consultancy figure (~98%), neither GB fleet-specific.
7. **Schimpe (2018) auxiliary-load magnitudes are 2015–2017 prototype vintage** at 192 kWh scale.
   The structural conclusions transfer; the absolute percentages do not. Source magnitudes from the
   actual product datasheet.
