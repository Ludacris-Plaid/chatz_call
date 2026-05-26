#!/usr/bin/env python3
"""
ClawCall Backend — Standalone VoIP + Crypto Payment Server
hushcircuits.online | Asterisk 22 | SIP.UP | Local SQLite | NOWPayments
Python stdlib only + bcrypt. No frameworks.
"""
import json, os, time, uuid, base64, hashlib, hmac, secrets, logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from http.cookies import SimpleCookie
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs
from urllib.error import HTTPError, URLError
from pathlib import Path
from caller_id import set_caller_id, get_caller_id, originate_call
from supabase_auth import register_user, login_user, validate_session, get_user_profile as local_get_profile, logout as local_logout
from supabase_data import (get_profile, update_profile, get_all_calls, get_next_sip_extension as local_next_ext,
    list_profiles, get_all_profiles, log_transaction, log_call, log_payment,
    get_user_transactions, get_user_calls, get_user_payments, get_payment, update_payment, init_tables,
    get_vip_days_left)
import supabase_voucher as voucher_system
import subprocess, socket
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────
DOMAIN          = os.environ.get("DOMAIN", "hushcircuits.online")
PUBLIC_IP       = os.environ.get("PUBLIC_IP", "18.223.24.42")
API_PORT        = int(os.environ.get("API_PORT", "8090"))
# Supabase removed — using local SQLite
NOWPAYMENTS_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "9ED8ZNB-1ZNMGM6-J92MPHH-BA68DV7")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
TOKEN_PRICE     = float(os.environ.get("PRICE_PER_MINUTE", "0.50"))
AMI_HOST        = os.environ.get("AMI_HOST", "172.18.0.1")
AMI_PORT        = int(os.environ.get("AMI_PORT", "5038"))
AMI_USER        = os.environ.get("AMI_USER", "clawcall")
AMI_SECRET      = os.environ.get("AMI_SECRET", "clawcall_ami_secret_2026")
VIP_PRICE       = float(os.environ.get("VIP_WEEKLY_PRICE", "250.00"))
SIPUP_USER      = os.environ.get("SIPUP_USERNAME", "10428")
SIPUP_PASS      = os.environ.get("SIPUP_PASSWORD", "Mcjhv877KAK9")
TWILIO_SID      = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
COOKIE_SECRET   = os.environ.get("COOKIE_SECRET", secrets.token_hex(32))
ADMIN_USERNAME  = os.environ.get("ADMIN_USERNAME", "dysthemix").strip().lower()

PAYMENT_COINS   = {"btc", "ltc"}
SIP_EXT_START   = 1000
STATIC_DIR      = Path(__file__).parent / "frontend"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("clawcall")
# File handler for admin log viewer
try:
    fh = logging.FileHandler("/tmp/clawcall.log", mode="a")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)
except Exception:
    pass

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ── In-memory session store ──────────────────────────────────────
sessions = {}  # token -> {"user_id": str, "username": str, "expires": float}
SESSION_TTL = 86400  # 24 hours

# ── Helpers ───────────────────────────────────────────────────────
# Supabase removed — using clawcall_data module

def nowpayments_request(path, body=None):
    """Call NOWPayments API."""
    headers = {**BROWSER_HEADERS, "x-api-key": NOWPAYMENTS_KEY}
    url = f"https://api.nowpayments.io/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method="POST" if body else "GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        raise ValueError(f"NOWPayments {path} failed: {e.code} {body_text[:200]}")

def sign_cookie(payload):
    """Create a signed session cookie value."""
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(COOKIE_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"

def verify_cookie(value):
    """Verify and decode a signed cookie. Returns dict or None."""
    try:
        raw, sig = value.rsplit(".", 1)
        expected = hmac.new(COOKIE_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw))
        if data.get("expires", 0) < time.time():
            return None
        return data
    except Exception:
        return None

# get_user_profile, update_profile, get_next_sip_extension are now in clawcall_data module
# Local wrappers for backward compat
def get_user_profile(user_id):
    return get_profile(user_id)

# update_profile is imported directly from clawcall_data

def get_next_sip_extension():
    return local_next_ext()

def parse_cookies(header_value):
    """Parse Cookie header into dict."""
    cookies = {}
    if header_value:
        for item in header_value.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookies[k.strip()] = v.strip()
    return cookies

def set_cookie_headers(session_data):
    """Generate Set-Cookie headers."""
    expires = time.time() + SESSION_TTL
    session_data["expires"] = expires
    cookie_val = sign_cookie(session_data)
    cookie = (
        f"clawcall_session={cookie_val}; "
        f"HttpOnly; Path=/; Max-Age={SESSION_TTL}; SameSite=Lax"
    )
    return [("Set-Cookie", cookie)]

# ── CORS ─────────────────────────────────────────────────────────
def cors_headers():
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
        ("Access-Control-Allow-Credentials", "true"),
    ]

# ═══════════════════════════════════════════════════════════════════
#  HTTP REQUEST HANDLER
# ═══════════════════════════════════════════════════════════════════
def create_pjsip_endpoint(extension, password):
    """Create PJSIP WebRTC endpoint in pjsip.conf + reload via AMI.
    Idempotent — skips if auth block already exists.
    Writes to /asterisk-config/pjsip.conf (shared volume with Asterisk container)."""
    conf_path = "/asterisk-config/pjsip.conf"
    
    # Guard: remove old block if it exists (so we can recreate with current password)
    try:
        with open(conf_path) as f:
            existing = f.read()
        if f"[auth{extension}]" in existing:
            log.info(f"PJSIP endpoint {extension} exists — removing old block to recreate")
            import re
            pattern = "\n; Extension " + re.escape(extension) + ".*?aors=" + re.escape(extension) + "-aor\n"
            existing = re.sub(pattern, "", existing, flags=re.DOTALL)
            with open(conf_path, "w") as f2:
                f2.write(existing)
    except FileNotFoundError:
        pass
    
    # Build config block — inherits from [webrtc-template] macro
    block = f"""
; Extension {extension} — auto-created by backend
[auth{extension}]
type=auth
auth_type=userpass
password={password}
username={extension}
realm=asterisk

[{extension}-aor]
type=aor
max_contacts=3

[{extension}](webrtc-template)
auth=auth{extension}
aors={extension}-aor
"""
    
    try:
        with open(conf_path, "a") as f:
            f.write(block)
        log.info(f"Wrote PJSIP endpoint {extension} to {conf_path}")
    except Exception as e:
        log.error(f"Failed to write PJSIP config for {extension}: {e}")
        return False
    
    # Reload via AMI
    try:
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((AMI_HOST, AMI_PORT))
        sock.recv(1024)  # banner
        cr = chr(13) + chr(10)
        sock.send(f"Action: Login{cr}Username: {AMI_USER}{cr}Secret: {AMI_SECRET}{cr}{cr}".encode())
        time.sleep(0.2)
        sock.recv(1024)  # login response
        sock.send(f"Action: Command{cr}Command: module reload res_pjsip.so{cr}{cr}".encode())
        time.sleep(0.5)
        sock.recv(4096)
        sock.close()
        log.info(f"AMI: reloaded PJSIP after creating endpoint {extension}")
    except Exception as e:
        log.warning(f"AMI reload failed (endpoint {extension} written but not reloaded): {e}")
        # Don't fail — endpoint is written, will load on next restart
    
    return True
    
    return True


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads."""
    daemon_threads = True




# ── App-Wide Event Log (ring buffer) ──
EVENT_LOG = []
MAX_EVENTS = 200

def log_event(event_type, message, detail=""):
    """Push an event onto the in-memory ring buffer."""
    from datetime import datetime, timezone
    entry = {
        "type": event_type,
        "message": message,
        "detail": str(detail)[:200] if detail else "",
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
    }
    EVENT_LOG.append(entry)
    if len(EVENT_LOG) > MAX_EVENTS:
        EVENT_LOG.pop(0)

class ClawCallHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        log.info(f"{self.client_address[0]} - {args[0]}")

    def _send_json(self, data, status=200, extra_headers=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for h, v in cors_headers():
            self.send_header(h, v)
        if extra_headers:
            for h, v in extra_headers:
                self.send_header(h, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, status=400):
        self._send_json({"ok": False, "error": message}, status)
        return True  # Signal that response was already sent

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def _get_session(self):
        """Extract and verify session from cookie."""
        cookie_header = self.headers.get("Cookie", "")
        cookies = parse_cookies(cookie_header)
        session_cookie = cookies.get("clawcall_session", "")
        if not session_cookie:
            return None
        return verify_cookie(session_cookie)

    def _get_auth_token(self):
        """Extract Bearer token from Authorization header."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _require_auth(self):
        """Get authenticated user. Returns (user_data, error_response)."""
        # Try Bearer token first
        token = self._get_auth_token()
        if token:
            session = validate_session(token)
            if session:
                user_id = session["user_id"]
                username = session["username"]
                profile = local_get_profile(user_id) or {}
                return ({"user_id": user_id, "username": username, **profile}, None)

        # Fall back to cookie
        session = self._get_session()
        if not session:
            return None, self._send_error("Authentication required", 401)
        profile = get_profile(session["user_id"])
        if not profile:
            return None, self._send_error("User not found", 401)
        if profile.get("is_banned"):
            return None, self._send_error("Account banned", 403)
        return profile, None

    # ── Routing ───────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        for h, v in cors_headers():
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        # Static file serving
        if path == "/" or path == "":
            return self._serve_static("index.html")
        if path.startswith("/static/") or path.endswith((".js", ".css", ".html", ".png", ".svg", ".ico", ".woff2")):
            return self._serve_static(path.lstrip("/"))

        # API routes
        routes = {
            "/api/me": self._handle_me,
            "/api/calls/authorize": self._handle_authorize_call,
            "/api/sip/credentials": self._handle_sip_credentials,
            "/api/sip/config": self._handle_sip_config,
            "/api/sip/status": self._handle_sip_status,
            "/api/cnam": self._handle_cnam,
            "/api/topups/": self._handle_poll_topup,
            "/api/payments/webhook": self._handle_nowpayments_webhook,
            "/api/admin/users": self._handle_admin_users,
            "/api/admin/stats": self._handle_admin_stats,
            "/api/admin/vouchers": self._handle_admin_vouchers,
            "/api/admin/call-analytics": self._handle_admin_call_analytics,
            "/api/admin/logs": self._handle_admin_logs,
            "/api/admin/deposits": self._handle_admin_deposits,
            "/api/admin/cleanup-deposits": self._handle_admin_cleanup_deposits,
            "/api/admin/user-detail": self._handle_admin_user_detail,
            "/api/wallet": self._handle_wallet,
            "/api/admin/overview": self._handle_admin_overview,
            "/api/calls/history": self._handle_call_history,
            "/api/calls/export": self._handle_call_export,
            "/api/stats": self._handle_stats,
        }

        for route, handler in routes.items():
            if path == route or path.startswith(route):
                return handler()

        self._send_error("Not found", 404)

    def do_POST(self):
        path = urlparse(self.path).path

        routes = {
            "/api/auth/register": self._handle_register,
            "/api/auth/login": self._handle_login,
            "/api/auth/logout": self._handle_logout,
            "/api/calls/report": self._handle_report_call,
            "/api/payments/webhook": self._handle_nowpayments_webhook,
            "/api/call/hangup": self._handle_hangup_call,
            "/api/topups/vip": self._handle_create_vip,
            "/api/topups": self._handle_create_topup,
            "/api/admin/": self._handle_admin_action,
            "/api/admin/restart-asterisk": self._handle_admin_restart_asterisk,
            "/api/admin/reload-config": self._handle_admin_reload_config,
            "/api/admin/restart-backend": self._handle_admin_restart_backend,
            "/api/vouchers/redeem": self._handle_redeem_voucher,
            "/api/caller-id": self._handle_set_caller_id,
            "/api/call": self._handle_originate_call,
        }

        for route, handler in routes.items():
            if path == route or path.startswith(route):
                return handler()

        self._send_error("Not found", 404)

    def do_PATCH(self):
        """Handle PATCH requests — admin user updates."""
        path = urlparse(self.path).path
        if path.startswith("/api/admin/users/"):
            return self._handle_admin_user_patch()
        self._send_error("Not found", 404)

    def _serve_static(self, filepath):
        """Serve static files from frontend directory."""
        full_path = STATIC_DIR / filepath.lstrip("/")
        content_types = {
            ".html": "text/html", ".js": "application/javascript",
            ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
            ".json": "application/json", ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }
        ext = Path(filepath).suffix
        content_type = content_types.get(ext, "application/octet-stream")

        try:
            with open(full_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for h, v in cors_headers():
                self.send_header(h, v)
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._send_error("File not found", 404)

    # ── AUTH HANDLERS ──────────────────────────────────────────

    def _handle_register(self):
        body = self._read_body()
        username = str(body.get("username", "")).strip().lower()
        password = str(body.get("password", ""))

        if not username or len(username) < 3:
            return self._send_error("Username must be at least 3 characters")
        if not password or len(password) < 6:
            return self._send_error("Password must be at least 6 characters")
        if not all(c.isalnum() or c == '_' for c in username):
            return self._send_error("Username must be alphanumeric (underscores allowed)")

        result = register_user(username, password)
        if not result.get("ok"):
            if "already taken" in str(result.get("error", "")).lower():
                return self._send_error("Username already taken", 409)
            return self._send_error(f"Registration failed: {result.get('error')}")

        user_id = result["user_id"]
        login_result = login_user(username, password)
        if not login_result.get("ok"):
            return self._send_error("Account created but login failed")

        token = login_result["token"]
        profile = local_get_profile(user_id) or {}
        self._send_json({
            "ok": True,
            "user": {
                "id": user_id,
                "username": username,
                "token_balance": float(profile.get("token_balance", 0)),
                "is_admin": profile.get("role") == "admin" or username == ADMIN_USERNAME,
                "is_vip": False,
            },
            "token": token,
        }, 201)

    def _handle_login(self):
        body = self._read_body()
        username = str(body.get("username", "")).strip().lower()
        password = str(body.get("password", ""))

        if not username or not password:
            return self._send_error("Username and password required")

        result = login_user(username, password)
        if not result.get("ok"):
            return self._send_error("Invalid username or password", 401)

        user_id = result["user_id"]
        token = result["token"]
        profile = local_get_profile(user_id) or {}

        # Update last_active timestamp
        update_profile(user_id, {"updated_at": "now()"})

        self._send_json({
            "ok": True,
            "user": {
                "id": user_id,
                "username": username,
                "token_balance": float(profile.get("token_balance", 0)),
                "is_admin": profile.get("role") == "admin" or username == ADMIN_USERNAME,
                "is_vip": False,
            },
            "token": token,
        }, 200)

    def _handle_logout(self):
        expire_cookie = ("Set-Cookie", "clawcall_session=; HttpOnly; Path=/; Max-Age=0")
        self._send_json({"ok": True}, 200, [expire_cookie])

    def _handle_me(self):
        profile, err = self._require_auth()
        if err:
            return err
        username = profile.get("username", "unknown")
        is_vip = bool(profile.get("is_vip"))
        
        # Calculate real VIP days remaining from transactions
        vip_days = 0
        vip_expires = None
        if is_vip:
            user_id = profile["id"]
            try:
                vip_days, vip_expires = get_vip_days_left(user_id)
            except Exception:
                pass

        # Get total calls count
        from supabase_data import get_user_calls
        user_calls = get_user_calls(profile["id"], 1000)
        total_user_calls = len(user_calls)
        
        self._send_json({
            "ok": True,
            "user": {
                "id": profile["id"],
                "username": username,
                "token_balance": float(profile.get("token_balance", 0)),
                "is_admin": profile.get("role") == "admin",
                "is_vip": is_vip,
                "vip_expires_at": vip_expires,
                "vip_days_left": vip_days,
                "account_status": "banned" if profile.get("is_banned") else ("suspended" if profile.get("is_suspended") else "active"),
                "caller_id": profile.get("caller_id", ""),
                "sip_extension": profile.get("sip_extension"),
                "sip_password": profile.get("sip_password"),
                "created_at": profile.get("created_at", ""),
                "total_calls": total_user_calls,
            },
            "vip_active": is_vip,
            "vip_days_left": vip_days,
            "vip_expires_at": vip_expires,
            "rate_per_minute_usd": TOKEN_PRICE,
            "vip_price_usd": VIP_PRICE,
        })

    # ── SIP / CALL HANDLERS ─────────────────────────────────────

    def _handle_sip_credentials(self):
        profile, err = self._require_auth()
        if err:
            return err

        # Each user gets a UNIQUE extension — assigned on first request, persisted in Supabase
        sip_ext = str(profile.get("sip_extension") or "")
        sip_pass = str(profile.get("sip_password") or "")
        
        if not sip_ext or sip_ext == "None" or not sip_pass or sip_pass == "None":
            sip_ext = get_next_sip_extension()
            sip_pass = secrets.token_hex(8)
            update_profile(profile["id"], {"sip_extension": sip_ext, "sip_password": sip_pass})
            log.info(f"Assigned new SIP extension {sip_ext} to user {profile['username']}")
        
        # Always ensure Asterisk endpoint exists (idempotent — skips if already present)
        try:
            create_pjsip_endpoint(sip_ext, sip_pass)
        except Exception as e:
            log.error(f"Failed to ensure PJSIP endpoint {sip_ext}: {e}")
            return self._send_error("Failed to provision SIP endpoint", 500)
        self._send_json({
        
            "ok": True,
            "extension": sip_ext,
            "password": sip_pass,
            "domain": DOMAIN,
            "wss_url": f"wss://{DOMAIN}/ws",
            "display_name": profile.get("username", "User"),
        })

    def _handle_sip_config(self):
        """Public SIP config for non-authenticated clients (ICE servers)."""
        self._send_json({
            "ok": True,
            "domain": DOMAIN,
            "wss_url": f"wss://{DOMAIN}/ws",
            "iceServers": [
                {"urls": f"stun:{DOMAIN}:3478"},
                {"urls": "stun:stun.l.google.com:19302"},
            ],
        })

    def _handle_sip_status(self):
        """Return SIP trunk status for frontend indicator."""
        try:
            # Use raw AMI connection for Command action — ami_command() truncates at Response:
            import socket
            s = socket.socket()
            s.settimeout(10)
            s.connect((os.environ.get("AMI_HOST", "172.18.0.1"), int(os.environ.get("AMI_PORT", "5038"))))
            s.recv(4096)  # Banner
            s.sendall(f"Action: Login\r\nUsername: {os.environ.get('AMI_USER', 'clawcall')}\r\nSecret: {os.environ.get('AMI_SECRET', 'clawcall_ami_secret_2026')}\r\n\r\n".encode())
            login_resp = s.recv(4096).decode()
            if "Success" not in login_resp:
                s.close()
                return self._send_json({"ok": True, "registered": False, "status": "offline", "trunk": "sipup"})
            s.sendall(b"Action: Command\r\nCommand: pjsip show registrations\r\n\r\n")
            output = ""
            while True:
                try:
                    chunk = s.recv(4096).decode()
                    if not chunk: break
                    output += chunk
                    if "--END COMMAND--" in output or "Objects found" in output:
                        break
                except socket.timeout:
                    break
            s.close()
            registered = "Registered" in output
            status = "online" if registered else "offline"
            log.info(f"SIP status: registered={registered}, output_len={len(output)}")
            return self._send_json({
                "ok": True, "registered": registered,
                "status": status, "trunk": "sipup"
            })
        except Exception as e:
            log.error(f"SIP status error: {e}")
        return self._send_json({"ok": True, "registered": False, "status": "unknown", "trunk": "sipup"})

    def _handle_authorize_call(self):
        profile, err = self._require_auth()
        if err:
            return err
        if profile is None:
            return self._send_error("Invalid session", 401)

        is_vip = bool(profile.get("is_vip"))
        is_admin = profile.get("role") == "admin"
        balance = float(profile.get("token_balance", 0))
        can_call = is_admin or is_vip or balance > 0

        if profile.get("is_suspended") or profile.get("is_banned"):
            can_call = False

        self._send_json({
            "ok": True,
            "authorized": can_call,
            "vip_active": is_vip,
            "admin_unlimited": is_admin,
        })

    def _handle_report_call(self):
        profile, err = self._require_auth()
        if err:
            return err

        body = self._read_body()
        duration = max(0, int(body.get("duration_seconds", 0) or 0))
        status = str(body.get("status", "")).upper()
        caller_id = str(body.get("caller_id", ""))
        destination = str(body.get("destination", ""))
        fs_uuid = str(body.get("freeswitch_uuid", ""))

        is_vip = bool(profile.get("is_vip"))
        is_admin = profile.get("role") == "admin"
        billed = 0.0

        if not is_admin and not is_vip and status == "COMPLETED":
            # Rate: TOKEN_PRICE per minute (default $0.50/min)
            # Minimum 1 minute billing — even sub-60s calls bill the full minute
            effective_minutes = max(1.0, duration / 60)
            billed = round(effective_minutes * TOKEN_PRICE, 4)
            # Deduct tokens
            balance = float(profile.get("token_balance", 0))
            new_balance = max(0, balance - billed)
            update_profile(profile["id"], {"token_balance": new_balance})

            # Record transaction locally
            log_transaction(profile["id"], -billed, "call_deduction", new_balance,
                          f"Call to {destination} ({duration}s)")

        # Record call
        try:
            log_call(profile["id"], caller_id, destination, duration, billed, "completed" if status == "COMPLETED" else "failed")
        except Exception as e:
            log.warning(f"Failed to record call: {e}")

        self._send_json({
            "ok": True,
            "billed_tokens": billed,
            "user": {
                "id": profile["id"],
                "token_balance": new_balance if not is_admin and not is_vip else float(profile.get("token_balance", 0)),
            },
            "vip_active": is_vip,
            "admin_unlimited": is_admin,
        })

    # ── CNAM LOOKUP ─────────────────────────────────────────────
    # Cache in SQLite to avoid freecnam.org rate limits.
    # Rate limit: 1 API call per 2 seconds max.

    def _handle_cnam(self):
        query = parse_qs(urlparse(self.path).query)
        number = query.get("q", [""])[0].strip()
        if not number:
            return self._send_error("Missing q parameter")

        digits = "".join(ch for ch in number if ch.isdigit())
        if not digits or len(digits) not in {10, 11}:
            return self._send_json({"ok": True, "number": digits, "label": "UNKNOWN"})

        # Check cache first (Supabase)
        cached = None
        try:
            from urllib.request import Request as Rq, urlopen as uo
            cache_url = f"{os.environ.get('SUPABASE_URL', 'https://kgnwqwghnosgieldiokc.supabase.co')}/rest/v1/clawcall_cnam_cache?number=eq.{digits}&select=*"
            cache_req = Rq(cache_url, headers={
                "apikey": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
                "Authorization": f"Bearer {os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')}"
            })
            with uo(cache_req, timeout=5) as resp:
                cache_data = json.loads(resp.read())
            if cache_data:
                cached = cache_data[0]
                return self._send_json({"ok": True, "number": digits, "label": cached.get("name", "UNKNOWN"), "cached": True})
        except Exception:
            pass  # Cache miss — fall through to API

        # Rate limit: enforce 2 seconds between API calls (Supabase)
        try:
            from urllib.request import Request as Rq, urlopen as uo
            supa_url = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            supa_hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}
            
            # Check current rate limit
            rl_req = Rq(f"{supa_url}/rest/v1/api_rate_limit?endpoint=eq.freecnam&select=*", headers=supa_hdrs)
            with uo(rl_req, timeout=5) as resp:
                rl_data = json.loads(resp.read())
            
            now = time.time()
            if rl_data and (now - rl_data[0].get("last_call", 0)) < 2.0:
                return self._send_json({"ok": True, "number": digits, "label": "UNKNOWN", "rate_limited": True})
            
            # Upsert rate limit
            import json as _json
            if rl_data:
                rl_body = _json.dumps({"last_call": now}).encode()
                rl_patch = Rq(f"{supa_url}/rest/v1/api_rate_limit?endpoint=eq.freecnam", data=rl_body, headers={**supa_hdrs, "Content-Type": "application/json"}, method="PATCH")
                uo(rl_patch, timeout=5)
            else:
                rl_body = _json.dumps({"endpoint": "freecnam", "last_call": now}).encode()
                rl_post = Rq(f"{supa_url}/rest/v1/api_rate_limit", data=rl_body, headers={**supa_hdrs, "Content-Type": "application/json", "Prefer": "return=minimal"}, method="POST")
                uo(rl_post, timeout=5)
        except Exception:
            pass  # If rate limit DB fails, proceed anyway

        # ── Primary: freecnam.org (free, US+CA) ──
        label = None
        try:
            req = Request(
                f"https://freecnam.org/dip?q={digits}",
                headers={**BROWSER_HEADERS},
            )
            with urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
            if raw and raw != "UNKNOWN" and not raw.startswith("ERR"):
                label = raw
        except Exception:
            pass

        # ── Fallback: Twilio Lookup v2 (paid, US only) ──
        if not label and TWILIO_SID and TWILIO_TOKEN:
            try:
                e164 = f"+1{digits}" if len(digits) == 10 else f"+{digits}"
                creds = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
                req = Request(
                    f"https://lookups.twilio.com/v2/PhoneNumbers/{e164}?Fields=caller_name",
                    headers={**BROWSER_HEADERS, "Authorization": f"Basic {creds}"},
                )
                with urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                cn = data.get("caller_name") or {}
                if cn.get("caller_name") and not cn.get("error_code"):
                    label = cn["caller_name"]
            except Exception:
                pass

        if not label:
            label = "UNKNOWN"

        # Cache the result (Supabase)
        try:
            supa_url = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            cache_body = json.dumps({"number": digits, "name": label}).encode()
            cache_post = Request(
                f"{supa_url}/rest/v1/clawcall_cnam_cache",
                data=cache_body,
                headers={
                    "apikey": supa_key,
                    "Authorization": f"Bearer {supa_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="POST",
            )
            urlopen(cache_post, timeout=5)
        except Exception:
            pass

        self._send_json({"ok": True, "number": digits, "label": label, "cached": False})

    # ── WALLET ──────────────────────────────────────────────────

    def _handle_wallet(self):
        profile, err = self._require_auth()
        if err: return err
        balance = float(profile.get("token_balance", 0))
        transactions = get_user_transactions(profile["id"], 20)
        self._send_json({
            "ok": True,
            "balance": balance,
            "max_tokens": 10000,
            "transactions": [{"type": t["transaction_type"], "amount": t["amount"], "date": t["created_at"][:10]} for t in transactions]
        })

    # ── ADMIN OVERVIEW ──────────────────────────────────────────

    def _handle_admin_overview(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        users = get_all_profiles()
        total_users = len(users)
        vip_users = sum(1 for u in users if u.get("is_vip"))
        # Count real calls and revenue
        try:
            all_calls = get_all_calls()
            total_calls = len(all_calls) if all_calls else 0
            total_revenue = sum(float(c.get('cost', 0) or 0) for c in all_calls) if all_calls else 0
        except:
            total_calls = 0
            total_revenue = 0
        
        self._send_json({
            "ok": True, "total_users": total_users, "total_calls": total_calls,
            "revenue": round(total_revenue, 2), "active_now": 0, "vip_users": vip_users
        })

    # ── NOWPAYMENTS HANDLERS ────────────────────────────────────

    def _handle_nowpayments_webhook(self):
        """NOWPayments IPN callback — credits tokens/VIP on confirmed payment."""
        # Verify IPN signature (HMAC-SHA512)
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""
        if raw_body and NOWPAYMENTS_IPN_SECRET:
            sig = self.headers.get("x-nowpayments-sig", "")
            expected = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), raw_body, hashlib.sha512).hexdigest()
            if not hmac.compare_digest(sig, expected):
                log.warning("Webhook signature verification FAILED")
                return self._send_error("Invalid IPN signature", 403)
        body = json.loads(raw_body) if raw_body else {}
        payment_id = str(body.get("payment_id", ""))
        payment_status = str(body.get("payment_status", "")).lower()
        
        if not payment_id:
            return self._send_error("Missing payment_id", 400)
        
        log.info(f"Webhook received: payment={payment_id} status={payment_status}")
        
        try:
            payment = get_payment(payment_id)
            if not payment:
                log.warning(f"Webhook for unknown payment: {payment_id}")
                return self._send_json({"ok": True, "note": "payment not found"})
            
            current = payment.get("payment_status", "")
            if current in ("finished", "confirmed", "credited"):
                return self._send_json({"ok": True, "note": "already processed"})
            
            update_payment(payment_id, {"payment_status": payment_status})
            
            if payment_status in ("finished", "confirmed"):
                user_id = str(payment.get("user_id", ""))
                usd_amount = float(payment.get("amount", 0))
                tokens = usd_amount / TOKEN_PRICE  # $0.50/min → 1 token = $0.50
                
                if tokens > 0:
                    # Token purchase
                    profile = get_profile(int(user_id)) if user_id else None
                    if profile:
                        balance = float(profile.get("token_balance", 0))
                        new_balance = balance + tokens
                        update_profile(int(user_id), {"token_balance": new_balance})
                        log_transaction(int(user_id), tokens, "purchase", new_balance,
                                      f"Purchased {tokens} tokens via NOWPayments (webhook)")
                        update_payment(payment_id, {"payment_status": "credited"})
                        log.info(f"Webhook credited {tokens} tokens to user {user_id}")
                else:
                    # VIP payment
                    profile = get_profile(int(user_id)) if user_id else None
                    if profile:
                        update_profile(int(user_id), {
                            "is_vip": True
                        })
                        update_payment(payment_id, {"payment_status": "credited"})
                        log.info(f"Webhook activated VIP for user {user_id}")
            
            return self._send_json({"ok": True})
        except Exception as e:
            log.error(f"Webhook processing failed: {e}")
            return self._send_error(f"Webhook error: {e}", 500)

    def _handle_create_topup(self):
        profile, err = self._require_auth()
        if err:
            return err

        body = self._read_body()
        token_amount = max(1, int(body.get("token_amount", 20)))
        pay_currency = str(body.get("pay_currency", body.get("coin", "ltc"))).lower()
        if pay_currency not in PAYMENT_COINS:
            return self._send_error("Currency must be btc or ltc")

        usd_amount = round(token_amount * TOKEN_PRICE, 2)
        user_id = profile["id"]
        username = profile.get("username", "unknown")

        try:
            np_body = {
                "price_amount": usd_amount,
                "price_currency": "usd",
                "pay_currency": pay_currency,
                "order_description": f"{token_amount} tokens for {username}",
                "order_id": f"tokens-{user_id[:8]}-{int(time.time())}",
                "is_fixed_rate": False,
                "is_fee_paid_by_user": True,
            }
            result = nowpayments_request("/payment", np_body)
            payment_id = str(result.get("payment_id") or "")

            if not payment_id:
                return self._send_error("Failed to create payment")

            # Store payment locally
            log_payment(user_id, usd_amount, payment_id, "waiting")

            pay_address = str(result.get("pay_address", ""))
            pay_amount = float(result.get("pay_amount", 0))
            payment_status = str(result.get("payment_status", "waiting"))

            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "pay_address": pay_address,
                "pay_amount": pay_amount,
                "pay_currency": pay_currency.upper(),
                "usd_amount": usd_amount,
                "token_amount": token_amount,
                "status": payment_status,
            })
        except Exception as e:
            self._send_error(f"Payment creation failed: {e}")

    def _handle_create_vip(self):
        profile, err = self._require_auth()
        if err:
            return err

        body = self._read_body()
        pay_currency = str(body.get("pay_currency", body.get("coin", "ltc"))).lower()
        if pay_currency not in PAYMENT_COINS:
            return self._send_error("Currency must be btc or ltc")

        user_id = profile["id"]
        username = profile.get("username", "unknown")

        try:
            np_body = {
                "price_amount": VIP_PRICE,
                "price_currency": "usd",
                "pay_currency": pay_currency,
                "order_description": f"VIP 7-day pass for {username}",
                "order_id": f"vip-{user_id[:8]}-{int(time.time())}",
                "is_fixed_rate": False,
                "is_fee_paid_by_user": True,
            }
            result = nowpayments_request("/payment", np_body)
            payment_id = str(result.get("payment_id") or "")

            if not payment_id:
                return self._send_error("Failed to create VIP payment")

            log_payment(user_id, VIP_PRICE, payment_id, "waiting")
            pay_address = str(result.get("pay_address", ""))
            pay_amount = float(result.get("pay_amount", 0))
            payment_status = str(result.get("payment_status", "waiting"))
            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "pay_address": pay_address,
                "pay_amount": pay_amount,
                "pay_currency": pay_currency.upper(),
                "usd_amount": VIP_PRICE,
                "product_type": "vip",
                "status": payment_status,
            })
        except Exception as e:
            self._send_error(f"VIP payment creation failed: {e}")

    def _handle_poll_topup(self):
        profile, err = self._require_auth()
        if err:
            return err

        # Extract payment_id from URL: /api/topups/{payment_id}
        path_parts = urlparse(self.path).path.rstrip("/").split("/")
        if len(path_parts) < 4:
            return self._send_error("Missing payment_id")
        payment_id = path_parts[-1]

        try:
            # Get payment from Supabase
            payment = get_payment(payment_id)
            if not payment:
                # Try creating a local record if it doesn't exist yet
                log_payment(profile["id"], 0, payment_id, "waiting")
                payment = get_payment(payment_id) or {"payment_status": "waiting", "tokens_to_credit": 0, "price_amount": 0}
            current_status = payment.get("payment_status", "waiting")

            # Poll NOWPayments if still pending
            if current_status in ("waiting", "confirming", "sending"):
                try:
                    np_status = nowpayments_request(f"/payment/{payment_id}")
                    new_status = str(np_status.get("payment_status", current_status))
                    update_payment(payment_id, {"payment_status": new_status})

                    # Handle credited
                    if new_status in ("finished", "confirmed") and current_status not in ("finished", "confirmed", "credited"):
                        # Check if this is a VIP payment
                        tokens = float(payment.get("tokens_to_credit", 0))
                        if tokens <= 0:
                            # VIP payment — activate VIP
                            update_profile(profile["id"], {
                                "is_vip": True
                            })
                        else:
                            # Token payment — credit balance
                            balance = float(profile.get("token_balance", 0))
                            new_balance = balance + tokens
                            update_profile(profile["id"], {"token_balance": new_balance})
                            log_transaction(profile["id"], tokens, "purchase", new_balance, f"Purchased {tokens} tokens via NOWPayments")
                            current_status = "credited"
                    else:
                        current_status = new_status
                except Exception as e:
                    log.warning(f"NOWPayments poll failed: {e}")

            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "product_type": "vip" if float(payment.get("tokens_to_credit", 0)) <= 0 else "tokens",
                "token_amount": float(payment.get("tokens_to_credit", 0)),
                "usd_amount": float(payment.get("price_amount", 0)),
                "pay_currency": payment.get("pay_currency", "").upper(),
                "pay_address": payment.get("pay_address", ""),
                "pay_amount": float(payment.get("pay_amount", 0)),
                "invoice_url": payment.get("nowpayments_payment_id", ""),
                "status": current_status,
                "user": {
                    "id": profile["id"],
                    "token_balance": float(profile.get("token_balance", 0)),
                    "is_vip": bool(profile.get("is_vip")),
                },
            })
        except Exception as e:
            self._send_error(f"Payment poll failed: {e}")

    # ── ADMIN HANDLERS ──────────────────────────────────────────

    def _handle_admin_users(self):
        profile, err = self._require_auth()
        if err:
            return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        qs = {}
        if self.path and "?" in self.path:
            from urllib.parse import parse_qs
            qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        page = int(qs.get("page", 1))
        per_page = int(qs.get("per_page", 15))
        result = list_profiles(page=page, per_page=per_page)
        if isinstance(result, dict):
            self._send_json({"ok": True, **result})
        else:
            self._send_json({"ok": True, "users": result, "total": len(result) if isinstance(result, list) else 0, "page": 1, "per_page": 15})

    def _handle_admin_stats(self):
        profile, err = self._require_auth()
        if err:
            return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        # Aggregate stats
        users = get_all_profiles()
        total_users = len(users) if isinstance(users, list) else 0
        vip_users = sum(1 for u in users if u.get("is_vip")) if isinstance(users, list) else 0
        banned_users = sum(1 for u in users if u.get("is_banned")) if isinstance(users, list) else 0
        suspended_users = sum(1 for u in users if u.get("is_suspended")) if isinstance(users, list) else 0

        # Total tokens in circulation
        total_tokens = sum(float(u.get("token_balance", 0)) for u in users if u.get("role") != "admin") if isinstance(users, list) else 0

        # Revenue + call stats from Supabase
        try:
            from urllib.request import Request as Rq, urlopen as uo
            supa_url = os.environ.get("SUPABASE_URL", "")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            supa_hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}

            # Deposits
            dep_req = Rq(f"{supa_url}/rest/v1/clawcall_deposits?payment_status=eq.finished&select=amount", headers=supa_hdrs)
            with uo(dep_req, timeout=5) as resp:
                dep_data = json.loads(resp.read())
            total_deposits = sum(float(p.get("amount", 0)) for p in dep_data) if dep_data else 0
            deposit_count = len(dep_data) if dep_data else 0

            # Calls
            call_req = Rq(f"{supa_url}/rest/v1/clawcall_calls?select=id,cost,duration_seconds,started_at&order=started_at.desc&limit=200", headers=supa_hdrs)
            with uo(call_req, timeout=5) as resp:
                call_data = json.loads(resp.read())
            total_calls = len(call_data) if call_data else 0
            total_call_cost = sum(float(c.get("cost", 0)) for c in call_data) if call_data else 0
            total_duration = sum(float(c.get("duration_seconds", 0)) for c in call_data) if call_data else 0

            # Revenue over time (last 7 days, simplified)
            from datetime import datetime, timedelta
            daily_calls = {}
            daily_revenue = {}
            now = datetime.utcnow()
            for i in range(7):
                day = (now - timedelta(days=i)).strftime("%a")
                daily_calls[day] = 0
                daily_revenue[day] = 0
            if call_data:
                for c in call_data:
                    try:
                        ts = c.get("started_at", "")
                        if ts:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            day = dt.strftime("%a")
                            if day in daily_calls:
                                daily_calls[day] += 1
                                daily_revenue[day] += float(c.get("cost", 0))
                    except Exception:
                        pass
        except Exception:
            total_deposits = 0
            deposit_count = 0
            total_calls = 0
            total_call_cost = 0
            total_duration = 0
            daily_calls = {}
            daily_revenue = {}

        self._send_json({
            "ok": True,
            "total_users": total_users,
            "vip_users": vip_users,
            "banned_users": banned_users,
            "suspended_users": suspended_users,
            "total_tokens": round(total_tokens, 2),
            "total_deposits": round(total_deposits, 2),
            "deposit_count": deposit_count,
            "total_calls": total_calls,
            "total_call_cost": round(total_call_cost, 4),
            "total_duration_min": round(total_duration / 60, 1) if total_duration else 0,
            "daily_calls": daily_calls,
            "daily_revenue": {k: round(v, 4) for k, v in daily_revenue.items()},
        })

    def _handle_admin_vouchers(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        vouchers = voucher_system.list_vouchers(limit=100)
        return self._send_json({"ok": True, "vouchers": vouchers})

    def _handle_admin_cleanup_deposits(self):
        """Delete waiting deposits older than 120 minutes."""
        internal_key = self.headers.get("X-Internal-Cleanup", "")
        if internal_key != "hushcircuits_cleanup_2026":
            profile, err = self._require_auth()
            if err: return err
            if profile.get("role") != "admin":
                return self._send_error("Admin access required", 403)
        from datetime import datetime, timedelta, timezone
        from urllib.request import Request as Rq, urlopen
        from urllib.error import HTTPError
        # Format cutoff without timezone for Supabase compatibility
        cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=120)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            supa_url = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            base_hdrs = {
                "apikey": supa_key,
                "Authorization": f"Bearer {supa_key}",
            }
            # Count stale waiting deposits
            count_url = f"{supa_url}/rest/v1/clawcall_deposits?select=id&payment_status=eq.waiting&created_at=lt.{cutoff}"
            count_req = Rq(count_url, headers=base_hdrs)
            with urlopen(count_req) as resp:
                stale = json.loads(resp.read().decode())
            count = len(stale) if isinstance(stale, list) else 0
            if count > 0:
                # Delete individually via _delete helper
                for item in stale:
                    dep_id = item.get("id")
                    if dep_id:
                        del_url = f"{supa_url}/rest/v1/clawcall_deposits?id=eq.{dep_id}"
                        del_req = Rq(del_url, headers={**base_hdrs, "Prefer": "return=minimal"}, method="DELETE")
                        try:
                            urlopen(del_req)
                        except HTTPError:
                            pass
                print(f"[cleanup] Removed {count} stale deposits older than 120 min")
            self._send_json({"ok": True, "removed": count})
        except Exception as e:
            print(f"[cleanup] Error: {e}")
            self._send_json({"ok": False, "error": str(e)})

    def _handle_admin_deposits(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        try:
            from urllib.request import Request as Rq, urlopen as uo
            supa_url = os.environ.get("SUPABASE_URL", "")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            supa_hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}
            dep_req = Rq(f"{supa_url}/rest/v1/clawcall_deposits?select=*&order=created_at.desc&limit=30", headers=supa_hdrs)
            with uo(dep_req, timeout=5) as resp:
                dep_data = json.loads(resp.read())
            deposits = dep_data if dep_data else []
        except Exception:
            deposits = []
        self._send_json({"ok": True, "deposits": deposits})

    def _handle_admin_call_analytics(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        calls = get_all_calls(500)
        total = len(calls)
        total_duration = sum(c.get("duration_seconds", 0) or 0 for c in calls)
        total_cost = sum(float(c.get("cost", 0) or 0) for c in calls)
        failed = sum(1 for c in calls if c.get("status") == "failed")
        # Top destinations
        dest_map = {}
        for c in calls:
            dest = c.get("target_number", "") or c.get("destination", "")
            if not dest: continue
            dest_map[dest] = dest_map.get(dest, 0) + 1
        top_dests = sorted(dest_map.items(), key=lambda x: -x[1])[:10]
        # Daily counts (last 7 days)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        daily = {}
        for i in range(7):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily[d] = 0
        for c in calls:
            ts = c.get("started_at") or c.get("created_at")
            if not ts: continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                day = dt.strftime("%Y-%m-%d")
                if day in daily:
                    daily[day] += 1
            except (ValueError, TypeError):
                pass
        daily_counts = [{"date": d, "count": daily[d]} for d in sorted(daily.keys())]
        # Status breakdown
        status_map = {}
        for c in calls:
            s = c.get("status", "unknown") or "unknown"
            status_map[s] = status_map.get(s, 0) + 1
        status_breakdown = [{"status": k, "count": v} for k, v in sorted(status_map.items(), key=lambda x: -x[1])]
        # Duration buckets
        buckets = {"<30s": 0, "30s-2m": 0, "2m-5m": 0, ">5m": 0}
        for c in calls:
            d = c.get("duration_seconds", 0) or 0
            if d < 30:
                buckets["<30s"] += 1
            elif d < 120:
                buckets["30s-2m"] += 1
            elif d < 300:
                buckets["2m-5m"] += 1
            else:
                buckets[">5m"] += 1
        duration_buckets = [{"bucket": k, "count": v} for k, v in buckets.items()]
        self._send_json({
            "ok": True,
            "total_calls": total,
            "total_duration_sec": total_duration,
            "avg_duration_sec": round(total_duration / total) if total else 0,
            "total_cost": round(total_cost, 4),
            "failed": failed,
            "fail_rate": round(failed / total * 100, 1) if total else 0,
            "top_destinations": [{"number": d, "count": c} for d, c in top_dests],
            "daily_counts": daily_counts,
            "status_breakdown": status_breakdown,
            "duration_buckets": duration_buckets,
        })

    def _handle_admin_user_detail(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        qs = {}
        if self.path and "?" in self.path:
            from urllib.parse import parse_qs
            qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        user_id = qs.get("user_id", "")
        if not user_id:
            return self._send_error("user_id required")
        target = get_user_profile(user_id)
        if not target:
            return self._send_error("User not found")
        calls = get_user_calls(user_id, 10)
        transactions = get_user_transactions(user_id, 10)
        self._send_json({
            "ok": True,
            "profile": {
                "id": str(target.get("id", "")),
                "username": target.get("username", ""),
                "token_balance": float(target.get("token_balance", 0)),
                "role": target.get("role", "user"),
                "is_vip": target.get("role") == "vip",
                "is_banned": target.get("status") == "banned",
                "is_suspended": target.get("status") == "suspended",
                "sip_extension": target.get("sip_extension", ""),
                "caller_id": target.get("caller_id", ""),
                "updated_at": target.get("updated_at", ""),
                "created_at": target.get("created_at", ""),
            },
            "calls": calls[:10],
            "transactions": transactions[:10],
        })

    def _handle_redeem_voucher(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        code = str(body.get("code", "")).strip().upper()
        if not code: return self._send_error("Voucher code required")
        result, ok = voucher_system.redeem_voucher(code, profile["id"])
        if not ok: return self._send_error(result)
        balance = float(profile.get("token_balance", 0))
        new_balance = balance + result
        update_profile(profile["id"], {"token_balance": new_balance})
        log_transaction(profile["id"], result, "voucher_redemption", new_balance, f"Redeemed voucher {code}")
        return self._send_json({"ok": True, "amount": result, "new_balance": new_balance, "code": code})

    def _handle_admin_logs(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        events = list(EVENT_LOG[-100:]) if EVENT_LOG else []
        try:
            with open("/tmp/clawcall.log") as lf:
                lines = lf.readlines()
                file_logs = "".join(lines[-20:])
        except:
            file_logs = ""
        self._send_json({"ok": True, "events": events, "file_logs": file_logs})

    def _handle_admin_restart_asterisk(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        import subprocess, threading
        def do_restart():
            subprocess.run(["docker", "restart", "clawcall-asterisk"],
                         capture_output=True, timeout=30)
        threading.Thread(target=do_restart, daemon=True).start()
        log.info(f"Asterisk restart initiated by {profile.get('username')}")
        self._send_json({"ok": True, "message": "Asterisk restart initiated"})

    def _handle_admin_reload_config(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        import subprocess
        try:
            result = subprocess.run(["docker", "exec", "clawcall-asterisk",
                                   "asterisk", "-rx", "module", "reload"],
                                  capture_output=True, text=True, timeout=10)
            self._send_json({"ok": True, "message": "Config reload initiated", "output": result.stdout[:500]})
        except Exception as e:
            self._send_error(f"Reload failed: {e}")

    def _handle_admin_restart_backend(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        log.warning(f"Backend restart initiated by {profile.get('username')} - service will restart")
        import os, signal
        self._send_json({"ok": True, "message": "Backend restarting..."})
        os.kill(os.getpid(), signal.SIGTERM)

    def _handle_admin_action(self):
        """Handle admin actions like adjusting balance or creating vouchers."""
        profile, err = self._require_auth()
        if err:
            return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/admin/adjust-balance" or body.get("action") == "adjust_balance":
            target_id = str(body.get("user_id", ""))
            amount = float(body.get("amount", 0))
            if not target_id or amount == 0:
                return self._send_error("user_id and amount required")

            target = get_user_profile(target_id)
            if not target:
                return self._send_error("User not found")

            balance = float(target.get("token_balance", 0))
            new_balance = max(0, balance + amount)
            update_profile(target_id, {"token_balance": new_balance})
            log_transaction(target_id, amount, "admin_adjustment", new_balance,
                          f"Admin adjustment by {profile.get('username', 'admin')}")

            return self._send_json({"ok": True, "user_id": target_id, "new_balance": new_balance})

        if path == "/api/admin/create-vouchers" or body.get("action") == "create_vouchers":
            amount = int(body.get("amount", 0))
            count = int(body.get("count", 1))
            if amount not in (10, 25, 50):
                return self._send_error("Amount must be 10, 25, or 50")
            if count < 1 or count > 50:
                return self._send_error("Count must be 1-50")
            codes = voucher_system.create_batch(amount, count, int(profile.get("id", 1)))
            return self._send_json({"ok": True, "codes": codes, "amount": amount, "count": len(codes)})

        self._send_error("Unknown admin action", 404)

    def _handle_admin_user_patch(self):
        """Update a user's fields. Admin only."""
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        path = urlparse(self.path).path
        user_id = path.rstrip("/").split("/")[-1]
        body = self._read_body()

        allowed = ["token_balance", "is_vip", "is_banned", "is_suspended", "role", "username"]
        updates = {}
        for k in allowed:
            if k in body:
                updates[k] = body[k]

        if not updates:
            return self._send_error("No valid fields to update")

        update_profile(user_id, updates)
        self._send_json({"ok": True, "user_id": user_id, "updated": list(updates.keys())})


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════


    def _handle_hangup_call(self):
        """Force-hangup via AMI. Insurance policy for trunk BYE."""
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        channel = str(body.get("channel", "") or body.get("target", ""))
        if not channel:
            return self._send_error("Channel or target required")
        try:
            import socket as _sock
            s = _sock.socket()
            s.settimeout(10)
            s.connect((AMI_HOST, AMI_PORT))
            s.recv(4096)
            s.sendall(f"Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {AMI_SECRET}\r\n\r\n".encode())
            resp = s.recv(4096).decode()
            if "Success" not in resp:
                s.close()
                return self._send_error("AMI login failed")
            s.sendall(f"Action: Command\r\nCommand: channel request hangup {channel}\r\n\r\n".encode())
            result = ""
            while True:
                try:
                    chunk = s.recv(4096).decode()
                    if not chunk: break
                    result += chunk
                    if "--END COMMAND--" in result or "Response:" in result:
                        break
                except _sock.timeout:
                    break
            s.close()
            log.info(f"AMI hangup sent for channel {channel}")
            return self._send_json({"ok": True, "message": f"Hangup sent for {channel}"})
        except Exception as e:
            log.error(f"AMI hangup failed: {e}")
            return self._send_error(f"Hangup failed: {e}")

    def _handle_call_export(self):
        """Export user calls as CSV."""
        profile, err = self._require_auth()
        if err: return err
        from supabase_data import get_user_calls
        import csv, io
        calls = get_user_calls(profile["id"], 500)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "Caller ID", "Destination", "Duration (s)", "Status", "Cost (TK)"])
        for c in calls:
            writer.writerow([
                c.get("started_at", c.get("created_at", "")),
                c.get("caller_id", c.get("cid", "")),
                c.get("target_number", c.get("destination", c.get("dest", ""))),
                c.get("duration_seconds", c.get("duration", 0)),
                c.get("status", ""),
                c.get("cost", 0),
            ])
        csv_data = output.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", "attachment; filename=clawcall_history.csv")
        self.send_header("Content-Length", str(len(csv_data)))
        for h, v in cors_headers():
            self.send_header(h, v)
        self.end_headers()
        self.wfile.write(csv_data.encode("utf-8"))

    def _handle_call_history(self):
        """Return user's call history."""
        profile, err = self._require_auth()
        if err: return err
        calls = get_user_calls(profile["id"], 30)
        self._send_json({"ok": True, "calls": calls})

    def _handle_stats(self):
        """Return aggregate call stats for current user."""
        profile, err = self._require_auth()
        if err: return err
        calls = get_user_calls(profile["id"], 1000)
        total_calls = len(calls)
        total_seconds = sum((c.get("duration_seconds", 0) or 0) for c in calls)
        total_cost = round(sum((c.get("cost", 0) or 0) for c in calls), 2)
        avg_seconds = round(total_seconds / total_calls) if total_calls else 0
        completed = sum(1 for c in calls if c.get("status") == "completed")
        failed = sum(1 for c in calls if c.get("status") == "failed")
        # Daily usage (last 7 days)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        daily = {}
        for i in range(7):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily[d] = 0
        for c in calls:
            ts = c.get("started_at") or c.get("created_at")
            if not ts: continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                day = dt.strftime("%Y-%m-%d")
                if day in daily:
                    daily[day] += 1
            except (ValueError, TypeError):
                pass
        daily_usage = [{"date": d, "count": daily[d]} for d in sorted(daily.keys())]
        # Status breakdown
        status_map = {}
        for c in calls:
            s = c.get("status", "unknown") or "unknown"
            status_map[s] = status_map.get(s, 0) + 1
        status_breakdown = [{"status": k, "count": v} for k, v in sorted(status_map.items(), key=lambda x: -x[1])]
        # Top destinations
        dest_map = {}
        for c in calls:
            dest = c.get("target_number", "") or c.get("destination", "")
            if not dest: continue
            dest_map[dest] = dest_map.get(dest, 0) + 1
        top_dests = sorted(dest_map.items(), key=lambda x: -x[1])[:5]
        self._send_json({
            "ok": True,
            "total_calls": total_calls,
            "total_cost": total_cost,
            "avg_duration": avg_seconds,
            "completed": completed,
            "failed": failed,
            "daily_usage": daily_usage,
            "status_breakdown": status_breakdown,
            "top_destinations": [{"number": d, "count": c} for d, c in top_dests],
            "calls": [dict(c) for c in calls[:20]],
        })

    def _handle_set_caller_id(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        number = str(body.get('caller_id', '')).strip()
        digits = ''.join(c for c in number if c.isdigit())
        if not digits or len(digits) < 10:
            return self._send_error('Invalid caller ID. Must be at least 10 digits.')
        if len(digits) == 10:
            digits = '1' + digits
        extension = str(profile.get("sip_extension") or profile.get("id", ""))
        success = set_caller_id(digits, extension)
        
        # Persist to Supabase
        try:
            update_profile(profile["id"], {"caller_id": digits})
        except Exception as e:
            log.warning(f"Failed to persist caller_id to Supabase: {e}")
        
        if success:
            return self._send_json({"ok": True, "caller_id": get_caller_id(extension)})
        return self._send_error("Failed to update caller ID")

    def _handle_originate_call(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        target = str(body.get('destination', '')).strip()
        caller_id = str(body.get('caller_id', '')).strip() or None
        
        # Validate number
        digits = ''.join(c for c in target if c.isdigit())
        if not digits or len(digits) not in {10, 11}:
            return self._send_error('Invalid target number')
        
        # Check authorization
        is_admin = profile.get('role') == 'admin'
        is_vip = bool(profile.get('is_vip'))
        if profile.get('is_banned') or profile.get('is_suspended'):
            return self._send_error('Account suspended or banned', 403)
        
        # Token check (admins and VIPs bypass)
        # Minimum balance required to place a call — actual billing happens
        # via /api/calls/report when the call ends (per-minute rate)
        balance = float(profile.get('token_balance', 0))
        MIN_CALL_TOKENS = 0.50
        new_balance = balance
        if not is_admin and not is_vip:
            if balance < MIN_CALL_TOKENS:
                return self._send_error(f'Insufficient tokens. Minimum {MIN_CALL_TOKENS} tokens required. Have {balance:.2f}.', 402)
        
        # Push caller ID to AstDB so it persists for WebRTC-originated calls
        if caller_id:
            extension = str(profile.get("sip_extension") or profile.get("id", ""))
            try:
                set_caller_id(caller_id, extension)
                log.info(f"Pushed caller ID {caller_id} to AstDB for ext {extension}")
            except Exception as e:
                log.warning(f"Failed to push caller ID to AstDB: {e}")
        
        result = originate_call(target, caller_id)
        
        if result['ok']:
            resp = dict(result)
            resp['cost'] = 0  # Billed per-minute via /api/calls/report when call ends
            resp['new_balance'] = new_balance
            return self._send_json(resp)
        return self._send_error(result['error'])


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    init_tables()  # Ensure all local tables exist
    server = ThreadedHTTPServer(("0.0.0.0", API_PORT), ClawCallHandler)
    log.info(f"ClawCall backend listening on 0.0.0.0:{API_PORT}")
    log.info(f"Domain: {DOMAIN} | DB: local SQLite")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
