"""
Tests for pdf_parser.py.

These tests use synthetic in-memory PDFs generated with PyMuPDF itself,
so they run without needing external PDF fixtures.
"""
import pytest
from pathlib import Path


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_pdf_with_toc(tmp_path: Path) -> Path:
    """Create a minimal PDF with a real TOC and three chapters."""
    try:
        import fitz
    except ImportError:
        pytest.skip("PyMuPDF not installed")

    doc = fitz.open()
    chapters = [
        ("Introduzione", "Questo è il testo dell'introduzione. " * 20),
        ("Capitolo 1 - Il viaggio", "Era una notte buia e tempestosa. " * 40),
        ("Capitolo 2 - L'incontro", "Il protagonista incontrò qualcuno. " * 40),
    ]

    for title, body in chapters:
        page = doc.new_page()
        page.insert_text((50, 50), title, fontsize=18)
        page.insert_text((50, 100), body, fontsize=11)

    # Add TOC (0-indexed pages)
    toc = [[1, title, i + 1] for i, (title, _) in enumerate(chapters)]
    doc.set_toc(toc)

    out = tmp_path / "sample_toc.pdf"
    doc.save(str(out))
    return out


def _make_pdf_without_toc(tmp_path: Path) -> Path:
    """PDF with chapter-like headings but no TOC."""
    try:
        import fitz
    except ImportError:
        pytest.skip("PyMuPDF not installed")

    doc = fitz.open()
    chapters = [
        ("Capitolo 1", "Prima parte del testo. " * 50),
        ("Capitolo 2", "Seconda parte del testo. " * 50),
        ("Capitolo 3", "Terza parte del testo. " * 50),
    ]
    for title, body in chapters:
        page = doc.new_page()
        # Title in large font
        page.insert_text((50, 50), title, fontsize=22)
        # Body in normal font
        page.insert_text((50, 100), body, fontsize=11)

    out = tmp_path / "sample_no_toc.pdf"
    doc.save(str(out))
    return out


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_parse_pdf_with_toc(tmp_path):
    from app.services.pdf_parser import parse_pdf

    pdf = _make_pdf_with_toc(tmp_path)
    chapters = parse_pdf(pdf)

    assert len(chapters) == 3
    assert chapters[0].order == 1
    assert "Introduzione" in chapters[0].title
    assert chapters[1].order == 2
    assert len(chapters[0].raw_text) > 10


def test_parse_pdf_chapter_text_non_empty(tmp_path):
    from app.services.pdf_parser import parse_pdf

    pdf = _make_pdf_with_toc(tmp_path)
    chapters = parse_pdf(pdf)

    for ch in chapters:
        assert ch.char_count > 0
        assert ch.raw_text.strip() != ""


def test_parse_pdf_without_toc_fallback(tmp_path):
    from app.services.pdf_parser import parse_pdf

    pdf = _make_pdf_without_toc(tmp_path)
    chapters = parse_pdf(pdf)

    # Should find at least 1 chapter even without TOC
    assert len(chapters) >= 1


def test_parse_pdf_orders_are_sequential(tmp_path):
    from app.services.pdf_parser import parse_pdf

    pdf = _make_pdf_with_toc(tmp_path)
    chapters = parse_pdf(pdf)
    orders = [ch.order for ch in chapters]
    assert orders == sorted(orders)
    assert orders[0] == 1


def test_extract_metadata_title(tmp_path):
    from app.services.pdf_parser import extract_book_metadata

    try:
        import fitz
    except ImportError:
        pytest.skip("PyMuPDF not installed")

    doc = fitz.open()
    doc.set_metadata({"title": "Il Nome della Rosa", "author": "Umberto Eco"})
    doc.new_page()
    out = tmp_path / "meta.pdf"
    doc.save(str(out))

    meta = extract_book_metadata(out)
    assert meta["title"] == "Il Nome della Rosa"
    assert meta["author"] == "Umberto Eco"
    assert meta["total_pages"] == 1


def test_extract_metadata_fallback_to_filename(tmp_path):
    from app.services.pdf_parser import extract_book_metadata

    try:
        import fitz
    except ImportError:
        pytest.skip("PyMuPDF not installed")

    doc = fitz.open()
    doc.new_page()
    out = tmp_path / "il_piccolo_principe.pdf"
    doc.save(str(out))

    meta = extract_book_metadata(out)
    assert "piccolo" in meta["title"].lower() or "principe" in meta["title"].lower()


def test_roman_numerals_helper():
    from app.services.pdf_parser import _remove_running_headers_footers
    # Just check the function exists and is callable; detailed logic tested in normalizer tests
    assert callable(_remove_running_headers_footers)


def test_hyphen_break_removed(tmp_path):
    from app.services.pdf_parser import _HYPHEN_BREAK

    text = "qual-\ncosa di bello"
    result = _HYPHEN_BREAK.sub(r"\1\2", text)
    assert "qualcosa" in result
    assert "-\n" not in result
