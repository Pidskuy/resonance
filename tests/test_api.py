"""Tests for app/api.py helpers (no sound card or window needed)."""

from app.api import format_label


def test_format_label():
    assert format_label({"format": "FLAC", "bit_depth": 24, "sample_rate": 96_000}) == "FLAC · 24/96"
    assert format_label({"format": "WAV", "bit_depth": 16, "sample_rate": 44_100}) == "WAV · 16/44.1"
    assert format_label({"format": "MP3", "bit_depth": None, "sample_rate": 44_100}) == "MP3 · 44.1 kHz"
