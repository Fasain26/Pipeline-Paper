"""Pipeline Transform + Load untuk arXiv, lengkap dengan pencatatan run dan quality gate."""

import logging
import sys

from src.db import get_connection
from src.load.papers import replace_rejected, upsert_papers
from src.transform.papers import (
    TransformResult,
    latest_run_date,
    load_raw_arxiv_records,
    validate_records,
)

logger = logging.getLogger(__name__)

PIPELINE_NAME = "arxiv_transform_load"
SOURCE = "arxiv"
MAX_REJECT_RATIO = 0.2  # lebih dari 20% ditolak = ada yang salah, hentikan run


def _start_run(run_date: str) -> int:
    """Catat awal run di pipeline_runs (transaksi sendiri) dan kembalikan id-nya."""
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO pipeline_runs (pipeline, source, run_date, status) "
            "VALUES (%s, %s, %s, 'running') RETURNING id",
            (PIPELINE_NAME, SOURCE, run_date),
        ).fetchone()
    return row[0]


def _finish_run(
    run_id: int,
    status: str,
    rows_in: int = 0,
    rows_out: int = 0,
    rows_rejected: int = 0,
    error: str | None = None,
) -> None:
    """Catat akhir run (transaksi sendiri, jadi tetap tersimpan walau transaksi utama gagal)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status = %s, rows_in = %s, rows_out = %s, "
            "rows_rejected = %s, error_message = %s, finished_at = now() WHERE id = %s",
            (status, rows_in, rows_out, rows_rejected, error[:1000] if error else None, run_id),
        )


def _check_quality_gate(result: TransformResult) -> None:
    ratio = len(result.rejected) / result.entries_in
    if ratio > MAX_REJECT_RATIO:
        raise RuntimeError(
            f"Quality gate gagal: {len(result.rejected)} dari {result.entries_in} "
            f"record ditolak ({ratio:.0%}), batas {MAX_REJECT_RATIO:.0%}"
        )


def run_transform_load(run_date: str | None = None) -> dict:
    """Jalankan satu putaran Transform + Load untuk satu partisi tanggal."""
    run_date = run_date or latest_run_date()
    run_id = _start_run(run_date)
    logger.info("Run %d dimulai untuk partisi %s", run_id, run_date)

    try:
        raw_records = load_raw_arxiv_records(run_date)
        if not raw_records:
            raise RuntimeError(f"Tidak ada record mentah untuk partisi {run_date} (manifest tidak ditemukan?)")

        result = validate_records(raw_records)

        # 1) Simpan bukti record tertolak LEBIH DULU (transaksi sendiri)
        with get_connection() as conn:
            replace_rejected(conn, SOURCE, run_date, result.rejected)

        # 2) Periksa gerbang kualitas
        _check_quality_gate(result)

        # 3) Tulis data bersih; semua-atau-tidak-sama-sekali
        with get_connection() as conn:
            stats = upsert_papers(conn, result.valid)

    except Exception as exc:
        _finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
        logger.error("Run %d GAGAL: %s", run_id, exc)
        raise

    _finish_run(
        run_id,
        "success",
        rows_in=result.entries_in,
        rows_out=len(result.valid),
        rows_rejected=len(result.rejected),
    )
    logger.info("Run %d sukses: %s", run_id, stats)
    return {
        "run_id": run_id,
        "run_date": run_date,
        "entries_in": result.entries_in,
        "valid": len(result.valid),
        "rejected": len(result.rejected),
        "duplicates_dropped": result.duplicates_dropped,
        **stats,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_transform_load(sys.argv[1] if len(sys.argv) > 1 else None)

    print("Ringkasan run:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    with get_connection() as conn:
        n_papers = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
        n_authors = conn.execute("SELECT count(*) FROM authors").fetchone()[0]
        n_links = conn.execute("SELECT count(*) FROM paper_authors").fetchone()[0]
    print(f"Isi database sekarang: {n_papers} paper, {n_authors} penulis, {n_links} relasi paper-penulis")