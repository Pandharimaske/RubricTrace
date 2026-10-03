import json
import logging
import urllib.request
from pathlib import Path
from uuid import uuid4

from backend.app.core.settings import (
    STORAGE_ACCESS_KEY,
    STORAGE_BACKEND,
    STORAGE_BUCKET,
    STORAGE_ENDPOINT_URL,
    STORAGE_REGION,
    STORAGE_SECRET_KEY,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    UPLOAD_DIR,
    ensure_data_dirs,
)
from fastapi import UploadFile

log = logging.getLogger("rubrictrace.storage")


def _s3_client():
    if STORAGE_BACKEND != "s3":
        return None
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 storage requires the boto3 dependency") from exc
    return boto3.client(
        "s3",
        endpoint_url=STORAGE_ENDPOINT_URL or None,
        aws_access_key_id=STORAGE_ACCESS_KEY or None,
        aws_secret_access_key=STORAGE_SECRET_KEY or None,
        region_name=STORAGE_REGION,
    )


def _object_key(script_id: str, suffix: str) -> str:
    return f"uploads/{script_id}/original{suffix}"


def ensure_bucket() -> None:
    client = _s3_client()
    if client is None:
        return
    try:
        client.head_bucket(Bucket=STORAGE_BUCKET)
    except Exception:
        try:
            client.create_bucket(Bucket=STORAGE_BUCKET)
        except Exception:
            pass


def _supabase_upload(object_key: str, content: bytes, content_type: str) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{object_key}"
    req = urllib.request.Request(
        url,
        data=content,
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": content_type or "application/octet-stream",
            "x-upsert": "true",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req):
            pass
    except Exception as exc:
        log.warning("Supabase storage upload failed: %s", exc)


def _supabase_delete(object_key: str) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}"
    payload = json.dumps({"prefixes": [object_key]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": "application/json",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req):
            pass
    except Exception as exc:
        log.warning("Supabase storage delete failed: %s", exc)


async def save_upload(file: UploadFile) -> tuple[str, Path, int]:
    ensure_data_dirs()

    suffix = Path(file.filename or "script").suffix.lower()
    script_id = uuid4().hex
    output_path = UPLOAD_DIR / f"{script_id}{suffix}"

    content = await file.read()
    output_path.write_bytes(content)

    if STORAGE_BACKEND == "supabase":
        _supabase_upload(
            _object_key(script_id, suffix), content, file.content_type or "application/octet-stream"
        )
    elif STORAGE_BACKEND == "s3":
        client = _s3_client()
        if client is not None:
            ensure_bucket()
            client.put_object(
                Bucket=STORAGE_BUCKET,
                Key=_object_key(script_id, suffix),
                Body=content,
                ContentType=file.content_type or "application/octet-stream",
            )

    return script_id, output_path, len(content)


def delete_upload(script_id: str, path: Path) -> None:
    path.unlink(missing_ok=True)
    if STORAGE_BACKEND == "supabase":
        _supabase_delete(_object_key(script_id, path.suffix.lower()))
    elif STORAGE_BACKEND == "s3":
        client = _s3_client()
        if client is not None:
            suffix = path.suffix.lower()
            client.delete_object(Bucket=STORAGE_BUCKET, Key=_object_key(script_id, suffix))
