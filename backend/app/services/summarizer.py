"""LLM 总结服务（阶段三，任务 E）：DeepSeek / 通义千问，OpenAI 兼容 chat.completions。

设计要点：
- 无状态：POST /api/summarize 直接收 {transcript, title, duration}，不关联任务注册表
- 时间戳三重保障：① 输入文字稿格式化为 "[mm:ss] text"；② prompt 明确要求 keyPoints.time
  必须取自文字稿时间戳、禁止编造；③ 后端二次校验——输出的 time 不在时间戳集合时**就近吸附**
  （并记日志），chapters 的 timeStart/timeEnd 同样吸附
- 结构化输出：response_format=json_object；解析失败或字段缺失重试（最多 MAX_RETRIES 次）
- chunking：文字稿超过 MAX_CHUNK_CHARS 按条目边界切块（条目不拆，保时间戳语义），
  逐块提取候选要点 → 合并调用产出终稿；单块直通一次调用
- 成本：每次调用记录 usage tokens 与估算成本（单价表 LLM_PRICE_TABLE，占位值待官网核对）→ logger
- 未配 Key：SummarizeNotConfiguredError（400，提交即报错不静默）
"""
import json
import logging

import httpx

from app.config import (
    DASHSCOPE_API_KEY,
    DEEPSEEK_API_KEY,
    LLM_MODEL,
    LLM_PRESETS,
    LLM_PRICE_TABLE,
    LLM_PROVIDER,
)
from app.errors import (
    ByokEndpointInvalidError,
    ByokKeyInvalidError,
    SummarizeFailedError,
    SummarizeNotConfiguredError,
    TranscriptTooLongError,
)
from app.services.byok import is_auth_failure, key_source

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 100_000  # 无状态请求体长度上限（超出报 TRANSCRIPT_TOO_LONG）
MAX_CHUNK_CHARS = 6_000  # 单次 LLM 调用输入的文字稿字符上限（按条目边界切块）
MAX_RETRIES = 2  # JSON 解析失败的重试次数
MAX_KEY_POINTS = 5

# ---- 总结详细程度（P1-11）----
# 为什么按「档位」而不是「字数 / 条数」连续参数：用户对总结的诉求只有三种
# ——「给我一句话」「够用就行」「我要能替代看视频」。连续参数既难解释也难验证，
# 三档足够，且每一档都能写进 prompt 并断言。
DETAIL_PRESETS = {
    "brief": {
        "summary": "不超过 60 字，只讲这一个视频最核心的结论",
        "keyPoints": "2-3 条",
        "chapters": "2-3 段，每段说明不超过 30 字",
        "maxKeyPoints": 3,
    },
    "standard": {
        "summary": "不超过 120 字",
        "keyPoints": "3-5 条",
        "chapters": "按内容逻辑分段，每段说明 1-2 句",
        "maxKeyPoints": 5,
    },
    "detailed": {
        "summary": "不超过 200 字，交代背景、结论与适用条件",
        "keyPoints": "5-7 条，每条讲清一个可独立成立的判断",
        "chapters": "分得细一些，每段说明 2-3 句，保留关键数据与例证",
        "maxKeyPoints": 7,
    },
}
DEFAULT_DETAIL = "standard"


def _detail_preset(detail: str | None) -> dict:
    """未知档位回落到 standard：前端旧版本 / 手工请求不该因拼错而失败。"""
    return DETAIL_PRESETS.get(detail or DEFAULT_DETAIL, DETAIL_PRESETS[DEFAULT_DETAIL])


def _system_prompt(detail: str | None) -> str:
    preset = _detail_preset(detail)
    return (
        "你是视频文字稿总结助手。只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。"
        "JSON 字段：\n"
        f'- "summary"：一句话概要（字符串，{preset["summary"]}）\n'
        f'- "keyPoints"：{preset["keyPoints"]}核心要点，每条 {{"time": 秒数, "text": 要点内容}}；'
        "time 必须从输入文字稿的时间戳中选择，禁止编造不存在的时刻\n"
        f'- "chapters"：{preset["chapters"]}，每段 {{"title": 段落标题, "timeStart": 秒, "timeEnd": 秒, '
        '"note": 该段要点说明}；timeStart/timeEnd 必须落在文字稿时间范围内\n'
        "所有时间单位为秒。"
    )


# 兼容旧调用方（测试 / 其他模块可能直接引用常量）：默认档位的 system prompt
_SYSTEM_PROMPT = _system_prompt(DEFAULT_DETAIL)


def _resolve_provider(
    byok_key: str | None = None,
    byok_base_url: str | None = None,
    byok_model: str | None = None,
) -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。

    三者各自独立回落，优先级从高到低：

    | 项 | 用户填了 | 用户没填 |
    | --- | --- | --- |
    | base_url | 用户填的（已过 SSRF 校验） | `LLM_PRESETS[LLM_PROVIDER].base_url` |
    | api_key | 用户填的（请求头） | 站点环境变量里的 Key |
    | model | 用户填的 | `LLM_MODEL` 或 `LLM_PRESETS[LLM_PROVIDER].default_model` |

    为什么允许三者独立回落、而不是「要么全填要么全不填」：最常见的用法是
    「站点配了 DeepSeek，我只想换成自己的 DeepSeek Key」——这时只需要 Key，
    地址和模型用站点的就对了。强行要求全填会把最简单的场景也变复杂。

    但有一个组合必须拒绝：**填了地址却没填模型**。站点默认模型（如
    `deepseek-flash`）在别家服务商那里不存在，发过去只会得到一句
    「model not found」，用户还得自己猜是哪里错了。这里提前拦住并说清楚。

    byok_key 为用户自带的 Key（来自请求头，见 services/byok.py）：给了就用它，
    站点没配 Key 也不算错——这正是 BYOK 的用途（站点不承担任何费用）。
    """
    preset = LLM_PRESETS.get(LLM_PROVIDER)
    if preset is None:
        raise SummarizeFailedError(f"未知 LLM_PROVIDER：{LLM_PROVIDER}")

    if byok_base_url and not byok_model:
        raise ByokEndpointInvalidError(
            "填写了自定义接口地址，还需要填写模型名（例如 deepseek-chat / gpt-4o-mini）"
        )

    base_url = byok_base_url or preset["base_url"]
    model = byok_model or LLM_MODEL or preset["default_model"]
    api_key = byok_key or (DEEPSEEK_API_KEY if preset["key_env"] == "DEEPSEEK_API_KEY" else DASHSCOPE_API_KEY)
    if not api_key:
        raise SummarizeNotConfiguredError()
    logger.info(
        "LLM 总结使用 %s Key（provider=%s，地址=%s，模型=%s）",
        key_source(byok_key, api_key),
        LLM_PROVIDER,
        "custom" if byok_base_url else "preset",
        model,
    )
    return base_url, api_key, model


def _format_timestamp(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def format_transcript(transcript: list[dict]) -> str:
    """文字稿 → "[mm:ss] text" 行（LLM 输入的时间戳格式）。"""
    return "\n".join(f"[{_format_timestamp(item['time'])}] {item['text']}" for item in transcript)


def _snap_to_nearest(time_value: float, sorted_times: list[float]) -> float:
    """time 不在时间戳集合时吸附到最近的时间戳（防 LLM 编造）。"""
    if not sorted_times:
        return 0.0
    return min(sorted_times, key=lambda t: abs(t - time_value))


def _snap_summary_times(
    payload: dict, sorted_times: list[float], duration: int, max_key_points: int = MAX_KEY_POINTS
) -> dict:
    """对 LLM 输出的 keyPoints.time 与 chapters.timeStart/timeEnd 做就近吸附。"""
    last_time = sorted_times[-1] if sorted_times else float(duration)
    for point in payload.get("keyPoints") or []:
        raw = float(point.get("time") or 0)
        snapped = _snap_to_nearest(raw, sorted_times)
        if snapped != raw:
            logger.info("keyPoint time 就近吸附 %.1f → %.1f", raw, snapped)
        point["time"] = snapped
    for chapter in payload.get("chapters") or []:
        for field in ("timeStart", "timeEnd"):
            raw = float(chapter.get(field) or 0)
            snapped = _snap_to_nearest(raw, sorted_times)
            if snapped != raw:
                logger.info("chapter %s 就近吸附 %.1f → %.1f", field, raw, snapped)
            chapter[field] = snapped
        if float(chapter["timeEnd"]) < float(chapter["timeStart"]):
            chapter["timeEnd"] = chapter["timeStart"]
    if payload.get("keyPoints") and sorted_times:
        payload["keyPoints"] = payload["keyPoints"][:max_key_points]
    if not payload.get("chapters") and sorted_times:
        payload["chapters"] = [
            {"title": "全文", "timeStart": sorted_times[0], "timeEnd": last_time, "note": payload.get("summary", "")}
        ]
    return payload


def _auth_error(status: int, from_user: bool):
    """401/403 的归属判断：用户自带的 Key 出问题，提示他改自己的；站点 Key 出问题，提示联系部署者。

    为什么必须分开：BYOK 上线后「Key 无效」有两种完全不同的责任方。若不区分，
    自带 Key 的用户会被告知「AI 总结未配置」并去找站点管理员，而管理员这边
    配置明明是好的——两边互相踢皮球。反之站点 Key 失效时也不能让用户去翻自己的设置。
    """
    if from_user:
        return ByokKeyInvalidError(
            f"你填写的 API Key 被服务商拒绝（HTTP {status}），请在右上角「API 设置」中检查或更换"
        )
    return SummarizeFailedError(
        f"站点配置的 AI 总结 Key 被服务商拒绝（HTTP {status}），请联系部署者更换"
    )


def _chat(
    base_url: str,
    api_key: str,
    model: str,
    user_content: str,
    detail: str | None = None,
    from_user: bool = False,
) -> tuple[str, dict]:
    """单次 chat.completions 调用（json_object），返回 (content, usage)；记录成本。

    from_user：本次用的是用户自带的 Key。401/403 时据此区分该由谁去修
    （用户改自己的 Key / 部署者换站点 Key），见 `_auth_error`。
    """
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _system_prompt(detail)},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if is_auth_failure(status):
            raise _auth_error(status, from_user) from exc
        raise SummarizeFailedError(f"AI 总结服务返回错误（HTTP {status}）") from exc
    except httpx.HTTPError as exc:
        raise SummarizeFailedError("AI 总结服务连接失败，请稍后重试") from exc

    payload = response.json()
    usage = payload.get("usage") or {}
    price = LLM_PRICE_TABLE.get(model)
    if price and usage:
        cost = usage.get("prompt_tokens", 0) / 1e6 * float(price["input"]) + usage.get("completion_tokens", 0) / 1e6 * float(price["output"])
        logger.info(
            "LLM 成本：%s 输入 %s tok + 输出 %s tok ≈ ¥%.4f（%s，cache-miss 空闲价）",
            model, usage.get("prompt_tokens"), usage.get("completion_tokens"), cost, price.get("currency"),
        )
    else:
        logger.info("LLM 用量：%s 输入 %s tok + 输出 %s tok（单价表缺该模型，未计成本）", model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SummarizeFailedError("AI 总结服务返回结构异常") from exc
    return content, usage


def _parse_payload(content: str) -> dict:
    """解析 LLM 的 JSON 输出并校验必需字段；不合规抛 ValueError（触发重试）。"""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("顶层不是对象")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise ValueError("缺少 summary")
    if not isinstance(data.get("keyPoints"), list) or not data["keyPoints"]:
        raise ValueError("缺少 keyPoints")
    if not isinstance(data.get("chapters"), list):
        raise ValueError("缺少 chapters")
    cleaned_points = [
        {"time": float(p.get("time") or 0), "text": str(p.get("text", "")).strip()}
        for p in data["keyPoints"]
        if isinstance(p, dict) and str(p.get("text", "")).strip()
    ]
    if not cleaned_points:
        raise ValueError("keyPoints 全部为空")
    cleaned_chapters = [
        {
            "title": str(c.get("title", "")).strip() or "未命名段落",
            "timeStart": float(c.get("timeStart") or 0),
            "timeEnd": float(c.get("timeEnd") or 0),
            "note": str(c.get("note", "")).strip(),
        }
        for c in data["chapters"]
        if isinstance(c, dict)
    ]
    return {"summary": data["summary"].strip(), "keyPoints": cleaned_points, "chapters": cleaned_chapters}


def _chat_summary_with_retry(
    base_url: str,
    api_key: str,
    model: str,
    user_content: str,
    detail: str | None = None,
    from_user: bool = False,
) -> dict:
    """调用 + 解析，失败把错误反馈给模型重试（最多 MAX_RETRIES 次）。"""
    last_error: ValueError | None = None
    feedback = ""
    for attempt in range(MAX_RETRIES + 1):
        content, _ = _chat(base_url, api_key, model, user_content + feedback, detail, from_user)
        try:
            return _parse_payload(content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            logger.warning("LLM 输出解析失败（第 %d 次）：%s", attempt + 1, exc)
            feedback = f"\n\n（你上一次的输出无法解析：{exc}。请严格只输出符合字段要求的 JSON 对象。）"
    raise SummarizeFailedError(f"AI 总结输出解析失败（重试 {MAX_RETRIES} 次后）：{last_error}")


def _split_chunks(transcript: list[dict]) -> list[list[dict]]:
    """按条目边界切块（条目不拆，保时间戳语义）。"""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for item in transcript:
        item_chars = len(str(item.get("text", ""))) + 12
        if current and current_chars + item_chars > MAX_CHUNK_CHARS:
            chunks.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


def summarize_transcript(
    transcript: list[dict],
    title: str,
    duration: int,
    detail: str | None = None,
    byok_key: str | None = None,
    byok_base_url: str | None = None,
    byok_model: str | None = None,
) -> dict:
    """业务入口：{transcript, title, duration, detail, byok_*} → Summary dict（含时间戳吸附）。

    byok_key 为用户自带的 Key（请求头，见 services/byok.py）：非空时优先于站点配置，
    站点没配 Key 也能正常总结。byok_base_url / byok_model 是同一份用户配置里的
    接口地址与模型名，三者一起描述「用哪个服务商、哪个模型、哪把 Key」。
    """
    total_chars = sum(len(str(item.get("text", ""))) for item in transcript)
    if total_chars > MAX_TRANSCRIPT_CHARS:
        raise TranscriptTooLongError()

    preset = _detail_preset(detail)
    base_url, api_key, model = _resolve_provider(byok_key, byok_base_url, byok_model)
    from_user = bool(byok_key)
    sorted_times = sorted({float(item.get("time") or 0) for item in transcript})

    chunks = _split_chunks(transcript)
    if len(chunks) == 1:
        user_content = (
            f"视频标题：{title or '（无标题）'}\n时长：{duration} 秒\n\n"
            f"文字稿（每行 [mm:ss] 文本）：\n{format_transcript(transcript)}"
        )
        payload = _chat_summary_with_retry(base_url, api_key, model, user_content, detail, from_user)
    else:
        # 多块：逐块提取候选要点（时间戳即原 transcript 时间，无需重映射），再合并终稿
        block_notes: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            block_prompt = (
                f"这是长视频文字稿的第 {index}/{len(chunks)} 块（标题：{title or '（无标题）'}）。"
                '请只输出 JSON：{"points": [{"time": 秒, "text": 该块核心要点}], '
                '"digest": "该块内容摘要（2-3 句）"}。time 必须取自本块文字稿的时间戳。'
                f"\n\n文字稿：\n{format_transcript(chunk)}"
            )
            content, _ = _chat(base_url, api_key, model, block_prompt, detail, from_user)
            try:
                block = json.loads(content)
            except json.JSONDecodeError:
                block = {}
            points = block.get("points") or []
            block_notes.append(
                f"[块 {index}] {block.get('digest', '')}\n候选要点：" + "；".join(
                    f"({_format_timestamp(float(p.get('time') or 0))}) {p.get('text', '')}" for p in points
                )
            )
        merge_prompt = (
            f"视频标题：{title or '（无标题）'}\n时长：{duration} 秒\n"
            "以下是对长视频逐块总结的要点与摘要，请合并产出最终总结"
            f"（keyPoints 精选 {preset['keyPoints']}，time 从候选要点的时间戳中选择；"
            "chapters 覆盖全片）：\n\n"
            + "\n\n".join(block_notes)
        )
        payload = _chat_summary_with_retry(base_url, api_key, model, merge_prompt, detail, from_user)

    payload = _snap_summary_times(payload, sorted_times, duration, preset["maxKeyPoints"])
    logger.info(
        "AI 总结完成：%d 字文字稿 / %d 块 / %d 条要点 / %d 章节 / 详细度 %s",
        total_chars,
        len(chunks),
        len(payload.get("keyPoints") or []),
        len(payload.get("chapters") or []),
        detail or DEFAULT_DETAIL,
    )
    return payload
