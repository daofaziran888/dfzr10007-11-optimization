.PHONY: install backend frontend verify backfill

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 旧异动窗口回填：幂等，重复执行不产生副本；失败区间保留原记录。
backfill:
	cd backend && .venv/bin/python -m app.backfill

# 异动窗口切换验收：迁移结果、同环比归因读取、流水线水位消费。
verify:
	cd backend && .venv/bin/python -m app.verify_anomaly
	cd frontend && npm run build
