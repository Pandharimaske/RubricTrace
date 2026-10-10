"""Storage for uploaded answer scripts.

The processing copy always lives on local disk, because the PDF/VLM libraries read file
paths. An optional ``ObjectMirror`` keeps a durable copy in S3-compatible storage or
Supabase; the mirror is chosen once from settings.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from backend.app.core.config import Settings

log = logging.getLogger("rubrictrace.storage")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class UploadRejected(ValueError):
    """The uploaded file is not acceptable (type or size)."""


@dataclass(frozen=True)
class StoredUpload:
    script_id: str
    path: Path
    size: int


class ObjectMirror(Protocol):
    """A durable second copy of every upload."""

    def put(self, key: str, content: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...


class S3Mirror:
    """S3 or any S3-compatible service (RustFS/MinIO locally, S3 on AWS).

    Failures propagate: if the durable copy cannot be written the upload fails.
    """

    def __init__(self, config: Settings) -> None:
        self._config = config
        self._client = None
        self._bucket_ready = False

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("S3 storage requires the boto3 dependency") from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self._config.storage_endpoint_url or None,
                aws_access_key_id=self._config.storage_access_key or None,
                aws_secret_access_key=self._config.storage_secret_key or None,
                region_name=self._config.storage_region,
            )
        return self._client

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self._config.storage_bucket)
        except Exception:
            log.info("Creating bucket %s", self._config.storage_bucket)
            client.create_bucket(Bucket=self._config.storage_bucket)
        self._bucket_ready = True

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self._get_client().put_object(
            Bucket=self._config.storage_bucket, Key=key, Body=content, ContentType=content_type
        )

    def delete(self, key: str) -> None:
        self._get_client().delete_object(Bucket=self._config.storage_bucket, Key=key)


class SupabaseMirror:
    """Supabase Storage over its REST API. Failures are logged, not raised: the local
    copy is authoritative for processing, so a mirror outage must not block uploads."""

    _TIMEOUT_SECONDS = 30

    def __init__(self, config: Settings) -> None:
        self._base = config.supabase_url.rstrip("/")
        self._key = config.supabase_service_key
        self._bucket = config.storage_bucket

    @property
    def configured(self) -> bool:
        return bool(self._base and self._key)

    def _headers(self, content_type: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
            "Content-Type": content_type,
        }

    def _send(self, request: urllib.request.Request, action: str) -> None:
        try:
            with urllib.request.urlopen(request, timeout=self._TIMEOUT_SECONDS):  # noqa: S310
                pass
        except Exception as exc:
            log.warning("Supabase storage %s failed: %s", action, exc)

    def put(self, key: str, content: bytes, content_type: str) -> None:
        if not self.configured:
            return
        request = urllib.request.Request(  # noqa: S310
            f"{self._base}/storage/v1/object/{self._bucket}/{key}",
            data=content,
            headers={
                **self._headers(content_type or "application/octet-stream"),
                "x-upsert": "true",
            },
            method="POST",
        )
        self._send(request, "upload")

    def delete(self, key: str) -> None:
        if not self.configured:
            return
        request = urllib.request.Request(  # noqa: S310
            f"{self._base}/storage/v1/object/{self._bucket}",
            data=json.dumps({"prefixes": [key]}).encode("utf-8"),
            headers=self._headers("application/json"),
            method="DELETE",
        )
        self._send(request, "delete")


class UploadStorage:
    """Validates, saves and deletes uploaded script files."""

    def __init__(self, config: Settings, mirror: ObjectMirror | None = None) -> None:
        self._config = config
        self._mirror = mirror

    @classmethod
    def from_settings(cls, config: Settings) -> UploadStorage:
        backend = config.storage_backend.strip().lower()
        mirror: ObjectMirror | None = None
        if backend == "s3":
            mirror = S3Mirror(config)
        elif backend == "supabase":
            mirror = SupabaseMirror(config)
        return cls(config, mirror)

    @staticmethod
    def _object_key(script_id: str, suffix: str) -> str:
        return f"uploads/{script_id}/original{suffix}"

    def validate(self, filename: str, size: int) -> str:
        """Return the normalised suffix, or raise UploadRejected."""
        suffix = Path(filename or "").suffix.lower()
        allowed = self._config.allowed_upload_suffixes
        if suffix not in allowed:
            raise UploadRejected(
                f"Unsupported file type {suffix or '(none)'}. Use: {', '.join(allowed)}"
            )
        if size <= 0:
            raise UploadRejected("The uploaded file is empty.")
        if size > self._config.max_upload_bytes:
            raise UploadRejected(f"File is larger than the {self._config.max_upload_mb} MB limit.")
        return suffix

    def save(self, filename: str, content_type: str | None, content: bytes) -> StoredUpload:
        suffix = self.validate(filename, len(content))
        self._config.ensure_data_dirs()
        script_id = uuid4().hex
        path = self._config.upload_dir / f"{script_id}{suffix}"
        path.write_bytes(content)
        if self._mirror is not None:
            self._mirror.put(
                self._object_key(script_id, suffix),
                content,
                content_type or "application/octet-stream",
            )
        return StoredUpload(script_id=script_id, path=path, size=len(content))

    def delete(self, script_id: str) -> None:
        """Remove every stored copy of one script. Only files this class wrote are touched
        (never a dataset PDF a script row merely points at)."""
        if not _SAFE_ID.fullmatch(script_id):
            return
        for path in self._config.upload_dir.glob(f"{script_id}.*"):
            path.unlink(missing_ok=True)
            if self._mirror is not None:
                self._mirror.delete(self._object_key(script_id, path.suffix.lower()))
