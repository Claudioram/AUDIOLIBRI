"""
PDF parsing: extract chapters with text via PyMuPDF.
Uses MiniMax LLM to identify chapters when TOC is absent.
Falls back to font-size heuristics or regex if LLM is unavailable.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # type: ignore[assignment]


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class Chapter:
    order: int
    title: str
    raw_text: str
    start_page: int
    end_page: int
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.char_count = len(self.raw_text)


# ── Constants ─────────────────────────────────────────────────────────────────

_CHAPTER_REGEX = re.compile(
    r"^\s*(?:Capitolo|Chapter|CAPITOLO|CHAPTER|Parte|Sezione)\s+"
    r"(?:[IVX]+|\d+)[.\s]",
    re.MULTILINE | re.IGNORECASE,
)

_NUMBERED_HEADING = re.compile(r"^\s*\d{1,2}\.\s+[A-ZÀÁÂÃÄÅÆÈÉÊËÌÍÎÏÒÓÔÙÚ]", re.MULTILINE)

_FRONT_MATTER_TITLES = re.compile(
    r"^\s*(Indice|Sommario|Table of Contents|Prefazione|Introduzione|"
    r"Ringraziamenti|Dedica|Colophon|Copyright|Copertina)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_FRONT_MATTER_TOC = re.compile(
    r"^\s*(Indice|Sommario|Table of Contents|Prefazione|Introduzione|"
    r"Ringraziamenti|Dedica|Colophon|Copyright|Copertina|Appendice|Bibliography|"
    r"Note|Glossario|Index)\s*$",
    re.IGNORECASE,
)

_FOOTNOTE_LINE = re.compile(r"^\s*\d{1,3}\s+\S")
_HYPHEN_BREAK = re.compile(r"(\w+)-\n(\w+)")

_MIN_CHAPTER_CHARS = 800   # capitoli con meno caratteri vengono uniti al successivo
_MAX_SANE_CHAPTERS = 60    # oltre questo numero il risultato è quasi certamente rumore


# ── Public API ────────────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path) -> list[Chapter]:
    """Parse a PDF and return a list of Chapter objects with raw text."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed")

    doc = fitz.open(str(pdf_path))
    logger.info(f"Opened PDF: {pdf_path.name}, {doc.page_count} pages")

    # Check if this is a scanned PDF (very little text per page)
    if _is_scanned(doc):
        logger.warning("PDF appears to be scanned — attempting OCR fallback")
        doc = _ocr_fallback(doc, pdf_path)

    chapters = _extract_chapters(doc)

    if not chapters:
        logger.warning("No chapters found; treating entire document as one chapter")
        full_text = _extract_page_range(doc, 0, doc.page_count - 1)
        chapters = [Chapter(order=1, title="Documento", raw_text=full_text, start_page=0, end_page=doc.page_count - 1)]

    logger.info(f"Extracted {len(chapters)} chapters")
    return chapters


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_scanned(doc) -> bool:
    """Return True if the PDF has very little extractable text (likely scanned)."""
    total_chars = 0
    sample_pages = min(10, doc.page_count)
    for i in range(sample_pages):
        total_chars += len(doc[i].get_text())
    avg_chars = total_chars / max(sample_pages, 1)
    return avg_chars < 100


def _ocr_fallback(doc, pdf_path: Path):
    """Run Surya OCR on scanned PDF pages and return a new in-memory fitz document."""
    try:
        from surya.ocr import run_ocr
        from surya.model.detection.model import load_model as load_det_model
        from surya.model.detection.processor import load_processor as load_det_processor
        from surya.model.recognition.model import load_model as load_rec_model
        from surya.model.recognition.processor import load_processor as load_rec_processor
        from PIL import Image
    except ImportError:
        logger.error("Surya OCR not available; returning original document without OCR")
        return doc

    logger.info("Loading Surya OCR models…")
    det_processor, det_model = load_det_processor(), load_det_model()
    rec_model, rec_processor = load_rec_model(), load_rec_processor()

    pages_pil: list[Image.Image] = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        pages_pil.append(img)

    results = run_ocr(pages_pil, [["it"]] * len(pages_pil), det_model, det_processor, rec_model, rec_processor)

    # Build a new in-memory PDF with the OCR text overlaid
    new_doc = fitz.open()
    for page_result, orig_page in zip(results, doc):
        new_page = new_doc.new_page(width=orig_page.rect.width, height=orig_page.rect.height)
        text = "\n".join(line.text for line in page_result.text_lines)
        new_page.insert_text((10, 20), text, fontsize=10)

    return new_doc


def _extract_chapters(doc) -> list[Chapter]:
    """Try MiniMax LLM first, then font heuristic, then regex."""
    logger.debug("Trying MiniMax LLM for chapter detection")
    chapters = _chapters_from_minimax(doc)
    if chapters:
        return chapters

    logger.debug("MiniMax LLM failed — trying font-size heuristic")
    chapters = _chapters_from_font_heuristic(doc)
    if chapters:
        return chapters

    logger.debug("Font heuristic failed — trying regex headings")
    return _chapters_from_regex(doc)


def _chapters_from_minimax(doc) -> list[Chapter]:
    """Pass full PDF text to MiniMax LLM for chapter analysis."""
    from app.config import settings
    if not settings.minimax_api_key:
        return []

    # Costruisce il testo completo del PDF con marcatori di pagina
    pages: list[str] = []
    for page_num in range(doc.page_count):
        text = doc[page_num].get_text().strip()
        if text:
            pages.append(f"[PAGINA {page_num + 1}]\n{text}")

    if not pages:
        return []

    full_text = "\n\n".join(pages)

    # Se il testo supera ~400k caratteri, invia le prime 300 pagine (evita timeout)
    MAX_CHARS = 400_000
    if len(full_text) > MAX_CHARS:
        full_text = full_text[:MAX_CHARS]
        logger.info(f"PDF text truncated to {MAX_CHARS} chars for MiniMax analysis")

    prompt = (
        "Analizza il testo completo di questo libro PDF. "
        "Identifica TUTTI i capitoli veri del libro (non sottosezioni, non pagine di indice, "
        "non copyright, non dediche, non note a piè di pagina).\n"
        "Per ogni capitolo indica il titolo esatto e il numero di pagina dove inizia "
        "(i numeri di pagina sono indicati dai marcatori [PAGINA N]).\n"
        "Rispondi ESCLUSIVAMENTE con un array JSON valido, senza testo aggiuntivo:\n"
        '[{"titolo": "Nome capitolo", "pagina": N}, ...]\n\n'
        f"{full_text}"
    )

    try:
        logger.info(f"Sending {len(full_text):,} chars to MiniMax for chapter detection")
        resp = httpx.post(
            "https://api.minimax.io/v1/text/chatcompletion_v2",
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "MiniMax-Text-01",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 4000,
            },
            params={"GroupId": settings.minimax_group_id},
            timeout=120,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"MiniMax raw response: {json.dumps(data)[:500]}")

        raw = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        logger.info(f"MiniMax chapter detection response: {raw[:500]}")

        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            logger.warning(f"MiniMax response contained no JSON array: {raw[:200]}")
            return []

        entries = json.loads(match.group(0))
        if not isinstance(entries, list) or not entries:
            return []

        validated: list[tuple[str, int]] = []
        for e in entries:
            title = str(e.get("titolo", "")).strip()
            page = int(e.get("pagina", 0)) - 1  # converti in 0-indexed
            if title and 0 <= page < doc.page_count:
                validated.append((title, page))

        if len(validated) < 2:
            logger.warning(f"MiniMax returned too few chapters: {len(validated)}")
            return []

        seen: set[int] = set()
        deduped: list[tuple[str, int]] = []
        for title, pg in sorted(validated, key=lambda x: x[1]):
            if pg not in seen:
                deduped.append((title, pg))
                seen.add(pg)

        logger.info(f"MiniMax detected {len(deduped)} chapters")

        chapters: list[Chapter] = []
        for i, (title, start_page) in enumerate(deduped):
            end_page = deduped[i + 1][1] - 1 if i + 1 < len(deduped) else doc.page_count - 1
            end_page = max(start_page, end_page)
            raw_text = _extract_page_range(doc, start_page, end_page)
            raw_text = _clean_text(raw_text, doc, start_page, end_page)
            chapters.append(Chapter(
                order=i + 1,
                title=title,
                raw_text=raw_text,
                start_page=start_page,
                end_page=end_page,
            ))

        return _merge_short_chapters(chapters)

    except Exception as exc:
        logger.warning(f"MiniMax chapter detection failed: {exc}")
        return []


def _merge_short_chapters(chapters: list[Chapter]) -> list[Chapter]:
    """Merge chapters shorter than _MIN_CHAPTER_CHARS into the next chapter."""
    if not chapters:
        return chapters

    merged: list[Chapter] = []
    i = 0
    while i < len(chapters):
        ch = chapters[i]
        # Absorb all following short chapters
        while ch.char_count < _MIN_CHAPTER_CHARS and i + 1 < len(chapters):
            nxt = chapters[i + 1]
            ch = Chapter(
                order=ch.order,
                title=ch.title,
                raw_text=ch.raw_text + "\n" + nxt.raw_text,
                start_page=ch.start_page,
                end_page=nxt.end_page,
            )
            i += 1
        merged.append(ch)
        i += 1

    # Re-number
    for idx, ch in enumerate(merged, 1):
        ch.order = idx

    return merged


def _chapters_from_toc(doc, toc: list) -> list[Chapter]:
    """Build chapters from PyMuPDF TOC — picks the level that yields 2..50 real chapters."""
    levels = sorted({entry[0] for entry in toc})

    best_entries: list[tuple[str, int]] = []
    for level in levels:
        candidates = [e for e in toc if e[0] == level]
        validated: list[tuple[str, int]] = []
        for entry in candidates:
            title, page = entry[1], entry[2]
            if _FRONT_MATTER_TOC.match(title.strip()):
                continue
            page_idx = max(0, page - 1)
            if page_idx < doc.page_count:
                validated.append((title.strip(), page_idx))

        if 2 <= len(validated) <= _MAX_SANE_CHAPTERS:
            best_entries = validated
            logger.debug(f"TOC level {level} → {len(validated)} chapters")
            break

    if len(best_entries) < 2:
        return []

    chapters: list[Chapter] = []
    for i, (title, start_page) in enumerate(best_entries):
        end_page = best_entries[i + 1][1] - 1 if i + 1 < len(best_entries) else doc.page_count - 1
        end_page = max(start_page, end_page)

        raw_text = _extract_page_range(doc, start_page, end_page)
        raw_text = _clean_text(raw_text, doc, start_page, end_page)

        chapters.append(Chapter(
            order=i + 1,
            title=title,
            raw_text=raw_text,
            start_page=start_page,
            end_page=end_page,
        ))

    return _merge_short_chapters(chapters)


def _chapters_from_font_heuristic(doc) -> list[Chapter]:
    """Detect chapter headings by font size — uses only the top 1% of sizes."""
    all_sizes: list[float] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        all_sizes.append(span["size"])

    if not all_sizes:
        return []

    all_sizes.sort()
    body_size = all_sizes[int(len(all_sizes) * 0.50)]  # median = corpo testo
    p99 = all_sizes[int(len(all_sizes) * 0.99)]

    # Heading deve essere significativamente più grande del corpo
    heading_threshold = max(body_size * 1.25, p99 * 0.95)

    heading_pages: list[tuple[str, int]] = []
    for page_num, page in enumerate(doc):
        page_text = page.get_text().strip()
        # Salta pagine con pochissimo testo (copertina, pagina vuota)
        if len(page_text) < 50:
            continue
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = " ".join(s["text"] for s in line.get("spans", [])).strip()
                sizes = [s["size"] for s in line.get("spans", []) if s["text"].strip()]
                if not sizes or not text:
                    continue
                avg_size = sum(sizes) / len(sizes)
                # Solo righe corte con font grande e non front matter
                if (avg_size >= heading_threshold
                        and 3 <= len(text) <= 80
                        and not _FRONT_MATTER_TOC.match(text)):
                    heading_pages.append((text, page_num))
                    break

    # Deduplica per pagina
    seen_pages: set[int] = set()
    deduped: list[tuple[str, int]] = []
    for title, pg in heading_pages:
        if pg not in seen_pages:
            deduped.append((title, pg))
            seen_pages.add(pg)

    if len(deduped) < 2 or len(deduped) > _MAX_SANE_CHAPTERS:
        return []

    chapters: list[Chapter] = []
    for i, (title, start_page) in enumerate(deduped):
        end_page = deduped[i + 1][1] - 1 if i + 1 < len(deduped) else doc.page_count - 1
        end_page = max(start_page, end_page)

        raw_text = _extract_page_range(doc, start_page, end_page)
        raw_text = _clean_text(raw_text, doc, start_page, end_page)

        chapters.append(Chapter(
            order=i + 1,
            title=title,
            raw_text=raw_text,
            start_page=start_page,
            end_page=end_page,
        ))

    return _merge_short_chapters(chapters)


def _chapters_from_regex(doc) -> list[Chapter]:
    """Fall back to regex pattern matching across the full document text."""
    pages_text = [doc[i].get_text() for i in range(doc.page_count)]

    heading_positions: list[tuple[str, int]] = []  # (title, page_idx)
    for page_idx, text in enumerate(pages_text):
        for pattern in (_CHAPTER_REGEX, _NUMBERED_HEADING):
            for m in pattern.finditer(text):
                line = m.group(0).strip()
                heading_positions.append((line, page_idx))
                break

    if len(heading_positions) < 2:
        return []

    # Deduplicate by page
    seen: set[int] = set()
    deduped: list[tuple[str, int]] = []
    for title, pg in heading_positions:
        if pg not in seen:
            deduped.append((title, pg))
            seen.add(pg)

    chapters: list[Chapter] = []
    for i, (title, start_page) in enumerate(deduped):
        end_page = deduped[i + 1][1] - 1 if i + 1 < len(deduped) else doc.page_count - 1
        end_page = max(start_page, end_page)

        raw_text = _extract_page_range(doc, start_page, end_page)
        raw_text = _clean_text(raw_text, doc, start_page, end_page)

        chapters.append(Chapter(
            order=i + 1,
            title=title,
            raw_text=raw_text,
            start_page=start_page,
            end_page=end_page,
        ))

    return _merge_short_chapters(chapters)


def _extract_page_range(doc, start: int, end: int) -> str:
    """Extract and join text from a range of pages."""
    parts: list[str] = []
    for i in range(start, end + 1):
        if i < doc.page_count:
            parts.append(doc[i].get_text())
    return "\n".join(parts)


def _clean_text(text: str, doc, start_page: int, end_page: int) -> str:
    """Remove recurring headers/footers, fix hyphenation, normalise whitespace."""
    text = _remove_running_headers_footers(text, doc, start_page, end_page)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _remove_footnote_references(text)
    text = _normalise_paragraphs(text)
    return text.strip()


def _remove_running_headers_footers(text: str, doc, start: int, end: int) -> str:
    """
    Identify lines that appear in >70% of the pages in this range and remove them.
    These are typically page headers/footers.
    """
    pages_in_range = end - start + 1
    if pages_in_range < 3:
        return text

    line_counts: Counter = Counter()
    for i in range(start, end + 1):
        if i < doc.page_count:
            for line in doc[i].get_text().splitlines():
                stripped = line.strip()
                if 3 < len(stripped) < 80:
                    line_counts[stripped] += 1

    threshold = pages_in_range * 0.70
    recurring = {line for line, count in line_counts.items() if count >= threshold}

    if not recurring:
        return text

    cleaned_lines = [
        line for line in text.splitlines()
        if line.strip() not in recurring
    ]
    return "\n".join(cleaned_lines)


def _remove_footnote_references(text: str) -> str:
    """Remove inline footnote markers like [1], ¹, ²."""
    text = re.sub(r"\[\d{1,3}\]", "", text)
    text = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", text)
    return text


def _normalise_paragraphs(text: str) -> str:
    """Collapse multiple blank lines to a single blank line; trim lines."""
    lines = text.splitlines()
    result: list[str] = []
    blank_count = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped:
            blank_count = 0
            result.append(stripped)
        else:
            blank_count += 1
            if blank_count == 1:
                result.append("")
    return "\n".join(result)


def extract_book_metadata(pdf_path: Path) -> dict:
    """Extract title, author, and page count from a PDF."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed")

    doc = fitz.open(str(pdf_path))
    meta = doc.metadata or {}

    title = meta.get("title") or pdf_path.stem.replace("_", " ").replace("-", " ").title()
    author = meta.get("author") or None
    total_pages = doc.page_count

    return {"title": title, "author": author, "total_pages": total_pages}
