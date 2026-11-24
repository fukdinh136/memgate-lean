.PHONY: install test smoke bench-clone k1

install:
	pip install -e ".[dev,gemini,ml]"

test:
	pytest -q

# Việc 1: gọi LLM thật hai lần, lần hai phải trúng cache (cần API key)
smoke:
	python -m harness.smoke

# Việc 2: lấy harness và kịch bản gốc của MEM-INV-Bench
bench-clone:
	git clone https://github.com/yedidel/mem-inv-bench third_party/mem-inv-bench

# K1 end-to-end với LLM thật (MemGate chạy được khi xong việc 5)
k1:
	python -m harness.run --scenario harness/scenarios/k1_relay.yaml harness/scenarios/k1_chain.yaml --gate null memgate
