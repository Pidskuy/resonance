"""The play queue: which song plays next, with shuffle, repeat and your own additions.

Plain logic, no sound and no window, so pytest can check every mode.

The idea: Up next is literally a list (`upcoming`), and Next takes the first song
off the top. Everything else just rearranges that list.

  context    the album / artist / list you clicked a song in (its original order)
  current    the song playing now
  upcoming   what plays after it, in order. This is what the Up next panel shows.
  history    songs already played (Previous goes back through these)
  cycle      with repeat on: the next full round of the context, ready in advance,
             so the panel can show it and it's exactly what will play

Each item is an Entry. `manual=True` means you put it there yourself (Add to queue,
or dragged it), so shuffle leaves it alone and new additions line up after it.
The same song may appear any number of times.
"""

import random
from dataclasses import dataclass

REPEAT_MODES = ("off", "all", "one")


@dataclass
class Entry:
    track_id: int
    context_index: int | None   # its position in the context list, or None if added by you
    manual: bool = False


class PlayQueue:
    def __init__(self, rng: random.Random | None = None):
        # rng = "random number generator". Tests pass one with a fixed seed,
        # so the "random" shuffle is the same every run and can be checked.
        self._rng = rng or random.Random()
        self.context: list[int] = []
        self.context_name = ""              # e.g. "All songs" or an album name ("Next from: …")
        self.current: Entry | None = None
        self.upcoming: list[Entry] = []
        self.history: list[Entry] = []
        self.cycle: list[Entry] = []
        self.shuffle = False
        self.repeat = "off"

    # ---------- looking ----------

    @property
    def current_id(self) -> int | None:
        return self.current.track_id if self.current else None

    def up_next(self) -> list[int]:
        """Exactly what Next will play, in order (what the panel shows)."""
        ids = [e.track_id for e in self.upcoming]
        if self.repeat != "off":
            ids += [e.track_id for e in self.cycle]
        return ids

    def peek_next(self) -> int | None:
        """The song that will play when this one ends by itself, WITHOUT moving.
        The engine reads it ahead of time for gapless playback."""
        if self.current is None:
            return None
        if self.repeat == "one":
            return self.current_id
        if self.upcoming:
            return self.upcoming[0].track_id
        if self.repeat != "off" and self.cycle:
            return self.cycle[0].track_id
        return None

    def queued_count(self) -> int:
        """How many songs at the top of Up next are yours (the "Next in queue" section)."""
        count = 0
        while count < len(self.upcoming) and self.upcoming[count].manual:
            count += 1
        return count

    # ---------- starting ----------

    def set(self, track_ids: list[int], start_id: int, name: str = "") -> None:
        """Play `start_id` from a list called `name`. Songs you added yourself stay at the top."""
        mine = [e for e in self.upcoming if e.manual]
        if self.current:
            self.history.append(self.current)
        self.context = list(track_ids)
        self.context_name = name
        start = self.context.index(start_id)
        self.current = Entry(start_id, start)
        self.upcoming = mine + self._context_after(start)
        self.cycle = self._make_cycle()

    # ---------- your own changes ----------

    def add(self, track_id: int) -> None:
        """Add a song to the top of Up next, after songs you added before it."""
        entry = Entry(track_id, None, manual=True)
        if self.current is None:              # nothing playing: it becomes the current song
            self.current = entry
            return
        self.upcoming.insert(self.queued_count(), entry)   # after your earlier additions

    def move(self, from_index: int, to_index: int) -> None:
        """Drag a song within Up next. It now counts as placed by you."""
        if not (0 <= from_index < len(self.upcoming)):
            return
        entry = self.upcoming.pop(from_index)
        entry.manual = True
        to_index = max(0, min(to_index, len(self.upcoming)))
        self.upcoming.insert(to_index, entry)

    def clear(self) -> None:
        """Empty Up next. The current song keeps playing; nothing follows it."""
        self.upcoming = []
        self.context = []
        self.context_name = ""
        self.cycle = []

    # ---------- moving ----------

    def next(self, auto: bool = False) -> int | None:
        """Move to the next song and return it, or None at the end of the queue.
        auto=True means "the song finished on its own": then repeat-one replays it."""
        if self.current is None:
            return None
        if auto and self.repeat == "one":
            return self.current_id
        if not self.upcoming:
            if self.repeat == "off" or not self.cycle:
                return None
            self.upcoming, self.cycle = self.cycle, []      # start the next round…
            self.cycle = self._make_cycle()                 # …and prepare the one after
        self.history.append(self.current)
        self.current = self.upcoming.pop(0)
        return self.current_id

    def previous(self) -> int | None:
        """Go back one song; the current one returns to the top of Up next."""
        if self.history:
            self.upcoming.insert(0, self.current)
            self.current = self.history.pop()
        return self.current_id

    def jump(self, index: int) -> int | None:
        """A click on the song at position `index` of up_next(). Songs above it are skipped."""
        if self.current is None:
            return None
        if index < len(self.upcoming):
            target = self.upcoming[index]
            self.upcoming = self.upcoming[index + 1:]
        else:
            k = index - len(self.upcoming)
            if self.repeat == "off" or k >= len(self.cycle):
                return None
            target = self.cycle[k]
            self.upcoming = self.cycle[k + 1:]
            self.cycle = self._make_cycle()
        self.history.append(self.current)
        self.current = target
        return self.current_id

    # ---------- modes ----------

    def set_shuffle(self, on: bool) -> None:
        """Shuffle (or un-shuffle) the list's songs. Songs you placed yourself stay put."""
        self.shuffle = on
        if self.current is None:
            return
        mine = [e for e in self.upcoming if e.manual]
        taken = {e.context_index for e in mine}
        if on:
            others = [Entry(t, i) for i, t in enumerate(self.context)
                      if i != self.current.context_index and i not in taken]
            self._rng.shuffle(others)
        else:
            others = [e for e in self._context_after(self._last_context_index())
                      if e.context_index not in taken]
        self.upcoming = mine + others
        self.cycle = self._make_cycle()

    def cycle_repeat(self) -> str:
        """off → all → one → off"""
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        return self.repeat

    # ---------- helpers ----------

    def _context_after(self, index: int) -> list[Entry]:
        """The list's songs after position `index` (all others, shuffled, if shuffle is on)."""
        if self.shuffle:
            others = [Entry(t, i) for i, t in enumerate(self.context) if i != index]
            self._rng.shuffle(others)
            return others
        return [Entry(t, i) for i, t in enumerate(self.context) if i > index]

    def _make_cycle(self) -> list[Entry]:
        """One full round of the list for repeat, shuffled if shuffle is on."""
        entries = [Entry(t, i) for i, t in enumerate(self.context)]
        if self.shuffle:
            self._rng.shuffle(entries)
        return entries

    def _last_context_index(self) -> int:
        """Where we are in the list: the most recent list song played (-1 if none)."""
        for entry in [self.current] + self.history[::-1]:
            if entry is not None and entry.context_index is not None:
                return entry.context_index
        return -1
