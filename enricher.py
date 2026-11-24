import json
import logging
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

import requests

# ============================================================================
# KONFIGURASI (Prioritas GROQ_API_KEY)
# ============================================================================
DB_FILE = "pseo_staging.db"
D1_FULL_EXPORT_FILE = "d1_enriched_sync.sql"

GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Model aktif sesuai Groq Playground Anda (Utama -> Fallback)
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("enricher.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)


# ============================================================================
# DETEKSI PROVIDER & PEMBERSIH JSON
# ============================================================================
def resolve_provider() -> Dict[str, Any]:
    if GROQ_KEY.startswith("gsk_"):
        logging.info(f"Provider aktif: Groq API (Utama: {GROQ_MODELS[0]})")
        return {
            "provider": "groq",
            "api_key": GROQ_KEY,
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "models": GROQ_MODELS,
            "sleep_sec": 3.5
        }

    if GEMINI_KEY.startswith("gsk_"):
        logging.info(f"Provider aktif: Groq API (Utama: {GROQ_MODELS[0]})")
        return {
            "provider": "groq",
            "api_key": GEMINI_KEY,
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "models": GROQ_MODELS,
            "sleep_sec": 3.5
        }

    if GEMINI_KEY.startswith("AIza"):
        logging.info("Provider aktif: Google AI Studio (gemini-2.5-flash)")
        return {
            "provider": "gemini",
            "api_key": GEMINI_KEY,
            "url": f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
            "models": ["gemini-2.5-flash"],
            "sleep_sec": 4.2
        }

    raise RuntimeError(
        "GROQ_API_KEY (gsk_...) belum terbaca di sesi PowerShell ini. "
        "Jalankan: $env:GROQ_API_KEY='gsk_...'"
    )


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Membersihkan tag <think>...</think> (dari model reasoning seperti gpt-oss / qwen)
    atau blok markdown ```json sebelum parsing JSON.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # Ambil objek JSON terluar jika masih ada teks pengantar
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    return json.loads(cleaned)


# ============================================================================
# MIGRASI AMAN DATABASE LOKAL
# ============================================================================
def ensure_geo_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(devices)")
    cols = {row[1] for row in cur.fetchall()}

    if "direct_answer_capsule" not in cols:
        cur.execute("ALTER TABLE devices ADD COLUMN direct_answer_capsule TEXT")
        logging.info("Migrasi DB: Kolom 'direct_answer_capsule' ditambahkan.")
    if "key_takeaways_json" not in cols:
        cur.execute("ALTER TABLE devices ADD COLUMN key_takeaways_json TEXT")
        logging.info("Migrasi DB: Kolom 'key_takeaways_json' ditambahkan.")
    conn.commit()


# ============================================================================
# PROMPT & PEMANGGILAN LLM (ZERO-HALLUCINATION + GEO)
# ============================================================================
def build_prompt(brand: str, model_name: str, category: str, products: List[Dict[str, Any]]) -> str:
    products_text = "\n".join([
        f"- ASIN: {p['asin']} | Rating: {p['rating']} ({p['review_count']} reviews) | Title: {p['raw_title']}"
        for p in products
    ])

    return f"""
You are a technical specification analyst and Generative Engine Optimization (GEO) editor for a US home appliance parts directory.

TARGET DEVICE:
- Brand: {brand}
- Model: {model_name}
- Category: {category}

SCRAPED AMAZON PRODUCT LISTINGS (GROUND TRUTH):
{products_text}

STRICT RULES (ZERO-HALLUCINATION POLICY):
1. Extract technical specifications ONLY from the scraped Amazon product titles above.
2. NEVER guess or invent filter part numbers, HEPA grades, shapes, or dimensions.
   - If `official_filter_code` is not explicitly stated in the listings, return "".
   - If `hepa_grade` is not explicitly stated, return "".
   - If `filter_shape` is not explicitly clear, return "unknown" (allowed: "cylindrical", "rectangular", "conical", "unknown").
   - If exact inch measurements (length, width, height) do not appear in the titles, set length_in=0, width_in=0, height_in=0.
3. For `products_analysis`, evaluate each ASIN:
   - `is_relevant`: true ONLY if the product fits {brand} {model_name}; false if unrelated.
   - `is_oem`: true ONLY if manufactured by {brand} (official/genuine); false for third-party replacements.
   - `pros` & `cons`: 2 short, factual bullet points per product grounded strictly in title attributes.

GEO & GOOGLE AI OVERVIEW OPTIMIZATION (US ENGLISH):
4. `direct_answer_capsule`: Write a 45-55 word neutral, high-density encyclopedic paragraph answering what replacement filter fits the {brand} {model_name}. Mention the official filter part code (if found), filtration grade (if found), and state that both OEM and multi-pack third-party replacements are available. No fluff or marketing hype.
5. `key_takeaways`: Provide exactly 3 crisp, factual bullet points summarizing compatibility, filtration features found in the listings, and buying options (OEM vs value packs).
6. `compatibility_warning`: Write 1-2 sentences warning buyers to verify their exact {brand} {model_name} model sticker/part number before ordering, referencing variant details seen in the listings.
7. `faqs`: Generate 3 specific, high-intent Q&As about {brand} {model_name} replacement filters grounded strictly in the listing facts (1. Filter compatibility/part code, 2. OEM vs third-party differences, 3. Filtration features/maintenance).

Return ONLY a valid JSON object matching this exact schema (no markdown, no explanation):
{{
  "official_filter_code": "",
  "hepa_grade": "",
  "filter_shape": "unknown",
  "dimensions": {{"length_in": 0, "width_in": 0, "height_in": 0}},
  "direct_answer_capsule": "",
  "key_takeaways": ["...", "...", "..."],
  "compatibility_warning": "",
  "faqs": [{{"q": "...", "a": "..."}}],
  "products_analysis": [{{"asin": "...", "is_relevant": true, "is_oem": false, "pros": ["...", "..."], "cons": ["...", "..."]}}]
}}
""".strip()


def call_llm_enrichment(
    cfg: Dict[str, Any],
    brand: str,
    model_name: str,
    category: str,
    products: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    prompt = build_prompt(brand, model_name, category, products)
    models_to_try = cfg["models"]

    for attempt in range(1, 4):
        model_name_api = models_to_try[(attempt - 1) % len(models_to_try)]
        try:
            if cfg["provider"] == "groq":
                headers = {
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model_name_api,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a strict JSON extraction API. Output only valid JSON matching the requested schema."
                        },
                        {"role": "user", "content": prompt}
                    ]
                }
                resp = requests.post(cfg["url"], headers=headers, json=payload, timeout=50)
            else:
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json"
                    }
                }
                resp = requests.post(cfg["url"], json=payload, timeout=50)

            if resp.status_code == 429:
                wait_sec = 12 * attempt
                logging.warning(f"Rate limit (429) pada {model_name_api}. Menunggu {wait_sec} detik...")
                time.sleep(wait_sec)
                continue

            if resp.status_code != 200:
                logging.warning(
                    f"Percobaan {attempt}/3 gagal ({brand} {model_name} via {model_name_api}) -> "
                    f"HTTP {resp.status_code}: {resp.text[:250]}"
                )
                time.sleep(3 * attempt)
                continue

            data = resp.json()
            if cfg["provider"] == "groq":
                raw_text = data["choices"][0]["message"]["content"]
            else:
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

            return parse_llm_json(raw_text)
        except Exception as e:
            logging.warning(f"Percobaan {attempt}/3 exception ({brand} {model_name} via {model_name_api}): {e}")
            time.sleep(3 * attempt)

    return None


# ============================================================================
# EKSPOR LENGKAP KE CLOUDFLARE D1 SQL
# ============================================================================
def sql_str(val: Optional[str]) -> str:
    if val is None:
        return "NULL"
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def export_enriched_d1(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    lines = [
        "-- Auto-generated Cloudflare D1 Full Sync (Brands + Enriched Devices + Verified Products)",
        "-- Jalankan: npx wrangler d1 execute pseo-filter-db --remote --file=d1_enriched_sync.sql\n"
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

    with open(D1_FULL_EXPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logging.info(f"File sinkronisasi D1 berhasil dibuat: {D1_FULL_EXPORT_FILE}")


# ============================================================================
# PIPELINE UTAMA
# ============================================================================
def run_enrichment():
    try:
        cfg = resolve_provider()
    except Exception as e:
        logging.error(f"Inisialisasi API gagal: {e}")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    ensure_geo_columns(conn)
    cur = conn.cursor()

    cur.execute("""
        SELECT d.id, b.name AS brand, d.model_name, d.category
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        WHERE d.status = 'scraped'
        ORDER BY d.id ASC
    """)
    devices = cur.fetchall()
    total = len(devices)
    logging.info(f"Ditemukan {total} perangkat berstatus 'scraped' untuk diproses AI + GEO.")

    allowed_shapes = {"cylindrical", "rectangular", "conical"}
    success_count = 0

    for idx, dev in enumerate(devices, start=1):
        dev_id = dev["id"]
        brand = dev["brand"]
        model_name = dev["model_name"]

        cur.execute(
            "SELECT asin, raw_title, rating, review_count FROM filter_products WHERE device_id = ?",
            (dev_id,)
        )
        prods = [dict(r) for r in cur.fetchall()]
        if not prods:
            cur.execute("UPDATE devices SET status = 'rejected' WHERE id = ?", (dev_id,))
            conn.commit()
            continue

        logging.info(f"[{idx}/{total}] Enriching & GEO: {brand} {model_name}...")
        result = call_llm_enrichment(cfg, brand, model_name, dev["category"], prods)

        if not result:
            logging.error(f"Gagal memproses {brand} {model_name}, dilewati sementara.")
            continue

        verified_products = 0
        for p_item in result.get("products_analysis", []):
            asin = p_item.get("asin")
            is_rel = 1 if p_item.get("is_relevant") else 0
            is_oem = 1 if p_item.get("is_oem") else 0
            pros_json = json.dumps(p_item.get("pros", []), ensure_ascii=False)
            cons_json = json.dumps(p_item.get("cons", []), ensure_ascii=False)

            cur.execute("""
                UPDATE filter_products
                SET is_oem = ?, pros_json = ?, cons_json = ?, is_verified = ?, updated_at = CURRENT_TIMESTAMP
                WHERE device_id = ? AND asin = ?
            """, (is_oem, pros_json, cons_json, is_rel, dev_id, asin))

            if is_rel == 1:
                verified_products += 1

        if verified_products == 0:
            cur.execute("UPDATE devices SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dev_id,))
            conn.commit()
            logging.warning(f" -> Rejected (0 produk relevan untuk {brand} {model_name})")
            continue

        clean_code = (result.get("official_filter_code") or "").strip() or None
        clean_hepa = (result.get("hepa_grade") or "").strip() or None
        raw_shape = (result.get("filter_shape") or "").strip().lower()
        clean_shape = raw_shape if raw_shape in allowed_shapes else None

        dims_val = result.get("dimensions") or {}
        if (
            dims_val.get("length_in", 0) > 0
            and dims_val.get("width_in", 0) > 0
            and dims_val.get("height_in", 0) > 0
        ):
            dims_json = json.dumps(dims_val)
        else:
            dims_json = None

        cur.execute("""
            UPDATE devices
            SET official_filter_code = ?,
                hepa_grade = ?,
                filter_shape = ?,
                dimensions_json = ?,
                compatibility_warning = ?,
                faqs_json = ?,
                direct_answer_capsule = ?,
                key_takeaways_json = ?,
                status = 'enriched',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            clean_code,
            clean_hepa,
            clean_shape,
            dims_json,
            result.get("compatibility_warning"),
            json.dumps(result.get("faqs", []), ensure_ascii=False),
            result.get("direct_answer_capsule"),
            json.dumps(result.get("key_takeaways", []), ensure_ascii=False),
            dev_id
        ))
        conn.commit()
        success_count += 1
        logging.info(f" -> Sukses! Code: {clean_code} | Verified ASIN: {verified_products}")

        time.sleep(cfg["sleep_sec"])

    logging.info(f"ENRICHMENT SELESAI: {success_count}/{total} perangkat siap terbit.")
    export_enriched_d1(conn)
    conn.close()


if __name__ == "__main__":
    run_enrichment()