"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = "数据洞察与自助分析平台"
    env: str = os.getenv("APP_ENV", "local")
    port: int = 8000
    # 启动时执行旧异动窗口回填：回填本身幂等，重复启动不会产生副本。
    # 置 APP_AUTO_BACKFILL=0 可关闭，改由部署脚本显式调用迁移接口。
    auto_backfill: bool = os.getenv("APP_AUTO_BACKFILL", "1") != "0"
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200


settings = Settings()
