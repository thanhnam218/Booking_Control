# Kiến trúc hệ thống

## 1. Nguyên tắc nền

> Không xây "Excel trên web", mà xây **Shipment Management + Workflow + Automation**, rồi làm giao diện 47 cột đủ giống Follow Up để người dùng chuyển đổi dễ dàng. **Database ở giữa**; Excel chỉ còn là nguồn nhập, file xuất và định dạng quen thuộc.

Từ v2 không còn khái niệm "Bot" (SRS v2 mục 54, 81): các tác vụ tự động là **job của Automation Engine** chạy phía máy chủ; người dùng làm việc qua web có đăng nhập và phân quyền.

Hệ quả trực tiếp:

- Mọi dữ liệu đọc từ Excel đều được **chuẩn hóa** rồi mới vào DB → logic nghiệp vụ không bao giờ chạm vào giá trị thô
- Mọi thay đổi đều có **lịch sử** (Audit Log) → truy được ai đổi gì, lúc nào
- Mọi thứ Automation **không chắc** đều vào Exception Center → không đoán bừa
- Automation ghi ra Excel là bước **cuối cùng**, có backup + version check + retry

---

## 2. Luồng xử lý

```
AUTO START
   │
   ▼
Load Configuration          config/*.yaml → Settings, FieldDictionary, BuyerRegistry
   │
   ▼
Scan Follow Up              mỗi file của mỗi user
   │
   ▼
Detect Follow Up Sheets     chấm điểm theo header, không theo tên sheet
   │                        confidence < 80% → hỏi User (EX005)
   ▼
Read Headers                alias + fuzzy → canonical field
   │                        không chắc → EX010
   ▼
Normalize Data              date / decimal / flag / ship mode
   │                        VALID | BLANK | NEED_REVIEW | INVALID
   ▼
Validate ──── Invalid ────► Exception Center (EX001, EX004, EX011)
   │
   ▼
Identify Shipment           ShipmentKey = sha256(owner|JO|ExFtyDate)  (NO la cong thuc)
   │                        trùng key → EX003, không tạo bản ghi trùng
   ▼
Group NO / JO / INV         GroupKey: NO -> BOOKING NO -> tung JO (theo Buyer Rule)
   │
   ▼
Apply Buyer Rule            CO, CO Form, container rule, site, declaration rule
   │
   ▼
Calculate Container         capacity >= CBM, ưu tiên 40HC → 40DC → 20ST
   │                        không đủ dữ liệu → NEED_REVIEW (EX009)
   ▼
Calculate Booking Status    RED > CANCELLED > COMPLETED/PARTIAL > WHITE > YELLOW > GREEN
   │
   ▼
Apply User Override         USER > AUTO
   │
   ▼
Check Deadline
   ├── T-20 ~ T-14 → Booking Reminder
   ├── T-7         → Weekly Planning + Custom Code + MM-Daily
   ├── T-3         → Final Information check
   └── T-1         → Lịch xuất hàng TDV/TGV
   │
   ▼
Check File Version
   ├── Changed → STOP + Alert (EX007)
   ├── Locked  → retry 30/60/120s → Pending Queue (EX006)
   │
   ▼
Backup → Update Excel (LOSSLESS + atomic) → Verify
   │
   ▼
Audit Log → WAIT NEXT SCAN
```

---

## 3. Bản đồ module

```
src/booking_control/
├── settings.py             Đọc & validate YAML → object có kiểu
├── logging_setup.py        Log xoay vòng theo ngày
├── audit.py                Audit Log + Exception Center helper
├── engine.py               ★ Điều phối toàn bộ pipeline
├── scheduler.py            Automation Engine: job + JobRun (retry, lịch sử)
├── followup/template.py    Khung 47 cột, quyền từng cột, công thức Python/JS/Excel
├── services/               Nghiệp vụ dùng chung web + automation (quyền, sửa ô, phân công, xuất Excel)
├── cli.py                  Giao diện dòng lệnh (typer)
│
├── db/
│   ├── models.py           21 bảng SQLAlchemy
│   └── session.py          Engine + session scope + nâng cấp cột tự động (kể cả đổi tên cột v1)
│
├── excel/                  ── TẦNG I/O ──
│   ├── mapping.py          Data Dictionary, header → canonical field, confidence
│   ├── detector.py         Nhận diện sheet Follow Up + dòng header
│   ├── reader.py           Đọc workbook → CanonicalRow (đã chuẩn hóa)
│   ├── writer.py           UPSERT an toàn + tô màu + conflict detection
│   ├── xlsx_patch.py       ★ Ghi lossless: sửa XML trực tiếp, giữ nguyên mọi thứ khác
│   ├── locking.py          Phát hiện file bị khóa, retry, ghi atomic
│   └── backup.py           Version token, hash, backup, rollback
│
├── core/                   ── TẦNG NGHIỆP VỤ (thuần, không I/O) ──
│   ├── normalize.py        Chuẩn hóa giá trị
│   ├── buyer_rules.py      Buyer registry + rule
│   ├── shipment.py         ShipmentKey, GroupKey, duplicate, split JO, gợi ý gộp lô
│   ├── container.py        Container Allocation Engine
│   ├── status.py           Status Engine + explain trace
│   ├── forwarder.py        Buyer-Forwarder Rule: AUTO_ASSIGNED / NEED_CONFIRM / NOT_ASSIGNED
│   └── deadline.py         T-20…T-1, kiểm tra T-3
│
├── outputs/                ── ÁNH XẠ FILE ĐÍCH ──
│   ├── base.py             OutputSpec chung
│   ├── weekly_planning.py  T-7
│   ├── custom_code.py      T-7
│   ├── mm_daily.py         T-7
│   └── shipping_schedule.py T-1, TDV/TGV
│
├── notify/
│   ├── channels.py         web (chuông + Desktop Notification) / email SMTP / console
│   └── engine.py           Dedup, phân loại severity, khung giờ, gom nhóm
│
├── integrations/
│   └── outlook.py          Đọc Outlook desktop (tùy chọn)
│
└── web/
    ├── app.py              FastAPI app + xử lý đăng nhập / cấm truy cập
    ├── auth.py deps.py     Mật khẩu, phiên, CSRF, ma trận quyền
    ├── routes_*.py         auth · main · grid (lưới + export/import) · admin
    ├── templates/          20 trang Jinja2
    └── static/             followup.js (lưới), app.js, vendor/ Jspreadsheet CE + jSuites (MIT)
```

**Quy tắc phụ thuộc:** `core/` không import `excel/`, `db/` hay `web/`. Toàn bộ logic nghiệp vụ là hàm thuần → test được không cần file Excel, không cần database.

---

## 4. Database

21 bảng, nhóm theo vai trò:

**Cấu hình & ánh xạ**

| Bảng | Vai trò |
|---|---|
| `users` | Tài khoản: vai trò admin / manager / user / viewer, mật khẩu băm, nhóm |
| `web_sessions` | Phiên đăng nhập (lưu hash token) + CSRF token |
| `source_files` | File Registry — đường dẫn, quyền đọc/ghi, version token cuối |
| `sheet_maps` | Sheet nào là Follow Up, confidence, User đã xác nhận chưa, chữ ký header |
| `column_mappings` | Header Excel → canonical field, do User chốt |
| `buyers` | Buyer + rule dạng JSON |

**Dữ liệu nghiệp vụ**

| Bảng | Vai trò |
|---|---|
| `shipments` | Lô hàng — khóa `shipment_key`, metadata nguồn, `data_hash`; v2 thêm `row_data` (47 cột), `field_src` (ô nào sửa tay, bởi ai), `version` (optimistic lock), `assigned_to` / `backup_owner`, `forwarder`, `origin` (EXCEL/WEB), `archived` |
| `assignment_history` | Lịch sử giao / giao lại (không xóa) |
| `invoices` | 1-n: một lô nhiều invoice |
| `customs_declarations` | 1-n: một lô nhiều tờ khai |
| `container_allocations` | Theo `group_key`, không theo từng lô |

**Vận hành**

| Bảng | Vai trò |
|---|---|
| `user_overrides` | USER > AUTO — trường nào User đã chốt |
| `audit_logs` | Ai · lúc nào · đổi gì · từ đâu |
| `exceptions` | Exception Center, có `dedup_key` chống trùng |
| `notifications` | Notification Center, `dedup_key` chống spam |
| `planning_records` | Automation đã ghi dòng nào ra file đích → chống INSERT trùng |
| `pending_writes` | Hàng đợi khi file bị khóa |
| `file_versions` | Lịch sử version + đường dẫn backup |
| `system_settings` | Cài đặt runtime |
| `scan_runs` | Lịch sử từng lần quét |
| `job_runs` | Mọi job Automation: bắt đầu, kết thúc, trạng thái, lỗi, số lần thử lại |

SQLite mặc định (WAL mode) — đủ cho một phòng vài chục người. Nhiều người / nhiều máy chủ → đổi `database.url` sang SQL Server hoặc PostgreSQL, không cần sửa code.

---

## 5. Bảy quyết định thiết kế quan trọng

### 5.1 ShipmentKey thay vì JO

```python
shipment_key = sha256(f"{owner}|{JO}|{ExFtyDate}")
```

Một JO tách thành 2 ngày xuất là **hai lô hàng**, mỗi lô có deadline, container và trạng thái riêng. Dùng JO làm khóa sẽ khiến lô thứ hai ghi đè lô thứ nhất.

**Vì sao không có NO trong khóa** — quyết định sau khi xem file Follow Up thật: cột NO là công thức `=IF(LEFT(M2,4)="TEGV",RIGHT(M2,5)-2108,"")`, trống cho đến khi có invoice. Nếu NO nằm trong khóa, lúc invoice xuất hiện lô hàng sẽ bị coi là lô mới. NO chỉ dùng để **gom lô**.

**Ngoại lệ đã xử lý:** khi `ExFtyDate` của **cùng một dòng Excel** thay đổi, Automation nhận ra qua vị trí dòng (`file + sheet + row`) cộng với `JO` không đổi → coi là **cùng một lô bị dời lịch**, đổi khóa và tính lại deadline, không tạo lô mới.

### 5.2 GroupKey tách khỏi ShipmentKey

Container tính theo **nhóm**, không theo từng lô: IKEA gom mọi JO cùng ngày xuất vào một container; buyer khác gom theo `NO + ExFtyDate`. Quy tắc gom nằm trong `buyer_rules.yaml`.

### 5.3 Trạng thái Automation và trạng thái hiển thị là hai cột riêng

```
auto_status         Automation tính, luôn cập nhật
effective_status   = user_override nếu có, ngược lại = auto_status
```

Nhờ tách hai cột: User override mà vẫn thấy Automation nghĩ gì; bỏ override là quay lại trạng thái Automation ngay, không cần quét lại.

### 5.4 Ba lớp chống mất dữ liệu

```
1. Version token   mtime_ns + size, so trước khi ghi → khác thì STOP
2. Row hash        sha256 của dòng đã chuẩn hóa → biết User sửa dòng nào
3. Backup          copy trước mỗi lần ghi, giữ 20 bản, rollback 1 click
```

Cộng với ghi **atomic**: ghi ra file tạm rồi `os.replace` — lỗi giữa chừng không làm hỏng file gốc.

### 5.5 Mọi mapping đều nằm trong config

Không có `column A = NO` ở bất kỳ đâu trong code. Đổi tên cột trong Excel → sửa `aliases` trong YAML hoặc ánh xạ trong Dashboard, không cần build lại.

### 5.6 Confidence thay cho boolean

Sheet detection và header mapping đều trả về **điểm số**, không phải có/không:

```
>= 0.88   tự động nhận
0.65–0.88 đưa vào Exception Center, hỏi User một lần
<  0.65   bỏ qua
```

Automation không bao giờ tự map mơ hồ. Sau khi User xác nhận, quyết định đó được lưu và ưu tiên tuyệt đối.

### 5.7 Ghi Excel lossless

Đây là quyết định quan trọng nhất được đưa ra **sau khi thử trên file Follow Up thật** (1 sheet, 47 cột, 33 external link, 1495 defined name, 1 comment, thiết lập in). Đo thực tế — mở rồi lưu lại bằng openpyxl:

| | Trước | Sau openpyxl |
|---|---|---|
| Cột NO (công thức) đọc lại | số NO | **`None`** — mất giá trị tính sẵn |
| Defined name | 1495 | **1493** |
| Thiết lập in, calcChain | có | **mất** |
| 33 external link | nguyên | **bị ghi lại** |

Automation chạy lần hai sẽ đọc NO = trống → mọi lô mất số folder. Vì vậy `excel/xlsx_patch.py` mở file như một gói zip và chỉ sửa đúng:

```
xl/styles.xml              thêm fill / xf mới (tái sử dụng nếu đã có)
xl/worksheets/sheetN.xml   thuộc tính s="" của ô cần tô, hoặc giá trị ô cần ghi
xl/workbook.xml            chỉ khi có công thức mới (bật fullCalcOnLoad)
```

Kết quả trên chính file thật sau 2 lần Automation ghi: **79/81 part giống từng byte**, NO vẫn đọc đúng, công thức, merge, comment, 1495 defined name còn nguyên.

Quy tắc đi kèm:
- **Không bao giờ ghi đè ô công thức** — `set_value()` trả `False`
- Dòng mới trong file đích: **kéo công thức** từ dòng mẫu (dịch tham chiếu như Excel fill-down); cột mà dòng mẫu dùng công thức thì Automation không ghi giá trị của nó vào
- Style mới được **dedup** → chạy 1000 lần `styles.xml` không phình
- Không có ô nào đổi → **không đụng vào file** (mtime giữ nguyên)
- openpyxl chỉ dùng để **đọc**

---

## 6. Đồng thời và độ bền

| Tình huống | Cách xử lý |
|---|---|
| Hai Automation cùng ghi 1 file chung | Version check → Automation thứ hai dừng, thử lại lần quét sau |
| File đang mở trong Excel | Phát hiện marker `~$`, retry 30/60/120s → Pending Queue |
| Automation bị kill giữa lúc ghi | Ghi atomic → file gốc nguyên vẹn; bản backup vẫn còn |
| Máy tắt đột ngột | SQLite WAL → DB không hỏng; lần chạy sau quét lại |
| Scan trước chưa xong đã tới giờ scan sau | `max_instances=1` + khóa nội bộ → bỏ qua lần trùng |

---

## 7. Mở rộng

| Muốn làm gì | Sửa ở đâu |
|---|---|
| Thêm cột mới từ Follow Up | Thêm field vào `config/field_mapping.yaml` |
| Đổi tên cột file đích | Sửa `SPEC.columns` trong `outputs/<file>.py` |
| Thêm file đích thứ 6 | Tạo module mới trong `outputs/`, đăng ký vào `REGISTRY` |
| Thêm trạng thái mới | Thêm vào `core/status.py` + `status.colors` trong config |
| Đổi luật container | `config.yaml → container`, hoặc `core/container.py` |
| Thêm kênh thông báo (Zalo, Teams…) | Kế thừa `Channel` trong `notify/channels.py` |
| Đổi sang SQL Server | Đổi `database.url` |

Chi tiết kèm ví dụ code: [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).


---

## 8. Kiến trúc v2 — Web + Workflow + Automation

### 8.1 Triển khai

```
 Người dùng (Chrome/Edge) ──HTTP LAN──▶  MÁY CHỦ NỘI BỘ Windows  (bookingctl run, web.host 0.0.0.0)
                                         ├── FastAPI: auth_guard (phiên + CSRF) → routes → services
                                         ├── AutomationScheduler (cùng tiến trình)
                                         │     followup_scan · notification_dispatch · excel_write_retry
                                         │     outlook_scan · housekeeping   → job_runs
                                         └── Database (SQLite WAL / SQL Server / PostgreSQL)
 File Excel (OneDrive đã sync về máy chủ) ◀── đọc Follow Up, ghi Planning/HS Code/MM/Lịch xuất (lossless)
```

Một tiến trình duy nhất cho web + automation: đơn giản để cài trên Windows, không cần Redis/Celery. Khóa `threading.Lock` trong scheduler bảo đảm hai lần quét không chồng nhau; mỗi request web dùng session DB riêng.

### 8.2 Nguồn dữ liệu và thứ tự ưu tiên (SRS v2 mục 71)

```
1. Người dùng / Quản lý sửa trên web      field_src[field] = USER | MANAGER   (thắng)
2. Booking sửa trên web / Email Outlook    field_src[field] = BOOKING | OUTLOOK (không ghi đè USER -> EX020)
3. Excel import (quét file / upload)       chỉ ghi ô chưa bị sửa tay / chưa do booking, email ghi
4. Rule mặc định / công thức
```

`engine._merge_web_overrides()` áp thứ tự này mỗi lần đọc Excel: ô đã sửa tay giữ giá trị web; nếu file Excel **đổi** ô đó sang giá trị khác thì tạo `EX015` (một lần cho mỗi giá trị) để Quản lý chọn *giữ web* hoặc *lấy Excel*. Nhờ `field_src[field].excel` (giá trị Excel lần cuối thấy), file không đổi thì không báo lại.

`apply_values()` là **một hàm duy nhất** gán 47 cột → các cột nghiệp vụ (trạng thái, CO, site, forwarder, cờ HOLD từ REMARK) cho cả Excel import lẫn sửa trên web → hai đường không bao giờ lệch logic.

### 8.3 Phân quyền

- **Vai trò** (`web/auth.py → PERMISSIONS`): admin ⊃ manager ⊃ user; viewer chỉ đọc.
- **Phạm vi dòng** (`services/shipment_service.visible_stmt`): user chỉ thấy lô `assigned_to` hoặc `backup_owner` là mình. Mọi truy vấn web (lưới, danh sách, chi tiết, export, exception, thông báo) đều đi qua đây. Lô ngoài quyền trả 404 (không lộ là có tồn tại).
- **Quyền cột** (`followup_template.yaml`): `edit / review / view` cho user, `edit / view` cho manager. `review` = cho sửa nhưng tạo `EX016`.
- **CSRF**: token theo phiên, bắt buộc với mọi POST (form: `_csrf`, fetch: header `X-CSRF-Token`). Cookie `HttpOnly`, `SameSite=Lax`.

### 8.4 Đồng thời

- **Optimistic lock**: mỗi lô có `version`; lưới gửi kèm `v`; lệch → không ghi, trả dòng mới nhất (`conflicts`). Automation, phân công, sửa tay đều tăng `version`.
- Excel import tăng `version` khi `data_hash` của dòng đổi → người đang mở lưới cũ sẽ nhận conflict thay vì ghi đè dữ liệu vừa quét.

### 8.5 Lưới Follow Up

Jspreadsheet CE 4 (MIT) đóng gói trong `static/vendor` — chạy offline trong LAN. `parseFormulas: false` + `stripHTML: true`: ô không bao giờ được hiểu là HTML hay công thức JS (chống XSS từ dữ liệu Excel). Công thức nghiệp vụ lấy từ server dưới dạng biểu thức đã kiểm tra bằng `ast`, dịch sang hàm JS chỉ gồm `F/A/S/M/D/R` → không `eval` chuỗi người dùng.

Cột công thức chỉ tự tính lại khi đang **"sống"** (giá trị hiện tại = kết quả công thức, hoặc trống) — dòng Excel gõ tay số NO không bị ghi đè; dòng tạo trên web luôn theo công thức.

### 8.6 Xuất Excel

`services/excel_export.build_workbook()` tạo file mới bằng openpyxl (không đụng file gốc): nhóm theo Buyer → sheet (≤ 31 ký tự, chống trùng), 47 cột theo template, công thức Excel thật cho cột công thức (trừ ô đã sửa tay), cột hệ thống ở cuối, freeze A–J, AutoFilter, màu trạng thái, merge CONT TYPE theo nhóm liên tiếp.


---

## 9. Kiến trúc v3 — Booking tách riêng, Email, Chứng từ, Quy trình

### 9.1 Mô hình dữ liệu

```
 shipments (47 cột + booking_status / hold_status / ship_status)
     │ N                                   N │
     └────────── booking_shipments ──────────┘  allocated_qty (None = cả lô)  → split / gộp
                        │ N
                        1
                     bookings  (status, booking_no, vessel, ETD, cut-off, conversation_id,
                                original_booking_id ↔ replacement_booking_id)   ← không bao giờ xóa
 shipment_executions   lần xuất thực tế (ngày, SL, cont, seal)  → PARTIALLY_SHIPPED / FULLY_SHIPPED
 shipment_events       dòng thời gian nghiệp vụ (BOOKING_CONFIRMED, ETD_CHANGED, EMAIL_*, TASK_*...)
 emails ─ email_attachments   message_id UNIQUE (idempotent), điểm, ứng viên, email vs PDF
 conversation_links    Conversation Outlook ↔ lô / booking (học từ lần ghép đầu)
 documents ─ document_versions   metadata + đường dẫn + SHA-256; file nằm ngoài DB
 workflow_tasks        B1…B15.1 mỗi lô (unique shipment_code + step_code), manual_due, manual_status
```

### 9.2 Trạng thái tách lớp

`core/booking.summarize()` gộp các booking của một lô → `booking_status` (booking hiệu lực mới nhất), `hold` (có booking ON_HOLD), `cancelled` (mọi booking đã hủy, chưa rebook), mức phân bổ (`FULL / PARTIAL / OVER`). `status_values()` đưa các cờ này vào Status Engine cũ → màu dòng (RED / CANCELLED / PARTIAL…) vẫn tính bằng **một** chỗ (`core/status.evaluate`). Ba cột `booking_status`, `hold_status` (NO_HOLD / HOLD / RELEASED), `ship_status` lưu riêng để lọc, báo cáo.

### 9.3 Đồng bộ với Follow Up

- **Excel → booking**: mỗi lần quét, `booking_service.sync_rows()` biến BOOKING NO thành booking (cùng số trên nhiều lô → một booking gộp). Booking đã hủy không bị "hồi sinh"; số booking đổi khi booking cũ còn hiệu lực → EX019, không tự hủy.
- **Booking → 47 cột**: `push_to_shipments()` ghi Booking No / ETD / ETA / Vessel / Booking Date / Closing vào lô qua `engine.apply_values()`; ô USER / MANAGER được giữ (trừ khi chính người dùng sửa booking trên web).
- **Lưới → booking**: gõ BOOKING NO trên lưới hoặc tạo lô có BOOKING NO → `sync_from_row(source=USER)`.

### 9.4 Email Outlook

Tách hai lớp để test được trên Linux: `integrations/outlook.py` (COM, chỉ đọc email thành `MailMessage`) và `services/email_service.EmailProcessor` (thuần Python). Mốc đọc lần cuối theo từng thư mục lưu ở `system_settings["outlook.last_sync"]`, đọc lùi 10 phút; trùng `message_id` bị bỏ qua. Chấm điểm bằng **token có thật trong DB** (không cần biết định dạng JO/PO của công ty); Buyer / ETD / Vessel / Forwarder chỉ cộng thêm khi đã có dấu hiệu định danh. Email ≠ PDF → `CONFLICT`, không bao giờ tự chọn.

### 9.5 Quy trình & chứng từ

`config/workflow.yaml` là nguồn duy nhất: bước, hạn (anchor + offset), phụ thuộc, chứng từ bắt buộc, điều kiện tự hoàn thành. `workflow_service.ensure_tasks()` chạy mỗi lần quét, đọc task dạng tuple và ghi hàng loạt (`insert` / `update` theo khóa chính) → 10.000 lô, ~150.000 task đồng bộ trong vài giây. Task người dùng đã đổi tay (`manual_status`) hoặc hạn đã chỉnh (`manual_due`) không bị Automation ghi đè. Đổi cấu hình bước → hash thay đổi → cập nhật tên / thứ tự / phụ thuộc cho task chưa xong một lần.

### 9.6 Hiệu năng

| Tác vụ (10.000 lô, SQLite, laptop) | Thời gian |
|---|---|
| Nhận diện header Excel (có cache + chặn trên SequenceMatcher) | nhanh hơn ~6 lần so với v2 |
| Đồng bộ booking + task mỗi lần quét | 2–5 giây |
| Trang Tổng quan / Lô hàng / Booking / Việc của tôi | 0,1–2 giây |
| Lưới Follow Up | phân trang phía máy chủ (`web.grid_page_size`, mặc định 2.000 dòng/trang) |

Index mới trên `shipments` (NO, JO, GO, PO, Buyer, Booking No, ETD, GroupKey) được tạo tự động cả trên database cũ (`db/session._ensure_indexes`).
