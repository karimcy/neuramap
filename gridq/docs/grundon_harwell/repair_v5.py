import openpyxl

XL = "Sim Work/Model_v5.xlsx"
wb = openpyxl.load_workbook(XL, read_only=True, data_only=True)
ws = wb["Portfolio Build"]

rows = {}
for i, row in enumerate(ws.iter_rows(values_only=True), 1):
    rows[i] = list(row)
wb.close()


def arr(r):
    return [x if isinstance(x, (int, float)) else 0.0 for x in rows[r][3:75]]


ebitda = arr(32)  # r32
capex = arr(24)  # r24 (negative)
debt_dr = arr(25)  # r25 (positive inflow)
interest = arr(28)  # r28 (POSITIVE magnitude of an expense)
princ = arr(29)  # r29 (negative)
proj = arr(33)  # r33 unlevered
pub_eq = arr(34)  # r34 as published

n = len(ebitda)

# as-published: r34 = r32+r24+r25+r28+r29   (interest ADDED)
eq_pub = [ebitda[i] + capex[i] + debt_dr[i] + interest[i] + princ[i] for i in range(n)]
# corrected: interest SUBTRACTED
eq_fix = [ebitda[i] + capex[i] + debt_dr[i] - interest[i] + princ[i] for i in range(n)]


def irr(cf, lo=-0.9999, hi=10.0):
    def npv(r):
        return sum(c / (1.0 + r) ** i for i, c in enumerate(cf))

    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def ann(q):
    return None if q is None else (1 + q) ** 4 - 1


max_abs_diff = max(abs(eq_pub[i] - pub_eq[i]) for i in range(n))
print(f"replication check vs published r34:  max abs diff = {max_abs_diff:.4f}")

u = ann(irr(proj))
lp = ann(irr(eq_pub))
lf = ann(irr(eq_fix))
tot_int = sum(interest)

print()
print("PORTFOLIO (100 MW, Central/Central, 72 quarters)")
print(f"  Unlevered IRR (unaffected)      : {u * 100:.2f}%   [Sim published 7.75%]")
print(f"  Levered IRR  as published       : {lp * 100:.2f}%   [Sim published 20.88%]")
print(f"  Levered IRR  interest corrected : {lf * 100:.2f}%")
print(f"  -> overstatement               : {(lp - lf) * 100:.2f} pp")
print(
    f"  Total interest mis-signed       : GBP {tot_int:,.0f} "
    f"(counted as income, x2 swing GBP {2 * tot_int:,.0f})"
)

eq_inv = 18080000.0
moic_pub = sum(c for c in eq_pub if c > 0) / eq_inv
print(f"  MOIC as published               : {moic_pub:.3f}   [Sim 3.325]")
print(f"  MOIC corrected                  : {sum(c for c in eq_fix if c > 0) / eq_inv:.3f}")

# ---- Asset Economics: repair the two defects, 1 MW, annual, 15 yr ----
capex1, gear, cod, tenor = 452000.0, 0.6, 0.085, 10
debt = capex1 * gear
eq0 = capex1 - debt
rev = [66991.72, 65220.63, 63394.55]
for y in range(3, 15):
    rev.append(63394.55 * (1.025 ** (y - 2)))
opex = [17000.0 * (1.025**y) for y in range(15)]

cf_u = [-capex1]
cf_e = [-eq0]
bal = debt
for y in range(15):
    ebit = rev[y] - opex[y]
    i_y = bal * cod if y < tenor else 0.0
    p_y = debt / tenor if y < tenor else 0.0
    cf_u.append(ebit)
    cf_e.append(ebit - i_y - p_y)
    bal = max(bal - p_y, 0.0)

print()
print("SINGLE ASSET (1 MW, Central, 15 yr) — defects repaired")
print(f"  Unlevered IRR : {irr(cf_u) * 100:.2f}%   [Sim sheet 6.15%]")
r = irr(cf_e)
print(
    f"  Levered IRR   : {r * 100:.2f}%   [Sim sheet -4.97%, corrupted]"
    if r
    else "  Levered IRR   : n/a"
)
