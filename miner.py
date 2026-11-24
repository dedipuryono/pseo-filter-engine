import json
import logging
import random
import re
import sqlite3
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================================
# KONFIGURASI LOGGING & TARGET MEREK
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("miner.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Format: (Nama Merek, Kategori)
TARGET_BRANDS: List[Tuple[str, str]] = [
    # Air Purifiers
    ("Levoit", "air_purifier"),
    ("Coway", "air_purifier"),
    ("Winix", "air_purifier"),
    ("Honeywell", "air_purifier"),
    ("Blueair", "air_purifier"),
    ("GermGuardian", "air_purifier"),
    ("Medify", "air_purifier"),
    ("Molekule", "air_purifier"),
    ("Rabbit Air", "air_purifier"),
    ("Alen", "air_purifier"),
    # Vacuums
    ("Dyson", "vacuum"),
    ("Shark", "vacuum"),
    ("Bissell", "vacuum"),
    ("Hoover", "vacuum"),
    ("Eureka", "vacuum"),
    ("Miele", "vacuum"),
    ("iRobot Roomba", "vacuum"),
    ("Roborock", "vacuum"),
    ("Tineco", "vacuum"),
    ("Eufy", "vacuum"),
]

# Kata kunci sampah yang wajib dibuang agar database 100% bersih
NOISE_WORDS = [
    "walmart", "target", "home depot", "lowes", "costco", "best buy",
    "near me", "manual", "pdf", "reddit", "youtube", "review", "price",
    "cost", "cheap", "free", "how to", "clean", "wash", "reset", "light",
    "blinking", "size", "amazon", "ebay", "oem", "generic", "best"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
]

DB_FILE = "pseo_staging.db"
D1_EXPORT_FILE = "d1_seed.sql"


# ============================================================================
# UTILITAS HTTP & PEMBERSIHAN TEKS
# ============================================================================
def create_http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def extract_clean_model(suggestion: str, brand: str) -> Optional[str]:
    """
    Mengekstrak nama model bersih dari kalimat Google Suggest.
    Contoh: 'replacement filter for levoit core 300s' -> 'Core 300s'
    """
    s = suggestion.lower().strip()
    b = brand.lower().strip()

    # Buang jika mengandung kata sampah
    for noise in NOISE_WORDS:
        if re.search(rf"\b{re.escape(noise)}\b", s):
            return None

    # Pastikan mengandung nama merek dan kata 'filter'
    if b not in s or "filter" not in s:
        return None

    # Hapus frasa umum di depan/belakang
    patterns_to_strip = [
        r"\breplacement\s+hepa\s+filters?\s+for\b",
        r"\breplacement\s+filters?\s+for\b",
        r"\bhepa\s+filters?\s+for\b",
        r"\bair\s+purifier\s+filters?\s+for\b",
        r"\bvacuum\s+filters?\s+for\b",
        r"\bfilters?\s+for\b",
        r"\breplacement\s+hepa\s+filters?\b",
        r"\breplacement\s+filters?\b",
        r"\bhepa\s+filters?\b",
        r"\bair\s+purifier\b",
        r"\bvacuum\s+cleaner\b",
        r"\bvacuum\b",
        r"\bfilters?\b",
        rf"\b{re.escape(b)}\b"
    ]

    cleaned = s
    for pat in patterns_to_strip:
        cleaned = re.sub(pat, " ", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Validasi: Model yang valid minimal 2 karakter dan biasanya mengandung angka
    if len(cleaned) < 2 or len(cleaned) > 40:
        return None
    if not re.search(r"\d", cleaned):
        return None

    # Format kapitalisasi rapi (contoh: 'core 300s' -> 'Core 300S')
    tokens = cleaned.split()
    formatted = []
    for t in tokens:
        if any(c.isdigit() for c in t):
            formatted.append(t.upper())
        else:
            formatted.append(t.capitalize())
    return " ".join(formatted)


def fetch_google_suggestions(session: requests.Session, query: str) -> List[str]:
    url = f"https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=us&q={quote_plus(query)}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = session.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            return [str(item) for item in data[1]]
    except Exception as e:
        logging.warning(f"Gagal mengambil query '{query}': {e}")
    return []


# ============================================================================
# DATABASE LOKAL & EKSPOR SQL D1
# ============================================================================
def init_local_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    with open("schema.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def export_to_d1_sql(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    lines = [
        "-- Auto-generated Cloudflare D1 Seed File",
        "-- Jalankan: wrangler d1 execute <NAMA_DB> --remote --file=d1_seed.sql\n"
    ]

    # Export Brands
    cur.execute("SELECT name, slug, category FROM brands ORDER BY id")
    for name, slug, category in cur.fetchall():
        safe_name = name.replace("'", "''")
        lines.append(
            f"INSERT OR IGNORE INTO brands (name, slug, category) "
            f"VALUES ('{safe_name}', '{slug}', '{category}');"
        )

    lines.append("")

    # Export Devices
    cur.execute("""
        SELECT b.slug, d.model_name, d.slug, d.category, d.seed_keyword
        FROM devices d
        JOIN brands b ON d.brand_id = b.id
        ORDER BY d.id
    """)
    for b_slug, model_name, d_slug, category, seed_kw in cur.fetchall():
        safe_model = model_name.replace("'", "''")
        safe_kw = seed_kw.replace("'", "''")
        lines.append(
            f"INSERT OR IGNORE INTO devices (brand_id, model_name, slug, category, seed_keyword) "
            f"VALUES ((SELECT id FROM brands WHERE slug = '{b_slug}'), "
            f"'{safe_model}', '{d_slug}', '{category}', '{safe_kw}');"
        )

    with open(D1_EXPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logging.info(f"Ekspor selesai: {D1_EXPORT_FILE} siap di-push ke Cloudflare D1.")


# ============================================================================
# MESIN UTAMA (MAIN PIPELINE)
# ============================================================================
def run_miner():
    conn = init_local_db()
    cur = conn.cursor()
    session = create_http_session()

    # Karakter ekspansi (a-z + 0-9) untuk memanen seluruh seri model
    suffixes = [""] + list("abcdefghijklmnopqrstuvwxyz0123456789")
    total_inserted = 0

    for brand_name, category in TARGET_BRANDS:
        brand_slug = slugify(brand_name)
        cur.execute(
            "INSERT OR IGNORE INTO brands (name, slug, category) VALUES (?, ?, ?)",
            (brand_name, brand_slug, category)
        )
        conn.commit()
        cur.execute("SELECT id FROM brands WHERE slug = ?", (brand_slug,))
        brand_id = cur.fetchone()[0]

        logging.info(f"=== Memanen Merek: {brand_name} ({category}) ===")
        brand_count = 0

        for char in suffixes:
            query = f"replacement filter for {brand_name} {char}".strip()
            suggestions = fetch_google_suggestions(session, query)

            for sug in suggestions:
                model = extract_clean_model(sug, brand_name)
                if not model:
                    continue

                device_slug = slugify(f"{brand_name}-{model}-replacement-filter")
                try:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO devices 
                        (brand_id, model_name, slug, category, seed_keyword) 
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (brand_id, model, device_slug, category, sug.lower().strip())
                    )
                    if cur.rowcount > 0:
                        brand_count += 1
                        total_inserted += 1
                except sqlite3.Error as e:
                    logging.error(f"DB Error untuk {model}: {e}")

            conn.commit()
            # Jeda acak sopan (0.4 - 0.9 detik) agar tidak terkena limit 429 Google
            time.sleep(random.uniform(0.4, 0.9))

        logging.info(f"Selesai {brand_name}: +{brand_count} model unik ditemukan.")

    logging.info(f"TOTAL PANEN SELESAI: {total_inserted} model perangkat unik tersimpan.")
    export_to_d1_sql(conn)
    conn.close()


if __name__ == "__main__":
    run_miner()