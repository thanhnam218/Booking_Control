# Hướng dẫn cài đặt và chạy

**Booking Control 3.3.0** — web nội bộ, chạy trên một máy Windows làm máy chủ, cả phòng mở bằng trình duyệt.

> Chỉ **máy chủ** cần cài Python. Người dùng chỉ cần trình duyệt Edge hoặc Chrome.

Mục lục:
[1. Chuẩn bị](#1-chuẩn-bị) ·
[2. Cài đặt](#2-cài-đặt-trong-5-phút) ·
[3. Chạy thử](#3-chạy-thử-với-dữ-liệu-demo) ·
[4. Tài khoản](#4-tạo-tài-khoản-cho-cả-phòng) ·
[5. Dữ liệu thật](#5-trỏ-vào-dữ-liệu-thật) ·
[6. Mạng LAN](#6-mở-cho-cả-phòng-dùng-chung-mạng-lan) ·
[7. Chạy nền](#7-chạy-nền-tự-động) ·
[8. Cấu hình](#8-cấu-hình-chi-tiết) ·
[9. Sao lưu](#9-sao-lưu-và-khôi-phục) ·
[10. Nâng cấp](#10-nâng-cấp) ·
[11. Sự cố](#11-khắc-phục-sự-cố)

---

## 1. Chuẩn bị

### Máy chủ

| | Tối thiểu | Nên có |
|---|---|---|
| Hệ điều hành | Windows 10 | Windows 11 hoặc Windows Server |
| RAM | 4 GB | 8 GB |
| Ổ đĩa trống | 2 GB | 10 GB (backup + chứng từ) |
| Python | 3.11 | 3.12 hoặc 3.13 |

Máy chủ nên là máy **luôn bật** — nếu tắt thì Automation không quét và cả phòng không mở được web.

### Cài Python

Tải tại <https://www.python.org/downloads/windows/>.

> **Quan trọng:** khi cài, tick ô **“Add python.exe to PATH”** ở màn hình đầu tiên. Quên tick thì phải gỡ ra cài lại.

Kiểm tra:

```bat
python --version
```

Phải hiện `Python 3.11.x` trở lên.

### Quyền truy cập file

Tài khoản Windows chạy phần mềm phải **đọc và ghi** được:

- các file Follow Up của từng nhân viên;
- file WEEKLY PLANNING, Custom Code, MM-Daily, Lịch xuất hàng.

Nếu file nằm trên OneDrive/SharePoint thì dùng **đường dẫn đã đồng bộ về máy** (kiểu `C:\Users\...\OneDrive - Công ty\...`), không dùng địa chỉ web.

---

## 2. Cài đặt trong 5 phút

Chép (hoặc giải nén) thư mục mã nguồn vào `C:\Tools\booking-control`, rồi mở **PowerShell**:

```powershell
cd C:\Tools\booking-control
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1
```

Script làm 5 việc:

1. tạo môi trường ảo `.venv`;
2. cài thư viện (1–3 phút);
3. tạo `config\config.yaml` và `config\buyer_rules.yaml`;
4. **hỏi mật khẩu để tạo tài khoản `admin`** — nhập 2 lần, tối thiểu 8 ký tự, phải có cả chữ và số;
5. tạo bộ dữ liệu demo trong `sample_data\`.

Xong sẽ thấy `=== XONG ===`.

### Nếu muốn làm từng bước

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m booking_control init
.venv\Scripts\python.exe -m booking_control user add admin --role admin --display-name "Quản trị"
.venv\Scripts\python.exe -m booking_control sample
```

---

## 3. Chạy thử với dữ liệu demo

```bat
.\scripts\run.bat run
```

Mở trình duyệt: **<http://127.0.0.1:8765>** — đăng nhập `admin` với mật khẩu vừa đặt.

Dừng: bấm `Ctrl + C` trong cửa sổ đang chạy.

### Các lệnh khác

| Lệnh | Việc |
|---|---|
| `run.bat run` | Chạy web + Automation (dùng hằng ngày) |
| `run.bat run --no-scheduler` | Chỉ chạy web, tắt Automation (khi muốn xem lại dữ liệu mà không quét) |
| `run.bat run --host 0.0.0.0` | Mở cho mạng LAN |
| `run.bat check` | Kiểm tra file, nhận diện sheet, map cột — **chạy lệnh này trước khi chạy thật** |
| `run.bat scan --dry-run` | Quét thử, báo sẽ ghi gì nhưng **không ghi** |
| `run.bat scan` | Quét một lần rồi thoát |
| `run.bat status` | In nhanh số liệu hiện tại |
| `run.bat outlook` | Đọc email Outlook ngay |
| `run.bat outlook --demo` | Nạp 5 email mẫu để thử (không cần Outlook) |
| `run.bat backups F02` | Liệt kê / khôi phục backup file đích |
| `run.bat user list` | Danh sách tài khoản |
| `run.bat version` | Phiên bản |

### Nên làm quen với dữ liệu demo trước

Bộ demo có 5 buyer, 37 lô, đủ các trạng thái trắng/vàng/xanh/đỏ, có lô hold, lô xuất một phần, lô đã hủy. Dùng nó để:

- xem lưới Follow Up 47 cột hoạt động thế nào;
- thử phân công, thử xuất Excel;
- thử gõ công thức vào ô;
- xem trang **Nhóm lô xuất** và **5 bảng xương sống**.

Dữ liệu demo nằm riêng trong `sample_data\`, xóa lúc nào cũng được.

---

## 4. Tạo tài khoản cho cả phòng

### Cách 1 — trên web (dễ hơn)

Đăng nhập `admin` → **Quản trị › Nhân viên** → *Thêm* → đặt mật khẩu tạm.
Người đó đăng nhập lần đầu sẽ bị bắt đổi mật khẩu.

### Cách 2 — dòng lệnh

```bat
.\scripts\run.bat user add nam.nguyen --role user --display-name "Nguyễn Văn Nam"
.\scripts\run.bat user add lan.tran  --role manager --display-name "Trần Thị Lan"
.\scripts\run.bat user passwd nam.nguyen
.\scripts\run.bat user disable nghi.le
```

### Năm vai trò

| Vai trò | Thấy gì | Làm được gì |
|---|---|---|
| `admin` | Tất cả | Toàn quyền, kể cả cấu hình và tài khoản |
| `manager` | Tất cả | Phân công, duyệt, xử lý exception, nhập Excel |
| `user` | **Chỉ lô được giao** | Sửa lô của mình, tạo booking, nộp chứng từ |
| `viewer` | Tất cả | Chỉ xem, không sửa |
| `auditor` | Tất cả | Chỉ đọc + xem Audit Log |

Tên đăng nhập: 2–40 ký tự, chữ/số/`.`/`_`/`-`, **không dấu tiếng Việt, không khoảng trắng**.

Tài khoản **không bị xóa** (giữ lịch sử) — chỉ khóa.

---

## 5. Trỏ vào dữ liệu thật

Mở `config\config.yaml`, sửa mục `files:`.

```yaml
files:
  - id: "F01-NAM"
    role: "follow_up"
    display_name: "Follow Up - Nguyễn Văn Nam"
    path: "C:/Users/may-chu/OneDrive - Cong ty/Logistics/FollowUp_Nam.xlsx"
    owner: "nam.nguyen"          # trùng tên đăng nhập
    read: true
    write: true                  # cho phép tô màu dòng
    # color_skip_columns: ["A"]  # cột công thức người dùng tự tô vàng -> đừng đụng

  - id: "F02"
    role: "weekly_planning"
    path: "C:/.../WEEKLY PLANNING 2026.xlsx"
    sheet: "2026"                # để trống = tự tìm sheet có header khớp
    header_row: 1                # để trống = tự dò
    owner: "common"
    read: true
    write: true
```

**Vai trò file** (`role`):

| role | File | Hệ thống làm gì |
|---|---|---|
| `follow_up` | Follow Up từng nhân viên | Đọc dữ liệu chính, tô màu dòng |
| `weekly_planning` | WEEKLY PLANNING | Ghi vào ở mốc D-7 |
| `custom_code` | Custom Code / HS Code | Ghi vào ở mốc D-7 |
| `mm_daily` | MM-DAILY PLANNING | Ghi vào |
| `shipping_schedule` | Lịch xuất hàng | Ghi vào ở mốc D-1, tách sheet TDV/TGV |

> Đường dẫn dùng dấu `/` hoặc `\\` (hai gạch chéo ngược). Một gạch `\` sẽ lỗi.

### Kiểm tra trước khi chạy thật

```bat
.\scripts\run.bat check
```

Lệnh này in ra:

- file nào **tồn tại**, file nào **đang bị mở** (khóa);
- sheet nào được nhận là Follow Up, độ tin cậy bao nhiêu phần trăm;
- cột nào map được, cột nào chưa — **cột chưa map không gây lỗi**, chỉ là hệ thống không dùng đến;
- cột nào là công thức, ô nào merge, chỗ nào openpyxl sẽ làm hỏng.

Cột quan trọng mà chưa map → thêm bí danh vào `config\field_mapping.yaml`, hoặc vào web **Quản trị › Mapping** xác nhận một lần.

### Chạy thử không ghi

```bat
.\scripts\run.bat scan --dry-run
```

Báo cáo sẽ ghi bao nhiêu dòng vào file nào, đổi màu bao nhiêu dòng — **nhưng không đụng vào file**. Xem kỹ rồi mới:

```bat
.\scripts\run.bat scan
```

### Nhập dữ liệu từ file Excel ngoài

File không theo khung 47 cột (ví dụ *Shipment schedule* hàng trăm cột) thì **không khai trong `files:`**. Thay vào đó vào web: **Quản lý › Nhập Excel ngoài** — xem [USER_GUIDE.md](USER_GUIDE.md).

---

## 6. Mở cho cả phòng dùng chung (mạng LAN)

### Bước 1 — cho phép nghe trên mọi địa chỉ

Trong `config\config.yaml`:

```yaml
web:
  host: "0.0.0.0"     # thay cho 127.0.0.1
  port: 8765
```

### Bước 2 — mở tường lửa

PowerShell **quyền Administrator**:

```powershell
.\scripts\open_firewall.ps1
```

Hoặc làm tay:

```powershell
New-NetFirewallRule -DisplayName "Booking Control" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow -Profile Domain,Private
```

### Bước 3 — lấy địa chỉ máy chủ

```powershell
ipconfig | Select-String IPv4
```

Gửi cho cả phòng: `http://<địa-chỉ-IP>:8765` — ví dụ `http://192.168.1.20:8765`.

### Lưu ý

- Trang tạo Admin đầu tiên **chỉ mở được trên chính máy chủ** (`auth.allow_remote_setup: false`).
- Chạy sau HTTPS (IIS/nginx reverse proxy) thì đặt `auth.secure_cookie: true`.
- Hàng chục người dùng cùng lúc: SQLite đã bật WAL và chờ khóa 15 giây, đủ cho một phòng. Đông hơn nữa thì xem §8.6.

---

## 7. Chạy nền tự động

Để máy chủ khởi động cùng Windows, không cần ai mở cửa sổ:

```powershell
.\scripts\install_autostart.ps1
```

Script tạo tác vụ trong Task Scheduler, chạy `scripts\start_server_hidden.vbs` (ẩn cửa sổ).

Kiểm tra: mở **Task Scheduler** → tìm `BookingControl`.

Gỡ:

```powershell
Unregister-ScheduledTask -TaskName "BookingControl" -Confirm:$false
```

**Xem log khi chạy nền:** thư mục `data\logs\`.

---

## 8. Cấu hình chi tiết

Tất cả trong `config\`. Sửa xong **khởi động lại** phần mềm.

| File | Nội dung | Có trong git? |
|---|---|---|
| `config.yaml` | Đường dẫn file, lịch quét, deadline, container, web, Outlook | Không (chứa đường dẫn nội bộ) |
| `buyer_rules.yaml` | Buyer, CO, quy tắc gom lô, **Buyer × Forwarder** | Không |
| `field_mapping.yaml` | Bí danh tên cột, quy tắc JO–GO | Có |
| `followup_template.yaml` | 47 cột, công thức, **quyền từng cột** | Có |
| `workflow.yaml` | Bước B1–B15.1, **chứng từ bắt buộc + hạn + ai làm** | Có |
| `email_profiles.yaml` | **Bộ đọc email từng Forwarder**, ngưỡng ghép lô | Có |

> Hai file `.yaml` không nằm trong git có bản mẫu `.example.yaml` đi kèm. Cài mới sẽ chép từ bản mẫu.

Xem toàn bộ năm bảng quy tắc và **kiểm tra chéo giữa chúng**: web → **Quản lý › 5 bảng xương sống**.

### 8.1 Lịch quét

```yaml
scheduler:
  scan_interval_hours: 3        # 1–4
  scan_anchor_hour: 9           # mốc đầu: 09:00, 12:00, 15:00, 18:00
  run_on_start: true
  notification_window:
    start: "08:00"
    end: "16:00"
  active_weekdays: ["mon","tue","wed","thu","fri","sat"]
  queue_outside_window: true    # ngoài giờ vẫn quét, dồn thông báo lại
```

### 8.2 Deadline

```yaml
deadlines:
  booking_window:
    start_days_before: 20       # bắt đầu nhắc
    end_days_before: 14         # hạn chót — quá mốc này thì viền đỏ
  hold_reminder_days: 3         # 1–7
  milestones:
    weekly_planning_days_before: 7
    custom_code_days_before: 7
    final_info_days_before: 3
    shipping_schedule_days_before: 1
  dedup_hours: 20               # chống spam thông báo
  overdue_grace_days: 3
```

### 8.3 Container

```yaml
container:
  capacity: { "40HC": 63, "40DC": 53, "20ST": 23 }
  priority: ["40HC", "40DC", "20ST"]
  strategy: "priority"          # priority | min_excess
  lcl_threshold_cbm: 15
  safety_factor: 1.0
  air_type: "AC"
  lcl_type: "CFS"
```

### 8.4 Email thông báo (tùy chọn)

```yaml
notification:
  channels: { web: true, email: true, console: true }
  email:
    enabled: true
    smtp_host: "smtp.company.local"
    smtp_port: 25
    sender: "booking-control@company.com"
    password_env: "BOOKINGCTL_SMTP_PASSWORD"   # mật khẩu để trong biến môi trường
    base_url: "http://192.168.1.20:8765"       # để chèn link vào email
```

**Không ghi mật khẩu vào file config.** Đặt biến môi trường cho tài khoản chạy dịch vụ:

```powershell
[Environment]::SetEnvironmentVariable("BOOKINGCTL_SMTP_PASSWORD","matkhau","User")
```

### 8.5 Đọc email Outlook (tùy chọn)

Yêu cầu: máy chủ **cài Outlook desktop**, đang mở hộp thư, và có `pywin32`.

```powershell
.venv\Scripts\python.exe -m pip install "pywin32>=306"
```

```yaml
outlook:
  enabled: true
  mailbox: ""                        # để trống = hộp thư mặc định
  folders: ["Inbox", "Sent Items"]   # Sent Items để nhớ Conversation của email mình gửi
  lookback_days: 14                  # lần đầu đọc lùi 14 ngày
  auto_apply: true                   # email đủ điểm và không mâu thuẫn -> tự cập nhật
```

Thử ngay:

```bat
.\scripts\run.bat outlook
.\scripts\run.bat outlook --demo     # 5 email mẫu, không cần Outlook
```

Khai **bộ đọc riêng cho từng Forwarder** trong `email_profiles.yaml` — xem [USER_GUIDE.md](USER_GUIDE.md) mục *5 bảng xương sống*.

### 8.6 Đổi sang SQL Server / PostgreSQL

SQLite chỉ cho **một người ghi tại một thời điểm**. Vài chục người thì vẫn ổn (đã bật WAL + chờ khóa 15 giây). Đông hơn, hoặc muốn backup tập trung:

```yaml
database:
  url: "mssql+pyodbc://user:pass@SERVER/BookingControl?driver=ODBC+Driver+17+for+SQL+Server"
  # hoặc
  # url: "postgresql+psycopg://user:pass@server/booking_control"
```

Cài thêm driver:

```powershell
.venv\Scripts\python.exe -m pip install pyodbc          # SQL Server
.venv\Scripts\python.exe -m pip install "psycopg[binary]"  # PostgreSQL
```

Bảng sẽ tự tạo ở lần chạy đầu. **Dữ liệu cũ trong SQLite không tự chuyển sang** — cần xuất/nhập thủ công.

### 8.7 Thư mục chứng từ

```yaml
documents:
  root: ""                      # để trống = <data_dir>/documents
  # root: "\\\\server\\share\\chung-tu"      # hoặc để trên NAS
  max_upload_mb: 25
```

---

## 9. Sao lưu và khôi phục

### Cái gì cần sao lưu

| Đường dẫn | Nội dung | Mức quan trọng |
|---|---|---|
| `data\booking_control.db` | **Toàn bộ dữ liệu nghiệp vụ** | Sống còn |
| `data\documents\` | File chứng từ đã nộp | Sống còn |
| `config\` | Cấu hình, quy tắc | Cao |
| `data\backups\` | Backup file Excel đích | Trung bình |
| `data\logs\` | Nhật ký | Thấp |

### Sao lưu hằng ngày

Tạo `backup.ps1`:

```powershell
$src = "C:\Tools\booking-control"
$dst = "D:\Backup\booking-control\$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item "$src\data\booking_control.db" $dst
Copy-Item "$src\config" $dst -Recurse -Force
Copy-Item "$src\data\documents" $dst -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem "D:\Backup\booking-control" | Sort-Object Name -Descending |
  Select-Object -Skip 30 | Remove-Item -Recurse -Force
```

Hẹn giờ chạy lúc 23:00 mỗi ngày:

```powershell
$t = New-ScheduledTaskTrigger -Daily -At 23:00
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File D:\Scripts\backup.ps1"
Register-ScheduledTask -TaskName "BookingControl-Backup" -Trigger $t -Action $a
```

> Nên **dừng phần mềm** trước khi chép file `.db`, hoặc dùng `sqlite3 .backup` để chép an toàn khi đang chạy.

### Backup file Excel đích

Hệ thống tự backup **trước mỗi lần ghi**, giữ 20 bản (`excel_write.backup_keep_versions`).

```bat
.\scripts\run.bat backups F02
```

Khôi phục: web → **Quản trị › Backup** → chọn bản → *Khôi phục*.

### Khôi phục toàn bộ

1. Dừng phần mềm.
2. Chép `booking_control.db`, thư mục `config\` và `documents\` từ bản backup về.
3. Chạy lại.

---

## 10. Nâng cấp

```powershell
cd C:\Tools\booking-control
# Dừng phần mềm trước
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .
.\scripts\run.bat run
```

**Không mất dữ liệu.** Lần chạy đầu sau nâng cấp, hệ thống tự:

- thêm cột mới với giá trị mặc định;
- tạo index mới;
- đổi tên cột cũ (nâng từ v1).

File `config\config.yaml` của bạn **không bị ghi đè**. Bản mới có thêm mục cấu hình thì xem trong `config\config.example.yaml` rồi chép sang phần mình cần.

Trình duyệt tự nạp lại CSS/JS mới — đường dẫn tệp tĩnh mang dấu vân tay theo nội dung, không cần xóa cache tay.

**Vẫn nên sao lưu `data\booking_control.db` trước khi nâng cấp.**

---

## 11. Khắc phục sự cố

### Không khởi động được

| Thông báo | Nguyên nhân | Xử lý |
|---|---|---|
| `Chua cai dat. Hay chay install.ps1` | Chưa có `.venv` | Chạy `.\scripts\install.ps1` |
| `KHONG TIM THAY PYTHON` | Chưa cài hoặc quên tick Add to PATH | Cài lại Python, nhớ tick |
| `error while attempting to bind on address ... 8765` | Cổng đang bị chiếm | Xem bên dưới |
| `Database chua duoc khoi tao` | Chưa chạy `init` | `.\scripts\run.bat` bất kỳ lệnh nào sẽ tự khởi tạo |

**Cổng 8765 bị chiếm** — thường do một bản cũ còn chạy ngầm:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*booking_control run*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Hoặc đổi cổng trong `config.yaml › web.port`.

### Đăng nhập

| Vấn đề | Xử lý |
|---|---|
| Quên mật khẩu | `.\scripts\run.bat user passwd <tên>` |
| Sai 5 lần, bị khóa | Chờ 10 phút, hoặc đổi `auth.max_failed_logins` |
| `Chưa có Admin có mật khẩu` | Mở web **trên chính máy chủ** để tạo, hoặc `user add admin --role admin` |
| Đăng nhập xong lại văng ra | Xóa cookie trình duyệt, hoặc kiểm tra `auth.secure_cookie` (chỉ bật khi thật sự có HTTPS) |

### File Excel

| Vấn đề | Xử lý |
|---|---|
| `Khong tim thay file` | Kiểm tra `path`, dùng `/` hoặc `\\`; OneDrive đã đồng bộ về máy chưa |
| `File dang bi khoa` | Ai đó đang mở file. Hệ thống tự thử lại 30s → 60s → 120s, rồi xếp hàng chờ. **Không ép ghi.** |
| Sheet không được nhận | `run.bat check` xem độ tin cậy; vào **Quản trị › Mapping** xác nhận một lần |
| Cột không map được | Thêm bí danh vào `field_mapping.yaml`, hoặc xác nhận trên trang Mapping |
| Công thức bị mất sau khi hệ thống ghi | Không xảy ra — ghi lossless. Nếu gặp, gửi file + log để kiểm tra |

### Dữ liệu trông sai

| Hiện tượng | Giải thích |
|---|---|
| Sửa Excel mà web không đổi | Chờ lần quét kế tiếp, hoặc bấm **Quét ngay** trên thanh trên cùng |
| Web khác Excel | **Giá trị web thắng** — người dùng đã sửa tay. Xem Exception Center (`EX015`) |
| Lô biến mất khỏi lưới | Có thể đã lưu trữ, hoặc bộ lọc *Ẩn lô đã xong / hủy* đang bật |
| Nhân viên không thấy lô | Đúng thiết kế — chỉ thấy lô được giao. Quản lý vào **Phân công** để giao |
| Màu không đúng mong đợi | Mở chi tiết lô → thẻ **Vì sao …?** giải thích từng bước |

### Thông báo

| Vấn đề | Xử lý |
|---|---|
| Không có thông báo nào | Ngoài khung giờ `08:00–16:00` thì bị dồn lại. Xem `scheduler.notification_window` |
| Quá nhiều thông báo | Tăng `deadlines.dedup_hours` |
| Email không gửi được | Kiểm tra `notification.email.enabled`, SMTP host/port, biến môi trường mật khẩu |

### Outlook

| Vấn đề | Xử lý |
|---|---|
| `Khong ket noi duoc Outlook` | Outlook desktop phải **đang mở** trên máy chủ; kiểm tra `pywin32` đã cài |
| Email không ghép được lô | Vào trang **Email Outlook**, chọn lô thủ công — hệ thống sẽ nhớ Conversation cho lần sau |
| PDF không đọc được | PDF ảnh scan không có lớp chữ (`NO_TEXT`) — cần nhập tay |
| Đọc sai số booking của một Forwarder | Khai mẫu riêng cho Forwarder đó trong `email_profiles.yaml` |

### Xem log

```powershell
Get-Content data\logs\booking_control.log -Tail 50 -Wait
```

Đổi mức chi tiết: `config.yaml › app.log_level` (`DEBUG` / `INFO` / `WARNING`).

### Kiểm tra sức khỏe hệ thống

```bat
.\scripts\run.bat status
```

Và trên web: **Quản lý › 5 bảng xương sống** — trang này tự đối chiếu các bảng cấu hình với nhau và báo chỗ vênh (rule trỏ tới bộ đọc email không tồn tại, chứng từ bắt buộc chưa ghi ai làm…).

---

## 12. Gỡ cài đặt

1. Dừng phần mềm, gỡ tác vụ tự khởi động:
   ```powershell
   Unregister-ScheduledTask -TaskName "BookingControl" -Confirm:$false
   ```
2. Gỡ luật tường lửa:
   ```powershell
   Remove-NetFirewallRule -DisplayName "Booking Control"
   ```
3. **Sao lưu `data\` và `config\` trước khi xóa.**
4. Xóa thư mục `C:\Tools\booking-control`.

File Excel gốc của bạn **không bị ảnh hưởng** — hệ thống chỉ đọc và tô màu dòng.

---

## Tài liệu liên quan

| Tài liệu | Dành cho |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | Người dùng hằng ngày |
| [SPECIFICATION.md](SPECIFICATION.md) | Hiểu hệ thống làm gì và theo quy tắc nào |
| [ARCHITECTURE.md](ARCHITECTURE.md) · [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | Người sửa mã nguồn |
