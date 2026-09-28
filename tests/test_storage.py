"""crawlerhub.core.storage 模块测试。"""

import asyncio
import json
from pathlib import Path

import pytest

from crawlerhub.core.storage import (
    BaseStorage,
    CsvStorage,
    JsonStorage,
    SqliteStorage,
    create_storage,
)


class TestJsonStorage:
    async def test_save_single_record(self, tmp_path: Path) -> None:
        storage = JsonStorage(tmp_path)
        await storage.save({"user_id": "123", "name": "test"}, "users")
        path = tmp_path / "users.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["user_id"] == "123"
        assert record["name"] == "test"
        assert "_saved_at" in record

    async def test_save_multiple_records(self, tmp_path: Path) -> None:
        storage = JsonStorage(tmp_path)
        await storage.save_many(
            [{"user_id": "1"}, {"user_id": "2"}, {"user_id": "3"}], "users"
        )
        path = tmp_path / "users.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    async def test_different_collections(self, tmp_path: Path) -> None:
        storage = JsonStorage(tmp_path)
        await storage.save({"id": "1"}, "users")
        await storage.save({"id": "2"}, "posts")
        assert (tmp_path / "users.jsonl").exists()
        assert (tmp_path / "posts.jsonl").exists()


class TestCsvStorage:
    async def test_save_creates_header(self, tmp_path: Path) -> None:
        storage = CsvStorage(tmp_path)
        await storage.save({"user_id": "123", "name": "test"}, "users")
        path = tmp_path / "users.csv"
        assert path.exists()
        content = path.read_text(encoding="utf-8-sig")
        assert "user_id" in content
        assert "name" in content

    async def test_multiple_rows(self, tmp_path: Path) -> None:
        storage = CsvStorage(tmp_path)
        await storage.save({"user_id": "1", "name": "a"}, "users")
        await storage.save({"user_id": "2", "name": "b"}, "users")
        path = tmp_path / "users.csv"
        lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
        assert len(lines) == 3  # header + 2 rows


class TestSqliteStorage:
    async def test_save_and_query(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path}/test.db"
        storage = SqliteStorage(db_url)
        await storage.save({"user_id": "123", "name": "test"}, "users")

        # 查询验证
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(text('SELECT * FROM users')).fetchall()
            assert len(result) == 1

    async def test_auto_add_column(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path}/test.db"
        storage = SqliteStorage(db_url)
        await storage.save({"user_id": "1", "name": "a"}, "users")
        # 第二次写入多一个字段，应自动加列
        await storage.save({"user_id": "2", "name": "b", "extra": "field"}, "users")

        from sqlalchemy import create_engine, inspect

        engine = create_engine(db_url)
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("users")}
        assert "extra" in cols


class TestCreateStorage:
    def test_create_json(self) -> None:
        s = create_storage("json", output_dir="data/output")
        assert isinstance(s, JsonStorage)

    def test_create_csv(self) -> None:
        s = create_storage("csv", output_dir="data/output")
        assert isinstance(s, CsvStorage)

    def test_create_sqlite(self) -> None:
        s = create_storage("sqlite", db_url="sqlite:///data/test.db")
        assert isinstance(s, SqliteStorage)

    def test_unknown_backend(self) -> None:
        with pytest.raises(ValueError, match="不支持的存储后端"):
            create_storage("invalid")
