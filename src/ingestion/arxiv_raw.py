"""Tahap Extract (raw layer): unduh halaman arXiv dan simpan XML mentahnya ke object storage."""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from src.extractors.arxiv import REQUEST_DELAY_SECONDS, fetch_page, parse_feed
from src.storage import get_object, list_keys, put_object

logger = logging.getLogger(__name__)


def _query_slug(search_query: str) -> str:
    """Ubah query (berisi spasi/tanda kutip) menjadi kode pendek yang aman untuk nama key."""
    return hashlib.sha1(search_query.encode("utf-8")).hexdigest()[:8]


def ingest_arxiv_raw(
    search_query: str,
    max_total: int = 200,
    page_size: int = 100,
    run_date: str | None = None,
) -> dict:
    """Unduh halaman arXiv, simpan XML mentahnya, lalu tulis manifest. Mengembalikan manifest."""
    run_date = run_date or datetime.now(timezone.utc).date().isoformat()
    prefix = f"arxiv/dt={run_date}/q-{_query_slug(search_query)}"

    pages: list[dict] = []
    start = 0

    while start < max_total:
        batch_size = min(page_size, max_total - start)
        xml_text = fetch_page(search_query, start=start, max_results=batch_size)

        # Parse HANYA untuk memvalidasi dan menghitung; yang disimpan tetap XML aslinya
        total_available, entries = parse_feed(xml_text)
        if not entries:
            break

        key = f"{prefix}/start-{start:06d}.xml"
        put_object(key, xml_text, content_type="application/xml")
        logger.info("Tersimpan %s (%d entri)", key, len(entries))
        pages.append({"key": key, "start": start, "entries": len(entries)})

        start += len(entries)
        if start >= total_available:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    manifest = {
        "source": "arxiv",
        "query": search_query,
        "run_date": run_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "page_size": page_size,
        "max_total": max_total,
        "pages": pages,
        "entries_total": sum(p["entries"] for p in pages),
    }
    put_object(
        f"{prefix}/manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    query = 'all:"machine learning" AND all:healthcare'
    manifest = ingest_arxiv_raw(query, max_total=250, page_size=100)
    print("Entri tersimpan:", manifest["entries_total"])

    print("Isi gudang untuk run ini:")
    for stored_key in list_keys(f"arxiv/dt={manifest['run_date']}/"):
        print(" -", stored_key)

    if manifest["pages"]:
        first_key = manifest["pages"][0]["key"]
        _, papers = parse_feed(get_object(first_key).decode("utf-8"))
        print("Uji baca ulang dari gudang:", len(papers), "paper. Contoh:", papers[0]["title"])
        