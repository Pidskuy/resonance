"""Tests for app/library/db.py: scanning, fast rescans, albums and artists."""

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.flac import Picture as FlacPicture

import app.library.db as db_module
from app.library.db import Library, find_audio_files

COVER = b"\xff\xd8\xff\xe0 pretend jpeg"


def make_flac(path: Path, title: str, artist: str, album: str, track: str, cover: bytes | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros((4410, 2)), 44_100, subtype="PCM_16")
    f = FLAC(path)
    f["title"], f["artist"], f["album"], f["tracknumber"] = title, artist, album, track
    if cover:
        pic = FlacPicture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
        f.add_picture(pic)
    f.save()
    return path


@pytest.fixture
def music(tmp_path):
    """A small music folder: one album of 2 tracks (same cover) + one single."""
    folder = tmp_path / "Music"
    make_flac(folder / "Reload" / "01.flac", "Fuel", "Metallica", "Reload", "1", COVER)
    make_flac(folder / "Reload" / "02.flac", "The Memory Remains", "Metallica", "Reload", "2", COVER)
    make_flac(folder / "Denial.flac", "Denial", "Sevendust", "Home", "3")
    return folder


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "data" / "library.db")
    yield lib                      # the test runs here
    lib.close()


def test_finds_audio_files_but_skips_hidden_and_other_files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ["a.mp3", "sub/b.FLAC", "c.wav", "._a.mp3", "notes.txt", "cover.jpg"]:
        (tmp_path / name).write_bytes(b"")
    names = [p.name for p in find_audio_files(tmp_path)]
    assert names == ["a.mp3", "c.wav", "b.FLAC"]


def test_first_scan_adds_everything(library, music):
    result = library.add_folder(music)
    assert (result.added, result.unchanged, result.failed) == (3, 0, 0)
    assert library.folders() == [str(music.resolve())]
    titles = [t["title"] for t in library.tracks()]
    assert titles == ["Fuel", "The Memory Remains", "Denial"]    # artist, album, track order


def test_rescan_of_unchanged_folder_reads_no_files(library, music, monkeypatch):
    library.add_folder(music)
    calls = []
    real_read = db_module.read_track
    monkeypatch.setattr(db_module, "read_track", lambda p: calls.append(p) or real_read(p))
    result = library.rescan()
    assert (result.added, result.updated, result.unchanged) == (0, 0, 3)
    assert calls == []                                            # nothing was opened


def test_rescan_picks_up_changed_new_and_deleted_files(library, music):
    library.add_folder(music)
    changed = music / "Denial.flac"
    f = FLAC(changed); f["title"] = "Denial (Live)"; f.save()
    os.utime(changed, ns=(1, 1))                                  # make sure the time differs
    make_flac(music / "New.flac", "New Song", "Sevendust", "Home", "4")
    (music / "Reload" / "02.flac").unlink()

    result = library.rescan()
    assert (result.added, result.updated, result.removed, result.unchanged) == (1, 1, 1, 1)
    titles = {t["title"] for t in library.tracks()}
    assert titles == {"Fuel", "Denial (Live)", "New Song"}


def test_albums_and_artists(library, music):
    library.add_folder(music)
    albums = {a["name"]: a for a in library.albums()}
    assert albums["Reload"]["artist"] == "Metallica"
    assert albums["Reload"]["tracks"] == 2
    assert albums["Home"]["art_id"] is None
    artists = {a["name"]: a for a in library.artists()}
    assert artists["Metallica"]["albums"] == 1
    assert artists["Sevendust"]["tracks"] == 1


def test_shared_cover_is_stored_once_and_cleaned_up(library, music):
    library.add_folder(music)
    art_ids = {t["art_id"] for t in library.tracks() if t["album"] == "Reload"}
    assert len(art_ids) == 1                                      # 2 tracks, 1 stored cover
    mime, data = library.picture(art_ids.pop())
    assert (mime, data) == ("image/jpeg", COVER)

    for p in (music / "Reload").iterdir():
        p.unlink()
    library.rescan()
    assert library._db.execute("SELECT COUNT(*) FROM art").fetchone()[0] == 0
