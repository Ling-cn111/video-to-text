"""AI 总结路由：POST /api/summarize（无状态，不关联任务注册表）。"""
from fastapi import APIRouter

from app.schemas import SummarizeRequest, SummaryResponse
from app.services.summarizer import summarize_transcript

router = APIRouter()


@router.post("/api/summarize", response_model=SummaryResponse)
def create_summary(payload: SummarizeRequest) -> SummaryResponse:
    summary = summarize_transcript(
        [item.model_dump() for item in payload.transcript],
        title=payload.title,
        duration=payload.duration,
    )
    return SummaryResponse(**summary)
