import logging
import re
import time
import xml.etree.ElementTree as ET

from src.http_client import get_with_retry

ARXIV_API_URL = "https://export.arxiv.org/api/query"
REQUEST_DELAY_SECONDS = 3  # aturan arXiv: maksimal 1 request per 3 detik
USER_AGENT = "paper-pipeline/0.1 (student portfolio project)"

# Peta "awalan -> alamat namespace" untuk membaca tag XML dari arXiv
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def fetch_page(search_query: str, start: int = 0, max_results: int = 100) -> str:
    """Kirim SATU request ke arXiv dan kembalikan XML mentahnya (berupa teks)."""
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = get_with_retry(
        ARXIV_API_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
    )
    return response.text


def _clean(text: str | None) -> str:
    """Rapikan teks: buang baris baru dan spasi berlebih."""
    return " ".join(text.split()) if text else ""


def parse_feed(xml_text: str) -> tuple[int, list[dict]]:
    """Ubah XML arXiv menjadi (total_hasil, daftar_paper)."""
    root = ET.fromstring(xml_text)
    total = int(root.findtext("opensearch:totalResults", default="0", namespaces=NS))

    papers = []
    for entry in root.findall("atom:entry", NS):
        entry_id = entry.findtext("atom:id", default="", namespaces=NS)

        # Saat terjadi error, arXiv mengirim "entry" palsu berisi pesan error
        if "/api/errors" in entry_id:
            message = _clean(entry.findtext("atom:summary", default="", namespaces=NS))
            raise RuntimeError(f"arXiv API error: {message}")

        # entry_id contoh: http://arxiv.org/abs/2101.00001v2
        abs_id = entry_id.split("/abs/")[-1]
        match = re.match(r"^(?P<base>.+)v(?P<version>\d+)$", abs_id)
        arxiv_id = match.group("base") if match else abs_id
        version = int(match.group("version")) if match else None

        pdf_url = None
        for link in entry.findall("atom:link", NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")

        primary = entry.find("arxiv:primary_category", NS)

        papers.append(
            {
                "source": "arxiv",
                "source_id": arxiv_id,
                "version": version,
                "title": _clean(entry.findtext("atom:title", default="", namespaces=NS)),
                "abstract": _clean(entry.findtext("atom:summary", default="", namespaces=NS)),
                "authors": [
                    _clean(a.findtext("atom:name", default="", namespaces=NS))
                    for a in entry.findall("atom:author", NS)
                ],
                "published": entry.findtext("atom:published", namespaces=NS),
                "updated": entry.findtext("atom:updated", namespaces=NS),
                "primary_category": primary.get("term") if primary is not None else None,
                "categories": [c.get("term") for c in entry.findall("atom:category", NS)],
                "doi": entry.findtext("arxiv:doi", namespaces=NS),
                "journal_ref": entry.findtext("arxiv:journal_ref", namespaces=NS),
                "pdf_url": pdf_url,
                "abs_url": entry_id,
            }
        )
    return total, papers


def fetch_papers(search_query: str, max_total: int = 200, page_size: int = 100) -> list[dict]:
    """Ambil banyak paper dengan membaca halaman demi halaman (pagination)."""
    papers: list[dict] = []
    start = 0

    while start < max_total:
        batch_size = min(page_size, max_total - start)
        xml_text = fetch_page(search_query, start=start, max_results=batch_size)
        total_available, page_papers = parse_feed(xml_text)

        if not page_papers:
            break
        papers.extend(page_papers)
        start += len(page_papers)

        if start >= total_available:
            break
        time.sleep(REQUEST_DELAY_SECONDS)  # sopan santun ke server arXiv

    return papers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    query = 'all:"machine learning" AND all:healthcare'
    results = fetch_papers(query, max_total=5, page_size=5)

    print(f"Jumlah paper diambil: {len(results)}")
    for paper in results:
        print("-" * 60)
        print(paper["source_id"], "|", paper["published"][:10])
        print(paper["title"])
        print("Penulis:", ", ".join(paper["authors"][:3]))
        print("PDF:", paper["pdf_url"])