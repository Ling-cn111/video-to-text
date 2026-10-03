"""平台注册表：与前端 src/lib/platforms/registry.ts 对应。

新增平台 = 在此追加条目（host 正则 + videoId 提取规则）。
"""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformMeta:
    id: str
    name: str
    host_patterns: tuple[re.Pattern[str], ...]
    share_host_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    video_id_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)


PLATFORM_REGISTRY: tuple[PlatformMeta, ...] = (
    PlatformMeta(
        id="bilibili",
        name="哔哩哔哩",
        host_patterns=(re.compile(r"^([\w-]+\.)*bilibili\.com$"),),
        share_host_patterns=(re.compile(r"^b23\.tv$"),),
        video_id_patterns=(re.compile(r"/video/(BV[\w]+)", re.IGNORECASE), re.compile(r"/video/(av\d+)", re.IGNORECASE)),
    ),
)


def detect_platform(hostname: str) -> PlatformMeta | None:
    """host 命中注册表则返回平台元数据；否则 None（不支持的平台）。"""
    host = (hostname or "").lower()
    for meta in PLATFORM_REGISTRY:
        if any(p.match(host) for p in meta.host_patterns) or any(
            p.match(host) for p in meta.share_host_patterns
        ):
            return meta
    return None


def extract_video_id(path: str, meta: PlatformMeta) -> str | None:
    """从 URL 路径提取 videoId；短链等取不到时返回 None（由 yt-dlp 结果兜底）。"""
    for pattern in meta.video_id_patterns:
        match = pattern.search(path or "")
        if match:
            return match.group(1)
    return None
