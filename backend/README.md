# backend — FastAPI 视频解析服务

当前实现 `POST /api/parse`：用 yt-dlp 解析 B站视频链接，返回与前端契约完全一致的
`{ title, cover, duration, platform, videoId }`。转写 / 总结尚未实现（M2 起接入）。

## 本地运行

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows（macOS/Linux: source .venv/bin/activate）
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

自检：<http://localhost:8000/api/health>；接口文档：<http://localhost:8000/docs>

## 前端联调

```bash
# 仓库根目录
cp .env.example .env.local      # 修改 NEXT_PUBLIC_USE_MOCK=false（BACKEND_ORIGIN 默认即本地 8000）
pnpm dev                        # 等价于 npm run dev
```

粘贴 B站链接，前端进度页/结果页即显示真实标题、封面、时长
（转写与总结当前仍为内置 Mock，真实元数据会透传展示）。

前端请求走同源 `/backend-api/*`，由 Next.js rewrites 服务端代理到 `BACKEND_ORIGIN`，
浏览器不直接跨域访问后端；后端 CORS（本地 + Vercel 域）仍按契约保留。

## 测试

```bash
pytest                    # 全量（含 @pytest.mark.network 真实 B站链接集成测试）
pytest -m "not network"   # 跳过外网集成测试（CI 使用）
pytest -m network         # 只跑真实链接集成测试
```

## 结构

```
backend/app/
├── main.py          # FastAPI 入口：CORS、统一错误形状 { error: { code, message } }
├── config.py        # CORS_ORIGINS / CORS_ORIGIN_REGEX（env 可覆盖）
├── schemas.py       # Pydantic 契约（与 src/lib/types.ts 一致）
├── platforms.py     # 平台注册表（与前端 registry 对应；当前仅 bilibili）
├── url_guard.py     # URL 安全守卫：仅 http/https，拒绝 localhost/私有/保留地址
├── errors.py        # AppException（INVALID_URL 400 / UNSUPPORTED_PLATFORM 422 / …）
├── routers/parse.py # POST /api/parse
└── services/parser.py  # yt-dlp 封装（extract_info, download=False）
```

## 环境变量

见 `.env.example`。`CORS_ORIGINS` 默认放行本地前端；`CORS_ORIGIN_REGEX`
默认放行所有 `*.vercel.app`（预览与生产域）。
