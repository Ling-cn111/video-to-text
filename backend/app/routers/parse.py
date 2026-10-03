"""解析路由：POST /api/parse。

用同步 def（FastAPI 自动放线程池），避免 yt-dlp 阻塞事件循环。
"""
from fastapi import APIRouter

from app.schemas import ParseRequest, VideoInfo
from app.services.parser import parse_video_url

router = APIRouter()


@router.post("/api/parse", response_model=VideoInfo)
def parse_video(payload: ParseRequest) -> VideoInfo:
    return parse_video_url(payload.url)
