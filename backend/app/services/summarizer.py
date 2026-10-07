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
from app.errors import SummarizeFailedError, SummarizeNotConfiguredError, TranscriptTooLongError

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 100_000  # 无状态请求体长度上限（超出报 TRANSCRIPT_TOO_LONG）
MAX_CHUNK_CHARS = 6_000  # 单次 LLM 调用输入的文字稿字符上限（按条目边界切块）
MAX_RETRIES = 2  # JSON 解析失败的重试次数
MAX_KEY_POINTS = 5

_SYSTEM_PROMPT = (
    "你是视频文字稿总结助手。只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。"
    "JSON 字段：\n"
    '- "summary"：一句话概要（字符串，不超过 120 字）\n'
    '- "keyPoints"：3-5 条核心要点，每条 {"time": 秒数, "text": 要点内容}；'
    "time 必须从输入文字稿的时间戳中选择，禁止编造不存在的时刻\n"
    '- "chapters"：按内容逻辑分段，每段 {"title": 段落标题, "timeStart": 秒, "timeEnd": 秒, '
    '"note": 该段要点说明}；timeStart/timeEnd 必须落在文字稿时间范围内\n'
    "所有时间单位为秒。"
)


def _resolve_provider() -> tuple[str, str, str]:
    """按 LLM_PROVIDER 返回 (base_url, api_key, model)；未配 Key 报错。"""
    preset = LLM_PRESETS.get(LLM_PROVIDER)
    if preset is None:
        raise SummarizeFailedError(f"未知 LLM_PROVIDER：{LLM_PROVIDER}")
    api_key = DEEPSEEK_API_KEY if preset["key_env"] == "DEEPSEEK_API_KEY" else DASHSCOPE_API_KEY
    if not api_key:
        raise SummarizeNotConfiguredError()
    return preset["base_url"], api_key, LLM_MODEL or preset["default_model"]


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


def _snap_summary_times(payload: dict, sorted_times: list[float], duration: int) -> dict:
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
        payload["keyPoints"] = payload["keyPoints"][:MAX_KEY_POINTS]
    if not payload.get("chapters") and sorted_times:
        payload["chapters"] = [
            {"title": "全文", "timeStart": sorted_times[0], "timeEnd": last_time, "note": payload.get("summary", "")}
        ]
    return payload


def _chat(base_url: str, api_key: str, model: str, user_content: str) -> tuple[str, dict]:
    """单次 chat.completions 调用（json_object），返回 (content, usage)；记录成本。"""
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SummarizeFailedError(f"AI 总结服务返回错误（HTTP {exc.response.status_code}）") from exc
    except httpx.HTTPError as exc:
        raise SummarizeFailedError("AI 总结服务连接失败，请稍后重试") from exc

    payload = response.json()
    usage = payload.get("usage") or {}
    price = LLM_PRICE_TABLE.get(model)
    if price:
        cost = usage.get("prompt_tokens", 0) / 1e6 * price["input"] + usage.get("completion_tokens", 0) / 1e6 * price["output"]
        logger.info(
            "LLM 成本：%s 输入 %s tok + 输出 %s tok ≈ ¥%.4f",
            model, usage.get("prompt_tokens"), usage.get("completion_tokens"), cost,
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


def _chat_summary_with_retry(base_url: str, api_key: str, model: str, user_content: str) -> dict:
    """调用 + 解析，失败把错误反馈给模型重试（最多 MAX_RETRIES 次）。"""
    last_error: ValueError | None = None
    feedback = ""
    for attempt in range(MAX_RETRIES + 1):
        content, _ = _chat(base_url, api_key, model, user_content + feedback)
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


def summarize_transcript(transcript: list[dict], title: str, duration: int) -> dict:
    """业务入口：{transcript, title, duration} → Summary dict（含时间戳吸附）。"""
    total_chars = sum(len(str(item.get("text", ""))) for item in transcript)
    if total_chars > MAX_TRANSCRIPT_CHARS:
        raise TranscriptTooLongError()

    base_url, api_key, model = _resolve_provider()
    sorted_times = sorted({float(item.get("time") or 0) for item in transcript})

    chunks = _split_chunks(transcript)
    if len(chunks) == 1:
        user_content = (
            f"视频标题：{title or '（无标题）'}\n时长：{duration} 秒\n\n"
            f"文字稿（每行 [mm:ss] 文本）：\n{format_transcript(transcript)}"
        )
        payload = _chat_summary_with_retry(base_url, api_key, model, user_content)
    else:
        # 多块：逐块提取候选要点（时间戳即原 transcript 时间，无需重映射），再合并终稿
        block_notes: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            block_prompt = (
                f"这是长视频文字稿的第 {index}/{len(chunks)} 块（标题：{title or '（无标题）'}）。"
                "请只输出 JSON：{\"points\": [{\"time\": 秒, \"text\": 该块核心要点}], "
                "\"digest\": \"该块内容摘要（2-3 句）\"}。time 必须取自本块文字稿的时间戳。"
                f"\n\n文字稿：\n{format_transcript(chunk)}"
            )
            content, _ = _chat(base_url, api_key, model, block_prompt)
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
            "（keyPoints 精选 3-5 条，time 从候选要点的时间戳中选择；chapters 覆盖全片）：\n\n"
            + "\n\n".join(block_notes)
        )
        payload = _chat_summary_with_retry(base_url, api_key, model, merge_prompt)

    payload = _snap_summary_times(payload, sorted_times, duration)
    logger.info(
        "AI 总结完成：%d 字文字稿 / %d 块 / %d 条要点 / %d 章节",
        total_chars, len(chunks), len(payload.get("keyPoints") or []), len(payload.get("chapters") or []),
    )
    return payload
