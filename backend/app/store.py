"""数据仓库：给每个业务模块准备一份可筛选、可流转的数据。

实现分两层：
- 运行期是一份内存表，业务服务直接读写字典；
- 落盘用 SQLite（默认 backend/app.sqlite3，可用 APP_DB_PATH 覆盖），
  每个写请求结束后整体持久化。这样旧异动窗口的回填结果不会被进程重启冲掉，
  部署与本地开发看到的也是同一份数据。

没有配置 APP_DB_PATH 或写盘失败时退化为纯内存，保证克隆下来直接能起。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.seed import SEED_ROWS, SEED_VERSION

# 迁移用的旧窗口源表与水位状态表属于内部表，不对外算作业务模块。
INTERNAL_MODULES = {"anomaly_legacy", "anomaly_migration", "etl_watermark"}

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "app.sqlite3")


class Store:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = os.getenv("APP_DB_PATH", DEFAULT_DB_PATH) if db_path is None else db_path
        self._persistent = bool(self._db_path) and self._db_path != ":memory:"
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._load()

    # ------------------------------ 持久化 ------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            "module TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY (module, position))"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return conn

    def _seed(self) -> None:
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def _load(self) -> None:
        if not self._persistent:
            self._seed()
            return
        try:
            with self._connect() as conn:
                version_row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'seed_version'"
                ).fetchone()
                stored = {}
                for module, position, payload in conn.execute(
                    "SELECT module, position, payload FROM records ORDER BY module, position"
                ):
                    stored.setdefault(module, []).append(json.loads(payload))
                if version_row is None or version_row[0] != SEED_VERSION or not stored:
                    self._seed()
                    self.persist()
                    return
                self._tables = stored
        except (sqlite3.Error, OSError, ValueError):
            # 落盘不可用时退回内存，不影响服务启动。
            self._persistent = False
            self._seed()

    def persist(self) -> None:
        if not self._persistent:
            return
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM records")
                for module, rows in self._tables.items():
                    conn.executemany(
                        "INSERT INTO records (module, position, payload) VALUES (?, ?, ?)",
                        [
                            (module, position, json.dumps(row, ensure_ascii=False))
                            for position, row in enumerate(rows)
                        ],
                    )
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('seed_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (SEED_VERSION,),
                )
        except (sqlite3.Error, OSError):
            self._persistent = False

    # ------------------------------ 读取 ------------------------------

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in INTERNAL_MODULES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
