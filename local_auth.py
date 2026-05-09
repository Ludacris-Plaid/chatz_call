import sqlite3, hashlib, secrets, time, logging
import bcrypt

DB_PATH = '/var/lib/asterisk/realtime.db'
log = logging.getLogger('clawcall.auth')

def _conn():
    return sqlite3.connect(DB_PATH)

def register_user(username: str, password: str) -> dict:
    try:
        conn = _conn()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            'INSERT INTO clawcall_users (username, password_hash) VALUES (?, ?)',
            [username.strip().lower(), pw_hash]
        )
        conn.commit()
        user_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        log.info(f'User registered: {username} (id={user_id})')
        return {'ok': True, 'user_id': str(user_id), 'username': username}
    except sqlite3.IntegrityError:
        return {'ok': False, 'error': 'Username already taken'}
    except Exception as e:
        log.error(f'Register failed: {e}')
        return {'ok': False, 'error': str(e)}

def login_user(username: str, password: str) -> dict:
    try:
        conn = _conn()
        row = conn.execute(
            'SELECT id, password_hash FROM clawcall_users WHERE username = ?',
            [username.strip().lower()]
        ).fetchone()
        conn.close()
        if not row:
            return {'ok': False, 'error': 'Invalid credentials'}
        user_id, pw_hash = row
        if not bcrypt.checkpw(password.encode(), pw_hash.encode()):
            return {'ok': False, 'error': 'Invalid credentials'}
        
        token = secrets.token_hex(32)
        expires = time.time() + 86400
        conn = _conn()
        conn.execute(
            'INSERT OR REPLACE INTO clawcall_sessions (token, user_id, username, expires_at) VALUES (?, ?, ?, ?)',
            [token, user_id, username, expires]
        )
        conn.commit()
        conn.close()
        return {'ok': True, 'token': token, 'user_id': str(user_id), 'username': username}
    except Exception as e:
        log.error(f'Login failed: {e}')
        return {'ok': False, 'error': str(e)}

def validate_session(token: str) -> dict:
    try:
        conn = _conn()
        row = conn.execute(
            'SELECT user_id, username, expires_at FROM clawcall_sessions WHERE token = ?',
            [token]
        ).fetchone()
        conn.close()
        if not row:
            return None
        user_id, username, expires = row
        if time.time() > expires:
            return None
        return {'user_id': str(user_id), 'username': username}
    except Exception as e:
        return None

def get_user_profile(user_id: str) -> dict:
    try:
        conn = _conn()
        row = conn.execute(
            'SELECT id, username, token_balance, is_vip, role FROM clawcall_users WHERE id = ?',
            [int(user_id)]
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            'id': str(row[0]),
            'username': row[1],
            'token_balance': row[2] or 0,
            'is_vip': bool(row[3]),
            'role': row[4] or 'user'
        }
    except:
        return None

def logout(token: str):
    try:
        conn = _conn()
        conn.execute('DELETE FROM clawcall_sessions WHERE token = ?', [token])
        conn.commit()
        conn.close()
    except:
        pass
