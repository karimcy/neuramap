"""Data contracts — the shared language of the codebase.

Pydantic v2 models for the whole pipeline (SiteModel -> Optimisation -> Finance ->
Quote -> Telemetry). Field-level spec: docs/03_data_contracts.md. Every model inherits
`GridQModel` (forbids unknown fields; carries `schema_version`). No pricing logic here.
"""

from __future__ import annotations

from gridq.schemas.commitment import (
    AvailabilityCommitment,
    AvailabilityWindow,
    CommitmentEvidence,
)
from gridq.schemas.confidentiality import Confidentiality
from gridq.schemas.connection import ConnectionDetails
from gridq.schemas.consents import ConsentState
from gridq.schemas.constraint_library import ConstraintLibraryRef
from gridq.schemas.counterparty import Counterparty
from gridq.schemas.customer_bill import CustomerBill
from gridq.schemas.derived import (
    SHAPE_LIKE_KEYS,
    STRUCTURAL_KEYS,
    DerivedAssumption,
    ceiling_for_key,
)
from gridq.schemas.dispatch_profile import DispatchProfile, DispatchProfilePoint
from gridq.schemas.enums import (
    BankableStatus,
    Confidence,
    ConfidenceBand,
    ConsentStatus,
    CreditStatus,
    DataTier,
    ExportRoute,
    Firmness,
    HalfHourlyBasis,
    ImplementationLevel,
    LegalEntityType,
    MarketStream,
    NdaStatus,
    PriceStructure,
    QuoteMode,
    QuoteOutcome,
    SelectionStatus,
    SolveMode,
    SolverStatus,
    VoltageLevel,
)
from gridq.schemas.export_arrangement import ExportArrangement
from gridq.schemas.finance import (
    CapitalRecoveryAccount,
    CovenantStatus,
    DebtAssumptions,
    DscrSummary,
    FinancialCase,
    PriceRecommendation,
)
from gridq.schemas.lender_terms import (
    AmortisationProfile,
    CalculationPeriod,
    CashflowCase,
    DscrCovenantTerms,
    DscrStatus,
    LenderTerms,
    LenderTermsProvenance,
    LenderTermsSource,
)
from gridq.schemas.lifecycle import (
    CapacityAgreementIndexation,
    CashflowLineBasis,
    CmAuctionType,
    ConstructionPhase,
    ConstructionRecord,
    ConstructionStatus,
    DatedFlowEntry,
    Decommissioning,
    DrawdownPoint,
    IndexationRecord,
    LifecycleFlow,
    LifecycleFlowEntry,
    LifecycleFlowStatus,
    LifecycleInputs,
    LifecycleProvenance,
    LifecycleRecord,
    LifecycleSource,
    LineBasisEntry,
    PriceBasis,
    Replacement,
    ReplacementComponent,
    ReturnBasis,
    TerminalValue,
    TerminalValueBasis,
)
from gridq.schemas.market import (
    GATED_UPSIDE_STREAMS,
    PRICED_FLOOR_FIRMNESS,
    ValueStreamResult,
    is_priced_floor,
)
from gridq.schemas.optimisation import (
    OptimisationRequest,
    OptimisationResult,
    SolverMetadata,
)
from gridq.schemas.provenance import SCHEMA_VERSION, GridQModel, Provenance
from gridq.schemas.provisioning import ProvisioningRequirement
from gridq.schemas.quote import (
    T0_UNDERWRITING_ACKNOWLEDGEMENT,
    QuoteRecord,
    ScenarioSensitivity,
    StatusHistoryEntry,
)
from gridq.schemas.references import (
    ConnectionAgreementRef,
    EvidenceLink,
    EvidenceRef,
)
from gridq.schemas.site import LoadDataSummary, PhysicalGateStatus, SiteModel
from gridq.schemas.sizing import (
    SIZING_GRID_SCHEMA_VERSION,
    SizingGrid,
    SizingGridPoint,
    SizingGridStreamRevenue,
)
from gridq.schemas.stream_consent import RateUnit, RevenueStreamConsent
from gridq.schemas.study import (
    STUDY_BASIS_CLASSES,
    STUDY_BASIS_LABELS,
    AllStreamsEnabledStudyBasis,
    NamedBasis,
    StudyBasis,
    study_basis_label,
)
from gridq.schemas.supply import SupplyBoundary
from gridq.schemas.tariff import (
    BandWindows,
    CdcmTariff,
    ClockWindow,
    EdcmTariff,
    TariffBasis,
    TariffProvenance,
    TariffStack,
    WindowedLevy,
    parse_clock_minute,
)
from gridq.schemas.telemetry import TelemetryEvent

__all__ = [
    # base / provenance
    "SCHEMA_VERSION",
    "GridQModel",
    "Provenance",
    # enums
    "DataTier",
    "MarketStream",
    "Firmness",
    "QuoteMode",
    "ImplementationLevel",
    "BandWindows",
    "BankableStatus",
    "SolverStatus",
    "SelectionStatus",
    "SolveMode",
    "ConsentStatus",
    "VoltageLevel",
    "ExportRoute",
    "CreditStatus",
    "Confidence",
    "PriceStructure",
    "LegalEntityType",
    "NdaStatus",
    "QuoteOutcome",
    # validation Gate 2 basis + confidence band (persisted on the QuoteRecord — N21)
    "HalfHourlyBasis",
    "ConfidenceBand",
    # site + components
    "SiteModel",
    "LoadDataSummary",
    "PhysicalGateStatus",
    "CdcmTariff",
    "ClockWindow",
    "TariffBasis",
    "TariffProvenance",
    "TariffStack",
    "WindowedLevy",
    "parse_clock_minute",
    "ConnectionDetails",
    "ConsentState",
    "Counterparty",
    "Confidentiality",
    "SupplyBoundary",
    # export / route-to-market arrangement (issue 018 / WS-A4; carries WS-C's aggregator cut)
    "ExportArrangement",
    # market
    "ValueStreamResult",
    "RateUnit",
    "RevenueStreamConsent",
    "GATED_UPSIDE_STREAMS",
    # optimisation
    "OptimisationRequest",
    "OptimisationResult",
    "SolverMetadata",
    # finance
    "FinancialCase",
    "CapitalRecoveryAccount",
    "DscrSummary",
    "DebtAssumptions",
    "PriceRecommendation",
    "CovenantStatus",
    # lender terms + the DSCR status vocabulary (WP-UW-03, D75)
    "LenderTerms",
    "LenderTermsProvenance",
    "LenderTermsSource",
    "DscrCovenantTerms",
    "AmortisationProfile",
    "CashflowCase",
    "CalculationPeriod",
    "DscrStatus",
    # lifecycle inputs + the derived lifecycle record (WP-UW-17)
    "LifecycleInputs",
    "LifecycleProvenance",
    "LifecycleSource",
    "ConstructionPhase",
    "DrawdownPoint",
    "CapacityAgreementIndexation",
    "CmAuctionType",
    "TerminalValue",
    "TerminalValueBasis",
    "Decommissioning",
    "Replacement",
    "ReplacementComponent",
    "PriceBasis",
    "LifecycleRecord",
    "IndexationRecord",
    "ConstructionRecord",
    "ConstructionStatus",
    "ReturnBasis",
    "DatedFlowEntry",
    "LifecycleFlow",
    "LifecycleFlowEntry",
    "LifecycleFlowStatus",
    "LineBasisEntry",
    "CashflowLineBasis",
    # sizing grid (the 3D surface's persisted data source)
    "SIZING_GRID_SCHEMA_VERSION",
    "PRICED_FLOOR_FIRMNESS",
    "is_priced_floor",
    "SizingGrid",
    "SizingGridPoint",
    "SizingGridStreamRevenue",
    # dispatch profile (the dispatch chart's persisted data source)
    "DispatchProfile",
    "DispatchProfilePoint",
    # customer bill before/after (the proposal headline's persisted data source — C017/W7-5)
    "CustomerBill",
    # availability commitments (the firming-contract shape — W7-1/W7-4, C026)
    "AvailabilityCommitment",
    "AvailabilityWindow",
    "CommitmentEvidence",
    # power provisioning (the load-serving obligation shape — issue 017, C033)
    "ProvisioningRequirement",
    # recorded derived assumptions — the never-block contract (D44)
    "DerivedAssumption",
    "ceiling_for_key",
    "STRUCTURAL_KEYS",
    "SHAPE_LIKE_KEYS",
    # the named feasibility-study basis that prices export into the floor (D46)
    "AllStreamsEnabledStudyBasis",
    "StudyBasis",
    "NamedBasis",
    "ConnectionAgreementRef",
    "ConstraintLibraryRef",
    "EdcmTariff",
    "EvidenceLink",
    "EvidenceRef",
    # the id -> basis / id -> banner maps a consumer reads off `QuoteRecord.study_basis_id`
    "STUDY_BASIS_CLASSES",
    "STUDY_BASIS_LABELS",
    "study_basis_label",
    # quote + telemetry
    "QuoteRecord",
    "ScenarioSensitivity",
    "StatusHistoryEntry",
    "T0_UNDERWRITING_ACKNOWLEDGEMENT",
    "TelemetryEvent",
]
