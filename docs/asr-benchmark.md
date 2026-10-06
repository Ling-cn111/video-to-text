# ASR 中文准确率评测（CER 基准）

> 生成：阶段二准确率专项（分支 fix/asr-accuracy）；引擎横向对比补充（分支 feat/asr-engine-swap）。复测命令见文末。

## 评测方法

- **测试集**：`backend/tests/asr_benchmark/cases.json` —— 科普 / 讲解 / 教程三类 5-10 分钟 B站中文视频（实测无可用字幕）
- **音频**：下载音轨后 FFmpeg 截取**前 150 秒**转 16kHz 单声道 WAV（与参考稿对齐）
- **参考稿**：`backend/tests/asr_benchmark/refs/<videoId>.txt`
  - ⚠️ **来源与局限**：参考稿由 **faster-whisper large-v3 (int8)** 转写生成（B站 AI 字幕需登录 Cookie 未配置，人工标注待补）。因此表中 CER 为**「相对 large-v3 的偏差」**：模型间**横向对比有效**（同一参考稿），large-v3 自身的 CER 接近 0 属预期；**绝对 CER 需人工修订参考稿后才成立**（修订后重跑脚本即可）
- **CER 归一化**（两侧同规则）：zhconv 繁→简 → cn2an 数字归一 → 去标点与空白 → 拉丁小写
- **指标**：CER = 字符编辑距离 / 参考字符数；另记录转写耗时（150 秒音频）与进程内存峰值

## 实测结果（2026-10-03，Windows 11 / CPU int8·bf16 / 前 150 秒音频 × 3 视频）

| 引擎 / 模型 | 科普 CER | 讲解(强BGM) CER | 教程 CER | 平均 CER | 转写耗时(150s音频) | 内存峰值 |
| --- | --- | --- | --- | --- | --- | --- |
| faster-whisper base (int8) | 54.20% | 96.75% | 16.20% | 55.72% | 7-22s | ~380-480MB |
| **faster-whisper small (int8，默认)** | **28.12%** | 86.77% | **6.29%** | **40.39%** | 36-69s | ~765MB |
| faster-whisper medium (int8) | 44.93% | 94.14% | 4.82% | 47.96% | 100-163s | ~1.7-1.9GB |
| large-v3 (int8，参考稿模型) | 0.00%* | 0.22%* | 0.00%* | 0.07%* | 141-204s | ~3.0-4.3GB |
| Qwen3-ASR-1.7B (bf16 CPU) | 43.19% | 98.26% | **5.35%** | 48.93% | 656-822s | ~6.6-8.1GB |
| Fun-ASR-Nano-2512 | 75.65% | 96.96% | 98.39% | 90.33% | 70-76s | ~5.1-6.9GB |

\* large-v3 为参考稿生成模型，自比 CER≈0 属预期（见「参考稿局限」）。

完整数据：`backend/tests/asr_benchmark/results*.csv`（含 ref/hyp 字符数）。

## 引擎横向对比解读

1. **faster-whisper small 仍是本地默认最优**（平均 40.39%）。Qwen3-ASR-1.7B 平均 48.93%：教程类更强（5.35% vs 6.29%，且原生多语言 + 唱歌鲁棒性官方声明支持）但科普较弱（43.19% vs 28.12%）、讲解仍崩（98.26%），且 CPU 耗时是 whisper 的 10-18 倍、内存 8-10 倍（transformers 逐 token 生成）。
2. **Fun-ASR-Nano 不可用**：三个视频全部大面积幻觉（教程类输出「这是小天才手表」×3 循环，与语音内容零重叠），平均 90.33%。文本对比取证确认是模型幻觉而非集成问题——0.8B 模型对本测试集不适用。
3. **强 BGM 讲解类对所有本地引擎都难**（86-98%，仅 large-v3 0.22% 幸存）。

## 人声分离（Demucs）实测——零成本方案触顶 ⚠️

在音频提取与 ASR 之间加入 Demucs (htdemucs) 人声分离（`ENABLE_VOCAL_SEPARATION` / 智能触发：非语音占比 > 0.4），只把 vocals 喂给 Whisper：

| 模型 | 科普 CER | 讲解(强BGM) CER | 教程 CER | 平均 CER | Demucs 耗时(150s音频) |
| --- | --- | --- | --- | --- | --- |
| small（无分离） | 28.12% | 86.77% | 6.29% | 40.39% | — |
| small + Demucs | 29.57% | **85.25%** | 7.10% | 40.64% | 91s |
| medium（无分离） | 44.93% | 94.14% | 4.82% | 47.96% | — |
| medium + Demucs | 71.30% | 93.49% | 5.35% | 56.71% | 91s（缓存复用） |

**结论：零成本方案触顶，决策门触发（讲解 85.25% > 25%）。**

- 分离对「讲解」几乎无效（86.77% → 85.25%），medium+Demucs 在科普上反而恶化（54.20→44.93 的 medium 退到 71.30）。
- **根因（文本对比取证）**：small 对「讲解」的转写无论分离与否都是**幻觉乱码**（「酷客。酷客。酷客…」，与真实解说词零重叠），而 large-v3 参考稿是清晰的发布会解说内容——问题不是 BGM 污染人声，而是**小模型对快节奏带货式解说的解码幻觉**；Demucs 分离出的人声无法阻止小模型幻觉。
- 分离机制本身工作正常：输出格式契约通过（16kHz/单声道/float32 断言，`tests/test_separation.py`），清晰口播类无收益（教程 6.29%→7.10% 略负）。
- **去向**：强 BGM / 快节奏解说场景需要云端 ASR（任务 D，`ASR_ENGINE=cloud`）或 large-v3 级别模型（GPU）。

## 结论与默认配置

| 场景 | 推荐配置 |
| --- | --- |
| 追求速度 | `ASR_ENGINE=faster-whisper` + `WHISPER_MODEL=base` |
| 平衡（**已定为默认**） | `ASR_ENGINE=faster-whisper` + `WHISPER_MODEL=small` |
| 教程/清晰口播追求准确 | `ASR_ENGINE=qwen3`（Qwen3-ASR-1.7B，教程类 5.35%，CPU 慢 10 倍）或 whisper medium |
| 唱歌/带 BGM 音频（尝试） | `ASR_ENGINE=qwen3`（官方声明支持 Songs with BGM；MV 实测建议人工复核） |
| 高精度 / 强 BGM 音频（可靠） | `ASR_ENGINE=cloud`（OpenAI 兼容接口，配 `ASR_API_KEY`） |

运行时热词：`POST /api/transcribe` 请求体支持 `hotwords: ["Faster Whisper", "B站"]`——faster-whisper 经 initial_prompt 注入；Qwen3 经 context 注入；Fun-ASR 经 hotword 参数（该引擎不建议使用）。

## 人声分离（Demucs）启用方式与边界

- 可选依赖：`pip install -r backend/requirements-separation.txt`（连带 torch）；未安装时相关代码自动跳过
- 触发：`ENABLE_VOCAL_SEPARATION=true` 无条件启用；或 VAD 统计非语音占比 > `VOCAL_SEP_TRIGGER_RATIO`（默认 0.4）自动启用
- 性能代价：150 秒音频 CPU 分离约 91 秒（htdemucs），转写前额外增加
- **适用边界（实测）**：对「小模型幻觉型」强 BGM 场景无效（幻觉与噪声无关），默认关闭；仅当确认失败原因是「人声被 BGM 压住、非幻觉」时手动开启尝试

## 复现

```bash
cd backend
python scripts/benchmark_asr.py --models base small medium --seconds 150
python scripts/benchmark_asr.py --models large-v3 --seconds 150   # 显存/内存充足时
python scripts/benchmark_asr.py --engine qwen3 --models qwen3-1.7b --seconds 150
python scripts/benchmark_asr.py --engine funasr --models funasr-nano --seconds 150
# 输出：tests/asr_benchmark/results*.csv
```

人工修订参考稿（refs/*.txt）后重跑，即可得到绝对 CER。
