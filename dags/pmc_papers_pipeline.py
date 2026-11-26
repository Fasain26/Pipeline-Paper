"""DAG harian: unduh artikel PMC, ekstrak PDF, lalu evaluasi metadata terhadap data resmi."""

from datetime import datetime, timedelta


from airflow.decorators import dag, task


@dag(
    dag_id="pmc_papers_pipeline",
    schedule="0 3 * * *",  # setiap hari pukul 03:00 UTC (arXiv jam 02:00, supaya tidak bersamaan)
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    tags=["papers", "pmc"],
)
def pmc_papers_pipeline():
    @task(execution_timeout=timedelta(minutes=30))
    def download_pmc() -> dict:
        from src.pipelines.pmc_pipeline import stage_download

        return stage_download()

    @task(execution_timeout=timedelta(minutes=30))
    def extract_pdfs() -> dict:
        from src.pipelines.pmc_pipeline import stage_extract

        return stage_extract()

    @task(execution_timeout=timedelta(minutes=30))
    def evaluate_metadata() -> dict:
        from src.pipelines.pmc_pipeline import stage_evaluate

        return stage_evaluate()

    download_pmc() >> extract_pdfs() >> evaluate_metadata()


pmc_papers_pipeline()