import sqlite3
import os
import hashlib
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "cekilis.db")


def get_database_url() -> Optional[str]:
    """DATABASE_URL ortam değişkenini temizleyip PostgreSQL bağlantı adresini döndürür."""
    raw = os.getenv("DATABASE_URL", "").replace("\r", "").replace("\n", "").strip()
    if not raw:
        return None
    if raw.lower().startswith("psql "):
        raw = raw[5:].strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1].strip()
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    return raw or None


def is_postgres() -> bool:
    return get_database_url() is not None


class PostgresCursorWrapper:
    """SQLite '?' yer tutucularını PostgreSQL '%s' formatına çeviren uyumluluk sarmalayıcısı."""

    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql: str, params: tuple = ()):
        pg_sql = sql.replace("?", "%s")
        self._cur.execute(pg_sql, params)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class PostgresConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor())

    def execute(self, sql: str, params: tuple = ()):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db():
    db_url = get_database_url()
    if db_url:
        import psycopg
        from psycopg.rows import dict_row

        try:
            raw_conn = psycopg.connect(
                db_url,
                row_factory=dict_row,
                prepare_threshold=None,
                connect_timeout=20,
            )
        except psycopg.OperationalError as e:
            if "channel_binding" in str(e) and "channel_binding=require" in db_url:
                fallback_url = db_url.replace("channel_binding=require", "channel_binding=prefer")
                raw_conn = psycopg.connect(
                    fallback_url,
                    row_factory=dict_row,
                    prepare_threshold=None,
                    connect_timeout=20,
                )
            else:
                raise
        return PostgresConnectionWrapper(raw_conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def hash_password(password: str) -> str:
    """Parolayı SHA-256 ve salt ile özetler."""
    salt = "yilbasi_cekilisi_2026_super_secret"
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    pg = is_postgres()
    id_col = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # Kullanıcılar Tablosu
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS users (
        id {id_col},
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        wishlist TEXT DEFAULT '',
        has_opened_envelope INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        avatar_icon TEXT DEFAULT '🎅',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Çekiliş Durumu Tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Eşleşmeler Tablosu (Gizli Kura Sonuçları)
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS matches (
        id {id_col},
        giver_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
        receiver_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Kalıcı Oturumlar Tablosu (Sunucu yeniden başlasa bile oturumlar düşmez)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users (id) ON DELETE CASCADE,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Canlı Akış Bildirimleri Tablosu
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS live_feed (
        id {id_col},
        event_type TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Varsayılan ayarları yükle
    default_settings = [
        ("is_drawn", "0"),
        ("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁"),
        ("budget_hint", "Önerilen Hediye Bütçesi: 250 - 500 TL"),
        ("admin_password", "admin123"),
    ]
    for k, v in default_settings:
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Veritabanı başarıyla oluşturuldu! Mod:", "PostgreSQL" if is_postgres() else "SQLite")
