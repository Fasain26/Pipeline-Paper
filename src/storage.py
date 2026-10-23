"""Akses ke object storage S3-compatible (SeaweedFS): simpan dan baca data mentah."""

from functools import lru_cache

import boto3
from botocore.config import Config

from src.config import get_env


@lru_cache(maxsize=1)
def get_s3_client():
    """Buat satu klien S3 yang dipakai ulang (dibuat sekali, lalu disimpan)."""
    return boto3.client(
        "s3",
        endpoint_url=get_env("S3_ENDPOINT_URL"),
        aws_access_key_id=get_env("S3_ACCESS_KEY"),
        aws_secret_access_key=get_env("S3_SECRET_KEY"),
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def put_object(key: str, body: bytes | str, content_type: str = "application/octet-stream") -> None:
    """Simpan satu object ke bucket mentah."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    get_s3_client().put_object(
        Bucket=get_env("S3_BUCKET_RAW"),
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def get_object(key: str) -> bytes:
    """Baca isi satu object."""
    response = get_s3_client().get_object(Bucket=get_env("S3_BUCKET_RAW"), Key=key)
    return response["Body"].read()


def list_keys(prefix: str = "") -> list[str]:
    """Daftar semua key yang diawali prefix tertentu."""
    paginator = get_s3_client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=get_env("S3_BUCKET_RAW"), Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def delete_object(key: str) -> None:
    """Hapus satu object."""
    get_s3_client().delete_object(Bucket=get_env("S3_BUCKET_RAW"), Key=key)


if __name__ == "__main__":
    test_key = "_smoke_test/hello.txt"
    put_object(test_key, "halo dari pipeline", content_type="text/plain")
    print("Tulis berhasil:", test_key)

    print("Isi dibaca kembali:", get_object(test_key).decode("utf-8"))
    print("Daftar key:", list_keys("_smoke_test/"))

    delete_object(test_key)
    print("Setelah dihapus:", list_keys("_smoke_test/"))