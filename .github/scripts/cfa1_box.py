"""Seal and open the CFA study-workbook extract (state/cfa1/workbook.enc).

The extract carries Logan's own nightly notes, and this repository is public, so it is stored
encrypted. The construction uses only the standard library, so it runs unchanged on his PC and in
the routine's sandbox: HMAC-SHA256 in counter mode as the keystream, then HMAC-SHA256 over nonce
and ciphertext (encrypt-then-MAC), with separate derived keys for the two jobs. The 32-byte key
lives in %USERPROFILE%\\.cfa1\\workbook.key on his PC and in the routine's environment as
CFA1_WORKBOOK_KEY. It is never written to the repository.
"""

import base64
import hashlib
import hmac
import os
import zlib

MAGIC = b"CFA1WB1\n"


def load_key(text):
    key = base64.b64decode(str(text).strip())
    if len(key) != 32:
        raise ValueError("the workbook key must be 32 bytes, base64-encoded")
    return key


def _subkeys(key):
    return (hmac.new(key, b"cfa1 workbook enc", hashlib.sha256).digest(),
            hmac.new(key, b"cfa1 workbook mac", hashlib.sha256).digest())


def _xor_stream(k, nonce, data):
    out = bytearray(len(data))
    for i in range(0, len(data), 32):
        block = hmac.new(k, nonce + (i // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        out[i:i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, block))
    return bytes(out)


def seal(key, plaintext):
    enc, mac = _subkeys(key)
    nonce = os.urandom(16)
    body = _xor_stream(enc, nonce, zlib.compress(plaintext, 9))
    tag = hmac.new(mac, MAGIC + nonce + body, hashlib.sha256).digest()
    return MAGIC + base64.b64encode(nonce + body + tag) + b"\n"


def open_sealed(key, blob):
    if not blob.startswith(MAGIC):
        raise ValueError("not a sealed workbook extract")
    raw = base64.b64decode(blob[len(MAGIC):].strip())
    if len(raw) < 48:
        raise ValueError("sealed extract is truncated")
    nonce, body, tag = raw[:16], raw[16:-32], raw[-32:]
    enc, mac = _subkeys(key)
    if not hmac.compare_digest(tag, hmac.new(mac, MAGIC + nonce + body, hashlib.sha256).digest()):
        raise ValueError("wrong key, or the file is damaged")
    return zlib.decompress(_xor_stream(enc, nonce, body))
