# Việc 10: kiểm tra độc lập K2 (thiết kế bản 3, CHỜ DUYỆT)

Soạn ngày 2026-11-25. Bản 3 sửa bản 2 để **không lệch đặc tả**: định nghĩa độc lập theo nguyên văn mục 7.2,
bỏ các luật nhóm tự thêm. Chưa có dòng code nào.

Đặc tả, mục 7.2:

- `roots(c)`: tập nguồn gốc ban đầu (Source) truy xuất từ chứng thực c qua đồ thị lineage.
- Hai chứng thực chỉ được coi là độc lập khi tập roots của chúng **không giao nhau** và đến từ **các domain
  khác nhau**.
- Quyền thực thi hành động chỉ dựa vào các chứng thực có roots toàn là nguồn T, hoặc người dùng xác nhận trực
  tiếp. (Đã làm cùng đợt sửa theo đặc tả: cổng hành động nhận root của version có roots toàn T.)

Tiêu chí FR6: chứng thực tương quan (K2) không làm tăng trust score. Mục 7.3: trust score có n = số chứng thực
độc lập; chỉ dùng để xếp hạng, không cấp quyền. Luồng ghi bước 5: ghi trust score cùng version.

## 1. Lineage xuyên message (để roots đúng, FR2)

Message giữa agent là một Source kind=agent; hiện roots dừng ở đó nên planner ghi lại thông tin nhận từ coder
trông như không liên quan tới README coder đã đọc. Khi người nhận observe message, ghi thêm các lần đọc của
người gửi vào `context_reads` của người nhận, nhãn mỗi dòng kẹp bằng nhãn message.

- Nhãn L của người nhận không đổi (nhãn message = min(A, L người gửi) ≤ nhãn mọi thứ người gửi đã đọc).
- Cổng hành động không đổi (nguồn T của người gửi tới người nhận chỉ còn ≤ A, không vào `trusted_sources()`).
- TMA-NM (`boundary_labeling` tắt) không chuyển lần đọc, để giữ đúng ablation.
- Không đổi schema.

## 2. Chứng thực và độc lập (theo nguyên văn 7.2)

- **Cạnh `corroborates`** (có trong ProvenanceEdge của đặc tả; đặc tả không nói cách tạo): worker
  (`process_pending`, inline) so version mới với tối đa 10 version active cùng scope; cặp có cosine ≥ ngưỡng và
  chung ít nhất một giá trị claim (email, URL/host, IP, số tài khoản; số tiền chỉ khi đi kèm giá trị khác) thì
  ghi cạnh và audit `op="corroborate"`. Ngưỡng chỉnh trên BGE; unit test dùng embedder giả.
- **Độc lập:** hai chứng thực độc lập khi `roots` (toàn bộ, không lọc) không giao nhau **và** tập domain của
  roots không giao nhau. "Giao nhau" xét theo nguồn: hai Source cùng `content_hash` là cùng một nguồn (cùng
  README đọc hai lần), đúng ghi chú "dùng để kiểm tra độc lập" của Source và index `content_hash` ở mục 6.
- **n(m):** gom {m} ∪ chứng thực của m thành nhóm bằng union-find (nối hai bằng chứng nếu roots giao nhau hoặc
  domain giao nhau); các nhóm khác nhau đôi một độc lập. **n = số nhóm − 1.**
- **Hệ quả của nguyên văn:** mọi agent trong harness đều đọc đề bài người dùng (domain `user`), nên các chứng
  thực trong cùng kịch bản luôn chung domain và n = 0. FR6 vẫn đo được (n và trust score không tăng). Trường hợp
  dương test bằng các mục chỉ có nguồn web, không có đề bài chung.
- **Chế độ hỏng cho việc 18** (`memgate.independence: by_agent`): n = số agent khác nhau − 1. Mặc định `roots`.
- Module mới `memgate/independence.py`.

## 3. Trust score (bản tạm tới việc 12)

- Ghi `versions.trust_score = σ(w0·b + w1·log(1+n))` khi ghi version và khi có cạnh chứng thực mới
  (b = 1 / 0,5 / 0 cho T / A / U; c, t bằng 0 tới việc 11b, 12; trọng số cố định).
- Xếp hạng truy xuất có kết hợp trust score (luồng truy xuất bước 2): code sẵn, bật sau khi việc 12 hiệu chỉnh
  trọng số, vì bật bằng trọng số chưa hiệu chỉnh sẽ đổi mọi kết quả đã đo. Đây là phần chưa xong, không phải lệch.

## Test (SQLite và PostgreSQL)

- K2: planner và coder cùng đọc một README U (kể cả planner chỉ nhận qua message) rồi cùng ghi endpoint:
  n = 0, trust score không tăng, thứ hạng không tăng.
- `by_agent`: cùng tình huống, n = 1, mục leo hạng.
- Dương: hai nguồn web khác domain, khác nội dung → n = 1.
- Không độc lập: cùng `content_hash` khác domain (mirror); khác nội dung cùng domain; chung đề bài người dùng.
- Lineage: roots xuyên message; L và authorize giống hệt trước; TMA-NM không chuyển lần đọc.
- Chứng thực từ nguồn U, n bao nhiêu cũng không cấp quyền hành động.
- Hồi quy readonly K1, K3, MEM-INV: trace giống hệt.

## Ghi vào "Khác biệt so với đặc tả"

Không có chỗ lệch mới. Chỉ ghi các chỗ làm rõ: cách tạo cạnh `corroborates` (đặc tả không quy định) và "cùng
nguồn" xét theo `content_hash`.

**Phạm vi:** khoảng 2 ngày. File: `memgate/independence.py` (mới), `core.py`, `normalize.py`, `lifecycle.py`,
`store/*` (đọc `context_reads`), `tests/test_independence.py`. Kịch bản K2 chạy với LLM thuộc việc 16.
