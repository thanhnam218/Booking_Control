# Đặc tả hệ thống — Booking Control

**Phiên bản 3.3.0** · Web nội bộ quản lý Export Shipment & Booking

Tài liệu này mô tả **hệ thống đang chạy thật**, không phải bản đề xuất. Mỗi quy tắc đều có
chỗ cấu hình và chỗ kiểm chứng bằng test. Đối chiếu với SRS gốc: [SRS_TRACEABILITY.md](SRS_TRACEABILITY.md).

| | |
|---|---|
| Nền tảng | Python 3.11+ · FastAPI · SQLAlchemy 2 · Jinja2 · APScheduler |
| Cơ sở dữ liệu | SQLite (mặc định) · SQL Server / PostgreSQL qua `database.url` |
| Giao diện | Web nội bộ, tiếng Việt, chạy trên Edge / Chrome |
| Quy mô thiết kế | 2.000+ lô/tháng · hàng trăm Buyer · hàng chục người dùng đồng thời |
| Mã nguồn | ~18.000 dòng Python · ~1.300 dòng JavaScript · 394 test |

---

## 1. Nguyên tắc nền

Bảy nguyên tắc này chi phối toàn bộ thiết kế. Vi phạm một trong số đó là lỗi nghiêm trọng,
không phải lựa chọn thiết kế.

| # | Nguyên tắc | Hệ quả cụ thể |
|---|---|---|
| 1 | **Database là trung tâm** | Excel và Outlook chỉ là nguồn vào/ra. Mọi trạng thái nghiệp vụ nằm trong DB. |
| 2 | **Người dùng > Tự động hóa** | Ô người dùng đã sửa tay thì Automation và email không ghi đè. Ghi rõ ai sửa, lúc nào. |
| 3 | **Không đủ bằng chứng thì không đoán** | Nhiều ứng viên ngang nhau → đưa vào hàng chờ duyệt, không tự chọn. |
| 4 | **Không xóa dữ liệu nghiệp vụ** | Hủy booking vẫn giữ bản ghi. Lô hàng chỉ lưu trữ, không xóa. |
| 5 | **Mọi thay đổi quan trọng đều có lịch sử** | Audit Log: trường nào, cũ→mới, ai, lúc nào, từ nguồn nào. |
| 6 | **Quy tắc nằm trong cấu hình** | Đổi Buyer/Forwarder/deadline/chứng từ = sửa file YAML, không sửa phần mềm. |
| 7 | **Trạng thái, Màu và Cảnh báo hạn là ba thứ khác nhau** | Xem §5. |

---

## 2. Đối tượng nghiệp vụ

Ba đối tượng **khác nhau**, không được coi là một:

```
SHIPMENT  ──►  EXPORT SHIPMENT GROUP  ──►  BOOKING
(một lô)       (gom để xuất chung)         (chỗ trên tàu)
```

### 2.1 Shipment (lô hàng)

Đơn vị cơ bản. **Khóa nhận dạng không phải JO** — một JO có thể tách thành nhiều ngày xuất.

```
ShipmentKey = hash(owner | JO | ExFtyDate)
ShipmentCode = SHP-YYYYMMDD-NNNN     (YYYYMMDD lấy từ EX-FACT DATE)
```

`shipment.key_includes_no: false` trong `config.yaml` — vì NO trong file thật là công thức
từ INVOICE, chưa có invoice thì NO trống, đưa NO vào khóa sẽ làm lô bị tách đôi khi invoice
xuất hiện.

Quan hệ: một Shipment → nhiều Invoice → nhiều Tờ khai → nhiều Container allocation.

### 2.2 Export Shipment Group (nhóm lô xuất)

Gom các lô cùng **BUYER + EX-FACT DATE + DEST + MODE**. Nhóm tự cộng tổng PCS, CTNS, G.W,
CBM, đếm JO, đếm GO và tính container dự kiến cho cả nhóm.

> **Nhóm không phải booking.** Forwarder vẫn có thể tách một nhóm thành nhiều booking.
> Hệ thống chỉ gom và đề xuất, không tự tạo booking.

Không giới hạn số JO/GO trong một nhóm. Lô đã xong hoặc đã hủy tự rời nhóm.
Lô thiếu CBM được đếm riêng — tổng ghi rõ "thiếu N lô", container chuyển `NEED_REVIEW`.

*Cài đặt:* `core/export_group.py` · bảng `export_shipment_groups` · trang `/groups`

### 2.3 Booking

Quan hệ **N:N** với Shipment qua bảng `booking_shipments`:

- **Split** — 1 lô → nhiều booking (Forwarder tách 1.000 pcs thành 600 + 400)
- **Gộp** — nhiều lô → 1 booking (xuất chung một chuyến)

Phân bổ chưa đủ số lượng → cảnh báo `EX018`, không tự làm tròn.

**Hủy không xóa.** Rebook tạo booking **mới** và trỏ ngược về booking cũ
(`original_booking_id` ↔ `replacement_booking_id`).

### 2.4 Quan hệ JO – GO

Một GO gồm nhiều JO. Không lưu GO dưới dạng text trong Shipment mà có bảng quan hệ riêng.

```
GO S26V02426
     ├── JO 26V02426KR01     nguồn IMPORT      (cùng dòng Excel — chắc chắn)
     └── JO 26V02426KR02     nguồn SYSTEM_RULE (cùng mã gốc — đề xuất, độ tin cậy 0.8)
```

Quy tắc tách mã gốc trong `config/field_mapping.yaml › jo_go`. Quan hệ do người dùng nối
(`USER`) không bao giờ bị Automation ghi đè.

*Cài đặt:* `core/jogo.py` · bảng `jo_go_links`

---

## 3. Nguồn dữ liệu

### 3.1 File Excel Follow Up (nguồn chính)

Mỗi nhân viên một file, mỗi sheet có thể là một Buyer, cấu trúc hơi khác nhau giữa các sheet.

Hệ thống **không hard-code cột A/B/C**. Nhận diện qua:

```
Sheet → đọc header → đối chiếu Data Dictionary → tính Confidence
     ≥ ngưỡng        → là Follow Up, quét
     trong khoảng xám → hỏi người dùng một lần, ghi nhớ
     < ngưỡng        → bỏ qua
```

Tên cột khác nhau (`ExFtyDate`, `EX FTY DATE`, `Ex Fty Date`) đều map về một trường chuẩn
qua `config/field_mapping.yaml`. Header đổi tên → cảnh báo `EX010`, không tự đoán.

**Ghi ngược vào file Excel là lossless**: chỉ đổi đúng ô cần đổi, giữ nguyên công thức,
external link, defined name, comment, định dạng in. Dùng `excel/xlsx_patch.py` thao tác
thẳng trên XML thay vì openpyxl (openpyxl làm mất giá trị công thức và defined name).

### 3.2 File Excel ngoài (Shipment schedule…)

File không theo khung 47 cột — ví dụ file máy xuất ra có 61 cột, nhiều ô `#REF!`, tiêu đề
lệch so với dữ liệu bên dưới.

Quy trình 4 bước, **không import thẳng**:

```
Tải file → Chọn cột → Xem trước → Nhập
```

Bảng chọn cột hiện **tất cả** cột kèm tỷ lệ ô có dữ liệu và giá trị mẫu — để người dùng
chọn theo dữ liệu thật chứ không theo tiêu đề. Hệ thống gợi ý ánh xạ nhưng **chỉ tự tick
khi chắc ≥ 90%**. Bắt buộc chọn JO, EX-FACT DATE, BUYER.

Bộ map lưu lại thành "profile" (bảng `import_profiles`) để lần sau chọn một phát.

*Cài đặt:* `excel/external.py` · `engine.import_external()` · trang `/import/external`

### 3.3 Outlook desktop

Đọc Inbox + Sent Items qua COM (pywin32). Chỉ đọc email mới kể từ mốc `outlook.last_sync`.
`emails.message_id` UNIQUE — quét lại cùng một email không xử lý hai lần.

Đọc được chữ trong PDF đính kèm (pdfplumber). PDF ảnh scan không có lớp chữ → báo
`NO_TEXT`, đưa người duyệt.

---

## 4. Ghép email với lô hàng

### 4.1 Thứ tự tín hiệu

| Dấu hiệu | Điểm mẫu |
|---|---|
| Conversation ID | 70 |
| Reply / In-Reply-To | 35 |
| Booking No | 70 |
| JO | 45 |
| Invoice | 35 |
| GO | 25 |
| PO | 20 |
| Forwarder đúng | 10 |
| ETD | 10 |
| Buyer / Vessel / Destination | 5 mỗi loại |

Ngưỡng trong `config/email_profiles.yaml › thresholds`: **≥ 85 tự ghép · 60–84 chờ duyệt ·
< 60 chưa ghép**. Hai ứng viên chênh nhau dưới `margin` (mặc định 10) → **không tự chọn**,
dù điểm cao.

### 4.2 Parser Profile theo từng Forwarder

Không dùng một parser cho tất cả. Mỗi Forwarder một bộ đọc; một Forwarder có nhiều nhân
viên, mỗi người viết một kiểu:

```
Email → domain/sender → Forwarder Profile → địa chỉ cụ thể → Contact Profile
```

Thứ tự ưu tiên khi lấy một trường: **mẫu của nhân viên > mẫu của Forwarder > alias chung**.

Mỗi profile khai được: `domains`, `senders`, `subject_must`, `field_aliases`, `patterns`
(regex có nhóm bắt giá trị `(?P<v>…)`), `attachment_pattern`, `contacts`.

Cấu hình sai — trường lạ, regex hỏng, thiếu nhóm bắt giá trị — bị **từ chối ngay lúc nạp**,
không đợi tới lúc xử lý email thật.

*Cài đặt:* `core/parser_profile.py` · `config/email_profiles.yaml › forwarders`

### 4.3 Email ≠ PDF

Email ghi ETD 02/10, PDF đính kèm ghi 04/10 → trạng thái `CONFLICT`, **hệ thống không tự
chọn**. Người dùng vào trang Email, xem cả hai giá trị cạnh nhau và bấm chọn.

Email báo thay đổi một ô mà người dùng đã chốt tay → giữ giá trị người dùng, ghi `EX020`.

---

## 5. Ba trục độc lập: Trạng thái · Màu · Cảnh báo hạn

Đây là điểm dễ nhầm nhất và là yêu cầu rõ ràng của SRS (BR-013).

| Trục | Là gì | Lưu ở đâu |
|---|---|---|
| **Trạng thái nghiệp vụ** | Lô đang ở đâu trong quy trình | `booking_status` + `hold_status` + `ship_status` |
| **Màu** | Lớp hiển thị | `effective_status` |
| **Cảnh báo hạn** | Sắp trễ hay chưa | `deadline_warning` |

Một dòng có thể **màu trắng mà vẫn viền đỏ**: trắng = chưa booking, viền đỏ = sắp hết hạn
để booking. Hai chuyện khác nhau.

### 5.1 Trạng thái nghiệp vụ

`NOT_BOOKED` · `BOOKED` · `ON_HOLD` · `DELAYED` · `PARTIALLY_SHIPPED` · `FULLY_SHIPPED` · `CANCELLED`

Ghép từ ba cột riêng, không suy từ màu. *Cài đặt:* `shipment_service.business_status()`

### 5.2 Màu

| Màu | Điều kiện |
|---|---|
| **Trắng** | Chưa booking |
| **Vàng** | Đã booking nhưng thiếu CDF hoặc BL |
| **Xanh lá** | Đã booking + có đủ CDF + BL (nghiệp vụ xong ~80%) |
| **Đỏ** | Đang hold — **ưu tiên cao nhất**, thắng mọi màu khác |
| **Xanh dương** | Đã xuất xong |
| **Cam** | Xuất một phần |
| **Xám** | Đã hủy |

Thứ tự xét: `HOLD? → BOOKING? → CDF+BL?`

Màu tùy chọn thêm khai trong `config.yaml › status.custom`, gán thông báo riêng được.

Người dùng tô màu tay trong file Excel cũng được đọc là quyết định của người dùng
(`status.read_user_colors`): tô đỏ = hold, tô vàng = yellow, màu lạ = Automation không đụng.

### 5.3 Cảnh báo hạn

```
Chưa có booking  AND  EX-FACT DATE − hôm nay < 14 ngày   →  viền đỏ
Chưa có booking  AND  đã qua ngày xuất                    →  viền đỏ đậm
```

Ngưỡng 14 lấy từ `deadlines.booking_window.end_days_before`, chỉnh được.
Tính từ **trạng thái nghiệp vụ** (đã book chưa), không tính từ màu.

Bảng đối chiếu (đã có test): D-20 bình thường · D-15 bình thường · **D-14 bình thường** ·
**D-13 cảnh báo** · D-7, D-3, D-1, D0 cảnh báo · D+1 quá hạn.

*Cài đặt:* `core/deadline.booking_warning()`

---

## 6. Container Engine

| Loại | Sức chứa |
|---|---|
| 20ST | 23 CBM |
| 40DC | 53 CBM |
| 40HC | 63 CBM |

**Quy tắc tuyệt đối:** tổng sức chứa **≥** tổng CBM. Được phép dư, **không được thiếu**.

Ưu tiên 40HC → 40DC → 20ST, nhưng kiểm tra tổ hợp hợp lệ chứ không greedy mù.

Ví dụ nghiệm thu: **160 CBM → 2×40HC + 1×40DC** = 179 CBM, dư 19.

- `MODE = AIR` → TYPE = `AC`
- `SEA` và CBM ≤ `lcl_threshold_cbm` (mặc định 15) → đề xuất LCL/CFS
- Người dùng tự chọn CONT TYPE trong file → tôn trọng, nhưng kiểm đủ tải; thiếu → `EX014`
- Thiếu CBM → `NEED_REVIEW`, **không đoán**

*Cấu hình:* `config.yaml › container` · *Cài đặt:* `core/container.py`

---

## 7. Deadline Engine

| Mốc | Việc | Cấu hình |
|---|---|---|
| D-20 | Bắt đầu nhắc booking | `deadlines.booking_window.start_days_before` |
| D-14 | Hạn chót booking — quá mốc này thì cảnh báo viền đỏ | `deadlines.booking_window.end_days_before` |
| D-7 | Ghi Weekly Planning + Custom Code / HS Code | `deadlines.milestones` |
| D-3 | Kiểm Inv · FinalQty · Carton · GW · CBM · Price · Discount | `deadlines.milestones` |
| D-1 | Lịch xuất TDV / TGV | `deadlines.milestones` |
| D0 | Ngày xuất | EX-FACT DATE |
| ETD+n | Hạn từng loại chứng từ (Draft Bill, BL, CO…) | `workflow.yaml › document_deadlines` |
| Mỗi 3 ngày | Nhắc lại lô đang Hold (chỉnh 1–7) | `deadlines.hold_reminder_days` |

Kiểm tra D-3 không chỉ có/không mà ba mức: `FOUND` / `MISSING` / `INVALID`.

**EX-FACT DATE đổi → tính lại toàn bộ deadline**, ghi Audit Log, và cảnh báo nếu dòng cũ
đã nằm trong file Planning (`EX007`).

Chống spam: cùng một lô + cùng loại thông báo chỉ bắn lại sau `dedup_hours` (mặc định 20h).

Khung giờ gửi thông báo mặc định **08:00–16:00**; ngoài khung thì dồn lại, bắn hôm sau.

---

## 8. Công thức

Một công thức được phân tích **một lần** thành cây, rồi Python (máy chủ), JavaScript (lưới)
và Excel xuất ra **cùng đọc cây đó** — nên ba nơi luôn cho cùng kết quả, kể cả cách làm
tròn 0,5 (Excel làm tròn ra xa số 0, Python mặc định làm tròn về số chẵn).

Trình duyệt **không `eval()`** gì cả — chỉ đi trên cây và tính.

### 8.1 Hai loại công thức

**Cột công thức cấu hình sẵn** — `config/followup_template.yaml › formulas`:

```yaml
DiscountPrice: { expr: "ROUND([Price]*0.95,2)" }
CdfAmount:     { expr: "[DiscountPrice]*[FinalExportQty]" }
```

**Công thức người dùng gõ thẳng vào ô** — như Excel, lưu trong `shipments.cell_formulas`:

```
=ROUND(W*0.95,2)                  tham chiếu bằng chữ cột, cùng dòng
=ROUND([Price]*0.95,2)            hoặc bằng tên cột
=IF([FinalCBM]>15,"FCL","LCL")
=SUM(N:Q)                         cộng nhiều cột trong CÙNG một dòng
```

### 8.2 Hàm hỗ trợ

`IF` `IFERROR` `AND` `OR` `NOT` · `SUM` `MIN` `MAX` `AVERAGE` `COUNT` `COUNTA` ·
`ROUND` `ROUNDUP` `ROUNDDOWN` `INT` `ABS` `MOD` `POWER` `SQRT` `CEILING` `FLOOR` ·
`LEN` `LEFT` `RIGHT` `MID` `TRIM` `UPPER` `LOWER` `CONCAT` `VALUE` ·
`ISBLANK` `ISNUMBER` · `TODAY` `DATE` `YEAR` `MONTH` `DAY` `DAYS`

Toán tử: `+ − × ÷ ^` · `&` (nối chuỗi) · `= <> < <= > >=`

### 8.3 Giới hạn có chủ đích

Công thức **chỉ tính trong một dòng**. Không hỗ trợ `SUM(A1:A100)` vì lưới sắp xếp lại được,
số dòng không có nghĩa ổn định. Tổng theo nhiều dòng xem ở thanh trạng thái dưới lưới.

Gõ số dòng (`W5`) bị từ chối kèm hướng dẫn dùng `W`.

*Cài đặt:* `followup/fx.py` (Python) · `web/static/fx.js` (JS) · bộ ca kiểm dùng chung
`tests/fx_cases.json`

---

## 9. Năm bảng xương sống

Toàn bộ quy tắc nghiệp vụ nằm trong năm file cấu hình. Buyer hay Forwarder đổi quy trình →
sửa file, **không sửa phần mềm**.

| # | Bảng | File | Nội dung |
|---|---|---|---|
| 1 | Buyer × Forwarder | `buyer_rules.yaml › forwarder_rules` | Buyer · Mode · Dest · Port · Hiệu lực · Forwarder · Dịch vụ · Quy tắc booking · Bộ đọc email |
| 2 | Email Pattern | `email_profiles.yaml › forwarders` | Domain · Sender · Subject · Alias · Mẫu regex · File đính kèm · Nhân viên |
| 3 | Deadline | `config.yaml › deadlines` | D-20 … D-1, hold reminder, khung giờ |
| 4 | Chứng từ | `workflow.yaml › document_*` | Mode/Buyer/Forwarder → chứng từ bắt buộc · hạn · ai làm |
| 5 | Quyền cột | `followup_template.yaml › columns` | 47 cột · nguồn · ai sửa được · tự động điền được không |

### Kiểm tra chéo

Trang `/admin/matrices` gộp cả năm và **đối chiếu chúng với nhau**. Đây là loại lỗi không
bao giờ báo ngay, chỉ âm thầm làm tự động hóa bỏ sót:

| Kiểm tra | Mức |
|---|---|
| Rule trỏ tới bộ đọc email không tồn tại | Lỗi |
| Bộ đọc email không khai domain lẫn sender → không bao giờ khớp email nào | Lỗi |
| Chứng từ bị yêu cầu nhưng không có trong danh mục | Lỗi |
| Forwarder có rule nhưng chưa có bộ đọc email | Cảnh báo |
| Nhiều rule cùng khớp một Buyer (phải hỏi, không tự chọn) | Cảnh báo |
| Chứng từ bắt buộc nhưng chưa ghi ai làm | Cảnh báo |
| Cột nguồn OUTLOOK nhưng không cho tự động điền | Cảnh báo |

*Cài đặt:* `services/matrices.py` (tách khỏi tầng web để test được)

---

## 10. Quy trình B1 → B15.1

Mỗi bước là một Workflow Task có hạn riêng, người phụ trách, chứng từ bắt buộc.

| Bước | Việc | Mốc | Ghi chú |
|---|---|---|---|
| B1 | Shipment Schedule - tạo lô | `D-20` | tự xong khi có bằng chứng |
| B2 | Carton box / GO → gửi yêu cầu booking | `D-14` | tự xong khi có bằng chứng |
| B3 | Yêu cầu SSC Invoice + Final PKL | `D-3` | cần INVOICE, PKL · tự xong khi có bằng chứng |
| B3.1 | Kéo container | `D-1` | chỉ SEA |
| B4 | PKL / SI / INV / DO DIA + kiểm HS Code | `D-2` | cần SI · tự xong khi có bằng chứng |
| B5 | Gửi chứng từ cho Forwarder | `D-2` | cần VGM · chỉ SEA |
| B6 | Chứng từ VVMV | `D-1` | — |
| B6.1 | E-Shipment | `D-1` | — |
| B6.2 | Báo PPC / Leader / Kho | `D-1` | — |
| B7 | Tờ khai xuất khẩu + E-Shipment | `D0` | cần EXPORT_DECL · tự xong khi có bằng chứng |
| B8 | BK | `D0` | — |
| B9 | Draft Bill / FCR - đối chiếu | `ETD+1` | cần DRAFT_BILL |
| B10 | Xin CO | `ETD+2` | chỉ khi Buyer yêu cầu CO |
| B11 | Draft CO - duyệt | `ETD+3` | cần CO · chỉ khi Buyer yêu cầu CO |
| B12 | PNK | `D+1` | — |
| B13 | ATD + Bill | `ETD+1` | tự xong khi có bằng chứng |
| B14 | Final CO | `ETD+10` | cần FINAL_CO · chỉ khi Buyer yêu cầu CO · tự xong khi có bằng chứng |
| B15 | Surrender Bill - kiểm tra | `ETD+5` | cần BL |
| B15.1 | Debit Note | `ETD+15` | — |

Bước trước chưa xong → bước sau `BLOCKED`. Thiếu chứng từ bắt buộc → không cho hoàn thành.
Hệ thống **tự hoàn thành** khi đã có bằng chứng (lô được tạo, booking đã gửi, chứng từ đã
nộp, tàu đã chạy) — người dùng không phải bấm tay việc mà hệ thống tự biết.

*Cấu hình:* `config/workflow.yaml › steps`

---

## 11. Chứng từ

16 loại: Booking / Booking Confirmation · Invoice · Packing List (Final PKL) · Shipping Instruction (SI) · VGM · Container Load Plan (CLP) · DO DIA · E-Shipment · Tờ khai xuất khẩu · BK · Draft Bill / FCR · CO (Draft) · Final CO · PNK · Final Bill / Surrender Bill · Debit Note.

Chứng từ bắt buộc phụ thuộc **Mode + Buyer + Forwarder**. Mỗi loại có hạn riêng (mốc + số
ngày) và **người chịu trách nhiệm**.

**Có version, không ghi đè.** File lưu ngoài database theo cấu trúc
`Năm/Buyer/NO/LOẠI/tên_vN.pdf`, DB chỉ giữ metadata + checksum SHA-256.

---

## 12. Phân quyền

Năm vai trò, 29 quyền, kiểm tra ở **backend** cho mọi thao tác.

| Vai trò | Phạm vi |
|---|---|
| **Quản trị** (admin) | Toàn quyền, kể cả cấu hình và người dùng |
| **Quản lý** (manager) | Xem tất cả, phân công, duyệt, xử lý exception, nhập Excel |
| **Nhân viên** (user) | Chỉ lô được giao (chính hoặc dự phòng); sửa, tạo booking, nộp chứng từ |
| **Chỉ xem** (viewer) | Xem tất cả, không sửa |
| **Kiểm soát** (auditor) | Chỉ đọc + Audit Log |

Nhân viên **không thấy** lô ngoài phạm vi — truy vấn bị chặn ở tầng dữ liệu
(`visible_stmt`), không phải chỉ ẩn trên giao diện. Mở lô ngoài quyền → báo "không tìm thấy",
không tiết lộ lô đó có tồn tại hay không.

### Quyền từng cột (Field Ownership)

Mỗi cột trong 47 cột khai riêng:

- `user: edit` — nhân viên sửa được
- `user: review` — sửa được nhưng tạo mục "Quản lý xem lại" (`EX016`)
- `user: view` — chỉ xem
- `auto: true` — tự động hóa được phép điền **khi người dùng chưa sửa tay**

---

## 13. Nhiều người dùng chung

| Cơ chế | Cách làm |
|---|---|
| Khóa lạc quan | Mỗi dòng có `version`. Gửi bản cũ → máy chủ trả lại dòng mới, **không ghi đè**. Áp dụng cho cả xung đột người ↔ Automation. |
| Tự đồng bộ | Lưới hỏi máy chủ mỗi 7 giây "dòng nào vừa đổi", kéo về và tô sáng. Dòng đang gõ dở không bị đụng. |
| Báo ai đang sửa | Mỗi 11 giây gửi nhịp; dòng người khác đang sửa có vạch tím và dấu ✎. |
| SQLite chịu tải | WAL + `busy_timeout=15s` + `synchronous=NORMAL` — nhiều người ghi cùng lúc không gặp *database is locked*. |

Danh sách người đang xem nằm trong bộ nhớ tiến trình máy chủ (mất khi khởi động lại,
không chia sẻ giữa nhiều worker) — đủ cho một phòng vài chục người.

---

## 14. An toàn khi ghi file Excel

| Tình huống | Xử lý |
|---|---|
| Người dùng vừa sửa file | Phát hiện qua data hash → **dừng ghi**, báo `EX007` |
| File đang mở / bị khóa | Thử lại 30s → 60s → 120s → hàng chờ, **không ép ghi** |
| Ghi nhiều lần | UPSERT theo khóa bản ghi, không tạo dòng trùng |
| Trước khi ghi | Backup có version, giữ 20 bản, khôi phục được |
| Sau khi ghi | Đọc lại xác nhận đã ghi đúng |
| Ô công thức ở file đích | **Không bao giờ ghi đè**; dòng mới thì kéo công thức xuống |

---

## 15. Exception Center

Tất cả chỗ hệ thống không chắc chắn đều vào đây, có mã để tra:

| Mã | Ý nghĩa |
|---|---|
| EX001 | Thiếu / sai EX-FACT DATE |
| EX003 | Nghi trùng lô |
| EX004 | CBM không hợp lệ |
| EX005 | Sheet chưa xác định — cần người dùng xác nhận |
| EX006 | File bị khóa |
| EX007 | Người dùng đã sửa trong lúc Automation xử lý |
| EX010 | Header Excel đổi — mapping không khớp |
| EX013 | Công thức Excel bị gõ đè / thiếu |
| EX014 | CONT TYPE người dùng chọn không đủ chỗ |
| EX015 | Excel khác giá trị người dùng đã sửa trên web (giữ giá trị web) |
| EX016 | Nhân viên sửa cột cần Quản lý xem lại |
| EX017 | Forwarder cần xác nhận (nhiều rule khớp) |
| EX018 | Booking chưa phân bổ đủ số lượng |
| EX019 | BOOKING NO đổi — cần xác nhận rebook |
| EX020 | Email báo đổi ô người dùng đã chốt tay |

### Hàng đợi "việc cần xử lý"

Đầu Trang tổng quan hiện **"⚠ N việc cần bạn xử lý"** thay vì bắt rà từng lô. Tám loại:
booking quá hạn · email ≠ PDF · thiếu CBM · booking bị hủy · cần rebook · thiếu chứng từ ·
phân bổ chưa đủ · ngày xuất vừa đổi. Bấm vào ra đúng danh sách.

---

## 16. Cơ sở dữ liệu

34 bảng. Nhóm chính:

| Nhóm | Bảng |
|---|---|
| Lô hàng | `shipments` `invoices` `customs_declarations` `container_allocations` `shipment_events` `user_overrides` |
| Nhóm lô | `export_shipment_groups` `jo_go_links` |
| Booking | `bookings` `booking_shipments` `shipment_executions` |
| Email | `emails` `email_attachments` `conversation_links` |
| Chứng từ | `documents` `document_versions` |
| Quy trình | `workflow_tasks` `notifications` |
| Nguồn dữ liệu | `source_files` `sheet_maps` `column_mappings` `file_versions` `pending_writes` `import_profiles` |
| Vận hành | `scan_runs` `job_runs` `exceptions` `audit_logs` `planning_records` |
| Người dùng | `users` `web_sessions` `assignment_history` `buyers` `system_settings` |

Index cho: NO · JO · GO · PO · Buyer · Booking No · ETD · EX-FACT · người phụ trách ·
thời điểm cập nhật · (đang hoạt động + đã lưu trữ + EX-FACT).

Nâng cấp phiên bản **không mất dữ liệu**: tự thêm cột mới với giá trị mặc định, tự tạo
index mới, đổi tên cột cũ từ v1.

---

## 17. Giới hạn đã biết

Ghi rõ để không ai kỳ vọng sai:

| Giới hạn | Lý do |
|---|---|
| Công thức chỉ tính trong một dòng | Lưới sắp xếp lại được, số dòng không ổn định |
| Danh sách người đang xem mất khi khởi động lại máy chủ | Nằm trong bộ nhớ tiến trình, không dùng Redis |
| Outlook cần bản desktop + pywin32 trên máy chủ | Chưa dùng Microsoft Graph |
| PDF ảnh scan không đọc được chữ | Chưa có OCR; báo `NO_TEXT` và đưa người duyệt |
| SQLite chỉ cho một người ghi tại một thời điểm | Đủ cho vài chục người; đông hơn thì đổi `database.url` sang SQL Server / PostgreSQL |
| Hệ thống không tự gửi email | Theo yêu cầu SRS — chỉ đọc, không gửi thay người dùng |

---

## 18. Tiêu chí nghiệm thu

Những điều kiện phải đúng mới coi là đạt. Tất cả đều có test tự động.

| Tiêu chí | Test |
|---|---|
| Không ghi đè dữ liệu người dùng | `test_bot_never_erases_user_value_with_blank` |
| Không tạo dòng trùng khi chạy nhiều lần | `test_insert_then_no_duplicate_on_rerun` |
| Không mất dữ liệu khi file bị khóa | `test_locked_file_is_detected_and_not_forced` |
| Nhận diện được sheet Follow Up | `test_sample_workbooks_detected` |
| Map được các tên cột khác nhau | `test_alias_variants_map_to_same_field` |
| Trắng/Vàng/Xanh/Đỏ đúng quy tắc | `test_status.py` |
| Đỏ luôn thắng khi Hold | `test_red_has_highest_priority` |
| Container không bao giờ thiếu sức chứa | `test_capacity_never_short` |
| 160 CBM → 2×40HC + 1×40DC | `test_srs_examples` |
| D-14 bình thường, D-13 cảnh báo | `test_booking_warning_follows_srs_table` |
| Nhóm 25 JO · 3 GO · 149,10 CBM | `test_group_totals_match_srs_example` |
| Công thức: Python = JavaScript = Excel | `tests/fx_cases.json` (93 test) |
| Người dùng override thắng Automation | `test_user_override_survives_next_scan` |
| Có Audit Log cho mọi thay đổi | `test_audit_log_records_bot_writes` |
| Nâng cấp không mất dữ liệu | `test_v1_database_is_upgraded_in_place` |

**394 test**, chạy được trên cả Windows và Ubuntu.

---

## 19. Tài liệu liên quan

| Tài liệu | Nội dung |
|---|---|
| [INSTALL.md](INSTALL.md) | Cài đặt, cấu hình, chạy, khắc phục sự cố |
| [USER_GUIDE.md](USER_GUIDE.md) | Hướng dẫn sử dụng theo công việc hằng ngày |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kiến trúc, quyết định thiết kế |
| [DATA_MAPPING.md](DATA_MAPPING.md) | Ánh xạ cột Excel ↔ trường dữ liệu |
| [SRS_TRACEABILITY.md](SRS_TRACEABILITY.md) | Đối chiếu từng mục SRS ↔ code ↔ test |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | Dành cho người sửa mã nguồn |
