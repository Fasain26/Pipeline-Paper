import pymupdf
import pytest

from src.pdf.extract import ExtractionError, _clean_text, extract_pdf


def make_pdf(pages: list[list[str]]) -> bytes:
    """Buat PDF kecil: tiap elemen `pages` adalah daftar baris teks untuk satu halaman."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 72 + 14 * i), line)
    data = doc.tobytes()
    doc.close()
    return data


def test_text_pdf_is_extracted():
    line = "The quick brown fox jumps over the lazy dog."
    result = extract_pdf(make_pdf([[line] * 30, [line] * 30]))
    assert result["page_count"] == 2
    assert "quick brown fox" in result["full_text"]
    assert result["needs_ocr"] is False


def test_blank_pdf_is_flagged_as_needing_ocr():
    result = extract_pdf(make_pdf([[]]))
    assert result["char_count"] == 0
    assert result["needs_ocr"] is True


def test_corrupt_pdf_raises_extraction_error():
    with pytest.raises(ExtractionError):
        extract_pdf(b"ini bukan pdf")


def test_nul_characters_are_removed():
    assert _clean_text("a\x00b") == "ab"