"""PCM 电平计算。"""

from __future__ import annotations

import math
import struct

from copilot_core.audio.level import SILENCE_DBFS, peak_dbfs, rms_dbfs


def pcm(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def test_empty_input_is_silence() -> None:
    assert peak_dbfs(b"") == SILENCE_DBFS
    assert rms_dbfs(b"") == SILENCE_DBFS


def test_all_zero_is_silence() -> None:
    silence = pcm([0] * 320)
    assert peak_dbfs(silence) == SILENCE_DBFS
    assert rms_dbfs(silence) == SILENCE_DBFS


def test_full_scale_square_is_zero_dbfs() -> None:
    square = pcm([32767, -32768] * 160)
    assert peak_dbfs(square) == 0.0
    assert rms_dbfs(square) > -0.1


def test_half_amplitude_is_minus_six_db() -> None:
    half = pcm([16384] * 320)
    assert peak_dbfs(half) == -6.0


def test_sine_rms_is_about_minus_three_db() -> None:
    samples = [int(32767 * math.sin(2 * math.pi * index / 32)) for index in range(320)]
    levels = rms_dbfs(pcm(samples))
    assert -3.2 < levels < -2.8
    assert peak_dbfs(pcm(samples)) == 0.0


def test_negative_peak_is_measured_by_magnitude() -> None:
    assert peak_dbfs(pcm([-32768] * 320)) == 0.0


def test_odd_length_ignores_trailing_byte() -> None:
    data = pcm([16384] * 10) + b"\x01"
    assert peak_dbfs(data) == -6.0
