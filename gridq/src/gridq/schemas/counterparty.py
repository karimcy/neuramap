"""The site's counterparty (the customer/offtaker behind the credit assessment)."""

from __future__ import annotations

from pydantic import Field

from gridq.schemas.enums import CreditStatus, LegalEntityType
from gridq.schemas.provenance import GridQModel


class Counterparty(GridQModel):
    """Identity and coarse credit standing of the counterparty.

    The deterministic credit-score -> rate/tenor mapping lives in the finance layer (M9);
    this only records identity and a coarse status.
    """

    legal_name: str
    company_number: str | None = None
    legal_entity_type: LegalEntityType = LegalEntityType.UNKNOWN
    sector: str | None = None
    credit_status: CreditStatus = CreditStatus.UNKNOWN
    expected_tenure_years: float | None = Field(default=None, ge=0)
    notes: str | None = None
