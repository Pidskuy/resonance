"""Try the engine's controls on a file (slice 3's "done when").

Run with:
    uv run python -m scripts.engine_demo "testdata/Metallica - Fuel.mp3" --device 1

Sequence: play 6 s → pause 2 s → seek to 1:00 while paused → resume →
play 6 s → seek to 0:30 while playing → play 4 s → stop.
Listen for clicks or dropouts at each step. Ctrl+C stops early.
"""

import argparse
import time

from app.engine.engine import Engine
from app.engine.play_file import format_time


def show(engine: Engine, label: str, seconds: float) -> None:
    """Print the position every 0.5 s for `seconds` seconds."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and engine.state != "stopped":
        print(f"  {format_time(engine.position)} / {format_time(engine.duration)}  "
              f"[{engine.state}]  {label}")
        time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo of play / pause / seek / resume.")
    parser.add_argument("file")
    parser.add_argument("--device", type=int, help="output device number (default: system output)")
    parser.add_argument("--seek", type=float, default=60, help="where to seek while paused, in seconds")
    args = parser.parse_args()

    engine = Engine(device=args.device)
    try:
        engine.play(args.file)
        if args.seek >= engine.duration:
            print(f"Note: the file is only {format_time(engine.duration)} long; try --seek 15")

        print("▶ play");                         show(engine, "playing from the start", 6)
        engine.pause();  print("⏸ pause");       show(engine, "paused: should be silent", 2)
        engine.seek(args.seek)
        print(f"⇥ seek to {format_time(args.seek)} (while paused)")
        show(engine, "still paused, position moved", 1)
        engine.resume(); print("▶ resume");      show(engine, "playing from the new spot", 6)
        engine.seek(30); print("⇤ seek to 0:30 (while playing)")
        show(engine, "playing from 0:30", 4)
    except KeyboardInterrupt:
        print("\nCtrl+C")
    finally:                                     # runs no matter what, so sound always stops
        engine.stop()
        print(f"⏹ stopped. Underruns (dropouts): {engine.underruns}")


if __name__ == "__main__":
    main()
