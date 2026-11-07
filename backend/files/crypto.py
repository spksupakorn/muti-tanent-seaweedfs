"""Envelope encryption (ADR 0001).

master key -> wraps each Tenant key -> wraps each File's data key -> encrypts the File body.

A File body is cut into 1 MiB segments, each sealed with AES-256-GCM under the File's own data
key. The nonce is the segment number. The "last segment" flag is authenticated data, so dropping
the end, appending, or reordering segments fails the check instead of yielding altered content.
"""
import os
from collections.abc import Iterable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

SEGMENT = 1 << 20
TAG = 16
BLOCK = SEGMENT + TAG


class IntegrityError(Exception):
    """Stored bytes do not authenticate: tampered, truncated, reordered, or wrong key."""


def _wrap(kek: bytes, key: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(kek).encrypt(nonce, key, aad)


def _unwrap(kek: bytes, blob: bytes, aad: bytes) -> bytes:
    try:
        return AESGCM(kek).decrypt(blob[:12], blob[12:], aad)
    except InvalidTag as e:
        raise IntegrityError("key does not authenticate") from e


def _master() -> bytes:
    if settings.MASTER_KEY is None:
        raise ImproperlyConfigured("MASTER_KEY is not set")
    return settings.MASTER_KEY


def new_tenant_key(tenant_id) -> tuple[bytes, bytes]:
    key = AESGCM.generate_key(bit_length=256)
    return key, _wrap(_master(), key, b"tenant-key:" + tenant_id.bytes)


def unwrap_tenant_key(tenant_id, wrapped: bytes) -> bytes:
    return _unwrap(_master(), wrapped, b"tenant-key:" + tenant_id.bytes)


def new_data_key(tenant_key: bytes, file_id) -> tuple[bytes, bytes]:
    key = AESGCM.generate_key(bit_length=256)
    return key, _wrap(tenant_key, key, b"data-key:" + file_id.bytes)


def unwrap_data_key(tenant_key: bytes, file_id, wrapped: bytes) -> bytes:
    return _unwrap(tenant_key, wrapped, b"data-key:" + file_id.bytes)


def stored_size(plain_size: int) -> int:
    """Bytes a File occupies in storage: its content plus one tag per segment (an empty file is one segment)."""
    return plain_size + TAG * max(1, -(-plain_size // SEGMENT))


def _nonce(index: int) -> bytes:
    return index.to_bytes(12, "big")


def _aad(final: bool) -> bytes:
    return b"\x01" if final else b"\x00"


def _read_exact(src, n: int) -> bytes:
    parts, got = [], 0
    while got < n:
        chunk = src.read(n - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def encrypt_stream(src, key: bytes) -> Iterator[bytes]:
    """Yield sealed segments of a file-like `src`. An empty file is one empty final segment."""
    aead = AESGCM(key)
    current = _read_exact(src, SEGMENT)
    index = 0
    while True:
        following = _read_exact(src, SEGMENT)
        final = not following
        yield aead.encrypt(_nonce(index), current, _aad(final))
        if final:
            return
        current, index = following, index + 1


class EncryptingReader:
    """File-like view of encrypt_stream, for streaming uploads with constant memory."""

    def __init__(self, src, key: bytes):
        self._segments = encrypt_stream(src, key)
        self._buf = b""

    def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self._buf) < size:
            seg = next(self._segments, None)
            if seg is None:
                break
            self._buf += seg
        if size < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:size], self._buf[size:]
        return out


def _blocks(chunks: Iterable[bytes]) -> Iterator[bytes]:
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        while len(buf) >= BLOCK:
            yield bytes(buf[:BLOCK])
            del buf[:BLOCK]
    if buf:
        yield bytes(buf)


def decrypt_stream(chunks: Iterable[bytes], key: bytes) -> Iterator[bytes]:
    """Yield plaintext segments. Raises IntegrityError on any alteration, before yielding that segment."""
    aead = AESGCM(key)
    blocks = _blocks(chunks)
    current = next(blocks, None)
    if current is None:
        raise IntegrityError("stored object is empty")
    index = 0
    try:
        for following in blocks:
            yield aead.decrypt(_nonce(index), current, _aad(False))
            current, index = following, index + 1
        yield aead.decrypt(_nonce(index), current, _aad(True))
    except InvalidTag as e:
        raise IntegrityError(f"segment {index} does not authenticate") from e
