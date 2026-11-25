import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

DB_FILE = "pseo_staging.db"
BATCH_SQL_FILE = "d1_publish_batch.sql"
ASTRO_DATA_FILE = os.path.join("web-app", "src", "data", "catalog.json")
DAILY_LIMIT = 15


def is_low_competition_variant(model_name: str) -> int:
    tokens = model_name.split()
    if len(tokens) >= 2 and any(len(t) <= 3 and t.isalpha() for t in tokens[1:]):
        return 1
    return 2


def publish_next_batch(limit: int = DAILY_LIMIT, sync_remote: bool = True) -> None:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT d.id, b.name AS brand_name, b.slug AS brand_slug, d.model_name, d.slug
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        WHERE d.status = 'enriched'
    """)
    candidates = [dict(r) for r in cur.fetchall()]
    candidates.sort(key=lambda x: (is_low_competition_variant(x["model_name"]), x["id"]))

    batch = candidates[:limit]
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if batch:
        sql_lines = [f"-- Drip-Feed Batch ({len(batch)} pages) - {now_iso} UTC"]
        for item in batch:
            cur.execute(
                "UPDATE devices SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?",
                (now_iso, now_iso, item["id"])
            )
            safe_slug = item["slug"].replace("'", "''")
            sql_lines.append(
                f"UPDATE devices SET status = 'published', published_at = '{now_iso}', "
                f"updated_at = '{now_iso}' WHERE slug = '{safe_slug}';"
            )
            print(f"[PUBLISH] {item['brand_name']} {item['model_name']} -> /{item['slug']}/")

        conn.commit()
        with open(BATCH_SQL_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(sql_lines) + "\n")

        if sync_remote:
            print("\nMenyinkronkan status 'published' ke Cloudflare D1...")
            subprocess.run(
                ["npx", "wrangler", "d1", "execute", "pseo-filter-db", "--remote", f"--file={BATCH_SQL_FILE}"],
                shell=(os.name == "nt"),
                check=True
            )
    else:
        print("Seluruh antrean 'enriched' sudah terbit. Mengekspor ulang katalog 'published'...")

    export_astro_catalog(conn)
    conn.close()


def export_astro_catalog(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, b.name AS brand_name, b.slug AS brand_slug, d.model_name, d.slug,
               d.category, d.official_filter_code, d.hepa_grade, d.filter_shape,
               d.dimensions_json, d.compatibility_warning, d.faqs_json,
               d.direct_answer_capsule, d.key_takeaways_json, d.published_at, d.updated_at
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        WHERE d.status = 'published'
        ORDER BY d.published_at DESC, d.id ASC
    """)
    devices_rows = cur.fetchall()

    catalog: List[Dict[str, Any]] = []
    for d in devices_rows:
        cur.execute("""
            SELECT asin, raw_title, is_oem, rating, review_count, pros_json, cons_json
            FROM filter_products
            WHERE device_id = ? AND is_verified = 1
            ORDER BY is_oem DESC, review_count DESC
        """, (d["id"],))
        products = [
            {
                "asin": p["asin"],
                "title": p["raw_title"],
                "is_oem": bool(p["is_oem"]),
                "rating": p["rating"],
                "review_count": p["review_count"] or 0,
                "pros": json.loads(p["pros_json"] or "[]"),
                "cons": json.loads(p["cons_json"] or "[]"),
            }
            for p in cur.fetchall()
        ]

        catalog.append({
            "id": d["id"],
            "brand_name": d["brand_name"],
            "brand_slug": d["brand_slug"],
            "model_name": d["model_name"],
            "slug": d["slug"],
            "category": d["category"],
            "official_filter_code": d["official_filter_code"],
            "hepa_grade": d["hepa_grade"],
            "filter_shape": d["filter_shape"],
            "dimensions": json.loads(d["dimensions_json"]) if d["dimensions_json"] else None,
            "compatibility_warning": d["compatibility_warning"],
            "faqs": json.loads(d["faqs_json"] or "[]"),
            "direct_answer_capsule": d["direct_answer_capsule"],
            "key_takeaways": json.loads(d["key_takeaways_json"] or "[]"),
            "published_at": d["published_at"],
            "updated_at": d["updated_at"],
            "products": products,
        })

    os.makedirs(os.path.dirname(ASTRO_DATA_FILE), exist_ok=True)
    with open(ASTRO_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Katalog Astro diperbarui: {len(catalog)} halaman aktif -> {ASTRO_DATA_FILE}")


if __name__ == "__main__":
    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else DAILY_LIMIT
    publish_next_batch(limit=batch_size)
