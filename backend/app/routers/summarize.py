"""AI 总结路由：POST /api/summarize（无状态，不关联任务注册表）。

支持用户自带配置（BYOK）：`X-Vtt-Llm-Key` 请求头非空时优先于站点配置的 Key，
请求体的 `llmBaseUrl` / `llmModel` 覆盖站点预设的接口地址与模型名。
Key 只从请求头读，绝不进请求体；地址与模型走请求体（不是秘密，且要过 pydantic
与 SSRF 校验）。详见 services/byok.py 与 services/summarizer.py 的 _resolve_provider。
"""
from fastapi import APIRouter, Header

from app.schemas import SummarizeRequest, SummaryResponse
from app.services.byok import HEADER_LLM_KEY, normalize_base_url, normalize_key, normalize_model
from app.services.summarizer import summarize_transcript

router = APIRouter()


@router.post("/api/summarize", response_model=SummaryResponse)
def create_summary(
    payload: SummarizeRequest,
    x_vtt_llm_key: str | None = Header(default=None, alias=HEADER_LLM_KEY),
) -> SummaryResponse:
    summary = summarize_transcript(
        [item.model_dump() for item in payload.transcript],
        title=payload.title,
        duration=payload.duration,
        detail=payload.detail,
        byok_key=normalize_key(x_vtt_llm_key),
        byok_base_url=normalize_base_url(payload.llmBaseUrl),
        byok_model=normalize_model(payload.llmModel),
    )
    return SummaryResponse(**summary)
