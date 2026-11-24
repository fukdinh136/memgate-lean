-- Việc 4c: schema PostgreSQL, cùng bảng với backend SQLite (memgate/store/sqlite.py).
-- Chạy trong schema hiện hành (search_path); extension vector nằm ở schema public.
CREATE TABLE sources (
    source_id    TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    locator      TEXT NOT NULL,
    content      TEXT NOT NULL,          -- đã redact
    content_hash TEXT NOT NULL,          -- hash nội dung gốc
    domain       TEXT NOT NULL,
    base_trust   DOUBLE PRECISION,
    label        SMALLINT NOT NULL
);
CREATE TABLE items (
    item_id            TEXT PRIMARY KEY,
    type               TEXT NOT NULL CHECK (type IN ('short_term','long_term','retrieved','shared')),
    scope              TEXT NOT NULL CHECK (scope IN ('private','team','global')),
    owner_agent_id     TEXT NOT NULL,
    current_version_id TEXT,
    status             TEXT NOT NULL,
    expires_at         TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL
);
CREATE TABLE versions (
    seq           BIGSERIAL UNIQUE,      -- thứ tự chèn, thay cho rowid của SQLite khi phá hoà
    version_id    TEXT PRIMARY KEY,
    item_id       TEXT NOT NULL REFERENCES items(item_id),
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    embedding_ref TEXT,
    label         SMALLINT NOT NULL,
    trust_score   DOUBLE PRECISION,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    reason        TEXT NOT NULL
);
CREATE TABLE edges (
    child_version_id TEXT NOT NULL REFERENCES versions(version_id),
    parent_kind      TEXT NOT NULL CHECK (parent_kind IN ('source','version')),
    parent_id        TEXT NOT NULL,
    relation         TEXT NOT NULL,
    PRIMARY KEY (child_version_id, parent_kind, parent_id, relation)
);
CREATE TABLE context_reads (
    agent   TEXT NOT NULL,
    session TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN ('source','version')),
    ref_id  TEXT NOT NULL,
    label   SMALLINT NOT NULL,
    PRIMARY KEY (agent, session, seq)
);
CREATE TABLE embeddings (
    model        TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector       vector NOT NULL,        -- đã chuẩn hoá L2; số chiều tuỳ model
    PRIMARY KEY (model, content_hash)
);
CREATE TABLE roles (
    role_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL
);
CREATE TABLE agents (
    agent_id  TEXT PRIMARY KEY,
    role_id   TEXT NOT NULL REFERENCES roles(role_id),
    name      TEXT NOT NULL,
    framework TEXT
);
CREATE TABLE policies (
    policy_id TEXT PRIMARY KEY,
    ord       INTEGER NOT NULL,
    role_id   TEXT NOT NULL,
    scope     TEXT NOT NULL CHECK (scope IN ('private','team','global','*')),
    action    TEXT NOT NULL CHECK (action IN ('read','write','promote','*')),
    effect    TEXT NOT NULL CHECK (effect IN ('allow','deny')),
    condition TEXT
);
CREATE TABLE policy_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE audit_events (
    seq       BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL,
    actor     TEXT NOT NULL,
    op        TEXT NOT NULL,
    target    TEXT NOT NULL,
    decision  TEXT NOT NULL CHECK (decision IN ('allow','deny','review')),
    reason    TEXT NOT NULL,
    prev_hash TEXT,
    hash      TEXT
);
-- Index theo mục 6 của đặc tả
CREATE INDEX idx_versions_item ON versions(item_id, created_at);
CREATE INDEX idx_items_status ON items(status, expires_at);
CREATE INDEX idx_edges_child ON edges(child_version_id);
CREATE INDEX idx_edges_parent ON edges(parent_id);
CREATE INDEX idx_sources_hash ON sources(content_hash);
CREATE INDEX idx_audit_actor ON audit_events(actor, decision);
-- HNSW chỉ cho embedding 384 chiều của bge-small-en-v1.5 (index cần số chiều cố định).
CREATE INDEX idx_embeddings_bge_hnsw ON embeddings
    USING hnsw ((vector::vector(384)) vector_cosine_ops)
    WHERE model = 'bge-small-en-v1.5';
