"""DAG harian: ambil data mentah arXiv, lalu transform dan load ke PostgreSQL."""

from datetime import datetime, timedelta

from airflow.sdk import dag, task

ARXIV_QUERY = 'all:"machine learning" AND all:healthcare'


@dag(
    dag_id="arxiv_papers_pipeline",
    schedule="0 2 * * *",  # setiap hari pukul 02:00 UTC
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    tags=["papers", "arxiv"],
)
def arxiv_papers_pipeline():
    @task(execution_timeout=timedelta(minutes=20))
    def extract_raw() -> str:
        """Extract: unduh halaman arXiv, simpan XML mentah ke gudang. Mengembalikan tanggal partisi."""
        from src.ingestion.arxiv_raw import ingest_arxiv_raw

        manifest = ingest_arxiv_raw(ARXIV_QUERY, max_total=250, page_size=100)
        return manifest["run_date"]

    @task
    def transform_load(run_date: str) -> dict:
        """Transform + Load: baca mentah, validasi, upsert ke PostgreSQL."""
        from src.pipelines.arxiv_etl import run_transform_load

        return run_transform_load(run_date)

    transform_load(extract_raw())


arxiv_papers_pipeline()