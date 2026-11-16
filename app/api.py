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
        # A background thread that starts the next song when one finishes.
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
        return self.get_state()

    def cycle_repeat(self) -> dict:
        with self._lock:
            self._queue.cycle_repeat()
        return self.get_state()

    def add_to_queue(self, track_id: int) -> dict:
        """+ button / right-click: add a song to the top of Up next (after earlier additions).
        With nothing playing, the song simply starts."""
        with self._lock:
            was_empty = self._queue.current is None
            self._queue.add(int(track_id))
            if was_empty:
                self._play_current()
        return self.get_state()

    def move(self, from_index: int, to_index: int) -> dict:
        """Dragging a song within Up next."""
        with self._lock:
            self._queue.move(int(from_index), int(to_index))
        return self.get_state()

    def clear_queue(self) -> dict:
        with self._lock:
            self._queue.clear()
        return self.get_state()

    def toggle(self) -> dict:
        """The play/pause button: pause, resume, or restart the current track."""
        with self._lock:
            if self._engine.state == "playing":
                self._engine.pause()
            elif self._engine.state == "paused":
                self._engine.resume()
            elif self._current is not None:
                self._engine.play(self._current["path"])
        return self.get_state()

    def seek(self, seconds: float) -> dict:
        with self._lock:
            if self._current is None:
                return self.get_state()
            if self._engine.state == "stopped":
                self._engine.play(self._current["path"])
            self._engine.seek(float(seconds))
        return self.get_state()

    # ---------- hidden helpers ----------

    def _play_current(self) -> None:
        """Play the queue's current song. Call only while holding self._lock.
        A song that can't be played (file deleted, damaged…) is skipped."""
        for _attempt in range(len(self._queue.up_next()) + 1):
            track = self._library.track(self._queue.current_id)
            if track is not None:
                try:
                    self._current = track                 # switch first, so the page never
                    self._engine.play(track["path"])      # shows the old song while this loads
                    return
                except Exception:
                    pass                                  # unplayable: try the next one
            if self._queue.next() is None:
                break
        self._engine.stop()

    def _watch_for_track_end(self) -> None:
        """Runs on its own thread: when a song finishes by itself, start the next."""
        while self._running:
            time.sleep(0.1)
            if not self._engine.ended:
                continue
            with self._lock:
                if self._engine.ended:                    # check again now that we hold the lock
                    self._engine.ended = False
                    if self._queue.next(auto=True) is not None:
                        self._play_current()

    def _shutdown(self) -> None:
        """Stop the sound and close the database when the window closes."""
        self._running = False
        with self._lock:
            self._engine.stop()
        self._library.close()
