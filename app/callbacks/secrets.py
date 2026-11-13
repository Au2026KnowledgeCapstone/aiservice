import base64
import hashlib
from cryptography.fernet import Fernet


def token_cipher(config) -> Fernet:
    """Use a dedicated key in production; a stable service token supports local A1."""
    if config.callback_encryption_key:
        return Fernet(config.callback_encryption_key.encode())
    if not config.service_token.strip():
        raise ValueError("Callback token encryption requires a key or service token")
    key = hashlib.sha256(b"blindspot-callback-token-v1\0" + config.service_token.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))
