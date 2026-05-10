#!/usr/bin/env python3
"""
ClawCall Backend — Standalone VoIP + Crypto Payment Server
hushcircuits.online | FreeSWITCH (asarov) | SIP.UP | Supabase | NOWPayments
Python stdlib only + bcrypt. No frameworks.
"""
import json, os, time, uuid, base64, hashlib, hmac, secrets, logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs
from urllib.error import HTTPError, URLError
from pathlib import Path
from caller_id import set_caller_id, get_caller_id, originate_call
from local_auth import register_user, login_user, validate_session, get_user_profile as local_get_profile, logout as local_logout
import subprocess, sqlite3

# ── Config ────────────────────────────────────────────────────────
DOMAIN          = os.environ.get("DOMAIN", "hushcircuits.online")
PUBLIC_IP       = os.environ.get("PUBLIC_IP", "18.223.24.42")
API_PORT        = int(os.environ.get("API_PORT", "8090"))
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "https://ejbdrsgciqwvanskckov.supabase.co")
SUPABASE_KEY    = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ANON_KEY        = os.environ.get("SUPABASE_ANON_KEY", "")
NOWPAYMENTS_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "9ED8ZNB-1ZNMGM6-J92MPHH-BA68DV7")
TWILIO_SID      = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
TOKEN_PRICE     = float(os.environ.get("PRICE_PER_MINUTE", "0.50"))
VIP_PRICE       = float(os.environ.get("VIP_WEEKLY_PRICE", "250.00"))
SIPUP_USER      = os.environ.get("SIPUP_USERNAME", "10428")
SIPUP_PASS      = os.environ.get("SIPUP_PASSWORD", "Mcjhv877KAK9")
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
def supabase_request(method, path, body=None, use_service_role=True, headers_extra=None):
    """Call Supabase REST API."""
    key = SUPABASE_KEY if use_service_role else ANON_KEY
    headers = {**BROWSER_HEADERS, "apikey": key, "Authorization": f"Bearer {key}"}
    if headers_extra:
        headers.update(headers_extra)
    url = f"{SUPABASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        raise ValueError(f"Supabase {method} {path} failed: {e.code} {body_text[:300]}")

def supabase_auth_request(path, body):
    """Call Supabase Auth API directly."""
    headers = {**BROWSER_HEADERS, "apikey": ANON_KEY}
    url = f"{SUPABASE_URL}/auth/v1{path}"
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        raise ValueError(f"Auth {path} failed: {e.code} {body_text[:200]}")

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

def get_user_profile(user_id):
    """Get user profile from Supabase."""
    result = supabase_request("GET", f"/rest/v1/profiles?id=eq.{user_id}&select=*")
    if isinstance(result, list) and result:
        return result[0]
    return None

def update_profile(user_id, updates):
    """Update user profile fields."""
    return supabase_request("PATCH", f"/rest/v1/profiles?id=eq.{user_id}", updates,
                            headers_extra={"Prefer": "return=representation"})

def get_next_sip_extension():
    """Get next available SIP extension number."""
    result = supabase_request("GET", "/rest/v1/profiles?select=sip_extension&order=sip_extension.desc&limit=1")
    if result and isinstance(result, list) and result:
        last = result[0].get("sip_extension", "0")
        try:
            ext_num = int(last) if last and last.isdigit() else SIP_EXT_START - 1
        except (ValueError, TypeError):
            ext_num = SIP_EXT_START - 1
        return str(max(SIP_EXT_START, ext_num + 1))
    return str(SIP_EXT_START)

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
        profile = get_user_profile(session["user_id"])
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
            "/api/sip/credentials": self._handle_sip_credentials,
            "/api/sip/config": self._handle_sip_config,
            "/api/health": self._handle_health,
            "/api/cnam": self._handle_cnam,
            "/api/topups/": self._handle_poll_topup,
            "/api/admin/users": self._handle_admin_users,
            "/api/admin/stats": self._handle_admin_stats,
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
            "/api/calls/authorize": self._handle_authorize_call,
            "/api/calls/report": self._handle_report_call,
            "/api/topups": self._handle_create_topup,
            "/api/topups/vip": self._handle_create_vip,
            "/api/admin/": self._handle_admin_action,
            "/api/caller-id": self._handle_set_caller_id,
            "/api/call": self._handle_originate_call,
        }

        for route, handler in routes.items():
            if path == route or path.startswith(route):
                return handler()

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
        if not username.isalnum():
            return self._send_error("Username must be alphanumeric")

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
        username = self._get_session()["username"]

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

        sip_ext = profile.get("sip_extension") or get_next_sip_extension()
        sip_pass = profile.get("sip_password") or secrets.token_hex(8)
        if not profile.get("sip_extension"):
            update_profile(profile["id"], {"sip_extension": sip_ext, "sip_password": sip_pass})

        self._send_json({
            "ok": True,
            "extension": sip_ext,
            "password": sip_pass,
            "domain": DOMAIN,
            "wss_url": f"wss://{DOMAIN}/ws",
            "display_name": self._get_session()["username"],
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

    def _handle_authorize_call(self):
        profile, err = self._require_auth()
        if err:
            return err

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
            billed = round(duration / 60, 4)
            # Deduct tokens
            balance = float(profile.get("token_balance", 0))
            new_balance = max(0, balance - billed)
            update_profile(profile["id"], {"token_balance": new_balance})

            # Record transaction
            supabase_request("POST", "/rest/v1/token_transactions", {
                "user_id": profile["id"],
                "amount": -billed,
                "transaction_type": "call_deduction",
                "balance_after": new_balance,
                "description": f"Call to {destination} ({duration}s)",
            })

        # Record call
        try:
            supabase_request("POST", "/rest/v1/calls", {
                "user_id": profile["id"],
                "caller_id_number": caller_id,
                "destination_number": destination,
                "duration_seconds": duration,
                "cost": billed,
                "status": "completed" if status == "COMPLETED" else "failed",
                "freeswitch_uuid": fs_uuid,
                "charged": billed > 0,
            })
        except Exception as e:
            log.warning(f"Failed to record call: {e}")

        self._send_json({
            "ok": True,
            "billed_tokens": billed,
            "user": {
                "id": profile["id"],
                "token_balance": float(profile.get("token_balance", 0)),
            },
            "vip_active": is_vip,
            "admin_unlimited": is_admin,
        })

    # ── CNAM LOOKUP ─────────────────────────────────────────────

    def _handle_health(self):
        """Health check endpoint — returns server status."""
        import subprocess as _sp
        try:
            fs = _sp.run(['pgrep','-c','asterisk'],capture_output=True).returncode==0
        except:
            fs = False
        try:
            mem = _sp.run(['free','-m'],capture_output=True,text=True).stdout
            mem = [l for l in mem.split('\n') if 'Mem:' in l]
            mem = mem[0].split()[2]+'/'+mem[0].split()[1]+' MB' if mem else 'N/A'
        except:
            mem = 'N/A'
        self._send_json({"ok":True,"server":"voip","ip":"34.225.190.118","instance":"t3.small","region":"us-east-1","os":"Ubuntu 22.04","freeswitch":fs,"backend":True,"dialer":True,"memory":mem})

    def _handle_cnam(self):
        """Twilio Lookup v2 — caller name lookup ($0.02/lookup)."""
        query = parse_qs(urlparse(self.path).query)
        number = query.get("number", [""])[0].strip()
        if not number:
            return self._send_error("Missing number parameter")

        digits = "".join(ch for ch in number if ch.isdigit())
        if not digits or len(digits) not in {10, 11}:
            return self._send_json({"ok": True, "number": digits, "label": "UNKNOWN"})

        # Normalize to E.164: 7804755555 → +17804755555
        if len(digits) == 10:
            e164 = f"+1{digits}"
        else:
            e164 = f"+{digits}"

        label = None

        try:
            import base64 as _b64
            creds = _b64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
            req = Request(
                f"https://lookups.twilio.com/v2/PhoneNumbers/{e164}?Fields=caller_name",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Accept": "application/json",
                },
            )
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            cn = data.get("caller_name") or {}
            if cn.get("caller_name") and not cn.get("error_code"):
                label = cn["caller_name"]
        except Exception:
            pass

        self._send_json({"ok": True, "number": digits, "label": label or "UNKNOWN"})

    # ── NOWPAYMENTS HANDLERS ────────────────────────────────────

    def _handle_create_topup(self):
        profile, err = self._require_auth()
        if err:
            return err

        body = self._read_body()
        token_amount = max(1, int(body.get("token_amount", 20)))
        pay_currency = str(body.get("pay_currency", "ltc")).lower()
        if pay_currency not in PAYMENT_COINS:
            return self._send_error("Currency must be btc or ltc")

        usd_amount = round(token_amount * TOKEN_PRICE, 2)
        user_id = profile["id"]
        username = self._get_session()["username"]

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

            # Store payment in Supabase
            supabase_request("POST", "/rest/v1/payments", {
                "user_id": user_id,
                "nowpayments_payment_id": payment_id,
                "order_id": np_body["order_id"],
                "pay_currency": pay_currency,
                "pay_amount": float(result.get("pay_amount", 0)),
                "price_amount": usd_amount,
                "pay_address": str(result.get("pay_address", "")),
                "payment_status": "waiting",
                "tokens_to_credit": token_amount,
            })

            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "invoice_url": result.get("invoice_url", ""),
                "pay_address": result.get("pay_address", ""),
                "pay_amount": float(result.get("pay_amount", 0)),
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
        pay_currency = str(body.get("pay_currency", "ltc")).lower()
        if pay_currency not in PAYMENT_COINS:
            return self._send_error("Currency must be btc or ltc")

        user_id = profile["id"]
        username = self._get_session()["username"]

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

            supabase_request("POST", "/rest/v1/payments", {
                "user_id": user_id,
                "nowpayments_payment_id": payment_id,
                "order_id": np_body["order_id"],
                "pay_currency": pay_currency,
                "pay_amount": float(result.get("pay_amount", 0)),
                "price_amount": VIP_PRICE,
                "pay_address": str(result.get("pay_address", "")),
                "payment_status": "waiting",
                "tokens_to_credit": 0,
            })

            self._send_json({
                "ok": True,
                "payment_id": payment_id,
                "invoice_url": result.get("invoice_url", ""),
                "pay_address": result.get("pay_address", ""),
                "pay_amount": float(result.get("pay_amount", 0)),
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
            payments = supabase_request("GET",
                f"/rest/v1/payments?nowpayments_payment_id=eq.{payment_id}&user_id=eq.{profile['id']}&select=*")
            if not payments or not isinstance(payments, list) or not payments:
                return self._send_error("Payment not found", 404)

            payment = payments[0]
            current_status = payment.get("payment_status", "waiting")

            # Poll NOWPayments if still pending
            if current_status in ("waiting", "confirming", "sending"):
                try:
                    np_status = nowpayments_request(f"/payment/{payment_id}")
                    new_status = str(np_status.get("payment_status", current_status))
                    supabase_request("PATCH",
                        f"/rest/v1/payments?nowpayments_payment_id=eq.{payment_id}",
                        {
                            "payment_status": new_status,
                            "ipn_raw": json.dumps(np_status),
                        })

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
                            supabase_request("POST", "/rest/v1/token_transactions", {
                                "user_id": profile["id"],
                                "amount": tokens,
                                "transaction_type": "purchase",
                                "balance_after": new_balance,
                                "description": f"Purchased {tokens} tokens via NOWPayments",
                            })

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

        users = supabase_request("GET", "/rest/v1/profiles?select=*&order=created_at.desc&limit=50")
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
        users = supabase_request("GET", "/rest/v1/profiles?select=id,role,token_balance,is_vip")
        payments = supabase_request("GET",
            "/rest/v1/payments?payment_status=eq.finished&select=price_amount,pay_currency")

        total_users = len(users) if isinstance(users, list) else 0
        total_revenue = sum(float(p.get("price_amount", 0)) for p in payments) if isinstance(payments, list) else 0
        vip_users = sum(1 for u in users if u.get("is_vip")) if isinstance(users, list) else 0

        self._send_json({
            "ok": True,
            "total_users": total_users,
            "total_revenue": round(total_revenue, 2),
            "vip_users": vip_users,
        })

    def _handle_admin_action(self):
        """Handle admin actions like adjusting balance."""
        profile, err = self._require_auth()
        if err:
            return err
        if profile.get("role") != "admin":
            return self._send_error("Admin access required", 403)

        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/admin/adjust-balance":
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

            supabase_request("POST", "/rest/v1/token_transactions", {
                "user_id": target_id,
                "amount": amount,
                "transaction_type": "admin_adjustment",
                "balance_after": new_balance,
                "description": f"Admin adjustment by {self._get_session()['username']}",
            })

            return self._send_json({"ok": True, "user_id": target_id, "new_balance": new_balance})

        self._send_error("Unknown admin action", 404)


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════


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
        target = str(body.get('target', '')).strip()
        caller_id = str(body.get('caller_id', '')).strip() or None
        if not target or len(target.replace('+','').replace('1','')) < 10:
            return self._send_error('Invalid target number')
        result = originate_call(target, caller_id)
        if result['ok']:
            return self._send_json(result)
        return self._send_error(result['error'])


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    server = HTTPServer(("0.0.0.0", API_PORT), ClawCallHandler)
    log.info(f"ClawCall backend listening on 0.0.0.0:{API_PORT}")
    log.info(f"Domain: {DOMAIN} | Supabase: {SUPABASE_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()