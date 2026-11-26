"""The published sources `dc_load_model` may quote, and the quotes it relies on.

The data rule for this package (WP-SITE-01, CLAUDE.md §2 and the work-package brief) is that every
parameter is either a user input with no default, or an opt-in preset cited to an open,
peer-reviewed or official published source that was retrieved and is quoted here. No field anywhere
in the package has a numeric default. A preset is a named constructor that returns a stated
distribution together with the `Citation` below; using one is a visible choice recorded on the
output.

Each source was retrieved on 2026-11-26. The SHA-256 is of the file as retrieved, so a later reader
can tell whether they hold the same document.

Sources named here but **not** used for any number: S2 and S3 support the model's *structure* only
(decision D81). ASHRAE TC 9.9 and ASHRAE Guideline 14 are paywalled, not held, and not
quoted anywhere in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["S1", "S2", "S2_EQ3", "S3", "SOURCES", "Citation", "Source"]


@dataclass(frozen=True)
class Source:
    """One retrieved, open document."""

    source_id: str
    reference: str
    url: str
    retrieved_on: str
    sha256: str
    licence_or_access: str


class Citation(BaseModel):
    """A preset's provenance: the source, where in it, and the exact words relied on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    #: How far the quoted figure carries, in plain words: what it is evidence of, and what this
    #: package added to it (a distribution shape on a bare range, a use as a bound). ``None`` only
    #: where the quote is used for structure and no number is taken from it.
    caveat: str | None

    def render(self) -> str:
        src = SOURCES[self.source_id]
        text = f'{src.reference}, {self.locator}: "{self.quote}"'
        return text if self.caveat is None else f"{text} Caveat: {self.caveat}"


S1: Final = Source(
    source_id="S1",
    reference=(
        "Shehabi, A., Smith, S.J., Hubbard, A., Newkirk, A., Lei, N., Siddik, M.A.B., Holecek, B., "
        "Koomey, J., Masanet, E., Sartor, D. (2024). 2024 United States Data Center Energy Usage "
        "Report. Lawrence Berkeley National Laboratory, LBNL-2001637. doi:10.71468/P1WC7Q"
    ),
    url="https://escholarship.org/uc/item/32d6m0d1",
    retrieved_on="2026-11-26",
    sha256="709618b4161a03b859ac6667f821ff98f3f931055c614406bb46efb2b13850ac",
    licence_or_access=(
        "Open access (eScholarship, marked 'Peer reviewed'). A copy on eta-publications.lbl.gov "
        "carries an embargo banner; it was not used."
    ),
)

S2: Final = Source(
    source_id="S2",
    reference=(
        "Zhang, Y., Liu, J. (2022). Prediction of Overall Energy Consumption of Data Centers in "
        "Different Locations. Sensors 22(10):3704. doi:10.3390/s22103704"
    ),
    url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9145178/",
    retrieved_on="2026-11-26",
    sha256="",
    licence_or_access="Open access, CC BY 4.0. Read as HTML; no file was kept, so no hash.",
)

S3: Final = Source(
    source_id="S3",
    reference=(
        "Chen, L., Wemhoff, A.P. (2023). The sustainability benefits of economization in data "
        "centers containing chilled water systems. Resources, Conservation & Recycling 196:107053"
    ),
    url="https://par.nsf.gov/servlets/purl/10435467",
    retrieved_on="2026-11-26",
    sha256="be2b725cedf5e3eb2758ba1e3f2a57285bff93ee11e0dec8d09166c829af6327",
    licence_or_access="NSF Public Access Repository copy of the published article.",
)

SOURCES: Final[dict[str, Source]] = {s.source_id: s for s in (S1, S2, S3)}

#: S2, Eq. (3), the structure the facility-power sum follows (no number is taken from S2).
S2_EQ3: Final = Citation(
    source_id="S2",
    locator="section 3.1, Eq. (3)",
    quote="pDC = pIT + (1−ηUPS)pUPS + αPDpPD + pL + ∑fpfFAN + ∑pppPUMP + pCH",
    caveat=None,
)
