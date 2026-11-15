"""指标异动归因业务规则：旧异动窗口迁移、水位回填与状态流转都收在这里。

旧窗口把「时间 + 方向 + 幅度 + 归因」塞在一段描述里，下游流水线没法直接消费。
迁移时把每条旧窗口拆成独立的异动条目，补齐归因维度与水位字段；原窗口表只读保留，
迁移失败的行不写入新表，直接留在原表里等待修正后重试。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "anomaly"
LEGACY_MODULE = "anomaly_legacy"
MIGRATION_MODULE = "anomaly_migration"
REQUIRED_FIELDS = ["记录编号", "指标名称", "异动方向"]
DIRECTIONS = ["上升", "下降", "波动", "持平"]
STATUS_ORDER = ["待分析", "分析中", "已归因", "已关闭"]
ACTION_RULES = {"开始分析": "待分析", "确认归因": "分析中", "关闭异动": "已归因"}
NEGATIVE_ACTIONS = []
LEGACY_STATUS_MAP = {
    "待分析": "待分析",
    "分析中": "分析中",
    "已归因": "已归因",
    "已关闭": "已关闭",
}

_TIME_RE = re.compile(r"(?P<日期>\d{4}-\d{2}-\d{2})(?:[ T](?P<时刻>\d{2}:\d{2}:\d{2}))?")
_DIRECTION_RE = re.compile(r"(?P<方向>上升|下降|波动|持平)")
_MAGNITUDE_RE = re.compile(r"(?P<幅度>-?\d+(?:\.\d+)?)\s*%")
_DIMENSION_RE = re.compile(r"归因维度\s*[:：]\s*(?P<维度>[^|｜;；]+)")
_CONCLUSION_RE = re.compile(r"归因结论\s*[:：]\s*(?P<结论>[^|｜;；]+)")


class MigrationError(ValueError):
    """旧窗口无法拆成新结构时抛出：消息会原样写进迁移失败清单。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _as_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _watermark_bounds(day: str) -> tuple[str, str]:
    return f"{day} 00:00:00", f"{day} 23:59:59"


def parse_legacy_window(row: dict[str, Any]) -> dict[str, Any]:
    """把一条旧窗口描述拆成时间、方向、幅度、归因维度与结论。

    任一段缺失都抛 MigrationError，由调用方保留原记录并登记失败原因，
    不做「猜一个值」式的兜底，避免把错误数据写进新结构。
    """
    text = str(row.get("异动描述") or "").strip()
    if not text:
        raise MigrationError("旧窗口描述为空，无法拆出异动幅度与归因维度")

    time_match = _TIME_RE.search(text)
    if not time_match:
        raise MigrationError("旧窗口描述里没有可识别的异动时间")
    direction_match = _DIRECTION_RE.search(text)
    if not direction_match:
        raise MigrationError("旧窗口描述里没有可识别的异动方向")
    magnitude_match = _MAGNITUDE_RE.search(text)
    if not magnitude_match:
        raise MigrationError("旧窗口描述里没有可识别的异动幅度（需带百分号）")
    dimension_match = _DIMENSION_RE.search(text)
    if not dimension_match:
        raise MigrationError("旧窗口描述里没有归因维度")
    conclusion_match = _CONCLUSION_RE.search(text)

    day = time_match.group("日期")
    moment = time_match.group("时刻") or "00:00:00"
    direction = direction_match.group("方向")
    magnitude = float(magnitude_match.group("幅度"))
    dimension = dimension_match.group("维度").strip()
    conclusion = conclusion_match.group("结论").strip() if conclusion_match else ""
    if magnitude < 0:
        magnitude = abs(magnitude)

    start, end = _watermark_bounds(day)
    return {
        "异动时间": f"{day} {moment}",
        "异动方向": direction,
        "异动幅度": magnitude,
        "异动幅度文本": f"{magnitude:g}%",
        "归因维度": dimension,
        "归因结论": conclusion,
        "水位日期": day,
        "水位起点": start,
        "水位终点": end,
    }


def _status_of(legacy: dict[str, Any]) -> str:
    status = str(legacy.get("处理状态") or legacy.get("status") or "待分析").strip()
    return LEGACY_STATUS_MAP.get(status, "待分析")


def _build_entry(legacy: dict[str, Any], parsed: dict[str, Any], batch: str, now: str) -> dict[str, Any]:
    status = _status_of(legacy)
    return {
        "id": 0,  # 由调用方补上
        "status": status,
        "pending": status != STATUS_ORDER[-1],
        "abnormal": parsed["异动方向"] in {"下降", "波动"},
        "记录编号": str(legacy.get("窗口编号") or ""),
        "指标名称": str(legacy.get("指标名称") or ""),
        "异动方向": parsed["异动方向"],
        "异动幅度": parsed["异动幅度"],
        "异动幅度文本": parsed["异动幅度文本"],
        "归因维度": parsed["归因维度"],
        "归因结论": parsed["归因结论"],
        "处理建议": str(legacy.get("处理建议") or ""),
        "异动时间": parsed["异动时间"],
        "水位": parsed["水位终点"],
        "水位起点": parsed["水位起点"],
        "水位终点": parsed["水位终点"],
        "水位日期": parsed["水位日期"],
        "水位序号": int(legacy.get("id", 0)),
        "源记录ID": int(legacy.get("id", 0)),
        "旧窗口描述": str(legacy.get("异动描述") or ""),
        "旧处理状态": str(legacy.get("处理状态") or legacy.get("status") or ""),
        "旧值": {
            "窗口编号": legacy.get("窗口编号"),
            "指标名称": legacy.get("指标名称"),
            "异动描述": legacy.get("异动描述"),
            "处理建议": legacy.get("处理建议"),
            "处理状态": legacy.get("处理状态"),
        },
        "迁移批次": batch,
        "迁移时间": now,
    }


class AnomalyService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        direction: str | None = None,
        dimension: str | None = None,
        watermark_from: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("记录编号", "")) or keyword in str(row.get("指标名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if direction:
            rows = [row for row in rows if row.get("异动方向") == direction]
        if dimension:
            rows = [row for row in rows if row.get("归因维度") == dimension]
        if watermark_from:
            rows = [row for row in rows if str(row.get("水位") or "") >= watermark_from]
        rows = sorted(rows, key=lambda row: (str(row.get("水位") or ""), int(row.get("水位序号") or 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry_id = _next_id(rows)
        moment = str(values.get("异动时间") or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        day = moment[:10] if _TIME_RE.match(moment) else datetime.now().strftime("%Y-%m-%d")
        start, end = _watermark_bounds(day)
        magnitude = _as_float(values.get("异动幅度"))
        direction = str(values.get("异动方向") or "").strip()
        entry = {
            "id": entry_id,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": direction in {"下降", "波动"},
            "记录编号": values.get("记录编号"),
            "指标名称": values.get("指标名称"),
            "异动方向": direction,
            "异动幅度": magnitude if magnitude is not None else 0.0,
            "异动幅度文本": f"{magnitude:g}%" if magnitude is not None else "",
            "归因维度": values.get("归因维度") or "",
            "归因结论": values.get("归因结论") or "",
            "处理建议": values.get("处理建议") or "",
            "异动时间": moment,
            "水位": end,
            "水位起点": start,
            "水位终点": end,
            "水位日期": day,
            "水位序号": entry_id,
            "源记录ID": None,
            "旧窗口描述": "",
            "旧处理状态": "",
            "旧值": {},
            "迁移批次": str(values.get("迁移批次") or "手工登记"),
            "迁移时间": _now(),
        }
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"异动记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标异动归因可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"异动记录已{action}"

    # ------------------------------ 旧窗口迁移 ------------------------------

    def list_legacy(self) -> list[dict[str, Any]]:
        """旧窗口原表 + 当前迁移状态；原记录字段不做任何改写。"""
        statuses = {
            int(row.get("源记录ID") or 0): row for row in store.rows(MIGRATION_MODULE)
        }
        items: list[dict[str, Any]] = []
        for legacy in store.rows(LEGACY_MODULE):
            status = statuses.get(int(legacy.get("id", 0)), {})
            item = dict(legacy)
            item["迁移状态"] = status.get("状态", "待迁移")
            item["迁移批次"] = status.get("迁移批次")
            item["失败原因"] = status.get("失败原因", "")
            item["条目ID"] = status.get("条目ID")
            items.append(item)
        return items

    def list_migrations(self) -> list[dict[str, Any]]:
        rows = sorted(
            store.rows(MIGRATION_MODULE),
            key=lambda row: int(row.get("源记录ID") or 0),
        )
        return [dict(row) for row in rows]

    def _record_status(
        self,
        legacy: dict[str, Any],
        *,
        state: str,
        batch: str,
        now: str,
        entry_id: int | None = None,
        reason: str = "",
    ) -> None:
        rows = store.rows(MIGRATION_MODULE)
        source_id = int(legacy.get("id", 0))
        row = next((item for item in rows if int(item.get("源记录ID") or 0) == source_id), None)
        if row is None:
            row = {
                "id": _next_id(rows),
                "源记录ID": source_id,
                "窗口编号": legacy.get("窗口编号"),
            }
            rows.append(row)
        row.update({
            "状态": state,
            "条目ID": entry_id,
            "失败原因": reason,
            "迁移批次": batch,
            "迁移时间": now,
        })

    def migrate_legacy(
        self,
        *,
        batch: str | None = None,
        codes: list[str] | None = None,
        fixes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """把旧异动窗口回填成新结构，可重复执行。

        - 按「源记录ID」判重：已迁移的窗口直接跳过，不产生副本；
        - 单条迁移是原子的：解析成功才写入条目，解析失败只登记失败原因，
          原窗口记录保持原样，修正后可用同一接口从原记录重新迁移；
        - fixes 提供「按原记录修正后的描述」，只用于本次迁移结果，原表旧值不动；
        - 返回失败区间清单，便于按记录编号逐条重试。
        """
        now = _now()
        batch = batch or f"MIG-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        wanted = {str(code).strip() for code in codes or [] if str(code).strip()}
        fixes = {str(key).strip(): str(value) for key, value in (fixes or {}).items() if str(value).strip()}
        summary: dict[str, Any] = {
            "批次": batch,
            "迁移时间": now,
            "扫描": 0,
            "迁移": 0,
            "跳过": 0,
            "失败": 0,
            "失败区间": [],
            "迁移记录编号": [],
        }
        entries = store.rows(MODULE)
        existing_sources = {
            int(entry.get("源记录ID")) for entry in entries if entry.get("源记录ID") is not None
        }
        for legacy in sorted(store.rows(LEGACY_MODULE), key=lambda row: int(row.get("id", 0))):
            code = str(legacy.get("窗口编号") or "").strip()
            if wanted and code not in wanted:
                continue
            summary["扫描"] += 1
            source_id = int(legacy.get("id", 0))
            if source_id in existing_sources:
                summary["跳过"] += 1
                continue
            # 修正描述只作用于迁移副本：原窗口表不做回写，旧值仍可追溯。
            source = dict(legacy)
            used_fix = False
            if code in fixes:
                source["异动描述"] = fixes[code]
                used_fix = True
            try:
                parsed = parse_legacy_window(source)
            except MigrationError as error:
                summary["失败"] += 1
                summary["失败区间"].append({"记录编号": code, "源记录ID": source_id, "原因": str(error)})
                self._record_status(legacy, state="失败", batch=batch, now=now, reason=str(error))
                continue
            entry = _build_entry(source, parsed, batch, now)
            entry["id"] = _next_id(entries)
            entry["迁移修正"] = used_fix
            entries.append(entry)
            existing_sources.add(source_id)
            self._record_status(legacy, state="已迁移", batch=batch, now=now, entry_id=entry["id"])
            summary["迁移"] += 1
            summary["迁移记录编号"].append(code)
        summary["重复迁移"] = summary["跳过"] > 0 and summary["迁移"] == 0
        return summary

    def rollback_batch(self, batch: str) -> dict[str, Any]:
        """按迁移批次撤销新条目，原窗口表不动；用于失败区间从原记录重新恢复。"""
        entries = store.rows(MODULE)
        kept = [entry for entry in entries if entry.get("迁移批次") != batch]
        removed = [entry for entry in entries if entry.get("迁移批次") == batch]
        entries[:] = kept
        status_rows = store.rows(MIGRATION_MODULE)
        reset = 0
        for row in status_rows:
            if row.get("迁移批次") == batch:
                row["状态"] = "待迁移"
                row["条目ID"] = None
                row["失败原因"] = ""
                reset += 1
        return {
            "批次": batch,
            "撤销条目": len(removed),
            "撤销记录编号": [entry.get("记录编号") for entry in removed],
            "重置迁移状态": reset,
            "说明": "原窗口记录保持完整，可直接重新迁移",
        }

    def summary(self) -> dict[str, Any]:
        entries = store.rows(MODULE)
        legacy = store.rows(LEGACY_MODULE)
        migrations = store.rows(MIGRATION_MODULE)
        return {
            "异动条目总数": len(entries),
            "已归因": sum(1 for row in entries if row.get("status") == "已归因"),
            "分析中": sum(1 for row in entries if row.get("status") == "分析中"),
            "旧窗口数": len(legacy),
            "已迁移": sum(1 for row in migrations if row.get("状态") == "已迁移"),
            "迁移失败": sum(1 for row in migrations if row.get("状态") == "失败"),
            "最新水位": max((str(row.get("水位") or "") for row in entries), default=""),
        }

    def attribution_for(self, metric: str, dimension: str | None = None) -> dict[str, Any] | None:
        """同环比分析读取迁移后的归因结论：按指标 + 维度匹配水位最新的一条。"""
        candidates = [
            row for row in store.rows(MODULE)
            if str(row.get("指标名称") or "") == metric
            and (not dimension or str(row.get("归因维度") or "") == dimension)
        ]
        if not candidates:
            return None
        row = max(candidates, key=lambda item: (str(item.get("水位") or ""), int(item.get("水位序号") or 0)))
        return {
            "归因记录编号": row.get("记录编号"),
            "归因维度": row.get("归因维度"),
            "归因结论": row.get("归因结论"),
            "异动方向": row.get("异动方向"),
            "异动幅度": row.get("异动幅度"),
            "归因水位": row.get("水位"),
        }
