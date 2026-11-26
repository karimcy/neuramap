"""Equipment catalogue for the UI's Equipment page and the wizard's equipment stage.

A display-only reference list of real, currently-shipping commercial products: solar panels
(priced per Wp), battery systems (per kWh), and inverters (per W). It informs the picker UI only;
it does not feed the optimiser (sizing/dispatch stay the MILP's job, untouched) and carries no
underwriting numbers. Prices are indicative list ballparks for the UI, not quotes. The curated
seed rows are sourced from the gridq.ai reference catalogue captured in
``docs/benchmark/gridq_ai_reference/``.

Users **extend** the catalogue from the Equipment page ("Upload spec sheet"): they type the
product's specs and, optionally, attach the manufacturer datasheet as the source-of-truth file
(``add_equipment``). No number is ever parsed or invented from the file - the human types the
authoritative figures and the datasheet is stored only as provenance (the deterministic-core /
"never invent a number" invariant, CLAUDE.md). User entries persist under the **gitignored** run
store (see ``store.run_store_dir``), so real product/pricing data is never committed, and
``catalogue()`` merges them with the curated seed for both the Equipment page and the picker.

Each row is a plain dict so templates can render it without model wiring, and carries ``source``
(``"curated"`` or ``"user"``) so the UI can mark user rows and link their datasheet.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

#: Solar PV modules: rated power (W), module efficiency (%), indicative price per watt-peak (£/Wp).
SOLAR_PANELS: list[dict[str, Any]] = [
    {
        "maker": "Canadian Solar",
        "model": "HiKu7 CS7N-665TB-AG",
        "power_w": 665,
        "eff_pct": 21.80,
        "price": 0.17,
    },
    {
        "maker": "Trina Solar",
        "model": "Vertex TSM-585DEG21C.20",
        "power_w": 585,
        "eff_pct": 21.60,
        "price": 0.17,
    },
    {
        "maker": "Risen Energy",
        "model": "RSM144-10-585BHDG",
        "power_w": 585,
        "eff_pct": 22.60,
        "price": 0.16,
    },
    {
        "maker": "Jinko Solar",
        "model": "Tiger Neo N-type 580W",
        "power_w": 580,
        "eff_pct": 22.44,
        "price": 0.17,
    },
    {
        "maker": "Astronergy",
        "model": "ASTRO N7 CHSM72N(DG)/F-BH 580W",
        "power_w": 580,
        "eff_pct": 22.44,
        "price": 0.15,
    },
    {
        "maker": "JA Solar",
        "model": "JAM72D40-575/LB",
        "power_w": 575,
        "eff_pct": 22.21,
        "price": 0.18,
    },
    {
        "maker": "Axitec",
        "model": "AXIpremium XXL HC 575Wp",
        "power_w": 575,
        "eff_pct": 22.20,
        "price": 0.17,
    },
    {
        "maker": "LONGi",
        "model": "Hi-MO 6 LR5-72HPH-570M",
        "power_w": 570,
        "eff_pct": 22.02,
        "price": 0.17,
    },
    {
        "maker": "JA Solar",
        "model": "JAM72S30-545/MR",
        "power_w": 545,
        "eff_pct": 21.10,
        "price": 0.17,
    },
    {
        "maker": "LONGi",
        "model": "Hi-MO 5 LR5-72HBD-540M",
        "power_w": 540,
        "eff_pct": 20.90,
        "price": 0.16,
    },
    {
        "maker": "Trina Solar",
        "model": "Vertex S+ TSM-445NEG9R.28",
        "power_w": 445,
        "eff_pct": 22.30,
        "price": 0.19,
    },
    {
        "maker": "Phono Solar",
        "model": "PS445M4H-24/TH",
        "power_w": 445,
        "eff_pct": 22.00,
        "price": 0.15,
    },
    {
        "maker": "Jinko Solar",
        "model": "Tiger Neo N-type 440W",
        "power_w": 440,
        "eff_pct": 22.02,
        "price": 0.19,
    },
    {
        "maker": "SunPower",
        "model": "Maxeon 6 AC 440W",
        "power_w": 440,
        "eff_pct": 22.80,
        "price": 0.32,
    },
    {
        "maker": "Yingli Solar",
        "model": "PANDA 3.0 PRO 440W",
        "power_w": 440,
        "eff_pct": 21.90,
        "price": 0.17,
    },
    {"maker": "REC", "model": "Alpha Pure-R 430W", "power_w": 430, "eff_pct": 22.30, "price": 0.28},
    {"maker": "Hyundai", "model": "HiE-S430VG", "power_w": 430, "eff_pct": 21.70, "price": 0.22},
    {
        "maker": "Canadian Solar",
        "model": "HiKu6 CS6R-425T",
        "power_w": 425,
        "eff_pct": 21.80,
        "price": 0.18,
    },
    {
        "maker": "Q CELLS",
        "model": "Q.TRON BLK M-G11+ 420W",
        "power_w": 420,
        "eff_pct": 21.60,
        "price": 0.21,
    },
    {
        "maker": "Q CELLS",
        "model": "Q.PEAK DUO BLK ML-G11S+ 405W",
        "power_w": 405,
        "eff_pct": 21.30,
        "price": 0.20,
    },
]

#: Battery systems: usable energy (kWh), chemistry, indicative price per kWh (£/kWh).
BATTERIES: list[dict[str, Any]] = [
    {"maker": "BYD", "model": "HVM 8.3", "energy_kwh": 8.28, "chemistry": "LFP", "price": 252.80},
    {"maker": "BYD", "model": "HVS 5.1", "energy_kwh": 5.12, "chemistry": "LFP", "price": 276.50},
    {
        "maker": "Huawei",
        "model": "LUNA2000-10-S0",
        "energy_kwh": 10.00,
        "chemistry": "LFP",
        "price": 244.90,
    },
    {
        "maker": "Pylontech",
        "model": "Force H2",
        "energy_kwh": 7.10,
        "chemistry": "LFP",
        "price": 221.20,
    },
    {
        "maker": "Tesla",
        "model": "Powerwall 3",
        "energy_kwh": 13.50,
        "chemistry": "LFP",
        "price": 355.50,
    },
]

#: Inverters: rated power (kW), hybrid flag, indicative price per watt (£/W).
INVERTERS: list[dict[str, Any]] = [
    {
        "maker": "Huawei",
        "model": "SUN2000-50KTL-M3",
        "power_kw": 50.0,
        "hybrid": False,
        "price": 0.06,
    },
    {"maker": "Fronius", "model": "Tauro 50-3", "power_kw": 50.0, "hybrid": False, "price": 0.07},
    {"maker": "GoodWe", "model": "GW50KN-MT", "power_kw": 50.0, "hybrid": False, "price": 0.06},
    {"maker": "Sungrow", "model": "SG50CX-P2", "power_kw": 50.0, "hybrid": False, "price": 0.06},
    {
        "maker": "SMA",
        "model": "Sunny Tripower 25000TL",
        "power_kw": 25.0,
        "hybrid": False,
        "price": 0.09,
    },
    {
        "maker": "Huawei",
        "model": "SUN2000-10KTL-M2",
        "power_kw": 10.0,
        "hybrid": True,
        "price": 0.11,
    },
    {
        "maker": "Fronius",
        "model": "Symo GEN24 10.0 Plus",
        "power_kw": 10.0,
        "hybrid": True,
        "price": 0.17,
    },
    {"maker": "SolarEdge", "model": "SE10000H", "power_kw": 10.0, "hybrid": True, "price": 0.16},
    {"maker": "GoodWe", "model": "GW10K-ET", "power_kw": 10.0, "hybrid": True, "price": 0.12},
    {
        "maker": "SMA",
        "model": "Sunny Tripower 8.0 SE",
        "power_kw": 8.0,
        "hybrid": True,
        "price": 0.14,
    },
]


# ---------------------------------------------------------------------------
# Category field specs - ONE source the upload form, its validation, and the merge all read.
# ---------------------------------------------------------------------------

#: Human category names for the upload form's selector and headings.
CATEGORY_LABELS: dict[str, str] = {
    "solar": "Solar panel",
    "batteries": "Battery system",
    "inverters": "Inverter",
}

#: Per-category fields. ``numbers`` carry an inclusive ``(min, max)`` bound and a required strictly
#: positive value (``> min``, ``<= max``); ``texts`` are free strings (required when flagged);
#: ``bools`` render as a checkbox (absent ⟹ False). Keys mirror the curated rows above so a merged
#: user row renders through the exact same template columns.
CATEGORY_SPECS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "solar": {
        "numbers": [
            {"key": "power_w", "label": "Power (W)", "min": 0.0, "max": 2000.0, "step": "1"},
            {"key": "eff_pct", "label": "Efficiency (%)", "min": 0.0, "max": 100.0, "step": "0.01"},
            {"key": "price", "label": "Price (GBP/Wp)", "min": 0.0, "max": 100.0, "step": "0.01"},
        ],
        "texts": [],
        "bools": [],
    },
    "batteries": {
        "numbers": [
            {
                "key": "energy_kwh",
                "label": "Usable energy (kWh)",
                "min": 0.0,
                "max": 100000.0,
                "step": "0.01",
            },
            {
                "key": "price",
                "label": "Price (GBP/kWh)",
                "min": 0.0,
                "max": 100000.0,
                "step": "0.01",
            },
        ],
        "texts": [
            {"key": "chemistry", "label": "Chemistry", "placeholder": "e.g. LFP", "required": True},
        ],
        "bools": [],
    },
    "inverters": {
        "numbers": [
            {"key": "power_kw", "label": "Power (kW)", "min": 0.0, "max": 100000.0, "step": "0.1"},
            {"key": "price", "label": "Price (GBP/W)", "min": 0.0, "max": 1000.0, "step": "0.01"},
        ],
        "texts": [],
        "bools": [
            {"key": "hybrid", "label": "Hybrid inverter"},
        ],
    },
}

#: Datasheet attachment limits (provenance only; never parsed for numbers).
ALLOWED_DATASHEET_EXT: set[str] = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_DATASHEET_BYTES: int = 10 * 1024 * 1024
_SAFE_DATASHEET = re.compile(r"^[A-Fa-f0-9]{1,64}\.(?:pdf|png|jpe?g)$")


# ---------------------------------------------------------------------------
# User catalogue - gitignored, under the run store; merged into catalogue().
# ---------------------------------------------------------------------------
def _equipment_dir() -> Path:
    """Gitignored directory holding the user catalogue and attached datasheets."""
    from gridq.app import store

    return store.run_store_dir() / "equipment"


def _catalogue_file() -> Path:
    return _equipment_dir() / "catalogue.json"


def _empty_user_catalogue() -> dict[str, list[dict[str, Any]]]:
    return {"solar": [], "batteries": [], "inverters": []}


def load_user_catalogue() -> dict[str, list[dict[str, Any]]]:
    """The user-added entries per category, or empty lists when none exist / the file is unreadable.

    Never raises: a missing or malformed store degrades to empty, so the Equipment page and picker
    always render (mirrors ``store.list_runs`` skipping unreadable runs rather than 500ing).
    """
    path = _catalogue_file()
    if not path.is_file():
        return _empty_user_catalogue()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_user_catalogue()
    out = _empty_user_catalogue()
    if isinstance(raw, dict):
        for cat in out:
            rows = raw.get(cat)
            if isinstance(rows, list):
                out[cat] = [r for r in rows if isinstance(r, dict)]
    return out


def _store_datasheet(data: bytes, ext: str) -> str:
    """Persist a datasheet content-addressed and return its stored filename (``<digest><ext>``)."""
    out = _equipment_dir() / "datasheets"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{hashlib.sha256(data).hexdigest()[:16]}{ext}"
    (out / name).write_bytes(data)
    return name


def datasheet_path(name: str) -> Path | None:
    """Resolve a stored datasheet by filename, rejecting anything but a content-addressed name.

    The strict regex (hex digest + known extension) blocks path traversal / arbitrary reads, so the
    serving route can hand this straight to a file response.
    """
    if not _SAFE_DATASHEET.match(name):
        return None
    path = _equipment_dir() / "datasheets" / name
    return path if path.is_file() else None


def add_equipment(
    category: str,
    entry: dict[str, Any],
    datasheet: tuple[bytes, str] | None = None,
) -> dict[str, Any]:
    """Append a user entry to the (gitignored) catalogue and return the stored row.

    ``entry`` is the already-validated row (the route owns field validation via ``CATEGORY_SPECS``);
    ``datasheet`` is an optional ``(data, ext)`` stored content-addressed and linked on the row.
    Entries are kept sorted by ``(maker, model)`` and the JSON is written with sorted keys, so the
    store is deterministic and re-adding an identical product+datasheet is idempotent.
    """
    if category not in CATEGORY_SPECS:
        raise ValueError(f"unknown equipment category: {category!r}")
    stored = dict(entry)
    if datasheet is not None:
        data, ext = datasheet
        stored["datasheet"] = _store_datasheet(data, ext)
    cat = load_user_catalogue()
    cat[category] = [r for r in cat[category] if not _same_product(r, stored)]
    cat[category].append(stored)
    cat[category].sort(
        key=lambda r: (str(r.get("maker", "")).lower(), str(r.get("model", "")).lower())
    )
    path = _catalogue_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cat, indent=2, sort_keys=True), encoding="utf-8")
    return stored


def _same_product(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same product when maker+model match (case-insensitive); re-adding overwrites in place."""
    return (
        str(a.get("maker", "")).strip().lower() == str(b.get("maker", "")).strip().lower()
        and str(a.get("model", "")).strip().lower() == str(b.get("model", "")).strip().lower()
    )


def _rows(curated: list[dict[str, Any]], user: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Curated seed rows first (tagged ``curated``), then user rows (tagged ``user``)."""
    rows = [{**r, "source": "curated"} for r in curated]
    rows += [{**r, "source": "user"} for r in user]
    return rows


def catalogue() -> dict[str, Any]:
    """The full catalogue (curated seed + user uploads) plus per-category counts.

    Read fresh per request so a just-uploaded product appears immediately on the Equipment page and
    in the wizard picker. Still display-only: it never feeds the optimiser.
    """
    user = load_user_catalogue()
    solar = _rows(SOLAR_PANELS, user["solar"])
    batteries = _rows(BATTERIES, user["batteries"])
    inverters = _rows(INVERTERS, user["inverters"])
    return {
        "solar": solar,
        "batteries": batteries,
        "inverters": inverters,
        "counts": {
            "solar": len(solar),
            "batteries": len(batteries),
            "inverters": len(inverters),
        },
    }
