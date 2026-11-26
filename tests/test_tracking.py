import pytest

from src.pipelines import tracking


def _patch(monkeypatch):
    calls = []
    monkeypatch.setattr(tracking, "start_run", lambda pipeline, source, run_date=None: 42)
    monkeypatch.setattr(
        tracking, "finish_run", lambda run_id, status, **kwargs: calls.append((run_id, status, kwargs))
    )
    return calls


def test_success_is_recorded_with_counts(monkeypatch):
    calls = _patch(monkeypatch)
    with tracking.tracked_run("p", "s") as counts:
        counts["rows_in"] = 5
    assert calls == [(42, "success", {"rows_in": 5, "rows_out": 0, "rows_rejected": 0})]


def test_failure_is_recorded_and_reraised(monkeypatch):
    calls = _patch(monkeypatch)
    with pytest.raises(ValueError):
        with tracking.tracked_run("p", "s") as counts:
            counts["rows_in"] = 2
            raise ValueError("boom")
    run_id, status, kwargs = calls[0]
    assert (run_id, status) == (42, "failed")
    assert "ValueError: boom" in kwargs["error"]
    assert kwargs["rows_in"] == 2