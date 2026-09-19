"""BOT ENGINE - dieu phoi toan bo pipeline (SRS muc 4 & 63).

    Load Configuration -> Scan Follow Up -> Detect Sheets -> Read Headers
    -> Normalize -> Validate -> Identify Shipment -> Group NO/JO/INV
    -> Apply Buyer Rule -> Calculate Container -> Calculate Status
    -> Apply User Override -> Check Deadline -> Check File Version
    -> Update Excel -> Verify -> Audit Log -> Wait Next Scan
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_audit, raise_exception
from .core import deadline as dl
from .core import status as st
from .core.buyer_rules import BuyerRegistry
from .core.container import plan_containers
from .core.normalize import clean_text, norm_key, to_canonical_date
from .core.shipment import (
    ShipmentCandidate,
    build_candidates,
    detect_merge_candidates,
    detect_split_jo,
    make_shipment_code,
    summarize_groups,
)
from .db import session_scope
from .db.models import (
    Buyer,
    ColumnMapping,
    ContainerAllocation,
    CustomsDeclaration,
    Invoice,
    PendingWrite,
    PlanningRecord,
    ScanRun,
    SheetMap,
    Shipment,
    SourceFile,
    User,
    UserOverride,
    utcnow,
)
from .excel.backup import snapshot
from .excel.mapping import FieldDictionary
from .excel.reader import CanonicalRow, read_workbook
from .excel.writer import ColorOp, ExcelTargetWriter, FollowUpColorWriter, WriteReport
from .logging_setup import get_logger
from .notify.engine import NotificationEngine
from .outputs import REGISTRY as OUTPUT_REGISTRY
from .outputs.base import AllocationLike
from .outputs.shipping_schedule import resolve_site
from .settings import Settings

log = get_logger("engine")


@dataclass
class ScanResult:
    started_at: dt.datetime = field(default_factory=dt.datetime.now)
    finished_at: dt.datetime | None = None
    dry_run: bool = False
    files_scanned: int = 0
    sheets_scanned: int = 0
    rows_read: int = 0
    shipments_total: int = 0
    shipments_new: int = 0
    shipments_changed: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    transitions: list[str] = field(default_factory=list)
    events: int = 0
    notifications_sent: int = 0
    exceptions: int = 0
    write_reports: list[WriteReport] = field(default_factory=list)
    color_ops: int = 0
    needs_confirmation: list[dict[str, Any]] = field(default_factory=list)
    merge_suggestions: list[dict[str, Any]] = field(default_factory=list)
    split_jo: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "files_scanned": self.files_scanned,
            "sheets_scanned": self.sheets_scanned,
            "rows_read": self.rows_read,
            "shipments_total": self.shipments_total,
            "shipments_new": self.shipments_new,
            "shipments_changed": self.shipments_changed,
            "status_counts": self.status_counts,
            "transitions": self.transitions[:50],
            "events": self.events,
            "notifications_sent": self.notifications_sent,
            "exceptions": self.exceptions,
            "color_ops": self.color_ops,
            "writes": [
                {
                    "file": r.file_code,
                    "insert": r.inserted,
                    "update": r.updated,
                    "skip": r.skipped,
                    "applied": r.applied,
                    "error": r.error,
                    "conflict": r.conflict,
                    "ops": [o.describe() for o in r.ops[:80]],
                }
                for r in self.write_reports
            ],
            "needs_confirmation": self.needs_confirmation,
            "merge_suggestions": self.merge_suggestions,
            "split_jo": self.split_jo,
            "errors": self.errors,
        }

    def render_text(self) -> str:
        """Bao cao Dry Run dang van ban (SRS muc 53)."""
        lines = [
            "=" * 62,
            f"{'DRY RUN - KHONG GHI FILE' if self.dry_run else 'SCAN'} "
            f"@ {self.started_at:%Y-%m-%d %H:%M:%S}",
            "=" * 62,
            f"File quet          : {self.files_scanned}",
            f"Sheet Follow Up    : {self.sheets_scanned}",
            f"Dong du lieu       : {self.rows_read}",
            f"Shipment           : {self.shipments_total} "
            f"(moi {self.shipments_new}, thay doi {self.shipments_changed})",
            "",
            "Trang thai:",
        ]
        icons = {"WHITE": "o", "YELLOW": "!", "GREEN": "+", "RED": "X",
                 "COMPLETED": "*", "PARTIAL": "/", "CANCELLED": "-"}
        for s, c in self.status_counts.items():
            if c:
                lines.append(f"  [{icons.get(s, '?')}] {s:<10} {c}")
        if self.transitions:
            lines += ["", "Chuyen trang thai:"] + [f"  {t}" for t in self.transitions[:20]]
        if self.write_reports:
            lines += ["", "Ghi file dich:"]
            for r in self.write_reports:
                flag = f"  LOI: {r.error}" if r.error else ""
                lines.append(
                    f"  {r.file_code:<10} +{r.inserted} ~{r.updated} ={r.skipped}"
                    f" {'(mau ' + str(r.colored) + ')' if r.colored else ''}{flag}"
                )
        if self.needs_confirmation:
            lines += ["", "Can User xac nhan sheet:"]
            for n in self.needs_confirmation:
                lines.append(f"  {n['file']} / {n['sheet']}  (confidence {n['confidence']:.0%})")
        if self.merge_suggestions:
            lines += ["", f"Goi y gop lo: {len(self.merge_suggestions)}"]
        lines += ["", f"Thong bao: {self.events} su kien, gui {self.notifications_sent}",
                  f"Exception: {self.exceptions}"]
        if self.errors:
            lines += ["", "Loi:"] + [f"  - {e}" for e in self.errors]
        lines.append("=" * 62)
        return "\n".join(lines)


class BookingBotEngine:
    def __init__(self, settings: Settings, notifier: NotificationEngine | None = None):
        self.settings = settings
        self.dictionary = FieldDictionary(settings.field_mapping)
        self.buyers = BuyerRegistry(settings.buyer_rules)
        self.notifier = notifier or NotificationEngine(settings)

    # ------------------------------------------------------------------
    # Bootstrap: dong bo config -> DB
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        with session_scope() as s:
            for u in self.settings.users:
                row = s.execute(select(User).where(User.name == u.name)).scalar_one_or_none()
                if row is None:
                    s.add(User(name=u.name, display_name=u.display_name, email=u.email, role=u.role))
                else:
                    row.display_name, row.email, row.role = u.display_name, u.email, u.role

            for f in self.settings.files:
                row = s.execute(select(SourceFile).where(SourceFile.code == f.id)).scalar_one_or_none()
                data = dict(
                    role=f.role, display_name=f.display_name, path=f.path, storage=f.storage,
                    owner=f.owner, can_read=f.read, can_write=f.write, options=f.options,
                )
                if row is None:
                    s.add(SourceFile(code=f.id, **data))
                else:
                    for k, v in data.items():
                        setattr(row, k, v)

            for rule in self.buyers.all_rules():
                row = s.execute(select(Buyer).where(Buyer.code == rule.code)).scalar_one_or_none()
                payload = {
                    "co_required": rule.co_required, "co_form": rule.co_form,
                    "container_rule": rule.container_rule, "shipment_group": rule.shipment_group,
                    "invoice_rule": rule.invoice_rule, "declaration_rule": rule.declaration_rule,
                    "site": rule.site, "lcl_threshold_cbm": rule.lcl_threshold_cbm,
                    "destination_default": rule.destination_default,
                }
                if row is None:
                    s.add(Buyer(code=rule.code, canonical_name=rule.canonical_name,
                                aliases=rule.aliases, rules=payload))
                else:
                    row.canonical_name, row.aliases, row.rules = rule.canonical_name, rule.aliases, payload
            log.info("Bootstrap xong: %s user, %s file, %s buyer",
                     len(self.settings.users), len(self.settings.files), len(self.buyers.all_rules()))

    # ------------------------------------------------------------------
    # Scan chinh
    # ------------------------------------------------------------------

    def run_scan(
        self, *, dry_run: bool = False, today: dt.date | None = None, deliver_notifications: bool = True
    ) -> ScanResult:
        today = today or dt.date.today()
        dry_run = dry_run or self.settings.excel_write.dry_run_default
        res = ScanResult(dry_run=dry_run)

        with session_scope() as s:
            run = ScanRun(mode="DRY_RUN" if dry_run else "SCAN")
            s.add(run)
            s.flush()

            try:
                candidates = self._scan_follow_ups(s, res)
                shipments = self._persist_shipments(s, candidates, res, today)
                allocations = self._allocate_containers(s, shipments, candidates)
                self._evaluate_status(s, shipments, res)
                events = self._evaluate_deadlines(s, shipments, candidates, res, today)
                self._dispatch(s, events, shipments, res, deliver_notifications)
                self._write_outputs(s, shipments, allocations, res, dry_run, today)
                self._write_colors(s, shipments, candidates, res, dry_run)
                self._analyse_extras(candidates, res)
                run.ok = True
            except Exception as exc:  # noqa: BLE001
                log.exception("Scan that bai")
                res.errors.append(str(exc))
                run.ok = False
                run.error = str(exc)

            res.finished_at = dt.datetime.now()
            run.finished_at = utcnow()
            run.files_scanned = res.files_scanned
            run.sheets_scanned = res.sheets_scanned
            run.shipments_found = res.shipments_total
            run.shipments_new = res.shipments_new
            run.shipments_changed = res.shipments_changed
            run.notifications = res.notifications_sent
            run.exceptions = res.exceptions
            run.summary = res.as_dict()

        return res

    # -- buoc 1: doc Follow Up -----------------------------------------

    def _scan_follow_ups(self, s: Session, res: ScanResult) -> list[ShipmentCandidate]:
        rows: list[CanonicalRow] = []

        for fcfg in self.settings.files_by_role("follow_up"):
            path = self.settings.resolve_path(fcfg.path)
            if not path.exists():
                res.errors.append(f"Khong tim thay file Follow Up: {path}")
                raise_exception(
                    s, "EX006", f"Khong tim thay file: {path}", severity="CRITICAL",
                    source_file=str(path),
                    suggested_action="Kiem tra duong dan trong config.yaml hoac OneDrive da sync chua",
                    dedup_key=f"MISSING:{fcfg.id}",
                )
                res.exceptions += 1
                continue

            overrides = {
                cm.source_header: cm.canonical_field
                for cm in s.execute(
                    select(ColumnMapping).where(ColumnMapping.file_code == fcfg.id)
                ).scalars()
                if cm.confirmed_by_user
            }
            confirmed = {
                sm.sheet_name: sm.is_follow_up
                for sm in s.execute(
                    select(SheetMap)
                    .join(SourceFile, SheetMap.file_id == SourceFile.id)
                    .where(SourceFile.code == fcfg.id)
                ).scalars()
                if sm.confirmed_by_user
            }

            try:
                wb_result = read_workbook(
                    path, fcfg.id, self.dictionary, overrides, confirmed, owner_user=fcfg.owner
                )
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"{fcfg.id}: {exc}")
                raise_exception(s, "EX006", f"Khong doc duoc file: {exc}", severity="CRITICAL",
                                source_file=str(path), dedup_key=f"READFAIL:{fcfg.id}")
                res.exceptions += 1
                continue

            res.files_scanned += 1
            self._sync_sheet_maps(s, fcfg.id, wb_result)

            for det in wb_result.needs_user_confirmation:
                res.needs_confirmation.append(
                    {"file": fcfg.id, "sheet": det.sheet_name, "confidence": det.confidence,
                     "note": det.note, "matched": det.matched_fields}
                )
                raise_exception(
                    s, "EX005",
                    f"Sheet '{det.sheet_name}' co the la Follow Up (confidence {det.confidence:.0%}) "
                    "nhung Bot khong chac chan.",
                    source_file=str(path), source_sheet=det.sheet_name,
                    suggested_action="Vao Dashboard > Mapping de xac nhan mot lan",
                    payload={"matched": det.matched_fields, "missing": det.missing_required},
                    dedup_key=f"EX005:{fcfg.id}:{det.sheet_name}",
                )
                res.exceptions += 1

            for sheet_res in wb_result.sheets:
                res.sheets_scanned += 1
                for row in sheet_res.rows:
                    rows.append(row)
                    for issue in row.issues:
                        code = {"ExFtyDate": "EX001", "FinalCBM": "EX004"}.get(issue["field"], "EX011")
                        raise_exception(
                            s, code, f"{issue['field']}: {issue['reason']}",
                            severity="CRITICAL" if issue["severity"] == "INVALID" else "WARNING",
                            source_file=str(path), source_sheet=row.sheet, source_row=row.row_index,
                            source_column=issue["column"], value=issue["raw"],
                            suggested_action="Sua truc tiep trong file Follow Up roi cho lan scan sau",
                            dedup_key=f"{code}:{fcfg.id}:{row.sheet}:{row.row_index}:{issue['field']}",
                        )
                        res.exceptions += 1

                det = sheet_res.detection
                if det.header_map and det.header_map.ambiguous:
                    for amb in det.header_map.ambiguous:
                        raise_exception(
                            s, "EX010",
                            f"Cot '{amb.source_header}' giong '{amb.canonical_field}' "
                            f"({amb.confidence:.0%}) nhung chua du chac chan.",
                            source_file=str(path), source_sheet=det.sheet_name,
                            source_column=amb.column_letter,
                            suggested_action="Dashboard > Mapping de xac nhan anh xa cot",
                            payload={"header": amb.source_header, "field": amb.canonical_field},
                            dedup_key=f"EX010:{fcfg.id}:{det.sheet_name}:{amb.source_header}",
                        )
                        res.exceptions += 1

        res.rows_read = len(rows)
        candidates, duplicates = build_candidates(rows, self.buyers.resolve)
        for dup in duplicates:
            raise_exception(
                s, dup["code"], dup["reason"], severity=dup["severity"],
                source_file=dup["source_file"], source_sheet=dup["source_sheet"],
                source_row=dup["source_row"], value=dup["value"],
                suggested_action=dup["suggested_action"], dedup_key=dup["dedup_key"],
            )
            res.exceptions += 1

        for cand in candidates:
            for issue in cand.issues:
                raise_exception(
                    s, issue["code"], issue["reason"], severity=issue["severity"],
                    source_file=cand.row.file_path, source_sheet=cand.row.sheet,
                    source_row=cand.row.row_index,
                    suggested_action=issue["suggested_action"],
                    dedup_key=f"{issue['code']}:{cand.buyer_rule.code}",
                )
                res.exceptions += 1

        return candidates

    def _sync_sheet_maps(self, s: Session, file_code: str, wb_result) -> None:
        src = s.execute(select(SourceFile).where(SourceFile.code == file_code)).scalar_one_or_none()
        if src is None:
            return
        all_dets = [sr.detection for sr in wb_result.sheets] + wb_result.needs_user_confirmation + wb_result.ignored
        for det in all_dets:
            row = s.execute(
                select(SheetMap).where(SheetMap.file_id == src.id, SheetMap.sheet_name == det.sheet_name)
            ).scalar_one_or_none()
            sig = det.header_map.signature() if det.header_map else ""
            if row is None:
                s.add(SheetMap(
                    file_id=src.id, sheet_name=det.sheet_name, buyer_raw=det.sheet_name,
                    is_follow_up=det.is_follow_up, confidence=det.confidence,
                    header_row=det.header_row, detection_note=det.note, header_signature=sig,
                ))
            else:
                if row.header_signature and sig and row.header_signature != sig:
                    row.detection_note = "Cau truc header da thay doi so voi lan truoc"
                if not row.confirmed_by_user:
                    row.is_follow_up = det.is_follow_up
                row.confidence = det.confidence
                row.header_row = det.header_row or row.header_row
                row.header_signature = sig or row.header_signature

        src.last_scanned_at = utcnow()
        path = self.settings.resolve_path(src.path)
        if path.exists():
            src.last_version_token = snapshot(path, with_hash=False).version_token

    # -- buoc 2: luu shipment ------------------------------------------

    def _persist_shipments(
        self, s: Session, candidates: list[ShipmentCandidate], res: ScanResult, today: dt.date
    ) -> dict[str, Shipment]:
        out: dict[str, Shipment] = {}
        seq_by_date: dict[str, int] = {}

        for cand in candidates:
            v = cand.row.values
            existing = s.execute(
                select(Shipment).where(Shipment.shipment_key == cand.key)
            ).scalar_one_or_none()

            if existing is None:
                # Cung dong Excel, cung NO + JO, chi ExFtyDate doi -> VAN LA 1 lo hang
                # (SRS muc 48), khong phai lo moi. Phai doi lai khoa va tinh lai deadline.
                moved = self._find_rescheduled(s, cand)
                if moved is not None:
                    log_audit(s, "SHIPMENT_REKEYED", entity="shipment",
                              entity_id=moved.shipment_code, field="shipment_key",
                              old_value=moved.shipment_key, new_value=cand.key,
                              note="ExFtyDate thay doi - giu nguyen lo hang, tinh lai deadline")
                    moved.shipment_key = cand.key
                    existing = moved
                    res.shipments_changed += 1

            if existing is None:
                stamp = to_canonical_date(cand.exfty).replace("-", "") or today.strftime("%Y%m%d")
                seq_by_date[stamp] = seq_by_date.get(stamp, self._next_seq(s, stamp)) + 1
                code = make_shipment_code(cand.exfty, seq_by_date[stamp])
                # bot_status/effective_status de rong -> lan dau khong tinh la "chuyen trang thai"
                sh = Shipment(shipment_code=code, shipment_key=cand.key,
                              bot_status="", effective_status="")
                s.add(sh)
                res.shipments_new += 1
                log_audit(s, "SHIPMENT_CREATED", entity="shipment", entity_id=code,
                          new_value=f"{cand.jo} / {to_canonical_date(cand.exfty)}",
                          source=f"{cand.row.file_code}/{cand.row.sheet}!{cand.row.row_index}")
            else:
                sh = existing
                if sh.data_hash and sh.data_hash != cand.row.data_hash:
                    res.shipments_changed += 1

            # ExFtyDate thay doi -> tinh lai deadline (SRS muc 48)
            new_exfty = v.get("ExFtyDate")
            if sh.exfty_date and new_exfty and sh.exfty_date != new_exfty:
                log_audit(s, "EXFTY_CHANGED", entity="shipment", entity_id=sh.shipment_code,
                          field="ExFtyDate", old_value=sh.exfty_date, new_value=new_exfty,
                          note="Tinh lai toan bo deadline T-20/T-7/T-3/T-1")
                sh.previous_exfty = sh.exfty_date
                sh.exfty_changed_at = utcnow()
                self._warn_stale_planning_rows(s, sh, new_exfty)

            rule = cand.buyer_rule
            sh.group_key = cand.group
            sh.source_file_code = cand.row.file_code
            sh.source_sheet = cand.row.sheet
            sh.source_row = cand.row.row_index
            sh.owner_user = cand.row.owner_user
            sh.shipment_no = clean_text(v.get("ShipmentNo"))
            sh.jo = clean_text(v.get("JO"))
            sh.exfty_date = new_exfty
            sh.buyer_raw = clean_text(v.get("Buyer"))
            sh.buyer_code = rule.code
            sh.ship_mode = clean_text(v.get("ShipMode")).upper()
            sh.destination = clean_text(v.get("Destination")) or rule.destination_default
            sh.customs_code = clean_text(v.get("CustomsCode"))
            sh.order_qty = v.get("OrderQty")
            sh.final_export_qty = v.get("FinalExportQty")
            sh.final_carton_qty = v.get("FinalCartonQty")
            sh.gross_weight = v.get("GrossWeight")
            sh.final_cbm = v.get("FinalCBM")
            sh.cdf = clean_text(v.get("CDF"))
            sh.bl = clean_text(v.get("BL"))
            sh.price = v.get("Price")
            sh.discount_price = v.get("DiscountPrice")
            sh.pic = clean_text(v.get("PIC")) or cand.row.owner_user
            sh.remark = clean_text(v.get("Remark"))
            sh.booking_flag = bool(v.get("BookingFlag")) or bool(sh.cdf) or bool(sh.bl)
            was_hold = sh.hold_flag
            sh.hold_flag = bool(v.get("HoldFlag"))
            sh.hold_reason = clean_text(v.get("HoldReason"))
            sh.cancel_flag = bool(v.get("CancelFlag"))
            sh.shipped_flag = bool(v.get("ShippedFlag"))
            sh.shipped_qty = v.get("ShippedQty")
            sh.co_required = rule.co_required
            sh.co_form = rule.co_form
            sh.data_hash = cand.row.data_hash
            sh.source_version = cand.row.source_version
            sh.data_confidence = cand.row.confidence
            sh.site = rule.site or resolve_site(sh, rule.site, self.settings.site_rules)
            sh.is_active = True
            sh.last_seen_at = utcnow()

            if sh.hold_flag and not was_hold:
                sh.hold_since = today
            elif not sh.hold_flag:
                sh.hold_since = None

            s.flush()
            self._sync_invoices(s, sh, cand)
            out[sh.shipment_code] = sh

        res.shipments_total = len(out)
        return out

    def _warn_stale_planning_rows(self, s: Session, sh: Shipment, new_exfty: dt.date | None) -> None:
        """Doi ExFtyDate sau khi da ghi Planning -> dong cu con o lai file dich.

        Bot KHONG tu xoa dong trong file dung chung (co the co ghi chu cua nguoi khac).
        Thay vao do bao cho User biet chinh xac dong nao can sua/xoa.
        """
        old_rows = list(
            s.execute(
                select(PlanningRecord).where(PlanningRecord.shipment_code == sh.shipment_code)
            ).scalars()
        )
        for pr in old_rows:
            pr.state = "STALE"
            raise_exception(
                s, "EX007",
                f"{sh.jo}: ExFtyDate doi {to_canonical_date(sh.exfty_date)} -> "
                f"{to_canonical_date(new_exfty)} nhung dong cu van con trong "
                f"{pr.target_role} (dong {pr.target_row}).",
                severity="WARNING",
                source_file=pr.target_file_code, source_sheet=pr.target_sheet,
                source_row=pr.target_row, shipment_code=sh.shipment_code,
                suggested_action="Xoa hoac sua dong cu trong file dich; Bot se them dong moi theo ngay moi",
                dedup_key=f"STALE:{pr.target_role}:{pr.external_key}",
            )

    def _find_rescheduled(self, s: Session, cand: ShipmentCandidate) -> Shipment | None:
        """Tim lo hang cu o dung dong Excel do, chi khac moi ExFtyDate."""
        row = cand.row
        sh = s.execute(
            select(Shipment).where(
                Shipment.source_file_code == row.file_code,
                Shipment.source_sheet == row.sheet,
                Shipment.source_row == row.row_index,
                Shipment.is_active.is_(True),
            )
        ).scalars().first()
        if sh is None:
            return None
        if norm_key(sh.shipment_no) != norm_key(row.values.get("ShipmentNo")):
            return None
        if norm_key(sh.jo) != norm_key(row.values.get("JO")):
            return None
        if sh.exfty_date == row.values.get("ExFtyDate"):
            return None
        return sh

    def _next_seq(self, s: Session, stamp: str) -> int:
        prefix = f"SHP-{stamp}-"
        rows = s.execute(
            select(Shipment.shipment_code).where(Shipment.shipment_code.like(prefix + "%"))
        ).scalars()
        best = 0
        for code in rows:
            try:
                best = max(best, int(code.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        return best

    def _sync_invoices(self, s: Session, sh: Shipment, cand: ShipmentCandidate) -> None:
        existing = {i.invoice_no for i in sh.invoices}
        for inv_no in cand.invoices:
            if inv_no not in existing:
                s.add(Invoice(shipment_id=sh.id, invoice_no=inv_no))
        rule = cand.buyer_rule
        if sh.cdf and not sh.declarations:
            if rule.declaration_rule == "one_per_invoice" and cand.invoices:
                for inv_no in cand.invoices:
                    s.add(CustomsDeclaration(shipment_id=sh.id, declaration_no=sh.cdf,
                                             customs_code=sh.customs_code, invoice_nos=[inv_no],
                                             group_key=f"{sh.shipment_no}:{inv_no}"))
            else:
                s.add(CustomsDeclaration(shipment_id=sh.id, declaration_no=sh.cdf,
                                         customs_code=sh.customs_code, invoice_nos=cand.invoices,
                                         group_key=sh.shipment_no))

    # -- buoc 3: container ---------------------------------------------

    def _allocate_containers(
        self, s: Session, shipments: dict[str, Shipment], candidates: list[ShipmentCandidate]
    ) -> dict[str, ContainerAllocation]:
        ccfg = self.settings.container
        groups = summarize_groups(candidates)
        by_group_ships: dict[str, list[Shipment]] = {}
        for sh in shipments.values():
            by_group_ships.setdefault(sh.group_key, []).append(sh)

        result: dict[str, ContainerAllocation] = {}
        for gkey, ships in by_group_ships.items():
            cands = groups.get(gkey, [])
            rule = cands[0].buyer_rule if cands else self.buyers.resolve(ships[0].buyer_code)
            total_cbm = sum(float(x.final_cbm or 0) for x in ships)
            ship_mode = next((x.ship_mode for x in ships if x.ship_mode), "SEA")
            threshold = rule.lcl_threshold_cbm if rule.lcl_threshold_cbm is not None else ccfg.lcl_threshold_cbm

            plan = plan_containers(
                total_cbm, ship_mode,
                capacity=ccfg.capacity, priority=ccfg.priority, strategy=ccfg.strategy,
                lcl_threshold_cbm=float(threshold), safety_factor=ccfg.safety_factor,
                air_type=ccfg.air_type, lcl_type=ccfg.lcl_type,
                forced_type=rule.forced_type, needs_user_review=rule.needs_user_review,
            )

            alloc = s.execute(
                select(ContainerAllocation).where(ContainerAllocation.group_key == gkey)
            ).scalar_one_or_none()
            if alloc is None:
                alloc = ContainerAllocation(group_key=gkey)
                s.add(alloc)

            if alloc.allocation_source == "USER":
                # USER > BOT: khong ghi de phuong an User da chot (SRS muc 35)
                alloc.total_cbm = plan.total_cbm
                result[gkey] = alloc
                continue

            alloc.total_cbm = plan.total_cbm
            alloc.qty_40hc = plan.qty_40hc
            alloc.qty_40dc = plan.qty_40dc
            alloc.qty_20st = plan.qty_20st
            alloc.estimated_capacity = plan.estimated_capacity
            alloc.excess_capacity = plan.excess_capacity
            alloc.booking_mode = plan.booking_mode
            alloc.container_type = plan.container_type
            alloc.allocation_status = plan.allocation_status
            alloc.allocation_source = plan.allocation_source
            alloc.note = plan.note
            alloc.shipment_id = ships[0].id

            for sh in ships:
                sh.booking_mode = plan.booking_mode

            if plan.allocation_status == "NEED_REVIEW":
                raise_exception(
                    s, "EX009", plan.note or "Bot khong tu quyet dinh duoc container",
                    source_file=ships[0].source_file_code, source_sheet=ships[0].source_sheet,
                    source_row=ships[0].source_row, value=f"CBM={plan.total_cbm}",
                    suggested_action="Dashboard > Shipments > chon container thu cong",
                    shipment_code=ships[0].shipment_code, dedup_key=f"EX009:{gkey}",
                )
            result[gkey] = alloc

        s.flush()
        return result

    # -- buoc 4: status -------------------------------------------------

    def _evaluate_status(self, s: Session, shipments: dict[str, Shipment], res: ScanResult) -> None:
        blanks = self.dictionary.blanks or None
        overrides = self._load_overrides(s)

        statuses = []
        for sh in shipments.values():
            values = {
                "HoldFlag": sh.hold_flag, "HoldReason": sh.hold_reason,
                "HoldReasonDetail": sh.hold_reason_detail, "CancelFlag": sh.cancel_flag,
                "ShippedFlag": sh.shipped_flag, "ShippedQty": sh.shipped_qty,
                "FinalExportQty": sh.final_export_qty, "BookingFlag": sh.booking_flag,
                "CDF": sh.cdf, "BL": sh.bl,
            }
            result = st.evaluate(values, blanks=blanks)
            old_effective = sh.effective_status

            if sh.bot_status and sh.bot_status != result.status:
                log_audit(s, "STATUS_CHANGED", entity="shipment", entity_id=sh.shipment_code,
                          field="bot_status", old_value=sh.bot_status, new_value=result.status,
                          note=result.reason)
            sh.bot_status = result.status
            sh.status_reason = result.as_dict()

            ov = overrides.get(sh.shipment_code, {}).get("status")
            effective, source = st.effective_status(result.status, ov)
            sh.effective_status = effective
            if source == "USER":
                sh.status_reason = {**sh.status_reason, "override": ov, "override_source": "USER"}

            if old_effective and old_effective != effective:
                res.transitions.append(f"{sh.jo or sh.shipment_code}: {old_effective} -> {effective}")
            if effective == "COMPLETED" and sh.completed_at is None:
                sh.completed_at = utcnow()
            statuses.append(effective)

        res.status_counts = st.status_counts(statuses)

    def _load_overrides(self, s: Session) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for ov in s.execute(select(UserOverride).where(UserOverride.active.is_(True))).scalars():
            out.setdefault(ov.shipment_code, {})[ov.field] = ov.user_value
        return out

    # -- buoc 5: deadline ----------------------------------------------

    def _evaluate_deadlines(
        self, s: Session, shipments: dict[str, Shipment], candidates: list[ShipmentCandidate],
        res: ScanResult, today: dt.date,
    ) -> list[dl.DeadlineEvent]:
        cfg = self.settings.deadlines
        by_code = {c.key: c for c in candidates}
        events: list[dl.DeadlineEvent] = []
        last_hold = self._last_hold_notice(s)

        for sh in shipments.values():
            cand = by_code.get(sh.shipment_key)
            t3 = None
            if cand is not None:
                t3 = dl.check_t3_fields(cand.row.values, cand.row.states)

            evs = dl.evaluate_shipment(
                shipment_code=sh.shipment_code, jo=sh.jo, buyer=sh.buyer_code,
                exfty=sh.exfty_date, status=sh.effective_status, cfg=cfg, today=today,
                hold_since=sh.hold_since, last_hold_notice=last_hold.get(sh.shipment_code),
                t3_check=t3, hold_reason=self.buyers.hold_reason_label(sh.hold_reason),
            )
            for ev in evs:
                ev.payload.setdefault("shipment_code", sh.shipment_code)
                ev.payload["status"] = sh.effective_status
                ev.payload["source_file"] = str(self.settings.resolve_path(
                    self.settings.file_by_code(sh.source_file_code).path
                )) if self.settings.file_by_code(sh.source_file_code) else ""
                ev.payload["source_sheet"] = sh.source_sheet
                ev.payload["source_row"] = sh.source_row
            events.extend(evs)

            if any(e.category == "INVALID_DATE" for e in evs):
                raise_exception(
                    s, "EX001", "ExFtyDate khong hop le hoac bi trong", severity="CRITICAL",
                    source_file=sh.source_file_code, source_sheet=sh.source_sheet,
                    source_row=sh.source_row, shipment_code=sh.shipment_code,
                    suggested_action="Dien lai ngay xuat trong file Follow Up",
                    dedup_key=f"EX001:{sh.shipment_code}",
                )
                res.exceptions += 1

        res.events = len(events)
        return events

    def _last_hold_notice(self, s: Session) -> dict[str, dt.date]:
        from .db.models import Notification

        out: dict[str, dt.date] = {}
        for n in s.execute(
            select(Notification).where(Notification.category == "HOLD_REMINDER")
        ).scalars():
            if n.shipment_code:
                d = n.created_at.date()
                if n.shipment_code not in out or out[n.shipment_code] < d:
                    out[n.shipment_code] = d
        return out

    # -- buoc 6: thong bao ---------------------------------------------

    def _dispatch(
        self, s: Session, events: list[dl.DeadlineEvent], shipments: dict[str, Shipment],
        res: ScanResult, deliver: bool,
    ) -> None:
        lookup = {
            sh.shipment_code: {
                "jo": sh.jo, "buyer": sh.buyer_code, "exfty": to_canonical_date(sh.exfty_date),
                "status": sh.effective_status,
                "hold_reason": self.buyers.hold_reason_label(sh.hold_reason) if sh.hold_reason else "",
            }
            for sh in shipments.values()
        }
        # Khong ban thong bao khi dang Dry Run
        out = self.notifier.dispatch_events(s, events, source_lookup=lookup,
                                            deliver=deliver and not res.dry_run)
        res.notifications_sent = out.sent

    # -- buoc 7: ghi file dich -----------------------------------------

    def _write_outputs(
        self, s: Session, shipments: dict[str, Shipment],
        allocations: dict[str, ContainerAllocation], res: ScanResult,
        dry_run: bool, today: dt.date,
    ) -> None:
        ecfg = self.settings.excel_write
        ccfg = self.settings.container

        for role, module in OUTPUT_REGISTRY.items():
            spec = module.SPEC
            for fcfg in self.settings.files_by_role(role):
                if not fcfg.write:
                    continue
                path = self.settings.resolve_path(fcfg.path)
                if not path.exists():
                    res.errors.append(f"Khong tim thay file dich {fcfg.id}: {path}")
                    continue

                by_sheet: dict[str | None, list[dict[str, Any]]] = {}
                for sh in shipments.values():
                    if sh.effective_status in ("CANCELLED",):
                        continue
                    due = dl.due_milestones(sh.exfty_date, self.settings.deadlines, today)
                    if spec.milestone not in due:
                        continue
                    alloc_row = allocations.get(sh.group_key)
                    alloc = AllocationLike(
                        container_type=alloc_row.container_type if alloc_row else "",
                        booking_mode=alloc_row.booking_mode if alloc_row else "",
                        qty_40hc=alloc_row.qty_40hc if alloc_row else 0,
                        qty_40dc=alloc_row.qty_40dc if alloc_row else 0,
                        qty_20st=alloc_row.qty_20st if alloc_row else 0,
                        total_cbm=alloc_row.total_cbm if alloc_row else 0.0,
                        allocation_status=alloc_row.allocation_status if alloc_row else "AUTO",
                    )
                    rec = module.build_record(sh, alloc, ccfg.air_type)
                    sheet_name = fcfg.sheet
                    if role == "shipping_schedule" and fcfg.sheets_by_site:
                        sheet_name = fcfg.sheets_by_site.get(sh.site or "TGV", fcfg.sheet)
                    by_sheet.setdefault(sheet_name, []).append(rec)

                for sheet_name, records in by_sheet.items():
                    if not records:
                        continue
                    writer = ExcelTargetWriter(
                        path, fcfg.id, sheet_name, spec.columns, spec.key_columns,
                        backup_dir=self.settings.backup_dir, header_row=fcfg.header_row,
                        lock_retry=ecfg.lock_retry_seconds, backup=ecfg.backup_before_write,
                        backup_keep=ecfg.backup_keep_versions,
                        verify_after_write=ecfg.verify_after_write,
                    )
                    expected = None
                    src = s.execute(select(SourceFile).where(SourceFile.code == fcfg.id)).scalar_one_or_none()
                    if src is not None and src.last_version_token and ecfg.conflict_policy == "stop":
                        expected = src.last_version_token

                    report = writer.apply(
                        records, expected_version=expected, dry_run=dry_run,
                        note=f"Bot ghi {spec.display} @ {dt.datetime.now():%Y-%m-%d %H:%M}",
                    )
                    res.write_reports.append(report)
                    self._post_write(s, fcfg, report, records, spec, sheet_name, dry_run, res)

    def _post_write(
        self, s: Session, fcfg, report: WriteReport, records: list[dict[str, Any]],
        spec, sheet_name: str | None, dry_run: bool, res: ScanResult,
    ) -> None:
        if dry_run:
            return

        if report.conflict:
            raise_exception(
                s, "EX007", report.conflict["message"], severity="CRITICAL",
                source_file=report.file_path, source_sheet=sheet_name or "",
                payload=report.conflict,
                suggested_action="Dong file, kiem tra thay doi cua User roi bam Scan lai",
                dedup_key=f"EX007:{fcfg.id}:{report.conflict['current_version']}",
            )
            res.exceptions += 1
            return

        if report.error:
            s.add(PendingWrite(
                target_file_code=fcfg.id, operation=spec.role,
                payload={"records": [_jsonable(r) for r in records], "sheet": sheet_name},
                last_error=report.error,
                next_attempt_at=utcnow() + dt.timedelta(minutes=30),
            ))
            raise_exception(
                s, "EX012", f"Khong ghi duoc {spec.display}: {report.error}",
                source_file=report.file_path, source_sheet=sheet_name or "",
                suggested_action="Bot se thu lai o lan scan sau (Pending Queue)",
                dedup_key=f"EX012:{fcfg.id}:{spec.role}",
            )
            res.exceptions += 1
            return

        src = s.execute(select(SourceFile).where(SourceFile.code == fcfg.id)).scalar_one_or_none()
        if src is not None:
            p = self.settings.resolve_path(fcfg.path)
            if p.exists():
                src.last_version_token = snapshot(p, with_hash=False).version_token
                src.last_written_at = utcnow()

        for op in report.ops:
            if op.kind not in ("INSERT", "UPDATE"):
                continue
            key = op.key
            pr = s.execute(
                select(PlanningRecord).where(
                    PlanningRecord.target_role == spec.role, PlanningRecord.external_key == key
                )
            ).scalar_one_or_none()
            payload = _jsonable(op.values)
            if pr is None:
                s.add(PlanningRecord(
                    target_role=spec.role, target_file_code=fcfg.id, target_sheet=sheet_name or "",
                    target_row=op.row, external_key=key, shipment_code=op.shipment_code,
                    payload=payload, state="WRITTEN", written_at=utcnow(),
                ))
            else:
                pr.target_row, pr.payload, pr.state = op.row, payload, "WRITTEN"
                pr.written_at = utcnow()

        if report.applied:
            log_audit(
                s, f"WRITE_{spec.role.upper()}", entity="file", entity_id=fcfg.id,
                new_value=f"+{report.inserted} ~{report.updated}",
                source=report.file_path, note=f"backup: {Path(report.backup_path).name if report.backup_path else '-'}",
            )

    # -- buoc 8: to mau Follow Up ---------------------------------------

    def _write_colors(
        self, s: Session, shipments: dict[str, Shipment], candidates: list[ShipmentCandidate],
        res: ScanResult, dry_run: bool,
    ) -> None:
        if not self.settings.status.write_color_to_excel:
            return

        by_key = {c.key: c for c in candidates}
        ops_by_file: dict[str, list[ColorOp]] = {}
        for sh in shipments.values():
            cand = by_key.get(sh.shipment_key)
            if cand is None:
                continue
            color = self.settings.status_color(sh.effective_status)
            current = (cand.row.fill_color or "").upper()
            if current and current.lstrip("0") == color.upper().lstrip("0"):
                continue
            ops_by_file.setdefault(sh.source_file_code, []).append(
                ColorOp(sheet=sh.source_sheet, row=sh.source_row, color=color,
                        status=sh.effective_status, shipment_code=sh.shipment_code,
                        previous=current)
            )

        ecfg = self.settings.excel_write
        for file_code, ops in ops_by_file.items():
            fcfg = self.settings.file_by_code(file_code)
            if fcfg is None or not fcfg.write:
                continue
            path = self.settings.resolve_path(fcfg.path)
            writer = FollowUpColorWriter(
                path, file_code, backup_dir=self.settings.backup_dir,
                lock_retry=ecfg.lock_retry_seconds, backup=ecfg.backup_before_write,
                backup_keep=ecfg.backup_keep_versions,
            )
            report = writer.apply(ops, dry_run=dry_run, note="Bot to mau trang thai")
            res.write_reports.append(report)
            res.color_ops += len(ops)
            if report.error and not dry_run:
                raise_exception(
                    s, "EX006", f"Khong to mau duoc {path.name}: {report.error}",
                    source_file=str(path),
                    suggested_action="Dong file Excel de Bot cap nhat o lan scan sau",
                    dedup_key=f"EX006:COLOR:{file_code}",
                )
                res.exceptions += 1

    # -- buoc 9: phan tich bo sung --------------------------------------

    def _analyse_extras(self, candidates: list[ShipmentCandidate], res: ScanResult) -> None:
        res.split_jo = detect_split_jo(candidates)
        res.merge_suggestions = detect_merge_candidates(candidates)

    # ------------------------------------------------------------------
    # Pending queue
    # ------------------------------------------------------------------

    def retry_pending(self) -> int:
        """Thu ghi lai cac ban ghi dang nam trong Pending Queue (SRS muc 41)."""
        done = 0
        ecfg = self.settings.excel_write
        with session_scope() as s:
            cutoff = utcnow() - dt.timedelta(hours=ecfg.pending_queue_max_age_hours)
            for pw in s.execute(select(PendingWrite).where(PendingWrite.state == "PENDING")).scalars():
                if pw.created_at < cutoff:
                    pw.state = "EXPIRED"
                    continue
                module = OUTPUT_REGISTRY.get(pw.operation)
                fcfg = self.settings.file_by_code(pw.target_file_code)
                if module is None or fcfg is None:
                    pw.state = "FAILED"
                    continue
                spec = module.SPEC
                path = self.settings.resolve_path(fcfg.path)
                writer = ExcelTargetWriter(
                    path, fcfg.id, (pw.payload or {}).get("sheet") or fcfg.sheet,
                    spec.columns, spec.key_columns, backup_dir=self.settings.backup_dir,
                    header_row=fcfg.header_row, lock_retry=ecfg.lock_retry_seconds,
                    backup=ecfg.backup_before_write, backup_keep=ecfg.backup_keep_versions,
                )
                report = writer.apply((pw.payload or {}).get("records", []), note="Retry pending queue")
                pw.attempts += 1
                if report.error:
                    pw.last_error = report.error
                    pw.next_attempt_at = utcnow() + dt.timedelta(minutes=30)
                else:
                    pw.state = "DONE"
                    done += 1
                    log_audit(s, "PENDING_FLUSHED", entity="file", entity_id=fcfg.id,
                              new_value=f"+{report.inserted} ~{report.updated}")
        return done


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = v.isoformat() if isinstance(v, dt.date) else v
    return out
