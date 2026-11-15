"""Read one audio file: its tags (mutagen) and technical facts (soundfile).

Tags are the text stored inside a music file: title, artist, album, track
number, and often a picture (the album art). MP3 and WAV files store them in
a format called ID3; FLAC files use "Vorbis comments". mutagen reads both.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import mutagen
import soundfile as sf
from mutagen.id3 import ID3

from app.engine.play_file import BIT_DEPTHS

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav"}
UNKNOWN_ARTIST = "Unknown artist"
UNKNOWN_ALBUM = "Unknown album"


@dataclass
class Picture:
    mime: str          # e.g. "image/jpeg"
    data: bytes        # the image file itself

    @property
    def hash(self) -> str:
        """A fingerprint of the image, so the same cover is stored only once."""
        return hashlib.sha1(self.data).hexdigest()


@dataclass
class TrackInfo:
    title: str
    artist: str
    album: str
    track_no: int | None
    duration: float          # seconds
    sample_rate: int
    bit_depth: int | None    # None for MP3 (it has no fixed bit depth)
    channels: int
    format: str              # "MP3", "FLAC" or "WAV"
    picture: Picture | None


def parse_track_number(text: str | None) -> int | None:
    """'3' → 3, '3/12' → 3, '' or 'A1' → None"""
    if not text:
        return None
    first = text.split("/")[0].strip()
    return int(first) if first.isdigit() else None


def names_from_filename(path: Path) -> tuple[str | None, str]:
    """'Metallica - Fuel.mp3' → ('Metallica', 'Fuel');  'tone_96k.flac' → (None, 'tone 96k')"""
    stem = path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return None, stem.replace("_", " ").strip()


def _read_tags(path: Path) -> dict:
    """Return whichever of title/artist/album/track/picture the file has."""
    try:
        f = mutagen.File(path)
    except mutagen.MutagenError:
        return {}
    if f is None or f.tags is None:
        return {}
    tags = f.tags

    if isinstance(tags, ID3):                            # MP3 and WAV
        def text(frame_id: str) -> str | None:
            frame = tags.get(frame_id)
            return str(frame.text[0]).strip() if frame and frame.text else None
        pictures = [(p.type, p.mime, p.data) for p in tags.getall("APIC")]
        found = {"title": text("TIT2"), "artist": text("TPE1"),
                 "album": text("TALB"), "track": text("TRCK")}
    else:                                                # FLAC (Vorbis comments)
        def first(key: str) -> str | None:
            values = tags.get(key)
            return values[0].strip() if values else None
        pictures = [(p.type, p.mime, p.data) for p in getattr(f, "pictures", [])]
        found = {"title": first("title"), "artist": first("artist"),
                 "album": first("album"), "track": first("tracknumber")}

    # Picture type 3 means "front cover"; prefer it, otherwise take the first one.
    pictures.sort(key=lambda p: p[0] != 3)
    if pictures:
        _type, mime, data = pictures[0]
        found["picture"] = Picture(mime or "image/jpeg", bytes(data))
    return found


def read_track(path: Path) -> TrackInfo:
    """Everything the library stores about one file."""
    info = sf.info(str(path))
    tags = _read_tags(path)
    file_artist, file_title = names_from_filename(path)
    return TrackInfo(
        title=tags.get("title") or file_title,
        artist=tags.get("artist") or file_artist or UNKNOWN_ARTIST,
        album=tags.get("album") or UNKNOWN_ALBUM,
        track_no=parse_track_number(tags.get("track")),
        duration=info.duration,
        sample_rate=info.samplerate,
        bit_depth=BIT_DEPTHS.get(info.subtype),
        channels=info.channels,
        format=info.format,
        picture=tags.get("picture"),
    )
