"""A pack may not cite a run record a later reader cannot fetch.

The 2026-08-13 adversarial review found the Bishops Cleeve pack's central figure living in a session
temp directory with **no run in `runs/`** at all, under a footer asserting every figure was copied
from run record ``quote_run_a30b80c0a4d44c4b``. When that tempdir cleared, the pack's numbers became
permanently unverifiable — and nobody outside that session could ever have checked them.

These tests pin the structural close: `publish_run` renders a pack **from the files it just wrote**,
and `require_persisted_run` refuses outright for a run that was never persisted. The first is the
guarantee (the pack and the evidence are the same bytes); the second is the same guarantee as a
precondition for callers that render directly.

Every test writes to ``tmp_path`` — never the real store.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.provenance.claims import PublicationReceipt, publication_claim
from gridq.quotes.assembly import assemble_quote_record
from gridq.quotes.persistence import (
    RECEIPT_FILE,
    RECORD_FILE,
    RunStoreError,
    UnpersistedRunError,
    is_persisted,
    load_persisted_run,
    persist_run,
    publish_run,
    require_persisted_run,
    run_dir,
    run_store_dir,
)

ASSUMPTION_VERSION = "gb_v0_1"


@pytest.fixture
def assumptions():
    from gridq.assumptions import load_assumptions

    return load_assumptions(ASSUMPTION_VERSION)


def _result(site: s.SiteModel, assumptions) -> s.OptimisationResult:
    from gridq.engine import BaselineEngine

    engine = BaselineEngine()
    return engine.solve(engine.build_optimisation_request(site, assumptions))


@pytest.fixture
def quote(make_site: Callable[..., s.SiteModel], assumptions) -> s.QuoteRecord:
    site = make_site()
    return assemble_quote_record(site, _result(site, assumptions), None, assumptions)


# --- the guard ---------------------------------------------------------------------------------


def test_require_persisted_run_refuses_a_run_that_was_never_persisted(tmp_path: Path) -> None:
    """The exact 2026-08-13 failure: no run in the store, so no pack.

    This is the guard that stops the blocker recurring. It is deliberately an exception, not a
    warning: a pack that names a record no reader can fetch is making an unverifiable claim about
    money, and there is no degraded version of that worth shipping.
    """
    with pytest.raises(UnpersistedRunError) as excinfo:
        require_persisted_run("quote_run_a30b80c0a4d44c4b", root=tmp_path)
    message = str(excinfo.value)
    assert "quote_run_a30b80c0a4d44c4b" in message
    assert "may not be generated" in message  # says what is refused, not just that something is
    assert not is_persisted("quote_run_a30b80c0a4d44c4b", root=tmp_path)


def test_require_persisted_run_returns_the_run_once_it_is_persisted(
    quote: s.QuoteRecord, tmp_path: Path
) -> None:
    persist_run(quote, root=tmp_path)
    got = require_persisted_run(quote.quote_id, root=tmp_path)
    assert got.quote.quote_id == quote.quote_id
    assert got.quote.input_hash == quote.input_hash


def test_a_record_on_disk_without_a_receipt_is_not_publishable(
    quote: s.QuoteRecord, tmp_path: Path
) -> None:
    """A record with nothing pinning its contents cannot support a provenance claim.

    Persisted-but-unpinned is not the same as persisted. Without a receipt the pack cannot state
    where its figures came from, and the honest response is to refuse rather than to print a claim
    nobody can test. (This is also why the five legacy `runs/` directories are not publishable
    as-is: they predate the receipt.)
    """
    directory = run_dir(quote.quote_id, tmp_path)
    directory.mkdir(parents=True)
    (directory / RECORD_FILE).write_text(quote.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(RunStoreError, match=RECEIPT_FILE):
        load_persisted_run(quote.quote_id, root=tmp_path)
    assert not is_persisted(quote.quote_id, root=tmp_path)


def test_a_directory_holding_someone_elses_record_is_refused(
    quote: s.QuoteRecord, tmp_path: Path
) -> None:
    """Directory name and recorded ``quote_id`` must agree, or a citation points at the wrong run.

    The check is identity equality, not a prefix rule: the five legacy runs record ids without the
    ``quote_`` prefix and are perfectly coherent.
    """
    persist_run(quote, root=tmp_path)
    impostor = run_dir("quote_run_deadbeefdeadbeef", tmp_path)
    impostor.mkdir(parents=True)
    (impostor / RECORD_FILE).write_text(quote.model_dump_json(indent=2), encoding="utf-8")
    (impostor / RECEIPT_FILE).write_text(
        (run_dir(quote.quote_id, tmp_path) / RECEIPT_FILE).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(RunStoreError, match="wrong run"):
        load_persisted_run("quote_run_deadbeefdeadbeef", root=tmp_path)


@pytest.mark.parametrize("unsafe", ["", "..", "../escape", "a/b", "a\\b"])
def test_run_ids_cannot_escape_the_store(unsafe: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe run id"):
        run_dir(unsafe, tmp_path)


# --- publish_run renders from the persisted files, not from memory ------------------------------


def test_publish_run_renders_from_the_reloaded_record_not_the_in_memory_one(
    make_site: Callable[..., s.SiteModel], assumptions, tmp_path: Path, monkeypatch
) -> None:
    """The structural close on blocker 1.

    ``publish_run`` must hand the renderer the record it read back off disk. If it handed over the
    in-memory record instead, a pack could again exist whose cited record is not an artifact — the
    file write would be an optional side-effect rather than the source. So we capture what the
    renderer was actually given and assert (a) it is a different object from ``run.quote``, and
    (b) the record file was already on disk at the moment of the call.
    """
    site = make_site()
    result = _result(site, assumptions)
    quote = assemble_quote_record(site, result, None, assumptions)

    class _Run:  # minimal duck of QuoteRunResult: publish_run reads quote/site/telemetry
        pass

    run = _Run()
    run.quote = quote  # type: ignore[attr-defined]
    run.site = site  # type: ignore[attr-defined]
    run.telemetry = None  # type: ignore[attr-defined]

    seen: dict[str, object] = {}

    def _fake_generate_artifacts(record, site_arg, **kwargs):
        seen["record"] = record
        seen["site"] = site_arg
        seen["record_file_existed"] = (run_dir(record.quote_id, tmp_path) / RECORD_FILE).is_file()

        class _Artifacts:
            memo_markdown = "memo"
            proposal_markdown = "proposal"
            report_html = "report"
            consulting_report_html = "consulting"

        return _Artifacts()

    import gridq.outputs

    monkeypatch.setattr(gridq.outputs, "generate_artifacts", _fake_generate_artifacts)

    published = publish_run(run, root=tmp_path)  # type: ignore[arg-type]

    assert seen["record_file_existed"] is True, "rendered before the record was durable"
    assert seen["record"] is not quote, "publish_run rendered from memory, not from the artifact"
    assert seen["record"].quote_id == quote.quote_id  # type: ignore[union-attr]
    assert seen["record"].input_hash == quote.input_hash  # type: ignore[union-attr]
    assert published.quote_id == quote.quote_id
    assert {p.name for p in published.written} == {
        "memo.md",
        "proposal.md",
        "report.html",
        "consulting_report.html",
    }
    for path in published.written:
        assert path.is_file()


def test_publish_run_writes_the_documents_beside_the_evidence(
    make_site: Callable[..., s.SiteModel], assumptions, tmp_path: Path
) -> None:
    """A real end-to-end publish: the pack and the record a reader would check share a directory."""
    from gridq.quotes import generate_quote

    run = generate_quote(make_site(), assumptions)
    published = publish_run(run, root=tmp_path)

    directory = published.persisted.directory
    for filename in (RECORD_FILE, "site.json", RECEIPT_FILE, "consulting_report.html"):
        assert (directory / filename).is_file(), filename
    # The pack cites the record; the record is right there.
    assert published.quote_id in (directory / "consulting_report.html").read_text(encoding="utf-8")


# --- the receipt --------------------------------------------------------------------------------


def test_receipt_pins_every_artifact_by_content(quote: s.QuoteRecord, tmp_path: Path) -> None:
    site_free = persist_run(quote, root=tmp_path)
    receipt = site_free.receipt
    assert receipt.quote_id == quote.quote_id
    record = receipt.artifact(RECORD_FILE)
    assert record is not None
    assert record.sha256.startswith("sha256:")
    assert record.size_bytes > 0
    # The hash is of the bytes actually on disk — recompute it and compare.
    from gridq.provenance import hash_file

    assert record.sha256 == hash_file(site_free.directory / RECORD_FILE)


def test_receipt_is_deterministic_and_carries_no_clock_or_absolute_path(
    quote: s.QuoteRecord, tmp_path: Path
) -> None:
    """Re-publishing the same run must rewrite identical bytes (the repo's determinism bar).

    A wall-clock reading in the receipt would break that and would also give the run a second,
    conflicting "when"; an absolute path would leak the producing machine's layout.
    """
    first = persist_run(quote, root=tmp_path)
    raw_first = (first.directory / RECEIPT_FILE).read_text(encoding="utf-8")
    second = persist_run(quote, root=tmp_path)
    raw_second = (second.directory / RECEIPT_FILE).read_text(encoding="utf-8")

    assert raw_first == raw_second
    assert first.receipt.record_created_at == quote.created_at.isoformat()
    payload = json.loads(raw_first)
    assert str(tmp_path) not in raw_first
    for artifact in payload["artifacts"]:
        assert "/" not in artifact["filename"] and "\\" not in artifact["filename"]


def test_receipt_round_trips_and_forbids_unknown_fields(
    quote: s.QuoteRecord, tmp_path: Path
) -> None:
    persisted = persist_run(quote, root=tmp_path)
    raw = (persisted.directory / RECEIPT_FILE).read_text(encoding="utf-8")
    assert PublicationReceipt.model_validate_json(raw) == persisted.receipt

    payload = json.loads(raw)
    payload["totally_new_field"] = 1
    with pytest.raises(ValueError):
        PublicationReceipt.model_validate(payload)


def test_run_store_dir_honours_the_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GRIDQ_RUN_STORE", str(tmp_path / "elsewhere"))
    assert run_store_dir() == tmp_path / "elsewhere"
    # An explicit root always wins, so tests can never be routed at the real store by an env var.
    assert run_store_dir(tmp_path) == tmp_path


def test_the_claim_cannot_be_stated_without_a_persisted_record() -> None:
    """No record file in the receipt, no claim. The sentence and its evidence are inseparable."""
    receipt = PublicationReceipt(
        quote_id="quote_run_nothing",
        input_hash="sha256:0",
        record_schema_version="0.1.0",
        record_created_at="2026-08-13T00:00:00",
        artifacts=[],
        recomputed_at_render=[],
    )
    with pytest.raises(ValueError, match="pins no quote.json"):
        publication_claim(receipt)
