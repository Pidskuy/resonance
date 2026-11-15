"""A ring buffer: fixed-size storage for audio that wraps around.

Picture a circular conveyor belt with `capacity` slots. The decoder thread
puts frames on at the write position, the audio callback takes them off at
the read position, and both wrap back to slot 0 when they reach the end.
The storage is made once and never resized, so taking frames off is fast
(important: the audio callback must never wait on slow work).

This class does NOT lock anything itself; the Engine wraps calls to it in a lock.
"""

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int, channels: int):
        self.capacity = capacity
        self._data = np.zeros((capacity, channels), dtype=np.float32)
        self._read = 0        # total frames ever taken off  (only grows)
        self._write = 0       # total frames ever put on     (only grows)
        # The slot for a counter is counter % capacity ("%" = remainder after dividing).

    @property
    def available(self) -> int:
        """Frames waiting to be played."""
        return self._write - self._read

    @property
    def space(self) -> int:
        """Frames that can still be put on."""
        return self.capacity - self.available

    def write(self, frames: np.ndarray) -> None:
        """Put frames on the belt. The caller must check `space` first."""
        n = len(frames)
        if n > self.space:
            raise ValueError(f"not enough space: {n} frames, {self.space} free")
        start = self._write % self.capacity
        first = min(n, self.capacity - start)          # how many fit before the end
        self._data[start : start + first] = frames[:first]
        self._data[: n - first] = frames[first:]       # the rest wraps to the start
        self._write += n

    def read_into(self, out: np.ndarray) -> int:
        """Copy up to len(out) frames into `out`; fill any shortfall with silence.

        Returns how many real frames were copied. Writes straight into `out`
        (the array sounddevice gave us), so no new array is created.
        """
        n = min(len(out), self.available)
        start = self._read % self.capacity
        first = min(n, self.capacity - start)
        out[:first] = self._data[start : start + first]
        out[first:n] = self._data[: n - first]
        out[n:] = 0.0                                   # not enough data → silence
        self._read += n
        return n

    def clear(self) -> None:
        """Throw away everything waiting (used when seeking)."""
        self._read = self._write
