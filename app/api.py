"""Functions the UI can call.

pywebview makes every public method of Api callable from JavaScript:
    await window.pywebview.api.play_track(12)
Names starting with "_" stay hidden from the page.
"""

import base64
import threading
from dataclasses import asdict
from pathlib import Path

import webview

from app.engine.engine import Engine
from app.engine.play_file import khz
from app.library.db import Library


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
        return {
            "state": self._engine.state,
            "position": self._engine.position,
            "track": with_label(self._current) if self._current else None,
        }

    def play_track(self, track_id: int) -> dict:
        """Play a track from the library, from the start."""
        track = self._library.track(int(track_id))
        if track is None:
            return self.get_state()
        with self._lock:
            self._current = track
            self._engine.play(track["path"])
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

    def _shutdown(self) -> None:
        """Stop the sound and close the database when the window closes."""
        with self._lock:
            self._engine.stop()
        self._library.close()
