"""Tahap Load: tulis record bersih dan record tertolak ke PostgreSQL."""

import psycopg
from psycopg.types.json import Jsonb

from src.transform.papers import PaperRecord

UPSERT_PAPER_SQL = """
INSERT INTO papers (
    source, source_id, version, doi, title, abstract,
    published_at, updated_at, primary_category, categories,
    journal_ref, pdf_url, abs_url, raw_key
) VALUES (
    %(source)s, %(source_id)s, %(version)s, %(doi)s, %(title)s, %(abstract)s,
    %(published_at)s, %(updated_at)s, %(primary_category)s, %(categories)s,
    %(journal_ref)s, %(pdf_url)s, %(abs_url)s, %(raw_key)s
)
ON CONFLICT (source, source_id) DO UPDATE SET
    version          = EXCLUDED.version,
    doi              = EXCLUDED.doi,
    title            = EXCLUDED.title,
    abstract         = EXCLUDED.abstract,
    published_at     = EXCLUDED.published_at,
    updated_at       = EXCLUDED.updated_at,
    primary_category = EXCLUDED.primary_category,
    categories       = EXCLUDED.categories,
    journal_ref      = EXCLUDED.journal_ref,
    pdf_url          = EXCLUDED.pdf_url,
    abs_url          = EXCLUDED.abs_url,
    raw_key          = EXCLUDED.raw_key,
    ingested_at      = now()
WHERE COALESCE(EXCLUDED.version, 0) >= COALESCE(papers.version, 0)
RETURNING id, (xmax = 0) AS inserted
"""


def _replace_authors(conn: psycopg.Connection, paper_id: int, authors: list[str]) -> None:
    """Ganti daftar penulis sebuah paper dengan daftar terbaru (urutan dijaga lewat position)."""
    conn.execute("DELETE FROM paper_authors WHERE paper_id = %s", (paper_id,))
    for position, name in enumerate(authors, start=1):
        author_id = conn.execute(
            "INSERT INTO authors (name) VALUES (%s) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id",
            (name,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO paper_authors (paper_id, author_id, position) VALUES (%s, %s, %s)",
            (paper_id, author_id, position),
        )


def upsert_papers(conn: psycopg.Connection, records: list[PaperRecord]) -> dict:
    """Upsert semua record. Mengembalikan statistik: inserted, updated, skipped_older."""
    stats = {"inserted": 0, "updated": 0, "skipped_older": 0}
    for record in records:
        row = conn.execute(UPSERT_PAPER_SQL, record.model_dump()).fetchone()
        if row is None:  # klausa WHERE menolak: versi di database lebih baru
            stats["skipped_older"] += 1
            continue
        paper_id, inserted = row
        stats["inserted" if inserted else "updated"] += 1
        _replace_authors(conn, paper_id, record.authors)
    return stats


def replace_rejected(
    conn: psycopg.Connection, source: str, run_date: str, rejected: list[dict]
) -> None:
    """Ganti catatan record tertolak untuk satu partisi (supaya run ulang tidak menggandakan)."""
    conn.execute(
        "DELETE FROM rejected_records WHERE source = %s AND starts_with(raw_key, %s)",
        (source, f"{source}/dt={run_date}/"),
    )
    for rec in rejected:
        conn.execute(
            "INSERT INTO rejected_records (source, source_id, raw_key, reason, payload) "
            "VALUES (%s, %s, %s, %s, %s)",
            (rec["source"], rec["source_id"], rec["raw_key"], rec["reason"], Jsonb(rec["payload"])),
        )