"""Where document files live (ADR-027): write once, never overwrite, never delete.

* ``S3BlobStore`` is production: an S3 bucket in the residency region with versioning and
  Object Lock (compliance mode) turned on in the infrastructure code, every object encrypted with
  AES-256 under the repository's KMS key (SSE-KMS), and a SHA-256 checksum S3 verifies on upload.
  Cross-region replication and AWS Backup give the second and third copies.
* ``LocalBlobStore`` is dev/test: AES-256-GCM in memory or in a folder. Staging and production
  refuse to start with it.

Keys look like ``<tenant>/<document>/v<n>-<sha256 prefix>``; a key is never written twice.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from syntegra.platform.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class BlobExists(Exception):
    """The key is already written; stored files are never replaced."""


class BlobMissing(Exception):
    """No file is stored under this key."""


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str, sha256: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class LocalBlobStore:
    """AES-256-GCM under a local key; in memory, or in ``root`` when given. Dev/test only."""

    def __init__(self, key: bytes, root: Path | None = None) -> None:
        if len(key) != 32:
            raise ValueError("the document key must be 32 bytes")
        self._aead = AESGCM(key)
        self._root = root
        self._mem: dict[str, bytes] = {}

    def _path(self, key: str) -> Path:
        assert self._root is not None
        return self._root / hashlib.sha256(key.encode()).hexdigest()

    def _exists(self, key: str) -> bool:
        return key in self._mem if self._root is None else self._path(key).exists()

    async def put(self, key: str, data: bytes, *, content_type: str, sha256: str) -> None:
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("checksum does not match the file")
        if self._exists(key):
            raise BlobExists(key)
        nonce = os.urandom(12)
        sealed = nonce + self._aead.encrypt(nonce, data, key.encode())
        if self._root is None:
            self._mem[key] = sealed
        else:
            self._root.mkdir(parents=True, exist_ok=True)
            self._path(key).write_bytes(sealed)

    async def get(self, key: str) -> bytes:
        if not self._exists(key):
            raise BlobMissing(key)
        sealed = self._mem[key] if self._root is None else self._path(key).read_bytes()
        return self._aead.decrypt(sealed[:12], sealed[12:], key.encode())


class S3BlobStore:
    def __init__(self, client: S3Client, bucket: str, kms_key_id: str) -> None:
        self._s3, self._bucket, self._kms = client, bucket, kms_key_id

    async def put(self, key: str, data: bytes, *, content_type: str, sha256: str) -> None:
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()

        def _put() -> None:
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                    ServerSideEncryption="aws:kms",
                    SSEKMSKeyId=self._kms,
                    ChecksumSHA256=checksum,
                    IfNoneMatch="*",  # never replace a stored file
                )
            except self._s3.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "PreconditionFailed":
                    raise BlobExists(key) from exc
                raise

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            except self._s3.exceptions.NoSuchKey as exc:
                raise BlobMissing(key) from exc

        return await asyncio.to_thread(_get)


def _dev_key() -> bytes:
    return hashlib.sha256(b"syntegra-dev-only-not-a-secret|documents").digest()


def build_blob_store(settings: Settings) -> BlobStore:
    if settings.document_store == "s3":
        if not settings.document_bucket or not settings.document_kms_key_id:
            raise RuntimeError(
                "SYNTEGRA_DOCUMENT_BUCKET and SYNTEGRA_DOCUMENT_KMS_KEY_ID are required for S3"
            )
        import boto3

        client = boto3.client("s3", region_name=settings.residency_region)
        return S3BlobStore(client, settings.document_bucket, settings.document_kms_key_id)
    if settings.env not in ("dev", "test"):
        raise RuntimeError("the local document store is for dev/test (SYNTEGRA_DOCUMENT_STORE=s3)")
    root = Path(settings.document_dir) if settings.document_dir else None
    return LocalBlobStore(_dev_key(), root)
