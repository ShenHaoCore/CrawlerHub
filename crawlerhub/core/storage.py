"""结构化输出存储模块。

支持 JSON、CSV、SQLite 三种后端，统一接口便于扩展。
"""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from crawlerhub.core.exceptions import StorageError
from crawlerhub.core.logger import logger


class BaseStorage(ABC):
    """存储后端抽象基类。"""

    @abstractmethod
    async def save(self, data: Dict[str, Any], collection: str) -> None:
        """保存单条数据。

        Args:
            data: 数据字典。
            collection: 集合/表名（如用户、内容等）。
        """

    async def save_many(self, items: List[Dict[str, Any]], collection: str) -> None:
        """批量保存数据。

        Args:
            items: 数据字典列表。
            collection: 集合/表名。
        """
        for item in items:
            await self.save(item, collection)


class JsonStorage(BaseStorage):
    """JSON 行存储：每条数据一行 JSON，便于追加和流式处理。

    Args:
        output_dir: 输出目录。
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, collection: str) -> Path:
        return self.output_dir / f"{collection}.jsonl"

    async def save(self, data: Dict[str, Any], collection: str) -> None:
        path = self._file_path(collection)
        record = {"_saved_at": datetime.now().isoformat(), **data}
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise StorageError(f"写入 JSON 失败: {exc}") from exc


class CsvStorage(BaseStorage):
    """CSV 存储。

    Args:
        output_dir: 输出目录。
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._headers_cache: Dict[str, List[str]] = {}

    def _file_path(self, collection: str) -> Path:
        return self.output_dir / f"{collection}.csv"

    async def save(self, data: Dict[str, Any], collection: str) -> None:
        path = self._file_path(collection)
        record = {"_saved_at": datetime.now().isoformat(), **data}
        keys = list(record.keys())
        file_exists = path.exists()

        try:
            with path.open("a", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                if not file_exists:
                    writer.writeheader()
                    self._headers_cache[collection] = keys
                elif collection not in self._headers_cache:
                    # 已有文件但未缓存表头，读取已有表头
                    with path.open("r", encoding="utf-8-sig", newline="") as rf:
                        reader = csv.reader(rf)
                        existing = next(reader, [])
                    # 合并新字段，重新写入表头（简单实现）
                    merged = list(dict.fromkeys(existing + keys))
                    self._headers_cache[collection] = merged
                    # 重建文件：读取所有行后重写
                    rows = []
                    with path.open("r", encoding="utf-8-sig", newline="") as rf:
                        rdr = csv.DictReader(rf)
                        rows = list(rdr)
                    with path.open("w", encoding="utf-8-sig", newline="") as wf:
                        wtr = csv.DictWriter(wf, fieldnames=merged)
                        wtr.writeheader()
                        for row in rows:
                            wtr.writerow({k: row.get(k, "") for k in merged})
                    writer = csv.DictWriter(f, fieldnames=merged)
                writer.writerow({k: record.get(k, "") for k in (self._headers_cache.get(collection, keys))})
        except OSError as exc:
            raise StorageError(f"写入 CSV 失败: {exc}") from exc


class SqliteStorage(BaseStorage):
    """SQLite 存储，基于 SQLAlchemy。

    自动建表，字段根据首次写入的数据动态创建。

    Args:
        db_url: 数据库 URL，如 ``sqlite:///data/crawlerhub.db``。
    """

    def __init__(self, db_url: str = "sqlite:///data/crawlerhub.db") -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # 确保目录存在
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(db_url, echo=False)
        self.Session = sessionmaker(bind=self.engine)
        self._tables: set[str] = set()

    def _ensure_table(self, collection: str, columns: List[str]) -> None:
        """确保表存在，缺失的列自动添加。"""
        from sqlalchemy import Column, Integer, MetaData, String, Table, inspect, text

        metadata = MetaData()
        inspector = inspect(self.engine)

        if not inspector.has_table(collection):
            # id 为自增主键，业务数据中不含 id 字段
            cols = [Column("id", Integer, primary_key=True, autoincrement=True)]
            for col in columns:
                if col != "id":
                    cols.append(Column(col, String))
            table = Table(collection, metadata, *cols)
            metadata.create_all(self.engine)
            logger.info(f"已创建表: {collection}")
        else:
            existing = {c["name"] for c in inspector.get_columns(collection)}
            new_cols = [c for c in columns if c not in existing and c != "id"]
            with self.engine.begin() as conn:
                for col in new_cols:
                    conn.execute(text(f'ALTER TABLE "{collection}" ADD COLUMN "{col}" VARCHAR'))

    async def save(self, data: Dict[str, Any], collection: str) -> None:
        from sqlalchemy import text

        record = {"_saved_at": datetime.now().isoformat(), **data}
        # id 为自增主键，不参与写入
        columns = [c for c in record.keys() if c != "id"]
        # 同步执行（SQLite 不支持真异步，这里用线程隔离的同步引擎）
        import asyncio

        def _do_save() -> None:
            self._ensure_table(collection, columns)
            placeholders = ", ".join(f":{c}" for c in columns)
            col_names = ", ".join(f'"{c}"' for c in columns)
            stmt = text(f'INSERT INTO "{collection}" ({col_names}) VALUES ({placeholders})')
            with self.Session() as session:
                session.execute(stmt, {c: str(record[c]) for c in columns})
                session.commit()

        await asyncio.to_thread(_do_save)


def create_storage(backend: str = "json", **kwargs) -> BaseStorage:
    """工厂方法：根据配置创建存储后端。

    Args:
        backend: 后端类型 ``json`` / ``csv`` / ``sqlite``。
        **kwargs: 传递给存储后端的参数。

    Returns:
        存储后端实例。
    """
    if backend == "json":
        return JsonStorage(**kwargs)
    if backend == "csv":
        return CsvStorage(**kwargs)
    if backend == "sqlite":
        return SqliteStorage(**kwargs)
    raise ValueError(f"不支持的存储后端: {backend}")
