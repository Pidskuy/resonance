"""Tests for app/library/tags.py, using small files made on the fly."""

from pathlib import Path

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.flac import Picture as FlacPicture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK

from app.library.tags import names_from_filename, parse_track_number, read_track

FAKE_JPEG = b"\xff\xd8\xff\xe0 not really an image"


def make_audio(path: Path, rate: int = 44_100, subtype: str | None = None) -> Path:
    sf.write(path, np.zeros((rate // 10, 2)), rate, subtype=subtype)   # 0.1 s of silence
    return path


def test_parse_track_number():
    assert parse_track_number("3") == 3
    assert parse_track_number("3/12") == 3
    assert parse_track_number("") is None
    assert parse_track_number(None) is None
    assert parse_track_number("A1") is None


def test_names_from_filename():
    assert names_from_filename(Path("Metallica - Fuel.mp3")) == ("Metallica", "Fuel")
    assert names_from_filename(Path("AC - DC - Song.flac")) == ("AC", "DC - Song")
    assert names_from_filename(Path("tone_96k_24bit.flac")) == (None, "tone 96k 24bit")


def test_flac_tags_and_cover(tmp_path):
    path = make_audio(tmp_path / "a.flac", 96_000, "PCM_24")
    f = FLAC(path)
    f["title"], f["artist"], f["album"], f["tracknumber"] = "Low Tide", "Mira Vale", "Saltwater", "4/10"
    pic = FlacPicture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", FAKE_JPEG
    f.add_picture(pic)
    f.save()

    t = read_track(path)
    assert (t.title, t.artist, t.album, t.track_no) == ("Low Tide", "Mira Vale", "Saltwater", 4)
    assert (t.format, t.sample_rate, t.bit_depth, t.channels) == ("FLAC", 96_000, 24, 2)
    assert t.picture.data == FAKE_JPEG
    assert abs(t.duration - 0.1) < 0.001


def test_mp3_id3_tags(tmp_path):
    path = make_audio(tmp_path / "b.mp3")
    tags = ID3()
    tags.add(TIT2(text="Fuel")); tags.add(TPE1(text="Metallica"))
    tags.add(TALB(text="Reload")); tags.add(TRCK(text="1"))
    tags.add(APIC(mime="image/jpeg", type=3, data=FAKE_JPEG))
    tags.save(path)

    t = read_track(path)
    assert (t.title, t.artist, t.album, t.track_no) == ("Fuel", "Metallica", "Reload", 1)
    assert t.format == "MP3" and t.bit_depth is None
    assert t.picture.mime == "image/jpeg"


def test_untagged_file_falls_back_to_filename(tmp_path):
    t = read_track(make_audio(tmp_path / "Sevendust - Denial.wav", subtype="PCM_16"))
    assert (t.title, t.artist, t.album, t.track_no) == ("Denial", "Sevendust", "Unknown album", None)
    assert t.picture is None

    t = read_track(make_audio(tmp_path / "tone_44.1k.wav"))
    assert (t.title, t.artist) == ("tone 44.1k", "Unknown artist")
