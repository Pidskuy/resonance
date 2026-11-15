"""Functions the UI can call.

pywebview makes every public method of Api callable from JavaScript:
    await window.pywebview.api.toggle()
Names starting with "_" stay hidden from the page.

Slice 4: one hard-coded track. The library (slice 5) and queue (slice 6) replace that.
"""

import threading
from pathlib import Path

import soundfile as sf

from app.engine.engine import Engine
from app.engine.play_file import format_label


def title_from_filename(path: Path) -> str:
    """'metallica_fuel.mp3' → 'Metallica Fuel' (until slice 5 reads real tags)."""
    return path.stem.replace("_", " ").replace("-", " ").title()


class Api:
    def __init__(self, track: Path):
        self._track = track
        self._info = sf.info(str(track))
        self._engine = Engine()          # None device = the Mac's current output
        self._lock = threading.Lock()    # one control action at a time

    def get_track(self) -> dict:
        """Facts about the current track that don't change while it plays."""
        return {
            "title": title_from_filename(self._track),
            "artist": "Unknown artist",
            "label": format_label(self._info.format, self._info.subtype, self._info.samplerate),
            "duration": self._info.duration,
            "rate": self._info.samplerate,
            "channels": self._info.channels,
        }

    def get_state(self) -> dict:
        """What's happening right now. The page asks for this ~5 times a second."""
        return {"state": self._engine.state, "position": self._engine.position}

    def toggle(self) -> dict:
        """The play/pause button: start, pause or resume depending on the state."""
        with self._lock:
            if self._engine.state == "playing":
                self._engine.pause()
            elif self._engine.state == "paused":
                self._engine.resume()
            else:
                self._engine.play(str(self._track))
        return self.get_state()

    def seek(self, seconds: float) -> dict:
        """Clicking the progress bar. Starts the track first if it isn't loaded."""
        with self._lock:
            if self._engine.state == "stopped":
                self._engine.play(str(self._track))
            self._engine.seek(float(seconds))
        return self.get_state()

    def _shutdown(self) -> None:
        """Stop the sound when the window closes (hidden from the page)."""
        with self._lock:
            self._engine.stop()
