"""Frozen cross-workstream interface contracts (spec §10A).

- IC-4 ``EvidenceObject``: `gridq.contracts.evidence`, version 1.2.0 (reads 1.1.0 and 1.0.0),
  git tag ``contracts/ic4-evidence-object/v1.2.0``.
- IC-1 ``ConnectionAgreement``: `gridq.contracts.connection`, version 1.1.0 (reads 1.0.0),
  git tag ``contracts/ic1-connection-agreement/v1.1.0``.
- IC-2 ``CurtailmentScenarioSet``: `gridq.contracts.curtailment`, version 1.1.0 (reads 1.0.0),
  git tag ``contracts/ic2-curtailment-scenario-set/v1.1.0``.
- IC-13 (roadmap NEW-IC-13) identity claims and audit events: `gridq.contracts.identity` and
  `gridq.contracts.audit`, version 1.0.0, git tag ``contracts/ic13-identity-audit/v1.0.0``.
- IC-11 (roadmap NEW-IC-11) thermal flex availability, flex instruction and acknowledgement:
  `gridq.contracts.thermal_flex`, version 1.1.0 (reads 1.0.0), git tag
  ``contracts/ic11-thermal-flex/v1.1.0``.
- IC-3 ``DispatchEnvelope`` and its compiled ``ResolvedTimeline``:
  `gridq.contracts.dispatch_envelope`, version 1.0.0, git tag
  ``contracts/ic3-dispatch-envelope/v1.0.0``.
- IC-5 edge ↔ cloud protocol (frames, link profile, setpoint schedules, acknowledgements,
  fail-safe trigger codes, latency budget; ANM local-only): `gridq.contracts.edge_cloud`,
  version 1.0.0, git tag ``contracts/ic5-edge-cloud/v1.0.0``.
- IC-12 (roadmap NEW-IC-12) the chassis, `SiteModel`, `OptimisationResult` and `QuoteRecord`:
  the models are in `gridq.schemas` and the version in `gridq.schemas.chassis`, version 3.1.0
  (reads 1.0.0, 2.0.0, 2.1.0 and 3.0.0; a 2.x site only through ``upgrade_site_2_to_3``), git tag
  ``contracts/ic12-chassis/v3.1.0``. Only the checker (``__main__``) reads them.
- IC-15 (roadmap NEW-IC-15) contract template set and executed contract terms:
  `gridq.contracts.contract_templates`, version 1.0.0, git tag
  ``contracts/ic15-contract-template-set/v1.0.0``. Every template is a draft that needs legal
  review.
- IC-9 compute turn-down declaration (WP-SITE-07, B8-01): `gridq.contracts.compute_turndown`,
  version 1.0.0, git tag ``contracts/ic9-compute-turndown/v1.0.0``. It also publishes a generated
  human-readable specification, ``specs/ic9_compute_turndown_declaration.v1.0.0.md``.
- IC-14 (roadmap NEW-IC-14) telemetry series, the raw and curated tiers of site telemetry
  (WP-TEL-01): `gridq.contracts.telemetry_series`, version 1.0.0, git tag
  ``contracts/ic14-telemetry-series/v1.0.0``, with a generated specification
  ``specs/ic14_telemetry_series.v1.0.0.md``. A missing sample is a gap with a reason, never a
  zero. Quote-cost telemetry (`gridq.telemetry`, X.3) is a different thing.

Each version's JSON Schema is committed under ``schemas/`` and a conformance test fails if the
model drifts from it. A superseded version's schema is archived beside it, pinned by SHA-256
(``__main__.ARCHIVED``). Changing a contract means a new version, a new schema file and a
changelog entry (``CHANGELOG.md`` beside this file), never an in-place edit. Regenerate / check
with ``python -m gridq.contracts --check`` (or ``--write`` after a deliberate version bump).

IC-4 evidence is third-party by default (ADR-D10 option b): `require_third_party` and
`resolve_ref` refuse a record Neura issued itself unless the caller admits it, by type.

These modules are a leaf: they import nothing from GridQ outside this package.
"""

from __future__ import annotations

from gridq.contracts._base import (
    NotApplicable,
    Unknown,
    canonical_json,
    content_hash,
    not_applicable,
    unknown,
)
from gridq.contracts.audit import AuditEvent, verify_chain
from gridq.contracts.compute_turndown import ComputeTurndownDeclaration
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.contract_templates import ContractTemplateSet, ExecutedContractTerms
from gridq.contracts.curtailment import CurtailmentScenarioSet
from gridq.contracts.dispatch_envelope import DispatchEnvelope, ResolvedTimeline
from gridq.contracts.edge_cloud import CloudToEdgeFrame, EdgeToCloudFrame, LinkProfile
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceRef,
    EvidenceStatus,
    assess,
    require_third_party,
    resolve_ref,
)
from gridq.contracts.identity import IdentityClaims, validate_token
from gridq.contracts.telemetry_series import (
    CuratedSeries,
    DqAnnotation,
    SeriesDescriptor,
    TelemetryBatch,
)
from gridq.contracts.thermal_flex import (
    FlexAcknowledgement,
    FlexInstruction,
    ThermalFlexAvailability,
)

__all__ = [
    "AuditEvent",
    "CloudToEdgeFrame",
    "ComputeTurndownDeclaration",
    "ConnectionAgreement",
    "ContractTemplateSet",
    "CuratedSeries",
    "CurtailmentScenarioSet",
    "DispatchEnvelope",
    "DqAnnotation",
    "EdgeToCloudFrame",
    "EvidenceObject",
    "EvidenceRef",
    "EvidenceStatus",
    "ExecutedContractTerms",
    "FlexAcknowledgement",
    "FlexInstruction",
    "IdentityClaims",
    "LinkProfile",
    "NotApplicable",
    "ResolvedTimeline",
    "SeriesDescriptor",
    "TelemetryBatch",
    "ThermalFlexAvailability",
    "Unknown",
    "assess",
    "canonical_json",
    "content_hash",
    "not_applicable",
    "require_third_party",
    "resolve_ref",
    "unknown",
    "validate_token",
    "verify_chain",
]
