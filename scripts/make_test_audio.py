"""Generate test audio files for Resonance into testdata/.

Run with:  uv run python -m scripts.make_test_audio

Makes:
- one sine tone per sample rate (44.1k/16-bit, 48k/24, 96k/24, 192k/24),
  each as FLAC and WAV, each at a different note so you can hear which plays;
- one continuous tone cut into two files (gapless_part1 + gapless_part2),
  plus the uncut version (gapless_full) to compare against.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "testdata"

AMPLITUDE = 0.25      # 0.25 of full scale = -12 dB: clear, but not harsh in headphones
FADE_SECONDS = 0.01   # 10 ms fade at the very start/end, so files don't click there
CHANNELS = 2          # stereo, like real music

# (sample rate in Hz, bit depth, note frequency in Hz)
TONES = [
    (44_100, 16, 440.00),   # A
    (48_000, 24, 523.25),   # C
    (96_000, 24, 659.25),   # E
    (192_000, 24, 783.99),  # G
]
TONE_SECONDS = 30

# The gapless test tone
SPLIT_RATE = 44_100
SPLIT_BITS = 16
SPLIT_FREQ = 440.0
SPLIT_SECONDS = 20

# soundfile calls bit depths "subtypes"
SUBTYPES = {16: "PCM_16", 24: "PCM_24"}


def sine(freq: float, rate: int, seconds: float) -> np.ndarray:
    """Return a stereo sine tone as a numpy array of shape (frames, channels).

    A "frame" is one moment in time: one sample for each channel.
    """
    frames = round(rate * seconds)
    t = np.arange(frames) / rate                 # the time of each frame, in seconds
    wave = AMPLITUDE * np.sin(2 * np.pi * freq * t)
    return np.column_stack([wave] * CHANNELS)    # same wave in left and right


def fade_ends(samples: np.ndarray, rate: int) -> np.ndarray:
    """Return a copy with a short fade-in at the start and fade-out at the end."""
    n = round(rate * FADE_SECONDS)
    ramp = np.linspace(0.0, 1.0, n)[:, None]     # 0 → 1, shaped to multiply every channel
    out = samples.copy()
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]                       # [::-1] reverses it: 1 → 0
    return out


def peak_near_middle(samples: np.ndarray, rate: int, freq: float) -> int:
    """Return the frame index of a wave peak near the middle of the tone.

    Cutting at a peak (the loudest point) means any gap in playback jumps
    from loud to silence: the loudest possible click, so it can't be missed.
    """
    middle = len(samples) // 2
    one_cycle = int(rate / freq) + 1
    window = np.abs(samples[middle : middle + one_cycle, 0])
    return middle + int(np.argmax(window))


def write(path: Path, samples: np.ndarray, rate: int, bits: int) -> None:
    """Save samples to path. The file type (FLAC/WAV) comes from the extension."""
    sf.write(path, samples, rate, subtype=SUBTYPES[bits])


def make_all(
    out_dir: Path = OUT_DIR,
    tone_seconds: float = TONE_SECONDS,
    split_seconds: float = SPLIT_SECONDS,
) -> list[Path]:
    """Generate every test file into out_dir and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    # 1. One tone per sample rate, as FLAC and WAV
    for rate, bits, freq in TONES:
        samples = fade_ends(sine(freq, rate, tone_seconds), rate)
        name = f"tone_{rate / 1000:g}k_{bits}bit"   # e.g. tone_44.1k_16bit
        for ext in ("flac", "wav"):
            path = out_dir / f"{name}.{ext}"
            write(path, samples, rate, bits)
            made.append(path)

    # 2. The gapless test: build ONE tone, fade only its outer ends, then cut it.
    #    No fade at the cut, so a gap between the parts will click.
    full = fade_ends(sine(SPLIT_FREQ, SPLIT_RATE, split_seconds), SPLIT_RATE)
    cut = peak_near_middle(full, SPLIT_RATE, SPLIT_FREQ)
    pieces = {"gapless_full": full, "gapless_part1": full[:cut], "gapless_part2": full[cut:]}
    for ext in ("flac", "wav"):
        for name, samples in pieces.items():
            path = out_dir / f"{name}.{ext}"
            write(path, samples, SPLIT_RATE, SPLIT_BITS)
            made.append(path)

    return made


def main() -> None:
    for path in make_all():
        size_mb = path.stat().st_size / 1_000_000
        print(f"{path.relative_to(PROJECT_DIR)}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
