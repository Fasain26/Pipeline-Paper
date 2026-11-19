-- Hasil ekstraksi metadata dari PDF beserta evaluasinya terhadap data resmi PMC.

CREATE TABLE IF NOT EXISTS pmc_pdf_metadata (
    pmcid               TEXT        NOT NULL,
    version             INTEGER     NOT NULL,
    pdf_title           TEXT,
    pdf_doi             TEXT,
    builtin_title       TEXT,
    truth_title         TEXT,
    truth_doi           TEXT,
    title_similarity    NUMERIC(4, 3),
    title_match         BOOLEAN,
    builtin_title_match BOOLEAN,
    doi_match           BOOLEAN,
    abstract_recall     NUMERIC(4, 3),
    extractor           TEXT        NOT NULL,
    pdf_md5             TEXT,
    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pmcid, version),
    FOREIGN KEY (pmcid, version) REFERENCES pmc_articles (pmcid, version) ON DELETE CASCADE
);