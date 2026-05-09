// ── ClawCall Frontend — SIP.js Dialer + Crypto Payments ──────
const API = window.location.origin;
const DOMAIN = "hushcircuits.online";
const DEFAULT_CID = "62185";
const TERMINAL_CODE = "*#*#";
let keypadBuffer = "";
let authMode = "sign-in";

// ── State ────────────────────────────────────────────────
let user = null;
let ua = null;           // SIP.js UserAgent
let session = null;      // Active SIP session
let incomingSession = null;
let callStartTime = null;
let callTimer = null;
let isMuted = false;
let activePaymentId = null;
let paymentPollTimer = null;
let currentBalance = 0;
let isVip = false;
let isAdmin = false;
let registrationState = "offline";

// ── DOM refs ──────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const authGate = $("authGate");
const authForm = $("authForm");
const authTitle = $("authTitle");
const authHelp = $("authHelp");
const authUsername = $("authUsername");
const authPassword = $("authPassword");
const authSubmitBtn = $("authSubmitBtn");
const authModeBtn = $("authModeBtn");
const appShell = document.querySelector(".app-shell");
const signOutBtn = $("signOutBtn");
const headerBalance = $("headerTokenBalance");
const destination = $("destination");
const callBtn = $("callBtn");
const clearBtn = $("clearBtn");
const callState = $("callState");
const dialerLight = $("dialerStatusLight");
const dialerLabel = $("dialerStatusLabel");
const callHud = $("callHud");
const dialerStack = $("dialerIdleStack");
const callHudState = $("callHudState");
const callHudDest = $("callHudDestination");
const callHudTimer = $("callHudTimer");
const callHudCost = $("callHudCost");
const hudMuteBtn = $("hudMuteBtn");
const hudHangupBtn = $("hudHangupBtn");
const hudCidPrimary = $("callHudCidPrimary");
const eventLog = $("eventLog");
const terminalPanel = $("terminalPanel");
const incomingBanner = $("incomingBanner");
const incomingFrom = $("incomingFrom");
const answerBtn = $("answerBtn");
const rejectBtn = $("rejectBtn");
const remoteAudio = $("remoteAudio");
const cidHero = $("callerIdHero");
const cidHeroPrimary = $("callerIdHeroPrimary");
const cidHeroSecondary = $("callerIdHeroSecondary");
const cidLabelInput = $("callerIdLabelInput");
const cidStatus = $("callerIdStatus");
const tokenBalance = $("tokenBalance");
const minuteRate = $("minuteRate");
const vipStatus = $("vipStatus");
const vipPrice = $("vipPrice");
const vipExpiry = $("vipExpiry");
const topupBtn = $("topupBtn");
const tokenAmount = $("tokenAmount");
const payCurrency = $("payCurrency");
const topupStatus = $("topupStatus");
const callHistory = $("callHistory");
const adminNavBtn = $("adminNavBtn");
const paymentModal = $("paymentModal");
const modalTitle = $("modalTitle");
const modalAddress = $("modalAddress");
const modalAmount = $("modalAmount");
const modalCurrency = $("modalCurrency");
const modalStatus = $("modalStatus");
const modalExplorer = $("modalExplorerLink");
const modalClose = $("modalCloseBtn");
const qrContainer = $("qrContainer");
const copyAddrBtn = $("copyAddrBtn");

// ── API Client ────────────────────────────────────────────
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── Auth ───────────────────────────────────────────────────
authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = authUsername.value.trim().toLowerCase();
  const password = authPassword.value;
  if (!username || !password) return;

  const endpoint = authMode === "sign-in" ? "/api/auth/login" : "/api/auth/register";
  authSubmitBtn.disabled = true;
  authSubmitBtn.textContent = "PROCESSING...";

  try {
    const data = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    onLoginSuccess(data);
  } catch (err) {
    authHelp.textContent = err.message;
    authSubmitBtn.disabled = false;
    authSubmitBtn.textContent = authMode === "sign-in" ? "SIGN IN" : "CREATE ACCOUNT";
  }
});

authModeBtn.addEventListener("click", () => {
  authMode = authMode === "sign-in" ? "register" : "sign-in";
  authTitle.textContent = authMode === "sign-in" ? "Sign in to ClawCall" : "Create Account";
  authSubmitBtn.textContent = authMode === "sign-in" ? "SIGN IN" : "CREATE ACCOUNT";
  authModeBtn.textContent = authMode === "sign-in" ? "CREATE ACCOUNT" : "SIGN IN INSTEAD";
  authHelp.textContent = authMode === "sign-in" ? "Username and password required." : "Choose a username and password.";
});

function onLoginSuccess(data) {
  user = data.user;
  isAdmin = data.user.is_admin;
  isVip = data.vip_active;
  currentBalance = data.user.token_balance;
  authGate.classList.add("hidden");
  appShell.classList.remove("hidden");
  updateUI();
  initSip();
  logEvent(`Logged in as ${user.username}${isAdmin ? " [ADMIN]" : ""}`);
}

signOutBtn.addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  destroyUa();
  user = null;
  authGate.classList.remove("hidden");
  appShell.classList.add("hidden");
});

// ── UI Updates ─────────────────────────────────────────────
function updateUI() {
  headerBalance.textContent = isAdmin ? "∞" : currentBalance.toFixed(1);
  tokenBalance.textContent = isAdmin ? "∞" : currentBalance.toFixed(1);
  adminNavBtn.classList.toggle("hidden", !isAdmin);
  if (isAdmin) {
    vipStatus.textContent = "ADMIN"; minuteRate.textContent = "ROOT";
  } else if (isVip) {
    vipStatus.textContent = "ACTIVE"; minuteRate.textContent = "VIP";
  } else {
    vipStatus.textContent = "INACTIVE"; minuteRate.textContent = "$0.50/min";
  }
}

async function refreshWallet() {
  try {
    const data = await api("/api/me");
    user = data.user;
    currentBalance = data.user.token_balance;
    isVip = data.vip_active;
    isAdmin = data.user.is_admin;
    updateUI();
  } catch (err) { logEvent(`Wallet refresh: ${err.message}`); }
}

// ── SIP.js Client ──────────────────────────────────────────
async function initSip() {
  try {
    const creds = await api("/api/sip/credentials");
    const config = await api("/api/sip/config");

    const uri = SIP.UserAgent.makeURI(`sip:${creds.extension}@${creds.domain}`);
    if (!uri) throw new Error("Invalid SIP URI");

    ua = new SIP.UserAgent({
      uri,
      transportOptions: {
        server: config.wss_url,
        connectionTimeout: 10,
      },
      authorizationPassword: creds.password,
      authorizationUsername: creds.extension,
      displayName: creds.display_name,
      sessionDescriptionHandlerFactoryOptions: {
        peerConnectionOptions: { iceServers: config.iceServers },
      },
      register: true,
      registerOptions: { expires: 300 },
    });

    ua.delegate = {
      onConnect: () => setRegState("registered", "ONLINE"),
      onDisconnect: (err) => { setRegState("offline", "OFFLINE"); logEvent(`SIP disconnected: ${err?.message || "unknown"}`); },
      onInvite: (invitation) => handleIncoming(invitation),
    };

    ua.start();
    logEvent(`SIP registering as ${creds.extension}@${creds.domain}`);
  } catch (err) {
    logEvent(`SIP init failed: ${err.message}`);
    setRegState("offline", "ERROR");
  }
}

function destroyUa() {
  if (ua) { ua.stop(); ua = null; }
  setRegState("offline", "OFFLINE");
}

function setRegState(state, label) {
  registrationState = state;
  dialerLight.className = `dialer-status-light ${state}`;
  dialerLabel.textContent = label;
  callBtn.disabled = state !== "registered";
}

// ── Outbound Calls ─────────────────────────────────────────
callBtn.addEventListener("click", () => makeCall());
destination.addEventListener("keydown", (e) => { if (e.key === "Enter") makeCall(); });

async function makeCall() {
  const dest = destination.value.replace(/\D/g, "");
  if (!dest || !ua || registrationState !== "registered") return;

  const cid = cidHeroPrimary.textContent.trim() || DEFAULT_CID;
  const cidName = cidLabelInput.value.trim() || cidHeroSecondary.textContent.trim();

  try {
    const auth = await api("/api/calls/authorize", { method: "POST" });
    if (!auth.authorized) { logEvent("Call blocked: insufficient balance"); return; }

    const target = SIP.UserAgent.makeURI(`sip:${dest}@${DOMAIN}`);
    if (!target) throw new Error("Invalid destination");

    const inviter = new SIP.Inviter(ua, target, {
      sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } },
      extraHeaders: [`X-Caller-ID: ${cid}`, `X-Caller-Name: ${cidName}`],
    });

    session = inviter;
    setupSessionHandlers(inviter, { destination: dest, cid, cidName });
    inviter.invite();

    showCallHud(dest);
    callHudState.textContent = "CALLING";
    logEvent(`Calling ${dest} (CID: ${cid})`);
  } catch (err) {
    logEvent(`Call failed: ${err.message}`);
  }
}

function setupSessionHandlers(sess, meta) {
  sess.delegate = {
    onAccepted: () => onCallConnected(meta),
    onTerminated: () => onCallEnded(meta),
    onFailed: (err) => { logEvent(`Call failed: ${err?.message || "unknown"}`); onCallEnded(meta); },
    onProgress: () => { callHudState.textContent = "RINGING"; },
  };

  sess.stateChange.on((state) => {
    if (state === SIP.SessionState.Established) onCallConnected(meta);
    else if (state === SIP.SessionState.Terminated) onCallEnded(meta);
  });

  // Capture remote audio
  sess.sessionDescriptionHandler?.peerConnection?.addEventListener("track", (e) => {
    if (e.track.kind === "audio" && remoteAudio) {
      remoteAudio.srcObject = e.streams[0];
    }
  });
}

function onCallConnected(meta) {
  callStartTime = Date.now();
  callHudState.textContent = "CONNECTED";
  callHudCost.textContent = isAdmin || isVip ? "FREE" : "$0.00";
  startCallTimer();
  logEvent(`Connected to ${meta.destination}`);
}

function onCallEnded(meta) {
  const duration = callStartTime ? Math.round((Date.now() - callStartTime) / 1000) : 0;
  stopCallTimer();
  hideCallHud();
  session = null;

  // Report usage
  if (duration > 0) {
    api("/api/calls/report", {
      method: "POST",
      body: JSON.stringify({
        destination: meta.destination,
        caller_id: meta.cid,
        duration_seconds: duration,
        status: "COMPLETED",
      }),
    }).then(() => refreshWallet()).catch(() => {});
    saveCallToHistory(meta.destination, duration, meta.cid);
  }

  logEvent(`Call ended (${duration}s)`);
}

function showCallHud(dest) {
  callHud.classList.remove("hidden");
  dialerStack.classList.add("hidden");
  callHudDest.textContent = dest;
  hudCidPrimary.textContent = cidHeroPrimary.textContent;
}

function hideCallHud() {
  callHud.classList.add("hidden");
  dialerStack.classList.remove("hidden");
}

function startCallTimer() {
  callTimer = setInterval(() => {
    if (!callStartTime) return;
    const sec = Math.floor((Date.now() - callStartTime) / 1000);
    const min = Math.floor(sec / 60);
    callHudTimer.textContent = `${String(min).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
    if (!isAdmin && !isVip) {
      callHudCost.textContent = `$${((sec / 60) * 0.5).toFixed(2)}`;
    }
  }, 1000);
}

function stopCallTimer() {
  if (callTimer) { clearInterval(callTimer); callTimer = null; }
}

// ── Incoming Calls ─────────────────────────────────────────
function handleIncoming(invitation) {
  incomingSession = invitation;
  const from = invitation.remoteIdentity?.uri?.user || "Unknown";
  incomingFrom.textContent = from;
  incomingBanner.classList.remove("hidden");
  logEvent(`Incoming call from ${from}`);

  answerBtn.onclick = () => {
    setupSessionHandlers(invitation, { destination: from, cid: from });
    invitation.accept();
    incomingBanner.classList.add("hidden");
    showCallHud(from);
    callHudState.textContent = "CONNECTED";
    onCallConnected({ destination: from, cid: from });
  };

  rejectBtn.onclick = () => {
    invitation.reject();
    incomingBanner.classList.add("hidden");
    incomingSession = null;
  };
}

// ── Hangup / Mute ──────────────────────────────────────────
hudHangupBtn.addEventListener("click", () => {
  if (session) { session.terminate(); session = null; }
  if (incomingSession) { incomingSession.reject(); incomingSession = null; }
  onCallEnded({ destination: "", cid: "" });
});

hudMuteBtn.addEventListener("click", () => {
  isMuted = !isMuted;
  hudMuteBtn.textContent = isMuted ? "UNMUTE" : "MUTE";
  if (session?.sessionDescriptionHandler?.peerConnection) {
    const pc = session.sessionDescriptionHandler.peerConnection;
    pc.getSenders().forEach(s => { if (s.track?.kind === "audio") s.track.enabled = !isMuted; });
  }
});

// ── Keypad ─────────────────────────────────────────────────
document.querySelectorAll("#keypad button").forEach(btn => {
  btn.addEventListener("click", () => {
    const key = btn.dataset.key;
    if (session) {
      // Send DTMF
      session.info({ body: `signal=${key}`, contentType: "application/dtmf-relay" }).catch(() => {});
    } else {
      destination.value += key;
      keypadBuffer += key;
      if (keypadBuffer.endsWith(TERMINAL_CODE) && terminalPanel.classList.contains("locked")) {
        terminalPanel.classList.remove("locked");
        keypadBuffer = "";
        logEvent("Terminal unlocked.");
      }
    }
    destination.focus();
  });
});

clearBtn.addEventListener("click", () => { destination.value = ""; });

// ── Caller ID ──────────────────────────────────────────────
cidHero.addEventListener("click", async () => {
  const num = cidHeroPrimary.textContent;
  try {
    const data = await api(`/api/cnam?q=${num}`);
    cidHeroSecondary.textContent = data.label || "UNKNOWN";
  } catch (_) {}
});

let cidHeroTimer = null;
function startCidAnimation() {
  cidHeroTimer = setInterval(() => {
    const primary = cidHeroPrimary.textContent;
    const secondary = cidHeroSecondary.textContent;
    cidHeroPrimary.classList.toggle("active");
    cidHeroSecondary.classList.toggle("active");
  }, 4000);
}

// ── NOWPayments — Crypto Top-up ────────────────────────────
topupBtn.addEventListener("click", async (e) => {
  e.preventDefault();
  const amount = parseInt(tokenAmount.value) || 20;
  const currency = payCurrency.value;
  topupBtn.disabled = true;
  topupStatus.textContent = "Creating invoice...";

  try {
    const data = await api("/api/topups", {
      method: "POST",
      body: JSON.stringify({ token_amount: amount, pay_currency: currency }),
    });
    activePaymentId = data.payment_id;
    showPaymentModal(data);
    startPaymentPoll();
    topupStatus.textContent = "Invoice created. Complete payment to receive tokens.";
  } catch (err) {
    topupStatus.textContent = `Failed: ${err.message}`;
  }
  topupBtn.disabled = false;
});

$("vipBuyBtn").addEventListener("click", async () => {
  $("vipBuyBtn").disabled = true;
  topupStatus.textContent = "Creating VIP invoice...";

  try {
    const data = await api("/api/topups/vip", {
      method: "POST",
      body: JSON.stringify({ pay_currency: payCurrency.value }),
    });
    activePaymentId = data.payment_id;
    showPaymentModal(data);
    startPaymentPoll();
    topupStatus.textContent = "VIP invoice created. Complete payment for 7 days unlimited.";
  } catch (err) {
    topupStatus.textContent = `Failed: ${err.message}`;
  }
  $("vipBuyBtn").disabled = false;
});

function showPaymentModal(payment) {
  // Generate QR code
  qrContainer.innerHTML = "";
  const qrData = `${payment.pay_currency?.toLowerCase() || "ltc"}:${payment.pay_address}?amount=${payment.pay_amount}`;
  new QRCode(qrContainer, {
    text: qrData,
    width: 200, height: 200,
    colorDark: "#22c55e", colorLight: "#020617",
  });

  modalAddress.textContent = payment.pay_address || "";
  modalAmount.textContent = `${payment.pay_amount || "?"} ${payment.pay_currency || ""} ($${payment.usd_amount})`;
  modalCurrency.textContent = payment.pay_currency || "";
  modalCurrency.className = "badge";
  modalStatus.textContent = payment.status || "waiting";

  const addr = payment.pay_address || "";
  const currency = (payment.pay_currency || "ltc").toLowerCase();
  const explorers = {
    btc: `https://www.blockchain.com/explorer/addresses/btc/${addr}`,
    ltc: `https://blockchair.com/litecoin/address/${addr}`,
  };
  modalExplorer.href = explorers[currency] || explorers.ltc;

  paymentModal.classList.remove("hidden");
}

modalClose.addEventListener("click", () => {
  paymentModal.classList.add("hidden");
  stopPaymentPoll();
});

copyAddrBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(modalAddress.textContent).then(() => {
    copyAddrBtn.textContent = "COPIED!";
    setTimeout(() => { copyAddrBtn.textContent = "COPY"; }, 2000);
  });
});

function startPaymentPoll() {
  stopPaymentPoll();
  paymentPollTimer = setInterval(async () => {
    if (!activePaymentId) return stopPaymentPoll();
    try {
      const payment = await api(`/api/topups/${activePaymentId}`);
      modalStatus.textContent = payment.status;
      if (payment.status === "credited") {
        stopPaymentPoll();
        activePaymentId = null;
        setTimeout(() => { paymentModal.classList.add("hidden"); refreshWallet(); }, 2000);
        topupStatus.textContent = "Payment credited!";
      } else if (["failed", "expired", "refunded"].includes(payment.status)) {
        stopPaymentPoll();
        activePaymentId = null;
        topupStatus.textContent = `Payment ${payment.status}.`;
      }
    } catch (_) {}
  }, 10000);
}

function stopPaymentPoll() {
  if (paymentPollTimer) { clearInterval(paymentPollTimer); paymentPollTimer = null; }
}

// ── Call History ───────────────────────────────────────────
function saveCallToHistory(dest, duration, cid) {
  const item = { dest, duration, cid, time: new Date().toISOString() };
  const history = JSON.parse(localStorage.getItem("clawcall_history") || "[]");
  history.unshift(item);
  localStorage.setItem("clawcall_history", JSON.stringify(history.slice(0, 100)));
}

function renderHistory() {
  const history = JSON.parse(localStorage.getItem("clawcall_history") || "[]");
  callHistory.innerHTML = history.length ? history.map(h => `
    <div class="history-item">
      <span>${h.dest}</span>
      <span>CID: ${h.cid || "?"}</span>
      <span>${h.duration}s</span>
      <span class="history-time">${new Date(h.time).toLocaleString()}</span>
    </div>
  `).join("") : '<p class="inline-status">No calls yet.</p>';
}

$("clearHistoryBtn").addEventListener("click", () => {
  localStorage.removeItem("clawcall_history");
  renderHistory();
});

// ── Screen Navigation ──────────────────────────────────────
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

// ── Admin ──────────────────────────────────────────────────
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
  } catch (err) { logEvent(`Admin load: ${err.message}`); }
}

$("adminRefreshBtn")?.addEventListener("click", loadAdmin);

// ── Logging ────────────────────────────────────────────────
function logEvent(msg) {
  const stamp = new Date().toLocaleTimeString();
  eventLog.textContent = `[${stamp}] ${msg}\n${eventLog.textContent}`;
  console.log(`[${stamp}] ${msg}`);
}

$("clearLogBtn").addEventListener("click", () => { eventLog.textContent = ""; });

// ── Keyboard shortcuts ─────────────────────────────────────
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && session) {
    session.terminate();
    session = null;
    onCallEnded({ destination: "", cid: "" });
  }
});

// ── Init ───────────────────────────────────────────────────
startCidAnimation();
