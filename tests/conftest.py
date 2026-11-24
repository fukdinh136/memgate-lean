import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _no_real_api_keys(monkeypatch, tmp_path):
    """Test không bao giờ đọc key thật (biến môi trường hay secrets/*.txt) của máy đang chạy."""
    for var in ("GEMINI_API_KEYS", "OPENROUTER_API_KEYS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEMGATE_KEYS_FILE", str(tmp_path / "khong_co_key.txt"))
