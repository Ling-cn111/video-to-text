"""用户自带 Key（BYOK）：请求级 Key 覆盖后端环境变量里的 Key。

## 为什么要有这个模块

站点自带的 Key 是**全站共用**的：谁打开页面都能用，额度被谁烧掉无法区分。
用户想「各用各的」时，唯一安全的做法是让他带自己的 Key 上来，而不是把站点的
Key 发给浏览器（那等于公开发布，任何人 F12 就能拿走盗刷）。

## 三条硬约束

1. **Key 只走请求头，绝不进请求体**。请求体会被错误日志、`payload=` 打印、
   请求体大小限制等一整套链路碰到；请求头不参与序列化，也不会被
   `readJsonBody` 之类读出来再写回日志。
2. **绝不回显**。响应体、错误信息、日志里都不出现 Key 本身，连前缀和长度都不给
   （与 `routers/capabilities.py` 同一条红线）。日志里只用 `<byok>` / `<server>`
   这种标记区分来源。
3. **服务地址走请求体，且必须过 SSRF 校验**。Key 是秘密、地址不是：地址放请求体
   才能用 pydantic 校验并给出可读的错误。放行用户自填地址是为了让 BYOK 真的
   「支持任意 OpenAI 兼容服务商」——旧实现把 `base_url` 钉死在 `LLM_PRESETS` 里，
   用户填了 OpenAI / Moonshot / 智谱的 Key，后端仍拿它去请求 DeepSeek，必然 401。
   但放行不等于放任：`normalize_base_url` 会拦掉环回 / 私有 / 保留地址
   （云厂商 metadata `169.254.169.254`、内网管理面等），否则浏览器就能借后端
   去访问它自己够不着的地方。

   **只对 AI 总结放行**：云端 ASR 的地址仍固定（`CLOUD_ASR_BASE_URL`），
   语音识别用的是站点的免费模型，界面不收集。

## 传给下游的方式

Key 需要跨函数层传到真正发请求的地方（`summarizer._chat` / `asr.CloudASREngine`），
而转写链路还要跨进程（API 函数 → Worker 函数）。所以分两条路：

- **同进程（总结、inline 转写）**：显式参数传递，不用 contextvar——显式参数在
  `asyncio.to_thread` 里不会丢，contextvar 在 `run_in_executor` 里要靠
  `contextvars.copy_context()` 才带得过去，容易漏。
- **跨进程（FC 异步转写）**：写进 `TranscribeJob` 载荷，随作业一起投递给 Worker。
  该载荷只经 HTTPS + `WORKER_TOKEN` 认证的内部调用传输，不落任务库（`tasks.py`
  的 `_payload_dict` 里没有这个字段，重启也不会被持久化）。
"""

import logging
from urllib.parse import urlparse

from app.errors import ByokEndpointInvalidError
from app.url_guard import is_blocked_host

logger = logging.getLogger(__name__)

# 请求头名（前端 src/lib/byok.ts 必须与此完全一致）
HEADER_LLM_KEY = "X-Vtt-Llm-Key"
HEADER_ASR_KEY = "X-Vtt-Asr-Key"

# 上限：真实 Key 都在 100 字符以内；放宽到 512 只为容忍将来厂商加长，
# 同时挡住「把整个文件塞进请求头」这种明显的误用（也防日志被撑爆）。
MAX_KEY_LENGTH = 512

# 用户自填接口地址的上限。OpenAI 兼容服务的 base_url 都很短
# （`https://api.deepseek.com/v1` 才 27 字符），留 512 足够宽松。
MAX_BASE_URL_LENGTH = 512

# 模型名上限。真实模型名最长的是 OpenRouter 那种带组织前缀的
# （`anthropic/claude-3.5-sonnet-20241022` 共 38 字符），128 绰绰有余。
MAX_MODEL_LENGTH = 128


def normalize_key(raw: str | None) -> str | None:
    """清洗请求头里的 Key：去空白，空串视为未提供。

    不做格式校验（`sk-` 前缀、长度区间）：各家 Key 格式不统一，猜错会把合法
    Key 拒掉。真正是否有效只能由上游服务回答，见 `is_auth_failure`。
    超长值直接丢弃并告警——这种请求不可能是正常用户发的。
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LENGTH:
        logger.warning("忽略超长 BYOK Key（%d 字符 > 上限 %d）", len(key), MAX_KEY_LENGTH)
        return None
    return key


def key_source(byok_key: str | None, server_key: str) -> str:
    """日志用的来源标记：只区分「用户带的」和「站点配的」，不输出任何 Key 内容。"""
    if byok_key:
        return "<byok>"
    return "<server>" if server_key else "<none>"


def is_auth_failure(status_code: int) -> bool:
    """上游返回的 HTTP 状态是否属于「Key 无效 / 无权限」。

    401 = 认证失败；403 = 认证通过但被拒（额度耗尽、模型未开通、欠费都可能落在
    这两个码上）。这两类必须与「服务端 500」区分开：前者要用户去改自己的 Key，
    后者是上游故障，让用户改 Key 只会白折腾。
    """
    return status_code in (401, 403)


def normalize_base_url(raw: str | None) -> str | None:
    """校验用户自填的 OpenAI 兼容接口地址；不合规抛 `ByokEndpointInvalidError`。

    为什么必须校验而不是直接用：这段字符串会原样拼进 `httpx.post` 的目标 URL。
    不校验就等于给浏览器开了一个「让后端替我请求任意地址」的接口（SSRF）——
    云厂商 metadata（`169.254.169.254`）、内网管理面、`file://` 之类都会被摸到。

    规则（与 `app/url_guard.py` 同一套判断，但错误码不同）：
    - 必须 http/https；空值视为未提供（返回 None，回落到站点配置）
    - 拒绝环回 / 私有 / 链路本地 / 保留地址，拒绝 `.local` / `.internal` 等内网域
    - 长度上限 `MAX_BASE_URL_LENGTH`
    - **不要求以 `/v1` 结尾**：有的服务商是 `/openai/v1`，有的直接用根路径
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > MAX_BASE_URL_LENGTH:
        raise ByokEndpointInvalidError(f"接口地址过长（超过 {MAX_BASE_URL_LENGTH} 字符）")

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ByokEndpointInvalidError("接口地址必须以 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise ByokEndpointInvalidError("接口地址缺少主机名，例如 https://api.deepseek.com/v1")
    if is_blocked_host(parsed.hostname):
        raise ByokEndpointInvalidError("接口地址不能指向本机或内网地址")

    # 去掉末尾斜杠：调用方统一用 f"{base_url}/chat/completions" 拼接，
    # 留着双斜杠有些网关会 404（`//chat/completions` 被当成不同路径）
    return value.rstrip("/")


def normalize_model(raw: str | None) -> str | None:
    """清洗用户自填的模型名：去空白，空串视为未提供。

    只做长度与空白检查，不猜命名规范：各家差异极大（`deepseek-chat`、
    `qwen-plus`、`moonshot-v1-8k`、`anthropic/claude-3.5-sonnet`），
    校验越严越容易把合法模型名拒掉。是否可用由服务商回答。
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > MAX_MODEL_LENGTH:
        raise ByokEndpointInvalidError(f"模型名过长（超过 {MAX_MODEL_LENGTH} 字符）")
    if any(ch.isspace() for ch in value):
        raise ByokEndpointInvalidError("模型名里不能有空格")
    return value
