let selectedAvatar = "🎅";

document.addEventListener("DOMContentLoaded", () => {
    setupAvatarSelector();
    setupAuthTabs();
    setupForms();
    checkAuth();
    setupWebSocket();
});

function setupAvatarSelector() {
    const buttons = document.querySelectorAll(".avatar-opt");
    buttons.forEach(btn => {
        btn.addEventListener("click", () => {
            buttons.forEach(b => {
                b.classList.remove("bg-yellow-500/20", "ring-2", "ring-yellow-400");
                b.classList.add("hover:bg-slate-700");
            });
            btn.classList.add("bg-yellow-500/20", "ring-2", "ring-yellow-400");
            btn.classList.remove("hover:bg-slate-700");
            selectedAvatar = btn.getAttribute("data-avatar");
        });
    });
}

function setupAuthTabs() {
    const tabRegister = document.getElementById("tabRegister");
    const tabLogin = document.getElementById("tabLogin");
    const registerForm = document.getElementById("registerForm");
    const loginForm = document.getElementById("loginForm");
    const authAlert = document.getElementById("authAlert");

    tabRegister.addEventListener("click", () => {
        tabRegister.className = "text-lg font-bold text-yellow-400 border-b-2 border-yellow-400 pb-1 transition";
        tabLogin.className = "text-lg font-bold text-gray-400 hover:text-white pb-1 transition";
        registerForm.classList.remove("hidden");
        loginForm.classList.add("hidden");
        authAlert.classList.add("hidden");
    });

    tabLogin.addEventListener("click", () => {
        tabLogin.className = "text-lg font-bold text-yellow-400 border-b-2 border-yellow-400 pb-1 transition";
        tabRegister.className = "text-lg font-bold text-gray-400 hover:text-white pb-1 transition";
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
        authAlert.classList.add("hidden");
    });
}

function showAlert(msg, isError = true) {
    const el = document.getElementById("authAlert");
    el.textContent = msg;
    el.classList.remove("hidden", "bg-red-900/80", "text-red-200", "bg-green-900/80", "text-green-200");
    if (isError) {
        el.classList.add("bg-red-900/80", "text-red-200");
    } else {
        el.classList.add("bg-green-900/80", "text-green-200");
    }
}

function setupForms() {
    const registerForm = document.getElementById("registerForm");
    const loginForm = document.getElementById("loginForm");
    const logoutBtn = document.getElementById("logoutBtn");
    const saveWishlistBtn = document.getElementById("saveWishlistBtn");
    const openEnvelopeBtn = document.getElementById("openEnvelopeBtn");
    const envelopeClickArea = document.getElementById("envelopeClickArea");

    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const display_name = document.getElementById("regDisplayName").value;
        const username = document.getElementById("regUsername").value;
        const password = document.getElementById("regPassword").value;
        const wishlist = document.getElementById("regWishlist").value;

        try {
            const res = await fetch("/api/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username, password, display_name, wishlist,
                    avatar_icon: selectedAvatar
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Kayıt başarısız");

            showAlert("Kayıt başarılı! Yönlendiriliyorsunuz...", false);
            setTimeout(() => checkAuth(), 600);
        } catch (err) {
            showAlert(err.message, true);
        }
    });

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = document.getElementById("loginUsername").value;
        const password = document.getElementById("loginPassword").value;

        try {
            const res = await fetch("/api/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Giriş başarısız");

            showAlert("Giriş başarılı!", false);
            setTimeout(() => checkAuth(), 500);
        } catch (err) {
            showAlert(err.message, true);
        }
    });

    logoutBtn.addEventListener("click", async () => {
        await fetch("/api/logout", { method: "POST" });
        location.reload();
    });

    if (saveWishlistBtn) {
        saveWishlistBtn.addEventListener("click", async () => {
            const wishlist = document.getElementById("dashWishlist").value;
            try {
                const res = await fetch("/api/update-wishlist", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ wishlist })
                });
                if (res.ok) {
                    const msg = document.getElementById("wishlistSaveMsg");
                    msg.classList.remove("hidden");
                    setTimeout(() => msg.classList.add("hidden"), 3000);
                }
            } catch (e) {}
        });
    }

    const triggerOpen = async () => {
        try {
            const res = await fetch("/api/open-envelope", { method: "POST" });
            if (res.ok) {
                playChime();
                triggerConfetti();
                setTimeout(() => checkAuth(), 500);
            }
        } catch (e) {}
    };

    if (openEnvelopeBtn) openEnvelopeBtn.addEventListener("click", triggerOpen);
    if (envelopeClickArea) envelopeClickArea.addEventListener("click", triggerOpen);
}

async function checkAuth() {
    try {
        const res = await fetch("/api/me");
        const data = await res.json();

        const authSection = document.getElementById("authSection");
        const dashboardSection = document.getElementById("dashboardSection");
        const logoutBtn = document.getElementById("logoutBtn");

        if (!data.authenticated) {
            authSection.classList.remove("hidden");
            dashboardSection.classList.add("hidden");
            logoutBtn.classList.add("hidden");
            return;
        }

        authSection.classList.add("hidden");
        dashboardSection.classList.remove("hidden");
        logoutBtn.classList.remove("hidden");

        // Kullanıcı bilgileri
        document.getElementById("userAvatar").textContent = data.user.avatar_icon || "🎅";
        document.getElementById("userDisplayName").textContent = data.user.display_name;
        document.getElementById("dashWishlist").value = data.user.wishlist || "";
        document.getElementById("budgetDisplay").textContent = data.budget_hint || "250 - 500 TL";

        const waitingDrawBox = document.getElementById("waitingDrawBox");
        const drawnBox = document.getElementById("drawnBox");
        const drawStatusBadge = document.getElementById("drawStatusBadge");

        if (!data.is_drawn) {
            waitingDrawBox.classList.remove("hidden");
            drawnBox.classList.add("hidden");
            drawStatusBadge.textContent = "Çekiliş Durumu: Canlı kura bekleniyor...";
            drawStatusBadge.className = "text-xs text-amber-400 mt-1";
        } else {
            waitingDrawBox.classList.add("hidden");
            drawnBox.classList.remove("hidden");
            drawStatusBadge.textContent = "Çekiliş Durumu: Kuralar Çekildi! 🎉";
            drawStatusBadge.className = "text-xs text-emerald-400 mt-1 font-bold";

            const sealedEnvelopeView = document.getElementById("sealedEnvelopeView");
            const revealedResultView = document.getElementById("revealedResultView");

            if (!data.user.has_opened_envelope) {
                sealedEnvelopeView.classList.remove("hidden");
                revealedResultView.classList.add("hidden");
            } else if (data.receiver) {
                sealedEnvelopeView.classList.add("hidden");
                revealedResultView.classList.remove("hidden");

                document.getElementById("targetAvatar").textContent = data.receiver.avatar_icon || "🎁";
                document.getElementById("targetName").textContent = data.receiver.display_name;
                document.getElementById("targetNameShort").textContent = data.receiver.display_name.split(" ")[0];

                const wishEl = document.getElementById("targetWishlist");
                if (data.receiver.wishlist && data.receiver.wishlist.trim()) {
                    wishEl.textContent = `"${data.receiver.wishlist}"`;
                } else {
                    wishEl.textContent = '"Henüz bir ipucu belirtilmemiş, gönlünden kopan güzel bir sürpriz seçebilirsin!"';
                }
            }
        }
    } catch (e) {
        console.error("Auth check failed:", e);
    }
}

function triggerConfetti() {
    if (typeof confetti === "function") {
        confetti({
            particleCount: 100,
            spread: 70,
            origin: { y: 0.6 }
        });
        setTimeout(() => {
            confetti({
                particleCount: 60,
                angle: 60,
                spread: 55,
                origin: { x: 0 }
            });
            confetti({
                particleCount: 60,
                angle: 120,
                spread: 55,
                origin: { x: 1 }
            });
        }, 200);
    }
}

function setupWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;
    let ws = null;

    function connect() {
        ws = new WebSocket(wsUrl);
        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "draw_completed" || msg.type === "draw_reset") {
                    checkAuth();
                }
            } catch (e) {}
        };
        ws.onclose = () => {
            setTimeout(connect, 3000);
        };
    }
    connect();
}
