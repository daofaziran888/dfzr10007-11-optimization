"""数据洞察与自助分析平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services.anomaly import AnomalyService
from app.store import store

app = FastAPI(title="数据洞察与自助分析平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def persist_after_write(request: Request, call_next):
    """写请求结束后落盘；读请求不动数据库，避免无谓写放大。"""
    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        store.persist()
    return response


for module in ROUTERS:
    app.include_router(module.router)

# 启动回填旧异动窗口：幂等，失败行留在原表，不阻断服务启动。
startup_backfill: dict[str, object] = {}
if settings.auto_backfill:
    startup_backfill = AnomalyService().migrate_legacy(batch="startup-backfill")
    store.persist()


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
