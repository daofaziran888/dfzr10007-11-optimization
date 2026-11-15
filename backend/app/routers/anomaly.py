"""指标异动归因接口：维护迁移后的异动条目，并提供旧窗口迁移与失败区间重试入口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, MigrationPayload, PageResult
from app.services.anomaly import AnomalyService

router = APIRouter(prefix="/api/anomaly", tags=["指标异动归因"])

service = AnomalyService()

LIST_FIELDS = ["记录编号", "指标名称", "异动方向", "异动幅度", "归因维度", "归因结论", "处理建议", "异动时间", "水位"]
STATUSES = ["待分析", "分析中", "已归因", "已关闭"]


# 静态路径必须注册在 /{entry_id} 之前，否则 legacy、export 会被当成整数解析。
@router.get("/legacy", response_model=dict)
def list_legacy() -> dict[str, Any]:
    """旧异动窗口原表与迁移状态：保留旧值供追溯，失败行标注原因。"""
    items = service.list_legacy()
    return {"items": items, "total": len(items)}


@router.get("/migrations", response_model=dict)
def list_migrations() -> dict[str, Any]:
    """迁移状态清单：每个源窗口一条，记录批次、条目 ID 与失败原因。"""
    items = service.list_migrations()
    return {"items": items, "total": len(items)}


@router.get("/migrations/summary", response_model=dict)
def migration_summary() -> dict[str, Any]:
    """迁移与水位概况，供切换后的核对使用。"""
    return service.summary()


@router.post("/migrations", response_model=ActionResult)
def migrate(payload: MigrationPayload) -> ActionResult:
    """回填旧异动窗口：重复执行不产生副本，失败窗口保留原记录并返回失败区间。"""
    summary = service.migrate_legacy(batch=payload.batch, codes=payload.codes, fixes=payload.fixes)
    ok = summary["失败"] == 0
    if summary["失败"]:
        message = f"迁移完成 {summary['迁移']} 条，失败 {summary['失败']} 条，失败区间保留原记录"
    elif summary["迁移"] == 0:
        message = "没有可迁移的旧窗口，已迁移记录保持不变"
    else:
        message = f"已迁移 {summary['迁移']} 条旧异动窗口"
    return ActionResult(ok=ok, message=message, entry={"summary": summary})


@router.post("/migrations/rollback", response_model=ActionResult)
def rollback(payload: MigrationPayload) -> ActionResult:
    """按批次撤销迁移条目，原窗口记录不受影响，可重新迁移。"""
    batch = str(payload.batch or "").strip()
    if not batch:
        return ActionResult(ok=False, message="缺少迁移批次，无法撤销")
    result = service.rollback_batch(batch)
    return ActionResult(ok=True, message=f"已撤销批次 {batch} 的 {result['撤销条目']} 条迁移条目", entry=result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出指标异动归因清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "anomaly", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号或指标名称检索"),
    status: str | None = Query(default=None, description="待分析、分析中、已归因、已关闭"),
    direction: str | None = Query(default=None, description="上升、下降、波动、持平"),
    dimension: str | None = Query(default=None, description="归因维度"),
    watermark_from: str | None = Query(default=None, description="水位起始时间，如 2026-09-01"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、状态、方向、归因维度与水位过滤异动条目；没有数据时返回空页。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        direction=direction,
        dimension=dimension,
        watermark_from=watermark_from,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条异动记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"异动记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条异动记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="异动记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条异动记录执行开始分析、确认归因、关闭异动；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
