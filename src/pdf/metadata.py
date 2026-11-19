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

EXTRACTOR = "heuristic-v1"
TITLE_MATCH_THRESHOLD = 0.90
MIN_TITLE_WORDS = 4
TITLE_REGION = 0.5  # judul dicari hanya di separuh atas halaman pertama

DOI_PATTERN = re.compile(r"10\.\d{4,9}/\s?[^\s\"<>]+")

LIGATURES = str.maketrans(
    {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}
)

PENDING_SQL = """
SELECT a.pmcid, a.version, a.pdf_key, a.xml_key, a.json_key, a.pdf_md5, e.full_text
FROM pmc_articles a
JOIN pmc_pdf_extractions e
  ON e.pmcid = a.pmcid AND e.version = a.version AND e.status = 'extracted'
LEFT JOIN pmc_pdf_metadata m ON m.pmcid = a.pmcid AND m.version = a.version
WHERE a.status = 'downloaded'
  AND (m.pmcid IS NULL
       OR m.pdf_md5 IS DISTINCT FROM a.pdf_md5
       OR m.extractor IS DISTINCT FROM %s
       OR e.extracted_at > m.evaluated_at)
ORDER BY a.downloaded_at
LIMIT %s
"""

SAVE_SQL = """
INSERT INTO pmc_pdf_metadata (
    pmcid, version, pdf_title, pdf_doi, builtin_title, truth_title, truth_doi,
    title_similarity, title_match, builtin_title_match, doi_match, abstract_recall,
    extractor, pdf_md5
) VALUES (
    %(pmcid)s, %(version)s, %(pdf_title)s, %(pdf_doi)s, %(builtin_title)s,
    %(truth_title)s, %(truth_doi)s, %(title_similarity)s, %(title_match)s,
    %(builtin_title_match)s, %(doi_match)s, %(abstract_recall)s, %(extractor)s, %(pdf_md5)s
)
ON CONFLICT (pmcid, version) DO UPDATE SET
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
    extractor           = EXCLUDED.extractor,
    pdf_md5             = EXCLUDED.pdf_md5,
    evaluated_at        = now()
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
    """Ambil DOI pertama pada teks halaman; menangani DOI terputus dan kata yang menempel."""
    match = DOI_PATTERN.search(page_text)
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group(0))
    raw = re.sub(r"(?<=\d)[A-Z][a-z]{3,}.*$", "", raw)  # buang kata menempel, mis. '...0362057Editor:'
    doi = raw.lower().rstrip(".,;:)]}'\"")
    return doi or None


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


def evaluate(pdf_bytes: bytes, full_text: str, truth: dict, xml_bytes: bytes) -> dict:
    """Ekstrak metadata dari PDF, lalu bandingkan dengan data resmi. Tidak menyentuh database."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        pdf_title = extract_title(doc)
        pdf_doi = extract_doi(doc[0].get_text("text"))
        builtin_title = _clean_title((doc.metadata or {}).get("title") or "") or None

    truth_title = truth.get("title")
    truth_doi = (truth.get("doi") or "").lower() or None
    heuristic_similarity = _similarity(pdf_title, truth_title)
    builtin_similarity = _similarity(builtin_title, truth_title)

    return {
        "pdf_title": pdf_title,
        "pdf_doi": pdf_doi,
        "builtin_title": builtin_title,
        "truth_title": truth_title,
        "truth_doi": truth_doi,
        "title_similarity": heuristic_similarity,
        "title_match": _is_match(heuristic_similarity, truth_title),
        "builtin_title_match": _is_match(builtin_similarity, truth_title),
        "doi_match": None if truth_doi is None else pdf_doi == truth_doi,
        "abstract_recall": abstract_recall(abstract_from_xml(xml_bytes), full_text),
    }


def run_metadata_evaluation(limit: int = 20) -> dict:
    stats = {"evaluated": 0, "failed": 0}
    with get_connection() as conn:
        pending = conn.execute(PENDING_SQL, (EXTRACTOR, limit)).fetchall()
        for pmcid, version, pdf_key, xml_key, json_key, pdf_md5, full_text in pending:
            pdf_bytes = get_object(pdf_key)  # error gudang dibiarkan naik (masalah infrastruktur)
            xml_bytes = get_object(xml_key)
            truth = json.loads(get_object(json_key).decode("utf-8"))
            try:
                result = evaluate(pdf_bytes, full_text, truth, xml_bytes)
            except Exception as exc:  # satu PDF aneh tidak boleh menghentikan seluruh batch
                logger.warning("%s v%s gagal dievaluasi: %s", pmcid, version, exc)
                stats["failed"] += 1
                continue
            conn.execute(
                SAVE_SQL,
                {**result, "pmcid": pmcid, "version": version, "extractor": EXTRACTOR, "pdf_md5": pdf_md5},
            )
            conn.commit()
            stats["evaluated"] += 1
            logger.info("%s v%s dievaluasi", pmcid, version)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print("Ringkasan run:", run_metadata_evaluation(limit))

    with get_connection() as conn:
        summary = conn.execute(
            "SELECT count(*), "
            "round(100.0 * avg(title_match::int), 1), "
            "round(100.0 * avg(builtin_title_match::int), 1), "
            "round(100.0 * avg(doi_match::int), 1), "
            "round(avg(abstract_recall), 3) "
            "FROM pmc_pdf_metadata WHERE extractor = %s",
            (EXTRACTOR,),
        ).fetchone()
        rows = conn.execute(
            "SELECT pmcid, title_similarity, title_match, builtin_title_match, doi_match, "
            "abstract_recall, left(pdf_title, 50), left(truth_title, 50), pdf_doi, truth_doi "
            "FROM pmc_pdf_metadata WHERE extractor = %s ORDER BY pmcid",
            (EXTRACTOR,),
        ).fetchall()

    print(f"\nAKURASI ({EXTRACTOR}) pada {summary[0]} artikel:")
    print(f"  Judul cocok (heuristik font terbesar): {summary[1]}%")
    print(f"  Judul cocok (metadata bawaan PDF)    : {summary[2]}%")
    print(f"  DOI cocok                            : {summary[3]}%")
    print(f"  Rata-rata abstract recall            : {summary[4]}")
    print("\nPer artikel (pmcid, kemiripan, judul?, bawaan?, doi?, recall, judul PDF, judul resmi, doi PDF, doi resmi):")
    for row in rows:
        print(" ", row)