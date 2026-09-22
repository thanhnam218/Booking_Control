"""Xu ly email Outlook: luu -> doc PDF -> phan loai -> trich xuat -> ghep lo -> ap dung / cho duyet.

    SRSv5 BR-003  Conversation / Reply duoc uu tien khi ghep
    SRSv5 BR-004  Booking Confirmation > Booking Request
    SRSv5 BR-006  Email va PDF khac nhau -> CONFLICT, nguoi dung chon
    SRSv5 BR-007  Nhieu lo sat diem -> khong tu chon
    SRSv5 BR-019  Khong du bang chung -> hang cho duyet, khong doan
    NFR-03        Cung message_id doc lai nhieu lan -> chi xu ly 1 lan

Doc lap voi Outlook: nhan danh sach MailMessage (integrations/outlook.py hoac test).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..audit import log_audit, raise_exception
from ..core import email_match as em
from ..core import email_parse as ep
from ..core.normalize import norm_key
from ..db.models import Notification, Shipment, utcnow
from ..db.models_v3 import Booking, BookingShipment, ConversationLink, Email, EmailAttachment
from ..integrations.outlook import MailMessage
from ..integrations.pdf_text import extract_text
from . import booking_service as bs
from . import document_service as ds

BOOKING_DATA = {"BOOKING_CONFIRMATION", "AMENDMENT", "DELAY", "REBOOK"}


class EmailError(ValueError):
    pass


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")[:120] or "file"


class EmailProcessor:
    def __init__(self, engine, settings=None):
        self.engine = engine
        self.settings = settings or engine.settings
        cfg = self.settings.email_profiles or {}
        self.categories: dict[str, list[str]] = cfg.get("categories") or {}
        self.base_aliases = cfg.get("field_aliases") or {}
        self.forwarders = cfg.get("forwarders") or []
        self.internal = [d.lower() for d in cfg.get("internal_domains") or []]
        th = cfg.get("thresholds") or {}
        self.auto, self.review, self.margin = int(th.get("auto", 85)), int(th.get("review", 60)), int(th.get("margin", 10))
        self.weights = cfg.get("weights") or {}
        self.window = (cfg.get("candidate_window_days") or [-90, 150])[:2]
        self._refs: list[em.ShipmentRef] | None = None

    # ------------------------------------------------------------------
    # Nhan dien
    # ------------------------------------------------------------------

    def detect_forwarder(self, sender: str) -> tuple[str, dict[str, list[str]]]:
        addr = (sender or "").lower().strip()
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        for f in self.forwarders:
            if addr and addr in [x.lower() for x in f.get("senders") or []]:
                return f["name"], f.get("field_aliases") or {}
            if domain and domain in [x.lower() for x in f.get("domains") or []]:
                return f["name"], f.get("field_aliases") or {}
        return "", {}

    def _shipment_refs(self, s: Session, today: dt.date) -> list[em.ShipmentRef]:
        if self._refs is not None:
            return self._refs
        lo, hi = today + dt.timedelta(days=int(self.window[0])), today + dt.timedelta(days=int(self.window[1]))
        ships = list(s.execute(select(Shipment).where(
            Shipment.archived.is_(False), Shipment.is_active.is_(True),
            or_(Shipment.exfty_date.is_(None), Shipment.exfty_date.between(lo, hi)))).scalars())
        by_code = bs.bookings_for(s, [sh.shipment_code for sh in ships])
        refs = []
        for sh in ships:
            inv = str((sh.row_data or {}).get("Invoice") or "")
            invoices = tuple(x for x in re.split(r"[,;/\s]+", inv) if x) + tuple(i.invoice_no for i in sh.invoices)
            bks = {sh.booking_no} | {b.booking_no for b, _ in by_code.get(sh.shipment_code, [])}
            refs.append(em.ShipmentRef(code=sh.shipment_code, jo=sh.jo, go=sh.go, po=sh.po, invoices=invoices,
                                       booking_nos=tuple(b for b in bks if b), buyer=sh.buyer_code or sh.buyer_raw,
                                       etd=sh.etd, vessel=sh.vessel, destination=sh.destination,
                                       forwarder=sh.forwarder))
        self._refs = refs
        return refs

    # ------------------------------------------------------------------
    # Nhan email
    # ------------------------------------------------------------------

    def ingest(self, s: Session, msgs: list[MailMessage], today: dt.date | None = None) -> dict[str, int]:
        today = today or dt.date.today()
        out = {"new": 0, "duplicate": 0, "applied": 0, "review": 0, "unmatched": 0, "conflict": 0, "errors": 0}
        for m in msgs:
            try:
                e = self.process(s, m, today)
            except Exception as exc:  # noqa: BLE001 - 1 email loi khong lam hong ca lan quet
                out["errors"] += 1
                log_audit(s, "EMAIL_ERROR", entity="email", entity_id=m.message_id[:80], note=str(exc)[:500])
                continue
            if e is None:
                out["duplicate"] += 1
                continue
            out["new"] += 1
            key = {"APPLIED": "applied", "REVIEW": "review", "CONFLICT": "conflict"}.get(e.match_status, "unmatched")
            out[key] += 1
        return out

    def process(self, s: Session, m: MailMessage, today: dt.date | None = None) -> Email | None:
        today = today or dt.date.today()
        if s.execute(select(Email.id).where(Email.message_id == m.message_id)).first():
            return None
        ref_date = m.received_at.date() if m.received_at else today
        forwarder, extra_alias = self.detect_forwarder(m.sender)
        alias_index = ep.build_alias_index(self.base_aliases, extra_alias)
        body_text = f"{m.subject}\n{m.body}"
        body_fields = ep.extract_fields(body_text, alias_index, ref_date)

        e = Email(message_id=m.message_id, conversation_id=m.conversation_id or "", in_reply_to=m.in_reply_to or "",
                  direction=m.direction, folder=m.folder, sender=m.sender, recipients=m.recipients[:2000],
                  subject=m.subject[:1000], received_at=m.received_at, body_excerpt=(m.body or "")[:6000],
                  forwarder=forwarder, extracted=body_fields)
        s.add(e)
        s.flush()

        pdf_fields: dict[str, Any] = {}
        pdf_text_all = []
        for a in m.attachments:
            att = self._store_attachment(s, e, a, alias_index, ref_date)
            if att.parse_status == "OK":
                pdf_text_all.append(att.parsed.pop("_text", ""))
                for k, v in att.parsed.items():
                    pdf_fields.setdefault(k, v)
        e.attachment_extracted = pdf_fields
        e.conflicts = ep.find_conflicts(body_fields, pdf_fields)
        full_text = "\n".join([body_text, *pdf_text_all])
        e.category, _kw = ep.classify(m.subject, "\n".join([m.body or "", *pdf_text_all]), self.categories)

        decision = self._match(s, e, full_text, ep.merge_fields(body_fields, pdf_fields), today)
        e.match_score = decision.score
        e.candidates = [c.as_dict() for c in decision.candidates[:8]]
        codes = decision.codes

        if decision.status == "AUTO_MATCHED":
            e.shipment_codes = codes
            if e.conflicts:
                e.match_status = "CONFLICT"
                self._notify(s, e, codes, "Email va PDF bao khac nhau - can chon gia tri", "WARNING")
            elif self.settings.outlook.auto_apply:
                self.apply(s, e, codes, actor="automation", today=today)
            else:
                e.match_status = "REVIEW"
        elif decision.status == "REVIEW":
            e.match_status = "REVIEW"
            e.shipment_codes = codes
            if e.category != "OTHER":
                self._notify(s, e, codes, f"Email {e.category} can xac nhan lo hang", "INFO")
        else:
            e.match_status = "UNMATCHED"
            if e.category != "OTHER" and e.direction == "IN":
                self._notify(s, e, [], f"Email {e.category} chua ghep duoc lo nao", "INFO")
        return e

    def _store_attachment(self, s: Session, e: Email, a, alias_index, ref_date) -> EmailAttachment:
        folder = self.settings.mail_dir / f"{ref_date:%Y}" / f"{ref_date:%m}" / str(e.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / _safe_name(a.filename)
        path.write_bytes(a.content)
        att = EmailAttachment(email_id=e.id, filename=a.filename[:250], stored_path=str(path),
                              sha256=hashlib.sha256(a.content).hexdigest(), size=len(a.content))
        if path.suffix.lower() == ".pdf":
            text, status, err = extract_text(a.content)
            att.parse_status, att.parse_error = status, err
            if status == "OK":
                att.parsed = {**ep.extract_fields(text, alias_index, ref_date), "_text": text[:20000]}
        else:
            att.parse_status = "SKIPPED"
        s.add(att)
        s.flush()
        return att

    def _match(self, s: Session, e: Email, text: str, fields: dict[str, Any], today: dt.date) -> em.MatchDecision:
        conv_codes: set[str] = set()
        if e.conversation_id:
            conv_codes |= {c for (c,) in s.execute(select(ConversationLink.shipment_code).where(
                ConversationLink.conversation_id == e.conversation_id)).all()}
            for b in s.execute(select(Booking).where(Booking.conversation_id == e.conversation_id)).scalars():
                conv_codes |= set(bs.shipment_codes_of(s, b))
        reply_codes: set[str] = set()
        if e.in_reply_to:
            prev = s.execute(select(Email).where(Email.message_id == e.in_reply_to)).scalar_one_or_none()
            if prev is not None and prev.match_status == "APPLIED":  # chi tin email da duoc xac nhan
                reply_codes = set(prev.shipment_codes or [])
        refs = self._shipment_refs(s, today)
        known = {r.code for r in refs}
        missing = (conv_codes | reply_codes) - known
        if missing:  # lo ngoai cua so ngay nhung da gan conversation -> van xet
            for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(list(missing)))).scalars():
                refs = refs + [em.ShipmentRef(code=sh.shipment_code, jo=sh.jo, go=sh.go, po=sh.po,
                                              booking_nos=(sh.booking_no,), buyer=sh.buyer_code, etd=sh.etd)]
        cands = em.score_candidates(refs, tokens=em.tokenize(text), text_norm=norm_key(text),
                                    fields={**fields, "_forwarder": e.forwarder},
                                    conversation_codes=conv_codes, reply_codes=reply_codes, weights=self.weights)
        return em.decide(cands, auto=self.auto, review=self.review, margin=self.margin)

    def _notify(self, s: Session, e: Email, codes: list[str], title: str, severity: str) -> None:
        owners = {""}
        if codes:
            owners = {x for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes))).scalars()
                      for x in (sh.assigned_to, sh.backup_owner) if x} or {""}
        for r in owners:
            s.add(Notification(category="EMAIL_REVIEW", severity=severity, title=title,
                               body=f"{e.subject[:160]} (tu {e.sender})", recipient=r,
                               shipment_code=codes[0] if len(codes) == 1 else "", dedup_key=f"EMAIL:{e.id}:{r}",
                               context={"email_id": e.id}, delivered_at=utcnow()))

    # ------------------------------------------------------------------
    # Ap dung vao booking / lo hang
    # ------------------------------------------------------------------

    def _booking_fields(self, e: Email, choices: dict[str, Any] | None) -> dict[str, Any]:
        merged = ep.merge_fields(e.extracted or {}, e.attachment_extracted or {})
        for f, pair in (e.conflicts or {}).items():
            if choices and f in choices:
                c = choices[f]
                merged[f] = pair.get(c, c) if c in ("email", "pdf") else c
            else:
                merged.pop(f, None)  # chua chon -> khong ghi
        out = {k: merged.get(k) for k in ("booking_no", "vessel", "voyage", "etd", "eta", "cy_cutoff",
                                          "si_vgm_cutoff", "closing_time", "port") if merged.get(k)}
        if merged.get("containers"):
            out["container_plan"] = merged["containers"]
        if e.forwarder:
            out["forwarder"] = e.forwarder
        return out

    def _find_booking(self, s: Session, e: Email, codes: list[str], booking_no: str) -> Booking | None:
        if booking_no:
            b = s.execute(select(Booking).where(Booking.booking_no == booking_no.upper())
                          .order_by(Booking.id.desc())).scalars().first()
            if b is not None:
                return b
        if e.conversation_id:
            b = s.execute(select(Booking).where(Booking.conversation_id == e.conversation_id)
                          .order_by(Booking.id.desc())).scalars().first()
            if b is not None:
                return b
        rows = s.execute(select(Booking).join(BookingShipment, BookingShipment.booking_id == Booking.id)
                         .where(BookingShipment.shipment_code.in_(codes or ["-"]))
                         .order_by(Booking.id.desc())).scalars().all()
        active = [b for b in rows if b.status not in ("CANCELLED", "REBOOKED")]
        return (active or rows or [None])[0]

    def apply(self, s: Session, e: Email, codes: list[str], *, actor: str, category: str | None = None,
              choices: dict[str, Any] | None = None, today: dt.date | None = None) -> dict[str, Any]:
        if not codes:
            raise EmailError("Chon it nhat 1 lo hang")
        if e.conflicts and not choices and actor == "automation":
            raise EmailError("Email va PDF mau thuan - can nguoi chon")
        missing = [f for f in (e.conflicts or {}) if not choices or f not in choices]
        if missing:
            raise EmailError(f"Chon gia tri cho: {', '.join(missing)}")
        today = today or dt.date.today()
        cat = category or e.category
        src = "EMAIL"
        fields = self._booking_fields(e, choices)
        b = self._find_booking(s, e, codes, fields.get("booking_no", ""))
        note = e.subject[:200]
        hold_days = self.settings.deadlines.hold_reminder_days
        result: dict[str, Any] = {"category": cat, "booking": None, "kept": []}

        def ensure_links(book: Booking) -> None:
            linked = set(bs.shipment_codes_of(s, book))
            for c in codes:
                if c not in linked:
                    bs.link_shipment(s, book, c, None, actor=actor, source=src)

        def upsert(status: str) -> Booking:
            nonlocal b
            if b is None:
                b = bs.create_booking(s, shipments=[(c, None) for c in codes], fields=fields, status=status,
                                      source=src, actor=actor, conversation_id=e.conversation_id, email_id=e.id)
                return b
            if b.status in bs.bk.CLOSED:  # booking cu da huy -> email moi = booking thay the
                b = bs.rebook(s, b, fields, actor=actor, source=src, email_id=e.id, reason=note)
                ensure_links(b)
                return b
            bs.update_fields(s, b, fields, actor=actor, source=src, email_id=e.id)
            ensure_links(b)
            if b.status != status and status in bs.bk.TRANSITIONS.get(b.status, set()):
                bs.set_status(s, b, status, actor=actor, source=src, reason=note, email_id=e.id, hold_days=hold_days)
            if not b.conversation_id and e.conversation_id:
                b.conversation_id = e.conversation_id
            return b

        if cat == "BOOKING_REQUEST":
            if b is None or b.status in ("CANCELLED", "REBOOKED"):
                b = bs.create_booking(s, shipments=[(c, None) for c in codes], fields=fields, status="REQUESTED",
                                      source=src, actor=actor, conversation_id=e.conversation_id, email_id=e.id)
            else:
                ensure_links(b)
                if not b.conversation_id:
                    b.conversation_id = e.conversation_id
        elif cat == "BOOKING_CONFIRMATION":
            upsert("CONFIRMED")
        elif cat == "AMENDMENT":
            upsert("AMENDED")
        elif cat == "DELAY":
            upsert("DELAYED")
        elif cat == "REBOOK":
            if b is not None and fields.get("booking_no") and norm_key(fields["booking_no"]) != norm_key(b.booking_no):
                b = bs.rebook(s, b, fields, actor=actor, source=src, email_id=e.id, reason=note)
            else:
                upsert("CONFIRMED")
        elif cat == "CANCEL":
            if b is not None and b.status not in ("CANCELLED", "REBOOKED"):
                target = "CANCEL_REQUESTED" if e.direction == "OUT" else "CANCELLED"
                bs.set_status(s, b, target, actor=actor, source=src, reason=note, email_id=e.id, force=True)
        elif cat == "HOLD":
            if b is not None and b.status != "ON_HOLD":
                bs.set_status(s, b, "ON_HOLD", actor=actor, source=src, reason=note, email_id=e.id,
                              hold_days=hold_days, force=True)
        elif cat == "RELEASE_HOLD":
            if b is not None and b.status == "ON_HOLD":
                bs.set_status(s, b, "CONFIRMED", actor=actor, source=src, reason=note, email_id=e.id)

        if b is not None and cat in BOOKING_DATA:
            result["kept"] = bs.push_to_shipments(s, self.engine, b, actor=actor if actor != "automation" else "email",
                                                  source="OUTLOOK", today=today)
            for k in result["kept"]:
                raise_exception(s, "EX020", k, shipment_code=codes[0], value=e.subject[:120],
                                suggested_action="Kiem tra email, sua tay neu dung",
                                dedup_key=f"EX020:{e.id}:{hashlib.md5(k.encode()).hexdigest()[:8]}")
            self._attach_documents(s, e, codes, actor)

        for c in codes:
            if e.conversation_id and not s.execute(select(ConversationLink.id).where(
                    ConversationLink.conversation_id == e.conversation_id,
                    ConversationLink.shipment_code == c)).first():
                s.add(ConversationLink(conversation_id=e.conversation_id, shipment_code=c,
                                       booking_id=b.id if b else None,
                                       source="AUTO" if actor == "automation" else "USER", created_by=actor))
            bs.log_event(s, "EMAIL_" + cat, shipment_code=c, booking_id=b.id if b else None, email_id=e.id,
                         source=src, actor=actor, new=fields.get("booking_no", ""), note=note)

        e.match_status, e.shipment_codes = "APPLIED", codes
        e.booking_id = b.id if b else None
        e.applied_at, e.applied_by = utcnow(), actor
        if category:
            e.category = category
        s.flush()
        bs.refresh(s, self.engine, codes, today)
        result["booking"] = b.id if b else None
        return result

    def _attach_documents(self, s: Session, e: Email, codes: list[str], actor: str) -> None:
        atts = s.execute(select(EmailAttachment).where(EmailAttachment.email_id == e.id)).scalars().all()
        for a in atts:
            if not a.filename.lower().endswith(".pdf") or not a.stored_path or not Path(a.stored_path).exists():
                continue
            content = Path(a.stored_path).read_bytes()
            for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes))).scalars():
                try:
                    ds.add_version(s, self.settings, sh, "BOOKING", a.filename, content, actor=actor, source="EMAIL",
                                   email_id=e.id, note=e.subject[:200])
                except ds.DocumentError:
                    continue

    def ignore(self, s: Session, e: Email, *, actor: str) -> None:
        e.match_status, e.applied_by, e.applied_at = "IGNORED", actor, utcnow()
        log_audit(s, "EMAIL_IGNORED", actor_type="USER", actor_name=actor, entity="email", entity_id=str(e.id),
                  note=e.subject[:200])
