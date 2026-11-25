# Tổng quan tài liệu cho trust score (việc 12)

Tổng hợp ngày 2026-11-25 từ tóm tắt và trích đoạn qua tìm kiếm web, **chưa đọc toàn văn**. Phần lớn paper năm
2026 là preprint chưa qua phản biện. Trước khi trích vào báo cáo phải đọc lại bản PDF.

Công thức giữ đúng mục 7.3 của đặc tả:

```latex
s(m) = \sigma\left(w_0 b + w_1 \log(1+n) - w_2 c - w_3 \frac{t}{\tau}\right)
```

b: độ tin gốc của nhãn và nguồn; n: số chứng thực độc lập; c: số mâu thuẫn chưa giải quyết; t: tuổi theo ngày.
Trust score chỉ dùng để xếp hạng, không cấp quyền hành động (mục 4, 7.3).

## 1. Truth discovery: tin cậy của nguồn và của sự thật

| Paper | Ý chính | Dùng cho |
| --- | --- | --- |
| TruthFinder: Yin, Han, Yu, KDD 2007 | Độ tin của nguồn và của sự thật phụ thuộc lẫn nhau, tính lặp; độ tin của sự thật xấp xỉ tổng độ tin các nguồn khẳng định nó, sau biến đổi log | n: căn cứ cho log(1+n), thêm chứng thực thì tăng chậm dần |
| Integrating Conflicting Data: The Role of Source Dependence: Dong, Berti-Equille, Srivastava, VLDB 2009 | Nguồn chép của nhau làm bỏ phiếu đa số sai; ước lượng xác suất chép nhau rồi giảm trọng số | n, K2: cơ sở lý thuyết của kiểm tra độc lập. Bài gốc phải ước lượng việc chép; MemGate biết chắc qua `roots` |
| Knowledge-Based Trust: Dong và cộng sự (Google), VLDB 2015 | Độ tin của nguồn web = tỉ lệ sự thật đúng nó cung cấp; tách lỗi trích xuất khỏi lỗi của nguồn | b: cách đặt `base_trust` theo nguồn |

## 2. Bằng chứng tích cực và tiêu cực

- **Beta Reputation System** (Jøsang và Ismail, 2002): cộng dồn bằng chứng tốt (r) và xấu (s) vào phân phối Beta;
  có nền thống kê. Dùng cho c (mâu thuẫn = bằng chứng tiêu cực). Hệ số quên cho t: **cần đọc lại PDF để xác nhận**.
- **Subjective Logic** (Jøsang): opinion gồm belief, disbelief, uncertainty, tương đương phân phối Beta; dùng nếu muốn
  biểu diễn cả mức chưa chắc.

## 3. Trust cho memory của LLM agent (2025–2026)

- **Securing LLM-Agent Long-Term Memory Against Poisoning** (TMA-NM, arXiv 2606.24322; tài liệu [1] của đặc tả):
  trust dựa trên nội dung bị đánh bại bởi tự tóm tắt, tool echo và chứng thực dựng sẵn; nhãn phải gắn với nguồn gốc.
  Căn cứ cho nguyên tắc "trust chỉ xếp hạng, không cấp quyền".
- **When Does Belief-Based Agent Memory Help?** (arXiv 2606.22030): trust = min(provenance, content), nội dung chỉ kéo
  điểm xuống, không vượt trần của kênh; tắt riêng tín hiệu nguồn thì tấn công thành công trở lại 100%. Ủng hộ việc b
  là thành phần nặng ký nhất. Ý "trần theo provenance" không có trong đặc tả: chỉ nhắc ở phần thảo luận, không cài.
- **Memory Provenance Laundering in LLM Agents** (arXiv 2607.29167): hợp nhất memory viết lại quan sát bên ngoài
  thành "lịch sử người dùng", mất nhãn. Tương ứng K3, dùng để so sánh.
- **A-MemGuard** (Wei và cộng sự, arXiv 2510.02373, ICML 2026; tài liệu [8] của đặc tả): kiểm tra đồng thuận giữa
  nhiều memory liên quan, dựa trên nội dung. Baseline đối chiếu.
- **MAPLE-Guard** (arXiv 2608.00426; có trong bảng công trình liên quan của đặc tả), **Utility Under Attack**
  (arXiv 2608.21230, giới hạn của xếp hạng theo provenance).
- Survey: arXiv 2604.16548 (bảo mật memory dài hạn), arXiv 2606.04990 (evidence tracing và provenance).
- Liên quan khác: EAL-Bench (arXiv 2609.01836), Compaction Laundering (arXiv 2610.29869), State Contamination
  (arXiv 2605.16746), Memory Poisoning Attack and Defense (arXiv 2601.05504).

## Áp vào việc 12

| Thành phần | Lấy từ | Cơ sở |
| --- | --- | --- |
| b | Nhãn T/A/U và `base_trust` theo loại nguồn | KBT, provenance-capped |
| n | Số chứng thực độc lập theo `roots` (việc 10) | TruthFinder, Dong 2009 |
| c | Số mâu thuẫn chưa giải quyết (NLI, việc 11b) | Beta reputation |
| t/τ | Tuổi theo ngày | Hệ số quên |
| w0..w3 | Hồi quy logistic trên tập dev có nhãn sạch/bẩn, mục tiêu AUC ≥ 0,85 trên tập test | Hiệu chỉnh chuẩn |

Các paper trên ủng hộ, không thay công thức của đặc tả.

## Nguồn

- TruthFinder: https://research.ibm.com/publications/truth-discovery-with-multiple-conflicting-information-providers-on-the-web
- Dong, Berti-Equille, Srivastava 2009: https://lunadong.com/publication/dependence_vldb.pdf
- Data Fusion (Google Research): https://research.google/pubs/data-fusion-resolving-conflicts-from-multiple-sources/
- Knowledge-Based Trust: https://arxiv.org/abs/1502.03519
- Beta Reputation System: https://mn.uio.no/ifi/english/people/aca/josang/publications/ji2002-bled.pdf
- Trust as opinion (Jøsang & Knapskog 1998): https://www.mn.uio.no/ifi/english/people/aca/josang/publications/jk1998-nsc.pdf
- TMA-NM: https://arxiv.org/pdf/2606.24322
- Belief-Based Agent Memory: https://arxiv.org/pdf/2606.22030
- Memory Provenance Laundering: https://arxiv.org/pdf/2607.29167
- A-MemGuard: https://arxiv.org/abs/2510.02373
- MAPLE-Guard: https://arxiv.org/pdf/2608.00426
- Utility Under Attack: https://arxiv.org/pdf/2608.21230
- Survey memory security: https://arxiv.org/pdf/2604.16548
- Survey provenance: https://arxiv.org/pdf/2606.04990
