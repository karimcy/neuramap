# Design: never-block runs via recorded derived assumptions

**Authority.** Olly, 2026-08-12, directed in session. Supersedes the hostile-review-era gate policy in
`CLAUDE.md` §2 and `docs/validation_requirements.md`. Recorded as decision **D44**.

**The directive, verbatim in substance.** No missing input may block a pipeline run. Where data is
absent, the Fable advisor derives the most reasonable assumption from the site's own data, its business
type and the literature; the run completes; the assumption is stated clearly in the results and the
report, flagged as requiring confirmation. Restrictions that previously stopped a run are removed and
replaced by a statement that further information is required.

## 1. The one gate that stays

| Gate | Was | Now |
|---|---|---|
| Gate 1 — MILP or nothing | Refuse if no half-hourly dispatch MILP will run | **Unchanged.** This is an engine-quality gate, not a data gate. It refuses numbers from the integration-only `BaselineEngine`. Once inputs are derived it can never block a run for a data reason. |
| Gate 2 — half-hourly basis (`ingestion/hh_mandate.py`) | Refuse without an HH basis | **Record and continue.** Synthesise from a reference profile scaled to the site's own totals, record a `DerivedAssumption`, cap the band. |
| Gate 3 — load reconciliation (`ingestion/reconciliation.py`) | Refuse on threshold breach | **Record and continue.** Record the discrepancy and the basis chosen, cap the band. |
| Connection capacity unknown | `NO_VIABLE_CONFIGURATION` / no candidates | **Record and continue.** Fable-derived import capacity, plus required-MIC reported as the headline data ask. |
| Consent absent | Stream gated to zero | **Model and label.** See §2 — permission, not data. |

## 2. Two classes of derived record

A missing *fact about the site* and a missing *permission from the customer* are not the same thing and
must never share report language.

- **`derived_input`** — data Fable may reasonably derive: connection capacity, load shape, tariff
  component, business type. Reported as "assumed, confirmation required".
- **`pending_permission`** — a permission that cannot be derived at all: revenue-stream consent, export
  MPAN, G99 approval. The stream is modelled and its value shown, labelled "requires customer consent".
  **A derived record is never written as consent granted.** Encoding the distinction in the schema is
  what stops a future agent fabricating consent fields.

## 3. Schema

New object, exported from `gridq.schemas`. `SiteModel` gains
`derived_assumptions: list[DerivedAssumption] | None = None` — a deliberate, versioned frozen-contract
change (record in `decisions.md`). Absent field must leave every existing run identity byte-identical:
follow the C033/D42 pop-if-None pattern in `assembly._canonical_run_input`.

```python
class DerivedAssumption(GridQModel):
    kind: Literal["derived_input", "pending_permission"]
    key: str                    # dotted target, e.g. "connection.mic_kw"
    missing_input: str          # what was absent, in plain words
    value: float | str | None   # None for pending_permission
    unit: str | None
    basis: str                  # the reasoning that justifies this value
    sources: list[str]          # citations; empty is allowed and must be visible
    derivation_method: Literal["fable_advisor", "sourced_default", "site_analogue", "not_derivable"]
    derived_by: str             # model identity, e.g. "claude-fable-5"
    derived_on_release: str     # assumption release in force, e.g. "gb_v1_7"
    confidence_ceiling: Literal["low", "medium", "high"]
    confirmation_ask: str       # exactly what to ask the customer
```

**Placement is in the site JSON itself, not a sidecar.** A sidecar-merge path is where N9 lived (the
HH fold wiping `half_hourly_basis`). One source of truth; the existing site input hash then covers it.

**Hash.** The derived values MUST enter `input_hash`, or two runs on different derived capacities share
a `run_id`. Since `examples/real/` and `data/` are gitignored, provenance cannot lean on git history:
persist the records into the run artifacts under `runs/` and into the report.

## 4. Ceiling policy (written once, applied everywhere)

- Structural feasibility inputs (connection capacity, export limit) cap the band at **low**.
- Shape-like inputs (load profile archetype, within-day shape) cap at **medium** — matches the existing
  `data/sites/hh/manifest.json` convention.
- `not_bankable` and the confidence band are **not** restrictions to remove. They are the mechanism that
  states confirmation is required, which is what the directive asks for. Leave both intact.

## 5. Fable derivation mechanics

Not the `advisor()` tool (it forwards a whole transcript and returns prose). Per-site derivation
subagents with `model: "fable"`, each handed an evidence pack (site JSON, manifest entry, monthly
totals, business type, relevant constants-register rows) and returning **schema-validated
DerivedAssumption JSON**.

Two hard rules in every derivation prompt:
1. **Derive inputs only, never outputs.** A derived MIC is an input. An adjusted IRR, DSCR or revenue
   figure is fabrication.
2. **Prefer an existing sourced value first.** `docs/bess_constants_register.md` has 422 sourced
   constants and the repo already has a "sourced default that announces itself" pattern. Fable's job is
   selecting and justifying, not inventing from air. `not_derivable` is a legitimate return.

Derivation runs as a separate step (`gridq derive-assumptions <site>`) writing a versioned provenance
file, so the pipeline itself stays deterministic and offline in CI. No LLM on the pricing path.

## 6. Report requirement

Every report carries a prominent, near-front **"Assumptions requiring confirmation"** section: one row
per record, giving the missing input, the value used, the basis, the resulting confidence ceiling and
the exact confirmation ask. For the no-MIC sites the report shows **both** numbers: the assumed import
capacity the run used, and the required import capacity of the recommended specification as the
headline data ask.

## 7. Rules rewrite — four targets

1. `CLAUDE.md` §2 — the "gate, not a guide" framing and the "MILP or nothing / refused" language.
2. `docs/validation_requirements.md` — authoritative gate definitions.
3. `docs/decisions.md` — D44, recording the directive, its author and date. The policy contradicts the
   hostile-review-era docs, so the authority chain must be visible.
4. **The session-briefing source** — the "never invent missing HH/price/tariff/connection/consent/revenue
   data" bullet that the `SessionStart` team_sync hook prints into every session. Locate and update it
   (likely `docs/shared_work/` or `parallel_development_plan.md`), or every future session opens with a
   rule contradicting this policy.

Then run `python -m gridq.dev.review_gates` — it screens docs for invented-number smell strings, and the
new policy text is exactly the kind of thing it may pin.
