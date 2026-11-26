"""The published sources ``thermal_plant_model`` quotes, and the figures it takes from them.

Data rule (WP-THERM-03; D168): physical constants and ASHRAE class limits come only
from a retrieved public document, quoted here with its URL and the SHA-256 of the file as
retrieved. No plant parameter lives in this module. A preset is a named, opt-in constructor; it
is never a default, and its use is visible on the output (the IC-11 ``source`` text).

Sources:

- ``ASHRAE_2011``: ASHRAE TC 9.9, *2011 Thermal Guidelines for Data Processing Environments -
  Expanded Data Center Classes and Usage Guidance* (whitepaper), Table 4 and its notes. The only
  copy reachable on 2026-11-26 was on a third-party host; ASHRAE's own TC 9.9 site refused the
  fetch (HTTP 403) and the ASHRAE reference-card URL was gone (HTTP 404). The file carries
  ASHRAE's notice that it may not be posted online without permission, so it is cited and
  hashed here and **not** committed. Class H1 is defined only in the paid 5th edition (2021),
  which is not held: there is **no H1 preset**.
- ``WMO_JCOMM_TR63``: WMO/IOC JCOMM Technical Report No. 63 (2015), *Recommended Algorithms for
  the Computation of Marine Meteorological Variables*, section I.3 to I.6, which give the WMO
  No-8 saturation vapour pressure over water, the dew point and the relative humidity formulae,
  and their validity range.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from gridq.contracts._base import Unknown
from gridq.contracts.thermal_flex import (
    AshraeClass,
    AshraeLimit,
    HumidityLimit,
    MeasurementPoint,
    TemperatureBand,
)

__all__ = [
    "ASHRAE_2011",
    "ASHRAE_A1_ALLOWABLE",
    "ASHRAE_A1_RECOMMENDED",
    "MAGNUS",
    "SOURCES",
    "WMO_JCOMM_TR63",
    "AshraePreset",
    "Comparison",
    "MagnusConstants",
    "Source",
    "a1_preset",
    "compare_to_preset",
]


@dataclass(frozen=True)
class Source:
    """One retrieved public document."""

    source_id: str
    reference: str
    url: str
    retrieved_on: str
    sha256: str
    access_note: str


ASHRAE_2011: Final = Source(
    source_id="ASHRAE_2011",
    reference=(
        "ASHRAE TC 9.9 (2011). 2011 Thermal Guidelines for Data Processing Environments - "
        "Expanded Data Center Classes and Usage Guidance. Whitepaper prepared by ASHRAE Technical "
        "Committee 9.9, Mission Critical Facilities, Technology Spaces, and Electronic Equipment."
    ),
    url="https://telecombloger.ru/wp-content/uploads/2012/12/ASHRAE_1.pdf",
    retrieved_on="2026-11-26",
    sha256="ce23e471da0ee486ad961713ae3299ee253cc892a59129b603d3cadb935ab549",
    access_note=(
        "ASHRAE-authored whitepaper, retrieved from a third-party host because "
        "tc0909.ashraetcs.org refused the fetch (HTTP 403). The file states it may not be posted "
        "online without ASHRAE's written permission: it is cited and hashed, not committed."
    ),
)

WMO_JCOMM_TR63: Final = Source(
    source_id="WMO_JCOMM_TR63",
    reference=(
        "WMO/IOC JCOMM (2015). Recommended Algorithms for the Computation of Marine "
        "Meteorological Variables. JCOMM Technical Report No. 63 (citing WMO-No. 8)."
    ),
    url=(
        "https://old.wmo.int/extranet/pages/prog/amp/mmop/documents/JCOMM-TR/J-TR-63-Algo/"
        "JCOMM-TR-63-Algorithms.pdf"
    ),
    retrieved_on="2026-11-26",
    sha256="8fa0eb32c3d5370f5badc4bb027310836d5d3512f67b7f5393238084a39ce4d1",
    access_note="Public WMO technical report.",
)

SOURCES: Final[dict[str, Source]] = {s.source_id: s for s in (ASHRAE_2011, WMO_JCOMM_TR63)}


# --------------------------------------------------------------------------------------------
# WMO No-8 Magnus constants (JCOMM TR 63, eq. 3a, 6a, 7a; validity section I.6)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MagnusConstants:
    """``e_w(t) = a * exp(b t / (c + t))`` hPa over water, valid for ``t_min_c <= t <= t_max_c``."""

    a_hpa: float
    b: float
    c_c: float
    t_min_c: float
    t_max_c: float
    locator: str
    quote: str


MAGNUS: Final = MagnusConstants(
    a_hpa=6.112,
    b=17.62,
    c_c=243.12,
    t_min_c=-45.0,
    t_max_c=60.0,
    locator="section I.3 eq. (3a); I.4 eq. (6a); I.5 eq. (7a); I.6",
    quote=(
        "e_w(t) = 6.112 * exp(17.62 * t / (243.12 + t)) ... the dewpoint formulae are valid for "
        "the temperature range of -45°C to 60°C"
    ),
)


# --------------------------------------------------------------------------------------------
# ASHRAE 2011 Table 4: class A1, recommended and allowable
# --------------------------------------------------------------------------------------------

_TABLE4: Final = "Table 4 (2011 ASHRAE Thermal Guidelines, SI), page 8, and notes e and f"


@dataclass(frozen=True)
class AshraePreset:
    """One ASHRAE envelope as Table 4 states it, in IC-11 units (tenths of a degree, per cent).

    ``min_dew_point`` / ``min_rh`` and ``max_dew_point`` / ``max_rh`` are ``None`` where the table
    states no such limit. The rate of change is note f's: 5 °C/h with tape drives, 20 °C/h with
    disk drives (stated in the allowable columns; the recommended row is read with the same note,
    which is this package's reading, recorded in ``caveat``).
    """

    ashrae_class: AshraeClass
    envelope: Literal["recommended", "allowable"]
    min_tenths_c: int
    max_tenths_c: int
    min_dew_point_tenths_c: int | None
    max_dew_point_tenths_c: int | None
    min_rh_percent: int | None
    max_rh_percent: int | None
    rate_tape_tenths_c_per_hour: int
    rate_disk_tenths_c_per_hour: int
    locator: str
    quote: str
    caveat: str

    def source_text(self, media: Literal["tape", "disk"] | None) -> str:
        rate = {
            "tape": "note f tape-drive rate (5 °C/h)",
            "disk": "note f disk-drive rate (20 °C/h)",
            None: "note f tape-drive rate (5 °C/h), the tighter one, as the storage media are "
            "not stated",
        }[media]
        return (
            f"{ASHRAE_2011.reference} {self.locator}; class {self.ashrae_class.value} "
            f"{self.envelope}; {rate}. Retrieved {ASHRAE_2011.retrieved_on} from "
            f"{ASHRAE_2011.url} (sha256 {ASHRAE_2011.sha256}). Caveat: {self.caveat}"
        )

    def humidity(self) -> HumidityLimit | Unknown:
        """The IC-11 humidity limit, when IC-11 v1.1.0 can carry it (min and max dew point and a
        max RH). A minimum stated as an RH has no IC-11 field, so that envelope is unknown here;
        the plant model still enforces it (D167 H-a)."""
        if (
            self.min_dew_point_tenths_c is None
            or self.max_dew_point_tenths_c is None
            or self.max_rh_percent is None
            or self.min_rh_percent is not None
        ):
            return Unknown(
                kind="unknown",
                reason=(
                    f"ASHRAE {self.ashrae_class.value} {self.envelope} humidity is stated as "
                    f"{self._humidity_words()}; IC-11 v1.1.0 HumidityLimit has no minimum RH "
                    "field, so it is not representable (the model enforces it)"
                ),
            )
        return HumidityLimit(
            kind="humidity_limit",
            min_dew_point_tenths_c=self.min_dew_point_tenths_c,
            max_dew_point_tenths_c=self.max_dew_point_tenths_c,
            max_relative_humidity_percent=self.max_rh_percent,
        )

    def _humidity_words(self) -> str:
        parts = []
        if self.min_rh_percent is not None:
            parts.append(f"{self.min_rh_percent}% RH minimum")
        if self.max_rh_percent is not None:
            parts.append(f"{self.max_rh_percent}% RH maximum")
        if self.max_dew_point_tenths_c is not None:
            parts.append(f"{self.max_dew_point_tenths_c / 10:g} °C maximum dew point")
        return ", ".join(parts)

    def rate_tenths_c_per_hour(self, media: Literal["tape", "disk"] | None) -> int:
        if media == "disk":
            return self.rate_disk_tenths_c_per_hour
        return self.rate_tape_tenths_c_per_hour

    def ic11(self, media: Literal["tape", "disk"] | None) -> AshraeLimit:
        """The preset as an IC-11 `AshraeLimit` at the IT inlet."""
        return AshraeLimit(
            ashrae_class=self.ashrae_class,
            envelope=self.envelope,
            temperature=TemperatureBand(
                point=MeasurementPoint.IT_INLET,
                min_tenths_c=self.min_tenths_c,
                max_tenths_c=self.max_tenths_c,
            ),
            humidity=self.humidity(),
            max_rate_of_change_tenths_c_per_hour=self.rate_tenths_c_per_hour(media),
            source=self.source_text(media),
        )


_CAVEAT: Final = (
    "the 2011 whitepaper, not the current (5th, 2021) edition, which is paid and not held; the "
    "file came from a third-party host and is ASHRAE-authored by its own text; the altitude "
    "derating of note e (1 °C per 300 m above 950 m for A1) is not applied and must be applied "
    "by the caller for a site above 950 m"
)

ASHRAE_A1_RECOMMENDED: Final = AshraePreset(
    ashrae_class=AshraeClass.A1,
    envelope="recommended",
    min_tenths_c=180,
    max_tenths_c=270,
    min_dew_point_tenths_c=55,
    max_dew_point_tenths_c=150,
    min_rh_percent=None,
    max_rh_percent=60,
    rate_tape_tenths_c_per_hour=50,
    rate_disk_tenths_c_per_hour=200,
    locator=_TABLE4,
    quote=(
        "Recommended (Applies to all A classes ...) A1 to A4: 18 to 27; 5.5ºC DP to 60% RH and "
        "15ºC DP. f. 5°C/hr for data centers employing tape drives and 20°C/hr for data centers "
        "employing disk drives."
    ),
    caveat=_CAVEAT
    + "; the recommended row states no rate, and note f's rate is applied to it by this package",
)

ASHRAE_A1_ALLOWABLE: Final = AshraePreset(
    ashrae_class=AshraeClass.A1,
    envelope="allowable",
    min_tenths_c=150,
    max_tenths_c=320,
    min_dew_point_tenths_c=None,
    max_dew_point_tenths_c=170,
    min_rh_percent=20,
    max_rh_percent=80,
    rate_tape_tenths_c_per_hour=50,
    rate_disk_tenths_c_per_hour=200,
    locator=_TABLE4,
    quote=(
        "Allowable A1: 15 to 32; 20% to 80% RH; Maximum Dew Point 17; Maximum Rate of Change "
        "5/20 (f). f. 5°C/hr for data centers employing tape drives and 20°C/hr for data "
        "centers employing disk drives."
    ),
    caveat=_CAVEAT,
)


def a1_preset(envelope: Literal["recommended", "allowable"]) -> AshraePreset:
    """The opt-in A1 preset. There is no H1 preset: H1 is not in the held source."""
    return ASHRAE_A1_RECOMMENDED if envelope == "recommended" else ASHRAE_A1_ALLOWABLE


Comparison = Literal["matches", "tighter", "looser", "not_comparable"]


def compare_to_preset(declared: AshraeLimit) -> Comparison:
    """How a declared ASHRAE temperature band compares with the held preset for its class.

    ``not_comparable`` where no preset is held (every class but A1, H1 included) or the band is
    stated at another point. Only ``looser`` while claiming the class is a finding: a declared
    band wider than the published class band misstates the class.
    """
    if declared.ashrae_class is not AshraeClass.A1:
        return "not_comparable"
    if declared.temperature.point is not MeasurementPoint.IT_INLET:
        return "not_comparable"
    p = a1_preset(declared.envelope)
    lo, hi = declared.temperature.min_tenths_c, declared.temperature.max_tenths_c
    if (lo, hi) == (p.min_tenths_c, p.max_tenths_c):
        return "matches"
    if lo >= p.min_tenths_c and hi <= p.max_tenths_c:
        return "tighter"
    return "looser"
