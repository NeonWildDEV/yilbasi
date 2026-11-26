import os
import secrets
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, Depends, Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from database import get_db, init_db, hash_password
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

# Basit bellek içi oturum yönetimi
SESSIONS: Dict[str, int] = {}       # session_token -> user_id
ADMIN_SESSIONS: set = set()          # admin session tokens

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
def get_current_user_id(session_token: Optional[str] = Cookie(None)) -> Optional[int]:
    if session_token and session_token in SESSIONS:
        return SESSIONS[session_token]
    return None

def is_admin_authenticated(admin_token: Optional[str] = Cookie(None)) -> bool:
    return admin_token is not None and admin_token in ADMIN_SESSIONS

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def read_template(filename: str) -> str:
    # 1. templates klasöründe ara
    p1 = os.path.join(TEMPLATES_DIR, filename)
    if os.path.isfile(p1):
        with open(p1, "r", encoding="utf-8") as f:
            return f.read()
    # 2. Ana dizinde (root) ara (GitHub'a doğrudan yüklenmişse)
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

# Gizli Admin Paneli Adresi: /neon
@app.get("/neon", response_class=HTMLResponse)
async def admin_page():
    return read_template("admin.html")

# Standart /admin adresini kapatıyoruz (404 döner)
@app.get("/admin")
async def fake_admin_404():
    raise HTTPException(status_code=404, detail="Sayfa bulunamadı")

# --- WebSocket Canlı Akış ---
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Bağlanır bağlanmaz güncel özet durumu gönder
        stats = get_public_stats_data()
        await websocket.send_json({"type": "init_state", "data": stats})
        while True:
            # Client'tan gelen olası ping/pong mesajlarını dinle
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# --- İstatistik & Canlı Bilgi Fonksiyonu ---
def get_public_stats_data():
    conn = get_db()
    is_drawn = get_setting("is_drawn", "0") == "1"
    draw_title = get_setting("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
    budget_hint = get_setting("budget_hint", "250 - 500 TL")
    
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
            "has_opened_envelope": bool(u["has_opened_envelope"])
        }
        for u in users
    ]
    
    opened_count = sum(1 for p in participants if p["has_opened_envelope"])
    
    conn.close()
    return {
        "is_drawn": is_drawn,
        "draw_title": draw_title,
        "budget_hint": budget_hint,
        "total_participants": len(participants),
        "opened_count": opened_count,
        "participants": participants
    }

@app.get("/api/public-stats")
async def public_stats():
    return get_public_stats_data()

# --- Kullanıcı Kayıt & Giriş API ---
@app.post("/api/register")
async def register(req: RegisterRequest, response: Response):
    username = req.username.strip().lower()
    display_name = req.display_name.strip()
    
    if len(username) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Kullanıcı adı en az 2, şifre en az 4 karakter olmalıdır.")
    if not display_name:
        display_name = req.username.strip()
        
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten alınmış!")
        
    # Çekiliş yapıldıktan sonra yeni kayıt alınmaz
    if get_setting("is_drawn", "0") == "1":
        conn.close()
        raise HTTPException(status_code=400, detail="Kura çekimi tamamlandığı için yeni kayıt alınamaz!")

    pwd_hash = hash_password(req.password)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (username, password_hash, display_name, wishlist, avatar_icon)
        VALUES (?, ?, ?, ?, ?)
    """, (username, pwd_hash, display_name, req.wishlist.strip(), req.avatar_icon or "🎅"))
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    token = secrets.token_hex(16)
    SESSIONS[token] = user_id
    response.set_cookie(key="session_token", value=token, httponly=True, max_age=86400*7)
    
    # Canlı ekrana anlık katılım yayını yap
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "user_registered",
        "user": {
            "display_name": display_name,
            "avatar_icon": req.avatar_icon or "🎅"
        },
        "stats": stats
    })
    
    return {"success": True, "message": "Kayıt başarılı!", "display_name": display_name}

@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    username = req.username.strip().lower()
    pwd_hash = hash_password(req.password)
    
    conn = get_db()
    user = conn.execute("SELECT id, display_name FROM users WHERE username = ? AND password_hash = ?", (username, pwd_hash)).fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı!")
        
    token = secrets.token_hex(16)
    SESSIONS[token] = user["id"]
    response.set_cookie(key="session_token", value=token, httponly=True, max_age=86400*7)
    return {"success": True, "display_name": user["display_name"]}

@app.post("/api/logout")
async def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token in SESSIONS:
        del SESSIONS[session_token]
    response.delete_cookie("session_token")
    return {"success": True}

@app.get("/api/me")
async def get_my_info(user_id: Optional[int] = Depends(get_current_user_id)):
    if not user_id:
        return {"authenticated": False}
        
    conn = get_db()
    user = conn.execute("SELECT id, username, display_name, wishlist, avatar_icon, has_opened_envelope FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return {"authenticated": False}
        
    is_drawn = get_setting("is_drawn", "0") == "1"
    draw_title = get_setting("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
    budget_hint = get_setting("budget_hint", "250 - 500 TL")
    
    receiver_info = None
    if is_drawn:
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
                "avatar_icon": match["avatar_icon"]
            }
            
    conn.close()
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "wishlist": user["wishlist"],
            "avatar_icon": user["avatar_icon"],
            "has_opened_envelope": bool(user["has_opened_envelope"])
        },
        "is_drawn": is_drawn,
        "draw_title": draw_title,
        "budget_hint": budget_hint,
        "receiver": receiver_info
    }

@app.post("/api/open-envelope")
async def open_envelope(user_id: Optional[int] = Depends(get_current_user_id)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Lütfen giriş yapın.")
        
    if get_setting("is_drawn", "0") != "1":
        raise HTTPException(status_code=400, detail="Çekiliş henüz yapılmadı!")
        
    conn = get_db()
    conn.execute("UPDATE users SET has_opened_envelope = 1 WHERE id = ?", (user_id,))
    conn.commit()
    
    user = conn.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "envelope_opened",
        "display_name": user["display_name"] if user else "Bir katılımcı",
        "stats": stats
    })
    
    return {"success": True}

@app.post("/api/update-wishlist")
async def update_wishlist(req: WishlistUpdateRequest, user_id: Optional[int] = Depends(get_current_user_id)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Lütfen giriş yapın.")
    conn = get_db()
    conn.execute("UPDATE users SET wishlist = ? WHERE id = ?", (req.wishlist.strip(), user_id))
    conn.commit()
    conn.close()
    return {"success": True}

# --- Admin API ---
@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest, response: Response):
    # Şifreyi doğrudan güvenli ortam değişkeninden (Environment Variable) oku
    env_pwd = os.getenv("ADMIN_PASSWORD")
    expected_pwd = env_pwd if env_pwd else get_setting("admin_password", "admin123")
    
    if req.password != expected_pwd:
        raise HTTPException(status_code=401, detail="Hatalı şifre!")
        
    token = secrets.token_hex(16)
    ADMIN_SESSIONS.add(token)
    response.set_cookie(key="admin_token", value=token, httponly=True, max_age=86400)
    return {"success": True}

@app.get("/api/admin/overview")
async def admin_overview(is_admin: bool = Depends(is_admin_authenticated)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Yetkiniz yok.")
        
    conn = get_db()
    users = conn.execute("""
        SELECT id, username, display_name, wishlist, avatar_icon, has_opened_envelope, created_at
        FROM users 
        WHERE is_admin = 0
        ORDER BY id ASC
    """).fetchall()
    
    is_drawn = get_setting("is_drawn", "0") == "1"
    draw_title = get_setting("draw_title", "2026 Yılbaşı Gizli Hediye Çekilişi 🎁")
    budget_hint = get_setting("budget_hint", "250 - 500 TL")
    
    # Eşleşmeleri yalnızca admin talep ettiğinde listele
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
                "receiver_name": m["receiver_name"]
            })
            
    conn.close()
    return {
        "users": [dict(u) for u in users],
        "is_drawn": is_drawn,
        "draw_title": draw_title,
        "budget_hint": budget_hint,
        "matches": matches
    }

@app.post("/api/admin/draw")
async def trigger_draw(is_admin: bool = Depends(is_admin_authenticated)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Yetkiniz yok.")
        
    conn = get_db()
    users = conn.execute("SELECT id FROM users WHERE is_admin = 0").fetchall()
    user_ids = [u["id"] for u in users]
    
    if len(user_ids) < 2:
        conn.close()
        raise HTTPException(status_code=400, detail="Çekiliş için en az 2 kişi gereklidir!")
        
    # 1. Eşleşmeleri üret (Derangement / Hamilton döngüsü)
    matches = generate_secret_santa_matches(user_ids)
    
    # 2. Veritabanına kaydet
    conn.execute("DELETE FROM matches")
    for giver, receiver in matches:
        conn.execute("INSERT INTO matches (giver_id, receiver_id) VALUES (?, ?)", (giver, receiver))
        
    # Sıfırlama: Zarfları kapat
    conn.execute("UPDATE users SET has_opened_envelope = 0")
    conn.commit()
    conn.close()
    
    set_setting("is_drawn", "1")
    
    # Canlı yayına animasyon ve çekiliş bildirimini fırlat
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "draw_started",
        "message": "🎁 Kura çekimi başladı! Sihirli hediye torbası karıştırılıyor...",
        "countdown": 4
    })
    
    # 4 saniye sonra tamamlandı sinyali gönder
    async def delayed_complete():
        await asyncio.sleep(4)
        await manager.broadcast({
            "type": "draw_completed",
            "message": "🎉 Çekiliş Tamamlandı! Herkes hesabına giriş yapıp hediye alacağı kişiyi görebilir!",
            "stats": get_public_stats_data()
        })
    asyncio.create_task(delayed_complete())
    
    return {"success": True, "count": len(matches)}

@app.post("/api/admin/reset")
async def reset_draw(is_admin: bool = Depends(is_admin_authenticated)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Yetkiniz yok.")
        
    conn = get_db()
    conn.execute("DELETE FROM matches")
    conn.execute("UPDATE users SET has_opened_envelope = 0")
    conn.commit()
    conn.close()
    
    set_setting("is_drawn", "0")
    
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "draw_reset",
        "message": "Çekiliş sıfırlandı.",
        "stats": stats
    })
    return {"success": True}

@app.delete("/api/admin/user/{user_id}")
async def delete_user(user_id: int, is_admin: bool = Depends(is_admin_authenticated)):
    if not is_admin:
        raise HTTPException(status_code=403, detail="Yetkiniz yok.")
        
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "user_removed",
        "stats": stats
    })
    return {"success": True}

@app.post("/api/admin/add-test-users")
async def add_test_users(is_admin: bool = Depends(is_admin_authenticated)):
    """Canlı yayında veya test aşamasında anında 22 kişilik katılımcı kadrosu oluşturur."""
    if not is_admin:
        raise HTTPException(status_code=403, detail="Yetkiniz yok.")
        
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
        ("selin", "Selin Özdemir", "🪄", "Minyatür biblo veya şal")
    ]
    
    conn = get_db()
    cursor = conn.cursor()
    pwd_hash = hash_password("123456")
    
    added = 0
    for uname, dname, avatar, wish in sample_users:
        try:
            cursor.execute("""
                INSERT INTO users (username, password_hash, display_name, wishlist, avatar_icon)
                VALUES (?, ?, ?, ?, ?)
            """, (uname, pwd_hash, dname, wish, avatar))
            added += 1
        except Exception:
            pass # Zaten varsa atla
            
    conn.commit()
    conn.close()
    
    stats = get_public_stats_data()
    await manager.broadcast({
        "type": "users_batch_added",
        "stats": stats
    })
    return {"success": True, "added_count": added}
