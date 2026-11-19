"""Ekstraksi teks dan metadata dasar dari PDF artikel PMC dengan PyMuPDF."""

import logging
import sys

import pymupdf
from psycopg.types.json import Jsonb

from src.db import get_connection
from src.storage import get_object

logger = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 100  # di bawah ini, PDF dianggap hasil scan (tanpa lapisan teks)
LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)
EXTRACTOR = f"pymupdf {pymupdf.__version__} clean-v2"

PENDING_SQL = """
SELECT a.pmcid, a.version, a.pdf_key, a.pdf_md5
FROM pmc_articles a
LEFT JOIN pmc_pdf_extractions e ON e.pmcid = a.pmcid AND e.version = a.version
WHERE a.status = 'downloaded'
  AND a.pdf_key IS NOT NULL
  AND (e.pmcid IS NULL
       OR e.pdf_md5 IS DISTINCT FROM a.pdf_md5
       OR e.extractor IS DISTINCT FROM %s)
ORDER BY a.downloaded_at
LIMIT %s
"""

SAVE_SQL = """
INSERT INTO pmc_pdf_extractions (
    pmcid, version, status, error_message, page_count, char_count, word_count,
    chars_per_page, needs_ocr, pdf_info, full_text, extractor, pdf_md5
) VALUES (
    %(pmcid)s, %(version)s, %(status)s, %(error_message)s, %(page_count)s,
    %(char_count)s, %(word_count)s, %(chars_per_page)s, %(needs_ocr)s,
    %(pdf_info)s, %(full_text)s, %(extractor)s, %(pdf_md5)s
)
ON CONFLICT (pmcid, version) DO UPDATE SET
    status         = EXCLUDED.status,
    error_message  = EXCLUDED.error_message,
    page_count     = EXCLUDED.page_count,
    char_count     = EXCLUDED.char_count,
    word_count     = EXCLUDED.word_count,
    chars_per_page = EXCLUDED.chars_per_page,
    needs_ocr      = EXCLUDED.needs_ocr,
    pdf_info       = EXCLUDED.pdf_info,
    full_text      = EXCLUDED.full_text,
    extractor      = EXCLUDED.extractor,
    pdf_md5        = EXCLUDED.pdf_md5,
    extracted_at   = now()
"""


class ExtractionError(Exception):
    """PDF tidak bisa diekstrak (rusak, terenkripsi, atau tanpa halaman)."""


def _clean_text(text: str) -> str:
    """Buang karakter NUL, ganti ligatur (ﬁ jadi fi), dan pastikan teks valid UTF-8."""
    text = text.replace("\x00", "").translate(LIGATURES)
    return text.encode("utf-8", "replace").decode("utf-8")


def extract_pdf(pdf_bytes: bytes) -> dict:
    """Ekstrak teks dan statistik dari isi sebuah PDF. Fungsi murni: tidak menyentuh database."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ExtractionError("PDF terenkripsi (butuh password)")
            if doc.page_count == 0:
                raise ExtractionError("PDF tidak punya halaman")
            page_texts = [page.get_text("text") for page in doc]
            page_count = doc.page_count
            pdf_info = {key: value for key, value in (doc.metadata or {}).items() if value}
    except ExtractionError:
        raise
    except Exception as exc:  # PyMuPDF melempar beragam jenis error untuk berkas rusak
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

    full_text = _clean_text("\n\n".join(page_texts))
    char_count = len(full_text)
    chars_per_page = round(char_count / page_count, 1)

    return {
        "page_count": page_count,
        "char_count": char_count,
        "word_count": len(full_text.split()),
        "chars_per_page": chars_per_page,
        "needs_ocr": chars_per_page < MIN_CHARS_PER_PAGE,
        "pdf_info": pdf_info,
        "full_text": full_text,
        "extractor": EXTRACTOR,
    }


def _save(conn, pmcid: str, version: int, pdf_md5: str | None, result: dict | None, error: str | None):
    data = result or {}
    conn.execute(
        SAVE_SQL,
        {
            "pmcid": pmcid,
            "version": version,
            "status": "failed" if error else "extracted",
            "error_message": error,
            "page_count": data.get("page_count"),
            "char_count": data.get("char_count"),
            "word_count": data.get("word_count"),
            "chars_per_page": data.get("chars_per_page"),
            "needs_ocr": data.get("needs_ocr"),
            "pdf_info": Jsonb(data["pdf_info"]) if "pdf_info" in data else None,
            "full_text": data.get("full_text"),
            "extractor": EXTRACTOR,
            "pdf_md5": pdf_md5,
        },
    )


def run_extraction(limit: int = 10) -> dict:
    """Ekstrak PDF yang belum (atau perlu) diekstrak, maksimal `limit` per run."""
    stats = {"extracted": 0, "failed": 0, "needs_ocr": 0}

    with get_connection() as conn:
        pending = conn.execute(PENDING_SQL, (EXTRACTOR, limit)).fetchall()
        for pmcid, version, pdf_key, pdf_md5 in pending:
            pdf_bytes = get_object(pdf_key)  # error gudang dibiarkan naik: itu masalah infrastruktur
            try:
                result = extract_pdf(pdf_bytes)
            except ExtractionError as exc:
                _save(conn, pmcid, version, pdf_md5, None, str(exc)[:500])
                stats["failed"] += 1
                logger.warning("%s v%s gagal diekstrak: %s", pmcid, version, exc)
            else:
                _save(conn, pmcid, version, pdf_md5, result, None)
                stats["extracted"] += 1
                if result["needs_ocr"]:
                    stats["needs_ocr"] += 1
                logger.info(
                    "%s v%s: %d halaman, %d kata, %.0f karakter/halaman",
                    pmcid, version, result["page_count"], result["word_count"], result["chars_per_page"],
                )
            conn.commit()  # simpan per artikel

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("Ringkasan run:", run_extraction(limit))

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT pmcid, page_count, word_count, chars_per_page, needs_ocr, status "
            "FROM pmc_pdf_extractions ORDER BY extracted_at DESC LIMIT 10"
        ).fetchall()
        sample = conn.execute(
            "SELECT pmcid, left(full_text, 500) FROM pmc_pdf_extractions "
            "WHERE status = 'extracted' ORDER BY extracted_at LIMIT 1"
        ).fetchone()

    print("Hasil terbaru:")
    for row in rows:
        print(" ", row)
    if sample:
        print(f"\nCuplikan teks awal {sample[0]} (500 karakter):")
        print(sample[1])