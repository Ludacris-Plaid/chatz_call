import os, re, logging, time, uuid
from pathlib import Path

DEFAULT_CID = "18022221111"
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

# In-memory cache: extension -> caller_id
_cid_cache = {}

def _ami_dbput(extension: str, cid: str) -> bool:
    """Push caller ID to Asterisk AstDB via AMI."""
    import socket
    AMI_HOST = os.environ.get("AMI_HOST", "172.21.0.1")
    AMI_PORT = int(os.environ.get("AMI_PORT", "5038"))
    AMI_USER = os.environ.get("AMI_USER", "clawcall")
    AMI_SECRET = os.environ.get("AMI_SECRET", "clawcall_ami_secret_2026")
    try:
        cr = chr(13) + chr(10)
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((AMI_HOST, AMI_PORT))
        sock.recv(1024)
        sock.send(f"Action: Login{cr}Username: {AMI_USER}{cr}Secret: {AMI_SECRET}{cr}{cr}".encode())
        time.sleep(0.2)
        sock.recv(1024)
        sock.send(f"Action: DBput{cr}Family: CALLERID{cr}Key: {extension}{cr}Val: {cid}{cr}{cr}".encode())
        time.sleep(0.3)
        resp = sock.recv(4096).decode()
        sock.close()
        return "Response: Success" in resp
    except Exception as e:
        log.warning(f"AMI DBput failed for ext {extension}: {e}")
        return False

def _ami_dbget(extension: str) -> str:
    """Read caller ID from Asterisk AstDB via AMI."""
    import socket
    AMI_HOST = os.environ.get("AMI_HOST", "172.21.0.1")
    AMI_PORT = int(os.environ.get("AMI_PORT", "5038"))
    AMI_USER = os.environ.get("AMI_USER", "clawcall")
    AMI_SECRET = os.environ.get("AMI_SECRET", "clawcall_ami_secret_2026")
    try:
        cr = chr(13) + chr(10)
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((AMI_HOST, AMI_PORT))
        sock.recv(1024)
        sock.send(f"Action: Login{cr}Username: {AMI_USER}{cr}Secret: {AMI_SECRET}{cr}{cr}".encode())
        time.sleep(0.2)
        sock.recv(1024)
        sock.send(f"Action: DBget{cr}Family: CALLERID{cr}Key: {extension}{cr}{cr}".encode())
        time.sleep(0.3)
        resp = sock.recv(4096).decode()
        sock.close()
        for line in resp.split(chr(10)):
            if line.startswith("Val: "):
                return line[5:].strip()
        return ""
    except Exception as e:
        log.warning(f"AMI DBget failed for ext {extension}: {e}")
        return ""

def get_caller_id(extension: str = None) -> str:
    """Get caller ID for an extension. Falls back to AstDB, then default."""
    if extension and extension in _cid_cache:
        return _cid_cache[extension]
    if extension:
        db_val = _ami_dbget(extension)
        if db_val:
            _cid_cache[extension] = db_val
            return db_val
    return DEFAULT_CID

def set_caller_id(number: str, extension: str = None) -> bool:
    """Store caller ID in memory cache and push to Asterisk AstDB."""
    cid = normalize_caller_id(number)
    if not cid:
        return False
    if extension:
        _cid_cache[extension] = cid
        _ami_dbput(extension, cid)
    return True