"""The playback engine: play / pause / resume / seek / stop, plus position.

How it works (two things running at the same time):

  decoder thread  ──puts frames on──►  RingBuffer  ──taken off by──►  audio callback ──► speakers/DAC
  (reads the file, keeps ~5 s ready)                                 (called by sounddevice every ~46 ms)

- A *thread* is a second line of work running alongside the main program.
- The *callback* is a function we give sounddevice; it calls it whenever the
  device needs the next block of sound. It must be tiny and fast: copy from
  the ring buffer, nothing else (no file reading, no printing).
- A *lock* makes sure only one of them touches the ring buffer at a time.
"""

import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.engine.ring_buffer import RingBuffer

BLOCK = 2048            # frames per callback (2048 at 44.1k ≈ 46 ms). Also the fade length.
BUFFER_SECONDS = 5      # how far ahead the decoder keeps the ring buffer filled
CHUNK = 8192            # frames the decoder reads from the file at a time
PREFILL_SECONDS = 0.5   # how much must be ready before (re)starting sound


def seek_frame(seconds: float, rate: int, total_frames: int) -> int:
    """Turn a time in seconds into a frame number, kept inside the file."""
    frame = round(seconds * rate)
    return max(0, min(frame, total_frames))


def frames_to_seconds(frames: int, rate: int) -> float:
    return frames / rate if rate else 0.0


class Engine:
    def __init__(self, device: int | None = None):
        self.device = device            # None = the system's default output
        self.state = "stopped"          # "stopped", "playing" or "paused"
        self.rate = 0
        self.channels = 0
        self.total_frames = 0
        self.underruns = 0              # times the callback ran out of data (should stay 0)
        self._file = None
        self._stream = None
        self._thread = None
        self._buffer = None
        self._lock = threading.Lock()
        self._frames_played = 0         # frame number we've sent to the device

    # ---------- information ----------

    @property
    def duration(self) -> float:
        return frames_to_seconds(self.total_frames, self.rate)

    @property
    def position(self) -> float:
        return frames_to_seconds(self._frames_played, self.rate)

    # ---------- controls (called from the main program) ----------

    def play(self, path: str) -> None:
        """Start playing a file from the beginning, at the file's own sample rate."""
        self.stop()
        self._file = sf.SoundFile(path)
        self.rate = self._file.samplerate
        self.channels = self._file.channels
        self.total_frames = self._file.frames
        self._buffer = RingBuffer(self.rate * BUFFER_SECONDS, self.channels)
        self._frames_played = 0
        self.underruns = 0
        self._end_of_file = False
        self._seek_request = None
        self._fade = None               # None, "in" or "out": applied to the next block only
        self._silent = False            # True while paused: callback sends silence

        # Fades, made once here so the callback never has to create arrays.
        # [:, None] turns the list into a column so it multiplies every channel.
        self._fade_in = np.linspace(0.0, 1.0, BLOCK, dtype=np.float32)[:, None]
        self._fade_out = self._fade_in[::-1].copy()

        # Start the decoder thread. daemon=True: it won't keep Python alive on exit.
        self._running = True
        self._thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._thread.start()
        self._wait_for_buffer()

        self._stream = sd.OutputStream(
            samplerate=self.rate,                     # the FILE's rate, never resampled by us
            channels=self.channels,
            dtype="float32",
            device=self.device,
            blocksize=BLOCK,
            latency="high",                           # extra safety margin against dropouts
            callback=self._callback,
            finished_callback=self._on_finished,
            extra_settings=sd.CoreAudioSettings(change_device_parameters=True),
        )
        self._stream.start()
        self.state = "playing"

    def pause(self) -> None:
        if self.state != "playing":
            return
        self._fade = "out"                            # callback fades the next block, then goes silent
        self._wait_until(lambda: self._silent)
        self.state = "paused"

    def resume(self) -> None:
        if self.state != "paused":
            return
        self._wait_for_buffer()
        self._fade = "in"                             # set the fade BEFORE un-silencing
        self._silent = False
        self.state = "playing"

    def seek(self, seconds: float) -> None:
        """Jump to a time. Works while playing or paused."""
        if self.state == "stopped":
            return
        was_playing = self.state == "playing"
        if was_playing:
            self.pause()                              # fade out first: no click
        done = threading.Event()                      # a flag the decoder raises when finished
        self._seek_request = (seek_frame(seconds, self.rate, self.total_frames), done)
        done.wait(timeout=2)
        if was_playing:
            self.resume()                             # fades back in

    def stop(self) -> None:
        if self.state == "playing":
            self.pause()                              # fade out first: no click
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._thread is not None:
            self._thread.join(timeout=1)              # wait for the decoder thread to end
            self._thread = None
        if self._file is not None:
            self._file.close()
            self._file = None
        self.state = "stopped"

    # ---------- the audio callback (runs on sounddevice's own thread) ----------

    def _callback(self, outdata, frames, time_info, status) -> None:
        """Fill `outdata` with the next block. Keep this tiny: no I/O, no printing."""
        if status.output_underflow:
            self.underruns += 1
        if self._silent:
            outdata.fill(0.0)
            return
        with self._lock:
            got = self._buffer.read_into(outdata)
            self._frames_played += got
        if self._fade == "out":
            outdata *= self._fade_out[:frames]        # "*=" changes outdata in place
            self._fade = None
            self._silent = True
        elif self._fade == "in":
            outdata *= self._fade_in[:frames]
            self._fade = None
        if got < frames and self._end_of_file:
            raise sd.CallbackStop                     # tells sounddevice: the track is over

    def _on_finished(self) -> None:
        """Called by sounddevice when the stream ends (track over, or stop())."""
        self.state = "stopped"

    # ---------- the decoder thread ----------

    def _decode_loop(self) -> None:
        """Keep the ring buffer topped up. Runs until stop()."""
        while self._running:
            request = self._seek_request
            if request is not None:
                target, done = request
                self._file.seek(target)
                with self._lock:
                    self._buffer.clear()
                    self._frames_played = target
                    self._end_of_file = False
                self._seek_request = None
                done.set()
                continue
            if self._end_of_file or self._buffer.space < CHUNK:
                time.sleep(0.01)                      # buffer full: rest 10 ms
                continue
            block = self._file.read(CHUNK, dtype="float32", always_2d=True)
            if len(block) > 0:
                with self._lock:
                    self._buffer.write(block)
            if len(block) < CHUNK:
                self._end_of_file = True

    # ---------- small helpers ----------

    def _wait_for_buffer(self) -> None:
        """Wait (max 3 s) until enough audio is ready, or the file has ended."""
        needed = int(self.rate * PREFILL_SECONDS)
        self._wait_until(lambda: self._buffer.available >= needed or self._end_of_file, timeout=3)

    @staticmethod
    def _wait_until(condition, timeout: float = 1.0) -> None:
        """Check `condition` every 5 ms until it's true or time runs out."""
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
