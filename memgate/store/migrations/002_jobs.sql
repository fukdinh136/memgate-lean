-- Việc 7: hàng đợi việc của worker (inline ở việc 7, chạy nền với FOR UPDATE SKIP LOCKED ở việc 9).
-- Hash chain của audit_events dùng hai cột prev_hash, hash đã có từ 001; dòng cũ chưa có hash được
-- PostgresStore._setup() nối chuỗi lại sau khi chạy migration (xem memgate/audit.py).
CREATE TABLE jobs (
    job_id     TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    ref_id     TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('queued','done','failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    seq        BIGSERIAL UNIQUE
);
CREATE INDEX idx_jobs_status ON jobs(status, created_at);
