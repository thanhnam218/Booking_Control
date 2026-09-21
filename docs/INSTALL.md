# Hướng dẫn cài đặt — Windows

Tài liệu này đi từ máy trắng đến Bot chạy ngầm hằng ngày. Làm theo đúng thứ tự.

---

## 1. Cài Python

1. Tải Python **3.11 trở lên**: <https://www.python.org/downloads/windows/>
2. Khi chạy file cài, **tích vào ô `Add python.exe to PATH`** ở màn hình đầu tiên. Bỏ qua bước này là nguyên nhân số 1 khiến các bước sau báo lỗi.
3. Kiểm tra — mở **PowerShell** (nút Start → gõ `powershell`):

```powershell
python --version
```

Phải ra `Python 3.11.x` hoặc cao hơn. Nếu báo *"không nhận diện được lệnh"*, cài lại và nhớ tích ô PATH.

---

## 2. Tải mã nguồn

**Cách A — có Git:**

```powershell
cd C:\Tools
git clone https://github.com/<tài-khoản>/booking-deadline-bot.git
cd booking-deadline-bot
```

**Cách B — không có Git:** tải file ZIP trên GitHub (`Code` → `Download ZIP`), giải nén vào `C:\Tools\booking-deadline-bot`, rồi:

```powershell
cd C:\Tools\booking-deadline-bot
```

> Đặt ở thư mục **không đồng bộ OneDrive** (ví dụ `C:\Tools\`). Để mã nguồn trong OneDrive dễ gây xung đột file khi Bot ghi database.

---

## 3. Chạy script cài đặt

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1
```

Script tự làm 4 việc: tạo môi trường ảo `.venv` → cài thư viện → tạo `config\config.yaml` → tạo bộ dữ liệu demo.

Mất khoảng 2–3 phút.

> `Set-ExecutionPolicy -Scope Process` chỉ có tác dụng trong cửa sổ PowerShell đang mở, không thay đổi cài đặt bảo mật của máy.

---

## 4. Chạy thử với dữ liệu demo

Trước khi đụng vào file thật, kiểm tra Bot hoạt động đúng:

```powershell
.\scripts\run.bat check
.\scripts\run.bat scan --dry-run
```

Kết quả mong đợi: 3 file, 6 sheet Follow Up, ~37 lô hàng, đủ các màu trạng thái. File `FollowUp_UserC_CauTrucThat.xlsx` có đúng cấu trúc 47 cột của file thật.

---

## 5. Trỏ vào file Excel thật

Mở `config\config.yaml` bằng Notepad hoặc VS Code. Sửa mục `files:`:

```yaml
files:
  - id: "F01-A"
    role: "follow_up"
    display_name: "Follow Up - Nguyen Van A"
    path: "C:/Users/nam/OneDrive - Cong ty/Logistics/Follow Up - Nam.xlsx"
    owner: "user_a"
    write: true          # cho phép Bot tô màu dòng

  - id: "F02"
    role: "weekly_planning"
    path: "C:/Users/nam/OneDrive - Cong ty/Chung/WEEKLY PLANNING 2026.xlsx"
    sheet: "2026"
    header_row: 1
    write: true
```

**Quy tắc viết đường dẫn:**

| | |
|---|---|
| Dùng `/` hoặc `\\` | `C:/Users/...` hoặc `C:\\Users\\...` — **một** dấu `\` sẽ lỗi |
| Có dấu cách | vẫn viết bình thường, YAML chấp nhận khi có dấu `"` bao ngoài |
| Có dấu tiếng Việt | được, file `config.yaml` lưu UTF-8 |

### File Follow Up theo cấu trúc thật (47 cột)

```yaml
  - id: "F01-NAM"
    role: "follow_up"
    path: "C:/Users/nam/OneDrive - Cong ty/Follow Up - Nam.xlsx"
    owner: "nam"
    write: true
    color_skip_columns: ["A"]    # cột NO là công thức, đang tô vàng -> Bot không tô đè
```

Rồi thêm **buyer thật** vào `config/buyer_rules.yaml` (file này không đưa lên GitHub). Buyer nào thiếu, lần quét đầu Bot sẽ báo `EX008` kèm đúng tên — ví dụ:

```yaml
  - code: "MA_BUYER"            # thay bằng mã buyer thật trong cột BUYER
    canonical_name: "MA_BUYER"
    co_required: true
    co_form: "VK"                # CO FORM trong file vẫn được ưu tiên nếu có
    shipment_group: "by_no_exfty"
    declaration_rule: "one_per_shipment"
```

`bookingbot check` sẽ in thêm: file có bao nhiêu external link / defined name / công thức, cột nào là cột công thức, ô nào đang merge — để bạn biết Bot "nhìn" file thế nào trước khi cho ghi.

### OneDrive / SharePoint

Dùng **đường dẫn local đã sync về máy**, không dùng URL `https://`:

```
✓  C:/Users/nam/OneDrive - Cong ty/Logistics/WEEKLY PLANNING 2026.xlsx
✗  https://congty.sharepoint.com/sites/Logistics/...
```

Bot ghi vào file local, OneDrive tự sync lên cloud. Cách này đơn giản và an toàn hơn gọi Microsoft Graph API.

Tìm đường dẫn nhanh: mở file trong Excel → `File` → `Info` → bấm `Copy path`, rồi bỏ cặp dấu `"` ở hai đầu.

> **Lưu ý về Files On-Demand:** nếu OneDrive để chế độ tiết kiệm dung lượng, file có thể chỉ là "vỏ" chưa tải về. Chuột phải file → `Always keep on this device` để Bot đọc được ổn định.

### Người dùng và cơ sở

```yaml
users:
  - name: "user_a"
    display_name: "Nguyen Van A"
    role: "user"

site_rules:
  default_site: "TGV"
  rules:
    - when: { field: "PIC", in: ["Nguyen Van A", "user_a"] }
      site: "TGV"            # cơ sở 1
    - when: { field: "PIC", in: ["Tran Thi B", "user_b"] }
      site: "TDV"            # cơ sở 2
```

---

## 6. Kiểm tra nhận diện

```powershell
.\scripts\run.bat check
```

Lệnh này in ra:

- **File Registry** — file nào tồn tại, file nào đang bị mở trong Excel
- **Từng sheet** — sheet nào được nhận là Follow Up, confidence bao nhiêu, dòng header là dòng mấy
- **Cột chưa map** — header Bot chưa hiểu

Xử lý:

| Vấn đề | Cách sửa |
|---|---|
| Sheet Follow Up bị đánh `bỏ qua` | Vào Dashboard → **Mapping** → chọn `Follow Up` → Lưu. Bot nhớ vĩnh viễn |
| Sheet không phải Follow Up bị nhận nhầm | Cũng ở Mapping → chọn `Bỏ qua` |
| Cột quan trọng chưa map | Thêm tên cột vào `aliases` của field tương ứng trong `config\field_mapping.yaml` |

Ví dụ thêm alias:

```yaml
  FinalCBM:
    aliases: ["Final CBM", "CBM", "M3", "Final CBM (M3)", "Tong khoi", "Khoi hang"]
```

---

## 7. Chạy thật

```powershell
.\scripts\run.bat scan --dry-run   # LUÔN chạy bước này trước
.\scripts\run.bat scan             # ghi thật
```

Dry Run in ra chính xác Bot **sẽ** làm gì: thêm bao nhiêu dòng, sửa dòng nào, đổi màu bao nhiêu dòng. Không file nào bị đụng tới.

Đọc kỹ báo cáo, thấy hợp lý rồi mới bỏ `--dry-run`.

---

## 8. Chạy ngầm hằng ngày

```powershell
.\scripts\run.bat run
```

Bot sẽ:
- Hiện icon ở **khay hệ thống** (góc phải thanh taskbar, có thể phải bấm mũi tên `^`)
- Quét mỗi 3 giờ (chỉnh được 1–4 giờ)
- Mở dashboard tại <http://127.0.0.1:8765>
- Bật popup khi có deadline gấp, trong khung giờ 08:00–16:00

Chuột phải vào icon: **Quét ngay** · **Dry Run** · **Mở Dashboard** · **Tạm dừng** · **Thoát**.

### Tự khởi động cùng Windows

```powershell
.\scripts\install_autostart.ps1
```

Đăng ký một scheduled task chạy lúc đăng nhập, ẩn cửa sổ console, tự chạy lại nếu Bot tắt bất thường.

Gỡ bỏ: `.\scripts\install_autostart.ps1 -Remove`

---

## 9. Phát cho đồng nghiệp (không cần cài Python)

```powershell
.\scripts\build_exe.ps1
```

Tạo ra `dist\BookingBot\` — copy cả thư mục sang máy khác, sửa `config\config.yaml` cho đúng đường dẫn của người đó, rồi bấm **`Chạy Bot.bat`**.

> Mỗi người nên có `config.yaml` riêng (file Follow Up riêng), nhưng **dùng chung** `field_mapping.yaml` và `buyer_rules.yaml` để rule nghiệp vụ đồng nhất.

---

## Lỗi thường gặp

<table>
<tr><th>Thông báo</th><th>Nguyên nhân &amp; cách sửa</th></tr>

<tr><td><code>python : The term 'python' is not recognized</code></td>
<td>Python chưa vào PATH. Cài lại Python, tích ô <code>Add python.exe to PATH</code>.</td></tr>

<tr><td><code>cannot be loaded because running scripts is disabled</code></td>
<td>Chạy <code>Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass</code> trước, trong cùng cửa sổ PowerShell.</td></tr>

<tr><td><code>Khong tim thay file Follow Up</code></td>
<td>Sai đường dẫn trong <code>config.yaml</code>. Mở file trong Excel → File → Info → Copy path, dán lại và đổi <code>\</code> thành <code>/</code>.</td></tr>

<tr><td><code>File van bi khoa sau 3 lan thu</code></td>
<td>Ai đó đang mở file đích trong Excel. Bot đã đưa vào Pending Queue và sẽ tự ghi ở lần quét sau — <b>không mất dữ liệu</b>. Đóng file để Bot ghi ngay.</td></tr>

<tr><td><code>DATA CHANGED / CONFLICT</code></td>
<td>Bạn sửa file trong lúc Bot đang xử lý. Bot đã dừng, <b>không ghi đè</b>. Kiểm tra Exception Center rồi bấm Quét ngay.</td></tr>

<tr><td>Sheet Follow Up không được nhận</td>
<td>Chạy <code>run.bat check</code> xem confidence. Dưới 80% → vào Dashboard → Mapping xác nhận thủ công một lần.</td></tr>

<tr><td>Không thấy icon khay hệ thống</td>
<td>Bấm mũi tên <code>^</code> góc phải taskbar. Muốn ghim: Settings → Personalization → Taskbar → Other system tray icons.</td></tr>

<tr><td>Dashboard không mở được</td>
<td>Cổng 8765 đang bị chiếm. Đổi <code>web.port</code> trong <code>config.yaml</code>, ví dụ <code>8766</code>.</td></tr>

<tr><td>Không có popup nào</td>
<td>Ngoài khung giờ 08:00–16:00 → Bot dồn lại, phát khi tới giờ. Xem ngay tại Dashboard → Thông báo.</td></tr>

<tr><td><code>ModuleNotFoundError: No module named 'booking_bot'</code></td>
<td>Chạy sai Python. Luôn dùng <code>scripts\run.bat</code> thay vì gõ <code>python</code> trực tiếp.</td></tr>
</table>

---

## Gỡ cài đặt

```powershell
.\scripts\install_autostart.ps1 -Remove   # gỡ tự khởi động
Remove-Item -Recurse -Force .venv, data   # xóa môi trường ảo + database
```

Bot **không** ghi gì vào Registry và không cài gì ra ngoài thư mục dự án. Các file Excel của bạn giữ nguyên; các bản backup nằm trong `data\backups\` — xóa hay giữ tùy bạn.
