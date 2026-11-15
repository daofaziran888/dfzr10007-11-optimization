#!/usr/bin/env python3
"""异动窗口切换验收脚本：核对迁移结果、幂等性与失败区间恢复。

用 TestClient 在独立进程内起应用，数据落在临时 SQLite 里，不影响正在运行的服务。
    python -m app.verify_anomaly
退出码：0 全部通过，1 有断言失败。
"""
from __future__ import annotations

import os
import sys
import tempfile

# 必须在导入 app.store 之前设置，保证验收使用独立数据库。
_TMP_DIR = tempfile.mkdtemp(prefix="anomaly-verify-")
os.environ["APP_DB_PATH"] = os.path.join(_TMP_DIR, "verify.sqlite3")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> int:
    client = TestClient(app)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))

    entries = client.get("/api/anomaly", params={"size": 200}).json()["items"]
    by_code = {row["记录编号"]: row for row in entries}

    anom2 = by_code.get("ANOM-0002")
    anom3 = by_code.get("ANOM-0003")
    check("ANOM-0002 已迁移为独立条目", anom2 is not None)
    check("ANOM-0002 异动幅度=8.2", bool(anom2) and anom2["异动幅度"] == 8.2, str(anom2 and anom2["异动幅度"]))
    check("ANOM-0002 异动方向=下降", bool(anom2) and anom2["异动方向"] == "下降")
    check("ANOM-0002 归因维度=渠道", bool(anom2) and anom2["归因维度"] == "渠道")
    check("ANOM-0002 水位终点=2026-09-02 23:59:59", bool(anom2) and anom2["水位终点"] == "2026-09-02 23:59:59")
    check("ANOM-0002 保留旧值", bool(anom2) and "异动描述" in str(anom2.get("旧值", {})))

    check("ANOM-0003 已迁移为独立条目", anom3 is not None)
    check("ANOM-0003 异动幅度=12.7", bool(anom3) and anom3["异动幅度"] == 12.7, str(anom3 and anom3["异动幅度"]))
    check("ANOM-0003 归因维度=活动", bool(anom3) and anom3["归因维度"] == "活动")
    check("ANOM-0003 归因结论保留", bool(anom3) and anom3["归因结论"] == "促销活动带来增长")

    check("旧窗口原表只读保留 4 条", len(client.get("/api/anomaly/legacy").json()["items"]) == 4)

    before = client.get("/api/anomaly/migrations/summary").json()["异动条目总数"]
    again = client.post("/api/anomaly/migrations", json={"batch": "verify-repeat"}).json()
    after = client.get("/api/anomaly/migrations/summary").json()["异动条目总数"]
    check("重复迁移不产生副本", before == after, f"{before} -> {after}")
    check("重复迁移全部跳过", again["entry"]["summary"]["迁移"] == 0)

    failed = again["entry"]["summary"]["失败区间"]
    check("失败区间返回 ANOM-0004", any(row["记录编号"] == "ANOM-0004" for row in failed))

    compare = client.get("/api/compare/export").json()["items"]
    comp2 = next((row for row in compare if row["方案编号"] == "COMP-0002"), None)
    check("同环比读取迁移后的归因结论", bool(comp2) and comp2["归因结论"] == "渠道投放减少导致下降")
    check("同环比带出归因水位", bool(comp2) and comp2.get("归因水位") == "2026-09-02 23:59:59")

    feed = client.get("/api/etl/watermark").json()
    check("流水线按水位消费 3 条", feed["total"] == 3, str(feed["total"]))
    check("流水线列出失败区间", any(row["记录编号"] == "ANOM-0004" for row in feed["失败区间"]))

    consumed = client.post("/api/etl/watermark/consume", json={}).json()
    check("流水线推进到最新水位", consumed["entry"]["消费到水位"] == "2026-09-03 23:59:59",
          consumed["entry"]["消费到水位"])
    check("水位推进后增量读取为空", client.get("/api/etl/watermark").json()["total"] == 0)

    # 同一天新增一条异动：水位相同但序号更大，不能被水位比较漏掉。
    created = client.post("/api/anomaly", json={"values": {
        "记录编号": "ANOM-0003B", "指标名称": "支付成功率", "异动方向": "下降",
        "异动幅度": "1.5", "归因维度": "活动", "归因结论": "同日新增的补充归因",
        "异动时间": "2026-09-03 18:00:00",
    }}).json()
    check("同日新增条目登记成功", created["ok"], created["message"])
    same_day = client.get("/api/etl/watermark").json()
    check("同水位的新条目仍可增量消费", same_day["total"] == 1, str(same_day["total"]))
    check("同水位新条目带出归因维度", same_day["items"][0]["归因维度"] == "活动")
    client.post("/api/etl/watermark/consume", json={})

    retry = client.post("/api/anomaly/migrations", json={
        "batch": "verify-retry",
        "codes": ["ANOM-0004"],
        "fixes": {"ANOM-0004": "2026-09-04 下降 2.0% | 归因维度：口径 | 归因结论：统计口径切换导致波动"},
    }).json()
    check("失败区间可从原记录重试", retry["entry"]["summary"]["迁移"] == 1)
    feed_after = client.get("/api/etl/watermark").json()
    check("重试后失败区间清空", feed_after["失败区间"] == [])
    check("重试后只增量消费 1 条", feed_after["total"] == 1, str(feed_after["total"]))
    check("重试条目带出修正后的归因维度", feed_after["items"][0]["归因维度"] == "口径")

    for name, ok, detail in checks:
        print(("PASS" if ok else "FAIL"), name, detail if detail and not ok else "")
    failed_count = sum(1 for _, ok, _ in checks if not ok)
    print(f"\n{len(checks) - failed_count}/{len(checks)} 通过")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
