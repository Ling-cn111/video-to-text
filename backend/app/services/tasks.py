"""转写任务注册表：SQLite 持久化 + 内存写穿缓存（任务 H）。

- 存储：backend/data/tasks.db（TASK_DB_PATH 可配），表 tasks(task_id, status, progress,
  stage, created_at, updated_at, payload_json)；payload_json 承载 video/transcript 等
  重字段。全部 SQL 以字面量内联于 execute 调用，值经 ? 参数绑定，无用户输入参与组装。
- 读：内存 _TASKS 为一级缓存（进程内轮询零 SQL）；未命中回源 DB（服务重启后恢复）
- 写：更新内存并同步写 DB
- 启动：init_task_store() 加载历史任务，并把 status='processing' 的任务标记为
  'interrupted'（进程重启即中断），前端显示「任务已中断，请重试」+ 重试按钮
- 接口签名与旧内存版完全一致：create_task / get_task / update_task / set_stage；
  多 worker 部署仍需换外部队列（SQLite 为单机文件库）
"""
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import TASK_DB_PATH
from app.schemas import TranscriptItem, VideoInfo

_TASKS: dict[str, "TranscribeTask"] = {}
_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_INITIALIZED = False
_DB_PATH: str = TASK_DB_PATH


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


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        # 静态 DDL（常量，不含任何用户输入）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL, stage TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, payload_json TEXT NOT NULL)"
        )
        conn.commit()
        _CONN = conn
    return _CONN


def _payload_of(task: TranscribeTask) -> str:
    return json.dumps(
        {
            "video": task.video.model_dump() if task.video else None,
            "transcript": [item.model_dump() for item in task.transcript],
            "plain_text": task.plain_text,
            "error": task.error,
            "engine": task.engine,
            "transcript_source": task.transcript_source,
        },
        ensure_ascii=False,
    )


def _task_of_row(row: tuple) -> "TranscribeTask":
    task_id, status, progress, stage, created_at, _updated, payload_json = row
    payload = json.loads(payload_json)
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


def _upsert_row(task: TranscribeTask) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO tasks (task_id, status, progress, stage, created_at, updated_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, progress=excluded.progress, stage=excluded.stage, updated_at=excluded.updated_at, payload_json=excluded.payload_json",
        (task.task_id, task.status, task.progress, task.stage, task.created_at, task.updated_at, _payload_of(task)),
    )
    conn.commit()


def init_task_store(db_path: str | None = None) -> None:
    """初始化（幂等）：建库/建表、加载历史任务、未完成标记 interrupted。main.py 启动时调用。"""
    global _INITIALIZED, _DB_PATH
    if _INITIALIZED:
        return
    if db_path:
        _DB_PATH = db_path
    with _LOCK:
        conn = _connect()
        rows = conn.execute(
            "SELECT task_id, status, progress, stage, created_at, updated_at, payload_json FROM tasks"
        ).fetchall()
        for row in rows:
            task = _task_of_row(row)
            if task.status == "processing":
                task.status = "interrupted"
                task.updated_at = time.time()
                _upsert_row(task)
            _TASKS[task.task_id] = task
        _INITIALIZED = True


def create_task(video: VideoInfo | None, engine: str | None = None) -> str:
    task_id = uuid.uuid4().hex
    with _LOCK:
        task = TranscribeTask(task_id=task_id, video=video, engine=engine)
        _TASKS[task_id] = task
        _upsert_row(task)
    return task_id


def get_task(task_id: str) -> TranscribeTask | None:
    with _LOCK:
        task = _TASKS.get(task_id)
    if task is not None:
        return task
    # 缓存未命中（服务重启后按 taskId 直访）→ 回源 DB
    with _LOCK:
        row = _connect().execute(
            "SELECT task_id, status, progress, stage, created_at, updated_at, payload_json FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        task = _task_of_row(row)
        _TASKS[task.task_id] = task
        return task


def update_task(task_id: str, **fields) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        task.updated_at = time.time()
        _upsert_row(task)


def set_stage(task_id: str, stage: str, progress: int) -> None:
    update_task(task_id, stage=stage, progress=progress)


def reset_task_store_for_tests(db_path: str) -> None:
    """测试辅助：指向独立 DB 并强制重新初始化（清空内存缓存）。"""
    global _CONN, _INITIALIZED, _DB_PATH
    with _LOCK:
        _TASKS.clear()
        _CONN = None
        _INITIALIZED = False
        _DB_PATH = db_path
    init_task_store(db_path)
