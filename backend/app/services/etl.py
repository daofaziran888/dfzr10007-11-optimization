"""数据准备流水线业务规则：状态流转、字段校验、异动水位消费与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.anomaly import AnomalyService
from app.store import store

MODULE = "etl"
WATERMARK_MODULE = "etl_watermark"
REQUIRED_FIELDS = ["任务编号", "任务名称", "输入源"]
STATUS_ORDER = ["草稿", "运行中", "已暂停", "已完成"]
ACTION_RULES = {"启动任务": "草稿", "暂停任务": "运行中", "归档任务": "已暂停"}
NEGATIVE_ACTIONS = []


class EtlService:
    def __init__(self) -> None:
        self._anomalies = AnomalyService()

    def _watermark_row(self) -> dict[str, Any]:
        rows = store.rows(WATERMARK_MODULE)
        if not rows:
            rows.append({"id": 1, "已消费水位": "", "已消费序号": 0, "更新时间": ""})
        return rows[0]

    def _cursor(self, since: str | None, since_seq: int | None) -> tuple[str, int]:
        """消费游标是「水位 + 水位序号」：同一天有多条异动时不会漏掉后补的记录。"""
        row = self._watermark_row()
        watermark = since if since is not None else str(row.get("已消费水位") or "")
        if since is not None:
            seq = since_seq or 0
        else:
            seq = int(row.get("已消费序号") or 0)
        return watermark, seq

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
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
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"准备任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据准备流水线可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"准备任务已{action}"

    def watermark_feed(self, *, since: str | None = None, since_seq: int | None = None) -> dict[str, Any]:
        """迁移回填后的异动条目按水位产出给流水线。

        只消费新结构齐全的条目；旧窗口未迁移成功时不猜测字段，而是把失败区间
        原样列出，原记录仍留在 anomaly_legacy 里等待重试。
        """
        from_watermark, from_seq = self._cursor(since, since_seq)
        items: list[dict[str, Any]] = []
        for entry in sorted(
            store.rows("anomaly"),
            key=lambda row: (str(row.get("水位") or ""), int(row.get("水位序号") or 0)),
        ):
            watermark = str(entry.get("水位") or "")
            seq = int(entry.get("水位序号") or 0)
            if from_watermark and (watermark < from_watermark or (watermark == from_watermark and seq <= from_seq)):
                continue
            missing = [
                field for field in ("异动幅度", "归因维度", "归因结论", "水位")
                if entry.get(field) in (None, "")
            ]
            if missing:
                continue
            items.append({
                "记录编号": entry.get("记录编号"),
                "指标名称": entry.get("指标名称"),
                "异动方向": entry.get("异动方向"),
                "异动幅度": entry.get("异动幅度"),
                "归因维度": entry.get("归因维度"),
                "归因结论": entry.get("归因结论"),
                "水位": watermark,
                "水位序号": seq,
                "源记录ID": entry.get("源记录ID"),
            })
        failures = [
            {
                "记录编号": row.get("窗口编号"),
                "源记录ID": row.get("源记录ID"),
                "原因": row.get("失败原因"),
            }
            for row in store.rows("anomaly_migration")
            if row.get("状态") == "失败"
        ]
        return {
            "since": from_watermark,
            "since_seq": from_seq,
            "items": items,
            "total": len(items),
            "失败区间": failures,
            "说明": "失败区间保留在旧窗口原表，修正后可重新迁移",
        }

    def consume_watermark(self, *, since: str | None = None, since_seq: int | None = None) -> dict[str, Any]:
        """推进流水线水位：只消费新结构条目，失败区间不阻断其余记录。"""
        feed = self.watermark_feed(since=since, since_seq=since_seq)
        row = self._watermark_row()
        if feed["items"]:
            latest = max(feed["items"], key=lambda item: (str(item["水位"]), int(item["水位序号"])))
            watermark, seq = str(latest["水位"]), int(latest["水位序号"])
            if (watermark, seq) > (str(row.get("已消费水位") or ""), int(row.get("已消费序号") or 0)):
                row["已消费水位"] = watermark
                row["已消费序号"] = seq
            row["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "消费条数": len(feed["items"]),
            "消费到水位": row.get("已消费水位") or "",
            "消费到序号": int(row.get("已消费序号") or 0),
            "失败区间": feed["失败区间"],
        }

    def consumed_watermark(self) -> str:
        return str(self._watermark_row().get("已消费水位") or "")
