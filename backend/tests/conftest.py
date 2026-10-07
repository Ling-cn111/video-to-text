"""backend 测试共享 fixture。"""
import pytest

from app.services import rate_limit as rate_limit_module


@pytest.fixture(autouse=True)
def _disable_global_rate_limit(monkeypatch):
    """单测默认禁用主应用的全局限流（限流行为在 test_rate_limit.py 内独立小容量验证）。"""
    monkeypatch.setattr(rate_limit_module._LIMITER, "per_minute", 0)
    yield
