"""WP-OPS-11 criterion 3: what the grant application and the competition scope say about the MW
figure, quoted with document and section, and every text not held listed as an ask to the owner.

The documents stay where the owner keeps them (outside this repository). When a document is
present, its sha256 is checked and every quoted fragment is found in its text; when it is absent
(CI), that check is skipped and the register's own invariants still run. A synthetic .docx built
in a temporary directory shows the checks refuse a wrong document and a wrong fragment.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from gridq.contracts._base import NotApplicable, Unknown
from gridq.ops.dossier_model import AskHolder
from gridq.ops.mw_grant_text import (
    ASK_OFFER_LETTER,
    ASK_SUBMITTED_VERSION,
    GRANT_ASKS,
    GRANT_TEXT_REGISTER,
    GRANT_TEXT_ROOT_ENV,
    GrantDocument,
    GrantTextUnavailable,
    concept_relations,
    document_path,
    extract_text,
    normalise,
)

REPO = Path(__file__).resolve().parents[2]
R = GRANT_TEXT_REGISTER
HELD = [d for d in R.documents if d.kind != "not_held"]


def _text_or_skip(doc: GrantDocument) -> str:
    try:
        return extract_text(doc)
    except GrantTextUnavailable as exc:
        pytest.skip(f"{doc.doc_id}: {exc}")


# =============================================================================================
# The quotes are in the documents, word for word
# =============================================================================================


@pytest.mark.parametrize("doc", HELD, ids=lambda d: d.doc_id)
def test_every_quote_is_verbatim_in_its_registered_document(doc: GrantDocument) -> None:
    text = _text_or_skip(doc)
    quotes = [q for q in R.quotes if q.doc_id == doc.doc_id]
    assert quotes, doc.doc_id
    for q in quotes:
        for fragment in q.fragments:
            assert normalise(fragment) in text, (q.quote_id, fragment)
        for f in q.figures:
            if isinstance(f.figure, str):
                assert f.figure in q.text, (q.quote_id, f.figure)


def test_the_draft_states_no_50_mw_figure() -> None:
    for dv in R.divergences:
        for check in dv.absences:
            text = _text_or_skip(R.document(check.doc_id))
            assert re.search(check.pattern, text) is None, check.statement
    # ...while the held application does.
    held = _text_or_skip(R.document("q9_application_pdf"))
    assert re.search(r"(?<![0-9])~?50 ?MW", held)


def _docx(path: Path, *paragraphs: str) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def _doc(path: Path, sha: str) -> GrantDocument:
    return GrantDocument.model_validate(
        {
            "doc_id": "synthetic_docx",
            "kind": "application_draft",
            "title": "TEST FIXTURE draft",
            "location": "TEST FIXTURE/synthetic.docx",
            "sha256": sha,
            "reader": "docx_xml",
            "note": "TEST FIXTURE",
            "ask_id": NotApplicable(kind="not_applicable", reason="held"),
        }
    )


def test_the_reader_refuses_a_different_document_and_misses_a_wrong_fragment(
    tmp_path: Path,
) -> None:
    path = _docx(tmp_path / "synthetic.docx", "TEST FIXTURE a  ~50MW", "distributed   pilot")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    text = extract_text(_doc(path, sha), path)
    assert text == "TEST FIXTURE a ~50MW distributed pilot"
    assert "~50MW distributed pilot" in text
    assert "~60MW distributed pilot" not in text
    with pytest.raises(ValueError, match="sha256 differs"):
        extract_text(_doc(path, "0" * 64), path)
    with pytest.raises(GrantTextUnavailable):
        extract_text(_doc(path, sha), tmp_path / "absent.docx")


def test_documents_are_looked_for_under_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(GRANT_TEXT_ROOT_ENV, str(tmp_path))
    pdf = R.document("q9_application_pdf")
    assert document_path(pdf) == tmp_path / "Qu9 App - NeuraFlex.pdf"
    with pytest.raises(GrantTextUnavailable):
        extract_text(pdf)
    with pytest.raises(GrantTextUnavailable):
        document_path(R.document("theory_of_change"))


def test_a_grant_document_is_never_located_in_a_gridq_source_tree() -> None:
    bad = {**_doc(Path("x"), "0" * 64).model_dump(), "location": "Neura GitHub/uk/docs/q9.docx"}
    with pytest.raises(ValidationError, match="never located inside a GridQ source tree"):
        GrantDocument.model_validate(bad)


def test_no_grant_document_is_copied_into_the_repository() -> None:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    names = {Path(f).name for f in files}
    registered = {d.sha256 for d in HELD}
    for d in HELD:
        assert isinstance(d.location, str)
        assert Path(d.location).name not in names, d.location
    for f in files:
        if f.lower().endswith((".docx", ".pdf", ".gz")) and (REPO / f).is_file():
            assert hashlib.sha256((REPO / f).read_bytes()).hexdigest() not in registered, f


# =============================================================================================
# What the texts say, what they do not, and who holds the rest
# =============================================================================================


def test_the_application_states_a_distributed_50_mw_figure_and_the_competition_states_none() -> (
    None
):
    figures = {
        (q.doc_id, f.notion, f.figure)
        for q in R.grant_quotes()
        for f in q.figures
        if isinstance(f.figure, str)
    }
    assert ("q9_application_pdf", "distributed_capacity", "~50MW") in figures
    comp = {(n, f) for d, n, f in figures if d == "competition_2509_overview"}
    assert all(n in ("system_target", "deployment_scale") for n, _ in comp)
    assert not any("50" in f for _, f in comp)


def test_internal_notes_are_context_never_grant_text() -> None:
    notes = [q for q in R.quotes if R.document(q.doc_id).kind == "internal_notes"]
    assert notes
    assert not set(notes) & set(R.grant_quotes())
    assert {r.quote_id for r in concept_relations()} <= {q.quote_id for q in R.grant_quotes()}


def test_award_only_figures_stay_unknown_with_the_offer_letter_ask() -> None:
    assert [a.award_ref for a in R.award_figures] == ["AW-01", "AW-04", "AW-09"]
    for a in R.award_figures:
        assert isinstance(a.value, Unknown)
        assert a.ask_id == ASK_OFFER_LETTER.ask_id


def test_every_text_not_held_is_an_ask_to_the_owner() -> None:
    not_held = [d for d in R.documents if d.kind == "not_held"]
    assert {d.doc_id for d in not_held} == {
        "applicant_briefing",
        "assessor_guidance",
        "competition_themes",
        "grant_offer_letter",
        "letters_of_support",
        "theory_of_change",
    }
    asks = {a.ask_id: a for a in R.asks}
    for d in not_held:
        assert d.ask_id in asks
    assert R.asks == GRANT_ASKS
    for a in GRANT_ASKS:
        assert a.holder is AskHolder.NEURA_OWNER and a.scope == "dossier"


def test_which_application_version_was_submitted_stays_unknown_with_its_ask() -> None:
    assert {dv.topic for dv in R.divergences} == {"mw_figure", "pilot_window", "targets_2030"}
    for dv in R.divergences:
        assert isinstance(dv.submitted_version, Unknown)
        assert dv.ask_id == ASK_SUBMITTED_VERSION.ask_id


def test_the_concept_relations_carry_no_fit_or_score() -> None:
    from gridq.ops.mw_grant_text import ConceptRelation

    assert set(ConceptRelation.model_fields) == {
        "quote_id",
        "definition_id",
        "shared",
        "not_stated_in_text",
        "other_concepts_in_text",
    }
    rel = {(r.quote_id, r.definition_id): r for r in concept_relations()}
    pilot = rel[("app_demonstrator_pilot", "verified_flexible_mw")]
    assert pilot.shared == ("flexibility", "verified")
