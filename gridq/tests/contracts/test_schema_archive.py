"""The schema archive (WP-BO-06 step 1): the tagged v1.0.0 schema files are pinned by SHA-256.
IC-4 1.2.0 (WP-GRC-09) adds the IC-4 v1.1.0 file, at its hash under its tag.
IC-12 2.1.0 (WP-UW-03, D79) adds the three IC-12 v2.0.0 files, at their hashes under their tag.
IC-12 3.0.0 (WP-UW-07, D103/D104) adds the three IC-12 v2.1.0 files, at their hashes under their
tag. IC-12 3.1.0 (WP-UW-17, D128 to D132) adds the three IC-12 v3.0.0 files the same way.

Once IC-4, IC-1, IC-2 and IC-11 move to 1.1.0, their 1.0.0 schema files are no longer checked
against a live model. They are kept byte for byte instead: ``--check`` verifies each archived
file against its pinned hash, and ``--write`` refuses (exit 2, nothing written) to change one.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel

from gridq.contracts import __main__ as cli

#: The six archived files and their hashes at the v1.0.0 tags (plan §0). Pinned here as well as in
#: ``cli.ARCHIVED`` so an edit to one table alone fails.
TAGGED_V1_0_0 = {
    "ic4_evidence_object.v1.0.0.schema.json": (
        "d457702203c285245704a29c3f8472cf8daa4413462db01d3de910ec73ebdf80"
    ),
    "ic1_connection_agreement.v1.0.0.schema.json": (
        "1c62e7139ab3a27171cbc5302fd0230a58ee99336877bf3d5e046407215b73b3"
    ),
    "ic2_curtailment_scenario_set.v1.0.0.schema.json": (
        "fd9e588cb7b061daa5370eac4b08ccfba39c91276e7bcd289d37d259c92c0318"
    ),
    "ic11_thermal_flex_availability.v1.0.0.schema.json": (
        "8ee3297cdea7d365e8a71be5ac0e037363227e6668603abde00cb35745c8dbfd"
    ),
    "ic11_flex_instruction.v1.0.0.schema.json": (
        "363f44f25a33cc0fc3098ea5252611977b4987c97305b4628b963b7a6355ad42"
    ),
    "ic11_flex_acknowledgement.v1.0.0.schema.json": (
        "2a44df0094548fe7a14104d82f49930454f0e5f1e0335bdff1d3d0e2af7b05d4"
    ),
    # IC-12 1.0.0: the chassis as it stood at the ``gridq_clean_baseline`` tag, rendered
    # retroactively when the chassis was named a contract (WP-CORE-03, D71).
    "ic12_site_model.v1.0.0.schema.json": (
        "490f4927ad35362dcb272a5bb7c8c9b3b771852f90591041baec8e28880e8c1a"
    ),
    "ic12_optimisation_result.v1.0.0.schema.json": (
        "a9fb5541837599aec543a65d8973a0cc45c3ac8665591970a06026d98acbe18e"
    ),
    "ic12_quote_record.v1.0.0.schema.json": (
        "455f95bc3dc7fa7b77ab3581e7a68e3786af08004e224c144ef0d885d6cdd4f6"
    ),
}


#: The IC-4 v1.1.0 file, archived by IC-4 1.2.0 (WP-GRC-09), at its hash under the tag
#: ``contracts/ic4-evidence-object/v1.1.0``.
TAGGED_V1_1_0 = {
    "ic4_evidence_object.v1.1.0.schema.json": (
        "f81888ab7d964c800717f94b18b7411f56ab3c142cbaba683c4112313e838ee4"
    ),
}

#: The three IC-12 v2.0.0 files, archived by the additive minor IC-12 2.1.0 (WP-UW-03, D79), at
#: their hashes under the tag ``contracts/ic12-chassis/v2.0.0``.
TAGGED_IC12_V2_0_0 = {
    "ic12_site_model.v2.0.0.schema.json": (
        "245a0be2bbd6ebf5db7aac7c580818890f9ed9d655e427d55a0f002c48f38d1f"
    ),
    "ic12_optimisation_result.v2.0.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v2.0.0.schema.json": (
        "236449bb9128d8478deaec23a65aabb6a4a5b479af10c312e7ad81151141a0a2"
    ),
}

#: The three IC-12 v2.1.0 files, archived by the major IC-12 3.0.0 (WP-UW-07, D103/D104), which
#: was rendered on 2.1.0, at their hashes under the tag ``contracts/ic12-chassis/v2.1.0``. The site
#: and result files are the 2.0.0 files byte for byte: 2.1.0 moved only `QuoteRecord`.
TAGGED_IC12_V2_1_0 = {
    "ic12_site_model.v2.1.0.schema.json": (
        "245a0be2bbd6ebf5db7aac7c580818890f9ed9d655e427d55a0f002c48f38d1f"
    ),
    "ic12_optimisation_result.v2.1.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v2.1.0.schema.json": (
        "2559daea119adfb8a2230af2555867fc056b01a7a7619cc89401d85a1455010b"
    ),
}
#: The three IC-12 v3.0.0 files, archived by the additive minor IC-12 3.1.0 (WP-UW-17, D128 to
#: D132), at their hashes under the tag ``contracts/ic12-chassis/v3.0.0``.
TAGGED_IC12_V3_0_0 = {
    "ic12_site_model.v3.0.0.schema.json": (
        "0f2868cd9aaa996756f235b8dc9cf45b10f14a20c153a775a486513b266e844d"
    ),
    "ic12_optimisation_result.v3.0.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v3.0.0.schema.json": (
        "3c52e3724290081c84f3e9a7e4be88508036797f9494f22e169f912a6e710d21"
    ),
}
#: The three IC-12 v3.1.0 files, archived by the additive minor IC-12 3.2.0 (WP-SIZE-02,
#: D178), at their hashes under the tag ``contracts/ic12-chassis/v3.1.0``.
TAGGED_IC12_V3_1_0 = {
    "ic12_site_model.v3.1.0.schema.json": (
        "0f2868cd9aaa996756f235b8dc9cf45b10f14a20c153a775a486513b266e844d"
    ),
    "ic12_optimisation_result.v3.1.0.schema.json": (
        "302e69c183d3a930cd5b2fd86484814eaf98cdcceab7090520780a53b4f9d3e1"
    ),
    "ic12_quote_record.v3.1.0.schema.json": (
        "55d36023d8b7b6da71dacffc0c0b3cc7166377660793457d209bebe5b5fdfad6"
    ),
}
TAGGED = {
    **TAGGED_V1_0_0,
    **TAGGED_V1_1_0,
    **TAGGED_IC12_V2_0_0,
    **TAGGED_IC12_V2_1_0,
    **TAGGED_IC12_V3_0_0,
    **TAGGED_IC12_V3_1_0,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_schemas(tmp_path: Path) -> Path:
    target = tmp_path / "schemas"
    shutil.copytree(cli.SCHEMA_DIR, target)
    return target


class _Drifted(BaseModel):
    """A model whose schema is not the archived one: what an in-place edit would produce."""

    drifted: int


def test_the_archive_pins_every_tagged_file() -> None:
    assert cli.ARCHIVED == TAGGED
    for name, pinned in cli.ARCHIVED.items():
        assert _sha(cli.SCHEMA_DIR / name) == pinned, name


def test_the_committed_archive_passes_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "DRIFT" not in out
    for name in cli.ARCHIVED:
        assert f"ok    {name} (archived, sha256 pinned)" in out


@pytest.mark.parametrize("name", sorted(TAGGED))
def test_a_tampered_archived_schema_fails_check(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schemas = _copy_schemas(tmp_path)
    assert cli.main(["--check"], schema_dir=schemas) == 0
    capsys.readouterr()
    path = schemas / name
    path.write_bytes(path.read_bytes().replace(b'"title"', b'"Title"', 1))
    assert _sha(path) != TAGGED[name]
    assert cli.main(["--check"], schema_dir=schemas) == 1
    assert f"DRIFT: archived {name} no longer matches its pinned sha256" in capsys.readouterr().out


def test_a_missing_archived_schema_fails_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schemas = _copy_schemas(tmp_path)
    (schemas / "ic1_connection_agreement.v1.0.0.schema.json").unlink()
    assert cli.main(["--check"], schema_dir=schemas) == 1
    assert "ic1_connection_agreement.v1.0.0.schema.json" in capsys.readouterr().out


def test_write_refuses_to_change_an_archived_file_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    schemas = _copy_schemas(tmp_path)
    archived = schemas / "ic4_evidence_object.v1.0.0.schema.json"
    before = archived.read_bytes()
    # A live file removed beforehand must not come back: a refusal writes nothing at all.
    live = schemas / "ic13_audit_event.v1.0.0.schema.json"
    live.unlink()
    monkeypatch.setattr(cli, "FROZEN", (*cli.FROZEN, ("ic4_evidence_object", _Drifted, "1.0.0")))
    assert cli.main(["--write"], schema_dir=schemas) == 2
    assert archived.read_bytes() == before
    assert _sha(archived) == TAGGED_V1_0_0[archived.name]
    assert not live.exists()
    out = capsys.readouterr().out
    assert f"REFUSED: {archived.name} is archived" in out
    assert "wrote" not in out


def test_write_on_the_committed_models_changes_no_file(tmp_path: Path) -> None:
    schemas = _copy_schemas(tmp_path)
    before = {p.name: p.read_bytes() for p in schemas.iterdir()}
    assert cli.main(["--write"], schema_dir=schemas) == 0
    assert {p.name: p.read_bytes() for p in schemas.iterdir()} == before
