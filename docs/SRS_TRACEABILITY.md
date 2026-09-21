# Bảng đối chiếu SRS ↔ Code ↔ Test

Requirements Traceability Matrix — mỗi mục SRS được cài đặt ở đâu và test nào chứng minh nó hoạt động.

---

## 1. Kiến trúc & môi trường

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 3 | Chạy ngầm Windows, tray, database, notification | `scheduler.py` · `tray.py` · `db/` · `notify/` | thủ công |
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
| 34 | Tách Bot Status và User Status | `bot_status` / `effective_status` | `test_user_override_wins` |
| 35, 69 | USER > BOT, Bot không ghi đè | `engine._load_overrides` | `test_engine_e2e::test_user_override_survives_next_scan` |
| 35 | User chốt container, Bot giữ nguyên | `_allocate_containers` | `test_user_container_choice_survives` |
| 36 | Audit Log đầy đủ | `audit.log_audit` · bảng `audit_logs` | `test_audit_log_records_bot_writes` |
| 58 | Phân quyền User / Bot | `users.role` | thủ công |
| 59 | Bổ sung Admin | `users.role = admin` | thủ công |

## 8. An toàn dữ liệu Excel

| SRS | Yêu cầu | Cài đặt | Test |
|---|---|---|---|
| 23 | Không INSERT trùng, có UPSERT | `ExcelTargetWriter.plan` | `test_insert_then_no_duplicate_on_rerun` |
| 37 | User sửa Excel → Bot không ghi đè | `protect_user_edits` | `test_bot_never_erases_user_value_with_blank` |
| 38 | Version Check trước khi ghi | `excel/backup.snapshot` | `test_conflict_detection_stops_write` |
| 39 | Popup Conflict | `EX007` + popup | `test_conflict_detection_stops_write` |
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
| 50 | Popup có Context | `notify/channels.PopupChannel` | thủ công |
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
| User override thắng Bot | `test_user_override_survives_next_scan` | ✅ |
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
| 4 dòng · 1 NO · CONT TYPE `1x20` merge · 15.14 CBM | 1 nhóm, `USER_CONFIRMED`, khớp đề xuất Bot `1x20ST` |
| Giả lập ngày T-7 | `GREEN` · ghi Weekly Planning / Custom Code / MM-Daily 4 dòng, CO = Yes, CO FORM = VK, HS CODE đúng |
| Ngày hiện tại (ETD đã qua) | `COMPLETED`, không phát thông báo thừa, không chèn lô cũ vào Lịch xuất |
| Sau 2 lần Bot tô màu | 79/81 part giống từng byte; NO, công thức, 1495 defined name, comment, merge nguyên vẹn |
