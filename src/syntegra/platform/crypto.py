"""Field-level encryption and blind indexes for Restricted data (CEDA §8.1).

* **Encryption:** AES-256-GCM with envelope keys. Each tenant gets a data key (DEK), wrapped
  with the key-encryption key (KEK) by AES Key Wrap (RFC 3394) and stored beside every
  ciphertext. In production the KEK is an AWS KMS key; ``LocalKeyProvider`` is the dev/test
  stand-in (ADR-008).
* **Associated data** binds a ciphertext to its tenant, table, column and row, so a value
  copied onto another record fails to decrypt.
* **Blind index:** HMAC-SHA256 under a separate regional key gives exact-match lookup and
  uniqueness without decrypting (``external_identifier.value_hmac``).

Ciphertext layout (v1):
``01 | kek_id (8) | len(wrapped_dek) (1) | wrapped_dek | nonce (12) | ct+tag``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

VERSION = 1
DEK_TTL_SECONDS = 600


class DecryptionError(Exception):
    """Ciphertext is corrupt, was moved to another row, or the key is not available."""


class KeyProvider(Protocol):
    """Wraps and unwraps data keys under a key-encryption key (KMS in production)."""

    @property
    def kek_id(self) -> bytes: ...

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, kek_id: bytes, wrapped: bytes) -> bytes: ...


class LocalKeyProvider:
    """KEK held in process memory. Dev/test only; production uses AWS KMS (ADR-008)."""

    def __init__(self, kek: bytes, previous: list[bytes] | None = None) -> None:
        if len(kek) != 32:
            raise ValueError("KEK must be 32 bytes")
        self._keys = {self._id(k): k for k in [kek, *(previous or [])]}
        self._active = self._id(kek)

    @staticmethod
    def _id(key: bytes) -> bytes:
        return hashlib.sha256(b"kek-id|" + key).digest()[:8]

    @property
    def kek_id(self) -> bytes:
        return self._active

    def wrap(self, dek: bytes) -> bytes:
        return aes_key_wrap(self._keys[self._active], dek)

    def unwrap(self, kek_id: bytes, wrapped: bytes) -> bytes:
        key = self._keys.get(kek_id)
        if key is None:
            raise DecryptionError("key-encryption key is not available")
        try:
            return aes_key_unwrap(key, wrapped)
        except Exception as exc:
            raise DecryptionError("data key could not be unwrapped") from exc


@dataclass(frozen=True, slots=True)
class _CachedDek:
    dek: bytes
    wrapped: bytes
    kek_id: bytes
    created: float


class FieldCipher:
    def __init__(self, provider: KeyProvider) -> None:
        self._provider = provider
        self._cache: dict[uuid.UUID, _CachedDek] = {}
        self._lock = threading.Lock()

    def _dek(self, tenant_id: uuid.UUID) -> _CachedDek:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(tenant_id)
            if (
                cached
                and now - cached.created < DEK_TTL_SECONDS
                and cached.kek_id == self._provider.kek_id
            ):
                return cached
            dek = AESGCM.generate_key(bit_length=256)
            cached = _CachedDek(dek, self._provider.wrap(dek), self._provider.kek_id, now)
            self._cache[tenant_id] = cached
            return cached

    @staticmethod
    def _aad(tenant_id: uuid.UUID, table: str, column: str, row_id: uuid.UUID) -> bytes:
        return f"v{VERSION}|{tenant_id}|{table}|{column}|{row_id}".encode()

    def encrypt(
        self, plaintext: str, *, tenant_id: uuid.UUID, table: str, column: str, row_id: uuid.UUID
    ) -> bytes:
        key = self._dek(tenant_id)
        nonce = os.urandom(12)
        ct = AESGCM(key.dek).encrypt(
            nonce, plaintext.encode("utf-8"), self._aad(tenant_id, table, column, row_id)
        )
        return bytes([VERSION]) + key.kek_id + bytes([len(key.wrapped)]) + key.wrapped + nonce + ct

    def decrypt(
        self, blob: bytes, *, tenant_id: uuid.UUID, table: str, column: str, row_id: uuid.UUID
    ) -> str:
        try:
            if blob[0] != VERSION:
                raise DecryptionError("unknown ciphertext version")
            kek_id = blob[1:9]
            n = blob[9]
            wrapped = blob[10 : 10 + n]
            nonce = blob[10 + n : 22 + n]
            ct = blob[22 + n :]
            dek = self._provider.unwrap(kek_id, wrapped)
            return (
                AESGCM(dek).decrypt(nonce, ct, self._aad(tenant_id, table, column, row_id)).decode()
            )
        except DecryptionError:
            raise
        except Exception as exc:
            raise DecryptionError("value could not be decrypted") from exc


class BlindIndex:
    """HMAC-SHA256 under a regional key, so equal values match across tenants in a region
    (needed for future state-wide de-duplication) without the key ever touching the DB."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("blind index key must be at least 32 bytes")
        self._key = key

    def digest(self, domain: str, normalized_value: str) -> bytes:
        return hmac.new(self._key, f"{domain}:{normalized_value}".encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class FieldCrypto:
    cipher: FieldCipher
    index: BlindIndex


DEV_KEY_LABEL = b"syntegra-dev-only-not-a-secret"


def _dev_key(purpose: bytes) -> bytes:
    # Fixed, public dev key so a local database stays readable across restarts.
    return hashlib.sha256(DEV_KEY_LABEL + b"|" + purpose).digest()


def build_field_crypto(
    *, env: str, kek_b64: str | None, previous_keks_b64: list[str], blind_index_key_b64: str | None
) -> FieldCrypto:
    if kek_b64 is None or blind_index_key_b64 is None:
        if env not in ("dev", "test"):
            raise RuntimeError(
                "SYNTEGRA_FIELD_KEK_B64 and SYNTEGRA_BLIND_INDEX_KEY_B64 are required "
                "outside dev/test"
            )
        kek = base64.b64decode(kek_b64) if kek_b64 else _dev_key(b"kek")
        bidx = (
            base64.b64decode(blind_index_key_b64)
            if blind_index_key_b64
            else _dev_key(b"blind-index")
        )
    else:
        kek, bidx = base64.b64decode(kek_b64), base64.b64decode(blind_index_key_b64)
    provider = LocalKeyProvider(kek, [base64.b64decode(k) for k in previous_keks_b64])
    return FieldCrypto(FieldCipher(provider), BlindIndex(bidx))
