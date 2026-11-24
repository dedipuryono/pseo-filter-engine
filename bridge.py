import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "pseo_staging.db"
PORT = 8787


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


class BridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        if self.path == "/next":
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                SELECT d.id, b.name AS brand, d.model_name, d.category
                FROM devices d
                JOIN brands b ON d.brand_id = b.id
                WHERE d.status = 'pending_scrape'
                ORDER BY d.id ASC
                LIMIT 1
            """)
            row = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM devices WHERE status = 'pending_scrape'")
            remaining = cur.fetchone()[0]
            conn.close()

            if not row:
                return self._send_json(200, {"done": True, "remaining": 0})

            return self._send_json(200, {
                "done": False,
                "remaining": remaining,
                "device": {
                    "id": row["id"],
                    "brand": row["brand"],
                    "model_name": row["model_name"],
                    "query": f"{row['brand']} {row['model_name']} replacement filter"
                }
            })

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            device_id = body.get("device_id")
            products = body.get("products", [])

            conn = get_db()
            cur = conn.cursor()
            saved = 0

            for p in products:
                asin = (p.get("asin") or "").strip()
                title = (p.get("title") or "").strip()
                if not asin or not title:
                    continue
                try:
                    cur.execute("""
                        INSERT OR REPLACE INTO filter_products
                        (device_id, asin, raw_title, is_oem, rating, review_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        device_id,
                        asin,
                        title,
                        1 if p.get("is_oem") else 0,
                        p.get("rating"),
                        p.get("review_count", 0)
                    ))
                    saved += 1
                except sqlite3.Error as e:
                    print(f"[DB ERROR] ASIN {asin}: {e}")

            new_status = "scraped" if saved > 0 else "rejected"
            cur.execute(
                "UPDATE devices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, device_id)
            )
            conn.commit()
            conn.close()
            print(f"[SAVED] Device ID {device_id} -> {saved} produk ({new_status})")
            return self._send_json(200, {"ok": True, "saved": saved, "status": new_status})

        self._send_json(404, {"error": "Not Found"})


if __name__ == "__main__":
    print(f"=== Local Bridge Aktif di http://localhost:{PORT} ===")
    print("Siap menerima koneksi dari Chrome Extension...")
    HTTPServer(("127.0.0.1", PORT), BridgeHandler).serve_forever()