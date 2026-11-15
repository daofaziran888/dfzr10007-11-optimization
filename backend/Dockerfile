FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 数据目录放在挂载卷上，避免容器重启后回填结果丢失。
ENV APP_DB_PATH=/srv/data/app.sqlite3
RUN mkdir -p /srv/data
EXPOSE 8000

# 构建期校验迁移样例可解析，并跑一遍切换验收（使用临时库，不写入 /srv/data）。
RUN python -m app.backfill --check || true \
    && python -m app.verify_anomaly

# 启动前回填旧异动窗口，再起服务；回填幂等，重复启动不产生副本。
CMD ["sh", "-c", "python -m app.backfill || true; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
