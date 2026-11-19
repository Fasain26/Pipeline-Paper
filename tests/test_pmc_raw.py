from src.ingestion.pmc_raw import _md5_from_url


def test_md5_is_read_from_s3_url():
    url = "s3://pmc-oa-opendata/PMC1.1/PMC1.1.pdf?md5=9c91f19803174073235fe352906e2ab0"
    assert _md5_from_url(url) == "9c91f19803174073235fe352906e2ab0"


def test_md5_missing_returns_none():
    assert _md5_from_url(None) is None
    assert _md5_from_url("s3://bucket/key") is None