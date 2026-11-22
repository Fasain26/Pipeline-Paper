-- Menandai artikel yang dipakai merancang heuristik ('dev') dan yang belum pernah dilihat ('test').
-- Artikel yang SUDAH ada saat migrasi ini pertama kali dijalankan otomatis menjadi 'dev';
-- artikel yang diunduh sesudahnya menjadi 'test'. Aman dijalankan berulang.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'pmc_articles'
          AND column_name = 'eval_set'
    ) THEN
        ALTER TABLE pmc_articles
            ADD COLUMN eval_set TEXT NOT NULL DEFAULT 'dev' CHECK (eval_set IN ('dev', 'test'));
        ALTER TABLE pmc_articles ALTER COLUMN eval_set SET DEFAULT 'test';
    END IF;
END
$$;