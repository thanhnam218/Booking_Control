# Kiến trúc hệ thống

## 1. Nguyên tắc nền

> Không xây một "Excel Macro thông minh", mà xây một hệ thống Automation có **Database ở giữa**, Excel là nguồn và đích dữ liệu.

Hệ quả trực tiếp:

- Mọi dữ liệu đọc từ Excel đều được **chuẩn hóa** rồi mới vào DB → logic nghiệp vụ không bao giờ chạm vào giá trị thô
- Mọi thay đổi đều có **lịch sử** (Audit Log) → truy được ai đổi gì, lúc nào
- Mọi thứ Bot **không chắc** đều vào Exception Center → không đoán bừa
- Bot ghi ra Excel là bước **cuối cùng**, có backup + version check + retry

---

## 2. Luồng xử lý

```
BOT START
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
Identify Shipment           ShipmentKey = sha256(owner|NO|JO|ExFtyDate)
   │                        trùng key → EX003, không tạo bản ghi trùng
   ▼
Group NO / JO / INV         GroupKey theo Buyer Rule (by_no_exfty | by_no | by_exfty)
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
Apply User Override         USER > BOT
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
Backup → Update Excel (atomic) → Verify
   │
   ▼
Audit Log → WAIT NEXT SCAN
```

---

## 3. Bản đồ module

```
src/booking_bot/
├── settings.py             Đọc & validate YAML → object có kiểu
├── logging_setup.py        Log xoay vòng theo ngày
├── audit.py                Audit Log + Exception Center helper
├── engine.py               ★ Điều phối toàn bộ pipeline
├── scheduler.py            APScheduler: quét định kỳ, flush thông báo, retry queue
├── tray.py                 Icon khay hệ thống (pystray)
├── cli.py                  Giao diện dòng lệnh (typer)
│
├── db/
│   ├── models.py           17 bảng SQLAlchemy
│   └── session.py          Engine + session scope
│
├── excel/                  ── TẦNG I/O ──
│   ├── mapping.py          Data Dictionary, header → canonical field, confidence
│   ├── detector.py         Nhận diện sheet Follow Up + dòng header
│   ├── reader.py           Đọc workbook → CanonicalRow (đã chuẩn hóa)
│   ├── writer.py           UPSERT an toàn + tô màu + conflict detection
│   ├── locking.py          Phát hiện file bị khóa, retry, ghi atomic
│   └── backup.py           Version token, hash, backup, rollback
│
├── core/                   ── TẦNG NGHIỆP VỤ (thuần, không I/O) ──
│   ├── normalize.py        Chuẩn hóa giá trị
│   ├── buyer_rules.py      Buyer registry + rule
│   ├── shipment.py         ShipmentKey, GroupKey, duplicate, split JO, gợi ý gộp lô
│   ├── container.py        Container Allocation Engine
│   ├── status.py           Status Engine + explain trace
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
│   ├── channels.py         console / toast / popup Tkinter / callback
│   └── engine.py           Dedup, phân loại severity, khung giờ, gom nhóm
│
├── integrations/
│   └── outlook.py          Đọc Outlook desktop (tùy chọn)
│
└── web/
    ├── app.py              FastAPI routes
    └── templates/          11 trang Jinja2
```

**Quy tắc phụ thuộc:** `core/` không import `excel/`, `db/` hay `web/`. Toàn bộ logic nghiệp vụ là hàm thuần → test được không cần file Excel, không cần database.

---

## 4. Database

17 bảng, nhóm theo vai trò:

**Cấu hình & ánh xạ**

| Bảng | Vai trò |
|---|---|
| `users` | Người dùng, vai trò admin/user |
| `source_files` | File Registry — đường dẫn, quyền đọc/ghi, version token cuối |
| `sheet_maps` | Sheet nào là Follow Up, confidence, User đã xác nhận chưa, chữ ký header |
| `column_mappings` | Header Excel → canonical field, do User chốt |
| `buyers` | Buyer + rule dạng JSON |

**Dữ liệu nghiệp vụ**

| Bảng | Vai trò |
|---|---|
| `shipments` | Lô hàng — khóa `shipment_key`, kèm metadata nguồn và `data_hash` |
| `invoices` | 1-n: một lô nhiều invoice |
| `customs_declarations` | 1-n: một lô nhiều tờ khai |
| `container_allocations` | Theo `group_key`, không theo từng lô |

**Vận hành**

| Bảng | Vai trò |
|---|---|
| `user_overrides` | USER > BOT — trường nào User đã chốt |
| `audit_logs` | Ai · lúc nào · đổi gì · từ đâu |
| `exceptions` | Exception Center, có `dedup_key` chống trùng |
| `notifications` | Notification Center, `dedup_key` chống spam |
| `planning_records` | Bot đã ghi dòng nào ra file đích → chống INSERT trùng |
| `pending_writes` | Hàng đợi khi file bị khóa |
| `file_versions` | Lịch sử version + đường dẫn backup |
| `system_settings` | Cài đặt runtime |
| `scan_runs` | Lịch sử từng lần quét |

SQLite mặc định (WAL mode). Nhiều user dùng chung → đổi `database.url` sang SQL Server, không cần sửa code.

---

## 5. Sáu quyết định thiết kế quan trọng

### 5.1 ShipmentKey thay vì JO

```python
shipment_key = sha256(f"{owner}|{NO}|{JO}|{ExFtyDate}")
```

Một JO tách thành 2 ngày xuất là **hai lô hàng**, mỗi lô có deadline, container và trạng thái riêng. Dùng JO làm khóa sẽ khiến lô thứ hai ghi đè lô thứ nhất.

**Ngoại lệ đã xử lý:** khi `ExFtyDate` của **cùng một dòng Excel** thay đổi, Bot nhận ra qua vị trí dòng (`file + sheet + row`) cộng với `NO` và `JO` không đổi → coi là **cùng một lô bị dời lịch**, đổi khóa và tính lại deadline, không tạo lô mới.

### 5.2 GroupKey tách khỏi ShipmentKey

Container tính theo **nhóm**, không theo từng lô: IKEA gom mọi JO cùng ngày xuất vào một container; buyer khác gom theo `NO + ExFtyDate`. Quy tắc gom nằm trong `buyer_rules.yaml`.

### 5.3 Trạng thái Bot và trạng thái hiển thị là hai cột riêng

```
bot_status         Bot tính, luôn cập nhật
effective_status   = user_override nếu có, ngược lại = bot_status
```

Nhờ tách hai cột: User override mà vẫn thấy Bot nghĩ gì; bỏ override là quay lại trạng thái Bot ngay, không cần quét lại.

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

Bot không bao giờ tự map mơ hồ. Sau khi User xác nhận, quyết định đó được lưu và ưu tiên tuyệt đối.

---

## 6. Đồng thời và độ bền

| Tình huống | Cách xử lý |
|---|---|
| Hai Bot cùng ghi 1 file chung | Version check → Bot thứ hai dừng, thử lại lần quét sau |
| File đang mở trong Excel | Phát hiện marker `~$`, retry 30/60/120s → Pending Queue |
| Bot bị kill giữa lúc ghi | Ghi atomic → file gốc nguyên vẹn; bản backup vẫn còn |
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
