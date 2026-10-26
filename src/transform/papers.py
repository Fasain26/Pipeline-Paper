"""Tahap Transform: baca XML mentah dari gudang, bersihkan, dan validasi dengan Pydantic."""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.extractors.arxiv import parse_feed
from src.storage import get_object, list_keys

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")


class PaperRecord(BaseModel):
    """Bentuk BAKU sebuah paper. Record yang tidak memenuhi aturan di sini ditolak."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: Literal["arxiv", "crossref"]
    source_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)
    doi: str | None = None
    title: str = Field(min_length=1)
    abstract: str | None = None
    authors: list[str] = Field(min_length=1)
    published_at: datetime
    updated_at: datetime | None = None
    primary_category: str | None = None
    categories: list[str] = Field(default_factory=list)
    journal_ref: str | None = None
    pdf_url: str | None = None
    abs_url: str | None = None
    raw_key: str | None = None

    @field_validator("doi", mode="before")
    @classmethod
    def normalize_doi(cls, value):
        """Seragamkan DOI; kalau formatnya tidak valid, jadikan None (bukan menolak record)."""
        if not value:
            return None
        doi = str(value).strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
        return doi if DOI_PATTERN.match(doi) else None

    @field_validator("abstract", "journal_ref", "primary_category", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        """Teks kosong atau hanya spasi dianggap tidak ada (None)."""
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("authors", mode="before")
    @classmethod
    def clean_authors(cls, value):
        """Rapikan spasi dan buang nama kosong atau ganda, urutan tetap dijaga."""
        if not isinstance(value, list):
            return value  # biarkan pemeriksaan tipe bawaan yang menolak
        seen: set[str] = set()
        cleaned: list[str] = []
        for name in value:
            name = " ".join(str(name).split())
            if name and name not in seen:
                seen.add(name)
                cleaned.append(name)
        return cleaned

    @field_validator("pdf_url", "abs_url")
    @classmethod
    def must_be_http(cls, value):
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("URL harus diawali http:// atau https://")
        return value

    @field_validator("published_at", "updated_at")
    @classmethod
    def ensure_utc(cls, value):
        """Tanggal tanpa zona waktu dianggap UTC, supaya semua tanggal bisa dibandingkan."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def check_dates(self):
        """Aturan yang melibatkan lebih dari satu kolom."""
        if self.updated_at and self.updated_at < self.published_at:
            raise ValueError("updated_at lebih awal daripada published_at")
        if self.published_at > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("published_at berada di masa depan")
        return self


@dataclass
class TransformResult:
    valid: list[PaperRecord] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    entries_in: int = 0
    duplicates_dropped: int = 0


def _from_arxiv(rec: dict, raw_key: str) -> dict:
    """Petakan record hasil parse arXiv ke nama kolom baku kita."""
    return {
        "source": rec.get("source"),
        "source_id": rec.get("source_id"),
        "version": rec.get("version"),
        "doi": rec.get("doi"),
        "title": rec.get("title"),
        "abstract": rec.get("abstract"),
        "authors": rec.get("authors"),
        "published_at": rec.get("published"),
        "updated_at": rec.get("updated"),
        "primary_category": rec.get("primary_category"),
        "categories": rec.get("categories") or [],
        "journal_ref": rec.get("journal_ref"),
        "pdf_url": rec.get("pdf_url"),
        "abs_url": rec.get("abs_url"),
        "raw_key": raw_key,
    }


def _format_errors(exc: ValidationError) -> str:
    """Ringkas daftar error Pydantic menjadi satu kalimat: 'kolom: pesan; kolom: pesan'."""
    parts = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"]) or "record"
        parts.append(f"{location}: {err['msg']}")
    return "; ".join(parts)


def validate_records(raw_records: list[tuple[dict, str]]) -> TransformResult:
    """Validasi daftar (record, raw_key). Hasilnya: yang lolos, yang ditolak, dan statistik."""
    result = TransformResult(entries_in=len(raw_records))
    best: dict[tuple[str, str], PaperRecord] = {}

    for rec, raw_key in raw_records:
        try:
            model = PaperRecord(**_from_arxiv(rec, raw_key))
        except ValidationError as exc:
            result.rejected.append(
                {
                    "source": rec.get("source") or "arxiv",
                    "source_id": rec.get("source_id"),
                    "raw_key": raw_key,
                    "reason": _format_errors(exc),
                    "payload": rec,
                }
            )
            continue

        key = (model.source, model.source_id)
        previous = best.get(key)
        if previous is None:
            best[key] = model
        else:
            result.duplicates_dropped += 1
            if (model.version or 0) > (previous.version or 0):
                best[key] = model

    result.valid = list(best.values())
    return result


def latest_run_date() -> str:
    """Cari tanggal partisi terbaru yang ada di gudang."""
    dates: set[str] = set()
    for key in list_keys("arxiv/dt="):
        match = re.match(r"^arxiv/dt=(\d{4}-\d{2}-\d{2})/", key)
        if match:
            dates.add(match.group(1))
    if not dates:
        raise RuntimeError(
            "Belum ada data mentah arXiv di gudang. Jalankan dulu: python -m src.ingestion.arxiv_raw"
        )
    return max(dates)


def load_raw_arxiv_records(run_date: str) -> list[tuple[dict, str]]:
    """Baca halaman XML mentah dari run yang LENGKAP (punya manifest) dan parse jadi record."""
    records: list[tuple[dict, str]] = []
    prefix = f"arxiv/dt={run_date}/"
    manifest_keys = [k for k in list_keys(prefix) if k.endswith("/manifest.json")]

    for manifest_key in manifest_keys:
        manifest = json.loads(get_object(manifest_key).decode("utf-8"))
        for page in manifest["pages"]:
            _, parsed = parse_feed(get_object(page["key"]).decode("utf-8"))
            records.extend((rec, page["key"]) for rec in parsed)
    return records


if __name__ == "__main__":
    run_date = sys.argv[1] if len(sys.argv) > 1 else latest_run_date()
    print("Memproses partisi tanggal:", run_date)

    result = validate_records(load_raw_arxiv_records(run_date))
    print(
        f"Masuk: {result.entries_in} | Lolos: {len(result.valid)} | "
        f"Ditolak: {len(result.rejected)} | Duplikat dibuang: {result.duplicates_dropped}"
    )

    if result.valid:
        sample = result.valid[0]
        print("Contoh record bersih:")
        print("  ID      :", sample.source_id, f"(v{sample.version})")
        print("  Judul   :", sample.title)
        print("  Penulis :", ", ".join(sample.authors[:3]))
        print("  Terbit  :", sample.published_at.isoformat())
        print("  DOI     :", sample.doi)

    for rejected in result.rejected[:3]:
        print("Ditolak:", rejected["source_id"], "->", rejected["reason"])