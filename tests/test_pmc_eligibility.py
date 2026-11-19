from src.extractors.pmc import eligibility


def make_meta(**overrides):
    base = {
        "is_pmc_openaccess": True,
        "is_retracted": False,
        "license_code": "CC BY",
        "pdf_url": "s3://pmc-oa-opendata/PMC1.1/PMC1.1.pdf?md5=abc",
    }
    base.update(overrides)
    return base


def test_eligible_article():
    ok, reason = eligibility(make_meta())
    assert ok
    assert reason == "ok"


def test_not_in_open_access_subset():
    ok, reason = eligibility(make_meta(is_pmc_openaccess=False))
    assert not ok
    assert "Open Access" in reason


def test_retracted_article_rejected():
    ok, reason = eligibility(make_meta(is_retracted=True))
    assert not ok
    assert "retracted" in reason


def test_non_commercial_license_rejected():
    ok, reason = eligibility(make_meta(license_code="CC BY-NC"))
    assert not ok
    assert "lisensi" in reason


def test_missing_pdf_rejected():
    ok, reason = eligibility(make_meta(pdf_url=None))
    assert not ok
    assert "PDF" in reason