"""P35-01：评测数据集 manifest、哈希与隐私边界。"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "evaluation" / "dataset"
MANIFEST = DATASET / "manifest.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_exists_and_has_samples() -> None:
    manifest = load_manifest()
    assert manifest["version"] == 1
    assert len(manifest["samples"]) >= 8
    ids = [sample["id"] for sample in manifest["samples"]]
    assert len(ids) == len(set(ids))


def test_every_sample_file_exists_and_hash_matches() -> None:
    for sample in load_manifest()["samples"]:
        path = DATASET / sample["file"]
        assert path.is_file(), path
        assert path.resolve().is_relative_to(DATASET.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sample["sha256"]


def test_audio_format_and_duration_match_manifest() -> None:
    for sample in load_manifest()["samples"]:
        with wave.open(str(DATASET / sample["file"]), "rb") as handle:
            assert handle.getframerate() == sample["sampleRate"] == 16_000
            assert handle.getnchannels() == sample["channels"] == 1
            assert handle.getsampwidth() * 8 == sample["bitsPerSample"] == 16
            assert abs(handle.getnframes() / 16_000 - sample["durationSeconds"]) < 0.01


def test_speech_samples_have_reference_and_silence_is_empty() -> None:
    for sample in load_manifest()["samples"]:
        if sample["category"] == "silence":
            assert sample["reference"] == ""
            assert sample["conditions"].get("speech") is False
        else:
            assert sample["reference"].strip()


def test_categories_cover_clean_technical_noisy_and_silence() -> None:
    categories = {sample["category"] for sample in load_manifest()["samples"]}
    assert {"clean", "technical", "numbers", "mixed", "filler", "long", "noisy", "silence"} <= categories
    noisy = [sample for sample in load_manifest()["samples"] if sample["category"] == "noisy"]
    assert {sample["conditions"]["snrDb"] for sample in noisy} >= {5, 10, 15}


def test_committed_dataset_is_synthetic_only() -> None:
    manifest = load_manifest()
    assert "synthetic" in manifest["name"]
    for sample in manifest["samples"]:
        assert sample["file"].startswith("audio/")
        assert sample["category"] != "meeting"
    assert "evaluation/local/" in manifest["localOnly"]["instruction"]
