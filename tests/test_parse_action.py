from harness.agents import parse_action


def test_plain_json():
    assert parse_action('{"tool": "fetch", "args": {"url": "u"}}')["tool"] == "fetch"


def test_fenced_json_with_text():
    out = parse_action('Mình sẽ đọc trang.\n```json\n{"final": "xong"}\n```')
    assert out == {"final": "xong"}


def test_garbage_returns_error():
    assert "error" in parse_action("không có json")
