-- Hasil ekstraksi teks dari PDF artikel PMC. Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS pmc_pdf_extractions (
    pmcid          TEXT        NOT NULL,
    version        INTEGER     NOT NULL,
    status         TEXT        NOT NULL CHECK (status IN ('extracted', 'failed')),
    error_message  TEXT,
    page_count     INTEGER,
    char_count     INTEGER,
    word_count     INTEGER,
    chars_per_page NUMERIC(10, 1),
    needs_ocr      BOOLEAN,
    pdf_info       JSONB,
    full_text      TEXT,
    extractor      TEXT        NOT NULL,
    pdf_md5        TEXT,
    extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pmcid, version),
    FOREIGN KEY (pmcid, version) REFERENCES pmc_articles (pmcid, version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pmc_extractions_status ON pmc_pdf_extractions (status);