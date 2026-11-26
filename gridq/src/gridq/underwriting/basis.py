"""`underwriting_basis_v1`: the evidence-gated study basis (WP-UW-01).

It is registered beside the academic basis (``all_streams_enabled_study_v1``, D55) through
`gridq.extensions` and meets the `gridq.schemas.study.NamedBasis` contract. It is a separate,
named basis rather than a flag, so a run on it can never share a ``run_id`` with an academic run:
the whole basis enters ``input_hash`` (its ``basis_id`` differs), and so do the plug-in's owner
and version (``study_basis_extension``).

Every field that can change a number is on the basis, so every one of them is in ``input_hash``:
the date evidence is assessed on, whether synthetic test fixtures may stand in for evidence, and
the hash of the rule table the gates read.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar, Final, Literal

from pydantic import ConfigDict, Field, model_validator

from gridq.schemas import ConfidenceBand, GridQModel
from gridq.underwriting.rules import RULE_TABLE_HASH, RULE_TABLE_VERSION

__all__ = [
    "BASIS_ID",
    "SYNTHETIC_LABEL",
    "UnderwritingBasis",
]

BASIS_ID: Final = "underwriting_basis_v1"

#: Printed on every artefact of a run whose basis admits synthetic test fixtures (D97).
SYNTHETIC_LABEL: Final = "SYNTHETIC TEST FIXTURES — NOT EVIDENCE"

EvidenceAdmission = Literal["registered_only", "synthetic_fixtures_test_only"]


class UnderwritingBasis(GridQModel):
    """The evidence-gated basis. A market stream enters the priced floor only on evidence.

    What the basis enforces, through `gridq.underwriting.gate` and `gridq.underwriting.engine`:

    - **Gates and consents.** Each market stream needs the IC-4 evidence objects its row of the
      rule table names (registration, route-to-market agreement, customer consent, export
      permission, contract). Evidence is admitted only when it is VALID on ``as_of`` and verified;
      anything less holds the stream out of the floor as conditional upside, with the unlock named.
    - **Size minimums.** The site's connection-bounded MW is compared with the quoted market
      minimum; below it, only an evidenced aggregation route admits the stream.
    - **Stacking.** A published exclusivity rule demotes the stream the rule binds.
    - **Route to market.** The route the product spec names must be evidenced by an agreement.

    Bill-side savings (tariff arbitrage, DUoS avoidance) have no counterparty and are not
    evidence-gated; the LABEL says so.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    BASIS_ID: ClassVar[str] = BASIS_ID
    LABEL: ClassVar[str] = (
        "STUDY BASIS (UNDERWRITING_BASIS_V1) — EVIDENCE-GATED, MARKET STREAMS ENTER THE FLOOR ONLY "
        "ON VALID VERIFIED THIRD-PARTY EVIDENCE, BILL-SIDE SAVINGS ARE NOT EVIDENCE-GATED — NOT "
        "YET A LENDER-READY OUTPUT"
    )

    basis_id: Literal["underwriting_basis_v1"]
    authorised_by: str = Field(min_length=1)
    #: The date every evidence object's validity is assessed on. Required, with no wall-clock
    #: default: expiry is a hashed input, so the same inputs give the same run (D96).
    as_of: date
    #: ``registered_only`` refuses synthetic fixtures, as the IC-4 README requires.
    #: ``synthetic_fixtures_test_only`` admits them and labels every artefact (D97).
    evidence_admission: EvidenceAdmission
    rule_table_version: str = RULE_TABLE_VERSION
    #: The content hash of the rule table this run was gated by. A basis built against another
    #: table is refused, so a changed rule is a new run identity and never a silent change.
    rule_table_hash: str = RULE_TABLE_HASH
    confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

    @model_validator(mode="after")
    def _check(self) -> UnderwritingBasis:
        if not self.authorised_by.strip():
            raise ValueError("underwriting_basis_v1 requires a non-blank authorised_by")
        if self.rule_table_hash != RULE_TABLE_HASH or self.rule_table_version != (
            RULE_TABLE_VERSION
        ):
            raise ValueError(
                "this basis names rule table "
                f"{self.rule_table_version} ({self.rule_table_hash}), but the installed table is "
                f"{RULE_TABLE_VERSION} ({RULE_TABLE_HASH}). A run is gated by the table it names; "
                "re-run on the installed table instead."
            )
        if self.confidence_ceiling is not ConfidenceBand.LOW:
            raise ValueError(
                "underwriting_basis_v1 caps the confidence band at LOW (D98): it is not "
                "yet a lender-ready output, so it may not declare a higher ceiling."
            )
        return self

    @property
    def admits_synthetic(self) -> bool:
        return self.evidence_admission == "synthetic_fixtures_test_only"

    def note(self) -> str:
        """The sentence leading every artefact. Starts ``f"{LABEL}. "`` (the renderer contract)."""
        parts = [f"{self.LABEL}."]
        if self.admits_synthetic:
            parts.append(
                f"{SYNTHETIC_LABEL}: this run admits synthetic schema-test fixtures in place of "
                "evidence, so no figure on it may be read as evidenced."
            )
        parts.append(
            f"Basis chosen by {self.authorised_by.strip()}; IC-4 evidence assessed as of "
            f"{self.as_of.isoformat()} against rule table {self.rule_table_version} "
            f"({self.rule_table_hash})."
        )
        parts.append(
            "A market stream is priced into the floor only when every evidence object its row "
            "requires is referenced by the site, held, VALID on that date and verified; "
            "otherwise it is conditional upside with its unlock named, or excluded at GBP 0 where "
            "IC-4 has no type for the document."
        )
        parts.append(
            "The D57 assumed-approval register is NOT adopted on this basis: no consent, award, "
            "registration or stacking permission is assumed. A floor holding two or more "
            "capacity services that share the inverter budget is demoted to conditional until "
            "WP-SIZE-03 enforces the shared budget in the solver."
        )
        parts.append(
            "KNOWN LIMITATION: the dispatch objective still co-optimises the Demand Flexibility "
            "Service ungated, so the sized battery may carry DFS-shaped dispatch that this basis "
            "then holds out of the floor (solver-level gating is WP-SIZE-03)."
        )
        parts.append(
            f"This run's confidence band is capped at {self.confidence_ceiling.value.upper()} and "
            "its result is held not bankable, because the basis is not yet lender-ready."
        )
        return " ".join(parts)
