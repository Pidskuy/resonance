"""Start Resonance: open a native Mac window showing the UI.

Run with:  uv run python -m app.main                       (plays the default track)
     or:   uv run python -m app.main testdata/some_file.flac
"""

import sys
from pathlib import Path

import webview  # the pywebview package is imported as "webview"

from app.api import Api

# Path(__file__) is this file (app/main.py). .parent.parent goes up two
# levels to the project folder, so the path works from any working directory.
PROJECT_DIR = Path(__file__).resolve().parent.parent
UI_FILE = PROJECT_DIR / "ui" / "index.html"

# Slice 4 plays one fixed track. Slices 5–6 replace this with the library and queue.
DEFAULT_TRACK = PROJECT_DIR / "testdata" / "metallica_fuel.mp3"


def main() -> None:
    track = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TRACK
    if not track.exists():
        sys.exit(f"Track not found: {track}\nGive a file: uv run python -m app.main path/to/file.mp3")

    api = Api(track.resolve())
    webview.create_window(
        title="Resonance",
        url=str(UI_FILE),
        js_api=api,                  # makes api's public methods callable from the page
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#141210",  # matches --bg in the design, so no white flash
    )
    # http_server=True: pywebview serves ui/ through a tiny local web server, so the
    # page can load its own files (app.js, styles.css, fonts). It's only reachable
    # from this Mac. start() keeps running until the window is closed.
    webview.start(http_server=True)
    api._shutdown()                  # window closed → stop the sound


# This block only runs when the file is started directly (not when imported).
if __name__ == "__main__":
    main()
