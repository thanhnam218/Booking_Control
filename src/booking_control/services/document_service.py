"""Chung tu cua lo hang: luu file NGOAI database, khong ghi de version (SRSv5 muc 56-57).

    <documents_dir>/<Nam>/<Buyer>/<NO hoac JO>/<LOAI>/<NO>_<LOAI>_v<k>.<ext>

Tai len lai dung noi dung cu (cung SHA-256) -> khong tao version moi.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit
from ..db.models import Shipment, utcnow
from ..db.models_v3 import Document, DocumentVersion
from .booking_service import log_event

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentError(ValueError):
    pass


def _safe(part: str, default: str = "X") -> str:
    return _SAFE.sub("_", (part or "").strip()).strip("._") or default


def doc_types(settings) -> dict[str, str]:
    return {d["code"]: d.get("label", d["code"]) for d in (settings.workflow or {}).get("document_types") or []}


def target_path(settings, sh: Shipment, doc_type: str, version: int, filename: str) -> Path:
    ext = Path(filename).suffix.lower() or ".bin"
    ref = sh.shipment_no or sh.jo or sh.shipment_code
    pattern = (settings.workflow or {}).get("naming") or "{REF}_{DOC}_v{VERSION}"
    name = pattern.format(REF=_safe(ref), NO=_safe(sh.shipment_no), JO=_safe(sh.jo), DOC=_safe(doc_type),
                          BUYER=_safe(sh.buyer_code), VERSION=version)
    year = str(sh.exfty_date.year) if sh.exfty_date else "NO_DATE"
    folder = settings.documents_dir / year / _safe(sh.buyer_code or sh.buyer_raw, "NO_BUYER") / _safe(ref) / _safe(doc_type)
    return folder / f"{_safe(name)}{ext}"


def add_version(s: Session, settings, sh: Shipment, doc_type: str, filename: str, content: bytes, *, actor: str,
                source: str = "UPLOAD", email_id: int | None = None, note: str = "") -> DocumentVersion | None:
    types = doc_types(settings)
    if doc_type not in types:
        raise DocumentError(f"Loai chung tu khong hop le: {doc_type}")
    ext = Path(filename).suffix.lower()
    if ext not in [e.lower() for e in settings.documents.allowed_exts]:
        raise DocumentError(f"Khong nhan dinh dang {ext or '(khong co duoi)'}")
    if len(content) > settings.documents.max_upload_mb * 1024 * 1024:
        raise DocumentError(f"File qua lon (> {settings.documents.max_upload_mb} MB)")
    digest = hashlib.sha256(content).hexdigest()
    doc = s.execute(select(Document).where(Document.shipment_code == sh.shipment_code,
                                           Document.doc_type == doc_type)).scalar_one_or_none()
    if doc is None:
        doc = Document(shipment_code=sh.shipment_code, doc_type=doc_type, current_version=0)
        s.add(doc)
        s.flush()
    else:
        last = s.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                         .order_by(DocumentVersion.version.desc())).scalars().first()
        if last is not None and last.sha256 == digest:
            return None  # trung noi dung -> khong tao version moi
    version = doc.current_version + 1
    path = target_path(settings, sh, doc_type, version, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    v = DocumentVersion(document_id=doc.id, version=version, filename=Path(filename).name[:250],
                        stored_path=str(path), sha256=digest, size=len(content), source=source,
                        email_id=email_id, uploaded_by=actor, note=note)
    s.add(v)
    doc.current_version, doc.status = version, "RECEIVED"
    doc.updated_at, doc.updated_by = utcnow(), actor
    log_event(s, "DOCUMENT_RECEIVED", shipment_code=sh.shipment_code, email_id=email_id, source=source,
              actor=actor, new=f"{doc_type} v{version}", note=Path(filename).name)
    log_audit(s, "DOCUMENT_UPLOADED", actor_type="USER" if source == "UPLOAD" else "AUTO", actor_name=actor,
              entity="shipment", entity_id=sh.shipment_code, field=doc_type, new_value=f"v{version} {filename}")
    s.flush()
    return v


def set_status(s: Session, doc: Document, status: str, *, actor: str, note: str = "") -> None:
    if status not in ("RECEIVED", "APPROVED", "REJECTED"):
        raise DocumentError("Trang thai chung tu khong hop le")
    old = doc.status
    doc.status, doc.note = status, note or doc.note
    doc.updated_at, doc.updated_by = utcnow(), actor
    log_event(s, "DOCUMENT_" + status, shipment_code=doc.shipment_code, actor=actor, old=old, new=status,
              note=f"{doc.doc_type} {note}".strip())


def versions(s: Session, shipment_code: str) -> dict[str, list[DocumentVersion]]:
    out: dict[str, list[DocumentVersion]] = {}
    docs = {d.id: d for d in s.execute(select(Document).where(Document.shipment_code == shipment_code)).scalars()}
    if not docs:
        return out
    for v in s.execute(select(DocumentVersion).where(DocumentVersion.document_id.in_(list(docs)))
                       .order_by(DocumentVersion.version.desc())).scalars():
        out.setdefault(docs[v.document_id].doc_type, []).append(v)
    return out
