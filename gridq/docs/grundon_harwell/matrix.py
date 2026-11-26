# Corrected scenario matrix — reconstruction of Model_v5's portfolio logic with the
# interest sign repaired. Deployment shape and conventions taken from Portfolio Build.
DEPLOY = [1, 1, 6, 12, 20, 24, 22, 14] + [0] * 64  # new MW per quarter, 72 quarters
GEAR, COD, TENOR_Q, OPEX0, ESC = 0.6, 0.085, 40, 17000.0, 0.025

REV = {  # (Year-1 net £/MW/yr, steady-state net £/MW/yr) from Revenue Build
    "Conservative": (35490.0, 33870.0),
    "Central": (65690.0, 69910.0),
    "Optimistic": (103500.0, 128200.0),
}
CAPEX = {"Conservative": 675000.0, "Central": 452000.0, "Optimistic": 377000.0}


def irr(cf):
    def npv(r):
        return sum(c / (1.0 + r) ** i for i, c in enumerate(cf))

    lo, hi = -0.9999, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(400):
        m = (lo + hi) / 2
        if npv(lo) * npv(m) <= 0:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def run(rev_s, cap_s):
    y1, ss = REV[rev_s]
    capex_mw = CAPEX[cap_s]
    n = 72

    # per-MW annual revenue path: Y1 -> steady state by year 3, then CPI; 2%/yr capacity fade
    def rev_per_mw(op_year):
        if op_year <= 1:
            base = y1
        elif op_year == 2:
            base = y1 + (ss - y1) * 0.5
        else:
            base = ss * (1.025 ** (op_year - 3))
        return base * (0.98 ** (op_year - 1))

    cum = []
    t = 0
    for q in range(n):
        t += DEPLOY[q]
        cum.append(t)
    ebitda = [0.0] * n
    capex = [0.0] * n
    draw = [0.0] * n
    for q in range(n):
        oy = q // 4 + 1
        r = rev_per_mw(oy) / 4.0
        o = OPEX0 * (1.025 ** (oy - 1)) / 4.0
        ebitda[q] = cum[q] * (r - o)
        capex[q] = -DEPLOY[q] * capex_mw
        draw[q] = DEPLOY[q] * capex_mw * GEAR
    # portfolio debt: each tranche amortises straight-line over 40 quarters
    bal = [0.0] * n
    inte = [0.0] * n
    prin = [0.0] * n
    for q in range(n):
        b = 0.0
        for s in range(q):
            rem = max(0, TENOR_Q - (q - s))
            b += draw[s] * rem / TENOR_Q
        bal[q] = b
        inte[q] = b * COD / 4.0
        prin[q] = -sum(draw[s] / TENOR_Q for s in range(q) if q - s <= TENOR_Q)
    proj = [ebitda[q] + capex[q] for q in range(n)]
    eqf = [ebitda[q] + capex[q] + draw[q] - inte[q] + prin[q] for q in range(n)]
    eqb = [ebitda[q] + capex[q] + draw[q] + inte[q] + prin[q] for q in range(n)]  # Sim's bug

    def ann(x):
        return None if x is None else (1 + x) ** 4 - 1

    inv = sum(-c for c in capex) * (1 - GEAR)
    return ann(irr(proj)), ann(irr(eqf)), ann(irr(eqb)), sum(c for c in eqf if c > 0) / inv


order = ["Conservative", "Central", "Optimistic"]


def show(title, idx):
    print(f"\n{title}")
    print(f"  {'Revenue v / Capex >':24s}" + "".join(f"{c:>16s}" for c in order))
    for r in order:
        cells = ""
        for c in order:
            v = run(r, c)[idx]
            cells += f"{(f'{v * 100:.2f}%' if v is not None else 'n/a'):>16s}"
        print(f"  {r:24s}" + cells)


show("PROJECT (UNLEVERED) IRR — unaffected by the bug", 0)
show("EQUITY (LEVERED) IRR — CORRECTED", 1)
show("EQUITY (LEVERED) IRR — with Sim's interest sign bug (for comparison)", 2)
show("EQUITY MOIC — CORRECTED", 3)

print("\nTarget test: which cells land in the 15%-25% levered band, corrected?")
for r in order:
    for c in order:
        u, lev, _, m = run(r, c)
        if lev is not None and 0.15 < lev < 0.25:
            print(
                f"  PASS  Revenue={r:12s} Capex={c:12s} -> unlevered {u * 100:5.2f}%  "
                f"levered {lev * 100:5.2f}%  MOIC {m:.2f}"
            )
print("\nSpread check (levered beats unlevered only when unlevered > 8.5% cost of debt):")
for r in order:
    for c in order:
        u, lev, _, _ = run(r, c)
        if u is not None and lev is not None:
            verdict = "LEVERAGE ACCRETIVE" if lev > u else "leverage dilutive"
            print(f"  {r:12s}/{c:12s}  unlev {u * 100:6.2f}%  lev {lev * 100:6.2f}%  {verdict}")
