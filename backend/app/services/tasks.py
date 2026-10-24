"""任务存储抽象（S1-A）：TaskStore 接口 + Memory / Redis 双实现。

- TaskStore（Protocol）：create / get / update 三个 async 存储操作
- MemoryTaskStore：SQLite 持久化 + 内存写穿缓存（任务 H 现有实现完整搬迁；
  进程内轮询零 SQL，未命中回源 DB；重启时 processing 任务标记 interrupted）
- RedisTaskStore：redis.asyncio 实现，task:{task_id} 存任务全量 JSON；
  重启语义（interrupted 标记）由 init() 扫描实现

S2-P0 地基（异步架构前置）：
- TTL：create / update / init 写入统一走 `_set`，按 TASK_TTL_SECONDS 滑动续期，
  避免任务 JSON（含完整文字稿）在 Redis 里无限增长
- init 语义可关：`init(recover_interrupted=False)` 只建表/预热，不做任何状态改写。
  Worker 必须用 False——Serverless 每次冷启动都会跑 init，recover 会误杀其他实例
  正在跑的任务；Redis 实现还要求任务「陈旧」（updated_at 早于 TASK_STALE_SECONDS）
  才判定 interrupted，避免把心跳正常的任务判死
- attempts：任务级重试计数（P2 幂等领取 / P4 reaper 使用），随 JSON 持久化
- 同步桥接复用**单个常驻事件循环**（后台线程），不再每次 asyncio.run 新建 loop：
  Redis client 按 loop 缓存，旧实现每次调用都会新建 client 且永不回收（连接泄漏）

S2-P2 执行锁（Worker 幂等领取）：
- acquire / release / renew 三个 async 方法，两个实现均支持
- Redis：`SET task:lock:{id} {token} NX EX ttl` 原子领取；释放/续期用 WATCH/MULTI
  比对令牌（不用 EVAL，fakeredis 无 Lua 运行时）；令牌不匹配说明锁已过期被他人接手
- Memory：进程内字典 + 过期时刻（单进程，语义与 Redis 对齐便于测试复用）
- init() 的恢复扫描已跳过含 `:` 的派生 key，`task:lock:*` 不会被当成任务

S2-P4 僵尸任务清理（Reaper）：
- list_processing_tasks / fail_if_stale 两个 async 方法，两个实现均支持
- 判定策略（陈旧阈值、错误码、文案）不落在存储层，全部收敛在 app/services/reaper.py
- fail_if_stale 必须做「状态 + 陈旧」二次校验：扫描与写入之间心跳可能刚刷新过，
  盲写会把正在跑的任务误杀；Redis 侧用 WATCH/MULTI 保证校验与写入原子
- started_at（None = 还在排队）决定 reaper 用哪档阈值：排队任务没有心跳写入，
  必须用更宽的 max(TASK_STALE_SECONDS, REAPER_QUEUED_STALE_SECONDS)

P1 并发修复（update 的 CAS）：
- RedisTaskStore.update 改 WATCH/MULTI 读-改-写；MemoryTaskStore 在锁内做等价保护
- 两者都带**终态保护**：任务已是 completed/failed/interrupted 且本次不改 status 时
  放弃写入，防止晚到的进度心跳把 Reaper 刚写的 failed 覆盖回 processing

环境变量（app/config.py）：
- TASK_STORE=memory（默认）| redis
- REDIS_URL：仅 redis 模式生效；未配置或 redis 包缺失自动降级 memory 并打警告
- TASK_TTL_SECONDS（默认 604800 = 7 天，0 = 不设 TTL）
- TASK_INIT_MODE=recover（默认）| none
- TASK_STALE_SECONDS（默认 600，仅 Redis init 判定陈旧用）

模块级同步兼容包装（create_task / get_task / update_task / set_stage）委托
当前 store，业务层零改动。桥接仅适用于同步上下文——路由改 async def 后
应直接 await store 方法（在事件循环内调用桥接会抛 RuntimeError）。
"""
import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.config import (
    REDIS_URL,
    REAPER_QUEUED_STALE_SECONDS,
    TASK_DB_PATH,
    TASK_INIT_MODE,
    TASK_STALE_SECONDS,
    TASK_STORE,
    TASK_TTL_SECONDS,
)
from app.schemas import TranscriptItem, VideoInfo

if TYPE_CHECKING:
    import redis.asyncio

logger = logging.getLogger(__name__)


@dataclass
class TranscribeTask:
    task_id: str
    status: str = "processing"  # processing | completed | failed | interrupted
    stage: str = "parse_link"  # parse_link | extract_audio | asr
    progress: int = 0
    video: VideoInfo | None = None
    transcript: list[TranscriptItem] = field(default_factory=list)
    plain_text: str | None = None
    error: dict | None = None  # { code, message }
    engine: str | None = None  # 本次任务的 ASR 引擎（local | cloud | 引擎名），重试沿用
    transcript_source: str | None = None  # 文字稿来源：subtitle_cc | subtitle_ai | asr
    attempts: int = 0  # 已执行次数（S2：Worker 重试计数 / reaper 判定依据）
    started_at: float | None = None  # Worker 抢到锁、真正开跑的时刻；None = 还在排队
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# ---- 共享序列化：重字段（video/transcript 等）与标量字段分开处理 ----
# 兼容历史 payload_json（缺省字段走 .get 兜底）；旧 SQLite 数据可继续回源


def _payload_dict(task: TranscribeTask) -> dict:
    return {
        "video": task.video.model_dump() if task.video else None,
        "transcript": [item.model_dump() for item in task.transcript],
        "plain_text": task.plain_text,
        "error": task.error,
        "engine": task.engine,
        "transcript_source": task.transcript_source,
        "attempts": task.attempts,
        "started_at": task.started_at,
    }


def _task_from_payload(
    payload: dict,
    task_id: str,
    status: str,
    progress: int,
    stage: str,
    created_at: float,
    updated_at: float | None = None,
) -> TranscribeTask:
    video = payload.get("video")
    return TranscribeTask(
        task_id=task_id,
        status=status,
        progress=progress,
        stage=stage,
        video=VideoInfo(title=video["title"], cover=video["cover"], duration=video["duration"], platform=video["platform"], videoId=video["videoId"]) if video else None,
        transcript=[TranscriptItem(time=item["time"], text=item["text"]) for item in payload.get("transcript") or []],
        plain_text=payload.get("plain_text"),
        error=payload.get("error"),
        engine=payload.get("engine"),
        transcript_source=payload.get("transcript_source"),
        attempts=int(payload.get("attempts") or 0),
        # started_at 决定 reaper 用哪档陈旧阈值（排队 vs 执行中），必须往返保真；
        # 旧数据无该字段 → None（按「排队中」处理，更保守：不会误杀刚派发的任务）
        started_at=float(payload["started_at"]) if payload.get("started_at") is not None else None,
        created_at=created_at,
        # updated_at 必须往返保真：S2 的「陈旧判定」（心跳超时）完全依赖它；
        # 旧数据无该字段时回落到 created_at（更保守：更容易被判为陈旧）
        updated_at=updated_at if updated_at is not None else created_at,
    )


def _dumps(task: TranscribeTask) -> str:
    """全量 JSON（Redis 存储格式；SQLite 的 payload_json 列也复用）。"""
    return json.dumps(
        {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "stage": task.stage,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            **_payload_dict(task),
        },
        ensure_ascii=False,
    )


def _loads(raw: str) -> TranscribeTask:
    data = json.loads(raw)
    return _task_from_payload(
        data,
        data["task_id"],
        data["status"],
        data["progress"],
        data["stage"],
        data["created_at"],
        data.get("updated_at"),
    )


class TaskStore(Protocol):
    """任务存储接口（S1-A）：全部方法 async。

    实现约定：create 返回全局未知 task_id；get 未知返回 None；
    update 未知 task 静默跳过（与现有调用方语义一致）。
    """

    async def create(self, video: VideoInfo | None, engine: str | None = None) -> str: ...

    async def get(self, task_id: str) -> TranscribeTask | None: ...

    async def update(self, task_id: str, **fields: object) -> None: ...

    # ---- 执行锁（S2-P2）：Worker 幂等领取用 ----

    async def acquire(self, task_id: str, ttl_seconds: int) -> str | None:
        """尝试领取任务锁，返回持有令牌；已被他人持有返回 None。"""
        ...

    async def release(self, task_id: str, token: str) -> None:
        """释放锁：仅当令牌匹配时删除（避免误删他人已续期的锁）。"""
        ...

    async def renew(self, task_id: str, token: str, ttl_seconds: int) -> bool:
        """续期锁：令牌匹配返回 True。"""
        ...

    # ---- 僵尸任务清理（S2-P4 Reaper）----

    async def list_processing_tasks(self) -> list[TranscribeTask]:
        """列出全部 status=processing 的任务（不做陈旧判定，策略留给 reaper 层）。

        为什么不在存储层过滤陈旧：`updated_at` 与 `status` 都在任务 JSON 体内，
        无论哪种实现都必须先反序列化才知道状态，服务端过滤省不掉工作量。
        把「多陈旧算僵尸」这条策略收敛在 app/services/reaper.py 一处，避免两套实现各写一遍。
        """
        ...

    async def fail_if_stale(
        self, task_id: str, stale_seconds: int, error: dict
    ) -> bool:
        """仅当「仍是 processing」且「now - updated_at > stale_seconds」时标 failed。

        返回是否真的改写。**必须二次校验**：扫描与写入之间心跳可能刚刷新过
        updated_at，盲写会把正在跑的任务误杀（Serverless 下实例可能只是慢）。

        Redis 用 WATCH/MULTI 比对（与 release 同一套路，不用 EVAL：fakeredis 无 Lua）。
        """
        ...


# Reaper 互斥锁的保留 id：复用 acquire/release，不额外扩接口。
# 为什么安全：task_id 恒为 uuid4().hex（32 位十六进制），与 "__reaper__" 不可能碰撞；
# 对应 Redis key 为 task:lock:__reaper__，其中含 ":" 会被派生 key 过滤规则跳过。
REAPER_LOCK_ID = "__reaper__"


# 可选依赖：redis 包仅 redis 模式需要（requirements-redis.txt）；
# 缺失时 _build_store 降级 memory，模块导入不炸
try:
    import redis.asyncio as _redis_asyncio
    from redis.exceptions import WatchError as _WatchError
except ImportError:
    _redis_asyncio = None  # type: ignore[assignment]
    _WatchError = None  # type: ignore[assignment]


def _as_str(value: object) -> str | None:
    """Redis 返回值归一：decode_responses 未开启时是 bytes（fakeredis 测试默认）。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


class MemoryTaskStore:
    """内存写穿缓存 + SQLite 持久化（任务 H 实现完整搬迁）。

    - 读：内存 _tasks 为一级缓存（进程内轮询零 SQL）；未命中回源 DB
    - 写：更新内存并同步写 DB
    - init：加载历史任务，processing 标记 interrupted（进程重启即中断）
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._tasks: dict[str, TranscribeTask] = {}
        self._lock = threading.Lock()
        # task_id -> (持有令牌, 过期时刻)；单进程锁，TTL 仅用于语义对齐 Redis
        self._locks: dict[str, tuple[str, float]] = {}
        self._conn: sqlite3.Connection | None = None
        self._initialized = False
        self._db_path: str = db_path or TASK_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            # 静态 DDL（常量，不含任何用户输入）
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL, stage TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, payload_json TEXT NOT NULL)"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    def _upsert_row(self, task: TranscribeTask) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO tasks (task_id, status, progress, stage, created_at, updated_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, progress=excluded.progress, stage=excluded.stage, updated_at=excluded.updated_at, payload_json=excluded.payload_json",
            (task.task_id, task.status, task.progress, task.stage, task.created_at, task.updated_at, json.dumps(_payload_dict(task), ensure_ascii=False)),
        )
        conn.commit()

    def _task_of_row(self, row: tuple) -> TranscribeTask:
        return _task_from_payload(json.loads(row[6]), row[0], row[1], row[2], row[3], row[4], row[5])

    async def init(self, recover_interrupted: bool = True) -> None:
        """幂等初始化：建库/建表、加载历史任务。

        recover_interrupted=True（API 默认）：processing 任务标记 interrupted（进程重启即中断）；
        False（Worker / 只读实例）：只加载，不改任何任务状态。
        """
        if self._initialized:
            return
        with self._lock:
            rows = self._connect().execute(
                "SELECT task_id, status, progress, stage, created_at, updated_at, payload_json FROM tasks"
            ).fetchall()
            for row in rows:
                task = self._task_of_row(row)
                if recover_interrupted and task.status == "processing":
                    task.status = "interrupted"
                    task.updated_at = time.time()
                    self._upsert_row(task)
                self._tasks[task.task_id] = task
            self._initialized = True

    async def create(self, video: VideoInfo | None, engine: str | None = None) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            task = TranscribeTask(task_id=task_id, video=video, engine=engine)
            self._tasks[task_id] = task
            self._upsert_row(task)
        return task_id

    async def get(self, task_id: str) -> TranscribeTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is not None:
            return task
        # 缓存未命中（服务重启后按 taskId 直访）→ 回源 DB
        with self._lock:
            row = self._connect().execute(
                "SELECT task_id, status, progress, stage, created_at, updated_at, payload_json FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            task = self._task_of_row(row)
            self._tasks[task.task_id] = task
            return task

    async def update(self, task_id: str, **fields: object) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            # 终态保护（与 RedisTaskStore.update 语义对齐）：已是 completed/failed/
            # interrupted 且本次不改 status → 放弃，防止晚到的进度心跳把 Reaper
            # 刚写的 failed 覆盖回 processing，导致前端永久转圈。
            if task.status != "processing" and "status" not in fields:
                return
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = time.time()
            self._upsert_row(task)

    # ---- 执行锁（单进程：内存字典 + TTL 过期语义）----

    def _lock_alive(self, task_id: str) -> str | None:
        """调用方需持有 self._lock；返回仍有效的令牌（过期条目顺手清理）。"""
        entry = self._locks.get(task_id)
        if entry is None:
            return None
        token, expires_at = entry
        if expires_at <= time.time():
            del self._locks[task_id]
            return None
        return token

    async def acquire(self, task_id: str, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        with self._lock:
            if self._lock_alive(task_id) is not None:
                return None
            self._locks[task_id] = (token, time.time() + max(0, ttl_seconds))
        return token

    async def release(self, task_id: str, token: str) -> None:
        with self._lock:
            if self._lock_alive(task_id) == token:
                self._locks.pop(task_id, None)

    async def renew(self, task_id: str, token: str, ttl_seconds: int) -> bool:
        with self._lock:
            if self._lock_alive(task_id) != token:
                return False
            self._locks[task_id] = (token, time.time() + max(0, ttl_seconds))
            return True

    # ---- 僵尸任务清理（S2-P4 Reaper）----

    async def list_processing_tasks(self) -> list[TranscribeTask]:
        """遍历内存缓存（init 已把全部历史行载入，缓存即全集）。"""
        with self._lock:
            return [task for task in self._tasks.values() if task.status == "processing"]

    async def fail_if_stale(
        self, task_id: str, stale_seconds: int, error: dict
    ) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != "processing":
                return False
            now = time.time()
            if now - task.updated_at <= stale_seconds:
                return False
            task.status = "failed"
            task.error = error
            task.updated_at = now
            self._upsert_row(task)
            return True


class RedisTaskStore:
    """redis.asyncio 实现：task:{task_id} 存任务全量 JSON。

    - 连接池绑定 event loop 不可跨 loop 复用 → 每个 loop 独立 client；
      `_run_async` 复用常驻 loop 后，client 数量收敛为「桥接 loop + 应用 loop」，
      不再随调用次数增长（S2-P0 修复连接泄漏）
    - 所有写入走 `_set`：按 TASK_TTL_SECONDS 滑动续期（0 = 不设 TTL）
    - init() 只把「陈旧」的 processing 任务标记 interrupted（recover_interrupted=False 时完全不改）
    """

    _KEY_PREFIX = "task:"

    def __init__(self, url: str, client_factory=None, ttl_seconds: int | None = None, stale_seconds: int | None = None) -> None:
        self._url = url
        self._client_factory = client_factory  # 测试注入 fakeredis 用
        self._ttl = TASK_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
        self._stale = TASK_STALE_SECONDS if stale_seconds is None else int(stale_seconds)
        self._clients: dict[asyncio.AbstractEventLoop, "redis.asyncio.Redis"] = {}

    def _client(self) -> "redis.asyncio.Redis":
        if _redis_asyncio is None:
            raise RuntimeError("redis 包未安装：pip install -r requirements-redis.txt")
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            if self._client_factory is not None:
                client = self._client_factory(self._url)
            else:
                client = _redis_asyncio.from_url(self._url, decode_responses=True)
            self._clients[loop] = client
        return client

    @staticmethod
    def _key(task_id: str) -> str:
        return f"{RedisTaskStore._KEY_PREFIX}{task_id}"

    async def _set(self, key: str, task: TranscribeTask) -> None:
        """统一写入入口：带 TTL 滑动续期（TASK_TTL_SECONDS=0 时退化为普通 SET）。"""
        if self._ttl > 0:
            await self._client().set(key, _dumps(task), ex=self._ttl)
        else:
            await self._client().set(key, _dumps(task))

    async def close(self) -> None:
        """关闭当前 loop 的 client（其余 loop 的由各自 loop 结束时回收）。

        生产无需显式调用（进程退出即断连）；测试与长驻 Worker 显式释放更干净。
        """
        loop = asyncio.get_running_loop()
        client = self._clients.pop(loop, None)
        if client is not None:
            await client.aclose()

    async def init(self, recover_interrupted: bool = True) -> None:
        """启动初始化。

        recover_interrupted=True：把**陈旧**的 processing 任务标记 interrupted。
        「陈旧」= 超过 TASK_STALE_SECONDS 没有写入（心跳），用于区分「上一个进程留下的
        遗留任务」与「别的实例正在跑的任务」——Serverless 下每个实例冷启动都会 init，
        无脑全量改写会把并发实例的在途任务误杀。

        recover_interrupted=False：只建连，不做任何状态改写（Worker 用）。
        """
        client = self._client()
        if not recover_interrupted:
            return
        now = time.time()
        # 与 reaper 同源的两档阈值：started_at 为空说明还在排队（派发积压 / 实例打满），
        # 排队期间没有心跳写入，套用 _stale 会把刚派发的任务误标 interrupted。
        queued_stale = max(self._stale, REAPER_QUEUED_STALE_SECONDS)
        async for key in client.scan_iter(match=f"{self._KEY_PREFIX}*"):
            # 只处理 task:{task_id}；未来的派生 key（如 task:lock:{id}）不参与恢复扫描
            suffix = key.decode() if isinstance(key, bytes) else key
            if ":" in suffix[len(self._KEY_PREFIX):]:
                continue
            raw = await client.get(key)
            if raw is None:
                continue
            task = _loads(raw)
            if task.status != "processing":
                continue
            threshold = self._stale if task.started_at is not None else queued_stale
            if now - task.updated_at < threshold:
                continue  # 心跳正常 / 仍在排队：可能有别的实例正在处理，不动它
            task.status = "interrupted"
            task.updated_at = now
            await self._set(key, task)

    async def create(self, video: VideoInfo | None, engine: str | None = None) -> str:
        task_id = uuid.uuid4().hex
        task = TranscribeTask(task_id=task_id, video=video, engine=engine)
        await self._set(self._key(task_id), task)
        return task_id

    async def get(self, task_id: str) -> TranscribeTask | None:
        raw = await self._client().get(self._key(task_id))
        return _loads(raw) if raw is not None else None

    async def update(self, task_id: str, **fields: object) -> None:
        """WATCH/MULTI 读-改-写（P1-2 CAS）：防止心跳把 Reaper 刚写的 failed 覆盖回 processing。

        两个保护：
        - **终态保护**：任务已是 completed/failed/interrupted 且本次不改 `status` → 直接
          放弃。晚到的进度心跳（30s 一拍）本来会把 failed 改回 processing，前端就会
          一直转圈等一个永远不会完成的转写。
        - **WATCH 冲突**：读到写之间若有人改过（Reaper / 别的实例），放弃本次写入；
          下一次心跳会带着最新状态重试，不丢正确性。
        """
        key = self._key(task_id)
        client = self._client()
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is None:
                    await pipe.unwatch()
                    return
                task = _loads(raw)
                if task.status != "processing" and "status" not in fields:
                    await pipe.unwatch()
                    return  # 终态不可被非状态字段覆盖
                for name, value in fields.items():
                    setattr(task, name, value)
                task.updated_at = time.time()
                pipe.multi()
                if self._ttl > 0:
                    pipe.set(key, _dumps(task), ex=self._ttl)
                else:
                    pipe.set(key, _dumps(task))
                await pipe.execute()
            except _WatchError:  # type: ignore[misc]
                return  # 期间被改动：本次写入作废，避免覆盖别人的状态

    # ---- 执行锁（SET NX EX 原子领取 + WATCH 比对释放）----

    @staticmethod
    def _lock_key(task_id: str) -> str:
        return f"{RedisTaskStore._KEY_PREFIX}lock:{task_id}"

    async def acquire(self, task_id: str, ttl_seconds: int) -> str | None:
        """SET key token NX EX ttl：单条命令原子，无需额外事务。"""
        token = uuid.uuid4().hex
        ok = await self._client().set(self._lock_key(task_id), token, nx=True, ex=max(1, ttl_seconds))
        return token if ok else None

    async def release(self, task_id: str, token: str) -> None:
        """比对令牌后删除。

        不用 Lua/EVAL：fakeredis 默认无 Lua 运行时，WATCH/MULTI 与 rate_limit 实现一致。
        比对是必要的——锁可能已因 TTL 过期被他人重新领取，直接 DEL 会删掉别人的锁。
        """
        key = self._lock_key(task_id)
        client = self._client()
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                if _as_str(await pipe.get(key)) != token:
                    await pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()
            except _WatchError:  # type: ignore[misc]
                return  # 期间被改动：对方持有，放手即可

    async def renew(self, task_id: str, token: str, ttl_seconds: int) -> bool:
        key = self._lock_key(task_id)
        client = self._client()
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                if _as_str(await pipe.get(key)) != token:
                    await pipe.unwatch()
                    return False
                pipe.multi()
                pipe.expire(key, max(1, ttl_seconds))
                result = await pipe.execute()
                return bool(result and result[0])
            except _WatchError:  # type: ignore[misc]
                return False

    # ---- 僵尸任务清理（S2-P4 Reaper）----

    async def list_processing_tasks(self) -> list[TranscribeTask]:
        """SCAN task:* → 跳过派生 key → 逐条反序列化后按状态过滤。

        用 SCAN 游标而非 KEYS：KEYS 在大 key 空间下会阻塞 Redis 主线程。
        复杂度 O(任务总数)，已完成任务也要读一遍——status 在 JSON 体内，
        没有廉价的服务端过滤手段（规模化优化见 docs/s2-async-recon.md 的 ZSET 索引）。
        """
        client = self._client()
        prefix = self._KEY_PREFIX
        tasks: list[TranscribeTask] = []
        async for key in client.scan_iter(match=f"{prefix}*"):
            suffix = _as_str(key) or ""
            if ":" in suffix[len(prefix):]:
                continue  # task:lock:* 等派生 key 不是任务本体
            raw = await client.get(key)
            if raw is None:
                continue  # SCAN 与 GET 之间被 TTL 删除
            task = _loads(raw)
            if task.status == "processing":
                tasks.append(task)
        return tasks

    async def fail_if_stale(
        self, task_id: str, stale_seconds: int, error: dict
    ) -> bool:
        """WATCH 任务 key → 二次校验 status/updated_at → MULTI SET。

        校验与写入之间任何写入都会让 WATCH 失效（WatchError），此时放弃本轮——
        说明有人刚动过这条任务（心跳/收尾），下一轮 Reaper 再判即可。
        """
        key = self._key(task_id)
        client = self._client()
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is None:
                    await pipe.unwatch()
                    return False
                task = _loads(raw)
                now = time.time()
                if task.status != "processing":
                    await pipe.unwatch()
                    return False
                if now - task.updated_at <= stale_seconds:
                    await pipe.unwatch()
                    return False
                task.status = "failed"
                task.error = error
                task.updated_at = now
                pipe.multi()
                if self._ttl > 0:
                    pipe.set(key, _dumps(task), ex=self._ttl)
                else:
                    pipe.set(key, _dumps(task))
                await pipe.execute()
                return True
            except _WatchError:  # type: ignore[misc]
                return False


def _build_store(mode: str, redis_url: str | None) -> TaskStore:
    """按 TASK_STORE 环境变量构造 store；redis 模式缺 REDIS_URL 或 redis 包时降级 memory。"""
    if mode == "redis":
        if not redis_url:
            logger.warning("TASK_STORE=redis 但 REDIS_URL 未配置，已降级为 memory")
            return MemoryTaskStore()
        if _redis_asyncio is None:
            logger.warning("TASK_STORE=redis 但 redis 包未安装（pip install -r requirements-redis.txt），已降级为 memory")
            return MemoryTaskStore()
        return RedisTaskStore(redis_url)
    if mode != "memory":
        logger.warning("未知 TASK_STORE=%r，使用 memory", mode)
    return MemoryTaskStore()


_STORE: TaskStore = _build_store(TASK_STORE, REDIS_URL)

# 同步桥接的常驻事件循环（懒启动，守护线程持有）
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()

# 单次桥接调用的最长等待（秒）：防止 Redis 挂起时永久占住请求线程
_BRIDGE_TIMEOUT_SECONDS = 30.0


def get_task_store() -> TaskStore:
    return _STORE


def _run_async(coro):
    """同步 → async 桥接：在**常驻后台事件循环**上执行并等待结果。

    为什么不是 asyncio.run：每次新建 loop 会让按 loop 缓存的 Redis client
    （RedisTaskStore._clients）只增不减——每次 store 调用泄漏一个连接池。
    现在所有桥接调用共用同一个 loop，client 数量收敛为常数。

    仅适用于同步上下文（FastAPI 同步 def 路由 / BackgroundTasks 线程）。
    在已有事件循环的协程里调用会抛 RuntimeError（避免阻塞自身 loop 造成死锁），
    此类调用方应改为直接 await store 方法。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()  # 避免 "coroutine was never awaited" 警告
        raise RuntimeError(
            "_run_async 不能在事件循环内调用：请直接 await store 的 async 方法"
        )
    future = asyncio.run_coroutine_threadsafe(coro, _bridge_loop())
    try:
        # 超时兜底：桥接 loop 卡死（Redis 连接挂起）时不至于把请求线程永久占住
        return future.result(timeout=_BRIDGE_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        raise TimeoutError(
            f"任务存储操作超时（>{_BRIDGE_TIMEOUT_SECONDS}s）：请检查 REDIS_URL 连通性"
        ) from None


def _bridge_loop() -> asyncio.AbstractEventLoop:
    """懒启动常驻事件循环（守护线程）；进程退出即结束，无需显式关闭。"""
    global _LOOP
    with _LOOP_LOCK:
        loop = _LOOP
        if loop is not None and not loop.is_closed():
            return loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        threading.Thread(target=_run, name="task-store-bridge", daemon=True).start()
        if not ready.wait(timeout=5):  # pragma: no cover - 极端调度失败
            raise RuntimeError("任务存储桥接事件循环启动超时")
        _LOOP = loop
        return loop


def create_task(video: VideoInfo | None, engine: str | None = None) -> str:
    return _run_async(_STORE.create(video, engine))


def get_task(task_id: str) -> TranscribeTask | None:
    return _run_async(_STORE.get(task_id))


def update_task(task_id: str, **fields) -> None:
    _run_async(_STORE.update(task_id, **fields))


def set_stage(task_id: str, stage: str, progress: int) -> None:
    update_task(task_id, stage=stage, progress=progress)


def init_task_store(db_path: str | None = None, recover_interrupted: bool | None = None) -> None:
    """启动初始化（幂等）。

    db_path 指定时重建 MemoryTaskStore（测试用）。
    recover_interrupted 缺省由 TASK_INIT_MODE 决定：recover（API，默认）| none（Worker）。
    显式传参优先于环境变量。
    """
    global _STORE
    if db_path is not None:
        _STORE = MemoryTaskStore(db_path)
    if recover_interrupted is None:
        recover_interrupted = TASK_INIT_MODE != "none"
    # init 为启动钩子（不属于 TaskStore 存储接口），两个实现均有
    init = getattr(_STORE, "init", None)
    if init is not None:
        _run_async(init(recover_interrupted=recover_interrupted))
    else:  # pragma: no cover - 防御分支
        logger.warning("当前 store（%s）无 init()，跳过启动初始化", type(_STORE).__name__)


def reset_task_store_for_tests(db_path: str) -> None:
    """测试辅助：指向独立 DB 并强制重新初始化（清空内存缓存）。"""
    global _STORE
    _STORE = MemoryTaskStore(db_path)
    _run_async(_STORE.init(recover_interrupted=True))
