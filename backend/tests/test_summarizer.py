"""LLM 总结服务测试（阶段三，任务 E）。

- 无标记：全 mock（httpx 打桩），覆盖 provider/未配 Key/解析重试/时间戳吸附/chunking/长度上限/HTTP 层
- @pytest.mark.network：真实调用 DeepSeek（CI 跳过，本地配 Key 后跑）
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.summarizer as summarizer_module
from app.main import app
from app.services import tasks as tasks_module  # noqa: F401  （确保任务模块可导入）
from app.services.summarizer import (
    _parse_payload,
    _snap_summary_times,
    _split_chunks,
    format_transcript,
    summarize_transcript,
)

client = TestClient(app)

TRANSCRIPT = [
    {"time": 0.0, "text": "大家好，今天讲语音转文字。"},
    {"time": 30.0, "text": "先讲原理：端到端模型与注意力机制。"},
    {"time": 92.0, "text": "接下来是五款工具横评。"},
    {"time": 380.0, "text": "最后是选购建议与隐私提醒。"},
]

VALID_LLM_PAYLOAD = {
    "summary": "本期视频系统讲解了语音转文字的原理、工具横评与选购建议。",
    "keyPoints": [
        {"time": 0.0, "text": "语音转文字已高度可用，差距在细节能力。"},
        {"time": 92.0, "text": "五款工具横评覆盖开源、云服务与网页方案。"},
        {"time": 380.0, "text": "选购建议：按场景选型，敏感音频注意隐私。"},
    ],
    "chapters": [
        {"title": "开场", "timeStart": 0.0, "timeEnd": 30.0, "note": "引入主题。"},
        {"title": "横评与建议", "timeStart": 92.0, "timeEnd": 380.0, "note": "五款工具对比与选购建议。"},
    ],
}


def _mock_chat(monkeypatch, payloads: list[str], usages: list[dict] | None = None):
    """替换 summarizer._chat：按顺序返回预设 content，记录调用次数；并注入假 Key 通过预检。"""
    calls = {"n": 0}

    def fake_chat(base_url, api_key, model, user_content):
        index = min(calls["n"], len(payloads) - 1)
        usage_list = usages or [{"prompt_tokens": 100, "completion_tokens": 50}]
        usage = usage_list[min(index, len(usage_list) - 1)]
        calls["n"] += 1
        return payloads[index], usage

    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(summarizer_module, "_chat", fake_chat)
    return calls


# ---- provider / 配置 ----

def test_missing_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(summarizer_module, "DASHSCOPE_API_KEY", "")
    with pytest.raises(Exception, match="AI 总结未配置"):
        summarize_transcript(TRANSCRIPT, "标题", 400)


def test_qwen_provider_uses_dashscope_key(monkeypatch):
    monkeypatch.setattr(summarizer_module, "LLM_PROVIDER", "qwen")
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(summarizer_module, "DASHSCOPE_API_KEY", "qwen-key")
    captured = {}

    def fake_chat(base_url, api_key, model, user_content):
        captured.update({"base_url": base_url, "api_key": api_key})
        return json.dumps(VALID_LLM_PAYLOAD, ensure_ascii=False), {"prompt_tokens": 10, "completion_tokens": 10}

    monkeypatch.setattr(summarizer_module, "_chat", fake_chat)
    summarize_transcript(TRANSCRIPT, "标题", 400)
    assert "dashscope.aliyuncs.com" in captured["base_url"]
    assert captured["api_key"] == "qwen-key"


# ---- 输入格式化 / 解析 ----

def test_format_transcript_timestamps():
    formatted = format_transcript([{"time": 92.0, "text": "横评环节"}])
    assert formatted == "[01:32] 横评环节"


def test_parse_payload_cleans_entries():
    payload = _parse_payload(json.dumps({
        "summary": "概要",
        "keyPoints": [{"time": "12", "text": "要点一"}, {"time": None, "text": ""}, "垃圾条目"],
        "chapters": [{"title": "", "timeStart": 0, "timeEnd": 10, "note": "n"}],
    }))
    assert payload["keyPoints"] == [{"time": 12.0, "text": "要点一"}]
    assert payload["chapters"][0]["title"] == "未命名段落"


def test_parse_payload_rejects_missing_fields():
    with pytest.raises(ValueError, match="缺少 keyPoints"):
        _parse_payload(json.dumps({"summary": "只有概要", "chapters": []}))


# ---- 时间戳三重保障：编造时间戳就近吸附 ----

def test_fabricated_timestamp_snapped_to_nearest(monkeypatch):
    """LLM 编造的 time（不在 transcript 时间戳集合）→ 就近吸附到真实时间戳。"""
    _mock_chat(monkeypatch, [json.dumps({
        "summary": "概要",
        "keyPoints": [{"time": 45.0, "text": "编造时刻 45s"}, {"time": 379.2, "text": "接近 380 的编造"}],
        "chapters": [{"title": "段", "timeStart": 10.0, "timeEnd": 500.0, "note": "n"}],
    }, ensure_ascii=False)])
    payload = summarize_transcript(TRANSCRIPT, "标题", 400)
    # transcript 时间集合 {0, 30, 92, 380}：45 → 30，379.2 → 380
    times = [p["time"] for p in payload["keyPoints"]]
    assert times == [30.0, 380.0]
    assert payload["chapters"][0]["timeEnd"] == 380.0  # 500 吸附到最晚时间戳 380


def test_snap_summary_times_handles_empty_chapters():
    sorted_times = [0.0, 92.0, 380.0]
    payload = _snap_summary_times({"summary": "s", "keyPoints": [{"time": 92.0, "text": "x"}], "chapters": []}, sorted_times, 400)
    assert payload["chapters"][0]["timeStart"] == 0.0
    assert payload["chapters"][0]["timeEnd"] == 380.0


# ---- 重试 ----

def test_retry_after_invalid_json(monkeypatch):
    calls = _mock_chat(
        monkeypatch,
        ["这不是JSON", json.dumps(VALID_LLM_PAYLOAD, ensure_ascii=False)],
    )
    payload = summarize_transcript(TRANSCRIPT, "标题", 400)
    assert calls["n"] == 2
    assert payload["summary"].startswith("本期视频")


def test_retry_exhausted_raises_friendly_error(monkeypatch):
    _mock_chat(monkeypatch, ["仍然不是JSON", "还不是JSON", "依旧不是JSON"])
    with pytest.raises(Exception, match="AI 总结输出解析失败"):
        summarize_transcript(TRANSCRIPT, "标题", 400)


# ---- 长度上限与 chunking ----

def test_transcript_over_100k_chars_rejected():
    huge = [{"time": float(i), "text": "字" * 200} for i in range(600)]  # 12 万字
    with pytest.raises(Exception, match="文字稿过长"):
        summarize_transcript(huge, "标题", 7200)


def test_split_chunks_respects_entry_boundaries():
    """切块不拆条目：块内条目完整、每块不超上限（除非单条超限）。"""
    transcript = [{"time": float(i * 10), "text": "字" * 900} for i in range(30)]  # 共 ~2.7 万字
    chunks = _split_chunks(transcript)
    assert len(chunks) >= 4
    flat = [item for chunk in chunks for item in chunk]
    assert flat == transcript  # 条目无损、顺序不变
    assert all(sum(len(str(i["text"])) + 12 for i in chunk) <= 6000 + 912 for chunk in chunks)


def test_long_transcript_multi_chunk_merge_keeps_original_timestamps(monkeypatch):
    """30 分钟级长文（多块）：逐块摘要 → 合并，keyPoints 时间戳仍指向原 transcript。"""
    long_transcript = [
        {"time": float(i * 12), "text": f"第{i}段：讲解知识点{i}，包含结论{i}。"} for i in range(600)
    ]  # ~150 分钟跨度 / 约 2.4 万字 → 多块
    assert len(_split_chunks(long_transcript)) > 1

    merged = {
        "summary": "长视频综合概要。",
        "keyPoints": [{"time": float(i * 12), "text": f"要点{i}"} for i in (10, 200, 400)],
        "chapters": [
            {"title": "前段", "timeStart": 0.0, "timeEnd": 2400.0, "note": "n1"},
            {"title": "后段", "timeStart": 2400.0, "timeEnd": 7188.0, "note": "n2"},
        ],
    }
    calls = _mock_chat(monkeypatch, [json.dumps(merged, ensure_ascii=False)])
    payload = summarize_transcript(long_transcript, "30分钟视频", 7200)

    assert calls["n"] >= 2  # 多块：至少逐块 + 合并
    transcript_times = {item["time"] for item in long_transcript}
    assert all(p["time"] in transcript_times for p in payload["keyPoints"])


# ---- HTTP 层 ----

def test_http_summarize_rejected_without_key(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(summarizer_module, "DASHSCOPE_API_KEY", "")
    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "t", "duration": 400})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SUMMARIZE_NOT_CONFIGURED"


def test_http_summarize_rejects_overlong_transcript(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "k")
    huge = [{"time": float(i), "text": "字" * 200} for i in range(600)]
    response = client.post("/api/summarize", json={"transcript": huge, "title": "t", "duration": 7200})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "TRANSCRIPT_TOO_LONG"


def test_http_summarize_success(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "test-key")
    _mock_chat(monkeypatch, [json.dumps(VALID_LLM_PAYLOAD, ensure_ascii=False)])
    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "t", "duration": 400})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"].startswith("本期视频")
    assert {p["time"] for p in body["keyPoints"]} <= {0.0, 30.0, 92.0, 380.0}
    assert {"title", "timeStart", "timeEnd", "note"} <= set(body["chapters"][0].keys())


# ---- 真实集成（CI 跳过；需 backend/.env.local 配 DeepSeek Key）----

@pytest.mark.network
def test_real_deepseek_summary():
    if not summarizer_module.DEEPSEEK_API_KEY:
        pytest.skip("需在 backend/.env.local 配置 DEEPSEEK_API_KEY 后本地运行")
    result = summarize_transcript(TRANSCRIPT, "语音转文字入门", 400)
    assert result["summary"]
    assert 3 <= len(result["keyPoints"]) <= 5
    transcript_times = {item["time"] for item in TRANSCRIPT}
    assert all(p["time"] in transcript_times for p in result["keyPoints"])
