import sqlite3
import os
import time
import hashlib
import threading
from typing import Optional, Dict

DB_PATH = os.path.join(os.path.dirname(__file__), "cekilis.db")

_pg_pool = None
_pool_lock = threading.Lock()
_conn_last_ok: Dict[int, float] = {}
CONN_CHECK_INTERVAL = 45.0


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


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        db_url = get_database_url()
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        def _smart_check(conn):
            now = time.monotonic()
            cid = id(conn)
            if now - _conn_last_ok.get(cid, 0.0) < CONN_CHECK_INTERVAL:
                return
            ConnectionPool.check_connection(conn)
            _conn_last_ok[cid] = time.monotonic()

        _pg_pool = ConnectionPool(
            conninfo=db_url,
            min_size=0,
            max_size=10,
            max_idle=240,
            timeout=20,
            check=_smart_check,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": None,
                "connect_timeout": 15,
            },
            open=True,
        )
        return _pg_pool


class PostgresCursorWrapper:
    """SQLite '?' yer tutucularını PostgreSQL '%s' formatına çeviren uyumluluk sarmalayıcısı."""

    def __init__(self, raw_conn, raw_cursor):
        self._conn = raw_conn
        self._cur = raw_cursor

    def execute(self, sql: str, params: tuple = ()):
        if params:
            pg_sql = sql.replace("%", "%%").replace("?", "%s")
            self._cur.execute(pg_sql, params)
        else:
            self._cur.execute(sql)
        _conn_last_ok[id(self._conn)] = time.monotonic()
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class PostgresConnectionWrapper:
    def __init__(self, pool, raw_conn):
        self._pool = pool
        self._conn = raw_conn
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            _conn_last_ok.pop(id(self._conn), None)
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False

    def cursor(self):
        return PostgresCursorWrapper(self._conn, self._conn.cursor())

    def execute(self, sql: str, params: tuple = ()):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        self._conn.commit()
        _conn_last_ok[id(self._conn)] = time.monotonic()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._pool.putconn(self._conn)


class SqliteConnectionWrapper:
    def __init__(self, raw_conn: sqlite3.Connection):
        self._conn = raw_conn
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False

    def cursor(self):
        return self._conn.cursor()

    def execute(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._conn.close()


def get_db():
    if is_postgres():
        pool = _get_pg_pool()
        raw_conn = pool.getconn()
        return PostgresConnectionWrapper(pool, raw_conn)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return SqliteConnectionWrapper(conn)


def hash_password(password: str) -> str:
    """Parolayı SHA-256 ve salt ile özetler (mevcut şifre uyumluluğu için salt sabit tutulur)."""
    salt = "yilbasi_cekilisi_2026_super_secret"
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        pg = is_postgres()
        id_col = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

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

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS matches (
            id {id_col},
            giver_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
            receiver_id INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users (id) ON DELETE CASCADE,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS live_feed (
            id {id_col},
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        default_settings = [
            ("is_drawn", "0"),
            ("draw_title", "2027 Yılbaşı Gizli Hediye Çekilişi 🎁"),
            ("budget_hint", "Önerilen Hediye Bütçesi: 250 - 500 TL"),
            ("admin_password", "admin123"),
        ]
        for k, v in default_settings:
            cursor.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                (k, v),
            )

        # Eski varsayılan 2026 başlığını 2027'ye güncelle
        cursor.execute(
            "UPDATE settings SET value = ? WHERE key = ? AND value LIKE ?",
            ("2027 Yılbaşı Gizli Hediye Çekilişi 🎁", "draw_title", "%2026%"),
        )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print("Veritabanı başarıyla oluşturuldu! Mod:", "PostgreSQL" if is_postgres() else "SQLite")
