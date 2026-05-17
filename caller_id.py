import os, socket, re, logging

AMI_HOST = os.environ.get("AMI_HOST", "172.21.0.1")
AMI_PORT = int(os.environ.get("AMI_PORT", "5038"))
AMI_USER = os.environ.get("AMI_USER", "clawcall")
AMI_SECRET = os.environ.get("AMI_SECRET", "clawcall_ami_secret_2026")
DEFAULT_CID = "17804755555"

# Per-process caller ID storage (set via /api/caller-id, used for calls without explicit caller_id)
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

def ami_command(action: dict) -> dict:
    try:
        s = socket.socket()
        s.settimeout(15)
        s.connect((AMI_HOST, AMI_PORT))
        s.recv(4096)
        login = "Action: Login\r\nUsername: " + AMI_USER + "\r\nSecret: " + AMI_SECRET + "\r\n\r\n"
        s.sendall(login.encode())
        resp = s.recv(4096).decode()
        if "Success" not in resp:
            s.close()
            return {"ok": False, "error": "AMI login failed"}
        lines = [k + ": " + v for k, v in action.items()]
        command = "\r\n".join(lines) + "\r\n\r\n"
        s.sendall(command.encode())
        result = ""
        while True:
            try:
                chunk = s.recv(4096).decode()
                if not chunk:
                    break
                result += chunk
                if "Originate successfully" in result:
                    break
            except socket.timeout:
                break
        s.close()
        if "Originate successfully" in result:
            return {"ok": True, "response": result}
        else:
            err_line = [l for l in result.split("\r\n") if "Message:" in l]
            return {"ok": False, "error": err_line[0] if err_line else result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def originate_call(target: str, caller_id: str = None) -> dict:
    digits = normalize_number(target)
    cid = normalize_caller_id(caller_id) if caller_id else _current_caller_id
    channel = "Local/" + digits + "@outbound-calls"
    action = {
        "Action": "Originate",
        "Channel": channel,
        "Application": "Echo",
        "CallerID": cid + " <" + cid + ">",
        "Timeout": "30000",
        "Async": "true",
    }
    result = ami_command(action)
    if result["ok"]:
        log.info("Call originated: " + digits + " from CID " + cid)
        return {"ok": True, "target": digits, "caller_id": cid, "channel": channel}
    else:
        err = result.get("error", "AMI originate failed")
        log.error("Call failed: " + str(err))
        return {"ok": False, "error": str(err)}

def get_caller_id() -> str:
    return _current_caller_id

def set_caller_id(number: str) -> bool:
    global _current_caller_id
    normalized = normalize_caller_id(number)
    if normalized:
        _current_caller_id = normalized
        return True
    return False
