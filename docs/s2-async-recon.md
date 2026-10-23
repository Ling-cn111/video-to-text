# S2 侦察报告：Serverless 异步架构改造（API / Worker 分离）

> 任务：S2 侦察阶段（只出报告，不写代码）
> 日期：2026-10-21　基线：`main` @ `7f1c51f`（PR #45 已合并）
> 关联：[docs/serverless-deploy.md](serverless-deploy.md) §8 已知限制 · [backend/README.md](../backend/README.md) · [ROADMAP.md](ROADMAP.md)

---

## 0. 一页结论

1. **病根不是超时，是生命周期**：`BackgroundTasks` 挂的是「响应返回之后」的执行，FC 在该请求结束后会冻结/回收实例，后台协程拿不到 CPU。所以哪怕函数超时配到 15 分钟也没用——任务根本没机会跑完。
2. **现状是「单进程、同步桥接、无重试、无 TTL、无心跳」**：`_run_async()` 每次调用都 `asyncio.run()` 新建事件循环 + 新建 Redis 连接且不回收；任务 key 没有 TTL；任务被云厂商强杀后没有任何机制把它标记为失败。
3. **三种方案里，A（API 异步调用 Worker）改动最小、延迟最低**；B（MNS）最稳但要引入新产品与事件解析；C（定时器扫 Redis）基础设施最省但会引入最多 60 秒延迟，且必须自己实现「原子领取」。
4. **无论选哪个方案，有 5 项改造是共同前置**（§5）：`init()` 语义、TTL、幂等领取、心跳 + 超时收割、`_run_async` 连接泄漏。这几项不做，任何方案上线都会踩同一个坑。
5. **有一个当前代码里的隐藏地雷**：`main.py:16` 在模块导入时调用 `init_task_store()`，而 Redis 实现的 `init()` 会把**所有** `processing` 任务标记为 `interrupted`（`tasks.py:251-262`）。**如果 Worker 复用这个启动逻辑，每个 Worker 实例冷启动都会把别人正在跑的任务全部打成「已中断」。**

---

## 1. 入口与流水线现状

### 1.1 `POST /api/transcribe` 怎么处理

`backend/app/routers/transcribe.py:53-61`：

```python
@router.post("/api/transcribe", response_model=StartTaskResponse)
def create_transcription(payload: StartTranscribeRequest, background_tasks: BackgroundTasks) -> StartTaskResponse:
    url = _resolve_target_url(payload)          # 校验 URL / 平台，缺 url 时用 videoId 重建
    engine = _resolve_engine(payload)           # 请求字段 > ASR_ENGINE；cloud 且无 Key 直接 400
    task_id = create_task(payload.video, engine) # 同步桥接写 Redis/SQLite
    background_tasks.add_task(
        run_transcription_pipeline, task_id, url, payload.video, True, payload.hotwords, engine
    )
    return StartTaskResponse(taskId=task_id, status="processing", stage="parse_link", progress=0)
```

关键点：

- 路由是**同步 `def`**（FastAPI 放线程池），所以可以安全调用同步的 `create_task`。
- `BackgroundTasks.add_task(...)` 注册的是**响应发送后**在同一进程内执行的可调用对象（Starlette 在 `Response.__call__` 之后 `await` 它）。
- 返回体是「已受理」语义（`status=processing`），前端不等待结果，靠轮询。
- `_resolve_engine`（`:18-27`）在**提交时**做云端 Key 预检，未配置直接抛 `CLOUD_NOT_CONFIGURED`，不建任务——这个行为在拆分后应保留在 API 侧。

### 1.2 pipeline 核心函数

`backend/app/services/transcribe.py:44` → `run_transcription_pipeline(task_id, url, video, prefer_subtitles=True, hotwords=None, engine=None)`。

内部是**线性三段 + 单一 try/except**：

| 阶段 | 行号 | 动作 | 进度锚点 |
| --- | --- | --- | --- |
| ① 字幕快路径 | `:59-69` | `try_extract_subtitle_transcript(url)`（yt-dlp CC → dm/view AI 字幕），命中即 `_finish()` **return** | 5 → 40 → 100 |
| ② 下载 + 转码 | `:71-75` | `download_audio(url)` → `convert_to_wav_16k_mono()`，全程内存 BytesIO | 15 → 35 |
| ③ 人声分离（可选） | `:79-87` | demucs 可用且触发时分离，失败静默降级 | — |
| ④ ASR | `:89-107` | `asr.transcribe_audio(...)`，`on_asr_progress` 回调持续写进度 | 40 → 95 → 100 |
| 收尾 | `:22-33` / `:36-41` | `_finish()` 写 completed + transcript + plainText + source；异常走 `_fail()` 写 failed | 100 |

对 S2 有意义的三件事：

1. **函数本身是纯同步的**，参数只有 `task_id` + `url` + 元数据 → **天然可序列化，适合丢进队列**。
2. **没有任何「任务已被别人领取」的判断**：重复投递会重复跑（字幕路径幂等无害，ASR 会重复计费）。
3. **进度写得很碎**：`on_asr_progress`（`:92-93`）**每个 ASR 分段回调一次** `update_task`。27 分钟视频可能有几十次调用 → 在 Redis 模式下就是几十次 `get + set`，见 §5.5 的连接泄漏。

### 1.3 其他相关事实

- `main.py:16`：`task_store.init_task_store()` 在**模块导入时**执行（不是 lifespan 钩子）。
- `main.py:32`：`RateLimitMiddleware` 对**所有 POST** 生效（`rate_limit.py:218`），Worker 若复用该 app 会被自己限流。
- 前端轮询固定 **800ms**（`src/hooks/use-task-polling.ts:7`），GET 不限流（`rate_limit.py:218` 只拦 POST）。
- 契约 `StartTaskResponse{taskId,status,stage,progress}` 与 `GET` 的 `TaskDetailResponse` 不变 → **拆分对前端透明**。

---

## 2. 任务状态流转与 Redis 存储

### 2.1 状态机

```
create_task()                       ← POST /api/transcribe（同步写一条 processing）
   status=processing, stage=parse_link, progress=0
        │
        ├─ pipeline 阶段①  stage=parse_link  progress=5
        ├─ 命中字幕          stage=asr        progress=40 → 100  ──► completed
        ├─ pipeline 阶段②  stage=extract_audio 15 → 35
        ├─ pipeline 阶段④  stage=asr         40 → 95（回调递增）
        ├─ 成功              progress=100 + transcript + plainText + transcriptSource ──► completed
        └─ 任意异常          error={code,message} ──► failed
                                                    │
服务重启 / 进程退出 → init() 扫到 processing ──► interrupted
```

- `status` 取值：`processing | completed | failed | interrupted`（`tasks.py:40`）。
- `stage` 取值：`parse_link | extract_audio | asr`（`:41`）。
- `interrupted` 是**启动时批量改写**的，不是运行期状态迁移；前端把它当「任务已中断（服务重启过），请重试」并给重试按钮（`src/stores/task-store.ts:157-161`）。
- **注意**：`failed` / `interrupted` 都没有「自动重试」，重试是**用户点按钮**发起一个**全新 taskId**（`task-store.ts:264-277`）。

### 2.2 Redis 里存了什么

`RedisTaskStore`（`tasks.py:219-281`）：

- Key：`task:{task_id}`（`:227` `_KEY_PREFIX`，`:247-249`）
- Value：**单个 JSON 字符串**，由 `_dumps()`（`:85-98`）序列化，字段为：

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `task_id` | `TranscribeTask.task_id` | uuid4 hex |
| `status` | `:40` | processing / completed / failed / interrupted |
| `stage` | `:41` | parse_link / extract_audio / asr |
| `progress` | `:42` | 0-100 整数 |
| `created_at` / `updated_at` | `:49-50` | epoch 秒 |
| `video` | `:43` | `{title, cover, duration, platform, videoId}` 或 null |
| `transcript` | `:44` | `[{time, text}]`，**完整文字稿** |
| `plain_text` | `:45` | 拼接全文（与 transcript 重复存一份） |
| `error` | `:46` | `{code, message}` 或 null |
| `engine` | `:47` | 本次任务引擎（重试沿用） |
| `transcript_source` | `:48` | subtitle_cc / subtitle_ai / asr |

另有 `ratelimit:{ip}` Hash（`rate_limit.py:93`），字段 `tokens` / `ts`。

### 2.3 TTL：**没有**

- `create()`：`await client.set(key, _dumps(task))`（`tasks.py:267`）——**无 EX/PX**。
- `update()`：`await client.set(key, _dumps(task))`（`:281`）——同样**无 TTL**。
- `init()`：`await client.set(key, _dumps(task))`（`:262`）——无 TTL。

对比：限流 key 有 `pexpire 120s`（`rate_limit.py:171`）。

**后果**：任务记录**永久驻留**。每条含完整 transcript（27 分钟视频约 7000-10000 字，JSON 约 20-30KB）。在 Serverless 下任务频繁卡在 `processing` 且无人清理，Redis 会**单调增长**直到 `maxmemory` 触发淘汰（默认 `noeviction` 时直接写失败）。

> 这条在 `docs/serverless-deploy.md` §8.2 已标注为「S2 增加 TTL/归档」，本报告给出具体落点：`create` 与 `update` 都加 `EX`，新增 `TASK_TTL_SECONDS`（建议默认 7 天，滑动续期）。

### 2.4 写入并发：读改写，无版本号

`update()`（`:274-281`）是 `get → setattr → set` 三步，**非原子**。API 侧只读（GET），Worker 侧写，理论上冲突面小；但一旦出现**两个 Worker 同时处理同一 taskId**（重试/重复投递），后写者会覆盖前写者的进度与结果。S2 必须加「领取锁」（§5.3）。

---

## 3. 独立 Worker 触发方式评估（阿里云 FC）

> ⚠️ 说明：本次侦察期间阿里云官方文档页多次返回 404/被重定向（`help.aliyun.com/zh/functioncompute/fc-3-0/...` 均不可达），**以下关于 FC 平台行为的描述中，标注「待核对」的部分请在控制台/官方文档确认后再定稿**。代码侧结论不受影响。

### 3.1 三种候选

| 方式 | 机制 | 与本项目的契合度 |
| --- | --- | --- |
| **① FC 异步调用（InvokeFunction, `x-fc-invocation-type: Event`）** | API 函数处理完请求后，用 FC OpenAPI/SDK 调一次 Worker 函数，立即返回；Worker 独立执行，生命周期与 API 请求解耦 | 高：一条 RPC 就能解耦，延迟最低（秒级） |
| **② MNS 队列触发** | API 把 `{taskId}` 发到 MNS Topic/Queue；Worker 函数配置 MNS 事件源，消息到达即触发 | 高：最标准，但要多一个产品 + 事件解析 |
| **③ 定时器轮询 Redis** | Timer 触发器每分钟拉起 Worker，Worker 从 Redis 里扫待处理任务并原子领取 | 中：基础设施最省，但**最少 1 分钟延迟**，且要自己实现领取协议 |

### 3.2 逐项评估

#### ① FC 异步调用自身

- **改动最小**：新增一个 `services/dispatcher.py`，接口 `enqueue(task_id)`；API 侧把 `background_tasks.add_task(...)` 换成 `dispatcher.enqueue(...)`。
- **延迟**：秒级，最接近现在的体验。
- **重试**：FC 异步调用自带重试配置（fc3 组件的 `asyncInvokeConfig` 支持 `maxAsyncRetryAttempts` / `maxAsyncEventAgeInSeconds`，已在 `devsapp/fc3` 的 `__tests__/complete-props.yaml` 中确认字段存在）。**但这是「至少一次」语义**，必须配合幂等领取。
- **代价**：
  - 需要给 API 函数的**执行角色**授予调用 Worker 函数的权限（RAM `fc:InvokeFunction`）。
  - 需要引入阿里云 FC SDK（`alibabacloud_fc20230330` 之类），**这是 `requirements-cloud.txt` 之外的新依赖**（约 10-20MB，含 tea 系列）。
  - **待核对**：自定义容器（custom-container）函数在**异步调用**时，事件如何投递到容器内的 HTTP 服务（是 POST 到 `/`？带什么 header？body 是 event JSON 还是被包装？）。这一点直接决定 Worker 的入口签名，必须先验证。
  - 若不想引 SDK，也可以用「API 函数自己 HTTP 调 Worker 的 `/internal/...`」——但那是**同步**调用，会阻塞 API 请求直到转写结束，等于没改，**不可取**。

#### ② MNS 队列

- **最稳**：天然削峰、死信队列、可视超时、重试次数与间隔可配，Worker 与 API 彻底解耦，不依赖「函数互相调用」的权限模型。
- **改动中等**：
  - 新增 MNS SDK（`mns-python` 或 `alibabacloud_mns`）依赖；
  - Worker 需要解析 MNS 事件源格式（event 里包着 message body，需 base64 解码）；
  - `s.yaml` 增加 `mns_topic` 触发器与 `invocationRole`；
  - 本地开发没有 MNS → 需要抽象出 `Queue` 接口 + 内存实现（测试可跑），否则本地必须连云。
- **风险**：消息体有大小限制（通常 64KB），所以**只能传 taskId**，元数据留在 Redis；这反而更干净。
- **适合场景**：未来要接本地 ASR（重、慢、要排队）、要限并发、要多环境部署。

#### ③ 定时器扫 Redis

- **基础设施最省**：不加任何新产品、不加任何依赖（redis 已在 `requirements-cloud.txt`）。`s.yaml` 加一个 `timer` 触发器（cron），Worker 起来后扫 Redis。
- **延迟硬伤**：FC 定时器最小粒度是分钟级 → **最坏 60 秒才被捡起**。当前字幕快路径 5-7 秒，加上 60 秒等于体验倒退一个数量级。
- **必须自己实现领取**：
  - 队列结构：`ZADD task:pending {created_at} {task_id}`，或直接扫 `task:*` 里 `status=processing` 且 `updated_at` 过旧的（后者无法区分「还没开始」和「正在跑」）。
  - 原子领取：`SET task:lock:{id} {worker_id} NX EX {timeout}`，抢到才跑；跑完 `DEL`。
  - 多实例定时器同时触发 → 必须靠上面的 NX 保证不重复。
- **额外好处**：这套「扫描 + 领取」逻辑**正好就是超时收割器（reaper）**，可以顺带解决「实例被强杀后任务卡 processing」的问题。

### 3.3 结论：推荐 A（FC 异步调用），并把 C 的 reaper 作为配套

- **纯从「代码改动最小」看：① 最小**（一个 dispatcher + 一个 Worker 入口，业务流水线零改动）。
- **纯从「平台稳定性」看：② 最稳**（队列语义完整、有 DLQ）。
- **③ 单独用不够**：60 秒延迟不可接受，但它的「扫 + 领 + 收割」逻辑是**必需品**，建议无论选 ① 还是 ② 都实现。

**推荐路径**：**S2-a 用 ①（FC 异步调用）打通链路 + 共同前置改造（§5）+ 用 ③ 的 reaper 兜底超时**；等本地 ASR 上云 / 需要排队限流时，再平滑切到 ②（dispatcher 已经是接口，换实现即可）。

---

## 4. 代码改造面：Worker 入口应该长什么样

### 4.1 三种入口形态

| 形态 | 做法 | 评价 |
| --- | --- | --- |
| **a. 同一 app 加内部路由** | `POST /internal/worker/transcribe`，靠 `WORKER_TOKEN` 头校验；`WORKER_MODE=1` 时挂载 | 改动小，但**会继承 `RateLimitMiddleware`（自己限流自己）、CORS**，且内部端点暴露在同一函数上，安全边界模糊 |
| **b. 同一镜像、独立 ASGI 应用**（推荐） | 新增 `app/worker_app.py`（只挂内部路由，不装限流/CORS），FC 用**另一个函数**、同镜像、`command` 覆盖成 `uvicorn app.worker_app:app --port 9000` | 隔离干净、镜像复用、s.yaml 两段配置；本地可用 `uvicorn app.worker_app:app` 直接跑 |
| **c. 非 HTTP 事件函数（custom runtime handler）** | 放弃 FastAPI，写 `def handler(event, context)` | 与现有依赖（FastAPI/uvicorn）割裂，且 custom-container 的事件投递方式待核对；**不建议** |

**推荐 b**。理由是：镜像完全复用（不用改 Dockerfile），中间件隔离，本地可跑，且 Worker 的「事件 → 调 pipeline」只有几行胶水。

### 4.2 建议的接口形状（伪代码，非最终实现）

```python
# app/worker_app.py
app = FastAPI()
task_store.init_task_store(mark_interrupted=False)   # ← 关键：Worker 不许把别人的任务打成 interrupted

@app.post("/internal/worker/transcribe")
def run_worker(payload: WorkerPayload, x_worker_token: str = Header(...)):
    verify_token(x_worker_token)
    if not claim_task(payload.taskId):        # SET task:lock:{id} NX EX
        return {"skipped": "already running"}
    try:
        run_transcription_pipeline(payload.taskId, payload.url, payload.video,
                                   True, payload.hotwords, payload.engine)
    finally:
        release_claim(payload.taskId)
    return {"ok": True}
```

要点：

- **入参就是现有 pipeline 的入参**（`task_id / url / video / hotwords / engine`），pipeline 一行不用改。
- `video` 是 pydantic `VideoInfo`，需按 JSON 传（payload 很小，几百字节）。
- **不要**在 Worker 里再 `create_task`——任务必须由 API 创建，否则前端拿不到 taskId。
- **认证**：若 Worker 用 HTTP 触发器，`authType` 建议 `function`（需要鉴权）而不是 `anonymous`；或用内部共享 token。FC 的 HTTP 触发器可以关闭公网 URL（`disableURLInternet`，fc3 触发器配置里存在该字段）。
- **限流**：Worker 必须**不装** `RateLimitMiddleware`，否则一个函数实例处理多个任务时会 429 自己。

### 4.3 API 侧的改动点（极小）

```python
# routers/transcribe.py
task_id = create_task(payload.video, engine)
enqueue(task_id, url, payload.video, payload.hotwords, engine)   # ← 替换 background_tasks.add_task
return StartTaskResponse(...)
```

`enqueue` 的三种实现对应三种方案：

| 方案 | `enqueue` 实现 |
| --- | --- |
| A | 调 FC OpenAPI `InvokeFunction(Event)` |
| B | `mns.send_message(json.dumps({"taskId": ...}))` |
| C | `ZADD task:pending {now} {task_id}`（或只写 `task:pending:{id}` 标记） |
| **local（必须保留）** | 退回 `BackgroundTasks`（本地/单机仍走现在的行为，零回归） |

`ENQUEUE_MODE=inline|fc|mns|redis` 环境变量切换，默认 `inline` → **本地开发与现有测试完全不受影响**，这是控制改造风险的关键设计。

---

## 5. 共同前置改造（不做则任何方案都会踩坑）

这 5 项与选 A/B/C 无关，属于「上云异步」的地基。

### 5.1 `init()` 的语义必须拆开（**最高优先级**）

现状：`main.py:16` 导入即 `init_task_store()`；Redis 实现 `init()`（`tasks.py:251-262`）`SCAN task:*`，把**所有** `processing` 改成 `interrupted`。

问题：这套逻辑在**单进程重启**场景是对的（重启即中断），但在 **Worker 多实例**场景是灾难——Worker 每冷启动一次，就把**正在被别的实例处理的任务**全部判死，前端立刻显示「任务已中断」。

改法（二选一）：

- `init_task_store(mark_interrupted: bool)`，API 传 `True`、Worker 传 `False`；
- 或 `TASK_INIT_MODE=recover|none`。

同时 `SCAN` 全量在任务量大时会变慢，建议改成「只在 API 侧做一次，并加时间窗口过滤（`updated_at` 超过 N 分钟才判 interrupted）」。

### 5.2 TTL

`create` / `update` / `init` 三处 `set` 都加 `EX TASK_TTL_SECONDS`（默认 604800 = 7 天）。滑动续期即可，不必区分终态。若担心「已完成任务被提前清掉」，可对 `completed` 用更长的 TTL。

### 5.3 幂等领取（at-least-once 的前提）

- `SET task:lock:{task_id} {worker_id} NX EX {WORKER_LOCK_TTL}`（TTL 必须 > 预期最长执行时间）。
- 领取失败 → 直接返回 `skipped`，不重复跑。
- 结束时 `DEL`（放在 `finally`）。
- `update_task` 建议顺带带 `worker_id` 或 `attempts` 字段，便于排查。

### 5.4 心跳 + 超时收割（reaper）

现状：**完全没有**。实例被 FC 强杀（超时/OOM/缩容）时，进程内的 `except` 不会执行，任务永远停在 `processing`。

需要：

1. `updated_at` 天然就是心跳（`update_task` 每次都会刷新），但要保证 ASR 阶段**至少每 30 秒**有一次写（见 5.5 的节流，不能只降频到「只在进度跨 5% 时写」——长音频可能几分钟不跨档）。
2. 收割器：扫 `task:*`，`status=processing` 且 `now - updated_at > STALE_SECONDS`（建议 ≥ 函数超时 + 2 分钟）→ 标 `failed`（`error.code=TASK_TIMEOUT`）或重新入队（若 `attempts < MAX_ATTEMPTS`）。
3. 收割器可挂在：Worker 的定时器触发器（方案 C 顺带）、或 API 函数每 N 次请求触发一次（不推荐）、或独立的 timer 函数（推荐，成本极低）。

**P4 实际落地**（与上述建议的差异及原因）：

- **只标 failed，不重新入队**。重投会撞上未过期的执行锁（`TASK_LOCK_SECONDS=3600`）返回 `skipped`；
  且自动重跑有重复计费风险。让用户点重试（新 `task_id` + 新锁）语义最干净。
- **不主动删 `task:lock:*`**。任务已 `failed`，任何重投都会在 `_run_job` 首个状态检查返回
  `skipped: already_failed`；强删锁会破坏「release 必须带 token」的不变量，若 worker 其实还活着
  就会让同一任务被跑第二遍。
- **挂在 worker 函数的定时触发器上**（不是独立 timer 函数）：改动最小，逻辑已独立成
  `app/services/reaper.py`，将来嫌冷启动贵只需加一个 resource。
- **踩到的坑**：定时触发器是**事件调用**，不能指定 HTTP 路径，FC 固定投递到容器 `/invoke`。
  所以 reaper 端点 `/internal/worker/reap` 只能作人工入口，`/invoke` 必须按事件特征分流。
- **并发安全**：`reaper:lock`（复用 `acquire`/`release`，保留 id `__reaper__`）只省算力；
  正确性靠 `fail_if_stale` 的 CAS（Redis 用 WATCH/MULTI）——即使锁失效，两个 reaper 同时扫
  也只会有一个写入成功。

### 5.5 `_run_async()` 的连接泄漏（**当前就有，S2 会放大**）

`tasks.py:306-310`：

```python
def _run_async(coro):
    return asyncio.run(coro)          # 每次新建并关闭一个 event loop
```

而 `RedisTaskStore._client()`（`:234-245`）用 **event loop 作为字典 key** 缓存 client：

```python
loop = asyncio.get_running_loop()
client = self._clients.get(loop)
if client is None:
    client = _redis_asyncio.from_url(...)
    self._clients[loop] = client       # 只增不减
```

**每次 `create_task/get_task/update_task` 都会新建一个 loop → 新建一个 Redis 连接池 → 永久挂在 `_clients` 里。** loop 关闭后连接池不关闭，属于**连接与内存双泄漏**。

量级估算：一个任务至少 6 次 `update_task`（3 个阶段锚点 + 进度回调 + 收尾），`on_asr_progress` 按分段回调，27 分钟视频几十次 → **单任务可能泄漏几十个连接**。本地短生命周期进程看不出来，**长驻 Worker 会先撞 Redis `maxclients`**。

三种解法（S2 一并处理）：

- **首选**：Worker 侧改 `async def` 路由 + 直接 `await store.*`，彻底删掉桥接层（`tasks.py:14-15` 的注释里已经预告了这个方向）；
- 次选：`_run_async` 复用**单个**常驻 loop（`loop.run_until_complete`），client 只建一次；
- 兜底：`_clients` 改 `WeakKeyDictionary` 或显式 `close()`。

### 5.6 进度写入节流（顺带）

`on_asr_progress`（`transcribe.py:92-93`）每次回调都 `get + set` 整个 JSON（含已累积的 transcript）。建议：只在 `progress` 变化 ≥ 5 时写，且**保证 30 秒心跳**（§5.4）。否则 Worker 的 Redis QPS 会随分段数线性上升。

---

## 6. 超时控制与重试现状

### 6.1 有没有处理「跑到一半被强杀」

**没有。** 具体表现：

| 层面 | 现状 | 结果 |
| --- | --- | --- |
| 进程被 FC 杀 | 无信号处理、无 `atexit`、无心跳超时判定 | 任务永久 `processing`，前端无限轮询（800ms/次） |
| 任务层面超时 | 无 | 同上 |
| 单步超时 | 有，但很粗：`_DOWNLOAD_TIMEOUT=300s`（`audio.py:26`）、`_FFMPEG_TIMEOUT=600s`（`:27`）、云端 ASR `timeout=600`（`asr.py:238`）、`EXTRACT_TIMEOUT_SECONDS=30`（`config.py:54`） | 单步能超时，但**整条流水线没有总预算** |
| 函数超时 | 部署文档建议 `timeout: 120`（`serverless-deploy.md` §5） | 对同步总结够用，对 ASR **远远不够** |

> 关于「FC 最大 15 分钟」：这个数字**待核对**（官方 limits 页本次不可达）。但结论不依赖它——**FC 3.0 的函数超时上限普遍远大于 15 分钟**，真正的约束是「实例被回收」而不是「超时」。设计上应假定：**单次函数执行必须能在配置的超时内完成，超时即视为被杀，靠 reaper 兜底**。

### 6.2 有没有重试

| 层级 | 现状 | 位置 |
| --- | --- | --- |
| 流水线（任务级） | **无**。`except Exception` → 直接 `failed`（`transcribe.py:108-109`） | — |
| ASR 引擎降级 | 有 1 次：主引擎失败 → `ASR_ENGINE_FALLBACK`（`asr.py:439-449`） | 进程内 |
| 云端 ASR 协议降级 | 有 1 次：`verbose_json` 400 → `json`（`asr.py:232-241`） | 进程内 |
| LLM 总结 | 有 2 次重试（解析失败重试，见 `summarize` 相关实现） | 进程内 |
| 任务重试 | 仅**用户手动点按钮**（`task-store.ts:264`），生成**新 taskId** | 前端 |
| 平台级重试 | 无（方案 A/B 可借平台的异步重试） | — |

**S2 需要补的**：`attempts` 字段 + `MAX_ATTEMPTS`（建议 2），失败原因区分「可重试（网络/5xx/超时）」与「不可重试（无字幕且无音频、AUDIO_TOO_LARGE、Key 未配置）」。**AUDIO_TOO_LARGE 重试多少次都一样**，必须直接 failed。

---

## 7. 三种架构方案对比

### 方案 A：纯 API 异步调用（FC InvokeFunction, Event）

```
浏览器 ──POST /api/transcribe──► API 函数
                                   ├─ create_task() → Redis
                                   ├─ enqueue() → FC InvokeFunction(Event) ──┐
                                   └─ 200 {taskId, processing}                │
                                                                              ▼
浏览器 ──GET /api/transcribe/{id}──► API 函数 ──读──► Redis ◄──写── Worker 函数
                                                                  （同镜像，worker_app）
```

| 维度 | 评估 |
| --- | --- |
| 延迟 | **秒级**（最好） |
| 代码改动 | **最小**：`services/dispatcher.py`（新）+ `worker_app.py`（新）+ 路由 1 行替换 + `config.py` 加 4-5 个 env |
| 依赖 | 新增阿里云 FC SDK（**要进 `requirements-cloud.txt`**，镜像体积 +10-20MB） |
| 基础设施 | 无新产品；需 RAM 授权 `fc:InvokeFunction` |
| 重试 | 借 FC 异步重试（`maxAsyncRetryAttempts`），至少一次 → **必须幂等** |
| 可观测 | FC 调用日志 + 任务 `attempts` |
| 主要风险 | ① 自定义容器异步调用的事件投递格式**待核对**；② 厂商绑定（换云要重写 dispatcher）；③ 自调用形成环路，需防「Worker 又调自己」 |
| 与 TaskStore 结合 | 只需 Redis 存 taskId + 元数据；`enqueue` 与 store 完全解耦 |

### 方案 B：MNS 队列

```
浏览器 ──POST──► API 函数 ──create_task──► Redis
                    └─ send_message({taskId}) ──► MNS Queue/Topic
                                                        │ 事件源触发
                                                        ▼
                                                   Worker 函数 ──写──► Redis
```

| 维度 | 评估 |
| --- | --- |
| 延迟 | 亚秒-秒级（略逊于 A） |
| 代码改动 | **中等**：dispatcher + `Queue` 抽象（内存实现供本地/测试）+ MNS 事件解析 + s.yaml 触发器 + `invocationRole` |
| 依赖 | MNS SDK |
| 基础设施 | **新增 MNS**（Topic/Queue、订阅、DLQ），有额外成本与配置面 |
| 重试 | **最好**：重试次数/间隔、死信队列、消息可见性超时都可配 |
| 可观测 | MNS 指标 + 消息轨迹 |
| 主要风险 | ① 消息体 64KB 上限 → 只能传 taskId（可接受）；② 事件格式解析易错；③ 本地无法直连，必须抽象 |
| 与 TaskStore 结合 | 好：消息只做「通知」，状态唯一事实源仍是 Redis |

### 方案 C：定时器扫 Redis

```
Timer（每 1 分钟）──► Worker 函数
                        ├─ 扫 task:pending（ZSET）或 processing + updated_at 过旧
                        ├─ SET task:lock:{id} NX EX  ← 原子领取
                        └─ run_transcription_pipeline(...) ──写──► Redis
```

| 维度 | 评估 |
| --- | --- |
| 延迟 | **最坏 60 秒**（硬伤） |
| 代码改动 | **小**：dispatcher 变成写 ZSET + 领取协议 + reaper（这套逻辑本来就要写） |
| 依赖 | **零新增**（redis 已有） |
| 基础设施 | 仅一个 timer 触发器，成本最低 |
| 重试 | 自己实现（`attempts` + 重新 ZADD），最灵活也最容易写错 |
| 可观测 | 需要自己打点 |
| 主要风险 | ① 延迟；② 领取协议写错 → 重复执行/任务饿死；③ 定时器并发实例数需限制 |
| 与 TaskStore 结合 | **最深**：直接把 TaskStore 当队列用（可复用 `task:*`，但建议单独的 `task:pending` ZSET，避免与业务 key 混用） |

### 7.1 汇总表

| 维度 | A. FC 异步调用 | B. MNS 队列 | C. 定时器扫 Redis |
| --- | --- | --- | --- |
| 端到端延迟 | ⭐⭐⭐ 秒级 | ⭐⭐⭐ 亚秒级 | ⭐ 最坏 60s |
| 代码改动量 | ⭐⭐⭐ 最小 | ⭐ 中等 | ⭐⭐ 小 |
| 新增依赖 | FC SDK | MNS SDK | 无 |
| 新增基础设施 | 无 | MNS | Timer |
| 重试能力 | ⭐⭐ 平台重试 | ⭐⭐⭐ 完整（含 DLQ） | ⭐ 自研 |
| 幂等要求 | 必须 | 必须 | 必须 |
| 本地可测性 | ⭐⭐ 可 mock dispatcher | ⭐ 需抽象队列 | ⭐⭐⭐ 直接跑（fakeredis） |
| 厂商绑定 | 高（FC API） | 中（MNS API） | 低（只要 Redis） |
| 适合阶段 | **S2-a 首选** | 规模化 / 接本地 ASR | reaper 兜底、低流量 |

### 7.2 推荐

**S2-a：方案 A（FC 异步调用）+ 方案 C 的 reaper 逻辑 + §5 的 5 项共同前置。**

理由：

1. 延迟不退化（字幕快路径仍是秒级，这是产品的核心卖点）。
2. 代码改动最小：pipeline 零改动，只加 dispatcher 与 worker_app。
3. `ENQUEUE_MODE=inline` 默认值保证本地开发、现有 421 行 `test_transcribe.py`、CI 全部零回归。
4. reaper 与「心跳 + TTL + 幂等领取」是**任何方案都要做**的，先做不浪费。

**何时切 B**：当需要「排队 + 限并发 + 死信 + 本地 ASR 长任务」时。因为 dispatcher 已经是接口，切换只改一个实现类 + s.yaml。

**不建议单独用 C**：60 秒延迟与产品定位（秒回）冲突。

---

## 8. 建议的落地顺序（供确认后执行）

| 阶段 | 内容 | 可独立验证 |
| --- | --- | --- |
| **P0 地基**（不改行为） | `init(mark_interrupted=)` 拆分；TTL；`attempts` 字段；`_run_async` 泄漏修复；进度节流 | 单测（fakeredis） |
| **P1 抽象** | `services/dispatcher.py` + `Queue`/`Enqueue` 接口 + `inline` 实现（保持现状） | 现有测试全绿，行为不变 |
| **P2 Worker 入口** | `app/worker_app.py` + 幂等领取 + 心跳 | 本地 `uvicorn app.worker_app:app` 跑通 |
| **P3 接 FC** | dispatcher 的 `fc` 实现 + `s.yaml` 双函数 + 触发器异步 | 真机部署验证（+ 本地 MockTransport 单测） |
| **P4 兜底** | reaper（timer 函数）+ `TASK_TIMEOUT` 错误码 + 前端文案 | 模拟强杀后任务能自愈 |
| **P5 文档** | 更新 `serverless-deploy.md` §8、`backend/README.md` | — |

> P0/P1 是**纯后端、可本地全量验证**的，风险最低；建议先做这两步再谈接云。
> **进度**：P0 ✅ / P1 ✅ / P2 ✅（`worker_app.py` + `task:lock:*` 幂等领取 + 心跳续期，事件体宽容解包，
> 业务失败回 200、基础设施异常回 5xx；见 `tests/test_worker_app.py`）/ **P3 ✅**（见下）。
>
> **P3 落地方式（与原计划的偏差，已与用户确认）**：原计划走 OpenAPI `InvokeFunction` + RAM 授权，
> 实做改为 **HTTP 触发器 + `X-Fc-Invocation-Type: Async`**。依据：FC 官方文档三处明确 HTTP 触发器支持
> 请求级异步调用（返回 202、落内部队列），且明确「无需认证时可在业务中校验 `Authorization` 自行鉴权」。
> 收益：**不需要 ACS3-HMAC-SHA256 签名、不需要 AK/SK、不需要 SDK**，凭据面最小。
> 代价：worker 函数需配 HTTP 触发器（仅开 POST、`authType: anonymous` + `WORKER_TOKEN` 兜底）。
> 实现：`FcDispatcher`（`app/services/dispatcher.py`）、`s.yaml` 双函数模板、`POST /invoke` 兼容位。

---

## 9. 待确认问题（P3 侦察已逐条关闭）

1. **FC 自定义容器 + 异步调用的事件投递格式**：事件是否以 HTTP POST 到容器、打到哪个路径、body 结构、是否有 `x-fc-*` 头？
   > **P2 已按宽容策略落地**：入口 `POST /internal/worker/transcribe`，只要求 body 能解出 `task_id`，
   > 同时兼容裸作业体 / `{"body": ...}` / `{"payload": ...}` / base64 包装。
   > **P3 已核实**：HTTP 触发器调用是**透传模式**（原始 method/path/headers/body 直达容器）；
   > 异步调用带 `X-Fc-Invocation-Type: Async` 返回 202 + `X-Fc-Request-Id`。`InvokeFunction` API 则固定
   > 投递到 `POST /invoke`、`Content-Type: application/octet-stream`、body = event（已补别名路由）。
   > **仍未确认**：异步重放时 path 是否保持不变（见下第 8 条）。
2. **FC 3.0 函数超时上限**：官方文档未在本次抓取的页面中给出确切数字，`s.yaml` 用 1800s，**部署时须对照
   当前地域配额确认**，超限需在配额中心申请。
3. **是否接受新增依赖**：**已不需要**。P3 走触发器异步，只用已有的 `httpx`，无 SDK、无新依赖。
4. **任务保留期**：`TASK_TTL_SECONDS=604800`（7 天），已落地为滑动续期。
5. **重试策略**：接受 FC 默认 3 次；应用层**不额外重试**（避免与 FC 重试叠加放大重复计费）。
   Worker 对「已完成 / 已失败 / 锁冲突」返回 200 `skipped`，让 FC 的重试不产生副作用。
6. **本地 ASR 是否上云**：不上云（镜像体积失控）。云端 ASR 免费且准确，`ASR_ENGINE=cloud`、
   `ASR_ENGINE_FALLBACK=""`。
7. **并发上限**：worker 设 `instanceConcurrency: 1`（一实例一作业，内存模型最简单）；
   api 设 10。Redis 连接数与云端 ASR 配额按实际压测再调。
8. **【新】异步调用是否原样透传 path**：官方只写「将请求持久化保存，然后立即返回响应」，未明确 path
   是否保持。同步透传是明确的，异步极可能一致。**这是 P3 唯一的真机待验证项**：
   `docs/serverless-deploy.md` §7.3 第 1 步的 curl 专门验它；若返回 404，只改 `WORKER_PATH`，代码零改动。
9. **【已由 P4 解决】Worker 崩溃后的重试被锁挡住**：FC 重试间隔 0.5/1/2 秒，`TASK_LOCK_SECONDS=3600`，
   3 次重试都会撞锁 `skipped`。任务停在 `processing` 直到 reaper 按 `TASK_STALE_SECONDS` 判定。
   **P4 落地方式**：worker 函数加定时触发器（`reapTimer`，每 5 分钟）→ FC 事件调用 → 容器 `/invoke`
   → 按 `triggerTime`/`triggerName` 特征分流到 `app/services/reaper.py`。**关键坑**：定时触发器
   不能指定 HTTP 路径，固定投递到 `/invoke`，所以 reaper 不能只挂在 `/internal/worker/reap`
   （那个端点只留给本地脚本与人工 curl）。详见 `docs/serverless-deploy.md` §5.4、§7.3.1。

---

## 10. 附：关键代码位置索引

| 关注点 | 位置 |
| --- | --- |
| POST 入口 / BackgroundTasks 挂载 | `backend/app/routers/transcribe.py:53-61` |
| 引擎预检（cloud 无 Key 直接 400） | `backend/app/routers/transcribe.py:18-27` |
| pipeline 主函数 | `backend/app/services/transcribe.py:44-109` |
| 字幕快路径 | `backend/app/services/transcribe.py:58-69` |
| 成功/失败收尾 | `backend/app/services/transcribe.py:22-41` |
| 任务模型字段 | `backend/app/services/tasks.py:37-50` |
| Redis key 与序列化 | `backend/app/services/tasks.py:85-98, 219-281` |
| Redis `init()` 全量打 interrupted | `backend/app/services/tasks.py:251-262` |
| 无 TTL 的 `set` | `backend/app/services/tasks.py:267, 281` |
| `_run_async` 桥接（连接泄漏源） | `backend/app/services/tasks.py:306-310` |
| 启动即 init | `backend/app/main.py:16` |
| 限流中间件（只拦 POST） | `backend/app/services/rate_limit.py:210-229` |
| 限流 TTL（对照：任务无 TTL） | `backend/app/services/rate_limit.py:171` |
| ASR 引擎降级 | `backend/app/services/asr.py:439-449` |
| 云端 ASR 协议降级 + timeout | `backend/app/services/asr.py:229-246` |
| 下载/FFmpeg 超时 | `backend/app/services/audio.py:26-27` |
| 前端轮询 800ms | `src/hooks/use-task-polling.ts:7` |
| 前端 interrupted / 重试 | `src/stores/task-store.ts:157-161, 264-277` |
| 存储双实现同用例 | `backend/tests/test_tasks_store.py`（135 行） |
| HTTP 层字幕流程用例 | `backend/tests/test_transcribe.py:378-401` |
| Worker 应用（P2） | `backend/app/worker_app.py` |
| 执行锁 acquire/release/renew | `backend/app/services/tasks.py`（Protocol + 两个实现） |
| Worker 端到端用例 | `backend/tests/test_worker_app.py` |
| 心跳钩子（锁续期） | `backend/app/services/transcribe.py` `ProgressReporter._fire_hook` |
| **FC 分发（P3）** | `backend/app/services/dispatcher.py` `FcDispatcher` / `DispatchError` |
| **FC 双函数模板（P3）** | `s.yaml`（仓库根目录） |
| **FC 相关环境变量（P3）** | `backend/app/config.py` `WORKER_URL` / `WORKER_PATH` / `ASYNC_TIMEOUT_SECONDS` |
| **InvokeFunction 兼容入口（P3）** | `backend/app/worker_app.py` `INVOKE_PATH` / `run_invoke_entry` |
| **分发单测（P3）** | `backend/tests/test_dispatcher.py`（MockTransport）、`test_transcribe_dispatch.py`（HTTP 端到端） |
| **Reaper 逻辑（P4）** | `backend/app/services/reaper.py` `reap_once` / `ReapResult` |
| **Reaper 端点（P4）** | `backend/app/worker_app.py` `REAP_PATH` / `reap_stale_tasks` |
| **定时事件分流（P4）** | `backend/app/worker_app.py` `_looks_like_timer_event` |
| **Reaper 存储原语（P4）** | `backend/app/services/tasks.py` `list_processing_tasks` / `fail_if_stale` |
| **Reaper 本地脚本（P4）** | `backend/scripts/reap.py`（`python -m scripts.reap --dry-run`） |
| **Reaper 单测（P4）** | `backend/tests/test_reaper.py`（memory + fakeredis 参数化） |
| **定时触发器配置（P4）** | `s.yaml` worker.triggers 的 `reapTimer` |
