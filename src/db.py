"""Koneksi PostgreSQL dan utilitas kecil untuk menjalankan file SQL."""

from pathlib import Path

import psycopg

from src.config import PROJECT_ROOT, get_env


def get_connection() -> psycopg.Connection:
    """Buka satu koneksi baru ke PostgreSQL."""
    return psycopg.connect(
        host=get_env("POSTGRES_HOST", "localhost"),
        port=int(get_env("POSTGRES_PORT", "5432")),
        dbname=get_env("POSTGRES_DB"),
        user=get_env("POSTGRES_USER"),
        password=get_env("POSTGRES_PASSWORD"),
    )


def run_sql_file(path: Path) -> None:
    """Jalankan seluruh isi satu file .sql dalam satu transaksi."""
    sql = path.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.execute(sql)
    # Keluar dari blok 'with' tanpa error = COMMIT; kalau ada error = ROLLBACK


if __name__ == "__main__":
    schema_file = PROJECT_ROOT / "sql" / "001_schema.sql"
    run_sql_file(schema_file)
    print("Skema berhasil diterapkan:", schema_file.name)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()

    print("Tabel di database:")
    for (table_name,) in rows:
        print(" -", table_name)