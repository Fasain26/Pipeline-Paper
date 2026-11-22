"""Laporan akurasi ekstraksi metadata PDF, dipisah per set (dev/test) dan per versi heuristik."""

from src.db import get_connection

REPORT_SQL = """
SELECT a.eval_set, m.extractor,
       count(*),
       count(*) FILTER (WHERE m.title_status = 'match'),
       count(*) FILTER (WHERE m.title_status = 'partial'),
       count(*) FILTER (WHERE m.title_status = 'wrong'),
       count(*) FILTER (WHERE m.title_status = 'missing'),
       count(*) FILTER (WHERE m.doi_status = 'match'),
       count(*) FILTER (WHERE m.doi_status = 'wrong'),
       count(*) FILTER (WHERE m.doi_status = 'missing'),
       count(*) FILTER (WHERE m.builtin_title_match),
       round(avg(m.abstract_recall), 3)
FROM pmc_pdf_metadata m
JOIN pmc_articles a ON a.pmcid = m.pmcid AND a.version = m.version
GROUP BY a.eval_set, m.extractor
ORDER BY a.eval_set, m.extractor
"""

PROBLEMS_SQL = """
SELECT m.pmcid, m.title_status, m.doi_status, m.title_similarity,
       left(m.pdf_title, 90), left(m.truth_title, 90), m.pdf_doi, m.truth_doi
FROM pmc_pdf_metadata m
JOIN pmc_articles a ON a.pmcid = m.pmcid AND a.version = m.version
WHERE m.extractor = %s
  AND a.eval_set = %s
  AND (m.title_status IS DISTINCT FROM 'match' OR m.doi_status IS DISTINCT FROM 'match')
ORDER BY m.pmcid
"""


def print_report() -> None:
    with get_connection() as conn:
        rows = conn.execute(REPORT_SQL).fetchall()
        problems = conn.execute(PROBLEMS_SQL, ("heuristic-v2", "test")).fetchall()

    print("RINGKASAN  (dev = dipakai merancang v2 | test = belum pernah dilihat)")
    for (eval_set, extractor, n, t_match, t_partial, t_wrong, t_missing,
         d_match, d_wrong, d_missing, builtin, recall) in rows:
        print(f"\n[{eval_set}] {extractor}  (n = {n})")
        print(f"  Judul : cocok {t_match} | sebagian {t_partial} | salah {t_wrong} | tidak ketemu {t_missing}")
        print(f"  DOI   : cocok {d_match} | salah {d_wrong} | tidak ketemu {d_missing}")
        print(f"  Judul bawaan PDF yang cocok: {builtin} | abstract recall rata-rata: {recall}")

    print("\nKasus di set TEST yang belum cocok penuh (heuristic-v2):")
    if not problems:
        print("  (tidak ada)")
    for row in problems:
        print(" ", row)


if __name__ == "__main__":
    print_report()