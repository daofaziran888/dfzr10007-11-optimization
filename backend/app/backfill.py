#!/usr/bin/env python3
"""旧异动窗口回填脚本：部署与本地开发共用。

用法：
    python -m app.backfill                     # 回填全部旧窗口
    python -m app.backfill --batch B-20261114  # 指定批次名
    python -m app.backfill --check             # 只做解析检查，不写入
    python -m app.backfill --retry-failed      # 只对仍可解析的失败窗口重试
    python -m app.backfill --rollback B-xxx    # 按批次撤销迁移条目

重复执行按源记录判重，不会产生副本；失败区间保留在旧窗口原表，
修正后可用 --retry-failed 从原记录重新迁移。退出码：0 全部成功，1 仍有失败区间。

注意：当前仓库使用进程内内存仓库，脚本每次运行都是独立的一份数据；
对正在运行的服务做回填/重试请调用 /api/anomaly/migrations 接口。
接上真实数据库后，脚本会直接作用于持久化数据。
"""
from __future__ import annotations

import argparse
import json
import sys

from app.services.anomaly import AnomalyService, MigrationError, parse_legacy_window
from app.store import store


def _dry_run() -> dict[str, object]:
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    for row in store.rows("anomaly_legacy"):
        code = str(row.get("窗口编号") or "")
        try:
            parse_legacy_window(row)
            ok.append(code)
        except MigrationError as error:
            failed.append({"记录编号": code, "原因": str(error)})
    return {"旧窗口数": len(ok) + len(failed), "可迁移": ok, "失败区间": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旧异动窗口迁移回填")
    parser.add_argument("--batch", default=None, help="迁移批次名，默认按时间生成")
    parser.add_argument("--check", action="store_true", help="只做解析检查，不写入新结构")
    parser.add_argument("--retry-failed", action="store_true", help="只重试仍可解析的失败窗口")
    parser.add_argument(
        "--fix",
        action="append",
        default=[],
        metavar="CODE=TEXT",
        help="按原记录编号提供修正后的描述，仅用于本次迁移副本，原表旧值不变",
    )
    parser.add_argument("--rollback", default=None, metavar="BATCH", help="撤销指定批次的迁移条目")
    args = parser.parse_args(argv)

    if args.check:
        report = _dry_run()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["失败区间"] else 1

    service = AnomalyService()

    if args.rollback:
        result = service.rollback_batch(args.rollback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    codes = None
    if args.retry_failed:
        codes = [row["记录编号"] for row in _dry_run()["失败区间"]]  # type: ignore[index]
        if not codes:
            print("没有待重试的失败区间")
            return 0

    fixes: dict[str, str] = {}
    for item in args.fix:
        code, _, text = item.partition("=")
        if not code.strip() or not text.strip():
            print(f"--fix 需要 CODE=TEXT 形式，收到：{item}", file=sys.stderr)
            return 2
        fixes[code.strip()] = text.strip()

    summary = service.migrate_legacy(batch=args.batch, codes=codes, fixes=fixes)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["失败"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
