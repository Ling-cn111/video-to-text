"""能力探测端点（任务 M2-P2-A）：只暴露布尔状态。

安全边界：响应仅含布尔值，**绝不返回 Key 本身、前缀、长度或任何派生信息**；
实现只做环境变量的「是否非空」判断与依赖「是否可导入」探测，
无任何 Key 值参与序列化。
"""
from functools import lru_cache

from fastapi import APIRouter

from app.config import CLOUD_ASR_API_KEY, DASHSCOPE_API_KEY, DEEPSEEK_API_KEY, LLM_PROVIDER
from app.schemas import CapabilitiesResponse

router = APIRouter()


@lru_cache(maxsize=1)
def _local_asr_available() -> bool:
    """本地 ASR 是否真的可用（决定前端要不要显示「本地 / 云端」选择器）。

    线上 FC 镜像按 requirements-cloud.txt 构建，**不含** faster-whisper 与 av，
    本地引擎 import 就会炸。前端若不知道这件事，会把用户引向一个必然失败的按钮。

    只在启动后第一次探测时 import（lru_cache 缓存），失败即判定不可用；
    这里不抛异常——探测本身不该让 /api/capabilities 500。
    """
    try:
        import faster_whisper  # noqa: F401
        import av  # noqa: F401
    except Exception:
        return False
    return True


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    llm_key = DEEPSEEK_API_KEY if LLM_PROVIDER == "deepseek" else DASHSCOPE_API_KEY
    return CapabilitiesResponse(
        cloudAsrConfigured=bool(CLOUD_ASR_API_KEY.strip()),
        summarizeConfigured=bool(llm_key.strip()),
        localAsrAvailable=_local_asr_available(),
    )
