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
3. **服务地址不接受用户输入**。BYOK 只覆盖 Key，`base_url` 仍然来自
   `config.py` 的 `LLM_PRESETS` / `CLOUD_ASR_BASE_URL`。否则浏览器就能让后端
   去请求任意地址（SSRF），也能把用户的 Key 骗到攻击者的服务器上。

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

logger = logging.getLogger(__name__)

# 请求头名（前端 src/lib/byok.ts 必须与此完全一致）
HEADER_LLM_KEY = "X-Vtt-Llm-Key"
HEADER_ASR_KEY = "X-Vtt-Asr-Key"

# 上限：真实 Key 都在 100 字符以内；放宽到 512 只为容忍将来厂商加长，
# 同时挡住「把整个文件塞进请求头」这种明显的误用（也防日志被撑爆）。
MAX_KEY_LENGTH = 512


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
