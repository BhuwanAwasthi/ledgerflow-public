"""Brand guard (scripts/brand_guard.py): what it flags, what it must not flag, and the repo is clean."""

import io

import pymupdf
import pytest
from PIL import Image, PngImagePlugin

from app.config_loader import read_config_files
from scripts import brand_guard as bg

CFG = bg.load_brand_guard(bg.REPO / "config")
TERMS = bg.compile_terms(CFG)


def terms_in(text: str) -> list[str]:
    return [h.term for h in bg.scan_text(text, "x", TERMS)]


@pytest.mark.parametrize(
    "text",
    [
        "space", "namespace", "workspace", "replace", "candidate POs", "POInferenceOutput.candidates",
        "Basalt", "pace yourself", "PACE", "asap", "ASAP", "PS-10", "PS-12a", "the user's workspace",
    ],
)
def test_allowed_vocabulary_is_not_flagged(text):
    assert terms_in(text) == []


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Zamp", "zamp"), ("ZampAI", "zamp"), ("built for zampai", "zamp"),
        ("ASA", "ASA"), ("the ASA team", "ASA"), ("(ASA)", "ASA"),
        ("Pace", "Pace"), ("PS-1", "PS-1"), ("PS-2.", "PS-2"), ("[PS-3]", "PS-3"),
        ("A Case Study", "case study"), ("case\n  study", "case study"), ("Take-Home task", "take-home"),
        ("Hiring", "hiring"), ("your recruiter", "recruiter"), ("Candidate Guide", "candidate guide"),
    ],
)
def test_banned_terms_are_flagged(text, term):
    assert term in terms_in(text)


def test_hit_reports_the_line():
    hits = bg.scan_text("one\ntwo\nby Zamp here\n", "docs/a.md", TERMS)
    assert [(h.where, h.text) for h in hits] == [("docs/a.md:3", "by Zamp here")]


def test_path_is_scanned():
    hits, error = bg.scan_file("docs/zamp-notes.md", b"clean\n", TERMS)
    assert error is None
    assert [h.where for h in hits] == ["docs/zamp-notes.md (path):1"]


def _pdf(**metadata: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), metadata.pop("text", "Invoice INV-1"))
    doc.set_metadata(metadata)
    return doc.tobytes()


def test_pdf_metadata_and_text_are_scanned():
    assert bg.scan_file("data/fixtures/a.pdf", _pdf(author="Demo Co.", title="Invoice"), TERMS) == ([], None)
    hits, _ = bg.scan_file("data/fixtures/a.pdf", _pdf(author="Zamp ASA"), TERMS)
    assert {h.where for h in hits} == {"data/fixtures/a.pdf (pdf author):1"}
    hits, _ = bg.scan_file("data/fixtures/b.pdf", _pdf(text="PS-1 sample"), TERMS)
    assert [h.where for h in hits] == ["data/fixtures/b.pdf (page 1):1"]


def test_png_text_chunks_are_scanned():
    def png(text: str) -> bytes:
        info = PngImagePlugin.PngInfo()
        info.add_text("Software", text)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, format="PNG", pnginfo=info)
        return buf.getvalue()

    assert bg.scan_file("docs/s.png", png("playwright"), TERMS) == ([], None)
    hits, _ = bg.scan_file("docs/s.png", png("ZampAI capture"), TERMS)
    assert [h.term for h in hits] == ["zamp"]


def test_unknown_binary_fails_as_unscannable():
    hits, error = bg.scan_file("data/a.xlsx", b"PK\x03\x04\x00\x00", TERMS)
    assert hits == [] and error is not None and "cannot be scanned" in error


@pytest.mark.parametrize("path", ["data/fixtures/x.pdf", "docs/x.png"])
def test_corrupt_pdf_or_png_fails_instead_of_passing(path):
    hits, error = bg.scan_file(path, b"not really this format", TERMS)
    assert hits == [] and error is not None and "cannot be scanned" in error


def test_only_the_term_list_and_these_tests_are_excluded():
    assert sorted(CFG.exclude) == ["backend/tests/test_brand_guard.py", "config/brand_guard.yaml"]


def test_term_list_is_not_app_config():
    assert "brand_guard.yaml" not in read_config_files(bg.REPO / "config")


def test_new_branch_or_rewritten_history_checks_only_the_pushed_commit():
    assert bg.commit_log_args(f"{bg.NULL_SHA}..abc123") == ["-1", "abc123"]
    assert bg.commit_log_args(f"{'f' * 40}..abc123") == ["-1", "abc123"]  # unknown base (force-push)
    assert bg.commit_log_args("HEAD") == ["HEAD"]


def test_tracked_tree_is_clean(capsys):
    assert bg.main([]) == 0, capsys.readouterr().out
