"""AUTO ENGINE - dieu phoi toan bo pipeline (SRS muc 4 & 63).

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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import log_audit, raise_exception
from .core import deadline as dl
from .core import forwarder as fw
from .core import status as st
from .core.buyer_rules import BuyerRegistry
from .core.container import ContainerSpec, merge_specs, parse_container_type, plan_containers
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
    ExceptionRecord,
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
from .followup import FollowUpTemplate
from .logging_setup import get_logger
from .notify.engine import NotificationEngine
from .outputs import REGISTRY as OUTPUT_REGISTRY
from .outputs.base import AllocationLike
from .outputs.shipping_schedule import resolve_site
from .services.assignment import auto_assign
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
    auto_assigned: int = 0
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
    formula_columns: list[dict[str, Any]] = field(default_factory=list)
    container_checks: list[dict[str, Any]] = field(default_factory=list)
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
            "auto_assigned": self.auto_assigned,
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
            "formula_columns": self.formula_columns,
            "container_checks": self.container_checks,
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
                  f"Exception dang mo: {self.exceptions}"]
        if self.errors:
            lines += ["", "Loi:"] + [f"  - {e}" for e in self.errors]
        lines.append("=" * 62)
        return "\n".join(lines)


class AutomationEngine:
    def __init__(self, settings: Settings, notifier: NotificationEngine | None = None):
        self.settings = settings
        self.dictionary = FieldDictionary(settings.field_mapping)
        self.buyers = BuyerRegistry(settings.buyer_rules)
        self.template = FollowUpTemplate(settings.followup_template or {"columns": []},
                                         settings.field_mapping.get("derived_fields"))
        self.forwarder_rules = fw.load_rules((settings.buyer_rules or {}).get("forwarder_rules"))
        self.notifier = notifier or NotificationEngine(settings, resolve_email=self._email_of)

    @staticmethod
    def _email_of(username: str) -> str:
        if not username:
            return ""
        with session_scope() as s:
            u = s.execute(select(User).where(User.name == username)).scalar_one_or_none()
            return u.email if u and u.active else ""

    # ------------------------------------------------------------------
    # Bootstrap: dong bo config -> DB
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        with session_scope() as s:
            for u in self.settings.users:
                row = s.execute(select(User).where(User.name == u.name)).scalar_one_or_none()
                # Chi tao san lan dau. Sau do Admin quan ly vai tro / mat khau tren web.
                if row is None:
                    s.add(User(name=u.name, display_name=u.display_name, email=u.email,
                               role=u.role if u.role in ("admin", "manager", "user", "viewer") else "user",
                               team=u.team))

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

        self._container_checks: list[dict[str, Any]] = []
        with session_scope() as s:
            run = ScanRun(mode="DRY_RUN" if dry_run else "SCAN")
            s.add(run)
            s.flush()

            try:
                candidates = self._scan_follow_ups(s, res)
                shipments = self._persist_shipments(s, candidates, res, today)
                allocations = self._allocate_containers(s, shipments, candidates)
                self._evaluate_status(s, shipments, candidates, res, today)
                events = self._evaluate_deadlines(s, shipments, candidates, res, today)
                self._dispatch(s, events, shipments, res, deliver_notifications)
                self._write_outputs(s, shipments, allocations, res, dry_run, today)
                self._write_colors(s, shipments, candidates, res, dry_run)
                self._analyse_extras(candidates, res)
                res.container_checks = list(self._container_checks)
                run.ok = True
            except Exception as exc:  # noqa: BLE001
                log.exception("Scan that bai")
                res.errors.append(str(exc))
                run.ok = False
                run.error = str(exc)

            res.exceptions = s.execute(
                select(func.count()).select_from(ExceptionRecord).where(ExceptionRecord.status == "OPEN")
            ).scalar_one()
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

    def _scan_follow_ups(
        self, s: Session, res: ScanResult, files: list | None = None
    ) -> list[ShipmentCandidate]:
        rows: list[CanonicalRow] = []

        for fcfg in (files if files is not None else self.settings.files_by_role("follow_up")):
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
                    "nhung Automation khong chac chan.",
                    source_file=str(path), source_sheet=det.sheet_name,
                    suggested_action="Vao Dashboard > Mapping de xac nhan mot lan",
                    payload={"matched": det.matched_fields, "missing": det.missing_required},
                    dedup_key=f"EX005:{fcfg.id}:{det.sheet_name}",
                )
                res.exceptions += 1

            for sheet_res in wb_result.sheets:
                res.sheets_scanned += 1
                res.formula_columns.extend(
                    {"file": fcfg.id, "sheet": sheet_res.detection.sheet_name, "field": fc.field,
                     "column": fc.column, "formula": fc.pattern, "rows": fc.formula_rows}
                    for fc in sheet_res.formula_columns
                )
                for issue in sheet_res.sheet_issues:
                    raise_exception(
                        s, issue["code"], issue["reason"], severity=issue["severity"],
                        source_file=str(path), source_sheet=sheet_res.detection.sheet_name,
                        suggested_action="Mo file bang Excel, bam Save roi cho lan quet sau",
                        dedup_key=f"{issue['code']}:STALE:{fcfg.id}:{sheet_res.detection.sheet_name}",
                    )
                    res.exceptions += 1
                for row in sheet_res.rows:
                    rows.append(row)
                    for issue in row.issues:
                        code = issue.get("code") or {"ExFtyDate": "EX001", "FinalCBM": "EX004"}.get(
                            issue["field"], "EX011")
                        action = (
                            f"Keo lai cong thuc {issue.get('expected', '')} cho o {issue['column']}{row.row_index}"
                            if code == "EX013"
                            else "Sua truc tiep trong file Follow Up roi cho lan scan sau"
                        )
                        raise_exception(
                            s, code, f"{issue['field']}: {issue['reason']}",
                            severity="CRITICAL" if issue["severity"] == "INVALID" else "WARNING",
                            source_file=str(path), source_sheet=row.sheet, source_row=row.row_index,
                            source_column=issue["column"], value=issue["raw"],
                            suggested_action=action,
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
        candidates, duplicates = build_candidates(
            rows, self.buyers.resolve, include_no=self.settings.shipment.key_includes_no
        )
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

    # ------------------------------------------------------------------
    # Nhap file Follow Up tu web (upload) - dung chung bo doc/nhan dien/mapping
    # ------------------------------------------------------------------

    def import_followup(
        self, path: str | Path, *, owner: str, file_label: str = "", dry_run: bool = True,
        today: dt.date | None = None,
    ) -> dict[str, Any]:
        """Xem truoc (dry_run) hoac nhap that 1 file Follow Up.

        Lo hang nhap vao co owner = nguoi phu trach duoc chon -> nhap lai cung file
        se CAP NHAT, khong tao trung. Khong ghi gi vao file Excel.
        """
        from .db.session import new_session
        from .settings import FileCfg

        today = today or dt.date.today()
        code = "UPLOAD:" + (file_label or Path(path).name)[:60]
        fcfg = FileCfg(id=code, role="follow_up", path=str(path), owner=owner, write=False)
        res = ScanResult(dry_run=dry_run)
        s = new_session()
        try:
            cands = self._scan_follow_ups(s, res, files=[fcfg])
            keys = {c.key for c in cands}
            existing = set(s.execute(select(Shipment.shipment_key).where(
                Shipment.shipment_key.in_(keys))).scalars()) if keys else set()
            preview = [
                {"jo": c.jo, "exfty": to_canonical_date(c.exfty), "buyer": c.buyer_rule.code,
                 "no": clean_text(c.row.values.get("ShipmentNo")), "sheet": c.row.sheet,
                 "row": c.row.row_index, "action": "CAP NHAT" if c.key in existing else "THEM MOI"}
                for c in cands
            ]
            out = {"file": code, "rows": len(cands), "new": sum(p["action"] == "THEM MOI" for p in preview),
                   "update": sum(p["action"] == "CAP NHAT" for p in preview), "preview": preview[:500],
                   "sheets": res.sheets_scanned, "errors": list(res.errors), "applied": False}
            if dry_run:
                s.rollback()
                return out
            self._container_checks = []
            shipments = self._persist_shipments(s, cands, res, today)
            self._allocate_containers(s, shipments, cands)
            self._evaluate_status(s, shipments, cands, res, today)
            log_audit(s, "EXCEL_IMPORTED", actor_type="USER", actor_name=owner, entity="file",
                      entity_id=code, new_value=f"{out['new']} moi / {out['update']} cap nhat")
            s.commit()
            out["applied"] = True
            out["auto_assigned"] = res.auto_assigned
            return out
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

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
                # auto_status/effective_status de rong -> lan dau khong tinh la "chuyen trang thai"
                sh = Shipment(shipment_code=code, shipment_key=cand.key,
                              auto_status="", effective_status="", origin="EXCEL",
                              row_data={}, field_src={}, version=1)
                s.add(sh)
                res.shipments_new += 1
                log_audit(s, "SHIPMENT_CREATED", entity="shipment", entity_id=code,
                          new_value=f"{cand.jo} / {to_canonical_date(cand.exfty)}",
                          source=f"{cand.row.file_code}/{cand.row.sheet}!{cand.row.row_index}")
            else:
                sh = existing
                if sh.data_hash and sh.data_hash != cand.row.data_hash:
                    res.shipments_changed += 1

            # USER > AUTOMATION: o nao User/Quan ly da sua tren web thi giu gia tri web
            v = self._merge_web_overrides(s, sh, dict(cand.row.values), cand)

            # ExFtyDate thay doi -> tinh lai deadline (SRS muc 48)
            new_exfty = v.get("ExFtyDate")
            if sh.exfty_date and new_exfty and sh.exfty_date != new_exfty:
                log_audit(s, "EXFTY_CHANGED", entity="shipment", entity_id=sh.shipment_code,
                          field="ExFtyDate", old_value=sh.exfty_date, new_value=new_exfty,
                          note="Tinh lai toan bo deadline T-20/T-7/T-3/T-1")
                sh.previous_exfty = sh.exfty_date
                sh.exfty_changed_at = utcnow()
                self._warn_stale_planning_rows(s, sh, new_exfty)

            sh.group_key = cand.group
            sh.source_file_code = cand.row.file_code
            sh.source_sheet = cand.row.sheet
            sh.source_row = cand.row.row_index
            sh.owner_user = cand.row.owner_user
            if sh.data_hash != cand.row.data_hash:
                sh.version = (sh.version or 0) + 1
            sh.data_hash = cand.row.data_hash
            sh.source_version = cand.row.source_version
            sh.data_confidence = cand.row.confidence
            derived = {**cand.row.derived, **({"merged": cand.row.merged_from} if cand.row.merged_from else {})}
            self.apply_values(sh, v, today, rule=cand.buyer_rule, derived=derived,
                              pic_default=cand.row.owner_user, s=s)

            s.flush()
            self._sync_invoices(s, sh, cand)
            out[sh.shipment_code] = sh

        # Lo hang tao truc tiep tren web (khong co file Excel nguon)
        for sh in s.execute(
            select(Shipment).where(Shipment.origin == "WEB", Shipment.archived.is_(False),
                                   Shipment.is_active.is_(True))
        ).scalars():
            out.setdefault(sh.shipment_code, sh)
        # Lo hang Quan ly da luu tru (archive) -> van cap nhat du lieu nhung khong theo doi nua
        out = {k: v for k, v in out.items() if not v.archived}

        if not res.dry_run:
            assigned = auto_assign(s, self.settings.assignment, list(out.values()))
            res.auto_assigned = len(assigned.changed)
        res.shipments_total = len(out)
        return out

    # ------------------------------------------------------------------
    # Gan gia tri 47 cot -> cac cot nghiep vu (dung chung cho Excel import va web)
    # ------------------------------------------------------------------

    def apply_values(
        self, sh: Shipment, v: dict[str, Any], today: dt.date, *, rule=None,
        derived: dict[str, Any] | None = None, pic_default: str = "", s: Session | None = None,
    ) -> None:
        """v = gia tri co kieu theo truong chuan (ShipmentNo, JO, ExFtyDate, ...)."""
        rule = rule or self.buyers.resolve(clean_text(v.get("Buyer")))
        tmpl = self.template
        # Cot cong thuc: file Excel co gia tri cache thi giu, thieu thi tinh giong Excel
        v.update(tmpl.compute_formulas(v, skip=self._manual_fields(sh), only_missing=True))

        sh.shipment_no = clean_text(v.get("ShipmentNo"))
        sh.jo = clean_text(v.get("JO"))
        sh.exfty_date = v.get("ExFtyDate")
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
        sh.pic = clean_text(v.get("PIC")) or pic_default or sh.assigned_to
        sh.remark = clean_text(v.get("Remark"))
        sh.booking_flag = bool(v.get("BookingFlag")) or bool(sh.cdf) or bool(sh.bl)
        was_hold = sh.hold_flag
        sh.hold_flag = bool(v.get("HoldFlag"))
        sh.hold_reason = clean_text(v.get("HoldReason"))
        sh.cancel_flag = bool(v.get("CancelFlag"))
        sh.shipped_flag = bool(v.get("ShippedFlag"))
        sh.shipped_qty = v.get("ShippedQty")
        sh.go = clean_text(v.get("GO"))
        sh.po = clean_text(v.get("PO"))
        sh.style = clean_text(v.get("Style"))
        sh.booking_no = clean_text(v.get("BookingNo"))
        sh.booking_date = v.get("BookingDate")
        sh.etd = v.get("ETD")
        sh.eta = v.get("ETA")
        sh.vessel = clean_text(v.get("Vessel"))
        sh.port = clean_text(v.get("Port"))
        sh.closing_time = clean_text(v.get("ClosingTime"))
        sh.container_type_user = clean_text(v.get("ContainerType"))
        sh.container_no = clean_text(v.get("ContainerNo"))
        sh.seal_no = clean_text(v.get("SealNo"))
        sh.co_no = clean_text(v.get("CoNo"))
        sh.term = clean_text(v.get("Term"))
        sh.currency = clean_text(v.get("Currency"))
        sh.cdf_amount = v.get("CdfAmount")
        sh.tsn_amount = v.get("TsnAmount")
        if derived is not None:
            sh.derived_notes = derived
        if sh.booking_no:
            sh.booking_flag = True

        # CO: file that co CO NO / CO FORM thuc te -> uu tien hon Buyer Rule
        co_form_file = clean_text(v.get("CoForm"))
        sh.co_required = rule.co_required or bool(sh.co_no or co_form_file)
        sh.co_form = co_form_file or rule.co_form
        sh.site = rule.site or resolve_site(sh, rule.site, self.settings.site_rules)
        sh.is_active = True
        sh.last_seen_at = utcnow()

        if sh.hold_flag and not was_hold:
            sh.hold_since = today
        elif not sh.hold_flag:
            sh.hold_since = None

        # Luu 47 cot (+ cac truong khac cua file) cho luoi web va xuat Excel
        sh.row_data = tmpl.row_to_json(v)
        self._resolve_forwarder(sh)
        if s is not None and sh.forwarder_status == fw.NEED_CONFIRM:
            cands = (sh.derived_notes or {}).get("forwarder_candidates", [])
            raise_exception(
                s, "EX017", f"{sh.jo}: {len(cands)} forwarder cung khop rule ({', '.join(cands)})",
                source_file=sh.source_file_code, source_sheet=sh.source_sheet, source_row=sh.source_row,
                shipment_code=sh.shipment_code, value=", ".join(cands),
                suggested_action="Mo lo hang > Chot forwarder, hoac sua forwarder_rules",
                dedup_key=f"EX017:{sh.shipment_code}",
            )

    @staticmethod
    def _manual_fields(sh: Shipment) -> set[str]:
        return {f for f, m in (sh.field_src or {}).items() if (m or {}).get("src") in ("USER", "MANAGER")}

    def _merge_web_overrides(
        self, s: Session, sh: Shipment, v: dict[str, Any], cand: ShipmentCandidate
    ) -> dict[str, Any]:
        """Excel import gap o da duoc sua tay tren web.

        Thu tu uu tien (SRS v2 muc 71): User/Manager tren web > Excel import.
        Excel doi gia tri khac voi web -> giu web, bao EX015 de Quan ly quyet.
        """
        src = dict(sh.field_src or {})
        if not src:
            return v
        tmpl = self.template
        changed = False
        for fld, meta in src.items():
            if (meta or {}).get("src") not in ("USER", "MANAGER"):
                continue
            web_val = (sh.row_data or {}).get(fld)
            ex_val = tmpl.to_json(fld, v.get(fld))
            if ex_val != meta.get("excel"):
                if ex_val != web_val:
                    raise_exception(
                        s, "EX015",
                        f"{tmpl.by_field[fld].label if fld in tmpl.by_field else fld}: Excel = '{ex_val}', "
                        f"web ({meta.get('by')}) = '{web_val}' -> dang giu gia tri web",
                        source_file=self._src_path(cand.row.file_code), source_sheet=cand.row.sheet,
                        source_row=cand.row.row_index, source_column=fld, value=ex_val,
                        shipment_code=sh.shipment_code,
                        suggested_action="Quan ly chon: giu gia tri web hoac nhan gia tri Excel",
                        payload={"field": fld, "excel": ex_val, "web": web_val},
                        dedup_key=f"EX015:{sh.shipment_code}:{fld}:{ex_val}",
                    )
                meta = {**meta, "excel": ex_val}
                src[fld] = meta
                changed = True
            v[fld] = tmpl.from_json(fld, web_val)
        if changed:
            sh.field_src = src
        return v

    def _resolve_forwarder(self, sh: Shipment) -> None:
        if sh.forwarder_status in (fw.MANUAL, fw.CONFLICT) or not self.forwarder_rules:
            return
        m = fw.resolve_forwarder(self.forwarder_rules, buyer=sh.buyer_code or sh.buyer_raw,
                                 mode=sh.ship_mode, dest=sh.destination, port=sh.port, exfty=sh.exfty_date)
        sh.forwarder_status = m.status
        sh.forwarder = m.forwarder
        if m.status == fw.NEED_CONFIRM:
            sh.derived_notes = {**(sh.derived_notes or {}), "forwarder_candidates": m.candidates}

    def recompute(self, s: Session, sh: Shipment, today: dt.date | None = None) -> ScanResult:
        """Tinh lai trang thai ngay sau khi sua tren web (khong can cho lan quet ke tiep)."""
        today = today or dt.date.today()
        res = ScanResult(dry_run=False)
        self._container_checks = []
        group = list(s.execute(
            select(Shipment).where(Shipment.group_key == sh.group_key, Shipment.archived.is_(False))
        ).scalars()) if sh.group_key else [sh]
        if sh not in group:
            group.append(sh)
        ships = {x.shipment_code: x for x in group}
        self._evaluate_status(s, ships, [], res, today)
        return res

    def _warn_stale_planning_rows(self, s: Session, sh: Shipment, new_exfty: dt.date | None) -> None:
        """Doi ExFtyDate sau khi da ghi Planning -> dong cu con o lai file dich.

        Automation KHONG tu xoa dong trong file dung chung (co the co ghi chu cua nguoi khac).
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
                suggested_action="Xoa hoac sua dong cu trong file dich; Automation se them dong moi theo ngay moi",
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
        old_no, new_no = norm_key(sh.shipment_no), norm_key(row.values.get("ShipmentNo"))
        if old_no and new_no and old_no != new_no:
            return None  # NO doi han sang so khac -> khong phai cung lo
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
            auto_label = plan.container_type

            alloc = s.execute(
                select(ContainerAllocation).where(ContainerAllocation.group_key == gkey)
            ).scalar_one_or_none()
            if alloc is None:
                alloc = ContainerAllocation(group_key=gkey)
                s.add(alloc)

            if alloc.allocation_source == "USER":
                # USER > AUTO: khong ghi de phuong an User da chot tren Dashboard (SRS muc 35)
                alloc.total_cbm = plan.total_cbm
                result[gkey] = alloc
                continue

            # CONT TYPE User da go trong Excel (o merge tinh 1 lan cho ca nhom)
            user_spec = self._user_container_spec(cands) if cands else self._web_container_spec(ships)
            if user_spec is not None:
                self._apply_user_container(s, alloc, plan, user_spec, ships, gkey, auto_label)
            else:
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
                if plan.allocation_status == "NEED_REVIEW":
                    raise_exception(
                        s, "EX009", plan.note or "Automation khong tu quyet dinh duoc container",
                        source_file=self._src_path(ships[0].source_file_code), source_sheet=ships[0].source_sheet,
                        source_row=ships[0].source_row, value=f"CBM={plan.total_cbm}",
                        suggested_action="Dashboard > Shipments > chon container thu cong",
                        shipment_code=ships[0].shipment_code, dedup_key=f"EX009:{gkey}",
                    )

            alloc.total_cbm = plan.total_cbm
            alloc.shipment_id = ships[0].id
            for sh in ships:
                sh.booking_mode = alloc.booking_mode
            result[gkey] = alloc

        s.flush()
        return result

    def _src_path(self, file_code: str) -> str:
        fcfg = self.settings.file_by_code(file_code)
        return str(self.settings.resolve_path(fcfg.path)) if fcfg else file_code

    @staticmethod
    def _user_container_spec(cands: list[ShipmentCandidate]) -> ContainerSpec | None:
        """Gom cac o CONT TYPE cua nhom. O merge (D2:D5) chi tinh 1 lan."""
        seen: dict[str, str] = {}
        for c in cands:
            raw = clean_text(c.row.values.get("ContainerType"))
            if raw:
                seen.setdefault(c.row.origin("ContainerType"), raw)
        if not seen:
            return None
        return merge_specs([parse_container_type(v) for v in seen.values()])

    @staticmethod
    def _web_container_spec(ships: list[Shipment]) -> ContainerSpec | None:
        """Lo hang tao tren web: CONT TYPE cung gia tri tren nhieu dong = 1 o merge."""
        seen = {x.container_type_user for x in ships if x.container_type_user}
        if not seen:
            return None
        return merge_specs([parse_container_type(v) for v in sorted(seen)])

    def _apply_user_container(
        self, s: Session, alloc: ContainerAllocation, plan, spec: ContainerSpec,
        ships: list[Shipment], gkey: str, auto_label: str,
    ) -> None:
        ccfg = self.settings.container
        first = ships[0]
        alloc.allocation_source = "USER_EXCEL"
        if not spec.valid:
            alloc.allocation_status = "NEED_REVIEW"
            alloc.container_type = spec.raw
            alloc.note = f"Khong doc duoc CONT TYPE '{spec.raw}': {spec.reason}. Automation de xuat: {auto_label}"
            raise_exception(
                s, "EX014", alloc.note, source_file=self._src_path(first.source_file_code),
                source_sheet=first.source_sheet, source_row=first.source_row, source_column="CONT TYPE",
                value=spec.raw, shipment_code=first.shipment_code,
                suggested_action="Ghi theo dang 1x20 / 1x40 / 2x40HC / LCL",
                dedup_key=f"EX014:PARSE:{gkey}",
            )
            return

        alloc.qty_40hc = spec.quantities.get("40HC", 0)
        alloc.qty_40dc = spec.quantities.get("40DC", 0)
        alloc.qty_20st = spec.quantities.get("20ST", 0)
        alloc.booking_mode = spec.mode or plan.booking_mode
        alloc.container_type = spec.label
        cap = spec.capacity(ccfg.capacity)
        alloc.estimated_capacity = round(cap, 3)
        alloc.excess_capacity = round(cap - plan.total_cbm, 3)
        check = {
            "group": gkey, "no": first.shipment_no, "jos": [x.jo for x in ships],
            "user": spec.label, "automation": auto_label, "cbm": plan.total_cbm, "capacity": cap,
        }

        if spec.mode == "FCL" and cap + 1e-9 < plan.total_cbm:
            alloc.allocation_status = "NEED_REVIEW"
            alloc.note = (f"CONT TYPE {spec.label} chi chua {cap:g} CBM, nhom co {plan.total_cbm:g} CBM "
                          f"-> THIEU {plan.total_cbm - cap:g} CBM. Automation de xuat: {auto_label}")
            check["result"] = "THIEU CHO"
            raise_exception(
                s, "EX014", alloc.note, severity="CRITICAL",
                source_file=self._src_path(first.source_file_code),
                source_sheet=first.source_sheet, source_row=first.source_row, source_column="CONT TYPE",
                value=spec.raw, shipment_code=first.shipment_code,
                suggested_action="Book them cont hoac tach lo", dedup_key=f"EX014:SHORT:{gkey}",
            )
        else:
            alloc.allocation_status = "USER_CONFIRMED"
            same = spec.label == auto_label
            alloc.note = (f"User book {spec.label} (du {alloc.excess_capacity:g} CBM)"
                          + ("" if same else f" | Automation de xuat {auto_label}"))
            check["result"] = "KHOP" if same else "DU CHO"
        self._container_checks.append(check)

    # -- buoc 4: status -------------------------------------------------

    def _evaluate_status(
        self, s: Session, shipments: dict[str, Shipment], candidates: list[ShipmentCandidate],
        res: ScanResult, today: dt.date,
    ) -> None:
        blanks = self.dictionary.blanks or None
        overrides = self._load_overrides(s)
        rule = self.settings.status.completed_rule
        completion = rule.model_dump()
        by_key = {c.key: c for c in candidates}

        # 1. Trang thai Automation tung dong
        results: dict[str, st.StatusResult] = {}
        for sh in shipments.values():
            cand = by_key.get(sh.shipment_key)
            has = cand.row.values if cand else (sh.row_data or {})
            values = {
                "HoldFlag": sh.hold_flag, "HoldReason": sh.hold_reason,
                "HoldReasonDetail": sh.hold_reason_detail, "CancelFlag": sh.cancel_flag,
                "ShippedFlag": sh.shipped_flag, "ShippedQty": sh.shipped_qty,
                "FinalExportQty": sh.final_export_qty, "BookingFlag": sh.booking_flag,
                "BookingNo": sh.booking_no, "CDF": sh.cdf, "BL": sh.bl,
                "_booking_mode": sh.booking_mode,
            }
            # Chi xet "da xuat xong" khi file co cot tuong ung (CONT/SEAL/ETD)
            for fld, attr in (("ContainerNo", "container_no"), ("SealNo", "seal_no"), ("ETD", "etd")):
                if fld in has:
                    values[fld] = getattr(sh, attr)
            use_completion = rule.enabled and ("ETD" in has or "ContainerNo" in has)
            results[sh.shipment_code] = st.evaluate(
                values, blanks=blanks, today=today,
                completion=completion if use_completion else {"enabled": False},
            )

        # 2. Nhom cung NO dong cont do dang -> PARTIAL ("xuat toi nay 1 nua, mai dong tiep")
        if rule.group_partial:
            self._apply_group_partial(shipments, results)

        # 3. User override (Dashboard + mau User to tay trong Excel) -> trang thai hien thi
        statuses = []
        for sh in shipments.values():
            result = results[sh.shipment_code]
            old_effective = sh.effective_status
            if sh.auto_status and sh.auto_status != result.status:
                log_audit(s, "STATUS_CHANGED", entity="shipment", entity_id=sh.shipment_code,
                          field="auto_status", old_value=sh.auto_status, new_value=result.status,
                          note=result.reason)
            sh.auto_status = result.status
            sh.status_reason = result.as_dict()

            cand = by_key.get(sh.shipment_key)
            if cand is not None and self.settings.status.read_user_colors:
                self._sync_color_override(s, sh, cand, result.status, overrides)

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

    @staticmethod
    def _apply_group_partial(shipments: dict[str, Shipment], results: dict[str, st.StatusResult]) -> None:
        groups: dict[str, list[Shipment]] = {}
        for sh in shipments.values():
            if (sh.booking_mode or "").upper() in ("AIR", "LCL", "EXPRESS"):
                continue
            groups.setdefault(sh.group_key, []).append(sh)
        for ships in groups.values():
            if len(ships) < 2:
                continue
            loaded = [x for x in ships if x.container_no and x.seal_no]
            if not 0 < len(loaded) < len(ships):
                continue
            no = ships[0].shipment_no or ships[0].booking_no or "?"
            reason = f"NO {no}: moi dong cont xong {len(loaded)}/{len(ships)} dong - con lai dong tiep"
            for x in ships:
                r = results[x.shipment_code]
                if r.status in ("GREEN", "YELLOW", "COMPLETED"):
                    r.trace.append({"check": "NHOM CONT", "value": f"{len(loaded)}/{len(ships)}", "note": reason})
                    r.status, r.reason = "PARTIAL", reason

    def _sync_color_override(
        self, s: Session, sh: Shipment, cand: ShipmentCandidate, auto_status: str,
        overrides: dict[str, dict[str, str]],
    ) -> None:
        """Doc mau User to tay trong Excel -> USER override (USER > AUTO).

        F = mau hien tai, B = mau Automation to lan cuoi, U = mau User da to (dang co hieu luc)
          F == U            -> giu nguyen override
          F rong / F == B   -> User khong can thiep (neu truoc do co U thi bo override)
          F moi, la mau cua trang thai Automation dang tinh -> User dong y voi Automation -> bo override
          F moi, la mau trang thai khac -> override = trang thai do
          F moi, mau la     -> dong thuoc ve User, Automation khong to de (khong doi trang thai)
        """
        F = _rgb(cand.row.fill_color)
        B, U = _rgb(sh.last_auto_color), _rgb(sh.user_color)
        if U and F == U:
            return
        if not F or F == B:
            if U:
                self._set_color_override(s, sh, "", overrides, note="User bo mau tay")
                sh.user_color = ""
            return

        status = self.settings.status.status_of_color(F)
        if status == auto_status or not status:
            self._set_color_override(s, sh, "", overrides, note="User to dung mau cua Automation")
            sh.user_color = "" if status else F
            if not status:
                log_audit(s, "USER_COLOR_CUSTOM", actor_type="USER", actor_name=f"excel:{sh.owner_user}",
                          entity="shipment", entity_id=sh.shipment_code, new_value=F,
                          note="Mau khong thuoc bang trang thai - Automation khong to de dong nay")
            return

        sh.user_color = F
        self._set_color_override(s, sh, status, overrides, note=f"User to mau {F} trong Excel")

    def _set_color_override(
        self, s: Session, sh: Shipment, status: str, overrides: dict[str, dict[str, str]], note: str
    ) -> None:
        current = s.execute(
            select(UserOverride).where(
                UserOverride.shipment_code == sh.shipment_code, UserOverride.field == "status",
                UserOverride.active.is_(True), UserOverride.user_name.like("excel:%"),
            )
        ).scalars().all()
        if status and any(o.user_value == status for o in current):
            return
        for o in current:
            o.active = False
            overrides.get(sh.shipment_code, {}).pop("status", None)
        if not status and not current:
            return
        if status:
            s.add(UserOverride(shipment_code=sh.shipment_code, field="status", user_value=status,
                               user_name=f"excel:{sh.owner_user}", note=note))
            overrides.setdefault(sh.shipment_code, {})["status"] = status
        log_audit(s, "USER_COLOR_OVERRIDE", actor_type="USER", actor_name=f"excel:{sh.owner_user}",
                  entity="shipment", entity_id=sh.shipment_code, field="status",
                  old_value=",".join(o.user_value for o in current), new_value=status or "(bo)", note=note)

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
            elif sh.row_data:
                t3 = dl.check_t3_fields(self.template.row_from_json(sh.row_data), {})

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
                "recipient": sh.assigned_to or "",
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
                    if sh.exfty_date and sh.exfty_date < today and not self._has_planning(s, role, sh):
                        continue  # lo da xuat roi: khong chen dong lich su, chi cap nhat dong da co
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
                        note=f"Automation ghi {spec.display} @ {dt.datetime.now():%Y-%m-%d %H:%M}",
                    )
                    res.write_reports.append(report)
                    self._post_write(s, fcfg, report, records, spec, sheet_name, dry_run, res)

    @staticmethod
    def _has_planning(s: Session, role: str, sh: Shipment) -> bool:
        return s.execute(
            select(PlanningRecord.id).where(
                PlanningRecord.target_role == role, PlanningRecord.shipment_code == sh.shipment_code
            ).limit(1)
        ).first() is not None

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
                suggested_action="Automation se thu lai o lan scan sau (Pending Queue)",
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
        """To mau dong theo trang thai - LOSSLESS (xem excel/xlsx_patch.py)."""
        if not self.settings.status.write_color_to_excel:
            return

        by_key = {c.key: c for c in candidates}
        ops_by_file: dict[str, list[tuple[ColorOp, Shipment]]] = {}
        for sh in shipments.values():
            cand = by_key.get(sh.shipment_key)
            if cand is None:
                continue
            current = _rgb(cand.row.fill_color)
            if sh.user_color and current == _rgb(sh.user_color):
                continue  # dong nay User dang to tay -> USER > AUTO, khong to de
            color = _rgb(self.settings.status_color(sh.effective_status))
            if current == color:
                if not dry_run:
                    sh.last_auto_color = color
                continue
            ops_by_file.setdefault(sh.source_file_code, []).append((
                ColorOp(sheet=sh.source_sheet, row=sh.source_row, color=color,
                        status=sh.effective_status, shipment_code=sh.shipment_code,
                        previous=current, last_col=cand.row.last_col),
                sh,
            ))

        ecfg = self.settings.excel_write
        for file_code, pairs in ops_by_file.items():
            fcfg = self.settings.file_by_code(file_code)
            if fcfg is None or not fcfg.write:
                continue
            ops = [op for op, _ in pairs]
            path = self.settings.resolve_path(fcfg.path)
            writer = FollowUpColorWriter(
                path, file_code, backup_dir=self.settings.backup_dir,
                lock_retry=ecfg.lock_retry_seconds, backup=ecfg.backup_before_write,
                backup_keep=ecfg.backup_keep_versions, skip_columns=fcfg.color_skip_columns,
            )
            report = writer.apply(ops, dry_run=dry_run, note="Automation to mau trang thai")
            res.write_reports.append(report)
            res.color_ops += len(ops)
            if report.error and not dry_run:
                raise_exception(
                    s, "EX006", f"Khong to mau duoc {path.name}: {report.error}",
                    source_file=str(path),
                    suggested_action="Dong file Excel de Automation cap nhat o lan scan sau",
                    dedup_key=f"EX006:COLOR:{file_code}",
                )
                res.exceptions += 1
                continue
            if not dry_run:
                for op, sh in pairs:
                    sh.last_auto_color = op.color
                src = s.execute(select(SourceFile).where(SourceFile.code == file_code)).scalar_one_or_none()
                if src is not None and path.exists():
                    src.last_version_token = snapshot(path, with_hash=False).version_token
                if report.changed_parts:
                    log_audit(s, "WRITE_COLORS", entity="file", entity_id=file_code,
                              new_value=f"{report.applied} dong",
                              note="lossless: chi doi " + ", ".join(report.changed_parts))

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


def _rgb(value: str | None) -> str:
    v = (value or "").strip().upper()
    if len(v) == 6:
        v = "FF" + v
    return v if len(v) == 8 and v not in ("00000000",) else ""


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = v.isoformat() if isinstance(v, dt.date) else v
    return out
