---
proposed_by: agent/counterparty
date: 2026-06-23
status: proposed   # proposed | accepted | rejected
---

## Operation
insert_after   (one edit)

## Target
`## Common failure modes (watch for these)` — insert as a new bullet directly after the
"LLM-authored or invented numbers in outputs …" line (or after the outputs/frontend bullets if
those proposals are applied first).

## Proposed text
- A deterministic **policy mapping** (credit band → rate/tenor, score → band, tier → terms, …) that hides a private constant, silently picks a default, or crosses a lane boundary → keep it a **pure function of `(input, versioned assumption library)`**: the *numbers* live in the library as labelled placeholders (no private constants; changing a value bumps the version), the *mechanism* is yours. Three rules that the credit→rate/tenor mapping (`finance/credit.py`, M9) makes concrete: (1) an **unassessed / missing input is *unavailable* + a warning + a ranked data ask — never defaulted to a band or a guessed number** (defaulting "to be conservative" fabricates an assessment); (2) where the policy has a directional sense (worse credit must not buy a cheaper or longer loan), **assert monotonicity in code** and refuse to map a table that violates it — the literal guard against "a glowing return at a rate the covenant doesn't justify"; (3) **don't pull in another workstream's input to complete the chain** — the credit mapping yields rate + tenor and stops; gearing is Sim's portfolio finance, so it is read if present but never invented, and the downstream value (DSCR) stays honestly *unavailable* until that owner supplies it. Round at parse time (fixed dp) so identical inputs reproduce identical terms and the run-context hash is stable. Seam a future data source (e.g. Creditsafe) at the **existing schema field** it would populate (`CreditStatus`), not by widening a frozen contract ahead of the producer.

## Evidence
W4-5 (counterparty → credit → finance, decision **D18**). The stated precondition "W3-5 deterministic
credit mapping" had never been built; the lane built it as `gridq.finance.credit`
(`map_credit_to_rate_tenor` / `build_debt_terms_from_credit`) reading a new versioned assumption
`financing.credit_rate_tenor_policy`. `unknown`/`not_assessed` → unavailable (tested: never falls
through to a band); monotonicity enforced (tested: a non-monotonic table refuses to map); gearing
deliberately left out (portfolio finance, B001 / module ownership) so rate/tenor reach
`FinancialCase.debt_assumptions` while DSCR stays unavailable and the case stays not-bankable on
placeholders. Resolves the rate/tenor half of **C008**; realises the **B001** workaround. 21 new
tests green (`tests/test_finance_credit.py`, `tests/test_ingestion_counterparty.py`);
ruff/format/mypy/review_gates clean on the lane's files.

## Notes
Procedural "how to act" whenever a deterministic policy/lookup feeds the priced core — it
generalises the existing "no module holds a private constant" + "missing input → ask, never invent"
rules to the specific traps a mapping invites (silent defaulting, non-monotonic policy, lane-boundary
leakage, contract pre-freezing). The *facts* (what was built) live in `docs/changelog.md`, the
*decision* in `docs/decisions.md` (D18); this is the reusable rule.
