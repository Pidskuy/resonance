"""Tests for app/api.py helpers (no sound card or window needed)."""

from pathlib import Path

from app.api import title_from_filename


def test_title_from_filename():
    assert title_from_filename(Path("testdata/metallica_fuel.mp3")) == "Metallica Fuel"
    assert title_from_filename(Path("tone_44.1k_16bit.flac")) == "Tone 44.1K 16Bit"
    assert title_from_filename(Path("Some-Song.wav")) == "Some Song"
