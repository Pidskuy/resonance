"""Tests for scripts/make_test_audio.py. Run with:  uv run pytest"""

import numpy as np
import soundfile as sf

from scripts.make_test_audio import AMPLITUDE, TONES, make_all, sine


def test_sine_has_right_length_and_channels():
    tone = sine(440.0, 48_000, 2.0)
    assert tone.shape == (96_000, 2)           # 2 s × 48,000 frames, 2 channels
    assert np.abs(tone).max() <= AMPLITUDE


def test_tone_files_have_right_rate_and_bit_depth(tmp_path):
    make_all(tmp_path, tone_seconds=1, split_seconds=2)
    for rate, bits, _freq in TONES:
        for ext in ("flac", "wav"):
            info = sf.info(tmp_path / f"tone_{rate / 1000:g}k_{bits}bit.{ext}")
            assert info.samplerate == rate
            assert info.subtype == f"PCM_{bits}"
            assert info.channels == 2


def test_gapless_parts_join_into_exactly_the_full_tone(tmp_path):
    make_all(tmp_path, tone_seconds=1, split_seconds=2)
    for ext in ("flac", "wav"):
        # dtype="int32" reads the raw whole-number samples, so the comparison is exact
        full, _ = sf.read(tmp_path / f"gapless_full.{ext}", dtype="int32")
        part1, _ = sf.read(tmp_path / f"gapless_part1.{ext}", dtype="int32")
        part2, _ = sf.read(tmp_path / f"gapless_part2.{ext}", dtype="int32")
        joined = np.concatenate([part1, part2])
        assert np.array_equal(joined, full)


def test_cut_is_at_a_loud_point(tmp_path):
    # If the cut were where the wave crosses zero, a gap might not click.
    # At a peak, any gap is a jump from loud to silence.
    make_all(tmp_path, tone_seconds=1, split_seconds=2)
    part1, _ = sf.read(tmp_path / "gapless_part1.flac")
    assert abs(part1[-1, 0]) > 0.95 * AMPLITUDE
