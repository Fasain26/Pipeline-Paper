import pymupdf

from src.pdf.extract import _clean_text
from src.pdf.metadata import abstract_from_xml, abstract_recall, extract_doi, extract_title


def make_pdf_with_title() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Journal Header", fontsize=9)
    page.insert_text((72, 110), "Machine Learning for Early Sepsis Prediction", fontsize=22)
    for i in range(10):
        page.insert_text((72, 200 + 14 * i), f"Body text line number {i}", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def test_title_is_the_largest_text_on_first_page():
    with pymupdf.open(stream=make_pdf_with_title(), filetype="pdf") as doc:
        assert extract_title(doc) == "Machine Learning for Early Sepsis Prediction"


def test_doi_found_after_url_prefix():
    text = "npj digital medicine https://doi.org/10.1038/s41746-026-02993-9 Expert Augmented"
    assert extract_doi(text) == "10.1038/s41746-026-02993-9"


def test_doi_split_by_newline_and_glued_word():
    text = "https://doi.org/10.1371/\njournal.pone.0362057Editor: Someone"
    assert extract_doi(text) == "10.1371/journal.pone.0362057"


def test_no_doi_returns_none():
    assert extract_doi("no identifier here") is None


def test_abstract_is_read_from_jats_xml():
    xml = (
        b'<?xml version="1.0"?><article><front><article-meta>'
        b"<abstract><p>Sepsis is <italic>common</italic>.</p></abstract>"
        b'<abstract abstract-type="graphical"><p>ignored</p></abstract>'
        b"</article-meta></front></article>"
    )
    assert abstract_from_xml(xml) == "Sepsis is common."


def test_abstract_recall_counts_shared_words():
    assert abstract_recall("sepsis prediction model", "A model for sepsis outcome") == round(2 / 3, 3)


def test_ligatures_are_expanded():
    assert _clean_text("Af\ufb01liated and classi\ufb01cation") == "Affiliated and classification"