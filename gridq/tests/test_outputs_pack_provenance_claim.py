"""The pack's footer says only what its reader can check, and publication refuses what it cannot.

The six 2026-08-13 client packs closed with a sentence that was false twice over: it said every
figure was "copied from run record X" when no such record was ever written, and that the generator
"performs no arithmetic" when the view-model seam beneath it sums the record's own stream revenues
at render time. A reader who believes a footer stops checking, so a false one is worse than none.

`gridq.provenance.claims.publication_claim` is the true replacement: it names each persisted file,
publishes its SHA-256, and enumerates exhaustively the values a pack recomputes rather than copies.
These pin that the pack actually carries it, verbatim, and that a pack with no receipt behind it
says so rather than borrowing the claim.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.outputs import formatting as fmt
from gridq.outputs import render_publishable_pack, render_report_for_quote
from gridq.provenance.claims import RECOMPUTED_AT_RENDER, publication_claim
from gridq.quotes.persistence import RunStoreError, UnpersistedRunError, persist_run


def _footer(html: str) -> str:
    return html[html.rindex("<footer") :]


def _as_rendered(text: str) -> str:
    """The claim as it reaches the page: HTML-escaped, then dash-rewritten like every other string.

    The claim's own copy carries em dashes; the deliverable carries none, and the rewrite happens on
    the finished document (a recorded decision, `formatting.no_dashes`). Comparing against the raw
    string would assert the report breaks that rule, not that it keeps the claim intact.
    """
    return fmt.no_dashes(escape(text))


def _persist(rich_quote, root: Path):
    return persist_run(rich_quote.quote, rich_quote.site, root=root)


# --- the claim a persisted pack may make -----------------------------------------------------


def test_a_pack_rendered_from_a_persisted_run_states_the_receipt_claim(rich_quote, tmp_path):
    """The footer is the receipt's own sentence, so every part of it is falsifiable."""
    persisted = _persist(rich_quote, tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    footer = _footer(html)
    assert _as_rendered(publication_claim(persisted.receipt)) in footer
    # The parts a reviewer would test: the file, its hash, and the exhaustive recomputed list.
    record = persisted.receipt.artifact("quote.json")
    assert record is not None and record.sha256 in footer
    for recomputed in RECOMPUTED_AT_RENDER:
        assert _as_rendered(recomputed) in footer


def test_the_claim_is_reproduced_verbatim_and_never_shortened(rich_quote, tmp_path):
    """Its precision is the point: a vaguer sentence withdraws what a reviewer could falsify."""
    persisted = _persist(rich_quote, tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    claim = publication_claim(persisted.receipt)
    # Rendering only escapes and rewrites dashes; nothing else may edit the sentence.
    assert _as_rendered(claim) in html
    assert len(RECOMPUTED_AT_RENDER) > 0
    assert f"Exactly {len(RECOMPUTED_AT_RENDER)} value(s)" in _footer(html)


def test_the_false_sentence_is_gone_from_every_pack(rich_quote, tmp_path):
    """Neither state of the footer may assert the two things that were untrue."""
    persisted = _persist(rich_quote, tmp_path)
    for html in (
        render_publishable_pack(persisted.quote_id, run_store_root=tmp_path),
        render_report_for_quote(rich_quote.quote, rich_quote.site, run_store_root=tmp_path / "x"),
    ):
        assert "The generator performs no arithmetic" not in html
        assert "copied from run record" not in html or "copied verbatim from the persisted" in html


# --- a pack with nothing behind it ----------------------------------------------------------


def test_an_unpersisted_run_is_not_publishable_at_all(rich_quote, tmp_path):
    """The refusal the 2026-08-13 packs should have raised. There is no degraded version of it."""
    with pytest.raises(UnpersistedRunError) as caught:
        render_publishable_pack(rich_quote.quote.quote_id, run_store_root=tmp_path)
    assert rich_quote.quote.quote_id in str(caught.value)
    assert "no later reader could fetch" in str(caught.value)


def test_a_working_render_says_it_is_not_a_publishable_pack(rich_quote, tmp_path):
    """Rendering in memory is allowed; borrowing the claim of a run that is not on disk is not."""
    html = render_report_for_quote(
        rich_quote.quote, rich_quote.site, run_store_root=tmp_path / "empty"
    )
    footer = _footer(html)
    assert "no such record is persisted" in footer
    assert "not a publishable pack" in footer
    assert "publish_run" in footer
    # It states no claim it cannot back.
    assert "sha256" not in footer.lower()
    assert "copied verbatim" not in footer


def test_the_unverified_notice_is_marked_as_the_stop_state_it_is(rich_quote, tmp_path):
    html = render_report_for_quote(
        rich_quote.quote, rich_quote.site, run_store_root=tmp_path / "empty"
    )
    assert '<p class="unverified">' in _footer(html)
    style = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert style is not None
    assert "p.unverified{color:var(--neura-signal)" in style.group(1)


def test_a_persisted_run_switches_the_footer_without_any_other_change(rich_quote, tmp_path):
    """Only the claim differs between the two states: persisting changes evidence, not figures."""
    before = render_report_for_quote(
        rich_quote.quote, rich_quote.site, run_store_root=tmp_path / "empty"
    )
    _persist(rich_quote, tmp_path)
    after = render_report_for_quote(rich_quote.quote, rich_quote.site, run_store_root=tmp_path)
    assert before[: before.rindex("<footer")] == after[: after.rindex("<footer")]
    assert "no such record is persisted" in _footer(before)
    assert "copied verbatim from the persisted run record" in _footer(after)


def test_a_record_on_disk_with_no_receipt_backs_no_claim(rich_quote, tmp_path):
    """Half-persisted is not persisted: nothing pins the file, so the footer cites nothing.

    The five run directories written before the receipt existed are in exactly this state. A working
    render of one must degrade to the unverifiable notice rather than raise, while the publication
    path still refuses it: a pack is only publishable when its evidence is complete.
    """
    persisted = _persist(rich_quote, tmp_path)
    (persisted.directory / "provenance.json").unlink()

    html = render_report_for_quote(rich_quote.quote, rich_quote.site, run_store_root=tmp_path)
    assert "no such record is persisted" in _footer(html)

    with pytest.raises(RunStoreError):
        render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)


# --- the gate on the one-call generator ------------------------------------------------------


def test_generate_artifacts_can_be_required_to_refuse_an_unpersisted_run(rich_quote, monkeypatch):
    """The precondition a caller writing documents for someone else to read must switch on."""
    from gridq.outputs import generate_artifacts

    monkeypatch.setenv("GRIDQ_RUN_STORE", str(Path(__file__).parent / "_no_such_store"))
    with pytest.raises(UnpersistedRunError):
        generate_artifacts(rich_quote.quote, rich_quote.site, require_persisted=True)
    # Off by default, because this is also the in-memory primitive the pipeline renders through.
    assert generate_artifacts(rich_quote.quote, rich_quote.site).consulting_report_html


def test_a_persisted_run_passes_the_gate(rich_quote, tmp_path, monkeypatch):
    from gridq.outputs import generate_artifacts

    persist_run(rich_quote.quote, rich_quote.site, root=tmp_path)
    monkeypatch.setenv("GRIDQ_RUN_STORE", str(tmp_path))
    artifacts = generate_artifacts(rich_quote.quote, rich_quote.site, require_persisted=True)
    assert artifacts.consulting_report_html.startswith("<!doctype html>")


def test_a_supplied_claim_wins_over_the_disk_lookup(rich_quote, tmp_path):
    """A caller that just wrote the run knows where its evidence is; this function does not.

    `publish_run(root=...)` persists to a store of the caller's choosing and then renders. Without
    the receipt travelling with the call, the footer would look in the default store, miss the run
    and disclaim a pack sitting beside its own receipt: a false statement in the other direction.
    """
    from gridq.outputs import generate_artifacts

    persisted = persist_run(rich_quote.quote, rich_quote.site, root=tmp_path)
    claim = persisted.provenance_claim

    # No lookup could find it: the default store does not hold this run.
    supplied = generate_artifacts(
        persisted.quote, persisted.site, provenance_claim=claim
    ).consulting_report_html
    assert _as_rendered(claim) in supplied
    assert "no such record is persisted" not in supplied

    # The root override reaches the same claim for a caller that does not hold the receipt.
    looked_up = generate_artifacts(
        persisted.quote, persisted.site, run_store_root=tmp_path
    ).consulting_report_html
    assert _as_rendered(claim) in looked_up

    # And with neither, the footer says what is true of the default store: nothing backs this page.
    assert (
        "no such record is persisted"
        in generate_artifacts(persisted.quote, persisted.site).consulting_report_html
    )


def test_the_publication_path_loads_the_release_the_record_pins(rich_quote, tmp_path):
    """Both sanctioned paths must produce the same pack, not one missing its assumption register."""
    from gridq.assumptions import load_assumptions

    persisted = _persist(rich_quote, tmp_path)
    assert persisted.quote.assumption_version
    auto = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    explicit = render_publishable_pack(
        persisted.quote_id,
        run_store_root=tmp_path,
        assumptions=load_assumptions(persisted.quote.assumption_version),
    )
    assert auto == explicit


# --- the pack is still the pack --------------------------------------------------------------


def test_the_published_pack_is_rendered_from_the_reloaded_record(rich_quote, tmp_path):
    """The bytes a reader can fetch are the bytes the pack was built from."""
    persisted = _persist(rich_quote, tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    assert persisted.quote.quote_id in html
    assert persisted.site is not None
    assert escape(persisted.site.site_name) in html
    # Deterministic: publishing twice from the same files gives the same document.
    assert html == render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)


def test_the_published_pack_carries_no_em_or_en_dash(rich_quote, tmp_path):
    """The claim travels through the same dash rewrite as everything else the reader sees."""
    quote = rich_quote.quote.model_copy(update={"warnings": []})
    persisted = persist_run(quote, rich_quote.site, root=tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    assert chr(0x2014) not in html
    assert chr(0x2013) not in html


def test_a_run_persisted_without_a_site_does_not_claim_one(rich_quote, tmp_path):
    """The claim must not reach for evidence the run does not have."""
    persisted = persist_run(rich_quote.quote, None, root=tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    footer = _footer(html)
    assert "No site model was persisted with this run" in footer
    assert "site.json" not in footer


def test_an_unknown_quote_id_is_refused_rather_than_rendered(tmp_path):
    with pytest.raises(UnpersistedRunError):
        render_publishable_pack("quote_run_a30b80c0a4d44c4b", run_store_root=tmp_path)


@pytest.mark.parametrize("unsafe", ["", "..", "a/b", "a\\b"])
def test_an_unsafe_run_id_never_reaches_the_store(unsafe: str, tmp_path) -> None:
    with pytest.raises(ValueError, match="unsafe run id"):
        render_publishable_pack(unsafe, run_store_root=tmp_path)


def test_a_missing_site_model_is_not_invented_by_the_publication_path(rich_quote, tmp_path):
    persisted = persist_run(rich_quote.quote, None, root=tmp_path)
    html = render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    assert "no site model was supplied alongside the run record" in html


def test_the_quote_record_itself_is_untouched_by_publication(rich_quote, tmp_path):
    """Persisting and rendering must not mutate the record the caller still holds."""
    before = rich_quote.quote.model_dump_json()
    persisted = persist_run(rich_quote.quote, rich_quote.site, root=tmp_path)
    render_publishable_pack(persisted.quote_id, run_store_root=tmp_path)
    assert rich_quote.quote.model_dump_json() == before
    assert isinstance(persisted.quote, s.QuoteRecord)
