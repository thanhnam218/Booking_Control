# Hướng dẫn phát triển

## Chuẩn bị môi trường

```bash
git clone https://github.com/<tài-khoản>/booking-control.git
cd booking-control

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt
pip install -e .

python -m booking_control sample     # tạo dữ liệu demo
pytest -q                        # ~230 test, khoảng 3 phút
```

Trên Linux/macOS thư viện Windows (`pywin32` — chỉ dùng cho Outlook) tự động bị bỏ qua; toàn bộ core, web và test vẫn chạy bình thường.

Tạo tài khoản để đăng nhập web khi phát triển:

```bash
python -m booking_control user add admin --role admin
python -m booking_control run --no-scheduler        # http://127.0.0.1:8765
```

---

## Quy ước code

| | |
|---|---|
| Python | 3.10+, type hints đầy đủ, `from __future__ import annotations` |
| Lint | `ruff check src tests scripts` |
| Comment & docstring | Tiếng Việt không dấu — để không hỏng font trên console Windows CP1258 |
| Chuỗi hiển thị cho người dùng | Tiếng Việt có dấu được, vì đi qua UTF-8 |
| Phụ thuộc | `core/` **không** import `excel/`, `db/`, `web/`; `services/` không import FastAPI |
| Quyền | Luôn kiểm ở **backend** (`web/auth.py` + `services/shipment_service.py`); template chỉ ẩn nút |
| Ghi DB từ web | Qua `services/` — không viết logic nghiệp vụ trong route |

Lý do tách `core/`: toàn bộ logic nghiệp vụ là hàm thuần, test được mà không cần file Excel hay database. Đây là phần đáng tin cậy nhất của hệ thống và cũng là phần dễ thay đổi theo nghiệp vụ nhất.

---

## Chạy trong lúc phát triển

```bash
python -m booking_control check                 # kiểm tra cấu hình + nhận diện sheet
python -m booking_control scan --dry-run        # chạy pipeline, không ghi file
python -m booking_control scan                  # ghi thật
python -m booking_control run --no-scheduler    # chỉ web, không quét tự động
python -m booking_control status                # số liệu nhanh trong terminal
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

## Bản đồ mã nguồn v2

```
src/booking_control/
├── core/                 Logic thuần: status, container, deadline, shipment key, forwarder rule
├── excel/                Đọc (openpyxl) + ghi lossless (lxml) file Excel
├── followup/template.py  Khung 47 cột + Field Ownership + công thức (Python / JS / Excel)
├── services/
│   ├── shipment_service.py   Phạm vi theo quyền, sửa ô, tạo lô, lưu trữ, xử lý EX015
│   ├── assignment.py         Giao / giao lại / auto-assign / workload
│   └── excel_export.py       Xuất 1 Buyer = 1 sheet, 47 cột + cột hệ thống
├── engine.py             AutomationEngine: pipeline quét + apply_values() dùng chung
├── scheduler.py          AutomationScheduler: các job + JobRun (retry)
├── web/
│   ├── auth.py           Mật khẩu, phiên, CSRF, PERMISSIONS, Principal
│   ├── deps.py           auth_guard (dependency toàn cục), render(), require()
│   ├── routes_auth.py    /login /setup /account
│   ├── routes_main.py    Tổng quan, lô hàng, exception, thông báo, audit
│   ├── routes_grid.py    /followup + /api/followup/* + /export + /import
│   ├── routes_admin.py   Người dùng, phân công, automation, mapping, cài đặt, backup
│   ├── templates/        Jinja2
│   └── static/           app.js, followup.js, *.css, vendor/ (Jspreadsheet CE, jSuites - MIT)
└── cli.py                bookingctl run | user | scan | check | ...
```

Luồng một lần sửa ô trên lưới:

```
followup.js (onafterchanges, gom 0,6s) ──POST /api/followup/save {code, v, values}──▶ routes_grid
   └─ shipment_service.apply_edits():
        kiểm quyền dòng (can_edit) + quyền cột (Column.can_edit) + kiểu dữ liệu (template.parse)
        → field_src[field] = USER/MANAGER  → recompute_formulas() (chỉ cột công thức "sống")
        → engine.apply_values() (cùng hàm với Excel import) → version += 1 → Audit FIELD_CHANGED
        → EX016 nếu cột "review" → engine.recompute() (trạng thái cả nhóm, PARTIAL)
   ◀── dòng chuẩn (cells, v, status, manual) ── followup.js cập nhật ô + màu dòng
```

---

## Công thức thường dùng (v2)

### Đổi quyền sửa một cột

`config/followup_template.yaml`, sửa `user:` (`edit` / `review` / `view`) hoặc `manager:` (`edit` / `view`) của cột đó. Không cần sửa code; khởi động lại là lưới + API + test quyền cùng đổi.

### Thêm một cột công thức

```yaml
formulas:
  NetAmount: { expr: "[TsnAmount]-[CdfAmount]" }     # chỉ số, + - * /, ( ), ROUND(x,n), [Trường]
columns:
  - { header: "NET", field: NetAmount, type: number, width: 10, user: view, manager: view, source: FORMULA }
```

Biểu thức được `ast` kiểm tra lúc nạp; cùng một định nghĩa sinh ra hàm Python, mã JS cho lưới và công thức Excel (`=AY2-Y2`) khi xuất. Nhớ thêm alias cho trường mới trong `field_mapping.yaml` nếu file Excel có cột đó.

> Thêm cột vào giữa 47 cột sẽ làm file xuất lệch khỏi Follow Up gốc. Cột mở rộng nên thêm vào `system_columns` (xuất ở cuối) hoặc cuối danh sách `columns`.

### Thêm quyền / vai trò

`web/auth.py → PERMISSIONS`: thêm khóa quyền và tập vai trò. Trong route: `require(request, "ten.quyen")`; trong template: `{% if me.can('ten.quyen') %}`. Vai trò mới (vd `customs`) thêm vào `ROLES` + `ROLE_LABELS`, và vào `followup/template.py → ROLES` nếu cần quyền cột riêng.

### Thêm một job Automation

```python
# scheduler.py - trong start()
self._sched.add_job(lambda: self.run_job("weekly_report", self._weekly_report),
                    CronTrigger(day_of_week="fri", hour=11), id="weekly_report", **opts)
```

`run_job()` tự ghi `JobRun` (bắt đầu, kết thúc, trạng thái, lỗi, số lần thử lại theo `jobs.max_retries`). Thêm tên + mô tả vào `JOB_TITLES` để hiện trên trang Automation, và vào `fns` trong `routes_admin.run_job` nếu muốn có nút *Chạy ngay*.

### API cho công cụ khác

Mọi API cần phiên đăng nhập (cookie) + header `X-CSRF-Token` cho thao tác ghi:

| Method | Đường dẫn | Việc |
|---|---|---|
| GET | `/api/followup/rows?buyer=&status=&assigned=&date_from=&date_to=&q=` | Dòng lưới trong phạm vi quyền |
| POST | `/api/followup/save` `{changes:[{code, v, values:{Field: "giá trị"}}]}` | Sửa ô (trả `rows`, `errors`, `conflicts`) |
| POST | `/api/followup/create` `{values:{JO, ExFtyDate, Buyer, ...}, assign_to}` | Tạo lô |
| POST | `/api/followup/assign` `{codes:[...] \| by_filter:{...}, to_user, kind}` | Giao lô |
| POST | `/api/followup/archive` `{codes, archived}` | Lưu trữ |
| GET | `/export/followup.xlsx?scope=mine\|filter&...` | Xuất Excel |
| GET | `/api/notifications/unread?since=ID` | Chuông thông báo |
| GET | `/api/status` | Trạng thái Automation + đếm theo màu |


---

## Công thức thường dùng (pipeline Excel)

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

> Cột mới được **tự thêm** vào database đang chạy khi khởi động (`db/session._add_missing_columns`, chỉ ADD COLUMN). Đổi kiểu / xóa cột thì cần Alembic.

### Thêm một file đích mới

Tạo `src/booking_control/outputs/my_report.py`:

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

Nếu file thật có header không nằm ở dòng 1, đặt `header_row` trong `config.yaml`. Để trống thì Automation tự dò.

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
    CRITICAL: ["web", "email", "teams"]
```

---

## Viết test

Test web dùng `fastapi.testclient.TestClient` trên bộ dữ liệu demo — xem `tests/test_web_v2.py` (hàm `login(app, user)` lấy sẵn CSRF). Mỗi quy tắc phân quyền nên có một test *bị từ chối* bên cạnh test *được phép*.

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
| `engine` | `AutomationEngine` với database riêng cho từng test |
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

### Ghi Excel: KHÔNG dùng openpyxl để lưu

openpyxl chỉ được dùng để **đọc**. Mọi thao tác ghi đi qua `excel/xlsx_patch.py`:

```python
from booking_control.excel.xlsx_patch import XlsxPackage

pkg = XlsxPackage("Follow Up.xlsx")
sh = pkg.sheet("KRB+KRX(KR)")
sh.fill_row(5, "FF92D050", cols=range(2, 48))   # tô màu, không đụng giá trị
sh.set_value(20, 7, "26V09999KR01")              # False nếu ô là công thức
sh.copy_formulas(from_row=19, to_row=20, cols=[1, 24, 25, 26])
pkg.save("Follow Up.tmp.xlsx")                   # chỉ part bị sửa được serialize lại
```

Lý do (đo trên file thật): openpyxl lưu lại làm **mất giá trị tính sẵn của mọi công thức** (cột NO đọc lại = `None`), mất defined name, thiết lập in, calcChain, viết lại 33 external link. Chi tiết: [ARCHITECTURE.md § 5.7](ARCHITECTURE.md#57-ghi-excel-lossless).

Chạy `bookingctl check` để xem từng file có gì mà openpyxl sẽ làm mất.

Khi test cần file có công thức **kèm giá trị tính sẵn** (giống Excel lưu): tạo bằng openpyxl rồi gọi `SheetPatch.set_cached_value()` — xem `scripts/make_sample_data._add_cached_values`.

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

Nếu file .exe báo thiếu module, thêm vào `--hidden-import` trong script. Các module hay bị bỏ sót: `uvicorn.protocols.*`, `apscheduler.triggers.*`. Thư mục `web/static` (gồm `vendor/`) phải được `--add-data`.

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
| SSO / Active Directory | Hiện dùng tài khoản riêng (PBKDF2); có thể thay `web/auth.py` bằng LDAP/Entra ID |
| Document / Task / Email Matrix | SRS v2 mục Document Matrix, Email Pattern Matrix — chưa làm, đã có chỗ trong `services/` |
| Hàng đợi job riêng | Khi nhiều máy chủ: chuyển `run_job` sang Celery/RQ (chạy trên Linux) |
| Outlook auto-apply | Đã có, mặc định tắt — cần chạy thử với hộp thư thật trước khi bật |
| Analytics | Tỷ lệ booking đúng hạn, thời gian trung bình từ book đến có BL |
