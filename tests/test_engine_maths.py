"""Tests for the position maths in app/engine/engine.py (no sound card needed)."""

from app.engine.engine import frames_to_seconds, seek_frame


def test_frames_to_seconds():
    assert frames_to_seconds(44_100, 44_100) == 1.0
    assert frames_to_seconds(2_646_000, 44_100) == 60.0       # 1:00
    assert frames_to_seconds(48_000, 96_000) == 0.5
    assert frames_to_seconds(100, 0) == 0.0                   # no file loaded


def test_seek_frame_converts_seconds():
    assert seek_frame(60, 44_100, 12_217_344) == 2_646_000
    assert seek_frame(1.5, 96_000, 10_000_000) == 144_000


def test_seek_frame_stays_inside_the_file():
    assert seek_frame(-5, 44_100, 1_000_000) == 0             # before the start → start
    assert seek_frame(999, 44_100, 1_000_000) == 1_000_000    # past the end → end
