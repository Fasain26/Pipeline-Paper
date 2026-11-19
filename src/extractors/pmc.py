"""PMC Open Access: pencarian lewat E-Utilities, metadata dan file lewat bucket S3 publik."""

import json
import logging
from functools import lru_cache

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

from src.config import get_env
from src.http_client import get_with_retry

logger = logging.getLogger(__name__)

PMC_BUCKET = "pmc-oa-opendata"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TOOL_NAME = "paper-pipeline"

# Hanya lisensi yang mengizinkan pemakaian komersial (pilihan konservatif)
ALLOWED_LICENSES = {"CC0", "CC BY", "CC BY-SA", "CC BY-ND"}

LICENSE_FILTER = (
    "(cc0_license[Filter] OR cc_by_license[Filter] "
    "OR cc_by-sa_license[Filter] OR cc_by-nd_license[Filter])"
)

TOPIC = (
    '"machine learning"[Title/Abstract] AND '
    '(sepsis[Title/Abstract] OR "intensive care"[Title/Abstract])'
)


@lru_cache(maxsize=1)
def get_pmc_client():
    """Klien S3 anonim (tanpa kredensial) untuk bucket publik PMC."""
    return boto3.client(
        "s3",
        region_name="us-east-1",
        config=Config(signature_version=UNSIGNED),
    )


def build_query(topic: str) -> str:
    """Gabungkan topik dengan filter lisensi, akses terbuka, dan ketersediaan PDF."""
    return f"({topic}) AND {LICENSE_FILTER} AND open_access[Filter] AND has_pdf[Filter]"


def esearch_pmc(term: str, retmax: int = 20, retstart: int = 0) -> dict:
    """Cari di PMC lewat E-Utilities. Mengembalikan jumlah hasil, daftar PMCID, dan diagnostik."""
    params = {
        "db": "pmc",
        "term": term,
        "retmax": retmax,
        "retstart": retstart,
        "retmode": "json",
        "tool": TOOL_NAME,
        "email": get_env("NCBI_EMAIL"),
    }
    response = get_with_retry(ESEARCH_URL, params=params)
    result = response.json()["esearchresult"]
    return {
        "total": int(result.get("count", 0)),
        "ids": result.get("idlist", []),
        "translation": result.get("querytranslation"),
        "warnings": result.get("warninglist"),
        "errors": result.get("errorlist"),
    }


def list_versions(pmcid_number: str) -> list[str]:
    """Daftar prefix versi untuk satu PMCID, misalnya ['PMC12810641.1/']."""
    response = get_pmc_client().list_objects_v2(
        Bucket=PMC_BUCKET,
        Prefix=f"PMC{pmcid_number}.",  # titik di akhir mencegah PMC12 cocok dengan PMC123
        Delimiter="/",
    )
    return [p["Prefix"] for p in response.get("CommonPrefixes", [])]


def read_metadata(version_prefix: str) -> dict:
    """Baca JSON metadata satu versi artikel."""
    name = version_prefix.rstrip("/")
    key = f"{version_prefix}{name}.json"
    body = get_pmc_client().get_object(Bucket=PMC_BUCKET, Key=key)["Body"].read()
    meta = json.loads(body)
    meta["s3_prefix"] = version_prefix
    return meta


def eligibility(meta: dict) -> tuple[bool, str]:
    """Periksa apakah satu versi artikel boleh diproses. Mengembalikan (layak, alasan)."""
    if not meta.get("is_pmc_openaccess"):
        return False, "bukan bagian dari PMC Open Access Subset"
    if meta.get("is_retracted"):
        return False, "artikel ditarik (retracted)"
    if meta.get("license_code") not in ALLOWED_LICENSES:
        return False, f"lisensi tidak diizinkan: {meta.get('license_code')}"
    if not meta.get("pdf_url"):
        return False, "tidak ada PDF"
    return True, "ok"


def find_eligible_version(pmcid_number: str) -> tuple[dict | None, str]:
    """Cari versi artikel yang layak. Mengembalikan (metadata atau None, alasan)."""
    versions = list_versions(pmcid_number)
    if not versions:
        return None, "tidak ditemukan di bucket"

    reasons = []
    for prefix in sorted(versions):
        try:
            meta = read_metadata(prefix)
        except ClientError as exc:
            reasons.append(f"{prefix.rstrip('/')}: JSON tidak terbaca ({exc.response['Error']['Code']})")
            continue
        ok, reason = eligibility(meta)
        if ok:
            return meta, "ok"
        reasons.append(f"{prefix.rstrip('/')}: {reason}")
    return None, "; ".join(reasons)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = esearch_pmc(build_query(TOPIC), retmax=20)
    print("Query setelah diterjemahkan NCBI:", result["translation"])
    print("Peringatan:", result["warnings"], "| Error:", result["errors"])
    print(f"Total cocok: {result['total']} | diambil: {len(result['ids'])}")
    print("-" * 70)

    eligible = 0
    licenses_seen: dict[str, int] = {}
    for pmcid in result["ids"]:
        meta, reason = find_eligible_version(pmcid)
        if meta:
            eligible += 1
            licenses_seen[meta["license_code"]] = licenses_seen.get(meta["license_code"], 0) + 1
            title = (meta.get("title") or "")[:65]
            print(f"PMC{pmcid:<10} LAYAK   | {meta['license_code']:<9} | {title}")
        else:
            print(f"PMC{pmcid:<10} DITOLAK | {reason}")

    print("-" * 70)
    print(f"Layak diproses: {eligible} dari {len(result['ids'])}")
    print("Lisensi yang ditemukan:", licenses_seen)