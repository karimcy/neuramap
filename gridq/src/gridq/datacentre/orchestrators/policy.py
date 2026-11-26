"""The operator's adapter policy: the facts an orchestrator does not carry (D159).

An orchestrator's state says which work is running, how it is preempted and what its nodes draw. It
does not say who the operator is, which site this is, what IT load must be retained, how much notice
the operator wants, what it charges or what its SLA allows. Those are the operator's to state. They
come in an `AdapterPolicy`, written by the operator (``operator_supplied``) or as a labelled example
(``synthetic``), and **never defaulted**: every fact is a value, `NotApplicable` (the operator has
no such term) or `Unknown` (not stated), and an adapter refuses where IC-9 needs a value it lacks.

`AdapterPolicy` holds the terms every adapter needs. Each adapter subclasses it with the terms its
orchestrator's semantics call for (`gridq.datacentre.orchestrators.slurm.SlurmPolicy`), and names
that subclass as its ``policy_type``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    Unknown,
    content_hash,
)
from gridq.contracts.evidence import EvidenceRef

__all__ = ["AdapterPolicy", "PolicyPrice"]


class PolicyPrice(ContractModel):
    """What the operator asks for one workload class, in IC-9's components (whole pence).

    For a synthetic policy every amount is a placeholder and the declaration carries a
    ``synthetic_placeholder`` basis; for an operator's it is ``indicative`` on ``stated_on``."""

    kind: Literal["policy_price"]
    availability_pence_per_mw_per_hour: int | NotApplicable | Unknown
    utilisation_pence_per_mwh: int | NotApplicable | Unknown
    per_event_pence: int | NotApplicable | Unknown
    stated_on: IsoDate | NotApplicable

    @model_validator(mode="after")
    def _positive(self) -> PolicyPrice:
        for v in (
            self.availability_pence_per_mw_per_hour,
            self.utilisation_pence_per_mwh,
            self.per_event_pence,
        ):
            if isinstance(v, int) and v < 1:
                raise ValueError("a price amount is a positive number of pence (never zero)")
        return self


class AdapterPolicy(ContractModel):
    """The terms every orchestrator adapter needs from the operator."""

    label: Literal["operator_supplied", "synthetic"]
    #: What this policy is, in words (a synthetic one starts SYNTHETIC).
    policy_note: NonEmptyStr
    site_id: NonEmptyStr | Unknown
    operator_id: NonEmptyStr | Unknown
    #: The IT load the operator never turns down below (IC-9 ``retained_it_floor_kw``).
    retained_it_floor_kw: int | Unknown
    #: The most the operator lets be turned down at once; ``not_applicable``: no cap of its own.
    max_concurrent_turn_down_kw: int | NotApplicable
    #: Workload classes in the order IC-9 ``stack_at`` takes them (an adapter places each class's
    #: envelopes together, in this order); ``not_applicable``: by class id.
    merit_order: tuple[NonEmptyStr, ...] | NotApplicable
    #: The declaration this one replaces (IC-9 ``supersedes``).
    supersedes: Sha256 | NotApplicable
    #: IC-4 evidence the operator lists in support (IC-9 ``DeclaredBasis``; may be empty).
    supporting_evidence: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def _labelled(self) -> AdapterPolicy:
        if (self.label == "synthetic") != self.policy_note.startswith("SYNTHETIC"):
            raise ValueError("policy_note starts SYNTHETIC exactly when the policy is synthetic")
        if isinstance(self.retained_it_floor_kw, int) and self.retained_it_floor_kw < 0:
            raise ValueError("retained_it_floor_kw is not negative")
        if (
            isinstance(self.max_concurrent_turn_down_kw, int)
            and self.max_concurrent_turn_down_kw < 1
        ):
            raise ValueError("max_concurrent_turn_down_kw is a positive kW")
        return self

    def content_hash(self) -> str:
        return content_hash(self)
