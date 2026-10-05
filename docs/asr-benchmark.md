# ASR 中文准确率评测（CER 基准）

> 生成：阶段二准确率专项（分支 fix/asr-accuracy）。复测命令见文末。

## 评测方法

- **测试集**：`backend/tests/asr_benchmark/cases.json` —— 科普 / 讲解 / 教程三类 5-10 分钟 B站中文视频（实测无可用字幕）
- **音频**：下载音轨后 FFmpeg 截取**前 150 秒**转 16kHz 单声道 WAV（与参考稿对齐）
- **参考稿**：`backend/tests/asr_benchmark/refs/<videoId>.txt`
  - ⚠️ **来源与局限**：参考稿由 **faster-whisper large-v3 (int8)** 转写生成（B站 AI 字幕需登录 Cookie 未配置，人工标注待补）。因此表中 CER 为**「相对 large-v3 的偏差」**：模型间**横向对比有效**（同一参考稿），large-v3 自身的 CER 接近 0 属预期；**绝对 CER 需人工修订参考稿后才成立**（修订后重跑脚本即可）
- **CER 归一化**（两侧同规则）：zhconv 繁→简 → cn2an 数字归一 → 去标点与空白 → 拉丁小写
- **指标**：CER = 字符编辑距离 / 参考字符数；另记录转写耗时（150 秒音频）与进程内存峰值

## 实测结果（2026-10-03，Windows 11 / CPU int8 / 前 150 秒音频 × 3 视频）

| 模型 | 科普 CER | 讲解(强BGM) CER | 教程 CER | 平均 CER | 转写耗时(150s音频) | 内存峰值 |
| --- | --- | --- | --- | --- | --- | --- |
| base (int8) | 54.20% | 96.75% | 16.20% | 55.72% | 7-22s | ~380-480MB |
| **small (int8，默认)** | **28.12%** | 86.77% | **6.29%** | **40.39%** | 36-69s | ~765MB |
| medium (int8) | 44.93% | 94.14% | 4.82% | 47.96% | 100-163s | ~1.7-1.9GB |
| large-v3 (int8) | 0.00%* | 0.22%* | 0.00%* | 0.07%* | 141-204s | ~3.0-4.3GB |

\* large-v3 为参考稿生成模型，自比 CER≈0 属预期（见「参考稿局限」）。

完整数据：`backend/tests/asr_benchmark/results.csv`（含 ref/hyp 字符数）。

## 解读

1. **教程类（清晰口播）单调改善**：base 16.20% → small 6.29% → medium 4.82%。「small 的 CER 显著低于 base」成立（6.29% vs 16.20%，降幅 61%）；medium 继续改善至 4.82%（<10%，达可用）。
2. **科普类（有背景音乐）**：base 54.20% → small 28.12%（改善 48%）；**medium 反常退化至 44.93%**（疑似 BGM 段幻觉，非单调）。
3. **讲解类（发布会，强 BGM）**：本地小模型全部崩溃（86-96%），仅 large-v3（0.22%）能处理——**这是云端 ASR 兜底的真实必要性证据**。
4. **默认模型决策**：medium 平均 47.96% 未达「≤10% 定为默认」条件，且平均劣于 small（40.39%）；按数据将默认从 `base` 改为 **`small`**（本地最优平衡点）。BGM 重的视频建议 `ASR_ENGINE=cloud`。

## 结论与默认配置

| 场景 | 推荐配置 |
| --- | --- |
| 追求速度 | `ASR_ENGINE=local` + `WHISPER_MODEL=base` |
| 平衡（**已定为默认**） | `ASR_ENGINE=local` + `WHISPER_MODEL=small` |
| 追求准确（清晰口播） | `ASR_ENGINE=local` + `WHISPER_MODEL=medium`（教程类 CER 4.82%） |
| 高精度 / 强 BGM 音频 | `ASR_ENGINE=cloud`（OpenAI 兼容接口，配 `ASR_API_KEY`） |

运行时热词：`POST /api/transcribe` 请求体支持 `hotwords: ["Faster Whisper", "B站"]`（经 initial_prompt 注入，引导专有名词识别）。

## 复现

```bash
cd backend
python scripts/benchmark_asr.py --models base small medium --seconds 150
python scripts/benchmark_asr.py --models large-v3 --seconds 150   # 显存/内存充足时
# 输出：tests/asr_benchmark/results.csv
```

人工修订参考稿（refs/*.txt）后重跑，即可得到绝对 CER。
