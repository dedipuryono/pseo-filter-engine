-- ============================================================================
-- PSEO FILTER DIRECTORY - ENTERPRISE SCHEMA V2 (GEO + AI OVERVIEW READY)
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK(category IN ('air_purifier', 'vacuum', 'both')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK(category IN ('air_purifier', 'vacuum')),
    seed_keyword TEXT NOT NULL,
    
    -- Data Teknis Terstruktur (Zero-Hallucination Grounded)
    official_filter_code TEXT,
    hepa_grade TEXT,
    filter_shape TEXT CHECK(filter_shape IN ('cylindrical', 'rectangular', 'conical', NULL)),
    dimensions_json TEXT,
    compatibility_warning TEXT,
    reset_instructions_json TEXT,
    faqs_json TEXT,
    
    -- Aset Khusus GEO (Optimasi Kutipan Google AI Overview)
    direct_answer_capsule TEXT,
    key_takeaways_json TEXT,
    
    -- State Machine untuk Pipeline Otomasi & Drip-Feed
    status TEXT NOT NULL DEFAULT 'pending_scrape' 
        CHECK(status IN ('pending_scrape', 'scraped', 'enriched', 'published', 'rejected')),
    published_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (brand_id) REFERENCES brands(id) ON DELETE CASCADE,
    UNIQUE(brand_id, model_name)
);

CREATE TABLE IF NOT EXISTS filter_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    asin TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    raw_bullets TEXT,
    is_oem INTEGER NOT NULL DEFAULT 0 CHECK(is_oem IN (0, 1)),
    rating REAL,
    review_count INTEGER DEFAULT 0,
    
    pros_json TEXT,
    cons_json TEXT,
    is_verified INTEGER NOT NULL DEFAULT 0 CHECK(is_verified IN (0, 1)),
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    UNIQUE(device_id, asin)
);

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_slug ON devices(slug);
CREATE INDEX IF NOT EXISTS idx_devices_brand_status ON devices(brand_id, status);
CREATE INDEX IF NOT EXISTS idx_devices_published ON devices(status, published_at);
CREATE INDEX IF NOT EXISTS idx_products_device ON filter_products(device_id, is_verified);