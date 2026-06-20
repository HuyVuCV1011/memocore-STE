# MemoCore & LinkedIn Content Engine Integration Bridge

Tài liệu này định nghĩa hợp đồng tích hợp giữa **MemoCore (memocore-STE)** và **LinkedIn Content Engine** (nằm ở thư mục `Content`). Việc này đảm bảo các AI Agent khác nhau khi làm việc trên hai repository này không ghi đè hoặc phá vỡ các giả định của nhau.

---

## 1. Nguyên tắc chia sẻ Cơ sở dữ liệu (Database Sharing)

*   **Đường dẫn Database:** `D:\HuyVu-Workspace\01_Code\memocore-STE\data\memocore.db`
*   **Chế độ truy cập (Access Mode):** Hệ thống Content chỉ được phép truy cập database ở chế độ **Chỉ đọc (Read-Only)** thông qua SQLite.
    *   *Lý do:* Tránh xung đột ghi dữ liệu (Database Lock / WAL conflicts) khi bot MemoCore đang chạy trực tuyến qua PM2 (`memocore-ste`).
*   **Các bảng dữ liệu được sử dụng:**
    *   `notes`: Đọc `raw_text`, `tags`, `created_at` để quét ghi chú thô.
    *   `memory_items`: Đọc các thông tin cá nhân (`bucket = 'profile'`), thông tin dự án (`bucket = 'project'`) có trạng thái `status = 'active'` làm ngữ cảnh viết bài.
    *   `tasks`: Đọc các công việc đã hoàn thành (`status = 'done'`) làm chất liệu viết bài thực tế.

---

## 2. Quy ước Hashtag cho Content (Hashtag Contracts)

Khi người dùng ghi chú qua Telegram, các hashtag sau được dành riêng cho hệ thống Content:

| Hashtag | Ý nghĩa | Hành động của Content Engine |
| --- | --- | --- |
| `#linkedin` hoặc `#li` | Chất liệu thô dành cho bài viết LinkedIn | Quét note này để tạo nháp bài viết |
| `#lesson` | Bài học kinh nghiệm từ công việc thực tế | Tập trung khai thác góc nhìn cá nhân và đúc kết |
| `#story` | Câu chuyện thực tế (gặp khách, làm dự án) | Tập trung viết theo lối kể chuyện (storytelling) |

> [!IMPORTANT]
> **Yêu cầu đối với MemoCore Agent:**
> *   Khi thực hiện dọn dẹp bộ nhớ (memory consolidation) hoặc cập nhật profile, Agent của MemoCore **không được tự ý xóa hoặc sửa đổi** các ghi chú có chứa các hashtag trên.
> *   Đảm bảo giữ nguyên danh sách `tags` trong bảng `notes` khi trích xuất thông tin.

---

## 3. Lộ trình tích hợp nâng cao (V5 Orchestration)

Trong tương lai, khi phát triển tính năng giao việc trực tiếp cho AI bằng cú pháp `@`, `#`, `/` trên Telegram:
*   **Giao việc:** Người dùng nhắn `@ai_content viết bài về chủ đề X`.
*   **Xử lý:** MemoCore Agent sẽ trích xuất tin nhắn này thành một `Task` với tag là `ai_task` và lưu vào SQLite.
*   **Thực thi:** Content Engine sẽ quét bảng `tasks` tìm các task có tag `ai_task`, tự động thực hiện và đẩy lại kết quả nháp vào một bảng trung gian hoặc gửi thông báo.

---
*Tài liệu này được tạo ra để đồng bộ hóa hoạt động giữa các Agent. Vui lòng cập nhật tài liệu này nếu có bất kỳ thay đổi nào về cấu trúc database hoặc quy ước tag.*
