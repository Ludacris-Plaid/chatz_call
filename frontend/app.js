// ═══════════════════════════════════════════════════════════════
//  CLAWCALL // NETRUNNER TERMINAL — FRONTEND APP
//  API: Bearer token auth, POST /api/call for outbound dialing
//  NO SIP.js/WebRTC — calls originate server-side via Asterisk AMI
// ═══════════════════════════════════════════════════════════════

const API = window.location.origin;
const CMD_UNLOCK = "*#*#";

// ── State ──────────────────────────────────────────────────
let user = null;
let authToken = null;
let authMode = "sign-in";
let callStartTime = null;
let callTimer = null;
let isMuted = false;
let activePaymentId = null;
let paymentPollTimer = null;
let currentBalance = 0;
let isVip = false;
let isAdmin = false;
let keypadBuffer = "";
let activeField = null; // null | "cid" | "dest"
let cidLocked = false;  // true when CNAM lookup has completed
let cidValue = "17804755555";

// ── DOM refs ────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const els = {
  authGate: $("authGate"), authForm: $("authForm"),
  authTitle: $("authTitle"), authHelp: $("authHelp"),
  authUsername: $("authUsername"), authPassword: $("authPassword"),
  authSubmitBtn: $("authSubmitBtn"), authModeBtn: $("authModeBtn"),
  appShell: document.querySelector(".app-shell"),
  signOutBtn: $("signOutBtn"), headerBalance: $("headerTokenBalance"),
  // Phone-style displays
  cidDisplay: $("cidDisplay"), cidNumber: $("cidNumber"),
  cidInfo: $("cidInfo"), cidCursor: $("cidCursor"),
  cidLockBtn: $("cidLockBtn"),
  destDisplay: $("destDisplay"), destNumber: $("destNumber"),
  destCursor: $("destCursor"),
  // Keypad + actions
  callBtn: $("callBtn"), backspaceBtn: $("backspaceBtn"),
  clearAllBtn: $("clearAllBtn"),
  // HUD + misc
  dialerStack: $("dialerIdleStack"), callState: $("callState"),
  dialerDot: $("dialerDot"), callHud: $("callHud"),
  callHudState: $("callHudState"),
  callHudDest: $("callHudDestination"), callHudTimer: $("callHudTimer"),
  callHudCost: $("callHudCost"), hudMuteBtn: $("hudMuteBtn"),
  hudHangupBtn: $("hudHangupBtn"), hudCidPrimary: $("callHudCidPrimary"),
  eventLog: $("eventLog"), terminalPanel: $("terminalPanel"),
  callerIdStatus: $("callerIdStatus"),
  tokenBalance: $("tokenBalance"), minuteRate: $("minuteRate"),
  vipStatus: $("vipStatus"), vipPrice: $("vipPrice"), vipExpiry: $("vipExpiry"),
  topupBtn: $("topupBtn"), tokenAmount: $("tokenAmount"),
  payCurrency: $("payCurrency"), topupStatus: $("topupStatus"),
  callHistory: $("callHistory"), adminNavBtn: $("adminNavBtn"),
  paymentModal: $("paymentModal"), modalAddress: $("modalAddress"),
  modalAmount: $("modalAmount"), modalCurrency: $("modalCurrency"),
  modalStatus: $("modalStatus"), modalExplorer: $("modalExplorerLink"),
  modalClose: $("modalCloseBtn"), qrContainer: $("qrContainer"),
  copyAddrBtn: $("copyAddrBtn"),
};

// ── API Client ──────────────────────────────────────────────
// Normalize number for API calls: always add leading 1 for 10-digit numbers
function apiDigits(display) {
  const d = display.replace(/\D/g, "");
  return d.length === 10 ? "1" + d : d;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...opts.headers };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const res = await fetch(API + path, {
    headers,
    ...opts,
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── Auth ─────────────────────────────────────────────────────
els.authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = els.authUsername.value.trim().toLowerCase();
  const password = els.authPassword.value;
  if (!username || !password) return;

  const endpoint = authMode === "sign-in" ? "/api/auth/login" : "/api/auth/register";
  els.authSubmitBtn.disabled = true;
  els.authSubmitBtn.textContent = "[ PROCESSING ]";

  try {
    const data = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    onLoginSuccess(data);
  } catch (err) {
    els.authHelp.textContent = `ERR: ${err.message}`;
    els.authSubmitBtn.disabled = false;
    els.authSubmitBtn.textContent = authMode === "sign-in" ? "AUTHENTICATE >" : "INITIALIZE >";
  }
});

els.authModeBtn.addEventListener("click", () => {
  authMode = authMode === "sign-in" ? "register" : "sign-in";
  els.authTitle.textContent = authMode === "sign-in" ? "SYS.AUTH" : "SYS.INIT";
  els.authSubmitBtn.textContent = authMode === "sign-in" ? "AUTHENTICATE >" : "INITIALIZE >";
  els.authModeBtn.textContent = authMode === "sign-in" ? "NEW OPERATOR" : "EXISTING OPERATOR";
  els.authHelp.textContent = authMode === "sign-in"
    ? "Enter credentials to establish uplink."
    : "Register new operator identity.";
});

function onLoginSuccess(data) {
  user = data.user;
  authToken = data.token;
  isAdmin = data.user.is_admin;
  isVip = data.vip_active || false;
  currentBalance = data.user.token_balance || 0;

  localStorage.setItem("clawcall_token", authToken);
  localStorage.setItem("clawcall_user", JSON.stringify(user));

  els.authGate.classList.add("hidden");
  els.appShell.classList.remove("hidden");
  updateUI();
  logEvent(`UPLINK ESTABLISHED // operator: ${user.username}${isAdmin ? " [ROOT]" : ""}`);
}

// Auto-login from stored token
(async function tryAutoLogin() {
  const savedToken = localStorage.getItem("clawcall_token");
  const savedUser = localStorage.getItem("clawcall_user");
  if (savedToken && savedUser) {
    try {
      authToken = savedToken;
      const data = await api("/api/me");
      user = data.user;
      isAdmin = data.user.is_admin;
      isVip = data.vip_active || false;
      currentBalance = data.user.token_balance || 0;
      els.authGate.classList.add("hidden");
      els.appShell.classList.remove("hidden");
      updateUI();
      logEvent(`SESSION RESTORED // operator: ${user.username}`);
      return;
    } catch (_) {
      localStorage.removeItem("clawcall_token");
      localStorage.removeItem("clawcall_user");
      authToken = null;
    }
  }
  // Show auth if not logged in
  els.authGate.classList.remove("hidden");
})();

els.signOutBtn.addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  authToken = null;
  user = null;
  localStorage.removeItem("clawcall_token");
  localStorage.removeItem("clawcall_user");
  els.authGate.classList.remove("hidden");
  els.appShell.classList.add("hidden");
  logEvent("UPLINK TERMINATED");
});

// ── UI Updates ───────────────────────────────────────────────
function updateUI() {
  els.headerBalance.textContent = isAdmin ? "∞" : currentBalance.toFixed(1);
  els.tokenBalance.textContent = isAdmin ? "∞" : currentBalance.toFixed(1);
  els.adminNavBtn.classList.toggle("hidden", !isAdmin);
  els.dialerDot.classList.add("registered"); // Always "online" since no SIP registration
  els.callBtn.disabled = false;

  if (isAdmin) {
    els.vipStatus.textContent = "ROOT"; els.minuteRate.textContent = "∞";
  } else if (isVip) {
    els.vipStatus.textContent = "ACTIVE"; els.minuteRate.textContent = "VIP";
  } else {
    els.vipStatus.textContent = "STANDBY"; els.minuteRate.textContent = "$0.50/min";
  }
}

async function refreshWallet() {
  try {
    const data = await api("/api/me");
    user = data.user;
    currentBalance = data.user.token_balance || 0;
    isVip = data.vip_active || false;
    isAdmin = data.user.is_admin;
    localStorage.setItem("clawcall_user", JSON.stringify(user));
    updateUI();
  } catch (err) { logEvent(`WALLET: ${err.message}`); }
}

// ── Outbound Calls (POST /api/call) ─────────────────────────
els.callBtn.addEventListener("click", () => placeCall());

async function placeCall() {
  const dest = els.destNumber.textContent.replace(/\D/g, "");
  if (!dest || dest.length < 10) {
    logEvent("CALL: Invalid destination");
    return;
  }

  const callerId = cidValue;

  els.callBtn.disabled = true;
  els.callBtn.textContent = "CONNECTING...";
  logEvent(`CALL: Originating to ${dest} [CID: ${callerId}]`);

  try {
    const result = await api("/api/call", {
      method: "POST",
      body: JSON.stringify({"destination": apiDigits(dest), "caller_id": apiDigits(callerId)}),
    });

    if (result.ok) {
      showCallHud(dest);
      els.callHudState.textContent = "CALLING";
      callStartTime = Date.now();
      startCallTimer();
      logEvent(`CALL: ${result.target} // channel: ${result.channel}`);
    }
  } catch (err) {
    logEvent(`CALL FAILED: ${err.message}`);
    els.callBtn.disabled = false;
    els.callBtn.textContent = "CALL";
  }
}

// ── Call HUD ─────────────────────────────────────────────────
function showCallHud(dest) {
  els.callHud.classList.remove("hidden");
  els.dialerStack.classList.add("hidden");
  els.callHudDest.textContent = dest;
  els.hudCidPrimary.textContent = cidValue;
  els.callHudState.textContent = "CONNECTED";
}

function hideCallHud() {
  els.callHud.classList.add("hidden");
  els.dialerStack.classList.remove("hidden");
  els.callBtn.disabled = false;
  els.callBtn.textContent = "CALL";
}

function startCallTimer() {
  stopCallTimer();
  callTimer = setInterval(() => {
    if (!callStartTime) return;
    const sec = Math.floor((Date.now() - callStartTime) / 1000);
    const min = Math.floor(sec / 60);
    els.callHudTimer.textContent = `${String(min).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
    els.callHudCost.textContent = isAdmin || isVip ? "∞" : `$${((sec / 60) * 0.5).toFixed(2)}`;
  }, 1000);
}

function stopCallTimer() {
  if (callTimer) { clearInterval(callTimer); callTimer = null; }
}

els.hudHangupBtn.addEventListener("click", () => {
  const duration = callStartTime ? Math.round((Date.now() - callStartTime) / 1000) : 0;
  stopCallTimer();
  hideCallHud();
  callStartTime = null;
  if (duration > 0) saveCallToHistory(els.callHudDest.textContent, duration, els.hudCidPrimary.textContent);
  logEvent(`CALL ENDED // duration: ${duration}s`);
});

els.hudMuteBtn.addEventListener("click", () => {
  isMuted = !isMuted;
  els.hudMuteBtn.textContent = isMuted ? "[ UNMUTE ]" : "[ MUTE ]";
});

// ── Phone Display Interaction ────────────────────────────────
// Click CID display → clears, activates cursor, keypad routes here
els.cidDisplay.addEventListener("click", () => {
  if (activeField === "cid") {
    // Clicking again deactivates
    deactivateField("cid");
    return;
  }
  activateField("cid");
});

// Click destination display → same pattern
els.destDisplay.addEventListener("click", () => {
  if (activeField === "dest") {
    deactivateField("dest");
    return;
  }
  activateField("dest");
});

function activateField(field) {
  // Deactivate current first
  if (activeField) deactivateField(activeField);
  activeField = field;
  cidLocked = false;

  if (field === "cid") {
    els.cidNumber.textContent = "";
    els.cidCursor.classList.remove("hidden");
    els.cidDisplay.classList.add("active");
    els.cidLockBtn.classList.remove("hidden");
    els.cidInfo.textContent = "ENTER NUMBER";
  } else {
    els.destNumber.textContent = "";
    els.destCursor.classList.remove("hidden");
    els.destDisplay.classList.add("active");
  }

  els.callBtn.disabled = false;
  logEvent(`INPUT: ${field === "cid" ? "caller ID" : "destination"} active`);
}

function deactivateField(field) {
  if (field === "cid") {
    els.cidCursor.classList.add("hidden");
    els.cidDisplay.classList.remove("active");
    els.cidLockBtn.classList.add("hidden");
    // Restore previous value or keep what was typed
    if (!els.cidNumber.textContent.trim()) {
      els.cidNumber.textContent = cidValue;
    }
    els.cidInfo.textContent = cidLocked ? els.cidInfo.textContent : "UNKNOWN";
  } else {
    els.destCursor.classList.add("hidden");
    els.destDisplay.classList.remove("active");
    if (!els.destNumber.textContent.trim()) {
      els.destNumber.textContent = "\u00A0";
    }
  }
  activeField = null;
}

// ── Keypad ───────────────────────────────────────────────────
document.querySelectorAll("#keypad button").forEach(btn => {
  btn.addEventListener("click", () => {
    if (!activeField) {
      // No field active — default to destination
      activateField("dest");
    }

    const key = btn.dataset.key;
    const target = activeField === "cid" ? els.cidNumber : els.destNumber;

    // Replace placeholder/nbsp on first digit
    if (target.textContent === "\u00A0" || target.textContent === "ENTER NUMBER") {
      target.textContent = "";
    }

    target.textContent += key;
    keypadBuffer += key;

    if (keypadBuffer.endsWith(CMD_UNLOCK) && els.terminalPanel.classList.contains("locked")) {
      els.terminalPanel.classList.remove("locked");
      keypadBuffer = "";
      logEvent("TERMINAL UNLOCKED // secret: " + CMD_UNLOCK);
    }

    // Show lock button when CID has digits
    if (activeField === "cid" && target.textContent.length >= 10) {
      els.cidLockBtn.classList.remove("hidden");
    }
  });
});

// ── Backspace ────────────────────────────────────────────────
els.backspaceBtn.addEventListener("click", () => {
  if (!activeField) return;
  const target = activeField === "cid" ? els.cidNumber : els.destNumber;
  const text = target.textContent;
  if (text && text !== "\u00A0") {
    target.textContent = text.slice(0, -1);
    if (!target.textContent) {
      target.textContent = activeField === "cid" ? "" : "\u00A0";
    }
  }
});

// ── Clear All ────────────────────────────────────────────────
els.clearAllBtn.addEventListener("click", () => {
  els.cidNumber.textContent = cidValue;
  els.cidInfo.textContent = "UNKNOWN";
  els.destNumber.textContent = "\u00A0";
  els.cidLockBtn.classList.add("hidden");
  cidLocked = false;
  activeField = null;
  els.cidDisplay.classList.remove("active");
  els.destDisplay.classList.remove("active");
  els.cidCursor.classList.add("hidden");
  els.destCursor.classList.add("hidden");
  els.callerIdStatus.textContent = "";
  keypadBuffer = "";
  logEvent("ALL CLEARED");
});

// ── CID Lock / CNAM Lookup ───────────────────────────────────
els.cidLockBtn.addEventListener("click", async () => {
  const digits = els.cidNumber.textContent.replace(/\D/g, "");
  if (!digits || digits.length < 10) {
    logEvent("CID: need 10+ digits to lookup");
    return;
  }

  els.cidLockBtn.disabled = true;
  els.cidLockBtn.textContent = "[ SCANNING... ]";
  els.cidInfo.textContent = "SCANNING...";
  els.callerIdStatus.textContent = "LOOKUP";

  try {
    const data = await api(`/api/cnam?q=${apiDigits(digits)}`);
    const label = data.label || "UNKNOWN";

    // Save CID + persist to server
    cidValue = digits;
    cidLocked = true;
    els.cidNumber.textContent = digits;

    // Glitch transition on the info line
    glitchText(els.cidInfo, label, 800);

    els.callerIdStatus.textContent = data.cached ? "CACHED" : "VERIFIED";
    els.cidLockBtn.textContent = "[ LOCKED ]";
    els.cidLockBtn.classList.add("locked");
    els.cidCursor.classList.add("hidden");
    els.cidDisplay.classList.remove("active");
    activeField = null;

    // Persist CID to server
    api("/api/caller-id", {
      method: "POST",
      body: JSON.stringify({ caller_id: apiDigits(digits) }),
    }).then(d => logEvent(`CID: ${d.caller_id} // saved`))
      .catch(e => logEvent(`CID: local (${e.message})`));

    logEvent(`CNAM: ${digits} → ${label}${data.cached ? " [cache]" : ""}`);
  } catch (err) {
    els.cidInfo.textContent = "UNKNOWN";
    els.callerIdStatus.textContent = "ERR";
    els.cidLockBtn.textContent = "[ LOOKUP CID ]";
    logEvent(`CNAM FAILED: ${err.message}`);
  }
  els.cidLockBtn.disabled = false;
});

// ── Glitch Text Transition Engine ─────────────────────────────
function glitchText(element, finalText, duration = 800) {
  const chars = "!@#$%^&*()_+-=[]{}|;:,.<>?/ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  const steps = 12;
  const stepMs = duration / steps;

  element.setAttribute("data-text", finalText);
  element.classList.add("cnam-glitching");

  let step = 0;
  const interval = setInterval(() => {
    step++;
    if (step >= steps) {
      clearInterval(interval);
      element.textContent = finalText;
      element.classList.remove("cnam-glitching");
      element.classList.add("cnam-resolved");
      element.setAttribute("data-text", finalText);
      setTimeout(() => element.classList.remove("cnam-resolved"), 500);
      return;
    }

    const progress = step / steps;
    let scrambled = "";
    for (let i = 0; i < finalText.length; i++) {
      scrambled += Math.random() < progress ? finalText[i] : chars[Math.floor(Math.random() * chars.length)];
    }
    if (step < steps - 2 && Math.random() > 0.7) {
      scrambled += chars[Math.floor(Math.random() * chars.length)];
    }
    element.textContent = scrambled;
    element.setAttribute("data-text", scrambled);
  }, stepMs);

  return { cancel: () => { clearInterval(interval); element.textContent = finalText; element.classList.remove("cnam-glitching"); } };
}

// ── NOWPayments — Crypto Top-up ──────────────────────────────
els.topupBtn.addEventListener("click", async (e) => {
  e.preventDefault();
  const amount = parseInt(els.tokenAmount.value) || 20;
  const currency = els.payCurrency.value;
  els.topupBtn.disabled = true;
  els.topupStatus.textContent = "Generating invoice...";

  try {
    const data = await api("/api/topups", {
      method: "POST",
      body: JSON.stringify({ token_amount: amount, pay_currency: currency }),
    });
    activePaymentId = data.payment_id;
    showPaymentModal(data);
    startPaymentPoll();
    els.topupStatus.textContent = "Invoice ready. Transfer crypto to receive tokens.";
  } catch (err) {
    els.topupStatus.textContent = `Failed: ${err.message}`;
  }
  els.topupBtn.disabled = false;
});

$("vipBuyBtn").addEventListener("click", async () => {
  $("vipBuyBtn").disabled = true;
  els.topupStatus.textContent = "Generating VIP invoice...";
  try {
    const data = await api("/api/topups/vip", {
      method: "POST",
      body: JSON.stringify({ pay_currency: els.payCurrency.value }),
    });
    activePaymentId = data.payment_id;
    showPaymentModal(data);
    startPaymentPoll();
    els.topupStatus.textContent = "VIP invoice ready. 7 days unlimited upon confirmation.";
  } catch (err) {
    els.topupStatus.textContent = `Failed: ${err.message}`;
  }
  $("vipBuyBtn").disabled = false;
});

function showPaymentModal(payment) {
  els.qrContainer.innerHTML = "";
  const qrData = `${payment.pay_currency?.toLowerCase() || "ltc"}:${payment.pay_address}?amount=${payment.pay_amount}`;
  new QRCode(els.qrContainer, {
    text: qrData,
    width: 200, height: 200,
    colorDark: "#22c55e", colorLight: "#020617",
  });

  els.modalAddress.textContent = payment.pay_address || "";
  els.modalAmount.textContent = `${payment.pay_amount || "?"} ${payment.pay_currency || ""} ($${payment.usd_amount})`;
  els.modalCurrency.textContent = payment.pay_currency || "";
  els.modalCurrency.className = "badge";
  els.modalStatus.textContent = payment.status || "waiting";

  const addr = payment.pay_address || "";
  const currency = (payment.pay_currency || "ltc").toLowerCase();
  const explorers = {
    btc: `https://www.blockchain.com/explorer/addresses/btc/${addr}`,
    ltc: `https://blockchair.com/litecoin/address/${addr}`,
  };
  els.modalExplorer.href = explorers[currency] || explorers.ltc;
  els.paymentModal.classList.remove("hidden");
}

els.modalClose.addEventListener("click", () => {
  els.paymentModal.classList.add("hidden");
  stopPaymentPoll();
});

els.copyAddrBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(els.modalAddress.textContent).then(() => {
    els.copyAddrBtn.textContent = "COPIED";
    setTimeout(() => { els.copyAddrBtn.textContent = "COPY"; }, 2000);
  });
});

function startPaymentPoll() {
  stopPaymentPoll();
  paymentPollTimer = setInterval(async () => {
    if (!activePaymentId) return stopPaymentPoll();
    try {
      const payment = await api(`/api/topups/${activePaymentId}`);
      els.modalStatus.textContent = payment.status;
      if (payment.status === "credited") {
        stopPaymentPoll(); activePaymentId = null;
        setTimeout(() => { els.paymentModal.classList.add("hidden"); refreshWallet(); }, 2000);
        els.topupStatus.textContent = "Payment credited!";
      } else if (["failed", "expired", "refunded"].includes(payment.status)) {
        stopPaymentPoll(); activePaymentId = null;
        els.topupStatus.textContent = `Payment ${payment.status}.`;
      }
    } catch (_) {}
  }, 10000);
}
function stopPaymentPoll() {
  if (paymentPollTimer) { clearInterval(paymentPollTimer); paymentPollTimer = null; }
}

// ── Call History ─────────────────────────────────────────────
function saveCallToHistory(dest, duration, cid) {
  const item = { dest, duration, cid, time: new Date().toISOString() };
  const history = JSON.parse(localStorage.getItem("clawcall_history") || "[]");
  history.unshift(item);
  localStorage.setItem("clawcall_history", JSON.stringify(history.slice(0, 100)));
}

function renderHistory() {
  const history = JSON.parse(localStorage.getItem("clawcall_history") || "[]");
  els.callHistory.innerHTML = history.length
    ? history.map(h => `
      <div class="history-item">
        <span>${h.dest}</span>
        <span>CID: ${h.cid || "?"}</span>
        <span>${h.duration}s</span>
        <span class="history-time">${new Date(h.time).toLocaleString()}</span>
      </div>
    `).join("")
    : '<p class="inline-status">// NO CALLS LOGGED</p>';
}

$("clearHistoryBtn").addEventListener("click", () => {
  localStorage.removeItem("clawcall_history");
  renderHistory();
});

// ── Screen Navigation ────────────────────────────────────────
document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".app-screen").forEach(s => s.classList.remove("active"));
    const target = $(btn.dataset.target);
    if (target) target.classList.add("active");
    if (btn.dataset.target === "historyScreen") renderHistory();
    if (btn.dataset.target === "adminScreen" && isAdmin) loadAdmin();
  });
});

// ── Admin ────────────────────────────────────────────────────
async function loadAdmin() {
  try {
    const [users, stats] = await Promise.all([
      api("/api/admin/users"),
      api("/api/admin/stats"),
    ]);
    $("adminUsersValue").textContent = stats.total_users;
    $("adminRevenueValue").textContent = `$${stats.total_revenue}`;
    $("adminVipValue").textContent = stats.vip_users;
    const list = $("adminUsersList");
    if (list) {
      list.innerHTML = users.users.map(u => `
        <div class="history-item">
          <span>${u.email?.split("@")[0] || u.id}</span>
          <span>Role: ${u.role}</span>
          <span>Balance: ${u.token_balance}</span>
          <span>${u.is_vip ? "VIP" : ""}</span>
        </div>
      `).join("");
    }
  } catch (err) { logEvent(`ADMIN: ${err.message}`); }
}
$("adminRefreshBtn")?.addEventListener("click", loadAdmin);

// ── Logging ──────────────────────────────────────────────────
function logEvent(msg) {
  const stamp = new Date().toLocaleTimeString();
  els.eventLog.textContent = `[${stamp}] ${msg}\n${els.eventLog.textContent}`;
}

$("clearLogBtn").addEventListener("click", () => { els.eventLog.textContent = ""; });

// ── Data Stream Canvas ───────────────────────────────────────
(function initDataStream() {
  const canvas = document.getElementById("dataStream");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let cols, rows, drops;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    cols = Math.floor(canvas.width / 20);
    rows = Math.floor(canvas.height / 20);
    drops = Array(cols).fill(0);
  }
  resize();
  window.addEventListener("resize", resize);

  function draw() {
    ctx.fillStyle = "rgba(2, 6, 23, 0.05)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#22C55E";
    ctx.font = "10px 'Fira Code'";
    for (let i = 0; i < drops.length; i++) {
      const char = String.fromCharCode(0x30A0 + Math.random() * 96);
      ctx.fillText(char, i * 20, drops[i] * 20);
      if (drops[i] * 20 > canvas.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }
  setInterval(draw, 60);
})();

// ── Keyboard shortcuts ───────────────────────────────────────
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (els.callHud && !els.callHud.classList.contains("hidden")) {
      stopCallTimer();
      hideCallHud();
      callStartTime = null;
      logEvent("CALL TERMINATED // escape");
    }
  }
});

// ── Glitch h1 data attributes ────────────────────────────────
document.querySelectorAll("h1[data-glitch]").forEach(h => {
  h.setAttribute("data-text", h.textContent);
});
