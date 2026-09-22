"""Doc email tu Outlook desktop tren may chu (pywin32 / COM) - SRSv5 muc 19-22, 44.

    - Doc Inbox + Sent Items (email yeu cau booking do minh gui -> ghi nho Conversation)
    - Chi doc email MOI tu lan quet truoc (incremental, luu moc thoi gian trong system_settings)
    - Lay Internet Message-ID (chong xu ly trung), ConversationID, In-Reply-To
    - Luu file dinh kem (PDF / Excel) de doc booking confirm trong PDF

Tren Linux / may khong co Outlook: available() = False, job outlook_scan bao loi ro rang.
Phan xu ly (phan loai, ghep lo, ap dung) nam o services/email_service.py - khong phu thuoc COM.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger("integrations.outlook")

PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_IN_REPLY_TO_ID = "http://schemas.microsoft.com/mapi/proptag/0x1042001F"
OL_FOLDER_INBOX, OL_FOLDER_SENT = 6, 5
OL_MAIL_ITEM = 43


@dataclass
class MailAttachment:
    filename: str
    content: bytes


@dataclass
class MailMessage:
    """Email da doc ra - doc lap voi Outlook (test dung truc tiep)."""

    message_id: str
    subject: str
    body: str
    sender: str = ""
    recipients: str = ""
    received_at: dt.datetime | None = None
    conversation_id: str = ""
    in_reply_to: str = ""
    direction: str = "IN"  # IN | OUT
    folder: str = "Inbox"
    attachments: list[MailAttachment] = field(default_factory=list)


def available() -> bool:
    try:
        import win32com.client  # noqa: F401

        return True
    except ImportError:
        return False


def _prop(item, tag: str) -> str:  # pragma: no cover - can Outlook
    try:
        return str(item.PropertyAccessor.GetProperty(tag) or "")
    except Exception:  # noqa: BLE001
        return ""


def _smtp_sender(item) -> str:  # pragma: no cover - can Outlook
    try:
        if item.SenderEmailType == "EX":
            ex = item.Sender.GetExchangeUser()
            if ex is not None:
                return str(ex.PrimarySmtpAddress or "")
        return str(item.SenderEmailAddress or "")
    except Exception:  # noqa: BLE001
        return str(getattr(item, "SenderEmailAddress", "") or "")


def _resolve_folder(ns, cfg, name: str):  # pragma: no cover - can Outlook
    low = name.strip().lower()
    root = ns.Folders[cfg.mailbox] if cfg.mailbox else None
    if low in ("inbox", "hop thu den", "hộp thư đến"):
        return root.Folders["Inbox"] if root else ns.GetDefaultFolder(OL_FOLDER_INBOX)
    if low in ("sent items", "sent", "thu da gui", "thư đã gửi"):
        return root.Folders["Sent Items"] if root else ns.GetDefaultFolder(OL_FOLDER_SENT)
    base = root or ns.GetDefaultFolder(OL_FOLDER_INBOX).Parent
    folder = base
    for part in name.replace("\\", "/").split("/"):
        folder = folder.Folders[part]
    return folder


def fetch_messages(cfg, since: dict[str, dt.datetime]) -> list[MailMessage]:  # pragma: no cover - can Outlook
    """Doc email moi hon moc `since[folder]` trong cac thu muc cau hinh."""
    import win32com.client  # type: ignore

    ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    out: list[MailMessage] = []
    exts = {e.lower() for e in cfg.attachment_exts}
    max_bytes = cfg.max_attachment_mb * 1024 * 1024
    for fname in cfg.folders:
        try:
            folder = _resolve_folder(ns, cfg, fname)
        except Exception as exc:  # noqa: BLE001
            log.warning("Khong mo duoc thu muc Outlook '%s': %s", fname, exc)
            continue
        start = since.get(fname) or (dt.datetime.now() - dt.timedelta(days=cfg.lookback_days))
        items = folder.Items
        items.Sort("[ReceivedTime]", True)
        restricted = items.Restrict("[ReceivedTime] >= '" + start.strftime("%m/%d/%Y %H:%M") + "'")
        direction = "OUT" if fname.strip().lower() in ("sent items", "sent", "thu da gui", "thư đã gửi") else "IN"
        count = 0
        for item in restricted:
            if count >= cfg.max_items:
                break
            if getattr(item, "Class", None) != OL_MAIL_ITEM:
                continue
            count += 1
            try:
                received = item.ReceivedTime if direction == "IN" else item.SentOn
                received = dt.datetime(received.year, received.month, received.day, received.hour,
                                       received.minute, received.second)
                atts: list[MailAttachment] = []
                for a in item.Attachments:
                    name = str(a.FileName or "")
                    if Path(name).suffix.lower() not in exts or (a.Size or 0) > max_bytes:
                        continue
                    with tempfile.TemporaryDirectory() as tmp:
                        p = Path(tmp) / Path(name).name
                        a.SaveAsFile(str(p))
                        atts.append(MailAttachment(filename=name, content=p.read_bytes()))
                msg_id = _prop(item, PR_INTERNET_MESSAGE_ID) or f"entry:{item.EntryID}"
                out.append(MailMessage(
                    message_id=msg_id, subject=str(item.Subject or ""), body=str(item.Body or "")[: cfg.body_chars],
                    sender=_smtp_sender(item), recipients=str(getattr(item, "To", "") or ""), received_at=received,
                    conversation_id=str(getattr(item, "ConversationID", "") or ""),
                    in_reply_to=_prop(item, PR_IN_REPLY_TO_ID), direction=direction, folder=fname, attachments=atts,
                ))
            except Exception as exc:  # noqa: BLE001
                log.warning("Bo qua 1 email loi doc: %s", exc)
    return out


def scan_outlook(settings, engine=None) -> dict[str, Any]:  # pragma: no cover - can Outlook
    """Job outlook_scan: doc email moi -> services.email_service xu ly. Chay trong thread nen."""
    if not available():
        raise RuntimeError("Can Windows + Outlook desktop dang mo + pywin32 (pip install pywin32)")
    import pythoncom  # type: ignore

    from ..db import session_scope
    from ..db.models import SystemSetting
    from ..services.email_service import EmailProcessor

    pythoncom.CoInitialize()
    try:
        with session_scope() as s:
            row = s.get(SystemSetting, "outlook.last_sync")
            last = {k: dt.datetime.fromisoformat(v) for k, v in ((row.value if row else {}) or {}).items()}
        # Doc lui 10 phut de khong lot email den cham; trung thi message_id chan lai
        since = {k: v - dt.timedelta(minutes=10) for k, v in last.items()}
        msgs = fetch_messages(settings.outlook, since)
        with session_scope() as s:
            summary = EmailProcessor(engine, settings).ingest(s, msgs)
            row = s.get(SystemSetting, "outlook.last_sync")
            newest: dict[str, str] = dict((row.value or {}) if row else {})
            for m in msgs:
                if m.received_at and (m.folder not in newest or m.received_at.isoformat() > newest[m.folder]):
                    newest[m.folder] = m.received_at.isoformat()
            if row is None:
                s.add(SystemSetting(key="outlook.last_sync", value=newest))
            else:
                row.value = newest
        return {"mails": len(msgs), **summary}
    finally:
        pythoncom.CoUninitialize()
