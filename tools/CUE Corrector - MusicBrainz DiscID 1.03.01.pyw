# -*- coding: utf-8 -*-
"""
CUE Corrector - MusicBrainz DiscID

A small standalone Windows utility that recreates the CUE Corrector workflow used for:
  - calculating a MusicBrainz Disc ID / TOC
  - looking up MusicBrainz releases by Disc ID
  - selecting the matching release/medium
  - applying MusicBrainz album/track metadata to an existing CUE sheet
  - correcting CUE FILE names to match the actual audio files
  - saving the corrected CUE while preserving unrelated lines

No third-party Python packages are required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "CUE Corrector - MusicBrainz DiscID"
APP_VERSION = "1.3.1"
MB_BASE = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = f"DiscographyCueCorrector/{APP_VERSION} (MusicBrainz desktop metadata client)"
MB_MIN_INTERVAL = 1.10

AUDIO_EXTENSIONS = {
    ".m4a", ".mp4", ".alac", ".flac", ".wav", ".wave", ".aiff", ".aif",
    ".ape", ".wv", ".tak", ".tta", ".mp3", ".ogg", ".opus"
}

FILE_RE = re.compile(r'^\s*FILE\s+(?:"(?P<quoted>[^"]+)"|(?P<bare>.+?))\s+(?P<type>WAVE|BINARY|MOTOROLA|AIFF|MP3)\s*$', re.I)
TRACK_RE = re.compile(r'^\s*TRACK\s+(?P<num>\d{1,3})\s+(?P<mode>\S+)\s*$', re.I)
INDEX_RE = re.compile(r'^\s*INDEX\s+(?P<num>\d{2})\s+(?P<time>\d{1,3}:\d{2}:\d{2})\s*$', re.I)
EAC_TOC_RE = re.compile(
    r'^\s*(?P<track>\d+)\s*\|\s*\d+:\d{2}\.\d{2}\s*\|\s*\d+:\d{2}\.\d{2}\s*\|\s*'
    r'(?P<start>-?\d+)\s*\|\s*(?P<end>-?\d+)\s*$', re.M
)


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def cue_frames(value: str) -> int:
    mm, ss, ff = (int(x) for x in value.split(":"))
    return (mm * 60 + ss) * 75 + ff


def safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def cue_quoted(value: str) -> str:
    # CUE has no universally supported escape for a literal quote inside a quoted field.
    # CUE Corrector sanitizes metadata; using apostrophe keeps the generated CUE valid.
    return '"' + safe_text(value).replace('"', "'") + '"'


def detect_text_file(path: Path) -> tuple[str, str, str]:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig", newline
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le"), "utf-16-le", newline
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be"), "utf-16-be", newline
    try:
        return raw.decode("utf-8"), "utf-8", newline
    except UnicodeDecodeError:
        # CUE Corrector's auto-detection commonly encounters Windows ANSI/Cyrillic CUEs.
        return raw.decode("cp1251", errors="replace"), "cp1251", newline


def write_text_file(path: Path, text: str, encoding: str, newline: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    out_encoding = encoding
    try:
        encoded = normalized.encode(out_encoding)
    except UnicodeEncodeError:
        # Never replace MusicBrainz characters with '?'. Switch losslessly to UTF-8 BOM.
        out_encoding = "utf-8-sig"
        encoded = normalized.encode(out_encoding)
    path.write_bytes(encoded)
    return out_encoding


def copy_backup_once(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        return backup
    # Keep the first backup immutable; this is the original pre-tool version.
    return backup


def artist_credit_text(credit: Any) -> str:
    if not credit:
        return ""
    if isinstance(credit, str):
        return credit
    parts: list[str] = []
    if isinstance(credit, list):
        for item in credit:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            name = safe_text(item.get("name"))
            if not name:
                name = safe_text((item.get("artist") or {}).get("name"))
            parts.append(name)
            parts.append(safe_text(item.get("joinphrase")))
    return "".join(parts).strip()


def all_label_info(release: dict[str, Any]) -> tuple[str, str]:
    """Return all release labels and catalog numbers, preserving MusicBrainz order."""
    infos = release.get("label-info") or release.get("label-info-list") or []
    labels: list[str] = []
    catnos: list[str] = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        label = safe_text((info.get("label") or {}).get("name"))
        catno = safe_text(info.get("catalog-number"))
        add_unique_name(labels, label)
        add_unique_name(catnos, catno)
    return "; ".join(labels), "; ".join(catnos)


def first_label_info(release: dict[str, Any]) -> tuple[str, str]:
    """Compatibility helper for CUE metadata mapping: CUE Corrector uses the first label-info entry."""
    infos = release.get("label-info") or release.get("label-info-list") or []
    if not infos:
        return "", ""
    info = infos[0] or {}
    return safe_text((info.get("label") or {}).get("name")), safe_text(info.get("catalog-number"))


def add_unique_name(target: list[str], value: str) -> None:
    value = safe_text(value)
    if value and value.casefold() not in {x.casefold() for x in target}:
        target.append(value)


def relation_artist_name(rel: dict[str, Any]) -> str:
    target_credit = safe_text(rel.get("target-credit"))
    if target_credit:
        return target_credit
    artist = rel.get("artist") or {}
    return safe_text(artist.get("name"))


def relation_role(rel: dict[str, Any]) -> str:
    typ = safe_text(rel.get("type"))
    attrs = [safe_text(x) for x in (rel.get("attributes") or []) if safe_text(x)]
    if not attrs:
        return typ
    attr_text = " ".join(attrs)
    # Matches CUE Corrector's GetRelationArtist: vocal/instrument become attributes only;
    # all other relation types are prefixed by their attributes.
    if typ in {"vocal", "instrument"}:
        return attr_text
    return (attr_text + " " + typ).strip()


def composer_songwriter_from_recording(recording: dict[str, Any]) -> tuple[str, str]:
    """Reproduce the role test used by CUE Corrector's MusicBrainz viewer.

    Songwriter: role contains 'lyricist' OR 'writer'.
    Composer:   role contains 'composer' OR 'writer'.
    The original also follows recording -> work -> artist relations.
    """
    songwriter: list[str] = []
    composer: list[str] = []

    def consume_artist_relation(rel: dict[str, Any]) -> None:
        if not rel.get("artist"):
            return
        role = relation_role(rel).casefold()
        artist = relation_artist_name(rel)
        if not artist:
            return
        if "lyricist" in role or "writer" in role:
            add_unique_name(songwriter, artist)
        if "composer" in role or "writer" in role:
            add_unique_name(composer, artist)

    for rel in recording.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        consume_artist_relation(rel)
        work = rel.get("work")
        if isinstance(work, dict):
            for wrel in work.get("relations") or []:
                if isinstance(wrel, dict):
                    consume_artist_relation(wrel)

    return ", ".join(songwriter), ", ".join(composer)


@dataclass
class CueFileEntry:
    line_index: int
    name: str
    file_type: str


@dataclass
class CueTrack:
    number: int
    mode: str
    track_line: int
    file_name: str = ""
    index01: Optional[int] = None
    index01_line: int = -1


class CueDocument:
    def __init__(self, path: Path):
        self.path = path
        text, self.encoding, self.newline = detect_text_file(path)
        self.had_final_newline = text.endswith(("\n", "\r"))
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if self.lines and self.lines[-1] == "" and self.had_final_newline:
            self.lines.pop()
        self.file_entries: list[CueFileEntry] = []
        self.tracks: list[CueTrack] = []
        self._parse_structure()

    def _parse_structure(self) -> None:
        self.file_entries = []
        self.tracks = []
        current_file = ""
        current_track: Optional[CueTrack] = None
        for i, line in enumerate(self.lines):
            fm = FILE_RE.match(line)
            if fm:
                current_file = safe_text(fm.group("quoted") or fm.group("bare"))
                self.file_entries.append(CueFileEntry(i, current_file, fm.group("type")))
                continue
            tm = TRACK_RE.match(line)
            if tm:
                current_track = CueTrack(int(tm.group("num")), tm.group("mode"), i, current_file, None, -1)
                self.tracks.append(current_track)
                continue
            im = INDEX_RE.match(line)
            if im and current_track is not None and im.group("num") == "01":
                # FILE may legally change between TRACK and INDEX 01 (for example,
                # a separate HTOA/pregap file). INDEX 01 belongs to the FILE that
                # is active on the INDEX line, not necessarily the FILE active on
                # the TRACK line.
                current_track.file_name = current_file
                current_track.index01 = cue_frames(im.group("time"))
                current_track.index01_line = i

    @property
    def audio_tracks(self) -> list[CueTrack]:
        return [t for t in self.tracks if t.mode.upper() == "AUDIO"]

    def _key_regex(self, key: str) -> re.Pattern[str]:
        bits = [re.escape(x) for x in key.split()]
        return re.compile(r"^\s*" + r"\s+".join(bits) + r"(?:\s+|$)", re.I)

    def _album_end(self) -> int:
        for i, line in enumerate(self.lines):
            if TRACK_RE.match(line):
                return i
        return len(self.lines)

    def _album_insert_index(self) -> int:
        for i, line in enumerate(self.lines[:self._album_end()]):
            if FILE_RE.match(line) or TRACK_RE.match(line):
                return i
        return self._album_end()

    def set_album_tag(self, key: str, value: str, quoted: bool = False) -> None:
        rx = self._key_regex(key)
        end = self._album_end()
        matches = [i for i in range(end) if rx.match(self.lines[i])]
        if not value:
            for i in reversed(matches):
                del self.lines[i]
            self._parse_structure()
            return
        rendered = f"{key} {cue_quoted(value) if quoted else value}"
        if matches:
            first = matches[0]
            indent = re.match(r"^\s*", self.lines[first]).group(0)
            self.lines[first] = indent + rendered
            for i in reversed(matches[1:]):
                del self.lines[i]
        else:
            self.lines.insert(self._album_insert_index(), rendered)
        self._parse_structure()

    def _track_span(self, track_number: int) -> tuple[int, int]:
        self._parse_structure()
        selected = next((t for t in self.tracks if t.number == track_number), None)
        if selected is None:
            raise ValueError(f"Track {track_number:02d} is not present in {self.path.name}")
        start = selected.track_line
        end = len(self.lines)
        for i in range(start + 1, len(self.lines)):
            if FILE_RE.match(self.lines[i]) or TRACK_RE.match(self.lines[i]):
                end = i
                break
        return start, end

    def set_track_tag(self, track_number: int, key: str, value: str, quoted: bool = True) -> None:
        start, end = self._track_span(track_number)
        rx = self._key_regex(key)
        matches = [i for i in range(start + 1, end) if rx.match(self.lines[i])]
        if not value:
            for i in reversed(matches):
                del self.lines[i]
            self._parse_structure()
            return
        rendered_value = cue_quoted(value) if quoted else value
        rendered = f"    {key} {rendered_value}"
        if matches:
            first = matches[0]
            indent = re.match(r"^\s*", self.lines[first]).group(0) or "    "
            self.lines[first] = indent + f"{key} {rendered_value}"
            for i in reversed(matches[1:]):
                del self.lines[i]
        else:
            # Place metadata before timing/flags information.
            insert_at = end
            timing_rx = re.compile(r"^\s*(INDEX|PREGAP|POSTGAP|FLAGS|ISRC|REM\s+ISRC)\b", re.I)
            for i in range(start + 1, end):
                if timing_rx.match(self.lines[i]):
                    insert_at = i
                    break
            self.lines.insert(insert_at, rendered)
        self._parse_structure()

    def correct_file_names(self) -> tuple[int, list[str]]:
        """Match CUE FILE entries to real audio files without renaming audio.

        Exact existing names are preserved. Remaining entries are matched first by normalized
        basename, then by leading track number, and finally by natural order when counts agree.
        If mapping is ambiguous, the CUE is left unchanged for those entries and a warning is returned.
        """
        self._parse_structure()
        folder = self.path.parent
        audio_files = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in AUDIO_EXTENSIONS],
            key=lambda p: natural_key(p.name)
        )
        if not self.file_entries or not audio_files:
            return 0, []

        def norm_stem(name: str) -> str:
            stem = Path(name).stem.casefold()
            stem = re.sub(r"^\s*\d{1,3}[\s._-]+", "", stem)
            return re.sub(r"[^\w]+", "", stem, flags=re.UNICODE)

        def lead_num(name: str) -> Optional[int]:
            m = re.match(r"^\s*(\d{1,3})(?:\D|$)", Path(name).stem)
            return int(m.group(1)) if m else None

        actual_by_lower = {p.name.casefold(): p for p in audio_files}
        unused = set(audio_files)
        mapping: dict[int, Path] = {}
        unresolved: list[CueFileEntry] = []

        # 1. Exact names.
        for entry in self.file_entries:
            p = actual_by_lower.get(entry.name.casefold())
            if p is not None:
                mapping[entry.line_index] = p
                unused.discard(p)
            else:
                unresolved.append(entry)

        # 2. Unique normalized-stem match.
        still: list[CueFileEntry] = []
        for entry in unresolved:
            key = norm_stem(entry.name)
            cand = [p for p in unused if norm_stem(p.name) == key and key]
            if len(cand) == 1:
                mapping[entry.line_index] = cand[0]
                unused.discard(cand[0])
            else:
                still.append(entry)
        unresolved = still

        # 3. Unique track-number match.
        still = []
        for entry in unresolved:
            n = lead_num(entry.name)
            cand = [p for p in unused if n is not None and lead_num(p.name) == n]
            if len(cand) == 1:
                mapping[entry.line_index] = cand[0]
                unused.discard(cand[0])
            else:
                still.append(entry)
        unresolved = still

        # 4. Natural-order fallback, only when the remaining counts are identical.
        if unresolved and len(unresolved) == len(unused):
            for entry, p in zip(unresolved, sorted(unused, key=lambda x: natural_key(x.name))):
                mapping[entry.line_index] = p
            unresolved = []

        warnings: list[str] = []
        if unresolved:
            warnings.append(
                "Could not safely map FILE entries: " + ", ".join(e.name for e in unresolved)
            )

        changed = 0
        for entry in self.file_entries:
            p = mapping.get(entry.line_index)
            if p is None or p.name == entry.name:
                continue
            old = self.lines[entry.line_index]
            indent = re.match(r"^\s*", old).group(0)
            self.lines[entry.line_index] = f'{indent}FILE "{p.name}" {entry.file_type.upper()}'
            changed += 1
        self._parse_structure()
        return changed, warnings

    def save(self) -> str:
        copy_backup_once(self.path)
        text = "\n".join(self.lines)
        if self.had_final_newline:
            text += "\n"
        self.encoding = write_text_file(self.path, text, self.encoding, "\r\n")
        return self.encoding


# ---------------------------- audio duration / TOC ----------------------------


def read_uint32be(data: bytes, pos: int) -> int:
    return struct.unpack_from(">I", data, pos)[0]


def mp4_atom_iter(data: bytes, start: int, end: int) -> Iterable[tuple[str, int, int, int]]:
    pos = start
    while pos + 8 <= end:
        size = read_uint32be(data, pos)
        typ = data[pos + 4:pos + 8].decode("latin1", errors="replace")
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield typ, pos, pos + size, header
        pos += size


def mp4_media_duration(path: Path) -> tuple[int, int]:
    """Return (duration units, timescale) for the first audio track in MP4/M4A."""
    data = path.read_bytes()
    moov = next((x for x in mp4_atom_iter(data, 0, len(data)) if x[0] == "moov"), None)
    if not moov:
        raise ValueError("MP4/M4A 'moov' atom not found")
    _, ms, me, mh = moov
    for typ, ts, te, th in mp4_atom_iter(data, ms + mh, me):
        if typ != "trak":
            continue
        mdia = next((x for x in mp4_atom_iter(data, ts + th, te) if x[0] == "mdia"), None)
        if not mdia:
            continue
        _, ds, de, dh = mdia
        handler = None
        mdhd = None
        for at, aps, ape, ah in mp4_atom_iter(data, ds + dh, de):
            if at == "hdlr":
                payload = aps + ah
                if payload + 12 <= ape:
                    handler = data[payload + 8:payload + 12]
            elif at == "mdhd":
                mdhd = (aps, ape, ah)
        if handler != b"soun" or not mdhd:
            continue
        aps, ape, ah = mdhd
        p = aps + ah
        if p + 4 > ape:
            continue
        version = data[p]
        if version == 1:
            if p + 32 > ape:
                continue
            timescale = read_uint32be(data, p + 20)
            duration = struct.unpack_from(">Q", data, p + 24)[0]
        else:
            if p + 20 > ape:
                continue
            timescale = read_uint32be(data, p + 12)
            duration = read_uint32be(data, p + 16)
        if timescale > 0 and duration > 0:
            return int(duration), int(timescale)
    raise ValueError("Audio mdhd duration not found")


def flac_media_duration(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        if f.read(4) != b"fLaC":
            raise ValueError("Not FLAC")
        header = f.read(4)
        if len(header) != 4:
            raise ValueError("Invalid FLAC metadata")
        block_type = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        if block_type != 0 or length < 18:
            raise ValueError("FLAC STREAMINFO not first metadata block")
        streaminfo = f.read(length)
    packed = int.from_bytes(streaminfo[10:18], "big")
    sample_rate = packed >> 44
    total_samples = packed & ((1 << 36) - 1)
    if sample_rate <= 0 or total_samples <= 0:
        raise ValueError("Invalid FLAC STREAMINFO")
    return total_samples, sample_rate


def wav_media_duration(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        head = f.read(12)
        if len(head) != 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise ValueError("Not WAV")
        sample_rate = block_align = data_size = None
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                break
            cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            payload_pos = f.tell()
            if cid == b"fmt ":
                fmt = f.read(min(size, 40))
                if len(fmt) >= 16:
                    sample_rate = struct.unpack_from("<I", fmt, 4)[0]
                    block_align = struct.unpack_from("<H", fmt, 12)[0]
            elif cid == b"data":
                data_size = size
            f.seek(payload_pos + size + (size & 1))
            if sample_rate and block_align and data_size is not None:
                break
    if not sample_rate or not block_align or data_size is None:
        raise ValueError("WAV duration data not found")
    samples = data_size // block_align
    return samples, sample_rate


def ffprobe_media_duration(path: Path) -> tuple[int, int]:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ValueError("Unsupported audio format and ffprobe is not installed")
    proc = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or "ffprobe failed")
    seconds = float(proc.stdout.strip())
    # Millisecond timescale is enough because the final result is rounded to 1/75 second.
    return int(round(seconds * 1000)), 1000


def audio_sectors(path: Path) -> int:
    ext = path.suffix.casefold()
    if ext in {".m4a", ".mp4", ".alac"}:
        duration, scale = mp4_media_duration(path)
    elif ext == ".flac":
        duration, scale = flac_media_duration(path)
    elif ext in {".wav", ".wave"}:
        duration, scale = wav_media_duration(path)
    else:
        duration, scale = ffprobe_media_duration(path)
    return int(round(duration * 75.0 / scale))


@dataclass
class TocInfo:
    starts: list[int]
    leadout: int
    source: str

    @property
    def track_count(self) -> int:
        return len(self.starts)

    @property
    def toc_string(self) -> str:
        # CUE Corrector's MusicBrainzTOC: first track=1, last audio=N,
        # leadout and offsets include the standard +150 frame lead-in.
        return " ".join(["1", str(self.track_count), str(self.leadout + 150)] + [str(x + 150) for x in self.starts])

    @property
    def disc_id(self) -> str:
        if not (1 <= self.track_count <= 99):
            raise ValueError("MusicBrainz Disc ID requires 1-99 audio tracks")
        payload = f"{1:02X}{self.track_count:02X}{self.leadout + 150:08X}"
        payload += "".join(f"{x + 150:08X}" for x in self.starts)
        payload += "0" * ((99 - self.track_count) * 8)
        digest = hashlib.sha1(payload.encode("ascii")).digest()
        return base64.b64encode(digest).decode("ascii").replace("+", ".").replace("/", "_").replace("=", "-")


def toc_from_rip_log(folder: Path, expected_tracks: int) -> Optional[TocInfo]:
    logs = sorted(folder.glob("*.log"), key=lambda p: natural_key(p.name))
    # AudioChecker is a verification log, not a rip TOC source.
    logs = [p for p in logs if p.name.casefold() != "audiochecker.log"]
    for log in logs:
        try:
            text, _, _ = detect_text_file(log)
        except Exception:
            continue
        rows = [(int(m.group("track")), int(m.group("start")), int(m.group("end"))) for m in EAC_TOC_RE.finditer(text)]
        if not rows:
            continue
        # Use a contiguous leading audio TOC; this matches EAC/CUE Corrector sector semantics.
        rows.sort(key=lambda x: x[0])
        if expected_tracks and len(rows) < expected_tracks:
            continue
        rows = rows[:expected_tracks] if expected_tracks else rows
        if [r[0] for r in rows] != list(range(1, len(rows) + 1)):
            continue
        starts = [r[1] for r in rows]
        leadout = rows[-1][2] + 1
        if starts and starts[0] == 0 and leadout > starts[-1]:
            return TocInfo(starts, leadout, f"LOG: {log.name}")
    return None


def toc_from_cue_audio(cue: CueDocument) -> TocInfo:
    tracks = cue.audio_tracks
    if not tracks:
        raise ValueError("No AUDIO tracks in CUE")
    folder = cue.path.parent

    missing_index = [t.number for t in tracks if t.index01 is None or t.index01_line < 0]
    if missing_index:
        nums = ", ".join(f"{n:02d}" for n in missing_index)
        raise ValueError(f"AUDIO track(s) without INDEX 01: {nums}")

    # Build the physical audio-file timeline from FILE entries, not merely from the
    # FILE attached to each TRACK. This matters for CUEs where a separate pregap/
    # HTOA FILE appears before TRACK 01 INDEX 01. Only FILE entries up to the last
    # AUDIO INDEX 01 can contribute to the audio-disc TOC.
    last_audio_line = max(t.index01_line for t in tracks)
    file_names: list[str] = []
    seen: set[str] = set()
    for entry in cue.file_entries:
        if entry.line_index > last_audio_line:
            break
        if entry.file_type.upper() == "BINARY":
            continue
        key = Path(entry.name).name.casefold()
        if key and key not in seen:
            seen.add(key)
            file_names.append(entry.name)
    if not file_names:
        raise ValueError("CUE has no usable audio FILE entries")

    actual = {p.name.casefold(): p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in AUDIO_EXTENSIONS}
    measured: dict[str, int] = {}
    canonical_key: dict[str, str] = {}
    for name in file_names:
        key = Path(name).name.casefold()
        p = actual.get(key)
        if p is None:
            raise ValueError(f"Audio file referenced by CUE not found after filename reconciliation: {name}")
        measured[key] = audio_sectors(p)
        canonical_key[name] = key

    base: dict[str, int] = {}
    cursor = 0
    for name in file_names:
        key = canonical_key[name]
        base[key] = cursor
        cursor += measured[key]

    starts: list[int] = []
    for t in tracks:
        if not t.file_name:
            raise ValueError(f"Track {t.number:02d} has no FILE active at INDEX 01")
        key = Path(t.file_name).name.casefold()
        if key not in base:
            raise ValueError(f"Track {t.number:02d} FILE is outside the usable audio timeline: {t.file_name}")
        starts.append(base[key] + int(t.index01))

    # Track 1 is *usually* logical sector 0 (MusicBrainz offset 150), but it can
    # legitimately be later when a disc has a longer pregap/HTOA. MusicBrainz's
    # TOC format accepts the actual track-1 offset, so do not force it to zero.
    if any(b <= a for a, b in zip(starts, starts[1:])):
        details = ", ".join(f"{t.number:02d}={off}" for t, off in zip(tracks, starts))
        raise ValueError(f"Derived track offsets are not strictly increasing ({details})")
    if cursor <= starts[-1]:
        raise ValueError(f"Derived leadout {cursor} is not after final track offset {starts[-1]}")
    return TocInfo(starts, cursor, "CUE + audio durations")


def calculate_toc(cue: CueDocument) -> TocInfo:
    info = toc_from_rip_log(cue.path.parent, len(cue.audio_tracks))
    if info:
        return info
    return toc_from_cue_audio(cue)


# ---------------------------- MusicBrainz client ----------------------------


class MusicBrainzClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._last = 0.0

    def _get_json(self, url: str) -> dict[str, Any]:
        # MusicBrainz 503 is special: retry forever until the service is available
        # again. Other transient/network failures keep the existing finite retry
        # policy.
        retry_codes = {429, 500, 502, 504}
        last_error: Optional[Exception] = None
        attempt = 0

        while True:
            retry_delay = 0.0
            retry_503 = False

            with self._lock:
                wait = MB_MIN_INTERVAL - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)

                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": MB_USER_AGENT,
                        "Accept": "application/json",
                    },
                )

                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        raw = resp.read()
                    self._last = time.monotonic()
                    return json.loads(raw.decode("utf-8"))

                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", errors="replace")[:500]
                    self._last = time.monotonic()
                    last_error = RuntimeError(
                        f"MusicBrainz HTTP {e.code}: {detail or e.reason}"
                    )

                    try:
                        retry_delay = float(e.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        retry_delay = 0.0

                    if e.code == 503:
                        retry_503 = True
                    elif e.code not in retry_codes or attempt >= 3:
                        raise last_error from e

                except urllib.error.URLError as e:
                    self._last = time.monotonic()
                    last_error = RuntimeError(
                        f"MusicBrainz connection failed: {e.reason}"
                    )
                    if attempt >= 3:
                        raise last_error from e

                except TimeoutError as e:
                    self._last = time.monotonic()
                    last_error = RuntimeError("MusicBrainz request timed out")
                    if attempt >= 3:
                        raise last_error from e

            if retry_503:
                time.sleep(max(retry_delay, 5.0))
                continue

            attempt += 1
            time.sleep(max(retry_delay, 2.0 * attempt))

    @staticmethod
    def _url(path: str, params: dict[str, str]) -> str:
        # MusicBrainz clients conventionally send multiple inc values as a
        # space-separated value; urlencode turns those spaces into '+'. This avoids
        # ambiguous hand-built query strings while matching the official clients.
        return f"{MB_BASE}/{path}?{urllib.parse.urlencode(params)}"

    def discid_lookup(self, disc_id: str, toc: str) -> dict[str, Any]:
        # Do NOT request `media`, `discids`, or `releases` here. In current server
        # validation these combinations can produce contradictory HTTP 400 responses
        # on the DiscID resource. A DiscID lookup already returns associated releases.
        # We only ask for review metadata that the endpoint accepts directly.
        params = {
            "inc": "artist-credits labels release-groups",
            "toc": toc,
            "cdstubs": "no",
            "fmt": "json",
        }
        url = self._url(f"discid/{urllib.parse.quote(disc_id)}", params)
        try:
            return self._get_json(url)
        except RuntimeError as e:
            # If MusicBrainz changes DiscID include validation again, fall back to a
            # plain DiscID/TOC lookup rather than failing the whole discography scan.
            msg = str(e).lower()
            if "http 400" not in msg or "inc parameter" not in msg:
                raise
            fallback = self._url(
                f"discid/{urllib.parse.quote(disc_id)}",
                {"toc": toc, "cdstubs": "no", "fmt": "json"},
            )
            return self._get_json(fallback)

    def release_review_lookup(self, release_id: str) -> dict[str, Any]:
        # Used only when a DiscID result is too sparse for the review table.
        params = {
            "inc": "artist-credits labels release-groups media discids",
            "fmt": "json",
        }
        return self._get_json(self._url(f"release/{urllib.parse.quote(release_id)}", params))

    def release_lookup(self, release_id: str) -> dict[str, Any]:
        # Full metadata is fetched only after the user explicitly selects a release.
        params = {
            "inc": (
                "artist-credits labels recordings release-groups media discids "
                "recording-level-rels work-rels work-level-rels artist-rels"
            ),
            "fmt": "json",
        }
        return self._get_json(self._url(f"release/{urllib.parse.quote(release_id)}", params))


@dataclass
class Candidate:
    release_id: str
    release_title: str
    release_artist: str
    date: str
    country: str
    barcode: str
    label: str
    catalog_number: str
    status: str
    disambiguation: str
    packaging: str
    release_group_id: str
    medium_position: int
    medium_title: str
    medium_format: str
    track_count: int
    exact_disc: bool = False

    @property
    def display_title(self) -> str:
        edition = f" ({self.disambiguation})" if self.disambiguation else ""
        return f"{self.release_artist} - {self.release_title}{edition}"


def candidates_from_lookup(data: dict[str, Any], disc_id: str, expected_tracks: int) -> list[Candidate]:
    out: list[Candidate] = []
    for release in data.get("releases") or []:
        if not isinstance(release, dict):
            continue
        label, catno = all_label_info(release)
        media = release.get("media") or []
        selected_media: list[tuple[dict[str, Any], bool]] = []
        for medium in media:
            if not isinstance(medium, dict):
                continue
            exact = any(safe_text(d.get("id")) == disc_id for d in (medium.get("discs") or []) if isinstance(d, dict))
            count = int(medium.get("track-count") or len(medium.get("tracks") or []))
            if exact:
                selected_media.append((medium, True))
            elif count == expected_tracks:
                selected_media.append((medium, False))
        # Prefer exact DiscID media within a release. If no exact medium exists, keep
        # track-count matches returned by the fuzzy TOC lookup.
        if any(exact for _, exact in selected_media):
            selected_media = [(m, e) for m, e in selected_media if e]
        for medium, exact in selected_media:
            out.append(Candidate(
                release_id=safe_text(release.get("id")),
                release_title=safe_text(release.get("title")),
                release_artist=artist_credit_text(release.get("artist-credit")),
                date=safe_text(release.get("date")),
                country=safe_text(release.get("country")),
                barcode=safe_text(release.get("barcode")),
                label=label,
                catalog_number=catno,
                status=safe_text(release.get("status")),
                disambiguation=safe_text(release.get("disambiguation")),
                packaging=safe_text(release.get("packaging")),
                release_group_id=safe_text((release.get("release-group") or {}).get("id")),
                medium_position=int(medium.get("position") or 1),
                medium_title=safe_text(medium.get("title")),
                medium_format=safe_text(medium.get("format")),
                track_count=int(medium.get("track-count") or len(medium.get("tracks") or [])),
                exact_disc=exact,
            ))
    # Exact DiscID first; then date/artist/title to keep the dialog deterministic.
    out.sort(key=lambda c: (not c.exact_disc, natural_key(c.date), c.release_artist.casefold(), c.release_title.casefold(), c.medium_position))
    return out


def release_ids_from_discid_lookup(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for release in data.get("releases") or []:
        if not isinstance(release, dict):
            continue
        rid = safe_text(release.get("id"))
        if rid and rid not in seen:
            seen.add(rid)
            ids.append(rid)
    return ids


def lookup_candidates_with_hydration(
    mb: "MusicBrainzClient", data: dict[str, Any], disc_id: str, expected_tracks: int
) -> list[Candidate]:
    # Most DiscID replies already include the matching medium, even with a minimal
    # `inc`. Use that fast path first. If the current server returns only release
    # stubs, hydrate each release through the normal release endpoint and retry.
    candidates = candidates_from_lookup(data, disc_id, expected_tracks)
    if candidates:
        return candidates

    release_ids = release_ids_from_discid_lookup(data)
    if not release_ids:
        return []

    hydrated: list[dict[str, Any]] = []
    for rid in release_ids:
        try:
            release = mb.release_review_lookup(rid)
            if isinstance(release, dict):
                hydrated.append(release)
        except Exception:
            # One broken/temporary release fetch must not erase the other matches.
            continue
    return candidates_from_lookup({"releases": hydrated}, disc_id, expected_tracks)


def choose_medium(release: dict[str, Any], candidate: Candidate, disc_id: str) -> dict[str, Any]:
    media = release.get("media") or []
    # Prefer the medium carrying this exact DiscID.
    for medium in media:
        if any(safe_text(d.get("id")) == disc_id for d in (medium.get("discs") or []) if isinstance(d, dict)):
            if int(medium.get("position") or 1) == candidate.medium_position:
                return medium
    for medium in media:
        if int(medium.get("position") or 1) == candidate.medium_position:
            return medium
    raise ValueError(f"Medium {candidate.medium_position} is not present in MusicBrainz release")


def apply_musicbrainz_to_cue(cue: CueDocument, release: dict[str, Any], candidate: Candidate, disc_id: str) -> tuple[int, list[str]]:
    medium = choose_medium(release, candidate, disc_id)
    mb_tracks = medium.get("tracks") or []
    cue_tracks = cue.audio_tracks
    if len(mb_tracks) != len(cue_tracks):
        raise ValueError(f"Track-count mismatch: CUE has {len(cue_tracks)}, MusicBrainz medium has {len(mb_tracks)}")

    album_artist = artist_credit_text(release.get("artist-credit"))
    album_title = safe_text(release.get("title"))
    label, catno = first_label_info(release)

    # Exact mapping observed in CUE Corrector's SearchMusicBrainz -> CreateCuesheet path
    # with the supplied config (UsePublisher=false, UseCatalogNumber=false).
    cue.set_album_tag("REM DATE", safe_text(release.get("date")), quoted=False)
    cue.set_album_tag("CATALOG", safe_text(release.get("barcode")), quoted=False)
    cue.set_album_tag("REM LABEL", label, quoted=False)
    cue.set_album_tag("REM LABELNUMBER", catno, quoted=False)
    cue.set_album_tag("PERFORMER", album_artist, quoted=True)
    cue.set_album_tag("TITLE", album_title, quoted=True)

    for cue_track, mb_track in zip(cue_tracks, mb_tracks):
        recording = mb_track.get("recording") or {}
        title = safe_text(mb_track.get("title")) or safe_text(recording.get("title"))
        credit = mb_track.get("artist-credit") or recording.get("artist-credit") or []
        performer = artist_credit_text(credit) or album_artist
        songwriter, composer = composer_songwriter_from_recording(recording)
        cue.set_track_tag(cue_track.number, "TITLE", title, quoted=True)
        cue.set_track_tag(cue_track.number, "PERFORMER", performer, quoted=True)
        cue.set_track_tag(cue_track.number, "SONGWRITER", songwriter, quoted=True)
        cue.set_track_tag(cue_track.number, "REM COMPOSER", composer, quoted=True)

    changed_files, warnings = cue.correct_file_names()
    return changed_files, warnings


# ---------------------------- GUI ----------------------------


@dataclass
class CueJob:
    path: Path
    cue: Optional[CueDocument] = None
    toc: Optional[TocInfo] = None
    disc_id: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    chosen: Optional[Candidate] = None
    status: str = "Not scanned"
    error: str = ""
    item_id: str = ""

    @property
    def rel_name(self) -> str:
        return self.path.parent.name + " / " + self.path.name


class CandidateDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, candidates: list[Candidate], disc_id: str):
        super().__init__(parent)
        self.title("Review MusicBrainz DiscID matches")
        self.geometry("1460x600")
        self.minsize(1050, 430)
        self.transient(parent)
        self.grab_set()
        self.result: Optional[Candidate] = None
        self.candidates = candidates
        self.disc_id = disc_id

        header = ttk.Frame(self, padding=(10, 10, 10, 4))
        header.pack(fill="x")
        ttk.Label(header, text="DiscID:").pack(side="left")
        ttk.Label(header, text=disc_id).pack(side="left", padx=(6, 12))
        ttk.Button(header, text="Open DiscID page", command=self._open_discid).pack(side="left")
        ttk.Label(
            header,
            text="Choose only when the identifiers match your physical release/source.",
        ).pack(side="right")

        frame = ttk.Frame(self, padding=(10, 4, 10, 8))
        frame.pack(fill="both", expand=True)
        cols = (
            "exact", "artist", "release", "date", "country", "label", "catno", "barcode",
            "medium", "format", "tracks", "packaging", "status", "mbid"
        )
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        self.tree = tree
        heads = {
            "exact": "DiscID", "artist": "Artist", "release": "Release", "date": "Date", "country": "Country",
            "label": "Label(s)", "catno": "Catalog #", "barcode": "Barcode", "medium": "Disc", "format": "Format",
            "tracks": "Tracks", "packaging": "Packaging", "status": "Status", "mbid": "Release MBID"
        }
        widths = {
            "exact": 62, "artist": 150, "release": 245, "date": 86, "country": 62, "label": 190, "catno": 150,
            "barcode": 120, "medium": 48, "format": 78, "tracks": 52, "packaging": 90, "status": 78, "mbid": 250
        }
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=widths[c], minwidth=45, stretch=c in {"artist", "release", "label", "catno"})
        y = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        for i, c in enumerate(candidates):
            tree.insert("", "end", iid=str(i), values=(
                "EXACT" if c.exact_disc else "TOC",
                c.release_artist,
                c.release_title + (f" ({c.disambiguation})" if c.disambiguation else ""),
                c.date, c.country, c.label, c.catalog_number, c.barcode,
                c.medium_position, c.medium_format, c.track_count, c.packaging, c.status, c.release_id
            ))
        if candidates:
            tree.selection_set("0")
            tree.focus("0")

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Skip / cannot identify", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use selected release", command=self._ok).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Open selected release", command=self._open_release).pack(side="right", padx=(0, 8))
        tree.bind("<Double-1>", lambda _e: self._open_release())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_window(self)

    def _selected(self) -> Optional[Candidate]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.candidates[int(sel[0])]

    def _open_discid(self) -> None:
        if self.disc_id:
            webbrowser.open(f"https://musicbrainz.org/cdtoc/{urllib.parse.quote(self.disc_id)}")

    def _open_release(self) -> None:
        c = self._selected()
        if c:
            webbrowser.open(f"https://musicbrainz.org/release/{urllib.parse.quote(c.release_id)}")

    def _ok(self) -> None:
        c = self._selected()
        if c is None:
            return
        self.result = c
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1220x700")
        self.minsize(940, 540)
        self.mb = MusicBrainzClient()
        self.jobs: list[CueJob] = []
        self.job_by_item: dict[str, CueJob] = {}
        self.busy = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="Discography folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.folder_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Browse...", command=self.browse).pack(side="left")
        ttk.Button(top, text="Scan", command=self.scan).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(root, padding=(0, 10, 0, 8))
        actions.pack(fill="x")
        self.lookup_btn = ttk.Button(actions, text="Lookup all", command=self.lookup_all)
        self.lookup_btn.pack(side="left")
        ttk.Button(actions, text="Process selected", command=self.process_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Review selected match", command=self.review_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open DiscID page", command=self.open_selected_discid).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Fix FILE names only", command=self.fix_selected_names).pack(side="left", padx=(8, 0))

        cols = ("release", "toc", "discid", "matches", "selected", "status")
        table_frame = ttk.Frame(root)
        table_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        self.tree = tree
        headings = {
            "release": "Release / CUE", "toc": "TOC source", "discid": "MusicBrainz DiscID",
            "matches": "Matches", "selected": "Selected release", "status": "Status"
        }
        widths = {"release": 320, "toc": 180, "discid": 220, "matches": 70, "selected": 280, "status": 180}
        for col in cols:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], minwidth=60, stretch=col in {"release", "selected", "status"})
        y = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        x = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        tree.bind("<Double-1>", self.on_double_click)

        bottom = ttk.Frame(root, padding=(0, 8, 0, 0))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status_var = tk.StringVar(value="Select a discography folder and click Scan.")
        ttk.Label(bottom, textvariable=self.status_var, anchor="e").pack(side="right", padx=(12, 0))

    def browse(self) -> None:
        folder = filedialog.askdirectory(title="Select discography folder")
        if folder:
            self.folder_var.set(folder)
            self.scan()

    def set_busy(self, value: bool, message: str = "") -> None:
        self.busy = value
        if message:
            self.status_var.set(message)

    def scan(self) -> None:
        if self.busy:
            return
        root = Path(self.folder_var.get().strip())
        if not root.is_dir():
            messagebox.showerror(APP_NAME, "Select a valid discography folder.")
            return
        self.jobs.clear()
        self.job_by_item.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        cues = sorted(root.rglob("*.cue"), key=lambda p: natural_key(str(p.relative_to(root))))
        for p in cues:
            job = CueJob(p)
            try:
                job.cue = CueDocument(p)
                # Reconcile stale FILE names in memory before calculating the TOC.
                # Nothing is written here; the original CUE is only saved after the
                # user explicitly selects and processes a MusicBrainz release.
                pending_file_fixes, file_warnings = job.cue.correct_file_names()
                job.toc = calculate_toc(job.cue)
                job.disc_id = job.toc.disc_id
                if file_warnings:
                    job.status = "Ready for lookup - FILE review"
                    job.error = "; ".join(file_warnings)
                elif pending_file_fixes:
                    job.status = f"Ready for lookup ({pending_file_fixes} FILE fix(es) pending)"
                else:
                    job.status = "Ready for lookup"
            except Exception as e:
                job.error = str(e)
                job.status = "TOC error"
            rel = str(p.relative_to(root))
            item = self.tree.insert("", "end", values=(
                rel,
                job.toc.source if job.toc else "",
                job.disc_id,
                "",
                "",
                job.status + (f": {job.error}" if job.error else "")
            ))
            job.item_id = item
            self.jobs.append(job)
            self.job_by_item[item] = job
        self.progress.configure(maximum=max(1, len(cues)), value=0)
        self.status_var.set(f"Found {len(cues)} CUE file(s).")

    def update_row(self, job: CueJob) -> None:
        if not job.item_id:
            return
        selected = job.chosen.display_title if job.chosen else ""
        self.tree.item(job.item_id, values=(
            self.tree.item(job.item_id, "values")[0],
            job.toc.source if job.toc else "",
            job.disc_id,
            len(job.candidates) if job.candidates else ("0" if job.status == "No MusicBrainz match" else ""),
            selected,
            job.status + (f": {job.error}" if job.error else "")
        ))

    def _run_worker(self, fn, done=None) -> None:
        if self.busy:
            return
        self.set_busy(True)
        def worker():
            err = None
            try:
                fn()
            except Exception:
                err = traceback.format_exc()
            def finish():
                self.set_busy(False)
                if err:
                    messagebox.showerror(APP_NAME, err)
                if done:
                    done()
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _lookup_job(self, job: CueJob) -> None:
        if not job.cue or not job.toc or not job.disc_id:
            return
        try:
            data = self.mb.discid_lookup(job.disc_id, job.toc.toc_string)
            job.candidates = lookup_candidates_with_hydration(
                self.mb, data, job.disc_id, len(job.cue.audio_tracks)
            )
            job.error = ""
            job.chosen = None
            if not job.candidates:
                job.status = "No MusicBrainz match"
            else:
                exact_count = sum(1 for c in job.candidates if c.exact_disc)
                if exact_count:
                    job.status = f"{len(job.candidates)} match(es), {exact_count} exact - REVIEW"
                else:
                    job.status = f"{len(job.candidates)} TOC match(es) - REVIEW"
        except Exception as e:
            job.error = str(e)
            job.status = "Lookup error"
        self.after(0, lambda j=job: self.update_row(j))

    def lookup_all(self) -> None:
        valid = [j for j in self.jobs if j.toc and j.disc_id]
        if not valid:
            messagebox.showinfo(APP_NAME, "No CUE files with a usable TOC were found.")
            return
        self.progress.configure(maximum=len(valid), value=0)
        def run():
            for n, job in enumerate(valid, 1):
                self.after(0, lambda n=n, total=len(valid): self.status_var.set(f"MusicBrainz lookup {n}/{total}..."))
                self._lookup_job(job)
                self.after(0, lambda n=n: self.progress.configure(value=n))
        def done():
            found = sum(1 for j in valid if j.candidates)
            self.status_var.set(f"Lookup complete. {found} CUE file(s) have MusicBrainz matches to review.")
        self._run_worker(run, done)

    def selected_jobs(self) -> list[CueJob]:
        return [self.job_by_item[x] for x in self.tree.selection() if x in self.job_by_item]

    def _select_candidate_ui(self, job: CueJob) -> bool:
        if not job.candidates:
            return False
        dialog = CandidateDialog(self, job.candidates, job.disc_id)
        if dialog.result:
            job.chosen = dialog.result
            job.status = "Selected - ready"
            self.update_row(job)
            return True
        return False

    def _process_job_with_choice(self, job: CueJob) -> None:
        if not job.cue or not job.toc or not job.chosen:
            return
        release = self.mb.release_lookup(job.chosen.release_id)
        # Re-read right before write, so changes made after Scan are not overwritten.
        cue = CueDocument(job.path)
        changed_names, warnings = apply_musicbrainz_to_cue(cue, release, job.chosen, job.disc_id)
        enc = cue.save()
        job.cue = cue
        job.error = "; ".join(warnings)
        job.status = f"Saved ({enc}; {changed_names} FILE name(s) fixed)"
        self.after(0, lambda j=job: self.update_row(j))

    def process_selected(self) -> None:
        selected = self.selected_jobs()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select at least one CUE row.")
            return
        # Network lookup may happen in a worker, but candidate selection must happen in the UI.
        def continue_after_lookup():
            queue: list[CueJob] = []
            for job in selected:
                if not job.candidates:
                    continue
                if not job.chosen and not self._select_candidate_ui(job):
                    continue
                queue.append(job)
            if not queue:
                return
            self.progress.configure(maximum=len(queue), value=0)
            def run_apply():
                for n, job in enumerate(queue, 1):
                    try:
                        self._process_job_with_choice(job)
                    except Exception as e:
                        job.error = str(e)
                        job.status = "Apply error"
                        self.after(0, lambda j=job: self.update_row(j))
                    self.after(0, lambda n=n: self.progress.configure(value=n))
            def done_apply():
                self.status_var.set(f"Processed {len(queue)} selected CUE file(s).")
            self._run_worker(run_apply, done_apply)

        missing = [j for j in selected if not j.candidates and j.toc and j.disc_id]
        if missing:
            self.progress.configure(maximum=len(missing), value=0)
            def run_lookup():
                for n, job in enumerate(missing, 1):
                    self._lookup_job(job)
                    self.after(0, lambda n=n: self.progress.configure(value=n))
            self._run_worker(run_lookup, continue_after_lookup)
        else:
            continue_after_lookup()

    def review_selected(self) -> None:
        selected = self.selected_jobs()
        if len(selected) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one CUE row.")
            return
        job = selected[0]
        if job.candidates:
            self._select_candidate_ui(job)
            return
        if not job.toc or not job.disc_id:
            messagebox.showinfo(APP_NAME, "This CUE has no usable DiscID/TOC.")
            return
        def run():
            self._lookup_job(job)
        def done():
            if job.candidates:
                self._select_candidate_ui(job)
        self._run_worker(run, done)

    def open_selected_discid(self) -> None:
        selected = self.selected_jobs()
        if len(selected) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one CUE row.")
            return
        job = selected[0]
        if not job.disc_id:
            messagebox.showinfo(APP_NAME, "This CUE has no usable DiscID.")
            return
        webbrowser.open(f"https://musicbrainz.org/cdtoc/{urllib.parse.quote(job.disc_id)}")

    def fix_selected_names(self) -> None:
        selected = self.selected_jobs()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select at least one CUE row.")
            return
        saved = 0
        warnings_all: list[str] = []
        for job in selected:
            try:
                cue = CueDocument(job.path)
                changed, warnings = cue.correct_file_names()
                if changed:
                    cue.save()
                    saved += 1
                job.cue = cue
                job.error = "; ".join(warnings)
                job.status = f"FILE names: {changed} changed"
                self.update_row(job)
                warnings_all.extend(f"{job.path.name}: {w}" for w in warnings)
            except Exception as e:
                job.error = str(e)
                job.status = "Filename error"
                self.update_row(job)
        self.status_var.set(f"Filename correction complete. Modified {saved} CUE file(s).")
        if warnings_all:
            messagebox.showwarning(APP_NAME, "\n".join(warnings_all[:20]))

    def on_double_click(self, _event=None) -> None:
        sel = self.selected_jobs()
        if len(sel) != 1:
            return
        job = sel[0]
        if job.candidates:
            self._select_candidate_ui(job)
        elif job.toc and job.disc_id:
            def run(): self._lookup_job(job)
            def done():
                if job.candidates:
                    self._select_candidate_ui(job)
            self._run_worker(run, done)


def self_test_discid() -> None:
    # Official MusicBrainz Disc ID algorithm example:
    # offsets 150,15363,32314,46592,63414,80489 and leadout 95462 ->
    # 49HHV7Eb8UKF3aQiNmu1GR8vKTY-
    info = TocInfo(
        starts=[0, 15363 - 150, 32314 - 150, 46592 - 150, 63414 - 150, 80489 - 150],
        leadout=95462 - 150,
        source="self-test"
    )
    expected = "49HHV7Eb8UKF3aQiNmu1GR8vKTY-"
    actual = info.disc_id
    if actual != expected:
        raise RuntimeError(f"DiscID self-test failed: {actual} != {expected}")


if __name__ == "__main__":
    try:
        self_test_discid()
        App().mainloop()
    except Exception:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME, traceback.format_exc())
            root.destroy()
        except Exception:
            pass