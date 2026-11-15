# 数据洞察与自助分析平台

面向业务分析师的自助式数据洞察平台。覆盖数据集市构建、自助SQL查询、可视化拖拽分析、分析看板协作、指标异动归因、分析模型沉淀、查询优化建议与数据故事编排的完整洞察链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   ├── app/backfill.py       旧异动窗口回填脚本
│   ├── app/verify_anomaly.py 异动窗口切换验收脚本
│   └── app/store.py          数据仓库（内存 + SQLite 落盘）
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

`run.sh` 会先做旧异动窗口回填（幂等），再启动服务。数据默认落在
`backend/app.sqlite3`，可用 `APP_DB_PATH` 改路径；置 `APP_AUTO_BACKFILL=0`
可关闭启动回填，改由部署脚本显式调用迁移接口。

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据集市构建 | `mart` | 集市定义 | 集市编号、集市名称、数据源表 |
| 自助SQL查询 | `sqlpad` | 查询脚本 | 脚本编号、脚本名称、SQL语句 |
| 拖拽可视化 | `vizdrag` | 可视化方案 | 方案编号、方案名称、数据源绑定 |
| 看板协作管理 | `collab` | 协作看板 | 看板编号、看板名称、协作成员 |
| 指标异动归因 | `anomaly` | 异动条目 | 记录编号、指标名称、异动方向、异动幅度、归因维度、异动时间、水位 |
| 分析模型沉淀 | `model` | 模型注册 | 模型编号、模型名称、模型类型 |
| 查询优化建议 | `opt` | 优化建议 | 建议编号、目标查询、优化类型 |
| 数据故事编排 | `story` | 故事模板 | 模板编号、故事标题、分析主题 |
| 数据准备流水线 | `etl` | 准备任务 | 任务编号、任务名称、输入源、消费水位 |
| 数据目录检索 | `catalog` | 目录条目 | 条目编号、数据集名称、业务域 |
| 数据采样管理 | `sample` | 采样配置 | 配置编号、数据源名称、采样算法 |
| 同环比分析 | `compare` | 对比方案 | 方案编号、对比指标、基准周期、归因维度、归因结论、归因水位 |
| 分析导出中心 | `export` | 导出任务 | 任务编号、数据源名称、导出格式 |
| 分析告警规则 | `alarm` | 告警规则 | 规则编号、监控指标、告警条件 |
| 数据定时刷新 | `refresh` | 刷新策略 | 策略编号、目标数据集、刷新周期 |
| 查询审计日志 | `audit` | 审计记录 | 记录编号、查询语句、执行人 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 旧异动窗口迁移

旧窗口把「时间 + 方向 + 幅度 + 归因」塞在一段 `异动描述` 文本里。迁移后：

- 每条窗口拆成一个独立的**异动条目**，`异动方向`、`异动幅度`（百分比数值）、
  `异动时间`、`归因维度`、`归因结论` 分列存储；
- 条目带**水位**字段（`水位起点`、`水位终点`、`水位日期`、`水位`），供水准备流水线增量消费；
- 原窗口表 `anomaly_legacy` 只读保留，条目的 `旧值` 字段同时存一份原始描述，供追溯；
- 迁移按源记录判重，重复执行不产生副本；
- 解析失败的窗口**不写入新结构**，只登记失败原因，原记录留在旧窗口表，修正后可重试。

| 接口 | 说明 |
| --- | --- |
| `GET /api/anomaly/legacy` | 旧窗口原表 + 迁移状态（已迁移/待迁移/失败 + 原因） |
| `POST /api/anomaly/migrations` | 回填；`{"batch": "...", "codes": ["ANOM-0004"], "fixes": {"ANOM-0004": "修正后的描述"}}` |
| `GET /api/anomaly/migrations` | 迁移状态清单 |
| `GET /api/anomaly/migrations/summary` | 迁移与水位概况 |
| `POST /api/anomaly/migrations/rollback` | 按批次撤销迁移条目，原窗口记录不动 |
| `GET /api/etl/watermark` | 按水位产出待消费的异动条目 + 失败区间；`since` 与 `since_seq` 组成复合游标 |
| `POST /api/etl/watermark/consume` | 推进流水线消费水位（持久化，重启后继续增量） |
| `GET /api/compare` / `/api/compare/export` | 同环比读取迁移后的归因维度、归因结论与归因水位 |

命令行：

```bash
cd backend
.venv/bin/python -m app.backfill --check        # 只检查哪些窗口能解析
.venv/bin/python -m app.backfill                # 幂等回填
.venv/bin/python -m app.backfill --retry-failed --fix 'ANOM-0004=2026-09-04 下降 2.0% | 归因维度：口径 | 归因结论：统计口径切换导致波动'
.venv/bin/python -m app.backfill --rollback <批次>
.venv/bin/python -m app.verify_anomaly          # 切换验收，核对 ANOM-0002 / ANOM-0003
```

样例数据里 `ANOM-0001～0003` 可正常迁移，`ANOM-0004` 故意缺少幅度，
用于验证「失败区间保留原记录、修正后从原记录重试」。
`--fix` 只作用于迁移副本，旧窗口表里的旧值不做回写。

领域：数据工程
子方向：数仓与数据湖
