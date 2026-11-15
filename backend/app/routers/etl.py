"""数据准备流水线接口：维护准备任务，并提供异动水位消费入口。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.etl import EtlService

router = APIRouter(prefix="/api/etl", tags=["数据准备流水线"])

service = EtlService()

LIST_FIELDS = ["任务编号", "任务名称", "输入源", "转换步骤", "输出目标", "调度周期", "依赖关系", "任务状态"]
STATUSES = ["草稿", "运行中", "已暂停", "已完成"]


# 静态路径先于 /{entry_id} 注册，避免 watermark、export 被当成整数解析。
@router.get("/watermark", response_model=dict)
def watermark_feed(
    since: str | None = Query(default=None, description="只读取该水位之后的异动条目"),
    since_seq: int | None = Query(default=None, description="同一水位内的起始序号，配合 since 使用"),
) -> dict[str, Any]:
    """按水位产出迁移后的异动条目；失败区间单独列出，不混进正常记录。"""
    return service.watermark_feed(since=since, since_seq=since_seq)


@router.post("/watermark/consume", response_model=ActionResult)
def consume_watermark(payload: EntryPayload) -> ActionResult:
    """推进流水线水位：消费新结构条目，失败区间保留原记录。"""
    since = str(payload.values.get("since") or "").strip() or None
    since_seq = payload.values.get("since_seq")
    try:
        since_seq = int(since_seq) if since_seq not in (None, "") else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="since_seq 需要是整数")
    result = service.consume_watermark(since=since, since_seq=since_seq)
    return ActionResult(
        ok=True,
        message=(
            f"已消费 {result['消费条数']} 条异动条目，"
            f"水位推进到 {result['消费到水位'] or '未推进'}#{result['消费到序号']}"
        ),
        entry=result,
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据准备流水线清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "etl", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、运行中、已暂停、已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤数据准备流水线列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条准备任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"准备任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条准备任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="准备任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条准备任务执行启动任务、暂停任务、归档任务；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
