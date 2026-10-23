-- Skema awal project paper-pipeline.
-- Aman dijalankan berulang kali (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS papers (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           TEXT        NOT NULL,
    source_id        TEXT        NOT NULL,
    version          INTEGER,
    doi              TEXT,
    title            TEXT        NOT NULL,
    abstract         TEXT,
    published_at     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ,
    primary_category TEXT,
    categories       TEXT[]      NOT NULL DEFAULT '{}',
    journal_ref      TEXT,
    pdf_url          TEXT,
    abs_url          TEXT,
    raw_key          TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_tsv       TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(abstract, ''))
    ) STORED,
    CONSTRAINT papers_source_unique UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_papers_doi          ON papers (doi);
CREATE INDEX IF NOT EXISTS idx_papers_published_at ON papers (published_at);
CREATE INDEX IF NOT EXISTS idx_papers_search       ON papers USING GIN (search_tsv);

CREATE TABLE IF NOT EXISTS authors (
    id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id  BIGINT  NOT NULL REFERENCES papers (id) ON DELETE CASCADE,
    author_id BIGINT  NOT NULL REFERENCES authors (id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (paper_id, position)
);

CREATE INDEX IF NOT EXISTS idx_paper_authors_author ON paper_authors (author_id);

CREATE TABLE IF NOT EXISTS rejected_records (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      TEXT        NOT NULL,
    source_id   TEXT,
    raw_key     TEXT,
    reason      TEXT        NOT NULL,
    payload     JSONB,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline      TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    run_date      DATE,
    status        TEXT        NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_in       INTEGER     NOT NULL DEFAULT 0,
    rows_out      INTEGER     NOT NULL DEFAULT 0,
    rows_rejected INTEGER     NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);