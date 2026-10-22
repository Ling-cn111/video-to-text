"""僵尸任务清理脚本（S2-P4）：本地一次性触发 reaper。

用法（在 backend/ 目录下，需先激活 .venv 或直接用解释器全路径）：

    python -m scripts.reap              # 收割陈旧任务
    python -m scripts.reap --dry-run    # 只列出会被判死的任务，不落库
    python -m scripts.reap --stale 60   # 临时改陈旧阈值（秒）

线上由 FC 定时触发器经 worker 的 `/invoke` 自动执行，本脚本只用于：
1. 本地开发时手工验一遍判定逻辑（尤其是 `--dry-run`）
2. 云上定时器异常时人工兜底

为什么不做成 `app.main` 的后台线程：API 函数在 FC 上会同时存在多个实例，
每个实例都起线程会让 N 个 reaper 互相抢锁空转，且把守护线程引入无状态的
请求进程没有必要。需要常驻就用云上的定时触发器。
"""
import argparse
import json
import logging
import sys

from app.services.reaper import reap_once_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="收割心跳超时的僵尸转写任务（S2-P4）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不落库（用于确认判定阈值是否合适）",
    )
    parser.add_argument(
        "--stale",
        type=int,
        default=None,
        help="陈旧阈值（秒），缺省取 TASK_STALE_SECONDS",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出 INFO 级日志",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = reap_once_sync(stale_seconds=args.stale, dry_run=args.dry_run)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    # skipped（另一个 reaper 正在跑）不是错误，退出码 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
