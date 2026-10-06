# ASR 中文准确率评测（CER 基准）

> 生成：阶段二准确率专项（fix/asr-accuracy）；引擎横向对比（feat/asr-engine-swap）；
> **基准审计与修复 + 云端引擎实测（feat/cloud-asr-manual，任务 D，2026-10-06）**。复现命令见文末。

## 评测方法

- **测试集**：`backend/tests/asr_benchmark/cases.json` —— 科普 / 讲解 / 教程三类 5-10 分钟 B站中文视频（实测无可用字幕）
- **音频**：下载音轨后 FFmpeg 截取**前 150 秒**转 16kHz 单声道 WAV（与参考稿对齐）
- **参考稿**：`backend/tests/asr_benchmark/refs/<videoId>.txt`，可信度分级：

  | case | 参考稿来源 | 状态 |
  | --- | --- | --- |
  | 教程 | large-v3 自动转写 | ✅ 完整（ref/输出字符比 1.00），数字可信 |
  | 科普 | large-v3 自动转写 | ⚠️ 缺动画喊叫声等背景人声（两个云端模型一致听到、ref 无）→ CER 绝对值偏高，横向对比仍可用；待任务 C 人工校对 |
  | 讲解(强BGM) | **0-60s 用户抽听人工裁决 + 60-150s large-v3** | ✅/⚠️ 开场已修复（见下），60-150s 段残留 large-v3 少量错字 |

- **CER 归一化**（两侧同规则）：zhconv 繁→简 → cn2an 数字归一 → 去标点与空白 → 拉丁小写
- **指标**：CER = 字符编辑距离 / 参考字符数；另记录转写耗时（150 秒音频）与进程内存峰值

## ⚠️ 基准缺陷审计与修复（2026-10-06，任务 D 触发）

**发现**：原 large-v3 参考稿在讲解（强BGM）case 上**缺失开场 0-60 秒（约 40% 内容）**，导致旧表中讲解列全部不可解读（本地 86-98% 的「全崩」与云端 88-97% 同为伪象）。证据链：

1. large-v3 本机复现（与参考稿生成同管线同音频）：**0-60s 零有效输出**（VAD 开=整段跳过；VAD 关=仅幻觉一字「带」）；
2. 参考稿正文恰好从音频第 60 秒内容开始，且 ref 字符数 461 vs 两个独立云端模型一致转出 795/821 字（语速 5.3+ vs 3.07 字/秒）；
3. difflib 覆盖率：ref 内容的 84-87% 被云端输出包含（ref 是子集）；重叠区 ref 自带错字（主床/冰穷/首名——云端反而转对为 祖传/冰穹/寿命）；
4. **用户抽听裁决（0-60s）**：确认开场确有大量语音（老罗串场、吐槽库克、KPL 梗），并修正主要错字。

**修复**：讲解参考稿重写为「用户裁决的 0-60s + large-v3 的 60-150s」（见 refs 文件头注释）。

**教训（新增 case 必查）**：参考稿=「单一模型输出」时，尺子自带该模型的时间盲区与家族偏好。新 case 必须先验 **ref 时间覆盖**（首段起点 + 语速 sanity：中文口播 3-6 字/秒）再跑 CER。

## 实测结果（基准修复后，2026-10-06，Windows 11 / CPU / 前 150 秒 × 3 视频）

| 引擎 / 模型 | 科普 CER¹ | 讲解(强BGM) CER | 教程 CER | 平均 CER² | 耗时(150s音频) | 内存峰值 | 成本(150s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **faster-whisper small (int8，本地默认)** | **28.12%** | 19.43% | 6.29% | **17.95%** | 36-155s³ | ~745MB | 0 |
| Qwen3-ASR-1.7B (bf16 CPU 本地) | 43.19%¹ | 97.14%⁴ | **5.35%** | 48.56% | 656-1372s | ~6.6-8.1GB | 0 |
| **CloudASR：XingChenASR-V3.2-Ultra（硅基流动，带分段时间戳）** | 40.58%¹ | 17.43%（敏感性下界 15.69%⁵） | **4.15%** | 20.72%² | 23-39s | ~76-83MB | **¥0（免费）** |
| CloudASR：Qwen3-ASR-1.7B（硅基流动，仅整段文本） | 39.42%¹ | 16.56%（敏感性下界 15.57%⁵） | 6.43% | 20.80%² | 8-11s | ~76-84MB | **¥0（免费）** |

¹ 科普参考稿缺背景人声（见参考稿分级），绝对值偏高，横向对比仍可用。
² 平均值混入科普的参考稿缺口，仅作粗略排序。
³ 文档历史区间 36-69s；本次复测单次 155s（含冷启动）。
⁴ Qwen3 **本地** qwen-asr 管线在强 BGM 下近乎整段静默失败（150s 音频仅输出 33 字 / ref 803 字）——同模型**云端托管版**同音频 16.56%，对照鲜明：本地包的推理管线无法处理该音频，云端服务可用。科普/教程两项沿用 2026-10-03 旧测。
⁵ 敏感性：将参考稿 60-150s 段高置信错字一并修正后的 CER——**最终绝对值待任务 C 全文人工校对**。

### 强 BGM 讲解类结论（对照 ≤15% 目标）

- 修复基准后，旧表「全崩」数字作废：small 实际 19.43%、云端 16.6-17.4%；**唯一仍失败的是 Qwen3 本地管线**（97.14%，150s 仅输出 33 字近乎静默——云端托管版同模型 16.56%）。
- 云端（XingChen，带时间戳）对本地 small 有 **2 个点**优势，且 CPU 耗时仅 1/2~1/7、内存 1/10；
- 16.6%/17.4% 尚未达 ≤15%，但敏感性下界已到 **15.6%**（残余误差含参考稿 60-150s 自身错字）——**绝对裁决待任务 C**；
- 云端模型对开场的转写经用户抽听确认基本正确；Demucs 对云端仅 +0.7 点收益，不值得 144s 代价（见下）。

## 存档：修复前旧数据（2026-10-03，⚠️ 讲解列基于缺陷参考稿，仅存档勿引用）

| 引擎 / 模型 | 科普 CER | 讲解(强BGM) CER ⚠️ | 教程 CER | 平均 CER |
| --- | --- | --- | --- | --- |
| faster-whisper base (int8) | 54.20% | 96.75% ⚠️ | 16.20% | 55.72% |
| faster-whisper small (int8) | 28.12% | 86.77% ⚠️ | 6.29% | 40.39% |
| faster-whisper medium (int8) | 44.93% | 94.14% ⚠️ | 4.82% | 47.96% |
| large-v3 (int8，参考稿模型) | 0.00%* | 0.22%* ⚠️ | 0.00%* | 0.07%* |
| Qwen3-ASR-1.7B (bf16 CPU) | 43.19% | 98.26% ⚠️ | **5.35%** | 48.93% |
| Fun-ASR-Nano-2512 | 75.65% | 96.96% ⚠️ | 98.39% | 90.33% |

\* large-v3 为参考稿生成模型，自比 CER≈0 属预期。

完整数据：`backend/tests/asr_benchmark/results*.csv`（含 ref/hyp 字符数）；审计中间产物在 `backend/.audio-bench/`（不入库）。

## 引擎横向对比解读（修订）

1. **本地默认仍是 faster-whisper small**（修复基准后平均 17.95%）。Qwen3-ASR-1.7B 教程类更强（5.35%）但科普较弱、CPU 耗时 10-18 倍、内存 8-10 倍——定位不变：可选引擎。
2. **Fun-ASR-Nano 不可用**（三视频大面积幻觉，平均 90.33%）——该结论与基准缺陷无关（幻觉是输出与语音内容零重叠，文本取证确认）。
3. **强 BGM 讲解类**：旧结论「本地全崩、仅 large-v3 幸存」**作废**——实际 small 19.43%、云端 16-17%；云端 XingChen 是当前该场景最优可用方案（准确 + 快 + 带时间戳 + 免费）。

## 云端 ASR 实测说明（硅基流动，任务 D）

- **模型目录（2026-10 实测，`GET /v1/models?sub_type=speech-to-text`）**：
  - `XingChenAGI/XingChenASR-V3.2-Ultra`：**支持 `response_format=verbose_json`（27 段分段时间戳）**，兼容 `language`/`prompt` 参数 → **默认云端模型**
  - `Qwen/Qwen3-ASR-1.7B`、`FunAudioLLM/SenseVoiceSmall`：仅整段 text（`verbose_json` 返回 400；引擎已自动降级 `json`，输出退化为整段单条、时间戳定位视图退化）
  - whisper-large-v3 **已不在售**（旧文档/早期调研过时）
- **计费**：ASR 模型全部**免费**（官网定价页 2026-10）；`usage` 按音频时长计（`{"type":"duration","seconds":150}`），转写 150s 音频实测 8-40s 返回。
- **引擎行为**：首选 `verbose_json`，400 自动降级 `json`；服务地址经 url_guard 校验（仅公网 http/https）；未配 `CLOUD_ASR_API_KEY` 时选择云端提交即报 `CLOUD_NOT_CONFIGURED`。

## 人声分离（Demucs）实测——旧结论作废，待重测 ⚠️

> ⚠️ 下表讲解列基于缺陷参考稿，「Demucs 无效」的量化结论**作废**；分离机制本身正常（格式契约通过）。基准修复后的小规模复测见下。

| 模型 | 科普 CER | 讲解(强BGM) CER ⚠️ | 教程 CER | 平均 CER |
| --- | --- | --- | --- | --- |
| small（无分离） | 28.12% | 86.77% ⚠️ | 6.29% | 40.39% |
| small + Demucs | 29.57% | 85.25% ⚠️ | 7.10% | 40.64% |
| medium + Demucs | 71.30% | 93.49% ⚠️ | 5.35% | 56.71% |

**基准修复后复测（讲解 case，2026-10-06）**：

| 配置 | 讲解(强BGM) CER | 额外代价 |
| --- | --- | --- |
| XingChenASR-V3.2-Ultra（无分离） | 17.43% | — |
| XingChenASR-V3.2-Ultra + Demucs | **16.69%** | +144s CPU 分离（150s 音频） |

结论：修复基准后 Demucs 对云端仅 **0.7 个点**改善（17.43%→16.69%），代价 +144s——**收益边际，维持默认关闭**；强 BGM 场景的最优解是直接用云端引擎。旧结论「Demucs 对小模型幻觉无效」的方向正确（幻觉与噪声无关），但量化数字当时受坏基准污染。

启用方式：`pip install -r backend/requirements-separation.txt`；`ENABLE_VOCAL_SEPARATION=true` 或 VAD 非语音占比 > `VOCAL_SEP_TRIGGER_RATIO`（默认 0.4）智能触发；150 秒音频 CPU 分离约 91-144 秒。

## 结论与默认配置

| 场景 | 推荐配置 |
| --- | --- |
| 追求速度 | `ASR_ENGINE=faster-whisper` + `WHISPER_MODEL=base` |
| 平衡（**已定为默认**） | `ASR_ENGINE=faster-whisper` + `WHISPER_MODEL=small` |
| 教程/清晰口播追求准确（本地） | `ASR_ENGINE=qwen3`（CPU 慢 10 倍）或 whisper medium |
| **强 BGM / 快节奏解说（当前最优可用）** | **前端「转写模式」选云端**（`CLOUD_ASR_*` 配置，默认硅基流动 XingChenASR-V3.2-Ultra：带时间戳、免费、讲解 17.43%） |
| 绝对精度裁决 | 待任务 C 人工校对参考稿后重跑（讲解 60-150s 段为最后一公里） |

运行时热词：`POST /api/transcribe` 请求体支持 `hotwords: ["Faster Whisper", "B站"]`——faster-whisper 经 initial_prompt 注入；Qwen3 经 context 注入；云端经 prompt 参数（XingChen 实测接受，输出与无 prompt 一致，效果中性）。

## 复现

```bash
cd backend
python scripts/benchmark_asr.py --models base small medium --seconds 150
python scripts/benchmark_asr.py --engine qwen3 --seconds 150
# 云端（需 backend/.env.local 配 CLOUD_ASR_API_KEY）：
CLOUD_ASR_MODEL=XingChenAGI/XingChenASR-V3.2-Ultra \
  python scripts/benchmark_asr.py --engine cloud --seconds 150 --cloud-price-per-hour 0
# 人声分离对照：
python scripts/benchmark_asr.py --engine cloud --seconds 150 --separate
# 输出：tests/asr_benchmark/results*.csv（或 --output 指定路径）
```

人工修订参考稿（refs/*.txt）后重跑，即可得到绝对 CER。**新增评测 case 时先验参考稿时间覆盖（首段起点 + 语速 3-6 字/秒 sanity）。**
