# Hướng dẫn cài đặt — Windows

Tài liệu đi từ máy trắng đến hệ thống chạy ổn định cho cả phòng. Làm theo đúng thứ tự.

**Mô hình triển khai khuyến nghị:** một máy Windows (PC để bàn hoặc laptop luôn bật trong giờ làm) làm **máy chủ nội bộ**. Máy này đọc được các file Excel Follow Up / Weekly Planning (qua OneDrive đã sync hoặc thư mục chia sẻ), chạy web + Automation. Mọi người khác chỉ cần trình duyệt Chrome/Edge, **không cài gì**.

> Dùng một mình trên laptop cá nhân cũng được — bỏ qua mục 7 (mạng LAN).

---

## 1. Cài Python (chỉ trên máy chủ)

1. Tải Python **3.11 trở lên**: <https://www.python.org/downloads/windows/>
2. Ở màn hình đầu tiên của bộ cài, **tích ô `Add python.exe to PATH`**. Bỏ qua bước này là nguyên nhân số 1 khiến các bước sau báo lỗi.
3. Kiểm tra — mở **PowerShell** (Start → gõ `powershell`):

```powershell
python --version        # phải ra Python 3.11.x hoặc cao hơn
```

---

## 2. Tải mã nguồn

```powershell
cd C:\Tools
git clone https://github.com/<tài-khoản>/booking-control.git
cd booking-control
```

Không có Git: trên GitHub bấm `Code` → `Download ZIP`, giải nén vào `C:\Tools\booking-control`.

> Đặt mã nguồn ở thư mục **không đồng bộ OneDrive** (ví dụ `C:\Tools\`). Database nằm trong `data\` — để trong OneDrive dễ bị xung đột khi đang ghi.

---

## 3. Chạy script cài đặt

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1
```

Script làm 5 việc: tạo môi trường ảo `.venv` → cài thư viện → tạo `config\config.yaml` + `config\buyer_rules.yaml` → **tạo tài khoản `admin`** (hỏi mật khẩu 2 lần, tối thiểu 8 ký tự có cả chữ và số) → tạo bộ dữ liệu demo trong `sample_data\`.

> `Set-ExecutionPolicy -Scope Process` chỉ có tác dụng trong cửa sổ PowerShell đang mở, không thay đổi cài đặt bảo mật của máy.

---

## 4. Chạy thử với dữ liệu demo

```powershell
.\scripts\run.bat run
```

Mở <http://127.0.0.1:8765> → đăng nhập `admin`. Automation quét lần đầu ngay khi khởi động: 3 file Follow Up demo, 6 sheet, ~37 lô hàng, đủ các màu trạng thái. Vào **Follow Up (lưới Excel)** để xem khung 47 cột.

Chưa tạo admin ở bước 3? Có hai cách:
- mở web **trên chính máy chủ** (`http://127.0.0.1:8765`) → trang *Thiết lập lần đầu* hiện ra, tạo Admin tại đó; hoặc
- `.\scripts\run.bat user add admin --role admin`

Dừng hệ thống: `Ctrl + C` trong cửa sổ đang chạy.

---

## 5. Tạo tài khoản cho cả phòng

Đăng nhập `admin` → **Quản trị › Người dùng** → *Thêm tài khoản*:

| Vai trò | Dùng cho | Thấy gì |
|---|---|---|
| **Quản trị** | 1–2 người phụ trách hệ thống | mọi thứ, gồm người dùng / mapping / cài đặt / backup |
| **Quản lý** | trưởng nhóm logistics | tất cả lô, phân công, Exception, nhập/xuất Excel, Automation |
| **Người dùng** | nhân viên làm booking | chỉ lô được giao cho mình (chính hoặc dự phòng) |
| **Chỉ xem** | kế toán, kho, sếp xem báo cáo | xem tất cả, không sửa |

Hệ thống tự sinh **mật khẩu tạm** (hiện một lần trên màn hình) — gửi riêng cho người đó; họ bị bắt đổi mật khẩu ở lần đăng nhập đầu. Quên mật khẩu: Admin bấm **Đặt lại MK**, hoặc chạy `.\scripts\run.bat user passwd <tên>`.

Có thể khai báo sẵn danh sách tài khoản trong `config\config.yaml` mục `users:` — hệ thống chỉ tạo tài khoản **chưa có mật khẩu**; Admin vẫn phải đặt mật khẩu tạm trên web.

> Tên đăng nhập trong `users:` nên **trùng** với `owner:` của file Follow Up tương ứng (mục 6). Khi đó lô hàng đọc từ file của ai sẽ tự giao cho người đó.

---

## 6. Trỏ vào file Excel thật

Mở `config\config.yaml` bằng Notepad / VS Code, sửa mục `files:`:

```yaml
files:
  - id: "F01-NAM"
    role: "follow_up"
    display_name: "Follow Up - Nam"
    path: "C:/Users/nam/OneDrive - Cong ty/Logistics/Follow Up - Nam.xlsx"
    owner: "nam.nguyen"          # = tên đăng nhập -> lô trong file tự giao cho người này
    write: true                  # cho phép tô màu dòng theo trạng thái
    color_skip_columns: ["A"]    # cột NO là công thức, đang tô vàng -> không tô đè

  - id: "F02"
    role: "weekly_planning"
    path: "C:/Users/nam/OneDrive - Cong ty/Chung/WEEKLY PLANNING 2026.xlsx"
    sheet: "2026"
    header_row: 1
    write: true
```

| Quy tắc đường dẫn | |
|---|---|
| Dùng `/` hoặc `\\` | `C:/Users/...` — **một** dấu `\` sẽ lỗi |
| Có dấu cách / tiếng Việt | được, miễn có `"` bao ngoài; file lưu UTF-8 |
| OneDrive / SharePoint | dùng **đường dẫn đã sync về máy**, không dùng URL `https://` |

Tìm đường dẫn nhanh: mở file trong Excel → `File` → `Info` → `Copy path`, bỏ cặp `"` ở hai đầu. Nếu OneDrive để chế độ *Files On-Demand*: chuột phải file → `Always keep on this device`.

**Buyer thật** khai báo trong `config\buyer_rules.yaml` (file này không lên GitHub). Buyer nào thiếu, lần quét đầu sẽ báo `EX008` kèm đúng tên. Cùng file có `forwarder_rules:` (Buyer + Mode + Dest → Forwarder).

**Rule tự phân công** trong `config\config.yaml` mục `assignment:` — ví dụ mọi lô SAMSUNG giao cho `user_b`, dự phòng `user_a`.

Kiểm tra trước khi chạy thật:

```powershell
.\scripts\run.bat check            # file nào có / thiếu / đang mở, sheet nào nhận là Follow Up, cột nào chưa map
.\scripts\run.bat scan --dry-run   # hệ thống SẼ làm gì — không file nào bị đụng tới
```

| Vấn đề | Cách sửa |
|---|---|
| Sheet Follow Up bị bỏ qua | Web → Quản trị › **Mapping** → chọn `Follow Up` → Lưu (nhớ vĩnh viễn) |
| Cột quan trọng chưa map | Thêm tên cột vào `aliases` trong `config\field_mapping.yaml` |
| Buyer thiếu rule (`EX008`) | Thêm vào `config\buyer_rules.yaml` |

---

## 7. Máy chủ nội bộ cho cả phòng (mạng LAN)

1. Trong `config\config.yaml`:

   ```yaml
   web:
     host: "0.0.0.0"     # nghe trên mọi card mạng
     port: 8765
   ```

2. Mở cổng tường lửa — PowerShell **Run as administrator**:

   ```powershell
   .\scripts\open_firewall.ps1        # chỉ mở cho mạng Private/Domain, không mở cho Public
   ```

   Script in ra địa chỉ cho đồng nghiệp, ví dụ `http://192.168.1.20:8765`.

3. Khởi động lại hệ thống (`Ctrl+C` rồi `.\scripts\run.bat run`, hoặc khởi động lại máy nếu đã cài chạy nền ở mục 8).

4. Nên đặt **IP tĩnh** cho máy chủ (hoặc nhờ IT đặt DHCP reservation) để địa chỉ không đổi. Có tên miền nội bộ / IIS / nginx làm HTTPS thì đặt `auth.secure_cookie: true`.

> Máy chủ phải **bật và đăng nhập Windows** trong giờ làm việc; Automation chỉ chạy khi hệ thống đang chạy. Tắt chế độ Sleep: *Settings → System → Power → Screen and sleep → Never* (khi cắm điện).

---

## 8. Chạy nền tự động khi đăng nhập Windows

```powershell
.\scripts\install_autostart.ps1          # đăng ký Scheduled Task "BookingControl"
.\scripts\install_autostart.ps1 -Remove  # gỡ
```

Task chạy `scripts\start_server_hidden.vbs` — không hiện cửa sổ, tự khởi động lại nếu tiến trình bị tắt bất thường. Kiểm tra: *Task Scheduler → Task Scheduler Library → BookingControl*. Nhật ký: `data\logs\`.

> Task chạy dưới tài khoản Windows đang đăng nhập để đọc được OneDrive của tài khoản đó. Không chạy dưới SYSTEM.

---

## 9. Email thông báo (tùy chọn)

Mặc định thông báo hiện trên web (chuông 🔔 + Desktop Notification khi tab đang mở). Muốn gửi thêm email cho người phụ trách với cảnh báo CRITICAL:

```yaml
notification:
  channels: { web: true, email: true, console: true }
  email:
    enabled: true
    smtp_host: "smtp.company.local"
    smtp_port: 25
    sender: "booking-control@company.com"
    base_url: "http://192.168.1.20:8765"
```

Mật khẩu SMTP (nếu có) đặt trong biến môi trường `BOOKINGCTL_SMTP_PASSWORD`, **không** ghi vào file cấu hình. Email lấy từ hồ sơ từng người (Tài khoản của tôi).

---

## 9b. Đọc email booking từ Outlook desktop

Hệ thống đọc **Outlook desktop cài trên chính máy chủ** (không dùng Microsoft Graph, không cần quyền Azure). Yêu cầu:

1. Máy chủ cài Outlook (Microsoft 365 / Outlook 2016+), đăng nhập hộp thư nhận email booking (hộp thư cá nhân hoặc hộp thư dùng chung, ví dụ `booking@company.com`).
2. Outlook **đang mở** trong phiên Windows đang chạy Booking Control (cùng tài khoản Windows). Chạy nền bằng Task Scheduler thì chọn *Run only when user is logged on*.
3. `pywin32` đã được `install.ps1` cài sẵn (kiểm tra: `.venv\Scripts\python -c "import win32com.client"`).

Bật trong `config\config.yaml`:

```yaml
jobs:
  outlook_interval_minutes: 30       # đọc mỗi 30 phút
outlook:
  enabled: true
  mailbox: ""                        # để trống = hộp thư mặc định; hoặc "booking@company.com"
  folders: ["Inbox", "Sent Items"]   # Sent Items: email yêu cầu booking mình gửi -> nhớ Conversation
  lookback_days: 14                  # lần đầu đọc lùi 14 ngày, sau đó chỉ đọc email mới
  auto_apply: true                   # false = mọi email vào hàng chờ duyệt
```

Thư mục con (ví dụ rule Outlook chuyển email forwarder vào `Inbox/Booking`) ghi `folders: ["Inbox/Booking", "Sent Items"]`.

Chạy thử ngay: `.\scripts\run.bat outlook` → bảng email vừa đọc. Trên máy không có Outlook: `.\scripts\run.bat outlook --demo` nạp 5 email mẫu để xem hàng chờ duyệt.

**Tinh chỉnh theo forwarder thật** — `config\email_profiles.yaml`:

- `forwarders`: tên miền / địa chỉ gửi của từng forwarder (nhận diện forwarder + nhãn riêng của họ).
- `field_aliases`: nhãn trong email / PDF (vd forwarder ghi "Sailing date:" thay cho "ETD:" → thêm `"sailing date"` vào `etd`).
- `categories`: từ khóa phân loại (thêm tiếng Việt / tiếng Hàn nếu cần).
- `thresholds` / `weights`: điểm tự ghép (mặc định ≥ 85 tự áp dụng, 60–84 chờ duyệt).

File PDF đính kèm được đọc bằng `pdfplumber` (PDF có lớp chữ). PDF scan ảnh → báo `NO_TEXT`, email vẫn vào hàng chờ duyệt để người dùng xem.

---

## 9c. Thư mục chứng từ

Mặc định chứng từ lưu ở `data\documents\Năm\Buyer\NO\LOẠI\`. Muốn lưu ở ổ mạng / NAS:

```yaml
documents:
  root: "\\\\nas01\\logistics\\booking-docs"   # tài khoản Windows chạy dịch vụ phải có quyền ghi
  max_upload_mb: 25
```

Loại chứng từ, chứng từ bắt buộc theo Mode / Buyer / Forwarder, hạn nộp và quy trình B1 → B15.1 nằm trong `config\workflow.yaml` (sửa file, khởi động lại là áp dụng; task chưa xong tự cập nhật theo cấu hình mới).

---

## 10. Sao lưu

| Cần sao lưu | Ở đâu | Gợi ý |
|---|---|---|
| Database | `data\booking_control.db` | copy hằng ngày sang ổ khác / NAS (dừng hệ thống hoặc copy lúc ít người dùng) |
| Cấu hình | `config\config.yaml`, `config\buyer_rules.yaml`, `config\email_profiles.yaml`, `config\workflow.yaml` | copy mỗi khi sửa |
| Chứng từ + file đính kèm email | `data\documents\`, `data\mail\` (hoặc `documents.root`) | sao lưu cùng database |
| Bản sao file Excel đích trước mỗi lần ghi | `data\backups\` | tự động, giữ 20 bản/file; khôi phục ở Quản trị › Backup |

Chuyển sang SQL Server / PostgreSQL khi nhiều người dùng: đổi `database.url` (xem ví dụ trong `config.example.yaml`), cài driver (`pyodbc` hoặc `psycopg`), khởi động lại — bảng được tạo tự động.

---

## 11. Phát bản chạy không cần Python (tùy chọn)

```powershell
.\scripts\build_exe.ps1
```

Tạo `dist\BookingControl\` — copy cả thư mục sang máy chủ khác, sửa `config\config.yaml`, tạo admin (`BookingControl.exe user add admin --role admin`), bấm **`Chay Booking Control.bat`**.

---

## 12. Nâng cấp từ v2 lên v3

1. Dừng hệ thống, **sao lưu `data\`** (database + backups).
2. `git pull` (hoặc chép mã nguồn mới), rồi `.venv\Scripts\pip install -r requirements.txt` (thêm `pdfplumber`).
3. Thêm vào `config\config.yaml` các mục mới từ `config.example.yaml`: `outlook` (dạng mới), `documents`, `weekly_planning`. Mục `outlook.booking_confirm_keywords` / `cancel_keywords` cũ không còn dùng — từ khóa nằm trong `config\email_profiles.yaml`.
4. Khởi động lại: bảng mới (booking, email, chứng từ, task) và cột mới được tạo tự động. Lần quét đầu: mỗi số BOOKING NO có trong Follow Up thành một booking (cùng số trên nhiều lô → booking gộp), task B1 → B15.1 được sinh cho lô có EX-FACT từ 30 ngày trước đến 75 ngày tới.
5. Tạo tài khoản Auditor nếu cần: `.\scripts\run.bat user add ten.ks --role auditor`.

---

## 13. Nâng cấp từ v1 (Booking Deadline Bot)

1. Dừng bản cũ, gỡ task cũ: trong thư mục cũ chạy `.\scripts\install_autostart.ps1 -Remove`.
2. Cài bản mới vào thư mục mới (mục 2–3). Copy `config\config.yaml` và `config\buyer_rules.yaml` cũ sang, rồi thêm các mục mới từ `config.example.yaml`: `assignment`, `auth`, `jobs`, `notification.email`, `web.host` (các mục `toast` / `popup` / `tray_balloon` cũ không còn dùng — xóa đi).
3. Database v1 dùng tiếp được (copy `data\bookingbot.db` → `data\booking_control.db`): cột mới tự thêm khi khởi động. Cột `bot_status` / `last_bot_color` cũ bị bỏ qua — trạng thái được tính lại ngay ở lần quét đầu.
4. Tạo tài khoản (mục 5). Lần quét đầu tự giao lô cho chủ file Follow Up.

---

## Lỗi thường gặp

| Thông báo | Nguyên nhân & cách sửa |
|---|---|
| `python : The term 'python' is not recognized` | Python chưa vào PATH — cài lại, tích `Add python.exe to PATH` |
| `running scripts is disabled` | Chạy `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` trong cùng cửa sổ |
| Đồng nghiệp không mở được web | `web.host` chưa là `"0.0.0.0"`; chưa chạy `open_firewall.ps1`; mạng Wi-Fi đang để *Public* (đổi sang *Private*); máy chủ đang Sleep |
| `Chưa có Admin có mật khẩu` khi khởi động | Mở web trên chính máy chủ để tạo, hoặc `run.bat user add admin --role admin` |
| Trang *Thiết lập lần đầu* báo chỉ mở trên máy chủ | Đúng — vì an toàn; mở bằng `http://127.0.0.1:8765` trên máy chủ |
| `Sai mật khẩu quá nhiều lần` | Chờ 10 phút, hoặc Admin đặt lại mật khẩu |
| `Không tìm thấy file Follow Up` | Sai đường dẫn trong `config.yaml` — lấy lại bằng File → Info → Copy path |
| `File vẫn bị khóa sau 3 lần thử` | Ai đó đang mở file đích; hệ thống đưa vào hàng chờ và tự ghi lần sau — **không mất dữ liệu** |
| `DATA CHANGED / CONFLICT` (EX007) | Có người sửa file trong lúc hệ thống đang ghi; hệ thống đã dừng, không ghi đè |
| Ô trên lưới báo *vừa được người khác cập nhật* | Hai người cùng sửa một dòng; dòng đã tải lại bản mới nhất, nhập lại nếu cần |
| Cổng 8765 đang bị chiếm | Đổi `web.port`, ví dụ `8766` (và mở lại tường lửa với `-Port 8766`) |
| `ModuleNotFoundError: booking_control` | Chạy sai Python — luôn dùng `scripts\run.bat` |
| Job `outlook_scan` lỗi *Can Windows + Outlook desktop dang mo + pywin32* | Outlook chưa mở trong phiên Windows đang chạy hệ thống, hoặc chạy nền dưới tài khoản khác — mở Outlook, chạy Task Scheduler với *Run only when user is logged on* |
| Outlook hỏi *A program is trying to access e-mail...* | Chính sách bảo mật Outlook: cài antivirus được Windows Security nhận diện, hoặc nhờ IT cho phép truy cập lập trình (Trust Center › Programmatic Access) |
| Email booking luôn vào *Chưa ghép* | Email không có JO / INVOICE / Booking No trùng dữ liệu: ghép tay một lần (hệ thống nhớ Conversation) hoặc thêm nhãn vào `email_profiles.yaml` |
| PDF đính kèm báo `NO_TEXT` | PDF scan ảnh, không có lớp chữ — xem file đính kèm và nhập tay |

---

## Gỡ cài đặt

```powershell
.\scripts\install_autostart.ps1 -Remove
.\scripts\open_firewall.ps1 -Remove        # Run as administrator
Remove-Item -Recurse -Force .venv, data    # xóa môi trường ảo + database
```

Hệ thống không ghi gì vào Registry và không cài gì ra ngoài thư mục dự án. File Excel của bạn giữ nguyên.
