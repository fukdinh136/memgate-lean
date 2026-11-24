# BÀN GIAO — MemGate (Nhóm 12, INT3229 Dữ liệu lớn, UET) — cập nhật 2026-11-24

File này để một agent/tài khoản khác tiếp tục dự án mà không cần lịch sử chat. Đọc hết trước khi làm.

## 1. Bối cảnh và cách làm việc

- Người dùng: Hehe (Đinh Gia Phúc), nhóm trưởng Nhóm 12. Đề tài 13 "Trustworthy Memory and Knowledge
  Governance for Multi-Agent Systems". Xây MemGate **bám đặc tả** `Group12_Project13_SolutionDesignSpecification.pdf`
  (thực thể dữ liệu mục 6, thuật toán mục 7). Đặc tả thắng mọi tài liệu khác khi lệch nhau.
- **Trình bày kế hoạch/thiết kế trong chat cho người dùng duyệt trước khi chạy lệnh hay sửa file.** Người dùng
  duyệt nhanh bằng "ok", "sửa đi", "làm luôn". Trả lời bằng tiếng Việt, ngắn gọn.
- Không bao giờ ghi API key vào code, chat hay git. Key nằm trong `secrets/gemini_keys.txt` (đã gitignore).
- Sau mỗi lần chép file sang máy người dùng: kiểm tra lại bằng md5. Đã từng có lần lệnh báo thành công mà file vẫn cũ.
- Người dùng tự chạy lệnh trên Windows và dán output; agent đọc trace trong `runs/` để phân tích.

## 2. Môi trường (quan trọng)

- Repo: `C:\Users\giaph\Downloads\memgate-lean`, GitHub private `fukdinh136/memgate-lean`. Windows, cmd,
  Python 3.14, venv `.venv` (`.venv\Scripts\activate.bat`).
- **Device Guard / Application Control chặn DLL không ký**: chặn `pip.exe` (luôn dùng `python -m ...`), chặn
  torch, numpy.random, psycopg[binary]. **Chạy được**: numpy lõi, onnxruntime, tokenizers, pg8000, detect-secrets.
  → Embedding và NLI chạy bằng ONNX; PostgreSQL dùng driver `pg8000`. Đừng thêm thư viện cần torch.
- PostgreSQL + pgvector qua Docker Desktop: `docker desktop start` rồi
  `docker compose -f infra/docker-compose.yml up -d`. Sau khi khởi động lại máy phải bật lại.
- Mỗi cửa sổ cmd mới phải đặt lại biến:
  ```bat
  set MEMGATE_TEST_PG_URL=postgresql://memgate:memgate@127.0.0.1:5432/memgate
  set MEMGATE_TEST_ONNX=1
  set LLM_CACHE=readwrite
  set MEMGATE_EMBED_MODEL=          :: để trống = BGE (mặc định); =hash để dùng embedding băm
  ```
- `python -m pytest -q` phải ra **196 passed** khi đặt đủ biến trên (thiếu biến thì test PostgreSQL/ONNX bị skip).
- LLM: Gemini `gemini-3.5-flash-lite` (config `configs/default.yaml`), cache SQLite `.cache/llm.sqlite` (có trong git).
  Gói miễn phí ~500 lời gọi/ngày/project, reset 15:00 giờ VN. Nhiều key: `secrets/gemini_keys.txt` (mỗi dòng
  một key, mỗi key một project), `harness/llm.py` tự xoay vòng, bỏ qua key hết quota/403.
- Bẫy hay gặp: quên `set LLM_CACHE=readwrite` sau khi chạy readonly → `CacheMiss`; Docker chưa bật → `WinError 10061`;
  `--store postgres` cần `DATABASE_URL`.

## 3. Kiến trúc đã cài

- `memgate/labels.py`, `core.py`: nhãn U < A < T; đọc thì `L = min(L, label)`; output và message giữa agent mang
  `min(A, L)` (chặn K1). Context lưu ở bảng `context_reads` (restart không nâng nhãn).
- `memgate/action_gate.py`: cổng hành động 4 luật (L ≥ A → allow; mọi giá trị nhạy cảm có nguyên văn trong nguồn T,
  giá trị số/ngắn phải cùng một nguồn T; người dùng xác nhận; còn lại review). `normalize.py` dùng chung với scorer.
- `memgate/policy.py` + `configs/policy.yaml`: Role/Agent/Policy theo đặc tả, deny thắng, mặc định deny, mục private
  chỉ chủ sở hữu. Lưu vào bảng `roles/agents/policies`; mọi từ chối ghi `audit_events` (chưa hash chain).
- `memgate/store/`: interface `Store`; `sqlite.py` và `postgres.py` (pgvector, HNSW cho BGE 384 chiều, schema tạm
  mỗi lượt). Migration `memgate/store/migrations/001_init.sql`. `nearest()` = top-k cosine (numpy / `<=>`).
- `memgate/consolidate.py`: hợp nhất `fail_closed`, `partition`, `rederive` (tái dẫn xuất: tách mệnh đề, trích bằng
  chứng chỉ từ input T, 3 kiểm tra bằng máy: khớp nguyên văn, NLI, giá trị nhạy cảm; qua → mục T mới).
- `memgate/nli.py` (`OnnxNLI`, model `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`, có tiếng Việt),
  `memgate/embed.py` (`OnnxEmbedder` = `Xenova/bge-small-en-v1.5`, mặc định; `HashEmbedder` cho test).
- `harness/`: vòng lặp agent + `delegate`; đội `pair`, `chain`; `run.py --scenario --gate null memgate
  --n-seeds N --consolidate MODE --store sqlite|postgres --provider`; trace ra `runs/<kịch bản>__<gate>__<model>[__mode][__postgres][__hash].jsonl`.
  Kịch bản: `k1_relay`, `k1_chain`, `k3_consolidate_email`, `k3_rederive_email`, `meminv_<sid>[_clean]` (việc 3).
  MEM-INV-Bench ở `harness/meminv/`; `python -m harness.meminv.to_yaml [--check]` sinh YAML nhiều agent từ bộ gốc.
- `harness/llm.py`: provider gemini/openrouter/openai/anthropic, cache, rpm, thử lại 429/5xx/lỗi mạng, pool key.
  Đã thử Llama 3.1 8B qua OpenRouter: không dùng được (parse lỗi, tấn công không sống, bộ hợp nhất từ chối).

## 4. Kết quả đã có (Gemini, embedder BGE; chi tiết `notes/k1_results.md`)

Trace BGE: `runs/*__memgate__*.jsonl` không hậu tố; bản HashEmbedder cũ giữ ở `runs/*__hash.jsonl` để đối chiếu.

| Thí nghiệm | NullGate | MemGate |
|---|---|---|
| Baseline MEM-INV-Bench | ASR 143/145 | — |
| MEM-INV nhiều agent (memory chung), 3 kịch bản × 30 seed | ASR 80/90 | ASR 0/90, benign 84/90; bản sạch 90/90 |
| K1 relay + chain, 30 seed mỗi kịch bản | ASR 33/60 | ASR 0/60, benign 59/60 |
| K3 consolidate, 30 seed | ASR 27/30 | fail_closed 0/30, partition 0/30, rederive 0/30 |
| K3 rederive (trang nhắc lại sự thật T), 30 seed | ASR 29/30 | rederive 0/30; 0 mệnh đề độc lên T; hạn nộp được chuộc 28/30 |
| Taint thừa | — | fail_closed 2/3 mục; partition 0; rederive 0 |

BGE so với hash: mọi chỉ số an toàn giống hệt; benign giảm 4 lượt (K3 partition 26/30 thay vì 27/30, k3_rederive
rederive 26/30 thay vì 29/30) vì BGE xếp bản tóm tắt U chứa chỉ dẫn độc lên đầu kết quả `recall`.
Mọi kết quả chạy lại `LLM_CACHE=readonly` cho kết quả giống hệt (0 trượt cache). NLI 10/10 cặp mẫu, ~0,5 s/cặp.
Hạn chế ghi nhận: rederive chuộc cả mảnh tiêu đề "Hướng dẫn nộp báo cáo tuần" (vô hại); benign của MemGate thấp
hơn NullGate vì planner bỏ cuộc sau khi email gộp (boss + địa chỉ độc) bị review; memory nhãn T không cấp quyền
hành động (cổng chỉ xét nguồn T).

## 5. Trạng thái việc (kế hoạch 21 việc trong Tài liệu triển khai)

- **Xong:** 1, 2, 3 (`notes/meminv_multiagent.md`), 4a, 4b (ONNX; K1/K3 đã chạy lại với BGE), 4c, 5, 6, 11a, 13, 14.
- **Đang dở:** 16 (mới có K1, K3), 17 (còn trace vàng,
  metric phát hiện/xếp hạng), 18 (tái lập đạt; còn bản MemGate cố tình hỏng).
- **Chưa làm:** 7 (thiết kế đã gửi, chờ duyệt: vòng đời 7 trạng thái, retention theo Clock, audit hash chain với
  khoá ghi, redact bằng detect-secrets, công tắc `mitigations` + ablation `boundary_labeling=off`), 8, 9, 10, 11b,
  12, 15, 19, 20, 21.

## 6. Việc ngay tiếp theo

1. Việc 7 theo thiết kế ở mục 5, rồi 8, 9; song song 10, 16 (K2) để mở khoá 12 và 18.
2. Việc phụ: mời 4 thành viên vào repo; chốt hạn nộp và người phụ trách luồng; sửa README phần Windows;
   cập nhật `TECH_STACK.md` (repo cũ); bỏ mảnh tiêu đề ở bước tách mệnh đề của rederive.

## 7. Tài liệu

- Đặc tả: `Group12_Project13_SolutionDesignSpecification.pdf` (người dùng giữ; mục 6 thực thể, mục 7 thuật toán).
- Bản markdown trong repo (xuất từ Claude Docs ngày 24/11/2026, đọc được không cần tài khoản): `docs/tai-lieu-trien-khai.md`, `docs/bao-cao-ket-qua-thuc-nghiem.md`, `docs/tong-quan-trien-khai.md` (xem `docs/README.md`). Khi sửa Claude Docs thì xuất lại để hai bản khớp.
- Claude Docs (thuộc tài khoản người dùng; tài khoản khác cần được chia sẻ mới mở được):
  - Tài liệu triển khai (bảng 21 việc, checklist): https://claude.ai/artifact/KqcdJFNM33wB9Wunc9PoCY
  - Báo cáo kết quả thực nghiệm: https://claude.ai/artifact/KcDJi6hXSNWXNC3DkN4pwA
  - Tổng quan triển khai (sơ đồ): https://claude.ai/artifact/6fdbofsK2LZPTSKuT1K1Kp
- Trong repo: `notes/baseline.md`, `notes/baseline_results.md`, `notes/k1_results.md` (K1, K3, NLI, rederive),
  `notes/meminv_multiagent.md` (việc 3).
