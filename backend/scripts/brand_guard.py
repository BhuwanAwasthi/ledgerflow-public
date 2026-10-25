"""Brand guard: fail when a banned term appears in a tracked file, a tracked path or a commit message.

Terms live in config/brand_guard.yaml. Text files are scanned as text. PDFs are scanned through PyMuPDF
(metadata, XMP and page text) and PNGs through their text chunks, since a raw scan of compressed bytes
would only find noise. Any other binary type fails as unscannable, so a new format cannot slip past.

    uv --directory backend run python -m scripts.brand_guard                       # tracked files
    uv --directory backend run python -m scripts.brand_guard --commits A..B        # + commit messages
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from app.config_loader import BrandGuardConfig, load_brand_guard

REPO = Path(__file__).resolve().parents[2]
NULL_SHA = "0" * 40
_WORD_CHAR = "A-Za-z0-9_"

Terms = list[tuple[str, re.Pattern[str]]]


@dataclass(frozen=True)
class Hit:
    where: str  # "path:line", "path (pdf author)", "commit 1a2b3c4d5e:2"
    term: str
    text: str

    def __str__(self) -> str:
        return f"{self.where}: '{self.term}' in: {self.text}"


def compile_terms(cfg: BrandGuardConfig) -> Terms:
    """Substrings: case-insensitive, any whitespace between words. Words: case-sensitive, whole word."""
    terms: Terms = [
        (t, re.compile(r"\s+".join(re.escape(w) for w in t.split()), re.IGNORECASE)) for t in cfg.substrings
    ]
    terms += [(t, re.compile(rf"(?<![{_WORD_CHAR}]){re.escape(t)}(?![{_WORD_CHAR}])")) for t in cfg.words]
    return terms


def scan_text(text: str, where: str, terms: Terms) -> list[Hit]:
    hits: list[Hit] = []
    for term, pat in terms:
        for m in pat.finditer(text):
            n = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[n - 1] if text else ""
            hits.append(Hit(f"{where}:{n}", term, line.strip()[:160]))
    return hits


def scan_pdf(data: bytes, where: str, terms: Terms) -> list[Hit]:
    hits: list[Hit] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for key, value in (doc.metadata or {}).items():
            if value:
                hits += scan_text(str(value), f"{where} (pdf {key})", terms)
        hits += scan_text(doc.get_xml_metadata(), f"{where} (pdf xmp)", terms)
        for i, page in enumerate(doc, start=1):
            hits += scan_text(page.get_text(), f"{where} (page {i})", terms)
    return hits


def scan_png(data: bytes, where: str, terms: Terms) -> list[Hit]:
    hits: list[Hit] = []
    with Image.open(io.BytesIO(data)) as im:
        for key, value in getattr(im, "text", {}).items():
            hits += scan_text(f"{key} {value}", f"{where} (png text)", terms)
    return hits


BINARY_SCANNERS = {".pdf": scan_pdf, ".png": scan_png}


def scan_file(path: str, data: bytes, terms: Terms) -> tuple[list[Hit], str | None]:
    """Hits in the path and the content, plus an error when the file type cannot be scanned."""
    hits = scan_text(path, f"{path} (path)", terms)
    scanner = BINARY_SCANNERS.get(Path(path).suffix.lower())
    if scanner is not None:
        try:
            return hits + scanner(data, path, terms), None
        except (RuntimeError, ValueError, OSError) as e:  # corrupt file (pymupdf FileDataError, PIL): fail
            return hits, f"{path}: cannot be scanned ({type(e).__name__}: {e})"
    if b"\0" in data[:8192]:
        return hits, f"{path}: binary file type cannot be scanned (add a scanner in scripts/brand_guard.py)"
    return hits + scan_text(data.decode("utf-8", errors="replace"), path, terms), None


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def tracked_files(cfg: BrandGuardConfig) -> list[str]:
    excluded = set(cfg.exclude)
    paths = [p for p in _git("ls-files", "-z").split("\0") if p]
    return [p for p in paths if p not in excluded and (REPO / p).is_file()]


def commit_log_args(rev_range: str) -> list[str]:
    """`A..B` for a push. A new branch (A = zeros) or a rewritten history (A unknown) checks only B."""
    before, sep, after = rev_range.partition("..")
    if not sep:
        return [rev_range]
    known = before != NULL_SHA and subprocess.run(
        ["git", "cat-file", "-e", f"{before}^{{commit}}"], cwd=REPO, capture_output=True, check=False
    ).returncode == 0
    return [rev_range] if known else ["-1", after]


def scan_commits(rev_range: str, terms: Terms) -> list[Hit]:
    hits: list[Hit] = []
    log = _git("log", "--format=%H%x1f%B%x1e", *commit_log_args(rev_range))
    for entry in log.split("\x1e"):
        sha, _, message = entry.strip().partition("\x1f")
        if sha:
            hits += scan_text(message, f"commit {sha[:10]}", terms)
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commits", metavar="RANGE", help="also check commit messages in this git range")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):  # a cp1252 pipe on Windows must not crash on a hit
        sys.stdout.reconfigure(errors="backslashreplace")

    cfg = load_brand_guard(REPO / "config")
    terms = compile_terms(cfg)
    hits: list[Hit] = []
    errors: list[str] = []
    files = tracked_files(cfg)
    for path in files:
        file_hits, error = scan_file(path, (REPO / path).read_bytes(), terms)
        hits += file_hits
        if error:
            errors.append(error)
    if args.commits:
        hits += scan_commits(args.commits, terms)

    for hit in hits:
        print(hit)
    for error in errors:
        print(error)
    if hits or errors:
        print(f"brand guard: FAILED ({len(hits)} banned term(s), {len(errors)} unscannable file(s))")
        return 1
    scope = f" and commits {args.commits}" if args.commits else ""
    print(f"brand guard: ok ({len(files)} tracked files{scope})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
