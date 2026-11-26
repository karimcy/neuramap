"""Data-protection governance for GridQ (WP-GRC-05, spec E-09).

`dpa_guard`: no customer dataset is stored, or loaded, before its IC-4 DPA evidence object exists.
`customer_data`: the committed manifest of customer datasets in git, and the scan that checks it.
``python -m gridq.governance.dpa_scan`` lists customer datasets in git without DPA evidence.
"""

from gridq.governance.dpa_guard import (
    DEMO_SITE_ID,
    DPA_SIDECAR,
    EVIDENCE_DIR_ENV,
    SYNTHETIC_SITE_PREFIX,
    DpaEvidenceMissing,
    DpaPermit,
    EvidenceRegistryError,
    LoadDecision,
    TransientWorkspace,
    authorise_store,
    check_load,
    load_evidence_registry,
    require_dpa,
    write_dpa_sidecar,
)

__all__ = [
    "DEMO_SITE_ID",
    "DPA_SIDECAR",
    "EVIDENCE_DIR_ENV",
    "SYNTHETIC_SITE_PREFIX",
    "DpaEvidenceMissing",
    "DpaPermit",
    "EvidenceRegistryError",
    "LoadDecision",
    "TransientWorkspace",
    "authorise_store",
    "check_load",
    "load_evidence_registry",
    "require_dpa",
    "write_dpa_sidecar",
]
