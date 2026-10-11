"""Seal a user's upstream token before it is stored."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def seal_token(token: str, key: str) -> str:
    nonce = os.urandom(16)
    body = _xor(token.encode("utf-8"), key, nonce)
    mac = hmac.new(key.encode("utf-8"), nonce + body, hashlib.sha256).digest()
    return base64.b64encode(nonce + mac + body).decode("ascii")


def open_token(ciphertext: str, key: str) -> str:
    raw = base64.b64decode(ciphertext.encode("ascii"))
    nonce, mac, body = raw[:16], raw[16:48], raw[48:]
    expected = hmac.new(key.encode("utf-8"), nonce + body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("stored credential cannot be opened")
    return _xor(body, key, nonce).decode("utf-8")


def _xor(payload: bytes, key: str, nonce: bytes) -> bytes:
    stream = b""
    counter = 0
    while len(stream) < len(payload):
        block = hmac.new(key.encode("utf-8"), nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        stream += block
        counter += 1
    return bytes(left ^ right for left, right in zip(payload, stream))
