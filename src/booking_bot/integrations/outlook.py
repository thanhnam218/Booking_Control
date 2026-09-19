"""Doc Outlook desktop de cap nhat trang thai booking (yeu cau bo sung trong SRS).

Chi chay tren Windows co Outlook desktop (pywin32).
Bot chi GOI Y: moi mail khop se tao mot Exception "can User xac nhan",
tru khi outlook.auto_apply = true trong config.

Ghep mail voi shipment bang cach tim JO hoac so Booking trong tieu de/noi dung.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.normalize import norm_key, strip_accents
from ..logging_setup import get_logger

log = get_logger("integrations.outlook")


@dataclass(slots=True)
class MailHit:
    subject: str
    received: dt.datetime
    sender: str
    kind: str  # CONFIRM | CANCEL
    matched_jos: list[str] = field(default_factory=list)
    snippet: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "received": self.received.isoformat(),
            "sender": self.sender,
            "kind": self.kind,
            "jos": self.matched_jos,
            "snippet": self.snippet[:400],
        }


def available() -> bool:
    try:
        import win32com.client  # noqa: F401

        return True
    except ImportError:
        return False


def _iter_items(folder, since: dt.datetime, limit: int = 800):  # pragma: no cover - can Outlook
    items = folder.Items
    items.Sort("[ReceivedTime]", True)
    count = 0
    for item in items:
        count += 1
        if count > limit:
            break
        try:
            received = item.ReceivedTime
            if received is None:
                continue
            received = dt.datetime(received.year, received.month, received.day,
                                   received.hour, received.minute, received.second)
            if received < since:
                break
            yield item, received
        except AttributeError:
            continue


def scan_mailbox(cfg, known_jos: list[str]) -> list[MailHit]:  # pragma: no cover - can Outlook
    """Quet Outlook, tra ve danh sach mail co ve lien quan den booking."""
    if not cfg.enabled:
        return []
    if not available():
        log.warning("Khong co pywin32 - bo qua Outlook")
        return []

    import win32com.client

    since = dt.datetime.now() - dt.timedelta(days=cfg.lookback_days)
    confirm_kw = [strip_accents(k).upper() for k in cfg.booking_confirm_keywords]
    cancel_kw = [strip_accents(k).upper() for k in cfg.cancel_keywords]
    jo_index = {norm_key(j): j for j in known_jos if j}
    jo_pattern = re.compile(r"[A-Z0-9][A-Z0-9\-/]{3,}", re.IGNORECASE)

    hits: list[MailHit] = []
    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")

    for folder_name in cfg.folders:
        try:
            folder = _resolve_folder(outlook, folder_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Khong mo duoc thu muc Outlook '%s': %s", folder_name, exc)
            continue

        for item, received in _iter_items(folder, since):
            subject = getattr(item, "Subject", "") or ""
            body = (getattr(item, "Body", "") or "")[:4000]
            blob = strip_accents(f"{subject}\n{body}").upper()

            kind = ""
            if any(k in blob for k in cancel_kw):
                kind = "CANCEL"
            elif any(k in blob for k in confirm_kw):
                kind = "CONFIRM"
            if not kind:
                continue

            found = []
            for token in set(jo_pattern.findall(f"{subject} {body}")):
                key = norm_key(token)
                if key in jo_index and jo_index[key] not in found:
                    found.append(jo_index[key])
            if not found:
                continue

            hits.append(MailHit(
                subject=subject, received=received,
                sender=getattr(item, "SenderName", "") or "",
                kind=kind, matched_jos=found,
                snippet=subject,
            ))

    log.info("Outlook: tim thay %s mail lien quan booking", len(hits))
    return hits


def _resolve_folder(namespace, name: str):  # pragma: no cover - can Outlook
    if name.lower() == "inbox":
        return namespace.GetDefaultFolder(6)  # olFolderInbox
    inbox = namespace.GetDefaultFolder(6)
    for f in inbox.Folders:
        if f.Name.lower() == name.lower():
            return f
    for f in namespace.Folders:
        for sub in f.Folders:
            if sub.Name.lower() == name.lower():
                return sub
    raise LookupError(f"Khong tim thay thu muc '{name}'")


def apply_hits(session, hits: list[MailHit], auto_apply: bool = False) -> int:
    """Tao Exception de User xac nhan, hoac ap dung ngay neu auto_apply."""
    from sqlalchemy import select

    from ..audit import log_audit, raise_exception
    from ..db.models import Shipment, UserOverride

    applied = 0
    for hit in hits:
        for jo in hit.matched_jos:
            ships = list(session.execute(select(Shipment).where(Shipment.jo == jo)).scalars())
            for sh in ships:
                if hit.kind == "CONFIRM" and sh.booking_flag:
                    continue
                if not auto_apply:
                    raise_exception(
                        session, "EX002" if hit.kind == "CANCEL" else "EX008",
                        f"Mail Outlook '{hit.subject}' bao {hit.kind} cho JO {jo}. "
                        "Xac nhan de cap nhat trang thai booking.",
                        severity="WARNING", shipment_code=sh.shipment_code,
                        value=hit.sender,
                        suggested_action="Mo chi tiet lo hang > User Override de chot trang thai",
                        payload=hit.as_dict(),
                        dedup_key=f"OUTLOOK:{hit.kind}:{jo}:{hit.received:%Y%m%d%H%M}",
                    )
                    continue

                if hit.kind == "CONFIRM":
                    sh.booking_flag = True
                    new_status = "YELLOW" if not (sh.cdf and sh.bl) else "GREEN"
                else:
                    sh.cancel_flag = True
                    new_status = "CANCELLED"
                session.add(UserOverride(
                    shipment_code=sh.shipment_code, field="status", user_value=new_status,
                    user_name="outlook", note=f"Tu mail: {hit.subject[:120]}",
                ))
                log_audit(session, f"OUTLOOK_{hit.kind}", actor_type="SYSTEM", actor_name="outlook",
                          entity="shipment", entity_id=sh.shipment_code, field="status",
                          old_value=sh.effective_status, new_value=new_status,
                          source=hit.sender, note=hit.subject[:200])
                applied += 1
    return applied
