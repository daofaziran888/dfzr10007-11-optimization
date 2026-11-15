#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

# 旧异动窗口回填：先做一次 dry-run 检查，再执行幂等回填。
# 失败区间会保留在旧窗口原表，不回填出半成品条目；服务启动时还会再跑一次。
.venv/bin/python -m app.backfill --check || true
.venv/bin/python -m app.backfill || true

exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
