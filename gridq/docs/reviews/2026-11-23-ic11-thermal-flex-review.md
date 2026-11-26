# IC-11 thermal flex 1.0.0: pre-tag review (2026-11-23)

Scope: `git diff 3163dff..c5556cb` on `roadmap/wp-therm-02` (commits `0f5a4c6`, `c5556cb`), against
roadmap WP-THERM-02 and spec A4-02, A4-03, TF-02, TF-04. Review only; no source changed.

## Verdict

**TAG AFTER the listed fixes.** Two code fixes (F1, F2), one sentence of semantics the owner must
confirm (F3), and text corrections (F4). Nothing structural is missing, and none of the builder's
open questions (1)-(8) requires a shape change before 1.0.0.

No consumer exists yet, so fixing these now costs nothing. After the tag, F1 and F2 are behaviour
changes to a frozen version.

## Builder's claims: all confirmed

Run in the worktree with `PYTHONPATH=src` and the `../uk/.venv` interpreter. The venv's editable
install points at `uk/`, so the worktree source has to be put on the path explicitly.

| Claim | Result |
|---|---|
| `pytest -o addopts="" -q -W ignore tests/contracts` | 543 passed, 5 skipped |
| ruff check (`src tests`), ruff format, mypy | clean; mypy 172 files |
| `python -m gridq.contracts --check` | ok for all 8 schemas; the diff touches no IC-1/2/4/13 schema |
| Six guard mutations each turn a test red | 6 red plus a passing control; the harness requires pytest exit 1, so a mutant that fails to load does not count |

## Acceptance criteria

| Criterion | Status |
|---|---|
| Contract version is tagged and conformance tests pass | Tests pass. The tag `contracts/ic11-thermal-flex/v1.0.0` does not exist yet; the coordinator creates it. |
| Zero flex is valid and distinct from unavailable | Met. Four cell kinds, each with one form; `query` and `check_instruction` keep them apart; tested. |
| Consumers can run against fixtures without a plant model | Met. A fresh-interpreter test imports only `gridq.contracts` and loads no engine or solver module. |

The spec has no IC-11 row in §10A; that absence is why the roadmap calls it NEW-IC-11. The
requirement is A4-03, TF-04 and the WP-THERM-02 `delivers` text, and the field map in the module
docstring covers each element.

## Findings

### F1 (high): `check_instruction` passes events that run into an unavailable window or past `valid_until`

`src/gridq/contracts/thermal_flex.py:874` and `:880`. Only `instruction.start` is checked; the
event's end is never checked.

Failing inputs, all on the committed fixtures:
- **Into an unavailable window.** Metered surface, where the host allows control 19:00-07:00 only.
  Start `2027-01-15T06:45:00Z`, 120 min, 500 kW gives `ok=True, reasons=()`. From 07:00 to 08:45
  the event sits in the `day` cell, which is `unavailable (outside_agreed_window)`. Start 06:59
  for 300 min at 200 kW also passes.
- **Past the validity window.** Modelled surface, `valid_until` 2028-01-01T00:00Z. Start
  `2027-12-31T23:59:00Z`, 240 min, 300 kW gives `ok=True`; the event ends at 03:59 on 1 January.

Indexing a cell by event start is right for the physics. It is wrong for authority: `Unavailable`
is documented as "No instruction may target it" (`:362`), and TF-06 gates control windows on host
sign-off.

Fix, mechanical once the rule is written down:
- refuse with `availability_unavailable` when `[start, end)` overlaps any `unavailable` cell;
- refuse with `outside_validity_window` when `end > valid_until`;
- state whether overlapping an `unknown` cell refuses too;
- add one test per case.

### F2 (medium): IT-safety nesting is skipped when measurement points differ, and `binding_source` is a free label

`thermal_flex.py:448` and `:452`: the nesting check runs only when the two bands share a `point`.

Failing inputs, on the metered fixture (H1 at `it_inlet` 18.0-22.0, SLA at `it_inlet` 18.0-23.0):
- `binding = {point: supply_air, min 50, max 450}`, that is 5.0-45.0 °C, validates.
- `binding_source: "ashrae"` with binding 18.5-21.5, which is not the ASHRAE band, validates.
- `binding_source: "customer_sla"` with binding equal to the ASHRAE band, while the SLA is looser,
  validates.

The README and CHANGELOG say the binding band "must lie inside both". That is true only at a
shared point.

Fix, mechanical:
- require `binding.point == ashrae.temperature.point`;
- make `binding_source` checkable: `ashrae` means binding equals the ASHRAE band, `customer_sla`
  means it equals the SLA band at the same point, `operator_margin` means it is inside both;
- add the SLA guard to the mutation list.

Related gap: `binding` carries temperature only. A4-02 names humidity as a constraint, and nothing
says which humidity limit is enforced when the ASHRAE and SLA limits differ. This is a v1
limitation to document, not a blocker.

### F3 (medium, owner): the meaning of a declared kW is not pinned

`FlexPoint` (`:314`) says the kW "can be held". A cell spans months and hours of varying ambient
and load, so the number is a statistic. The contract does not say whether it is the worst case in
the cell (firm) or a typical value.

The adequacy simulator (WP-SITE-04) and sizing (WP-SIZE-02) will treat it as deliverable. A
producer that emits a mean changes the BESS size. If the meaning is settled after the tag, the
shape stays the same and the numbers silently mean something else, which is the worst kind of
break.

Needs one sentence in the docstring and README before the tag. This is a method choice, so it is
the owner's. Recommended answer: firm, meaning deliverable at any start in the cell with the
binding limits honoured. That also makes question (3) a clean later addition.

### F4 (low): text and fixture corrections

- README and CHANGELOG "inside both": correct if F2 is not fixed as proposed.
- `tests/contracts/_ic11_fixtures.py:36` cites the 5th edition (2021), but the A1 recommended
  humidity at `:163` is 60 % RH. From my recollection, 60 % is the 4th-edition figure and the 5th
  edition gives 70 %; I did not check the standard. Either drop the edition from the source string
  or correct the value. Do it before the tag, because the golden hashes pin the fixture.
- Metered fixture: the trial ran January to March, yet the October to December cells are served as
  `metered`, because the winter season includes them. State the rule that `evidence_class` is the
  floor for every `flex` and `zero_flex` cell and that a cell without that grade is `unknown`, and
  make the fixture follow it.

### F5 (low): accepted, not blocking; list in the README as known limits or tighten

| Where | Accepted input |
|---|---|
| `Rebound` `:293` | `peak_kw` 999999 on a 1200 kW plant; the peak is not bounded by `cooling_electrical_capacity_kw` |
| `MeteredBasis` and `TrialledBasis` `:488-523` | `observed_until` 2031 on a surface issued in 2026; `baseline_method` the same ref as a meter record |
| `ResponseTerms` `:533` | `ramp_min` 600 against 10-minute curve points; `check_instruction` ignores the ramp |
| `query` `:823-827` | beyond the longest point the answer is `status=flex, deliverable_kw=0`, a second form of zero in the answer type |
| `accepted_kw` `:922` | public, and returns 500 for an acknowledgement of another instruction; only `verify_acknowledgement` binds |
| `Kw` `:134` | no upper bound: 10^29 kW validates and exceeds the exact-integer range of JSON readers outside Python |
| `FlexInstruction` `:693-694` | `on_behalf_of` not applicable with a `stream` set; `on_behalf_of: lender` |
| All contracts, inherited | a payload that omits `contract` and `contract_version` validates as 1.0.0; neither is `required` in any schema, IC-2 included |

Sound as built: the four cell kinds, the curve's single encoding, integer units, the rebound
arithmetic, evidence class against basis, the id-and-hash binding of the acknowledgement, UTC
instants, and the GB clock handling across clock changes.

Not in v1 and safe to add later as new payloads: precedence between two surfaces for one site,
instruction cancellation, a delivery report.

## The builder's open questions

House rule that governs every "later" below: a version is a `Literal` on the instance, models are
`extra="forbid"`, and schemas are byte-frozen. So every addition is a new version that 1.0.0
readers refuse. "Breaking" here means how much consumer code changes, not whether the wire changes.

| # | Question | Owner before tag? | Later change | Freeze now or add now |
|---|---|---|---|---|
| 1 | Weekday/weekend split | No. Safe v1 limit; A4-03 asks only for time of day and season. | Breaking: the cell key changes for every consumer. | Freeze. It is an axis the spec does not ask for; the signal is weak for data-centre cooling and matters for the P2 hotel and C&I variant (TF-07). |
| 2 | Evidence class per cell or per surface | No. Per surface is the conservative reading, provided the floor rule in F4 is written. | Additive: an optional per-cell override. | Freeze, with the rule stated. |
| 3 | P50/P90 range per cell | The range: no. The meaning of the single number: **yes, F3**. | Additive once F3 is pinned; a silent semantic break if it is not. | Freeze. WP-DISP-01 publishes its distribution in its own `forecast_service` product and only consumes IC-11. |
| 4 | Pre-cooling and demand increase | No. Outside WP-THERM-02's `delivers`. | Additive: a turn-up curve and an instruction direction or a new payload. `reduction_kw` fixes the direction in 1.0.0. | Freeze. Adding now would fix pre-cool semantics before TF-01 diligence and the plant model exist. See the note below. |
| 5 | Partial acceptance by duration | No. Safe v1 limit. | Additive: a new response variant; consumers that match on `response.kind` add a branch. | Freeze. |
| 6 | IC-4 trial-report and baseline-method types (IC-4 1.1.0) | Not before this tag. **Owner before the first registered trialled or metered surface.** | IC-4 1.1.0 changes the `EvidenceRef` version literal, so it forces a re-version of IC-11, and of IC-1 and IC-2. | Tag. `modelled` is the only class WP-THERM-03 produces. |
| 7 | ASHRAE values unverified, placeholder hashes | Not before the tag. Placeholder hashes are house style, as in the IC-1 and IC-4 fixtures. | Fixture only. | Fix the source label now (F4). The standard is a gated document: the owner supplies it before any registered surface. |
| 8 | Location `src/gridq/contracts/` | No. | None. | Tag. All four frozen contracts live there and are tagged from there; no top-level `contracts/` directory exists, and the roadmap gave each of them a `contracts/icNN_*/` path too. The roadmap is stale, not the code. The coordinator reconciles `roadmap.yaml`: WS-THERM's owned path `contracts/ic11_thermal_flex/` does not exist, and path-prefix ownership cannot be enforced on a shared package. |

Note on (4): B2-04 is P0 and says "pre-cool ahead of forecast restrictions and price peaks", and
TF-04 gives B2 the single joint dispatch. WP-DISP-03 delivers pre-cooling and must pass IC-11
conformance, so it cannot meet B2-04 on 1.0.0. The roadmap needs an IC-11 1.x work package ahead of
WP-DISP-03. That is a scope item for the coordinator, not an owner choice.

Note on (6): IC-4 has no Neura issuer role by design, yet WP-THERM-04 registers a Neura-authored
baseline method "as an evidence object", and trial records come from Neura's own controller. Who
issues those documents is a decision about what counts as evidence.

## Questions that need the owner

1. **Before the tag (F3).** Is a declared kW firm, meaning deliverable at any start in the cell
   within the binding limits, or a typical value? Recommended: firm.
2. **Before the first registered trialled or metered surface (6).** Who is the IC-4 issuer of the
   cooling baseline method and of the trial and meter records, given that Neura cannot issue
   evidence? Which evidence types does IC-4 1.1.0 add?
3. **Before any registered surface (7).** Supply the ASHRAE TC9.9 text so the envelope numbers are
   transcribed from the source.

Everything else in (1)-(8) is a technical v1 limitation or coordinator housekeeping.
