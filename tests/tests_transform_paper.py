from src.transform.papers import validate_records


def make_record(**overrides):
    """Record arXiv yang valid; sebagian kolom bisa ditimpa untuk membuat kasus rusak."""
    base = {
        "source": "arxiv",
        "source_id": "2601.00001",
        "version": 1,
        "doi": None,
        "title": "A Title",
        "abstract": "Some abstract.",
        "authors": ["Ada Lovelace"],
        "published": "2026-01-02T03:04:05Z",
        "updated": "2026-01-02T03:04:05Z",
        "primary_category": "cs.LG",
        "categories": ["cs.LG"],
        "journal_ref": None,
        "pdf_url": "https://arxiv.org/pdf/2601.00001v1",
        "abs_url": "http://arxiv.org/abs/2601.00001v1",
    }
    base.update(overrides)
    return base


def run(*records):
    return validate_records([(rec, "raw/key.xml") for rec in records])


def test_valid_record_passes():
    result = run(make_record())
    assert len(result.valid) == 1
    assert result.rejected == []
    assert result.valid[0].published_at.tzinfo is not None


def test_blank_title_is_rejected():
    result = run(make_record(title="   "))
    assert result.valid == []
    assert len(result.rejected) == 1
    assert "title" in result.rejected[0]["reason"]


def test_missing_authors_is_rejected():
    result = run(make_record(authors=[]))
    assert len(result.rejected) == 1
    assert "authors" in result.rejected[0]["reason"]


def test_invalid_date_is_rejected():
    result = run(make_record(published="bukan-tanggal"))
    assert len(result.rejected) == 1
    assert "published_at" in result.rejected[0]["reason"]


def test_updated_before_published_is_rejected():
    result = run(make_record(published="2026-03-01T00:00:00Z", updated="2026-02-01T00:00:00Z"))
    assert len(result.rejected) == 1
    assert "updated_at" in result.rejected[0]["reason"]


def test_future_date_is_rejected():
    result = run(make_record(published="2999-01-01T00:00:00Z", updated="2999-01-01T00:00:00Z"))
    assert len(result.rejected) == 1
    assert "published_at" in result.rejected[0]["reason"]


def test_doi_is_normalized_and_bad_doi_becomes_none():
    good = run(make_record(doi="https://DOI.org/10.1234/ABC.def"))
    assert good.valid[0].doi == "10.1234/abc.def"

    bad = run(make_record(doi="bukan-doi"))
    assert len(bad.valid) == 1
    assert bad.valid[0].doi is None


def test_authors_are_cleaned():
    result = run(make_record(authors=["  Ada   Lovelace ", "Ada Lovelace", "Alan Turing"]))
    assert result.valid[0].authors == ["Ada Lovelace", "Alan Turing"]


def test_duplicates_keep_highest_version():
    result = run(make_record(version=1), make_record(version=2))
    assert len(result.valid) == 1
    assert result.valid[0].version == 2
    assert result.duplicates_dropped == 1