"""ASR 准确率评测脚本（阶段二准确率专项）。

对 tests/asr_benchmark/cases.json 中的视频，用指定模型规格跑完整转写，
与人工参考稿对比 CER（字错误率），输出 CSV 与控制台对比表。

用法（在 backend/ 目录下）：
    python scripts/benchmark_asr.py --models base small medium --seconds 150
    python scripts/benchmark_asr.py --models large-v3 --seconds 150

- 参考稿：tests/asr_benchmark/refs/<videoId>.txt（首行 # 开头为来源注释，解析时跳过）
- CER 预处理：繁→简（zhconv）、中文数字归一（cn2an）、去标点与空白、拉丁小写——
  两侧同规则，消除书写差异对误差的干扰
- 音频：下载后用 FFmpeg 截取前 --seconds 秒并转 16kHz 单声道 WAV（与参考稿对齐）
- 指标：CER = 参考稿与转写的字符编辑距离 / 参考稿字符数；另记录转写耗时与内存峰值
"""
import argparse
import csv
import json
import statistics
import sys
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import cn2an  # noqa: E402
import psutil  # noqa: E402
import zhconv  # noqa: E402

from app.services.asr import LocalWhisperEngine, build_engine  # noqa: E402
from app.services.audio import download_audio  # noqa: E402
from app.services import separation  # noqa: E402

BENCHMARK_DIR = BACKEND_ROOT / "tests" / "asr_benchmark"
STRIP_CHARS = "，。！？、；：""''「」『』（）()[]{}《》<>·…—～~,.!?;:\"' \t\n\r"


def normalize_text(text: str) -> str:
    """CER 对比前的两侧统一归一化。"""
    text = zhconv.convert(text, "zh-cn")
    try:
        text = cn2an.transform(text, "smart")
    except Exception:
        pass  # 个别无法解析的写法保持原样
    text = text.lower()
    return "".join(ch for ch in text if ch not in STRIP_CHARS and not ch.isspace())


def cer(reference: str, hypothesis: str) -> float:
    """字错误率 = 字符编辑距离 / 参考字符数（归一化后）。"""
    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def load_reference(path: Path) -> str:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "".join(lines)


class MemorySampler:
    """采样当前进程 RSS 峰值（MB）。"""

    def __init__(self):
        self._proc = psutil.Process()
        self._peak = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            self._peak = max(self._peak, self._proc.memory_info().rss / 1024 / 1024)
            time.sleep(0.25)

    def __enter__(self):
        self._peak = self._proc.memory_info().rss / 1024 / 1024
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(timeout=1)

    @property
    def peak_mb(self) -> float:
        return round(self._peak, 1)


def prepare_audio(url: str, seconds: int, cache_dir: Path) -> Path:
    """下载音轨并用 FFmpeg 截取前 seconds 秒 → 16kHz 单声道 WAV（带缓存）。"""
    import subprocess

    cache_dir.mkdir(parents=True, exist_ok=True)
    source = download_audio(url, cache_dir)
    trimmed = cache_dir / f"{source.stem}-first{seconds}s-16k.wav"
    if not trimmed.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-t", str(seconds), "-ac", "1", "-ar", "16000", str(trimmed)],
            check=True,
            capture_output=True,
            timeout=600,
        )
    return trimmed


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR 准确率评测（CER）")
    parser.add_argument("--models", nargs="+", default=["base", "small", "medium"])
    parser.add_argument(
        "--engine", default="faster-whisper",
        help="faster-whisper（配合 --models 选规格）| qwen3 | funasr",
    )
    parser.add_argument("--seconds", type=int, default=150, help="评测音频时长（秒），与参考稿对齐")
    parser.add_argument("--cases", default=str(BENCHMARK_DIR / "cases.json"))
    parser.add_argument("--output", default=str(BENCHMARK_DIR / "results.csv"))
    parser.add_argument(
        "--separate", action="store_true",
        help="转写前先做 Demucs 人声分离（需 backend/requirements-separation.txt），对比分离前后 CER",
    )
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    results: list[dict] = []
    separate_seconds_by_video: dict[str, float] = {}

    def build_engine_for_run(model: str):
        if args.engine == "faster-whisper":
            return LocalWhisperEngine(model_size=model)
        return build_engine(args.engine)

    for model in args.models:
        engine = build_engine_for_run(model)
        engine_label = f"{args.engine} {model}".strip()
        print(f"\n===== {engine_label} =====", flush=True)

        for case in cases:
            reference = load_reference(BENCHMARK_DIR / case["reference"])
            cache_dir = BENCHMARK_DIR.parent.parent / ".audio-bench" / case["videoId"]

            print(f"[{engine_label} / {case['category']}] 下载与截取音频…", flush=True)
            wav = prepare_audio(case["url"], args.seconds, cache_dir)

            if args.separate:
                if case["videoId"] not in separate_seconds_by_video:
                    sep_start = time.perf_counter()
                    wav = separation.separate_vocals(wav, cache_dir / "vocal-sep")
                    separate_seconds_by_video[case["videoId"]] = round(time.perf_counter() - sep_start, 1)
                    print(
                        f"[separation] {case['videoId']} Demucs 耗时 "
                        f"{separate_seconds_by_video[case['videoId']]}s",
                        flush=True,
                    )
                else:
                    wav = separation.separate_vocals(wav, cache_dir / "vocal-sep")  # 命中缓存

            with MemorySampler() as mem:
                start = time.perf_counter()
                items = engine.transcribe(
                    wav, language="zh", initial_prompt=case.get("title", "")
                )
                elapsed = time.perf_counter() - start

            hypothesis = "".join(item["text"] for item in items)
            score = cer(reference, hypothesis)
            row = {
                "model": engine_label + ("+Demucs" if args.separate else ""),
                "video_id": case["videoId"],
                "category": case["category"],
                "cer": round(score, 4),
                "transcribe_seconds": round(elapsed, 1),
                "separate_seconds": separate_seconds_by_video.get(case["videoId"], 0.0),
                "audio_seconds": args.seconds,
                "memory_mb": mem.peak_mb,
                "ref_chars": len(normalize_text(reference)),
                "hyp_chars": len(normalize_text(hypothesis)),
            }
            results.append(row)
            print(
                f"[{model} / {case['category']}] CER={score:.2%} 耗时={elapsed:.1f}s "
                f"内存峰值={mem.peak_mb}MB",
                flush=True,
            )

    # 写 CSV
    output = Path(args.output)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV 已写入 {output}")

    # 控制台对比表
    print("\n===== 对比表（CER 越低越好）=====")
    print(f"{'model':<10} {'category':<8} {'CER':>8} {'耗时(s)':>9} {'内存(MB)':>9}")
    for row in results:
        print(
            f"{row['model']:<10} {row['category']:<8} {row['cer']:>7.2%} "
            f"{row['transcribe_seconds']:>9} {row['memory_mb']:>9}"
        )
    by_model: dict[str, list[float]] = {}
    for row in results:
        by_model.setdefault(row["model"], []).append(row["cer"])
    print("\n===== 模型平均 CER =====")
    for model, scores in by_model.items():
        print(f"{model:<10} 平均 CER = {statistics.mean(scores):.2%}")


if __name__ == "__main__":
    main()
