"""Start Resonance: open a native Mac window showing the UI.

Run with:  uv run python -m app.main
"""

from pathlib import Path

import webview  # the pywebview package is imported as "webview"

# Path(__file__) is this file (app/main.py). .parent.parent goes up two
# levels to the project folder, so the path works from any working directory.
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Slice 0: show the design reference as-is. Slice 4 will switch this to ui/index.html.
UI_FILE = PROJECT_DIR / "design" / "resonance-ui.html"


def main() -> None:
    webview.create_window(
        title="Resonance",
        url=UI_FILE.as_uri(),  # turns the path into a file:///... address
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#141210",  # matches --bg in the design, so no white flash
    )
    # start() opens the window and keeps the program running until it is closed.
    webview.start()


# This block only runs when the file is started directly (not when imported).
if __name__ == "__main__":
    main()
