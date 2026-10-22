"""僵尸任务清理（S2-P4 Reaper）：把心跳超时的 processing 任务标为 failed。

为什么需要它：Worker 被 FC 强杀（超时 / OOM / 缩容）时进程内的 except 不会执行，
任务永远停在 processing。而 FC 的 3 次重试（间隔 0.5/1/2 秒）都会撞上
`TASK_LOCK_SECONDS=3600` 的执行锁返回 skipped，没人能把它推下去。

触发方式（生产）：FC 定时触发器 → 事件调用 → 容器 `/invoke` → 按事件特征分流到这里。
为什么不是「Timer 直接打 /internal/worker/reap」：定时触发器是**事件调用**，不能指定
HTTP 路径，FC 固定把 event POST 到 `/invoke`（见 docs/serverless-deploy.md §5.4）。
`/internal/worker/reap` 保留给本地脚本与人工 curl。

设计要点：
- 只标 failed，不重新入队：重投会撞上旧锁（见 docs/serverless-deploy.md §8.4 R3），
  且自动重跑有重复计费风险；让用户点重试（新 task_id + 新锁）语义最干净
- 不主动删 `task:lock:*`：任务已 failed，任何重投都会在 `_run_job` 的首个状态检查
  返回 skipped；强删锁会破坏「release 必须带 token」的不变量，若 Worker 其实还活着
  就会导致同一任务被跑第二遍
- `reaper:lock` 只省算力不承担正确性：fail_if_stale 自身是 CAS + 幂等
"""
import logging
import time
from dataclasses import dataclass, field

from app.config import REAPER_LOCK_SECONDS, TASK_STALE_SECONDS
from app.services import tasks as task_store
from app.services.tasks import REAPER_LOCK_ID, TaskStore

logger = logging.getLogger(__name__)

# 超时错误码（前端 src/lib/types.ts 的 ApiErrorCode 必须同步）
TASK_TIMEOUT_CODE = "TASK_TIMEOUT"
TASK_TIMEOUT_MESSAGE = "任务处理超时，可能因服务器资源受限中断，请重试"

# 抢不到 reaper 锁时的 skipped 原因
_SKIP_LOCKED = "locked"


@dataclass
class ReapResult:
    """一次收割的结果（端点直接序列化成响应体，便于验收与排查）。"""

    status: str = "ok"  # ok | skipped
    reason: str | None = None
    scanned: int = 0  # 扫描到的 processing 任务数
    reaped: int = 0  # 实际标为 failed 的任务数
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload: dict = {"status": self.status}
        if self.reason is not None:
            payload["reason"] = self.reason
        payload["scanned"] = self.scanned
        payload["reaped"] = self.reaped
        if self.task_ids:
            payload["task_ids"] = self.task_ids
        return payload


async def reap_once(
    store: TaskStore | None = None,
    *,
    stale_seconds: int | None = None,
    lock_seconds: int | None = None,
    dry_run: bool = False,
) -> ReapResult:
    """扫一遍僵尸任务。幂等：重复执行只会看到已 failed 的任务并跳过。

    dry_run=True 只统计不落库（本地排查「到底哪些任务会被判死」用）。
    """
    store = store or task_store.get_task_store()
    stale = TASK_STALE_SECONDS if stale_seconds is None else int(stale_seconds)
    lock_ttl = REAPER_LOCK_SECONDS if lock_seconds is None else int(lock_seconds)

    token = await store.acquire(REAPER_LOCK_ID, lock_ttl)
    if token is None:
        logger.info("另一个 Reaper 正在扫描，跳过本轮")
        return ReapResult(status="skipped", reason=_SKIP_LOCKED)

    try:
        processing = await store.list_processing_tasks()
        result = ReapResult(scanned=len(processing))
        error = {"code": TASK_TIMEOUT_CODE, "message": TASK_TIMEOUT_MESSAGE}
        now = time.time()

        for task in processing:
            if now - task.updated_at <= stale:
                continue  # 心跳正常：可能有实例正在处理，绝不能动
            if dry_run:
                result.reaped += 1
                result.task_ids.append(task.task_id)
                continue
            # 二次校验在存储层内做（扫描到这里之间可能刚被心跳刷新）
            if await store.fail_if_stale(task.task_id, stale, error):
                result.reaped += 1
                result.task_ids.append(task.task_id)

        if result.reaped:
            logger.warning(
                "Reaper 收割了 %d 个僵尸任务（扫描 %d 个 processing，阈值 %ss）：%s",
                result.reaped,
                result.scanned,
                stale,
                ",".join(result.task_ids[:20]),
            )
        else:
            logger.info("Reaper 扫描完成：%d 个 processing 任务均存活", result.scanned)
        return result
    finally:
        await store.release(REAPER_LOCK_ID, token)


def reap_once_sync(**kwargs) -> ReapResult:
    """同步入口（本地 CLI 脚本）。

    必须走 tasks 的常驻事件循环桥接，不能 asyncio.run：后者每次新建 loop，
    会让 RedisTaskStore 按 loop 缓存的 client 只增不减（S2-P0 修掉的连接泄漏）。
    """
    return task_store._run_async(reap_once(**kwargs))
