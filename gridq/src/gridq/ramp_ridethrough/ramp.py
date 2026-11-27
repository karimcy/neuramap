"""The ramp screen: the site's import ramp against the IC-1 ramp terms (spec A3-03; D-NEW-SITE06-2).

**The ramp terms are read from the IC-1 instance and nowhere else.** `ConnectionAgreement.ramp` is
the only source of a limit; there is no default, no configured limit and no published fallback.
G99 Issue 2 clause 10.3.4 is why: in general there is no maximum admissible ramp rate, and where a
network-specific issue needs one, it is specified in the Connection Agreement.

Direction: IC-1 ramp terms limit changes of site **import**. More charge, or less discharge,
raises import (judged against ``max_ramp_up_mw_per_min``); more discharge, or less charge, lowers
it (``max_ramp_down_mw_per_min``).

The three IC-1 states:

- `RampTerms` -> `AgreedRamp`: the PPC settings check (always run when the settings are
  declared); the admissible envelope (the IC-1 limits themselves, labelled as a ceiling for the
  OEM's PPC configuration, never a recommended setting); the series check when a minute net-import
  series is supplied; and the trip steps, reported for the network operator's interpretation, never
  judged.
- `NotApplicable` -> `NoLimitAgreed`: no verdict; the declared PPC settings become the G99 10.3.4
  "assumed maximum ramp rate" statement for the connection application.
- `Unknown` -> `RampTermsUnknown`: an ask for the agreement's ramp terms.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final, Literal

from gridq.adequacy._common import ExactRational, NonNegInt
from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Sha256, Unknown
from gridq.contracts.connection import ConnectionAgreement, RampTerms
from gridq.contracts.evidence import EvidenceType
from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.ramp_ridethrough._common import (
    Cite,
    NotComputed,
    bess_ask,
    cite,
    declarant_ask,
    exact,
    rational,
    site_ask,
    sorted_asks,
)
from gridq.ramp_ridethrough.inputs import BessDeclaration, NetImportSeries

__all__ = [
    "AdmissibleEnvelope",
    "AgreedRamp",
    "DirectionCheck",
    "Exceedance",
    "NoLimitAgreed",
    "RampScreen",
    "RampTermsUnknown",
    "SeriesCheck",
    "TripStep",
    "ramp_terms_ask",
    "screen_ramp",
]

_W_PER_MW = 1_000_000
ENVELOPE_LABEL: Final = "ceiling_for_oem_ppc_configuration_not_a_recommended_setting"


class DirectionCheck(ContractModel):
    direction: Literal["towards_charge", "towards_discharge"]
    declared_w_per_min: int
    limit_w_per_min: ExactRational
    #: ``limit - declared``; negative when the setting exceeds the agreed limit.
    margin_w_per_min: ExactRational
    verdict: Literal["within", "exceeds"]


class SettingsCheck(ContractModel):
    kind: Literal["settings_check"]
    towards_charge: DirectionCheck
    towards_discharge: DirectionCheck

    @property
    def within(self) -> bool:
        return self.towards_charge.verdict == "within" and self.towards_discharge.verdict == (
            "within"
        )


class AdmissibleEnvelope(ContractModel):
    """The largest PPC ramp settings the agreement admits, for the OEM's configuration."""

    kind: Literal["admissible_envelope"]
    label: Literal["ceiling_for_oem_ppc_configuration_not_a_recommended_setting"]
    agreement_hash: Sha256
    max_towards_charge_w_per_min: ExactRational
    max_towards_discharge_w_per_min: ExactRational


class Exceedance(ContractModel):
    minute_index: NonNegInt
    change_w: int
    limit_w: ExactRational


class SeriesCheck(ContractModel):
    kind: Literal["series_check"]
    series_hash: Sha256
    basis: Literal["metered", "modelled"]
    record_class: Literal["registered", "synthetic_fixture"]
    minutes: NonNegInt
    max_rise_w_per_min: int
    max_fall_w_per_min: int
    exceedances: tuple[Exceedance, ...]
    verdict: Literal["within", "exceeds"]


class TripStep(ContractModel):
    """A PCS protection trip is an instantaneous import step. Reported, never judged: whether the
    ramp terms bind a protection trip is the network operator's interpretation."""

    kind: Literal["trip_step"]
    event: Literal["trip_at_full_discharge", "trip_at_full_charge"]
    import_change_w: int
    limit_one_minute_w: ExactRational
    #: ``|import change| / (limit x 1 min)``.
    multiple_of_limit: ExactRational
    outcome: Literal["for_dno_interpretation"]


class AgreedRamp(ContractModel):
    kind: Literal["agreed_ramp"]
    agreement_hash: Sha256
    settings: SettingsCheck | NotComputed
    envelope: AdmissibleEnvelope
    series: SeriesCheck | NotComputed
    trip_steps: tuple[TripStep | NotComputed, ...]
    basis: tuple[Cite, ...]
    requests: tuple[DossierAsk, ...]


class NoLimitAgreed(ContractModel):
    kind: Literal["no_limit_agreed"]
    agreement_hash: Sha256
    reason: NonEmptyStr
    #: The G99 10.3.4 statement of the assumed maximum ramp rate, from the declared PPC settings.
    g99_assumed_max_ramp_statement: NonEmptyStr | NotComputed
    basis: tuple[Cite, ...]
    requests: tuple[DossierAsk, ...]


class RampTermsUnknown(ContractModel):
    kind: Literal["ramp_terms_unknown"]
    agreement_hash: Sha256
    reason: NonEmptyStr
    basis: tuple[Cite, ...]
    requests: tuple[DossierAsk, ...]


RampScreen = AgreedRamp | NoLimitAgreed | RampTermsUnknown


def _limits_w(terms: RampTerms) -> tuple[Fraction, Fraction]:
    return (
        exact(terms.max_ramp_up_mw_per_min) * _W_PER_MW,
        exact(terms.max_ramp_down_mw_per_min) * _W_PER_MW,
    )


def _settings_asks(bess: BessDeclaration) -> list[DossierAsk]:
    asks = []
    for name, words in (
        ("ppc_ramp_towards_charge_w_per_min", "towards charge (site import rising)"),
        ("ppc_ramp_towards_discharge_w_per_min", "towards discharge (site import falling)"),
    ):
        if isinstance(getattr(bess, name), Unknown):
            asks.append(
                declarant_ask(
                    f"Declare the PPC active-power ramp-limit setting {words}, in W per minute",
                    site_id=bess.site_id,
                    declarant=bess.declared_by,
                )
            )
    return asks


def _direction(direction: str, declared: int, limit: Fraction) -> DirectionCheck:
    margin = limit - declared
    return DirectionCheck(
        direction=direction,  # type: ignore[arg-type]
        declared_w_per_min=declared,
        limit_w_per_min=rational(limit),
        margin_w_per_min=rational(margin),
        verdict="within" if margin >= 0 else "exceeds",
    )


def _series_check(
    series: NetImportSeries, up: Fraction, down: Fraction, series_hash: str
) -> SeriesCheck:
    rises: list[int] = []
    falls: list[int] = []
    exceed: list[Exceedance] = []
    values = series.import_w
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        (rises if d >= 0 else falls).append(d)
        limit = up if d >= 0 else down
        if abs(d) > limit:
            exceed.append(Exceedance(minute_index=i, change_w=d, limit_w=rational(limit)))
    return SeriesCheck(
        kind="series_check",
        series_hash=series_hash,
        basis=series.basis,
        record_class=series.record_class,
        minutes=len(values) - 1,
        max_rise_w_per_min=max(rises, default=0),
        max_fall_w_per_min=-min(falls, default=0),
        exceedances=tuple(exceed),
        verdict="exceeds" if exceed else "within",
    )


def _trip_steps(
    agreement: ConnectionAgreement, bess: BessDeclaration, up: Fraction, down: Fraction
) -> tuple[tuple[TripStep | NotComputed, ...], list[DossierAsk]]:
    out: list[TripStep | NotComputed] = []
    asks: list[DossierAsk] = []
    for event, rating, limit, sign in (
        ("trip_at_full_discharge", bess.registered_capacity_w, up, 1),
        ("trip_at_full_charge", bess.rated_import_power_w, down, -1),
    ):
        if isinstance(rating, Unknown):
            what = "Registered Capacity" if sign == 1 else "Rated Import Power"
            a = bess_ask(bess, "registered_capacity_w" if sign == 1 else "rated_import_power_w")
            out.append(NotComputed(kind="not_computed", reason=f"{what} unknown", requests=(a,)))
            asks.append(a)
            continue
        out.append(
            TripStep(
                kind="trip_step",
                event=event,  # type: ignore[arg-type]
                import_change_w=sign * rating,
                limit_one_minute_w=rational(limit),
                multiple_of_limit=rational(Fraction(rating) / limit),
                outcome="for_dno_interpretation",
            )
        )
    asks.append(
        site_ask(
            f"State in writing whether the import ramp terms of connection agreement "
            f"{agreement.agreement_ref} apply to a protection trip of the BESS (an instantaneous "
            "import step), or only to controlled operation",
            site_id=bess.site_id,
            holder=AskHolder.NETWORK_OPERATOR,
            closes_with=(EvidenceType.CONNECTION_AGREEMENT,),
        )
    )
    return tuple(out), asks


def ramp_terms_ask(agreement: ConnectionAgreement) -> DossierAsk:
    """The ask for an unknown IC-1 ramp term: the customer holds the agreement or the offer."""
    return site_ask(
        f"Provide the import ramp terms (MW per minute, up and down) of connection agreement "
        f"{agreement.agreement_ref}, or confirm in writing that it states none",
        site_id=agreement.site_id,
        holder=AskHolder.CUSTOMER,
        closes_with=(EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER),
    )


def screen_ramp(
    agreement: ConnectionAgreement,
    bess: BessDeclaration,
    series: NetImportSeries | None,
    *,
    series_hash: str | None = None,
) -> RampScreen:
    """Screen the site import ramp against ``agreement.ramp`` (the only source of a limit)."""
    agreement_hash = agreement.content_hash()
    basis = (cite("G99_ISSUE_2", "10.3.4"),)
    ramp = agreement.ramp
    if isinstance(ramp, Unknown):
        a = ramp_terms_ask(agreement)
        return RampTermsUnknown(
            kind="ramp_terms_unknown",
            agreement_hash=agreement_hash,
            reason=f"IC-1 ramp is unknown: {ramp.reason}",
            basis=basis,
            requests=(a,),
        )
    if isinstance(ramp, NotApplicable):
        c, d = bess.ppc_ramp_towards_charge_w_per_min, bess.ppc_ramp_towards_discharge_w_per_min
        statement: str | NotComputed
        asks = _settings_asks(bess)
        if isinstance(c, int) and isinstance(d, int):
            statement = (
                f"Assumed maximum active-power ramp rate of the BESS (G99 10.3.4): {c} W per "
                f"minute towards charge and {d} W per minute towards discharge, as set in the PPC."
            )
        else:
            statement = NotComputed(
                kind="not_computed",
                reason="the PPC ramp-limit settings are not declared",
                requests=tuple(asks),
            )
        return NoLimitAgreed(
            kind="no_limit_agreed",
            agreement_hash=agreement_hash,
            reason=f"IC-1 states no ramp term: {ramp.reason}",
            g99_assumed_max_ramp_statement=statement,
            basis=basis,
            requests=sorted_asks(asks),
        )

    up, down = _limits_w(ramp)
    asks = _settings_asks(bess)
    c, d = bess.ppc_ramp_towards_charge_w_per_min, bess.ppc_ramp_towards_discharge_w_per_min
    settings: SettingsCheck | NotComputed
    if isinstance(c, int) and isinstance(d, int):
        settings = SettingsCheck(
            kind="settings_check",
            towards_charge=_direction("towards_charge", c, up),
            towards_discharge=_direction("towards_discharge", d, down),
        )
    else:
        settings = NotComputed(
            kind="not_computed",
            reason="the PPC ramp-limit settings are not declared",
            requests=tuple(asks),
        )
    envelope = AdmissibleEnvelope(
        kind="admissible_envelope",
        label=ENVELOPE_LABEL,
        agreement_hash=agreement_hash,
        max_towards_charge_w_per_min=rational(up),
        max_towards_discharge_w_per_min=rational(down),
    )
    series_out: SeriesCheck | NotComputed
    if series is None:
        sa = site_ask(
            "Supply a minute-resolution site net-import series (metered, or a modelled dispatch "
            "of the proposed BESS) to check observed ramps against the agreed terms",
            site_id=bess.site_id,
            holder=AskHolder.CUSTOMER,
            closes_with=(EvidenceType.METERED_DATA_RECORD,),
        )
        series_out = NotComputed(
            kind="not_computed", reason="no net-import series supplied", requests=(sa,)
        )
        asks.append(sa)
    else:
        assert series_hash is not None
        series_out = _series_check(series, up, down, series_hash)
    trips, trip_asks = _trip_steps(agreement, bess, up, down)
    return AgreedRamp(
        kind="agreed_ramp",
        agreement_hash=agreement_hash,
        settings=settings,
        envelope=envelope,
        series=series_out,
        trip_steps=trips,
        basis=basis,
        requests=sorted_asks(asks, trip_asks),
    )
