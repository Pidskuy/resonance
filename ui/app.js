/* Resonance UI behaviour (Alpine.js).

   The page talks to Python through window.pywebview.api (see app/api.py).
   Every ~200 ms it asks Python for the state and position; Alpine then
   updates every spot in the HTML that uses those values.
*/

const POLL_MS = 200;
const SCRUB_BARS = 72;

/** 125.4 → "2:05" */
function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The Python side is ready only after pywebview fires "pywebviewready". */
function pythonReady() {
  return new Promise(resolve => {
    if (window.pywebview && window.pywebview.api) resolve();
    else window.addEventListener('pywebviewready', resolve, { once: true });
  });
}

/** Decorative bar heights for the Now-playing scrubber (real peaks come in slice 9). */
function scrubHeights() {
  let seed = 7;
  const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
  return Array.from({ length: SCRUB_BARS }, (_, i) => {
    const env = Math.sin(Math.PI * (i + 1) / (SCRUB_BARS + 1));
    return Math.round(4 + (0.2 + 0.8 * (0.45 * env + 0.55 * rnd())) * 40);
  });
}

document.addEventListener('alpine:init', () => {
  Alpine.data('player', () => ({
    // ---- data ----
    track: { title: '', artist: '', label: '', duration: 0, rate: 0, channels: 2 },
    state: 'stopped',        // "stopped" | "playing" | "paused"  (from Python)
    position: 0,             // seconds (from Python)
    busy: false,             // true while a click is being handled by Python
    tab: 'now',              // right panel tab: "now" | "eq"
    scrub: scrubHeights(),

    // ---- computed values ("getters") ----
    get playing() { return this.state === 'playing'; },
    get fraction() { return this.track.duration ? Math.min(1, this.position / this.track.duration) : 0; },
    get elapsed() { return fmt(this.position); },
    get remaining() { return fmt(this.track.duration - this.position); },
    get total() { return fmt(this.track.duration); },
    get rateText() {
      const ch = this.track.channels === 1 ? 'Mono' : 'Stereo';
      return this.track.rate ? `${ch} · ${this.track.rate / 1000} kHz` : '';
    },

    // ---- start-up: Alpine runs init() automatically ----
    async init() {
      await pythonReady();
      this.track = await window.pywebview.api.get_track();
      setInterval(() => this.poll(), POLL_MS);
    },

    async poll() {
      if (this.busy) return;               // don't overwrite while a click is in progress
      const s = await window.pywebview.api.get_state();
      if (!this.busy) this.apply(s);
    },

    apply(s) {
      this.state = s.state;
      this.position = s.position;
    },

    // ---- actions (called from @click in the HTML) ----
    async toggle() {
      this.busy = true;
      try { this.apply(await window.pywebview.api.toggle()); }
      finally { this.busy = false; }
    },

    /** Click on a progress bar or scrubber: seek to that spot. */
    async seekFrom(event) {
      const box = event.currentTarget.getBoundingClientRect();
      const f = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
      const seconds = f * this.track.duration;
      this.busy = true;
      this.position = seconds;             // move the bar right away; Python confirms
      try { this.apply(await window.pywebview.api.seek(seconds)); }
      finally { this.busy = false; }
    },
  }));
});
