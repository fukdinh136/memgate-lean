from memgate.store.base import Store
from memgate.store.sqlite import SQLiteStore


def open_store(url: str = ":memory:", schema: str | None = None, temp: bool = False) -> Store:
    """':memory:', 'sqlite:///đường/dẫn.db', đường dẫn file, hoặc 'postgresql://...'.
    Với PostgreSQL: `schema` chọn schema; `temp=True` tạo schema tạm, xoá khi close()."""
    if url.startswith("sqlite:///"):
        return SQLiteStore(url[len("sqlite:///"):])
    if url.startswith(("postgres://", "postgresql://")):
        from memgate.store.postgres import PostgresStore   # cần extra [postgres] (pg8000)
        return PostgresStore(url, schema=schema, temp=temp)
    return SQLiteStore(url)


__all__ = ["Store", "SQLiteStore", "open_store"]
