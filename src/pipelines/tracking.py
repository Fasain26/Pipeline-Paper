"""Pencatatan run pipeline ke tabel pipeline_runs, dipakai bersama oleh semua pipeline."""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from src.db import get_connection

logger = logging.getLogger(__name__)


def start_run(pipeline: str, source: str, run_date: str | None = None) -> int:
    """Catat awal run (transaksi sendiri) dan kembalikan id-nya."""
    run_date = run_date or datetime.now(timezone.utc).date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO pipeline_runs (pipeline, source, run_date, status) "
            "VALUES (%s, %s, %s, 'running') RETURNING id",
            (pipeline, source, run_date),
        ).fetchone()
    return row[0]


def finish_run(
    run_id: int,
    status: str,
    rows_in: int = 0,
    rows_out: int = 0,
    rows_rejected: int = 0,
    error: str | None = None,
) -> None:
    """Catat akhir run (transaksi sendiri, jadi tetap tersimpan walau pekerjaannya gagal)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status = %s, rows_in = %s, rows_out = %s, "
            "rows_rejected = %s, error_message = %s, finished_at = now() WHERE id = %s",
            (status, rows_in, rows_out, rows_rejected, error[:1000] if error else None, run_id),
        )


@contextmanager
def tracked_run(pipeline: str, source: str, run_date: str | None = None):
    """Bungkus satu tahap pipeline: catat awal, lalu sukses atau gagal beserta pesan errornya.

    Pemakaian:
        with tracked_run("nama_tahap", "sumber") as counts:
            ...pekerjaan...
            counts["rows_in"] = 10
            counts["rows_out"] = 9
    """
    run_id = start_run(pipeline, source, run_date)
    logger.info("Run %d (%s) dimulai", run_id, pipeline)
    counts = {"rows_in": 0, "rows_out": 0, "rows_rejected": 0}
    try:
        yield counts
    except Exception as exc:
        finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}", **counts)
        logger.error("Run %d (%s) GAGAL: %s", run_id, pipeline, exc)
        raise  # teruskan error supaya Airflow tahu tugas ini gagal
    finish_run(run_id, "success", **counts)
    logger.info("Run %d (%s) sukses: %s", run_id, pipeline, counts)