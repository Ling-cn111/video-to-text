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
| `e2e/pr4-reading-and-export.spec.ts` | 回归 7-9：SRT/VTT 导出、目录搜索字号、总结详细度 |
| `e2e/pr5-errors-and-theme.spec.ts` | 回归 10-13：404 页、错误边界产物、robots/sitemap、安全响应头、深色模式 |
| `e2e/pr6-byok.spec.ts` | 回归 14-17：BYOK 面板存取与掩码、Key 走请求头不进请求体、错误归属、未配置提示的自助出路 |
| `e2e/pr7-no-key-and-engine.spec.ts` | 回归 18-21：没有可用 Key 时不发请求也不报红卡、引导态一键打开设置、填完 Key 立刻可用、转写模式选择器按 `localAsrAvailable` 显示、面板只收 LLM Key 且文案如实 |
| `e2e/pr8-custom-provider.spec.ts` | 回归 22-25：自定义服务商三项（Key/地址/模型）存取、预设填空、地址与模型真进请求体而 Key 仍只在请求头、只填地址不填模型被拦、只填地址不填 Key 被拦（站点 Key 不得外泄）、换服务商后总结缓存被清、SSRF 与末尾斜杠纯函数断言、裸 BV/av 号直接可用（含正文大小写原样透传） |
| `e2e/pr9-mobile-shell.spec.ts` | 回归 26-29b：导出函数必须是 async（不得退回 fire-and-forget）、桌面端下载行为未被改造破坏、用户取消分享不报错也不误报「已下载」、分享成功提示走分享文案；回归 28 是返回键决策的纯函数断言（非首页回退 / 无上一页回首页 / 首页二次确认退出，含 `pathname` 为 `null` 与空串的边界）；回归 29 / 29b 把 `--safe-area-inset-*` 手工注入 `<html>` 后断言 `body` 四边 padding、顶栏 `top` 与 fixed 横幅都让开了状态栏。**回归 27/27b/27c 在 `VTT_EXPECT_MODE=real` 下自动 skip**（它们依赖 Mock 任务号打开结果页，真实构建里那号是 404，与导出功能无关；28/29/29b 不依赖任务号，真实模式照跑） |
| `e2e/pr10-desktop-updater.spec.ts` | 回归 30-30g：版本比较必须按数值（`0.1.10 > 0.1.9`）、降级不算更新、解析失败不比当前新、只有桌面壳 + 生产构建才启用更新、进度总量未知返回 `null`、发布说明截断、端点与下载页同仓库；回归 30g 断言**网页构建里「检查更新」按钮渲染 0 个**。全部是纯函数 / 页面断言，不依赖 Mock 任务号，真实模式照跑 |
| `e2e/pr11-external-links-and-mobile-update.spec.ts` | 回归 31-31h：APK 直链解析、无 APK 时不编假链、畸形输入返 null、与桌面端共用 `compareVersions`、检查更新的成功 / 5xx / 网络异常三条路径（注入假 fetch，不依赖真实 GitHub）、端点与超时常量、网页构建不渲染移动端更新区块与桌面端更新入口 |
| `e2e/pr12-share-intent.spec.ts` | 回归 32-32f：整段分享文案能抽链接并归一成可提交输入（含裸 BV/av 号、认不出返 null）、抽链接不吞中文也不带尾随标点、`canonicalUrlFor` 把短链还原成 `bilibili.com` 且认不出形态返 null、`shouldIgnoreShare` 的 5 秒去重语义、**Android manifest 真的声明了 SEND + text/plain 且没有 BROWSABLE**、`MainActivity` 在 super 前注册插件、插件用 retain、网页构建不注册分享监听 |

**`test_byok.py`（后端）** 与上面的 BYOK 前端用例是配套的：前端只证明「头发出去了」，后端证明「收到后按请求级覆盖、被拒时报对错误码、不落库、不降级」。改任一侧都要跑另一侧。

### 2.1 关键陷阱：`NEXT_PUBLIC_USE_MOCK` 是构建期常量

Next.js 只在 `pnpm build` 时把 `NEXT_PUBLIC_*` 内联进产物，**改这个变量必须重新构建**，改完重启进程无效。显式设置的同名环境变量优先于 `.env.local`。

这会导致一个高频误判：

- 直接 `pnpm build` 会读取 `.env.local`。若其中是 `NEXT_PUBLIC_USE_MOCK=false`，构建出来的是**真实模式**。
- 而锁定用例全部按 **Mock 模式**编写。对真实模式构建跑全套会**因环境原因全红**（Mock taskId 在真实接口取不到数据；真实模式的健康检查走 `/backend-api/health`，测试里对 `/api/health` 的拦截不生效）——**这不是代码回归**。
- 唯一例外是 `pr9-mobile-shell.spec.ts` 里那三条要打开结果页的用例：它们用 `VTT_EXPECT_MODE=real` 判断并自动 skip，所以真实模式下跑全量只会看到 3 skipped，不会再有 3 failed。

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

> **另一个高频坑：端口 3000 被旧服务占用。** `playwright.config.ts` 的 `reuseExistingServer` 在非 CI 下为 true，本地若还留着一个 Mock 模式 `pnpm start`，真实模式用例会**打到那个旧服务上**，表现为「刚构建的真实产物却断言出 Mock 行为」。切换模式前先确认 3000 端口空闲：
>
> ```bash
> netstat -ano | grep ":3000.*LISTENING"     # 有输出说明被占用
> ```
>
> 这不是代码问题，重跑前杀掉旧进程即可。

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
| `node scripts/verify-shell.mjs [desktop\|mobile]` | 静态壳产物验收：把 `out/` 当站点起起来，用 Playwright 断言**打进安装包的那份字节**（BYOK 三项、预设、裸号、请求体无 `engine`、不注册 SW、桌面端有「检查更新」入口而移动端没有）。桌面端 18 项、移动端 16 项。跑前需 `pnpm build:desktop` 或 `pnpm build:mobile` |
| `node scripts/make-latest-json.mjs --version X.Y.Z` | 生成自动更新清单 `latest.json`：从 `src-tauri/target/release/bundle/` 找 `.sig` 与安装包，拼出 Tauri updater 要求的 `{version, notes, pub_date, platforms}`。**没有 `.sig` 时直接失败退出**——无签名的更新包客户端装不上，宁可发版失败 |
| `python scripts/gen-android-icons.py` | 从 `public/icons/icon-1024.png` 生成 Android 全套图标与启动图（27 个文件） |

PWA 验收需先 `pnpm build && pnpm start`；桌面端相关脚本见 [desktop-packaging.md](desktop-packaging.md)。

## 5. 提交前自查

| 改动范围 | 必跑 |
| --- | --- |
| 前端 UI / 逻辑 | `pnpm lint` + `pnpm build` + Mock 模式全量 E2E |
| 前端路由 / Mock 数据 / 平台宣称 / 导出 | 同上，另加真实模式 `-g "锁定 7\|平台宣称"`（记得同时传 `NEXT_PUBLIC_USE_MOCK=false`） |
| 改根布局 / 主题 / Service Worker | 同上，**并确认 `public/sw.js` 的 `CACHE_NAME` 已提升**（v3 → v4），否则老客户端长期跑旧外壳 |
| 改 `next.config.mjs`（响应头 / 重写） | `pnpm build` 后 `curl -sI http://localhost:3000/` 逐条核对头，E2E 回归 12 也会拦 |
| 后端 | 上面的 pytest 命令（应与 CI 同为全绿，0 failed） |
| 改限流（`rate_limit.py` / `main.py` 中间件顺序） | `pytest tests/test_rate_limit.py tests/test_rate_limit_redis.py`。**中间件顺序是命门**：`add_middleware` 后注册的包在外层，必须「先限流、后 CORS」，否则 429 绕过 CORS 处理，跨源前端只看到网络错误（`test_429_response_carries_cors_headers` 会拦） |
| 改 `s.yaml` | `pytest tests/test_deploy_manifest.py`——它会把 §5 的模板副本与真实 `s.yaml` 逐字段比对，改一处忘另一处就红 |
| 改 BYOK（`src/lib/byok.ts` / `services/byok.py` / 设置面板） | 前端 `pnpm exec playwright test e2e/pr6-byok.spec.ts e2e/pr7-no-key-and-engine.spec.ts e2e/pr8-custom-provider.spec.ts` + 后端 `pytest tests/test_byok.py tests/test_capabilities.py`。**两侧必须一起跑**：前端只证明「头发出去了」，后端证明「按请求级覆盖、被拒时报对错误码」。改请求头常量时前后端两个文件都要改，名字不一致不会报错、只会静默回落站点 Key |
| 改自定义服务商（`LLM_PROVIDER_PRESETS` / `normalize_base_url` / `_resolve_provider`） | 后端 `pytest tests/test_byok.py` + 前端 `pnpm exec playwright test e2e/pr8-custom-provider.spec.ts`。**四条不变量别碰**：①填了地址必须填模型（否则拿站点默认模型去问别家，只会得到 model not found）②填了地址必须填用户自己的 Key（否则站点 Key 会被发往用户指定的地址，`/api/summarize` 无鉴权等于公开泄露）③地址必须过 SSRF 校验**且解析 DNS 后复检**（只查字面量挡不住 `nip.io` 这类域名，这是放行用户输入的唯一安全前提，不能为了「支持自建 Ollama」放宽）④Key 只走请求头、地址与模型走请求体 |
| 改裸视频号识别（`src/lib/platform.ts` 的 `resolveBareVideoId` / `normalizeVideoInput`） | `pnpm exec playwright test e2e/pr8-custom-provider.spec.ts -g "回归 24"`。**BV 号正文大小写敏感**：`BV1xx411c7mD` 与 `BV1XX411C7MD` 是两个不同的号，任何 `toUpperCase()` / `toLowerCase()` 规范化正文的改动都会把用户送到不存在的视频。只允许规范 `BV` / `av` 前缀本身 |
| 改转写引擎选择 / 降级链（`_resolve_engine` / `local_engine_available` / `capabilities.localAsrAvailable`） | `pytest tests/test_capabilities.py tests/test_asr_cloud.py` + `pnpm exec playwright test e2e/pr7-no-key-and-engine.spec.ts`。**注意线上镜像不含 faster-whisper**：任何「默认走 local」的改动在线上都会变成 ModuleNotFoundError，必须由后端按能力挑 |
| 改导出 / 分享（`src/lib/export.ts` / `share-file.ts` / `export-menu.tsx`） | `pnpm exec playwright test e2e/pr4-reading-and-export.spec.ts e2e/pr9-mobile-shell.spec.ts`。**三条不变量别碰**：①`exportResult` / `exportSubtitles` 必须返回 `Promise<ShareOutcome>`（退回 fire-and-forget 后 WebView 里失败无任何提示）②用户取消分享返回 `cancelled` 且**不弹任何提示**③TXT/MD 零时间戳、SRT/VTT 必带时间轴，两类产物不得互相复用生成函数 |
| 改移动端壳（`capacitor.config.ts` / `scripts/build-shell.mjs` / `use-android-back.ts` / `runtime.ts` / `globals.css` 的安全区） | `pnpm build:mobile && node scripts/verify-shell.mjs mobile`，再 `npx cap sync android` + `cd android && ./gradlew --init-script gradle/init-mirrors.gradle assembleDebug`。**六个致命点**：①`NEXT_PUBLIC_USE_MOCK` 必须显式注入 false（缺省是 Mock）②`server.url` 绝不能配（配了资源又回到被墙的 vercel.app）③`androidScheme` 必须 https（改 http 会让剪贴板 API 静默失效）④`gradle --init-script gradle/init-mirrors.gradle` 不能漏（Capacitor 子工程的 `google()` 在最前，直连 dl.google.com 会卡十几分钟再失败）⑤返回键**禁止读 `history.state.idx`**（App Router 不写这个字段，v0.1.3 真机「按返回没反应」就是这么来的），决策走 `src/lib/back-navigation.ts` 的纯函数并由 E2E 回归 28 兜住 ⑥安全区**四边都要用**，且顶栏 sticky 的 `top` 与 fixed 元素（横幅 / toast）必须各自加 `--safe-top`——它们相对视口定位，不吃 `body` 的 padding |
| 改分享接收（`ShareIntentPlugin.java` / `AndroidManifest.xml` / `use-share-intent.ts` / `share-intent.ts` / `platform.ts` 的 `extractFirstUrl`） | `pnpm exec playwright test e2e/pr12-share-intent.spec.ts` + `pnpm build:mobile && node scripts/verify-shell.mjs mobile`，再 `npx cap sync android` + `assembleDebug` 并用 `aapt2 dump xmltree` 确认 SEND 意图进包。**五个致命点**：①**只声明 `SEND` + `text/plain`，不要声明 `https://*.bilibili.com` 的 BROWSABLE VIEW**——过不了 `autoVerify` 就会让用户每点一次 B站 链接都弹选择框 ②`registerPlugin` 必须在 `super.onCreate` **之前**，否则冷启动那次分享直接丢（热启动正常，极容易漏测）③`notifyListeners(..., true)` 的 retain 必须为 true（JS 监听器必然晚于原生派发），且**不要额外加主动拉取接口**，否则同一次分享提交两次 ④`extractFirstUrl` 不能用 `\S+` 宽松正则，会把分享文案里的中文一起吞掉 ⑤`submitUrl` 必须送 `canonicalUrlFor(video) ?? url`，短链会打掉后端字幕 / playurl 两条快路径 |
| 改自动更新（`src/lib/updater.ts` / `use-app-update.ts` / `update-button.tsx` / `tauri.conf.json` 的 updater / `make-latest-json.mjs` / `release-desktop.yml`） | `pnpm exec playwright test e2e/pr10-desktop-updater.spec.ts` + `pnpm build:desktop && node scripts/verify-shell.mjs desktop`。**五条不变量别碰**：①版本比较必须按数值拆三段，字符串比较会让 `0.1.10` 被当成旧版 ②解析失败一律「不比当前新」（宁可漏一次更新，不要提示用户装坏包）③只有桌面壳 + 生产构建 + Tauri 桥存在才启用（用 `IS_STATIC_SHELL` 会让 Android 壳去调不存在的命令）④自动检查失败必须静默，只有手动检查才显示错误 ⑤`make-latest-json.mjs` 缺 `.sig` 必须失败退出，不能发无签名的更新包 |
| 发版（推 `v*` tag） | 三处版本号一致：`package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml`（workflow 会在装依赖前就校验，不一致直接红）。发布说明放 `docs/releases/vX.Y.Z.md`（不放则用 GitHub 自动生成）。**签名私钥必须已离线备份**：丢失后已安装客户端永远收不到自动更新 |
| 文档 / 构建脚本 | 若动了 `next.config.mjs`、`scripts/`，跑一次 `pnpm build` |
