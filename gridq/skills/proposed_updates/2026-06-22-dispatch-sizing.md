---
proposed_by: agent/dispatch-sizing (Olly)
date: 2026-06-22
status: proposed   # proposed | accepted | rejected
---

## Operation
append (one edit)

## Target
"## Common failure modes (watch for these)" — append one bullet.

## Proposed text
- Promoting a cross-lane volume/shape onto a frozen contract **before its producer exists** →
  freezes a guessed granularity no one can satisfy. Instead carry it on the *new* producer-lane
  artifact using the **agreed future field names** + a converter to the consumer's injected hook
  (e.g. `DispatchResult.annual_charge_kwh/discharge_kwh` → finance `DispatchEnergy`), so the later
  promotion onto the frozen seam is a rename-free copy. "Becomes computable" = the seam is wired and
  tested with a *synthetic* value, never that the placeholder fabricates one.

## Evidence
W3-2 (HANDOFF_LOG 2026-06-22, branch `agent/dispatch-sizing`): built the dispatch solver contract
(`gridq.dispatch`) + sizing skeleton (`gridq.sizing`) and wired the C010/D13 `DispatchEnergy`
producer seam. `tests/test_dispatch_energy_seam.py` proves finance levies-on-charge + price-to-target
compute the moment a synthetic dispatch result carries volumes, while the placeholder leaves them
`None` (unavailable, never invented). Followed decision D13 exactly (no field added to the frozen
`OptimisationResult`).

## Notes
Procedural ("how to act" when wiring a cross-lane seam ahead of its producer) — generalises the D13
pattern that already recurs (D9/D10/D11). Belongs in SKILLS.md, not the changelog: the *event* (W3-2
shipped) is logged in `docs/changelog.md`; this is the reusable rule. Bounded to a single bullet
under an existing heading; touches no protected block.
