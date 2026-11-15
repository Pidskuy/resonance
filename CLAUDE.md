# Instructions for Claude Code

## Who you're working with
The user is a Business IT student who knows **some Python but is not an expert**, and is new to JavaScript, Git and desktop apps. They're building this project to learn and as a portfolio piece. **You write the code; they learn from it.**

## The plan
`PLAN.md` holds every decision and the slice order. Follow it. If something in it seems wrong or impossible, stop and explain why in plain English before changing course. Don't silently deviate.

The visual and behavioural reference is `design/resonance-ui.html`. Match its look (colours, fonts, spacing, layout) when building `ui/`.

## How to work
1. **One slice at a time.** When the user says "start slice N", first explain in 3–6 plain-English sentences what this slice will do and which files it touches. Then build it.
2. **Explain every change before applying it.** Say what the code does and why, in plain language, no jargon without a one-line definition. Assume the user can read basic Python but not advanced patterns (threads, callbacks, decorators): explain those when they first appear.
3. **Keep changes small** so each one can be read and understood.
4. **Show how to check it works.** At the end of each slice, give the exact command(s) to run and what they should see or hear. Use the slice's "done when" in PLAN.md as the test.
5. **Commit at the end of each slice** with a clear message (e.g. `slice 2: play one file at native sample rate`), after the user confirms it works. Explain what the commit is.
6. **No LEARNING.md step.** The user chose (2026-11-15) to stop writing LEARNING.md entries after each slice. Don't ask for them. If they add one themselves and ask, check it gently.
7. **If something breaks,** explain what the error means before fixing it.

## Technical rules
- Use **uv** for Python and packages (`uv add <pkg>`, `uv run ...`). Don't use conda or global pip.
- The **audio callback must stay tiny**: no file I/O, no database, no logging, no allocation. Copy from the ring buffer, apply volume only if ≠ 1.0, done.
- Open output streams with `extra_settings=sd.CoreAudioSettings(change_device_parameters=True)` at the **file's own sample rate**. Never resample in our code.
- At volume 1.0 with EQ off, samples must pass through unchanged.
- Write pytest tests for logic that doesn't need a sound card (library scanning, queue order, gapless planning, time formatting).
- Keep `PLAN.md` up to date if a decision changes (with the user's agreement).
