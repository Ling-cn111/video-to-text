"""任务持久化（SQLite）测试：持久化往返、重启恢复 interrupted、缓存回源。"""
import pytest

from app.schemas import VideoInfo
from app.services import tasks as tasks_module
from app.services.tasks import (
    create_task,
    get_task,
    reset_task_store_for_tests,
    set_stage,
    update_task,
)

VIDEO = VideoInfo(title="测试视频", cover="https://i1.hdslb.com/c.jpg", duration=135, platform="bilibili", videoId="BV1GJ411x7h7")


@pytest.fixture()
def task_db(tmp_path, monkeypatch):
    """独立 SQLite 实例（每测试一个文件），结束后还原内存状态。"""
    db = str(tmp_path / "tasks.db")
    reset_task_store_for_tests(db)
    yield db
    reset_task_store_for_tests(str(tmp_path / "tasks-reload.db"))


def test_create_and_get_roundtrip(task_db):
    task_id = create_task(VIDEO, engine="cloud")
    task = get_task(task_id)
    assert task is not None
    assert task.status == "processing"
    assert task.engine == "cloud"
    assert task.video is not None and task.video.title == "测试视频"


def test_update_persists_to_db(task_db):
    task_id = create_task(VIDEO)
    update_task(
        task_id,
        status="completed",
        progress=100,
        transcript_source="subtitle_ai",
        plain_text="全文",
    )
    # 直接查 DB（绕过内存缓存）：新连接读到同一行
    import sqlite3

    conn = sqlite3.connect(task_db)
    row = conn.execute("SELECT status, progress, payload_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    conn.close()
    assert row[0] == "completed" and row[1] == 100
    assert "subtitle_ai" in row[2]


def test_get_task_falls_back_to_db_after_cache_loss(task_db):
    """服务重启场景：内存缓存清空后按 taskId 直访仍能从 DB 恢复任务。"""
    task_id = create_task(VIDEO, engine="local")
    update_task(task_id, status="completed", progress=100)
    # 模拟重启：清空内存（保留同一 DB）
    tasks_module._STORE._tasks.clear()
    task = get_task(task_id)
    assert task is not None and task.status == "completed"
    assert task.engine == "local"
    assert [i.text for i in task.transcript] == []  # 空文字稿往返无损


def test_startup_marks_processing_tasks_interrupted(task_db):
    """启动加载：DB 中 status=processing 的历史任务被标记 interrupted。"""
    task_id = create_task(VIDEO)
    set_stage(task_id, "asr", 42)
    done_id = create_task(VIDEO)
    update_task(done_id, status="completed")

    # 模拟进程重启：同一 DB 重新 init
    reset_task_store_for_tests(task_db)

    assert get_task(task_id).status == "interrupted"
    assert get_task(done_id).status == "completed"  # 已完成不受影响
    # DB 中也已落盘 interrupted（重启后再次 init 不回弹）
    assert get_task(task_id).status == "interrupted"


def test_init_task_store_without_recovery_keeps_processing(task_db):
    """Worker 启动语义：init_task_store(recover_interrupted=False) 不改写任务状态。

    Serverless 每次冷启动都会执行启动初始化；若沿用「全量打 interrupted」，
    会把其他实例正在跑的任务全部误杀（S2-P0）。
    """
    task_id = create_task(VIDEO)

    tasks_module.init_task_store(task_db, recover_interrupted=False)

    assert get_task(task_id).status == "processing"


def test_init_task_store_honours_init_mode_env(task_db, monkeypatch):
    """TASK_INIT_MODE=none 时，即使不显式传参也不做恢复改写。"""
    task_id = create_task(VIDEO)
    monkeypatch.setattr(tasks_module, "TASK_INIT_MODE", "none")

    tasks_module.init_task_store(task_db)

    assert get_task(task_id).status == "processing"


def test_get_unknown_task_returns_none(task_db):
    assert get_task("no-such-task") is None
