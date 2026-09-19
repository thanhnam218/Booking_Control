# Hướng dẫn phát triển

## Chuẩn bị môi trường

```bash
git clone https://github.com/<tài-khoản>/booking-deadline-bot.git
cd booking-deadline-bot

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt
pip install -e .

python -m booking_bot sample     # tạo dữ liệu demo
pytest -q                        # 141 test, chạy khoảng 20s
```

Trên Linux/macOS các thư viện Windows (`pystray`, `winotify`, `pywin32`) tự động bị bỏ qua; toàn bộ core, web và test vẫn chạy bình thường.

---

## Quy ước code

| | |
|---|---|
| Python | 3.10+, type hints đầy đủ, `from __future__ import annotations` |
| Lint | `ruff check src tests scripts` |
| Comment & docstring | Tiếng Việt không dấu — để không hỏng font trên console Windows CP1258 |
| Chuỗi hiển thị cho người dùng | Tiếng Việt có dấu được, vì đi qua UTF-8 |
| Phụ thuộc | `core/` **không** import `excel/`, `db/`, `web/` |

Lý do tách `core/`: toàn bộ logic nghiệp vụ là hàm thuần, test được mà không cần file Excel hay database. Đây là phần đáng tin cậy nhất của hệ thống và cũng là phần dễ thay đổi theo nghiệp vụ nhất.

---

## Chạy trong lúc phát triển

```bash
python -m booking_bot check                 # kiểm tra cấu hình + nhận diện sheet
python -m booking_bot scan --dry-run        # chạy pipeline, không ghi file
python -m booking_bot scan                  # ghi thật
python -m booking_bot serve --no-scheduler  # chỉ dashboard, không quét tự động
python -m booking_bot status                # số liệu nhanh trong terminal
```

Xem log chi tiết:

```yaml
# config/config.yaml
app:
  log_level: "DEBUG"
database:
  echo: true        # in SQL ra console
```

---

## Công thức thường dùng

### Thêm một trường mới từ Follow Up

**Bước 1** — khai báo trong `config/field_mapping.yaml`:

```yaml
  VesselName:
    type: string
    required: false
    description: "Ten tau / so chuyen bay"
    aliases: ["Vessel", "Vessel Name", "Ten tau", "Flight No", "Voyage"]
```

**Bước 2** — thêm cột vào `db/models.py`:

```python
vessel_name: Mapped[str] = mapped_column(String(120), default="")
```

**Bước 3** — gán trong `engine.py → _persist_shipments`:

```python
sh.vessel_name = clean_text(v.get("VesselName"))
```

Xong. Reader và mapper tự động xử lý phần còn lại — không phải sửa gì trong `excel/`.

> SQLite không tự thêm cột. Trong lúc phát triển: xóa `data/bookingbot.db`. Khi đã chạy thật: dùng Alembic hoặc `ALTER TABLE` thủ công.

### Thêm một file đích mới

Tạo `src/booking_bot/outputs/my_report.py`:

```python
from .base import AllocationLike, OutputSpec, ShipmentLike, num, type_for

SPEC = OutputSpec(
    role="my_report",
    display="Bao cao cua toi",
    columns=["NGAY", "JO", "BUYER", "CBM", "LOAI CONT"],
    key_columns=["JO", "NGAY"],       # khóa UPSERT
    milestone="T3",                   # T7 | T3 | T1
)


def build_record(shipment: ShipmentLike, alloc: AllocationLike | None = None,
                 air_type: str = "AC") -> dict:
    return {
        "NGAY": shipment.exfty_date,
        "JO": shipment.jo,
        "BUYER": shipment.buyer_code,
        "CBM": num(shipment.final_cbm),
        "LOAI CONT": type_for(shipment, alloc, air_type),
        "_shipment_code": shipment.shipment_code,   # tiền tố _ = không ghi ra Excel
    }
```

Đăng ký trong `outputs/__init__.py`:

```python
from . import my_report
REGISTRY = {..., my_report.SPEC.role: my_report}
```

Khai báo file trong `config.yaml`:

```yaml
  - id: "F06"
    role: "my_report"
    path: "./sample_data/BaoCao.xlsx"
    sheet: "DATA"
    write: true
```

Không phải sửa `engine.py` — pipeline tự lấy từ `REGISTRY`.

### Điều chỉnh theo file Excel thật của công ty

Đây là việc **bắt buộc** trước khi chạy production. Cột của 4 file đích hiện dùng bộ tên hợp lý theo SRS, chưa phải tên thật.

```bash
python - <<'PY'
from openpyxl import load_workbook
wb = load_workbook("duong/dan/WEEKLY PLANNING 2026.xlsx")
for name in wb.sheetnames:
    ws = wb[name]
    print(f"\n=== {name} ({ws.max_row} dòng) ===")
    for r in range(1, 6):
        print(r, [c.value for c in ws[r][:15]])
PY
```

Rồi sửa `SPEC.columns` trong module tương ứng cho khớp. So khớp dùng `norm_key` nên **không phân biệt hoa thường, dấu cách hay dấu tiếng Việt**: `EXFTY`, `Ex Fty`, `exfty` là một.

Nếu file thật có header không nằm ở dòng 1, đặt `header_row` trong `config.yaml`. Để trống thì Bot tự dò.

### Thêm trạng thái mới

Trong `core/status.py`:

```python
ALL_STATUSES = [..., "WAITING_DOC"]

def evaluate(values, *, blanks=None, strict_booking_column=False):
    ...
    # chèn vào đúng vị trí ưu tiên
    if booked and not cdf_filled and values.get("DocRequested"):
        return StatusResult("WAITING_DOC", trace, "Da yeu cau chung tu, dang cho", inferred)
```

Thêm màu vào `config.yaml`:

```yaml
status:
  colors:
    WAITING_DOC: "FF7030A0"
```

Nhớ thêm vào bậc thang trong `is_upgrade()` nếu trạng thái này nằm trong luồng tiến triển bình thường.

### Thêm kênh thông báo

```python
# notify/channels.py
class TeamsChannel(Channel):
    name = "teams"

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, payload: NotificationPayload) -> bool:
        import requests
        body = {"text": f"**{payload.title}**\n{payload.body}"}
        return requests.post(self.url, json=body, timeout=10).ok
```

Đăng ký trong `NotificationEngine.__init__`, rồi ánh xạ severity trong `config.yaml`:

```yaml
notification:
  severity_to_channel:
    CRITICAL: ["popup", "toast", "teams"]
```

---

## Viết test

Bộ test chia hai nhóm:

**Test thuần** (`test_container.py`, `test_status.py`, `test_deadline.py`, `test_normalize.py`, `test_shipment.py`) — không cần file, chạy trong mili-giây:

```python
def test_red_wins_over_green():
    r = evaluate({"HoldFlag": True, "BookingFlag": True, "CDF": "C", "BL": "B"})
    assert r.status == "RED"
```

**Test tích hợp** (`test_excel_safety.py`, `test_engine_e2e.py`, `test_mapping_detection.py`) — dùng fixture trong `conftest.py`:

| Fixture | Cho gì |
|---|---|
| `dictionary` | `FieldDictionary` từ `field_mapping.yaml` thật |
| `buyers` | `BuyerRegistry` từ `buyer_rules.example.yaml` |
| `workspace` | `Settings` trỏ vào bộ Excel demo sinh trong `tmp_path` |
| `engine` | `BookingBotEngine` với database riêng cho từng test |
| `sample_dir` | Thư mục chứa Excel demo |

```python
def test_my_feature(engine):
    result = engine.run_scan(today=dt.date(2026, 9, 18), deliver_notifications=False)
    assert result.shipments_total > 0
```

Mỗi test có `tmp_path` riêng → chạy song song được, không ảnh hưởng nhau.

**Test mới nên bám vào tiêu chí nghiệm thu SRS mục 73.** Xem bảng đối chiếu trong [SRS_TRACEABILITY.md](SRS_TRACEABILITY.md).

---

## Những điểm cần cẩn thận

### openpyxl và định dạng Excel

Mở file để **ghi** phải dùng `data_only=False`:

```python
load_workbook(path, data_only=False)   # ✓ giữ công thức
load_workbook(path, data_only=True)    # ✗ công thức bị thay bằng giá trị khi save
```

`data_only=True` chỉ dùng khi **đọc**.

openpyxl giữ được: công thức, định dạng ô, màu nền, độ rộng cột, freeze panes, conditional formatting cơ bản.

openpyxl **có thể mất**: biểu đồ, hình ảnh, pivot table, macro VBA, một số conditional formatting phức tạp, data validation nâng cao.

→ Nếu file đích chứa những thứ này, cân nhắc dùng `xlwings` (điều khiển Excel thật, cần Excel cài trên máy) cho riêng file đó. Backup trước mỗi lần ghi là lớp bảo vệ cuối cùng.

### Ngày tháng

Excel trả về `datetime`, code nghiệp vụ dùng `date`. Ranh giới chuyển đổi:

- `core/normalize.parse_date()` → luôn trả `date`
- `excel/writer._excel_safe()` → `date` → `datetime` khi ghi xuống
- `excel/writer.key_part()` → chuẩn hóa mọi kiểu ngày về `YYYYMMDD` để so khóa

Sai chỗ này sẽ tạo dòng trùng trong file đích. Có test riêng cho nó (`test_key_part_normalizes_dates`).

### Khóa nghiệp vụ

`key_columns` phải chọn sao cho **mỗi bản ghi là duy nhất**:

```
Weekly Planning    ["JO", "EXFTY"]   ← phải có EXFTY vì JO tách nhiều ngày
Custom Code        ["JO"]            ← HS code gắn với JO, không gắn với ngày
```

Chọn sai → hoặc mất dữ liệu (khóa quá rộng, ghi đè nhau), hoặc trùng dòng (khóa quá hẹp).

### Session và commit

`session_scope()` tự commit khi thoát, rollback khi có exception. **Không** gọi `session.commit()` bên trong.

Cần ID ngay sau khi `add()` thì gọi `session.flush()`.

---

## Đóng gói

```powershell
.\scripts\build_exe.ps1
```

Nếu file .exe báo thiếu module, thêm vào `--hidden-import` trong script. Các module hay bị bỏ sót: `uvicorn.protocols.*`, `apscheduler.triggers.*`, `pystray._win32`.

---

## CI

`.github/workflows/ci.yml` chạy trên Ubuntu + Windows, Python 3.11 + 3.12:

```
ruff check  →  pytest --cov  →  sample + check + scan --dry-run
```

Bước cuối là smoke test: đảm bảo toàn bộ pipeline chạy được từ đầu đến cuối trên máy trắng.

---

## Lộ trình còn lại

| Việc | Ghi chú |
|---|---|
| Alembic migration | Hiện dùng `create_all`; cần migration khi đã có dữ liệu production |
| Microsoft Graph API | Thay đường dẫn OneDrive sync bằng API — bỏ được ràng buộc phải sync về máy |
| Xác thực Dashboard | Hiện chỉ lắng nghe `127.0.0.1`; cần auth nếu mở ra mạng nội bộ |
| Outlook auto-apply | Đã có, mặc định tắt — cần chạy thử với hộp thư thật trước khi bật |
| Analytics | Tỷ lệ booking đúng hạn, thời gian trung bình từ book đến có BL |
