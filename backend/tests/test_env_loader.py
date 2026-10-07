"""`.env.local` 自动加载器测试（M2-P2-A 暴露的根因修复）。

覆盖：文件值加载 / 已存在的环境变量优先（含显式 export 覆盖）/ 注释与空行 /
成对引号剥离 / 文件缺失静默。
"""
import os

import app.config as config_module


def test_env_file_loads_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text("VTT_TEST_NEW=from-file\n", encoding="utf-8")
    monkeypatch.delenv("VTT_TEST_NEW", raising=False)

    config_module._load_env_file(str(env_file))
    assert os.environ["VTT_TEST_NEW"] == "from-file"


def test_existing_env_wins(tmp_path, monkeypatch):
    """已显式导出的环境变量优先于文件值（部署场景覆盖本地文件）。"""
    env_file = tmp_path / ".env.local"
    env_file.write_text("VTT_TEST_PRIORITY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("VTT_TEST_PRIORITY", "from-shell")

    config_module._load_env_file(str(env_file))
    assert os.environ["VTT_TEST_PRIORITY"] == "from-shell"


def test_comments_blanks_and_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "# 注释行\n\nVTT_TEST_QUOTED=\"sk-quoted\"\nVTT_TEST_RAW=plain-value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VTT_TEST_QUOTED", raising=False)
    monkeypatch.delenv("VTT_TEST_RAW", raising=False)

    config_module._load_env_file(str(env_file))
    assert os.environ["VTT_TEST_QUOTED"] == "sk-quoted"  # 引号被剥离
    assert os.environ["VTT_TEST_RAW"] == "plain-value"


def test_missing_file_silent(tmp_path):
    config_module._load_env_file(str(tmp_path / "no-such-file"))  # 不抛异常即通过
