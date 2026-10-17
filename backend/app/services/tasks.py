"""任务存储抽象（S1-A）：TaskStore 接口 + Memory / Redis 双实现。

- TaskStore（Protocol）：create / get / update 三个 async 存储操作
- MemoryTaskStore：SQLite 持久化 + 内存写穿缓存（任务 H 现有实现完整搬迁；
  进程内轮询零 SQL，未命中回源 DB；重启时 processing 任务标记 interrupted）
- RedisTaskStore：redis.asyncio 实现，task:{task_id} 存任务全量 JSON；
  重启语义（interrupted 标记）由 init() 扫描实现，与 Memory 对齐

环境变量（app/config.py）：
- TASK_STORE=memory（默认）| redis
- REDIS_URL：仅 redis 模式生效；未配置或 redis 包缺失自动降级 memory 并打警告

模块级同步兼容包装（create_task / get_task / update_task / set_stage）委托
当前 store，业务层零改动。桥接用 asyncio.run：仅适用于同步上下文——
未来路由改 async def 时，须改为直接 await store 方法并删除桥接层。
"""
import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.config import REDIS_URL, TASK_DB_PATH, TASK_STORE
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
    }


def _task_from_payload(payload: dict, task_id: str, status: str, progress: int, stage: str, created_at: float) -> TranscribeTask:
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
        created_at=created_at,
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
    return _task_from_payload(data, data["task_id"], data["status"], data["progress"], data["stage"], data["created_at"])


class TaskStore(Protocol):
    """任务存储接口（S1-A）：全部方法 async。

    实现约定：create 返回全局未知 task_id；get 未知返回 None；
    update 未知 task 静默跳过（与现有调用方语义一致）。
    """

    async def create(self, video: VideoInfo | None, engine: str | None = None) -> str: ...

    async def get(self, task_id: str) -> TranscribeTask | None: ...

    async def update(self, task_id: str, **fields: object) -> None: ...


# 可选依赖：redis 包仅 redis 模式需要（requirements-redis.txt）；
# 缺失时 _build_store 降级 memory，模块导入不炸
try:
    import redis.asyncio as _redis_asyncio
except ImportError:
    _redis_asyncio = None  # type: ignore[assignment]


class MemoryTaskStore:
    """内存写穿缓存 + SQLite 持久化（任务 H 实现完整搬迁）。

    - 读：内存 _tasks 为一级缓存（进程内轮询零 SQL）；未命中回源 DB
    - 写：更新内存并同步写 DB
    - init：加载历史任务，processing 标记 interrupted（进程重启即中断）
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._tasks: dict[str, TranscribeTask] = {}
        self._lock = threading.Lock()
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
        return _task_from_payload(json.loads(row[6]), row[0], row[1], row[2], row[3], row[4])

    async def init(self) -> None:
        """幂等初始化：建库/建表、加载历史任务、未完成标记 interrupted。"""
        if self._initialized:
            return
        with self._lock:
            rows = self._connect().execute(
                "SELECT task_id, status, progress, stage, created_at, updated_at, payload_json FROM tasks"
            ).fetchall()
            for row in rows:
                task = self._task_of_row(row)
                if task.status == "processing":
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
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = time.time()
            self._upsert_row(task)


class RedisTaskStore:
    """redis.asyncio 实现：task:{task_id} 存任务全量 JSON。

    - 连接池绑定 event loop 不可跨 loop 复用 → 每个 loop 独立 client
      （同步桥接 asyncio.run 每次新建 loop，对应每次新 client，无跨 loop 复用问题）
    - init() 扫描全量任务并标记 interrupted，与 Memory 重启语义对齐
    """

    _KEY_PREFIX = "task:"

    def __init__(self, url: str, client_factory=None) -> None:
        self._url = url
        self._client_factory = client_factory  # 测试注入 fakeredis 用
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

    async def init(self) -> None:
        """启动初始化：processing 任务标记 interrupted（服务重启即中断）。"""
        client = self._client()
        async for key in client.scan_iter(match=f"{self._KEY_PREFIX}*"):
            raw = await client.get(key)
            if raw is None:
                continue
            task = _loads(raw)
            if task.status == "processing":
                task.status = "interrupted"
                task.updated_at = time.time()
                await client.set(key, _dumps(task))

    async def create(self, video: VideoInfo | None, engine: str | None = None) -> str:
        task_id = uuid.uuid4().hex
        task = TranscribeTask(task_id=task_id, video=video, engine=engine)
        await self._client().set(self._key(task_id), _dumps(task))
        return task_id

    async def get(self, task_id: str) -> TranscribeTask | None:
        raw = await self._client().get(self._key(task_id))
        return _loads(raw) if raw is not None else None

    async def update(self, task_id: str, **fields: object) -> None:
        task = await self.get(task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        task.updated_at = time.time()
        await self._client().set(self._key(task_id), _dumps(task))


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


def get_task_store() -> TaskStore:
    return _STORE


def _run_async(coro):
    """同步 → async 桥接：仅适用于同步上下文（FastAPI 同步 def 路由 /
    BackgroundTasks）。未来路由改 async def 时，须改为直接 await store 的
    async 方法并删除本桥接层（asyncio.run 不允许在已有 event loop 中调用）。"""
    return asyncio.run(coro)


def create_task(video: VideoInfo | None, engine: str | None = None) -> str:
    return _run_async(_STORE.create(video, engine))


def get_task(task_id: str) -> TranscribeTask | None:
    return _run_async(_STORE.get(task_id))


def update_task(task_id: str, **fields) -> None:
    _run_async(_STORE.update(task_id, **fields))


def set_stage(task_id: str, stage: str, progress: int) -> None:
    update_task(task_id, stage=stage, progress=progress)


def init_task_store(db_path: str | None = None) -> None:
    """启动初始化（幂等）：main.py 启动时调用。db_path 指定时重建 MemoryTaskStore（测试用）。"""
    global _STORE
    if db_path is not None:
        _STORE = MemoryTaskStore(db_path)
    # init 为启动钩子（不属于 TaskStore 存储接口），两个实现均有
    init = getattr(_STORE, "init", None)
    if init is not None:
        _run_async(init())
    else:  # pragma: no cover - 防御分支
        logger.warning("当前 store（%s）无 init()，跳过启动初始化", type(_STORE).__name__)


def reset_task_store_for_tests(db_path: str) -> None:
    """测试辅助：指向独立 DB 并强制重新初始化（清空内存缓存）。"""
    global _STORE
    _STORE = MemoryTaskStore(db_path)
    _run_async(_STORE.init())
