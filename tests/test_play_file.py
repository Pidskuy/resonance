"""Tests for the text helpers in app/engine/play_file.py (no sound card needed)."""

from app.engine.play_file import format_label, format_time, khz


def test_format_time():
    assert format_time(0) == "0:00"
    assert format_time(5.9) == "0:05"      # partial seconds are dropped, not rounded up
    assert format_time(125.4) == "2:05"
    assert format_time(3600) == "60:00"


def test_khz():
    assert khz(44_100) == "44.1 kHz"
    assert khz(96_000) == "96 kHz"
    assert khz(176_400) == "176.4 kHz"


def test_format_label():
    assert format_label("FLAC", "PCM_24", 96_000) == "FLAC · 24/96"
    assert format_label("WAV", "PCM_16", 44_100) == "WAV · 16/44.1"
    assert format_label("MP3", "MPEG_LAYER_III", 44_100) == "MP3 · 44.1 kHz"
