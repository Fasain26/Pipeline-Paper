-- Buku catatan artikel PMC yang diunduh ke gudang. Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS pmc_articles (
    pmcid          TEXT        NOT NULL,
    version        INTEGER     NOT NULL,
    pmid           BIGINT,
    doi            TEXT,
    title          TEXT,
    license_code   TEXT        NOT NULL,
    is_manuscript  BOOLEAN     NOT NULL DEFAULT FALSE,
    s3_prefix      TEXT        NOT NULL,
    pdf_key        TEXT,
    xml_key        TEXT,
    json_key       TEXT,
    pdf_md5        TEXT,
    pdf_size_bytes BIGINT,
    status         TEXT        NOT NULL CHECK (status IN ('downloaded', 'failed')),
    error_message  TEXT,
    downloaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pmcid, version)
);

CREATE INDEX IF NOT EXISTS idx_pmc_articles_status ON pmc_articles (status);
CREATE INDEX IF NOT EXISTS idx_pmc_articles_doi    ON pmc_articles (doi);