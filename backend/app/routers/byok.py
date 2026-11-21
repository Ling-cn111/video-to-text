"""BYOK 测活路由：POST /api/byok/test —— 用一次极短真实请求验证「地址 + 模型 + Key」。

信任模型与 /api/summarize 完全相同：Key 只从请求头读取，随本次调用转发给用户
指定的服务商，**不落任何存储、不写日志**；区别仅在于调用由用户在面板里主动
发起（测活），而非生成总结——与「不做 /models 拉取」的锁定语义不冲突（那是
禁止后端代持 Key 拉取模型列表，见 docs/frontend-features.md §14）。

响应约定：校验类失败（缺字段 / SSRF）走标准 {error:{code,message}} 形状；
服务商侧结果一律 HTTP 200 + {ok, ...}，前端直接展示 message。
限流：POST 自动计入 RateLimitMiddleware。
"""
from fastapi import APIRouter, Header

from app.errors import ByokEndpointInvalidError, SummarizeNotConfiguredError
from app.schemas import ByokTestRequest
from app.services.byok import HEADER_LLM_KEY, normalize_base_url, normalize_key, normalize_model
from app.services.summarizer import test_provider

router = APIRouter()


@router.post("/api/byok/test")
def test_byok(
    payload: ByokTestRequest,
    x_vtt_llm_key: str | None = Header(default=None, alias=HEADER_LLM_KEY),
) -> dict:
    api_key = normalize_key(x_vtt_llm_key)
    base_url = (payload.llmBaseUrl or "").strip()
    model = (payload.llmModel or "").strip()
    if not base_url:
        raise ByokEndpointInvalidError("测试前请先填写接口地址")
    if not model:
        raise ByokEndpointInvalidError("测试前请先填写模型名")
    if not api_key:
        raise SummarizeNotConfiguredError("测试前请先填写 API Key")

    # SSRF + DNS 复检与 summarize 完全同源，绝不放宽（失败抛 BYOK_ENDPOINT_INVALID 400）
    normalized_url = normalize_base_url(base_url)
    normalized_model = normalize_model(model)
    return test_provider(normalized_url, normalized_model, api_key)
