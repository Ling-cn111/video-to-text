"""Pydantic 契约模型：与前端 src/lib/types.ts 保持一致（camelCase 字段）。"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ParseRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    """对应前端 types.ts 的 VideoInfo"""

    title: str
    cover: str
    duration: int
    platform: str
    videoId: str


class TranscriptItem(BaseModel):
    """对应前端 types.ts 的 TranscriptItem；time 为相对视频开头的秒数"""

    time: float
    text: str


class FileRef(BaseModel):
    """本地文件引用（M3）：objectKey 是后端 upload/init 签发的 OSS 对象键。

    为什么后端生成：预签名 PUT 只对签发时锁定的 key 有效，客户端拿不到
    「往任意位置写对象」的能力；transcribe-init 再按格式复核一次，Worker 侧
    第三道复核（不信任派发方）。
    """

    objectKey: str
    fileName: str
    size: int


class StartTranscribeRequest(BaseModel):
    """POST /api/transcribe 请求体。

    - kind 选择任务来源：url（默认，链接解析）| file（本地上传，M3）
    - kind=url：videoId 必填；url 缺省时由 videoId 按平台规则重建
    - kind=file：file 必填（upload/init 返回的引用）；videoId/video 可省，
      由后端按文件名合成（platform=local、cover 空、duration 由 Worker 转码后回填）
    - video 为解析阶段透传的元数据（供前端刷新恢复展示）
    - hotwords 为可选热词（专有名词列表，经 initial_prompt 引导 ASR 识别）
    - engine 为可选转写引擎手动选择：local（本地 faster-whisper）/ cloud（云端 OpenAI 兼容）。
      不传时由 routers/transcribe.py 的 _resolve_engine 按实际可用性自动挑
      （有云端 Key → cloud，否则本地可用 → local，都没有 → CLOUD_NOT_CONFIGURED）；
      传了就照办。网页端与桌面端因线上镜像不含本地 ASR，一律不传
    """

    videoId: str | None = None
    kind: Literal["url", "file"] = "url"
    url: str | None = None
    file: FileRef | None = None
    video: VideoInfo | None = None
    hotwords: list[str] | None = None
    engine: Literal["local", "cloud"] | None = None

    @model_validator(mode="after")
    def _check_required_by_kind(self) -> "StartTranscribeRequest":
        """按 kind 校验必填项，缺失走 pydantic 422（与旧契约一致）。

        kind=url 时 videoId 必填（M3 之前的旧契约：缺失 → 422，前端「链接无效」）；
        kind=file 时 file 必填（缺引用说明客户端上传流程没走完就提交了）。
        路由层还会做更细的存在性校验（对象是否真的在 OSS），这里只守形状。
        """
        if self.kind == "url" and not (self.videoId or "").strip():
            raise ValueError("videoId 为必填字段（kind=url）")
        if self.kind == "file" and self.file is None:
            raise ValueError("kind=file 时必须提供 file（objectKey/fileName/size）")
        return self


class StartTaskResponse(BaseModel):
    taskId: str
    status: str
    stage: str
    progress: int


class AppErrorBody(BaseModel):
    """所有非 2xx 响应统一为 { error: { code, message } }，与前端 ApiErrorBody 一致"""

    code: str
    message: str


class ErrorResponse(BaseModel):
    error: AppErrorBody


# ---- AI 总结（阶段三，任务 E）----

class ByokTestRequest(BaseModel):
    """POST /api/byok/test 请求体：测活只验「地址 + 模型 + Key」三元组（Key 走请求头）。"""

    llmBaseUrl: str = ""
    llmModel: str = ""


class SummarizeRequest(BaseModel):
    """POST /api/summarize 请求体（无状态，不关联任务）。

    transcript 为已完成的文字稿；title/duration 作 prompt 上下文。
    detail 控制总结的详细程度（brief / standard / detailed），缺省 standard
    与旧行为一致，老客户端不传该字段也能正常调用。

    llmBaseUrl / llmModel 是用户自填的接口地址与模型名（配合 `X-Vtt-Llm-Key`）：
    这三者合起来才描述得清「任意 OpenAI 兼容服务商」。旧实现只让用户填 Key，
    地址与模型钉死在 `LLM_PRESETS` 里，用户填了 OpenAI / Moonshot 的 Key，
    后端仍拿它去请求 DeepSeek —— 这就是「别家的 Key 用不了」的根因。

    为什么地址与模型放请求体、Key 却必须走请求头：Key 是秘密，请求体会被错误
    日志、`payload=` 打印等链路碰到；地址与模型不是秘密，放请求体才能用
    pydantic 校验并给出可读的错误。地址仍要过 SSRF 校验，见
    `services/byok.py` 的 `normalize_base_url`。
    """

    transcript: list[TranscriptItem]
    title: str = ""
    duration: int = 0
    detail: Literal["brief", "standard", "detailed"] = "standard"
    llmBaseUrl: str | None = None
    llmModel: str | None = None


class KeyPoint(BaseModel):
    """核心要点：time 为秒，必须来自原 transcript 的时间戳。"""

    time: float
    text: str


class ChapterNote(BaseModel):
    """章节笔记：字段名用 note，避免与顶层 summary 混淆。"""

    title: str
    timeStart: float
    timeEnd: float
    note: str


class SummaryResponse(BaseModel):
    summary: str
    keyPoints: list[KeyPoint] = Field(default_factory=list)
    chapters: list[ChapterNote] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    """能力探测（任务 M2-P2-A）：仅布尔状态，绝不含 Key 的任何信息。

    localAsrAvailable 是**依赖可用性**探测，不是配置探测：线上 FC 镜像按
    requirements-cloud.txt 构建，不含 faster-whisper，本地引擎根本跑不起来。
    前端据此决定是否显示「本地 / 云端」选择器。
    fileUploadConfigured 是**配置探测**：OSS 四项（bucket/endpoint/AK/SK）是否齐备，
    决定首页要不要显示「上传本地视频」入口（M3）。
    """

    cloudAsrConfigured: bool
    summarizeConfigured: bool
    localAsrAvailable: bool = False
    fileUploadConfigured: bool = False


# ---- 本地文件上传（M3）----

class UploadInitRequest(BaseModel):
    """POST /api/upload/init 请求体：只有元数据，**没有任何文件字节**。

    字节走「浏览器 → OSS 预签名 PUT」直传（FC HTTP 触发器同步请求体上限 32MB，
    文件过不了后端）；本接口只负责校验声明 + 签发上传凭证。
    """

    fileName: str
    size: int
    mimeType: str = ""


class UploadInitResponse(BaseModel):
    uploadUrl: str
    objectKey: str
    expiresIn: int


class TaskDetailResponse(BaseModel):
    """GET /api/transcribe/{taskId} 响应（前端 GetTaskResponse 的后端实现）"""

    taskId: str
    status: str
    stage: str
    progress: int
    video: VideoInfo | None = None
    transcript: list[TranscriptItem] = Field(default_factory=list)
    plainText: str | None = None
    error: AppErrorBody | None = None
    # 文字稿来源：subtitle_cc（B站 CC 字幕）/ subtitle_ai（B站 AI 字幕）/ asr（语音识别）
    transcriptSource: str | None = None
