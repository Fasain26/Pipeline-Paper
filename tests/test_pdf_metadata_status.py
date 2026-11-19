import pymupdf

from src.pdf.metadata import doi_from_pages, doi_status, title_status


def make_two_page_pdf() -> bytes:
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Title and authors only, no identifier here", fontsize=12)
    page2 = doc.new_page()
    page2.insert_text((72, 72), "JMIR AI 2026;5:e105057; doi: 10.2196/105057 Keywords: x", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def test_doi_on_second_page_needs_two_pages():
    with pymupdf.open(stream=make_two_page_pdf(), filetype="pdf") as doc:
        assert doi_from_pages(doc, 1) is None
        assert doi_from_pages(doc, 2) == "10.2196/105057"


def test_title_exact_match():
    assert title_status("A Title About Sepsis Prediction", "A Title About Sepsis Prediction") == "match"


def test_title_partial_when_subtitle_is_missing():
    pdf = "Predictive model for acute kidney injury in critical patients with pulmonary embolism based on machine learning"
    truth = pdf + ": A multicenter clinical study"
    assert title_status(pdf, truth) == "partial"


def test_title_wrong():
    assert title_status("Completely different heading text here", "Machine learning for sepsis mortality prediction") == "wrong"


def test_title_missing_and_not_assessable():
    assert title_status(None, "Some truth title") == "missing"
    assert title_status("anything", None) is None


def test_doi_statuses():
    assert doi_status("10.1/a", "10.1/a") == "match"
    assert doi_status("10.1/b", "10.1/a") == "wrong"
    assert doi_status(None, "10.1/a") == "missing"
    assert doi_status("10.1/a", None) is None