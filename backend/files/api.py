import itertools
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
from django.db import IntegrityError, transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from ninja import File as FormFile
from ninja import Form, Router, Schema, UploadedFile
from ninja.errors import HttpError

from accounts import rls
from accounts.auth import TenantAuth
from storage import client as storage

from . import crypto
from .models import File, TenantKey

log = logging.getLogger(__name__)
router = Router()

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# A second connection, so the pending row commits before the body is written (see upload).
DIRECT = "direct"


class FileOut(Schema):
    id: UUID
    name: str
    folder: str
    size: int
    content_type: str
    created_at: datetime


class ListOut(Schema):
    folder: str
    folders: list[str]
    files: list[FileOut]


class UpdateIn(Schema):
    name: Optional[str] = None
    folder: Optional[str] = None


NAME_TAKEN = "A file with this name already exists in this folder"


class TenantKeyDestroyed(Exception):
    pass


def normalize_folder(raw: str) -> str:
    parts = [p for p in raw.strip().split("/") if p]
    if any(p in (".", "..") or len(p) > 255 or "\x00" in p or "\\" in p for p in parts):
        raise HttpError(422, "Invalid folder path")
    folder = "/" + "/".join(parts)
    if len(folder) > 1024:
        raise HttpError(422, "Folder path too long")
    return folder


def normalize_name(raw: str) -> str:
    name = raw.strip()
    if not name or name in (".", "..") or len(name) > 255 or any(c in name for c in "/\\\x00"):
        raise HttpError(422, "Invalid file name")
    return name


def _tenant_key(tenant, using: str, create: bool) -> bytes:
    row = TenantKey.objects.using(using).filter(tenant=tenant).first()
    if row is None and create:
        key, wrapped = crypto.new_tenant_key(tenant.id)
        try:
            with transaction.atomic(using=using):
                TenantKey.objects.using(using).create(tenant=tenant, wrapped_key=wrapped)
            return key
        except IntegrityError:  # another first upload won the race
            row = TenantKey.objects.using(using).get(tenant=tenant)
    if row is None:
        raise crypto.IntegrityError("tenant has Files but no key")
    if row.wrapped_key is None:
        raise TenantKeyDestroyed
    return crypto.unwrap_tenant_key(tenant.id, bytes(row.wrapped_key))


def _discard(request, record: File) -> None:
    """Best effort after a failed upload; whatever survives is for the pending-row cleanup."""
    try:
        storage.delete(record.object_key)
        with rls.scope(request, DIRECT):
            File.objects.using(DIRECT).filter(id=record.id).delete()
    except Exception:
        log.exception("could not discard failed upload %s", record.id)


@router.post("/files", auth=TenantAuth(), response=FileOut)
def upload(
    request,
    file: UploadedFile = FormFile(...),
    folder: str = Form("/"),
    name: Optional[str] = Form(None),
):
    # shortcut: an oversize body is spooled to disk before this check (only authenticated Tenant
    # members get this far); add a body limit at the proxy once this is exposed beyond local use.
    if file.size > MAX_UPLOAD_BYTES:
        raise HttpError(413, "File is larger than the 100 MB limit")
    folder = normalize_folder(folder)
    name = normalize_name(name or file.name or "")
    tenant = request.tenant
    # Fail early for the common case; the unique index still decides a race (see the except below).
    if File.objects.filter(tenant=tenant, folder=folder, name=name, status=File.Status.ACTIVE).exists():
        raise HttpError(409, NAME_TAKEN)
    file_id = uuid4()

    # Commit the pending row first, on its own connection; the row becomes active only once the
    # body is stored, so a restored database never points at a missing object.
    with rls.scope(request, DIRECT):
        try:
            tenant_key = _tenant_key(tenant, DIRECT, create=True)
        except TenantKeyDestroyed:
            raise HttpError(410, "This tenant's encryption key was destroyed")
        data_key, wrapped = crypto.new_data_key(tenant_key, file_id)
        record = File.objects.using(DIRECT).create(
            id=file_id,
            tenant=tenant,
            folder=folder,
            name=name,
            content_type=file.content_type or "application/octet-stream",
            wrapped_data_key=wrapped,
        )

    try:
        file.seek(0)
        storage.put_stream(record.object_key, crypto.EncryptingReader(file, data_key))
        with rls.scope(request, DIRECT):
            File.objects.using(DIRECT).filter(id=file_id).update(status=File.Status.ACTIVE, size=file.size)
    except IntegrityError:  # another upload took the name between the check above and now
        log.warning("upload %s lost the race for its name; discarding", file_id)
        _discard(request, record)
        raise HttpError(409, NAME_TAKEN)
    except Exception:
        log.exception("upload %s failed", file_id)
        _discard(request, record)
        raise HttpError(500, "Upload failed")
    record.size, record.status = file.size, File.Status.ACTIVE
    return record


@router.patch("/files/{file_id}", auth=TenantAuth(), response=FileOut)
def update_file(request, file_id: UUID, data: UpdateIn):
    """Rename and/or move. Only the record changes: the stored object is keyed by id."""
    if data.name is None and data.folder is None:
        raise HttpError(422, "Give a new name, a new folder, or both")
    record = get_object_or_404(File, id=file_id, tenant=request.tenant, status=File.Status.ACTIVE)
    name = normalize_name(data.name) if data.name is not None else record.name
    folder = normalize_folder(data.folder) if data.folder is not None else record.folder
    if (name, folder) == (record.name, record.folder):
        return record
    record.name, record.folder = name, folder
    try:
        with transaction.atomic():  # a savepoint, so a conflict does not poison the request transaction
            record.save(update_fields=["name", "folder"])
    except IntegrityError:
        raise HttpError(409, NAME_TAKEN)
    return record


@router.get("/files", auth=TenantAuth(), response=ListOut)
def list_files(request, folder: str = "/"):
    folder = normalize_folder(folder)
    active = File.objects.filter(tenant=request.tenant, status=File.Status.ACTIVE)
    prefix = "/" if folder == "/" else folder + "/"
    below = active.filter(folder__startswith=prefix).exclude(folder=folder).values_list("folder", flat=True).distinct()
    children = sorted({f[len(prefix) :].split("/")[0] for f in below})
    return ListOut(folder=folder, folders=children, files=list(active.filter(folder=folder).order_by("name")))


@router.get("/files/{file_id}/download", auth=TenantAuth())
def download(request, file_id: UUID):
    record = get_object_or_404(File, id=file_id, tenant=request.tenant, status=File.Status.ACTIVE)
    try:
        tenant_key = _tenant_key(request.tenant, "default", create=False)
        data_key = crypto.unwrap_data_key(tenant_key, record.id, bytes(record.wrapped_data_key))
        body = storage.open_stream(record.object_key)
        segments = crypto.decrypt_stream(body.iter_chunks(crypto.BLOCK), data_key)
        # Decrypt the first segment now so a bad small file fails with a status instead of a cut connection.
        first = next(segments)
    except TenantKeyDestroyed:
        raise HttpError(410, "This tenant's encryption key was destroyed; its files can no longer be read")
    except (crypto.IntegrityError, ClientError):
        log.exception("file %s failed to load", record.id)
        raise HttpError(500, "Stored file failed its integrity check or is missing")
    # The response is built from memory only; the request transaction ends before streaming starts.
    response = StreamingHttpResponse(itertools.chain([first], segments), content_type="application/octet-stream")
    response["Content-Length"] = record.size
    response["Content-Disposition"] = content_disposition_header(True, record.name)
    return response
