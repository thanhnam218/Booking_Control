# Bảng đối chiếu SRS ↔ Code ↔ Test

Requirements Traceability Matrix — mỗi mục SRS được cài đặt ở đâu và test nào chứng minh nó hoạt động.

---

## 1. Kiến trúc & môi trường

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 3 | Chạy ngầm Windows, database, notification | `scheduler.py` (Automation Engine) · `db/` · `notify/` · `scripts/install_autostart.ps1` | `test_job_run_records_retries` |
| 4 | Database là lớp trung gian, không đọc–ghi Excel trực tiếp | `engine.py` · `db/models.py` | `test_engine_e2e.py` |
| 61 | Stack phù hợp Windows | Python 3.11 + SQLite + FastAPI | CI (Windows + Ubuntu) |
| 62 | Không dùng VBA làm core | — | — |

## 2. Excel Scanner

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 5 | Cấu hình đường dẫn, không phụ thuộc tên file | `settings.FileCfg` | `test_engine_e2e::test_scan_reads_all_follow_up_sheets` |
| 6 | Nhận diện sheet bằng header, không bằng tên | `excel/detector.py` | `test_mapping_detection::test_detect_follow_up_sheet` |
| 6.1 | Confidence Score | `detector.detect_sheet` | `test_sample_workbooks_detected` |
| 7 | User xác nhận sheet lần đầu, lưu lại | `sheet_maps.confirmed_by_user` · `/mapping` | `test_ask_user_for_ambiguous_sheet` |
| 8 | Header Mapping, không hard-code cột | `excel/mapping.py` · `field_mapping.yaml` | `test_alias_variants_map_to_same_field` |
| 34 (DMS) | Mapping Confidence, không map mơ hồ | `FieldDictionary.match_header` | `test_unknown_header_is_not_force_mapped` |
| 35 (DMS) | Mapping lưu trong config, không hard-code | `config/field_mapping.yaml` | `test_user_override_beats_dictionary` |
| 36 (DMS) | Header đổi tên → cảnh báo, không tự ghi | `EX010` + `/mapping` | `test_header_renamed_later_still_matches` |

## 3. Chuẩn hóa dữ liệu

| SRS/DMS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 9 | Canonical fields + metadata nguồn | `excel/reader.CanonicalRow` | `test_engine_e2e` |
| 6 (DMS) | Kiểu dữ liệu & validation từng trường | `normalize.parse_by_type` | `test_normalize.py` (11 test) |
| 7 (DMS) | Date Mapping → `YYYY-MM-DD` | `normalize.parse_date` | `test_parse_date_formats` |
| 56 | Data Confidence VALID / NEED_REVIEW / INVALID | `ParseResult.state` | `test_parse_decimal_uncertain_flags_review` |
| 20–21 (DMS) | CDF/BL "đã điền" là thế nào | `normalize.is_blank` + `blank_equivalents` | `test_status::test_blank_equivalents_are_not_filled` |

## 4. Shipment Identification

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 10 | ShipmentID, **không** dùng JO làm khóa | `core/shipment.shipment_key` | `test_jo_is_not_the_primary_key` |
| 11 | Quan hệ User → File → Sheet → Shipment → JO → INV → Tờ khai | `db/models.py` | `test_engine_e2e` |
| 12 | 3 trường hợp NO + INV + tờ khai | `engine._sync_invoices` + `declaration_rule` | `test_engine_e2e` |
| 13 | Duplicate JO không mặc định là lỗi | `shipment.build_candidates` | `test_split_jo_two_dates_are_two_shipments` |
| 13 | Trùng hoàn toàn → Exception | `EX003` | `test_true_duplicate_is_flagged_critical` |

## 5. Buyer & Container

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 14, 65, 67 | Bảng Buyer Rule, CO Yes/No, CO Form | `core/buyer_rules.py` · `buyer_rules.yaml` | `test_buyer_alias_resolves` |
| 15 | Capacity 23/53/63, capacity ≥ CBM | `core/container.py` | `test_capacity_never_short` (14 mốc) |
| 16 | Ưu tiên 40HC → 40DC → 20ST | `allocate_priority` | `test_srs_examples` |
| 17 | 10/30/60/160 CBM | `allocate_priority` | `test_srs_examples` (4 case) |
| 18 | Lưu đủ trường allocation | `ContainerAllocation` | `test_engine_e2e::test_160_cbm_allocation` |
| 19 | AUTO / USER_CONFIRMED / NEED_REVIEW | `ContainerPlan.allocation_status` | `test_missing_cbm_needs_review` |
| 20 | Tách Ship Mode / Booking Mode / Type | `plan_containers` | `test_lcl_suggestion_under_threshold` |
| 21 | AIR → Type = AC | `plan_containers` | `test_air_becomes_ac` |
| 66 | Các mốc CBM biên | `allocate_priority` | `test_boundaries_around_thresholds` |

## 6. Status Engine

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 27 | 4 trạng thái chính + COMPLETED | `core/status.ALL_STATUSES` | `test_status.py` |
| 28 | WHITE | `evaluate` | `test_white_when_not_booked` |
| 29 | YELLOW | `evaluate` | `test_yellow_missing_bl` · `test_yellow_missing_cdf` |
| 30, 68 | GREEN khi đủ CDF + BL (~80%) | `evaluate` | `test_green_when_cdf_and_bl` |
| 31 | RED ưu tiên cao nhất | `evaluate` | `test_red_has_highest_priority` |
| 32 | Hold Reason có danh mục | `buyer_rules.yaml → hold_reasons` | `test_engine_e2e::test_red_wins_over_green` |
| 33 | Thứ tự ưu tiên | `evaluate` | `test_hold_beats_cancel_and_shipped` |
| 52 | Explainable — "Why Yellow?" | `StatusResult.trace` · `/shipments/{id}` | `test_explain_trace_is_readable` |

## 7. User Override & Audit

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 34 | Tách Automation Status và User Status | `auto_status` / `effective_status` | `test_user_override_wins` |
| 35, 69 | USER > AUTO, Automation không ghi đè | `engine._load_overrides` | `test_engine_e2e::test_user_override_survives_next_scan` |
| 35 | User chốt container, Automation giữ nguyên | `_allocate_containers` | `test_user_container_choice_survives` |
| 36 | Audit Log đầy đủ | `audit.log_audit` · bảng `audit_logs` | `test_audit_log_records_bot_writes` |
| 58 | Phân quyền User / Automation | `users.role` | thủ công |
| 59 | Bổ sung Admin | `users.role = admin` | thủ công |

## 8. An toàn dữ liệu Excel

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 23 | Không INSERT trùng, có UPSERT | `ExcelTargetWriter.plan` | `test_insert_then_no_duplicate_on_rerun` |
| 37 | User sửa Excel → Automation không ghi đè | `protect_user_edits` | `test_bot_never_erases_user_value_with_blank` |
| 38 | Version Check trước khi ghi | `excel/backup.snapshot` | `test_conflict_detection_stops_write` |
| 39 | Cảnh báo Conflict | `EX007` + thông báo web | `test_conflict_detection_stops_write` |
| 40, 70 | File lock → retry 30/60/120s | `excel/locking.py` | `test_locked_file_is_detected_and_not_forced` |
| 41 | Pending Queue, không mất dữ liệu | `PendingWrite` · `retry_pending` | `test_locked_file_is_detected_and_not_forced` |
| 53 | Dry Run | `run_scan(dry_run=True)` | `test_dry_run_writes_nothing` · `test_dry_run_does_not_touch_files` |
| 54 | Backup & version trước khi ghi | `excel/backup.make_backup` | `test_backup_created_before_write` |
| 54 | Rollback | `excel/backup.rollback` · `/backups` | `test_rollback_restores_previous_content` |

## 9. Output Mapping

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 22 | Weekly Planning tại T-7 | `outputs/weekly_planning.py` | `test_weekly_planning_written_at_t7` |
| 24 | Custom Code tại T-7 | `outputs/custom_code.py` | `test_custom_code_written` |
| 25 | MM-Daily Planning | `outputs/mm_daily.py` | `test_engine_e2e` |
| 26 | Lịch xuất T-1, TDV/TGV | `outputs/shipping_schedule.py` | `test_shipping_schedule_written_at_t1_only` |

## 10. Deadline & Notification

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 42 | Quét 3h/lần, chỉnh 1–4h | `scheduler.py` · validator | `settings.SchedulerCfg` |
| 43 | Khung giờ 08:00–16:00, chọn ngày | `notify/engine.in_window` | `test_deadline.py` |
| 44 | Cửa sổ booking T-20…T-14 | `deadline.evaluate_shipment` | `test_booking_window_warns` (4 mốc) |
| 45 | Hold reminder 3 ngày, chỉnh 1–7 | `deadline.evaluate_shipment` | `test_hold_reminder_respects_interval` |
| 46 | Workflow T-20/T-7/T-3/T-1 | `deadline.due_milestones` | `test_t7_t3_t1_fire_in_order` |
| 47 | T-3: FOUND / MISSING / INVALID | `deadline.check_t3_fields` | `test_t3_field_check_states` |
| 48 | Đổi ExFtyDate → tính lại deadline | `engine._persist_shipments` | `test_exfty_change_is_audited_and_recalculated` |
| 49 | Phân loại Critical / Warning / Info | `notify/engine.dispatch_events` | `test_notifications_cover_all_deadline_types` |
| 50 | Thông báo có Context | `notify/channels.NotificationPayload.context_lines` · chuông web · email | thủ công |
| 51 | Nút Open Source → đúng sheet, đúng dòng | `notify/channels.open_file` | thủ công |
| 44 | Chống spam thông báo | `dedup_key` + `dedup_hours` | `test_notification_dedup` |

## 11. Control & Exception

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 55 | Exception Center | `audit.raise_exception` · `/exceptions` | `test_duplicate_and_invalid_data_go_to_exception_center` |
| 57 | Schema database | `db/models.py` (17 bảng) | — |
| 60 | Control Dashboard + bộ lọc | `web/app.py` · `templates/` | smoke test |

---

## 12. Tiêu chí nghiệm thu (SRS mục 73)

| Tiêu chí | Test chứng minh | Kết quả |
|---|---|---|
| Không ghi đè User | `test_bot_never_erases_user_value_with_blank` · `test_conflict_detection_stops_write` | ✅ |
| Không tạo duplicate khi chạy nhiều lần | `test_insert_then_no_duplicate_on_rerun` · `test_rerun_is_idempotent` | ✅ |
| Không mất dữ liệu khi file lock | `test_locked_file_is_detected_and_not_forced` | ✅ |
| Nhận diện được sheet Follow Up | `test_sample_workbooks_detected` | ✅ |
| Mapping được các header khác nhau | `test_alias_variants_map_to_same_field` | ✅ |
| WHITE đúng khi chưa booking | `test_white_when_not_booked` | ✅ |
| YELLOW đúng khi booking chưa đủ | `test_yellow_missing_bl` · `test_yellow_missing_cdf` | ✅ |
| GREEN đúng khi CDF + BL có | `test_green_when_cdf_and_bl` | ✅ |
| RED luôn ưu tiên khi Hold | `test_red_has_highest_priority` · `test_red_wins_over_green` | ✅ |
| Container không bao giờ thiếu capacity | `test_capacity_never_short` | ✅ |
| Cho phép dư capacity | `test_excess_is_allowed_but_bounded` | ✅ |
| **160 CBM → 2×40HC + 1×40DC** | `test_srs_examples` · `test_160_cbm_allocation` | ✅ |
| T-20~T-14 booking reminder | `test_booking_window_warns` | ✅ |
| T-7 Weekly Planning | `test_weekly_planning_written_at_t7` | ✅ |
| T-7 Custom Code | `test_custom_code_written` | ✅ |
| T-3 Final Information | `test_t3_field_check_states` | ✅ |
| T-1 TDV/TGV | `test_shipping_schedule_written_at_t1_only` | ✅ |
| Hold reminder 3 ngày, chỉnh 1–7 | `test_hold_reminder_respects_interval` | ✅ |
| User override thắng Automation | `test_user_override_survives_next_scan` | ✅ |
| Có Audit Log | `test_audit_log_records_bot_writes` | ✅ |
| Có Conflict Alert | `test_conflict_detection_stops_write` | ✅ |

---

## 13. Yêu cầu bổ sung ngoài SRS gốc

Những tình huống nghiệp vụ được nêu trong phần mô tả ban đầu:

| Yêu cầu | Cài đặt | Test |
|---|---|---|
| "JO tách thành 2 nhưng vẫn xuất chung 1 ngày" | `ShipmentKey` gồm ExFtyDate | `test_split_jo_creates_two_shipments` |
| "Đã book xuất rồi nhưng lại hold" | RED giữ nguyên thông tin booking | `test_red_wins_over_green` |
| "Đã book rồi lại cancel, đợi lô sau gộp chung" | `CANCELLED` + `detect_merge_candidates` | `test_merge_suggestion_for_small_nearby_lots` |
| "Xuất tối nay một nửa, mai đóng tiếp" | `PARTIAL` / `COMPLETED` theo `ShippedQty` | `test_partial_and_completed` |
| "Cập nhật trạng thái đã book từ mail Outlook" | `integrations/outlook.py` | cần Outlook desktop |
| "Các màu còn lại gán thông báo theo ý từng người" | `status.custom` trong `config.yaml` | — |
| "Đổi Themes Color theo dòng" | `FollowUpColorWriter` | `test_follow_up_rows_get_status_color` |

---

## 14. Phần cần hoàn thiện với dữ liệu thật

SRS mục 64 nêu rõ những điểm chỉ chốt được sau khi xem 5 file Excel thật:

| Cần xác nhận | Hiện tại | Sửa ở đâu |
|---|---|---|
| Header chính xác của Weekly Planning | Bộ tên theo SRS | `outputs/weekly_planning.py → SPEC.columns` |
| Header + cách tìm JO của Custom Code | Khóa = JO | `outputs/custom_code.py` |
| Mapping MM-Daily Planning | Bộ tên hợp lý, đánh dấu cần xác nhận | `outputs/mm_daily.py` |
| Cách phân biệt TDV/TGV trong file thật | 2 sheet riêng + `site_rules` | `outputs/shipping_schedule.py` · `config.yaml` |
| Merged cells / hidden rows / công thức trong Follow Up | Đọc được; merged cell lấy giá trị ô trên-trái | `excel/reader.py` |
| Vị trí CDF/BL và cách ghi "đã có" | Khác rỗng và khác blank-equivalent | `field_mapping.yaml → blank_equivalents` |
| Buyer Rule thật (CO, Form, grouping) | 5 buyer mẫu | `config/buyer_rules.yaml` |

Quy trình chuyển từ demo sang thật: [DATA_MAPPING.md § 11](DATA_MAPPING.md#11-điều-chỉnh-theo-file-thật).

---

## 15. Bổ sung sau khi đối chiếu file Follow Up thật

SRS mục 64 yêu cầu chốt lại Follow Up khi có file thật. Những điểm đã chốt và kiểm chứng:

| Phát hiện trên file thật | Cài đặt | Test (`test_real_structure.py`) |
|---|---|---|
| 47 cột, header có xuống dòng (`EX-FACT\nDATE`) | `field_mapping.yaml` — 53 field | `test_all_real_headers_map_with_full_confidence` |
| `GO`, `CDF AMOUNT`, `Booking date`, `CO NO`, `CONT` dễ map nhầm | alias tường minh cho từng cột | `test_no_dangerous_mis_mapping` (6 case) |
| NO là công thức từ INVOICE | `derived_fields` + khóa không chứa NO | `test_derive_matches_excel_formula` · `test_key_survives_invoice_arriving_later` |
| Công thức có thể bị gõ đè / thiếu ở dòng mới | `reader._check_formulas` → `EX013` | `test_overwritten_formula_is_flagged` · `test_missing_formula_flagged_and_no_derived_from_invoice` |
| File lưu bởi công cụ khác mất giá trị công thức | phát hiện cấp sheet + tự tính NO | `test_stale_formula_cache_detected_and_no_rebuilt` |
| CONT TYPE merge `D2:D5` cho cả nhóm | `reader._merged_map` + `origin()` | `test_merged_cont_type_is_shared_by_group` · `test_split_jo_same_invoice_is_one_container_group` |
| CONT TYPE là lựa chọn của User | `parse_container_type` + kiểm đủ tải → `EX014` | `test_parse_user_container_type` · `test_user_container_shortage_is_critical` |
| MODE ghi `S` / `A` | `parse_ship_mode` trong reader | `test_ship_mode_letters_are_normalized` |
| Không có cột HOLD / CANCEL | `remark_keywords` | `test_hold_from_remark` · `test_remark_keyword_respects_negation` |
| CO FORM thật có trong file | ưu tiên hơn Buyer Rule | `test_co_form_taken_from_file` |
| Có CONT + SEAL + ETD → biết khi nào xuất xong | `status._check_completed` | `test_completed_needs_cont_seal_and_etd` |
| "Xuất 1 nửa, mai đóng tiếp" | `engine._apply_group_partial` | `test_group_partial_when_half_loaded` |
| Trạng thái đang được đánh dấu bằng màu tay | `engine._sync_color_override` | `test_user_paints_red_then_back` · `test_unknown_user_color_is_left_alone` |
| **openpyxl làm mất giá trị công thức, defined name, thiết lập in** | `excel/xlsx_patch.py` ghi lossless | `test_color_write_is_lossless` · `test_color_write_twice_is_noop` · `test_styles_do_not_grow_on_repeated_runs` |
| Không được ghi đè công thức ở file đích | `set_value` trả `False`, kéo công thức cho dòng mới | `test_formula_cell_never_overwritten` · `test_target_insert_extends_formulas_and_keeps_existing` |

### Kiểm chứng trên chính file thật (không đưa lên repo)

| | Kết quả |
|---|---|
| Cột nhận diện | 46/47, độ tin cậy 100% (`SQ` chưa rõ nghĩa) |
| 4 dòng · 1 NO · CONT TYPE `1x20` merge · 15.14 CBM | 1 nhóm, `USER_CONFIRMED`, khớp đề xuất Automation `1x20ST` |
| Giả lập ngày T-7 | `GREEN` · ghi Weekly Planning / Custom Code / MM-Daily 4 dòng, CO = Yes, CO FORM = VK, HS CODE đúng |
| Ngày hiện tại (ETD đã qua) | `COMPLETED`, không phát thông báo thừa, không chèn lô cũ vào Lịch xuất |
| Sau 2 lần Automation tô màu | 79/81 part giống từng byte; NO, công thức, 1495 defined name, comment, merge nguyên vẹn |


---

## 16. Yêu cầu v2 (bổ sung trong mô tả mới)

| Yêu cầu | Thực hiện | Test |
|---|---|---|
| Phân quyền Admin / Quản lý / Người dùng (+ Chỉ xem) | `web/auth.py` (PERMISSIONS, PBKDF2, phiên, CSRF, khóa tạm) · `web/deps.auth_guard` · trang Người dùng | `test_auth.py` · `test_pages_require_login` · `test_admin_pages_forbidden_for_manager_and_user` · `test_viewer_is_read_only` · `test_write_without_csrf_is_rejected` · `test_wrong_password_then_lockout` · `test_temp_password_forces_change` · `test_first_admin_setup_only_when_no_admin` |
| Quản lý phân bổ lô hàng cho từng người phụ trách | `services/assignment.py` (assign, auto_assign, workload) · `/assignments` · nút *Giao* trên lưới · `assignment_history` | `test_scan_auto_assigns_by_rule_then_file_owner` · `test_manager_bulk_assign_and_rescan_never_overrides` · `test_bulk_assign_by_filter` · `test_user_cannot_assign` |
| Người dùng chỉ thấy lô được giao (SRS v2 mục 53, 64) | `shipment_service.visible_stmt / get_visible` | `test_user_sees_only_assigned_rows_manager_sees_all` · `test_user_cannot_open_or_edit_foreign_shipment` |
| Khung mẫu giống file Follow Up, chức năng giống Excel | `config/followup_template.yaml` · `followup/template.py` · `/followup` (Jspreadsheet CE: sửa ô, dán từ Excel, kéo điền, hoàn tác, lọc cột, tìm, sắp xếp, cố định A–J) · `static/followup.js` | `test_template_has_47_columns_in_real_order` · `test_excel_formulas_match_real_file` · `test_edit_recalculates_formulas_and_status` · kiểm thử trình duyệt (Playwright) khi phát triển |
| Công thức Excel đúng (NO, 95% FOB, CDF AMOUNT, TSN AMOUNT) | Một định nghĩa → Python / JS / công thức Excel; ROUND kiểu Excel | `test_python_formulas_follow_dependency_order` · `test_excel_round_is_half_up_not_bankers` · `test_live_formulas_keep_typed_values` · `test_manager_can_override_formula_and_clear_it` |
| Field Ownership Matrix (Source / User / Manager / Auto / Override) | cột `user` / `manager` / `auto` / `source` trong template · EX016 cho cột `review` | `test_field_ownership` · `test_column_permissions_and_review` |
| USER > AUTOMATION, lưu Automation value / User value / ai / lúc nào | `field_src` + Audit `FIELD_CHANGED` + `_merge_web_overrides` + EX015 | `test_web_edit_survives_excel_rescan_and_conflict_goes_to_exception` |
| Optimistic lock (SRS v2 mục 54) | `shipments.version` | `test_stale_version_is_rejected` |
| Bỏ "Bot", chỉ dùng công nghệ tự động (mục 54-56, 81) | Đổi tên toàn bộ; `scheduler.py` với job + `job_runs` (Job ID, start, end, status, error, retry); bỏ tray / popup / toast | `test_job_run_records_retries` |
| Xuất Excel từ dữ liệu web: 1 Buyer = 1 sheet, 47 cột, tên sheet ≤ 31 ký tự, kiểm quyền backend | `services/excel_export.py` · `/export/followup.xlsx` | `test_export_my_shipments_one_sheet_per_buyer` · `test_manager_filtered_export` · `test_manager_export_by_filter_and_sheet_names` |
| Import Excel có xem trước (mục 70) | `engine.import_followup` · `/import` | `test_import_preview_then_apply_is_idempotent` |
| Buyer-Forwarder Rule: AUTO_ASSIGNED / NEED_CONFIRM / NOT_ASSIGNED / CONFLICT (test case 62) | `core/forwarder.py` · `buyer_rules.yaml › forwarder_rules` · EX017 | `test_forwarder_case_a…d` · `test_create_web_shipment_and_rescan_keeps_it` |
| Tạo lô trực tiếp trên web | `shipment_service.create_shipment` | `test_create_web_shipment_and_rescan_keeps_it` |
| Không xóa dữ liệu nghiệp vụ (mục 48) | `archived` (lưu trữ), khóa tài khoản thay vì xóa | `test_archive_hides_row` |
| Dashboard User (My Shipments theo D-x) / Manager (Team Workload) | `routes_main.dashboard` · `assignment.workload` | kiểm thử giao diện |
| Nâng cấp từ v1 không mất dữ liệu | `db/session._rename_legacy_columns` + mặc định cho cột mới | `test_v1_database_is_upgraded_in_place` |

### Chưa làm trong v2 → đã làm trong v3 (mục 17)

Document Matrix, Email Pattern / Confidence, Outlook Inbox, Workflow B1–B15.1 — xem bảng dưới.

---

## 17. Yêu cầu v3 (SRSv5: Booking N:N, Email Outlook, Chứng từ, Workflow, Weekly Planning, Auditor)

| Yêu cầu SRSv5 | Thực hiện | Test |
|---|---|---|
| Booking tách riêng khỏi lô; 1 lô nhiều booking (split), nhiều lô 1 booking (gộp) (mục 7-9) | `bookings` + `booking_shipments.allocated_qty` · `core/booking.allocation_status` · EX018 | `test_allocation_and_ship_status_rules` · `test_split_booking_allocation_and_partial_shipment` · `test_split_booking_partial_shipment_via_web` · `test_combined_booking_confirmation_matches_all_shipments` |
| Xuất một phần theo số lượng thực tế (mục 10, BR-012) | `shipment_executions` · `booking_service.record_execution` · `ship_status` | `test_split_booking_partial_shipment_via_web` |
| Không xóa booking; Cancel / Rebook giữ lịch sử (BR-008, BR-009) | `set_status(CANCELLED)` · `rebook()` (original ↔ replacement) · `TRANSITIONS` | `test_cancel_then_rebook_keeps_history` · `test_hold_cancel_rebook_via_web` · `test_transitions_block_invalid_moves` |
| Hold / Release, tách Booking Status / Hold Status / Ship Status (mục 11-13, Appendix A) | `core/booking.summarize / status_values` · 3 cột trên `shipments` | `test_hold_and_release` · `test_summary_prefers_active_booking_and_flags_hold` |
| Booking cũ không bị file Excel "hồi sinh"; đổi số booking → xác nhận | `booking_service.sync_rows` · EX019 | `test_cancelled_booking_is_not_resurrected_by_next_scan` · `test_changed_booking_no_in_excel_raises_ex019` · `test_excel_booking_numbers_become_bookings` |
| Đọc Outlook desktop (Inbox + Sent), chỉ email mới, chống xử lý trùng (mục 19-22, NFR-03) | `integrations/outlook.py` (COM, Message-ID, ConversationID, In-Reply-To, mốc `outlook.last_sync`) · `emails.message_id UNIQUE` | `test_request_then_confirmation_in_same_conversation` (ingest lại → duplicate) |
| Phân loại email (Request / Confirmation / Amendment / Delay / Rebook / Hold / Release / Cancel) (BR-004) | `core/email_parse.classify` · `email_profiles.yaml › categories` | `test_email_parse_fields_and_category` |
| Đọc PDF booking confirmation đính kèm | `integrations/pdf_text.py` (pdfplumber) · `EmailProcessor._store_attachment` | `test_email_pdf_conflict_requires_choice` · `test_mini_pdf_is_readable` |
| Email ≠ PDF → không tự chọn (BR-006) | `find_conflicts` → `CONFLICT` · chọn giá trị trên `/emails/{id}` | `test_email_pdf_conflict_requires_choice` · `test_email_review_queue_flow` |
| Ghép lô theo Conversation + điểm tin cậy; sát điểm → không tự chọn (mục 27, BR-003, BR-007, BR-019) | `core/email_match.score_candidates / decide` · `conversation_links` · ngưỡng 85 / 60 / margin 10 | `test_match_scoring_rules` · `test_request_then_confirmation_in_same_conversation` · `test_unmatched_and_user_value_kept` |
| Hàng chờ duyệt email, người dùng chọn lô → hệ thống học Conversation | `/emails` · `EmailProcessor.apply` | `test_email_review_queue_flow` |
| Email không ghi đè dữ liệu người dùng đã sửa | `push_to_shipments` giữ USER/MANAGER · EX020 | `test_unmatched_and_user_value_kept` |
| Hủy booking → thông báo khẩn cho người phụ trách | `set_status` → Notification CRITICAL | `test_cancel_email_notifies_owner` · `test_cancel_then_rebook_keeps_history` |
| Chứng từ có version, lưu ngoài DB, không ghi đè (mục 56-57) | `document_service.add_version` (SHA-256, `Năm/Buyer/NO/LOẠI/..._vN`) | `test_documents_versions_and_task_rules` |
| Document Matrix theo Mode / Buyer / Forwarder, hạn chứng từ, duyệt | `workflow.yaml › document_rules / document_deadlines` · `workflow_service.checklist` · `/documents/{id}/status` | `test_workflow_rules_pure` · `test_documents_versions_and_task_rules` |
| Workflow B1–B15.1, BLOCKED khi bước trước chưa xong, thiếu chứng từ không cho hoàn thành (mục 28-31) | `core/workflow.py` · `workflow_service.ensure_tasks / set_task_status` · `/tasks` | `test_documents_versions_and_task_rules` · `test_workflow_rules_pure` |
| Tự động hóa (không bấm tay khi hệ thống đã có bằng chứng) | `auto_done` (shipment_created / booking_requested / documents / departed) · `manual_status` | `test_documents_versions_and_task_rules` |
| Task / chứng từ quá hạn → thông báo người phụ trách | `workflow_service.deadline_events` → `engine._evaluate_deadlines` | chạy trong mọi test quét (`test_engine_e2e.py`) |
| Weekly Planning hạn 12:00 thứ 6, lô tuần sau từ thứ 5, nhắc theo mốc (mục 36) | `core/weekly.py` · `weekly_service` · job `weekly_planning_check` · `engine._write_outputs` (horizon) · `/weekly` | `test_weekly_planning_deadline_and_reminders` · `test_weekly_file_includes_next_week_from_thursday` |
| Vai trò Auditor chỉ đọc + ma trận quyền (mục 63) | `auth.ROLES / PERMISSIONS / PERMISSION_LABELS` · `/admin/permissions` | `test_auditor_reads_everything_but_cannot_change` · `test_new_pages_render_for_every_role` |
| Timeline sự kiện lô hàng (mục 73-74) | `shipment_events` · trang lô hàng › Dòng thời gian | `test_split_booking_allocation_and_partial_shipment` |
| Hiệu năng (NFR) | index `shipments`, `sync_rows` 2 truy vấn, `ensure_tasks` ghi hàng loạt, cache nhận diện header, phân trang lưới | `test_grid_booking_no_creates_booking_and_paging` · đo tải 10.000 lô (ARCHITECTURE 9.6) |
| Nâng cấp v2 → v3 tại chỗ | `create_all` + `_add_missing_columns` + `_ensure_indexes` | `test_v2_database_gets_v3_tables_columns_and_indexes` |

### Chưa làm trong v3 (theo phạm vi đã chốt)

| Hạng mục | Ghi chú |
|---|---|
| AI / OCR ảnh scan | PDF không có lớp chữ báo `NO_TEXT` → người duyệt; chỗ mở rộng: `integrations/pdf_text.py` |
| Microsoft Graph / thư mục .eml | Chỉ Outlook desktop theo yêu cầu; phần xử lý độc lập nguồn email |
| Tự gửi email booking / form booking | SRS khuyến nghị không tự gửi email; hệ thống đọc email người dùng gửi (Sent Items) để nhớ Conversation |

---

## 18. Yêu cầu v3.2 (SRS v5.1 → v5.5)

| Yêu cầu | Thực hiện | Test |
|---|---|---|
| **Status, Color và Deadline Warning là ba khái niệm độc lập** (v5.1 §1) · Màu UI không phải Business Status (BR-013) | `shipment_service.business_status()` ghép từ `booking_status` / `hold_status` / `ship_status`; `effective_status` chỉ còn là lớp màu; cột mới `shipments.deadline_warning`. Trang lô hàng hiện cả ba trục cạnh nhau | `test_v51_group_jogo_warning.py` |
| Cảnh báo viền đỏ: chưa book và còn dưới 14 ngày (v5.1 §16) | `core/deadline.booking_warning()` — ngưỡng lấy từ `deadlines.booking_window.end_days_before`, không hard-code; lưới tô **viền** đỏ (nền vẫn là màu trạng thái) | `test_booking_warning_follows_srs_table` (9 mốc D-20…D+1) · `test_warning_threshold_is_configurable` |
| Cảnh báo tính từ Business Status chứ không từ màu | `engine._evaluate_status` truyền `booked=sh.booking_status not in (NOT_BOOKED, CANCELLED)` | `test_booked_shipment_never_warns` · `test_closed_shipment_never_warns` |
| Lọc theo cảnh báo hạn và theo trạng thái nghiệp vụ | `apply_filters` nhận `warn=` và `biz=`; ô tick trên lưới; link từ Dashboard | kiểm thử trình duyệt |
| **Quan hệ JO – GO** — "không nên chỉ lưu GO dưới dạng text" (v5.1 §5, v5.4 §8) | `core/jogo.py` + bảng `jo_go_links` (jo, go, core, source, confidence). Quy tắc tách mã gốc trong `field_mapping.yaml › jo_go`, không hard-code | `test_jo_go_example_from_srs` (đúng ví dụ 26V02426KR01/02 ↔ S26V02426) |
| Nguồn quan hệ: IMPORT / SYSTEM_RULE / USER | Cùng dòng Excel → `IMPORT` (tin cậy 1.0); cùng mã gốc → `SYSTEM_RULE` (0.8, đánh dấu `?` trên giao diện); quan hệ do người dùng nối không bị Automation ghi đè | `test_second_jo_is_linked_even_when_its_row_has_no_go` · `test_unrelated_codes_are_not_linked` |
| **Export Shipment Group** — Shipment / Group / Booking là ba đối tượng khác nhau (v5.1 §2-4, v5.4 §6-7) | `core/export_group.py` + bảng `export_shipment_groups` + cột `shipments.export_group_key`; gom theo **BUYER + EX-FACT DATE + DEST + MODE**; trang `/groups` và `/groups/{key}` | `test_group_key_uses_all_four_fields` |
| Nhóm tự cộng tổng, nhân viên không phải cộng tay (v5.5 §4) | Tổng PCS / CTNS / G.W / CBM, đếm JO, đếm GO, container dự kiến cho cả nhóm (dùng lại `plan_containers`) | `test_group_totals_match_srs_example` (đúng ví dụ 25 JO · 3 GO · 149.10 CBM) |
| Nhóm **không** đồng nghĩa với một Booking (v5.1 §3) | Nhóm chỉ tổng hợp và liệt kê các booking đang có, không tự tạo/gộp booking; trang nhóm ghi rõ khi một nhóm nằm trên nhiều booking | `test_group_collects_booking_numbers_but_does_not_merge_them` |
| Không giới hạn số JO/GO trong một nhóm | Không có ngưỡng cứng trong code (ví dụ 25 JO của SRS chỉ là một trường hợp) | `test_group_totals_match_srs_example` |
| Thiếu CBM thì không đoán bừa | Đếm riêng `missing_cbm`, container chuyển `NEED_REVIEW`, giao diện báo "tổng chưa đủ" | `test_missing_cbm_is_counted_not_guessed` |
| **"Exception Only"** — hệ thống rà hết, người dùng chỉ nhìn việc cần làm (v5.5 §10) | `services/exception_queue.py` + khối đầu Dashboard, đủ 8 loại của SRS: booking quá hạn · email ≠ PDF · thiếu CBM · booking bị hủy · cần rebook · thiếu chứng từ · phân bổ chưa đủ · ngày xuất vừa đổi. Mỗi mục bấm vào là ra đúng danh sách | kiểm thử trình duyệt |

### Điểm mâu thuẫn trong tài liệu — chưa đổi, chờ chốt

SRS v5.1 §19 liệt kê `NOT_BOOKED`→White, `BOOKING`→**Green**, `DELAY`→**Yellow**.
SRS v5 §16 và mô tả gốc lại quy định: Vàng = đã booking nhưng **thiếu thông tin**, Xanh lá = **đủ CDF + BL**.

Hai cách hiểu khác hẳn nhau. Code đang theo **v5 §16 + mô tả gốc** (đã có test và đang chạy đúng).
Phần kiến trúc mà cả hai bản đều thống nhất — *màu không phải business status* — thì đã làm (xem dòng đầu bảng trên).
Nếu chốt theo v5.1 §19, chỗ cần sửa là `core/status.py` và `config/config.yaml › status.colors`.

---

## 19. Yêu cầu v3.3 — 5 bảng xương sống (SRSv4 §68)

SRSv4 §68 nêu năm bảng *"mới là xương sống của hệ thống"* và cảnh báo: *"Nếu chỉ dựa vào file Follow Up Excel để code thì hệ thống sẽ nhanh chóng trở nên rất khó bảo trì khi Buyer hoặc Forwarder thay đổi quy trình."*

| Bảng | Yêu cầu | Thực hiện | Test |
|---|---|---|---|
| **1 · Buyer × Forwarder** (§14, §68) | Buyer + Mode + Destination + Effective Date + Forwarder + **Service** + **Booking Rule** + **Email Parser** | `core/forwarder.ForwarderRule` thêm `service`, `booking_rule`, `parser`, `note`; `config/buyer_rules.yaml › forwarder_rules` khai đủ cột | `test_forwarder_rule_has_all_srs_columns` |
| | "Không được thiết kế Buyer → một Forwarder cố định" | Rule chọn theo buyer + mode + dest + port + khoảng ngày xuất; nhiều rule khớp → `NEED_CONFIRM`, không tự chọn (§16) | `test_one_buyer_can_have_many_forwarders` · `test_forwarder_case_a…d` |
| **2 · Email Pattern** (§15, §26, BR-020) | "Không dùng một parser cho tất cả" — mỗi Forwarder một Parser Profile: sender, domain, subject, body, attachment, booking no, JO, ETD, vessel, closing time | `core/parser_profile.py` — profile theo domain/sender, `patterns` cho 14 trường, `subject_must`, `attachment_pattern`. Wired vào `email_service.resolve_profile` | `test_forwarder_pattern_beats_generic_alias` · `test_subject_gate` |
| | Một Forwarder nhiều nhân viên, mỗi người một format (SRSv5 §25) | `contacts:` lồng trong profile; ưu tiên **mẫu nhân viên → mẫu Forwarder → alias chung** | `test_contact_pattern_beats_forwarder_pattern` · `test_contact_inherits_forwarder_aliases` |
| | Cấu hình sai phải phát hiện sớm | Pattern thiếu nhóm bắt giá trị / sai cú pháp / trường lạ → lỗi ngay lúc nạp, không phải lúc chạy | `test_bad_pattern_is_rejected_at_load_not_at_runtime` |
| **3 · Deadline** (§20, §21) | D-20 · D-14 · D-7 · D-3 · D-1 · D0 · D+1 · ETD+1 · sau ATD — tất cả configurable | `config.yaml › deadlines` (trước ngày xuất) + `workflow.yaml › document_deadlines` (sau ngày xuất). Bổ sung PNK (EX-FACT+1), BK (ETD+2), SURRENDER_BILL (ETD+12), DEBIT_NOTE (ETD+15), DO_DIA, E_SHIPMENT | `test_broken_booking_window_is_reported` |
| **4 · Document** (§68 bảng 4) | Mode · Buyer · Forwarder · Required Document · Deadline · **Owner** | `workflow.yaml › document_types` thêm `owner` cho cả 16 loại; `workflow_service.checklist` trả kèm owner | `test_every_document_has_owner_and_deadline` |
| **5 · Field Ownership** (§35, §68 bảng 5) | 47 cột · Source · Editable by User · Editable by Manager · Auto Update · **Override Rule** | `followup_template.yaml › columns` đã có `source`/`user`/`manager`/`auto`; quy tắc ghi đè suy ra và hiển thị thành câu | `test_every_field_states_who_can_edit` |
| **Kiểm tra chéo 5 bảng** | — (SRS không nêu, nhưng lỗi thật nằm ở chỗ nối giữa các bảng) | `services/matrices.py` — rule trỏ parser không tồn tại, profile không ai dùng, chứng từ thiếu hạn/owner, cột không ai sửa được, cửa sổ booking ngược… | `test_shipped_config_has_no_errors` · `test_parser_reference_must_exist` |
| **Trang xem** | — | `/matrices` — 5 tab + bảng cảnh báo, ghi rõ mỗi bảng sửa ở file nào. Quyền `rules.view` (Admin · Quản lý · Auditor) | `test_all_five_tables_are_populated` |

Nguồn vẫn là các file YAML trong `config/` chứ không đưa vào database — để thay đổi còn vào được git, review được và quay lui được.

---

## 19. 5 bảng xương sống (SRSv4 §68) — `/admin/matrices`

SRS gọi đây là xương sống và yêu cầu chốt trước khi code: *“Nếu chỉ dựa vào file Follow Up Excel để code thì hệ thống sẽ nhanh chóng trở nên rất khó bảo trì khi Buyer hoặc Forwarder thay đổi quy trình.”*

| # | Bảng | Cấu hình ở đâu | Cột theo SRS | Trạng thái |
|---|---|---|---|---|
| 1 | Buyer × Forwarder | `config/buyer_rules.yaml › forwarder_rules` | Buyer · Mode · Destination · Effective Date · Forwarder · **Service** · **Booking Rule** · **Email Parser** | Đủ — 3 cột in đậm mới bổ sung (`core/forwarder.py`) |
| 2 | Email Pattern | `config/email_profiles.yaml › forwarders` | Forwarder · Sender · Subject · Keyword · Attachment · Field Mapping | Đủ — `core/parser_profile.py` mới, có cả profile riêng cho **từng nhân viên** của một Forwarder |
| 3 | Deadline | `config/config.yaml › deadlines` + `workflow.yaml › document_deadlines` | D-20 · D-14 · D-7 · D-3 · D-1 · sau ngày xuất | Đủ — mốc sau ngày xuất nằm ở hạn chứng từ (ETD+n), không khai trùng |
| 4 | Document | `config/workflow.yaml › document_*` | Mode · Buyer · Forwarder · Required Document · Deadline · **Owner** | Đủ — cột Owner mới bổ sung cho 15 loại chứng từ |
| 5 | Field Ownership | `config/followup_template.yaml › columns` | 47 fields · Source · User · Manager · Auto Update · Override Rule | Đủ từ v2 |

### Parser Profile theo Forwarder (BR-020)

SRSv4 §15: *“Không dùng một parser cho tất cả”*. SRSv5 §25: một Forwarder có nhiều nhân viên, mỗi người một format.

```
Email → sender/domain → Forwarder Profile → địa chỉ cụ thể → Contact Profile → đọc dữ liệu
```

Thứ tự ưu tiên: **mẫu của nhân viên > mẫu của Forwarder > alias chung**. Cấu hình sai (trường lạ, regex hỏng, thiếu nhóm bắt giá trị) bị từ chối **ngay lúc nạp**, không đợi tới lúc xử lý email thật.

| Thực hiện | Test |
|---|---|
| `core/parser_profile.py` · `email_parse.normalize_extracted` · `email_service.resolve_profile` | `test_matrices.py` — 10 test parser |

### Kiểm tra chéo giữa các bảng

Giá trị lớn nhất của trang không phải là hiển thị bảng, mà là bắt **chỗ các bảng không khớp nhau** — loại lỗi không báo ngay, chỉ âm thầm làm tự động hóa bỏ sót:

| Kiểm tra | Mức |
|---|---|
| Rule Buyer × Forwarder trỏ tới parser không tồn tại | Lỗi |
| Parser Profile không khai domain lẫn sender → không bao giờ khớp email nào | Lỗi |
| Chứng từ bị yêu cầu nhưng không có trong `document_types` | Lỗi |
| Forwarder có rule nhưng chưa có bộ đọc email | Cảnh báo |
| Nhiều rule cùng khớp một Buyer (BR-007 — hệ thống phải hỏi, không tự chọn) | Cảnh báo |
| Chứng từ bắt buộc nhưng chưa ghi ai chịu trách nhiệm | Cảnh báo |
| Cột nguồn OUTLOOK nhưng `auto: false` → email về không điền được | Cảnh báo |

Cài đặt: `services/matrices.py` (tách khỏi tầng web để test được) · `web/routes_matrix.py` · `templates/matrices.html`.
Test: `tests/test_matrices.py` — 16 test.
