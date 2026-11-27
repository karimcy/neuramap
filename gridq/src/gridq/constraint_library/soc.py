"""SoC window: the usable state-of-energy band and the start state inside it (D-169/JSON-F-46).

Constraint ``soc_window``. The band is ``soc_min·E ≤ soe_t ≤ soc_max·E`` on nameplate energy ``E``,
read from the release beside the warranty cycle cap it belongs with. An absent pair is today's
full 0-100% sweep with a loud data ask; a partial pair defaults the missing side to the nameplate
bound; an invalid pair is refused whole. A rolling-horizon start state outside the band is refused,
never moved onto the floor (that would credit energy the pack never bought).

Moved verbatim from ``gridq.optimisation.milp_solver`` (WP-SIZE-02); the caller reads the two
release values and passes them in, so this module never reads an assumption library.
"""

from __future__ import annotations

from typing import Any

# --- D-169 / JSON-F-46: the usable SoC window ---------------------------------------------------
# Release keys, NOT constants. The register row "Usable state-of-charge window (published case
# studies)" (Xu 15–95%, Hesse 10–95%, Garifi 15–85%) carries its own caveat: "Sources disagree by
# up to 15 percentage points on usable depth; the value is a warranty/vendor parameter, not a
# physical constant, and must come from the specific product datasheet." So the solver reads it
# from the release beside the warranty cycle cap it belongs with, and refuses to invent a number
# when absent — it warns and asks, leaving today's 0–100% sweep visible rather than silently
# "fixed" with a literature figure the register forbids reusing.
SOC_MIN_KEY = "warranty_cycle_limits.soc_min_fraction"
SOC_MAX_KEY = "warranty_cycle_limits.soc_max_fraction"


def resolve_soc_window(lo: float | None, hi: float | None) -> tuple[float, float, str | None]:
    """The usable SoC window ``(soc_min, soc_max)`` as fractions of nameplate energy (D-169).

    ``lo`` and ``hi`` are the release's ``soc_min_fraction`` / ``soc_max_fraction`` (``None`` when
    absent). Returns ``(0.0, 1.0, warning)`` — today's full 0–100% sweep — when the release carries
    neither key or carries an unusable pair, because the register's own caveat forbids substituting
    a literature window for the product's datasheet value. A partial or invalid declaration is
    refused as a pair (never half-applied) and warned.
    """
    if lo is None and hi is None:
        return (
            0.0,
            1.0,
            (
                f"USABLE SoC WINDOW NOT IN THE ASSUMPTION RELEASE (data ask, {SOC_MIN_KEY} / "
                f"{SOC_MAX_KEY}): state of energy is optimised over the FULL 0-100% of nameplate, "
                "which overstates deliverable energy per cycle by the BMS reserve (typically "
                "5-10%) "
                "and can breach the warranty DoD terms this same solve's cycle cap is honouring "
                "(D-169). Published windows disagree by up to 15 percentage points (15-95%, "
                "10-95%, 15-85% — docs/bess_constants_register.md, 'Usable state-of-charge "
                "window'), and the register states the value is a warranty/vendor parameter, not a "
                "physical constant, so "
                "no window is assumed here. Supply the product datasheet window as a release key."
            ),
        )
    lo_v = 0.0 if lo is None else lo
    hi_v = 1.0 if hi is None else hi
    if not (0.0 <= lo_v < hi_v <= 1.0):
        return (
            0.0,
            1.0,
            (
                f"usable SoC window declared as [{lo_v:g}, {hi_v:g}] fraction of nameplate is "
                "not a "
                f"valid window (need 0 <= {SOC_MIN_KEY} < {SOC_MAX_KEY} <= 1) — NOT applied; the "
                "full 0-100% sweep stands (never half-applied, never silently corrected). Fix the "
                "release."
            ),
        )
    note = None
    if lo is None or hi is None:
        note = (
            f"usable SoC window only partially declared ({SOC_MIN_KEY}="
            f"{'absent' if lo is None else lo} / {SOC_MAX_KEY}="
            f"{'absent' if hi is None else hi}); the missing side defaults to the nameplate bound "
            f"({lo_v:g}/{hi_v:g}) — declare both sides explicitly."
        )
    return lo_v, hi_v, note


def soe_window_kwh(
    soc_min_frac: float, soc_max_frac: float, energy_kwh: float
) -> tuple[float, float]:
    """``(soe_floor, soe_ceiling)`` in stored kWh: the window's fractions of nameplate energy."""
    return soc_min_frac * energy_kwh, soc_max_frac * energy_kwh


def usable_window_note(
    *,
    soc_min_frac: float,
    soc_max_frac: float,
    usable_energy: float,
    deliverable_energy: float,
    energy_kwh: float,
    eta_dis: float,
) -> str | None:
    """The disclosure a narrowed window carries: usable and AC-deliverable energy (D-169 + B-01).

    ``None`` on the full 0-100% sweep, which ``resolve_soc_window`` has already warned about.
    """
    if soc_min_frac > 0.0 or soc_max_frac < 1.0:
        return (
            f"usable energy {round(usable_energy, 3)} kWh of {round(energy_kwh, 3)} kWh nameplate "
            f"(SoC window {soc_min_frac:g}-{soc_max_frac:g}), delivering "
            f"{round(deliverable_energy, 3)} kWh at the AC terminals after discharge losses "
            f"(eta_dis {round(eta_dis, 4)}). The warranty throughput cap stays on NAMEPLATE energy "
            "because the release does not declare the warranty's own metering basis (C-31) — "
            "declare it before treating the cycle count as contractual."
        )
    return None


def initial_soe_refusal(
    initial_soe_kwh: float | None,
    *,
    soe_floor: float,
    soe_ceiling: float,
    soc_min_frac: float,
    soc_max_frac: float,
    energy_kwh: float,
) -> str | None:
    """Why a rolling-horizon start state cannot be honoured, or ``None`` when it can.

    D-169 fallout, caught here rather than left to HiGHS. A rolling-horizon caller supplies its own
    start state; the ``soe_initial`` row then fixes ``soe[0]`` at a value the
    ``[soe_floor, soe_ceiling]`` bounds forbid unless the first half-hour can charge the whole
    reserve. So say what is actually wrong, and REFUSE rather than repair: raising ``soe[0]`` to
    the floor would gift the pack ``soc_min·energy`` kWh it never bought, which is a value-creating
    invention on the pricing path. The fix is the CALLER's start-state convention.
    """
    if initial_soe_kwh is not None and not (
        soe_floor - 1e-9 <= float(initial_soe_kwh) <= soe_ceiling + 1e-9
    ):
        return (
            f"Insufficient data: the rolling-horizon start state "
            f"initial_soe_kwh={float(initial_soe_kwh):g} kWh lies outside the usable state-of-"
            f"energy window [{soe_floor:g}, {soe_ceiling:g}] kWh set by "
            f"{SOC_MIN_KEY}/{SOC_MAX_KEY} ({soc_min_frac:g}-{soc_max_frac:g} of "
            f"{round(energy_kwh, 3)} kWh nameplate). A pack cannot start outside its own operating "
            "window, and the start state is NOT silently moved onto the floor because that "
            "would credit energy the pack never bought (D-169). Start the horizon at or above "
            f"{soe_floor:g} kWh."
        )
    return None


def add_state_of_energy(model: Any, soe_floor: float, soe_ceiling: float, coords: Any) -> Any:
    """The ``soe`` variable, bounded to the usable window: the SoC constraint itself (D-169)."""
    return model.add_variables(lower=soe_floor, upper=soe_ceiling, coords=[coords], name="soe")
