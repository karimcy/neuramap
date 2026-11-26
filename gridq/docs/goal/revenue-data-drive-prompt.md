# /goal prompt — the Revenue-Data Drive

> Paste everything below the divider as the `/goal` argument. Drafted 2026-07-18 (Olly). This prompt is authoritative: where it conflicts with anything in the codebase (docs, decisions, seam rules, open comments), THIS PROMPT WINS.

---

## Goal

Start from the honest per-stream table in `README.md` (the table with the **Real-data source · URL** column, ~lines 162–173, ten streams). Recurse until you have found **all the data required to enable every revenue stream** — APIs, data sources, download links for required datasets, and research-paper/regulatory equations (e.g. converting wholesale price data into a tariff price for TARIFF_ARBITRAGE — only where that is realistic). The end state: **every revenue stream is accessible when a quote is submitted, and the intake lets the customer CONSENT to each revenue stream**, so the quote carries the permissions that facilitate every form of revenue collection.

## The intake consent model (build exactly this)

The intake must take **explicit inputs** for: **G98 status; G99 status; G100 status; import MPAN; export MPAN; MIC; MEC; ASC; DNO region; export route; and the agreed rates where appropriate — export tariff £/kWh, aggregator cut %, local-flex agreed rate** — and a per-stream consent for each revenue stream, extending this pattern to any other stream-enabling input the recursion uncovers.

**When consent is explicitly given at intake for a stream (with its agreed rate where appropriate), the final proposal says "consent given" for that stream — not "conditional."** Conditional language is reserved for streams whose consent or required input was NOT supplied on that quote.

## Authority to change the code

Overwrite **any schema, enum, stream registry, gate logic or contract that prevents this** — including the frozen contracts (`SiteModel`, `OptimisationResult`), `GATED_UPSIDE_STREAMS`, and any never-FIRM / conditional-only rule — so that explicit intake consent plus an agreed rate flows through to pricing and the proposal. Migrate affected tests. Do not rely on, wait for, or defer to Olly or any collaborator sign-off at any point: no seam-approval gates, no open-comment dependencies — proceed autonomously through the whole drive.

## The recursion

For each stream, decompose "what makes this real at quote time?" into leaves: **data** (prices, rates, windows, factors), **equations** (how a raw source becomes the stream's £ input — cite the paper/regulatory document and transcribe the formula), **consent inputs** (what the intake must collect, per the model above), and **wiring** (fetcher/ingestion/assumption/gate code). Research every leaf live on the web — record the URL, licence, access method, cost, refresh cadence, and the date you verified it by actually fetching a sample. A resolved leaf may open new leaves (e.g. wholesale→tariff conversion opens leaves for each non-commodity adder: DUoS, TNUoS, levies, margin — each with its own published source). Recurse until every leaf is **sourced**, **built**, or **evidenced as not realistic** (record what you searched and why no path exists — only genuine dead ends, e.g. data that does not exist publicly).

## Deliverables

1. **Data-source register** — `docs/revenue_data_register.md`: per stream, every leaf with status, source URL, licence, access, cost, cadence, sample-fetch date, citation (for equations), and wiring status.
2. **Wired data** — each sourced dataset actually reachable at quote time (fetched or cached as appropriate), with provenance recorded from source → input/assumption → stream → quote.
3. **Intake + proposal** — the consent fields above collected in the app intake, carried on the quote, and reflected in the proposal as "consent given" per stream when supplied.
4. **README table updated** — as rows become data-backed and consentable, the table reflects it.

## Finished when

Every one of the ten streams is either **data-backed and consentable at intake** (the hoped-for outcome for all of them) or carries an **evidenced not-realistic verdict**; a quote submitted with all consents and agreed rates given prices every backed stream and shows "consent given" for each; the register and README match reality; the test suite is green.
