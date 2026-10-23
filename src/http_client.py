"""Fungsi HTTP bersama: GET dengan retry otomatis dan jeda bertahap (exponential backoff)."""

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

# Status yang biasanya sementara, jadi layak dicoba lagi
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _retry_delay(attempt: int, base_delay: float, response: requests.Response | None) -> float:
    """Hitung berapa detik harus menunggu sebelum mencoba lagi."""
    if response is not None:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():  # server secara eksplisit meminta kita menunggu
            return float(retry_after)
    return base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)


def get_with_retry(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    max_retries: int = 5,
    base_delay: float = 2.0,
) -> requests.Response:
    """GET dengan retry. Error sementara diulang; error permanen langsung dilempar."""
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        response = None
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            reason = type(exc).__name__
        else:
            if response.ok:
                return response
            error = requests.HTTPError(
                f"{response.status_code} dari {url}: {response.text[:2000]}",
                response=response,
            )
            if response.status_code not in RETRYABLE_STATUS:
                raise error  # misalnya 400/404: mengulang tidak akan membantu
            last_error = error
            reason = f"HTTP {response.status_code}"

        if attempt == max_retries:
            break

        delay = _retry_delay(attempt, base_delay, response)
        logger.warning(
            "Percobaan %d/%d gagal (%s). Ulangi dalam %.1f detik.",
            attempt, max_retries, reason, delay,
        )
        time.sleep(delay)

    raise last_error