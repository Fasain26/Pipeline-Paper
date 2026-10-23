"""Membaca pengaturan dari file .env (satu tempat untuk seluruh project)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_env(name: str, default: str | None = None) -> str:
    """Ambil satu pengaturan; beri error yang jelas kalau wajib tapi kosong."""
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Pengaturan '{name}' belum diisi di file .env")
    return value