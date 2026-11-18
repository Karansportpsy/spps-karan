import hashlib,hmac,secrets,time
from datetime import datetime, timedelta, timezone

def make_password(raw):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',raw.encode(),salt,390000)
    return 'pbkdf2_sha256$390000$'+salt.hex()+'$'+digest.hex()

def verify_password(raw,encoded):
    try:
        algo,rounds,salt,expected=encoded.split('$')
        if algo!='pbkdf2_sha256': return False
        v=hashlib.pbkdf2_hmac('sha256',raw.encode(),bytes.fromhex(salt),int(rounds))
        return hmac.compare_digest(v,bytes.fromhex(expected))
    except Exception:return False

def new_token():return secrets.token_urlsafe(36)
def hash_token(token):return hashlib.sha256(token.encode()).hexdigest()
def expiry(hours=10):return datetime.now(timezone.utc)+timedelta(hours=hours)
def is_expired(dt):
    if not dt:return True
    if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
    return dt<datetime.now(timezone.utc)
