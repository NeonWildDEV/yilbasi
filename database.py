import sqlite3
import os
import hashlib
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "cekilis.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def hash_password(password: str) -> str:
    """Parolayı SHA-256 ve salt ile özetler."""
    salt = "yilbasi_cekilisi_2026_super_secret"
    return hashlib.sha256((password + salt).encode('utf-8')).hexdigest()

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Kullanıcılar Tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        giver_id INTEGER NOT NULL UNIQUE,
        receiver_id INTEGER NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (giver_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (receiver_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    
    # Canlı Akış Bildirimleri Tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_feed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Varsayılan ayarları yükle
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('is_drawn', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('draw_title', '2026 Yılbaşı Gizli Hediye Çekilişi 🎁')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('budget_hint', 'Önerilen Hediye Bütçesi: 250 - 500 TL')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', 'admin123')")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Veritabanı başarıyla oluşturuldu!")
