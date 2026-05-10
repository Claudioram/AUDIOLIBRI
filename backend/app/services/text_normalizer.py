"""
Text normalization pipeline for Italian TTS.
Converts numbers, abbreviations, dates, and special characters into
spoken Italian. Also inserts MiniMax pause markers between paragraphs.
"""
from __future__ import annotations

import re
from typing import Optional

try:
    from num2words import num2words
    _NUM2WORDS_AVAILABLE = True
except ImportError:
    _NUM2WORDS_AVAILABLE = False


# ── Abbreviation dictionary (Italian) ────────────────────────────────────────

ABBREVIATIONS: dict[str, str] = {
    # Titles
    "Sig.": "Signor",
    "Sig.ra": "Signora",
    "Sig.na": "Signorina",
    "Dott.": "Dottor",
    "Dott.ssa": "Dottoressa",
    "Prof.": "Professor",
    "Prof.ssa": "Professoressa",
    "Avv.": "Avvocato",
    "Ing.": "Ingegner",
    "Arch.": "Architetto",
    "On.": "Onorevole",
    "Sen.": "Senatore",
    "Gen.": "Generale",
    "Col.": "Colonnello",
    "Cap.": "Capitano",
    "Sgt.": "Sergente",
    "Sr.": "Signor",
    "Dr.": "Dottor",
    "Mr.": "Mister",
    "Mrs.": "Mistress",
    "Ms.": "Miss",
    # Common
    "ecc.": "eccetera",
    "etc.": "eccetera",
    "p.es.": "per esempio",
    "es.": "esempio",
    "cfr.": "confronta",
    "vd.": "vedi",
    "v.": "vedi",
    "op. cit.": "opera citata",
    "loc. cit.": "luogo citato",
    "ibid.": "ibidem",
    "ivi.": "ivi",
    # Publication
    "pag.": "pagina",
    "pp.": "pagine",
    "p.": "pagina",
    "vol.": "volume",
    "voll.": "volumi",
    "cap.": "capitolo",
    "art.": "articolo",
    "n.": "numero",
    "nr.": "numero",
    "fig.": "figura",
    "tab.": "tabella",
    "ed.": "edizione",
    "trad.": "traduzione",
    "a cura di": "a cura di",
    # Time / dates
    "sec.": "secolo",
    "secc.": "secoli",
    "a.C.": "avanti Cristo",
    "d.C.": "dopo Cristo",
    "A.D.": "anno Domini",
    # Units (expand to spoken form)
    "km": "chilometri",
    "km²": "chilometri quadrati",
    "m²": "metri quadrati",
    "m³": "metri cubi",
    "cm": "centimetri",
    "mm": "millimetri",
    "kg": "chilogrammi",
    "€": "euro",
    "$": "dollari",
    "%": "percento",
}

_ROMAN_NUMERALS = re.compile(
    r"\b(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\b"
)

_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATE_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

_ORDINAL_NUMBER = re.compile(r"\b(\d+)°\b")
_FOOTNOTE_MARKER = re.compile(r"\[\d{1,3}\]")
_SUPERSCRIPT = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+")
_QUOTES_GUILLEMETS = re.compile(r"«(.*?)»", re.DOTALL)
_QUOTES_LOW9 = re.compile("„(.*?)“", re.DOTALL)
_ELLIPSIS = re.compile(r"\.{3,}|…")
_DASH_VARIANTS = re.compile(r"[–—]")
_MULTIPLE_SPACES = re.compile(r"[ \t]+")
_MULTIPLE_NEWLINES = re.compile(r"\n{3,}")

_ITALIAN_MONTHS = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre",
}

# MiniMax pause markers
PAUSE_PARAGRAPH = "<#0.8#>"   # between paragraphs
PAUSE_SECTION = "<#1.5#>"     # before new chapter/section


# ── Public API ────────────────────────────────────────────────────────────────

def normalize_for_tts(text: str) -> str:
    """Full normalization pipeline: returns text ready for MiniMax TTS."""
    text = remove_footnote_markers(text)
    text = handle_quotes(text)
    text = expand_abbreviations(text)
    text = handle_dates(text)
    text = handle_ordinals(text)          # must run before numbers_to_italian_words
    text = numbers_to_italian_words(text)
    text = roman_numerals_to_words(text)
    text = normalise_punctuation(text)
    text = collapse_whitespace(text)
    # NOTE: pause markers (<#0.8#>) removed — MiniMax speech-02-hd reads them
    # as literal text instead of inserting silence. Natural \n\n paragraph
    # breaks already produce correct prosodic pauses in this model.
    return text.strip()


# ── Pipeline steps ────────────────────────────────────────────────────────────

def remove_footnote_markers(text: str) -> str:
    text = _FOOTNOTE_MARKER.sub("", text)
    text = _SUPERSCRIPT.sub("", text)
    return text


def handle_quotes(text: str) -> str:
    text = _QUOTES_GUILLEMETS.sub(r'"\1"', text)
    text = _QUOTES_LOW9.sub(r'"\1"', text)
    text = text.replace("'", "'").replace("`", "'")
    return text


def expand_abbreviations(text: str) -> str:
    # Sort by length descending to avoid partial replacement
    for abbr, expansion in sorted(ABBREVIATIONS.items(), key=lambda x: -len(x[0])):
        escaped = re.escape(abbr)
        text = re.sub(r"(?<!\w)" + escaped + r"(?!\w)", expansion, text)
    return text


def handle_dates(text: str) -> str:
    """Convert numeric dates to spoken Italian: 12/03/1995 → dodici marzo millenovecentonovantacinque."""

    def _replace_date(m: re.Match) -> str:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _date_to_words(day, month, year)

    text = _DATE_SLASH.sub(_replace_date, text)
    text = _DATE_DOT.sub(_replace_date, text)
    return text


def numbers_to_italian_words(text: str) -> str:
    """Replace all standalone numbers with Italian words."""
    if not _NUM2WORDS_AVAILABLE:
        return text

    def _replace_number(m: re.Match) -> str:
        raw = m.group(0).replace(".", "").replace(",", ".")
        try:
            val = float(raw) if "." in raw else int(raw)
            return num2words(val, lang="it")
        except (ValueError, TypeError):
            return m.group(0)

    # Match integers and decimals (with Italian comma separator too)
    text = re.sub(r"\b\d{1,3}(?:\.\d{3})*(?:,\d+)?\b", _replace_number, text)
    text = re.sub(r"\b\d+\b", _replace_number, text)
    return text


def roman_numerals_to_words(text: str) -> str:
    """Convert Roman numerals to Italian ordinal words."""
    if not _NUM2WORDS_AVAILABLE:
        return text

    def _replace_roman(m: re.Match) -> str:
        roman = m.group(1)
        # Skip single letters that are likely regular words (I, V)
        if len(roman) == 1 and roman in ("I", "V"):
            return roman
        val = _roman_to_int(roman)
        if val == 0:
            return roman
        try:
            return num2words(val, lang="it", to="ordinal")
        except Exception:
            return roman

    return _ROMAN_NUMERALS.sub(_replace_roman, text)


def handle_ordinals(text: str) -> str:
    """Convert 1°, 2°… to spoken ordinals."""
    if not _NUM2WORDS_AVAILABLE:
        return text

    def _replace(m: re.Match) -> str:
        n = int(m.group(1))
        try:
            return num2words(n, lang="it", to="ordinal")
        except Exception:
            return m.group(0)

    return _ORDINAL_NUMBER.sub(_replace, text)


def normalise_punctuation(text: str) -> str:
    text = _ELLIPSIS.sub("…", text)
    text = _DASH_VARIANTS.sub(" — ", text)
    return text


def collapse_whitespace(text: str) -> str:
    text = _MULTIPLE_SPACES.sub(" ", text)
    text = _MULTIPLE_NEWLINES.sub("\n\n", text)
    return text


def add_paragraph_pauses(text: str) -> str:
    """Replace paragraph breaks with the text followed by a MiniMax pause marker."""
    paragraphs = text.split("\n\n")
    joined = f" {PAUSE_PARAGRAPH}\n\n".join(p.strip() for p in paragraphs if p.strip())
    return joined


def split_into_chunks(text: str, max_chars: int = 50_000) -> list[str]:
    """
    Split long text into chunks of at most max_chars characters,
    breaking only at paragraph boundaries to preserve prosody.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the \n\n separator
        if current_len + para_len > max_chars and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = [para]
            current_len = para_len
        else:
            current_parts.append(para)
            current_len += para_len

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


# ── Private helpers ───────────────────────────────────────────────────────────

def _date_to_words(day: int, month: int, year: int) -> str:
    if not _NUM2WORDS_AVAILABLE:
        return f"{day}/{month}/{year}"

    month_name = _ITALIAN_MONTHS.get(month, str(month))
    day_words = num2words(day, lang="it")
    year_words = num2words(year, lang="it")
    return f"{day_words} {month_name} {year_words}"


def _roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to an integer. Returns 0 on failure."""
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50,
                 "C": 100, "D": 500, "M": 1000}
    result = 0
    prev = 0
    for char in reversed(s.upper()):
        val = roman_map.get(char, 0)
        if val == 0:
            return 0
        if val < prev:
            result -= val
        else:
            result += val
        prev = val
    return result
