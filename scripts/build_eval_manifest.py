"""生成 P3.5 合成评测集的噪声变体、静音样本与 manifest。

只处理 TTS 基线 WAV；真实会议录音不进入仓库。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "dataset"
AUDIO = DATASET / "audio"
MANIFEST = DATASET / "manifest.json"

REFERENCES = {
    "clean_01": "大家好，今天我们讨论实时转写和会议助手的架构设计。",
    "clean_02": "请把接口文档发到群里，下午三点前确认。",
    "technical_01": "我们用 Vue3 和 TypeScript 实现桌面端，后端是 Python 和 SQLite。",
    "numbers_01": "第一季度营收是一千二百三十四万元，同比增长百分之十五。",
    "mixed_01": "这个 API 的 P95 延迟要控制在八百毫秒以内。",
    "filler_01": "嗯，那个，我们再确认一下这个方案，好吧？",
    "long_01": (
        "今天的会议主要讨论三个议题。第一是实时转写的准确率，第二是端到端延迟，"
        "第三是会议结束后的记录整理。请各位分别说明自己负责的部分，并在周五之前给出结论。"
    ),
}

CATEGORIES = {
    "clean_01": "clean",
    "clean_02": "clean",
    "technical_01": "technical",
    "numbers_01": "numbers",
    "mixed_01": "mixed",
    "filler_01": "filler",
    "long_01": "long",
}

NOISE_CASES = [
    ("clean_01", 15),
    ("clean_01", 5),
    ("technical_01", 10),
]
SILENCE_SECONDS = 3.0


def read_pcm(path: Path) -> tuple[int, int, bytes]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2 or channels != 1:
        raise ValueError(f"expected 16-bit mono: {path}")
    return rate, channels, frames


def write_pcm(path: Path, rate: int, frames: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


def add_noise(frames: bytes, snr_db: int, seed: str) -> bytes:
    samples = list(struct.unpack(f"<{len(frames) // 2}h", frames))
    if not samples:
        return frames
    signal_rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if signal_rms == 0:
        return frames
    noise_rms = signal_rms / (10 ** (snr_db / 20))
    rng = random.Random(hashlib.sha256(seed.encode()).hexdigest())
    noisy = []
    for value in samples:
        noise = rng.gauss(0, noise_rms)
        noisy.append(max(-32768, min(32767, round(value + noise))))
    return struct.pack(f"<{len(noisy)}h", *noisy)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_entry(
    *,
    sample_id: str,
    filename: str,
    category: str,
    reference: str,
    rate: int,
    frames: int,
    conditions: dict[str, object],
    notes: str | None = None,
) -> dict[str, object]:
    path = AUDIO / filename
    entry: dict[str, object] = {
        "id": sample_id,
        "file": f"audio/{filename}",
        "category": category,
        "language": "zh-CN",
        "durationSeconds": round(frames / rate, 3),
        "sampleRate": rate,
        "channels": 1,
        "bitsPerSample": 16,
        "sha256": sha256(path),
        "reference": reference,
        "conditions": conditions,
    }
    if notes:
        entry["notes"] = notes
    return entry


def main() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []

    for sample_id, reference in REFERENCES.items():
        rate, _, frames = read_pcm(AUDIO / f"{sample_id}.wav")
        entries.append(
            sample_entry(
                sample_id=sample_id,
                filename=f"{sample_id}.wav",
                category=CATEGORIES[sample_id],
                reference=reference,
                rate=rate,
                frames=len(frames) // 2,
                conditions={"noise": "none"},
            )
        )

    for sample_id, snr in NOISE_CASES:
        rate, _, frames = read_pcm(AUDIO / f"{sample_id}.wav")
        noisy = add_noise(frames, snr, f"{sample_id}-{snr}")
        filename = f"{sample_id}_snr{snr}.wav"
        write_pcm(AUDIO / filename, rate, noisy)
        entries.append(
            sample_entry(
                sample_id=f"{sample_id}_snr{snr}",
                filename=filename,
                category="noisy",
                reference=REFERENCES[sample_id],
                rate=rate,
                frames=len(noisy) // 2,
                conditions={"noise": "white", "snrDb": snr},
                notes="deterministic additive white Gaussian noise",
            )
        )

    silence_rate = 16_000
    silence_frames = int(silence_rate * SILENCE_SECONDS)
    silence = b"\x00\x00" * silence_frames
    silence_path = AUDIO / "silence_01.wav"
    write_pcm(silence_path, silence_rate, silence)
    entries.append(
        sample_entry(
            sample_id="silence_01",
            filename="silence_01.wav",
            category="silence",
            reference="",
            rate=silence_rate,
            frames=silence_frames,
            conditions={"noise": "none", "speech": False},
            notes="no-speech control; reference is empty",
        )
    )

    manifest = {
        "version": 1,
        "name": "realtime-copilot-p35-synthetic",
        "created": "2026-11-22",
        "description": "P3.5 ASR 评测的合成 TTS 基线 + 确定性噪声/静音控制样本。",
        "privacy": "仅合成语音；不含真实会议或个人信息。真实会议录音只在本地评测，不进入仓库。",
        "license": "由 Windows System.Speech 本地合成，仅用于本项目测试。",
        "generator": {
            "clean": "scripts/build-eval-dataset.ps1 (Microsoft Huihui, 16k mono PCM16)",
            "noiseAndManifest": "scripts/build_eval_manifest.py",
        },
        "samples": entries,
        "localOnly": {
            "instruction": "把授权的真实会议录音放在 evaluation/local/，用同名 manifest 记录 reference；不得提交音频。",
            "manifestSchema": {
                "id": "string",
                "file": "local/xxx.wav",
                "category": "meeting",
                "reference": "人工校对文本",
                "consent": "记录授权来源",
            },
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST} ({len(entries)} samples)")


if __name__ == "__main__":
    main()
