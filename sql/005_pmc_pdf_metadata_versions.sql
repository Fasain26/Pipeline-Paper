-- Biarkan beberapa versi heuristik hidup berdampingan, dan catat status hasil lebih rinci.

ALTER TABLE pmc_pdf_metadata ADD COLUMN IF NOT EXISTS title_status TEXT;
ALTER TABLE pmc_pdf_metadata ADD COLUMN IF NOT EXISTS doi_status   TEXT;

ALTER TABLE pmc_pdf_metadata DROP CONSTRAINT IF EXISTS pmc_pdf_metadata_pkey;
ALTER TABLE pmc_pdf_metadata ADD PRIMARY KEY (pmcid, version, extractor);