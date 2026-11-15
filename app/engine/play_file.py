"""Play one audio file at its own sample rate (slice 2: no UI yet).

Run with:
    uv run python -m app.engine.play_file testdata/tone_96k_24bit.flac
    uv run python -m app.engine.play_file testdata/tone_96k_24bit.flac --device 1

Without --device it lists your outputs and asks which one to use.
Press Ctrl+C to stop.
"""

import argparse
import time

import soundfile as sf
import sounddevice as sd

from app.engine import coreaudio

# How soundfile names bit depths ("subtypes"). MP3 has no fixed bit depth.
BIT_DEPTHS = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32, "FLOAT": 32}


def format_time(seconds: float) -> str:
    """125.4 → '2:05'"""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def khz(rate: float) -> str:
    """44100 → '44.1 kHz', 96000 → '96 kHz'"""
    return f"{rate / 1000:g} kHz"


def format_label(file_format: str, subtype: str, rate: int) -> str:
    """('FLAC', 'PCM_24', 96000) → 'FLAC · 24/96'   ('MP3', 'MPEG_LAYER_III', 44100) → 'MP3 · 44.1 kHz'"""
    bits = BIT_DEPTHS.get(subtype)
    if bits is None:
        return f"{file_format} · {khz(rate)}"
    return f"{file_format} · {bits}/{rate / 1000:g}"


def output_devices() -> list[tuple[int, str]]:
    """(number, name) for every device that can play sound."""
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]


def choose_device() -> int:
    """List the outputs and ask which one to use (Enter = the system default)."""
    default = sd.default.device[1]               # [input, output] → take the output
    print("Output devices:")
    for number, name in output_devices():
        marker = "  (default)" if number == default else ""
        print(f"  {number}: {name}{marker}")
    answer = input(f"Device number [Enter = {default}]: ").strip()
    return int(answer) if answer else default


def main() -> None:
    parser = argparse.ArgumentParser(description="Play one audio file at its own sample rate.")
    parser.add_argument("file", help="path to an MP3, FLAC or WAV file")
    parser.add_argument("--device", type=int, help="output device number (see the list)")
    args = parser.parse_args()

    device = args.device if args.device is not None else choose_device()
    device_name = sd.query_devices(device)["name"]

    info = sf.info(args.file)
    print(f"\nFile:    {args.file}")
    print(f"Format:  {format_label(info.format, info.subtype, info.samplerate)}, "
          f"{info.channels} ch, {format_time(info.duration)}")
    rates = coreaudio.supported_rates(device_name)
    print(f"Device:  {device_name} (supports {', '.join(khz(r) for r in rates) or 'unknown rates'})")

    # Decode the whole file into memory as 32-bit floats: the format CoreAudio
    # itself uses, so 16- and 24-bit samples convert exactly, with no change.
    # (Slice 3 replaces this with a decoder thread + ring buffer.)
    samples, rate = sf.read(args.file, dtype="float32", always_2d=True)

    # Open the output AT THE FILE'S RATE and allow sounddevice to switch the
    # device to that rate. sd.play() starts playing and returns immediately.
    sd.play(
        samples,
        samplerate=rate,
        device=device,
        latency="high",
        extra_settings=sd.CoreAudioSettings(change_device_parameters=True),
    )

    time.sleep(0.3)  # give macOS a moment to finish switching the rate
    actual = coreaudio.current_rate(device_name)
    if actual is None:
        print("Rate:    couldn't read the device's rate from macOS")
    elif actual == rate:
        print(f"Rate:    ✓ native: the device is running at {khz(actual)}, same as the file")
    else:
        print(f"Rate:    ⚠ converted: the device can't run at {khz(rate)} (it's at {khz(actual)}),\n"
              f"         so macOS is converting the rate. Not bit-perfect.")

    # Show the playing time until the file ends or you press Ctrl+C.
    print()
    try:
        start = time.monotonic()                 # a clock that never jumps
        while sd.get_stream().active:
            elapsed = time.monotonic() - start
            # \r jumps back to the start of the line, so the time updates in place
            print(f"\r▶ {format_time(elapsed)} / {format_time(info.duration)}", end="", flush=True)
            time.sleep(0.25)
        print("\nDone.")
    except KeyboardInterrupt:                    # Ctrl+C
        sd.stop()
        print("\nStopped.")


if __name__ == "__main__":
    main()
