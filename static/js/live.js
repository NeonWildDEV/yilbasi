document.addEventListener("DOMContentLoaded", () => {
    setupLiveWebSocket();
    setupOverlay();
});

let currentStats = null;

function setupLiveWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;
    let ws = null;

    function connect() {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log("Canlı WebSocket yayınına bağlanıldı.");
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
            console.log("Bağlantı koptu, 2 sn sonra tekrar bağlanılıyor...");
            setTimeout(connect, 2000);
        };
    }

    connect();
}

function handleLiveEvent(payload) {
    switch (payload.type) {
        case "init_state":
            renderStats(payload.data);
            break;

        case "user_registered":
            showToast("Yeni Katılımcı!", `${payload.user.display_name} çekilişe katıldı!`, payload.user.avatar_icon || "🎅");
            playPingSound();
            renderStats(payload.stats);
            break;

        case "users_batch_added":
            showToast("Toplu Katılım!", `Yeni katılımcılar listeye eklendi!`, "🎁");
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

    stats.participants.forEach((p, index) => {
        const card = document.createElement("div");
        card.className = "bg-slate-800/80 hover:bg-slate-800 border border-slate-700/80 rounded-2xl p-3 flex items-center gap-3 shadow-md card-pop transition";

        const badgeHtml = stats.is_drawn
            ? (p.has_opened_envelope
                ? `<span class="text-[10px] text-emerald-400 bg-emerald-950/80 px-2 py-0.5 rounded-full border border-emerald-800">Açtı ✉️</span>`
                : `<span class="text-[10px] text-amber-400 bg-amber-950/80 px-2 py-0.5 rounded-full border border-amber-800">Mühürlü ⏳</span>`)
            : `<span class="text-[10px] text-blue-400 bg-blue-950/80 px-2 py-0.5 rounded-full border border-blue-800">Hazır 🎄</span>`;

        card.innerHTML = `
            <div class="text-3xl bg-slate-900/90 w-11 h-11 rounded-xl flex items-center justify-center border border-slate-700">
                ${p.avatar_icon || "🎅"}
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

    toast.classList.remove("translate-y-24", "opacity-0");
    toast.classList.add("translate-y-0", "opacity-100");

    setTimeout(() => {
        toast.classList.remove("translate-y-0", "opacity-100");
        toast.classList.add("translate-y-24", "opacity-0");
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
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
