# Resonance: build plan

A desktop audio player for macOS that plays your local music files at their real quality (lossless and hi-res, sent to your DAC at the file's own sample rate), with gapless playback, a library, a queue and live visualizers.

This file holds every decision made during planning. If something here needs to change, change it here first, then in the code.

---

## 1. What we are building

- **Platform:** macOS desktop app (Apple Silicon). Runs fully offline.
- **Music source:** local files only. No streaming services, no accounts.
- **How music gets in:** you point the app at one or more folders. It reads the files where they are (nothing is copied), stores their info in a small local database, and you can rescan to pick up new files.
- **Formats in v1:** MP3, FLAC, WAV. ALAC (.m4a) comes later (see v2).
- **Purpose:** a real player you'll use, a portfolio piece on GitHub, and a way to learn how software is built.

### Audio quality rules (the core promise)

1. **Sample-rate matching.** When a track plays, the output device (your DAC) is switched to that track's sample rate (44.1 / 48 / 88.2 / 96 / 176.4 / 192 kHz). macOS must not resample.
   - **If the device can't run at the file's rate** (e.g. built-in speakers with a 96k or 192k file): play it anyway and let macOS convert the rate, with the bit-perfect badge **off**. Our own code still never resamples. The app checks the device's actual rate after opening the stream to know which case it's in. *(Decided 2026-11-15.)*
2. **Untouched signal.** When the volume is at 100% and the EQ is off, the samples reach the DAC unchanged.
3. **Bit-perfect badge.** A small badge next to the format label (e.g. `FLAC · 24/96`) lights up only when all three are true: device rate = file rate, volume = 100%, EQ off.
4. **Not in scope:** exclusive "hog" mode (other apps can still play sound) and DSD.

### Gapless playback (in v1)

- Two tracks in a row with the **same sample rate and channel count** play with zero gap. The stream stays open and the next track's samples follow straight on.
- When the next track has a **different sample rate**, the stream must close and reopen at the new rate. A short gap there is unavoidable (every player has it). It should be as short as possible and must not click.
- **Known risk:** MP3 files carry encoder padding (silence added at the start and end). FLAC and WAV are exactly gapless. Check whether our MP3 decoder strips that padding. If it doesn't, document it as a known limitation. Don't burn days on it.

---

## 2. Tech stack

| Part | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | The user already knows some Python |
| Env / packages | **uv** (`uv init`, `uv add`) | One tool, installs Python too, avoids conda conflicts |
| Window | **pywebview** | Shows our HTML UI in a native Mac window (WKWebView) |
| UI | **HTML + CSS + Alpine.js**, `<canvas>` for visualizers | No build step, no npm. Alpine keeps list rendering readable |
| Audio output | **sounddevice** (PortAudio) with `sd.CoreAudioSettings(change_device_parameters=True)` | Lets the app switch the DAC's sample rate on macOS |
| Decoding | **soundfile** (libsndfile): MP3, FLAC, WAV | Simple, exact sample counts (needed for gapless) |
| Tags & album art | **mutagen** | Reads ID3, FLAC/Vorbis tags, embedded pictures |
| Library database | **SQLite** (Python's built-in `sqlite3`) | One file, no server |
| Maths (FFT, waveforms, EQ later) | **numpy** (+ **scipy** for EQ in v2) | |
| Tests | **pytest** | For the non-audio logic (library, queue, gapless planning) |
| Packaging (last slice) | **PyInstaller** → `Resonance.app` | Built on your own Mac, so no "unverified developer" problem for you |

**Why not Electron:** browser audio can't switch the DAC's sample rate, which would break the core promise.

### How the pieces talk

```
┌──────────── pywebview window ─────────────┐
│  ui/index.html + Alpine.js + canvas       │
│     │  calls  window.pywebview.api.play() │
│     ▼                                     │
│  Python Api class (app/api.py)            │◄── pushes state & visualizer
└─────┬─────────────────────────────────────┘    frames via evaluate_js (~30 fps)
      │
      ├── library/   folder scan → mutagen → SQLite
      ├── queue.py   order, shuffle, repeat
      └── engine/    decoder thread → ring buffer → sounddevice callback → DAC
                                         └── analysis tap → FFT / peaks
```

**Engine rules (important for stability):**
- The sounddevice **callback must stay tiny**: copy samples out of a pre-filled ring buffer, apply volume only if volume ≠ 1.0, and nothing else. No file I/O, no database, no allocations in the callback.
- A separate **decoder thread** keeps the ring buffer full (several seconds ahead) and, near the end of a track, starts decoding the next one into the same buffer (gapless).
- Use a generous `blocksize`/`latency='high'`. Python has a GIL, so give the callback headroom.
- Visualizer data is computed in its own thread from a copy of the samples, never in the callback.

---

## 3. Project layout

```
resonance/
├── CLAUDE.md          instructions for Claude Code (read every session)
├── PLAN.md            this file
├── LEARNING.md        your own notes from slices 0–2 (optional from slice 3 on)
├── README.md
├── design/
│   └── resonance-ui.html   the Claude Design export (visual + behaviour reference)
├── app/
│   ├── main.py        starts the window
│   ├── api.py         functions the UI can call
│   ├── engine/        playback, devices, gapless, analysis
│   ├── library/       scanning, tags, database
│   └── queue.py
├── ui/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── scripts/           helper tools, not part of the app (e.g. make_test_audio.py)
├── tests/
└── testdata/          generated test tones + your own test music (never committed)
```

---

## 4. Build slices (in order)

Each slice is small, ends in something you can see or hear, and is committed to Git when its "done when" is true.

### Slice 0: Setup
Install uv, create the project, Git repo, **public GitHub repo**, `.gitignore`. Open a pywebview window that just shows `design/resonance-ui.html`.
**Done when:** `uv run python -m app.main` opens a Mac window showing the design, and the first commit is on GitHub.

### Slice 1: Make test audio
A small script generates test files with numpy + soundfile: sine tones at 44.1k/16-bit, 48k/24-bit, 96k/24-bit, 192k/24-bit (FLAC and WAV), plus **one continuous tone split across two files** (to test gapless by ear: any click or dropout = not gapless).
**Done when:** `testdata/` has the files, and you understand why the split tone is the gapless test.

### Slice 2: Play one file at native rate (no UI)
A command-line script: list output devices, pick one, play a file. The stream opens at the file's sample rate with `change_device_parameters=True`.
**Done when:** playing the 96k file makes **Audio MIDI Setup** (and your DAC's indicator) show 96 kHz, then 44.1k for the 44.1k file.
*On the MacBook Air speakers (DAC on loan):* they support 44.1/48/88.2/96 kHz, so the 96k → 44.1k test works as written. The 192k file must still play, with the script saying macOS is converting it (speakers stay at 96k). Repeat the 192k test once the DAC is back.

### Slice 3: Engine controls
Engine class with play / pause / resume / stop / seek, plus current position and duration. Decoder thread + ring buffer as described above.
**Done when:** a small test script can pause, seek to 1:00 and resume without glitches, and pytest covers the position maths.

### Slice 4: Connect UI to engine
Port the design into `ui/` using Alpine.js. Play/pause button and progress bar drive the real engine (still a hard-coded file).
**Done when:** clicking play in the window plays audio, and the progress bar and time move correctly.

### Slice 5: Library
Choose a music folder → scan → read tags and album art with mutagen → store in SQLite (path, title, artist, album, track no., duration, sample rate, bit depth, channels, format, file modified time). Rescan only re-reads changed files. Track table and Albums / Artists views show real data.
**Done when:** your test folder (and later real music) shows up correctly, and rescanning an unchanged folder is fast.

### Slice 6: Queue & transport
Up Next queue, next / previous, shuffle, repeat (off / all / one), click a track to play from there (single click: user's choice, 2026-11-15). **Add to queue** with the + on each song or by right-clicking it. Added songs go to the **top** of Up next, ahead of the playlist, in the order you added them; the same song can be added any number of times; **drag** songs in Up next to reorder them; songs you added stay on top when you start another list. *(User's requests, 2026-11-16; replaces an earlier "Play next" option.)*
*Layout (user's requests, 2026-11-16):* the right panel keeps the design's **Now playing | Equalizer** tabs, with Up next, Live and Now playing together. Up next lists the **whole** queue in its own scrolling box that takes the panel's spare height, with small "Next in queue" / "Next from: <list>" / "Then from the start" labels (modelled on Spotify). In the song list, a compact bar (play button + list name) and the column headings stick to the top while scrolling (also modelled on Spotify). Each column (library, songs, right panel) scrolls on its own.
**Done when:** the queue panel matches what actually plays next, in every mode.

### Slice 7: Gapless + rate switching
Decoder pre-loads the next track. Same rate + channels → continue in the same stream. Different rate → clean stream restart at the new rate (fade or silence-pad to avoid clicks).
**Done when:** the split test tone plays with **no audible gap**, and a 44.1k → 96k transition switches the DAC without a click.

### Slice 8: Output device, format label, volume
Output-device picker in the player bar (remembers your DAC), format label (`FLAC · 24/96`), volume slider (100% = untouched), bit-perfect badge.
**Done when:** the badge is lit at 100% volume and turns off as soon as volume drops.

### Slice 9: Visualizers
Spectrum bars and live waveform in the right panel, waveform scrubber in Now Playing (precompute each track's peaks once, cache them), peak dB readout.
**Done when:** the visuals move in time with the music and CPU use stays reasonable (check Activity Monitor).

### Slice 10: Package as a Mac app
PyInstaller → `Resonance.app` with an icon. README with screenshots and a 30-second screen recording.
**Done when:** you can double-click `Resonance.app` from Applications and it works with no Terminal open.

---

## 5. Later (v2)

- 10-band EQ with presets and preamp (scipy biquad filters, state carried between blocks); turning EQ on turns the bit-perfect badge off
- Playlists (create, rename, add/remove, reorder)
- Search with ⌘K
- ALAC (.m4a) support via PyAV
- Automatic folder watching instead of manual rescan
- Genre chips, only if your files actually have genre tags

## 6. Cut from the design (on purpose)

Discover, Radio, Account, "Download for offline" (streaming features), lyrics (no data source), BPM (gimmick, hard maths), back/forward navigation.

---

## 7. Getting music to test with

You don't have files yet. Until then, use the generated tones from slice 1. For real hi-res music, buy downloads in FLAC from stores that sell hi-res files (e.g. Qobuz, Bandcamp). Streaming apps like Spotify or Apple Music don't give you files you can use.
