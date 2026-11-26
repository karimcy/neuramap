"""IC-12 (roadmap NEW-IC-12): the GridQ chassis named as a frozen interface contract.

The chassis is the three models every Assess workstream couples through: `SiteModel`,
`OptimisationResult` and `QuoteRecord`. They stay where they have always lived
(`gridq.schemas`, on the `GridQModel` base); this module names them as one contract, states its
version, and lists the models whose JSON Schemas ``python -m gridq.contracts --check`` pins.

Version 1.0.0 is the chassis as it stood at the ``gridq_clean_baseline`` tag. It was frozen by
convention only (CLAUDE.md §7); WP-CORE-03 rendered its schemas so every later version has a
byte-pinned predecessor to be compared with.

Version 2.0.0 (release ``gridq_chassis_v2``) adds, to all three models, a typed IC-1 connection
reference and IC-4 evidence references (`gridq.schemas.references`), and opens four extension
registries (`gridq.extensions`). Every added field is optional and is absent from the dump while
unset, so a 2.0.0 reader reads every 1.0.0 payload with the same meaning and the same hashes. A
1.0.0 reader refuses a payload that sets a 2.0.0 field, which is correct: it could not honour it.
It is a major version because the *contract* for connection terms moved: at 1.0.0 it was the
free-text ``anm_or_curtailment_notes``; at 2.0.0 that field is commentary and the terms come only
from the referenced IC-1 agreement.

Version 2.1.0 (WP-UW-03, D79) is an additive minor of 2.0.0, and only `QuoteRecord`'s schema
moved: the `FinancialCase` it embeds gained the typed lender terms (`LenderTerms`, D75) and the
DSCR status, covenant-tier and debt-sizing members of D75 to D77. Every added member is optional
and defaults to ``None``; nothing became required, and no existing member, type or definition was
removed or changed except two descriptions. Every 2.0.0 payload is a valid 2.1.0 payload, and no
identity hash moves (D78). Unlike the 2.0.0 members these are dumped as ``null`` while unset, so a
2.0.0 reader refuses a 2.1.0 record that carries a financial case. The `SiteModel` and
`OptimisationResult` schemas are byte-identical at 2.1.0. The release name stays
``gridq_chassis_v2``.

Version 3.0.0 (release ``gridq_chassis_v3``, WP-UW-07, D103/D104) is rendered on 2.1.0 and is a
major because two members became **required**: every `TariffStack` states its `TariffBasis`
(whether its £ figures include VAT and at what rate, whether levies and network charges are
itemised or inside the unit rates, and how the VAT basis is known) and its `TariffProvenance`
(whose rates each of commodity, network and levies are). A 2.x site payload (2.0.0 or 2.1.0, whose
`SiteModel` schemas are byte-identical) has neither, so it is refused, never read with a guessed
basis: a VAT-inclusive invoice rate read as net overstates every saving by the VAT in it.
`upgrade_site_2_to_3` lifts one, but only with a basis and a provenance the caller states, plus the
`DerivedAssumption` when the VAT basis is derived. Added beside them, all optional and absent from a
dump while unset: ``TariffStack.network_tariff`` (a published CDCM or EDCM row),
``.import_rate_windows`` and ``.evidence``, and ``CustomerBill.reactive_before_gbp`` /
``.reactive_after_gbp``. `QuoteRecord` keeps every 2.1.0 member (the lender terms and DSCR status
of D79). Strip the two required members (and the VAT record) from a 3.0.0 dump of any committed
site and it hashes to its 2.x bytes, so nothing else about any identity moved. The
`OptimisationResult` schema is byte-identical to 2.0.0 and 2.1.0, and every 2.1.0 `QuoteRecord`
and `OptimisationResult` payload is a valid 3.0.0 one with the same bytes. A 2.x reader refuses a
3.0.0 site, which is correct: it could not honour the basis.

Version 3.1.0 (WP-UW-17, D128 to D132) is an additive minor of 3.0.0. WP-UW-17 released these
members as 2.2.0 on a branch cut before 3.0.0; on this line they are 3.1.0. Again only
`QuoteRecord`'s schema moved: the `FinancialCase` it embeds gained the typed lifecycle inputs and
the derived lifecycle record (`gridq.schemas.lifecycle`), and its `PriceRecommendation` gained the
levelisation basis, the measured IRR with the price and the equity closed-loop price. Every added
member is optional and defaults to ``None``; nothing became required and no existing member,
type or definition was removed or changed. ``lifecycle_inputs`` is dropped from the canonical
dump while unset, and the derived members are excluded from the case identity, so no existing
identity hash moves. The `SiteModel` and `OptimisationResult` schemas are byte-identical at
3.1.0, so a 3.0.0 site is a 3.1.0 site. The release name stays ``gridq_chassis_v3``.

The contract version is **not** `gridq.schemas.SCHEMA_VERSION`. That string is stamped on every
instance and enters every ``run_id``, so moving it would re-mint every identity in the repo. The
contract version is carried here and in the schema file names, the way `SizingGrid` versions its
own additive growth locally (D52).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel

from gridq.schemas.derived import DerivedAssumption
from gridq.schemas.optimisation import OptimisationResult
from gridq.schemas.quote import QuoteRecord
from gridq.schemas.site import SiteModel
from gridq.schemas.tariff import TariffBasis, TariffProvenance

__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "FROZEN_MODELS",
    "GIT_TAG",
    "RELEASE",
    "SUPPORTED_VERSIONS",
    "upgrade_site_2_to_3",
]

CONTRACT_ID: Final = "IC-12"
CONTRACT_VERSION: Final = "3.1.0"
#: Versions this code reads. It writes the last. A 2.x *site* (2.0.0 or 2.1.0: the same site
#: schema) is read only through `upgrade_site_2_to_3`, which needs the caller's statement of its
#: tariff basis; a 2.1.0 `QuoteRecord` or `OptimisationResult` is a 3.0.0 one, byte for byte, and
#: every 3.0.0 payload is a 3.1.0 one (an additive minor, WP-UW-17).
SUPPORTED_VERSIONS: Final = ("1.0.0", "2.0.0", "2.1.0", "3.0.0", "3.1.0")
GIT_TAG: Final = "contracts/ic12-chassis/v3.1.0"
#: The release this version belongs to (roadmap data product, WP-CORE-03); a major names a new one
#: and a minor keeps it.
RELEASE: Final = "gridq_chassis_v3"

#: The `DerivedAssumption` key a derived VAT basis is recorded on (`gridq.tariffs.basis`).
_VAT_BASIS_KEY: Final = "tariff_stack.basis.vat"

#: (schema file stem, model) for each chassis model, in dependency order.
FROZEN_MODELS: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("ic12_site_model", SiteModel),
    ("ic12_optimisation_result", OptimisationResult),
    ("ic12_quote_record", QuoteRecord),
)


def upgrade_site_2_to_3(
    payload: Mapping[str, Any],
    *,
    basis: TariffBasis,
    provenance: TariffProvenance,
    vat_record: DerivedAssumption | None = None,
) -> SiteModel:
    """Lift a 2.x `SiteModel` payload to 3.0.0 with the basis and provenance the caller states.

    2.x is 2.0.0 or 2.1.0: 2.1.0 changed only `QuoteRecord`, so the two site schemas are the same
    file byte for byte and one upgrader serves both.

    Never guesses. The two members 3.0.0 requires are statements about the site's tariff that only
    its record can make, so they are arguments, not defaults: there is no "assume excluded" path.
    When ``basis.vat_attestation`` is ``"derived"`` the derivation must come with its
    `DerivedAssumption` on ``tariff_stack.basis.vat``, appended to the site's records; a record
    for a basis that is stated, or a payload that already states a basis, is refused. The result is
    validated, so a lifted site satisfies every 3.0.0 rule (D103/D104).
    """
    lifted: dict[str, Any] = copy.deepcopy(dict(payload))
    stack = lifted.get("tariff_stack")
    if not isinstance(stack, dict):
        raise ValueError("a 2.x site payload carries a tariff_stack object; this one does not")
    already = sorted(k for k in ("basis", "provenance") if k in stack)
    if already:
        raise ValueError(
            f"tariff_stack already states {already}: this is not a 2.x payload, and an upgrade "
            "never overwrites a stated basis"
        )
    derived = basis.vat_attestation == "derived"
    if derived and vat_record is None:
        raise ValueError(
            "basis.vat_attestation is 'derived', so the upgrade needs the DerivedAssumption that "
            f"records the derivation (key {_VAT_BASIS_KEY!r})"
        )
    if not derived and vat_record is not None:
        raise ValueError("a vat_record was given for a VAT basis the record states, not derives")
    stack["basis"] = basis.model_dump(mode="json")
    stack["provenance"] = provenance.model_dump(mode="json")
    if vat_record is not None:
        if vat_record.key != _VAT_BASIS_KEY:
            raise ValueError(f"vat_record must be keyed {_VAT_BASIS_KEY!r}, not {vat_record.key!r}")
        records = list(lifted.get("derived_assumptions") or [])
        records.append(vat_record.model_dump(mode="json"))
        lifted["derived_assumptions"] = records
    return SiteModel.model_validate(lifted)
