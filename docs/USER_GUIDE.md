# Hướng dẫn sử dụng

Dành cho người làm chứng từ / kế hoạch xuất khẩu. Không cần biết lập trình.

---

## Một ngày làm việc với Bot

| Giờ | Việc xảy ra |
|---|---|
| 08:00 | Bot quét lần đầu, popup nếu có lô gấp |
| Trong ngày | Bạn cập nhật Follow Up như bình thường |
| 09:00 · 12:00 · 15:00 | Bot quét lại, đổi màu dòng, điền Planning khi tới mốc |
| Bất cứ lúc nào | Chuột phải icon khay → **Quét ngay** nếu muốn cập nhật ngay |

Bạn **không cần đổi cách làm việc**. Cứ điền Follow Up như cũ; Bot đọc và làm phần còn lại.

---

## Đọc màu trong file Follow Up

Bot tô nền cả dòng theo trạng thái:

| Màu | Nghĩa | Bạn cần làm gì |
|---|---|---|
| ⚪ Trắng | Chưa booking | Đặt booking, nhất là khi còn dưới 14 ngày |
| 🟡 Vàng | Đã book, thiếu CDF hoặc BL | Theo dõi chứng từ |
| 🟢 Xanh lá | Đã có CDF + BL — xong khoảng 80% | Theo dõi tới ngày xuất |
| 🔴 Đỏ | Đang HOLD | Xử lý lý do hold; Bot nhắc lại mỗi 3 ngày |
| 🔵 Xanh dương | Đã xuất xong | Không cần làm gì |
| 🟠 Cam | Xuất một phần | Đóng tiếp phần còn lại |
| ⚫ Xám | Đã hủy booking | Đặt lại hoặc gộp vào lô sau |

**Đỏ luôn thắng.** Một lô có đủ CDF + BL nhưng đang HOLD vẫn là đỏ — vì hold là việc cần xử lý ngay.

**Xanh dương = đã xuất xong thật sự**, khi đủ cả: CDF# + BL# + CONT + SEAL (hàng đi container) + ETD đã qua. Hàng AIR / LCL không cần CONT + SEAL.

**Cam = xuất dở dang.** Cùng một NO mà có dòng đã ghi CONT + SEAL, có dòng chưa → cả nhóm cam, lý do ghi rõ "đóng xong 2/4 dòng". Đóng đủ + tàu chạy → chuyển xanh dương.

Bot **không tô cột A (NO)** — cột này là công thức, đang tô vàng để đánh dấu. Muốn đổi: `config.yaml → files → color_skip_columns`.

---

## Bạn vẫn có thể tô màu tay

Trước giờ trạng thái được đánh dấu bằng màu dòng — Bot giữ nguyên thói quen đó. **Màu bạn tô = bạn chốt trạng thái**, Bot không tô đè:

| Bạn làm trong Excel | Bot hiểu |
|---|---|
| Tô đỏ một dòng | Lô đang HOLD → nhắc lại mỗi 3 ngày |
| Tô vàng / xanh lá / xám | Chốt trạng thái theo màu đó |
| Tô lại đúng màu Bot đang tô | Trả lại cho Bot tự quản |
| Tô màu khác (tím, xám đậm…) | Dòng đó thuộc về bạn — Bot không tô đè, không đổi trạng thái |
| Xóa màu | Bot quản lại từ lần quét sau |

Mọi lần như vậy đều ghi vào Audit Log với tên `excel:<người dùng>`.

## HOLD / CANCEL ghi ở cột REMARK

File Follow Up không có cột HOLD riêng, nên chỉ cần gõ vào REMARK:

| REMARK | Bot hiểu |
|---|---|
| `HOLD - chờ khách duyệt mẫu` | RED, lý do = cả câu |
| `CANCEL booking, gộp lô sau` | CANCELLED |
| `UNHOLD` / `release hold` | **không** phải hold |

Danh sách từ khóa: `config/field_mapping.yaml → remark_keywords`.

## Bot kiểm tra CONT TYPE bạn đã book

Cột D (CONT TYPE) là container **bạn** book — Bot không đổi, chỉ kiểm tra có đủ chỗ không. Ô merge cho cả nhóm (`D2:D5`) được tính **một lần**, không phải mỗi dòng một cont.

| Bạn ghi | Tổng CBM nhóm | Bot báo |
|---|---|---|
| `1x20` | 15.14 | Khớp — đủ chỗ, dư 7.86 CBM |
| `1x20` | 27.1 | **THIẾU 4.1 CBM** — popup đỏ `EX014` |
| `2x40HC` | 115 | Đủ chỗ (ghi kèm phương án Bot tính được: 1x40HC + 1x40DC) |

Bot đọc được: `1x20`, `1x40`, `1x40HC`, `1X40HQ`, `2x40HC+1x20`, `LCL`, `AIR`.

## Cột công thức phải còn nguyên

Các cột **NO, 95% FOB, CDF AMOUNT, TSN AMOUNT** là công thức. Bot kiểm mỗi lần quét:

| Tình huống | Bot báo `EX013` |
|---|---|
| Ai đó gõ số đè lên công thức NO | "A13 bị gõ đè 999, đúng ra là 902 (tính từ INVOICE TEGV03010)" |
| Thêm dòng mới quên kéo công thức | "Dòng 14 chưa có công thức cột A" — Bot **tự tính** NO = 903 để không bị lỡ việc |

Cách sửa: kéo lại công thức từ dòng trên xuống. Bot **không bao giờ** tự sửa công thức của bạn.

---

## Bot nhắc gì, khi nào

| Mốc | Nội dung nhắc |
|---|---|
| Còn 20 → 14 ngày | "Chưa booking" — cảnh báo vàng |
| Còn dưới 14 ngày | "TRỄ BOOKING" — popup đỏ |
| Còn 7 ngày | Bot tự điền Weekly Planning + Custom Code |
| Còn 3 ngày | "Còn thiếu Carton, GW không hợp lệ" — nêu đích danh trường nào |
| Còn 1 ngày | Bot tự điền Lịch xuất hàng TDV/TGV |
| Đang HOLD | Nhắc lại mỗi 3 ngày cho tới khi hết hold |

Popup có đủ ngữ cảnh: JO, Buyer, ngày xuất, còn mấy ngày, lý do — và nút **Mở file nguồn** nhảy thẳng tới đúng sheet, đúng dòng.

Ngoài khung giờ 08:00–16:00 Bot vẫn quét nhưng **dồn thông báo lại**, phát khi tới giờ làm việc. Xem bất cứ lúc nào tại Dashboard → **Thông báo**.

---

## Dashboard

Mở bằng: chuột phải icon khay → **Mở Dashboard**, hoặc gõ <http://127.0.0.1:8765>

### Trang chủ — Booking Control Center

Số lô theo từng trạng thái, danh sách **cần xử lý gấp** và **lịch xuất sắp tới**. Nút **Quét ngay** ở góc phải.

### Lô hàng

Lọc theo Buyer · PIC · cơ sở · trạng thái · số ngày còn lại · tìm theo JO hoặc NO.

Bấm vào JO để mở chi tiết.

### Chi tiết lô hàng

Bốn khối thông tin:

**1. Thông tin lô** — tất cả trường đã chuẩn hóa, kèm nút mở file nguồn.

**2. Why {trạng thái}?** — từng bước Bot suy luận:

```
HOLD      NO
CANCEL    NO
BOOKING   YES
CDF       FILLED    CDF-556677
BL        EMPTY
KẾT LUẬN  YELLOW
```

Nhìn là biết ngay thiếu gì. Nếu Bot sai, bạn thấy ngay nó sai ở bước nào.

**3. Container** — tổng CBM của cả nhóm, số container từng loại, capacity, phần dư, và ghi chú giải thích.

**4. User Override** — chốt trạng thái theo ý bạn.

---

## Khi Bot sai: User Override

**Quy tắc bất di bất dịch: USER > BOT.** Bạn chốt gì, Bot tôn trọng cái đó ở mọi lần quét sau.

Cách làm: mở chi tiết lô → khối **User Override** → chọn trạng thái → điền tên và lý do → **Áp dụng**.

Sau đó:
- Bot vẫn tính trạng thái của nó (xem ở dòng "KẾT LUẬN") để bạn đối chiếu
- Nhưng màu hiển thị và màu ghi vào Excel theo bạn
- Mọi thay đổi đều vào **Audit Log**, có tên và thời gian

Muốn trả lại cho Bot: chọn dòng đầu `-- Bỏ override, dùng theo Bot --`.

Điều tương tự áp dụng cho container: bạn chốt phương án nào, Bot không tính lại nữa.

---

## Exception Center

Mọi thứ Bot **không chắc chắn** đều vào đây thay vì bị đoán bừa.

| Mã | Ý nghĩa | Cách xử lý |
|---|---|---|
| `EX001` | Ngày xuất sai hoặc trống | Sửa trong Follow Up |
| `EX003` | Nghi ngờ dòng trùng | Kiểm tra 2 dòng được nêu, xóa dòng thừa |
| `EX004` | CBM không hợp lệ (`"25?"`) | Đo lại và điền số chính xác |
| `EX005` | Sheet chưa rõ có phải Follow Up | Vào Mapping xác nhận một lần |
| `EX006` | File bị khóa / không đọc được | Đóng file trong Excel |
| `EX007` | Bạn sửa file lúc Bot đang ghi | Bot đã dừng, không ghi đè. Bấm Quét ngay |
| `EX008` | Buyer chưa có rule | Thêm vào `buyer_rules.yaml` |
| `EX009` | Không tự quyết được container | Vào chi tiết lô chọn thủ công |
| `EX010` | Header Excel đổi tên | Vào Mapping ánh xạ lại cột |
| `EX013` | Công thức bị gõ đè / thiếu | Kéo lại công thức từ dòng trên |
| `EX014` | CONT TYPE không đủ chỗ / không đọc được | Book thêm cont hoặc sửa cách ghi |

Xử lý xong bấm **Đã xử lý** (hoặc **Bỏ qua** nếu không liên quan).

> Exception **không** làm Bot dừng. Bot xử lý tiếp các lô khác bình thường.

---

## Dry Run — xem trước khi ghi

Dashboard → **Dry Run**, hoặc chuột phải icon khay → **Dry Run**.

Bot chạy toàn bộ quy trình nhưng **không đụng vào file nào và không gửi thông báo**, chỉ in ra:

```
Shipment           : 24 (mới 2, thay đổi 5)

Trạng thái:
  [!] YELLOW     5
  [+] GREEN      4
  [X] RED        2

Ghi file đích:
  F02        +3 ~2 =4
  F03        +3 ~0 =6

Gợi ý gộp lô: 6
```

`+` thêm dòng · `~` sửa dòng · `=` không đổi.

Nên chạy Dry Run mỗi khi vừa đổi cấu hình hoặc vừa thêm buyer mới.

---

## Backup & Rollback

Trước **mỗi lần** ghi, Bot copy file gốc vào `data\backups\<mã file>\`. Giữ 20 bản gần nhất.

Muốn quay lại: Dashboard → **Backup** → chọn file → chọn bản → **Rollback**.

Bot backup luôn bản hiện tại trước khi rollback, nên có thể undo lần nữa.

---

## Cài đặt thường chỉnh

Dashboard → **Cài đặt**:

| Mục | Khoảng cho phép | Mặc định |
|---|---|---|
| Chu kỳ quét | 1–4 giờ | 3 giờ |
| Khung giờ thông báo | bất kỳ | 08:00–16:00 |
| Nhắc lại khi Hold | 1–7 ngày | 3 ngày |
| Cửa sổ booking | tùy | T-20 → T-14 |
| Tô màu file Follow Up | Có / Không | Có |
| Luôn chạy Dry Run | Có / Không | Không |

Thay đổi ở đây có hiệu lực ngay. Muốn giữ vĩnh viễn (sau khi tắt Bot), sửa thêm trong `config\config.yaml`.

---

## Các tình huống nghiệp vụ Bot đã xử lý

**JO tách thành 2 ngày xuất** — hai dòng cùng JO khác `ExFtyDate` là **hai lô riêng**, mỗi lô có deadline riêng. Không bị báo trùng.

**Đã book rồi lại hold** — chuyển sang đỏ, giữ nguyên thông tin booking, nhắc lại mỗi 3 ngày.

**Đã book rồi lại cancel, chờ gộp lô sau** — điền `Cancel = Yes` → xám. Bot còn tự gợi ý ghép: cùng buyer, ngày xuất cách nhau ≤ 7 ngày, tổng CBM vừa một container → hiện ở Dry Run, mục *Gợi ý gộp lô*. Bot chỉ gợi ý, bạn quyết định.

**Xuất tối nay một nửa, mai đóng tiếp** — ghi CONT + SEAL cho các dòng đã đóng. Nhóm cùng NO còn dòng chưa đóng → cả nhóm cam (`PARTIAL`, "đóng xong 2/4 dòng"). Đủ CONT + SEAL + CDF + BL và ETD đã qua → xanh dương (`COMPLETED`). File có cột `Shipped Qty` thì Bot dùng thêm cột đó.

**JO tách KR01 / KR02 nhưng xuất chung** — hai JO khác nhau, cùng invoice → cùng NO → Bot coi là **một nhóm container**, tính CBM chung.

**Chưa có invoice** — NO còn trống (công thức trả ""). Bot vẫn theo dõi lô bằng JO + ngày xuất; khi invoice có, NO hiện ra và **lô vẫn giữ nguyên lịch sử**, không bị nhân đôi.

**Đổi ngày xuất** — Bot tính lại toàn bộ deadline, ghi Audit Log, và báo dòng cũ còn sót ở file Planning để bạn xóa. Bot **không tự xóa** dòng trong file dùng chung vì có thể có ghi chú của người khác.

**Nhiều invoice, nhiều tờ khai** — điền nhiều invoice cách nhau bằng `,` hoặc `;`. Bot tách và lưu riêng từng invoice, nhóm tờ khai theo `declaration_rule` của buyer.

---

## Những điều Bot KHÔNG bao giờ làm

- Ghi đè giá trị bạn vừa sửa trong Excel
- Ghi đè khi file đã thay đổi từ lúc Bot đọc
- Ép ghi khi file đang mở
- Xóa dữ liệu bạn nhập tay bằng ô trống
- Tạo dòng trùng dù chạy bao nhiêu lần
- Đoán bừa khi dữ liệu không rõ
- Tự quyết container khi buyer yêu cầu duyệt
- Xóa dòng trong file dùng chung
- Ghi đè hoặc sửa công thức của bạn
- Làm mất external link, defined name, comment, thiết lập in của file (Bot chỉ sửa đúng ô cần sửa)

---

## Câu hỏi nhanh

**Tắt máy thì sao?** Bot dừng. Lần đăng nhập sau tự chạy lại (nếu đã cài autostart) và quét bù ngay.

**Nhiều người cùng chạy Bot được không?** Được. Mỗi người quét file Follow Up của mình. Khi ghi vào file chung, Bot kiểm tra version — nếu người khác vừa ghi, Bot dừng và thử lại sau, không ghi đè.

**Bot có gửi dữ liệu đi đâu không?** Không. Chạy hoàn toàn local, dashboard chỉ lắng nghe `127.0.0.1`.

**Muốn tạm ngưng vài ngày?** Chuột phải icon khay → **Tạm dừng**. Bot giữ nguyên dữ liệu, không quét, không thông báo.

**Lỡ để Bot ghi sai hàng loạt?** Dashboard → Backup → Rollback về bản trước đó.
