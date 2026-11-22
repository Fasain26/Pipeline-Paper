"""Unduh set uji PMC dari beberapa topik berbeda, supaya ragam penerbit dan tata letaknya lebih luas."""

import logging

from src.ingestion.pmc_raw import run_pmc_download

TEST_TOPICS = {
    "radiology": '("deep learning"[Title/Abstract] AND (radiology[Title/Abstract] OR imaging[Title/Abstract]))',
    "ehr_nlp": '("natural language processing"[Title/Abstract] AND "electronic health records"[Title/Abstract])',
    "genomics": '("machine learning"[Title/Abstract] AND ("gene expression"[Title/Abstract] OR genomics[Title/Abstract]))',
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    for name, topic in TEST_TOPICS.items():
        stats = run_pmc_download(max_new=10, topic=topic)
        print(f"Topik {name}: {stats}")