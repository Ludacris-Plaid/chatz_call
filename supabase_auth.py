#!/usr/bin/env python3
"""Supabase auth layer — drop-in replacement for local_auth.py.
Uses Supabase REST API with service_role key."""

import os, json, time, secrets, logging, bcrypt
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

log = logging.getLogger("clawcall.auth")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
    "User-Agent": "ClawCall/2.0",
}

def _get(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        modifiers = {"select", "order", "limit", "offset"}
        parts = []
        for k, v in params.items():
            if k in modifiers:
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}=eq.{v}")
        url += "?" + "&".join(parts)
    req = Request(url, headers=HEADERS)
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        if e.code == 404:
            return []
        log.error(f"GET {path}: {e.code}")
        return None
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return None

def _post(path, data):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    body = json.dumps(data).encode()
    req = Request(url, data=body, headers=HEADERS, method="POST")
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"POST {path}: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"POST {path}: {e}")
        return None

def _delete(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        modifiers = {"select", "order", "limit", "offset"}
        parts = []
        for k, v in params.items():
            if k in modifiers:
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}=eq.{v}")
        url += "?" + "&".join(parts)
    req = Request(url, headers=HEADERS, method="DELETE")
    try:
        resp = urlopen(req, timeout=10)
        return resp.status in (200, 204)
    except Exception as e:
        log.error(f"DELETE {path}: {e}")
        return False

def register_user(username: str, password: str) -> dict:
    try:
        username = username.strip().lower()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        
        data = {
            "username": username,
            "password_hash": pw_hash,
            "role": "user",
            "status": "active",
            "tokens": 0,
        }
        result = _post("clawcall_users", data)
        if not result:
            return {"ok": False, "error": "Username may already be taken"}
        
        user = result[0] if isinstance(result, list) else result
        log.info(f"User registered: {username} (id={user['id']})")
        return {"ok": True, "user_id": str(user["id"]), "username": username}
    except Exception as e:
        log.error(f"Register failed: {e}")
        return {"ok": False, "error": str(e)}

def login_user(username: str, password: str) -> dict:
    try:
        username = username.strip().lower()
        rows = _get("clawcall_users", {
            "username": username,
            "select": "id,password_hash,status,role",
        })
        
        if not rows:
            return {"ok": False, "error": "Invalid credentials"}
        
        user = rows[0]
        
        # Check ban/suspension
        if user.get("status") == "banned":
            return {"ok": False, "error": "Account is banned"}
        if user.get("status") == "suspended":
            return {"ok": False, "error": "Account is suspended"}
        
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return {"ok": False, "error": "Invalid credentials"}
        
        user_id = user["id"]
        token = secrets.token_hex(32)
        expires = time.time() + 86400
        
        # Create session in Supabase
        session_data = {
            "token": token,
            "user_id": int(user_id),
            "username": username,
            "expires_at": expires,
        }
        _post("clawcall_sessions", session_data)
        
        return {"ok": True, "token": token, "user_id": str(user_id), "username": username}
    except Exception as e:
        log.error(f"Login failed: {e}")
        return {"ok": False, "error": str(e)}

def validate_session(token: str) -> dict:
    try:
        rows = _get("clawcall_sessions", {
            "token": token,
            "select": "user_id,username,expires_at",
        })
        if not rows:
            return None
        
        session = rows[0]
        if time.time() > session["expires_at"]:
            _delete("clawcall_sessions", {"token": token})
            return None
        
        return {"user_id": str(session["user_id"]), "username": session["username"]}
    except Exception as e:
        return None

def get_user_profile(user_id: str) -> dict:
    try:
        rows = _get("clawcall_users", {
            "id": str(int(user_id)),
            "select": "id,username,tokens,role,sip_extension,status",
        })
        if not rows:
            return None
        user = rows[0]
        return {
            "id": str(user["id"]),
            "username": user["username"],
            "token_balance": float(user.get("tokens", 0)),
            "is_vip": user.get("role") == "vip",
            "role": user.get("role", "user"),
            "sip_extension": user.get("sip_extension", ""),
            "sip_password": user.get("sip_password", ""),
        }
    except Exception:
        return None

def logout(token: str):
    try:
        _delete("clawcall_sessions", {"token": token})
    except Exception:
        pass
