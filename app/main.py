"""Start Resonance: open a native Mac window showing the UI.

Run with:  uv run python -m app.main
"""

import os
from pathlib import Path

import webview  # the pywebview package is imported as "webview"

from app.api import Api
from app.library.db import Library

# Path(__file__) is this file (app/main.py). .parent.parent goes up two
# levels to the project folder, so the path works from any working directory.
PROJECT_DIR = Path(__file__).resolve().parent.parent
UI_FILE = PROJECT_DIR / "ui" / "index.html"

# The library database lives where Mac apps keep their data. Setting the
# RESONANCE_DB environment variable uses a different file (handy for testing).
DEFAULT_DB = Path.home() / "Library" / "Application Support" / "Resonance" / "library.db"


def main() -> None:
    db_path = Path(os.environ.get("RESONANCE_DB", DEFAULT_DB))
    api = Api(Library(db_path))
    window = webview.create_window(
        title="Resonance",
        url=str(UI_FILE),
        js_api=api,                  # makes api's public methods callable from the page
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#141210",  # matches --bg in the design, so no white flash
    )
    api._window = window             # the Api needs the window to show the folder picker
    # http_server=True: pywebview serves ui/ through a tiny local web server, so the
    # page can load its own files. It's only reachable from this Mac.
    webview.start(http_server=True)
    api._shutdown()                  # window closed → stop the sound, close the database


# This block only runs when the file is started directly (not when imported).
if __name__ == "__main__":
    main()
