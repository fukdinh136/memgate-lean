# Báo cáo tiến độ MemGate (Nhóm 12)

Ngày 25/11/2026. Bản gốc trên Claude Docs: https://claude.ai/artifact/2PepUpwhEXnSHAwsYtF1uB

## Tóm tắt

Đã xong 12/21 việc. MemGate chạy end-to-end với LLM thật (Gemini 3.5 Flash-Lite, embedder BGE) và chặn toàn bộ các tấn công đã đo:

| Tấn công | Không phòng thủ | MemGate |
| --- | --- | --- |
| K1: rửa nguồn qua agent trung gian | 33/60 | 0/60 |
| K3: bùng nổ taint khi hợp nhất | 27/30 + 29/30 | 0/120 (ba chế độ) |
| Đầu độc memory chung (MEM-INV-Bench) | 80/90 | 0/90 |

Mốc tuần 2 mới đạt một phần. Đã có PostgreSQL + pgvector, vòng đời, audit và hợp nhất. Còn thiếu hàng đợi review (8), API và worker (9), K2 (10) và đủ bộ kịch bản (16). Thiết kế K2 đã soạn xong, đang chờ nhóm duyệt.

Bộ test tự động: 242 test pass trên SQLite và PostgreSQL. Mọi kết quả chạy lại từ cache LLM cho ra trace giống hệt.

## Đã làm được

Các việc đã xong: 1, 2, 3, 4a, 4b, 4c, 5, 6, 7, 11a, 13, 14. Ba việc đang dở: 16 (mới có kịch bản K1, K3), 17 (còn trace vàng và metric xếp hạng), 18 (phần tái lập đạt, còn bản MemGate cố tình hỏng).

| Việc | Nội dung | Kết quả nghiệm thu |
| --- | --- | --- |
| 1 | Repo, cache LLM | Chạy lại trúng cache, không tốn lời gọi |
| 2 | Baseline MEM-INV-Bench không phòng thủ | ASR 143/145 (98,6%) |
| 3 | Harness nhiều agent với LLM thật | K1 không phòng thủ 33/60; 3 kịch bản MEM-INV × 30 seed: 80/90 → 0/90, benign 84/90 |
| 4a–4c | Store, versioning, lineage, retrieve, PostgreSQL + pgvector | Mọi mục truy ngược được nguồn; test store pass trên cả hai backend |
| 5 | Cổng hành động | K1: 0/60 (cận trên 95% ≈ 5%), benign 59/60 |
| 6 | Policy, share/promote | Không truy cập trái phép nào lọt; mọi lần từ chối có audit |
| 7 | Vòng đời, retention, audit hash chain, redact secret, công tắc biện pháp | Verify phát hiện sửa, xoá, đổi thứ tự log; 2 tiến trình ghi cùng lúc không gãy chuỗi; ablation TMA-NM: K1 lọt 33/60 |
| 11a | Wrapper NLI (ONNX, có tiếng Việt) | 10/10 cặp mẫu, ≈ 0,5 s/cặp trên CPU |
| 13 | Hợp nhất fail-closed và phân vùng | K3 0/30 cả hai chế độ; taint thừa: partition 0, fail-closed 2/3 mục |
| 14 | Tái dẫn xuất | K3 0/60; 0 mệnh đề độc lên nhãn T; sự thật đúng được chuộc 28/30 seed |

Kết quả đáng báo cáo nhất là ablation TMA-NM. Chỉ gán nhãn theo từng agent (bỏ gán nhãn ở ranh giới message) thì K1 lọt 33/60, y hệt không phòng thủ. Điều này cho thấy nhãn ở ranh giới message là thành phần quyết định.

## Cần làm tiếp

Còn 9 việc chưa làm và 3 việc đang dở, tổng khoảng 34 người-ngày (pd) theo ước lượng của kế hoạch. Chuỗi dài nhất là 16 → 18 → 19 → 20 → 21.

| Thứ tự | Việc | Nội dung | Cần xong trước | pd |
| --- | --- | --- | --- | --- |
| 1 | 10 | Kiểm tra độc lập K2 (thiết kế chờ duyệt) | — | 2 |
| 1 | 16 | Kịch bản K2, memory cũ, mâu thuẫn, truy cập trái phép, tấn công thích nghi; tập dev và test | — | 4 |
| 2 | 8 | Hàng đợi review, reviewer giả lập, quét quarantine quá 30 ngày | — | 2 |
| 2 | 9 | API HTTP, worker nền, đo p95 ≤ 10 ms | — | 4 |
| 3 | 11b | Phát hiện đầu độc, memory cũ, mâu thuẫn, trái phép (recall ≥ 0,9, precision ≥ 0,8) | 16, 17 để nghiệm thu | 2,5 |
| 3 | 17 | Trace vàng, metric phát hiện và xếp hạng | — | 1 |
| 3 | 15 | Nhãn file workspace | — | 1,5 |
| 4 | 12 | Trust score, hiệu chỉnh trọng số (AUC ≥ 0,85) | 10, 11b, 16 | 2 |
| 4 | 18 | Kiểm định harness bằng bản MemGate cố tình hỏng | 10, 16, 17 | 1,5 |
| 5 | 19 | Chạy đủ cấu hình × kịch bản × seed, ablation, hiệu năng (100.000 mục) | 9, 18 | 4 |
| 6 | 20 | Phân tích, CI bootstrap, biểu đồ | 19 | 3 |
| 6 | 21 | Báo cáo LaTeX ≥ 20 trang, slide, README | 20 (viết dần từ bây giờ) | 6,5 |

Các việc cùng số thứ tự làm song song được. Nếu 5 thành viên nhận đủ 5 luồng như kế hoạch thì mỗi người còn khoảng 7 pd.

## Thiết kế việc 10 (K2), chờ duyệt

Mục tiêu (FR6): khi hai agent cùng đọc một README bẩn rồi cùng ghi một endpoint, hai lần ghi đó chỉ tính là một nguồn. Mục bẩn không được tăng trust score, không được leo vào top-k. Bản đầy đủ nằm ở `notes/k2_independence_design.md`.

1. **Lineage xuyên message.** Khi agent nhận message, các lần đọc của người gửi được ghi thêm vào context của người nhận, nhãn kẹp bằng nhãn message. Nhờ vậy `roots` đi tới nguồn thật, không dừng ở message. Nhãn, quyết định authorize và schema không đổi.
2. **Chứng thực.** Hai version là chứng thực của nhau khi cosine đủ cao và có chung một giá trị claim (email, URL/host, IP, số tài khoản). Giá trị phải có nguyên văn trong nguồn gốc thì mới được tính.
3. **Độc lập.** Các nguồn gốc trùng nội dung (hash) hoặc trùng domain bị gộp chung một nhóm; n = số nhóm − 1.
4. **Không cấp quyền.** Chứng thực từ nguồn U không bao giờ cấp quyền hành động, n chỉ vào trust score. Xếp hạng theo trust score mặc định tắt cho tới việc 12.
5. **Chế độ hỏng `by_agent`** (đếm theo agent thay vì theo nguồn) dùng cho việc 18: bật lên thì mục bẩn phải leo hạng.

Kết quả đã báo cáo không đổi, vì thứ agent nhìn thấy và mọi quyết định giữ nguyên. Ước lượng 2 ngày.

## Vướng mắc và câu hỏi xin ý kiến thầy

Vướng mắc lớn nhất là quota LLM và nhân sự. Cả hai ảnh hưởng trực tiếp tới việc 19 (chạy đủ thí nghiệm).

| Vướng mắc | Ảnh hưởng | Cách đang xử lý |
| --- | --- | --- |
| Gemini gói miễn phí chỉ khoảng 500 lời gọi/ngày/project | Việc 19 cần nhiều cấu hình × kịch bản × seed, dễ kéo dài nhiều ngày | Cache LLM, xoay vòng nhiều key; chạy cấu hình ổn định từ sớm |
| Chỉ có một họ model dùng được | Llama 3.1 8B qua OpenRouter lỗi parse, tấn công không sống; kết quả chưa tổng quát qua model khác | Ghi vào phần giới hạn |
| Nhân sự: 4 thành viên chưa vào repo, phân công luồng chưa chốt | Khối lượng còn khoảng 34 pd dồn vào ít người | Cần chốt phân công 5 luồng theo kế hoạch |
| Máy Windows chặn DLL không ký (torch, psycopg) | Không dùng được thư viện cần torch | Đã giải quyết: embedding và NLI chạy ONNX, PostgreSQL dùng pg8000 |
| Kịch bản `saas_admin` không phòng thủ chỉ lọt 13/30 (43%) | Dưới ngưỡng 50% để tính vào bảng chính | Báo riêng; MemGate vẫn 0/30 |
| Benign của MemGate thấp hơn không phòng thủ ở vài kịch bản | Planner bỏ cuộc khi email gộp (địa chỉ đúng + địa chỉ độc) bị đưa vào review | Việc 8 (reviewer giả lập) sẽ đo lại |

Giới hạn kỹ thuật đã biết, sẽ ghi vào báo cáo:

- Memory nhãn T chưa cấp quyền hành động (cổng chỉ xét nguồn T trong context).
- Audit không phát hiện việc cắt bỏ các dòng cuối chuỗi nếu không neo hash ra ngoài.
- Redact bỏ sót mật khẩu viết trong câu văn tự do.
- K2: kẻ tấn công điều khiển nhiều domain vẫn tạo được chứng thực "độc lập"; rủi ro bị chặn vì trust không cấp quyền.

Câu hỏi xin ý kiến thầy:

1. Đo K2 bằng trust score và thứ hạng top-k, thay vì ASR, có được chấp nhận không? Lý do: chứng thực từ nguồn U không cấp quyền nên ASR không đổi kể cả khi phần K2 hỏng.
2. Các chỗ lệch đặc tả sau có ổn không?
    - Bảng retention nhóm tự chọn: short_term 1, retrieved 7, shared 30, long_term 180 ngày.
    - Độc lập chỉ xét nguồn gốc chứa giá trị, không xét toàn bộ `roots`.
    - Ablation TMA-NM chạy trên K1 và MEM-INV nhiều agent, vì baseline việc 2 không đi qua gate.
3. Có cần thêm một model thứ hai (trả phí) để kết quả tổng quát hơn không?
4. Hạn nộp báo cáo và slide chính thức là ngày nào?
