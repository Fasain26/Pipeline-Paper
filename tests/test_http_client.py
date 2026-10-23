import pytest
import requests

from src import http_client


class FakeResponse:
    """Pura-pura menjadi respons HTTP, cukup dengan atribut yang dipakai kode kita."""

    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    @property
    def ok(self):
        return self.status_code < 400


def _patch(monkeypatch, responses):
    """Ganti requests.get dengan versi palsu, dan matikan time.sleep agar tes cepat."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return responses[min(len(calls), len(responses)) - 1]

    monkeypatch.setattr(http_client.requests, "get", fake_get)
    monkeypatch.setattr(http_client.time, "sleep", lambda seconds: None)
    return calls


def test_retry_then_success(monkeypatch):
    calls = _patch(monkeypatch, [FakeResponse(429), FakeResponse(429), FakeResponse(200, "ok")])
    result = http_client.get_with_retry("https://example.org")
    assert result.text == "ok"
    assert len(calls) == 3


def test_no_retry_on_400(monkeypatch):
    calls = _patch(monkeypatch, [FakeResponse(400, "bad request")])
    with pytest.raises(requests.HTTPError):
        http_client.get_with_retry("https://example.org")
    assert len(calls) == 1


def test_gives_up_after_max_retries(monkeypatch):
    calls = _patch(monkeypatch, [FakeResponse(503)])
    with pytest.raises(requests.HTTPError):
        http_client.get_with_retry("https://example.org", max_retries=3)
    assert len(calls) == 3