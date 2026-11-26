"""D45: a real site fixture may never declare a permission nobody granted.

**The defect this pins.** `examples/real/Robert Thomas Farms/robert_thomas_sitemodel.json`
recorded ``g98/g99/g100 = "approved"``, ``dno_application_state = "approved"``,
``supplier_consent_state = "approved"`` and ``export_mpan = "1900000000003"``, on a note that
said in plain words *"declared by owner fiat (issue 018, 2026-07-17) for meeting readiness; no
DNO/aggregator evidence yet"*. It carried ``connection.mec_kw = 500.0`` on the same fiat.

D45 forbids exactly that, by name: a `pending_permission` (consent, export MPAN, G99) is a
permission, not a fact, and must never be written as granted. A G99 or G100 approval is a factual
claim about a DNO; an export MPAN is a factual claim about a registered supply point. Neither is
made safe by a caveat in an adjacent prose field, because **the prose is not what production
reads**. The enums are:

* `value_stack/gates.py:197` -- `has_export_consent` is ``g99 == APPROVED and g100 == APPROVED``
  (over ``mec_kw > 0``);
* `value_stack/gates.py:212` -- `export_dispatch_enabled` adds ``bool(consents.export_mpan)``;
* `optimisation/engine.py:1498` -- `MilpEngine._export_upside` runs the consent-gated export
  overlay **only** when that predicate is true, so the fiat switched export dispatch on;
* `value_stack/gb_streams.py:2197` -- `WholesaleP415Stream.check_gates` reads the same predicate
  and moves the stream from CONDITIONAL to OPEN/CONTRACTED on it;
* `outputs/viewmodel.py:801` -- and the pack then prints "G99: approved" to a reader.

So the direction of the fiat was the flattering one: it turned an export revenue overlay ON, and
relabelled a gated-upside stream as contracted, for a real named company on no evidence at all.

**Why these three layers, and why over discovered files.**

1. **Enum layer** (raw JSON bytes). Predicate-only checking has a hole: ``g99 = "approved"`` with
   ``mec_kw`` null leaves `export_dispatch_enabled` False, so a predicate test would pass green on
   a file still asserting a consent nobody granted, and still printing it through
   `viewmodel.py:801`. The literal declaration is checked, before pydantic.
2. **Predicate layer** (production loader + production gates). What the engine actually branches
   on, asserted through the functions the engine calls, not a re-implementation of them.
3. **Ask layer**. A permission that is merely absent is only half of D45: the stream must be
   *modelled and shown, labelled requires customer consent*. `build_ranked_data_asks` is what puts
   it in front of a reader (`app/app.py:1188` renders it on the results page), so its
   ``export_consents`` entry is asserted rather than assumed.

Plus a **reference layer**, because the same file declared a `half_hourly_file_ref` that pointed
at nothing (the series has always been one directory further down). That escaped
`test_gate3_declared_ref_resolution.py` because that module globs ``examples/real/sites/*.json``
only, and this site does not live in ``sites/``. Discovery here is ``examples/real/**`` for that
reason.

**Scope: `examples/real/` only.** A consent field on a fixture under ``examples/real/`` is a claim
about a real, named third party (a DNO, a supplier, a customer). ``examples/demo_site/`` is a
fictional site, explicitly labelled synthetic and not bankable, so its consent block asserts
nothing about anybody; it carries the same fiat shape and is deliberately out of scope here rather
than silently swept in. It is recorded as an open question for the owner instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridq.cli import _resolve_declared_hh_ref
from gridq.ingestion import build_ranked_data_asks, load_site_model_json
from gridq.value_stack import gates

REPO = Path(__file__).resolve().parents[1]
REAL = REPO / "examples" / "real"

#: The consent fields that are permissions rather than facts. `export_mpan` is in the list on the
#: same footing: a meter point administration number is a claim that a registered export point
#: exists, and the fiat file invented one (1900000000003, a sequential neighbour of the demo
#: site's fabricated 1900000000001).
CONSENT_FIELDS = ("g98", "g99", "g100", "dno_application_state", "supplier_consent_state")


def _discovered() -> list[Path]:
    """Every real-site model under ``examples/real/``, found STRUCTURALLY, never by name.

    The filter is "a JSON object carrying a ``consents`` block", so a site in a new subdirectory is
    picked up automatically. A name list is what let the original defect sit unchecked: the file
    lived outside ``sites/``, and every test that looks at real sites globs ``sites/``.
    """
    out: list[Path] = []
    for path in sorted(REAL.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # pragma: no cover - a corrupt fixture fails louder below
            continue
        if isinstance(data, dict) and "consents" in data and "load_data" in data:
            out.append(path)
    return out


SITE_FILES: list[Path] = _discovered()

#: The static floor under the discovery. ``checked == len(discovered)`` passes VACUOUSLY at
#: ``0 == 0``, so the discovered count must clear a number written down in advance. Ten is the
#: current set: the nine models in ``examples/real/sites/`` plus Robert Thomas Farms. Both
#: ``examples/real/`` and ``data/`` have been tracked since 2026-08-11, so a missing fixture is a
#: deleted fixture, never an uninstalled host, and it must ASSERT rather than skip. Raise this
#: when sites are added; lowering it is a deliberate act that belongs in the diff.
MINIMUM_REAL_SITES = 10


def test_the_real_site_fixtures_are_discovered_and_clear_their_floor() -> None:
    """The floor under every test below: an empty or shrunken glob FAILS, never skips."""
    assert len(SITE_FILES) >= MINIMUM_REAL_SITES, (
        f"only {len(SITE_FILES)} real site fixtures under {REAL} (expected at least "
        f"{MINIMUM_REAL_SITES}): {[p.stem for p in SITE_FILES]}. examples/real is TRACKED, so a "
        "missing fixture is a deleted fixture, not an uninstalled host."
    )


@pytest.mark.parametrize("path", SITE_FILES, ids=lambda p: p.stem)
def test_no_real_site_fixture_declares_a_consent_as_granted(path: Path) -> None:
    """Enum layer: not one of the five consent fields reads ``approved``, and no export MPAN.

    Asserted on the raw JSON, before pydantic, because this is about what the file DECLARES. A
    site that has genuinely obtained a DNO approval will fail here, and that is correct: the
    evidence (the DNO's reference) belongs in the diff that flips it, so the flip is argued for
    once rather than arriving as a fiat again.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    consents = data["consents"]
    granted = [f for f in CONSENT_FIELDS if consents.get(f) == "approved"]
    assert not granted, (
        f"{path.name} declares {granted} as 'approved'. A consent is a pending_permission, never a "
        "fact (D45): it is a claim about a DNO or a supplier, it cannot be derived, and a caveat "
        "in consents.notes does not reach value_stack/gates.py:197, which reads the enum."
    )
    assert consents.get("export_mpan") is None, (
        f"{path.name} declares export_mpan {consents['export_mpan']!r}. An MPAN is a claim that a "
        "registered export point exists; gates.py:212 reads it to enable export dispatch."
    )


@pytest.mark.parametrize("path", SITE_FILES, ids=lambda p: p.stem)
def test_no_real_site_run_can_reach_the_consent_gated_export_overlay(path: Path) -> None:
    """Predicate layer: the production gates say no, through the production loader.

    This is the layer `MilpEngine._export_upside` (`optimisation/engine.py:1498`) and
    `WholesaleP415Stream.check_gates` (`value_stack/gb_streams.py:2197`) branch on, so it is the
    layer asserted, rather than a restatement of the boolean algebra inside this test.
    """
    site = load_site_model_json(str(path))
    assert gates.has_export_consent(site) is False, (
        f"{path.name}: G99 and G100 read as approved over a positive MEC, so the export streams "
        "would be treated as permitted on a consent no DNO has given."
    )
    assert gates.export_dispatch_enabled(site) is False, (
        f"{path.name}: the consent triple (G99 and G100 approved, MEC > 0, an export MPAN) "
        "coheres, so MilpEngine._export_upside would run the export overlay and WHOLESALE_P415 "
        "would open to CONTRACTED on declared-but-unevidenced permissions."
    )


@pytest.mark.parametrize("path", SITE_FILES, ids=lambda p: p.stem)
def test_every_real_site_reports_its_export_consent_as_an_outstanding_ask(path: Path) -> None:
    """Ask layer: absent is not enough, D45 requires the permission be SHOWN as outstanding.

    `build_ranked_data_asks` is what a reader meets (`app/app.py:1188` puts it on the results
    page). Without this, "no fiat consent" could be satisfied by a fixture that says nothing about
    consent at all, and the customer would never be asked.
    """
    site = load_site_model_json(str(path))
    ask_ids = {a.ask_id for a in build_ranked_data_asks(site)}
    assert "export_consents" in ask_ids, (
        f"{path.name}: export consent is neither granted nor asked for, so it is simply invisible."
    )


def test_every_declared_half_hourly_ref_resolves_the_way_a_run_resolves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reference layer: every declared series resolves, from a cwd that is not the repo root.

    `_resolve_declared_hh_ref` is the CLI's own resolver, and the run is driven from an arbitrary
    directory, which is why the check is made from ``tmp_path``. The Robert Thomas file declared
    ``examples/real/robert_thomas_haywood_oaks_HH.csv`` while its series sat in
    ``examples/real/Robert Thomas Farms/``: a site unable to find its own metered data, which
    `test_gate3_declared_ref_resolution.py` cannot catch because it globs ``sites/`` only. A
    missing file is an ASSERTION here, never a skip and never a `continue`.
    """
    refs = {
        path: json.loads(path.read_text(encoding="utf-8"))["load_data"].get("half_hourly_file_ref")
        for path in SITE_FILES
    }
    monkeypatch.chdir(tmp_path)
    checked = 0
    for path, ref in refs.items():
        assert ref, (
            f"{path.name} declares no half_hourly_file_ref. Real metered data is never displaced "
            "by generated data (validation_requirements Gate 2), so a real site that has a series "
            "must point at it."
        )
        resolved = _resolve_declared_hh_ref(ref, path)
        assert resolved is not None, (
            f"{path.name}: declared half_hourly_file_ref {ref!r} resolves to nothing from "
            f"{tmp_path}. A broken reference is a site that cannot read its own load."
        )
        assert resolved.exists(), f"{path.name}: {ref!r} resolved to a path that is not there"
        checked += 1

    assert checked == len(SITE_FILES), (
        f"only {checked} of {len(SITE_FILES)} declared series were checked; an unreadable fixture "
        "is a broken fixture, not a skip condition"
    )
    assert checked >= MINIMUM_REAL_SITES, (
        f"checked {checked} refs against a floor of {MINIMUM_REAL_SITES}: 'checked == discovered' "
        "is satisfied by an empty directory, so the count itself has to clear the floor"
    )
