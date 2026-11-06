# Nhật ký thay đổi

## 3.3.0 — ưu tiên theo 5 bảng xương sống (SRSv4 §68)

- **Trang `/admin/matrices`**: gom cả năm bảng về một chỗ — Buyer × Forwarder, Email Pattern, Deadline, Chứng từ, Quyền cột — và ghi rõ mỗi bảng nằm ở file cấu hình nào.
- **Kiểm tra chéo giữa các bảng** — phần đáng giá nhất. Bắt được những chỗ vênh mà trước đây không ai thấy: rule trỏ tới parser không tồn tại, Parser Profile không khai domain lẫn sender (không bao giờ khớp email nào), Forwarder có rule nhưng chưa có bộ đọc email, chứng từ bắt buộc mà chưa ghi ai làm, cột nguồn OUTLOOK nhưng `auto: false`.
- **Parser Profile theo từng Forwarder** (SRSv4 §15, BR-020 — *“không dùng một parser cho tất cả”*): mỗi Forwarder một bộ mẫu riêng, và **một Forwarder có nhiều nhân viên thì mỗi người khai được mẫu riêng** (SRSv5 §25). Thứ tự ưu tiên: mẫu của nhân viên > mẫu của Forwarder > alias chung. Cấu hình sai bị từ chối ngay lúc nạp chứ không đợi tới lúc xử lý email thật.
- **Buyer × Forwarder đủ cột theo SRS**: thêm `service`, `booking_rule`, `parser` bên cạnh buyer / mode / dest / port / khoảng ngày hiệu lực.
- **Document Matrix thêm cột Owner**: 15 loại chứng từ nay ghi rõ bộ phận chịu trách nhiệm, hiện luôn trong checklist của lô.
- Thêm 16 test cho 5 bảng và Parser Profile.

## 3.3.0 — 5 bảng xương sống (SRSv4 §68)

SRSv4 §68 gọi năm bảng này là *"xương sống của hệ thống"*: nếu chỉ dựa vào file Follow Up để viết phần mềm thì mỗi lần Buyer hay Forwarder đổi quy trình là phải sửa code. Bản này đưa cả năm bảng thành cấu hình đầy đủ và kiểm tra chéo được.

- **Trang mới `/matrices` — 5 bảng xương sống** (Quản lý › 5 bảng xương sống): xem cả năm bảng một chỗ, mỗi bảng ghi rõ sửa ở file nào, kèm phần **kiểm tra chéo** giữa các bảng. Quyền `rules.view` cho Admin / Quản lý / Auditor.
- **Bảng 1 — Buyer × Forwarder**: thêm ba cột SRS yêu cầu mà trước đây thiếu — `service` (FCL/LCL/AIR), `booking_rule` (quy tắc đặt chỗ của Forwarder đó), `parser` (bộ đọc email dùng cho họ). Vẫn giữ hiệu lực theo khoảng ngày xuất.
- **Bảng 2 — Email Pattern / Parser Profile** (BR-020: *"Mọi Forwarder phải được cấu hình bằng Parser Profile, không hard-code format"*): mỗi Forwarder khai bộ mẫu riêng cho 14 trường (booking no, ETD, vessel, cut-off…), lọc theo tiêu đề và tên file đính kèm. **Mỗi nhân viên của Forwarder khai riêng được** — một hãng nhưng mỗi người gửi một kiểu. Thứ tự ưu tiên: mẫu nhân viên → mẫu Forwarder → alias chung. Pattern sai cú pháp hoặc thiếu nhóm bắt giá trị bị báo lỗi ngay lúc nạp cấu hình.
- **Bảng 3 — Deadline**: bổ sung các mốc **sau ngày xuất** còn thiếu — PNK (EX-FACT+1), BK (ETD+2), Surrender Bill (ETD+12), Debit Note (ETD+15), DO DIA, E-Shipment. Giờ mọi loại chứng từ đều có hạn.
- **Bảng 4 — Chứng từ**: thêm cột **Owner** (bộ phận phụ trách) cho cả 16 loại chứng từ; danh sách chứng từ trên trang lô hàng hiện kèm người chịu trách nhiệm.
- **Bảng 5 — Quyền từng cột**: hiển thị thành câu rõ nghĩa cho từng cột trong 47 cột (ai sửa được, Automation có ghi đè không, cột công thức xử lý ra sao).
- **Kiểm tra chéo** bắt các lỗi nằm giữa hai bảng: rule trỏ tới parser không tồn tại, profile không rule nào dùng, chứng từ bắt buộc mà chưa có hạn hoặc chưa có người phụ trách, cột không ai sửa được, cửa sổ booking đặt ngược. Chạy trên cấu hình mẫu đi kèm: **0 lỗi**.
- Thêm 15 test cho năm bảng.

## 3.2.0 — theo SRS v5.1 → v5.5

- **Tách ba trục độc lập** (v5.1 §1, BR-013): *Trạng thái nghiệp vụ* (ghép từ booking / hold / ship status) · *Màu hiển thị* · *Cảnh báo hạn*. Trước đây màu bị dùng luôn làm trạng thái nghiệp vụ. Trang lô hàng nay hiện cả ba cạnh nhau.
- **Cảnh báo viền đỏ** (v5.1 §16): lô chưa có booking mà còn dưới 14 ngày được tô **viền** đỏ trên lưới — nền vẫn giữ màu trạng thái, vì đây là hai thông tin khác nhau. Ngưỡng lấy từ cấu hình. Thêm ô lọc *Chỉ lô cảnh báo hạn booking*.
- **Quan hệ JO – GO** (v5.1 §5): bảng riêng thay cho việc chỉ lưu GO dạng text. Một GO gom nhiều JO qua phần mã gốc (`26V02426KR01` + `26V02426KR02` ↔ `S26V02426`). Ghi rõ quan hệ đến từ đâu: **từ file** (chắc chắn) hay **suy ra** (đề xuất, có dấu `?`). Quy tắc tách mã trong `config/field_mapping.yaml › jo_go`.
- **Export Shipment Group** (v5.1 §2–4, v5.5 §4): trang **Nhóm lô xuất** gom theo `BUYER + EX-FACT DATE + DEST + MODE`, cộng sẵn tổng PCS / CTNS / G.W / CBM, đếm JO, đếm GO và tính container cho cả nhóm. Nhóm **không phải** booking — hệ thống chỉ gom và đề xuất, một nhóm vẫn có thể nằm trên nhiều booking. Thiếu CBM thì đếm riêng và báo "tổng chưa đủ" chứ không coi là 0.
- **Hàng đợi "Exception Only"** (v5.5 §10): đầu Dashboard hiện *"⚠ N việc cần bạn xử lý"* thay vì bắt rà từng lô, đủ 8 loại của SRS — booking quá hạn, email ≠ PDF, thiếu CBM, booking bị hủy, cần rebook, thiếu chứng từ, phân bổ chưa đủ, ngày xuất vừa đổi. Bấm vào là ra đúng danh sách.
- Thêm 23 test lấy thẳng ví dụ trong SRS (bảng D-20…D+1, cặp JO/GO, nhóm 25 JO · 3 GO · 149.10 CBM).
- **Sửa lỗi**: một byte NUL lọt vào `followup.js` khiến công cụ coi file là nhị phân.

## 3.1.0

- **Đổi tên vai trò**: "Người dùng" → **Nhân viên** trên toàn bộ giao diện.
- **Nhập Excel ngoài** (`/import/external`): tải file bất kỳ (vd *GARMENT SHIPMENT SCHEDULE* 61 cột) → chọn sheet + dòng tiêu đề → xem **tất cả cột kèm tỷ lệ có dữ liệu và giá trị mẫu** → tick đúng cột cần lấy → xem trước → nhập. Gợi ý ánh xạ tự động (chỉ tự chọn khi chắc ≥ 90%). Lưu thành **bộ map** dùng lại lần sau. Ô lỗi `#REF!` / `#N/A` coi như trống; dòng thiếu JO hoặc EX-FACT DATE bị bỏ kèm lý do.
- **Menu con**: menu chính gom thành nhóm *Lô hàng · Công việc · Quản lý · Quản trị*, menu con thụt vào có vạch dọc, nhớ trạng thái mở/đóng.
- **Lưới Follow Up thao tác như Excel desktop**: lọc từng cột (danh sách giá trị có ô tìm + đếm số dòng), sắp xếp A→Z / Z→A, ẩn/hiện cột (nhớ theo máy), thanh công thức, thanh trạng thái hiện Tổng / TB / Nhỏ nhất / Lớn nhất của vùng chọn, Ctrl+D điền xuống, Ctrl+R điền sang phải, Ctrl+F tìm, Ctrl+H thay thế, chế độ dòng sát.
- **Công thức Excel đầy đủ hơn**: bộ hàm mở rộng (IF, IFERROR, AND, OR, NOT, SUM, MIN, MAX, AVERAGE, COUNT, COUNTA, ROUND, ROUNDUP, ROUNDDOWN, INT, ABS, MOD, POWER, SQRT, CEILING, FLOOR, LEN, LEFT, RIGHT, MID, TRIM, UPPER, LOWER, CONCAT, VALUE, ISBLANK, ISNUMBER, TODAY, DATE, YEAR, MONTH, DAY, DAYS) cho cả cột công thức cấu hình sẵn **và** công thức người dùng gõ thẳng vào ô (`=ROUND(W*0.95,2)`). Một cây công thức duy nhất dùng chung cho Python / trình duyệt / file Excel xuất ra — không sinh mã, không `eval()`. Bộ ca kiểm dùng chung: `tests/fx_cases.json`.
- **Phân công trực quan**: tab *Giao theo bộ lọc* (lọc → xem trước số lô + đang của ai + ví dụ → giao hàng trăm lô một lần), tab *Bảng kéo–thả* (mỗi thẻ là một nhóm **buyer + tuần EX-FACT**, kéo một lần giao cả nhóm), tab *Tải công việc*.
- **Dùng chung cùng lúc**: lưới tự kéo thay đổi của người khác về mỗi 7 giây và tô sáng dòng vừa đổi; hiện "ai đang sửa dòng nào" và số người đang mở trang; SQLite thêm `busy_timeout` + `synchronous=NORMAL`; Automation nay cũng tăng `version` và đóng dấu thời gian khi sửa, nên khóa lạc quan chặn được cả xung đột người ↔ Automation.
- **Quy mô**: index mới cho `backup_owner`, `updated_at`, và (`is_active`, `archived`, `exfty_date`); ô lọc buyer đổi sang gõ-tìm thay cho danh sách thả xuống hàng trăm dòng.
- **Sửa lỗi**: trùng `shipment_code` khi lô chưa có EX-FACT DATE và ngày trên đồng hồ khác ngày quét (bộ đếm thứ tự và mã lô lấy hai ngày khác nhau) — lỗi này làm hỏng cả lần quét. Trang nhập Excel hiện số "cập nhật" bị in ra tên phương thức thay vì con số.

## 3.0.0

- **Booking tách riêng khỏi lô hàng**: split (1 lô nhiều booking theo số lượng), gộp (nhiều lô 1 booking), Hold / Giải tỏa / Trễ lịch / Xin hủy / Hủy, Rebook giữ lịch sử, ghi nhận xuất một phần. Ba trạng thái riêng Booking / Hold / Xuất hàng. EX018, EX019.
- **Email Outlook desktop**: đọc Inbox + Sent Items, phân loại, đọc PDF booking confirmation (pdfplumber), ghép lô theo Conversation + điểm tin cậy, hàng chờ duyệt, email ≠ PDF bắt buộc chọn, không ghi đè ô người dùng đã sửa (EX020). `bookingctl outlook --demo`.
- **Chứng từ có version** + Document Matrix theo Mode / Buyer / Forwarder, hạn, duyệt / trả lại.
- **Quy trình B1 → B15.1**: task tự sinh, hạn theo EX-FACT / ETD, BLOCKED / OVERDUE, tự hoàn thành khi có bằng chứng, trang *Việc của tôi*, nhắc task / chứng từ quá hạn.
- **Weekly Planning hạn 12:00 thứ 6**: lô tuần sau từ thứ 5, nhắc theo mốc, Quản lý chốt.
- Vai trò **Auditor** (chỉ đọc) + trang ma trận quyền.
- Hiệu năng: cache nhận diện header Excel, index mới, đồng bộ booking / task hàng loạt, phân trang lưới phía máy chủ.

## 2.0.0

- Web nội bộ nhiều người dùng: đăng nhập, phân quyền Admin / Quản lý / Người dùng / Chỉ xem, phân công lô hàng.
- Lưới Follow Up 47 cột giống Excel (Jspreadsheet CE), công thức đúng như file thật, USER > AUTOMATION, optimistic lock.
- Xuất Excel theo quyền (1 Buyer = 1 sheet), nhập Excel có xem trước.
- Bỏ "Bot": Automation Engine với job + lịch sử chạy.

## 1.1.0

- Khớp cấu trúc 47 cột của file Follow Up thật, ghi Excel lossless (lxml), nhận diện merged cell, công thức, NO từ invoice.

## 1.0.0

- Quét Follow Up, Status Engine, container, deadline D-20 → D-1, ghi Weekly Planning / HS Code / MM Daily / Lịch xuất, Exception Center, Dry Run.
