# Data Mapping Specification (DMS)

Tài liệu trả lời chính xác: *Bot lấy dữ liệu ở đâu → hiểu đó là gì → xử lý theo rule nào → ghi vào đâu → ghi thế nào → dữ liệu sai/thay đổi thì làm gì?*

---

## 1. File Registry

| ID | File | Vai trò | Nguồn/Đích | Chủ | Mốc |
|---|---|---|---|---|---|
| `F01-*` | Follow Up | Nguồn chính | Cá nhân | mỗi user 1 file | quét mỗi 1–4h |
| `F02` | WEEKLY PLANNING 2026 | Đích | Dùng chung | common | T-7 |
| `F03` | Custom Code | Đích | Dùng chung | common | T-7 |
| `F04` | MM-DAILY PLANNING (GV) | Đích | Dùng chung | common | T-7 |
| `F05` | Lịch xuất hàng - IP (DV) | Đích | Dùng chung | common | T-1 |

Khai báo trong `config/config.yaml → files:` với các thuộc tính: `id`, `role`, `path`, `owner`, `storage`, `read`, `write`, `sheet`, `header_row`, `sheets_by_site`.

Bot lưu thêm `last_version_token` và `last_scanned_at` trong bảng `source_files` để phát hiện thay đổi.

---

## 2. Sheet Mapping

Bot **không** dựa vào tên sheet. Quy trình nhận diện:

```
Sheet → dò 15 dòng đầu → chấm điểm từng dòng theo số canonical field khớp
     → chọn dòng nhiều nhất làm header
     → confidence = (số trường nhận diện khớp) / (tổng trường nhận diện) + thưởng
```

Trường dùng để nhận diện (`sheet_detection.required_any`):

```
ShipmentNo · ExFtyDate · JO · FinalCBM · Invoice · FinalExportQty
```

Ngưỡng quyết định:

| Confidence | Quyết định |
|---|---|
| `>= 0.80` | `SCAN` — tự động quét |
| `0.35 – 0.80` | `ASK_USER` — vào Exception Center `EX005`, hỏi một lần |
| `< 0.35` | `IGNORE` |

Ngoài ra bỏ qua ngay: sheet ẩn, sheet dưới 2 dòng, tên khớp `^summary$`, `^report$`, `^data$`, `^template$`…

Ví dụ thực tế từ bộ demo:

| Sheet | Là Follow Up? | Confidence | Hành động |
|---|---|---|---|
| ABC | ✓ | 100% | Scan |
| XYZ | ✓ | 100% | Scan |
| IKEA | ✓ | 100% | Scan |
| Summary | ✗ | 0% | Ignore |
| Data | ✗ | 0% | Ignore |

Quyết định của User (Dashboard → Mapping) lưu vào bảng `sheet_maps` với `confirmed_by_user = true` và **ưu tiên tuyệt đối** hơn kết quả tự động.

---

## 3. Column Mapping — Canonical Data Model

Excel có thể đổi tên cột; hệ thống bên trong chỉ dùng **một** tên chuẩn.

```
Follow Up A          Follow Up B          Follow Up C
"ExFtyDate"          "EX FTY DATE"        "Ngày xuất"
      └──────────────────┴────────────────────┘
                         ↓
                  Canonical: ExFtyDate
                         ↓
                     DATABASE
```

### Bảng canonical field

| Canonical Field | Kiểu | Bắt buộc | Alias tiêu biểu |
|---|---|---|---|
| `ShipmentNo` | string | ✓ | No, NO., STT, Số NO, NO Folder, Folder No |
| `ExFtyDate` | date | ✓ | ExFtyDate, EX FTY DATE, Export Factory Date, Ngày xuất |
| `Buyer` | string | ✓ | BUYER, Customer Name, Khách hàng, Consignee |
| `JO` | string | ✓ | JO, JO No, Job Order, Số JO |
| `CustomsCode` | string | | Customs Code(GO), HS Code, Mã HS |
| `ShipMode` | string | | Ship, Ship Mode, Mode, By, MODE |
| `Destination` | string | | Destination, POD, Port of Discharge, Cảng đến |
| `OrderQty` | decimal | | OrderQty, Order Qty, SL đặt |
| `Invoice` | string | | EELInvoice, INV, Invoice No, Số Invoice |
| `FinalExportQty` | decimal | | FinalExportQty, Final Qty, PCS, SL xuất |
| `FinalCartonQty` | decimal | | Final CartonQty, Carton, CTN, Số thùng |
| `GrossWeight` | decimal | | Gross Weight (GW), G.W, Trọng lượng |
| `FinalCBM` | decimal | | Final CBM, CBM, M3, Final CBM (M3), Khối |
| `CDF` | string | | CDF, C/D, Customs Declaration, Tờ khai |
| `BL` | string | | BL, B/L, AWB, MBL, HBL, Vận đơn |
| `Price` | decimal | | Price, Unit Price, Giá, FOB Price |
| `DiscountPrice` | decimal | | Discount Price, Net Price, Giá sau CK |
| `BookingFlag` | flag | | Booking, Booked, BKG, Booking No, Đã book |
| `HoldFlag` | flag | | Hold, On Hold, Tạm dừng, Giữ hàng |
| `HoldReason` | string | | Hold Reason, Lý do, Lý do hold |
| `CancelFlag` | flag | | Cancel, Cancelled, Hủy |
| `PIC` | string | | PIC, Người xuất, Người phụ trách, Merchandiser |
| `ShippedFlag` | flag | | Shipped, Đã xuất, Completed |
| `ShippedQty` | decimal | | Shipped Qty, SL đã xuất, Loaded Qty |
| `Remark` | string | | Remark, Note, Ghi chú |

Định nghĩa đầy đủ: `config/field_mapping.yaml`.

### Mapping Confidence

| Cách khớp | Confidence | Kết quả |
|---|---|---|
| Trùng alias sau chuẩn hóa | `1.00` | Nhận ngay |
| Alias chiếm ≥ 60% header (`"Final CBM (M3)"` ⊃ `"Final CBM"`) | `0.90` | Nhận |
| Fuzzy ≥ `0.88` | điểm thật | Nhận |
| Fuzzy `0.65 – 0.88` | điểm thật | `EX010` — hỏi User |
| Dưới `0.65` | — | Bỏ qua |

Chuẩn hóa trước khi so: bỏ dấu tiếng Việt, bỏ ký tự không phải chữ/số, viết hoa.
→ `"Ngày xuất"`, `"NGAY XUAT"`, `"ngay-xuat"` là một.

**Bot không bao giờ tự map mơ hồ.** Header lạ chỉ bị bỏ qua, không bị ép vào field gần đúng.

---

## 4. Chuẩn hóa giá trị

### Ngày — canonical `YYYY-MM-DD`

Nhận được: `datetime`, `date`, số serial Excel, và các chuỗi `20/09/2026` · `20-09-2026` · `2026-09-20` · `20.9.2026` · `20-Sep-2026`.

Mặc định `day_first: true` (định dạng Việt Nam). Đổi trong `field_mapping.yaml → date_parsing`.

`31/02/2026` → `INVALID` → `EX001`. Bot **không** tự sửa thành `28/02`.

### Số

| Đầu vào | Kết quả | Trạng thái |
|---|---|---|
| `1,234.56` | `1234.56` | VALID |
| `1.234,56` | `1234.56` | VALID (tự nhận dạng EU) |
| `25 CBM` | `25.0` | VALID |
| `25?` | `25.0` | **NEED_REVIEW** → `EX004` |
| `khoảng 30` | `30.0` | NEED_REVIEW |

### Ô trống

Các giá trị được coi là **trống** khi kiểm tra CDF/BL:

```
""  "-"  "--"  "N/A"  "NA"  "NONE"  "TBA"  "TBC"  "PENDING"  "WAITING"  "CHƯA CÓ"  "?"
```

Nghĩa là `BL = "TBA"` vẫn là **YELLOW**, không phải GREEN. Danh sách chỉnh trong `field_mapping.yaml → value_maps.blank_equivalents`.

### Cờ Yes/No

`YES · Y · TRUE · 1 · X · V · OK · DONE · CÓ · BOOKED` → `True`
`NO · N · FALSE · 0 · - · NA · KHÔNG · CHƯA · PENDING` → `False`

Ô Booking ghi số booking (`BKG-77120`) → hiểu là **đã book**.

### Ship Mode

`AIR · Air · A · By Air · AIRFREIGHT` → `AIR`
`SEA · Sea · S · By Sea · OCEAN · Vessel` → `SEA`
`DHL · FEDEX · UPS` → `EXPRESS`

Giá trị lạ → `NEED_REVIEW`, không đoán.

### Invoice nhiều giá trị

```
"INV001, INV002 ; INV003"  →  ["INV001", "INV002", "INV003"]
```

Tách bằng `,` `;` `/` `|` hoặc xuống dòng. Lưu vào bảng `invoices` (quan hệ 1-n).

---

## 5. Shipment Identification

### Khóa

```
ShipmentKey = sha256(owner | ShipmentNo | JO | ExFtyDate)
```

| Tình huống | Kết quả |
|---|---|
| Cùng NO + JO, **khác** ExFtyDate | **2 shipment** (JO tách 2 đợt xuất) |
| Cùng NO + JO + ExFtyDate + Invoice | **Nghi duplicate** → `EX003` CRITICAL |
| Cùng NO + JO + ExFtyDate, khác Invoice | → `EX003` WARNING, chờ User xác nhận |
| Cùng dòng Excel, chỉ ExFtyDate đổi | **Cùng 1 shipment bị dời lịch** → đổi khóa, tính lại deadline |

Mã hiển thị: `SHP-20260912-0001`

### GroupKey — dùng để tính container

| `shipment_group` | Khóa nhóm | Dùng khi |
|---|---|---|
| `by_no_exfty` (mặc định) | Buyer + NO + ExFtyDate | Thông thường |
| `by_no` | Buyer + NO | Một folder = một lô, bất kể ngày |
| `by_exfty` | Buyer + ExFtyDate | Gom mọi JO cùng ngày (IKEA) |
| `manual` | từng dòng riêng | User tự gom |

---

## 6. Quan hệ dữ liệu

```
User → Follow Up File → Sheet → Shipment → JO
                                    ├── Invoice (1-n)
                                    ├── Customs Declaration (1-n)
                                    └── Container Allocation (theo GroupKey)
```

Ba trường hợp được hỗ trợ:

| # | Cấu trúc | `declaration_rule` |
|---|---|---|
| 1 | 1 lô · 1 INV · 1 tờ khai | `one_per_shipment` |
| 2 | 1 lô · nhiều INV · 1 tờ khai | `one_per_shipment` |
| 3 | 1 lô · nhiều INV · nhiều tờ khai | `one_per_invoice` |

Không xác định được → `user_review`, Bot **không đoán**.

---

## 7. Business Rule Mapping

### Buyer Rule (`config/buyer_rules.yaml`)

| Trường | Giá trị | Tác dụng |
|---|---|---|
| `co_required` | true/false | Điền cột `CO` của Weekly Planning |
| `co_form` | Form D / E / AK | Điền cột `CO FORM` |
| `container_rule` | `standard` · `user_review` · `forced:<TYPE>` · `lcl_only` · `fcl_only` | Cách tính container |
| `shipment_group` | xem mục 5 | Cách gom lô |
| `invoice_rule` | `single` · `multi` | Số invoice cho phép |
| `declaration_rule` | `one_per_shipment` · `one_per_invoice` · `user_review` | Nhóm tờ khai |
| `site` | TDV · TGV | Ghi đè quy tắc cơ sở |
| `lcl_threshold_cbm` | số | Ngưỡng LCL riêng |

Buyer chưa khai báo → `EX008`, Bot dùng rule mặc định và báo cho User.

Nhận diện buyer qua alias + fuzzy: `"ABC CO.,LTD"` → `ABC`.

### Container Engine

```
40HC = 63 CBM    ưu tiên 1
40DC = 53 CBM    ưu tiên 2
20ST = 23 CBM    ưu tiên 3

Luật:  Capacity >= Total CBM     — dư được phép, thiếu thì KHÔNG
```

Thuật toán `priority` (mặc định, đúng như SRS):

```
1. Lấp đầy bằng container lớn nhất:  n = floor(CBM / 63)
2. Phần dư → container NHỎ NHẤT mà vẫn chứa đủ
```

| CBM | Kết quả | Capacity | Dư |
|---|---|---|---|
| 10 | 1 × 20ST | 23 | 13 |
| 30 | 1 × 40DC | 53 | 23 |
| 60 | 1 × 40HC | 63 | 3 |
| **160** | **2 × 40HC + 1 × 40DC** | **179** | **19** |

Thuật toán `min_excess` (tùy chọn): tìm tổ hợp dư ít nhất — cho kết quả khác SRS, chỉ dùng khi công ty đổi quy tắc.

Kết quả lưu: `total_cbm`, `qty_40hc/40dc/20st`, `estimated_capacity`, `excess_capacity`, `allocation_status` (`AUTO` / `USER_CONFIRMED` / `NEED_REVIEW`), `allocation_source` (`BOT` / `USER`).

### Ship Mode / Booking Mode / Container Type

Ba khái niệm **tách biệt**:

| Ship Mode | Booking Mode | Type |
|---|---|---|
| AIR | AIR | `AC` |
| SEA + CBM ≤ ngưỡng | LCL | `CFS` |
| SEA + CBM > ngưỡng | FCL | `2x40HC + 1x40DC` |

Thứ tự quyết định: **Buyer Rule → Explicit Shipment Rule → Default Rule**.

### Status Engine

```
1. HOLD?    YES → RED         (ưu tiên cao nhất)
2. CANCEL?  YES → CANCELLED
3. SHIPPED? đủ → COMPLETED · một phần → PARTIAL
4. BOOKING? NO  → WHITE
5. CDF + BL đủ? NO → YELLOW · YES → GREEN
```

Mỗi kết quả kèm **trace** từng bước để trả lời "Why Yellow?".

---

## 8. Output Mapping

### F02 — WEEKLY PLANNING (T-7)

Khóa: `JO + EXFTY`

| Cột đích | Nguồn | Biến đổi |
|---|---|---|
| NO | `ShipmentNo` | text |
| EXFTY | `ExFtyDate` | date |
| BUYER | `Buyer` | Buyer Mapping |
| JO | `JO` | text |
| PCS | `FinalExportQty` | number |
| CBM | `FinalCBM` | decimal |
| MODE | `ShipMode` | AIR / SEA |
| TYPE | Container Engine | tính toán |
| PIC | `PIC` hoặc owner file | text |
| CO | Buyer Rule | Yes/No |
| CO FORM | Buyer Rule | text |
| STATUS | Status Engine | text |

### F03 — CUSTOM CODE (T-7)

Khóa: `JO` — *SRS mục 24: JO đã tồn tại → UPDATE hoặc SKIP*

| Cột đích | Nguồn |
|---|---|
| NO | `ShipmentNo` |
| JO | `JO` |
| BUYER | `Buyer` |
| CUSTOMS CODE | `CustomsCode` (để trống nếu Follow Up chưa có) |
| EXFTY | `ExFtyDate` |
| PIC | `PIC` |

Bot **không ghi đè** Customs Code User đã nhập tay.

### F04 — MM-DAILY PLANNING (T-7)

Khóa: `JO + DATE`. Cột: `DATE · NO · BUYER · JO · PCS · CTN · GW · CBM · MODE · TYPE · PIC · REMARK`

### F05 — LỊCH XUẤT HÀNG (T-1)

Khóa: `JO + EXFTY`. Cột: `NO · EXFTY · BUYER · JO · CBM · TYPE · PIC`

Hai sheet:

```
TDV = Cơ sở 2
TGV = Cơ sở 1
```

Chọn sheet theo thứ tự: **Buyer Rule `site` → `site_rules` trong config.yaml → `default_site`**.

### Quy tắc ghi chung

| | |
|---|---|
| Khớp khóa | **UPDATE** chỉ những ô thay đổi |
| Không khớp | **INSERT** vào dòng trống đầu tiên |
| Giá trị mới rỗng, ô cũ có dữ liệu | **SKIP** — không xóa dữ liệu User |
| File đổi version | **STOP** — không ghi đè (`EX007`) |
| File bị khóa | retry 30/60/120s → Pending Queue (`EX006`) |
| Trước mỗi lần ghi | Backup, giữ 20 bản |
| Sau khi ghi | Verify đọc lại xác nhận |

---

## 9. Validation & Exception

| Field | Validation |
|---|---|
| `ExFtyDate` | Ngày hợp lệ, bắt buộc |
| `JO` | Không rỗng |
| `Buyer` | Có trong Buyer Master (không có → `EX008`) |
| `FinalCBM` | Số ≥ 0 |
| `FinalExportQty` | Số ≥ 0 |
| `GrossWeight` | Số ≥ 0 |
| `CDF` / `BL` | Khác rỗng và khác blank-equivalent |

### Mã exception

| Mã | Ý nghĩa |
|---|---|
| `EX001` | Thiếu / sai ExFtyDate |
| `EX002` | Buyer chưa định nghĩa |
| `EX003` | Shipment nghi trùng lặp |
| `EX004` | CBM không hợp lệ |
| `EX005` | Sheet chưa xác định |
| `EX006` | File bị khóa / không đọc được |
| `EX007` | User đã thay đổi dữ liệu trong lúc Bot xử lý |
| `EX008` | Thiếu Buyer Rule |
| `EX009` | Không tính được container |
| `EX010` | Header Excel thay đổi |
| `EX011` | Trường bắt buộc bị trống |
| `EX012` | Không ghi được file đích |

Mỗi exception có: `severity`, `source_file`, `source_sheet`, `source_row`, `source_column`, `value`, `reason`, `suggested_action`, `dedup_key`.

### Data Confidence

```
VALID         dùng được ngay
NEED_REVIEW   Bot không chắc → Exception Center
INVALID       chắc chắn sai → không dùng để tính toán
```

---

## 10. Conflict & User Override

### Version Check

```
Bot đọc:      version = mtime_ns + size  →  A
Bot xử lý...
Bot sắp ghi:  version hiện tại           →  B

A ≠ B  →  STOP WRITE + EX007 + popup
```

Bot **không bao giờ** force overwrite.

### Effective Value

```
Effective = User Override  (nếu có)
          = Bot Value      (nếu không)
```

| Trường | Bot | User | Hiển thị |
|---|---|---|---|
| Status | GREEN | YELLOW | **YELLOW** |
| Container | 2×40HC | 1×40HC + 1×40DC | **User** |
| Màu dòng | GREEN | YELLOW | **YELLOW** |

Bot vẫn tính và lưu giá trị của nó trong `bot_status` để User đối chiếu.

---

## 11. Điều chỉnh theo file thật

Phần này **bắt buộc** làm trước khi chạy production.

**Bước 1 — chụp cấu trúc file thật**

```bash
python - <<'PY'
from openpyxl import load_workbook
for f in ["WEEKLY PLANNING 2026.xlsx", "Custom Code.xlsx",
          "MM-DAILY PLANNING SEP 2026 (GV).xlsx", "Lich xuat hang - IP (DV).xlsx"]:
    wb = load_workbook(f)
    print(f"\n{'='*60}\n{f}")
    for name in wb.sheetnames:
        ws = wb[name]
        print(f"  [{name}]  {ws.max_row} dòng × {ws.max_column} cột")
        for r in range(1, 4):
            print(f"    dòng {r}:", [c.value for c in ws[r][:15]])
    wb.close()
PY
```

**Bước 2 — đối chiếu và sửa**

| Phát hiện | Sửa ở đâu |
|---|---|
| Tên cột khác | `SPEC.columns` trong `src/booking_bot/outputs/<file>.py` |
| Header không ở dòng 1 | `header_row` trong `config.yaml` |
| Tên sheet khác | `sheet` trong `config.yaml` |
| Follow Up có cột mới | `aliases` trong `config/field_mapping.yaml` |
| TDV/TGV nằm trong một cột thay vì 2 sheet | Sửa `outputs/shipping_schedule.py` |

**Bước 3 — kiểm chứng**

```bash
python -m booking_bot check
python -m booking_bot scan --dry-run
```

Đọc kỹ báo cáo Dry Run: đúng số dòng, đúng cột, đúng giá trị rồi mới chạy thật.

**Bước 4 — bổ sung Buyer Rule thật**

```yaml
buyers:
  - code: "<mã buyer thật>"
    aliases: ["<các cách viết trong Follow Up>"]
    co_required: true
    co_form: "Form D"
    shipment_group: "by_no_exfty"
    declaration_rule: "one_per_shipment"
```

Thiếu buyer nào Bot sẽ báo `EX008` kèm tên chính xác — cứ chạy Dry Run rồi bổ sung dần.
