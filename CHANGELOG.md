# Nhật ký thay đổi

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
