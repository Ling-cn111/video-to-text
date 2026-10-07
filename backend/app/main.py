"""FastAPI 入口：CORS + 统一错误形状 + 路由挂载。"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import CORS_ORIGINS, CORS_ORIGIN_REGEX
from app.errors import AppException
from app.routers.capabilities import router as capabilities_router
from app.routers.parse import router as parse_router
from app.routers.summarize import router as summarize_router
from app.routers.transcribe import router as transcribe_router
from app.services import tasks as task_store
from app.services.rate_limit import RateLimitMiddleware

# 任务持久化初始化（SQLite）：加载历史任务，未完成的标记 interrupted
task_store.init_task_store()

app = FastAPI(
    title="video-to-text API",
    version="0.1.0",
    description="视频链接转文字后端（当前仅实现解析）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
# 请求限流（任务 H）：每 IP 每分钟 POST 上限（RATE_LIMIT_PER_MINUTE，0 禁用）
app.add_middleware(RateLimitMiddleware)

app.include_router(parse_router)
app.include_router(transcribe_router)
app.include_router(summarize_router)
app.include_router(capabilities_router)


@app.exception_handler(AppException)
async def handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(_: Request, exc: Exception) -> JSONResponse:
    # 兜底：任何未预期异常也保持前端约定的错误形状
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务开小差了，请稍后重试"}},
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
