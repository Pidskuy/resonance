"""The music library: a SQLite database plus folder scanning.

SQLite keeps the whole database in one file. Tables are like spreadsheet tabs:
  folders  the music folders you added
  tracks   one row per audio file (tags + technical facts + when it changed)
  art      album covers, each stored once even if 12 tracks share it

Rescanning is fast because a file whose modified time and size haven't changed
is skipped without being opened.
"""

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.library.tags import AUDIO_EXTENSIONS, UNKNOWN_ALBUM, Picture, read_track

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    path TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS art (
    id   INTEGER PRIMARY KEY,
    hash TEXT UNIQUE NOT NULL,
    mime TEXT NOT NULL,
    data BLOB NOT NULL               -- BLOB = raw bytes (the image file)
);
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL,
    album       TEXT NOT NULL,
    track_no    INTEGER,
    duration    REAL NOT NULL,
    sample_rate INTEGER NOT NULL,
    bit_depth   INTEGER,
    channels    INTEGER NOT NULL,
    format      TEXT NOT NULL,
    mtime_ns    INTEGER NOT NULL,    -- file's "last modified" time, in nanoseconds
    size        INTEGER NOT NULL,    -- file size in bytes
    art_id      INTEGER REFERENCES art(id)
);
"""

TRACK_COLUMNS = ("id, path, title, artist, album, track_no, duration, sample_rate, "
                 "bit_depth, channels, format, art_id")


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0          # files that couldn't be read (damaged, unsupported…)
    seconds: float = 0.0

    def __add__(self, other: "ScanResult") -> "ScanResult":
        """Lets us write result_a + result_b to total two scans."""
        return ScanResult(self.added + other.added, self.updated + other.updated,
                          self.unchanged + other.unchanged, self.removed + other.removed,
                          self.failed + other.failed, self.seconds + other.seconds)


def find_audio_files(folder: Path) -> list[Path]:
    """Every MP3/FLAC/WAV under `folder`, including subfolders. Skips hidden files
    (names starting with "."), such as the "._song.mp3" files macOS leaves on USB drives."""
    found = []
    for directory, subdirs, files in os.walk(folder):
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]   # don't go into hidden folders
        for name in files:
            if not name.startswith(".") and Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                found.append(Path(directory) / name)
    return sorted(found)


class Library:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: pywebview calls us from several threads; our lock
        # below makes sure only one of them uses the database at a time.
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row          # rows act like dicts: row["title"]
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    # ---------- folders and scanning ----------

    def folders(self) -> list[str]:
        with self._lock:
            return [row["path"] for row in self._db.execute("SELECT path FROM folders ORDER BY path")]

    def add_folder(self, folder: Path) -> ScanResult:
        folder = folder.resolve()
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (str(folder),))
            self._db.commit()
        return self.scan(folder)

    def rescan(self) -> ScanResult:
        total = ScanResult()
        for folder in self.folders():
            total = total + self.scan(Path(folder))
        return total

    def scan(self, folder: Path) -> ScanResult:
        """Bring the database in line with what's in `folder` right now."""
        start = time.monotonic()
        result = ScanResult()
        prefix = str(folder) + os.sep
        with self._lock:
            # What the database already knows about this folder: {path: (mtime, size)}
            known = {row["path"]: (row["mtime_ns"], row["size"]) for row in self._db.execute(
                "SELECT path, mtime_ns, size FROM tracks WHERE substr(path, 1, ?) = ?",
                (len(prefix), prefix))}

            seen = set()
            for path in find_audio_files(folder):
                key = str(path)
                seen.add(key)
                stat = path.stat()
                if known.get(key) == (stat.st_mtime_ns, stat.st_size):
                    result.unchanged += 1                # same as last time: skip
                    continue
                try:
                    info = read_track(path)
                except Exception:                        # unreadable file: skip it, keep going
                    result.failed += 1
                    continue
                self._save_track(key, info, stat)
                if key in known:
                    result.updated += 1
                else:
                    result.added += 1

            # Files that were in the database but are gone from the folder
            for key in known.keys() - seen:
                self._db.execute("DELETE FROM tracks WHERE path = ?", (key,))
                result.removed += 1
            # Covers no track uses any more
            self._db.execute("DELETE FROM art WHERE id NOT IN "
                             "(SELECT art_id FROM tracks WHERE art_id IS NOT NULL)")
            self._db.commit()                            # save all changes in one go
        result.seconds = time.monotonic() - start
        return result

    def _save_track(self, path: str, info, stat) -> None:
        art_id = self._save_picture(info.picture)
        self._db.execute(
            """INSERT INTO tracks (path, title, artist, album, track_no, duration, sample_rate,
                                   bit_depth, channels, format, mtime_ns, size, art_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   title=excluded.title, artist=excluded.artist, album=excluded.album,
                   track_no=excluded.track_no, duration=excluded.duration,
                   sample_rate=excluded.sample_rate, bit_depth=excluded.bit_depth,
                   channels=excluded.channels, format=excluded.format,
                   mtime_ns=excluded.mtime_ns, size=excluded.size, art_id=excluded.art_id""",
            (path, info.title, info.artist, info.album, info.track_no, info.duration,
             info.sample_rate, info.bit_depth, info.channels, info.format,
             stat.st_mtime_ns, stat.st_size, art_id))

    def _save_picture(self, picture: Picture | None) -> int | None:
        """Store a cover once (by its fingerprint) and return its id."""
        if picture is None:
            return None
        self._db.execute("INSERT OR IGNORE INTO art (hash, mime, data) VALUES (?, ?, ?)",
                         (picture.hash, picture.mime, picture.data))
        return self._db.execute("SELECT id FROM art WHERE hash = ?", (picture.hash,)).fetchone()["id"]

    # ---------- reading the library ----------

    def tracks(self) -> list[dict]:
        """All tracks, sorted by artist, album, track number, title."""
        with self._lock:
            rows = self._db.execute(
                f"""SELECT {TRACK_COLUMNS} FROM tracks
                    ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE,
                             track_no IS NULL, track_no, title COLLATE NOCASE""")
            return [dict(row) for row in rows]

    def track(self, track_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute(f"SELECT {TRACK_COLUMNS} FROM tracks WHERE id = ?",
                                   (track_id,)).fetchone()
            return dict(row) if row else None

    def albums(self) -> list[dict]:
        """One row per album. An album with several artists shows 'Various artists'.
        Songs without an album tag aren't listed here (they're still in All songs)."""
        with self._lock:
            rows = self._db.execute(
                """SELECT album AS name,
                          CASE WHEN COUNT(DISTINCT artist) > 1 THEN 'Various artists'
                               ELSE MIN(artist) END AS artist,
                          COUNT(*) AS tracks, SUM(duration) AS duration, MIN(art_id) AS art_id
                   FROM tracks WHERE album != ?
                   GROUP BY album ORDER BY album COLLATE NOCASE""", (UNKNOWN_ALBUM,))
            return [dict(row) for row in rows]

    def artists(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """SELECT artist AS name, COUNT(*) AS tracks, COUNT(DISTINCT album) AS albums,
                          SUM(duration) AS duration, MIN(art_id) AS art_id
                   FROM tracks GROUP BY artist ORDER BY artist COLLATE NOCASE""")
            return [dict(row) for row in rows]

    def picture(self, art_id: int) -> tuple[str, bytes] | None:
        with self._lock:
            row = self._db.execute("SELECT mime, data FROM art WHERE id = ?", (art_id,)).fetchone()
            return (row["mime"], row["data"]) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()
