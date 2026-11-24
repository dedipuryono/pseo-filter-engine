import json
import re
import sqlite3
from typing import List, Optional

DB_FILE = "pseo_staging.db"
CLEAN_D1_FILE = "d1_clean_sync.sql"

# Pola nama model artefak hasil regex yang menyebabkan kanibalisasi SEO
BAD_MODEL_PATTERNS = [
    r"^replacing\s+",
    r"^replace\s+",
    r"^replacement\s+for\b",
    r"\breplacement\s+for$",
    r"^pack\s+for\b",
    r"^kit\s+for\b",
    r"^airx\s+kit\b",
    r"^genuine\s+[a-z0-9]+\s+for\b",
    r"^hepa\s+[a-z0-9]+\s+for\b",
    r"\bdue\s+for\s+cleaning\b",
    r"\bby\s+co\s+ltd\b",
    r"^core\s+300\s+core\s+300s$",
]

# Merek third-party umum agar tidak salah dilabeli OEM = 1
THIRD_PARTY_BRANDS = [
    "cabiclean", "funmit", "lemige", "bagean", "filter-monster", "filter monster",
    "maximalpower", "envirocare", "lifesupplyusa", "seteams", "linniw", "artriase",
    "ez spares", "airx filters", "puurvsas", "rovacs"
]


def is_noisy_model(model_name: str) -> bool:
    m = model_name.strip().lower()
    for pat in BAD_MODEL_PATTERNS:
        if re.search(pat, m):
            return True
    return False


def sql_str(val: Optional[str]) -> str:
    if val is None:
        return "NULL"
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def run_qa_and_clean():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=================================================================")
    print("   PSEO DATA VALIDATION & QUALITY ASSURANCE AUDIT")
    print("=================================================================\n")

    # 1. Cek Status Pipeline Keseluruhan
    cur.execute("SELECT status, COUNT(*) as cnt FROM devices GROUP BY status")
    status_map = {r["status"]: r["cnt"] for r in cur.fetchall()}
    print("[1] STATUS PIPELINE SAAT INI:")
    for st in ["pending_scrape", "scraped", "enriched", "published", "rejected"]:
        print(f"    - {st:<15}: {status_map.get(st, 0)} perangkat")

    if status_map.get("scraped", 0) > 0:
        print(f"\n[PERINGATAN] Masih ada {status_map['scraped']} perangkat berstatus 'scraped' yang belum diproses enricher.py!")

    # 2. Audit & Karantina Model Artefak (Anti-Cannibalization)
    cur.execute("SELECT id, model_name, slug FROM devices WHERE status IN ('enriched', 'published')")
    enriched_rows = cur.fetchall()

    quarantined_models: List[str] = []
    for row in enriched_rows:
        if is_noisy_model(row["model_name"]):
            cur.execute(
                "UPDATE devices SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],)
            )
            quarantined_models.append(f"{row['model_name']} ({row['slug']})")

    conn.commit()
    print(f"\n[2] FILTER ANTI-KANIBALISASI & ARTEFAK SLUG:")
    print(f"    - Model artefak dipindahkan ke 'rejected': {len(quarantined_models)}")
    for item in quarantined_models[:10]:
        print(f"      * {item}")
    if len(quarantined_models) > 10:
        print(f"      * ... dan {len(quarantined_models) - 10} lainnya.")

    # 3. Audit Produk (Koreksi False-Positive OEM & Judul Terpotong)
    cur.execute("SELECT id, raw_title, is_oem, pros_json, cons_json FROM filter_products WHERE is_verified = 1")
    products = cur.fetchall()

    fixed_oem = 0
    disabled_truncated = 0
    broken_product_json = 0

    for p in products:
        title = (p["raw_title"] or "").strip()
        lower_title = title.lower()

        # Nonaktifkan produk dengan judul terlalu pendek/terpotong (misal: hanya 'Levoit')
        if len(title.split()) < 3:
            cur.execute("UPDATE filter_products SET is_verified = 0 WHERE id = ?", (p["id"],))
            disabled_truncated += 1
            continue

        # Koreksi is_oem = 1 jika judul menyebut merek third-party
        if p["is_oem"] == 1 and any(tp in lower_title for tp in THIRD_PARTY_BRANDS):
            cur.execute("UPDATE filter_products SET is_oem = 0 WHERE id = ?", (p["id"],))
            fixed_oem += 1

        # Validasi JSON pros & cons
        try:
            pros = json.loads(p["pros_json"] or "[]")
            cons = json.loads(p["cons_json"] or "[]")
            if not isinstance(pros, list) or not isinstance(cons, list):
                broken_product_json += 1
        except Exception:
            broken_product_json += 1

    conn.commit()
    print(f"\n[3] AUDIT PRODUK AMAZON (ASIN):")
    print(f"    - Koreksi false-positive OEM -> Third-Party : {fixed_oem} produk")
    print(f"    - Produk judul terpotong dinonaktifkan      : {disabled_truncated} produk")
    print(f"    - Produk dengan JSON korup                  : {broken_product_json} produk")

    # 4. Validasi Integritas Data Halaman Siap Terbit
    cur.execute("""
        SELECT d.id, b.name AS brand, d.model_name, d.slug, d.official_filter_code,
               d.hepa_grade, d.dimensions_json, d.direct_answer_capsule,
               d.key_takeaways_json, d.faqs_json,
               (SELECT COUNT(*) FROM filter_products fp WHERE fp.device_id = d.id AND fp.is_verified = 1) AS active_prods
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        WHERE d.status IN ('enriched', 'published')
    """)
    clean_devices = cur.fetchall()

    json_errors = 0
    zero_prod_devices = 0
    short_capsule = 0
    with_part_code = 0
    with_hepa = 0
    with_dims = 0

    for d in clean_devices:
        if d["official_filter_code"]:
            with_part_code += 1
        if d["hepa_grade"]:
            with_hepa += 1
        if d["dimensions_json"]:
            with_dims += 1

        if d["active_prods"] == 0:
            cur.execute("UPDATE devices SET status = 'rejected' WHERE id = ?", (d["id"],))
            zero_prod_devices += 1
            continue

        capsule_words = len((d["direct_answer_capsule"] or "").split())
        if capsule_words < 25:
            short_capsule += 1

        try:
            tk = json.loads(d["key_takeaways_json"] or "[]")
            fq = json.loads(d["faqs_json"] or "[]")
            if d["dimensions_json"]:
                json.loads(d["dimensions_json"])
            if len(tk) == 0 or len(fq) == 0:
                json_errors += 1
        except Exception:
            json_errors += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM devices WHERE status IN ('enriched', 'published')")
    final_ready = cur.fetchone()[0]

    print(f"\n[4] SKOR VALIDASI FINAL (HALAMAN SIAP TERBIT):")
    print(f"    - Total Halaman Bersih & Tervalidasi : {final_ready} halaman")
    print(f"    - Memiliki Official Part Code        : {with_part_code}/{final_ready}")
    print(f"    - Memiliki Spesifikasi HEPA Grade    : {with_hepa}/{final_ready}")
    print(f"    - Memiliki Dimensi Inci (SVG)        : {with_dims}/{final_ready}")
    print(f"    - Halaman tanpa produk aktif (dibuang): {zero_prod_devices}")
    print(f"    - Error parsing JSON                 : {json_errors}")
    print(f"    - Kapsul GEO terlalu pendek (<25 kata): {short_capsule}")

    # 5. Ekspor Ulang SQL Bersih untuk Cloudflare D1
    export_clean_sql(conn)
    conn.close()


def export_clean_sql(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    lines = [
        "-- CLEAN & VALIDATED Cloudflare D1 Sync File",
        "-- Jalankan: npx wrangler d1 execute pseo-filter-db --remote --file=d1_clean_sync.sql\n"
    ]

    cur.execute("SELECT name, slug, category FROM brands ORDER BY id")
    for name, slug, category in cur.fetchall():
        lines.append(
            f"INSERT OR IGNORE INTO brands (name, slug, category) "
            f"VALUES ({sql_str(name)}, {sql_str(slug)}, {sql_str(category)});"
        )
    lines.append("")

    cur.execute("""
        SELECT b.slug, d.model_name, d.slug, d.category, d.seed_keyword,
               d.official_filter_code, d.hepa_grade, d.filter_shape,
               d.dimensions_json, d.compatibility_warning, d.faqs_json,
               d.direct_answer_capsule, d.key_takeaways_json, d.status
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        WHERE d.status IN ('enriched', 'published')
        ORDER BY d.id
    """)
    for row in cur.fetchall():
        (b_slug, model_name, d_slug, category, seed_kw,
         code, hepa, shape, dims, warn, faqs, capsule, takeaways, status) = row

        lines.append(
            f"INSERT INTO devices (brand_id, model_name, slug, category, seed_keyword, "
            f"official_filter_code, hepa_grade, filter_shape, dimensions_json, "
            f"compatibility_warning, faqs_json, direct_answer_capsule, key_takeaways_json, status) "
            f"VALUES ((SELECT id FROM brands WHERE slug = {sql_str(b_slug)}), "
            f"{sql_str(model_name)}, {sql_str(d_slug)}, {sql_str(category)}, {sql_str(seed_kw)}, "
            f"{sql_str(code)}, {sql_str(hepa)}, {sql_str(shape)}, {sql_str(dims)}, "
            f"{sql_str(warn)}, {sql_str(faqs)}, {sql_str(capsule)}, {sql_str(takeaways)}, {sql_str(status)}) "
            f"ON CONFLICT(slug) DO UPDATE SET "
            f"official_filter_code=excluded.official_filter_code, "
            f"hepa_grade=excluded.hepa_grade, "
            f"filter_shape=excluded.filter_shape, "
            f"dimensions_json=excluded.dimensions_json, "
            f"compatibility_warning=excluded.compatibility_warning, "
            f"faqs_json=excluded.faqs_json, "
            f"direct_answer_capsule=excluded.direct_answer_capsule, "
            f"key_takeaways_json=excluded.key_takeaways_json, "
            f"status=excluded.status, updated_at=CURRENT_TIMESTAMP;"
        )
    lines.append("")

    cur.execute("""
        SELECT d.slug, p.asin, p.raw_title, p.is_oem, p.rating, p.review_count,
               p.pros_json, p.cons_json, p.is_verified
        FROM filter_products p
        JOIN devices d ON p.device_id = d.id
        WHERE d.status IN ('enriched', 'published') AND p.is_verified = 1
        ORDER BY p.id
    """)
    for d_slug, asin, title, is_oem, rating, rev_cnt, pros, cons, is_ver in cur.fetchall():
        r_val = "NULL" if rating is None else str(rating)
        lines.append(
            f"INSERT INTO filter_products (device_id, asin, raw_title, is_oem, rating, review_count, pros_json, cons_json, is_verified) "
            f"VALUES ((SELECT id FROM devices WHERE slug = {sql_str(d_slug)}), "
            f"{sql_str(asin)}, {sql_str(title)}, {is_oem}, {r_val}, {rev_cnt or 0}, "
            f"{sql_str(pros)}, {sql_str(cons)}, {is_ver}) "
            f"ON CONFLICT(device_id, asin) DO UPDATE SET "
            f"is_oem=excluded.is_oem, rating=excluded.rating, review_count=excluded.review_count, "
            f"pros_json=excluded.pros_json, cons_json=excluded.cons_json, is_verified=excluded.is_verified;"
        )

    with open(CLEAN_D1_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[5] EKSPOR SELESAI: File '{CLEAN_D1_FILE}' siap di-push ke Cloudflare D1.")


if __name__ == "__main__":
    run_qa_and_clean()