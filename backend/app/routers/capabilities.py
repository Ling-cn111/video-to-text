"""能力探测端点（任务 M2-P2-A）：只暴露布尔配置状态。

安全边界：响应仅含两个布尔值，**绝不返回 Key 本身、前缀、长度或任何派生信息**；
实现只做环境变量的「是否非空」判断，无任何 Key 值参与序列化。
"""
from fastapi import APIRouter

from app.config import CLOUD_ASR_API_KEY, DASHSCOPE_API_KEY, DEEPSEEK_API_KEY, LLM_PROVIDER
from app.schemas import CapabilitiesResponse

router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    llm_key = DEEPSEEK_API_KEY if LLM_PROVIDER == "deepseek" else DASHSCOPE_API_KEY
    return CapabilitiesResponse(
        cloudAsrConfigured=bool(CLOUD_ASR_API_KEY.strip()),
        summarizeConfigured=bool(llm_key.strip()),
    )
