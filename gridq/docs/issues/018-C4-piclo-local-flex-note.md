# 018 · C4 — Piclo / local-flex investigation + the manual tender path

**Status:** investigation complete (2026-07-17, Olly). Conclusion: **do not build against a Piclo
pricing API** — there isn't an open one. Use a manual, human-entered tender path that flows through
the existing `GateContext` seam (decision D12) and the C1/C2 aggregator-cut pricing.

## Findings — is there a fetchable Piclo tender/price API?

**No open/free public pricing API.** Piclo does expose APIs, but access is gated on all of:
- a **Flexibility Service Provider (FSP) account**, registered via piclomax.com;
- **per-System-Operator company qualification** (each DNO/TSO may require its own qualification);
- a **contracted API package** — "access to some endpoints may be limited by the package purchased
  and agreed in contract with Piclo", and what's available in production depends on what the
  regional DSO/TSO uses Piclo for.

Tenders are **per-DNO, per-service-window, month-by-month** external market state (e.g. SPEN tenders
month-by-month with 4-year visibility). Prices/awards are therefore **time-varying external facts**,
not a static dataset we can pin — exactly the reason D12 keeps them off `SiteModel` and behind
`GateContext`. Commercial onboarding (FSP account, package terms) is **Karim's lane** (route-to-market
/ partnerships), not the Product-Engineer layer — this is B002-adjacent.

**So:** the engine must NOT try to auto-fetch Piclo tenders/prices. Local-flex value stays
**CONDITIONAL** upside until a real, awarded tender is entered by hand.

## The manual tender path (design — uses existing seams, minimal new code)

Everything needed already exists; this is a wiring/UX task, not a new pricing mechanism:

1. **Input (human-entered, per run):** an awarded/expected tender as `(DNO area, availability
   window(s), utilisation £/MWh, expected delivered MWh)`. The DNO area is already
   `connection.dno_region`; the utilisation rate has an assumption default
   (`local_flex_piclo.utilisation_price_gbp_per_mwh`, needs_review) and a site-level override lane
   via `ExportArrangement.local_flex_agreed_rate_gbp_per_mwh` (added in D8/A4).
2. **Gate:** set `GateContext.local_flex_tender_open = True` when a tender is entered (the existing
   `LocalFlexPicloStream.check_gates` already opens on `dno_region` + `local_flex_tender_open`).
3. **Value:** the existing `LocalFlexPicloStream._price` already prices
   `utilisation £/MWh × tender-window delivered MWh × scenario`, and (C1/C2, this drive) nets the
   **aggregator cut** — `net = gross × (1 − cut)`, cut precedence: `ExportArrangement.aggregator_cut_pct`
   → `local_flex_piclo.aggregator_cut_pct` (gb_v1_5, 0.20) → 0.0. Firmness CONTRACTED only when the
   tender is genuinely awarded; otherwise CONDITIONAL, out of the priced floor.
4. **Delivered MWh:** `DispatchSummary.tender_window_discharge_mwh` — supplied from the dispatch in
   the awarded windows.

**Remaining wiring (small, deferred to the app/intake lane — do NOT build against a speculative API):**
a lightweight way to enter the tender in the UI (a `GateContext` field set / a run-time input) and,
optionally, a manual tender CSV (DNO, window, £/MWh) the run can reference. Until then, the value is
demonstrable via a supplied `GateContext(local_flex_tender_open=True)` + delivered MWh (as the
value-stack tests already do), which is the honest "conditional upside" story for a meeting.

## Bottom line for the meeting
Local flex is a real, priceable **conditional** upside: when a tender is open in the site's DNO area
and a rate is agreed, the engine prices it net of the aggregator cut. We will NOT fabricate a tender
or scrape Piclo; a tender is entered as a declared input (like consents), and priced honestly.
Relates OPEN_COMMENTS **C007** (the utilisation-rate source is still needs_review).

Sources: [Introduction to Piclo APIs](https://support.piclo.com/article/269-introduction-to-piclo-apis) ·
[Register for a Flex Provider Account](https://support.picloflex.com/article/89-register-for-a-flex-provider-account) ·
[Company Qualification](https://support.picloflex.com/article/138-company-qualification) ·
[Why APIs are the next step for flexibility markets](https://www.piclo.energy/blog/lets-connect-why-apis-are-the-next-step-for-flexibility-markets)
