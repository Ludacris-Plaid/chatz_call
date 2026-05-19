import os, re, logging, time, uuid
from pathlib import Path

DEFAULT_CID = "17804755555"
CALL_SPOOL = Path("/var/spool/asterisk/outgoing")

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
    digits = re.sub(r"\D", "", cid)
    if len(digits) < 10:
        return DEFAULT_CID
    if len(digits) == 10:
        return "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return digits
    if len(digits) > 11:
        return digits[-11:]
    return digits

def originate_call(target: str, caller_id: str = None, user_id: int = None) -> dict:
    digits = normalize_number(target)
    cid = normalize_caller_id(caller_id)
    
    call_id = str(uuid.uuid4())[:8]
    ts = str(int(time.time() * 1000000))
    call_file = CALL_SPOOL / f"clawcall_{digits}_{ts}.call"
    
    content = (
        f"Channel: Local/{digits}@public\n"
        f"CallerID: {cid} <{cid}>\n"
        f"MaxRetries: 0\n"
        f"RetryTime: 1\n"
        f"WaitTime: 45\n"
        f"Application: Echo\n"
    )
    
    try:
        call_file.write_text(content)
        log.info(f"Call originated: {digits} from CID {cid}, call_id={call_id}")
        return {
            "ok": True, 
            "target": digits, 
            "caller_id": cid, 
            "channel": f"Local/{digits}@public",
            "call_id": call_id
        }
    except Exception as e:
        log.error(f"Call file write failed: {e}")
        return {"ok": False, "error": str(e)}

def get_caller_id() -> str:
    return DEFAULT_CID

def set_caller_id(number: str) -> bool:
    return True