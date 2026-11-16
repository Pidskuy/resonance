/* Resonance UI behaviour (Alpine.js).

   The page talks to Python through window.pywebview.api (see app/api.py).
   - The library (tracks, albums, artists) is loaded once and after each scan.
   - Every ~200 ms the page asks Python for the playback state and position.
   Alpine then updates every spot in the HTML that uses those values.
*/

const POLL_MS = 200;
const SCRUB_BARS = 72;

/** 125.4 → "2:05" */
function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** 3790 → "1 h 3 m", 754 → "12 m" */
function fmtLong(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} m` : `${m} m`;
}

/** 1 → "1 song", 3 → "3 songs" */
function plural(n, word) { return `${n} ${word}${n === 1 ? '' : 's'}`; }

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
    library: { folders: [], tracks: [], albums: [], artists: [] },
    byId: {},                // track id → track, for turning queue ids into songs
    queue: { up_next: [], count: 0, draggable: 0, queued: 0, from: '', shuffle: false, repeat: 'off' },   // from Python
    queueSnapshot: '',       // the last queue as text, to skip redrawing when nothing changed
    scrolled: false,         // song list scrolled past the big header → show the compact bar
    menu: null,              // the right-click menu: null (closed) or { track, x, y }
    drag: { from: null, over: null },   // which Up next song is being dragged, and over which
    scrollSpeed: 0,          // while dragging near the queue box's edge: pixels to scroll per tick
    scrollTimer: null,
    notice: '',              // short message at the bottom, e.g. "Denial will play next"
    noticeTimer: null,
    view: 'albums',          // sidebar tab: "albums" | "artists"
    selected: null,          // null = all songs, or { kind: 'album'|'artist', name }
    current: null,           // the loaded track (from Python), or null
    state: 'stopped',        // "stopped" | "playing" | "paused"  (from Python)
    position: 0,             // seconds (from Python)
    busy: false,             // true while a click is being handled by Python
    scanning: false,
    scanNote: '',            // e.g. "3 new · 0.12 s"
    arts: {},                // art_id → data URL (filled in as covers are needed)
    tab: 'now',              // right panel tab: "now" | "eq"
    scrub: scrubHeights(),
    fmt, fmtLong, plural,

    // ---- computed values ("getters") ----
    get playing() { return this.state === 'playing'; },
    get duration() { return this.current ? this.current.duration : 0; },
    get fraction() { return this.duration ? Math.min(1, this.position / this.duration) : 0; },
    get elapsed() { return fmt(this.position); },
    get remaining() { return fmt(this.duration - this.position); },
    get total() { return fmt(this.duration); },
    get rateText() {
      if (!this.current) return '';
      const ch = this.current.channels === 1 ? 'Mono' : 'Stereo';
      return `${ch} · ${this.current.sample_rate / 1000} kHz`;
    },
    get sideItems() { return this.view === 'albums' ? this.library.albums : this.library.artists; },
    get visibleTracks() {
      const s = this.selected;
      if (!s) return this.library.tracks;
      return this.library.tracks.filter(t => (s.kind === 'album' ? t.album : t.artist) === s.name);
    },
    get selectedGroup() {
      const s = this.selected;
      if (!s) return null;
      const list = s.kind === 'album' ? this.library.albums : this.library.artists;
      return list.find(x => x.name === s.name) || null;
    },
    get upNext() { return this.queue.up_next.map(id => this.byId[id]).filter(Boolean); },
    get heroEyebrow() { return !this.selected ? 'Library' : this.selected.kind === 'album' ? 'Album' : 'Artist'; },
    get heroTitle() { return this.selected ? this.selected.name : 'All songs'; },
    get heroMeta() {
      const tracks = this.visibleTracks;
      const parts = [plural(tracks.length, 'song'), fmtLong(tracks.reduce((sum, t) => sum + t.duration, 0))];
      if (this.selected && this.selected.kind === 'album' && this.selectedGroup) parts.unshift(this.selectedGroup.artist);
      return parts;
    },

    // ---- start-up: Alpine runs init() automatically ----
    async init() {
      await pythonReady();
      this.setLibrary(await window.pywebview.api.get_library());
      setInterval(() => this.poll(), POLL_MS);
    },

    setLibrary(lib) {
      this.library = lib;
      this.byId = Object.fromEntries(lib.tracks.map(t => [t.id, t]));
    },

    async poll() {
      if (this.busy) return;               // don't overwrite while a click is in progress
      const s = await window.pywebview.api.get_state();
      if (!this.busy) this.apply(s);
    },

    apply(s) {
      this.state = s.state;
      this.position = s.position;
      this.current = s.track;
      const snapshot = JSON.stringify(s.queue);
      if (snapshot !== this.queueSnapshot) {      // only redraw the queue if it changed
        this.queueSnapshot = snapshot;
        this.queue = s.queue;
      }
    },

    /** Run a Python call that changes playback, ignoring polls meanwhile. */
    async control(call) {
      this.busy = true;
      try { this.apply(await call()); }
      finally { this.busy = false; }
    },

    // ---- library actions ----
    async addFolder() { await this.scan(() => window.pywebview.api.add_folder()); },
    async rescan() { await this.scan(() => window.pywebview.api.rescan()); },

    async scan(call) {
      this.scanning = true;
      this.scanNote = 'Scanning…';
      try {
        const r = await call();
        if (r.cancelled) { this.scanNote = ''; return; }
        this.setLibrary(r.library);
        const s = r.scan;
        const parts = [['new', s.added], ['updated', s.updated], ['removed', s.removed],
                       ['unchanged', s.unchanged], ['unreadable', s.failed]]
          .filter(([, n]) => n > 0).map(([word, n]) => `${n} ${word}`);
        const time = s.seconds < 1 ? `${(s.seconds * 1000).toPrecision(2)} ms` : `${s.seconds.toFixed(1)} s`;
        this.scanNote = `${parts.join(' · ') || 'No audio files found'} · ${time}`;
      } catch (error) {
        this.scanNote = `Scan failed: ${error.message || error}`;   // show it instead of failing silently
      } finally {
        this.scanning = false;
      }
    },

    select(kind, name) { this.selected = kind ? { kind, name } : null; },

    /** The song list scrolled: close the menu, and show the compact bar once the
        big header has (almost) disappeared off the top. */
    onMainScroll(main) {
      this.menu = null;
      const hero = main.querySelector('.hero');
      this.scrolled = hero ? main.scrollTop > hero.offsetHeight - 48 : false;
    },

    /** The big Play button and the compact bar's button: pause/resume what's playing,
        or start the list shown from its first song. */
    heroPlay() {
      if (this.current && this.state !== 'stopped') return this.toggle();
      if (this.visibleTracks.length) return this.playTrack(this.visibleTracks[0].id);
    },

    /** The album to show in the table: blank for songs without an album tag. */
    albumName(track) { return track.album === 'Unknown album' ? '' : track.album; },

    /** Inline style showing a cover, fetching it from Python the first time. */
    artStyle(artId) {
      if (!artId) return '';
      if (!(artId in this.arts)) {
        this.arts[artId] = null;            // mark as "loading" so we only ask once
        window.pywebview.api.get_art(artId).then(url => { this.arts[artId] = url; });
      }
      const url = this.arts[artId];
      return url ? `background-image:url(${url});background-size:cover;background-position:center` : '';
    },

    // ---- playback actions (called from the HTML) ----
    /** Play a song, queueing the list it was clicked in (album, artist or all songs). */
    playTrack(id) {
      const context = this.visibleTracks.map(t => t.id);
      return this.control(() => window.pywebview.api.play_track(id, context, this.heroTitle));
    },
    next() { return this.control(() => window.pywebview.api.next()); },
    previous() { return this.control(() => window.pywebview.api.previous()); },
    jump(i) { return this.control(() => window.pywebview.api.jump(i)); },
    toggleShuffle() { return this.control(() => window.pywebview.api.set_shuffle(!this.queue.shuffle)); },
    cycleRepeat() { return this.control(() => window.pywebview.api.cycle_repeat()); },
    clearQueue() { return this.control(() => window.pywebview.api.clear_queue()); },
    async addToQueue(track) {
      await this.control(() => window.pywebview.api.add_to_queue(track.id));
      this.say(`${track.title} added to the queue`);
    },

    /** Open the queue menu for a song: below the + button, or at the mouse for a right-click. */
    openMenu(track, event, fromButton) {
      const MENU_W = 170, MENU_H = 90;           // roughly the menu's size, to keep it on screen
      let x = event.clientX, y = event.clientY, above = event.clientY - MENU_H;
      if (fromButton) {
        const box = event.currentTarget.getBoundingClientRect();
        x = box.right - MENU_W;
        y = box.bottom + 4;
        above = box.top - MENU_H - 4;
      }
      if (y + MENU_H > window.innerHeight - 8) y = above;       // no room below: open upwards
      x = Math.max(8, Math.min(x, window.innerWidth - MENU_W - 8));
      y = Math.max(8, Math.min(y, window.innerHeight - MENU_H - 8));
      this.menu = { track, x, y };
    },

    /** "Add to queue" in the right-click menu. */
    menuChoice() {
      const track = this.menu.track;
      this.menu = null;
      return this.addToQueue(track);
    },

    // ---- dragging songs in Up next ----
    startDrag(i, event) {
      this.drag = { from: i, over: null };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(i));    // some browsers need data to start a drag
    },
    dragOver(i) {
      if (this.drag.from !== null && i < this.queue.draggable) this.drag.over = i;
    },
    dropAt(i) {
      const from = this.drag.from;
      this.endDrag();
      if (from === null || from === i || i >= this.queue.draggable) return;
      return this.control(() => window.pywebview.api.move(from, i));
    },
    endDrag() {
      this.drag = { from: null, over: null };
      this.stopEdgeScroll();
    },

    /** While dragging: scroll the queue box when the pointer is near (or past) its top or
        bottom edge, faster the closer it gets. Browsers don't do this for boxes inside a page. */
    edgeScroll(event, box) {
      if (this.drag.from === null) return;
      const ZONE = 50, MAX = 30;                         // edge zone in px, top speed in px per tick
                                                         // (gentle at the edge, fastest past it)
      const b = box.getBoundingClientRect(), y = event.clientY;
      let speed = 0;
      if (y < b.top + ZONE) speed = -Math.min(MAX, Math.ceil((b.top + ZONE - y) / 3));
      else if (y > b.bottom - ZONE) speed = Math.min(MAX, Math.ceil((y - (b.bottom - ZONE)) / 3));
      this.scrollSpeed = speed;
      if (speed === 0) return this.stopEdgeScroll();
      if (!this.scrollTimer) {                           // ~60 nudges a second while near an edge
        this.scrollTimer = setInterval(() => { box.scrollTop += this.scrollSpeed; }, 16);
      }
    },
    stopEdgeScroll() {
      clearInterval(this.scrollTimer);
      this.scrollTimer = null;
      this.scrollSpeed = 0;
    },

    /** Show a short message for 2 seconds. */
    say(text) {
      this.notice = text;
      clearTimeout(this.noticeTimer);
      this.noticeTimer = setTimeout(() => { this.notice = ''; }, 2000);
    },
    toggle() {
      if (!this.current && this.visibleTracks.length) return this.playTrack(this.visibleTracks[0].id);
      return this.control(() => window.pywebview.api.toggle());
    },

    /** Click on a progress bar or scrubber: seek to that spot. */
    seekFrom(event) {
      if (!this.current) return;
      const box = event.currentTarget.getBoundingClientRect();
      const f = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
      this.position = f * this.duration;    // move the bar right away; Python confirms
      return this.control(() => window.pywebview.api.seek(f * this.duration));
    },
  }));
});
