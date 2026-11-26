# Cooling-flex baseline method, version 1

| | |
|---|---|
| Method id | `cooling_baseline_method` |
| Version | 1 (`v1`) |
| Written | 2026-11-23, revised 2026-11-24 before hashing. Olly (Fable 5.2 method advisor) |
| Roadmap | WP-THERM-04. Spec A4-04, B6-04, TF-05 |
| Measures | IC-11 thermal flex 1.0.0 (`src/gridq/contracts/thermal_flex.py`) |
| Status | Pre-registration document. Hashed and frozen (section 20) |
| Issuer | Neura, self-issued (ADR-D10, option b). Registration is WP-LEDGER-04 |

### Revision note (2026-11-24, before hashing)

The 2026-11-23 draft of this file was never hashed, tagged or published, so this revision is
still version 1. It changes the draft for the four answers the owner gave on 2026-11-23, and
for one fact checked against a primary source. Nothing else in the draft was changed.

| # | Owner's answer, 2026-11-23 (option label) | What changed |
|---|---|---|
| 1 | **"Hash on secondary sources"**: "Freeze v1 now with the limits marked as secondary-sourced. Any correction later becomes v2." | Section 8.2 now has a box that lists the three secondary-sourced limits (CV(RMSE) ≤ 30 %, \|NMBE\| ≤ 10 %, FSU68 ≤ 50 %), the sources each reached this document through, and the rule that a correction is v2. The same marking is in section 12.4 and the parameter register. |
| 2 | **"Recompute only (as written)"**: "Neura pre-registers the method alone, as a self-issued baseline method under D-10. Verifiers recompute figures but do not approve the method." | Section 14.2 and section 17.3 item 1 now state this. No rule changed. |
| 3 | **"Allow a short tolerance"**: "A breach under a set number of minutes does not void the event." | Section 11.5 is rewritten. A breach of the binding band totalling **15 minutes or less** in an event and its recovery window does not void the event. Breach minutes are excluded from credit. Any minute outside the declared ASHRAE envelope or a declared customer SLA band, or any change faster than the declared rate of change, still voids the event with no tolerance. The owner gave no number. The number and the rules are decided by Fable 5.2 under the owner's delegation of 2026-11-23, from the sources in section 19.1. |
| 4 | **"Allow documented adjustments"**: "Permit engineering adjustments for non-routine events, each documented and recomputable. They are faster but easier to contest." | Section 9.3 is revised and section 9.4 is new. Adjustment is allowed only for three pre-declared types with fixed formulas and measured, hashed inputs, capped at 10 % of the baseline and limited to 60 days. Re-baselining on 2,688 eligible intervals (28 days) stays the only route back to an unadjusted baseline. The list, the formulas, the cap and the time limit are decided by Fable 5.2 under the owner's delegation of 2026-11-23. |
| 5 | Not an owner answer: a fact check | Section 10.2 and section 15 said that Gate Closure falls one hour before the Settlement Period was unconfirmed. It is now confirmed from BSC Annex X-1 [S7]. The BL01 rules in section 15 are restated with their section numbers in [S1]. |

To match these changes, the definitions (section 2, "Breach minute"), sections 11.3, 11.8,
12.4, 14.1, 14.2, 16, 17.2 (W6 and W17 revised, W19 and W20 added), 17.3 item 1, 18 and 19
(new sources S7 to S12, a kind and a use for every source, arXiv links for S5 and S6) are
updated. Section 20 now defines how this file's hash is computed (20.1), and the file records
that hash. No other text of the 2026-11-23 draft was changed.

This document fixes, before any trial, how Neura computes what a data-centre cooling plant would
have drawn had a flex event not happened, and so how many kW and kWh an event delivered.

**What existed when this was written.** No telemetry from any UK data-centre site, no trial
record and no ThermaFlex DC data had been seen by the author. The ThermaFlex code and the Cyprus
M&V report format are not in this repository (the product spec says the code is held by GridQ
Ltd, Cyprus). Every number in this document
was therefore chosen without sight of the data it will judge. That is the point of
pre-registration, and it is also weakness W15 in section 17.

**Labels used throughout.**

| Label | Meaning |
|---|---|
| [S] | Taken from a source the author read on 2026-11-23 or 2026-11-24. The source is in section 19. |
| [2ry] | Taken from a secondary source that reports a standard the author could not read. |
| [M] | A method parameter set by this document. It has no external source and claims none. |
| [I] | An inference. It needs confirmation and may be wrong. |
| [U] | Unconfirmed: the author could not check it against a readable source. |

**Not a revenue gate.** The gates in this document decide whether a *measured delivery* may be
stated as verified. They are evidence-quality rules for M&V. They do not open, close or price any
revenue stream in the underwriting engine, and decision D57 is untouched.

---

## 1. Scope and claim

1. **Applies to** one site's cooling plant that publishes an IC-11 `ThermalFlexAvailability`
   surface and receives IC-11 `FlexInstruction` payloads, for the topologies IC-11 names.
2. **Measures** the reduction in the cooling plant's electrical demand during an instructed
   event, the energy not drawn, and the rebound after release.
3. **Approach.** Retrofit isolation with all-parameter measurement (IPMVP Option B, as named in
   [S3] section 4.3): the cooling plant is sub-metered and its counterfactual is a regression on
   independent variables. A whole-site view (the P376 BL01 baseline) is computed beside it and
   compared (section 15).
4. **Does not** set IT-safety limits (IC-11 declares them), size or dispatch anything, compute
   money, or define the import-reduction baseline (WP-LEDGER-04).
5. **Direction.** Reductions only. IC-11 1.0.0 has no turn-up or pre-cool payload, so v1 measures
   none.

## 2. Definitions and time base

| Term | Definition |
|---|---|
| Sample | One reading per quantity per whole UTC minute. Power is the mean over the minute. [M] |
| Interval | A clock-aligned 15-minute UTC period (`:00`, `:15`, `:30`, `:45`). The model works on interval means. [M] |
| Hour | A clock-aligned UTC hour: the mean of its four intervals. The accuracy gates are computed on hours, because the cited thresholds are stated for hourly data. |
| Valid sample | Present and carrying a good quality flag under the telemetry contract (NEW-IC-14). |
| Valid interval | At least 13 of its 15 samples are valid, for every quantity the calculation needs. [M] |
| Valid hour | All four intervals are valid. [M] |
| Local clock | `Europe/London`, as IC-11 `CLOCK`. Used only to find the IC-11 season and band, and to align placebo windows (section 12). All arithmetic is in UTC. |
| Event | One accepted or partially accepted `FlexInstruction`: `[start, end)`, `end = start + duration_min`. A rejected instruction is not an event. |
| Breach minute | A minute in which any IT-safety sensor is outside the binding band (section 11.5). |
| Baseline control | The site's normal control with no flex instruction active, as it ran in the training window. |
| Epoch | One UTC calendar month. One registered model serves one epoch (section 7). |

Rounding of reported figures is in section 11.7. Internal arithmetic is IEEE 754 double precision.

## 3. Measurement boundary

The boundary is the **cooling-plant electrical boundary**: every electrical load whose demand
the flex controller can change, directly or through plant sequencing.

Inside the boundary, each on a meter listed in the site measurement plan (section 5):

- chillers (compressors and their auxiliaries);
- chilled-water pumps, primary and secondary;
- condenser-water pumps, where fitted;
- heat-rejection fans (cooling towers, dry coolers, adiabatic coolers);
- CRAH and CRAC fans and compressors, where the controller's setpoints reach them.

Outside the boundary: IT load, UPS and distribution losses, lighting, the BESS and on-site
generation.

`P` is the sum of the metered loads inside the boundary, in kW. A load inside the boundary that
has no meter makes the boundary incomplete, and the site measurement plan must say so. The
method then measures only the metered part and reports the unmetered loads by name.

**Interactive effects are not measured in v1.** Warmer inlet air can raise server fan power,
which sits in the IT load outside the boundary [I]. Section 11.6 reports a diagnostic, and
section 17 lists this as weakness W5.

## 4. Measured quantities

Every quantity below is needed. WP-LEDGER-04 binds each to a telemetry-contract field.

"Accuracy class" is a requirement this method sets [M]. The class names are those of the standard
named beside them. The author did not read those standards, so no clause is cited and the
edition is left to the certificate of each installed instrument, which the site measurement plan
records [U]. Where an installed instrument is worse than the class required, the plan records its
actual class and section 12.3 uses that figure.

Use: **C** counterfactual input, **D** delivered-quantity input, **G** gate or validity check,
**T** IT-safety check, **R** reconciliation or diagnostic.

### 4.1 Metered quantities

| Id | Quantity | Unit | Measurement point | Resolution | Accuracy class required | Use and reason |
|---|---|---|---|---|---|---|
| Q-01 | Cooling-plant active power, one value per metered feeder | kW | Electrical sub-meter on each feeder inside the boundary (section 3), at the switchboard or motor control centre | 1 min | Meter class 0.5 S (IEC 62053-22) with class 0.5 current transformers (IEC 61869-2) | C, D. Their sum is `P`, the quantity the counterfactual predicts and the event reduces. |
| Q-02 | Cooling-plant active energy, cumulative register, same meters as Q-01 | kWh | Same meters | Register read each minute | As Q-01 | D, G. Recovers the energy across a gap in Q-01 (section 13) and cross-checks the integral of Q-01. |
| Q-03 | IT electrical load | kW | UPS output or the sum of PDU inputs, as the site measurement plan names | 1 min | Meter class 1 (IEC 62053-21) or better | C. Principal driver of cooling load. Also the interactive-effect diagnostic (section 11.6). |
| Q-04 | Chilled-water flow rate | l/s | Flow meter on the common chilled-water return or supply header | 1 min | Thermal energy meter of accuracy class 2 (EN 1434-1) or better, flow sensor and matched temperature pair | C on Path T (section 6). With Q-05 and Q-06 gives cooling load `Q`. |
| Q-05 | Chilled-water supply temperature | °C | Common supply header, the matched pair of Q-04 | 1 min | As Q-04 | C on Path T, R. |
| Q-06 | Chilled-water return temperature | °C | Common return header, the matched pair of Q-04 | 1 min | As Q-04 | C on Path T, R. |
| Q-07 | Site boundary active import and export | kW, and kWh per Settlement Period | The settlement boundary meter, and its minute pulse or EPMS repeat | 30 min settlement values, and 1 min | The settlement meter's own class under the applicable BSC metering Code of Practice. The author did not confirm which Code applies; it is site-specific [U] | R. The BL01 comparison (section 15) and the site-level reconciliation. |
| Q-08 | BESS active power, signed | kW | BESS point-of-connection meter | 1 min | Class 0.5 S (IEC 62053-22) | R. Needed to separate cooling flex from battery discharge at the boundary. |
| Q-09 | On-site generation active power, per unit | kW | Generator output meters | 1 min | Class 1 (IEC 62053-21) or better | R. As Q-08. Not applicable where the site has none; the plan says so. |
| Q-10 | Outdoor dry-bulb temperature | °C | Site weather station in a ventilated radiation shield, sited away from heat-rejection exhaust | 1 min | ±0.3 K [M] | C. Envelope and ventilation gain, and the plant's driving ambient for dry heat rejection. |
| Q-11 | Outdoor relative humidity | % | Same station | 1 min | ±3 % RH [M] | R. Archived so a verifier can check Q-12. |
| Q-12 | Outdoor wet-bulb temperature | °C | Same station, or the BMS outdoor wet-bulb point the plan names | 1 min | ±0.5 K [M] | C where heat rejection is evaporative (section 6.2). See the note below. |
| Q-13 | IT inlet air temperature, every sensor the binding limit is enforced on | °C | At the point IC-11 `limits.binding.point` names (`it_inlet` or `supply_air`) | 1 min | ±0.5 K [M] | T. An event counts only if the binding band held (section 11.5). |
| Q-14 | Supply-air temperature, per air-handling unit | °C | CRAH or CRAC discharge | 1 min | ±0.5 K [M] | T where the binding point is `supply_air`; otherwise R. |
| Q-15 | Supply-air or room dew point, or relative humidity | °C or % | Where the IC-11 humidity limit is measured | 1 min | ±1 K dew point or ±3 % RH [M] | T. Reported against the declared humidity limit. IC-11 1.0.0 states no binding humidity limit, so it does not gate. |

**Note on Q-12.** The method registers no psychrometric formula. The author could not confirm
the coefficients of any wet-bulb formula from a readable source, so none is written here [U].
The archived Q-12 value is the model input as it stands. Q-10 and Q-11 are archived beside it so
a verifier can check it with a psychrometric calculation of their own. This is weakness W13.

### 4.2 BMS and controller quantities

| Id | Quantity | Unit | Source | Resolution | Use and reason |
|---|---|---|---|---|---|
| B-01 | Chilled-water supply temperature setpoint, as commanded | °C | Chiller plant manager | On change, and 1 min | G. Shows the reference window ran on baseline control (section 10). A change to the baseline schedule is a non-routine event (section 9). |
| B-02 | Supply-air temperature setpoint, per unit | °C | CRAH or CRAC controller | On change, and 1 min | G. As B-01. |
| B-03 | Chilled-water differential-pressure setpoint | kPa | Chiller plant manager | On change, and 1 min | G. As B-01. |
| B-04 | Run status, per chiller | on or off | Chiller plant manager | On change, and 1 min | G, R. Staging explains block-level noise; a chiller out of service is a static-factor change. |
| B-05 | Free-cooling or economiser mode status | enum | Chiller plant manager | On change, and 1 min | R. Checks the regime split of section 6.4. |
| B-06 | Pump and fan drive speeds | % or Hz | Drives, through the BMS | 1 min | R. Diagnoses a dispute; not a model input. |
| B-07 | Plant alarm and fault state | enum | BMS | On change | G. Faulted intervals are excluded from training and invalidate a reference window. |
| B-08 | Manual or host override state | boolean | BMS and controller | On change | G. Overridden intervals are excluded; an override during an event is reported. |
| B-09 | Maintenance state, per plant item | boolean | BMS or the host's permit-to-work record | On change | G. Excluded from training. |
| B-10 | Flex controller mode | `baseline`, `event`, `recovery`, `fallback` | Flex controller | On change, and 1 min | G. Defines when baseline control was in force. |
| B-11 | IC-11 `FlexInstruction` and `FlexAcknowledgement`, with their content hashes | record | Dispatch and flex controller | Per event | D. Define the event, the instructed kW and the accepted kW. |
| B-12 | IC-11 `ThermalFlexAvailability` surface the instruction names, by hash | record | Producer | Per surface | D, T. Supplies the declared kW, the declared rebound, the ramp and the binding band. |
| B-13 | Flex controller software version | string | Flex controller | On change | G. A change is a static-factor change (section 9). |
| B-14 | Clock offset of each data source against UTC | s | Edge gateway | 1 min | G. A sample whose source is more than 30 s from UTC is not valid. [M] |
| B-15 | Quality flag for every sample of every quantity above | enum | Telemetry contract | Per sample | G. Defines a valid sample. |

### 4.3 Static records

Held in the site measurement plan, not as time series: meter and sensor identities, calibration
certificates and dates, current-transformer ratios, the chilled-water fluid and its glycol
fraction, the feeder list of section 3, and the static-factor register of section 9.2.

## 5. Site measurement plan

Each site has one **site measurement plan**. It holds only facts about the site and choices this
method makes by rule. It is hashed and registered before the site's first training window opens,
and it is re-issued as a new version when a fact changes.

It contains:

1. the boundary: every feeder inside it, with the meter that measures it, and any unmetered load;
2. for each quantity in section 4, the instrument, its point, its installed accuracy class and
   its certificate;
3. the path, T or E, by the rule in section 6.1;
4. the ambient variable, by the rule in section 6.2;
5. the regime rule, by section 6.4;
6. the backup source for Q-10, Q-11 and Q-12 (section 13.2);
7. the static-factor register (section 9.2), including the baseline setpoint schedule;
8. the fluid properties used for cooling load (section 6.3), with their source;
9. the hash of this method document.

Nothing in the plan may be set after the site's event data has been seen. A plan that changes a
rule of this method is not a plan; it is a new method version (section 20).

## 6. Counterfactual model

### 6.1 Structure and path

The counterfactual is built in two stages, so that load and plant efficiency can be told apart.

- **Stage 1, cooling load.** What heat the plant would have had to remove.
- **Stage 2, plant power.** What electrical power the plant would have drawn to remove it.

**Path T** (thermal metered) uses both stages. **Path E** (electrical only) skips stage 1.

Rule: a site is on Path T when Q-04, Q-05 and Q-06 are installed and listed in its plan.
Otherwise it is on Path E. The path is fixed in the plan and does not change within a plan
version.

### 6.2 Independent variables

| Symbol | Variable | From |
|---|---|---|
| `L` | IT electrical load, kW | Q-03 |
| `D` | Outdoor dry-bulb temperature, °C | Q-10 |
| `T` | Driving ambient temperature, °C | Q-12 where the IC-11 `topology` includes `water_cooled_chillers_with_towers`, or the plan records evaporative or adiabatic heat rejection. Q-10 otherwise. |

These are the only regression drivers. They are chosen because an event does not move them.

**Setpoints are deliberately not regression drivers.** An event works by moving setpoints, so a
setpoint measured during an event is not a counterfactual input. Setpoints enter the method in
two other ways: the baseline setpoint schedule is a static factor whose change is a non-routine
event (section 9.2), and the commanded setpoints prove that a reference window ran on baseline
control (section 10). The regression therefore learns the plant's power under the baseline
schedule as it was in the training window.

**No lagged or autoregressive terms.** A lag of `P` or `Q` measured during or just before an
event carries the event into its own counterfactual. The model is static in `L`, `D` and `T`.

If IT workload is shifted at the same time as a cooling event, `L` falls and the counterfactual
falls with it. The cooling reduction caused by lower IT load is therefore not counted as cooling
flex. That is intended.

### 6.3 Stage 1: cooling load (Path T)

Measured cooling load on interval `i`, in kW thermal:

```
Q_i = rho * c_p * V_i * (T_return_i - T_supply_i)
```

`V` is Q-04 in m³/s, the temperatures are Q-06 and Q-05, and `rho * c_p` is the volumetric heat
capacity of the chilled-water fluid in kJ/(m³·K). The plan records that value and its source for
the site's fluid and glycol fraction. This document fixes no number for it [U].

Counterfactual cooling load:

```
Q_hat_i = a0 + a1 * L_i + a2 * D_i
```

fitted by ordinary least squares on the eligible training intervals (section 7.3).

`Q_hat - Q` during an event is the heat the room and the chilled-water loop absorbed. It is
reported as a diagnostic (section 11.6). It is not a deliverable.

### 6.4 Stage 2: plant power

Three candidate forms, in order of complexity. `x` is the load term: measured `Q` when fitting on
Path T, `Q_hat` when predicting on Path T, and `L` on Path E.

```
C1:  P_hat = b0 + b1*x + b2*T
C2:  P_hat = b0 + b1*x + b2*T + b3*x*T
C3:  P_hat = b0 + b1*x + b2*T + b3*x*T + b4*x^2
```

Each is fitted by ordinary least squares on the eligible training intervals. `b3` lets the power
per unit of load rise with ambient, which is how lift affects a chiller. `b4` lets efficiency
vary with part load. Both are standard physical effects [I]; the candidate set is this
document's choice [M].

**Regimes.** If the IC-11 `topology` includes `free_cooling`, stage 1 and stage 2 are each
fitted separately on two regimes, `T < T*` and `T >= T*`. Otherwise there is one regime.
`T*` is found by grid search over 0.0 °C to 25.0 °C in steps of 0.5 °C [M]. A grid value is
admissible only if each regime holds at least 672 eligible intervals [M]. The admissible value
with the lowest end-to-end sum of squared errors in `P` on the training intervals is taken; a
tie takes the lower `T*`. If no value is admissible, one regime is used.

**Bounds.** The prediction is clipped: `P_hat = max(0, P_hat)`, and where the IC-11 surface
declares `cooling_electrical_capacity_kw`, `P_hat` is also capped at that value.

**End to end.** Every gate in section 8 is computed on the final `P_hat`, that is on
`P_hat(Q_hat(L, D), T)` for Path T, never on a stage in isolation.

## 7. Fitting

### 7.1 Epochs and freezing

One model serves one UTC calendar month. It is fitted, hashed and registered **before** that
month begins, so the coefficients that judge an event exist before the event does.

| Step | Rule |
|---|---|
| Training cutoff | 72 hours before the epoch starts. [M] |
| Training window | The 365 days ending at the training cutoff, or from the plan's first valid day, or from the latest re-baselining time (section 9.3), whichever is latest. [M] |
| Registration | The model card (section 14.1) is hashed and registered before the epoch starts. |
| Late card | A card registered after the epoch starts serves only events whose instruction was issued after the registration time. Earlier events in that epoch have no registered model and are unverified. |
| No refit | A model is never refitted for an epoch after that epoch starts. |

### 7.2 Minimum data

A model may be fitted only on at least **2,688 eligible intervals**, the equivalent of 28 days
[M]. The figure follows spec TF-06, which holds a site in read-only for at least four weeks
before any control.

A short window is allowed. What stops it over-reaching is the coverage rule in section 8.4: an
event whose conditions the training window never saw is not verified.

### 7.3 Eligible intervals and exclusions

An interval is eligible for training when all of these hold. There is no other exclusion.

1. It is a valid interval for `P`, `L`, `D`, `T` and, on Path T, `Q`.
2. It is outside every **contaminated span**. An event's contaminated span runs from `t0` to
   `end + R`, where `t0` is the earlier of `start` and the first minute after `issued_at` at
   which B-10 left `baseline`, and `R` is the recovery window of section 11.4.
3. B-10 is `baseline` throughout.
4. B-07 shows no fault, B-08 no override and B-09 no maintenance on a plant item inside the
   boundary.
5. It is after the latest re-baselining time (section 9.3).

**No residual-based outlier removal.** An interval is never dropped because the model fits it
badly. Every exclusion above is decided by a recorded flag or a recorded event, not by the
result.

### 7.4 Cross-validation folds

Folds are fixed by the calendar. Let `n` be the number of days from 1970-01-01 to the interval's
UTC date. The block is `floor(n / 7)` and the fold is `block mod 5`. Five folds, each made of
whole 7-day blocks [M]. Blocks are used because adjacent intervals are autocorrelated.

The **out-of-fold prediction** of an interval comes from the model fitted on the other four
folds, with the same form and the same `T*` as the full model.

### 7.5 Model selection

Selection is a fixed procedure with no judgement in it.

1. Fit stage 1 (Path T), and C1, C2 and C3, on all eligible intervals.
2. A candidate is **admissible** when `a1 > 0` (Path T), and the mean over the training
   intervals of the derivative of `P_hat` with respect to `x` is above zero. A plant that draws
   less power for more load is a fitting artefact [M].
3. For each admissible candidate compute the out-of-fold hourly CV(RMSE) (section 8.1).
4. Start with C1. Replace it with C2 only if C2 is admissible and lowers that figure by at least
   0.5 percentage points. Then replace the current choice with C3 on the same test [M].
5. If C1 is inadmissible, take the first admissible candidate in the order C2, C3. If none is
   admissible, no model is registered for the epoch.

## 8. Accuracy gates

A baseline may be used only when its gates pass. A failed gate never stops a record being made:
the event is computed where it can be and labelled unverified with the reason (section 11.8).

### 8.1 Statistics

Computed on valid hours, on `P_hat` before any same-day adjustment. `y` is measured `P`, `y_hat`
the prediction, `n` the number of hours, `p` the number of fitted coefficients across both
stages and regimes, and `y_bar` the mean of `y`.

```
CV(RMSE) = 100 * sqrt( sum( (y - y_hat)^2 ) / (n - p) ) / y_bar
NMBE     = 100 * sum( y_hat - y ) / sum( y )
```

For out-of-fold statistics the divisor is `n`, not `n - p`, because the predicted hours were not
fitted. A positive NMBE means the model over-predicts, which would overstate a reduction.

The CV(RMSE) form with `n - p` is as given in [S4] section 3.1. The NMBE form is the mean bias
error of [S3] equation 4-1. [S3] equations 4-2 to 4-4 divide by the number of intervals rather
than `n - p`; this method takes the stricter divisor.

### 8.2 Thresholds

| Gate | Test | Threshold | Basis |
|---|---|---|---|
| G1 | Eligible intervals in the training window | at least 2,688 | [M], section 7.2 |
| G2 | In-sample hourly CV(RMSE) and NMBE | CV(RMSE) ≤ 30 %, \|NMBE\| ≤ 10 % | [2ry], see below |
| G3 | Out-of-fold hourly CV(RMSE) and NMBE | the same | [M] applies the G2 values out of sample |
| G4 | Out-of-fold hourly NMBE in each IC-11 (season, band) cell | \|NMBE\| ≤ 10 %, on at least 24 valid hours in the cell | [M] applies the G2 value per cell |

G1 to G3 are model gates: if any fails, no model is registered for the epoch. G4 is a cell gate:
a cell that fails it, or holds fewer than 24 valid hours, has **no baseline** in that epoch, and
an event starting in it is unverified.

**Where the thresholds come from, stated exactly.** The 30 % and ±10 % hourly values are in
[S3] Table 4-2, "Acceptable Calibration Tolerances", which attributes them to "ASHRAE Guideline
14-2015, Section 5.3.3.3.10". Three cautions:

1. The author did not read ASHRAE Guideline 14. The clause number is [S3]'s and is unconfirmed
   [U].
2. [S3] writes "14-2015" in that footnote and "14-2014" elsewhere. The published edition is
   ASHRAE Guideline 14-2014. The footnote is taken to be a misprint [I].
3. [S3] gives these values as tolerances for a **calibrated simulation** (its Option D). This
   method applies them to a regression baseline. Whether Guideline 14 sets different values for
   regression baselines the author could not check [U].

The values are loose. A model at 30 % CV(RMSE) cannot verify a 10 % reduction. They are the
floor below which no baseline exists. What actually decides whether an event is verified is the
uncertainty gate G6 in section 12.4.

> **Secondary-sourced limits (owner, 2026-11-23, "Hash on secondary sources").** Three limits
> in this method come from secondary sources, because the standards they are attributed to,
> ASHRAE Guideline 14-2014 and IPMVP Core Concepts (EVO 10000-1:2022), are paid documents the
> author has not read:
>
> | Limit | Value | Where used | Reached this document through | Attributed by that source to |
> |---|---|---|---|---|
> | Hourly CV(RMSE) | ≤ 30 % | G2, G3 | [S3] Table 4-2 | ASHRAE Guideline 14, "Section 5.3.3.3.10" |
> | Hourly NMBE | ≤ 10 % in magnitude | G2, G3, G4, drift rule (9.3), adjustment cap (9.4) | [S3] Table 4-2 | ASHRAE Guideline 14, "Section 5.3.3.3.10" |
> | Fractional savings uncertainty | FSU68 ≤ 50 % | G6 (12.4) | [S5] section 4.6.2 | ASHRAE Guideline 14, and IPMVP for the "twice the standard error" form |
>
> The coverage rule for IT load in section 8.4 (0.9 × minimum to 1.1 × maximum) is secondary-
> sourced in the same way, through [S5].
>
> v1 is frozen with these values as written. If a reading of the standards later shows a value,
> clause or scope above to be wrong, the correction is made in **v2** (section 20), never in this
> file, and every v1 figure stays a v1 figure.

### 8.3 Per-cell gate and the IC-11 evidence floor

IC-11 says a cell without evidence of the surface's class is `unknown`. G4 is the method's side
of that rule: a cell with no baseline cannot yield a verified event, so it cannot gain trialled
or metered evidence in that epoch.

### 8.4 Coverage gate (G5), per event

Every interval of the event and of its reference window must have drivers inside the range the
training window covered:

| Driver | Allowed range | Basis |
|---|---|---|
| `L` | 0.9 × training minimum to 1.1 × training maximum | [2ry]: [S5] section 4.6.2 reports this as the ASHRAE Guideline 14 rule for independent variables |
| `D`, `T` | training minimum − 1.0 K to training maximum + 1.0 K | [M]. A percentage of a Celsius value depends on the unit's zero, so the cited rule is not applied to temperatures |

An event with any interval outside the range is unverified, reason `out_of_coverage`.

## 9. Adjustments

### 9.1 Routine adjustments

Routine adjustments account for expected variation in independent variables ([S3] glossary).
In this method they are made only by:

1. the model's response to `L`, `D` and `T`; and
2. the same-day adjustment of section 10.

There is no other routine adjustment. In particular there is no manual adjustment, no degree-day
normalisation and no adjustment for occupancy or day of week.

### 9.2 Static factors

The site measurement plan registers these. A change to any of them is a **non-routine event**:

- the number, type, capacity and sequencing of chillers, pumps and heat-rejection units;
- any plant item inside the boundary taken out of service for more than 7 days [M];
- the baseline setpoint schedule (B-01, B-02, B-03) and the baseline control sequence;
- the flex controller software version (B-13), where the release changes baseline control;
- air-management changes: containment, floor layout, added or removed air-handling units;
- commissioned IT hall capacity;
- any meter, current transformer or sensor of section 4 replaced, moved or recalibrated;
- the chilled-water fluid.

### 9.3 Non-routine events

A non-routine event is **declared** when the host or Neura records a static-factor change, with
its time.

A non-routine event is **detected** by this drift rule, run each UTC day of an epoch on the
epoch's model with no same-day adjustment:

- the day's NMBE is computed over its eligible hours, and the day qualifies if it has at least
  12 [M];
- if \|NMBE\| exceeds 10 % on 5 consecutive qualifying days [M], a non-routine event is detected
  at the start of the first of those days.

**Every non-routine event starts a re-baseline.** Re-baselining is the only route back to an
unadjusted baseline. A documented adjustment (section 9.4) may bridge the gap for some declared
events. It never replaces the re-baseline.

- The re-baselining time is the time of the declared change, or the start of the first drift
  day.
- From that time the registered model is suspended. Events are unverified, reason
  `nre_rebaselining`, unless a documented adjustment under section 9.4 applies to them. This
  includes events inside the five drift days, which are re-labelled once the run completes. The
  relabelling is by this pre-registered rule and is recorded as an appended record.
- A new model needs 2,688 eligible intervals after the re-baselining time and registration under
  section 7.1. A card registered mid-epoch for this purpose is treated as a late card. Once it is
  registered, every documented adjustment for that non-routine event ends.

A temporary condition that B-07, B-08 or B-09 flags is not a non-routine event. Its intervals are
excluded and the static factors return to their registered values.

The cost is stated plainly: a non-routine event that section 9.4 cannot adjust removes verified
cooling flex for at least 28 days.

### 9.4 Documented adjustments for non-routine events

Owner's answer of 2026-11-23, "Allow documented adjustments". The owner chose to permit
engineering adjustments, each documented and recomputable, and accepted that they are easier to
contest. The design below is decided by Fable 5.2 under the owner's delegation of 2026-11-23. Its
aim is to leave nothing to contest except the measurements themselves: no adjustment rests on a
person's estimate.

**9.4.1 Closed list.** Only these three types exist. Any other non-routine event, and every
**detected** non-routine event (the drift rule of 9.3, whose cause is not known), can only be
re-baselined.

Notation: `P_hat` is the registered model's prediction (section 6) and `X` the design of that
model, meaning its chosen form, its `T*` and its list of eligible training intervals.
`fit_X(y)` is an ordinary least-squares fit of the series `y` on design `X`, giving a prediction.
Because least squares is linear in `y`, `fit_X(P) = fit_X(P - p_f) + fit_X(p_f)` exactly, before
clipping. NRA-1 rests on that identity.

| Type | Non-routine event | Measured inputs | Formula |
|---|---|---|---|
| **NRA-1** Boundary transfer of a separately metered load | A feeder leaves the boundary (decommissioned, or moved to a switchboard outside it), or a load that was metered outside the boundary joins it. The feeder must have its own Q-01 meter with valid data on every interval of the registered model's eligible-interval list. Otherwise the event is not NRA-1 | The feeder's Q-01 series over the registered training window, the time of transfer, and the sign `s` (−1 leaves, +1 joins) | `p_hat_f = fit_X(p_f)` on the same design. `P_hat' = max(0, P_hat + s * p_hat_f)`, then capped as in section 6.4 |
| **NRA-2** Replacement of a Q-01 or Q-02 meter or current transformer, with a parallel run | A plant-power meter or CT on feeder `f` is replaced, and the old and new instruments both measured the feeder at the same time beforehand | Both meters' minute series over the parallel run | `k = sum(P_new) / sum(P_old)` over the parallel run's valid intervals. `P_hat' = max(0, P_hat + (k - 1) * p_hat_f)`, with `p_hat_f` as in NRA-1 |
| **NRA-3** Replacement of a driver instrument (Q-03, Q-10 or Q-12), with a parallel run | A driver sensor or meter is replaced or moved, and the old and new instruments ran in parallel beforehand | Both instruments' minute series over the parallel run | Temperature: `delta = mean(T_new - T_old)`; the model is driven by `T_new - delta`. IT load: `k_L = sum(L_new) / sum(L_old)`; the model is driven by `L_new / k_L` |

**9.4.2 Conditions on the inputs** [M]:

1. A parallel run (NRA-2, NRA-3) has at least **672** intervals on which both instruments are
   valid, within the 30 days before the replacement.
2. Its ratio or offset is stable. The figure is computed separately on the first and second
   halves of the parallel run, in time order. The two halves differ by no more than 0.5 % for a
   ratio, or 0.2 K for an offset. A run that fails this test cannot be used.
3. Every input series, the record of the change and its time are taken from the input archive
   (section 14.1) and named in the adjustment record by hash. A value that is not in the archive
   cannot be an input.

**9.4.3 Size cap.** The size of an adjustment is

```
S = mean over the registered eligible training intervals of | P_hat' - P_hat |
    divided by the mean over the same intervals of P_hat
```

The cap is **S ≤ 10 %**, counting all adjustments active at once together (computed on the
combined `P_hat'`). It equals the NMBE limit: an adjustment larger than the bias the gates
tolerate is a new plant, not a corrected one [M]. Above the cap no adjustment is made, and
re-baselining is the only remedy.

**9.4.4 Gates.** For NRA-1, G2 to G4 are recomputed on the adjusted target `P + s * p_f` with
the adjusted prediction, which is exact by linearity. A failed gate means no adjustment. For
NRA-2 and NRA-3 the training data were measured by the old instrument, so the gates of the
registered model stand, and the cap and the stability test bound the change. Under NRA-3 the
coverage gate (8.4) is tested on the converted drivers.

**9.4.5 Registration and time limit.**

- An adjustment record is hashed and registered before it is used. It applies only to events
  whose instruction was issued after that registration. Events between the re-baselining time
  and the registration are unverified, reason `nre_rebaselining`.
- An adjustment ends at the earliest of: the registration of the re-baselined model (9.3);
  **60 days** after the re-baselining time [M]; a drift detection under 9.3 on the adjusted
  model; or a further non-routine event that is not itself adjusted. After it ends, events are
  unverified, reason `nre_rebaselining`, until the re-baselined model is registered.
- The 60 days allow for the 28 days of eligible data that re-baselining needs, losses to events
  and exclusions, and registration. They are not an extension of the re-baseline. Re-baselining
  starts at the re-baselining time, whether or not an adjustment is made.

**9.4.6 Uncertainty of an adjusted event.** For NRA-1 the placebo set of section 12.2 is
recomputed on the adjusted target and the adjusted out-of-fold predictions; no further term is
added. For NRA-2 and NRA-3 an adjustment term is added in quadrature to `U68_mean` and
`U68_block`:

```
u_adj = | mean over W of ( P_hat'_first - P_hat'_second ) |
```

where `P_hat'_first` and `P_hat'_second` use the ratio or offset from the first and second
halves of the parallel run. The full half-to-half difference is used, not half of it [M].

**9.4.7 Reporting and labelling.**

- Every adjustment is its own record (section 14.1). It holds its type, the non-routine event,
  every input hash, `k` or `delta`, `S`, the gate results and its start and end.
- An adjusted event is reported with its figures computed on both `P_hat'` and `P_hat`, side by
  side, and carries the flag `nre_adjusted` with the adjustment's id. Its credit is computed on
  `P_hat'`.
- Monthly totals report adjusted events as a separate line, never merged with unadjusted ones.
- Every adjustment record, and every figure computed on `P_hat'`, carries the label
  "self-issued by Neura (ADR-D10)". Consumers that require third-party evidence refuse them,
  as they refuse the rest of this method's records.
- A verifier recomputes an adjustment from the archive alone (section 14.2). A recomputation that
  does not agree withdraws the adjustment, and the affected events revert to unverified, reason
  `nre_rebaselining`.

## 10. Event-day baseline and same-day adjustment

### 10.1 Unadjusted event baseline

For each interval of the event and of its recovery window, `P_hat` is the registered model's
prediction from that interval's measured `L`, `D` and `T`.

### 10.2 Same-day adjustment

The adjustment moves the level of the baseline to the plant's state on the day. It is additive,
as the BL01 In Day Adjustment is ([S1] section 2.2).

**Reference window.** Let `s` be the event `start` rounded down to a 15-minute boundary. The
reference window is the twelve intervals in `[s - 240 min, s - 60 min)`: three hours, ending one
hour before the event [M]. BL01 uses a three-hour window ending at Gate Closure for the first
Settlement Period with an Acceptance ([S1] section 3.4.3). BSC Annex X-1 defines Gate Closure,
in relation to a Settlement Period, as "the spot time one hour before the spot time at the start
of that Settlement Period" [S7]. The BL01 window is therefore the three hours ending one hour
before the dispatched Settlement Period starts, and this window has the same position relative
to the event's start.

**Adjustment.**

```
A_raw = mean over the valid reference intervals of ( P - P_hat )
cap   = 0.10 * mean over the same intervals of P_hat
A     = min( cap, max( -cap, A_raw ) )
P_adj = max( 0, P_hat + A )        for every interval of the event and its recovery window
```

The cap is 10 % [M]. It equals the NMBE gate: the model is allowed to be biased by up to 10 %,
so an adjustment larger than that is correcting something the gates did not accept. If
\|A_raw\| exceeds twice the cap, the event carries the flag `baseline_suspect`.

**Validity.** The adjustment is used only if all of these hold. Otherwise `A = 0` and the
reason is recorded.

1. At least 8 of the 12 reference intervals are valid [M].
2. B-10 is `baseline` in every minute from the window's start to the event's `start`.
3. B-01, B-02 and B-03 equal the registered baseline schedule over the same minutes. Where
   baseline control sets a setpoint dynamically, the plan registers its bounds and the test is
   that the setpoint stayed inside them. This is the guard against pre-cooling the plant to
   inflate the baseline.
4. No minute from the window's start to the event's `start` lies in another event's
   contaminated span, or carries a B-07, B-08 or B-09 flag.

**Second event on a day.** If condition 4 fails only because of an earlier event on the same
local day, the earlier event's `A` is reused, provided that one was valid. BL01 does the same
([S1] section 3.4.3).

The adjustment and its cap are symmetric. They raise or lower the baseline by the same rule.

## 11. Delivered kW and kWh against the declared IC-11 kW

### 11.1 Reference quantities from IC-11

| Symbol | Value |
|---|---|
| `K_decl` | The surface's declared kW for the event: `query_at(surface, start, duration_min).deliverable_kw`, on the surface the instruction names by hash |
| `K_ref` | The kW the plant agreed to: `reduction_kw` if the acknowledgement is `accepted`, `accepted_kw` if `partially_accepted` |
| `ramp` | `response.ramp_min` of the surface; 0 if `response` is unknown |
| declared rebound | The `rebound` of the curve point that supplied `K_decl` |

A declared kW is firm (owner, 2026-11-23): deliverable for its duration at any start in the cell,
within the binding IT-safety band. The method therefore tests the **lowest** sustained
reduction, not the average.

### 11.2 Per-minute reduction

For each minute `m`, `i(m)` is the interval containing it:

```
r(m) = P_adj( i(m) ) - P(m)
```

### 11.3 Delivered quantities

The **assessment window** `W` is `[start + ramp, end)`. IC-11 counts the ramp inside the
duration, so the full kW is owed from the end of the ramp.

`W` is cut into consecutive 15-minute **blocks** from its first minute. A final remainder
shorter than 15 minutes joins the block before it. A `W` shorter than 15 minutes is one block,
and the event carries the flag `short_event`. `R_b` is the mean of `r(m)` over block `b`.

| Quantity | Definition | Unit |
|---|---|---|
| Mean delivered reduction | `dP_mean` = mean of `r(m)` over `W` | kW |
| **Firm delivered reduction** | `dP_firm` = minimum of `R_b` over the blocks of `W` | kW |
| Event energy | `E_event` = sum of `r(m)` over `[start, end)`, divided by 60. The ramp is included | kWh |
| Energy per Settlement Period | The same sum over the minutes of each half hour the event touches | kWh |

Breach minutes tolerated under section 11.5 are left out of every mean and sum in this table.

### 11.4 Rebound

The recovery window is `[end, end + R)`, with `R` the larger of the declared `recovery_min` and
180 minutes [M]. The one precedent the author read uses a 3-hour recovery window in simulation
([S6]); it is a precedent, not an authority.

| Quantity | Definition | Unit |
|---|---|---|
| Rebound energy | `E_rb` = sum of `P(m) - P_adj(i(m))` over the recovery window, divided by 60. Signed | kWh |
| Rebound peak | The largest 15-minute block mean of `P - P_adj` in the recovery window, not below 0 | kW |
| Recovery time | Minutes from `end` to the end of the last block whose mean excess is above `u_block` (section 12.2). If the last block of the window is still above it, the flag `recovery_incomplete` is set | min |
| **Net energy** | `E_net = E_event - E_rb` | kWh |

Each is reported beside the declared `rebound.energy_kwh`, `peak_kw` and `recovery_min`. IC-11
defines rebound as demand above the counterfactual baseline, which is what these measure.

### 11.5 IT-safety compliance

A declared kW is firm only within the binding band. Owner's answer of 2026-11-23, "Allow a short
tolerance": a breach shorter than a set number of minutes does not void the event. The owner set
no number. The number and every rule below are decided by Fable 5.2 under the owner's delegation
of 2026-11-23.

**Span and sensors.** The thermal span is every minute from `start` to the end of the recovery
window. The sensors are every Q-13 sensor, or every Q-14 sensor where `limits.binding.point` is
`supply_air`. All bands are those of the IC-11 surface the instruction names, as declared. This
method holds no ASHRAE number of its own.

**Three tests, in this order.**

| Test | Band or limit | Rule | Tolerance |
|---|---|---|---|
| T1 Safety envelope | `limits.ashrae.temperature` (the declared ASHRAE envelope, `allowable` or `recommended`), and `limits.customer_sla.temperature` where an SLA is declared | Any sensor, in any minute of the span, outside either band | **None.** The event is `thermal_breach` |
| T2 Rate of change | `limits.ashrae.max_rate_of_change_tenths_c_per_hour`, as `rho` in K per hour | Any sensor and minute `m` of the span where both samples are valid and `\|T(m) - T(m-60)\| > rho` or `\|T(m) - T(m-15)\| > rho / 4`. Samples before `start` may serve as `T(m-60)` and `T(m-15)` | **None.** The event is `thermal_breach`. Where the rate is declared `unknown` the test is not run, the largest 15- and 60-minute changes are reported, and the event carries the flag `rate_limit_undeclared` |
| T3 Binding band | `limits.binding` | A **breach minute** is a minute in which any sensor is outside the band. Breach minutes are counted once per minute, however many sensors breach | **Up to 15 breach minutes in total over the span.** More than 15 is `thermal_breach` |

T1 is the IT-safety line, and it is not negotiable. A minute outside the declared envelope voids
the event whatever its length. Where `binding_source` is `ashrae`, the binding band *is* the
envelope, so T1 catches every breach before T3 is reached, and there is no tolerance at all.

**Total, not longest.** The tolerance counts all breach minutes in the span, whether in one
excursion or several. A cap on the longest excursion alone would let repeated short excursions
pass. 15 minutes in total also bounds the longest excursion at 15 minutes.

**Breach minutes are excluded from credit, not credited.** In an event that passes all three
tests with one or more breach minutes:

- in section 11.3, a breach minute inside `[start, end)` is left out of every mean and every sum:
  `dP_mean`, each block mean `R_b`, `E_event` and the energy per Settlement Period. A block whose
  every minute is a breach minute has `R_b = 0`. Excluded minutes do not count as data loss under
  section 13.4;
- in section 11.4, breach minutes in the recovery window stay in the rebound figures, which
  count against Neura;
- the event carries the flag `thermal_tolerance_used`, with its breach minutes, the longest
  excursion in minutes, and the largest distance outside the binding band in K.

**Counts reported for every event.** *Thermal compliance minutes*: minutes with every sensor
inside the binding band. *Breach minutes*: minutes with any sensor outside it. *Envelope
minutes*: minutes failing T1. *Rate exceedances*: minutes failing T2.

An event with the status `thermal_breach` has its figures reported, and its credited kW and kWh
are 0. A reduction bought by leaving the band beyond the tolerance, or by leaving the declared
envelope at all, is not the product IC-11 declares.

**Why 15 minutes, and what it rests on.** No source read for this document sets a tolerated
duration for a data-centre binding band. The number is [M]. It is chosen to sit on the shortest
time scale that the sources do use:

1. ASHRAE TC 9.9 sets its shortest temperature limit over 15 minutes: for solid-state IT
   equipment, at most 20 °C per hour and no more than 5 °C in any 15-minute period, under the
   5th edition (2021) of *Thermal Guidelines for Data Processing Environments*. This is
   [2ry], reported by [S8] and, for the hourly figures, by [S9]. The author did not read the
   Thermal Guidelines, which are a paid publication. T2 takes the declared rate from IC-11 and
   applies it pro rata over 15 minutes, as [S9] says the specification is relative.
2. The allowable envelope is where manufacturers verify that equipment functions. Exposure
   outside the recommended range harms reliability when it is *prolonged*, and operation outside
   the allowable envelope "risks catastrophic equipment failure" ([S10], primary). That is why a
   short excursion inside the envelope is tolerated and any excursion outside it is not.
3. TC 9.9 expresses the reliability effect of temperature as a time-weighted failure-rate factor
   over a year's operation, the "X-factor" ([S11], secondary). [S9] reports that TC 9.9 left
   the length of an "acceptably short excursion" to each operator. 15 minutes is less than 0.003 %
   of a year [I].
4. The NESO Demand Flexibility Service terms assess delivery per 30-minute Settlement Period,
   with a graded performance factor, and set no thermal or minute-level tolerance ([S12],
   primary, a draft). No DSR term read for this document points to a different number.
5. 15 minutes is the model interval and the block length of this method (sections 2 and 11.3).
   A tolerance of one block means that at most one block's worth of minutes is excluded.

### 11.6 Diagnostics, reported and not credited

| Diagnostic | Definition |
|---|---|
| Heat absorbed | On Path T: the sum over the event of `Q_hat - Q`, in kWh thermal |
| IT load shift | Mean `L` over `W` minus mean `L` over the reference window, in kW. A rise may be server fans responding to warmer air [I]. It is not deducted, because IT load also moves for its own reasons and a one-sided deduction would bias the result |
| Boundary reduction | The BL01 replica on Q-07 for the same Settlement Periods (section 15) |
| Algebraic uncertainty | Section 12.5 |

### 11.7 Rounding

IC-11 carries integers. Reported figures are rounded so that rounding never favours Neura:

- delivered and credited kW and kWh are rounded **down** to the integer;
- rebound energy, rebound peak, recovery time and every uncertainty are rounded **up**;
- signed figures round toward the less favourable value.

### 11.8 Status, verdict and credit

Every event gets exactly one **status**, never a null:

| Status | When |
|---|---|
| `verified` | A model was registered in time (or a documented adjustment under section 9.4 applies), gates G1 to G6 pass, data rules are met, and section 11.5 tests T1 to T3 pass |
| `thermal_breach` | Otherwise verifiable, but section 11.5 test T1, T2 or T3 failed |
| `unverified` | Anything else, with one or more reasons: `no_registered_model`, `cell_baseline_unavailable`, `out_of_coverage`, `nre_rebaselining`, `data_loss`, `uncertainty_too_high` |

A `verified` event gets one **verdict** against the agreed kW, using `U68_block` from section 12:

| Verdict | Test |
|---|---|
| `delivered` | `dP_firm ≥ K_ref` |
| `delivered_within_uncertainty` | `dP_firm < K_ref ≤ dP_firm + U68_block` |
| `shortfall` | `dP_firm + U68_block < K_ref` |

**Credit.**

```
credited_kw  = min( dP_firm, K_ref )                      if verified, else 0
credited_kwh = min( E_event, K_ref * duration_min / 60 )  if verified, else 0
```

Over-delivery is reported and not credited. An unverified event is credited nothing, and no
later model may be used to verify it. The delivery ratio `dP_firm / K_ref` is reported for every
event that has a computable figure.

### 11.9 What an event says about a declared curve point

An event **supports** a declared point `(duration d, reduction k)` of the cell it started in
when it is `verified`, its duration is at least `d`, and `dP_firm ≥ k`. An event instructed
below `k` cannot support `k`, however well it went.

For each supporting event the record states `L`, `D` and `T` over the event, so a reader can see
how close the event was to the worst conditions in the cell. Firmness across a whole cell cannot
be proved by a finite number of events (weakness W8). How many supporting events a producer
needs before it raises a cell's evidence class is not set here; it is an owner question.

## 12. Uncertainty

### 12.1 Principle: placebo events

The model's error is measured directly, by running the whole event calculation on windows where
nothing happened. On such a window the true reduction is zero, so whatever the method reports is
its error. This avoids relying on an algebraic formula whose treatment of autocorrelation is
known to be unreliable on sub-daily data ([S4] section 1.2).

### 12.2 Placebo set

For an event with a given start time, duration and IC-11 season, the placebo set is every day of
the training window, in that season, on which the window with the **same local clock start time
and the same duration** satisfies all of:

1. every interval from the start of its reference window to the end of its recovery window is
   eligible (section 7.3);
2. its same-day adjustment is valid (section 10.2).

Each placebo window is computed exactly as an event, with ramp and blocks as the real event,
except that **out-of-fold predictions** (section 7.4) replace `P_hat`.

From the `K` placebo windows:

```
e_k      = dP_mean of placebo window k            (true value 0)
u_mean   = sqrt( mean over k of e_k^2 )
bias     = mean over k of e_k
scatter  = standard deviation over k of e_k
u_block  = sqrt( mean over every block of every placebo window of R_b^2 )
```

`u_mean` and `u_block` are root-mean-square errors, so they include any bias.

**Too few placebos.** If `K < 20` [M], the season condition is dropped. If `K` is still below
20, then `u_mean = u_block =` the out-of-fold hourly RMSE of the whole training window, with no
reduction for averaging and with `bias` set to that same figure. The fallback is recorded.

### 12.3 Measurement uncertainty

The model was trained on the same meters that measure the event, so a meter's gain error scales
`P_adj` and `P` alike and acts on the reduction, not on the level [I].

```
e_rel   = sqrt( e_meter^2 + e_ct^2 )
u_meter = e_rel * dP_mean
```

`e_meter` and `e_ct` are the class limits of the installed meter and current transformers from
the plan, as fractions. A class limit is used as a standard uncertainty without reduction, which
overstates it [M]. Where feeders have different classes the largest applies.

Driver-sensor error is not added separately. The placebo windows used the same sensors, so their
error is already inside `u_mean`, as long as no sensor changed. A sensor change is a static-factor
change.

### 12.4 Statement of uncertainty, and gate G6

```
U68_mean  = sqrt( u_mean^2  + u_meter^2 )        U95_mean  = 2 * U68_mean
U68_block = sqrt( u_block^2 + u_meter^2 )        U95_block = 2 * U68_block
U68_E     = U68_mean * duration_min / 60         kWh
FSU68     = U68_mean / dP_mean                   fractional savings uncertainty
```

The factor 2 for the 95 % level is the usual normal approximation; the author did not re-read a
metrology source for it [U]. The empirical 2.5 and 97.5 percentiles of `e_k` are reported beside
it when `K ≥ 40` [M].

Every delivered figure is stated in this form:

> `dP_mean` = *x* kW ± *U68_mean* kW (68 %), ± *U95_mean* kW (95 %); FSU68 = *f* %;
> *K* placebo windows; meter term *u_meter* kW.

**Gate G6.** An event is verified only if `FSU68 ≤ 50 %`. Otherwise it is unverified, reason
`uncertainty_too_high`.

Basis: [S5] section 4.6.2 reports that ASHRAE Guideline 14 requires uncertainty below 50 % of
annual reported savings at 68 % confidence, and that IPMVP requires savings larger than twice the
standard error of the baseline. At the level of one event the two say the same thing:
`dP_mean ≥ 2 × U68_mean`. Both are [2ry]; the author read neither standard. Guideline 14 states
its limit for annual savings. Applying it to a single event is this method's choice [M]. The
50 % value is one of the secondary-sourced limits listed in section 8.2; a correction is v2.

**Minimum verifiable kW.** For each `flex` cell and each duration on its curve, the model card
publishes `2 × U68_mean` for a window starting at the band's first minute, with `u_meter` taken
at `K_decl`. Dispatch and the reader therefore know before an event the smallest reduction this
baseline can verify there.

### 12.5 Algebraic cross-check

The fractional savings uncertainty formula of ASHRAE Guideline 14 is also computed, as
paraphrased in [S4] section 3.1:

```
FSU_alg = t * 1.26 * CV * sqrt( (n / n') * (1 + 2 / n') * (1 / m) ) / F
n'      = n * (1 - rho) / (1 + rho)
```

`CV` is the in-sample hourly CV(RMSE) as a fraction, `n` the training hours, `m` the event hours
(`max(1, ceil(duration_min / 60))`), `F = dP_mean / mean of P_adj over W`, `rho` the lag-1
autocorrelation of the hourly training residuals (not below 0), and `t = 1` for the 68 % level
[M].

It is reported and **gates nothing**. [S4] is a paraphrase, not the standard, and [S4] itself
concludes that the algebraic treatment of autocorrelation "should not be trusted if applied to
hourly data". A large gap between `FSU_alg` and `FSU68` is a prompt to look, not a result.

### 12.6 Aggregating events

Model bias is shared between events, so event errors are not independent. For a set of events
`j` with durations `h_j` in hours:

```
U68_month = sqrt( ( sum_j |bias_j| * h_j )^2 + sum_j ( scatter_j * h_j )^2 + ( e_rel * sum_j E_event_j )^2 )
```

Bias adds linearly, scatter in quadrature, and the meter term is fully correlated. Only verified
events enter a total.

### 12.7 What the uncertainty does not cover

Stated in every report: interactive effects outside the boundary (W5), deliberate inflation of
the baseline (W3), whether placebo days resemble event days (W7), and whether a cell is firm at
conditions no event met (W8).

## 13. Missing data and meter failure

No rule in this section estimates a value in Neura's favour. A gap never becomes a null: it
becomes a flagged substitution allowed below, or a reduction of zero, or an unverified event.

### 13.1 In the training window

A non-valid interval is dropped. Nothing is interpolated or filled. If the eligible intervals
fall below 2,688, gate G1 fails.

### 13.2 Drivers during an event or a reference window

| Gap | Rule |
|---|---|
| `L`, `D` or `T` missing for up to 5 consecutive minutes | Linear interpolation between the neighbouring valid samples. Flagged [M] |
| `D` or `T` missing for longer | The backup weather source named in the plan, for the whole event. Flagged `backup_weather`. The backup's training-window values are archived so its offset against the site station can be seen |
| `L` missing for longer | The affected intervals have no counterfactual |

An interval with no counterfactual contributes a reduction of **zero**.

### 13.3 Plant power during an event

| Gap | Rule |
|---|---|
| Q-01 missing, Q-02 register valid on both sides | The energy across the gap is the register difference. It is spread evenly over the missing minutes. Flagged `register_recovered` |
| Q-01 and Q-02 both missing | The affected minutes have `r(m) = 0` |
| One feeder's meter failed for the whole event | That feeder's `r(m)` is 0 for the event: its measured power is taken to equal its share of the baseline. The feeder is named |

### 13.4 When an event becomes unverified

An event is unverified, reason `data_loss`, when any of these holds [M]:

- more than 10 % of the minutes in `[start, end)` have `r(m)` set to zero by sections 13.2 or
  13.3;
- any whole block of `W` has no valid minute;
- the IC-11 instruction, acknowledgement or surface is missing or its hash does not match;
- more than 10 % of the Q-13 samples needed by section 11.5 are missing. Compliance cannot then
  be shown, and it is not presumed.

### 13.5 Meter found out of class

If a verification test shows a meter was outside its class, the plan is re-issued with the
measured error, and every affected event is restated under this same method version with that
`e_meter`. If the error cannot be bounded, the affected events become unverified. A replaced or
recalibrated meter is a non-routine event (section 9.2).

## 14. Records, recompute and disputes

### 14.1 Records

All records are canonical JSON, hashed with SHA-256 as `gridq.contracts` hashes its payloads.
They are append-only. A correction is a new record that names the one it supersedes.

| Record | Holds | Made |
|---|---|---|
| Method | This file | Once per version |
| Site measurement plan | Section 5 | Before the first training window, and on change |
| Model card | Epoch; training window bounds; the hash of the list of eligible intervals; path; `T*`; candidate chosen; every coefficient; G1 to G4 results per cell; training ranges of `L`, `D`, `T`; minimum verifiable kW; method, plan and code hashes | Before each epoch |
| Event record | Instruction, acknowledgement and surface hashes; the model card hash; the hash of any adjustment record applied; every quantity of section 11 with its uncertainty; status, verdict, credit, flags and reasons | After the recovery window closes |
| Adjustment record | Section 9.4.7 | Before the adjustment is first used, and when it ends |
| Input archive | Every sample of every quantity of section 4 over the training window and the event, with quality flags, as the telemetry store's immutable raw tier holds it | Continuous |
| Dispute record | Section 14.3 | On dispute |

Under ADR-D10 the method, the trial records, the event records and the adjustment records are
self-issued by Neura and carry that label wherever they are cited. Consumers that require
third-party evidence refuse them.

### 14.2 Recompute

A verifier holding the method, the plan, the model card, the event record, any adjustment
record and the input archive can recompute every figure without Neura's software. Nothing else
is an input.

**The verifier recomputes; it does not approve** (owner, 2026-11-23, "Recompute only (as
written)"). Neura pre-registers this method alone, as a self-issued baseline method under
ADR-D10. No verifier, host or counterparty approves or countersigns it, and its standing does not
depend on one. A verifier's role is to recompute figures from the records and to dispute them
(section 14.3). In particular no
figure depends on a forecast, a dispatch decision, a plant model or a person's judgement.

A recomputation **agrees** when:

- the list of eligible intervals has the same hash;
- every coefficient matches to a relative difference of 10⁻⁶ [M]; and
- every reported integer matches to within 1 unit [M].

The tolerances exist because two correct least-squares solvers differ in the last bits. They are
not room for a different answer.

### 14.3 Disputes

The host, the counterparty the event served and the independent verifier may each dispute any
figure, for as long as the input archive is retained. The archive is kept at least as long as
any IC-11 surface cites the event as evidence. The retention period beyond that belongs to the
telemetry retention policy and is an owner question.

| Ground | Procedure | Outcome |
|---|---|---|
| The arithmetic | The verifier recomputes from the archive | If it does not agree, Neura's figure is withdrawn and the recomputed figure replaces it, under the same method version |
| An input | The disputed meter or sensor is tested | Section 13.5 |
| The record | The archive's hashes are checked against the registry | A sample that fails its hash is treated as missing (section 13) |
| A static factor | The change log and B-series records are examined | If a non-routine event was missed, section 9.3 applies from its true time and affected events are relabelled |
| The method itself | Recorded | No v1 figure changes. The objection is input to a later version (section 20) |

Every dispute, its evidence and its outcome is an appended record. A disputed figure is shown as
disputed until it closes.

## 15. Compatibility with the P376 BL01 baseline

BL01 is the import-reduction counterpart. The facts below are from the Baselining Methodology
Document [S1].

| Aspect | BL01 [S1] | This method | Compatible? |
|---|---|---|---|
| Baselined entity | An MSID Pair at the boundary, an asset-metered AMSID Pair, or asset differencing | The cooling-plant sub-meters | Partly. BL01 admits asset metering, so a BL01 replica on the cooling meters is well defined |
| Resolution | Settlement Period, 30 min | 1 min, with energy also reported per Settlement Period (section 11.3) | Yes, by aggregation |
| Baseline form | Arithmetic mean of the same Settlement Period on recent like days (section 3.4.2) | Regression on IT load and ambient | **No** |
| History used | 60 days, D−60 to D−1. Up to the ten most recent Eligible Days for a Working Day. For a Non-Working Day the four most recent Eligible Days, of which the middle two are used, ranked by the day's total Baselined Entity Metered Volume (Table 1, sections 3.4.1 and 3.4.2) | Up to 365 days, minimum 28 days of eligible data | **No** |
| Day type | Working Day or Non-Working Day | None. IC-11 has no day-type axis | **No** |
| Event days | Excluded when the Lead Party notifies them (Appendix A) | Contaminated spans excluded, to the minute | Same principle, finer grain |
| Too little history | Fewer than five Working Days, or four Non-Working Days: the baseline defaults to out-turn, so no volume is delivered | Gate failure: unverified, credit 0 | Yes, same effect |
| In-day adjustment | Additive. Mean difference over the six Settlement Periods of the In Day Reference Window: the three hours ending at Gate Closure for the first Settlement Period of day D with an Acceptance (section 3.4.3). Gate Closure is one hour before that period starts [S7] | Additive. Mean difference over a three-hour window ending one hour before the start | Yes, by design |
| Adjustment cap | None in sections 3.4.3 and 3.4.4 | ±10 % | **No**. This method is stricter |
| Adjustment unavailable | Set to zero | Set to zero | Yes |
| Second dispatch in a day | The first adjustment is reused | The same | Yes |
| Clock-change days | Not eligible; periods remapped | No special case: arithmetic is in UTC | Different, no conflict |
| Rebound | Not measured | Measured and netted | **No**. This method reports more |
| Uncertainty | Not stated | Stated per event | **No**. This method reports more |
| Who computes | SVAA, under the BSC | Neura, self-issued, recomputable by a verifier | **No** |

Two notes on [S1] itself. Its Table 2 says five to nine eligible Working Days are all used, while
section 3.4.2 says "six to 10"; the author takes Table 2 with section 3.4.1. And the page's
download link is named `Baselining_Methodology_Document_v3.1.pdf` although the page states
version 3.0; the author read the on-page text of 3.0.

**Why the form cannot match.** BL01 averages recent days. Cooling-plant power follows ambient
temperature and IT load, so a recent-day average is wrong whenever the event day is hotter or
colder than the days behind it, and the in-day adjustment has to carry the whole correction [I].
BL01 also loses its baseline when dispatch is frequent, because event days are not eligible. A
regression survives both. [S1] section 4 itself lists, as a reason to request a change to the
methodology, "demand side response technologies that are not accurately baselined".

**Dual reporting.** For every event the report shows, per Settlement Period:

1. the cooling reduction by this method;
2. a BL01 replica on the cooling meters (Q-02), treating them as an asset;
3. a BL01 replica on the boundary meter (Q-07);
4. the BESS and generation contribution over the same periods (Q-08, Q-09);
5. the differences.

The replicas follow WP-LEDGER-04's BL01 implementation. This document does not define it.

**Which figure governs.** Money in a scheme is settled on that scheme's baseline. This method's
figure is the evidence for the IC-11 surface and the attribution of a site-level reduction to
the cooling plant. Where the two differ, the settlement figure governs payment and the
difference is reported as a variance with its evidence link (spec B6-03). The cooling figure is
never substituted for a settlement volume.

## 16. Reporting

Spec TF-05 reuses the Cyprus monthly M&V report format. That format was not available to the
author, so this section fixes the **content** and leaves the layout to WP-THERM-09.

A monthly report states, from records only:

- the method version and hash, the plan hash and the model card hash for the epoch;
- gate results G1 to G4, per cell, and the minimum verifiable kW;
- every event: status, reasons, flags, `K_decl`, `K_ref`, `dP_firm`, `dP_mean`, `E_event`,
  `E_rb`, `E_net`, each with its uncertainty; verdict; credited kW and kWh;
- thermal compliance minutes, breach minutes, envelope minutes and rate exceedances, and every
  event that used the thermal tolerance;
- every documented adjustment (section 9.4), with adjusted events as a separate line;
- measured against declared rebound;
- the section 15 dual-reporting table;
- monthly totals with `U68_month`, over verified events only;
- the count of unverified events by reason. They are never omitted;
- non-routine events, drift days and open disputes;
- plant efficiency on non-event intervals (mean `P / Q` on Path T), month by month, so a drift
  in baseline efficiency is visible (W3);
- the limits of section 12.7 and the words "self-issued by Neura".

## 17. Known weaknesses, and what a regulator may challenge

### 17.1 What is actually known about the regulatory review

Spec B6-04 says the baselines "must withstand Ofgem's 2027 baselining review [R]". The author
found **no document that names a formal Ofgem baselining review for 2027** [U].

What was found and read is Ofgem's open letter of 3 November 2026 (source S2).
It says that "recent industry feedback has highlighted concerns about baselining", that Ofgem
will hold further industry engagement "in late 2026, with potential continuation into 2027",
with a focus that includes "baselining and compensation reference price", and that this will
inform "whether regulatory intervention is needed". The letter concerns flexibility market
arrangements under the BSC (it names P415, P502, P509 and P510). It does not mention cooling,
data centres or sub-metered assets.

So the review is, on the evidence read, an engagement that may lead to intervention, about
settlement baselines. Everything in 17.3 about what it may ask is inference.

### 17.2 Weaknesses

| # | Weakness | What v1 does about it |
|---|---|---|
| W1 | The counterfactual is never observed. Every figure is a model's opinion of what did not happen. | Fixed form, fixed selection, frozen coefficients, placebo error measured and stated. |
| W2 | Neura writes the method, runs the controller, holds the data path and computes the result. | Hashed before any trial; self-issued label (ADR-D10); recompute without Neura's software; consumers needing third-party evidence refuse it. |
| W3 | The party that earns from a large reduction also controls the plant on baseline days. Running the plant inefficiently when no event is on would raise the baseline. | Not prevented. Baseline efficiency is reported monthly; the baseline schedule is a registered static factor; drift detection catches a step, not a slow slide [I]. |
| W4 | The same-day adjustment can be inflated by pre-cooling or by loading the plant before an event. | Window ends an hour before the start; setpoints must equal the registered schedule; ±10 % cap; flag at twice the cap. A host action outside the BMS record would not be seen. |
| W5 | The boundary is the cooling plant, not the site. Server fans, UPS losses and humidification may rise during an event and are not netted off [I]. | IT load shift and the boundary BL01 replica are reported. No deduction. |
| W6 | The 30 % and ±10 % gates and the 50 % uncertainty gate are cited through secondary sources (the 30 % and ±10 % as tolerances for calibrated simulation), and are loose for this use. | Marked as secondary-sourced in section 8.2, by the owner's choice of 2026-11-23. A correction is v2. The binding test is G6. |
| W7 | Placebo error assumes non-event days resemble event days. Events are called at times of system stress, which may coincide with unusual weather or load [I]. | Coverage gate; placebos matched on season, clock time and duration; conditions of every event recorded. |
| W8 | A firm kW means any start in the cell. A finite number of events cannot prove that. | Section 11.9 states what each event does and does not support. |
| W9 | The model has no thermal state. It cannot tell a loop that is already warm from a second event from one at rest, and its recovery-window baseline ignores stored heat. | Contaminated spans; recovery window of at least 180 minutes; `recovery_incomplete` flag. |
| W10 | Residuals are autocorrelated and, with chiller staging, not normal. Algebraic uncertainty is unreliable on such data. | Block cross-validation; empirical placebo error; the algebraic figure gates nothing. |
| W11 | A site's first months give a short training window with little seasonal range, so many events will be out of coverage, above all in the first summer [I]. | They are labelled unverified, not extrapolated. |
| W12 | Free-cooling transitions and chiller staging are discontinuous. A single change point may fit them poorly. | Regime split; per-cell bias gate; the cell is unavailable if it fails. |
| W13 | Wet-bulb is taken as the instrument reports it. Its internal calculation is not recomputed. | Dry-bulb and humidity archived for an independent check. |
| W14 | No day-of-week term. A site with weekly load patterns the IT load does not explain will show cell bias. | Per-cell gate. IC-11 has no day-type axis to carry one. |
| W15 | Every [M] parameter was chosen without data. Some will prove too strict or too loose. | Change control (section 20). A v1 result stays a v1 result. |
| W16 | Sensor siting, especially outdoor temperature near heat rejection, biases a driver. | Siting requirement in Q-10; a moved sensor is a non-routine event. |
| W17 | Re-baselining after a non-routine event costs at least 28 days of verified flex. A site that changes often is rarely verifiable. | Three measured adjustment types bridge the gap (section 9.4). Every other change still costs the full re-baseline. |
| W18 | Two numbers will exist for one event, this method's and BL01's, and they will differ. | Dual reporting, and a rule for which governs. |
| W19 | Documented adjustments are easier to contest than a re-baseline (the owner's own words, 2026-11-23). | Closed list, fixed formulas, measured and hashed inputs, 10 % cap, 60-day limit, separate reporting, recompute from the archive, and both adjusted and unadjusted figures shown. |
| W20 | The 15-minute thermal tolerance is a method parameter. No source read sets a tolerated duration for a binding band. | Tied to the 15-minute scale TC 9.9 uses for rate of change (via secondary sources). No tolerance at the declared envelope or the declared rate. Breach minutes are excluded from credit. |

### 17.3 What a review may challenge [I]

Each item is the author's inference from the weaknesses above and from [S1] and the letter. None
is a statement of Ofgem's position.

1. **Independence.** A method written and run by the party it pays. Expect a demand that an
   independent party owns or approves the method, not only recomputes it. The owner chose, on
   2026-11-23 ("Recompute only (as written)"), that Neura pre-registers the method alone and
   verifiers only recompute. The challenge is accepted as a known exposure, not answered.
2. **Departure from BL01.** Why a bespoke regression should stand beside, or in place of, the
   approved methodology. [S1] section 4 gives the route: a request to the BSC Panel on the
   ground that a technology is not accurately baselined. That would need evidence this method
   has not yet produced.
3. **Gaming.** Baseline inflation (W3) and adjustment inflation (W4).
4. **Sub-metering.** Whether asset-level reduction is real at the boundary (W5), and whether the
   sub-meters meet settlement metering requirements. The author did not check which metering
   Code of Practice would apply [U].
5. **Thresholds.** The provenance and looseness of the accuracy gates (W6).
6. **Rebound.** Whether a reduction followed by a rebound delivers system value. This method
   measures and nets the rebound; BL01 does not.
7. **Verifiability in practice.** How many events end up unverified. A method that verifies
   little is honest and commercially weak.

## 18. Parameter register

Every number the method fixes. None may change within v1.

| Parameter | Value | Basis | Section |
|---|---|---|---|
| Sample period | 1 minute | [M]; matches the roadmap's one-minute standard telemetry resolution | 2 |
| Model interval | 15 minutes, UTC-aligned | [M]; [S6] also uses 15-minute slots | 2 |
| Gate resolution | 1 hour | The cited thresholds are hourly | 2, 8 |
| Valid interval | at least 13 of 15 samples | [M] | 2 |
| Clock offset limit | 30 s | [M] | 4.2 |
| Epoch | one UTC calendar month | [M] | 7.1 |
| Training cutoff | 72 h before the epoch | [M] | 7.1 |
| Training window | up to 365 days | [M] | 7.1 |
| Minimum eligible intervals | 2,688 (28 days) | [M]; spec TF-06 four-week read-only period | 7.2 |
| Folds | 5, in 7-day blocks | [M] | 7.4 |
| Selection step | 0.5 percentage points of out-of-fold CV(RMSE) | [M] | 7.5 |
| Free-cooling grid | 0.0 to 25.0 °C, step 0.5 °C; at least 672 intervals per regime | [M] | 6.4 |
| CV(RMSE) gate | 30 %, hourly | [2ry] via [S3] Table 4-2. Secondary-sourced limit; a correction is v2 | 8.2 |
| NMBE gate | ±10 %, hourly | [2ry] via [S3] Table 4-2. Secondary-sourced limit; a correction is v2 | 8.2 |
| Cell gate | ±10 % NMBE on at least 24 valid hours | [M] | 8.2 |
| Coverage, IT load | 0.9 × min to 1.1 × max | [2ry] via [S5] | 8.4 |
| Coverage, temperature | ±1.0 K beyond the training range | [M] | 8.4 |
| Out-of-service limit | 7 days | [M] | 9.2 |
| Drift rule | \|NMBE\| above 10 % on 5 consecutive days of at least 12 eligible hours | [M] | 9.3 |
| Adjustment types | NRA-1, NRA-2, NRA-3 only; detected non-routine events are never adjusted | [M], Fable 5.2 under the owner's delegation of 2026-11-23 | 9.4.1 |
| Parallel run | at least 672 intervals with both instruments valid, within 30 days before the replacement | [M] | 9.4.2 |
| Parallel-run stability | halves differ by at most 0.5 % (ratio) or 0.2 K (offset) | [M] | 9.4.2 |
| Adjustment size cap | S ≤ 10 %, all active adjustments together | [M]; equals the NMBE limit | 9.4.3 |
| Adjustment time limit | ends at re-baselined model registration, or 60 days after the re-baselining time, whichever is first | [M] | 9.4.5 |
| Reference window | 3 h, ending 60 min before the start | [M]; mirrors BL01's three-hour window [S1] | 10.2 |
| Reference validity | at least 8 of 12 intervals | [M] | 10.2 |
| Adjustment cap | ±10 % of the mean prediction in the window | [M] | 10.2 |
| Suspect flag | \|A_raw\| above twice the cap | [M] | 10.2 |
| Block length | 15 minutes | [M] | 11.3 |
| Recovery window | the larger of declared `recovery_min` and 180 min | [M]; precedent [S6] | 11.4 |
| Thermal tolerance | up to 15 breach minutes in total over the event and its recovery window, binding band only; breach minutes excluded from credit | [M], Fable 5.2 under the owner's delegation of 2026-11-23; time scale from [S8], [S9] ([2ry]) | 11.5 |
| Envelope and SLA breach | no tolerance | IT safety; [S10] | 11.5 |
| Rate-of-change test | declared rate over 60 min, and a quarter of it over 15 min; no tolerance | [M]; 15-minute form from [S8], relative form from [S9] ([2ry]) | 11.5 |
| Minimum placebo count | 20; percentiles reported from 40 | [M] | 12.2, 12.4 |
| 95 % factor | 2 | [U] normal approximation | 12.4 |
| FSU gate | FSU68 ≤ 50 % | [2ry] via [S5]; applied per event by [M]. Secondary-sourced limit; a correction is v2 | 12.4 |
| Short-gap interpolation | 5 minutes | [M] | 13.2 |
| Data-loss limit | 10 % of event minutes; 10 % of inlet samples | [M] | 13.4 |
| Recompute tolerance | coefficients 10⁻⁶ relative; integers ±1 | [M] | 14.2 |

Instrument accuracy requirements are in section 4.

## 19. Sources

This section is the method's only source list. There is no separate sources file (decided by
Fable 5.2 under the owner's delegation of 2026-11-23, so that the hash covers the sources).
"Primary" means the body that sets the rule or publishes the standard wrote the text read.
"Secondary" means someone else reports it.

### 19.1 Read by the author on 2026-11-23 (S1 to S6) and 2026-11-24 (S1 again, S7 to S12)

| Ref | Source | Kind | What was read | Supports |
|---|---|---|---|---|
| S1 | Elexon, *Baselining Methodology Document*, Version 3.0, effective 7 November 2024, status LIVE. BSC Category 3 document. https://bscdocs.elexon.co.uk/category-3-documents/baselining-methodology-document | Primary | Sections 2.2, 3.1, 3.3 (Table 1), 3.4 to 3.4.4, 4 and Appendix A, as on-page text. Re-read on 2026-11-24 from the text embedded in the page: section 2.2 (Gate Closure, In Day Reference Window), Table 1, 3.4.1 to 3.4.3 | Section 10.2 and section 15: BL01 history, day types, the In Day Reference Window and the reuse of the first adjustment |
| S2 | Ofgem, *Flexibility market arrangements: long-term goal and engagement*, open letter, published 3 November 2026. https://www.ofgem.gov.uk/sites/default/files/2026-11/flexibility-market-arrangements-long-term-goal-and-forward-industry-engagement_0.pdf | Primary | Whole letter | Section 17.1 |
| S3 | US Department of Energy, Federal Energy Management Program, *M&V Guidelines: Measurement and Verification for Performance-Based Contracts*, Version 4.0, November 2015. https://www.energy.gov/sites/prod/files/2016/01/f28/mv_guide_4_0.pdf | Primary for its own text; secondary for ASHRAE Guideline 14 | Sections 4.3, 4.5.3 (Table 4-2, equations 4-1 to 4-4), 5.3.1, glossary | Sections 1, 8.1, 8.2 (the 30 % and ±10 % limits), 9.1 |
| S4 | SBW Consulting for Bonneville Power Administration, *Uncertainty Approaches and Analyses for Regression Models and ECAM*, 11 August 2017. https://www.bpa.gov/-/media/Aep/energy-efficiency/evaluation-projects-studies/uncertainty-methods-comparisons-final.pdf | Secondary for ASHRAE Guideline 14 | Sections 1.2, 2.3.1, 2.3.2, 3.1 | Sections 8.1, 12.1, 12.5 |
| S5 | Gallagher, Leahy, O'Donovan, Bruton and O'Sullivan, *Development and application of a machine learning supported methodology for measurement and verification (M&V) 2.0*, arXiv:1801.08175v1, 24 January 2018. https://arxiv.org/abs/1801.08175 | Secondary for ASHRAE Guideline 14 and IPMVP | Sections 4.5 and 4.6.2 | Section 8.4 (coverage rule) and 12.4 (the 50 % limit) |
| S6 | Takci, Day and Qadrdan (Cardiff University), *Characterisation and Quantification of Data Centre Flexibility for Power System Support*, arXiv:2511.07159v1, 10 November 2025. https://arxiv.org/abs/2511.07159. The "Cardiff study" of spec A4-03 | Primary for its own simulation | Read through an automated summary of the HTML, not line by line. It is a simulation of a hypothetical 1 MW data centre, not a measured plant | Section 11.4 (precedent only) |
| S7 | BSC Section X, *Annex X-1: General Glossary*, version 115.0 (latest entry on its summary page: P395, implemented 2 November 2023), as published as Appendix 4, "Legal Text for Elexon Ownership Change", to a UK Government consultation. https://assets.publishing.service.gov.uk/media/65afb532bc0de3000d187343/Appendix_4_-_Legal_Text_for_Elexon_Ownership_Change_-_Section_X-1_of_the_BSC.pdf | Primary (BSC legal text, in a government-published copy) | The summary page and the definition of "Gate Closure". The live Annex X-1 on bscdocs.elexon.co.uk could not be read, because its text loads by script; a later version may exist | Section 10.2 and section 15: Gate Closure is one hour before the start of the Settlement Period |
| S8 | Envigilance, *ASHRAE TC 9.9: Critical Data Center Thermal Guide 2026*, web page. https://envigilance.com/compliance/ashrae-tc-9-9/ | Secondary (a compliance vendor reporting the 5th edition, 2021, of the TC 9.9 Thermal Guidelines) | The page, through an automated reader that quoted it | Section 11.5: 20 °C per hour and no more than 5 °C in any 15-minute period for solid-state IT equipment; 5 °C per hour for tape |
| S9 | Ian Seaton, *What Is the Difference Between ASHRAE's Recommended and Allowable Data Center Environmental Limits? – Part 4*, Upsite Technologies blog, 27 November 2019. https://www.upsite.com/blog/what-is-the-difference-between-ashraes-recommended-and-allowable-data-center-environmental-limits-part-4/ | Secondary | The post, through an automated reader that quoted it | Section 11.5: the rate limit is a relative specification; allowable limits "allow for short operating excursions"; TC 9.9 left the length of an acceptably short excursion to each operator |
| S10 | ASHRAE Handbook, chapter *Data Centers and Telecommunication Facilities* (SI). https://handbook.ashrae.org/Handbooks/A15/SI/a15_ch19/a15_ch19_si.aspx | Primary (ASHRAE) | The chapter's envelope text and Table 1, through an automated reader that quoted parts of sentences. The edition is not settled: the URL reads A15 (taken to be the 2015 Applications volume [I]), and the reader reported a 2016 *HVAC Systems and Equipment* heading | Section 11.5: the allowable envelope is where manufacturers verify function, "not a statement of reliability"; prolonged exposure outside the recommended range reduces reliability; operation outside the allowable envelope "risks catastrophic equipment failure". The chapter states no rate-of-change limit and no excursion duration |
| S11 | William Tschudi (Lawrence Berkeley National Laboratory), *ASHRAE Thermal Guidelines*, slide deck, SVLG 2015 (PDF created 16 April 2015). https://datacenters.lbl.gov/sites/default/files/ASHRAE%20Thermal%20Guidelines_%20SVLG%202015.pdf | Secondary (a presentation of the 2011 TC 9.9 guidance) | Slide text extracted from the PDF. The charts are images and were not read | Section 11.5: allowable is the range ITE is designed to function in; recommended is guidance for reliability; the reliability "X-Factor" assumes continuous operation and is netted over a year |
| S12 | NESO, *Demand Flexibility Service: Service Terms*, Version 4.0. The copy read leaves "Effective From" and "Date Published" blank, so it is taken to be a draft [I]. https://www.neso.energy/document/376646/download | Primary (NESO), draft | Paragraphs 6 (Performance Monitoring), 6.8 and Schedule 1 (the performance factor) | Section 11.5: DFS assesses delivery per Contracted Settlement Period with a graded performance factor, and has no thermal or minute-level tolerance |

Two searches found nothing usable for section 11.5: no free primary text of the TC 9.9 Thermal
Guidelines, and no DSR or DNO flexibility term that sets a tolerated duration for a thermal or
comfort limit. No such term is cited.

Internal: the product spec (A4-01 to A4-05, B6-02 to B6-05, TF-02 to TF-06);
`src/gridq/contracts/thermal_flex.py` and its README; the IC-11 pre-tag review of 2026-11-23;
ADR-D10; the roadmap entries for WP-THERM-04, WP-LEDGER-04, WP-TEL-01 and WP-TEL-02; and, on
2026-11-24, IC-4 1.1.0 as it stands on branch `roadmap/wp-bo06` (`src/gridq/contracts/evidence.py`:
`EvidenceType.BASELINE_METHOD`, `SelfIssuerRole.NEURA_SELF_ISSUED`, `SELF_ISSUABLE_TYPES`).

### 19.2 Named but not read

No clause, table or number in this document is taken directly from any of these.

| Source | Edition as far as known | Status |
|---|---|---|
| ASHRAE Guideline 14, *Measurement of Energy, Demand, and Water Savings* | 2014. Errata sheets dated 2019 and 2024 were seen listed, not read | Not read. Its thresholds, its fractional-savings-uncertainty formula and its range rule reach this document only through S3, S4 and S5 |
| IPMVP Core Concepts, Efficiency Valuation Organization | EVO 10000-1:2022, as an EVO web page cites it | Not read. "IPMVP-style" in this document means the structure S3 describes: a measurement boundary, a baseline model on independent variables, routine and non-routine adjustments, and stated uncertainty |
| *Uncertainty Assessment for IPMVP*, EVO | EVO 10100-1:2019, as an EVO news item of 5 July 2019 cites it | Not read |
| *IPMVP Application Guide on Non-Routine Events and Adjustments*, EVO | Announced 27 October 2020. Document number not confirmed | Not read |
| Reddy and Claridge, *Uncertainty of "Measured" Energy Savings from Statistical Baseline Models*, HVAC&R Research, January 2000 | | Not read. Origin of the 1.26 factor according to S4 |
| ASHRAE TC 9.9, *Thermal Guidelines for Data Processing Environments* | 5th edition, 2021, as S8 reports it (the IC-11 review, finding F4, left the edition open) | Not read; a paid publication. This method takes every limit from the IC-11 surface and holds no ASHRAE number. The 15-minute rate-of-change scale used to choose the tolerance in section 11.5 reaches this document only through S8 and S9 |
| ASHRAE Guideline 14-2014 and IPMVP Core Concepts 2022 | As above | Paid documents. The three secondary-sourced limits of section 8.2 rest on them and were not checked against them |
| IEC 62053-21, IEC 62053-22, IEC 61869-2, EN 1434-1 | Edition not confirmed | Not read. Used only as the names of accuracy classes |
| BSC metering Codes of Practice; BSC Sections S and X; BSCP602 | | Not read |
| Stull, *Wet-Bulb Temperature from Relative Humidity and Air Temperature*, J. Appl. Meteor. Climatol. 50, 2011 | | Not read. Considered for Q-12 and not used, because its coefficients could not be confirmed |

## 20. Version and change control

1. **Identity.** This file is `cooling_baseline_method` version 1. Its identity is its
   pre-registration hash, defined in section 20.1. The hash is recorded in this file, in the
   message of the commit that freezes it, in the changelog entry of that commit and in the
   annotated tag `methods/cooling-flex-baseline-v1` on that commit.
2. **Frozen once hashed.** After the tag, no byte of this file changes. A corrected typo is a
   change.
3. **A change is a new version.** Any change, to a rule, a parameter, a definition or a word,
   is made in a new file, `cooling_flex_baseline_method_v2.md`, with a new hash, a new tag and
   its own registration. The new file lists what changed from v1 and why.
4. **A trial run under v1 stays under v1.** Every model card and event record carries the method
   hash it was computed under. A figure computed under v1 is never recomputed under a later
   version and presented as the same figure. A later version may be applied to old inputs only
   as a separate, labelled analysis.
5. **No retrospective choice.** A new version applies only to epochs whose model card is
   registered after the new version's own registration. Which version judges an event is
   therefore settled before the event.
6. **Before trial data.** The tag on this file must predate the first sample of any site's
   training window. If it does not, the method is not pre-registered for that site, and every
   record for that site says so.
7. **Registration.** Registration in the registry, as a self-issued record under ADR-D10, is
   WP-LEDGER-04. The registered hash must equal the hash recorded at the tag. The record is an
   IC-4 evidence object of type `baseline_method` with issuer role `neura_self_issued`, which
   IC-4 1.1.0 (WP-BO-06) introduces. Its `document_hash` is `sha256:` followed by the hash of
   section 20.1.
8. **Site measurement plans and model cards** are versioned and hashed in the same way. They
   may change only facts and fitted values, never a rule of this document.
9. **Withdrawal.** If v1 is found unsound it is withdrawn by an appended record giving the
   reason. Its figures stay on the record, marked as produced under a withdrawn method. They are
   not deleted.

### 20.1 Pre-registration hash

**Rule.** The pre-registration hash is the SHA-256, in lower-case hexadecimal, of this file's
exact bytes with the one line that begins `Pre-registration hash:` removed, together with that
line's terminating newline.

**To recompute it** (any POSIX shell, from the repository root):

```
grep -v '^Pre-registration hash:' docs/methods/cooling_flex_baseline_method_v1.md | shasum -a 256
```

`sha256sum` may be used in place of `shasum -a 256`. No other line of this file begins with
that text. The file is UTF-8 with LF line endings and ends with a newline.

Pre-registration hash: sha256:c164f04ce7c1058f41f205c263c0bef3e34c0108c8dda1a1eb615834b6f4e95a
