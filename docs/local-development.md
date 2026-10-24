# 本地开发与日常运行

> README 只保留最短路径，本文是全部细节：Windows 一键脚本、系统托盘、手动启动、故障排查。

## 1. 环境要求

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Node.js | 18.17+（CI 用 20） | 前端（Next.js 14），见 `package.json` 的 `engines` |
| pnpm | 12.5.1 | 由 `packageManager` 字段锁定，`corepack enable` 可自动对齐 |
| Python | 3.11+ | 后端（FastAPI） |
| FFmpeg | 可选 | 未安装时自动降级到 PyAV 解码 |

## 2. Windows 一键脚本

### 2.1 首次配置：`setup.bat`

双击运行，自动完成：

1. 检查 Node.js 与 pnpm（**pnpm 需预先安装**：`npm install -g pnpm`，脚本检测不到会直接报错退出）
2. 创建 `backend/.venv` 虚拟环境
3. 安装后端依赖（`pip install -r backend/requirements.txt`）与前端依赖（`pnpm install`）
4. 从模板复制配置：`backend/.env.example → backend/.env.local`、`.env.example → .env.local`

> **已存在的配置文件不会被覆盖**，重复运行是安全的。

按提示填写 `backend/.env.local` 中的两个可选 Key：

- `DEEPSEEK_API_KEY`：AI 总结（不填则总结步骤明确报错）
- `CLOUD_ASR_API_KEY`：云端转写（不填则云端转写选项禁用）

### 2.2 日常使用方式一：系统托盘控制台（推荐）

双击 `控制台.bat`，托盘区出现状态圆点，右键菜单提供：

| 菜单项 | 说明 |
| --- | --- |
| 启动 / 停止 / 重启 | 控制前后端进程 |
| 查看日志 | 打开 `logs/` 目录 |
| 开机自启 | 勾选后写入启动项（首次启动会主动询问一次） |
| 退出 | 弹出确认框：可只退出托盘（服务继续跑）或连服务一起停 |

- 图标状态：🟢 运行中 ｜ 🟡 启动中 ｜ ⚪ 已停止 ｜ 🔴 出错（气泡提示，日志见 `logs/`）
- 服务在后台隐藏运行，**无黑窗口**；启动完成自动打开浏览器
- **左键双击**图标切换启停；右键打开菜单
- 未安装快捷方式时，也可右键 `控制台.ps1` →「使用 PowerShell 运行」
- 看不到图标？Windows 11 默认把新出现的托盘图标收进折叠区：点任务栏的 `^` 展开即可；在「设置 → 个性化 → 任务栏 → 其他系统托盘图标」里可设为常显

命令行等价：

```powershell
powershell -ExecutionPolicy Bypass -File 控制台.ps1 -Action start|stop|restart|status
```

托盘与 `run.bat` 共享端口 / 日志 / PID 约定，两者可以互相启停。

### 2.3 日常使用方式二：命令行脚本 `run.bat`

| 命令 | 作用 |
| --- | --- |
| `run.bat start` | 前后端各开一个窗口，浏览器自动打开 <http://localhost:3000> |
| `run.bat stop` | 停止（按 PID → 窗口标题 → 端口，三层兜底） |
| `run.bat restart` | 重启 |
| `run.bat status` | 显示运行状态、PID 与最新日志文件 |
| `run.bat help` | 查看用法 |

双击运行时如遇错误会停留等待按键，便于查看提示。

**产物位置**（均不入库）：

- 日志：`logs/backend-*.log`、`logs/frontend-*.log`（自动轮转，各保留 20 份）
- PID：`.vtt-pids/`
- 任务数据库：`backend/data/tasks.db`

## 3. 手动启动（Mac / Linux / 调试）

```bash
# 前端
pnpm install
cp .env.example .env.local     # 至少确认 NEXT_PUBLIC_USE_MOCK 的取值
pnpm dev                       # 开发模式，不注册 Service Worker
```

```bash
# 后端（另开终端）
cd backend
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

验收地址：

- 前端：<http://localhost:3000>（首页提供示例链接，含失败 / 不支持平台演示）
- 后端自检：<http://localhost:8000/api/health>
- 接口文档：<http://localhost:8000/docs>

生产模式（注册 Service Worker，可验证 PWA 安装）：

```bash
pnpm build && pnpm start
```

## 4. 前端环境变量

三个核心变量见 [README 的快速开始](../README.md#快速开始)；完整模板在 [`.env.example`](../.env.example)，后端变量全表见 [backend/README.md](../backend/README.md#环境变量)。

本文只补两条容易踩的细节：

- **`NEXT_PUBLIC_*` 是构建期内联**：改变取值必须重新 `pnpm build`，重启进程无效。构建前建议删掉 `.next`，避免残留产物沿用旧常量。
- **`NEXT_PUBLIC_API_BASE` 由构建脚本注入**：`scripts/build-desktop.mjs` 会把 `DESKTOP_API_BASE` 写进它。手动设这个变量只在排除脚本、直接跑 `BUILD_TARGET=desktop pnpm build` 时才有意义。

## 5. 故障排查

### 5.1 停止服务后刷新页面，界面还在？

这是设计行为，不是 bug。曾访问过生产构建（或点过首页「安装」）的浏览器，Service Worker 会缓存**离线外壳**，服务停止后刷新仍显示界面。

此时页面顶部会出现「后端服务不可达」横幅（含启动指引，后端恢复后 15 秒内自动消失），页面内任何数据操作都会明确报「网络异常」。

彻底清除：F12 → Application → Service Workers → Unregister，或在浏览器设置中清除该站点数据。

### 5.2 重建 / 重启服务后旧标签页报 webpack 错？

例如 `Unhandled Runtime Error: Cannot read properties of undefined (reading 'call')`，栈顶在 `webpack.js` 的 `options.factory`。

原因：**浏览器里保留的旧页面与刚重建的 `.next` 不匹配**（旧页面引用的 chunk 在当前构建里已不存在），不是代码故障。

解决：在报错页按 **Ctrl+Shift+R 硬刷新**。若从已安装的 PWA 窗口打开，先关闭该窗口并清除站点数据。

`pnpm dev` 现在会自动注销残留 Service Worker 并清理 `vtt-shell-*` 外壳缓存，避免再次混用；生产环境行为不变。

### 5.3 后端连不上 / 任务永远停在 processing

- 本地：确认 `uvicorn` 在 8000 端口运行，`curl http://localhost:8000/api/health`
- 云端：`REDIS_URL` 连不上时应用会**静默降级到 memory**，表现为任务永远 `processing` 而接口看起来正常。排查步骤见 [serverless-deploy.md §7.1](serverless-deploy.md)

## 6. 任务持久化说明

- 任务已持久化到 SQLite（`backend/data/tasks.db`）：服务重启后历史任务可查
- 重启时未完成的任务标记为「已中断」并引导重试
- 多 worker / 云端部署需替换为 Redis（接口已封装，见 [backend/README.md](../backend/README.md)）
