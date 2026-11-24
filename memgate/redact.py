"""Redact secret trước khi lưu (NFR7). Áp cho cả nội dung source và nội dung version.

TODO(việc 7): thay thân hàm bằng detect-secrets. Gate so khớp nguyên văn trên nội dung đã redact,
nên giá trị chứa secret sẽ không khớp và rơi vào review (fail-closed, NFR2).
"""


def redact(text: str) -> str:
    return text
