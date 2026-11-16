"""Functions the UI can call.

pywebview makes every public method of Api callable from JavaScript:
    await window.pywebview.api.play_track(12)
Names starting with "_" stay hidden from the page.
"""

import base64
import threading
import time
from dataclasses import asdict
from pathlib import Path

import webview

from app.engine.engine import Engine
from app.engine.play_file import khz
from app.library.db import Library
from app.queue import PlayQueue

RESTART_SECONDS = 3     # Previous after this many seconds restarts the song instead
QUEUE_SHOWN = 500       # how many upcoming songs we send to the page
RATE_CHANGE_LEAD_IN = 0.15   # seconds of silence before a song at a new sample rate,
                             # so the DAC can switch rate cleanly (no click)
_UNSENT = object()      # "nothing sent to the engine yet" (different from None = "no next song")


def format_label(track: dict) -> str:
    """{'format': 'FLAC', 'bit_depth': 24, 'sample_rate': 96000} → 'FLAC · 24/96'"""
    if track["bit_depth"] is None:
        return f"{track['format']} · {khz(track['sample_rate'])}"
    return f"{track['format']} · {track['bit_depth']}/{track['sample_rate'] / 1000:g}"


def with_label(track: dict) -> dict:
    """The track plus its format label, ready to send to the page."""
    return {**track, "label": format_label(track)}


class Api:
    def __init__(self, library: Library):
        self._library = library
        self._engine = Engine()          # None device = the Mac's current output
        self._lock = threading.Lock()    # one playback action at a time
        self._current = None             # the track dict that's loaded, or None
        self._window = None              # set by main.py; needed for the folder picker
        self._queue = PlayQueue()
        self._sent_next = _UNSENT        # the next-song path we last told the engine
        self._seen_changes = 0           # the engine's track_changes we've already handled
        # A background thread that follows the engine from song to song.
        self._running = True
        threading.Thread(target=self._watch_for_track_end, daemon=True).start()

    # ---------- library ----------

    def get_library(self) -> dict:
        return {
            "folders": self._library.folders(),
            "tracks": [with_label(t) for t in self._library.tracks()],
            "albums": self._library.albums(),
            "artists": self._library.artists(),
        }

    def add_folder(self) -> dict:
        """Show the Mac's folder picker, then scan the chosen folder."""
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {"cancelled": True}
        result = self._library.add_folder(Path(chosen[0]))
        return {"scan": asdict(result), "library": self.get_library()}

    def rescan(self) -> dict:
        result = self._library.rescan()
        return {"scan": asdict(result), "library": self.get_library()}

    def get_art(self, art_id: int) -> str | None:
        """A cover as a data URL ("data:image/jpeg;base64,...") the page can show directly."""
        found = self._library.picture(art_id)
        if found is None:
            return None
        mime, data = found
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    # ---------- playback ----------

    def get_state(self) -> dict:
        """What's happening right now. The page asks for this ~5 times a second."""
        upcoming = self._queue.up_next()
        return {
            "state": self._engine.state,
            "position": self._engine.position,
            "track": with_label(self._current) if self._current else None,
            "queue": {
                "up_next": upcoming[:QUEUE_SHOWN],    # track ids; the page has the details
                "count": len(upcoming),
                # The first `draggable` songs are this round; with repeat on, the rest
                # are the next round ("Then from the start"), which can't be dragged.
                "draggable": len(self._queue.upcoming),
                "queued": self._queue.queued_count(),    # your additions at the top ("Next in queue")
                "from": self._queue.context_name,        # "Next from: …"
                "shuffle": self._queue.shuffle,
                "repeat": self._queue.repeat,
            },
        }

    def play_track(self, track_id: int, context_ids: list[int] | None = None,
                   context_name: str = "") -> dict:
        """Play a track, queueing the list it was clicked in (album, artist, all songs)."""
        track_id = int(track_id)
        ids = [int(i) for i in context_ids] if context_ids else [track_id]
        with self._lock:
            self._queue.set(ids, track_id, str(context_name or ""))
            self._play_current()
        return self.get_state()

    def next(self) -> dict:
        with self._lock:
            if self._queue.next() is not None:
                self._play_current()
        return self.get_state()

    def previous(self) -> dict:
        """Restart the song if it's past 3 seconds, otherwise go to the previous one."""
        with self._lock:
            if self._engine.state != "stopped" and self._engine.position > RESTART_SECONDS:
                self._engine.seek(0)
                self._sync_next(force=True)
            elif self._queue.previous() is not None:
                self._play_current()
        return self.get_state()

    def jump(self, up_next_index: int) -> dict:
        """A click on a song in the Up next panel."""
        with self._lock:
            if self._queue.jump(int(up_next_index)) is not None:
                self._play_current()
        return self.get_state()

    def set_shuffle(self, on: bool) -> dict:
        with self._lock:
            self._queue.set_shuffle(bool(on))
            self._sync_next()
        return self.get_state()

    def cycle_repeat(self) -> dict:
        with self._lock:
            self._queue.cycle_repeat()
            self._sync_next()
        return self.get_state()

    def add_to_queue(self, track_id: int) -> dict:
        """+ button / right-click: add a song to the top of Up next (after earlier additions).
        With nothing playing, the song simply starts."""
        with self._lock:
            was_empty = self._queue.current is None
            self._queue.add(int(track_id))
            if was_empty:
                self._play_current()
            self._sync_next()
        return self.get_state()

    def move(self, from_index: int, to_index: int) -> dict:
        """Dragging a song within Up next."""
        with self._lock:
            self._queue.move(int(from_index), int(to_index))
            self._sync_next()
        return self.get_state()

    def clear_queue(self) -> dict:
        with self._lock:
            self._queue.clear()
            self._sync_next()
        return self.get_state()

    def toggle(self) -> dict:
        """The play/pause button: pause, resume, or restart the current track."""
        with self._lock:
            if self._engine.state == "playing":
                self._engine.pause()
            elif self._engine.state == "paused":
                self._engine.resume()
            elif self._current is not None:
                self._play_current()
        return self.get_state()

    def seek(self, seconds: float) -> dict:
        with self._lock:
            if self._current is None:
                return self.get_state()
            if self._engine.state == "stopped":
                self._play_current()
            self._engine.seek(float(seconds))
            self._sync_next(force=True)               # a seek drops the engine's read-ahead
        return self.get_state()

    # ---------- hidden helpers ----------

    def _play_current(self, lead_in: float = 0.0) -> None:
        """Play the queue's current song. Call only while holding self._lock.
        A song that can't be played (file deleted, damaged…) is skipped."""
        for _attempt in range(len(self._queue.up_next()) + 1):
            track = self._library.track(self._queue.current_id)
            if track is not None:
                try:
                    self._current = track                 # switch first, so the page never
                    self._engine.play(track["path"], lead_in)  # shows the old song while this loads
                    self._seen_changes = self._engine.track_changes
                    self._sync_next(force=True)           # a new stream starts with no plan
                    return
                except Exception:
                    pass                                  # unplayable: try the next one
            if self._queue.next() is None:
                break
        self._engine.stop()

    def _sync_next(self, force: bool = False) -> None:
        """Tell the engine which file plays after the current song, so it can read it
        early (gapless). Only sends when the answer changed, unless `force`."""
        next_id = self._queue.peek_next()
        track = self._library.track(next_id) if next_id is not None else None
        path = track["path"] if track else None
        if force or path != self._sent_next:
            self._engine.set_next(path)
            self._sent_next = path

    def _watch_for_track_end(self) -> None:
        """Runs on its own thread, following the engine from song to song:
        - a gapless switch (track_changes went up): move the queue on, show the new song;
        - the sound ended (new sample rate, or nothing followed): start the next song."""
        while self._running:
            time.sleep(0.02)
            if self._engine.track_changes != self._seen_changes:
                with self._lock:
                    self._follow_gapless_switch()
            elif self._engine.ended:
                with self._lock:
                    if self._engine.ended:                # check again now that we hold the lock
                        self._engine.ended = False
                        self._start_next_after_end()

    def _follow_gapless_switch(self) -> None:
        """The engine moved into the next song by itself; catch the queue up."""
        if self._engine.track_changes == self._seen_changes:
            return
        self._seen_changes = self._engine.track_changes
        self._queue.next(auto=True)
        track = self._library.track(self._queue.current_id)
        if track is not None and track["path"] == self._engine.path:
            self._current = track
            self._sent_next = _UNSENT                     # the engine used up its "next"
            self._sync_next()
        else:
            self._play_current()                          # out of step (rare): restart cleanly

    def _start_next_after_end(self) -> None:
        """The sound ended. Start the next song, with a moment of silence if the rate changes."""
        previous_rate = self._current["sample_rate"] if self._current else None
        if self._queue.next(auto=True) is None:
            return                                        # end of the queue
        track = self._library.track(self._queue.current_id)
        new_rate = track["sample_rate"] if track else None
        self._play_current(RATE_CHANGE_LEAD_IN if new_rate != previous_rate else 0.0)

    def _shutdown(self) -> None:
        """Stop the sound and close the database when the window closes."""
        self._running = False
        with self._lock:
            self._engine.stop()
        self._library.close()
