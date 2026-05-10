"""
Tests for text_normalizer.py.

Covers the most edge-case-prone parts:
- Numbers to Italian words
- Roman numerals
- Dates
- Abbreviations
- Pause insertion
- Chunking
"""
import pytest


@pytest.fixture(autouse=True)
def require_num2words():
    try:
        import num2words  # noqa: F401
    except ImportError:
        pytest.skip("num2words not installed")


# ── Number conversion ─────────────────────────────────────────────────────────

def test_integer_to_italian():
    from app.services.text_normalizer import numbers_to_italian_words

    result = numbers_to_italian_words("Nel 1492 Colombo scoprì l'America.")
    assert "millequattrocentonovantadue" in result.lower()


def test_year_not_split_digit_by_digit():
    from app.services.text_normalizer import numbers_to_italian_words

    result = numbers_to_italian_words("1984")
    # Should be a single compound word, not "uno-nove-otto-quattro"
    assert "millenovecento" in result.lower()


def test_small_number():
    from app.services.text_normalizer import numbers_to_italian_words

    result = numbers_to_italian_words("Ho 3 gatti.")
    assert "tre" in result.lower()


def test_zero():
    from app.services.text_normalizer import numbers_to_italian_words

    result = numbers_to_italian_words("Capitolo 0")
    assert "zero" in result.lower()


def test_price():
    from app.services.text_normalizer import numbers_to_italian_words

    result = numbers_to_italian_words("Costa 42 euro.")
    assert "quarantadue" in result.lower()


# ── Roman numerals ────────────────────────────────────────────────────────────

def test_roman_numeral_chapter():
    from app.services.text_normalizer import roman_numerals_to_words

    result = roman_numerals_to_words("Capitolo XIV")
    assert "quattoordicesimo" in result.lower() or "quattordicesimo" in result.lower()


def test_roman_numeral_century():
    from app.services.text_normalizer import roman_numerals_to_words

    result = roman_numerals_to_words("nel XIX secolo")
    assert "diciannove" in result.lower()


def test_single_roman_I_preserved():
    from app.services.text_normalizer import roman_numerals_to_words

    # Single-letter I and V should not be converted (ambiguous with pronouns)
    result = roman_numerals_to_words("I gatti dormono.")
    assert result == "I gatti dormono."


def test_roman_to_int_helper():
    from app.services.text_normalizer import _roman_to_int

    assert _roman_to_int("XIV") == 14
    assert _roman_to_int("XIX") == 19
    assert _roman_to_int("IV") == 4
    assert _roman_to_int("XLII") == 42
    assert _roman_to_int("M") == 1000
    assert _roman_to_int("MCMXCIX") == 1999
    assert _roman_to_int("") == 0
    assert _roman_to_int("NOTVALID") == 0


# ── Dates ─────────────────────────────────────────────────────────────────────

def test_date_slash_format():
    from app.services.text_normalizer import handle_dates

    result = handle_dates("Il 12/03/1995 fu un giorno speciale.")
    assert "dodici" in result.lower()
    assert "marzo" in result.lower()
    assert "millenovecentonovantacinque" in result.lower()


def test_date_dot_format():
    from app.services.text_normalizer import handle_dates

    result = handle_dates("Nato il 01.01.2000.")
    assert "uno" in result.lower() or "primo" in result.lower()
    assert "gennaio" in result.lower()
    assert "duemila" in result.lower()


# ── Abbreviations ─────────────────────────────────────────────────────────────

def test_expand_dott():
    from app.services.text_normalizer import expand_abbreviations

    result = expand_abbreviations("Il Dott. Rossi arrivò.")
    assert "Dottor" in result


def test_expand_ecc():
    from app.services.text_normalizer import expand_abbreviations

    result = expand_abbreviations("mele, pere, ecc.")
    assert "eccetera" in result
    assert "ecc." not in result


def test_expand_prof():
    from app.services.text_normalizer import expand_abbreviations

    result = expand_abbreviations("La Prof. Bianchi insegna.")
    assert "Professor" in result


def test_expand_avanti_cristo():
    from app.services.text_normalizer import expand_abbreviations

    result = expand_abbreviations("Giulio Cesare visse nel 44 a.C.")
    assert "avanti Cristo" in result


# ── Footnote removal ──────────────────────────────────────────────────────────

def test_footnote_brackets_removed():
    from app.services.text_normalizer import remove_footnote_markers

    result = remove_footnote_markers("Il testo[1] continua[23].")
    assert "[1]" not in result
    assert "[23]" not in result
    assert "Il testo continua." in result


def test_superscript_removed():
    from app.services.text_normalizer import remove_footnote_markers

    result = remove_footnote_markers("nota¹ e nota²")
    assert "¹" not in result
    assert "²" not in result


# ── Pauses ────────────────────────────────────────────────────────────────────

def test_paragraph_pause_inserted():
    from app.services.text_normalizer import add_paragraph_pauses, PAUSE_PARAGRAPH

    text = "Primo paragrafo.\n\nSecondo paragrafo."
    result = add_paragraph_pauses(text)
    assert PAUSE_PARAGRAPH in result


def test_no_pause_for_single_paragraph():
    from app.services.text_normalizer import add_paragraph_pauses, PAUSE_PARAGRAPH

    text = "Un solo paragrafo senza interruzioni."
    result = add_paragraph_pauses(text)
    assert PAUSE_PARAGRAPH not in result


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_short_text_not_split():
    from app.services.text_normalizer import split_into_chunks

    text = "Un testo corto."
    chunks = split_into_chunks(text, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_text_split_at_paragraph():
    from app.services.text_normalizer import split_into_chunks

    # Create a long text with clear paragraph breaks
    para = "A" * 200
    text = "\n\n".join([para] * 10)  # 2000+ chars
    chunks = split_into_chunks(text, max_chars=500)
    assert len(chunks) > 1
    # Each chunk should be roughly within the limit
    for ch in chunks:
        assert len(ch) <= 600  # some slack for the separator


# ── Full pipeline ─────────────────────────────────────────────────────────────

def test_normalize_for_tts_full_pipeline():
    from app.services.text_normalizer import normalize_for_tts

    text = (
        "Il Dott. Rossi nacque il 15/06/1960.\n\n"
        "Nel Capitolo XIV si parla di ecc.[1].\n\n"
        "«Ciao!» disse il protagonista."
    )
    result = normalize_for_tts(text)

    # Numbers should be words
    assert "1960" not in result
    assert "15" not in result
    # Footnote removed
    assert "[1]" not in result
    # Abbreviation expanded
    assert "Dottor" in result
    # Guillemets replaced
    assert "«" not in result
    assert "»" not in result
    # Pause marker present (two paragraphs)
    from app.services.text_normalizer import PAUSE_PARAGRAPH
    assert PAUSE_PARAGRAPH in result
