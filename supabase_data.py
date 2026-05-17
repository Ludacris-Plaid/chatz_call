#!/usr/bin/env python3
"""Supabase REST API data layer — drop-in replacement for clawcall_data.py.
Uses service_role key for all operations. No RLS, no auth overhead."""

import os, json, time, logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

log = logging.getLogger("clawcall.data")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
    "User-Agent": "ClawCall/2.0",
}

def _rpc(fn_name, params=None):
    """Call a Postgres function via Supabase RPC."""
    url = f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}"
    body = json.dumps(params or {}).encode()
    req = Request(url, data=body, headers=HEADERS, method="POST")
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"RPC {fn_name} failed: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"RPC {fn_name} exception: {e}")
        return None

def _get(path, params=None):
    """GET from Supabase REST API."""
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
        body = e.read().decode(errors="replace")
        log.error(f"GET {path} failed: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"GET {path} exception: {e}")
        return None

def _post(path, data):
    """POST to Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    body = json.dumps(data).encode()
    headers = dict(HEADERS)
    headers["Prefer"] = "return=representation"
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"POST {path} failed: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"POST {path} exception: {e}")
        return None

def _patch(path, data, params=None):
    """PATCH to Supabase REST API."""
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
    body = json.dumps(data).encode()
    headers = dict(HEADERS)
    headers["Prefer"] = "return=representation"
    req = Request(url, data=body, headers=headers, method="PATCH")
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"PATCH {path} failed: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"PATCH {path} exception: {e}")
        return None

def _delete(path, params=None):
    """DELETE from Supabase REST API."""
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
        return resp.status == 204 or resp.status == 200
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"DELETE {path} failed: {e.code} {body[:200]}")
        return False
    except Exception as e:
        log.error(f"DELETE {path} exception: {e}")
        return False

def _escape_val(v):
    """Escape value for Supabase URL query params."""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).replace("'", "''")

# ── Profile Operations ─────────────────────────────────────────────

def get_profile(user_id):
    rows = _get("clawcall_users", {"id": str(int(user_id)), "select": "*"})
    if not rows:
        return None
    row = rows[0]
    # Normalize field names to match SQLite format
    return {
        "id": str(row["id"]),
        "username": row.get("username", ""),
        "password_hash": row.get("password_hash", ""),
        "email": row.get("email", ""),
        "role": row.get("role", "user"),
        "status": row.get("status", "active"),
        "sip_extension": row.get("sip_extension", ""),
        "caller_id": row.get("caller_id", ""),
        "token_balance": float(row.get("tokens", 0)),
        "tokens": float(row.get("tokens", 0)),
        "is_vip": row.get("role") == "vip",
        "is_banned": row.get("status") == "banned",
        "is_suspended": row.get("status") == "suspended",
    }

def update_profile(user_id, updates):
    # Map field names for Supabase
    # Schema: id, username, password_hash, email, role, status, sip_extension, caller_id, tokens, created_at, updated_at, sip_password
    supa_updates = {}
    vip_value = None
    status_value = None  # derived from is_banned/is_suspended
    for k, v in updates.items():
        if k in ("id", "created_at"):
            continue
        if k == "is_vip":
            vip_value = v
        elif k == "is_banned":
            if v:
                status_value = "banned"
            elif status_value == "banned":
                status_value = "active"
        elif k == "is_suspended":
            if v:
                status_value = "suspended"
            elif status_value == "suspended":
                status_value = "active"
        elif k == "vip_expires_at":
            supa_updates["vip_expires_at"] = v
        elif k == "token_balance":
            supa_updates["tokens"] = float(v)
        elif k == "role":
            pass  # derived from is_vip
        elif k == "status":
            pass  # derived from is_banned/is_suspended
        else:
            supa_updates[k] = v
    if vip_value is not None:
        # Check current role first - never overwrite admin
        existing = _get("clawcall_users", {"id": str(int(user_id)), "select": "role"})
        current_role = existing[0].get("role") if existing else None
        if current_role == "admin":
            pass  # Never change admin role
        elif vip_value:
            supa_updates["role"] = "vip"
            # Log VIP activation for expiry tracking (7 days)
            expiry_ts = time.time() + 7 * 86400
            log.info(f"VIP activated for user {user_id}, expires {time.strftime('%Y-%m-%d', time.localtime(expiry_ts))}")
            _post("clawcall_transactions", {
                "user_id": int(user_id),
                "amount": 0,
                "transaction_type": "vip_activated",
                "balance_after": 0,
                "description": str(expiry_ts),
            })
        else:
            supa_updates["role"] = "user"
            # Log VIP deactivation
            _post("clawcall_transactions", {
                "user_id": int(user_id),
                "amount": 0,
                "transaction_type": "vip_deactivated",
                "balance_after": 0,
                "description": str(time.time()),
            })
    if status_value is not None:
        supa_updates["status"] = status_value
    supa_updates["updated_at"] = "now()"
    return _patch("clawcall_users", supa_updates, {"id": str(int(user_id))})

def list_profiles(limit=50):
    rows = _get("clawcall_users", {
        "select": "id,username,tokens,role,status",
        "order": "id.desc",
        "limit": str(limit),
    })
    if not rows:
        return []
    result = []
    for r in rows:
        result.append({
            "id": str(r["id"]),
            "username": r.get("username", ""),
            "token_balance": float(r.get("tokens", 0)),
            "is_vip": r.get("role") == "vip",
            "role": r.get("role", "user"),
            "is_banned": r.get("status") == "banned",
            "is_suspended": r.get("status") == "suspended",
        })
    return result

def get_all_profiles():
    rows = _get("clawcall_users", {"select": "id,role,tokens"})
    if not rows:
        return []
    result = []
    for r in rows:
        result.append({
            "id": str(r["id"]),
            "role": r.get("role", "user"),
            "token_balance": float(r.get("tokens", 0)),
            "is_vip": r.get("role") == "vip",
        })
    return result

# ── Transaction Logging ────────────────────────────────────────────

def log_transaction(user_id, amount, tx_type, balance_after, description=""):
    data = {
        "user_id": int(user_id),
        "amount": float(amount),
        "transaction_type": tx_type,
        "balance_after": float(balance_after),
        "description": description,
    }
    # Using clawcall_transactions table
    return _post("clawcall_transactions", data)

def log_call(user_id, caller_id, destination, duration=0, cost=0, status="completed"):
    data = {
        "user_id": int(user_id),
        "caller_id": str(caller_id),
        "target_number": str(destination),
        "duration_seconds": int(duration),
        "cost": float(cost),
        "tokens_used": float(cost),
        "status": status,
    }
    return _post("clawcall_calls", data)

def log_payment(user_id, amount, payment_id, status="pending"):
    data = {
        "user_id": int(user_id),
        "amount": float(amount),
        "payment_id": str(payment_id),
        "payment_status": status,
    }
    return _post("clawcall_deposits", data)

# ── Query Operations ───────────────────────────────────────────────

def get_user_transactions(user_id, limit=20):
    rows = _get("clawcall_transactions", {
        "user_id": str(int(user_id)),
        "select": "*",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    if not rows:
        return []
    for r in rows:
        r["id"] = str(r["id"])
        r["user_id"] = str(r["user_id"])
    return rows

def get_user_calls(user_id, limit=30):
    rows = _get("clawcall_calls", {
        "user_id": str(int(user_id)),
        "select": "*",
        "order": "started_at.desc",
        "limit": str(limit),
    })
    if not rows:
        return []
    for r in rows:
        r["id"] = str(r["id"])
        r["user_id"] = str(r["user_id"])
        # Map to SQLite field names for compatibility
        r["destination"] = r.get("target_number", "")
        r["duration"] = r.get("duration_seconds", 0)
        r["created_at"] = r.get("started_at", "")
    return rows


def get_all_calls(limit=500):
    """Get all calls across all users."""
    rows = _get("clawcall_calls", {
        "select": "*",
        "order": "started_at.desc",
        "limit": str(limit)
    })
    return rows if rows else []

def get_user_payments(user_id, limit=20):
    rows = _get("clawcall_deposits", {
        "user_id": str(int(user_id)),
        "select": "*",
        "order": "created_at.desc",
        "limit": str(limit),
    })
    if not rows:
        return []
    for r in rows:
        r["id"] = str(r["id"])
        r["user_id"] = str(r["user_id"])
        r["status"] = r.get("payment_status", "pending")
    return rows

def get_payment(payment_id):
    rows = _get("clawcall_deposits", {
        "payment_id": str(payment_id),
        "select": "*",
    })
    if not rows:
        return None
    r = rows[0]
    r["id"] = str(r["id"])
    r["user_id"] = str(r["user_id"])
    r["status"] = r.get("payment_status", "pending")
    return r

def update_payment(payment_id, updates):
    supa_updates = {}
    for k, v in updates.items():
        if k == "status":
            supa_updates["payment_status"] = v
        else:
            supa_updates[k] = v
    supa_updates["updated_at"] = "now()"
    return _patch("clawcall_deposits", supa_updates, {"payment_id": str(payment_id)})

def get_next_sip_extension():
    rows = _get("clawcall_users", {
        "select": "sip_extension",
        "order": "sip_extension.desc.nullsfirst",
        "limit": "1",
    })
    if not rows or not rows[0].get("sip_extension"):
        return "1001"
    try:
        return str(int(rows[0]["sip_extension"]) + 1)
    except (ValueError, TypeError):
        return "1001"

# ── CNAM Cache ─────────────────────────────────────────────────────

def get_cnam(number):
    rows = _get("clawcall_cnam_cache", {
        "number": str(number),
        "select": "*",
    })
    if not rows:
        return None
    return rows[0]

def cache_cnam(number, name):
    data = {"number": str(number), "name": str(name)}
    return _post("clawcall_cnam_cache", data)

# ── Init ────────────────────────────────────────────────────────────

def init_tables():
    """Supabase tables are created via migrations. No-op here."""
    pass

# ── Transaction table ──────────────────────────────────────────────

def ensure_transactions_table():
    """Create clawcall_transactions if it doesn't exist. Uses Management API."""
    pass  # TODO: create via migration
