"""The playback engine: play / pause / resume / seek / stop, position, and gapless playback.

How it works (two things running at the same time):

  decoder thread  ──puts frames on──►  RingBuffer  ──taken off by──►  audio callback ──► speakers/DAC
  (reads the file, keeps ~5 s ready)                                 (called by sounddevice every ~46 ms)

- A *thread* is a second line of work running alongside the main program.
- The *callback* is a function we give sounddevice; it calls it whenever the
  device needs the next block of sound. It must be tiny and fast: copy from
  the ring buffer, nothing else (no file reading, no printing).
- A *lock* makes sure only one of them touches the ring buffer at a time.

Gapless: when the decoder reaches the end of a file and the next file (see
set_next) has the same sample rate and channel count, it opens it and keeps
writing into the same buffer. It notes the *boundary*: the buffer position of
the next song's first frame. The callback switches to the next song exactly when
playback passes that point. A next song with a different rate can't share the
stream: the stream ends and the next song starts a new one at its own rate.
"""

import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.engine.ring_buffer import RingBuffer

BLOCK = 2048            # frames per callback (2048 at 44.1k ≈ 46 ms). Also the pause fade length.
BUFFER_SECONDS = 5      # how far ahead the decoder keeps the ring buffer filled
CHUNK = 8192            # frames the decoder reads from the file at a time
PREFILL_SECONDS = 0.5   # how much must be ready before (re)starting sound
END_FADE = 256          # frames (~6 ms) faded at the very end of the sound, so a song that
                        # ends mid-wave can't click when the stream closes


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
        self.path = None                # the file being heard right now
        self.rate = 0
        self.channels = 0
        self.total_frames = 0
        self.underruns = 0              # times the callback ran out of data (should stay 0)
        self.ended = False              # True when the sound finished by itself (not stopped)
        self.track_changes = 0          # +1 each time playback crosses into a gapless next song
        self._file = None               # the file the decoder is reading (may be the next song)
        self._file_path = None
        self._stream = None
        self._thread = None
        self._buffer = None
        self._lock = threading.Lock()
        self._frames_played = 0         # frame number of the current song we've sent to the device
        self._end_of_file = False       # True when no more frames will come at all
        self._at_track_end = False      # the decoder finished reading the current file
        self._next_path = None          # what plays after this song (set by set_next)
        self._next_version = 0          # +1 each time set_next is called
        self._boundary = None           # buffer position where the read-ahead next song starts
        self._preloaded_version = None  # the set_next version the read-ahead was based on
        self._decided_version = None    # the set_next version that led to "nothing follows"
        self._next_total = 0            # length of the read-ahead next song, in frames
        self._next_loaded = None        # path of the read-ahead next song
        self._seek_request = None

    # ---------- information ----------

    @property
    def duration(self) -> float:
        return frames_to_seconds(self.total_frames, self.rate)

    @property
    def position(self) -> float:
        return frames_to_seconds(max(0, self._frames_played), self.rate)

    # ---------- controls (called from the main program) ----------

    def set_next(self, path: str | None) -> None:
        """Tell the engine which file plays after the current one (None = nothing).
        Safe to call at any time; the decoder picks it up."""
        self._next_path = path
        self._next_version += 1

    def play(self, path: str, lead_in: float = 0.0) -> None:
        """Start playing a file from the beginning, at the file's own sample rate.
        lead_in: seconds of silence first, giving the DAC time to switch rate cleanly."""
        self.stop()
        self._load(path, lead_in)
        self._wait_for_buffer()
        self._open_stream()

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
        """Jump to a time in the current song. Works while playing or paused."""
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
        track_over = self._buffer is not None and self._end_of_file and self._buffer.available == 0
        if self.state == "playing" and not track_over:
            self.pause()                              # fade out first: no click
                                                      # (skipped if the sound already ran out:
                                                      #  nothing is playing, so there's nothing to fade)
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

    # ---------- starting (split in two so tests can run without a sound card) ----------

    def _load(self, path: str, lead_in: float = 0.0) -> None:
        """Open the file and start the decoder thread (no sound yet)."""
        self._file = sf.SoundFile(path)
        self._file_path = self.path = path
        self.rate = self._file.samplerate
        self.channels = self._file.channels
        self.total_frames = self._file.frames
        self._buffer = RingBuffer(self.rate * BUFFER_SECONDS, self.channels)
        self._frames_played = 0
        self.underruns = 0
        self.ended = False
        self._end_of_file = False
        self._at_track_end = False
        self._boundary = None
        self._preloaded_version = None
        self._decided_version = None
        self._seek_request = None
        self.set_next(None)             # forget the old plan; the Api sets the new next song
        self._fade = None               # None, "in" or "out": applied to the next block only
        self._silent = False            # True while paused: callback sends silence

        # Fades, made once here so the callback never has to create arrays.
        # [:, None] turns the list into a column so it multiplies every channel.
        self._fade_in = np.linspace(0.0, 1.0, BLOCK, dtype=np.float32)[:, None]
        self._fade_out = self._fade_in[::-1].copy()
        self._end_fade = np.linspace(1.0, 0.0, END_FADE, dtype=np.float32)[:, None]

        if lead_in > 0:                 # silence first; the position starts counting after it
            silence = int(self.rate * lead_in)
            self._buffer.write(np.zeros((silence, self.channels), dtype=np.float32))
            self._frames_played = -silence

        # Start the decoder thread. daemon=True: it won't keep Python alive on exit.
        # Each thread gets a generation number; an older thread that is somehow still
        # running sees a newer number and quits, so two threads never read one file.
        self._running = True
        self._generation = getattr(self, "_generation", 0) + 1
        self._thread = threading.Thread(target=self._decode_loop, args=(self._generation,), daemon=True)
        self._thread.start()

    def _open_stream(self) -> None:
        """Open the output AT THE FILE'S RATE and start the sound."""
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

    # ---------- the audio callback (runs on sounddevice's own thread) ----------

    def _callback(self, outdata, frames, time_info, status) -> None:
        """Fill `outdata` with the next block. Keep this tiny: no I/O, no printing."""
        if status.output_underflow:
            self.underruns += 1
        if self._silent:
            outdata.fill(0.0)
            return
        with self._lock:
            start = self._buffer.read_count
            got = self._buffer.read_into(outdata)
            boundary = self._boundary
            if boundary is not None and start + got > boundary:
                # We just played the first frames of the read-ahead next song: switch to it.
                self._frames_played = start + got - boundary
                self._boundary = None
                self.total_frames = self._next_total
                self.path = self._next_loaded
                self.track_changes += 1               # the Api watches this to move the queue on
            else:
                self._frames_played += got
            finished = got < frames and self._end_of_file
        if self._fade == "out":
            outdata *= self._fade_out[:frames]        # "*=" changes outdata in place
            self._fade = None
            self._silent = True
        elif self._fade == "in":
            outdata *= self._fade_in[:frames]
            self._fade = None
        if finished:
            n = min(got, END_FADE)                    # tiny fade on the very last frames
            if n:
                outdata[got - n:got] *= self._end_fade[END_FADE - n:]
            self.ended = True                         # the Api watches this to start the next song
            raise sd.CallbackStop                     # tells sounddevice: the sound is over

    def _on_finished(self) -> None:
        """Called by sounddevice when the stream ends (sound over, or stop())."""
        self.state = "stopped"

    # ---------- the decoder thread ----------

    def _decode_loop(self, generation: int) -> None:
        """Keep the ring buffer topped up, and read the next song early. Runs until stop()."""
        while self._running and generation == self._generation:
            if self._seek_request is not None:
                self._do_seek()
            elif self._boundary is not None and self._preloaded_version != self._next_version:
                self._undo_preload()                  # the next song changed after we read ahead
            elif self._end_of_file and self._decided_version != self._next_version:
                self._reconsider_end()                # a next song was set while the last one ends
            elif self._at_track_end:
                if self._boundary is None:
                    self._continue_or_finish()
                else:
                    time.sleep(0.01)                  # read-ahead song already fully read: wait
            elif self._end_of_file or self._buffer.space < CHUNK:
                time.sleep(0.01)                      # buffer full: rest 10 ms
            else:
                block = self._file.read(CHUNK, dtype="float32", always_2d=True)
                if len(block) > 0:
                    with self._lock:
                        self._buffer.write(block)
                if len(block) < CHUNK:
                    self._at_track_end = True         # this file is done; decide what follows

    def _continue_or_finish(self) -> None:
        """The current file is fully read. Same rate and channels next? Read it straight on."""
        self._at_track_end = False
        version, path = self._next_version, self._next_path
        nxt = None
        if path is not None:
            try:
                nxt = sf.SoundFile(path)
            except Exception:
                nxt = None                            # unreadable: treat as "nothing follows"
            if nxt is not None and (nxt.samplerate != self.rate or nxt.channels != self.channels):
                nxt.close()                           # different rate: needs a new stream
                nxt = None
        if nxt is None:
            self._decided_version = version
            self._end_of_file = True                  # the sound ends after what's buffered
            return
        with self._lock:
            self._boundary = self._buffer.write_count # the next song's first frame goes here
            self._next_total = nxt.frames
            self._next_loaded = path
        if self._file is not None:
            self._file.close()
        self._file, self._file_path = nxt, path
        self._preloaded_version = version
        self._next_path = None              # used up: the song after THIS one is still unknown;
                                            # the Api sets it once playback reaches this song
                                            # (cleared without bumping the version on purpose,
                                            #  so this read-ahead isn't undone)

    def _undo_preload(self) -> None:
        """Take back the read-ahead of a next song that's no longer next."""
        with self._lock:
            if self._boundary is None:                # playback already reached it: too late
                return
            self._buffer.truncate(self._boundary)
            self._boundary = None
        self._file.close()
        self._file, self._file_path = None, None
        self._preloaded_version = None
        self._at_track_end = True                     # the current song is fully read; decide again

    def _reconsider_end(self) -> None:
        """We'd decided nothing follows, but set_next has been called since."""
        with self._lock:
            still_playing = self._buffer.available > 0
            if still_playing:
                self._end_of_file = False
        if still_playing:
            self._at_track_end = True                 # decide again with the new next song
        else:
            self._decided_version = self._next_version   # too late: the sound has finished

    def _do_seek(self) -> None:
        target, done = self._seek_request
        if self._file_path != self.path:              # we'd moved on to the next song's file:
            if self._file is not None:                # reopen the current song
                self._file.close()
            self._file, self._file_path = sf.SoundFile(self.path), self.path
        self._file.seek(target)
        with self._lock:
            self._buffer.clear()
            self._frames_played = target
            self._end_of_file = False
            self._boundary = None
        self._at_track_end = False
        self._preloaded_version = None
        self._decided_version = None
        self._seek_request = None
        done.set()

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
