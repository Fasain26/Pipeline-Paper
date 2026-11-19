"""Ekstraksi metadata (judul, DOI) dari PDF dengan heuristik, dan evaluasinya terhadap data resmi PMC."""

import json
import logging
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

import pymupdf

from src.db import get_connection
from src.storage import get_object

logger = logging.getLogger(__name__)

# Versi heuristik. v2 = v1 + DOI dicari di dua halaman pertama (bukan hanya halaman 1).
EXTRACTORS = {
    "heuristic-v1": {"doi_pages": 1},
    "heuristic-v2": {"doi_pages": 2},
}

TITLE_MATCH_THRESHOLD = 0.90
MIN_TITLE_WORDS = 4
TITLE_REGION = 0.5  # judul dicari hanya di separuh atas halaman pertama
MIN_PARTIAL_CHARS = 20  # judul 'sebagian' minimal sepanjang ini, supaya awalan pendek tidak dihitung

DOI_PATTERN = re.compile(r"10\.\d{4,9}/\s?[^\s\"<>]+")

LIGATURES = str.maketrans(
    {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}
)

PENDING_SQL = """
SELECT a.pmcid, a.version, a.pdf_key, a.xml_key, a.json_key, a.pdf_md5, e.full_text
FROM pmc_articles a
JOIN pmc_pdf_extractions e
  ON e.pmcid = a.pmcid AND e.version = a.version AND e.status = 'extracted'
LEFT JOIN pmc_pdf_metadata m
  ON m.pmcid = a.pmcid AND m.version = a.version AND m.extractor = %s
WHERE a.status = 'downloaded'
  AND (m.pmcid IS NULL
       OR m.pdf_md5 IS DISTINCT FROM a.pdf_md5
       OR e.extracted_at > m.evaluated_at
       OR (m.title_status IS NULL AND m.truth_title IS NOT NULL)
       OR (m.doi_status IS NULL AND m.truth_doi IS NOT NULL))
ORDER BY a.downloaded_at
LIMIT %s
"""

SAVE_SQL = """
INSERT INTO pmc_pdf_metadata (
    pmcid, version, pdf_title, pdf_doi, builtin_title, truth_title, truth_doi,
    title_similarity, title_match, builtin_title_match, doi_match, abstract_recall,
    extractor, pdf_md5, title_status, doi_status
) VALUES (
    %(pmcid)s, %(version)s, %(pdf_title)s, %(pdf_doi)s, %(builtin_title)s,
    %(truth_title)s, %(truth_doi)s, %(title_similarity)s, %(title_match)s,
    %(builtin_title_match)s, %(doi_match)s, %(abstract_recall)s, %(extractor)s,
    %(pdf_md5)s, %(title_status)s, %(doi_status)s
)
ON CONFLICT (pmcid, version, extractor) DO UPDATE SET
    pdf_title           = EXCLUDED.pdf_title,
    pdf_doi             = EXCLUDED.pdf_doi,
    builtin_title       = EXCLUDED.builtin_title,
    truth_title         = EXCLUDED.truth_title,
    truth_doi           = EXCLUDED.truth_doi,
    title_similarity    = EXCLUDED.title_similarity,
    title_match         = EXCLUDED.title_match,
    builtin_title_match = EXCLUDED.builtin_title_match,
    doi_match           = EXCLUDED.doi_match,
    abstract_recall     = EXCLUDED.abstract_recall,
    pdf_md5             = EXCLUDED.pdf_md5,
    title_status        = EXCLUDED.title_status,
    doi_status          = EXCLUDED.doi_status,
    evaluated_at        = now()
"""

REPORT_SQL = """
SELECT extractor,
       count(*),
       count(*) FILTER (WHERE title_status = 'match'),
       count(*) FILTER (WHERE title_status = 'partial'),
       count(*) FILTER (WHERE title_status = 'wrong'),
       count(*) FILTER (WHERE title_status = 'missing'),
       count(*) FILTER (WHERE doi_status = 'match'),
       count(*) FILTER (WHERE doi_status = 'wrong'),
       count(*) FILTER (WHERE doi_status = 'missing'),
       count(*) FILTER (WHERE builtin_title_match),
       round(avg(abstract_recall), 3)
FROM pmc_pdf_metadata
GROUP BY extractor
ORDER BY extractor
"""

PROBLEMS_SQL = """
SELECT pmcid, title_status, doi_status, pdf_doi, truth_doi
FROM pmc_pdf_metadata
WHERE extractor = %s
  AND (title_status IS DISTINCT FROM 'match' OR doi_status IS DISTINCT FROM 'match')
ORDER BY pmcid
"""


def normalize_for_compare(text: str) -> str:
    """Seragamkan teks untuk perbandingan: ligatur, huruf kecil, hanya huruf dan angka."""
    text = unicodedata.normalize("NFKC", text).translate(LIGATURES).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _clean_title(text: str) -> str:
    return " ".join(text.translate(LIGATURES).split())


def extract_title(doc: pymupdf.Document) -> str | None:
    """Tebak judul: kelompok baris dengan font terbesar di separuh atas halaman pertama."""
    page = doc[0]
    limit_y = page.rect.height * TITLE_REGION

    lines: list[tuple[float, str]] = []  # (ukuran font, teks)
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # 0 = blok teks, 1 = gambar
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans or line["bbox"][1] > limit_y:
                continue
            text = " ".join(s["text"].strip() for s in spans)
            size = round(max(s["size"] for s in spans), 1)
            lines.append((size, text))

    # Gabungkan baris berurutan yang ukuran fontnya (hampir) sama menjadi satu kelompok
    groups: list[tuple[float, list[str]]] = []
    for size, text in lines:
        if groups and abs(groups[-1][0] - size) <= 0.6:
            groups[-1][1].append(text)
        else:
            groups.append((size, [text]))

    # Kelompok berfont terbesar yang cukup panjang dianggap judul
    for _, texts in sorted(groups, key=lambda g: g[0], reverse=True):
        title = _clean_title(" ".join(texts))
        if len(title.split()) >= MIN_TITLE_WORDS:
            return title
    return None


def extract_doi(page_text: str) -> str | None:
    """Ambil DOI pertama pada teks; menangani DOI terputus dan kata yang menempel."""
    match = DOI_PATTERN.search(page_text)
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group(0))
    raw = re.sub(r"(?<=\d)[A-Z][a-z]{3,}.*$", "", raw)  # buang kata menempel, mis. '...0362057Editor:'
    doi = raw.lower().rstrip(".,;:)]}'\"")
    return doi or None


def doi_from_pages(doc: pymupdf.Document, pages: int) -> str | None:
    """Cari DOI di `pages` halaman pertama (v1 = 1 halaman, v2 = 2 halaman)."""
    count = min(pages, doc.page_count)
    text = "\n".join(doc[i].get_text("text") for i in range(count))
    return extract_doi(text)


def abstract_from_xml(xml_bytes: bytes) -> str | None:
    """Ambil abstrak utama dari XML JATS (abaikan abstrak khusus seperti ringkasan grafis)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for abstract in root.iter("abstract"):
        if abstract.get("abstract-type") is None:
            text = " ".join("".join(abstract.itertext()).split())
            if text:
                return text
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", normalize_for_compare(text)))


def abstract_recall(abstract: str | None, full_text: str) -> float | None:
    """Persentase kata unik abstrak resmi yang juga ada di teks hasil ekstraksi PDF."""
    if not abstract:
        return None
    abstract_words = _words(abstract)
    if not abstract_words:
        return None
    return round(len(abstract_words & _words(full_text)) / len(abstract_words), 3)


def _similarity(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return round(SequenceMatcher(None, normalize_for_compare(a), normalize_for_compare(b)).ratio(), 3)


def _is_match(similarity: float | None, truth: str | None) -> bool | None:
    """None = tidak bisa dinilai (data resmi kosong); False = ada data resmi tapi tidak cocok."""
    if not truth:
        return None
    return similarity is not None and similarity >= TITLE_MATCH_THRESHOLD


def title_status(pdf_title: str | None, truth_title: str | None) -> str | None:
    """'match', 'partial' (judul utama benar, subjudul hilang/berlebih), 'wrong', 'missing', atau None."""
    if not truth_title:
        return None  # tidak ada data resmi: tidak bisa dinilai
    if not pdf_title:
        return "missing"
    pdf_n = normalize_for_compare(pdf_title)
    truth_n = normalize_for_compare(truth_title)
    if SequenceMatcher(None, pdf_n, truth_n).ratio() >= TITLE_MATCH_THRESHOLD:
        return "match"
    shorter, longer = sorted((pdf_n, truth_n), key=len)
    if len(shorter) >= MIN_PARTIAL_CHARS and longer.startswith(shorter):
        return "partial"
    return "wrong"


def doi_status(pdf_doi: str | None, truth_doi: str | None) -> str | None:
    """'match', 'wrong' (ketemu tapi salah), 'missing' (tidak ketemu), atau None (tak bisa dinilai)."""
    if not truth_doi:
        return None
    if not pdf_doi:
        return "missing"
    return "match" if pdf_doi == truth_doi else "wrong"


def evaluate(pdf_bytes: bytes, full_text: str, truth: dict, xml_bytes: bytes, doi_pages: int) -> dict:
    """Ekstrak metadata dari PDF, lalu bandingkan dengan data resmi. Tidak menyentuh database."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        pdf_title = extract_title(doc)
        pdf_doi = doi_from_pages(doc, doi_pages)
        builtin_title = _clean_title((doc.metadata or {}).get("title") or "") or None

    truth_title = truth.get("title")
    truth_doi = (truth.get("doi") or "").lower() or None
    t_status = title_status(pdf_title, truth_title)
    d_status = doi_status(pdf_doi, truth_doi)

    return {
        "pdf_title": pdf_title,
        "pdf_doi": pdf_doi,
        "builtin_title": builtin_title,
        "truth_title": truth_title,
        "truth_doi": truth_doi,
        "title_similarity": _similarity(pdf_title, truth_title),
        "title_match": None if t_status is None else t_status == "match",
        "builtin_title_match": _is_match(_similarity(builtin_title, truth_title), truth_title),
        "doi_match": None if d_status is None else d_status == "match",
        "abstract_recall": abstract_recall(abstract_from_xml(xml_bytes), full_text),
        "title_status": t_status,
        "doi_status": d_status,
    }


def run_metadata_evaluation(extractor: str, limit: int = 100) -> dict:
    doi_pages = EXTRACTORS[extractor]["doi_pages"]
    stats = {"evaluated": 0, "failed": 0}

    with get_connection() as conn:
        pending = conn.execute(PENDING_SQL, (extractor, limit)).fetchall()
        for pmcid, version, pdf_key, xml_key, json_key, pdf_md5, full_text in pending:
            pdf_bytes = get_object(pdf_key)  # error gudang dibiarkan naik (masalah infrastruktur)
            xml_bytes = get_object(xml_key)
            truth = json.loads(get_object(json_key).decode("utf-8"))
            try:
                result = evaluate(pdf_bytes, full_text, truth, xml_bytes, doi_pages)
            except Exception as exc:  # satu PDF aneh tidak boleh menghentikan seluruh batch
                logger.warning("%s v%s gagal dievaluasi (%s): %s", pmcid, version, extractor, exc)
                stats["failed"] += 1
                continue
            conn.execute(
                SAVE_SQL,
                {**result, "pmcid": pmcid, "version": version, "extractor": extractor, "pdf_md5": pdf_md5},
            )
            conn.commit()
            stats["evaluated"] += 1
    return stats


def print_report() -> None:
    with get_connection() as conn:
        rows = conn.execute(REPORT_SQL).fetchall()
        problems = conn.execute(PROBLEMS_SQL, ("heuristic-v2",)).fetchall()

    print("\nRINGKASAN PER VERSI")
    for (extractor, n, t_match, t_partial, t_wrong, t_missing,
         d_match, d_wrong, d_missing, builtin, recall) in rows:
        print(f"\n{extractor}  (n = {n})")
        print(f"  Judul : cocok {t_match} | sebagian {t_partial} | salah {t_wrong} | tidak ketemu {t_missing}")
        print(f"  DOI   : cocok {d_match} | salah {d_wrong} | tidak ketemu {d_missing}")
        print(f"  Judul bawaan PDF yang cocok: {builtin} | abstract recall rata-rata: {recall}")

    print("\nKasus yang belum cocok penuh (heuristic-v2):")
    if not problems:
        print("  (tidak ada)")
    for row in problems:
        print(" ", row)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    for name in EXTRACTORS:
        print(name, run_metadata_evaluation(name, limit))
    print_report()