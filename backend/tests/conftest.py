"""backend 测试公共 fixture。"""
import pytest

from app.services import rate_limit as rate_limit_module


@pytest.fixture(autouse=True)
def _disable_global_rate_limit(monkeypatch):
    """测试默认关闭全局限流（含每客户端与全站两级）；只有 test_rate_limit*.py 内部小窗口验证时打开。"""
    monkeypatch.setattr(rate_limit_module._LIMITER, "per_minute", 0)
    monkeypatch.setattr(rate_limit_module._GLOBAL_LIMITER, "per_minute", 0)
    yield
