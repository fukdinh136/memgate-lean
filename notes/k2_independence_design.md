# Việc 10: kiểm tra độc lập K2 (thiết kế bản 2, CHỜ DUYỆT)

Soạn ngày 2026-11-25. Bản 1 đã qua một vòng góp ý; bản này sửa theo 5 điểm góp ý và dùng cách làm lineage
không đổi schema do người dùng đề xuất. Chưa có dòng code nào.

**Mục tiêu (FR6).** Mục bẩn có chứng thực tương quan không được tăng trust score và không được leo vào top-k.
Tình huống điển hình: planner và coder cùng đọc một README bẩn rồi cùng ghi "endpoint là evil.com". Hai lần ghi
đó phải tính là **một** nguồn.

## Hiện trạng trong code

- `store.roots()` đã có (duyệt cạnh lineage về `Source` gốc), code chính chưa dùng.
- `Source.domain` đã có (host của URL, nếu không có URL thì lấy `kind`). Cạnh `corroborates` có trong model
  nhưng chưa nơi nào tạo. Cột `versions.trust_score` đã có, đang để trống.
- Việc 7 đã xong: `process_pending()` chạy inline sau mọi lần ghi, nên phần nhận diện chứng thực chạy ở đó,
  không cần đợi việc 9.
- **Lỗ hổng:** message giữa agent là một `Source` (kind=agent), không nối về những gì người gửi đã đọc. Planner
  ghi lại thông tin nhận từ coder thì `roots` dừng ở message và trông như độc lập với README (rửa nguồn K2 qua
  message).

## 1. Lineage xuyên message (không đổi schema)

Khi người nhận observe một message, ghi thêm các lần đọc của người gửi (source và version, bỏ những gì người nhận
đã có) vào `context_reads` của người nhận, nhãn mỗi dòng kẹp bằng `meet(nhãn gốc, nhãn message)`. Khi người nhận
ghi memory, cạnh `read_in_context` trỏ tới README, nên `roots` đi tới nguồn thật.

- **L của người nhận không đổi:** nhãn message = min(A, L người gửi) ≤ nhãn mọi thứ người gửi đã đọc, nên các
  dòng kẹp đều bằng nhãn message mà người nhận đã có.
- **Cổng hành động không đổi:** `AgentContext.sources` lấy nhãn từ dòng `context_reads` (đã kẹp), nên nguồn T
  của người gửi tới người nhận chỉ còn ≤ A và không vào `trusted_sources()`.
- **TMA-NM (`boundary_labeling` tắt) không chuyển lần đọc:** message mang nhãn T thì kẹp không còn tác dụng,
  lần đọc U của người gửi sẽ hạ nhãn người nhận và làm hỏng ablation. Khớp giả định TMA-NM là framework không
  có ranh giới.
- Luật phòng thủ: gốc kind=agent (message cũ chưa có lineage, nguồn "tự thân" của agent) không bao giờ được tính
  là bằng chứng.

## 2. Chứng thực và cách đếm n

- **Giá trị claim:** email, URL/host, IP, số tài khoản (dãy ≥ 8 chữ số, cho phép gạch). Số tiền chỉ tính khi
  cặp đó còn chung một giá trị khác (đi kèm người nhận). Năm, cổng mạng, số ngắn bị loại.
  Hàm `claim_values()` trong `normalize.py`.
- **Nhận diện trong `process_pending`:** tối đa 10 version active cùng scope có cosine ≥ `min_cosine` (tạm 0,80)
  và chung ít nhất một giá trị claim → cạnh `v -corroborates-> u` + audit `op="corroborate"`.
  Không thêm tool cho agent, prompt và cache LLM không đổi.
- **Chỉnh ngưỡng:** đo cosine BGE trên một bộ cặp nhỏ (cùng claim viết khác; claim khác nhưng chung giá trị),
  ghi vào notes. Unit test dùng embedder giả có vector định sẵn (ngưỡng vô nghĩa với embedder hash).
- **Gốc hỗ trợ** của bằng chứng e: root của e (kind khác agent) chứa giá trị claim nguyên văn (`appears_in`).
- **n(m):** gom mọi gốc hỗ trợ của {m} ∪ chứng thực của m bằng union-find (chung nhóm nếu trùng `content_hash`
  hoặc trùng domain); **n = số nhóm − 1**. Một version tự đọc 2 trang web độc lập có n = 1.
- **Chế độ hỏng cho việc 18:** `memgate.independence: by_agent` → n = số agent khác nhau − 1. Mặc định `roots`.
- Module mới `memgate/independence.py`: `support_roots`, `independent_groups`, `corroboration_count`.

## 3. Cổng hành động

- Chứng thực từ nguồn U không bao giờ cấp quyền. Chứng thực có roots toàn T, hoặc xác nhận của người dùng,
  cấp quyền qua luật 2 và 3 hiện có. Mặc định authorize không đổi.
- Cờ `memgate.authorize_memory_roots`, **mặc định tắt**: khi bật, kho nguồn T đối chiếu có thêm root T của
  version agent đã đọc, với đủ 3 điều kiện: (1) chính root mang nhãn T; (2) mục chứa version còn active lúc
  authorize; (3) giữ luật số tiền cùng nguồn với người nhận. Chỉ bật khi chạy lại toàn bộ ở tuần 4.

## 4. Trust score tạm và xếp hạng

- `trust_score = σ(w0·b + w1·log(1+n))`, b = 1 / 0,5 / 0 cho T / A / U; trọng số cố định; c và t bằng 0 tới
  việc 11b, 12. Tính lại cho cả hai đầu khi có cạnh chứng thực mới; ghi vào `versions.trust_score`.
- Xếp hạng `cos × (λ + (1−λ)·s)`, λ = 0,7, cờ `memgate.trust_ranking.enabled` **mặc định tắt** tới việc 12.

## Test (SQLite và PostgreSQL, embedder giả)

- K2: planner và coder cùng đọc README U rồi cùng ghi endpoint, kể cả khi planner chỉ nhận qua message:
  n = 0, trust score và thứ hạng không tăng.
- `by_agent`: cùng tình huống, n = 1, mục leo hạng (phép đo đủ nhạy).
- Dương: 2 web domain → n = 1; một version đọc 2 trang → n = 1.
- Bị loại: bản mirror; giá trị không có trong roots; hai file workspace khác nhau (cùng domain "file"); hai câu
  chỉ chung năm "2026".
- Lineage: `roots` xuyên message; L và authorize giống hệt trước; TMA-NM không chuyển lần đọc.
- Cờ `authorize_memory_roots`: tắt như cũ; bật thì root U, mục không active, số tiền khác nguồn đều bị từ chối.
- Hồi quy readonly K1, K3, MEM-INV: kỳ vọng 0 trượt cache, trace giống hệt.

## Khác biệt so với đặc tả và giới hạn

- Độc lập chỉ xét gốc hỗ trợ, không xét toàn bộ `roots` (root chung như đề bài task gần như luôn có).
  Gộp theo `content_hash` chặt hơn kế hoạch.
- Không có URL thì domain theo kind: các file workspace với nhau, các input người dùng với nhau không bao giờ
  độc lập (đếm thiếu, không đếm thừa).
- Kẻ tấn công điều khiển nhiều domain vẫn qua được; rủi ro bị chặn trên vì trust không cấp quyền.
- Câu phủ định chung giá trị ("không dùng evil.com") vẫn có thể thành chứng thực; mâu thuẫn thuộc việc 11b (NLI).
- Trust score là bản tạm, trọng số cố định.

**Phạm vi:** khoảng 2 ngày. File: `memgate/independence.py` (mới), `core.py`, `normalize.py`, `lifecycle.py`,
`action_gate.py`, `store/*` (đọc `context_reads`), `tests/test_independence.py`. Kịch bản K2 chạy với LLM thuộc
việc 16.
