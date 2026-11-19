"""Unduh PDF, XML, dan JSON artikel PMC yang layak ke gudang SeaweedFS (dengan verifikasi MD5)."""

import hashlib
import json
import logging
import sys
import time
from urllib.parse import parse_qs, urlparse

from botocore.exceptions import ClientError
from psycopg.types.json import Jsonb

from src.db import get_connection
from src.extractors.pmc import (
    PMC_BUCKET,
    TOPIC,
    build_query,
    esearch_pmc,
    find_eligible_version,
    get_pmc_client,
)
from src.storage import list_keys, put_object

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 100 * 1024 * 1024  # tolak berkas lebih dari 100 MB
MAX_CONSECUTIVE_FAILURES = 3
SEARCH_PAGE_SIZE = 20
SEARCH_DELAY_SECONDS = 0.4  # NCBI: maksimal 3 request per detik tanpa API key

SAVE_LEDGER_SQL = """
INSERT INTO pmc_articles (
    pmcid, version, pmid, doi, title, license_code, is_manuscript, s3_prefix,
    pdf_key, xml_key, json_key, pdf_md5, pdf_size_bytes, status, error_message
) VALUES (
    %(pmcid)s, %(version)s, %(pmid)s, %(doi)s, %(title)s, %(license_code)s,
    %(is_manuscript)s, %(s3_prefix)s, %(pdf_key)s, %(xml_key)s, %(json_key)s,
    %(pdf_md5)s, %(pdf_size_bytes)s, %(status)s, %(error_message)s
)
ON CONFLICT (pmcid, version) DO UPDATE SET
    pmid           = EXCLUDED.pmid,
    doi            = EXCLUDED.doi,
    title          = EXCLUDED.title,
    license_code   = EXCLUDED.license_code,
    is_manuscript  = EXCLUDED.is_manuscript,
    s3_prefix      = EXCLUDED.s3_prefix,
    pdf_key        = EXCLUDED.pdf_key,
    xml_key        = EXCLUDED.xml_key,
    json_key       = EXCLUDED.json_key,
    pdf_md5        = EXCLUDED.pdf_md5,
    pdf_size_bytes = EXCLUDED.pdf_size_bytes,
    status         = EXCLUDED.status,
    error_message  = EXCLUDED.error_message,
    downloaded_at  = now()
"""


def _md5_from_url(s3_url: str | None) -> str | None:
    """Ambil kode md5 dari alamat berbentuk s3://bucket/key?md5=abc123."""
    if not s3_url:
        return None
    values = parse_qs(urlparse(s3_url).query).get("md5")
    return values[0] if values else None


def _download_verified(key: str, expected_md5: str | None) -> bytes:
    """Unduh satu objek dari bucket PMC dan pastikan MD5-nya cocok dengan yang dijanjikan."""
    client = get_pmc_client()
    head = client.head_object(Bucket=PMC_BUCKET, Key=key)
    if head["ContentLength"] > MAX_FILE_BYTES:
        raise ValueError(f"{key} terlalu besar: {head['ContentLength']} byte")

    data = client.get_object(Bucket=PMC_BUCKET, Key=key)["Body"].read()
    actual_md5 = hashlib.md5(data).hexdigest()
    if expected_md5 and actual_md5 != expected_md5:
        raise ValueError(f"MD5 tidak cocok untuk {key}: harapan {expected_md5}, diterima {actual_md5}")
    return data


def _already_downloaded(conn, pmcid: str, version: int, pdf_md5: str | None) -> bool:
    if not pdf_md5:
        return False
    row = conn.execute(
        "SELECT 1 FROM pmc_articles "
        "WHERE pmcid = %s AND version = %s AND status = 'downloaded' AND pdf_md5 = %s",
        (pmcid, version, pdf_md5),
    ).fetchone()
    return row is not None


def _save_ledger(conn, meta: dict, status: str, **extra) -> None:
    doi = (meta.get("doi") or "").lower() or None
    params = {
        "pmcid": meta["pmcid"],
        "version": meta["version"],
        "pmid": meta.get("pmid"),
        "doi": doi,
        "title": meta.get("title"),
        "license_code": meta["license_code"],
        "is_manuscript": bool(meta.get("is_manuscript")),
        "s3_prefix": meta["s3_prefix"],
        "pdf_key": None,
        "xml_key": None,
        "json_key": None,
        "pdf_md5": None,
        "pdf_size_bytes": None,
        "status": status,
        "error_message": None,
    }
    params.update(extra)
    conn.execute(SAVE_LEDGER_SQL, params)


def _record_rejection(conn, pmcid_number: str, reason: str) -> None:
    """Catat artikel yang tidak layak; run ulang menggantikan catatan lama (idempotent)."""
    source_id = f"PMC{pmcid_number}"
    conn.execute("DELETE FROM rejected_records WHERE source = 'pmc' AND source_id = %s", (source_id,))
    conn.execute(
        "INSERT INTO rejected_records (source, source_id, raw_key, reason, payload) "
        "VALUES ('pmc', %s, NULL, %s, %s)",
        (source_id, reason, Jsonb({"stage": "eligibility"})),
    )


def process_article(conn, pmcid_number: str) -> str:
    """Proses satu artikel. Mengembalikan 'downloaded', 'skipped', 'rejected', atau 'failed'."""
    meta, reason = find_eligible_version(pmcid_number)
    if meta is None:
        _record_rejection(conn, pmcid_number, reason)
        return "rejected"

    prefix = meta["s3_prefix"]  # contoh: PMC5334499.1/
    name = prefix.rstrip("/")  # contoh: PMC5334499.1
    pdf_md5 = _md5_from_url(meta.get("pdf_url"))

    if _already_downloaded(conn, meta["pmcid"], meta["version"], pdf_md5):
        return "skipped"

    out_prefix = f"pmc/{name}"
    keys = {
        "pdf_key": f"{out_prefix}/{name}.pdf",
        "xml_key": f"{out_prefix}/{name}.xml",
        "json_key": f"{out_prefix}/{name}.json",
    }

    try:
        # Alamat sumber disusun SENDIRI dari prefix, bukan diambil dari URL di dalam JSON
        pdf = _download_verified(f"{prefix}{name}.pdf", pdf_md5)
        xml = _download_verified(f"{prefix}{name}.xml", _md5_from_url(meta.get("xml_url")))

        put_object(keys["pdf_key"], pdf, "application/pdf")
        put_object(keys["xml_key"], xml, "application/xml")
        put_object(
            keys["json_key"],
            json.dumps(meta, ensure_ascii=False, indent=2),
            "application/json",
        )
    except (ClientError, ValueError) as exc:
        _save_ledger(conn, meta, "failed", error_message=str(exc)[:500])
        logger.warning("Gagal memproses %s: %s", name, exc)
        return "failed"

    _save_ledger(conn, meta, "downloaded", pdf_md5=pdf_md5, pdf_size_bytes=len(pdf), **keys)
    return "downloaded"


def run_pmc_download(max_new: int = 5, max_scan: int = 100, topic: str = TOPIC) -> dict:
    """Unduh sampai max_new artikel BARU; periksa paling banyak max_scan kandidat dari pencarian."""
    query = build_query(topic)
    stats = {"downloaded": 0, "skipped": 0, "rejected": 0, "failed": 0}
    consecutive_failures = 0
    retstart = 0

    with get_connection() as conn:
        while stats["downloaded"] < max_new and retstart < max_scan:
            page = esearch_pmc(query, retmax=SEARCH_PAGE_SIZE, retstart=retstart)
            if not page["ids"]:
                break

            for pmcid_number in page["ids"]:
                if stats["downloaded"] >= max_new:
                    break
                outcome = process_article(conn, pmcid_number)
                conn.commit()  # simpan per artikel, supaya kemajuan tidak hilang kalau run dihentikan
                stats[outcome] += 1
                logger.info("PMC%s -> %s", pmcid_number, outcome)

                if outcome == "failed":
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise RuntimeError(
                            f"{consecutive_failures} kegagalan berturut-turut; "
                            "kemungkinan masalah infrastruktur, run dihentikan"
                        )
                else:
                    consecutive_failures = 0

            retstart += len(page["ids"])
            time.sleep(SEARCH_DELAY_SECONDS)

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    max_new = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    summary = run_pmc_download(max_new=max_new)
    print("Ringkasan run:", summary)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT pmcid, version, license_code, status, pdf_size_bytes "
            "FROM pmc_articles ORDER BY downloaded_at DESC LIMIT 10"
        ).fetchall()
    print("Catatan terbaru di pmc_articles:")
    for row in rows:
        print(" ", row)

    print("Jumlah objek di gudang (awalan pmc/):", len(list_keys("pmc/")))