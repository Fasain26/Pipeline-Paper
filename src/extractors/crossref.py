import logging
import re
import time

from src.config import get_env
from src.http_client import get_with_retry

CROSSREF_API_URL = "https://api.crossref.org/works"
REQUEST_DELAY_SECONDS = 1  # polite pool sekitar 3 req/detik; kita pelan saja
USER_AGENT = "paper-pipeline/0.1 (student portfolio project)"

# Hanya minta kolom yang kita butuhkan, supaya respons ringan.
# Semua nama di bawah sudah dicocokkan dengan daftar "Valid selects" dari Crossref.
SELECT_FIELDS = ",".join(
    [
        "DOI",
        "title",
        "abstract",
        "author",
        "issued",
        "container-title",
        "type",
        "publisher",
        "is-referenced-by-count",
        "references-count",
        "URL",
    ]
)


def fetch_page(
    query: str,
    cursor: str = "*",
    rows: int = 100,
    filters: str | None = None,
) -> dict:
    """Kirim SATU request ke Crossref dan kembalikan bagian 'message' dari JSON-nya."""
    params = {
        "query": query,
        "rows": rows,
        "cursor": cursor,
        "select": SELECT_FIELDS,
        "mailto": get_env("CROSSREF_MAILTO"),
    }
    if filters:
        params["filter"] = filters

    response = get_with_retry(
        CROSSREF_API_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
    )
    return response.json()["message"]


def _clean(text: str | None) -> str:
    """Rapikan teks: buang baris baru dan spasi berlebih."""
    return " ".join(text.split()) if text else ""


def _strip_tags(text: str | None) -> str:
    """Abstrak Crossref berbentuk XML (<jats:p>...); buang tag-nya."""
    if not text:
        return ""
    return _clean(re.sub(r"<[^>]+>", " ", text))


def _date_from_parts(issued: dict | None) -> str | None:
    """Ubah {'date-parts': [[2024, 5, 3]]} menjadi '2024-05-03' (bisa '2024-05' atau '2024')."""
    try:
        parts = issued["date-parts"][0]
    except (TypeError, KeyError, IndexError):
        return None
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    return "-".join(f"{p:04d}" if i == 0 else f"{p:02d}" for i, p in enumerate(parts))


def parse_work(item: dict) -> dict:
    """Ubah satu item JSON Crossref menjadi dictionary dengan bentuk seragam."""
    authors = []
    for a in item.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip() or a.get("name", "")
        if name:
            authors.append(_clean(name))

    titles = item.get("title") or []
    venues = item.get("container-title") or []
    doi = (item.get("DOI") or "").lower()

    return {
        "source": "crossref",
        "source_id": doi,
        "doi": doi,
        "title": _clean(titles[0]) if titles else "",
        "abstract": _strip_tags(item.get("abstract")),
        "authors": authors,
        "published": _date_from_parts(item.get("issued")),
        "venue": _clean(venues[0]) if venues else None,
        "type": item.get("type"),
        "publisher": item.get("publisher"),
        "cited_by_count": item.get("is-referenced-by-count"),
        "reference_count": item.get("references-count"),
        "url": item.get("URL"),
    }


def fetch_works(
    query: str,
    max_total: int = 200,
    rows: int = 100,
    filters: str | None = None,
) -> list[dict]:
    """Ambil banyak artikel dengan pagination berbasis cursor."""
    rows = min(rows, max_total)
    works: list[dict] = []
    cursor: str | None = "*"

    while cursor and len(works) < max_total:
        message = fetch_page(query, cursor=cursor, rows=rows, filters=filters)
        items = message.get("items", [])
        if not items:
            break
        works.extend(parse_work(item) for item in items)
        cursor = message.get("next-cursor")
        time.sleep(REQUEST_DELAY_SECONDS)

    return works[:max_total]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    query = "machine learning healthcare"
    filters = "type:journal-article,from-pub-date:2024-01-01,has-abstract:true"
    results = fetch_works(query, max_total=5, rows=5, filters=filters)

    print(f"Jumlah artikel diambil: {len(results)}")
    for work in results:
        print("-" * 60)
        print(work["doi"], "|", work["published"])
        print(work["title"])
        print("Penulis:", ", ".join(work["authors"][:3]))
        print("Jurnal:", work["venue"], "| Dikutip:", work["cited_by_count"])