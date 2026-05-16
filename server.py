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
    get_user_transactions, get_user_calls, get_user_payments, get_payment, update_payment, init_tables)
import supabase_voucher as voucher_system
import subprocess, socket
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────
DOMAIN          = os.environ.get("DOMAIN", "hushcircuits.online")
PUBLIC_IP       = os.environ.get("PUBLIC_IP", "18.223.24.42")
API_PORT        = int(os.environ.get("API_PORT", "8090"))
# Supabase removed — using local SQLite
NOWPAYMENTS_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "9ED8ZNB-1ZNMGM6-J92MPHH-BA68DV7")
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
    """Endpoints are baked into the Asterisk container pjsip.conf.
    This is a no-op stub — backend no longer manages endpoint config."""
    log.info(f"Endpoint {extension} assumed to exist (container-managed)")
    return True

#def create_pjsip_endpoint(extension, password):
#    """Create PJSIP WebRTC endpoint via config file + AMI reload.
#    Idempotent — skips if auth block already exists."""
#    conf_path = "/etc/asterisk/pjsip_wss.conf"
#    
#    # Guard: check if already exists
#    try:
#        with open(conf_path) as f:
#            if f"[auth{extension}]" in f.read():
#                log.info(f"PJSIP endpoint {extension} already exists, skipping")
#                return True
#    except FileNotFoundError:
#        pass
#    
#    # Build config block
#    block = f"""
#; Extension {extension} — auto-created by backend
#[auth{extension}]
#type=auth
#auth_type=userpass
#password={password}
#username={extension}
#
#[{extension}-aor]
#type=aor
#max_contacts=3
#
#[{extension}](webrtc-template)
#auth=auth{extension}
#aors={extension}-aor
#"""
#    
#    try:
#        with open(conf_path, "a") as f:
#            f.write(block)
#        log.info(f"Wrote PJSIP endpoint {extension} to {conf_path}")
#    except Exception as e:
#        log.error(f"Failed to write PJSIP config for {extension}: {e}")
#        return False
#    
#    # Reload via AMI
#    try:
#        sock = socket.socket()
#        sock.settimeout(5)
#        sock.connect((AMI_HOST, AMI_PORT))
#        sock.recv(1024)  # banner
#        sock.send(f"Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {AMI_SECRET}\r\n\r\n".encode())
#        time.sleep(0.2)
#        sock.recv(1024)  # login response
#        sock.send(b"Action: Command\r\nCommand: module reload res_pjsip.so\r\n\r\n")
#        time.sleep(0.5)
#        sock.recv(4096)
#        sock.close()
#        log.info(f"AMI: reloaded PJSIP after creating endpoint {extension}")
#    except Exception as e:
#        log.warning(f"AMI reload failed (endpoint {extension} written but not reloaded): {e}")
        # Don't fail — endpoint is written, will load on next restart
    
    return True


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads."""
    daemon_threads = True


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
            "/api/admin/users": self._handle_admin_users,
            "/api/admin/stats": self._handle_admin_stats,
            "/api/admin/vouchers": self._handle_admin_vouchers,
            "/api/wallet": self._handle_wallet,
            "/api/admin/overview": self._handle_admin_overview,
            "/api/calls/history": self._handle_call_history,
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
            "/api/call/hangup": self._handle_hangup_call,
            "/api/topups/vip": self._handle_create_vip,
            "/api/topups": self._handle_create_topup,
            "/api/admin/": self._handle_admin_action,
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
                "token_balance": new_balance if not is_admin and not is_vip else float(profile.get("token_balance", 0)),
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

        self._send_json({
            "ok": True,
            "user": {
                "id": profile["id"],
                "username": username,
                "token_balance": float(profile.get("token_balance", 0)),
                "is_admin": profile.get("role") == "admin",
                "is_vip": bool(profile.get("is_vip")),
                "vip_expires_at": profile.get("vip_expires_at"),
                "account_status": "banned" if profile.get("is_banned") else ("suspended" if profile.get("is_suspended") else "active"),
                "sip_extension": profile.get("sip_extension"),
                "sip_password": profile.get("sip_password"),
            },
            "vip_active": bool(profile.get("is_vip")),
            "rate_per_minute_usd": TOKEN_PRICE,
            "vip_price_usd": VIP_PRICE,
        })

    # ── SIP / CALL HANDLERS ─────────────────────────────────────

    def _handle_sip_credentials(self):
        profile, err = self._require_auth()
        if err:
            return err

        sip_ext = profile.get("sip_extension") or get_next_sip_extension()  # noqa
        sip_pass = profile.get("sip_password") or secrets.token_hex(8)
        if not profile.get("sip_extension"):
            update_profile(profile["id"], {"sip_extension": sip_ext, "sip_password": sip_pass})

        # Always ensure Asterisk endpoint exists (idempotent)
        try:
            create_pjsip_endpoint(sip_ext, sip_pass)
        except Exception as e:
            log.error(f"Failed to ensure PJSIP endpoint {sip_ext}: {e}")

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
            result = nowpayments_request("/invoice", np_body)
            payment_id = str(result.get("id") or result.get("invoice_id") or "")

            if not payment_id:
                return self._send_error("Failed to create payment invoice")

            # Store payment locally
            log_payment(user_id, usd_amount, payment_id, "waiting")

            # Poll NOWPayments for the actual payment address
            pay_address = ""
            pay_amount = 0.0
            try:
                np_payment = nowpayments_request(f"/payment/{payment_id}")
                pay_address = str(np_payment.get("pay_address", ""))
                pay_amount = float(np_payment.get("pay_amount", 0))
            except Exception:
                pass

            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "invoice_url": result.get("invoice_url", ""),
                "pay_address": pay_address or result.get("pay_address", ""),
                "pay_amount": pay_amount or float(result.get("pay_amount", 0)),
                "pay_currency": pay_currency.upper(),
                "usd_amount": usd_amount,
                "token_amount": token_amount,
                "status": "waiting",
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
            result = nowpayments_request("/invoice", np_body)
            payment_id = str(result.get("id") or result.get("invoice_id") or "")

            if not payment_id:
                return self._send_error("Failed to create VIP invoice")

            log_payment(user_id, VIP_PRICE, payment_id, "waiting")
            pay_address = ""
            pay_amount = 0.0
            try:
                np_payment = nowpayments_request(f"/payment/{payment_id}")
                pay_address = str(np_payment.get("pay_address", ""))
                pay_amount = float(np_payment.get("pay_amount", 0))
            except Exception:
                pass
            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "invoice_url": result.get("invoice_url", ""),
                "pay_address": pay_address or result.get("pay_address", ""),
                "pay_amount": pay_amount or float(result.get("pay_amount", 0)),
                "pay_currency": pay_currency.upper(),
                "usd_amount": VIP_PRICE,
                "product_type": "vip",
                "status": "waiting",
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
                            now = int(time.time())
                            seven_days = now + 7 * 86400
                            update_profile(profile["id"], {
                                "is_vip": True,
                                "vip_expires_at": f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(seven_days))}"
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

        users = list_profiles(50)
        if not isinstance(users, list):
            users = []

        self._send_json({"ok": True, "users": users})

    def _handle_admin_stats(self):
        profile, err = self._require_auth()
        if err:
            return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        # Aggregate stats
        users = get_all_profiles()
        # Revenue stats from Supabase
        try:
            from urllib.request import Request as Rq, urlopen as uo
            supa_url = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
            supa_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            supa_hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}
            pay_req = Rq(f"{supa_url}/rest/v1/clawcall_deposits?payment_status=eq.finished&select=amount", headers=supa_hdrs)
            with uo(pay_req, timeout=5) as resp:
                p_data = json.loads(resp.read())
            total_revenue = sum(float(p.get("amount", 0)) for p in p_data) if p_data else 0
        except:
            total_revenue = 0

        total_users = len(users) if isinstance(users, list) else 0
        vip_users = sum(1 for u in users if u.get("is_vip")) if isinstance(users, list) else 0

        self._send_json({
            "ok": True,
            "total_users": total_users,
            "total_revenue": round(total_revenue, 2),
            "vip_users": vip_users,
        })

    def _handle_admin_vouchers(self):
        profile, err = self._require_auth()
        if err: return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)
        vouchers = voucher_system.list_vouchers(limit=100)
        return self._send_json({"ok": True, "vouchers": vouchers})

    def _handle_redeem_voucher(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        code = str(body.get("code", "")).strip().upper()
        if not code: return self._send_error("Voucher code required")
        result, ok = voucher_system.redeem_voucher(code, profile.get("username", "unknown"))
        if not ok: return self._send_error(result)
        balance = float(profile.get("token_balance", 0))
        new_balance = balance + result
        update_profile(profile["id"], {"token_balance": new_balance})
        log_transaction(profile["id"], result, "voucher_redemption", new_balance, f"Redeemed voucher {code}")
        return self._send_json({"ok": True, "amount": result, "new_balance": new_balance, "code": code})

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
            codes = voucher_system.create_batch(amount, count, profile.get("username", "admin"))
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
        total_seconds = sum((c.get("duration", 0) or 0) for c in calls)
        total_cost = round(sum((c.get("cost", 0) or 0) for c in calls), 2)
        total_minutes = round(total_seconds / 60, 1)
        avg_seconds = round(total_seconds / total_calls) if total_calls else 0
        completed = sum(1 for c in calls if c.get("status") == "completed")
        failed = sum(1 for c in calls if c.get("status") == "failed")
        self._send_json({
            "ok": True,
            "total_calls": total_calls,
            "total_minutes": total_minutes,
            "total_cost": total_cost,
            "avg_duration": avg_seconds,
            "completed": completed,
            "failed": failed,
            "calls": [dict(c) for c in calls[-20:]]  # Last 20 as proper dicts
        })

    def _handle_set_caller_id(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        number = str(body.get('caller_id', '')).strip()
        if not number or len(number) < 10:
            return self._send_error('Invalid caller ID. Must be 10-digit North American number.')
        success = set_caller_id(number)
        if success:
            return self._send_json({'ok': True, 'caller_id': get_caller_id()})
        return self._send_error('Failed to update caller ID')

    def _handle_originate_call(self):
        profile, err = self._require_auth()
        if err: return err
        body = self._read_body()
        target = str(body.get('destination', '')).strip()
        caller_id = str(body.get('caller_id', '')).strip() or None
        
        # Validate number
        digits = ''.join(c for c in target if c.isdigit())
        if not digits or len(digits.replace('1','').replace('+','')) < 10:
            return self._send_error('Invalid target number')
        
        # Check authorization
        is_admin = profile.get('role') == 'admin'
        is_vip = bool(profile.get('is_vip'))
        if profile.get('is_banned') or profile.get('is_suspended'):
            return self._send_error('Account suspended or banned', 403)
        
        # Token check (admins and VIPs bypass)
        balance = float(profile.get('token_balance', 0))
        CALL_COST = 1
        new_balance = balance
        if not is_admin and not is_vip:
            if balance < CALL_COST:
                return self._send_error(f'Insufficient tokens. Need {CALL_COST}, have {int(balance)}. Buy tokens to call.', 402)
            new_balance = balance - CALL_COST
            update_profile(profile['id'], {'token_balance': new_balance})
            log_transaction(profile['id'], -CALL_COST, 'call_charge', new_balance,
                          f'Call to {digits} (CID: {caller_id})')
        
        result = originate_call(target, caller_id)
        
        # Log the call
        try:
            log_call(profile['id'], caller_id or 'unknown', digits, 0, 
                    CALL_COST if not is_admin and not is_vip else 0,
                    'initiated' if result['ok'] else 'failed')
        except Exception:
            pass
        
        if result['ok']:
            resp = dict(result)
            resp['cost'] = CALL_COST if not is_admin and not is_vip else 0
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