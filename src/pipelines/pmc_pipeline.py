"""Pipeline PMC: tiga tahap (unduh, ekstrak PDF, evaluasi metadata), masing-masing tercatat di pipeline_runs."""

import logging

from src.db import get_connection
from src.ingestion.pmc_raw import run_pmc_download
from src.pdf.extract import run_extraction
from src.pdf.metadata import run_metadata_evaluation
from src.pipelines.tracking import tracked_run

logger = logging.getLogger(__name__)

SOURCE = "pmc"
MAX_CORPUS = 200  # batas jumlah artikel di gudang, supaya disk laptop tidak penuh
MAX_EXTRACT_FAIL_RATIO = 0.3
METADATA_EXTRACTOR = "heuristic-v2"


def stage_download(max_new: int = 5) -> dict:
    """Tahap 1: unduh artikel PMC yang layak (maksimal max_new artikel baru)."""
    with tracked_run("pmc_download", SOURCE) as counts:
        with get_connection() as conn:
            total = conn.execute(
                "SELECT count(*) FROM pmc_articles WHERE status = 'downloaded'"
            ).fetchone()[0]

        if total >= MAX_CORPUS:
            logger.info("Korpus sudah %d artikel (batas %d); unduhan dilewati", total, MAX_CORPUS)
            return {"downloaded": 0, "note": "corpus_cap_reached"}

        stats = run_pmc_download(max_new=max_new)
        counts["rows_in"] = sum(stats.values())
        counts["rows_out"] = stats["downloaded"]
        counts["rows_rejected"] = stats["rejected"]
        return stats


def stage_extract(limit: int = 20) -> dict:
    """Tahap 2: ekstrak teks dari PDF yang belum diekstrak."""
    with tracked_run("pmc_extract", SOURCE) as counts:
        stats = run_extraction(limit)
        total = stats["extracted"] + stats["failed"]
        counts["rows_in"] = total
        counts["rows_out"] = stats["extracted"]
        counts["rows_rejected"] = stats["failed"]

        if total and stats["failed"] / total > MAX_EXTRACT_FAIL_RATIO:
            raise RuntimeError(
                f"Quality gate gagal: {stats['failed']} dari {total} PDF gagal diekstrak "
                f"(batas {MAX_EXTRACT_FAIL_RATIO:.0%})"
            )
        return stats


def stage_evaluate(limit: int = 50) -> dict:
    """Tahap 3: ekstrak metadata dari PDF dan evaluasi terhadap data resmi."""
    with tracked_run("pmc_evaluate", SOURCE) as counts:
        stats = run_metadata_evaluation(METADATA_EXTRACTOR, limit)
        counts["rows_in"] = stats["evaluated"] + stats["failed"]
        counts["rows_out"] = stats["evaluated"]
        counts["rows_rejected"] = stats["failed"]
        return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("download:", stage_download(max_new=2))
    print("extract :", stage_extract())
    print("evaluate:", stage_evaluate())