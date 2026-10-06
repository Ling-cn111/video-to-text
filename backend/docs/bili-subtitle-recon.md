# B站播放器字幕接口侦察报告（任务 G 第一步）

> 日期：2026-10-06 ｜ 状态：**侦察成功——未登录可稳定获取 AI 字幕** ｜ 结论已待用户确认
> 侦察脚本：`backend/.audio-bench/recon_bili{,2,3}.py`（本地临时不入库）；原始输出 `recon_result*.txt`

## 1. 背景与方法

目标：不依赖登录 Cookie 获取 B站 AI 字幕（任务 F 的 Cookie 方案有月度过期维护成本）。
参考 vruses/beefreely（TypeScript 浏览器扩展，145★）的「拦截/构造播放器 API」思路。

**侦察走了三轮**（含一次关键转向）：

| 轮次 | 探测对象 | 结果 |
| --- | --- | --- |
| 第一轮 | `/x/player/v2`、`/x/player/wbi/v2`（用户最初指定的候选接口），5 视频 × 无签名 | 接口未登录可访问（HTTP 200 code=0），但 **subtitle.subtitles 全部为空** |
| 第二轮 | 变量排查矩阵：匿名设备指纹 cookie（buvid3/buvid4/b_nut/_uuid）、aid 参数形式、**wbi 签名 PoC**（公开算法，nav API 取 key），3 视频 × 6 变体 | 全部 18 组合仍为空——排除参数/签名/指纹因素，**问题出在接口本身**：AI 字幕不在 player API 的未登录响应里 |
| 第三轮 | **调研 beefreely 源码**（`src/bilibili/www/video/hooks/useSubtitle.ts`）：它取字幕用的是**弹幕元数据接口 `/x/v2/dm/view?aid=&oid=&type=1`**（不是 player API！）→ 立即验证 | **未登录直接返回 AI 字幕轨道** ✅ |

> 教训：用户给的候选接口（player/v2、wbi/v2）是「播放器信息接口」，AI 字幕轨道实际挂在**弹幕元数据接口**的 `data.subtitle` 字段下。第二轮的签名 PoC 顺带回答了「wbi 签名成本」：公开算法约 30 行代码（MD5 为 B站协议规定），但本路径**完全用不到它**。

## 2. 视频选择与理由

| 视频 | 理由 |
| --- | --- |
| BV1gCYc6BEPv / BV1S6aB63Er7 / BV1C9aB6aE5f | benchmark 三用例（科普/讲解/教程），此前 yt-dlp 未登录实测「无可用字幕」——验证 dm/view 是否覆盖它们 |
| av116187438517161 | 阶段二验收记录中的「无字幕视频」（ASR 路径实测用）——验证 dm/view 对它的行为 |
| 热门池动态取 2 个（`/x/web-interface/popular`） | 避免硬猜失效 BV 号；高播放视频（138-155万）AI 字幕概率高、内容类型多样（七七漫画社动画 / 蔚蓝丶羽翼） |
| BV1hQHW63EMc（央视新闻《缅北电诈覆灭纪实》） | 官方号 + 纪录片，AI 字幕覆盖率应最高的极端样本——用作「覆盖上限」检验（结果见下，反而为 0，证明 AI 字幕非全覆盖） |

请求约束：Chrome UA + `Referer/Origin: https://www.bilibili.com/`，间隔 1.5s，全程无登录态，共 ~30 个请求。

## 3. 核心发现：字幕获取链路（未登录，零 Cookie）

```
1. GET https://api.bilibili.com/x/web-interface/view?bvid={bvid}
   → data.aid, data.cid（弹幕接口用 aid/oid，与 player API 的 bvid/cid 参数体系不同）
2. GET https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1
   → data.subtitle.subtitles[]：8 条轨道（ai-zh 中文自动生成 + ai-en/ja/es/ar/pt/th/id 自动翻译）
   → 无需 Cookie、无需 wbi 签名、无登录态
3. 轨道字段：{ id_str, lan: "ai-zh", lan_doc: "中文（自动生成）", ai_status: 2,
              subtitle_url: "http://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/...?auth_key=..." }
   → 注意字段名是 subtitle_url（不是 url）；ai_status=2 = 已生成
4. GET {subtitle_url}（http→https 升级后下载，域名 aisubtitle.hdslb.com 在现有白名单内）
   → {"body": [{"from": 0.08, "to": 1.2, "content": "短的发布会"}, ...]}
   → 与 B站 CC 字幕同格式，现有 parse_subtitle_body('json') 直接解析 ✅
```

实测：BV1S6aB63Er7 的 ai-zh 轨道下载 16.9KB → **171 条带时间戳字幕**，HTTP 200，无需任何认证。
质量抽样（对照用户人工裁决的开场文本）：「短的发布会 / 多恩的发布会会唠这一场 / 离退休老头库克最后精神家园 / 库克复刻复刻」——与裁决稿高度吻合（「唠」vs 人耳裁决「绕」，属 AI 字幕正常误差级）。

## 4. 覆盖范围矩阵（6 视频）

| 视频 | dm/view 轨道数 | ai-zh | 说明 |
| --- | --- | --- | --- |
| BV1gCYc6BEPv 科普骨折 | 8 | ✅ | benchmark 判定「无可用字幕」实为 yt-dlp 未登录拿不到——**dm/view 覆盖了它** |
| BV1S6aB63Er7 讲解iQOO | 8 | ✅ | 同上 |
| BV1C9aB6aE5f 教程小天才 | 8 | ✅ | 同上（第一轮测得） |
| BV1YtHs62EHZ 热门池 | 1 | ✅ | 覆盖 |
| av116187438517161 生理科普 | 1 | ✅ | **验收记录里的「无字幕视频」实际有 AI 字幕** |
| BV1hQHW63EMc 央视纪录片 | **0** | ❌ | AI 字幕非 100% 覆盖（官方号也有例外）→ 降级链仍必要 |

## 5. 四问回答

1. **未登录能否拿 AI 字幕？** ✅ 能。`/x/v2/dm/view` 无需 Cookie、无需 wbi 签名、无需设备指纹。
2. **lan 类型？** `ai-zh`（中文自动生成，主用）+ `ai-en/ai-ja/ai-es/ai-ar/ai-pt/ai-th/ai-id`（机器自动翻译）；另有 UP 主手传 CC（`lan=zh-CN` 等，同接口的 subtitle.subtitles 里 `ai_status=0`）。
3. **字幕 URL 是否直连可下载？** ✅ `subtitle_url` 指向 `aisubtitle.hdslb.com`（B站自 CDN，现有域名白名单已覆盖），http 直连可用、https 升级可用，带 `auth_key` 时效签名（下载即用，无二次认证）。
4. **与 yt-dlp 未登录相比覆盖范围？** **显著更大**：yt-dlp 未登录 0 轨道（6/6 视频），dm/view 5/6 有轨道——包括 3 个 benchmark 视频和 1 个验收记录中的「无字幕视频」。但**非全覆盖**（央视纪录片 0 条），无字幕时仍需 ASR 兜底。

## 6. 成功后的工期预估

| 步骤 | 内容 | 预估 |
| --- | --- | --- |
| 第二步 | `bili_player_api.py`（view/dm-view/下载，白名单+间隔+https 升级） | 0.5h |
| 第三步 | 管线分层降级接入（yt-dlp CC → dm/view AI → 预留 Cookie 层 → ASR）+ 来源标记 | 0.5h |
| 第四步 | transcriptSource 契约 + 前端 Badge + E2E 锁定 5 | 1h |
| 第五步 | mock 单测 ~10 条 + network 集成测试 + 4 处文档 + PR/CI | 1-1.5h |
| **合计** | | **3-3.5h**（AI 辅助下实际会话 1-2 轮） |

## 7. 集成测试说明（第五步交付，CI 跳过）

- 标记 `@pytest.mark.network`（CI backend job 排除，同现有约定）
- **请求数**：每视频 2-3 个（view + dm/view + 字幕下载），3 视频 ≈ **7-9 个请求**，间隔 1.5s
- **耗时**：约 15-25s（含间隔）
- **本地运行**：`cd backend && .venv\Scripts\activate && pytest -m network tests/test_bili_subtitle_network.py -v`
- 覆盖：真实无 Cookie 拿 AI 字幕 → 下载 → 解析全链路断言（条目数 > 0、含时间戳）

## 8. 风险与预案（侦察后更新）

| 风险 | 状态 |
| --- | --- |
| 未登录拿不到字幕 | ✅ 已排除（dm/view 路径成立） |
| 需要 wbi 签名 | ✅ 已排除（dm/view 无需签名；PoC 已验证签名算法可行，留作备用） |
| 接口改版失效 | 降级链天然兜底：yt-dlp CC → dm/view AI →（预留 Cookie 层）→ ASR，任一层失败静默降级；dm/view 为弹幕体系老接口（比 player API 稳定） |
| 反爬 | 请求间隔 1.5s；每任务最多 2-3 个请求（view/dm-view/字幕下载），低于网页播放器正常行为；集成测试 CI 跳过 |

## 9. 建议决策

**继续执行任务 G 第二~五步**（dm/view 路径成立，预期收益：有 AI 字幕的视频全部秒级返回 + 零成本 + 质量优于任何 ASR；benchmark 3 视频与验收「无字幕」视频全部被覆盖）。
