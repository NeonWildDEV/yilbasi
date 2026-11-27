import os
import time
import secrets
import asyncio
from typing import List, Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, HTTPException, Depends, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from database import get_db, init_db, hash_password, is_postgres
from secret_santa import generate_secret_santa_matches

init_db()

app = FastAPI(title="Yılbaşı Çekiliş Sistemi")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Veritabanı destekli (kalıcı) + bellek önbellekli (0ms okuma) oturum ve istatistik katmanı
SESSION_CACHE: Dict[str, int] = {}
ADMIN_SESSION_CACHE: set = set()
_stats_cache: Optional[dict] = None
_stats_cache_ts: float = 0.0
STATS_CACHE_TTL = 10.0


# WebSocket Bağlantı Yöneticisi (Canlı Yayın Ekranı ve Katılımcılar İçin)
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()


# --- Pydantic Modelleri ---
class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str
    wishlist: Optional[str] = ""
    avatar_icon: Optional[str] = "🎅"


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginRequest(BaseModel):
    password: str


class WishlistUpdateRequest(BaseModel):
    wishlist: str


# --- Yardımcı Fonksiyonlar ---
def fetch_settings_map(conn) -> Dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting_on_conn(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def create_session_on_conn(conn, user_id: Optional[int] = None, is_admin: bool = False) -> str:
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO sessions (token, user_id, is_admin) VALUES (?, ?, ?)",
        (token, user_id, 1 if is_admin else 0),
    )
    if is_admin:
        ADMIN_SESSION_CACHE.add(token)
    elif user_id is not None:
        SESSION_CACHE[token] = user_id
    return token


def resolve_user_id(session_token: Optional[str], conn=None) -> Optional[int]:
    if not session_token:
        return None
    cached = SESSION_CACHE.get(session_token)
    if cached is not None:
        return cached

    if conn is not None:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND is_admin = 0",
            (session_token,),
        ).fetchone()
    else:
        with get_db() as c:
            row = c.execute(
                "SELECT user_id FROM sessions WHERE token = ? AND is_admin = 0",
                (session_token,),
            ).fetchone()

    if row and row["user_id"]:
        SESSION_CACHE[session_token] = row["user_id"]
        return row["user_id"]
    return None


def resolve_admin(admin_token: Optional[str], conn=None) -> bool:
    if not admin_token:
        return False
    if admin_token in ADMIN_SESSION_CACHE:
        return True

    if conn is not None:
        row = conn.execute(
            "SELECT token FROM sessions WHERE token = ? AND is_admin = 1",
            (admin_token,),
        ).fetchone()
    else:
        with get_db() as c:
            row = c.execute(
                "SELECT token FROM sessions WHERE token = ? AND is_admin = 1",
                (admin_token,),
            ).fetchone()

    if row is not None:
        ADMIN_SESSION_CACHE.add(admin_token)
        return True
    return False


def is_admin_authenticated(admin_token: Optional[str] = Cookie(None)) -> bool:
    return resolve_admin(admin_token)


def read_template(filename: str) -> str:
    p1 = os.path.join(TEMPLATES_DIR, filename)
    if os.path.isfile(p1):
        with open(p1, "r", encoding="utf-8") as f:
            return f.read()
    p2 = os.path.join(BASE_DIR, filename)
    if os.path.isfile(p2):
        with open(p2, "r", encoding="utf-8") as f:
            return f.read()
    return f"<h1>Hata: {filename} dosyası bulunamadı</h1>"


# --- Sayfa Yönlendirmeleri (HTML Views) ---
@app.get("/", response_class=HTMLResponse)
async def index_page():
    return read_template("index.html")


@app.get("/live", response_class=HTMLResponse)
async def live_page():
    return read_template("live.html")


@app.get("/neon", response_class=HTMLResponse)
async def admin_page():
    return read_template("admin.html")


@app.get("/admin")
async def fake_admin_404():
    raise HTTPException(status_code=404, detail="Sayfa bulunamadı")


# --- İstatistik & Canlı Bilgi Fonksiyonu ---
def _compute_public_stats_on_conn(conn) -> dict:
    global _stats_cache, _stats_cache_ts
    settings = fetch_settings_map(conn)
    is_drawn = settings.get("is_drawn", "0") == "1"
    draw_title = settings.get("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
    budget_hint = settings.get("budget_hint", "250 - 500 TL")

    users = conn.execute("""
        SELECT id, display_name, avatar_icon, has_opened_envelope, created_at
        FROM users
        WHERE is_admin = 0
        ORDER BY id ASC
    """).fetchall()

    participants = [
        {
            "id": u["id"],
            "display_name": u["display_name"],
            "avatar_icon": u["avatar_icon"],
            "has_opened_envelope": bool(u["has_opened_envelope"]),
        }
        for u in users
    ]

    opened_count = sum(1 for p in participants if p["has_opened_envelope"])
    result = {
        "is_drawn": is_drawn,
        "draw_title": draw_title,
        "budget_hint": budget_hint,
        "total_participants": len(participants),
        "opened_count": opened_count,
        "participants": participants,
    }
    _stats_cache = result
    _stats_cache_ts = time.monotonic()
    return result


def refresh_public_stats() -> dict:
    with get_db() as conn:
        return _compute_public_stats_on_conn(conn)


def get_public_stats_cached() -> dict:
    if _stats_cache is not None and (time.monotonic() - _stats_cache_ts) < STATS_CACHE_TTL:
        return _stats_cache
    return refresh_public_stats()


# --- WebSocket Canlı Akış ---
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        stats = await asyncio.to_thread(get_public_stats_cached)
        await websocket.send_json({"type": "init_state", "data": stats})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@app.get("/api/public-stats")
def public_stats():
    return get_public_stats_cached()


# --- Kullanıcı Kayıt & Giriş API ---
@app.post("/api/register")
async def register(req: RegisterRequest, response: Response):
    username = req.username.strip().lower()
    display_name = req.display_name.strip() or req.username.strip()
    avatar = req.avatar_icon or "🎅"
    wishlist_val = (req.wishlist or "").strip()

    if len(username) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Kullanıcı adı en az 2, şifre en az 4 karakter olmalıdır.")

    def _do_register():
        with get_db() as conn:
            settings = fetch_settings_map(conn)
            if settings.get("is_drawn", "0") == "1":
                raise HTTPException(status_code=400, detail="Kura çekimi tamamlandığı için yeni kayıt alınamaz!")

            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten alınmış!")

            pwd_hash = hash_password(req.password)
            conn.execute("""
                INSERT INTO users (username, password_hash, display_name, wishlist, avatar_icon)
                VALUES (?, ?, ?, ?, ?)
            """, (username, pwd_hash, display_name, wishlist_val, avatar))
            created_row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            user_id = created_row["id"]
            token = create_session_on_conn(conn, user_id=user_id, is_admin=False)
            conn.commit()
            stats = _compute_public_stats_on_conn(conn)
            return token, stats

    token, stats = await asyncio.to_thread(_do_register)
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=86400 * 30)

    await manager.broadcast({
        "type": "user_registered",
        "user": {
            "display_name": display_name,
            "avatar_icon": avatar,
        },
        "stats": stats,
    })

    return {"success": True, "message": "Kayıt başarılı!", "display_name": display_name}


@app.post("/api/login")
def login(req: LoginRequest, response: Response):
    username = req.username.strip().lower()
    pwd_hash = hash_password(req.password)

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, display_name FROM users WHERE username = ? AND password_hash = ?",
            (username, pwd_hash),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı!")
        token = create_session_on_conn(conn, user_id=user["id"], is_admin=False)
        conn.commit()
        display_name = user["display_name"]

    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax", max_age=86400 * 30)
    return {"success": True, "display_name": display_name}


@app.post("/api/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token:
        SESSION_CACHE.pop(session_token, None)
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
            conn.commit()
    response.delete_cookie("session_token")
    return {"success": True}


@app.get("/api/me")
def get_my_info(session_token: Optional[str] = Cookie(None)):
    if not session_token:
        return {"authenticated": False}

    with get_db() as conn:
        user_id = resolve_user_id(session_token, conn=conn)
        if not user_id:
            return {"authenticated": False}

        user = conn.execute(
            "SELECT id, username, display_name, wishlist, avatar_icon, has_opened_envelope FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            SESSION_CACHE.pop(session_token, None)
            return {"authenticated": False}

        settings = fetch_settings_map(conn)
        is_drawn = settings.get("is_drawn", "0") == "1"
        draw_title = settings.get("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
        budget_hint = settings.get("budget_hint", "250 - 500 TL")

        receiver_info = None
        # Güvenlik önlemi: Zarf açılana kadar hedef kişi ağ üzerinden (F12 Network) asla gönderilmez!
        if is_drawn and user["has_opened_envelope"]:
            match = conn.execute("""
                SELECT u.display_name, u.wishlist, u.avatar_icon
                FROM matches m
                JOIN users u ON m.receiver_id = u.id
                WHERE m.giver_id = ?
            """, (user_id,)).fetchone()
            if match:
                receiver_info = {
                    "display_name": match["display_name"],
                    "wishlist": match["wishlist"],
                    "avatar_icon": match["avatar_icon"],
                }

        return {
            "authenticated": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"],
                "wishlist": user["wishlist"],
                "avatar_icon": user["avatar_icon"],
                "has_opened_envelope": bool(user["has_opened_envelope"]),
            },
            "is_drawn": is_drawn,
            "draw_title": draw_title,
            "budget_hint": budget_hint,
            "receiver": receiver_info,
        }


@app.post("/api/open-envelope")
async def open_envelope(session_token: Optional[str] = Cookie(None)):
    def _do_open():
        with get_db() as conn:
            user_id = resolve_user_id(session_token, conn=conn)
            if not user_id:
                raise HTTPException(status_code=401, detail="Lütfen giriş yapın.")

            settings = fetch_settings_map(conn)
            if settings.get("is_drawn", "0") != "1":
                raise HTTPException(status_code=400, detail="Çekiliş henüz yapılmadı!")

            user = conn.execute(
                "SELECT display_name, has_opened_envelope FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not user:
                raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı.")

            already_opened = bool(user["has_opened_envelope"])
            if not already_opened:
                conn.execute("UPDATE users SET has_opened_envelope = 1 WHERE id = ?", (user_id,))
                conn.commit()

            match = conn.execute("""
                SELECT u.display_name, u.wishlist, u.avatar_icon
                FROM matches m
                JOIN users u ON m.receiver_id = u.id
                WHERE m.giver_id = ?
            """, (user_id,)).fetchone()

            receiver_info = None
            if match:
                receiver_info = {
                    "display_name": match["display_name"],
                    "wishlist": match["wishlist"],
                    "avatar_icon": match["avatar_icon"],
                }

            stats = _compute_public_stats_on_conn(conn)
            return user["display_name"], already_opened, receiver_info, stats

    display_name, already_opened, receiver_info, stats = await asyncio.to_thread(_do_open)

    if not already_opened:
        await manager.broadcast({
            "type": "envelope_opened",
            "display_name": display_name or "Bir katılımcı",
            "stats": stats,
        })

    return {"success": True, "receiver": receiver_info}


@app.post("/api/update-wishlist")
def update_wishlist(req: WishlistUpdateRequest, session_token: Optional[str] = Cookie(None)):
    with get_db() as conn:
        user_id = resolve_user_id(session_token, conn=conn)
        if not user_id:
            raise HTTPException(status_code=401, detail="Lütfen giriş yapın.")
        conn.execute("UPDATE users SET wishlist = ? WHERE id = ?", (req.wishlist.strip(), user_id))
        conn.commit()
    return {"success": True}


# --- Admin API ---
@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, response: Response):
    env_pwd = os.getenv("ADMIN_PASSWORD")
    expected_pwd = env_pwd if env_pwd else "acunbey0641"

    if req.password != expected_pwd:
        raise HTTPException(status_code=401, detail="Hatalı şifre!")

    with get_db() as conn:
        token = create_session_on_conn(conn, user_id=None, is_admin=True)
        conn.commit()

    response.set_cookie(key="admin_token", value=token, httponly=True, samesite="lax", max_age=86400 * 7)
    return {"success": True}


@app.get("/api/admin/overview")
def admin_overview(admin_token: Optional[str] = Cookie(None)):
    with get_db() as conn:
        if not resolve_admin(admin_token, conn=conn):
            raise HTTPException(status_code=403, detail="Yetkiniz yok.")

        users = conn.execute("""
            SELECT id, username, display_name, wishlist, avatar_icon, has_opened_envelope, created_at
            FROM users
            WHERE is_admin = 0
            ORDER BY id ASC
        """).fetchall()

        settings = fetch_settings_map(conn)
        is_drawn = settings.get("is_drawn", "0") == "1"
        draw_title = settings.get("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
        budget_hint = settings.get("budget_hint", "250 - 500 TL")

        matches = []
        if is_drawn:
            raw_matches = conn.execute("""
                SELECT
                    g.id as giver_id, g.display_name as giver_name,
                    r.id as receiver_id, r.display_name as receiver_name
                FROM matches m
                JOIN users g ON m.giver_id = g.id
                JOIN users r ON m.receiver_id = r.id
            """).fetchall()
            for m in raw_matches:
                matches.append({
                    "giver_name": m["giver_name"],
                    "receiver_name": m["receiver_name"],
                })

        return {
            "users": [dict(u) for u in users],
            "is_drawn": is_drawn,
            "draw_title": draw_title,
            "budget_hint": budget_hint,
            "matches": matches,
            "db_mode": "postgresql" if is_postgres() else "sqlite",
        }


@app.post("/api/admin/draw")
async def trigger_draw(admin_token: Optional[str] = Cookie(None)):
    def _do_draw():
        with get_db() as conn:
            if not resolve_admin(admin_token, conn=conn):
                raise HTTPException(status_code=403, detail="Yetkiniz yok.")

            users = conn.execute("SELECT id FROM users WHERE is_admin = 0").fetchall()
            user_ids = [u["id"] for u in users]
            if len(user_ids) < 2:
                raise HTTPException(status_code=400, detail="Çekiliş için en az 2 kişi gereklidir!")

            matches = generate_secret_santa_matches(user_ids)
            conn.execute("DELETE FROM matches")
            for giver, receiver in matches:
                conn.execute("INSERT INTO matches (giver_id, receiver_id) VALUES (?, ?)", (giver, receiver))

            conn.execute("UPDATE users SET has_opened_envelope = 0")
            set_setting_on_conn(conn, "is_drawn", "1")
            conn.commit()

            stats = _compute_public_stats_on_conn(conn)
            return len(matches), stats

    match_count, stats = await asyncio.to_thread(_do_draw)

    await manager.broadcast({
        "type": "draw_started",
        "message": "🎁 Kura çekimi başladı! Sihirli hediye torbası karıştırılıyor...",
        "countdown": 4,
    })

    async def delayed_complete():
        await asyncio.sleep(4)
        latest_stats = await asyncio.to_thread(get_public_stats_cached)
        await manager.broadcast({
            "type": "draw_completed",
            "message": "🎉 Çekiliş Tamamlandı! Herkes hesabına giriş yapıp hediye alacağı kişiyi görebilir!",
            "stats": latest_stats,
        })

    asyncio.create_task(delayed_complete())
    return {"success": True, "count": match_count}


@app.post("/api/admin/reset")
async def reset_draw(admin_token: Optional[str] = Cookie(None)):
    def _do_reset():
        with get_db() as conn:
            if not resolve_admin(admin_token, conn=conn):
                raise HTTPException(status_code=403, detail="Yetkiniz yok.")
            conn.execute("DELETE FROM matches")
            conn.execute("UPDATE users SET has_opened_envelope = 0")
            set_setting_on_conn(conn, "is_drawn", "0")
            conn.commit()
            return _compute_public_stats_on_conn(conn)

    stats = await asyncio.to_thread(_do_reset)
    await manager.broadcast({
        "type": "draw_reset",
        "message": "Çekiliş sıfırlandı.",
        "stats": stats,
    })
    return {"success": True}


@app.delete("/api/admin/user/{user_id}")
async def delete_user(user_id: int, admin_token: Optional[str] = Cookie(None)):
    def _do_delete():
        with get_db() as conn:
            if not resolve_admin(admin_token, conn=conn):
                raise HTTPException(status_code=403, detail="Yetkiniz yok.")
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            for tok in [t for t, uid in SESSION_CACHE.items() if uid == user_id]:
                SESSION_CACHE.pop(tok, None)
            return _compute_public_stats_on_conn(conn)

    stats = await asyncio.to_thread(_do_delete)
    await manager.broadcast({
        "type": "user_removed",
        "stats": stats,
    })
    return {"success": True}


@app.delete("/api/admin/clear-all")
async def clear_all_users(admin_token: Optional[str] = Cookie(None)):
    def _do_clear():
        with get_db() as conn:
            if not resolve_admin(admin_token, conn=conn):
                raise HTTPException(status_code=403, detail="Yetkiniz yok.")
            conn.execute("DELETE FROM users WHERE is_admin = 0")
            conn.execute("DELETE FROM matches")
            set_setting_on_conn(conn, "is_drawn", "0")
            conn.commit()
            SESSION_CACHE.clear()
            return _compute_public_stats_on_conn(conn)

    stats = await asyncio.to_thread(_do_clear)
    await manager.broadcast({
        "type": "draw_reset",
        "message": "Tüm katılımcılar silindi.",
        "stats": stats,
    })
    return {"success": True}


@app.post("/api/admin/add-test-users")
async def add_test_users(admin_token: Optional[str] = Cookie(None)):
    sample_users = [
        ("ahmet", "Ahmet Yılmaz", "🎅", "Bilim kurgu kitabı veya termos kupa"),
        ("mehmet", "Mehmet Kaya", "🦌", "Kulaklık veya kahve çekirdeği"),
        ("ayse", "Ayşe Demir", "🎄", "Kokulu mum veya atkı"),
        ("fatma", "Fatma Çelik", "⭐", "Ajanda veya masa bitkisi"),
        ("ali", "Ali Öztürk", "🎁", "Masa lambası veya fincan seti"),
        ("zeynep", "Zeynep Şahin", "⛄", "Çikolata kutusu veya yün çorap"),
        ("can", "Can Yıldız", "❄️", "Tasarım anahtarlık veya mousepad"),
        ("elif", "Elif Aydın", "✨", "Cilt bakım ürünü veya kupa bardak"),
        ("burak", "Burak Koç", "🔔", "Poster veya bez çanta"),
        ("gamze", "Gamze Arslan", "🧦", "Kedi temalı hediyeler"),
        ("emre", "Emre Polat", "🍪", "Filtre kahve veya çerez kutusu"),
        ("merve", "Merve Doğan", "🎀", "Roman veya puzzle"),
        ("oguz", "Oğuz Kılıç", "🌟", "Powerbank veya USB bellek"),
        ("seda", "Seda Güneş", "🍷", "Bardak altlığı veya defter"),
        ("tolga", "Tolga Er", "🧣", "Bere veya eldiven"),
        ("ece", "Ece Aksoy", "🕊️", "Mini sukulent veya tütsü seti"),
        ("kerem", "Kerem Bulut", "🎇", "Akrilik boya veya eskiz defteri"),
        ("hazal", "Hazal Taş", "🎈", "Termos veya kışlık çorap"),
        ("serkan", "Serkan Yavuz", "🧸", "Masa takvimi veya nostaljik radyo"),
        ("ipek", "İpek Çetin", "👑", "Fransız pres veya çay süzgeci"),
        ("murat", "Murat Kurt", "🚀", "T-shirt veya rozet seti"),
        ("selin", "Selin Özdemir", "🪄", "Minyatür biblo veya şal"),
    ]

    def _do_add():
        with get_db() as conn:
            if not resolve_admin(admin_token, conn=conn):
                raise HTTPException(status_code=403, detail="Yetkiniz yok.")
            cursor = conn.cursor()
            pwd_hash = hash_password("123456")
            added = 0
            for uname, dname, avatar, wish in sample_users:
                cursor.execute("""
                    INSERT INTO users (username, password_hash, display_name, wishlist, avatar_icon)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (username) DO NOTHING
                """, (uname, pwd_hash, dname, wish, avatar))
                if cursor.rowcount and cursor.rowcount > 0:
                    added += 1
            conn.commit()
            stats = _compute_public_stats_on_conn(conn)
            return added, stats

    added, stats = await asyncio.to_thread(_do_add)
    await manager.broadcast({
        "type": "users_batch_added",
        "stats": stats,
    })
    return {"success": True, "added_count": added}
