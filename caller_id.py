import os, socket, re, logging

AMI_HOST = os.environ.get('AMI_HOST', '172.21.0.1')
AMI_PORT = int(os.environ.get('AMI_PORT', '5038'))
AMI_USER = os.environ.get('AMI_USER', 'clawcall')
AMI_SECRET = os.environ.get('AMI_SECRET', 'clawcall_ami_secret_2026')

log = logging.getLogger('clawcall.cid')

def normalize_number(num: str) -> str:
    digits = re.sub(r'\D', '', num)
    if len(digits) == 10:
        return '1' + digits
    elif len(digits) == 11 and digits.startswith('1'):
        return digits
    return digits

def normalize_caller_id(cid: str) -> str:
    return normalize_number(cid)

def ami_command(action: dict) -> dict:
    try:
        s = socket.socket()
        s.settimeout(15)
        s.connect((AMI_HOST, AMI_PORT))
        s.recv(4096)
        login = f'Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {AMI_SECRET}\r\n\r\n'
        s.sendall(login.encode())
        resp = s.recv(4096).decode()
        if 'Success' not in resp:
            s.close()
            return {'ok': False, 'error': 'AMI login failed'}
        lines = [f'{k}: {v}' for k, v in action.items()]
        command = '\r\n'.join(lines) + '\r\n\r\n'
        s.sendall(command.encode())
        result = ''
        while True:
            try:
                chunk = s.recv(4096).decode()
                if not chunk:
                    break
                result += chunk
                if 'Response:' in result:
                    break
            except socket.timeout:
                break
        s.close()
        if 'Success' in result:
            return {'ok': True, 'response': result}
        else:
            err_line = [l for l in result.split('\r\n') if 'Message:' in l]
            return {'ok': False, 'error': err_line[0] if err_line else result}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def originate_call(target: str, caller_id: str = '17804755555') -> dict:
    digits = normalize_number(target)
    cid = normalize_caller_id(caller_id)
    # Use dialplan routing via Local/ channel — reliable, sets CallerID properly
    # AMI direct PJSIP originate fails with 'Failure' in this container
    channel = f'Local/{digits}@public'
    action = {
        'Action': 'Originate',
        'Channel': channel,
        'Application': 'Echo',
        'CallerID': f'"{cid}" <{cid}>',
        'Timeout': '30000',
        'Async': 'true',
    }
    result = ami_command(action)
    if result['ok']:
        log.info(f'Call originated: {digits} from CID {cid}')
        return {'ok': True, 'target': digits, 'caller_id': cid, 'channel': channel}
    else:
        log.error(f'Call failed: {result["error"]}')
        return {'ok': False, 'error': result['error']}

def get_caller_id() -> str:
    return '17804755555'

def set_caller_id(number: str) -> bool:
    return True
