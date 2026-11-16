"""Tests for app/engine/ring_buffer.py."""

import numpy as np
import pytest

from app.engine.ring_buffer import RingBuffer


def frames(start: int, count: int) -> np.ndarray:
    """Easy-to-check stereo frames: 0, 1, 2, ... in both channels."""
    values = np.arange(start, start + count, dtype=np.float32)
    return np.column_stack([values, values])


def test_write_then_read_gives_same_frames():
    buf = RingBuffer(capacity=10, channels=2)
    buf.write(frames(0, 4))
    out = np.empty((4, 2), dtype=np.float32)
    assert buf.read_into(out) == 4
    assert np.array_equal(out, frames(0, 4))
    assert buf.available == 0


def test_wraps_around_the_end():
    buf = RingBuffer(capacity=5, channels=2)
    out = np.empty((3, 2), dtype=np.float32)
    buf.write(frames(0, 3))
    buf.read_into(out)                  # read position is now at slot 3
    buf.write(frames(3, 4))             # slots 3, 4, then wraps to 0, 1
    out = np.empty((4, 2), dtype=np.float32)
    assert buf.read_into(out) == 4
    assert np.array_equal(out, frames(3, 4))


def test_short_read_is_padded_with_silence():
    buf = RingBuffer(capacity=10, channels=2)
    buf.write(frames(1, 2))
    out = np.full((5, 2), 99.0, dtype=np.float32)
    assert buf.read_into(out) == 2
    assert np.array_equal(out[:2], frames(1, 2))
    assert np.all(out[2:] == 0.0)


def test_cannot_overfill():
    buf = RingBuffer(capacity=4, channels=2)
    buf.write(frames(0, 3))
    assert buf.space == 1
    with pytest.raises(ValueError):
        buf.write(frames(0, 2))


def test_clear_empties_it():
    buf = RingBuffer(capacity=10, channels=2)
    buf.write(frames(0, 6))
    buf.clear()
    assert buf.available == 0
    assert buf.space == 10


def test_truncate_takes_back_frames_written_after_a_point():
    buf = RingBuffer(capacity=10, channels=2)
    buf.write(frames(0, 3))
    mark = buf.write_count                    # the next song would start here
    buf.write(frames(100, 4))                 # read-ahead of the next song
    buf.truncate(mark)
    buf.write(frames(200, 2))                 # a different next song instead
    out = np.empty((5, 2), dtype=np.float32)
    assert buf.read_into(out) == 5
    assert np.array_equal(out, np.concatenate([frames(0, 3), frames(200, 2)]))


def test_truncate_never_takes_back_frames_already_played():
    buf = RingBuffer(capacity=10, channels=2)
    buf.write(frames(0, 6))
    buf.read_into(np.empty((4, 2), dtype=np.float32))
    buf.truncate(2)                           # asks to go back before what was played
    assert buf.write_count == buf.read_count == 4
    assert buf.available == 0
