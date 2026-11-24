import sqlite3
from urllib.parse import quote_plus

DB_FILE = "pseo_staging.db"


def score_and_audit():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT d.id, b.name, d.model_name, d.seed_keyword
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        ORDER BY d.id ASC
    """)
    rows = cur.fetchall()

    scored = []
    for dev_id, brand, model, kw in rows:
        tokens = model.split()
        # Beri skor prioritas tinggi untuk sub-varian (punya akhiran huruf/kode tambahan)
        is_subvariant = len(tokens) >= 2 and any(
            len(t) <= 3 and t.isalpha() for t in tokens[1:]
        )
        priority = "HIGH (Low Competition)" if is_subvariant else "NORMAL (Flagship/Core)"
        scored.append((priority, dev_id, brand, model, kw))

    high_prio = [x for x in scored if x[0].startswith("HIGH")]
    print(f"Total Keyword: {len(scored)} | Prioritas Persaingan Rendah (Sub-Varian): {len(high_prio)}\n")
    print("=== 10 SAMPEL KEYWORD PERSAINGAN RENDAH + LINK UJI GOOGLE US ===")

    for prio, dev_id, brand, model, kw in high_prio[:10]:
        allintitle_q = quote_plus(f'allintitle:"{kw}"')
        serp_q = quote_plus(kw)
        print(f"[{brand} {model}] -> {kw}")
        print(f"  1. Cek Kompetitor Judul : https://www.google.com/search?gl=us&hl=en&q={allintitle_q}")
        print(f"  2. Cek Weak Spot SERP   : https://www.google.com/search?gl=us&hl=en&q={serp_q}\n")

    conn.close()


if __name__ == "__main__":
    score_and_audit()