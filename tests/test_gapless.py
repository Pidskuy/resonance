"""Gapless playback, tested through the real decoder thread and audio callback,
without a sound card: the test calls the callback itself and collects every frame
it hands out, which is exactly what the speakers would have received."""

import time
from pathlib import Path

import numpy as np
import pytest
import sounddevice as sd
import soundfile as sf

from app.engine.engine import BLOCK, END_FADE, Engine
from scripts.make_test_audio import make_all


class FakeStatus:
    output_underflow = False


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.002)


def pull(engine: Engine, blocks: int | None = None, on_block=None) -> np.ndarray:
    """Call the callback like sounddevice would, and return everything it played."""
    out = []
    count = 0
    while blocks is None or count < blocks:
        # Like a real stream, give the decoder a moment to keep up (no underruns in tests).
        wait_until(lambda: engine._buffer.available >= BLOCK or engine._end_of_file)
        block = np.zeros((BLOCK, engine.channels), dtype=np.float32)
        try:
            engine._callback(block, BLOCK, None, FakeStatus())
        except sd.CallbackStop:
            out.append(block)
            break
        out.append(block)
        count += 1
        if on_block:
            on_block(count)
    return np.concatenate(out)


def read(path: Path) -> np.ndarray:
    return sf.read(path, dtype="float32", always_2d=True)[0]


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("audio")
    make_all(folder, tone_seconds=1, split_seconds=2)
    return folder


@pytest.fixture
def engine():
    e = Engine()
    yield e
    e._running = False
    if e._thread:
        e._thread.join(timeout=1)


def same_except_end(played: np.ndarray, expected: np.ndarray) -> bool:
    """Equal sample for sample, apart from the tiny fade on the very last frames."""
    n = len(expected) - END_FADE
    return np.array_equal(played[:n], expected[:n])


# ---------- the slice 7 test: part1 + part2 = the uncut tone ----------

@pytest.mark.parametrize("ext", ["flac", "wav"])
def test_split_tone_plays_back_identical_to_the_uncut_tone(engine, files, ext):
    part1, part2, full = (files / f"gapless_{n}.{ext}" for n in ("part1", "part2", "full"))
    engine._load(str(part1))
    engine.set_next(str(part2))
    played = pull(engine)
    expected = read(full)
    assert same_except_end(played, expected)          # no gap, nothing missing, nothing extra
    assert np.all(played[len(expected):] == 0)        # only silence after the end
    assert engine.track_changes == 1
    assert engine.path == str(part2)


def test_switches_song_at_exactly_the_right_frame(engine, files):
    part1, part2 = files / "gapless_part1.flac", files / "gapless_part2.flac"
    len1 = sf.info(part1).frames
    engine._load(str(part1))
    engine.set_next(str(part2))
    seen = []
    pull(engine, on_block=lambda n: seen.append((n * BLOCK, engine.track_changes, engine._frames_played)))
    # the first block that contains part2's first frame is where the switch happens
    first_after = next(s for s in seen if s[1] == 1)
    assert first_after[0] > len1 >= first_after[0] - BLOCK
    assert first_after[2] == first_after[0] - len1    # position restarts from part2's start
    assert engine.total_frames == sf.info(part2).frames


# ---------- when it can't (or shouldn't) be gapless ----------

def test_next_song_at_a_different_rate_is_not_read_ahead(engine, files):
    engine._load(str(files / "tone_44.1k_16bit.flac"))
    engine.set_next(str(files / "tone_96k_24bit.flac"))
    played = pull(engine)
    assert engine.track_changes == 0 and engine.ended
    assert same_except_end(played, read(files / "tone_44.1k_16bit.flac"))


def test_nothing_next_just_ends(engine, files):
    engine._load(str(files / "gapless_part1.wav"))
    played = pull(engine)
    assert engine.ended and engine.track_changes == 0
    assert same_except_end(played, read(files / "gapless_part1.wav"))


# ---------- the plan changes after reading ahead ----------

def test_changing_the_next_song_after_reading_ahead_plays_the_new_one(engine, files):
    a, b, c = files / "gapless_part1.flac", files / "gapless_part2.flac", files / "gapless_full.flac"
    engine._load(str(a))
    engine.set_next(str(b))
    wait_until(lambda: engine._boundary is not None)      # it has read ahead into b
    engine.set_next(str(c))                               # …but now c should come next
    wait_until(lambda: engine._next_loaded == str(c))
    played = pull(engine)
    expected = np.concatenate([read(a), read(c)])
    assert same_except_end(played, expected)
    assert engine.path == str(c)


def test_seek_while_next_song_is_read_ahead(engine, files):
    part1, part2 = files / "gapless_part1.flac", files / "gapless_part2.flac"
    engine._load(str(part1))
    engine.set_next(str(part2))
    wait_until(lambda: engine._boundary is not None)
    engine.state = "paused"                               # seek() needs a non-stopped state
    engine._silent = True
    engine.seek(0.5)
    engine._silent = False
    engine.set_next(str(part2))                # the Api re-sends the plan after a seek
    played = pull(engine)
    start = 22_050                                        # 0.5 s at 44.1 kHz
    expected = np.concatenate([read(part1)[start:], read(part2)])
    assert same_except_end(played, expected)


def test_repeat_one_loops_the_same_file_gaplessly(engine, files):
    tone = files / "gapless_part2.flac"
    engine._load(str(tone))
    engine.set_next(str(tone))
    seen = {"changes": 0}

    def like_the_api(_count):                 # after each switch, the Api says "same song next"
        if engine.track_changes != seen["changes"]:
            seen["changes"] = engine.track_changes
            engine.set_next(str(tone))

    played = pull(engine, blocks=int(sf.info(tone).frames * 2.5) // BLOCK, on_block=like_the_api)
    loop = read(tone)
    n = len(loop)
    assert np.array_equal(played[:n], loop) and np.array_equal(played[n:2 * n], loop)
    assert engine.track_changes >= 2


def test_lead_in_silence_before_the_song(engine, files):
    tone = files / "tone_48k_24bit.flac"
    engine._load(str(tone), lead_in=0.1)
    assert engine.position == 0                          # silence doesn't count as song time
    played = pull(engine)
    silence = 4_800                                      # 0.1 s at 48 kHz
    assert np.all(played[:silence] == 0)
    assert same_except_end(played[silence:], read(tone))
