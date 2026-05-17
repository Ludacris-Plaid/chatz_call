import os, re, logging, time
from pathlib import Path

DEFAULT_CID = "17804755555"
CALL_SPOOL = Path("/var/spool/asterisk/outgoing")

_current_caller_id = DEFAULT_CID

log = logging.getLogger("clawcall.cid")

def normalize_number(num: str) -> str:
    if not num:
        return ""
    digits = re.sub(r"\D", "", num)
    if len(digits) == 10:
        return "1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        return digits
    return digits

def normalize_caller_id(cid: str) -> str:
    if not cid:
        return DEFAULT_CID
    return normalize_number(cid)

def originate_call(target: str, caller_id: str = None) -> dict:
    digits = normalize_number(target)
    cid = normalize_caller_id(caller_id) if caller_id else _current_caller_id
    
    # Use call file method — AMI originate to Local channels is unreliable
    ts = str(int(time.time() * 1000000))
    call_file = CALL_SPOOL / f"clawcall_{digits}_{ts}.call"
    
    content = (
        f"Channel: Local/{digits}@public\n"
        f"CallerID: {cid} <{cid}>\n"
        f"MaxRetries: 0\n"
        f"RetryTime: 0\n"
        f"WaitTime: 30\n"
        f"Application: Echo\n"
    )
    
    try:
        call_file.write_text(content)
        log.info(f"Call originated: {digits} from CID {cid} (call file: {call_file.name})")
        return {"ok": True, "target": digits, "caller_id": cid, "channel": f"Local/{digits}@public"}
    except Exception as e:
        log.error(f"Call file write failed: {e}")
        return {"ok": False, "error": str(e)}

def get_caller_id() -> str:
    return _current_caller_id

def set_caller_id(number: str) -> bool:
    global _current_caller_id
    normalized = normalize_caller_id(number)
    if normalized:
        _current_caller_id = normalized
        return True
    return False
