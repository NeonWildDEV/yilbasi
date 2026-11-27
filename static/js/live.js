document.addEventListener("DOMContentLoaded", () => {
    setupLiveWebSocket();
    setupOverlay();
    setupLiveChat();
    setupKeepAlive();
});

let currentStats = null;
let chatIdentity = { authenticated: false, displayName: "", avatar: "🎄" };

function setupKeepAlive() {
    // Ekran açık olduğu sürece Render'ın 15 dk uyku moduna girmesini engeller (DB sorgusu yapmaz)
    setInterval(() => {
        fetch("/api/ping").catch(() => {});
    }, 4 * 60 * 1000);
}

function setupLiveWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;
    let ws = null;
    let pingTimer = null;

    function connect() {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            if (pingTimer) clearInterval(pingTimer);
            pingTimer = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send("ping");
                }
            }, 25000);
        };

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                handleLiveEvent(payload);
            } catch (e) {
                console.error("Mesaj parse hatası:", e);
            }
        };

        ws.onclose = () => {
            if (pingTimer) clearInterval(pingTimer);
            setTimeout(connect, 2500);
        };
    }

    connect();
}

function handleLiveEvent(payload) {
    switch (payload.type) {
        case "init_state":
            renderStats(payload.data);
            renderChatHistory(payload.chat || []);
            updateOnlineCount(payload.online_count);
            break;

        case "presence":
        case "pong":
            updateOnlineCount(payload.online_count);
            break;

        case "chat_message":
            appendChatMessage(payload.message);
            break;

        case "chat_cleared":
            renderChatHistory([]);
            break;

        case "user_registered":
            showToast("Yeni Katılımcı!", `${payload.user.display_name} çekilişe katıldı!`, payload.user.avatar_icon || "🎅");
            playPingSound();
            renderStats(payload.stats);
            break;

        case "users_batch_added":
            showToast("Toplu Katılım!", "Yeni katılımcılar listeye eklendi!", "🎁");
            renderStats(payload.stats);
            break;

        case "user_removed":
            renderStats(payload.stats);
            break;

        case "envelope_opened":
            showToast("Zarf Açıldı!", `${payload.display_name} gizli hediye zarfını açtı! ✉️`, "✨");
            playPingSound();
            renderStats(payload.stats);
            break;

        case "draw_started":
            triggerDrawAnimation(payload.countdown || 4);
            break;

        case "draw_completed":
            completeDrawAnimation(payload.stats);
            break;

        case "draw_reset":
            showToast("Kura Sıfırlandı", "Admin tarafından kuralar sıfırlandı.", "🔄");
            renderStats(payload.stats);
            break;
    }
}

function updateOnlineCount(count) {
    const el = document.getElementById("onlineCountBadge");
    if (el && typeof count === "number") {
        el.textContent = `${count} Canlı`;
    }
}

async function setupLiveChat() {
    const guestBox = document.getElementById("chatGuestBox");
    const loggedBox = document.getElementById("chatLoggedIdentity");
    const loggedNameEl = document.getElementById("chatLoggedName");
    const guestInput = document.getElementById("chatGuestName");
    const chatForm = document.getElementById("chatForm");
    const chatInput = document.getElementById("chatInput");
    const chatSendBtn = document.getElementById("chatSendBtn");
    const chatError = document.getElementById("chatError");

    // Kayıtlı misafir adını yükle
    const savedGuest = localStorage.getItem("yilbasi_chat_guest") || "";
    const savedAvatar = localStorage.getItem("yilbasi_chat_avatar") || "🎄";
    if (guestInput && savedGuest) {
        guestInput.value = savedGuest;
    }
    chatIdentity.avatar = savedAvatar;

    // Kullanıcı zaten giriş yapmış mı kontrol et
    try {
        const res = await fetch("/api/me");
        const data = await res.json();
        if (data.authenticated && data.user) {
            chatIdentity = {
                authenticated: true,
                displayName: data.user.display_name,
                avatar: data.user.avatar_icon || "🎅",
            };
            if (guestBox) guestBox.classList.add("hidden");
            if (loggedBox && loggedNameEl) {
                loggedNameEl.textContent = `${chatIdentity.avatar} ${chatIdentity.displayName}`;
                loggedBox.classList.remove("hidden");
            }
        }
    } catch (e) {}

    // Hızlı emoji butonları
    document.querySelectorAll(".quick-emoji-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            if (!chatInput) return;
            chatInput.value = (chatInput.value + btn.getAttribute("data-emoji")).slice(0, 200);
            chatInput.focus();
        });
    });

    if (!chatForm) return;

    let sending = false;
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (sending || !chatInput) return;

        const text = chatInput.value.trim();
        if (!text) return;

        let senderName = chatIdentity.displayName;
        if (!chatIdentity.authenticated && guestInput) {
            senderName = guestInput.value.trim();
            if (senderName.length < 2) {
                showChatError("Lütfen önce en az 2 harfli adınızı yazın.");
                guestInput.focus();
                return;
            }
            localStorage.setItem("yilbasi_chat_guest", senderName);
        }

        sending = true;
        if (chatSendBtn) chatSendBtn.disabled = true;
        if (chatError) chatError.classList.add("hidden");

        try {
            const res = await fetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text,
                    sender_name: senderName,
                    avatar_icon: chatIdentity.avatar,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                showChatError(data.detail || "Mesaj gönderilemedi.");
            } else {
                chatInput.value = "";
            }
        } catch (err) {
            showChatError("Bağlantı hatası, tekrar deneyin.");
        } finally {
            sending = false;
            if (chatSendBtn) chatSendBtn.disabled = false;
            chatInput.focus();
        }
    });
}

function showChatError(msg) {
    const el = document.getElementById("chatError");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 3500);
}

function renderChatHistory(messages) {
    const box = document.getElementById("chatMessages");
    const emptyEl = document.getElementById("chatEmptyState");
    if (!box) return;

    box.innerHTML = "";
    if (!messages || messages.length === 0) {
        if (emptyEl) emptyEl.classList.remove("hidden");
        return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");

    messages.forEach((m) => appendChatMessage(m, false));
    box.scrollTop = box.scrollHeight;
}

function appendChatMessage(m, autoScroll = true) {
    if (!m) return;
    const box = document.getElementById("chatMessages");
    const emptyEl = document.getElementById("chatEmptyState");
    if (!box) return;
    if (emptyEl) emptyEl.classList.add("hidden");

    // Tekrarlı mesaj kontrolü
    if (m.id && box.querySelector(`[data-msg-id="${m.id}"]`)) return;

    const item = document.createElement("div");
    item.className = "bg-slate-800/75 border border-slate-700/60 rounded-2xl px-3 py-2 text-xs card-pop";
    if (m.id) item.setAttribute("data-msg-id", String(m.id));

    const header = document.createElement("div");
    header.className = "flex items-center justify-between gap-2 mb-0.5";

    const left = document.createElement("div");
    left.className = "flex items-center gap-1.5 min-w-0";

    const avatarSpan = document.createElement("span");
    avatarSpan.className = "text-sm shrink-0";
    avatarSpan.textContent = m.avatar || "🎄";

    const senderSpan = document.createElement("span");
    senderSpan.className = m.verified
        ? "font-bold text-amber-300 truncate"
        : "font-semibold text-blue-300 truncate";
    senderSpan.textContent = m.sender || "Katılımcı";

    left.appendChild(avatarSpan);
    left.appendChild(senderSpan);

    const timeSpan = document.createElement("span");
    timeSpan.className = "text-[10px] text-gray-500 shrink-0";
    timeSpan.textContent = m.time || "";

    header.appendChild(left);
    header.appendChild(timeSpan);

    const body = document.createElement("p");
    body.className = "text-gray-100 break-words leading-relaxed";
    body.textContent = m.text || "";

    item.appendChild(header);
    item.appendChild(body);
    box.appendChild(item);

    while (box.children.length > 60) {
        box.removeChild(box.firstChild);
    }

    if (autoScroll) {
        box.scrollTop = box.scrollHeight;
    }
}

function renderStats(stats) {
    if (!stats) return;
    currentStats = stats;

    document.getElementById("totalCount").textContent = `${stats.total_participants} Kişi`;
    const budgetEl = document.getElementById("budgetSpan");
    if (budgetEl) budgetEl.textContent = stats.budget_hint || "";
    document.getElementById("openedCount").textContent = `${stats.opened_count} / ${stats.total_participants}`;

    const liveStatusText = document.getElementById("liveStatusText");
    if (stats.is_drawn) {
        liveStatusText.textContent = "Kuralar Çekildi! (Zarflar Dağıtıldı)";
        liveStatusText.className = "text-xs font-semibold px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40";
    } else {
        liveStatusText.textContent = "Kura Çekimi Bekleniyor...";
        liveStatusText.className = "text-xs font-semibold px-3 py-1 rounded-full bg-yellow-500/10 text-yellow-400 border border-yellow-500/30";
    }

    const grid = document.getElementById("participantsGrid");
    const emptyState = document.getElementById("emptyListState");

    if (stats.participants.length === 0) {
        grid.innerHTML = "";
        emptyState.classList.remove("hidden");
        return;
    }

    emptyState.classList.add("hidden");
    grid.innerHTML = "";

    stats.participants.forEach((p) => {
        const card = document.createElement("div");
        card.className = "bg-slate-800/80 hover:bg-slate-800 border border-slate-700/80 rounded-2xl p-3 flex items-center gap-3 shadow-md card-pop transition";

        const badgeHtml = stats.is_drawn
            ? (p.has_opened_envelope
                ? `<span class="text-[10px] text-emerald-400 bg-emerald-950/80 px-2 py-0.5 rounded-full border border-emerald-800">Açtı ✉️</span>`
                : `<span class="text-[10px] text-amber-400 bg-amber-950/80 px-2 py-0.5 rounded-full border border-amber-800">Mühürlü ⏳</span>`)
            : `<span class="text-[10px] text-blue-400 bg-blue-950/80 px-2 py-0.5 rounded-full border border-blue-800">Hazır 🎄</span>`;

        card.innerHTML = `
            <div class="text-3xl bg-slate-900/90 w-11 h-11 rounded-xl flex items-center justify-center border border-slate-700 shrink-0">
                ${escapeHtml(p.avatar_icon || "🎅")}
            </div>
            <div class="overflow-hidden flex-1">
                <div class="text-xs font-bold text-white truncate">${escapeHtml(p.display_name)}</div>
                <div class="mt-0.5">${badgeHtml}</div>
            </div>
        `;
        grid.appendChild(card);
    });
}

function showToast(title, msg, icon = "🎁") {
    const toast = document.getElementById("liveToast");
    document.getElementById("toastTitle").textContent = title;
    document.getElementById("toastMsg").textContent = msg;
    document.getElementById("toastIcon").textContent = icon;

    toast.classList.remove("-translate-y-24", "opacity-0");
    toast.classList.add("translate-y-0", "opacity-100");

    setTimeout(() => {
        toast.classList.remove("translate-y-0", "opacity-100");
        toast.classList.add("-translate-y-24", "opacity-0");
    }, 4000);
}

function triggerDrawAnimation(countdownSeconds = 4) {
    const overlay = document.getElementById("drawOverlay");
    const mixingStage = document.getElementById("drawMixingStage");
    const completedStage = document.getElementById("drawCompletedStage");
    const timerEl = document.getElementById("countdownTimer");

    overlay.classList.remove("hidden");
    mixingStage.classList.remove("hidden");
    completedStage.classList.add("hidden");

    let count = countdownSeconds;
    timerEl.textContent = count;

    const interval = setInterval(() => {
        count--;
        if (count > 0) {
            timerEl.textContent = count;
            playTockSound();
        } else {
            clearInterval(interval);
        }
    }, 1000);
}

function completeDrawAnimation(stats) {
    const mixingStage = document.getElementById("drawMixingStage");
    const completedStage = document.getElementById("drawCompletedStage");

    mixingStage.classList.add("hidden");
    completedStage.classList.remove("hidden");

    playCelebrationFanfare();
    shootMegaConfetti();

    if (stats) renderStats(stats);
}

function setupOverlay() {
    const closeBtn = document.getElementById("closeOverlayBtn");
    closeBtn.addEventListener("click", () => {
        document.getElementById("drawOverlay").classList.add("hidden");
    });
}

function shootMegaConfetti() {
    if (typeof confetti !== "function") return;
    const duration = 4000;
    const end = Date.now() + duration;

    (function frame() {
        confetti({
            particleCount: 5,
            angle: 60,
            spread: 55,
            origin: { x: 0, y: 0.6 }
        });
        confetti({
            particleCount: 5,
            angle: 120,
            spread: 55,
            origin: { x: 1, y: 0.6 }
        });

        if (Date.now() < end) {
            requestAnimationFrame(frame);
        }
    }());
}

function playPingSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        gain.gain.setValueAtTime(0.1, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.3);
    } catch (e) {}
}

function playTockSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.15);
    } catch (e) {}
}

function playCelebrationFanfare() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const notes = [523.25, 659.25, 783.99, 1046.50, 1318.51];
        notes.forEach((freq, idx) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            const time = ctx.currentTime + idx * 0.15;
            osc.frequency.setValueAtTime(freq, time);
            gain.gain.setValueAtTime(0.2, time);
            gain.gain.exponentialRampToValueAtTime(0.001, time + 0.8);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(time);
            osc.stop(time + 0.8);
        });
    } catch (e) {}
}

function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
