#!/usr/bin/env python3
"""Voucher system — Supabase backend."""

import os, json, random, string, logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kgnwqwghnosgieldiokc.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

log = logging.getLogger("clawcall.voucher")

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
        return []
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return []

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

def _patch(path, data, params):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    modifiers = {"select", "order", "limit", "offset"}
    parts = []
    for k, v in params.items():
        if k in modifiers:
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}=eq.{v}")
    url += "?" + "&".join(parts)
    body = json.dumps(data).encode()
    req = Request(url, data=body, headers=HEADERS, method="PATCH")
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        log.error(f"PATCH {path}: {e.code} {body[:200]}")
        return None
    except Exception as e:
        log.error(f"PATCH {path}: {e}")
        return None

def _generate_code(amount):
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"HUSH-{amount}-{suffix}"

def create_voucher(amount, created_by):
    code = _generate_code(amount)
    for _ in range(5):
        result = _post("clawcall_vouchers", {
            "code": code,
            "amount": int(amount),
            "created_by": created_by,
            "status": "active",
        })
        if result:
            return code
        code = _generate_code(amount)
    return None

def create_batch(amount, count, created_by):
    codes = []
    for _ in range(count):
        code = create_voucher(amount, created_by)
        if code:
            codes.append(code)
    return codes

def redeem_voucher(code, user_id):
    rows = _get("clawcall_vouchers", {"code": code, "select": "*"})
    if not rows:
        return ("Voucher not found", False)
    voucher = rows[0]
    if voucher.get("status") != "active":
        return ("Voucher already redeemed", False)
    
    result = _patch("clawcall_vouchers", {
        "status": "redeemed",
        "redeemed_by": int(user_id),
        "redeemed_at": "now()",
    }, {"code": code})
    
    if result:
        return (voucher.get("amount", 0), True)
    return ("Failed to redeem", False)

def list_vouchers(limit=50, redeemed_only=False):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    rows = _get("clawcall_vouchers", params)
    if not rows:
        return []
    if redeemed_only:
        rows = [r for r in rows if r.get("status") == "redeemed"]
    return rows
