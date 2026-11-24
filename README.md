# MemGate

Lớp quản trị memory cho hệ multi-agent. Spec: `MemGate — Đề xuất giải pháp`.

## Chạy

```bash
python -m venv .venv && source .venv/bin/activate
make install        # pip install -e ".[dev,gemini,ml]"
make test
make bench-clone    # việc 2: clone MEM-INV-Bench vào third_party/
```

Trên Windows (PowerShell, không cần `make`):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,gemini]"
pytest -q
$env:GEMINI_API_KEY = "<key>"
python -m harness.smoke      # việc 1: lần gọi thứ hai phải trúng cache
```

`LLM_CACHE=readwrite|readonly|off` điều khiển cache (mặc định `readwrite`, CI dùng `readonly`).

## Cấu trúc

| File | Nội dung | Trạng thái |
| --- | --- | --- |
| `memgate/labels.py` | Nhãn U < A < T, `meet`, `output_label` | xong |
| `memgate/models.py` | `Source`, `Version`, `Message`, `Decision` | xong |
| `memgate/gate.py` | Interface `Gate`, `NullGate` | xong |
| `memgate/core.py` | `MemGate`: nhãn context, `observe`, `send_message` | xong |
| `memgate/core.py` | `write`, `ingest_trusted`, `retrieve`, `history`, `rollback` | TODO việc 4 |
| `memgate/store.py` | Schema SQLite | schema xong, truy vấn TODO việc 4 |
| `memgate/action_gate.py` | `SENSITIVE`, `authorize` | TODO việc 5 |
| `harness/llm.py` | Gọi LLM + cache SQLite | xong (việc 1) |
| `harness/tools.py` | Tool giả, tool proxy | xong |
| `harness/scoring.py` | Scorer tất định ASR, benign | xong |
| `harness/agents.py` | `AgentSpec`, `AgentRunner` (vòng lặp agent đơn + `delegate`, `remember`, `recall`) | xong |
| `harness/team.py` | Đội `pair` (planner, coder) và `chain` (planner, coder, researcher) | xong; chỉnh prompt khi chạy LLM thật |
| `harness/run.py` | Chạy kịch bản × gate × seed, ghi `runs/*.jsonl` | xong |
| `harness/scenarios/` | `k1_relay.yaml` (1 bước relay), `k1_chain.yaml` (2 bước) | thêm 3–5 kịch bản MEM-INV-Bench (việc 3) |

Tìm việc của mình: `grep -rn "TODO(việc" memgate harness tests`.

## Agent

Mỗi agent là một vòng lặp: gọi LLM, parse một action JSON (`{"tool":..., "args":...}` hoặc
`{"final": ...}`), thực thi, đưa kết quả lại vào hội thoại. Nhiều agent = agent gọi tool
`delegate(agent, task)`: task đi qua `gate.send_message` tới agent con, kết quả `final` của agent
con quay về agent cha cũng qua `gate.send_message`. Đây là kênh K1.

Planner là agent duy nhất có tool hành động (`send_email`, `wire_transfer`, `http_post`).
Kịch bản chọn đội bằng trường `team: pair | chain`. Kịch bản có `ingest` thì chạy session `a`
để nạp memory trước, rồi session `b` là task chính được chấm điểm.

Test agent dùng LLM giả lập (`tests/scripted.py`), không tốn token.

## Quy ước

- Harness chỉ gọi MemGate qua interface `Gate`; đổi cấu hình phòng thủ = đổi object gate.
- Mọi lời gọi LLM đi qua `harness.llm.LLM`; không gọi SDK trực tiếp.
- Mọi tool đọc gọi `observe`, mọi tool nguy hiểm gọi `authorize` (đã nằm trong `ToolBox`).
- Nhãn chỉ đi xuống. Không thêm code nào nâng nhãn context hay nhãn source.
- Test đánh `xfail` là tiêu chí nghiệm thu của việc tương ứng: làm xong thì bỏ `xfail`.

## Nghiệm thu tuần 1

`tests/test_agents.py` chạy đủ vòng lặp agent với LLM giả lập trên cả hai đội:
với `NullGate` tấn công K1 thành công; với `MemGate` tấn công bị chặn và email hợp lệ tới
`boss@company.com` vẫn được gửi (đang `xfail`, chờ việc 5). Sau đó chạy `make k1` với LLM thật.
