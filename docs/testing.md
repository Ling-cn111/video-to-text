# 测试指南

> 覆盖四层：前端静态检查 → 前端生产构建 → E2E 功能锁定 → 后端 pytest。
> CI 配置见 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)，本文是本地复现方式与踩坑说明。

## 1. 前端

```bash
pnpm lint        # ESLint
pnpm build       # 生产构建（含类型检查）
pnpm start       # 以生产模式启动，默认 3000 端口
```

`pnpm build` 失败时优先看两点：TypeScript 报错（`npx tsc --noEmit` 可单独跑）、以及 `.env.local` 里的环境变量是否触发了真实模式构建。

## 2. E2E（Playwright）

`e2e/locked-features.spec.ts` 是[前端功能锁定协议](frontend-features.md)的自动化实现。Playwright 的 `webServer` 会自动执行 `pnpm start`（端口 3000），本地已有服务在跑时会复用。

其余 spec 按「回归 N」编号，**不占用锁定编号**（锁定编号专属于协议文件）：

| 文件 | 覆盖 |
| --- | --- |
| `e2e/locked-features.spec.ts` | 锁定 1-11，协议条文的强制层 |
| `e2e/p0-regressions.spec.ts` | 回归 1-3：429 冷却、刷新不重复 summarize、engine 偏好持久化 |
| `e2e/platform-claims.spec.ts` | 平台宣称 1-4：首页文案 / 示例链接 / 文案函数 / `resolveVideoUrl` 与 `SUPPORTED_PLATFORMS` 一致 |
| `e2e/pr3-progress-and-history.spec.ts` | 回归 4-6：最近转写记录、进度页离开出口、剩余时间与轮询节奏纯函数 |

### 2.1 关键陷阱：`NEXT_PUBLIC_USE_MOCK` 是构建期常量

Next.js 只在 `pnpm build` 时把 `NEXT_PUBLIC_*` 内联进产物，**改这个变量必须重新构建**，改完重启进程无效。显式设置的同名环境变量优先于 `.env.local`。

这会导致一个高频误判：

- 直接 `pnpm build` 会读取 `.env.local`。若其中是 `NEXT_PUBLIC_USE_MOCK=false`，构建出来的是**真实模式**。
- 而锁定用例全部按 **Mock 模式**编写。对真实模式构建跑全套会**因环境原因全红**（Mock taskId 在真实接口取不到数据；真实模式的健康检查走 `/backend-api/health`，测试里对 `/api/health` 的拦截不生效）——**这不是代码回归**。

### 2.2 本地跑全量锁定用例（Mock 模式）

```powershell
# PowerShell
$env:NEXT_PUBLIC_USE_MOCK = "true"
pnpm build
pnpm exec playwright test
```

```bash
# bash / zsh
NEXT_PUBLIC_USE_MOCK=true pnpm build
pnpm exec playwright test
```

> 保险做法：先删掉 `.next` 再构建，避免残留产物用旧常量。

### 2.3 真实模式：跑锁定 7 与平台宣称

锁定 7 是「Mock 标识在真实模式不出现」，平台宣称组断言「真实模式只宣称 B站」。CI 第二阶段单独跑这两组：

```bash
NEXT_PUBLIC_USE_MOCK=false pnpm build
NEXT_PUBLIC_USE_MOCK=false VTT_EXPECT_MODE=real pnpm exec playwright test -g "锁定 7|平台宣称"
```

```powershell
# PowerShell 等价
$env:NEXT_PUBLIC_USE_MOCK = "false"
pnpm build
$env:VTT_EXPECT_MODE = "real"; pnpm exec playwright test -g "锁定 7|平台宣称"
```

> **必须把 `NEXT_PUBLIC_USE_MOCK` 同时传给测试进程**，只传 `VTT_EXPECT_MODE` 会红。原因：`e2e/platform-claims.spec.ts` 在 Node 里 `import` 了 `src/lib/platforms/registry.ts` 来算「本构建该宣称哪些平台」，而 `SUPPORTED_PLATFORMS` 是构建期常量。测试进程没拿到该变量时，它按 Mock 模式期望「B站 / 抖音」，页面却按真实模式只显示「B站」，两边打架。

### 2.4 CI 为什么不会遇到这个问题

CI 环境没有 `.env.local`、也不设该变量，默认构建即 Mock 模式。CI 的 e2e job 依次执行：

1. `pnpm build` → `pnpm playwright test`（Mock 模式，全部锁定项 + 全部回归项）
2. 带 `NEXT_PUBLIC_USE_MOCK=false` 重新 `pnpm build` → `NEXT_PUBLIC_USE_MOCK=false VTT_EXPECT_MODE=real pnpm playwright test -g "锁定 7"`（真实模式）
3. 同上环境再跑 `-g "平台宣称"`（真实模式平台宣称）

## 3. 后端（pytest）

```bash
cd backend
.\.venv\Scripts\python.exe -m pytest -m "not network and not separation and not asr_heavy" -q
```

这是与 CI 等价的命令。三类标记的含义（见 `backend/pytest.ini`）：

| 标记 | 跳过的内容 | 何时才跑 |
| --- | --- | --- |
| `network` | 真实访问外部视频站点的集成测试 | 需要外网时 |
| `separation` | 依赖 Demucs / torch（`requirements-separation.txt`） | 测人声分离时 |
| `asr_heavy` | 需下载大模型权重的本地 ASR 引擎（`requirements-asr.txt`） | 测 CER 基准时 |

本地全量运行直接 `pytest`（不加 `-m`）。

> CI 里有两处防「假绿」的硬性措施，本地复现时值得注意：
> - 必须装 `requirements-dev.txt`（含 `fakeredis`），否则 `TaskStore` / 限流的 Redis 用例会被 `importorskip` 整体跳过
> - CI 固定安装 FFmpeg 并在测试前断言可用，避免 `test_audio_memory.py` 的管道用例静默跳过

## 4. 专项验收脚本

| 脚本 | 用途 |
| --- | --- |
| `node scripts/verify-pwa.mjs` | PWA 21 项验收：manifest 字段、图标可访问、apple-touch-icon 180×180、theme-color、`viewport-fit=cover`、SW activated、缓存预置、刷新后仍生效 |
| `node scripts/check-msi-locale.mjs` | 直接读 MSI 数据库，确认产品名为中文 |
| `node scripts/generate-icons.mjs` | 重新生成 `public/icons/`（改图标源后运行） |

PWA 验收需先 `pnpm build && pnpm start`；桌面端相关脚本见 [desktop-packaging.md](desktop-packaging.md)。

## 5. 提交前自查

| 改动范围 | 必跑 |
| --- | --- |
| 前端 UI / 逻辑 | `pnpm lint` + `pnpm build` + Mock 模式全量 E2E |
| 前端路由 / Mock 数据 / 平台宣称 | 同上，另加真实模式 `-g "锁定 7\|平台宣称"`（记得同时传 `NEXT_PUBLIC_USE_MOCK=false`） |
| 后端 | 上面的 pytest 命令（应与 CI 同为全绿，0 failed） |
| 文档 / 构建脚本 | 若动了 `next.config.mjs`、`scripts/`，跑一次 `pnpm build` |
