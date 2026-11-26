"""Site deployment and operations data products (roadmap workstream WS-OPS).

`dossier`: WP-OPS-01, the candidate site dossier (`candidate_site_dossier`). Every candidate
demonstrator site is characterised by connection type and IC-1 connection terms, connecting DNO,
DNO licence area, metering class, net export, gensets and on-site generation, cooling topology,
and BESS owner and funder. Each field is evidenced, or unknown with its data ask. It is the
evidence input to gate D-4 and makes no go/no-go statement.

``python -m gridq.ops.dossier`` builds the owner-side dossier into a git-ignored directory.

`demonstrator_mw`: WP-OPS-11, the demonstrator MW options pack (`demonstrator_mw_options`). The
three candidate definitions (site load under management, BESS MW actively dispatched, verified
flexible MW) are typed formula templates (`mw_formula`, `mw_definitions`) with every open parameter
a slot; each term is bound to a frozen contract field, a dossier field or "not yet measurable" with
the roadmap product that will measure it (`mw_bindings`). The pack quotes the grant texts in place
(`mw_grant_text`), gives a worked count per site from evidenced dossier fields, and carries a draft
agreement text (`mw_draft`). It recommends no definition; agreement is WP-OPS-02.

``python -m gridq.ops.demonstrator_mw`` builds the worked count into a git-ignored directory;
``--write-pack`` renders ``docs/ops/demonstrator_mw_options.md``.
"""

from typing import TYPE_CHECKING, Any

from gridq.ops.dossier_inputs import (
    CandidateEntry,
    CandidateRegister,
    DossierInputError,
    FieldTranscription,
    default_register,
)
from gridq.ops.dossier_model import (
    NO_VERDICT_STATEMENT,
    CandidateSiteDossier,
    DossierAsk,
    DossierField,
    SiteDossier,
)

if TYPE_CHECKING:
    from gridq.ops.dossier import build_dossier, render_markdown, write_dossier


def __getattr__(name: str) -> Any:
    # The builder is imported lazily so ``python -m gridq.ops.dossier`` runs the module once.
    if name in ("build_dossier", "render_markdown", "write_dossier"):
        from gridq.ops import dossier

        return getattr(dossier, name)
    raise AttributeError(f"module 'gridq.ops' has no attribute {name!r}")


__all__ = [
    "NO_VERDICT_STATEMENT",
    "CandidateEntry",
    "CandidateRegister",
    "CandidateSiteDossier",
    "DossierAsk",
    "DossierField",
    "DossierInputError",
    "FieldTranscription",
    "SiteDossier",
    "build_dossier",
    "default_register",
    "render_markdown",
    "write_dossier",
]
