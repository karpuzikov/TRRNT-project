# Music Library Organizer v0.3.22 - Under construction ⚠️
# Standalone TRRNT organizer. MCC v0.5.18 is the visual reference, not a dependency.
# Sources are READ ONLY. All writes target an explicitly selected separate directory.
from __future__ import annotations

import hashlib
import logging
import urllib.request
import urllib.parse
import urllib.error
import base64
import concurrent.futures
import tempfile
import zipfile
from datetime import datetime
import importlib
import json
import io
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid

APP = "Music Library Organizer"
VERSION = "0.3.22"
AUDIO = {".flac", ".ape", ".wv", ".wav", ".m4a", ".aiff", ".aif"}
LOSSY = {".mp3", ".aac", ".ogg", ".opus", ".wma", ".mp4"}
DISC = re.compile(r"^(?:CD|Disc)\s*\d+(?:\b|$)", re.I)
RELEASE = re.compile(r"^\d{4}(?:-\d\d){0,2} - .+ \[[^\[\]]+\]$")
TRACK = re.compile(r"^\s*(\d{1,3})[\s._-]+(.+?)\s*$")
FILE = re.compile(r'^\s*FILE\s+"([^"]+)"\s+\w+\s*$', re.I)
INDEX = re.compile(r"^\s*INDEX\s+(\d\d)\s+(\d\d):(\d\d):(\d\d)\s*$", re.I)
QTITLE = re.compile(r'^\s*TITLE\s+"(.*)"\s*$', re.I)


def documents() -> Path:
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


DATA = documents() / "Karpuzikov Tools" / APP
SETTINGS = DATA / "settings.json"
UNDO = DATA / "undo.json"
HISTORY = DATA / "undo-history"
REPORT = DATA / "last-run.json"
LOG = DATA / "activity.log"  # Legacy path only; never append new runs here.
DEPS = DATA / "dependencies"
IS_WIN = os.name == "nt"


def ensure_data():
    DATA.mkdir(parents=True, exist_ok=True)


def _new_log(kind):
    """Create a collision-proof, isolated log for one launch/job/undo."""
    log_dir = DATA / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(8):
        name = f"{kind}-{datetime.now():%Y-%m-%d-%H-%M-%S}-{uuid.uuid4().hex[:12]}.log"
        path = log_dir / name
        try:
            with path.open("x", encoding="utf-8"):
                pass
            return path
        except FileExistsError:
            continue
    raise OSError("Cannot allocate a unique log filename in " + str(log_dir))


def _migrate_legacy_logs():
    """Move old shared logs without deleting or overwriting any history."""
    candidates = (LOG, DATA / "webview-startup.log", DATA / "startup-error.log")
    existing = [p for p in candidates if p.is_file() and not p.is_symlink()]
    if not existing:
        return []
    legacy = DATA / "logs" / "legacy"
    errors = []
    try:
        legacy.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ["Cannot create legacy log folder: " + str(exc)]
    for source in existing:
        target = legacy / source.name
        if target.exists():
            target = legacy / (source.stem + "-" + uuid.uuid4().hex[:12] + source.suffix)
        try:
            source.replace(target)
        except OSError as exc:
            errors.append("Cannot preserve " + source.name + ": " + str(exc))
    return errors


def _most_recent_operation_log():
    """Find previous processing/Undo activity, excluding startup diagnostics."""
    log_dir = DATA / "logs"
    paths = list(log_dir.glob("run-*.log")) + list(log_dir.glob("undo-*.log"))
    files = [path for path in paths if path.is_file() and not path.is_symlink()]
    if not files:
        return None
    try:
        return max(files, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return None


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def hidden():
    return subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def cmd(args, timeout=3600):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace", timeout=timeout,
                          creationflags=hidden(), check=False)


def ensure_ffmpeg():
    f = shutil.which("ffmpeg")
    p = shutil.which("ffprobe")
    if f and p:
        return f, p
    if not IS_WIN:
        raise RuntimeError("FFmpeg and FFprobe are required for lossless conversion.")
    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("WinGet is missing. Install/update Windows App Installer to enable automatic FFmpeg setup.")
    r = cmd([winget, "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"], 900)
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
                 Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "ffmpeg" / "bin"):
        a, b = base / "ffmpeg.exe", base / "ffprobe.exe"
        if a.is_file() and b.is_file():
            return str(a), str(b)
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return shutil.which("ffmpeg"), shutil.which("ffprobe")
    raise RuntimeError("FFmpeg installation did not provide ffmpeg/ffprobe; restart and retry. " + r.stderr[-400:])


def load_ui():
    if DEPS.is_dir():
        sys.path.insert(0, str(DEPS))
    try:
        importlib.import_module("webview")
        importlib.import_module("mutagen")
    except ImportError:
        if not IS_WIN:
            raise RuntimeError("pywebview is not installed; Windows WebView2 is the supported GUI.")
        ensure_data()
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--upgrade", "--target", str(DEPS), "pywebview>=5.4,<7", "mutagen>=1.47,<2"],
                       check=True, creationflags=hidden())
        if str(DEPS) not in sys.path:
            sys.path.insert(0, str(DEPS))
        importlib.invalidate_caches()
    return importlib.import_module("webview")


def metadata(path: Path):
    try:
        from mutagen import File as MutagenFile
        tag = MutagenFile(str(path), easy=True)
        if tag:
            def first(k):
                v = tag.get(k, [])
                return str(v[0]).strip() if v else ""
            return first("tracknumber"), first("title"), first("artist")
    except Exception:
        pass
    return "", "", ""


_TITLE_GROUP = re.compile(r"(?<!\w)([\(\[\{])([^()\[\]{}]+)([\)\]\}])")
_FT_PREFIX = re.compile(r"(?i)^(?:ft\.?|feat\.?|featuring)\s+(.+)$")
_FT_JOINT = re.compile(r"(?i)^(.+?)\s+(?:ft\.?|feat\.?|featuring)\s+(.+)$")
_EDIT_KEYWORD = re.compile(
    r"(?i)\b(?:radio|edit|extended|version|remix|mix|club|stadium|instrumental|acoustic|dub|original|vip)\b")
_VERSION_HINT = re.compile(
    r"(?i)\b(?:remix|mix|radio|edit|extended|version|club|stadium|instrumental|acoustic|dub|vip|live)\b")



# English minor-word exceptions derived from the user's Picard script.
_PICARD_MINOR_WORDS = frozenset(
    "a an the and but or nor for yet so as at by in of on to from versus".split())
_PICARD_PHRASAL = frozenset((
    "Bun Up", "Break In", "Check In", "Come In", "Drop In", "Fill In",
    "Get In", "Give In", "Join In", "Let In", "Move In", "Tune In",
    "Turn In", "Walk In", "Bring On", "Carry On", "Come On", "Get On",
    "Go On", "Hold On", "Keep On", "Move On", "Pass On", "Put On",
    "Take On", "Turn On", "Try On", "Come By", "Drop By", "Get By",
    "Go By", "Pass By", "Stand By", "Stop By", "Swing By", "Walk By"))
_BARE_TRACK_FEATURE = re.compile(
    r"(?i)\s+(?:ft\.?|feat\.?|featuring)\s+(?P<credit>[\w][\w .&,'’\-]*)$")
_WIN_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


_TITLE_ACRONYMS = frozenset((
    "DJ", "CD", "DVD", "EP", "LP", "EU", "UK", "USA", "US", "EDM",
    "FIFA", "GIMS", "INNA", "VIP", "OST", "TV", "HD", "NBA", "UFC",
    "BBC", "NASA", "AI", "ISRC", "ALAC", "FLAC", "WAV", "MP3", "ID",
))
_PROTECTED_TITLE_NAMES = re.compile(r"(?i)(?<!\w)(?:G-DRAGON|3OH!3)(?!\w)")


def picard_small_word_case(text):
    """Normalize words to title case, not merely lowercase English minor words.

    Leave intentional mixed-case names and protected artist names intact;
    preserve known acronyms, digits and accents. Ordinary ALL CAPS are not
    evidence of an acronym (e.g. SIN TI, JOE PESCI).
    """
    value = str(text or "")
    words = list(re.finditer(r"(?u)\b[^\W\d_]+(?:['’][^\W\d_]+)*\b", value))
    protected = [(m.start(), m.end()) for m in _PROTECTED_TITLE_NAMES.finditer(value)]
    output = []
    previous = 0
    for index, match in enumerate(words):
        word = match.group(0)
        before = words[index - 1] if index else None
        phrase = (before.group(0) + " " + word).title() if before else ""
        first = not index or bool(re.search(r"[:!?]\s*$", value[:match.start()]))
        inside_name = any(start <= match.start() and match.end() <= end
                          for start, end in protected)
        if inside_name:
            normalized = word
        elif word.upper() in _TITLE_ACRONYMS:
            normalized = word.upper()
        elif word.casefold() in {"ft", "feat", "featuring", "vs"}:
            normalized = word.lower()
        elif (word.casefold() in _PICARD_MINOR_WORDS and not first
              and phrase not in _PICARD_PHRASAL):
            normalized = word.lower()
        elif word != word.upper() and word != word.lower():
            # Keep intentional CamelCase (iPhone, eBay, McDonald).
            normalized = word
        else:
            normalized = word[:1].upper() + word[1:].lower()
        output.append(value[previous:match.start()] + normalized)
        previous = match.end()
    output.append(value[previous:])
    return "".join(output)


def _normalize_feature_credit(name):
    return re.sub(r"(?<=\w)\s*&\s*(?=\w)", " & ", " ".join(str(name or "").split()))


def _sanitize_windows_name(text):
    return re.sub(r"\s+", " ", _WIN_UNSAFE.sub(" - ", str(text))).strip(" .-")


def _clean_group_spelling(text):
    text = " ".join(str(text).split())
    if _FT_PREFIX.match(text):
        return "ft. " + _normalize_feature_credit(_FT_PREFIX.match(text).group(1))
    # Case only established descriptor words, not proper names or acronyms.
    text = _EDIT_KEYWORD.sub(lambda match: match.group(0) if match.group(0).isupper() else
                             match.group(0)[0].upper() + match.group(0)[1:].lower(), text)
    return picard_small_word_case(text)


def _song_parts(value):
    """Return base, meaningful ordered groups, and original individual credits."""
    value = " ".join(str(value or "").split())
    trailing = _BARE_TRACK_FEATURE.search(value)
    if trailing and not _FT_PREFIX.match(value):
        name = _normalize_feature_credit(trailing.group("credit"))
        value = value[:trailing.start()] + " (ft. " + name + ")"
    matches = list(_TITLE_GROUP.finditer(value))
    base = value[:matches[0].start()].strip() if matches else value
    groups = []
    feature = ""
    named_main = ""
    for match in matches:
        component = match.group(2).strip()
        feat = _FT_PREFIX.match(component)
        split = _FT_JOINT.match(component)
        if feat and not feature:
            feature = feat.group(1).strip()
            groups.append(("feature", "ft. " + feature))
        elif split and not feature:
            named_main, feature = split.group(1).strip(), split.group(2).strip()
            groups.append(("feature", "ft. " + feature))
        else:
            groups.append(("version", _clean_group_spelling(component)))
    if matches and value[matches[-1].end():].strip():
        # Unparsed trailing text is significant; preserve rather than guessing.
        tail = value[matches[-1].end():].strip()
        groups.append(("unparsed", tail))
    return base, groups, named_main, feature


def _song_key(value):
    import unicodedata
    base = _song_parts(value)[0]
    plain = "".join(c for c in unicodedata.normalize("NFKD", base)
                    if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", plain.casefold())


def normalize_artist_title(value, artist="", main_artist="", consensus=None):
    """Render artist/TITLE separately; never put an artist prefix in TITLE."""
    value = " ".join(str(value or "").strip().split())
    artist = " ".join(str(artist or "").strip().split())
    base, groups, named_main, feature = _song_parts(value)
    tagged_main = _FT_JOINT.match(artist)
    if tagged_main and not named_main:
        named_main, feature = tagged_main.group(1).strip(), tagged_main.group(2).strip()
        artist = named_main
        if not any(role == "feature" for role, _ in groups):
            groups.append(("feature", "ft. " + feature))
    if named_main:
        artist = named_main
    if feature and main_artist and artist:
        # A track explicitly crediting (ft. X) should not redundantly store
        # the same performer in a combined ARTIST tag. Limit the removal to
        # exact two-person spellings; do not erase genuine co-headliners.
        main = str(main_artist).strip()
        credited = feature.strip()
        joined = (
            main + " & " + credited, main + ", " + credited,
            main + " ft. " + credited, main + " feat. " + credited,
            main + " featuring " + credited,
        )
        if artist.casefold() in {x.casefold() for x in joined}:
            artist = main
    if feature and consensus:
        pair = frozenset((artist.casefold(), feature.casefold()))
        preferred = consensus.get((_song_key(value), pair))
        if preferred and preferred.casefold() == feature.casefold():
            artist, feature = feature, artist
            groups = [(role, "ft. " + feature if role == "feature" else component)
                      for role, component in groups]
    # Feature already on the ordinary recording is prior to Radio/Edit/Version.
    # Unknown remix-added credits remain after a genuine Remix component.
    feat_at = next((i for i, (role, _) in enumerate(groups) if role == "feature"), -1)
    if feat_at > 0 and all(
            not re.search(r"(?i)\bremix\b", text)
            for role, text in groups[:feat_at] if role == "version"):
        credited = groups.pop(feat_at)
        # Preserve an intrinsic parenthetical title (no edit/version keywords).
        pos = 0
        while pos < len(groups) and groups[pos][0] == "version" and not _VERSION_HINT.search(groups[pos][1]):
            pos += 1
        groups.insert(pos, credited)
    if not groups:
        return picard_small_word_case(value), artist
    brackets = ("()", "[]", "{}")
    if len(groups) > 3 or any(role == "unparsed" for role, _ in groups):
        # Never discard fourth-level descriptors.
        return value, artist
    rendered = picard_small_word_case(base)
    for i, (role, text) in enumerate(groups):
        item = _clean_group_spelling(text)
        rendered += " " + brackets[i][0] + item + brackets[i][1]
    return rendered, artist


def normalized_title(value: str) -> str:
    title, _ = normalize_artist_title(value)
    return title


def track_filename(number: int, title: str, ext: str) -> str:
    title = normalized_title(title)
    title = _sanitize_windows_name(title)
    if not title or number <= 0:
        raise ValueError("Missing valid title/track number")
    return f"{number:02d} {title}{ext}"


def extract_number_title(path: Path, main_artist="", consensus=None):
    tag_num, tag_title, artist = metadata(path)
    number = 0
    if tag_num:
        m = re.match(r"\s*(\d+)", tag_num)
        if m:
            number = int(m.group(1))
    old = TRACK.match(path.stem)
    if not number and old:
        number = int(old.group(1))
    file_title = old.group(2).strip() if old else ""
    title = (tag_title or "").strip()
    # A richer filename must not lose a version solely because its tag is short.
    if file_title and (not title or (
        file_title.casefold().startswith(title.casefold()) and
        len(file_title) > len(title) and
        (_VERSION_HINT.search(file_title[len(title):]) or
         (_song_parts(file_title)[3] and not _song_parts(title)[3]))
    )):
        title = file_title
    title, artist = normalize_artist_title(title, artist, main_artist, consensus)
    return number, title, artist


def collection_credit_consensus(releases, main_artist):
    """Learn majority orientation from actual collection metadata, not a preset."""
    from collections import Counter
    votes = {}
    main = str(main_artist or "").casefold()
    for release in releases:
        try:
            files = collect_files(release)
        except (OSError, ValueError):
            continue
        for file in files:
            if file.suffix.lower() not in AUDIO:
                continue
            _, title, artist = metadata(file)
            if not title:
                matched = TRACK.match(file.stem)
                title = matched.group(2) if matched else ""
            filematch = TRACK.match(file.stem)
            if filematch:
                filetitle = filematch.group(2)
                _, _, _, filefeat = _song_parts(filetitle)
                if filefeat and not _song_parts(title)[3]:
                    title = filetitle
            if not title:
                continue
            base, _, named_main, feature = _song_parts(title)
            if named_main:
                artist = named_main
            else:
                join = _FT_JOINT.match(artist or "")
                if join:
                    artist, feature = join.group(1).strip(), join.group(2).strip()
            if not (artist and feature and
                    main in (artist.casefold(), feature.casefold())):
                continue
            key = (_song_key(title),
                   frozenset((artist.casefold(), feature.casefold())))
            votes.setdefault(key, Counter())[artist] += 1
    decided = {}
    for key, counts in votes.items():
        ordered = counts.most_common(2)
        if len(ordered) == 1 or ordered[0][1] > ordered[1][1]:
            decided[key] = ordered[0][0]
    return decided



def cue_read(path: Path):
    blob = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251", "cp1252"):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeError:
            continue
    lines = text.splitlines()
    current_file, records = None, []
    for line in lines:
        f = FILE.match(line)
        if f:
            current_file = f.group(1)
            continue
        m = re.match(r"^\s*TRACK\s+(\d\d)\s+AUDIO\b", line, re.I)
        if m:
            records.append({"number": int(m.group(1)), "file": current_file,
                            "title": "", "index": {}, "index_files": {},
                            "lines": [line]})
            continue
        if records:
            records[-1]["lines"].append(line)
            q = QTITLE.match(line)
            if q and not records[-1]["title"]:
                records[-1]["title"] = q.group(1)
            z = INDEX.match(line)
            if z:
                index_no = int(z.group(1))
                records[-1]["index"][index_no] = (
                    (int(z.group(2)) * 60 + int(z.group(3))) * 75 + int(z.group(4)))
                records[-1]["index_files"][index_no] = current_file
                if index_no == 1:
                    # The TRACK and INDEX 00 can belong to the *previous*
                    # file, while INDEX 01 belongs to a new FILE directive.
                    # Associate the track with its actual INDEX 01 audio.
                    records[-1]["file"] = current_file
    return {"lines": lines, "records": records, "encoding": encoding}


def cue_image_plan(cue_path: Path, cue):
    """Detect real whole-album images; use INDEX00 boundaries where present."""
    groups = {}
    for rec in cue["records"]:
        if rec["file"]:
            groups.setdefault(rec["file"].casefold(), []).append(rec)
    images = []
    for group in groups.values():
        if len(group) < 2:
            continue
        src = cue_path.parent / group[0]["file"].replace("\\", os.sep)
        if not src.is_file():
            # Legacy CUE references an absent WAV; do not misclassify the
            # already split lossless tracks as a missing whole-album image.
            continue
        if src.is_symlink() or src.suffix.lower() not in AUDIO:
            raise ValueError("Unsafe or unsupported CUE album image: " + str(src))
        ticks = []
        for rec in group:
            idx = rec["index"]
            if 1 not in idx or not rec["title"]:
                raise ValueError("Image CUE lacks INDEX 01 or TITLE.")
            if not cue_index_source_consistent(rec):
                raise ValueError("INDEX00 occurs after INDEX01 in the same audio file.")
            if any(value < idx[1] for num, value in idx.items() if num > 1):
                raise ValueError("Invalid additional CUE index.")
            ticks.append(idx.get(0, idx[1]))
        if ticks[0] > 0 and 0 not in group[0]["index"]:
            # First-track hidden audio before INDEX01 is kept with track 01.
            ticks[0] = 0
        if ticks[0] != 0 or ticks != sorted(set(ticks)):
            raise ValueError("CUE image has an unsafe or overlapping index layout.")
        images.append((src, group, ticks))
    return images



def cue_index_source_consistent(record):
    """INDEX 00 > INDEX 01 is invalid only in the *same* audio file."""
    idx = record.get("index") or {}
    files = record.get("index_files") or {}
    if 0 not in idx or 1 not in idx:
        return True
    prev_file = str(files.get(0) or record.get("file") or "")
    next_file = str(files.get(1) or record.get("file") or "")
    if prev_file.casefold() == next_file.casefold():
        return idx[0] <= idx[1]
    return True


def image_cue_text(cue, new_names):
    """Write a separated-image CUE, keeping INDEX 00/01 and other indexes."""
    def stamp(frames):
        if frames < 0:
            raise ValueError("Negative CUE position.")
        minute, other = divmod(frames, 75 * 60)
        second, frame = divmod(other, 75)
        return f"{minute:02d}:{second:02d}:{frame:02d}"

    out = []
    for line in cue["lines"]:
        if FILE.match(line) or re.match(r"^\s*TRACK\b", line, re.I):
            break
        out.append(line)
    for rec in cue["records"]:
        if rec["number"] not in new_names:
            raise ValueError("Unmapped audio image CUE track.")
        name, start, end = new_names[rec["number"]]
        out.append('FILE "' + name + '" WAVE')
        first = rec["lines"][0] if rec["lines"] else "  TRACK %02d AUDIO" % rec["number"]
        out.append(first)
        idx = rec["index"]
        if rec["number"] == cue["records"][0]["number"] and start == 0 and idx[1] > 0 and 0 not in idx:
            out.append("    INDEX 00 00:00:00")
        for line in rec["lines"][1:]:
            if FILE.match(line):
                continue
            m = INDEX.match(line)
            if m:
                index_no = int(m.group(1))
                relative = idx[index_no] - start
                if relative < 0 or (end is not None and idx[index_no] >= end):
                    raise ValueError("CUE index lies outside generated track.")
                out.append("    INDEX %02d " % index_no + stamp(relative))
            else:
                out.append(line)
    return "\n".join(out).rstrip() + "\n"




def reconstruct_missing_image_cue(cue, cue_path, mapping):
    """Build a playable track-per-file CUE only after exact sector verification.

    This is separate from DiscID edition matching. A 16-track match never
    establishes how an obsolete shared-image CUE represented index-00 audio.
    If per-file PCM lengths agree with original INDEX 01 sector deltas and
    there are no hidden/pregap indexes, the remap is sample-exact.
    """
    records = cue.get("records") or []
    tracks = {}
    for original, output in mapping.items():
        if original.parent != cue_path.parent:
            continue
        parsed = TRACK.match(original.stem)
        if not parsed:
            continue
        number = int(parsed.group(1))
        if number in tracks:
            return None, "Multiple audio files have the same track number"
        tracks[number] = (original, output)
    if not records or len(tracks) != len(records):
        return None, (f"Original CUE lists {len(records)} tracks but only "
                      f"{len(tracks)} separately numbered audio tracks were mapped")
    if any(not rec.get("file") or 1 not in rec.get("index", {})
           for rec in records):
        return None, "Original CUE has missing FILE or INDEX 01 entries"
    names = [rec["number"] for rec in records]
    if sorted(names) != sorted(tracks):
        return None, "CUE and surviving audio track numbers do not match"
    refs = {str(rec["file"]).casefold() for rec in records}
    if len(refs) != 1:
        return None, "Original CUE does not describe one shared image"
    if any(0 in rec["index"] or len(rec["index"]) != 1 or
           any(re.match(r"(?i)^\s*(?:PREGAP|POSTGAP)\b", line)
               for line in rec["lines"]) for rec in records):
        return None, "INDEX 00, hidden audio or explicit pregap requires original image evidence"
    starts = [rec["index"][1] for rec in records]
    if starts[0] != 0 or starts != sorted(set(starts)):
        return None, "Original CUE has a nonzero initial index or non-monotonic INDEX 01 positions"
    try:
        for index, rec in enumerate(records[:-1]):
            expected = starts[index + 1] - starts[index]
            actual = _exact_cd_sectors(tracks[rec["number"]][0])
            if actual != expected:
                return None, (f"Track {rec['number']:02d} has {actual} sectors "
                              f"but original CUE specifies {expected}; pregap alignment unproven")
        _exact_cd_sectors(tracks[records[-1]["number"]][0])
    except (ValueError, OSError, KeyError) as exc:
        return None, "Sample-exact verification unavailable: " + str(exc)
    output = []
    for line in cue["lines"]:
        if FILE.match(line) or re.match(r"^\s*TRACK\b", line, re.I):
            break
        output.append(line)
    for rec in records:
        new_audio = tracks[rec["number"]][1]
        target = os.path.relpath(new_audio, new_audio.parent)
        output.append('FILE "' + target + '" WAVE')
        output.extend(rec["lines"][:1])
        for line in rec["lines"][1:]:
            if INDEX.match(line):
                output.append("    INDEX 01 00:00:00")
            else:
                output.append(line)
    return "\n".join(output).rstrip() + "\n", ""


def normal_cue_text(cue, names):
    out = []
    for line in cue["lines"]:
        f = FILE.match(line)
        if not f:
            out.append(line)
            continue
        key = f.group(1).replace("\\", "/").casefold()
        if key not in names:
            raise ValueError("CUE FILE reference could not be mapped: " + f.group(1))
        out.append('FILE "' + names[key] + '" WAVE')
    return "\n".join(out).rstrip() + "\n"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def pcm_hash(ff, path, start=None, end=None):
    filt = []
    if start is not None:
        val = "atrim=start_sample=" + str(start)
        if end is not None:
            val += ":end_sample=" + str(end)
        filt.extend(["-af", val])
    p = cmd([ff, "-v", "error", "-i", str(path), "-map", "0:a:0"] + filt +
            ["-c:a", "pcm_s32le", "-f", "hash", "-hash", "sha256", "-"], timeout=7200)
    if p.returncode:
        raise RuntimeError("Decoded PCM verification failed: " + p.stderr[-450:])
    if "SHA256=" not in p.stdout:
        raise RuntimeError("Missing decoded PCM verification hash.")
    return p.stdout.split("SHA256=")[-1].strip()


# Mandatory encoder backends. FFmpeg is used ONLY for decoding WAV intermediates,
# probing and decoded-PCM verification; NEVER for ALAC/FLAC encoding.
_ENCODER_LOCK = threading.RLock()
_ENCODER_CACHE = {}
_FLAC_VERSION = "1.5.0"
_FLAC_ZIP_URL = "https://ftp.osuosl.org/pub/xiph/releases/flac/flac-1.5.0-win.zip"
_FLAC_ZIP_SHA256 = "53f1500f0d6e7c61379d7fee50d4a9f7f504c650009506d9ba015530d76c0dde"
_QAAC_FALLBACK_TAG = "v3.07"
_QAAC_FALLBACK_ARCHIVE = "qaac_3.07.zip"
_QAAC_FALLBACK_SHA256 = "1fb3ab4aa81725e2607ac1b31afa0e13d2617ee6eb5900f5070c092f5271cbb3"
_QAAC_ACTIVE_SHA256 = [_QAAC_FALLBACK_SHA256]


def _safe_zip_extract(content, output):
    import io
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.infolist()
        if len(names) > 1000:
            raise RuntimeError("Unexpected encoder archive file count.")
        expanded = 0
        for entry in names:
            if entry.is_dir():
                continue
            rel = Path(entry.filename.replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError("Unsafe encoder archive entry.")
            # ZIP Symlinks and non-regular file attributes are not followed.
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Unexpected encoder archive symlink.")
            expanded += entry.file_size
            if expanded > 200 * 1024 * 1024:
                raise RuntimeError("Unreasonably large encoder archive.")
            location = output.joinpath(*rel.parts)
            if output.resolve() not in location.resolve().parents:
                raise RuntimeError("Encoder ZIP attempted path traversal.")
            location.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as src, location.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _fetch_official_archive(url, expected_sha=None):
    if not url.startswith(("https://github.com/nu774/qaac/releases/download/",
                           "https://ftp.osuosl.org/pub/xiph/releases/flac/")):
        raise RuntimeError("Untrusted encoder download URL.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Music-Library-Organizer/" + VERSION})
    with urllib.request.urlopen(request, timeout=70) as response:
        data = response.read(40 * 1024 * 1024 + 1)
    if not data or len(data) > 40 * 1024 * 1024:
        raise RuntimeError("Encoder archive empty or too large.")
    if expected_sha and hashlib.sha256(data).hexdigest().lower() != expected_sha.lower():
        raise RuntimeError("Official FLAC archive SHA-256 verification failed.")
    return data


def _qaac_official_url():
    tag, archive = _QAAC_FALLBACK_TAG, _QAAC_FALLBACK_ARCHIVE
    _QAAC_ACTIVE_SHA256[0] = _QAAC_FALLBACK_SHA256
    # The official source publishes current release assets. The pinned version
    # remains a valid fallback if the GitHub API is briefly unreachable.
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/nu774/qaac/releases/latest",
            headers={"User-Agent": "Music-Library-Organizer/" + VERSION,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=15) as reply:
            release = json.load(reply)
        t = str(release.get("tag_name") or "")
        matches = [a for a in release.get("assets", [])
                   if re.fullmatch(r"qaac_[\d.]+\.zip", a.get("name", ""))]
        if re.fullmatch(r"v[\d.]+", t) and len(matches) == 1:
            version = matches[0]["name"]
            verified = f"https://github.com/nu774/qaac/releases/download/{t}/{version}"
            digest = str(matches[0].get("digest") or "")
            if (matches[0].get("browser_download_url") == verified and
                    re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
                tag, archive = t, version
                _QAAC_ACTIVE_SHA256[0] = digest[7:].lower()
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        pass
    return f"https://github.com/nu774/qaac/releases/download/{tag}/{archive}"


def _find_encoder_in(root, filename):
    possibilities = [x for x in root.rglob(filename) if x.is_file()]
    if not possibilities:
        raise RuntimeError("Official encoder ZIP did not include " + filename)
    # Prefer x64 architecture when archives contain both executables.
    possibilities.sort(key=lambda p: (
        not any(c in str(p.parent).lower() for c in ("win64", "x64", "64-bit", "64bit")),
        len(p.parts), str(p).lower()))
    return str(possibilities[0])


def ensure_encoder(fmt):
    """Use only refalac64.exe or Xiph flac.exe, isolated from other programs."""
    name = "refalac64.exe" if fmt == "ALAC" else "flac.exe"
    if fmt not in ("ALAC", "FLAC"):
        raise RuntimeError("Only ALAC and FLAC outputs are supported.")
    with _ENCODER_LOCK:
        cached = _ENCODER_CACHE.get(fmt)
        if cached and Path(cached).is_file():
            return cached
        location = DEPS / "encoders" / fmt.lower()
        if location.is_dir():
            try:
                path = _find_encoder_in(location, name)
                _ENCODER_CACHE[fmt] = path
                return path
            except RuntimeError:
                pass
        # Install only official checksum-validated encoders in this tool's
        # isolated directory; an unrelated PATH executable is not trusted.
        if not IS_WIN:
            raise RuntimeError(name + " is required; auto-bootstrap supports Windows.")
        ensure_data()
        url = _qaac_official_url() if fmt == "ALAC" else _FLAC_ZIP_URL
        try:
            binary = _fetch_official_archive(
                url, _QAAC_ACTIVE_SHA256[0] if fmt == "ALAC" else _FLAC_ZIP_SHA256)
            # Atomically publish a complete extracted encoder set.
            location.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="encoder_", dir=str(location.parent)) as d:
                staging = Path(d) / "files"
                _safe_zip_extract(binary, staging)
                candidate = _find_encoder_in(staging, name)
                if location.exists():
                    shutil.rmtree(location)
                os.replace(staging, location)
                installed = location / Path(candidate).relative_to(staging)
        except Exception as exc:
            raise RuntimeError(
                f"Required {name} could not be installed from its official source: {exc}") from exc
        _ENCODER_CACHE[fmt] = str(installed)
        return str(installed)


def probe(probe_exe, path):
    proc = cmd([probe_exe, "-v", "error", "-select_streams", "a:0",
                "-show_entries",
                "stream=sample_rate,codec_name,sample_fmt,bits_per_raw_sample,bits_per_sample,channels",
                "-of", "json", str(path)], 100)
    if proc.returncode:
        raise RuntimeError("Audio probe failed: " + proc.stderr[-400:])
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        raise RuntimeError("No decodable audio: " + str(path))
    return streams[0]


def _lossless_pcm_format(ffprobe, input_path):
    details = probe(ffprobe, input_path)
    codec = str(details.get("codec_name", "")).lower()
    if codec not in _MB_LOSSLESS:
        raise RuntimeError("Lossy source cannot be relabeled as lossless: " + codec)
    if str(details.get("sample_fmt", "")).startswith(("flt", "dbl")) or codec.startswith("pcm_f"):
        raise RuntimeError("Floating-point PCM cannot be converted bit-perfectly to integer ALAC/FLAC.")
    raw_bits = details.get("bits_per_raw_sample") or details.get("bits_per_sample") or 0
    try:
        bits = int(raw_bits)
    except (ValueError, TypeError):
        bits = 0
    if not bits and codec == "flac":
        try:
            from mutagen.flac import FLAC
            bits = int(FLAC(str(input_path)).info.bits_per_sample)
        except Exception:
            pass
    if not bits:
        match = re.search(r"(?:s|u)(8|16|24|32)(?:le|be)", codec)
        bits = int(match.group(1)) if match else 0
    if not bits and details.get("sample_fmt") == "s16":
        bits = 16
    if bits not in (16, 24, 32):
        raise RuntimeError(f"Unsupported/unknown lossless source bit depth: {bits or 'unknown'}")
    rate = int(details.get("sample_rate") or 0)
    if rate <= 0:
        raise RuntimeError("Source sample rate is unknown.")
    return "pcm_s" + str(bits) + "le", bits, rate


def _update_copied_track_tags(path, title, artist, number):
    """Change staged COPY only. Never mutate source, never re-encode audio."""
    from mutagen import File as MutagenFile
    tags = MutagenFile(str(path), easy=True)
    if tags is None:
        raise RuntimeError("Cannot update tags in organized audio: " + str(path))
    if tags.tags is None:
        tags.add_tags()
    tags["title"] = [title]
    if artist:
        tags["artist"] = [artist]
    tags["tracknumber"] = [str(number)]
    tags.save()
    check = MutagenFile(str(path), easy=True)
    if str(check.get("title", [""])[0]) != title:
        raise RuntimeError("Track TITLE was not saved correctly.")
    if artist and str(check.get("artist", [""])[0]) != artist:
        raise RuntimeError("Track ARTIST was not saved correctly.")


def _copy_audio_metadata(src_path, dst_path, fmt, title, number, artist=""):
    """Carry standard and custom metadata through an untagged WAV intermediate."""
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    try:
        source = MutagenFile(str(src_path), easy=True)
        metadata = dict(source.tags or {}) if source else {}
        raw = MutagenFile(str(src_path))
    except Exception as exc:
        raise RuntimeError("Cannot inspect source metadata: " + str(exc))
    if title:
        metadata["title"] = [title]
    if artist:
        metadata["artist"] = [artist]
    if number:
        metadata["tracknumber"] = [str(number)]
    destination = MutagenFile(str(dst_path), easy=True)
    if destination is None:
        raise RuntimeError("Native encoder output not recognized.")
    if destination.tags is None:
        destination.add_tags()

    # Any Vorbis comment can be represented as a FLAC tag. EasyMP4 supports
    # standard iTunes fields; additional source keys use MP4 freeform atoms
    # rather than being silently dropped.
    additional = {}
    for key, values in metadata.items():
        key = str(key).lower()
        if not values:
            continue
        items = [str(v) for v in values] if isinstance(values, (list, tuple)) else [str(values)]
        try:
            destination[key] = items
        except (ValueError, KeyError, TypeError):
            if fmt != "ALAC":
                raise RuntimeError("Cannot preserve FLAC metadata field: " + key)
            additional[key] = items
    destination.save()

    artworks = []
    if isinstance(raw, FLAC):
        artworks = [(pic.data, pic.mime, pic.type, pic.desc) for pic in raw.pictures]
    elif isinstance(raw, MP4) and raw.tags:
        for cover in raw.tags.get("covr", []):
            mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            artworks.append((bytes(cover), mime, 3, "Front cover"))

    if fmt == "ALAC":
        target = MP4(str(dst_path))
        if target.tags is None:
            target.add_tags()
        for key, values in additional.items():
            tag = "----:com.apple.iTunes:" + key
            target.tags[tag] = [MP4FreeForm(v.encode("utf-8")) for v in values]
        if artworks:
            target.tags["covr"] = [
                MP4Cover(data, imageformat=(
                    MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG))
                for data, mime, _, _ in artworks]
        target.save()
    elif artworks:
        target = FLAC(str(dst_path))
        target.clear_pictures()
        for data, mime, role, description in artworks:
            picture = Picture()
            picture.data = data
            picture.mime = mime
            picture.type = role
            picture.desc = description
            target.add_picture(picture)
        target.save()
    # Verify tags survived. Audio parity is checked separately.
    check = MutagenFile(str(dst_path), easy=True)
    if title and (not check or str(check.get("title", [""])[0]) != title):
        raise RuntimeError("Output title tag verification failed.")
    if number and (not check or
                   str(check.get("tracknumber", [""])[0]).split("/")[0] != str(number)):
        raise RuntimeError("Output track-number tag verification failed.")
    if artist and (not check or str(check.get("artist", [""])[0]) != artist):
        raise RuntimeError("Output artist tag verification failed.")
    if artworks:
        output_raw = MutagenFile(str(dst_path))
        stored = ([p.data for p in output_raw.pictures]
                  if isinstance(output_raw, FLAC)
                  else [bytes(p) for p in output_raw.tags.get("covr", [])]
                  if isinstance(output_raw, MP4) and output_raw.tags else [])
        if [data for data, _, _, _ in artworks] != stored:
            raise RuntimeError("Album artwork changed during transcoding.")



def convert(ff, input_path, output_path, fmt, start=None, end=None,
            title="", number=0, artist="", ffprobe=None):
    """Decode to PCM WAV, encode *only* with refalac64 or official FLAC CLI."""
    if fmt not in ("ALAC", "FLAC"):
        raise ValueError("ALAC or FLAC format required.")
    input_path, output_path = Path(input_path), Path(output_path)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if output_path.exists():
        raise FileExistsError("Refusing to overwrite output: " + str(output_path))
    ensure_data()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe required for lossless source precision check.")
    pcm_codec, bits, sample_rate = _lossless_pcm_format(ffprobe, input_path)
    encoder = ensure_encoder(fmt)
    with tempfile.TemporaryDirectory(prefix="MLO_PCM_", dir=str(DATA)) as td:
        wav = Path(td) / "input.wav"
        args = [ff, "-hide_banner", "-v", "error", "-nostdin", "-i", str(input_path),
                "-map", "0:a:0", "-vn", "-sn", "-dn"]
        if start is not None:
            trim = "atrim=start_sample=" + str(start)
            if end is not None:
                trim += ":end_sample=" + str(end)
            args += ["-af", trim + ",asetpts=PTS-STARTPTS"]
        args += ["-c:a", pcm_codec, "-ar", str(sample_rate), "-rf64", "auto",
                 "-f", "wav", str(wav)]
        decoded = cmd(args, timeout=7200)
        if decoded.returncode or not wav.is_file():
            raise RuntimeError("Lossless PCM decoding failed: " + decoded.stderr[-600:])
        if fmt == "ALAC":
            # refalac64.exe is the ONLY encoder allowed for ALAC.
            command = [encoder, "-o", str(output_path), str(wav)]
        else:
            # Official Xiph flac.exe is the ONLY encoder allowed for FLAC.
            command = [encoder, "--best", "--verify", "-o", str(output_path), str(wav)]
        try:
            encoded = cmd(command, timeout=7200)
            if encoded.returncode or not output_path.is_file():
                raise RuntimeError(
                    f"{Path(encoder).name} encoding failed: " + encoded.stderr[-600:])
            output_info = probe(ffprobe, output_path)
            expected_codec = "alac" if fmt == "ALAC" else "flac"
            if output_info.get("codec_name") != expected_codec:
                raise RuntimeError("Native encoder produced incorrect audio codec.")
            if int(output_info.get("sample_rate") or 0) != sample_rate:
                raise RuntimeError("Native encoder changed sample rate.")
            actual_bits = output_info.get("bits_per_raw_sample") or output_info.get("bits_per_sample")
            try:
                actual_bits = int(actual_bits)
            except (ValueError, TypeError):
                actual_bits = 0
            if actual_bits and actual_bits != bits:
                raise RuntimeError(f"Native encoder changed bit depth: {bits} -> {actual_bits}.")
            if pcm_hash(ff, input_path, start, end) != pcm_hash(ff, output_path):
                raise RuntimeError("Source and encoded decoded-PCM SHA-256 differ.")
            _copy_audio_metadata(input_path, output_path, fmt, title, number, artist)
            return output_path
        except Exception:
            output_path.unlink(missing_ok=True)
            raise



def discover(root: Path):
    folders = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [x for x in dirs if not (Path(base) / x).is_symlink()]
        if not any(Path(x).suffix.lower() in AUDIO | LOSSY for x in files):
            continue
        here = Path(base)
        cur = here
        release = None
        while cur != root and root in cur.parents:
            if RELEASE.fullmatch(cur.name):
                release = cur
                break
            cur = cur.parent
        if release is None:
            release = here.parent if DISC.fullmatch(here.name) else here
        folders.add(release)
    return sorted(folders, key=lambda p: str(p).casefold())


def group_disc_releases(releases):
    """One operation for an unambiguous consecutive set of sibling CD folders."""
    releases = list(releases)
    families = {}
    for folder in releases:
        match = re.fullmatch(r"(?i)(.+?)\s+CD\s*(\d+)", folder.name)
        if match:
            key = (folder.parent, match.group(1).casefold())
            families.setdefault(key, []).append((int(match.group(2)), folder))
    group_for = {}
    for family in families.values():
        numbers = [number for number, _ in family]
        if (len(numbers) < 2 or len(set(numbers)) != len(numbers) or
                sorted(numbers) != list(range(1, len(numbers) + 1))):
            continue
        ordered = tuple(folder for _, folder in sorted(family))
        name = re.sub(r"(?i)\s+CD\s*1$", "", ordered[0].name)
        for folder in ordered:
            group_for[folder] = (name, ordered)
    result, emitted = [], set()
    for folder in releases:
        if folder in emitted:
            continue
        name, members = group_for.get(folder, (folder.name, (folder,)))
        result.append((name, members))
        emitted.update(members)
    return result


def source_disc_counts(disc_folders):
    counts = []
    for folder in disc_folders:
        files = collect_files(folder)
        audios = [p for p in files if p.suffix.lower() in AUDIO]
        cues = [p for p in files if p.suffix.lower() == ".cue"]
        parsed, observed = _source_medium_track_counts(cues, audios)
        if len(parsed) > 1:
            raise ValueError("One numbered CD folder contains multiple CUE discs.")
        counts.append(observed)
    return tuple(counts)


def collect_files(path: Path):
    out = []
    for here, dirs, files in os.walk(path, followlinks=False):
        if any((Path(here) / x).is_symlink() for x in dirs):
            raise ValueError("Symlink directory is not supported: " + str(here))
        dirs[:] = [x for x in dirs if not (Path(here) / x).is_symlink()]
        for fn in files:
            p = Path(here) / fn
            if p.is_symlink() or not p.is_file():
                raise ValueError("Symlink/unreadable file in release: " + str(p))
            out.append(p)
    return sorted(out, key=lambda p: str(p).casefold())


def catalog(name):
    m = re.search(r"\[([^\[\]]+)\]\s*$", name)
    if not m:
        return "", False
    value = m.group(1)
    match = re.match(r"^(?:[A-Z]{2}|EU|UK|XE|XW)\s+-\s+(.+?)(?:\s+-\s+\d{4})?$", value)
    return (match.group(1) if match else value), bool(match)


def suggested_output(source):
    raw = str(source or "").strip().strip('"')
    if not raw:
        return ""
    name = Path(raw).name
    if not name:
        return ""
    if name.casefold().endswith(" - sorted"):
        name += " - Organized"
    else:
        name += " - Sorted"
    return str(Path(raw).parent / name)


def worker_limit():
    return max(1, min(64, int(os.cpu_count() or 1)))


def worker_defaults():
    logical = worker_limit()
    return min(4, logical), min(4, logical)


def check_paths(src_text, dst_text, fmt):
    source = Path(src_text.strip().strip('"')).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Source must be an existing folder.")
    if not dst_text.strip():
        raise ValueError("Choose an explicit, separate destination folder.")
    dest = Path(dst_text.strip().strip('"')).expanduser().resolve(strict=False)
    if dest == source or source in dest.parents or dest in source.parents:
        raise ValueError("Source and destination must be separate, non-nested roots.")
    if fmt not in ("ALAC", "FLAC"):
        raise ValueError("Only ALAC and FLAC lossless output formats are supported.")
    if fmt == "FLAC" and dest.name.lower().endswith(" alac"):
        raise ValueError("FLAC output cannot go into a folder named ALAC.")
    if fmt == "ALAC" and dest.name.lower().endswith(" flac"):
        raise ValueError("ALAC output cannot go into a folder named FLAC.")
    return source, dest



# --- Release enrichment. All lookups are read-only; cache under this app only. ---
import urllib.request
import urllib.parse
import urllib.error
import difflib

_MB_WAIT = [0.0]
_MB_UNAVAILABLE_UNTIL = [0.0]
_BARCODE = re.compile(r"^\d{8,14}$")
_DATE = re.compile(r"^\d{4}(?:-\d{2}){0,2}$")
_RELEASE_PARTS = re.compile(r"^(?P<date>\d{4}(?:-\d{2}){0,2}) - (?P<title>.*?) \[(?P<id>[^\[\]]+)\]$")
_ALT_RELEASE = re.compile(r"(?i)^(?:inna|.+?)\s+-\s+(?P<title>.+?)\s+\(?(?P<year>(?:19|20)\d\d)\)?$")
_LEGACY_DATE_TITLE = re.compile(
    r"^(?P<year>(?:19|20)\d{2})(?:[.-](?P<month>\d{2}))?"
    r"(?:[.-](?P<day>\d{2}))?\s+-\s+(?P<title>.+)$")
_MB_CACHE = DATA / "metadata-cache"
_MB_USER_AGENT = "MusicLibraryOrganizer/0.3.0 (https://github.com/karpuzikov/TRRNT-project)"
_MB_LOSSLESS = {"flac", "alac", "ape", "wavpack", "pcm_s16le", "pcm_s24le",
                "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_s16be", "pcm_s24be", "pcm_s32be"}


def mb_json(path, ttl_seconds=86400 * 14):
    """Rate-limited MusicBrainz WS2 JSON. Failure never invents metadata."""
    cache_key = hashlib.sha256(path.encode("utf-8")).hexdigest()
    stored = _MB_CACHE / (cache_key + ".json")
    if stored.is_file() and time.time() - stored.stat().st_mtime < ttl_seconds:
        return json.loads(stored.read_text(encoding="utf-8"))
    if time.monotonic() < _MB_UNAVAILABLE_UNTIL[0]:
        raise RuntimeError("MusicBrainz currently unreachable; preserving original release metadata.")
    wait = max(0.0, 1.1 - (time.monotonic() - _MB_WAIT[0]))
    if wait:
        time.sleep(wait)
    url = "https://musicbrainz.org/ws/2/" + path
    last = None
    for retry in range(3):
        _MB_WAIT[0] = time.monotonic()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _MB_USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            _MB_CACHE.mkdir(parents=True, exist_ok=True)
            write_json(stored, data)
            return data
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            last = exc
            if retry < 2:
                time.sleep(1.5 * (retry + 1))
    _MB_UNAVAILABLE_UNTIL[0] = time.monotonic() + 180
    raise RuntimeError("MusicBrainz lookup unavailable: " + str(last))


def _key(s):
    s = str(s or "").lower().replace("’", "'")
    s = re.sub(r"(?i)\s*(?:-|[\[(])\s*(?:single|ep|album)\s*[\])]?\s*$", "", s)
    return re.sub(r"[^\w]+", "", s)


_RELEASE_FEATURE = re.compile(
    r"(?i)\s*[\(\[]\s*(?:ft\.?|feat\.?|featuring)\s+[^()\[\]]+[\)\]]")
_RELEASE_FEATURE_BARE = re.compile(
    r"(?i)\s+(?:ft\.?|feat\.?|featuring)\s+[\w .&,'’\-]+$")
_RELEASE_FORMAT_NOTE = re.compile(r"(?i)\s*\((?:Promo|FLAC|ALAC|MP3|WAV)\)\s*$")
_RELEASE_MEDIA_NOTE = re.compile(r"(?i)\s*\((?:Maxi\s+CD\s+Single|CD\s+Single)\)")
_RELEASE_YEAR_NOTE = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")


def release_feature_credit(value):
    """Return a real featured-credit token from a release heading if present."""
    found = re.search(
        r"(?i)[\(\[]\s*(?:ft\.?|feat\.?|featuring)\s+([^()\[\]]+)[\)\]]",
        str(value or ""))
    return found.group(1).strip() if found else ""


def clean_release_title(value, root_artist=""):
    """Featured artists exist on TRACKS, not on release/folder TITLE."""
    original = " ".join(str(value or "").split())
    if root_artist:
        original = re.sub(
            r"(?i)^" + re.escape(str(root_artist).strip()) + r"\s+-\s+",
            "", original, count=1)
        if str(root_artist).strip().casefold() == "inna":
            # Verified against the user's existing correctly accented
            # "Déjà Vu" title; do not change unrelated "Deja Vu" names.
            original = re.sub(r"(?i)\bDéjá Vu\b", "Déjà Vu", original)
    # Old release source folders may include the barcode/catalog as a final
    # bracket but that is not part of the musical release TITLE.
    last_id = re.search(r"\s+\[([^\[\]]+)\]$", original)
    if last_id:
        candidate_id = last_id.group(1)
        if (re.fullmatch(r"\d{8,14}", candidate_id) or
                re.fullmatch(r"(?i)[A-Z]{0,8}\s*-?\s*[A-Z]*\d[\w .-]*", candidate_id)):
            original = original[:last_id.start()]
    original = _RELEASE_YEAR_NOTE.sub("", original).strip()
    original = re.sub(r"(?i)\bDeluxe\s+Editon\b", "Deluxe Edition", original)
    original = _RELEASE_FORMAT_NOTE.sub("", original).strip()
    # Digital storefronts use [Music from ...] for the release subtitle,
    # whereas MusicBrainz represents the same note with parentheses.
    original = re.sub(r"(?i)\s*\[\s*(music from the [^\[\]]+)\]\s*$",
                      r" (\1)", original)
    original = _RELEASE_MEDIA_NOTE.sub("", original).strip()
    original = _RELEASE_FORMAT_NOTE.sub("", original).strip()
    original = _RELEASE_FEATURE.sub("", original)
    original = _RELEASE_FEATURE_BARE.sub("", original)
    # Case-correct musical version labels only; keep named artist capitalization.
    original = re.sub(
        r"(?i)(?<=\()(extended\s+mix|radio\s+edit|remix|club\s+mix|extended\s+version)(?=\))",
        lambda x: " ".join(w[0].upper() + w[1:].lower() for w in x.group(1).split()),
        original)
    return picard_small_word_case(" ".join(original.split()).strip(" -"))


def release_title_normalize(title, release_type=None, inferred_single=False):
    """Single/EP designation always lives at the end, except plural Remixes."""
    value = clean_release_title(title)
    if not value:
        return value
    # A plural Remixes release never gets an EP/Single suffix, even if
    # the source says "The Remixes EP" or "Remixes (Single)".
    plural = re.search(
        r"(?i)^(.*?)\s*(?:-|\s)\s*((?:The\s+|Club\s+)?Remixes)"
        r"(?:\s+(?:-|\()?\s*(?:EP|Single)\)?)?$", value)
    if plural:
        prefix, label = plural.group(1).strip(" -()"), plural.group(2)
        return ((prefix + " ") if prefix else "") + "(" + label + ")"
    enclosed_plural = re.search(r"(?i)\((?:The\s+|Club\s+)?Remixes\)", value)
    if enclosed_plural:
        return re.sub(r"(?i)\s*(?:-|\()\s*(?:EP|Single)\)?\s*$", "", value).strip()

    kind = str(release_type or "").lower()
    designated = re.findall(r"(?i)\((Single|EP)\)|\s+-\s+(Single|EP)(?=\s|$)", value)
    explicit = next((x or y for x, y in designated if x or y), "")
    if not kind and explicit:
        kind = explicit.lower()

    value = re.sub(r"(?i)\s*\(\s*(?:Single|EP)\s*\)", "", value)
    value = re.sub(r"(?i)\s+-\s+(?:Single|EP)(?=\s|$)", "", value)
    value = re.sub(r"(?i)\s+(?:Single|EP)\s*$", "", value)
    # "Maxi CD Single" is an edition descriptor plus a release-type
    # designation; retain "Maxi CD" and put only "Single" in the suffix.
    value = re.sub(r"(?i)\((Maxi\s+CD)\s+Single\)", r"(\1)", value)
    value = " ".join(value.split())
    if re.search(r"(?i)\s+Remix$", value):
        value = re.sub(r"(?i)\s+Remix$", " (Remix)", value)
    value = re.sub(r"(?i)\b(?:feat|featuring)\.\s+", "ft. ", value)
    if kind == "single" or (not kind and inferred_single):
        return value + " - Single"
    if kind == "ep":
        return value + " - EP"
    return value




# Reused MusicBrainz DiscID/TOC algorithm and EAC rip-log precedence from
# the project's CUE Corrector - MusicBrainz DiscID 1.3.1.pyw.
_EAC_TOC_RE = re.compile(
    r"^\s*(\d+)\s*\|\s*\d+:\d{2}\.\d{2}\s*\|\s*\d+:\d{2}\.\d{2}\s*\|\s*(-?\d+)\s*\|\s*(-?\d+)\s*$",
    re.M)


def _disc_identity(starts, leadout):
    count = len(starts)
    if not (1 <= count <= 99 and leadout > starts[-1] and
            all(y > x for x, y in zip(starts, starts[1:]))):
        raise ValueError("Not a valid physical CD track table.")
    toc = " ".join(["1", str(count), str(leadout + 150)] +
                   [str(value + 150) for value in starts])
    payload = f"{1:02X}{count:02X}{leadout + 150:08X}"
    payload += "".join(f"{value + 150:08X}" for value in starts)
    payload += "0" * (8 * (99 - count))
    token = base64.b64encode(hashlib.sha1(payload.encode("ascii")).digest()).decode("ascii")
    discid = token.replace("+", ".").replace("/", "_").replace("=", "-")
    return {"discid": discid, "toc": toc, "tracks": count}


def _exact_cd_sectors(audio):
    """Use exact decoded sample counts; never estimate TOC from rounded seconds."""
    try:
        from mutagen import FLAC
        if audio.suffix.casefold() == ".flac":
            obj = FLAC(str(audio))
            samples = getattr(obj.info, "total_samples", None)
            if samples is not None:
                rate = obj.info.sample_rate
                if rate == 44100 and samples % 588 == 0:
                    return samples // 588
    except Exception:
        pass
    probe_exe = shutil.which("ffprobe")
    if not probe_exe:
        raise ValueError("Need FFprobe to calculate exact CD TOC from audio.")
    result = cmd([probe_exe, "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=sample_rate,duration_ts,time_base",
                  "-of", "json", str(audio)], timeout=90)
    if result.returncode:
        raise ValueError("Cannot obtain sample-exact CD audio duration.")
    from fractions import Fraction
    stream = (json.loads(result.stdout).get("streams") or [{}])[0]
    if int(stream.get("sample_rate") or 0) != 44100:
        raise ValueError("CD DiscID requires native 44100-Hz source.")
    samples = Fraction(int(stream["duration_ts"])) * Fraction(str(stream["time_base"])) * 44100
    if samples.denominator != 1 or samples.numerator % 588:
        raise ValueError("CD audio duration does not resolve to exact 75-Hz frames.")
    return samples.numerator // 588


def toc_from_cd_release(folder, files):
    """Prefer authoritative EAC TOC; derive CUE/audio exact TOC only if complete."""
    by_folder = {}
    for file in files:
        if file.suffix.lower() == ".cue":
            by_folder.setdefault(file.parent, []).append(file)
    if not by_folder:
        return []
    found = []
    for disc_folder, cues in sorted(by_folder.items(), key=lambda x: str(x[0])):
        if len(cues) != 1:
            continue  # Multiple disc CUEs in one folder require edition review.
        parsed = cue_read(cues[0])
        records = parsed.get("records", [])
        if not records or any(1 not in row["index"] for row in records):
            continue
        expected = len(records)
        used = None
        for log in sorted(disc_folder.glob("*.log")):
            if log.name.casefold() == "audiochecker.log":
                continue
            try:
                blob = log.read_bytes()
                text = blob.decode("utf-8-sig", errors="replace")
                if not _EAC_TOC_RE.search(text):
                    text = blob.decode("cp1252", errors="replace")
                rows = [(int(a), int(b), int(c))
                        for a, b, c in _EAC_TOC_RE.findall(text)]
                rows.sort()
                if (len(rows) >= expected and
                        [r[0] for r in rows[:expected]] == list(range(1, expected+1))):
                    starts = [r[1] for r in rows[:expected]]
                    leadout = rows[expected-1][2] + 1
                    if starts and starts[0] == 0 and leadout > starts[-1]:
                        used = _disc_identity(starts, leadout)
                        used["source"] = "EAC ripping log " + log.name
                        break
            except (OSError, ValueError):
                continue
        if used is not None:
            found.append(used)
            continue
        # CUE sector timeline, allowing image or one file per track.
        parts, offsets = [], {}
        for row in records:
            name = row.get("file")
            if not name:
                parts = []
                break
            original = (disc_folder / name.replace("\\", os.sep)).resolve()
            if not original.is_file():
                # Historical WAV references may have become FLAC/ALAC files.
                guesses = [x for x in files if x.parent == disc_folder and
                           x.suffix.lower() in AUDIO and
                           extract_number_title(x)[0] == row["number"]]
                if len(guesses) != 1:
                    parts = []
                    break
                original = guesses[0]
            if original not in offsets:
                offsets[original] = 0
                parts.append(original)
        if not parts:
            continue
        try:
            base = {}
            cursor = 0
            for path in parts:
                base[path] = cursor
                cursor += _exact_cd_sectors(path)
            starts = []
            for row in records:
                name = row["file"]
                path = (disc_folder / name.replace("\\", os.sep)).resolve()
                if not path.is_file():
                    options = [x for x in parts if
                               extract_number_title(x)[0] == row["number"]]
                    if len(options) != 1:
                        raise ValueError("Ambiguous historical CUE track.")
                    path = options[0]
                starts.append(base[path] + row["index"][1])
            used = _disc_identity(starts, cursor)
            used["source"] = "CUE with sample-exact audio duration"
            found.append(used)
        except (OSError, ValueError, KeyError):
            pass
    return found


def discid_release_candidates(folder, files, barcode=""):
    toc_data = toc_from_cd_release(folder, files)
    if not toc_data:
        return [], []
    # Matching multiple discs is intersection of release IDs; not union.
    lists = []
    details = []
    for toc in toc_data:
        params = urllib.parse.urlencode({"toc": toc["toc"], "cdstubs": "no", "fmt": "json"})
        page = mb_json("discid/" + urllib.parse.quote(toc["discid"], safe="") + "?" + params)
        ids = {str(r["id"]) for r in page.get("releases", []) if r.get("id")}
        lists.append(ids)
        details.append(toc)
    intersection = set.intersection(*lists) if lists else set()
    # All matching candidates are retained; never silently choose one
    # from several physical editions with the same DiscID.
    return sorted(intersection), details


def _disc_editions_by_medium(candidates, toc_data):
    """DiscID must match the actual count/order/track-count of CD media."""
    if not toc_data:
        return list(candidates), []
    accepted = []
    rejected = []
    for rid in candidates:
        details = mb_json("release/" + str(rid) +
                          "?inc=recordings+discids+release-groups+labels+artists&fmt=json")
        media = details.get("media", [])
        if len(media) != len(toc_data):
            rejected.append((rid, f"medium count {len(media)} != {len(toc_data)}"))
            continue
        compatible = True
        for physical, expected in zip(media, toc_data):
            if "cd" not in str(physical.get("format") or "").lower():
                compatible = False
                break
            count = physical.get("track-count")
            if count is None:
                count = len(physical.get("tracks", []))
            if int(count or 0) != expected["tracks"]:
                compatible = False
                break
            listed_discids = {
                str(row.get("id") or "") for row in physical.get("discs", [])
            }
            # When MB exposes the disc IDs on the selected medium, require
            # the actual local DiscID among them, never a neighbouring disc.
            if listed_discids and expected["discid"] not in listed_discids:
                compatible = False
                break
        if compatible:
            accepted.append(details)
        else:
            rejected.append((rid, "CD format, per-medium track-count or DiscID mismatch"))
    return accepted, rejected



def _valid_gtin(value):
    """Check 8/12/13/14-digit GTIN check digit; numeric catalog != UPC."""
    value = str(value or "").strip()
    if not value.isascii() or not value.isdecimal() or len(value) not in (8, 12, 13, 14):
        return False
    digits = [int(ch) for ch in value]
    total = sum(d * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(reversed(digits[:-1])))
    return ((10 - total % 10) % 10) == digits[-1]


def _source_medium_track_counts(cues, audios):
    """A two-image CD rip is not a two-track release: count CUE tracks."""
    if not cues:
        return (), len(audios)
    counts = []
    try:
        for cue_path in sorted(cues, key=lambda p: str(p).casefold()):
            records = cue_read(cue_path)["records"]
            numbers = [int(record["number"]) for record in records]
            if not numbers or numbers != list(range(1, len(numbers) + 1)):
                return (), len(audios)
            counts.append(len(numbers))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return (), len(audios)
    return tuple(counts), sum(counts)


def _catalog_key(value):
    """MusicBrainz displays e.g. BETA 021 while source may use BETA021."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _source_cd_region(release_name):
    """Return source-supplied CD region, not a substituted MB release country."""
    matched = re.search(
        r"\[(?P<region>EU|UK|XE|XW|[A-Z]{2})\s+-\s+[^\[\]]+\]\s*$",
        str(release_name or ""))
    return matched.group("region") if matched else ""


def _legacy_date_and_title(value):
    match = _LEGACY_DATE_TITLE.fullmatch(str(value or ""))
    if not match:
        return "", ""
    year, month, day = match.group("year", "month", "day")
    if day and not month:
        return "", ""
    if month and not 1 <= int(month) <= 12:
        return "", ""
    if day:
        try:
            datetime(int(year), int(month), int(day))
        except ValueError:
            return "", ""
    date = year + (("-" + month) if month else "") + (("-" + day) if day else "")
    return date, match.group("title")


def _is_genuine_rip_log(path):
    """A generic analysis .log is not proof of a physical CD."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(8192)
    except OSError:
        return False
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        beginning = raw.decode("utf-16", errors="replace").casefold()
    elif raw.count(b"\x00") > len(raw) // 4:
        beginning = raw.decode("utf-16-le", errors="replace").casefold()
    else:
        beginning = raw.decode("utf-8-sig", errors="replace").casefold()
    return any(marker in beginning for marker in (
        "exact audio copy", "eac extraction logfile",
        "x lossless decoder", "dbpoweramp cd ripper", "dbpoweramp release"))


def _release_catalog(detail):
    return [str(row.get("catalog-number") or "") for row in
            detail.get("label-info", [])]


def _matches_catalog(detail, catalog_number):
    token = _catalog_key(catalog_number)
    return bool(token and any(_catalog_key(v) == token
                              for v in _release_catalog(detail)))


def _release_medium_matches(detail, physical, expected_audio_count, source_medium_counts=()):
    media = detail.get("media") or []
    if not media:
        return False
    if physical:
        formats_ok = all("cd" in str(m.get("format") or "").casefold()
                         for m in media)
    else:
        formats_ok = all("digital media" in str(m.get("format") or "").casefold()
                         for m in media)
    if not formats_ok:
        return False
    observed = []
    for medium in media:
        count = medium.get("track-count")
        if count is None:
            count = len(medium.get("tracks") or [])
        observed.append(int(count or 0))
    # Never claim a specific digital edition when the 2-track candidate
    # actually has 3 or more tracks, or when the count was not supplied.
    if sum(observed) != int(expected_audio_count) or not all(n > 0 for n in observed):
        return False
    if physical and source_medium_counts:
        return observed == list(source_medium_counts)
    return True



def _known_search_medium_conflict(entry, physical, expected_audio_count, source_medium_counts=()):
    """Fast negative filter ONLY when search response has complete evidence.

    Search entries sometimes omit media track counts and label details;
    those unknowns require a full release lookup, not early rejection.
    """
    media = entry.get("media") or []
    if not media:
        return False
    formats = [str(m.get("format") or "").casefold() for m in media]
    if all(formats):
        valid = all(("cd" in f if physical else "digital media" in f)
                    for f in formats)
        if not valid:
            return True
    counts = []
    for medium in media:
        count = medium.get("track-count")
        if count is None and medium.get("tracks"):
            count = len(medium["tracks"])
        if count is None:
            return False
        try:
            counts.append(int(count))
        except (ValueError, TypeError):
            return False
    if sum(counts) != int(expected_audio_count):
        return True
    return bool(physical and source_medium_counts and
                counts != list(source_medium_counts))


def _mb_catalog_search(catalog_number, physical=None, audio_count=None, source_medium_counts=()):
    """Try indexed exact catalog, then spaced prefix-number equivalent.

    Search results are always verified against concrete labels/medium later.
    """
    lookup = str(catalog_number or "").strip()
    queries = [lookup]
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", lookup)
    if spaced.casefold() != lookup.casefold():
        queries.append(spaced)
    unique = {}
    for index, query in enumerate(queries):
        url = "release/?" + urllib.parse.urlencode({
            "query": 'catno:"' + query.replace('"', "") + '"',
            "fmt": "json", "limit": "100"})
        matches = mb_json(url).get("releases", [])
        for entry in matches:
            rid = str(entry.get("id") or "")
            if rid:
                unique[rid] = entry
        # A catalog may have a Vinyl entry without the Digital one until
        # the spaced form (e.g. BETA 021) is searched. Continue only when
        # available search metadata disproves every existing candidate.
        if unique and (physical is None or audio_count is None or
                       any(not _known_search_medium_conflict(
                           row, physical, audio_count, source_medium_counts)
                           for row in unique.values())):
            break
    return list(unique.values())


def _release_title_similarity(source_title, candidate_title):
    """Compare title variants without ignoring real remix/edition qualifiers.

    An optional 'music from the ...' soundtrack note is editorial metadata,
    not a separate recording. A matching base title with one such note
    absent may be considered a strong match, but differing notes are not.
    """
    source_text, candidate_text = str(source_title or ""), str(candidate_title or "")
    direct = difflib.SequenceMatcher(
        None, _key(source_text), _key(candidate_text)).ratio()
    soundtrack_note = re.compile(
        r"(?i)\s*[\[(]\s*music from the [^\[\]()]+[\])]\s*$")
    base_source = soundtrack_note.sub("", source_text)
    base_candidate = soundtrack_note.sub("", candidate_text)
    if ((base_source != source_text) != (base_candidate != candidate_text)
            and _key(base_source) and _key(base_source) == _key(base_candidate)):
        return max(direct, 0.95)
    return direct


def _deluxe_title_base_match(source_title, candidate_title):
    """An explicit Deluxe Edition may be indexed under its base album name."""
    match = re.fullmatch(r"(?i)(.+?)\s*\(Deluxe(?: Edition)?\)", str(source_title or ""))
    return bool(match and _key(match.group(1)) == _key(candidate_title))


def release_identity(folder, files, default_artist="", allow_network=True,
                     source_name=None, disc_folders=()):
    """Physical DiscID + barcode corroboration, then actual edition metadata."""
    current = source_name or folder.name
    source = _RELEASE_PARTS.fullmatch(current)
    date = source.group("date") if source else ""
    title = source.group("title") if source else ""
    ident, bracket_cd = catalog(current)
    source_region = _source_cd_region(current)
    audios = [p for p in files if p.suffix.lower() in AUDIO]
    cue_files = [p for p in files if p.suffix.lower() == ".cue"]
    source_medium_counts, expected_track_count = _source_medium_track_counts(
        cue_files, audios)
    rip_logs = [p for p in files if p.suffix.lower() == ".log" and
                p.name.casefold() != "audiochecker.log" and _is_genuine_rip_log(p)]
    # A physical rip may lack either CUE or LOG. Do not misclassify a CD as
    # WEB merely because one rip-document is missing.
    is_cd = bool(bracket_cd or cue_files or rip_logs or disc_folders)
    album, artist, tagged_barcode = "", default_artist, ""
    if audios:
        try:
            from mutagen import File as MutagenFile
            meta = MutagenFile(str(audios[0]), easy=True)
            if meta:
                album = str((meta.get("album") or [""])[0])
                artist = str((meta.get("albumartist") or meta.get("artist") or [artist])[0])
                tagged_barcode = str((meta.get("barcode") or meta.get("upc") or [""])[0])
        except Exception:
            pass
    if not title:
        alt = _ALT_RELEASE.fullmatch(current)
        title = str(album or (alt.group("title") if alt else current)).strip()
        title = re.sub(r"\s+\(?\d{4}\)?$", "", title).strip()
        if alt:
            date = alt.group("year")
    legacy_date, legacy_title = _legacy_date_and_title(current)
    if legacy_date and not date:
        date = legacy_date
    if disc_folders and legacy_title:
        title = legacy_title
    elif legacy_title and not album and not source:
        title = legacy_title
    if disc_folders:
        source_medium_counts = source_disc_counts(disc_folders)
        expected_track_count = sum(source_medium_counts)
    title = title or album or current
    # An 11-digit numeric catalog like 88883792232 is not the UPC
    # 888837922326: validate the GS1 check digit before barcode lookup.
    source_catalog = ident if ident and not _valid_gtin(ident) else ""
    barcode = (tagged_barcode if _valid_gtin(tagged_barcode) else
               ident if _valid_gtin(ident) else "")
    title = clean_release_title(title, default_artist)
    feature_hint = release_feature_credit(current) or release_feature_credit(album)
    # Source explicitly labels a Maxi CD Single/CD Single. This is reliable
    # release-type evidence even if an exact online pressing is unresolved.
    declared_type = ("Single" if re.search(
        r"(?i)\((?:Maxi\s+CD\s+Single|CD\s+Single)\)", current) else "")
    result = {"folder": current, "title": title, "date": date,
              "feature_hint": feature_hint,
              "root_artist": default_artist,
              "catalog": source_catalog, "barcode": barcode,
              "source_identifier": ident, "physical": is_cd, "country": source_region,
              "source_region": source_region, "musicbrainz_region": "",
              "country_provenance": ("source-supplied-region" if source_region else ""),
              "release_type": declared_type,
              "selected_release": "", "metadata_verified": False,
              "edition_date": "",
              "notes": [], "track_count": expected_track_count,
              "source_medium_counts": list(source_medium_counts),
              "discids": [], "lookup_sources": []}
    if not allow_network:
        result["notes"].append("Metadata lookup not attempted.")
        return result
    try:
        disc_candidates = []
        disc_data = []
        if is_cd and cue_files:
            try:
                disc_candidates, disc_data = discid_release_candidates(folder, files, barcode)
                result["discids"] = [x["discid"] for x in disc_data]
                if disc_data:
                    result["lookup_sources"].append("MusicBrainz DiscID/TOC")
            except Exception as exc:
                result["notes"].append("MusicBrainz DiscID could not resolve: " + str(exc))
        # Barcode lookup is always attempted where known, independently
        # of DiscID. Leading zeroes are tolerated only in identity checks.
        barcode_entries = []
        if barcode:
            url = "release/?" + urllib.parse.urlencode(
                {"query": "barcode:" + barcode, "fmt": "json", "limit": "100"})
            barcode_entries = [x for x in mb_json(url).get("releases", [])
                               if str(x.get("barcode") or "").lstrip("0") == barcode.lstrip("0")]
            result["lookup_sources"].append("MusicBrainz barcode")
        chosen = None
        if disc_candidates:
            # Discard mismatching 2-CD / 1-CD editions *before* reporting
            # ambiguity. The local TOC is the authoritative disc structure.
            filtered, rejected = _disc_editions_by_medium(disc_candidates, disc_data)
            if rejected:
                result["notes"].append(
                    f"Discarded {len(rejected)} incompatible DiscID edition(s) by medium/track counts.")
            possible = filtered
            if barcode:
                confirmed = {str(x.get("id")) for x in barcode_entries}
                possible = [r for r in possible if str(r.get("id")) in confirmed]
                if not possible:
                    result["notes"].append("No DiscID edition also matches the source barcode.")
                    return result
            if len(possible) > 1 and result.get("catalog"):
                cat_matches = [entry for entry in possible if
                               _matches_catalog(entry, result["catalog"])]
                if cat_matches:
                    possible = cat_matches
                    result["lookup_sources"].append("Exact DiscID + catalog")
            if len(possible) > 1 and source_region:
                country_matches = [entry for entry in possible
                                   if str(entry.get("country") or "") == source_region]
                if len(country_matches) == 1:
                    possible = country_matches
                    result["lookup_sources"].append("Source region + DiscID")
            if len(possible) != 1:
                result["notes"].append(
                    f"{len(possible)} structurally matching DiscID edition(s) remain "
                    "after catalog and source-region checks; no arbitrary selection.")
                return result
            chosen = possible[0]
        else:
            # Known exact catalog is authoritative for BOTH digital and CD.
            # Previous builds searched catno only for physical CDs and then
            # wrongly reported existing digital releases as nonexistent.
            entries = barcode_entries
            using_catalog = bool(not barcode and result.get("catalog"))
            if using_catalog:
                result["lookup_sources"].append("MusicBrainz exact catalog")
                entries = _mb_catalog_search(result["catalog"], is_cd,
                                             expected_track_count, source_medium_counts)
            elif not barcode:
                if not (title and artist):
                    result["notes"].append(
                        "No DiscID/barcode/catalog and artist/title pair for lookup.")
                    return result
                search_title = clean_release_title(album or title, default_artist)
                # EP/Single is a release-group type, not necessarily part
                # of the concrete MusicBrainz release title.
                search_title = re.sub(
                    r"(?i)(?:\s+-\s+(?:EP|Single)|\s*\((?:EP|Single)\))$",
                    "", search_title).strip()
                query = 'release:"' + search_title.replace('"', "") + \
                        '" AND artist:"' + str(artist).replace('"', "") + '"'
                result["lookup_sources"].append("MusicBrainz artist/title")
                url = "release/?" + urllib.parse.urlencode(
                    {"query": query, "fmt": "json", "limit": "100"})
                entries = mb_json(url).get("releases", [])
            # Search results are cheap; full release detail requests are
            # slow/rate-limited. Compare likely titles *before* fetching
            # details. Exact catalog tolerates mix/EP spelling differences.
            preliminary = []
            seen_ids = set()
            for entry in entries:
                rid = str(entry.get("id") or "")
                if not rid or rid in seen_ids:
                    continue
                seen_ids.add(rid)
                similarity = _release_title_similarity(
                    title, entry.get("title") or "")
                if disc_folders and _deluxe_title_base_match(
                        title, entry.get("title") or ""):
                    similarity = max(similarity, 0.95)
                threshold = 0.60 if using_catalog else 0.82
                if similarity < threshold:
                    continue
                # MusicBrainz release search can already include per-medium
                # format/track count and label fields. Reject incompatible
                # vinyl/CD candidates here to avoid unnecessary full
                # release lookups; verify again after retrieving the chosen
                # concrete edition.
                if _known_search_medium_conflict(entry, is_cd, expected_track_count,
                                                  source_medium_counts):
                    continue
                # An incomplete search label is UNKNOWN, not a mismatch;
                # the full concrete edition will supply authoritative labels.
                indexed_catalogs = [x for x in _release_catalog(entry)
                                    if _catalog_key(x)]
                if using_catalog and indexed_catalogs and not _matches_catalog(
                        entry, result["catalog"]):
                    continue
                preliminary.append((similarity, rid))
            preliminary.sort(key=lambda pair: (-pair[0], pair[1]))
            compatible = []
            rejected_media = 0
            rejected_catalog = 0
            rejected_count = 0
            for similarity, rid in preliminary:
                # Correct MusicBrainz release lookup includes. Detailed
                # lookup is only used on preliminary candidates.
                detail = mb_json("release/" + rid +
                                 "?inc=release-groups+labels+media+artists&fmt=json")
                if using_catalog and not _matches_catalog(
                        detail, result["catalog"]):
                    rejected_catalog += 1
                    continue
                if barcode and detail.get("barcode") and (
                        str(detail["barcode"]).lstrip("0") != barcode.lstrip("0")):
                    continue
                media = detail.get("media") or []
                formats = [str(m.get("format") or "").casefold() for m in media]
                format_ok = (any("cd" in f for f in formats) if is_cd
                             else any("digital media" in f for f in formats))
                if not format_ok:
                    rejected_media += 1
                    continue
                # A source with N separate tracks must match an N-track
                # concrete digital release (not just album title/catno).
                if not _release_medium_matches(detail, is_cd, expected_track_count,
                                               source_medium_counts):
                    rejected_count += 1
                    continue
                compatible.append((similarity, detail))
            if not compatible:
                reasons = []
                if using_catalog and not entries:
                    reasons.append("No MusicBrainz catalog search results")
                if rejected_media:
                    reasons.append(f"{rejected_media} candidate(s) have the wrong physical/digital medium")
                if rejected_count:
                    reasons.append(f"{rejected_count} candidate(s) have a different track count")
                if rejected_catalog:
                    reasons.append(f"{rejected_catalog} candidate(s) fail exact catalog matching")
                if not preliminary and entries:
                    reasons.append(("Catalog" if using_catalog else "Barcode" if barcode else "Artist/title") +
                                   " candidates did not pass title, media and identifier checks")
                result["notes"].append(
                    "No compatible concrete MusicBrainz edition: " +
                    ("; ".join(reasons) if reasons else "no candidates passed the identity checks"))
                return result
            if len(compatible) > 1 and abs(compatible[0][0] - compatible[1][0]) < .015:
                result["notes"].append(
                    f"{len(compatible)} compatible MusicBrainz editions are "
                    "indistinguishable by catalog/title/media/track count; no guess.")
                return result
            chosen = compatible[0][1]
        rid = str(chosen.get("id") or "")
        if not rid:
            return result
        details = chosen if "label-info" in chosen else mb_json(
            "release/" + rid + "?inc=release-groups+labels+media+artists&fmt=json")
        physical_media = [str(x.get("format") or "").casefold() for x in details.get("media", [])]
        if is_cd and not any("cd" in x for x in physical_media):
            result["notes"].append("DiscID candidate is not a CD concrete edition.")
            return result
        if not is_cd and not any("digital media" in x for x in physical_media):
            result["notes"].append("Barcode/title candidate is not a WEB concrete edition.")
            return result
        actual_barcode = str(details.get("barcode") or "")
        if barcode and actual_barcode and actual_barcode.lstrip("0") != barcode.lstrip("0"):
            result["notes"].append("MusicBrainz barcode conflicts with source barcode.")
            return result
        concrete_date = str(details.get("date") or "")
        if _DATE.fullmatch(concrete_date):
            result["edition_date"] = concrete_date
        labels = [str(row.get("catalog-number") or "").strip()
                  for row in details.get("label-info", [])]
        cats = [x for x in labels if x.casefold() not in ("", "none", "n/a", "[none]")]
        if len(set(cats)) > 1:
            result["notes"].append("Multiple catalog numbers attached to exact edition.")
        catalog_number = cats[0] if len(set(cats)) == 1 else ""
        release_group = details.get("release-group") or {}
        first_date = str(release_group.get("first-release-date") or "")
        if _DATE.fullmatch(first_date):
            result["date"] = first_date
        else:
            result["notes"].append("Earliest worldwide release date missing.")
        result["feature_hint"] = (result["feature_hint"] or
                                  release_feature_credit(details.get("title")))
        concrete_title = clean_release_title(details.get("title") or title, default_artist)
        # A valid two-disc structure proves the matching deluxe edition,
        # even when MB labels that concrete release simply "Arc".
        result["title"] = (title if disc_folders and _deluxe_title_base_match(
            title, concrete_title) else concrete_title)
        result["catalog"] = catalog_number or result["catalog"]
        result["barcode"] = actual_barcode or barcode
        result["release_type"] = str(release_group.get("primary-type") or "")
        if is_cd:
            country = str(details.get("country") or "")
            if re.fullmatch(r"[A-Z]{2}|EU|XW|XE", country):
                result["musicbrainz_region"] = country
                if source_region:
                    # MB release country is NOT proof of pressing country.
                    # A source-labelled EU edition must stay EU, never GB.
                    result["country"] = source_region
                    result["country_provenance"] = "source-supplied-region"
                else:
                    result["country"] = country
                    result["country_provenance"] = "verified-release-territory"
            elif not source_region:
                result["notes"].append("No documented CD release region.")
        result["selected_release"] = rid
        result["metadata_verified"] = True
    except Exception as exc:
        result["notes"].append("MusicBrainz metadata lookup failed: " + str(exc))
    return result



def digital_date_precision(date, physical=False):
    """A January 1 date on DIGITAL is only a year placeholder."""
    date = str(date or "")
    if not physical and re.fullmatch(r"\d{4}-01-01", date):
        return date[:4]
    return date


def release_folder_target(info):
    title = release_title_normalize(
        clean_release_title(info["title"], info.get("root_artist", "")), info.get("release_type"),
        inferred_single=(not info.get("release_type") and not info.get("physical")
                         and info.get("track_count") == 1
                         and not re.search(r"(?i)\b(?:album|remixes|the remixes)\b",
                                           info.get("title", ""))))
    date = digital_date_precision(info.get("date"), info.get("physical"))
    ident = info.get("catalog") or info.get("barcode") or ""
    if not date or not _DATE.fullmatch(date):
        info["notes"].append("Original release date unverified; cleaned fallback used instead of legacy folder name.")
        return _sanitize_windows_name(title or clean_release_title(info["folder"], info.get("root_artist", "")))
    if not ident:
        info["notes"].append("Release identifier unverified; cleaned fallback used instead of legacy folder name.")
        return _sanitize_windows_name(date + " - " + title)
    if info.get("physical"):
        # MusicBrainz stores Europe as XE; TRRNT folder names use EU.
        country = "EU" if info.get("country") == "XE" else info.get("country")
        if not country:
            info["notes"].append("CD pressing country not verified; cleaned provisional folder used.")
            return _sanitize_windows_name(date + " - " + title + " [" + ident + "]")
        suffix = country + " - " + ident
    else:
        suffix = ident
    edition_date = str(info.get("edition_date") or "")
    if (_DATE.fullmatch(edition_date) and edition_date[:4] != date[:4]):
        # Keep the initial worldwide date first and the concrete CD/WEB
        # reissue year last, without changing the actual edition metadata.
        suffix += " - " + edition_date[:4]
    candidate = date + " - " + title + " [" + suffix + "]"
    clean = _sanitize_windows_name(candidate)
    if clean != candidate:
        info["notes"].append("Windows-disallowed punctuation normalized in destination title.")
    return clean


# --- Genuine auCDtect engine, embedded and hash-verified. ---
# Original 2004 auCDtect v0.8: Oleg Berngardt, Alexander Djourik.
# Original bundled Readme: "This program is free software; you may redistribute
# it freely as long as you do not charge for it." This project is free of charge.
# Keep upstream Readme next to the extracted executable for attribution.
# Binary was recovered from the user's earlier Audio Checker v1.2.0.pyw and
# statically examined as a PE32/x86 console executable. No approximations.
# The archive contains ONLY the original executable and Readme; both
# are allowlisted and SHA-256 verified before extraction and execution.
_AC_NATIVE_RESULT = re.compile(
    r"This track looks like\s+(CDDA|MPEG)\s+with probability\s+(\d{1,3})%", re.I)

_AUCDTECT_ALLOWED = {
    "Codecs/Analyzer/aucdtect.exe": "efe47eec21c33431e7fceea4b171b8a15a729e3adcda967ee0fc165a6a41adba",
    "Codecs/Analyzer/Readme": "6dfebdef58614151cf3ff009fb2ad6212affc643325637c46d379188a5d499fd",
}
_AUCDTECT_REFERENCE_ARCHIVE = (
    '{Wp48S^xk9=GL@E0stWa8~^|S5YJf50q_1$O9KQH000080Pq1$UBYPF-z^*g002M&02}}S07GwNWn*(ML2hAed3t4XFJ'
    'X0KWOQX?bS`ChW$e8TSX9;aKYoA_Mjf3|vCycdnv9gX7^oPSgP~ATf}&EPVo_09Q9;)V4H^)pVU%0mvNE-@u9X#)*NbH'
    '~AUgO?Y6&`HSX5(J`7XXB|FzcM=d2mR`+NT1=lgs9|DGqF$8*+R`)%!g_RIQw&OYO9cUCDOilXS?Uq^?c)F|T5s{Eh-n'
    '&4mW%RlR_tm?k)ikhIbZC7OGJ@AA%|M5rfe|+Y{=6hy7^2nnF=DY7TKVJBV`GH5w$rC1-AAU6V-s^ky>S2*(?3{J}+n0'
    'J(RjU7HUE5eWpYD~FzvKOSd*zE1_m9f2NbjtiNB0jZUxxd{2kyzke4TAeAFC*7K_SXZvnM5~x)#M4)H5hdQL2L!rBO$^'
    'XTm@>f#3=ftwz}-`a@Cpu2hAJIQR>pt4W4Un%?ABf2%22NmTwX{DGb3pbJM|UvTg20=PH4C)-sW6lfUynH44H`p0u;7R'
    '*$X)Ty%VIcgv9AM}RxdXY@YodrE%4yMd>4@~D${`&jt@2|hV{{H&=>+i3>zyALE`|IzozrX(e`uqR&-;}A2(<L4GgT^S'
    '1qXotXgQiUNeRoh%Z1sj2)7O`rN4WR+)hAO7=EBRT=3(f;puA;qV-(*mpP~#oY^yhkL{IE1J6X_Y>J&A4l@ALsIZ_NWY'
    '%S*L>#OA76vvUWlZBVRVXjS5?sN3A!0oM?f@I}Bd#|-{t3i4C`m&vceILx8;`{JtEJa`DDhRQ*1v@t8b%4ovm?piWe+1'
    '-}>8rFq+o8BgQyk4@`y5B7+6ycud$z?;IPyzu;M50$oXHl;gO+sPMPS|YDTYtYU}EX|g8V#FU6PVl1&Mr#hp5U2gM3HZ'
    'pe_SMO!CbEI~?t3Nk?JLgV~dO{R0hK{|CcJ&O21A7W+dRbt)A58b~EYmdJ^>j2mYxTNOkh%haSPV~u6+h)~ph4;Pq?Ws'
    '4x>NVY^LBwNhJ^5-FRpRLGpgJLY5E9IL^#<IsjwzZp{?BPs5?AYNOa=xQuGB!2MVt!Dc;T!T3JqWOL*cXW9N6xZD6bvq'
    '%VKA1}gUyaiOC;nv@q0%HX3E4&6MZLtl$i<}jsKZt8SY54n2aC&>d3Z4#_p{Bp;zolUo6y<p0`MApstO&6xz4~jM@c7w'
    '}m)wQRJWr7NcT`TCTD5W2`*Q67P%~XZ%mHWq97Qf-y>Z2B<PAi^%XnFY1DDFh9=mH5i%(hJsnwICc&BCgJBNE)6-+c1h'
    'lB&5F4{t4}WuAK9_8lQ;<WC0k5$LOtE`Azj%%7!m=R{ii*>^5E{Phb-xN=~!4b7B=)}zb2L5(9zK`ZN~JwZ1r(<Iwdbk'
    'tFwnzrweAz)~bNNNmKK5K{#t1dmX2Jd(>f5I3O=Z8*-Ztss%g?(i8_g#v!Mi%ZVa;zE4GX@1)Y5g(uy`ilk?33D|R{>4'
    'n|xK{M<jcY!&MJ~QmkVZ1#EgIs@JO}$#p-B5U5j24`P!Eh~@j6vUeH8>1|yS3mT4AyJGi!o@`f_f@S3!bLAZ_PWdweTk'
    '&mZk-pFqp;dr}PmID!ylZojdT2l`xOWT(E2yOE=(<8K-mfPS41TcB{D^kjF8_;25W$n4WiTqpB`I_3e?4DG?bH^BOhvG'
    '*pkXIBqvXA<H%OI4UI0F)juShjI$Em}n{|(J>_+%1PDK{Xy*=m+E=a3YL2#EqT#e)R~_!D$Da^Iy_X2GHFrYipiMQxIx'
    'uy1C3)I(q}X-=7y!(Dde5E0<NA*8zAMkEhp=fpT15c^OkO`z!))P`}lCkF4T&fOBEQ}COarU478CY>sHdJIXt@XI>&rb'
    '#CE?Tw*Q1h<Y+}C$RhgY#b~-~NT<(>)-?TvCf4P@_`=_V9eL|MOHy**AQCp$el^}p4xI~8lu47POw9}5l%&L-q%z#emP'
    'kFs!VHb_ZTJzVe1ls2Y7Dw24N!bVv>u1sA#uV+`zlMgPEiVsjt1Wt5F9=`EN5+@P(*~|SI1gQBq%?}X_9c(T}o+T)^-@'
    '@|B6fLF-R5(wGtgF=a-|@V#b1W)y47G72a!Hml|ZN-DNv{%im*+tL-ZXT9poqa}nI0PYt%Yf=f3Q^h)=APm8$@Yzl`(I'
    '6TTY{~0LXS9PwV<KSaB39~KcgLx=iS`EYZ;4~6(Bp;jzVqOSb(qXNQo9K&#6~S3*NhD30B{4$`#U^O7?QBP3H(vr23Q4'
    '0M%%135`n|e*ggR$gqTBkyeg+ZgzIoqcUqIAYC@UGt+Bg;zjj;YcueX+NDm?8hvP2K5o8ahpenL<RDB2wjwFiS8M~}Vj'
    'Xs{g!Ikr4+1z7jla=3sMF}|^9J33(9H90FSF;HBFbrLi-!uPySH6UO1-~Up5U!RfnEu{J;%K8SX^%cB4$@d<N33$ZxxC'
    '$(0$1F=EJOp~$;}TBcvo%&;2fGqRwsrFb%O*hP?%<om_>MpwAY9|v<{KVNJ74(Z*iF98r=WvNEvcYNhp0<n$2oY9*m-H'
    'AdId+xFtoJnq_MOLL!%T|lH#}&_j&YE^xs|BOW8$tDBlD#Ag>Arc)HL21I;k#TLUOFI`%-d%Y&pvj_tm9v}hq&G!dfg2'
    'I%HW3i^J*ep-zSJ$O+zXk{cQ-l(QE(pZYKI#1Ci(PLt`pwojb3xc_5^C<;GQwuImExavntG0xuiiB+=y^vHc3;+3^Ru)'
    'Lp*56OPjMUj$)?Sp=;I}fI6}|BW^%(Shx-Ywxrpp^EG|jC-<A5H3r#WN#v{=_&x%OVPR@Cc3wSMx&R%JT;yB7XE0RP6r'
    'KSlRH`U_HpU{wg=^~zBjyK@aLj`pe=SZ?eUYw=oDP4X(Z1}<+^j;|)g?z6vIH38yZEv4%+x-O>cBDhYO>ik5fbW>_@WZ'
    'Ivr#E9oA@OqhhoDS=Xbvfti*PI`%D97GjGaF~oXAKa4?EN+9`8Yo-oe<#{*g=6DTqr)%DWM!FeYd+`%%om^G3QKvG4F&'
    '!V8(R&t1hy!o~|`?T}#($x~`(@3c9WoEyG@Tbva!Z(RDFhtLVCrt`&5xr0WW}x}RGKS4FW{@4~K|>^Nj=ZO`y_!;Nf`x'
    'J_{s8XV12V_j>MVi@)3kG=00Yl`iN-IU<Ae~$fVoWB&O*<`(=V`Spy!kbEpHbcL)SPP68{2K;c9pAbNuTI!es263nb@%'
    'Evd#=uc?Zq3p*<r&6VYSoE6TR6{Tic=wIqbd2e?yH(*fBSJ<mdQg3Je`KthmF54>MtheXia<*8nBX?coh=b~SC<zuwyf'
    'MuOo<2@S@|`(RUX*zn1En-bgwBR&1qVv`D^9k-bhb~rOS?3oq=_?d?Nr<>1sMx}#gb4`wo%`ThEbc-kTus7>);Zgfd-X'
    '{B89k`W_AEwnAl&XwL;A9&n!{Qb(CDv8;%hb71jxBB7)!nMY(Z?RXp{6idab)N~S__iZR%=SwQ1rutL6h?^#@BuYq&)G'
    '}d6<{69k%m2W90|J(~tNZUtKzNCtQ%sR%db=POJdQn`E-B*Lke%Hn+~^GTRQCJ-u?kTw9$rVZZmzYOl4)Gc!i$ym!`kl'
    'cGG<7B%Sdf;=-y4K{d_%pR*x4K{i~o@rKtO<wC^Pf~;$Y{B46E$A~=K878eZHY)YKWB`?4G+y}xG7{kc*BNm*feMO3A}'
    'k>kJfvFZ9nUwFK?IGvkrTc+C6vc)nI#JlVigxL+Tudp(oA<&5dzvt39A|-fVKF4LIza(&2oKdNkfX(lJ|Se@f@I>YH1j'
    'JntQP+d<Q<g+IjZjCCdaJohrkmK8w-dT&s3i+4bq-kSu;AdT$x)`X)D0CV>6Q})bLI(X!fbDw~nBf{}b?RPq7TJT}#tl'
    '!$5*{9m=;IYI02v5P}=wUl#a@5=Pj$3v1RGs5iy<?)mo~pN}8+?&wf%&|VZbwi1ZHi-(&OTWOxe%&aZ9kiAZF<`;dT@o'
    'nA%f~mMIXReF+&5<ox&Ao?xO$>BAnySJ0r0JLu%0i+)W9Ok!>cdf2+NZBVA|D&^gle4y(bQp|>X)e2Xu?K>bl&*B?>z0'
    'W9Bl-GO7bN}KPtL)n1Bk2r2K)ZikP&@dIO!#Bt1^wCH*mc`<ZcUySbCQyZwYF-ag=^b+-oYQsCVG&S?$uTw@(GEh>iHK'
    'GwrDsg^ZMd4e!gS|69c+f_lVI0;0rq=Bj;PZqY81q*02ivP!&qt)kTX2iReQi7XB4<H>IxE3=E6q7l#NuOp=T5>C}l2B'
    '<wh*i86M;f-{a}F43y1>nm08cX?A%^H{x>bT}(GzD^RmTXz<ol7}_xpcM9t$d=dY+6Y(sz2q&cV791DQ4Ys=Agj#ecu}'
    '_W9g~2>1S%*u~G9x_F{*HOLOu^>aGu9;+i*QBQt@f-IvdDhUk@sBk)8-j|3Rc!EfFAb3M295^=3etL*x7TM4vG~H*0^j'
    '-PGL)P?f%Wpo0<-KdM(0sp7Az&yH$bIw8{78K1ErBFaL)$%pPDnec7CFz!JE1dAiZcyI2JLR>N^#ifKCHnTNZEcL`nR;'
    'm#uSEI}Fiab5+*({<h=zj%_hB9ou#?|BI~8Vq!MTJK$gfwo|86F%?|g?3FZ>?=Wv&85FZ6w>?)J}g`leu&-YoEO&-f<A'
    'gA{0m1m;L8pe^*BXfeTD9aK8u%qmniR6c~6%2RC!O6_jGyBl=m!o&+h73z&%RdqvbtD-sA8dyQyrG_qhg;3VOi<if?Kb'
    'ZPj@*E%9ZWNNE+yq{2{mXjW`K<O!yzDF5_PGJTAH`WTrW>If2bWclaGl6i9c^W>;`6g5w(f1XsCC*40!I_62jW%_ik@w'
    'sbYOrv?8TuV&z0c;pZR&Tco8e7eE`_-If`wD{{-+H?hi-=9ln=#7Ugpmf}mKW;mMHUkp0cE69X;~oUc)QK+RGLB1N0cG'
    'l+b#bBWk$**k!AY|OcFNeW!4B>P$$?9>PtErU<TvDWL&TdiKu<B*B9L2w4MA7K;}oawZ2{e&(TLy=K}=*%kvajg0ie&X'
    't#ij=+9{xG2oq7?U%7yWDNgfM)du-MhJN4)%azs5g8-9s1f``t`P#>c`m<<D9@Nqeym(_<oaHdd@@w=Dd79#GC$QZM-R'
    '^2P)K&{eH_HWVld~+L0us4v*o==EJ(OiticK9?Q*6bbgo;L0fxKRB*--Fw|Mlh9A)VXkHqeD&ToYLRk-JCK3$Tr^eapo'
    '6rlPis*>~px>C}a{B#9RdBHL93qOLMj9>U3^n3imS>B`=|L|?zq<H`E7;lo*KRm)#*TYtuZab4`tQ-eZq3zl+ZG&LhZ@'
    'a=)7t$7KtJAezY^&?m*4I`S+Sbcf7X}+`oxUx^R@WW&gm~%&M~64ezfAotw%_z+o8}yZF=<`X1J@3hbFHNco|tp7B|SV'
    'C9>{0iFymQxKp0=L-WsYXw;p@H@DL0MD>AU)&@MQRYkIA7A+6t5bxVPIvuIg_*#+C8*`WuI0kA9<j7tL4ng?lSshkR=O'
    '~rL1wgdT9U(Nvqlcj@0m)aI8{H-!v0&-xYMM07%b)WSOgQT7`OL~fJz24T*4a7Xp&F$X1+8^JJiwe#c)WPbUYAvb5CfM'
    '336?zC@LaL{<z5@5Q#dI@P7NR2w=0gL`=<`5VCZ$q)K!I&CmJNa=4|W4&y*mt0H~{X5-T=6vdD+msbl(e`6@{V^nq#yt'
    'OhiZbMy{sPk`Ku;eD7~3*E$<Ie%%TfuY~6S(G*#7ZS|IfJ;ntN(KBXe={J~Q$Qi6YMfxAut+D$$?+DbnurWEPx2QAwwL'
    'lM5dhpTSY&+Y{1354Sq>L)sOI}@?B?=6Q2DcKL2s6r_elBSO)`d+jd<iVFLVP^<)kErR?Yg38op**fE5E4*n{XE0aTv!'
    '`P@xF}14tGw6HKQ&+C`iJWj)CS58<2==?Z(He1}OyOvH&LQ(+7QB1B9)zzi7Ywpw$-w?&SGJ#(&s`ms+gP=$UB`1uKYi'
    'duwkv*td3H^&s<^n?e5vI0Wc9KyBRIWIaSROSB!PBY_UaAurG(U}nhotlRld<^@r1Hmb9W&~qjb1(#gW)A8gn99L!5RB'
    '&_?a~|!^Deejh^J2)oFyp3Jp!M&C$)WlEeLrYJ>8=`sX{P&QcWbJdQu}ui1(zLNw9iSqd+L7f<37*q)hju#*+}?Nww~;'
    'ZQcYZ&!c#(dXLrQu|{~T0M-D_#dxgo9;>x^6IEU@``r))yg{tLqF9xqJQWqHZ1z-CsdB2PVv#Dxdn%Txvei?uLdepjif'
    'T0~-BVGc$`PIl7s<_AJjGbCr?{A|6?Cnl>ms@?qw5N~R@1eHu9&}hi!i-?VwkdjgD_p0BxNvNugYM$NtMC$2vr8t&8iH'
    'hM+sRqJw}ZJ)8kbcOt-3XmZuo2_7r2Ko?<;+O>~W*tC_A*bd8~FJYBH_c<foS=g;*}_HUFuugci-s*F9a%GmR&j6JW)*'
    'z+LUYJ0$twz<+v&cqZd7^Su*M@<Q>$x~%qCZI+6YS;wUf(+KuHJ+|kx~9@KovvAQ&7o@^U9kjck;z!O6o6OrHt-Iu&0E'
    'oTZQhKYB59(Uw>EEU-VE1TzYVQW-*g0L8TyUhrmmt=MHKb8Ut|wY0dyfuIf_Q(J4KpQH+jvy_^##jy~eU@gK@hA|8Ah;'
    'B?Gu=_LrjH4Obj5nMjTSxvd}gNEqdrhf&@ocy*=+mF+BybS&H$&Y2c&j0nsX{)b$_GFPO3E^}b6$Uo!?k-04XxuODdS^'
    'khqCv!#n=ZXo;75#@?-DIvf|6K8bx#IqiD^%u6^v`7t%$4|uTwyX-vVX4Bz+B0H$fcLL()@F!2j)uqL$2;JSEhfitiW8'
    'EopRY+Jw_n{Gd{Nu9R0RPz`5t%4p%(Ow|mZYQ#_^HBVi8#yo=;&QpxG91?Y6V4N0|KCjb)ST>I>P;Zj97=f|O4<PUi=X'
    'x~eEx-S6OG3UKa=qb?N1G{h^5!DByI{c!7JQ;cs)e|QJ?HsTHXPEqo3ZnE8BF^N=i13RG_K%D3WSITpLj2>*o{T8JIGu'
    'l7lqVy`FRq(^T#P3p-Y+iHKQ0~|T4<@QHgy#C8u1;KC(c990fHyS1Xi{+3QSG_bWs59GGP-Y0>$RC-r}ioJqt+};Z<lo'
    'vt8aSmy~N7D7L{bw!xd#pvE>*Y@=Unqc^KjjcuaXCcoGwZ&s5U+d{D|ez7gytQIxaN3lM?Sf4k`r^X(p*u#FYNdKy_?G'
    ')SY7uya1$XFH%4ytp0_*W6=wwP6y2Yun#j@W(nwYuupgA}-}61VEt6%nFa^`#<osSJsfaD)72@Eo(i2aotnk5$}y-gf$'
    'iIXBu)Z!a2VI~_9T4%_M9=Vl^B<t<i<J32h21}yDn?_#=p6;cE#6b@3_t_@5I)}&w*+(pvY6-o*>=dB%BRRUHu9ILtut'
    'C}M6>$|859F@Sj1_!3~uS=xm%4}z_rZZU68La7yR+Fi#noM2SWa_FWQ|D#{UAS2hT~!v*b!8D<RTj~?vfv9>X6~vobJv'
    'xbyQ<9Gxw4Q8R~FS(Wl>#M7S&Z{QJpK(UAVHCt}2V^y0VzADvRk{S+@&U7T;B6@m*II-&JMtohu8yaAnr6DzkQ7nYF9R'
    'teq<hyKrTxT~(Iab!DktRhHUmlGXLV^#h#D(`RtIto-n7M+Yt~`vh-*2k^20dYT254zSeb){nA2469NR0dZ5a%TuXafn'
    '_hI8-)}RB7~GBvWj$<ikM3kN_VNq1vg`5>zR&@z_$z*7u+N80LYrgFnJ!$Te>xUVUXbISAwW0&1XEl8lilTh(Ra@n<@k'
    'VQmV<qgS2G(4>A{x^mCKg@MQ+i;;pH06MpB_tjyAsCL&1>0uqD!{?l!u1Sq)VG#XhL=3Pi$x=?ynQDFZj4<J+N%_8nZU'
    '!BO+lGD3;W4Bw7@0g>fUe6@T1K-#~2}ldrU#=D}0HeK2@dHnL`6{uIq<UwV>_&tSkSq=9So>6b*%NP1LGm*ZcHbFM_6Y'
    '`vg_sEM)EJ0Nwx>keCz$P1EslA}z1vgb9rN*}uYE$I<0X23Jk{!$N3W1mk{$Eu?ec_F$4m5rd1{)2$Vq!jx??`Qex8u&'
    'c!}OjPt9`7qnFbu*^)B#bK-kTGITXYw1vuEpOGV}&cKvJVy><UNSQ|j3ATB$r9tfuD$`zRp~F^GG<Jl&)KV=vC=udTiP'
    'uI}(5^+18V&nzr3DXD$)+!~tQ2L&L$XHf<As(Qp-Bc2k6J>8-S}V^Tbk&A7aM6`>Jkwx;^Y^;@VL~qNgZU55H`HDMpI0'
    ')FGYjxOR-LOIgXI~;LlxNNz$*Qp<P%;uWCUY(<_x4(zpa8j3zH#M!A=Z(_vrA*Gf8Nl5?SDu_CxGtOmI*?1K#7mqwzd;'
    'vqqmOi(EkpbdC<GZk}V=i$4$G$+hnd|mhCDw@f;j&gC*Eyha?06<zr4R>R9N2R40ZwoD@!efpS$?Xf!Q2Wcvi5PR|I~K'
    'Ua!--2uSZ}9*vGhL*9<l^O&~jw!R!9TU@^oJ})$&TJ5ffC(1Xwi^jH#5pTDBw2nQ6(In4aPL{8MTA%Uzqkr$U?}D;6!#'
    'On+IJzMQ7pGBSNnSEd(UPpICHoG!Z(kBd<)?(&uL6I<=+jJ&MyTF8;_nNN>ZJltf<Y|s1}3gcBv{9rINaIt(uD`dm5M<'
    ')7?^^!dz8iENbWr7O%_);l*wd|iXXPPA|V`94R-A`nXEa<vNcE}zn6%Vsm?GZ|Dr}1WgS@g(`e16QHOIN_ywA#fpry|8'
    '@H>kaEcywD2?h)*S8J&8ez`qyNP6#}|lWEbp>GNgx82Yx`TS61V<$0xAd#N};C}Ulo5}HSAsQ7_UmRBesb&Zxfj9!qmS'
    '!JBx36m&UxSy(jK;<<UfMV$`6U!=V)G7ndA3-Axo-~UA?mD>Z_SZI_@js#@-p`aAI*zZ>#~LP0iQQ>y51Kl+zvMf|tV3'
    'fRn|J+~aJcBGqtN7-rZ;}H(a~W0E-2QOa0K93oc%U~<NTbPzB&!NOj?@`XG3glSwmq@*gwBIjbcH2Li^lshhe=}ch+t='
    '>quL#v!|Wac_Y_5ZZm|OPdHt)*|FKNaaW5oalO+}M(+mFEa8p@^bDMdZl}%-TYqA#>#NfVjR<RvWyq3`9BVMnFAJjceT'
    'j}8;5I}IIW}a=kh6~Ube6x!64}-pk6S>zN#JKd7?v=vF3h8NbwM5r@~lxP=|Y=pn+|v*gPLl+5kv_^H68RuhT`Es3e<W'
    'd!=R|}kW;lk0p<;HClnf>5_8eggNo?8GM90FyI8^#9jC~cU0@Cv<1}o+qZfYmWct~21>keACf;KiDXfY2=thz?*_Ir5U'
    'bTp8$p+}Yp&ng~NEPF;4E6MCLQjV*9||T80LyiKJUWwCXYyG3$mlwZ?$f-vsm>cY(%Vc$Vc}MMDIhY($YwXu)`72CBk{'
    'aJI0ch27>`a_$eG&jLPF|7HYdz6pg(4;yk5LFiNld`n_(n8G@~;O#;S8n5FU<b3&98wY_4EiT`;N~8(}oH;J8t}FK66('
    'XW~ZnxffaT;fZUHKhZJA@YU(E4#*mh;|q`O8t3Ep0$@-+_BHhKS?FQMdKjA6%|nj0p-*D+DvoSRxW^KMeI+^=S-m{$8('
    'q9;cf9tv^YC$-NPtHj13DNUL$SkyuuG{YMedRQy*^U*x-6g%I#U=3+0|4_ZwJD?1j45o;0a;RQG%v~UB=2R?7C@s>OJ('
    'RpkZY@i{fDVMAjY%sXZ9H&UN(|#ktmkYu~ZA{q!cGPY)tJo@KtxlmLCBy;n=KWE-8hNH`O>Lm$RFPM`V~hV77Ar*0#J@'
    'hw=EJkJDKtg$Co;L|b{_NyA_*iOsSt`_v^%W4aLb<_>n2up&avL5Yltg6Qa*}0ZNP!Vhd^=Jq@mrTb{Bpw{M=pbMPF?s'
    'G}oOzDTLmF(g!JkpN-<5q+U_xz@7A`zYeQ<Qh5pU`7Wx{(hds5n+x(zv&F^Wg;y&NL25>HY)ga#mI4TfjKf@XzLmmuC;'
    'fGxN*>_15JLujlX7tSIg#UVt)8PY+T#g;{Kqd|p?u<oyg2;6~;&hcVhiJ<9RMDNS8@Hq|n2DJG@PC3pFK`lDhMV5lLP)'
    'JiC-oJUeQ?cR=Ob!@~q3%#8+GOZWjJqLJzHc#vig0g;FD08ihG4Jo4?Kn*^oFKb(=L))Y@?Ybws<iFQ#;!-8$E+2Y+99'
    'Qni&S?UR)Bwn!cCYEe`Hr&vKC>qIt7N7lz&dt#u^eI)_<|O$PwYeD96yK?}zKZ?i{dly3wub#GOM3`SZQzm*GP$c}_*2'
    '4m%+^KxaFhR`4zhA8w~9;D0)sSP;~ay*RYkRu7;DZpdPhEB|aVH0nxeBgJ;IMy&S8P<}@F9042_eAVk@Dy}VoZcCpl(2'
    'a!*6VOTan+<L_@qKkIy#JB4#3x(A=?2tCTzwxq28?a#}7gYfPlO~L)_xnKy7=-4DW#T`2CjwPf$kU3CbueB?>mcvpOu<'
    '0`nIvSSQYQo1Kf>X+~9T1;vmJ2o>pcq_dUg!|y)v+ScT?r9uNdgFLtEJ-3?x{Aq4@4;8w#_4oA8+N8axKs5$&E_#<D0C'
    'hy`?bkZq3kT8uz&TvH{s+Z*FLwmnOVBLGm~-|LG}CLt(*H+=Ev15<qgIwQVxK(MN-}nbH{^fR-dV80EEQ$p)n0-f?09Y'
    '~$*Y2?-y>`rbaqIH&&TG{mAIEdub0%(wU(~y@tUxKUeZ_2#Zd>(4w57G42NeM1&gh7iCk73bq~0(4bDn^i&(&-oRv}75'
    '=Y%bu1iDQ&Y}(UxQAeYrXhRVbo5yMXODokq01e*9`+yyI<6*r#<^}-eTkc{wRjy56-5=kg2y6l^@dL8$6<Wp_8B-v%~N'
    'ALV36XGCS&Cw>_1oyoNJHK_<WJZrx(wvVdFcE(^V(1N^d!iQS5(@xnyJK9`&T^MK^m=P2MH5@w<qX*XEpqvG^`t9q$z&'
    'XY+3MIZ=eS^m<f#%#e?Ah(sa-)$?99$;tS-qa4fQv?3m__IG3O*}a!cax@bB<$>jjc)Z%*rCIV`G|4zkZ19kDcnTVZOF'
    '|od<zuY8sDnQ45Mu%QD&g#$0wOzhJg|wzz`{N);uTAjW6wi7FAc$!-8|%kv+`Q%tIV&JA>Vp+7nRguYUn9?T+*6X$%3`'
    'L@n&B`fg&j9Z^9h$e8=OtmBKIlpKJ!MERkg$#`!M`R}s0AaD2Ss_;}4zU>p3(S%P*sqYp~2F{({3-SH~N=k40zgtCsg5'
    'za{wj_u-na}Qgce!~9R!Xr59_T6kM*+64#BVK9HHEr^~PKH(nlhqXzxc!{B$zBnOv=>Z<!X;qrj62qM#s}wu@$A;rJS-'
    '=#qMxGp5&+gc81bOqF23mT4H4l;7|qeX%Ra#`OYlR+a7Vk(^a)MAxY&J;!;Z1xwtDlB_3os+u{jy5-ATDEyBe+bJ96xI'
    'WZCZkA<BM-$^Hs%E%rO|-AS{4F<&y;Sh`}EqNEy2ue}m(H3;q8NtxI5?G3l|?hTK&xRcV_b~aeuNvRWSE-SMqW!saI?M'
    'c!0q;Pu)0mxU#T`57xYELS#m#iga4RHZa_6C3zw4m-plsidx=*zF582#Hnth(2oWN6rYB~%gl_i5WeX@2<2VTtY}i|va'
    '6pp5%TzY~=4=F_j8b|)p?{U)ZI{rczA{^m|9x~<Q#(e4u5w>Rz>_ME!}vF*+K_ks>L-o+uG{opQH2vYOD??6YpdT!<bc'
    'L`$ejnj^U4)N^Sw6JsT5(KbAP8<LoLhG6-&osG95R>=pc6{__%kxL|uxCWrGh#kdlt`C5G^k%qp?jQe#DWB?dz}7MQ0l'
    '|(afa_&he61M4wri#4j}rDZhC}!TqMXY`*gE=9E`7ct9^Qudt9`lB!Mo*JuXgB#(^&0JuVR`ANzDDB^kgz#HQM(q}ivZ'
    'yT@fJN}Sa_4<Gi4>z??`JrBQMjlMk*bb64k^__gdJ<ovOe5HdC^Uw%sN078*NkkBa=`^|$?XoX1Q`myxVyo4@B#Oe(6r'
    'Tw3F%*uY@EC}X#|>jiB3-Q%mrTFT26iq<MeR#z6qinMnUwEFv>66<#rXLNU^7T>zn_3MgJjrNI1p?G>48mTbFgM}ux4{'
    'GmK%xJCHVVhU~jN$PZWitDV&7%Qkaf8QhVYlS0Y`l6qihC$zBSlQ8*mJ=@ibS@F=ty*5%7CDVYs6gEZ}B>SvI8KC|}#*'
    'bLG~N66+7&E^oz<`C-V5VdSGg)J0LM!P8-jp5>((QXRIg=qau;Y9I!b`Wo+a59A_q3sk-qp%rmr*I~PN2Bck4A<@d{Rr'
    '3&(sVDj2fyCBYxOr5gY6)7zikBCuG4JSX}0UgcA`NBqwN&7V7QoUr*JgIk4D=m9H-OzoWhAZ&2|bW>$Gt~;WUcB5^bk&'
    'rcUekZi<q<X}%L|2g#aDV+Ewzjwzpj?I7Lr%y6{5n`V1A&Gv3&dpEUgGleY_PD0x$9NkT`ox*Vxz6osy$v!W!8;uu=OQ'
    'yICw41_d6gHvV6wZ`+!R}B+X*6C{3U-5(@&wrp($d7}-C#FJug=H$YYNru4%O@qCA&khtU+iug)J1Oc}d}D3>VWlr*Is'
    '_k3+jDoERzyIIx|<$ryIgc%g6_#p}^_3TILn?Nq`P<>xB~dck&(lAuno9i-GR=UfW5gA{L!N87_R+ru>5!^rk9EGq(Sr'
    '?4eVj76}W!qH(`pHnyv!^Je#DV!Ll_N$e`$zfW*Q#dV5o2L}c4AX4aYqo=w6i59Il52KoFR&e?-xFw_>NVT-n(cbB9Rr'
    'F5ZKtqBuh~xFXuY-`QaDbp*-qg^z1pu<3MXUOMYii{P1MtxNO75zb|l)}9jtxsJ2%)3(xQH3H%Qr|uK5D&2I<jRWOsMX'
    '?(Ul1-O29mSXLC;O<_xSZC#{rba%~e3deQV<|u^|yK8n+IJvuKH-*!>Ykf}P%<kG)?*Tqnb^Kzm9i-F$Cfh->+<mJBYz'
    'OJ9ePnwN&GsIe?LBBc??LN%53;+5*54G4?m>2<-4u?a_?yvg%9Ti0E5#-E(AE_Sr}farIE6ENXnRmkMR~V8uK;WYDJ+B'
    '5M3BB~om&nzgY;%9tt&k>n|o?D_oV*rsg6-Ig)Kd4KBC<ej;8ozw41_lJ=Jw5p2CSDKh94IC->C)yC;p4o-|G<F0-e$9'
    'vWcwl@1;Tc7ycHVcLg4YOl+E59|ghC!G4)pxJHE>^6|y25oLq*kaK7o5IlsZ6Bg=oIzVxD4a-PJ=#s-WP`S@P&myXUQ~'
    'hY6wWkgbF-JC3|(z%1=~SdR!r*(NUx1*>;bleWVnLXm0p_dy)@f<(KzWv<D?ha-Ah}?DIDEP8z&TwqcGV`c@rtGmEw{y'
    '&Pp~@IIWj9wke!RVeDH4oYc=>``3+NGf1uDaJ=h5`g!yHPl3%KUAB^J?ycF}TeG=0^>=S=->0ypx8`3_IGW<AzbPEoTl'
    'EX#d(-%)tCixCDKCu?3a9nf_Du?BQW&4VVpNn>)&GL^S_jhky*TFeMtoP(^F^>5q<3GSabnc$HfnYo$!?>zo>16gRM)v'
    'E3P&5Y{-$sog=t<=IMJw%11p7-joLV&aGFuGox+($ZJd}CW%Zh&qri5MX8wcbCP=5Fci6yokUn2e{)$Pn-K5!WqH$uPa'
    'bhC7P2yRC-4u>CQ9ICX3dfnW{-$sug~M@tQ#jeA*-d4qQQ7Gfmub@0lRk<v@U6m3uo<Lb2WXstH0a#K(O@%3kG@LlNgv'
    'JTKAO#asK5JYehP&xeMH${H-)47X#Gv$IEud&?WS;IA8pU1a59FilhN)zWOpC3o8mJ2X!}lIMR{z~WB&rXK`JPqISJAN'
    'r*$pZ4bq2GF`cQeW_MrB?!IJqUv0mnu%)lo-xQ9f@F=vK!f}1I^}8>PZ@OA3F1fEZzA2p6SDTj<&ZIE*qXJm?r5#5T!E'
    'TUh&eHe>Nx$&6HDEVLb(hfi?kDX|wje}N4&FKpQRjy#2c8Dhx%_g(lKNk7J-7r==f#m%1D1@Scl1e-^M2oo;InLgXb^z'
    'U$}a~(T+I3tQQ3e&jkgm9J@d|qL4ZNu9BDxe`tJkPaezS&-=kvCt(FC80)s~XQ^la$w*MX@FzD+as~B{DaQEj02EF_(6'
    '@v~<h`Cx|&=J3>7_|Gw+P4J;y{Gr_(QB|dpW%~mhi)BPbPb@-`@SB5=yS2>Hvou+@WTKsZ#UUrYG>5xx<f;q;C)HdIoY'
    'bB&YLyVd8JiFoe3bNy2qu_nkA9vV2M26erV9Q0(s7QC|)AZ3DZtT3*@;YVWibQ-$Y2TJ*X4nbY%r1PVxr*5hqv`fH-fK'
    'h!aLSg(Iv1s1xlZ)Cr?TL$LnHGfE=Q2`_y3ia?%4Z6hS|yzKelIRbfJ`CB~NOenKGSVf#*Ga^CPU9LdXd6O#ubrzG&ey'
    'DSdM4e!Bu(sR#Ay2ZIk*BcP4|zsQ<oRLA2Ym(djD9ChBF~vEYfcH|ng1B|a|qcS(g|_8vO6J8L=1k2(>lc(fH(=&`6Eu'
    'U*$;8zj_Zdwsg3@KbErg|3tEp}E)eI=J;Np9tll*6gg~68!cl0mj%?O-LYl=>J0ne30MabZv<4te;hp#)%}8qi(j?U6h'
    'ct!Fen@k$M4DTF-LzgH%`szDq*--e^s54CZht8eZSF=kck6^Oi?cc*Od8LOFkLh@7-5RBaRG#htEV5rB(KsRVGfoEv*_'
    '#15@F65GeRQFuMd?>5ePG91&xbPvNyC7vJ~UO4_V@l=#MNXSp$*f*iOhYG7wo3V)jFpqL2NM<@FL--ZQq>I|5nGRAMEv'
    '%(%~%CXnR~KPRBgVPtbyCq!91JrGfnm+FTotu!wD5M@dyL`iFgAEHDX{SYO2FaC%!Mk31F*VKJ35M_3DtVEPYtJ5D6i1'
    'JnAXtbHoUAw*$lC<8{2}#Dg0+A$*3qK^8)CozFC&EY)c5Q(q!DhYM@BV0VkVKQW!!{t$Wcs-qB$|BS#)JU^O-A+~fi`z'
    '1o4a>Lk=dP4B<|jRD6*K=3_lb(rW1-Jn;Aulb&gS_7|(tva<D{^O(%LjDNyA4%Z5u7`C+bmoIsK0B{ZH1F}C+m(IePQ2'
    '=Pu=0D>e>$sa*ZwRVjl!R8(sqG1#%#xtWxF`oTUWQ;_S*Vx`_6)5uSkvB>d`OSuh;{=K<{)omiA-eXSozSCedLVifYcr'
    '!qSB5nJJqk4AhaSy==uxb56i(C7s6T=nED_{&pS=E?K#>1>X^2FSYqyLo5(x5|U&&?z*=*>999`1_kt2E1{>YK~nUSN|'
    'PyCQ$WFT?`R2zUC#T@iQjxiEBZhJu)CXnO5bt5Hm{N(r>Jq2?7c|EPog!I~bsfZD5?xn5Gey9;y5I@xDqVdeAk@_*7!i'
    'l{!n;AJGV;_JVsh|Cj<6wy#3xD`DQ6NX-J2y(?m=W&lCy?V)KhqrSO*Z%Lgc!wI$B0oG<_bWJVtw{QjH4uC1e<$nV}nt'
    'nGQt{&8WHjPqsAK~YTP?H^|U~Zw`_=(s8O)>h#KF%nQS(a&Bjh>QS2#<7KMN0hZf1^|AH22eP*;M_CZFA!awpui-RRvd'
    '}zqYmjzn%ejg*z;^loNT`AC_U91~~-r7wnN(7rtn$3(9#k#>r(M5a8e?f{$AW{_LnZju%ZEX0V#b}8Z*M2yoTA;;CP7a'
    'l5@#=%GZW3s5#FiV;W<qZ5eLA5;v2OUGMCxZoiB{TE7$u5+_CtxZ)-g&H>js6>`l$P&KT^CwBE?<(UYaeCqV<iT5-Hwy'
    '`^y^yQmns^=3rm4xo>BLIK2}>B%2u_T4`+fA;hr~A%e{UVOj%FB8^`^lqklAA4-gtDA7KB+{XeXdR`qWQR2+?yM_pqcy'
    'D)F>-v$+{mABiWOG04=R4453S0WoK87|^IJ%$ahf+AMpT?&4BcG1SvQk`fKWz`Ba9TfY{!uuypY)Hy2*;%e9G9YBeyHv'
    'icZ%Vup=G1(GfeiBaCb@sA+D52f?Fx(elL7_n>)oa=Wa|9WlxEAr^FDxNr@wHlM=sg@^j1GDT%wjFNG9VdrGo9C6&-bN'
    '*Y0ll=S__?z`Qcl6mvS=O9IvJtf<nl0&|GN-jC>DS2y_^tsWUGV989Hb{|gPbqMx%qCAcrHI_*as<1p&y*Lt%Mt9B)k9'
    'rN=}j`7ba$5{@FiEZ9Dy&ns^tiLPaVJc26s6E-)=ua*>uufoIG}yBM2r}kxmkmt5}X8_?_r4zjxDz5#y$U#qvXmwUh^s'
    'B$KOFjtBY4RV&AD2*&NGTH-F>3R261^B@l${4d{4dGL@wxk}|8MY>Amcnsk4&%GbJ%ke<MwU1zX=mbvrA<A=Dk*-QP{v'
    'ZdrD&+{Wum3C+Y&!>1>h0Jb`la7;gvai3gvR75lp{1ISD_rC@rX^Pr`+Y?cz7fg>xrN|k(3AFF>Tf52$5;4E=P#`%HZd'
    'Jc9$beUb_<eAcpe9Q67ZIv{jZPOs1`}9AWY&>vt}7mm^dzDS^?llwQ!5r%@h+$+T6KBUGlXsyqu{9WVd#Wp{ZtNOd;sw'
    ';aloOL_2jQ)#OxpA{sxit>Dro*LoFbC(x@R5TpxnN4|$C{J;aT&l}UgXB_Oj=uxa(qrc?cX=g917~0#;IC)k`3!scLdu'
    '7~uuaQl`QjkCT$V2bX?>Kn!Cj6&{4r`0_V)^^YbE8u|4@sTt8)D9Zd$I&@rdQ&eh$66d@V=|%dsvO<*BDU4MB3bD96J*'
    'v|N<q390yZ*Wc?d-wjg!Z`h_L%Hs(VzX`Q;!42Qh&*_+sj>g}l>YhGn3;rh6Hv=vB6D?Le;vs&6YP3syXq@B{KX(yr6~'
    '8qVf#F$1QP9uu#9GDA?+kUh#rb^nsNLc#4$4((S*VD&y>9X0038M>wTO>I%qXtpw5#N&BX03!41IA`O5eKRJ6#aBl6V7'
    '(yF~)b=266!3}P#ZBj`gd+1yBI?w5F!_}-Z}8Sz7|<w@_2K6v#t|CiXc)E;}@%6BKFjVi$7Ch0ev`S6fCDYNL2UdX&%z'
    'tMH0JE>^?<tp>$T(hxGFmDeXRhhT9e*WZ`VBY@m(E-W4)eboKyI|h#e)8mKaau+2Z%^Jk^A*9rHB9+V@^3G`pLI^~Z|~'
    '$E8!e8p(4iPS?}DdZlIa9YfjsXbPQcKS7k{p9WY1^zy2t4bEWH}Jx~<d3H@L?c!n}q*aCNvNO0G_?adi{46CxIn;_cHD'
    'Ri4i5k~|&ml#-_#EqS_uXRfIcJY81Ob&{uRy}mA7@N_>s-s8XUbm&XqLS!eNE`XyWrq-XMi*a@0=v;vuoyirz(ILwiz|'
    'oDB99`AWzh5Ufx+@l3D>=HNo^2ZiNB4QG>B1Zx;SYa~4&OldadfWCz>^{LhQptu(|6+NuIzL=Br1@j15743x;xG^b`u='
    'kiBUJGr$fHFB42QH7Hhu?b9Cevn@MY-s~<myPdwm6$PKPQeoo)%M2Mx+iI72o{2bsh!OxxgXl0z>=dLZhUh;G8`@dZ#_'
    '_;66^tv!VM?8xkKPURw?<7b@;7JfVT@+8HB+}KNn=^Mh1rim=%@vQ8+}yqI<zxzOZsO<|$<4i4^Zjjto12?px-d8AqW<'
    ';c=JbK5KV0dZPJhI?1UKg<Zcd(D@#E*hJDmU-)QO+FMe=j~H(vCd;O8PUqa{DL<?98v34ZSA`aT!t=g7A-lh#64KW@&N'
    '-s#i_%{xDCj^>>oH>dA(>SItRZf>mP<_^F4ky&ta+g3zLZmvi8#}$H`oB1!}g}FKM-_4}8(AEE>$ArL>9!jh$ke5?Bo%'
    'AqwI_WVuke3r<0+!Q9b1yqBcsX0ib&{70z0Q&&c)5Z*j2GtR2-o@Za{9m%9-@z#ms5tj0(rTJz!M%6_UGlI0(rUORLRR'
    'ZCIrO@UT#T7wB+T+-n;%)!OLx)*yqB$9Pw^u(pu>1f3jm@;K>fr$9|lg5_qyhtZRNJJEA*ra<@oM?xjx*j|on0<@g&UC'
    '#U~mPNd-EwjJqnVNT9E(JDAOJ8^P>eB5oFPIS;1?)pRrojmhD(Q#!U7gwAlxj6H0lZOZ{uHT1$lU&@O;g|0bT-?0Dh6{'
    '6X#bm1=7Z(wDl0%FKzmpu)zf?*hUHy5ua902iC+uY&t~f>Va3e;%bB*BPqTd)SdAOa*{$Rnwb$`Ckg?Tv9w|*x$Or1_}'
    '+}`N~$52-w4@Yn2{dl;jPA53bfjnIC&60;}9`#g~;NezmxK8qLZ7VNo5<J{J!N&i>!_oNl<KdLR(;Ia9#h-^GoBen=8p'
    'D1(+@MY;IIir(!zD`|u3wKn`GSYjZ;O#U+*>zox>WFR!-w|yFFYKr6Qs4!)sKS{W5SPv!*S}*!QnXY=itnpPi_QqaC9O'
    'DIXFY&smlZhcYX7<l7q__|HKf%!Il2ocwr8XxF3HGP6<4*Va*6Uu_5+mKMs!k2|o_b(&@xTWG4=8jO5_@-}uqTf`c0$c'
    'b(+m8h7@eB{;aN)|vhb2bX0P931p9UH$pDNu5q=(7f~G-)J2B@o$4%f&5!^C;lx}@^2%fX1*i%w_je4mHb=DjH_-D{F~'
    '`~-wX3^qL2OfH}Zd&e^b&sozl4870ACu1fJ3m`?DYaW(nlq#QN43e4x6&oe=!nvCt^Vzpc^@2on5T|M`Xs^KVw_V?X|l'
    'Z1(5hI-Ss<anbb&4Ka@WPG}4Y<lcn6{S;;S0LMFmd;4^JjO5-t&*t7JxVM5E`&^iNvyvZAT$+XA{CGEoe0b*F#NI*S#D'
    '3a3;m5tv{^7^HSpvB?+?&G)m!$|?mZIPHRamP##ZdUzKbU(X6qOQ55Guty>Dh%hyHhL~wNB>V2)Crf5ok$?|204NQ+G<'
    '@v|>ARZ-f$3(g-4?q#qdAkm^p!j9E3Gxi|95Q*y~EPsw}zhELydr_5@-+sWJ;dBQ10<OUP>R{n(jjNslD^sVfIdm}fLx'
    'Hob`iF=#T`tUlzy%mHl=z@DASCF_jas`Qd+uv9jEx5PgBj<O)y^%Xc+#9)L#Jvr<VL)HOy_uUFU2t#Y4iWc8?htWr-Sh'
    'A0F1WYQx1aBVdn1>GxHoc1h<m$ecXmI)z5RV=RTtbFxdz0&k!wKQ+o_uSlLhw{I@Z|*_eR?@ac{Ie6Zh7C(!)7|d$T^U'
    'pbPGewnyULXnQ2??X{sNDg^h|^Kxew+#79A#J%D6gg+9D?3du+77RUmui)S|yjRr)2S-~4ad5O%5C>;E-}7q0!5J@E&;'
    '<uaOEqzDv{VxZ_tuZ^w+asKp1<2A2j|bf(Q--r8!eZ_zvcD1^K!wzCH<$W3;vCkE8^d1xg!4Ush-Pz6Z~7V;_QNdqa}m'
    '*H(D}?e_Ok8c%9(iy4_~~1OK*Y-5wM2Z;^Y5kcxlWQGN>fx5z~oSq1MFV->vHFv+_qF2TDEw-WDGJVx?vBURqbs_|}d%'
    ')7-g?{*{eZiAV38|Kn@w<wp!yAc~Fc(;*OjdvSn)p)mX=G_LlG~P|=%)7~EM#^TI+eZv~5qY;+O)ri<_(8w_wQs5DQ!e'
    'epF8P_4{OId{>pLrdrtSaywblO{rp@omEJ3>cA3o2r-){Q<_R}muy#0AT%M!%fpXZY-LAw3<KF1Q|+n?`KEJ4Ekxjw@Z'
    'B;23x6D&c-{rNt>5~SRp>(eVi%KiC1yAtHwpXZY+LD2oVKDQDi-JkDMD?!x#c|NleB;B9u6DvW~{rNtx5@g+<=hG@d*8'
    'RCYs}f}0pX-w<N!Y0z#XKbLr0q=J(fS{7aLyWda4xA6fj7<?NZ^?Q2|TgC5K)*$WT5}Sxe?aS$mIwlke4DrUW$H>uD{c'
    'rVt8QaTxReHj-^Br7E3W_^_$$?onpxepUVs$0icvPLO&_-na}6m?M_MD@%|iU@Cb6Gq!HdoNsqFgz22RY+5Y4+%;1r8p'
    'OQ<SeM(;L!>@00r_4$Xo5KtqxzQ;_<U<pK_v9;2-Xs{jQ%^qI1%pREEHQZG!xDq%e7AL|VDLs+p9x^_>@V}Ll9KO8?A<'
    'f36eS7vZuZ-A1K2w|{R*mJ?#O2&=5Fe`>NA45Yj@1;g1IA~iI_X`nTWYN`_Pang1PI_V{RAB9r-K7+>yUR%w6TJ32B14'
    'i~Yx(E|@#=8Hl+fpMjXW2mZ0JUNCp3UZ2wib4PnNF?Y0Q6LXgnyYdRb+#NMO(*<)!`z0}Vv|kc)_v@we_e<vP_|si5ce'
    'G~_b4Pm?F?MfdTjvSJ?!|BBbivrso<WQq?HR<__3AgUNHBIs7e3PkV@K;XF?O_G6Jz)2N6u4%u^T$Cs0+r9)=XmTXw4+'
    'XF5dZBy<qIlwmsDaV@GQiF?O_O5o34Ds>WV|vD<#fvt2NDv}O=vM{5Q#c7r26zE&`HBd?tE2gYvi4|iRTjNLbv5EB=_='
    'dJuv$k_ELPY{fqVB|8<UlWYoP<c}B2Fci^NX9NwGImyG?2?$VyWXWSc6zJE*bR4SjNK?cDK~-{yJ%+YBABtel26J-Fk?'
    'rj>OAsyZ#Bk_%#M)FjFinZzjyD(;mFwOt}Mr-I3~jb|DVeAPX8~Z`u|bpDS`i0pZG8O#4i*|v4X`a78kL&jKvi!R<l^c'
    'qKm}_78_Y?VzEUP1O9Wg!=v%{0AZUji2u(P1p4*WQVUMCbjMhI#)Cl<(|yN2qW}KgBL3^#aI38@!q%>r|6QD+$W5gb(9'
    'd<@51JYt48os2Mp|hi{uVOM#|+<Ps{#utslzqI)*h~vgAWW@BJ6J}vIq?8q5kJ$^#AQr6;L+XH!)q*gLKtI--NDg<g(X'
    '47&Hmmg*K%-(=3UY5$&4joA)XGpY^z0U`z}&8RC%Vnl#b(%EwS@sRh@GbYIJT;>XJ~eBXdH)z=8uDZb73(eFuSdD1NTj'
    '>!hcSd*<jzS%XSxn;U{;9cGbZ>a^BGwoLz|M>gD$TMIzpJFU+xrBbp%VWY@+0IMoQ7Kz;<Cnl)x1)GFo?g|<uwh5>It-'
    'g+I9$d@wA@oo$3HV4+Js^A$-ASj!9l%uay^ElWcp~CKSqY*WH?@i6J>eU4{yElEdGzJ<}F`gIQ5}>4wd2>@N&yH7*4-x'
    '{Eu0<7VMwA8^c*Lf40n@Bg45eoG0y@CGE?X<rT>CW>3j2pf#iK$$g{c45A{$@<FAD$wUQ-m4wO?^NP$BiwapRrkE^YF_'
    '*B6(=O+<IPspp>GFH!s#9OqGFr|-YNl9#_D^mdEvIA3)Pujuwdlszei$uh=5tfGye^leS6Y4?Ehni=yOq=8e0@6q@88J'
    '9>XC$BM$4(Y<<=J`%5|&p;iIGFEM9iQ`8je4+gL`c8E)UB^Z$7bh`5KFPPU=;FqeCr%f+pHNzv1@K*YU#OYyI$#fjKll'
    'zj=#Kin5K{)SrI)?c1-Gxjg;>z8dgf?C|zGav1RCE(8f&z6&@#jSn%_=YeLaep86+6mN}xm*jEi%TjENHH<?PCky>7%m'
    '}>ONeJ164?ga^uHc-JN6{*`hSliEl%f(d!~8ZVlt1jokDFo+ndSuq6^S*)Ax05F}F)h$55NYCFF7m=nh<x`@%m!M1SDZ'
    '>NBWChv272$6Ns-dIZ6}NsBJQxT0(B0}=g#FBYAn@e5+#iajTA{Gyj~@+^&CF1M1)t>Ux`Iqf1YcQKcX&cc!Lzpusdi#'
    '}LGJB?ouW8n|SFN&X1+p4+TRa`E*4)fx_*?{90#E0YPjh2g_TRlCu4jR9Bw0_~*8*%)ik5GJ`#xMSwK-PCbIDYYM;v_1'
    '!iOcnHxh<@1Wv!1(IK(9!=I3^tpBuh_d0}+KY#hJn`Hdniz7$zI^@B7VzaX|>P5pqcMdTC0_agEM;foPjTX?Wm8@?WqP'
    'afZq$S034N(N7z{W6YU{KeIF8mYL4$T5lUOyrovT}aMNd~+h_CcZk6^BCWs$a##Li+n=(CPhA>RBlBYw*p_N$a#TpRph'
    '+L3f7)bcCa?Ha#)+o+B~**7TcT8_7<?cvxBvjt0-7oxr&3em8&#ZTe&KNdHoH>_4go+Ul4WqpW+i*2;#@pKhyXP*4C)S'
    '!P*+NjO|^{_O4*dR<dQ)!P?4&zkVxMF8ozj->tg$aQuS!tm%7Ne_314+6HdhR&HA(w{17KtqH_;?%9q{$OGcZt9`Woa='
    'EQsu8;fS5cNYdjo)BxZ#f>U?Jf91PVOT3tD$li!IxyR&(QyoeTJ{eWV!gBOqPo;%H&QK9-{4J__|D%i|@>6ABOP@B5kc'
    'OenF(&6~=FfW*NRela|GBxp7=>JZlqKYvmG>xr9`<H;wI0XB#rvhO7{656BL|J)oY(FNn9T9`qcJUl6^MSJC)o?JU;jv'
    '$lY>v)SGvvUeSgUrt-fX)8dixvZ=W$1jK(HA`sxg7|w_P9%<B5WmcQgT^n2ON)OWgX0&($q&Cp<Cj~poLjMiZCJ@RRC8'
    '^sxV9SBu4S!@Ypdtl8o1o8Ty7)VyPNH8;&MG)ZVPK$S?go%A=Vyd?Qzz&v-TWo@nxyB0pFHN8}zI-uohpK$`ZnLS_u)X'
    'jbyEvwHDS!ak<f4ZVYSVSR2nJBytH>)+Vzym9=TCO=oQ;YqMCJ&DtE+=CU@AwX;~8&)Ndk&Sq^9Yl~T1%GwInR<gE=wF'
    '_Chh_#DZyNtEVS-XOJawYd<HEUO~wuVbs%O$v2ThH1C)^25OBWriFwu!YK*0!*=m9;+B9%Aib)*feVJ8RFe7GIi6Ext9'
    'ET0LtGtTnMVoV5|GjbyEvwHDS!u{N5uF|3VaZ9HoeS!-o&GHX*=o5tF7)@HIci?!LT&0%dWYx7t;i?#WzEnw|z))uk0n'
    '6;&>tzc~>YpYnhkhP0gyO_1hSi79HD_FadwbiU$#o8Lyu4S!@we_rRVC`1cHnMg%Ynxc>VQmX*TUqO4?IG44X6<p-wzK'
    'veYw-YuI(|cW{Ib@-S`%x-SsTIHNY<KJYhi5^Yol2k!`e93#<Mn2Y7cIcq{gO|`wt+g@nl{jks2~=kYUr&r(>=~QsbtZ'
    'h$M*kVAkbFk<@tU{aPY52EF&6P$V^0-%6xHRN`A7+=`^em1p-5sqxK?kBvl9V|vM6A~oi189WO~jnEn*8LZEi&P2v9`G'
    'xldsWE-<@E?)XD6HQ?q{e{At3N_gWBX7+YAla1e}$xmuEzl)HLh8!`xlZLH&uO4q=qbS)(h`be7;OyAk)v5@kJCbNDXS'
    'TAT^%MYf(uJN+?JTnHEWn{V$f6NmAp^U9Bpqaec`4iIUX#WU)^rHRx4~AT^Q>{i>208=QA-m88ZF=Wi;hVO-txizGEZz'
    '4cd>)Q}~t<r0w8c=eNAMoDV?d}W(TYRD3{atTOk1T7!_m?SkGAAL+EHSRq8=0}p$FyE$;8grG2uSrrPevL+ItV%TgDoK'
    's)cOFqmjmgXFnk1<)rs|kVYP{L==es1S@ngXWmDG^6A*ms2LsH|?DK$SxQsb(3e^E(|#r5WDNoqWvcUUDg&Q6{3sw6cQ'
    'K6hLtHEw?5^>LEaxbwYJDyboDKvF~6fTV_O8<HBbZSh=NBG-nb#_Uy()k{+2&T|^6@zQK-vm`YZzJEj|HKaC^wMc4|rd'
    '*ODNsW~oPN<~DkdrTaC8_aZ*zYQ-q5si$M3NfMymnqCHCp$k50#|G_HR$Cq=sx;5w{IVjS(%!)=N@Df8dx(YK*$(i$^7'
    '?QS?i@N@~0nx+_MK8b$RQsUhuM%=Rv0?Q+&4sd39^_Z^j_#<tbxR8m7~SFyH+Td|f~fuzQYiJyi^Qsd5&Gb*VewOd)+$'
    'lBekZQ@pVxD_p2TPxS*<32mYeTJlloEJ!H$a&GuJ$a6M5=o6`&)%3XNsaCLQ!1$u*47azNsS-Jx2vQ^Q_8<Sl%z({MJH'
    '8K<E}PGsU$U4FK$;!jWrd&)k{)i<;6t8AgLk81CknYJRqqdX8@8Kat0u&AxA2b8gir}sUhbkk{WVuBB>!~A(9$$79y#k'
    'j^AJ&zros^M^Z!1`5aE0%W09+kTVNO4LP%r)R3zYk{WU~LQ-SQ&rhC|q{ctPeo;vc+7bk*5nA)3N@~16<6w~_HD+J*qe'
    '^PXau;&Bi&(puwMc4I_C0>FBsHF{Z&OK)`{xb|lca|JzC$XhA=9qnv^A_<%UUEgW{#M3mn1b7-tm)4YS2a_NDbO+1gX('
    'pdwRDdH9o#|ze;M*rYJ}a+7|_>F+96<i6k{X{B@^FYRG*ENe$W~1*sv^9_O@3YRH`oNe$Y+1*suRKvF}NfTV`(btE-tF'
    'BYVRv<yiNX;}noBUy{2hO_}m4QT_C8ss1dQiFU1L25jF^z9%?YHS<2N+UIz6W)@f#s{~qQ%Mb3LNb?tq{fovAAKW9jnO'
    'x)QArJ1ZYGzT#oBDv=5V>WTrQFt<X{L=WBbs0mDG5;IQ|YvYOK2TLzUE!@9~OQEM~Ek#R?WHS*&7lA&ZMxT+HG!7MHWQ'
    'g2k0AR<pQ@#TpjZvgl&5p2Y?hx3bvC;%*k3SoE;i!eT3nJ{AwLc$mfGEVi?FjzvYMi8>bbEE-reu^7%`1dEYUJcuVe(f'
    'J*^)3Fzw-`@w5Gpd(igAALdZhImbo!@oicaZaY_udisy-eiP1-r=kz2%z&K6HKud&xPCdhxsO`=Rsu$uV-CV`O=8vb^|'
    ';uP>j8&TmAuhn(LxnhPIA=XdPg`^fo~>C<HTbXi}ftS>A7t4$Np`EC2%OU|#1&z-tJIKQ&~S)UwhA?H`xU$Accx8(emx'
    '71Xk^ZVMXn)6Fpg!4<eg!4-k3FnvU63(woyO7hO^Bc5x%BRx#-9Gy#)%k7w>b;kw^IH)9v+Deg+P!+FbbiB2e^8y@rLD'
    'bRlg@8LT8rxZO6^+KqVqd&|H@kF{GJ`Gz0s@LmRTX4-;}%CROeUPu$ygY;u1Vu0y@9cmBRU@-W1NS)E;8(VJ_h~mw?Xi'
    'Bc8kGO6T_x-*>9>OOISQzx33F^DEP$^DEQR|2iak5}jY!lM$?qWGy<svc>59$`+&ZTf6y<vC{cneeAI6{66>7{`J!Ny*'
    'lNH>imx1lKO>oe*bm!H`V!-eTL4j>@#$Jr!*PzrSqFKyj696rDd6HSr(U@&E=x=YjbUWLpr}>haXp+U#XqN+I+URfbB)'
    '+cg4o>Bc$_dPdchPzYnf0cu_jP5v3<o=XXf+$9<&pTe0Va>io*u7IJNixZK5DE;_$2x_2Fw&Tq)nUsUIJ+t#gHrSrQr?'
    'Xc?ng4d{*&hLS*PO8qYv|%mV;NsfqxwZxdBIx`+?mMbFzp^KHb5El4yL8j}r=;^cu=<ed{K~eqa@%}t!y&c-onQL+O*p'
    '^V3pD3f_WC*Qb##6YeyzMMo!>V$`c&t)x2NPr>HPli)sL$4D@PwXzjE}U^ZUz*Ul&N{_tfB%s`GpKo_Wtp=l8L>C)GE4'
    '>EGV)FX{Yl`R<tN{Ho(On8z<`6IqMSuWTDSzp`!U{K`>+&aWIL=={<Txe4dj_^amp$~6j|U%5u*ur`;q=={FF<=P<W{0'
    '{AYNOgYY*g@x4jvaJ<<<msxS3XU2esgy$*&?0a-OGPfo!{~eD;|>0?=QdqqB_5F^r7=BM;|)Bw8#tRcis5kROeULww!B'
    'Q!L_aA+R*uxYY95PaxJOha@TUXF1DebZD`<fw{p3S+=|`Y3Uq$u3WLtCTw&1pt>5viQ98fVKmS>Eer4K2oEDv5xf-GKD'
    '_5g-9;xSeq@wdHTY=86Yy~>MGA%m4GA%m4a#W-9D@Qdtzw#NQ^DCb*I=|8~bbh5}QCvbamw?W%+(pp&mAeQ!zvMg!=Xb'
    'ETRds&lvq0xpJ_~eyPwW{IEuG(*%YCZzD|cyhe&sHW&ad2Y(D{`+4m!WG&(QgmeTL4jch}dKO6S+{@Ilr2mAzBIy@Ss0'
    ')BBy>r1M*r^qu-fk6akx{E`<VoZrv9TjogT_wn$(>Ki?&UC3H`qbK%xbbjSNkIt|3HPHE$z6LtK<RA&>mwY7Q{K^)uq8'
    '4ve-{{E_)=~+L>Ki?2Lp|Hjz&*K@d$N(s-OWAO#99w)$@$&<?HAJdeQRTr`bJOMaENU<%-Z9uZRfU~<F=vmD|;QC-?F5'
    'g>Ki@T3Uq!S58tf5(Tn``s{csmcaXPUeWMq0>gW;a{C+w9BlV4*bcro2MzI*pVhoFMEXK2#$fA|SWEN9dOk**f#Y`5nS'
    'j=WIhs9hL^H`k4Vm^xnEY4=Jh{a+SOIfU7v696q78kO(h{eS$E@N>yiz`@M$znB&t5~dIaV?837VBATU~wypjV$hFv57'
    '?wi!Cg+vgl*+5Q~RdJkDY}i|1HWx-ocTQO}}*MH7qREJm;x$)cG>3yV=KMza{hVjPR{EGDvOWigq>R2I`%OlL8Z#Vi)H'
    'S<GQEm&H65XR(;iVgZY@SuA3)n8i{SD_E>#v5LioEG}YkF^kJsT+ZSO7FV)Z&EhH+Ygk;%qKn0P78_XH%3>pnyIE{v(Z'
    'ga3i>)mBSUkkyVHS_G*v{fP7L`yQzbxumG_Yu5F`UH+79&|SvuI&4ip6LaV_1x1F`h;F<+S1*s~7)syr)+STzw7kuw%I'
    '9^oVtNlk4f=^C(2hbGy~xP1XJ3{ImF2Am9l7tWHPkb2}Zm&+c>-KeN*j{WK-;=zg-iTjf1b-s9yxPTphqjC{bEd5gT8<'
    'vmi~Bji0?-c9mukaxYj>*QS<9ehIH$6{+2=k~Q<9O-nhe|MKB|F?GeX+V9K-wv$p^5cS4U4DJAvdhmCmUsF6!s0GJa9G'
    '&o7Z8<Qej-uIVljWeP}J%3irgSx?O4oaF^k1a7SmZwV=<M*WEQO~CbAgMVjPPxEJm{!#iE5pGmDWdMo7`-QzRv$PIMLx'
    'Eb3X*v8b?kj=Qm)#p5g<W^op0%afw9v}z!IlYpNGry9$i8z^=Qh~Cp&^{iw|?lbLE*^-YPZ-z;><feJQsBFoJ`$tcdY)'
    'P`vB3m-_<;;gATQZ{dSCuVU`omNCk}diBH%C;q<b9oEy<|&XnQ=;GOWxf-ut~BdgKN*LY{}eVVS34y{OHqO71GC6f-M;'
    ')(jr?@|H_PQk}V0U3suiQ=eIqwM6xA6o5HM;E&2G(&|Q)(i8<GyvL&Yu&fY5766@8$R>_tmp1SOM$(D@#FibuFT=zh&L'
    '$W37>Vnnt&nb_eHcGbS;E2;ITM~cW?5`wSGSzfeWlO&P=Z>S2ElG?&t+FLMqPwR{wq(-yb1GXRTa0XpY%#JWzpU=LO|m'
    '70uePgf$^OTlpD)>x*2mANY>8|!vL&*`$d=g7yfjj>CDXnMvP!l@whh@5**0WL<`+J8iDXN9<%C!zTO!L%=5moO$=y0_'
    'yktun%BkFReg#Rd3PridmdIX5wnX+ivL#o%Sh-lTC1t-;ZFy|NEVcpJ63?}lu9R#^#}ib-Y__b3Ekm|MJ_}?^<g=*Yo~'
    '-1aM79Km&d-u9sd|lGuP)*~Tg-iiYzh6)kYGzTijlg4OIXPzAY0=4sl1P5OX{vVud*fmjwbDpY{`9JpH|tDTZ&)3Q?ez'
    'izd5b4CDz&v&r7ysVA^q&ExBmeqIr@nIdIiUl`Z-DlYg(1Y)Pu`xXP9cQaTn&wq$U|A(btWXWS3b8Tap0w&dR<+Uq1+k'
    '}KJ2{HdZR*RPgr$=Fv8t8B^0-#WgRY)Ps87nLp9*Z)k8WJ?CTrm-bYkGLpYvL&HEw5n{$)agWDB3p7g;eg7P$Pz4ELKJ'
    'JGS&M8*&0Q~qNVeq3VFy&UWWbG2rc1WO-lnl7_ow#XAlZ`lSAMUuCG}Tak}BDfi(hL}*^+C!d8bRZWM{-al`WAaWK#*d'
    'G`3_&zqyhvd3Wi0l`WCw&Z2S~RJP>r_g-v}Y{`df)~jrZ)E2Q8*^<8dFDsR7NpaCOl`WArRI&}omc&L})mO45WiM@0*%'
    'Dd8Vk+Sal`Xky!Aol-Tk_GGPgS-=wqhl>qMBQ=id#{`C9LHVT&%5UZ3Ek|m2GHb?QU*c6W8Y9+FH2WRxa1a+C!{8%-Z9'
    'uZD;K{)*@T-Kyl=L$(DTa%Udd2;(gupPsx_NQTCR~mPl<lYa_UXNG`$5X)T;Kiql4O+89n7M`=G*&p%6hku8z-TDgQ|E'
    '+LgmNaNblS)0k)EG{9NOZeY8|14`O;@XP2wo<OGg40%V+A41GLT>RQPP>@XUikd8dVZ9()ojBmwxNd0UCZVEb^iIU^Ur'
    '^sfBx(IbJype=P-a$={6Q;vslDpF^i=vR<KyfVik)ESzN^8ViuRNxSYimEUsj+n#ENt*08viMHh?pEH<#XmBmIDceB{U'
    'qKCy67F${Lv3Q8Z!z><WvE2_B`=RAv#?UP4Sv0U{VlkY>2o@t*G_z=7F^a`#7Gqe9V=<n^L>8?qCbO8zVj7F-EM~Hp#b'
    'P##IV|R~n8)HQ7V}vwU~x8!MJyJxSju7ri<K-^vAB@MMJz66aT$xtSzN*5N*1eGT*V@twcHXsLISA8H@~j}D6b{7L;|R'
    'P-u$@=pzhyL@`eOZSv9Lw0QK@eOQuTz)$h7bQ~-7Jmx@6GsQ%ZzsRAfjLMxYm0BX?VwF4x8I`?~n3ZVM^rc96k>du;tD'
    'u9YVQPL^_)ay5IQ2~@piva3QajFsl)b2HJE|dVuv~s@+pyI!r`KAO=N#evP0w`H70;tz--lqa6X&C~jbH6vK0IKQuvSk'
    'uLg<tob3ZP`#I7;hN0hBy=LIA4vunM4#Tz~Nq37}qIbW8<M|DNE!M*^tO!N*kqCDUedS_Dvls&=Zco3A^n0;q_b%U_oO'
    '>a)j>s{rcb(>22-fXb{qs{$zfoh4UD0QLTB1j!LVU6&L59|@p-9Yll>0;pe_^z{-zwVml<l>ln{q)Q){04g@JmsJ9&2?'
    'uk2mjG(%%{{FWKs8vdx?cjQnG3pGC4iz|uoM95(Q~9l09Aa%Fi--hGxt*o^t$==YYQZRnto4js{~LnxrOgb0QFNY*?<5'
    'lswOK#0w~)9WN!mowv{bw<W}tFRv>`txNFmV380)-(zdW=t!$Z(+jfZCh5*Vrc2~0mP@BwTLp%56IqpdWP~M#nrb+<ix'
    'r%-X2mw@5?^_p10QJD<I;#XwZ(a80R}w&7s_SNz0P69JPOg#wYW=sA76DYXd0UnQP;sw|v+f{1-%$O$1W+SBIimt7NrW'
    'JPI&f9H3ZUeOLI5R46auK|-uKUy0BTs+F%>`+{C4043822;{+kM*em`@?a0#HY#5r>WP*1F>O_u<w{GUIl07`0eS)0e&'
    'S*%3>ReQ<K6%s(XZuv<CQ1N>oTQ33BZ-ahR0o2UlLGvVl`eDNNDu9wJ0Rkww5+HzD95XXc0;nPT_NV|#t}qCo<Z6TfDr'
    'lCqR061)$Lm!9HSKKh(-J@(*uFyrP*S^!wKZJAS}p+rlw2q4b=o?K07|a32%zLjivUWl;0U1P3XT9uU4M1F{<5}}wLaD'
    '!BJB$*fRftdtZirQIo2Y8l3D~%QmbdJfwd;qhO;(;wUMkfv)01eDAq=^Hiorvtc_=FB5SRzO=fK>YtvYp&e}}YX0bM#w'
    'K=TKWo;g7XR$V)wFRu5&DtW?7PGdLwH2(bWNj5|7qWH{YZtS28Ecobb_Hu!vbLJFt5{pZ+O@28v9_MI4XoYD+D6vyW^E'
    'H|J*;hEZ7XYitUbiq!>m2d+IH5SV=V$Gb^M0%_+_nuwI<evvo?aYk*qbd*23B-)<&~7hP835jc0A5)E>lNul)-^{iy*|'
    '%?H;dN&xk@{x7O;pbqUBGfV=g%}ZCRZ=h_ee{52Jf#;KI?G2RBMza<H)ZAGk?vns&?%w~X=XI-pRB9!FiX8QsdR}*7Y4'
    'TnPpf)aDtG<C+FtFd<5<p%5?nd>z?(b!n4V3`u=|{Gx=XGV;Y)*>+>b;^JsS-eyj;mMC>&g;laR~^ZR=0<%0P6bzjp}*'
    'b0lvK#O90i_bCdc8>h<xDO_l(v`R^OlH&D`sO11$3)Kgbh9FqVlKln@aysqq>#oRjxpf>Jz{7nL=z2DcWZ=mE8T1ii6y'
    '?S0(_Sq`#GXzk%dsZHl04k>crz(JwPpF=sPy?sk%4r)p?QTws0BYx$ni>h9iuV3X1yEAk%32?naEMDd%-Z9uZRc@zj>j'
    '1SsQ-tzw*il;x)z4d%$!M1k_l&kAW?$^859j@bifjazyvZO2!n$m6Cx(qUdWB9_EI<p#b#jQ$+S7yj?#PUS9`TTTx>sj'
    '+uM4pRs5;WWFS94C4kjL5)2mgj1ysyG?_rmdDq(KOeR?SKKFgTpX8bIv-aL=uf6u#Yp=Ecj@7W>$GQV`=ZyVH2Wnj29M'
    'c`BJ7!2p2TI3)4wQ}|HwDd0LGx2kTMCK})a?UXzppz`!9RqO4wPOO=s@Xpfew^j7wACgb>U1wOH)vEpmZrg2TGR`bfB_'
    'cZM5kQ)V$&&NeAktYm2gU2kLttbR-?9r2Lvv@@tx?d32!knnwppZ&~O-=`HKAR4E!$rSPXHYfe$tlA`R%6lHr;&>y9s&'
    '!*^oK1DA&P(P^n<P+V2y8gtUlQU3{{O;dB)g7po#*@hzD7{^v1GUX~JUIiUL*Gn6(SiEc;{`v{9jM1&=uOT*<qm%T5#5'
    '0ze;7#4K)rbP_7{>4RN+U-8K{4bj}YB~>iySq$r&j9+&OfhnyjBBXP}C+b8Wf<<@?~%<P6la@{KR(4%Bx(y^x%N(m6Rs'
    'Ir+Eb4AeL76+3kY>d*1f<P6kP$L8+Q9jL-WW72^dylj3$cc8wxDJ$tfovpd;Bi(^={BU~Gf%^Dh#of9CweiF2k`9#n(o'
    '+?>10^)iaq14#QESKRx&w9a&-0wR1NEO5Pr7vn>RW~Low@_H`PsEIbO-9mBR4s92TGqMM+d6Ld!ti#pqd8%c3yX&o__U'
    'Or|v*K^XlB^bqDI2H?2<Hfl_`x@Gaeea%EFD5FMzVEy2fi2dbrPDCt1iw&Wkz9jMzrJePE!RtNq&pgU0c>3vBDO4s1%K'
    '<OGB9VoqZqXVV4Zh8Wb);7_B()+h(Q_$yAP;{Vly@L*vu6GWk;{G-j_r(<S<rMT_3i@UW8cIPArJ$WD=&=;EI|Y3=1?@'
    '>e`%=(>6!csQs-&QUDQG+e9ZEqHDd<=VN@t)nC^}F&luJP^DX29C%}GIXQ_#E=G(QEkrJ(au&;==IVG6n^1znPYE=@t5'
    'DQIa5TAqTgO+h^==!O(@V+#5}3i@CQx+MjzPeC6}LARx#LJIm=3fh>0`cu&66tpD;eKG~zn}Yr*1${OJeLe;KMGE>t3V'
    'I*~{cQ^RVhZ|l3VJXFeKQ3OrJ#pW(9RU}SPI&mg1(!A_N1VFDd<27dM*W3QqaK^G@gPErJ#uvbSwp>Gf=wxrlsUJ1+}E'
    '0))X`+1<g%C^HR|K6x5c2&QC!Xq@aZ<=%N&KNea4DhkkXvE`Cqsf3jXz8njlB2W+`sBG?vGkw%-%TUDXl^?$2s{{yyse'
    '7^NvcrLXqAf+}>Z-}h5t?ke44NHe|`>%zUrM=<JJ>l9-eZ`o0Z>Z-8q+{rn>kGmAN4;G=ZD&UNdwWCu*Y?f8SRW1yA1V'
    '>K{+@n~;u)QA?VpNZ^Wzju0oOvVTwnHs7zJ?BU?3vz@oOM}!84&d=!(ex>j)zqvPLjEPy|rAfcaVt`nBgXOM@0tX@PtW'
    'm*6U8N4M8^7ZEvf<e@HHxNLf-ppA6X^RxR6y=VJ3*tnivjp{TY5AtaW<A<g05eI6&at1V>x2@qtwsOc(D+w2LNC}p_-?'
    ')i%JQTtlyun9J!RB1Q1qk)~FR7~3&i00+HnykB%v=jy^~E!7kdr?&psK*cF71u^bn=_?^VD18YqgFOJ{xI!D1`NE>Ikx'
    'tf+I*jV@66>CnJjIH}_uNl%g2eIEh{hX{#g;+bjhUpw~+-H44b^;2PsYT@-C6iD>j!1;1wsuKhC;Y@R=<3>|ErtQUrsq'
    'A5(va`{YHD@mV>S9oi}lJvFU6~nM3eKKCpgd1Pl?D)cW8}KoYwCknmG(~7;rH7e<_w*9=W$1NqCM*ut)+CE04dzv#EYC'
    ';IFR`Bvu2LNl83devY92ZarM5yqy{M9$FX)sXw&4QxTzY<gqYV!hUn+wD#WH~h*5Gp}MpYGFrSnT@0UK<EeY22H(jf~K'
    'g~#&&*-{iZ`$^d<dssQgAa5|RxP)AVzgV5)L*IN&#L4kK!Db_e3y$`NwEF0435^2QXYne2*58BqgfJf$b9FX?v3CG7>J'
    '9a|@TBtm{z|5&t3S7It<Bau(4S`re%s)<07bF!K{FJj5;5I0uBuS8Yw_^(zB{2Ap!l8QpZozsS&$2O3V}&B)8QZ+#^cw'
    'ec4Jc*YZ8R8Jz0xwtX6@VM2_?w@PB|L;}xi(b=h|SuTYEu4Mywr0ABl_)udIc*S-UI?SB@pjn6^Wg^u@q_awzNXqgW+Y'
    '<VZRsGW2av`s!AzBkm=POJMV<djzB<$b@V%=-ytUO!@U_4ha~@K35(ZeRr}CMahPo_sHbxpLAE&|IkBoTkH=VN)9lG0H'
    '{{RQOUE10gE^qzMqzkoO-tiQ)atcOHZswECcx5qz0K9_LVOu#(9X$YzneHjyA+R^E^pL+);_-6S2#(*#o2F`z-PedW;B'
    'g`$}oTxlT33p%Ayw$V+FCfk=cxXl#Y&}Kz(?=Ru7F78Q4A+~=-Tngv+tsaot@*N|fP{aKt@xFTo@Sf<qM}d29<2#T|Zm'
    'pxkcl|@BDY~BE+J2^=ZFBZz02dq`(7G;euGw^zzADuyvm#}={iU%)RiB_Fg1+$p^Z>C$qrD*F4KBI@yvq-6v7z+jR8>}'
    'Zl(|s)K5V9iAYOfo?zmSv&q0z}&evBvDG$B;YyYnNP|*=8m5ui&m5MjG1C&ay^=hR8ELa@}S)@`3OmK`$sFT$__()}L^'
    '}vK8Sq<E1Y=bO;7w`LKPw2!5>W_Z*#EL$Swn4}H5bwfQ_#q8aLi3T(d{iX4Rl$|2T@8fzr#?ZE{63NvbX)1i41gIRKMS'
    'fVD!s}ymA1J;;81?wqRV=tdmoC2o#$Fx4ygREYh6+(-*Kt0zPo>8_e86z@9E#zGtr*v2l_V-OthqWrGKN+_(vcu&vD8('
    'Z?ej7C(z&~ig&8-;lCvMANWh6@iWMNL4OYrVg(9wKo(Y&<5v{)X0TFqB(NCJ1akN%Z^v?ga;d0X?^XIiB|%9xgMyU~ao'
    'YZ#_3JB04fJe23;%ncHK3B?vM(XKIWgTHiv9*j@e(noZlug&F$f$f%avVYF-wPQ3_pJrGZ8nZhuuob*g!~_>*yq-+l?e'
    'qrQTJcJTQY{Zt-!q>}CziKn0pUB)&v!jl(^*{Wi%r#*na(VLv${jzOQ==BZG3T9p;k8B~^l=`Jau3MIF=I8#q7*Q>0Vh'
    'M%HPtA!g|OhCp;)y)AZl8<Bfz)mcr%UZj6Q&&V<XR(4bx;R8^iN)YOKXxAL9;mk*kgA05g23hkR3TGR4T4!xjpAl$-~z'
    'nJWfn2ptL%nqG=MBr+f}dfPr0CD%Ph^I>Rp^PFa}X#IR^}a^#CVWh7Yk~dPi9<4y{(Hf-MIN@T-{&WAB0@0I~ct;ngG@'
    '%kD5A63dutaK}iey16#8@4+X_z@GCq<kX7k2Qhgp&C%{lC++(&q=EG)&*946P<8eG4UJ`tctZp0l^0`}^2AjE=7#Gw)<'
    '*0nU)y%m3MOXWhd|k4gocF@IU1k8f@%B=g8dLo+QJDlVFS+Zl`-TC>66)njU$ht+C-C-p+ce+O``)4v+-Sfs5YWJ3}yE'
    'hb34lzMleUeeLcfOj$Qo+@&l5?*+Wv7`o-_0uwkTEx?pH^S-ym~4#SdkfwhNXM$$31xhtYX^N@5gOJfJ_$clJ?ZPw&Zd'
    'GLCMT6qmlc(ve9=}=K*Es^>JbiC?}oKOj%5U#0L;MJqfuxjsV5SLZE>2-z$V8`|)py&`{RJ*-ZuUYa}FmK!7Vh=fvlZ!'
    'iM0vXH6lt34RpMXZ}7}@?gfELvEUkldu;#myy76Y+K``aAc939m=c#@`J(_&w=hoByz_+I;PS<zxE@Tf|(sv@V($O}=*'
    'a^WWVEKIA*DlfJ`_<tZH#rsJ1TBsw)`Idd<@E|FyBOJSi=(JA2%ouAwzU}zy!TCVMTZKqTJ4pa3={hMLtYdup#e<75c<'
    'Db1mV8!bbFDZXi0d#Tp=f0*zlE-`x+c=VG%%FUcj$ZufPn(?lK=z8QKqI|0iYQ5SL8qyU{)efw<I2PSv{&~K>&LL!@!*'
    '+yvpsfuv(#y0L2rD9Q^m2N|^ngTUIa~<_&OtaBc}aZG@-8^yvY3;^@<Z@bnva+P?)JB1#1ER35#BVfN=jDU|Es>GVetl'
    'tF1QUpg1>4fR5g`5~C-A3jV~V=k&l;KkB2(ZI=FH}vM&h{e`)5kH*P6b58_q;7_4(34DTfgY;Cdo=h5Obm^VoRKz!iU)'
    'ha^2A?Pgj%JbF+pkr!&T&rXehI*()hrmMN;v6XkRArda0e4K8ZVK<-qHs*%iu{ci{I40&2Bb;BDa4#WsMeGx9-95Gvj%'
    '6_?YNcCA;LcMEOf;%{S9DjJw7<&X2JQbY!BicnP+7#0@lIeEnyN}HCzD`a|<v+!J9T?;jt%)<tG)JCGnKaE+S(zz<Vu7'
    'a)sPlxLq7O=xLxB@@yBPy~upsdW(H1RM=2-m6ZbB}SvHQ>)@V%eAkWMKXx%m|6<ifAowK5dQSl&kbz#2$*!Y;%c=la?@'
    'In)DdU2y24w#Cm8ag<1<<8gwb1peq581sdd5{#DmdgX)3X7kG)|<vd<K!;9ik{<H`w%mba@sdix=O@p{4aGxK<#qMIOp'
    'nnJk-ULDS(1y8$vRVQobGbTW5xg`Qs~zF(yp%BQoF^qXAxBC~6Ry9C_}KTfSKKBVyC7%dbL>+>u`a|dk0U+Thvw2(V5&'
    't<3kE`%p^^y<mCM**pp(8{8OK*H80e<2gY?xx0)6zs8Vo21m)di@N`jUuhji2u_n6(yZdUla80Krr_m$!;)tLYPL)mT{'
    'zp8AruP&SQe^a(Rt!&V{Xk}9tEykiv^e5<VR|UHG=6^-Qqk{B;tmllh;}II@dW%`%sF4o@kl>qJ)1-DoOFDEjQq&+6G>'
    '3%SwV2|^q!;3e-<1Ap5~io3L5y!mF@yLys)bFpn<FC~7+ts~iG**)ZLk_t{7ge4Xn~MycvCH!*PA2qOwg>D=g(hP$H#Y'
    '*G*h<}SM6=&8t5B-%@_<#LSOSqVL|h-aKlI&XhBA>;2qTAA$evjx9KBQg;q8*HU*v-`ox0MiCCc{1-uq}NKV)C5Qm`<e'
    'Be=)emaX%P1}FWP{zNK=247`G)-}le`QLUG`f(S0E=d{KxFY9u#FZrW=f+)d(2X_FlK^YS{hXaOOW*k*%FKr>z6JstQC'
    'zTJ(1{s$b>3?PI-$qZ4^1M4qX;0WC3B@LS~^GM@(Dx)4Pi!H5T$uW8{%BbzoCh8^>&{{sC)K2Q;hFTeXEOW~e?H>u@EW'
    'HkDvsgPlVH`v?A2L}4LLRvP${xGi!CyM+`}#5H5GV~b2P1BHi(ss3OA<rvUm4|zNw@?)x)qhg;+yPLl{P_ptN7?Kdz7!'
    'Ks1`^GguXS~g-ZsTZ2m#=O^yCPqCNsEs~tQf<hARTp}c6cDF!%4RQ+e6NnyTJeebz2U-TIm&0iP|I0`=2#j$f(C`jF^S'
    'ZQM4$3L-!Rqlt;duul`dmpxabKd5U%c6b39O@~DMuwvu+BTNb&-CDOPmnw5_MA#q#R1=&}EtW;Fa`pk}UZnqgjEkLa5w'
    'rB|uk?$xL*PT-)-$C5Dvca*+@+j+w?=&kv12mffM=c-$%NV%9qU$DeBs&kf8o8X^kN3zvyvDK!=U8iFg$!T?=#h&~RNT'
    '<}u{7AxEoP7vdIHi3PAnt<DDbN@Si+)Sk4G0G-78qAN=@S<)1<9~P!_P&hlnf3+2M+x2SJeC2^6S(R6JaRLD!%rQ>vJ|'
    '9$2g7Afir^1e0)=hHz1Zw6%xnaP>`Nai|ZRkpaj;cAX=xJlUlX7YM+hv^CC1TZiD6fZrHIk1tJwJhu+Xk0g?L_5@u$8u'
    'AVoU8w<Vm0jK7MaZsqgRXA;w4Pe1&e#a$t*(}~9%Im95oS&_0#ef>n{+9SWsoE`;ySj85)1WPiI!8nAM!>ix#4AosX^f'
    'c$%JZPM@s0q5p;D4a1n<)H-Hmrq-S$RP&}Z)dm6^;TL8fpl{Td<@;pWx^vNe1IkL?H5+U5?5NtMKesIxFVtGl%3NE)uj'
    'W4l`IIZ4*-?b}!iBs@A1$DzqTr9HXX?lnSbnR$O*h5<g{d{!OE*3Kaxn6G}_gRY8bE28j7O7bOh8238Sf>7fwYi3qSF-'
    'ZAE!CUaSOyFc;F3`=E3={A&=gR9Uc@l<sNBH9fh3?y8glEoBVb(r6exKc&ln&b%75Wd-ByJR$;-UzjB@Os*IDK4Zs>@>'
    'o7~@rcP}T;7{G^}mZneCJ&<7WyYMQF8U;(!JB&XG2oG?kw*5WuWLK*+N1?81iTi$WHYs0f>TD|VxIn8<<6kYwEzZIN3}'
    '+W-;mKiU*>^5>v-BDsWiVyu7xqxmYq$qLHbvScOxgJnzT?3U{4*gCvnu87*`OJrdCW-_`qsM{rXf%H?GnAnisus8*3}A'
    'r7+Lm>DL=5#!F#UZdgUZq-Rg`|Aa1`CY<uOAWnj+cqd6Nc)?;y+ZLatnIv_)Njsk80v>@<~4k6v6v><3-9E!Vt2reFVu'
    '>v=B@j19ETeGQkPU5oISL8b<rg@d=Lts1?!X;J!&5Cd&Lw5Ig)+@g%qMDvSEr>mm?;L}XM<6F<8bp@dV=8G6Zat>VS&r'
    'UcF?#(wri}EM4uJ*;Fh3%UC$c^WGS?KvpoMq|7_?}E1)bzjz(CFt)UF=v<)vZ<EK-JV#-U4TkmG;%0O%ypOY{7|*J8Yg'
    'eD6|Q9bs*C(!icNDX}NG#m0&ENACv$_Uh?{JO5A8nxp3Bm|IS7SAU)jhyU?2l}a{k1UZr526#ER4i%2)IZWTQ)V2rfRR'
    'CDiS%!)4fhb(lC_eGcKgAlB*I^-^g2!KL&|&-{;aVH-KkMfM75H(si^^<><R3MN#@+#`I0tQeKJaUR1+$kxlXx99NAw)'
    'LPXs=Bb)d4_%`o>PeVmdDLBplCYdFzV60@HP)0P=K44^5T7>D<CeE*}pD|U^&Yo4m|OcrPlQA>Q<&1hfd?4F0zqW!@$#'
    'rP<uWkCe-V4zEULmCvwvGvlR6`AyajeD;DyPUHAUh0uo_kWY?H%NzsK!|Vt*sW`==nBm&u_QC(_`nFrB^{f}nTIX~QXv'
    '#8b;zEFZG7nS^pR5AOg}ZdPA?4Ix`Ju`GmeSl8YL$B1tji0-~4C3xe4;<9f&>-xBi}9CHh@_4=dnP0=T)04?Iuv>F+tc'
    'u&`i-PR>i1Jk&eZ)BE4da~}@;Oq|)5T?pJ94<qzUFNGP$!;$`;NNPZcZ|;J`2b_E$jCZ4uC3WV50UP}THIof>zqZhkp$'
    'xC`OW;Oz%uLESIZfS}tM24*#OG;7AF=|QSojwlQE4Dv%W&Hh;x|B@fx%TJ<dD&@Y9>qngkvw#+1bIR$7ANVoj_aby{!N'
    '26aLXBHj5ceN0@w28D2}%ZaStU=8$!k;V_Og9o9#hpk0wE<&Y8oilIcFQX4@VT^g`TA!|mNMXF=bu&>oROKS!Cd5*;kA'
    'kQjZ`aO09?cszun5PW^nWRr<hd(iy+FL#Ba2r^^K|CrHJ5HaKAwCN^F4PRS8O4pmZRNtOR;PJsrf`kwwzi}bQ}A9xhl{'
    'Q`Y%eV|XErcF@XydFGObq^LASY9b4ChvXT%K7h?pg3cY=0`E~FooeS{7$-bO#$`a_WJ>ga3(?N=+~hRUJf+CsJ!EOuVL'
    '1bGBb3K{<8nIdapV@BJR7BtC-d&t!32+OX7>>lE!cG~wQTA8KS@y)-+&%9>~SuN?SR97O3XV<JQawm3xJ(6(xmt=wq>P'
    'i@6)sQ&$9f+Zk-(U#3R1YytE<mPM0C*~sd~rr_64P*LAzLm?Yjvr}kM{IzlGRdrhdDOi;j@TYexI5tn%mPeW4R561{FJ'
    'gTp|VXfgu75@mB%qKn?%ITAWt`mIVbUY}x@Ql+W0bR^YS9J}{PsVmZ=0WEi8lX&K4k!%ZoN3CG{iHv?l4jma=MVQP1b@'
    '8leS9oN?CzDy2yGSWvTX_VW3%cWncwrBFJ(A9Rlj)78?-ENTvOr}oN7AWMwR<Q*aK=59;zX-2;op5;=AK*9lL~B}0dTW'
    '`XWtE||%m`>(RvD#slhi?xADciPT%C$bV;Q7&qts!<SihQxwO5M+4jK{&ZWe^LOvW)CZVdbqkarw@3j7A(*8{(9_>t15'
    '&dH@upM$#scLnZ)a36$w9PV+r55av1?g_Xj;64WTu^<~no5vJ4C6e4wvCxa)=Y-z|gt)lc&0UebNa5sXt@JtsXuOIez>'
    'Z|xpm4`nu(4=NuLE1E4zj<z^vf~G_uy2_ccd3MT|bqUH(4j6TAdVNoxEwKb%K%i9)ssN{Bq%?wahg4M9V6Zbe?kW0_EN'
    'xCZR^!^f;-yIu9{vEwoIExpz`Lh?J_c^Csy6lFUEh1zEis_rq`#;MWMhX81h`zaPQxdHDT)Dy^G5;B_9{bK#x~_Z+zAz'
    '}*UWE8Hz`x4@l)J9NrVv2bUvt{aU<op9}j-vIoErcSDxG80fesV?aI)%7qLc2(V>?cgw@b_8R4$5zrA9yOCVD8=B?AXS'
    'h&Xvg8wJ6MBI?omEiqt8FA1OqphI+J-Gb%qCx)*k5!9fCns5i{CD;6`S6ls8smZ*c=j4}!+<=YL5e|FA{t<B8B8kxu_v'
    'll`=R)F|Fv_iN4|EP~#qiYzNg1k~L(S9!3*A#12Yade10otoIigA0u_6X`hFAstmWLmKQ0<g{86nHVv|9t@95tFDcB6$'
    '14c2vrMa+FuNpWjh5PasPEKM8Hm^HiB~iz#03n{j^;zqO%XX-E@Y5urERpmymMkog|@9YP4AmBJWZD4M&BMN3&MOu0tp'
    'zP9F{%g()88F+E5$W+loT6j|d-G$DxyMr~xX!Js?|(W<rX%aZhvt@hJJn^{>L@|on#Y=fb~tCWmW1J@A&FS4<LR9Wgq+'
    'Bk#A6|7>T)3F;#C--usr=WMZ%_P?(gd*}@__CSgJVWdCjP%#p!=)dveqlN};ppCx?Y}!yG(kVG!ZCCECG_*e8=$9%qFv'
    'B@^0D(*jhaPYg8T3)3-p)hN|Guza8l&vR3@T0W>pY2waG4y_OmES$*z`4QX1sg&|t*TgCS-2ms$~AW8y5biUX(0)EUdb'
    '&Iu^toR0AJOT(+O^J9xJ`WU8I3n}Pi5hN>eagSz`d#QC`2J1jDkyW{vG0g^E0SAcrEv_*r2yu^@!nEIz221O4{9nnKtB'
    'o6RSR(BA_amJhv^C(-dBGNp7Q4>>AXpTf+U_)Gl5A?cgt(?Kh5>;eK@A1L{DWrjAi7&noy)*1?$A6`C)$c{;B#SRu%12'
    '0aF8e-%)718Na97*D)HU-b}8I6l6KxmNfMSw8WnQ7PQ*2niQp#_@QKvHYM2_|h@}zwWLE>jyUW110XyujRyQ*oHVC%?o'
    '5U<2^8syW`3BN4X)5eE8f0uI$&xvU-%`%5a!C=^G2Zxkjs5gKj1pYbBP~-!ci?n;8QUb98-_Q=GD)PI8egHm0pg9xv1M'
    'Uq)kHR67o*H1jPf!u&A*Oy#&A4V%!EfS#=_OVQo3Sld|su5t1~u0vdy*j&?f0nX~bLAV5*&Hccqis-R;9pizH-wl&4nf'
    'D#@@)8qaB*N#m%CUQ~KNRaN>T*<NI-lg;f-Y-JIBaE>e%9tE=y%)Hig`{||MgjOQgj0x8*-7fG;ALRqr0f%2R;QmjGy-'
    'Rw>5aV9UK)dZ7qy6NH==%)LNb}7fplAG6NJ?zj^IhRvl2Rb7l9X$q3Y96s>~cQvLkKUb8RG*jbfAT9N0GYnqd(T*qA&r'
    'oMkQ=lVW;?@)a8ur0<|G}rL*JGCu35VvFROj8Djr*kouRM;?k_nm{(H1C6tv51(K2_Op}x}@uuO!;FvdcF;0m4jiQ=_n'
    'A72+yJ4C>QQrnzYc9Y9)_yW}T@wBdjhlrD6-WrVtZGfaJHiKU07X=f8-~EBU5{4Wqvhx!f-mGa>cdIR>xe5yHG^Hq$lE'
    'OPMpn+1H*)gEJh|LTn;L4bV&pq$&tZGWab<Tk$)=T<)8sfMOdDPSm6+~ybTpoiEhmebVm6X!$Pa&NR35=01}(14@><0&'
    'O?O-oa><C}5)^fvRh4U5xz-}ra`FvwZQf?mjuS)bVw^No)HO6c&K;8YJ_L~y1>I5`>-b{Fy4Pqkytu<i-Z8zi-lJ^0N1'
    'IL-XG-nXB!*2%ps_ZhAu6D=mizG%9yJCnEwY9md_LJAnemyerL#ba2%N))G160A9;wHAQ=Yql;-kq$wjp<8M9OFOP0Vo'
    'GLy-nceJ+j;?YRx*<lM{F6-<+l-gexGV|c`Mj!uzgsogQ~8=N>`frAs0>l_>Viy}cX#4#UnI5Zq@YB=8fpW;}KIA~Xi{'
    'oofG3xw@W<9lyf0aQbkXElw#h|kbOom$mvYac{+MOAQVU;%jX(sJevlu<FiLiyz^ib75orw7Y&8jLc!jT_~%TyWoF*<`'
    'uAo0FHxwYgB2bxrFm#;Kd3={vQ47gQ<Bc!fD=6+q`~73O=Do)J~`*JYoV>T((BCPu2`81NoI{|-ALLb^wJ6(T_AK3rmP'
    '3Te_VjuC9?YWA@#GPT918u(X2F$*+SsPymT;-C+x4$+j67E#maG%kt9rh^~RN{5$~SFyidrUD+Hh1v6IZA}FFJSXUlIO'
    '!GUNP9R&ydLv5q{XglHK}XCcsZ^-0f|;(s&t?ql6?YFNvExFS5|V`s)7#T%dSD$rO2*#!If*xwRbh2=9?F%GYqtv1THJ'
    '-TqXGwM)q0wC-VU8zmngW<G=FQ9-e<I!oTH5VUY%SG`9G+n!`)rwIMy6V5`w1?BsuNIt^N`Euh?30@5Y9hg8{hNOm2QU'
    '461^0DVSRPxRF^R9+ED$rl`wViFcg3I|%N9F>WQ4?GLOk~_ggpVl7O==Ze;L(~Tkes{tj6*P!3`fY?<xmBDaxvVU4S&4'
    'Hj0A=jsgX{IwBQ<?d7d=L|kALeZ&cKwGbVH0NE(%7kJ9@K@<A$qo4DrEiiepHPvJ}D4WCTOe3ua8WTDZ-BrC!MQUwK%V'
    '=Dz|e{C$MGN4e-bW=euJdRTkNkK#J+*7T)!i8=l~hDG9(RjVMPAGjU;4+vRmr|>%|eCy~QgtsDm3*Y<%-XG$dAIJN@@X'
    'g!tF7nL{cz=v<{ubUh^3C^SR$Dt6lHI*v1%spZ{40^+(h>t2U2ZNYDy<RaKCoz7XRiYM6QJb)iq0}?HIw*f{^>F_coJL'
    'rpr7`o8~LYu=#i(tN4T<*Zzk{xm3BY;pi8jufqv+<>PB3NLQZ6{{NQd&qpE0rjZW<YZ!yryX&jbaalRRMAcBj}?OO{X3'
    'J~Iocev>G%qVX}WkPp&`Xl+kJg4LvVuFACEzl{8aXuKQJ=+it<wpw)SLb|HX2Q4lx1D32U;vYPl(nlg9SaS?w^5&ih}|'
    'nG_X`^(B||s_y(jKn!yG$YgPC+*Eb$ihP=Pai^Bg0cAtDB{o|VF!?6yYl0#K`StcBu-B#2PU3`!KmjeV2lN?1tB*+7?Y'
    ')36KNySEwH9T%qlq@e9YB3bf(!xT_&(N=7{<r-%^bV-;v#ee0WMW;Rj6#E9XN{d`WyG<aEZ{s{jk#A^sK1iy$h7R!jd#'
    '<JmUGEC%$lrL(M8`O+gj2;sVA6qoE9>=f5Nh5NbiM5L$nKZH!NM5_UqU*R)AT7FH=KeMAaY(Xvv6hsDX|1p3fMpa%NB|'
    'paNke{_2X5lT{yPXj<$;ea*Jc@l49nY|H#lIb8#l3T&Tqs-+T&qRSvKxX&g`i|7O6t?Y<cc=~`VUEn$Q-r!&wcUMg_KP'
    'b8waznRKS`GnlW(pUQ-ni*sL4Pr&tQXE5gzO?M_9(E=CP&Za8!Ud2|tE-z`-|jkB*y8%Q!!<yML8k*;kI2!8MgTyGjL}'
    'ha8lV;-G+(PpA)}7Cl{%S=nc*&Tx+FzA3|f5;M=&Co8xg0Fjsp0Tn3aFkITjlM2&5M;*iT~EjKIk~QybYpXh+sak93k5'
    '`2blM5EG<_To#RX6}j%qNLKk&X=`HSs%j^ZtPYWtwhk~s@i$QOs(UC9+LMN0!A4wzKe^9SRiPX$#Z{k$(rA%f6GGIxO('
    'u7D%anDoV?oz%3fjv+9F5pFh;cA?-tiX|iZ~gWt-9mH9d9=jI&p%}cZ}f!A8SnliN9&M!yu#q7Z=8Ih9lZzK`di9VidC'
    'AF&`L9r})Ia0&$=~92|~V#Hk=ML6_of$XCV_)D=_30q{2g7IdFeri?-zM7U}J5V9g)<!va05apB>Rr2*PLW$%XWOh4nI'
    ';AEqyF#ivWDnI56A15bJ<7u?8D?emN|5^9Hv?J^#rs?36-3|*G1X#sj90}$C>7%UIe@F7R#{;ZU3JOK-erVrReV=5^Z{'
    'V&yI|dc**+V(%mC7}Szk;+T*@ZpuhVfs#r9bx%+U2E%+$ORrUrh`!fzk^dg1pQ2%}~W|5lnuahFo{S}ojKCKV5Wsx|<4'
    'w*{9VzPu8x1h7=73g=&8OQZp;jQ3j8TC>jqsAWo7>y(zX)^O{TvSw%$wRA|OqIDJq#>{sg1^c#@gOI<qa?a*1;nrA|e{'
    '`#0@sGNaAs0w@xqnHaaCu3tXee*ZbgpRbYT?A;O%zo>lUl89KoiWVFD6>Q9v6OsX)aukYlam6D!Q1Ux|+BXGAR5pxt#5'
    'a1YMj*IgkqCWUea`P{D=~a=ePOgkhW>uW7`q{ACrL<)F@yzjz7G#(?=Q+)Qe$`xe2Qy(@5<|9RMcQd+W*#Q7h!kx^5ng'
    '|sQZ#}VASaH@D4s}?F()}TmQ353PH!IcIN*dEIk@;?gc2u$6kBkPqH?x7P;5CU|GY&)~i892RrYVa{bE8oIttR)>SoV{'
    'yFHqui6Td;2UAB9bwrlaeXUeFzqF9%m%bqc0-gNu||N|1%OkutV?mwBY8-LSAkN^EJ&{7J)>+L3X_K-%Cv6W=rSccuh~'
    '(7{isi6qO5`$a0D(DU~##p(W1TOL`AWfxb1|A&R3`IB%vQ|N!tz{GF|QI1w@7K<H+>`kRMn|Gqnrie#5xO$?jF)^*(v@'
    'rN>14Oi|t3WwSA%~SmK()2Im_k>~Xi~sx%wu-11h5ytzo0VaV#rR`6&Us{7&)WUDy9_)w!B7GhAV+25FfWlR5cV<Dy;^'
    '6YP$wUzUVwWE_PFQvg%#buYqn4s%W*Gy_8lg)Tjr_R<R$;YI0l1Xq93DqG`S3qL4#5<wa(UyB{z7^I<75){^an=f?Sth'
    '|dOco!OecsyQT%=C2yBP(@3-8L2dQm3`VMRcxjz=IB5S_bSh5>#+UvXmMyY;E<Qkx>dLnt}bB#T<dVjW{$qR#jByJiKt'
    '?33OWnLZ|YT`yEW7`k$Q$PF>o<3O3|9+Y947Yx8EEBaq6#0^qUXThj_m$-X|vd+=;$N;{7#Bzf0+xmD<>4hE~@F&8{m_'
    'f9}YnCAc%{JC96Wf@^MI)b2?1$3RkQeK77v!nF>8lfu3BP-`h}p&FGgv)k_&>6Ol!1WUn*<l-xFm~`SCei(07xE+x`GK'
    'Hf?A??y{AbF-1?=y@?S7D)ki0mufi3_O(UR(c*4qH-7NX58}z$j)*#kh{ZyqyL#m^MOS5u|N^CKakjUk%DoPs^|$mrDw'
    '_Dq%WSY1)s=E5#e?aX+_$tCexUj887A+!8|9B7#Wk@#_Pr<(ByxGApJw(4`*IJPxO=rFj;-?a#$ws*s$WLx<e*;EMKuG'
    'qMkZgBJfjE4{bGrs49N0*|HXG6PYzXGozOM|k^Z$pw{GP-&#ZiW5LpWV~RPMQR@lU*O5{hq~~HkKmIATrLQf9htIvIBb'
    'yJW8f6v>GQ$7JIHjoa)@}x<jMr892#yj$Tb#G2w!IJkuERaX=+%0KUu>%)^Oqq>2f}f;`w)6OOBk11=kwckyCRXI9NKK'
    'A<hK?=3E=g3*Iv@5%T;&{D=hvreR<;iz}ciII@YwQAqcYd_Rlo5w7=fas?Q!W8}nDxz;44zXCBsd;sTF3%~%WNrbPk4a'
    ';TGvZmH?W~Y&yF}V|3D#v?(03h!<J`kW6w2<$zj_-1!L%Ndx7NvP6IeO~+$a`}Hpm(i_73UyPcf_s>t{I^4mo;iJh|>F'
    'AmSW+^YF1vSJ+ovrC$Hmx$>5jPaGLe$_<(OlYZSY0%L!5qu{j)*f!OQd%E8sKjuRhj1*WvP`SCDtu?3!Y%=7zru%F2h%'
    'F|OZ$TZ_x`b@c7dayJjH+Bn+WCgZPidD_R`|I@mvtTo&JHtv`V{)2YS~v!#r;5|>0NsbehJ&y8Jra_gk7JyR9|obU$HA'
    '`fS>9A`c>^7ZiuLMyS~yLj<IeDK^p!ZRTA=ho3;ixh5;|VKQhO}7`gg5l#0=>{cAP?_$H0((r~;b8HjDZm1*zMtS1&}S'
    'LQZB-5oi730VC<85eqtlYo`vb50>Yw_b8=Kx*!XUvTTg+Fk_URtn>(%Lt6)JLl9#>!sTKla}%yZ*05(|o2s&_j<C2WSS'
    'ouU7mvCa(^Vx&5r7m&2ig>yBa+qzX^Yhn;G?(x5SR$brP}ClFKW_Sx1&QYv(h1<l?`k?4w%<t1FceLY|}$6;xu)qwQ6#'
    '~W8Km^PL-F*yIIvtb1Ab*oJ!hjH)|#hZOt6?SKqZ(*-ytzCkDWJ^o`<vD%zZz3$ZyvLvSVTmz|spol}UrM;vNk$rY-|!'
    'k_Y{U6y*RTAdVhPH==D7p(+;%xuH$n{wsYqbtdgg5%++CAi93B9$jpsb!F77&W+e3~sU&?iav^bdJUG)x&o96?niGGv>'
    '2eJW48YV!@1qsKI+JU~S1IT<{)v;N%ik+VZnf2D*6p&7Yy*w3e-G?Hms`hoHV%)~ysx8IQ44)}U>EHga-~ha(X>WU5X3'
    'Xi5#cZ7U8>llKeOgJPQ*{*1>By;mu=A#(4S^htcR<04M9lG)$}%Qa*CQ<V@3egGeE;TKwa9hdm09XKoG9t)4MtEJK9vO'
    'BRetzmgJ+{tFvv6&NBN~5}(yAGRMBDmH-_1s?6b2n*vZnhL2m>l^0-mj>-dpW=+;{!|faPls&M6Cty7~Eqxr^G+?OKgw'
    '6G0-YumAzL|mj~D0F@{=iGpQL0=3SA&blgPK*I4ChPF};JVp+q%)ltofRjq5&L77YkWuk&INoRND`F+z-nH-<2OoF9*O'
    'UdzAJ`J-5@BNAnX{JO2IN98(x#}JZmfkrgbMjil7^t~QOR&r!uNad{?@UDTgF5z56hEfJ2X99{)>v)<%<ByDnz7&m_A#'
    'ib@Ho3p8ea~b2*9vNwZpD4r~s-X#W6jY1^{y$_hP>?KGCnd1Da1rOY(b4s$W@0`<1k>Wj6FH!0$(?&V7^vtf+QHt4SP_'
    'C+N{KbPokB7s)PUrTjh0-!*XM^bQW#nx^aMabRm2Wh>Ru@4QV}bOAY6MA`ZdvX!dlbpw+EcTKQ$-6XcUtg=Zi%^d?1At'
    'l~95Py;il)^Kmv+)Y(qd_vgpkt~)JoAG~pOAfHzAWh-_0)OEols-hAbQY{?Zc-OX#V@C_Un<wA;*WY94H5I%ylQ`j%OV'
    'gv!qClqh?I5;kJJYchN>Zh)umJMkdDS!3c_@M;ic<XHkSJt;8x_RzX1sIpj%NAkw(EG@0-&LP2vY6F8b4EH5Q@%oT^S-'
    'Xr3WIFtOaSqoELV^WwUMUdouA&Pq9?_2HKy`^?_?mOZmdM=PdL;mJit@J&Pp(Oz7fcWhfumm?j2{LF%it@OlN1USYmsO'
    'SY1Wp4)$gNObh1Y7b#wr`2B+?R<57@wv1Ic|HvORHjRiF!$DdfIklqH!3U=>|VO07Gw8wJHN!Z*_ev-hx$dpPj{Xl7JG'
    'eK(Qg*nY->jY~ij<(k2{544oZ@EhkvJ8^^m&&|n-Er3R)MSGHVm|E;P*N*d$Ld=3l5OvNPRyQLOkPk;5XXRZiWJn%IeZ'
    'e;esZo&>*n2n`BCN*<@_J6Et5y@(tO<ej&Gq}TfN@kJp)p;^a7>cjv5dx+QZy+bN_I+aGrorwS@6vkU2dtpbn1M@H$cA'
    'yx`Zq_Q(ik2bpfbIF>X46%^g5@LTewwopZrm26>ryonsdEz~VH(<)GGrn66v9=!J8^6>L)WE9gR{GRuJ{NUfD`zN9a@i'
    '{afM%*6Ey2LOH=8L*C%2FB$nxQ#?#)wb2b;3myZV{$p(joQSF!0D4RSry8%J2V?T-K$)=8C)Kmo#6~s!Z%47N0tT44e}'
    'JSF1H}DI-r7aw>!JaQ=u%s6<32>VujKbRk(&kCd~^U)#im`=7YFUZ`jG^3)wh(U!k=1YwK&vxK=mU!o_l+v#Y|<h6?4v'
    'E!6)6ltn(vW+9DkKLuUD(Yas{WV*Hx7hUL1-=Ga0)TZ+fr$fT=GXBX6I4?QgiUWU7{}rwkRiwRv{mG`0D`<``4KlZ_U`'
    '#%1;3ROfy<rOm!1J`=_YuQS-oWDzC?ri9HSRX7Qa)dS`<!uTIvE&Tr#5Vn#klOl(Y^3bsz*i}*y@6wV43=pkx^2aD_0J'
    'XPCT#S5$r#Yn~s7zCR&0PCvoKj^E!z;@7t%Bnm)kGto(&5n01mb-w=F0%imPlD2s!#>zwTF6J`Na6=zAsHi*IqIYC#SM'
    '|sDo&8wxEX3YoiX7JKuCS(U))P{S>x!8B~0|N-|TxP3A!gQO?^j3ou#!WoM8bG`N;Rd<|N8f-0dPm!SqmYs@h&Z?ToN('
    'Q*=?0uBxFk>&jIlolcY!ty?i!DA!Cha*Ou=1axammGV^-@_%;Tg`*D@2_O+BN}VrIHKN89dWNN)F;I+5R5NU^ASSh!i='
    '?lVo>?gQC%$TKx}wN86zUu9$_UN%Qs^m_wWAIGUn|5S!<S-@SG%Fj#a_}k>E$_A6Zz15M8vuWU7M#I{!KC~X2Mp4Fg*4'
    '814DtjpC8?XBf35OL<D(-v?nq-SB6ZFPrv|PI)4F+}bA%F&5W9s5_yUG|(gE8pJ^gz!4vVv}`1IPNkI>|TAh{vHf=Y%d'
    'g6rc|i?zS{=>y^E?BaL8p>qCU)(f~JkKbLlhi#4kgnJi|Y(>`*x&B|1aoF%wQm>bz*%Q0zBXHBOzK~QpABMwoD(;bWH{'
    '>dz_@}~q{u_Vq742y;`EtUZq7~N*+z1scOCQt_+y^+pH|EIRrNxTO9eonAbSla~+b_SejbusR|q#K_O<NBlOh_;u#H0a'
    '^{cQe9LG+Dv|o;|VtKBZotxDjt0PB#kX;WX%c=++ukWAJJaG7cC8ZaB>-n)Cqz!Za=UryY+B2ndkoZNO2V=qYHf4c2<*'
    'DAaokk5&~&c5t$IPBmi$I%Dm#I>a1di!`q8Hai|=8_mj*bh?Unzy!vRsgv9xiv!6u6uVePm?7`r>P-e5&%2jX1p}*erE'
    '4M;yOy{HmF-YIDoh%7%u^hIJYzpb@lvkEbsdnSS@{+u16;~2Kv{%FJ0}%W!gr^s+4z7$n~TIBokE>8VA%DfJP$l)=su='
    'm2J6*rxr)-IZ6i=VglNExB`yp9ObBU+p1|46^(@XQ8%*tila1NnYLi8G63Z0R5mUG}(%`J0k2ySqIrO44AaZieIh<r5t'
    'HxwkpSm+Q=z7<q%sGnHCcC=#t;LN^u4DT=^xi2iMgW*%`#m~Ob#AF*o+{>6sA5i);yH@yJO|VkMu_gGONj>wq(4MlhqS'
    '5_=fR`&7?_uN2zx9i-~4aa;wCTv<^fWA9ITKI585suT0-K^0gq00y@{l+f`@}-y;ZJ!*?v;4e9^Jqvcnc!^sl7m<=~>1'
    'NG<D#>@IecaeI~oR~j&C<(q_Ug9p!g`>?l4>DET}pt58UI$@47bsEj=obp6AFn2}lfW{-@>XTQEsbaUX^a$ESu4DT(Hg'
    'r-pG%cegx=Y#(#VKsst(E0w$v41=b8ruca6K?q`50=h8ftZl@;)%g<5jZ8Y2$!;cVexavOwM!aVhp?6cC(F<eOImAN;#'
    'GhHqYumQ*<x=n^xBohCoujBCAd0mfr2cxx7_<_zkN+yk7jSb44#^#~4HpbBd+R4KolfP9W+T$wAU2iI}RF`Um#kDkHBL'
    'eSL8ly6SNJFKVt_5=i%>Zv<(Ad@QP;_b9!mUic`eBkGZG99BlgT3;vq}{oUXjf+FDFq+mnVWq37r+5|0}YBB=WeT%7c^'
    'wC`<0JY>XGijB=O|iQe@!xO1f7_xE)eL`r$NIm=4-C193ojm2#|IRb$sEiLoT$4Fs5#kLkoORPS%HXq%z>Kl&d41x*jF'
    'E4u=B*1xB0rxhak@^FKhX!6EPAl8Zxkf`6#c1L=ZpIiYWc@<07*nz}50WO!}{zS6I;>8i;ACIA2%?CQuy&fe464&VC5i'
    'YI?&o+gs!>`Ip#T)7V^tq~NQ^ow`s55TB^eDBRhy;2J&E*4=Ca(%-W}tKNc(vGyu>&Yc5bzHU<v3i49UChg-)JlawYFz'
    'DSq09VH9WwAp;0~rB}_vZ5^n~p17t+B1vkb4iM?x5rkoPT{gq-KWUJyb$1$k`(Qf}wIuN9pR&;|Y=ihn`*U8B)r4dZ<T'
    't^8i(k2`T1-Wf<U}NxB@c|L14!&nKy-l0LmZI<Fh?}MF<uPJ4)hZ}0_n-*Jsd8yBcxBK=E^E8%oIyEVqIU{r1&=<~S{N'
    'UoTN+#()f?8L$8x~A0c`=1Oh&5kwaodzWk$=I58!eF$e9m(N<C)<_u3;PlT7>*^iW`uBMaRFvTKZ&e#Ov(SVX0$kbfGT'
    'Fvm7_&mK~433QQdR@ptcdplVJYS|J5KO!U0C07oCySW>9GenlCJMv^-pJEx+dc#?&J1)6~*dkYa&xgQL)jcS+TXCLq&l'
    '|A<RXj(0pjK?e-8WeRD##hADCNN~alb1E^+7sRTy>hj@oJ6ACmsNq^n;)ZnGJ>tB|M6POFaWffpA2piIp;0vMQACQni;'
    'KI~n~ChRQ$4RxU}a!tF(~X;nEopuhTqwuKX{^g3a-v>Up@f5CPkKGdLkwb>EnX*!*1jd`Wr7Dn*sdVK*@%g&o=#|64dn'
    '8m+RX}gaSlIKkcxOxI<n61Hq(n&$e4>by@n_q+4(SzFYkI;ecdqA6>n2yTsE33|;iRC!BSpOjH|1t8jx2*Vwk$<I4tLX'
    'cn*H7%ph9Xzuwi~yVJTj)-2aVTTrFL(wCH*x3t0b|eQEd34`88ZwuwE&|=3Tw1tIfjDg;|zK=LvuXS>eUSStr4EbXX4m'
    '0>DC=Q%X$Rev!n(pO_p^A&3vm#YDLKQA!y2W|UY*8_u)XZo%o}<`2<<86Bf57jP?OJ%02!7#PA+0Pbt4OsF&b+R)^54O'
    'su?J0*-qVTMp9^mxHP;Z1pdF2eu;ORIO@q6aAQTs%fYdy^DLgUOVrKL`k_l(*le^;@OXK@|0s7jWT7k5UaU!BSIRwG=W'
    '{O9Nxd5>!Kd_0L0^3WZ!!)v5~RM{uiumO^BB)Dl-HJ_K9y4NUzu8dkFm)&7Fmy!+7E3({EXUCQu{pyxg6T@6FpLST<Nq'
    'Y-YwU*f?B`auV4hE#3qPLgp#HgMopooV8ndgPZ<4+-`?NQI^vk8siUlioM3FN!CX+`boY1<GD4gsZn`sxURU$SH*_!9{'
    ';Wh~u5@u^{UVvH|+mxr2o$Mtc`_%yUo`Rda#UeDgJ^=&qPWi}XznH@QcrPykdNoj%w?O(!rSEc@}1l(15Q;{!)A>;T{;'
    'GkyzKQ@5kdHv_VVyqCaxK{u{RY`5Gaj*yI|(We+DL>9jc0ivbDjCWk$8{645x)(x{uqK3kt7-IylVGUAV(Xel|7{YCp7'
    '?Ng(`axK><NV3)HM3^B-r;6Hmzy&UlEq6!O)XdDJ2b@da7EckFj(tyGK0*4XBzH6A#45z)92@&FDaYcV1yXT{O$nSjKp'
    '(Kx4=52L3I^t-5yAaY>vOD_mME7*=_eL_XAhP)IYhrrjM8KMS&d!f(iXlwV!K?#^+gS}w+Ix+3nhh;7tRRK{-gDsMwVq'
    'PYCaimQcdE0vuP-w~;n(}iz9Km0|UcI_Rk>2FJJ5KpQ!nZmpd*3T?_lxKICOB=PreC(lW+*l5N3}&R9ehnkxBj$KcJH('
    ';JVn10_$yLeMkkJkkHC8jA!AmZVm5cmKZWO10n4IK)6n2sm|9xX@p_&#@1#>Ic!mn!O_*Z(J!_rxB7Foffhvc@*Ugd1='
    '0>r|dfs^8!{?UBF>>te#{{X?P|DRQAZTpvi5_k_M0l0|>Y0lWi^4M7#gyU<tlw%Sxs>yzOrTt{g=v?7n@_;ak#N9O9W)'
    'Nr79r%`Dxxrs_qsWqQ%xFJt?<#VS0ok&9?8Ik*lhCSE++_E_DmCpM@e-~v5z6U~j!ewI81fgE1WpULL9}c6S56E+p_M8'
    'P%IQ?oXnA8i|4P?zlNMkCsG8=C)}s%J1-qF6)KKc^9D3*&x;-W-pHWSlwP7xL@`YV3D)!Lxxs;cdfCV-0;YZ7KMfg_zP'
    'LwE33GTy9;EoP?F#|v$9autoL?bzaW>ZyAOxU|fL~&3%2j^SDpBPQ;9%b!Clq}qGs%la-590?d%P<Kf$ixJ$vGVHuxEm'
    'bg54VQnfmJ}^`t|#^0WB4kxIFz?oWnkCA9h3<j5Xpy^?ed|ToRxyVHCQ~>jrI8lazQ2ocF`~5gs#B44}e9A2b9`PqnMA'
    '=myd*Ofem<E6No|wc8h@_(zbuqN1Xpz2Zc|>G4)n$fdSLkVbLDggYx0e_WG#+$TJ@qM{NH1hYXb^?f`@h@J|T^(r(4Jf'
    'IH`HuS19_TpZPNX+9nEO3qpJz=a=o$(|>k&yV38-z^fl3ei$UWN<WokXcra^rey6j{3(%Yx!7Z@1G?Jiyq3Y4LQbP2k?'
    'tztB{|Xvai&@CXJxS2PH}f&pC8?@Gv96%TM1{75`9Qg$VRuDEvgLxi~FTV#5g0<~?{<C63q=sA=FxS{~9P-qf${QPi-i'
    '+<bVsy-gwxHr%}*E{(t@ADf;=VFG*XFNZB{{Dg!LdN=^(v8mif5??%(mYmMQ$Tmb54%)WxQ1?@0)fm1wEHo5KSP%q(1Y'
    '=gCG7_*o|V<5@AAeEGca9|I(}NIjfHEmKYay|IBmvWdf$-vg0~LT@jJQ~)bWQy0P$XihhrvfYfH#5ZyYH4g0Eg0h(kdk'
    ';R5<#3PCX!1(;JM%<C8SFigP-DQe9+f}7~UpO8*NuCF1VUf(lrf>hr*QpaBjV>a9+{u4+$ExQNuhrB-o;QKd0mK{jVJf'
    '!ZcS?2|;V1C2H0{VbiZ(J0}{}9RX&f81zY>Y$dV}S1z;5#)N@I{bVvzE&PXR+i16t_XgZDKT(g`ZN~IrITBA9h~AhYg1'
    'zodTFI1J)jh5r*HN;nxT7Zn%F8@OF4U0dYnE|5}>`vJIO6bP9g$0Gic9nSA&?1ilOLKY_R%@C-N~_@a)_QRl^hdBu3)r'
    'I}{1t3JZ#g>kSQO>M9L!U>%1Ubt;3!(?^iA`#?mFNc&r@Cl-XJmzV6Z0Lm_!rJQis*|rj2r29MLnk3)YfLA42!2?A58!'
    'tms&9rzrA0sc1-~!?_lp3_evra84%YG8J~<B$=U?9gEGy_Xt7i>B#6rADml^)4kIZ-isx+`iI_qx=`ymp<9)Ros)y7%d'
    'C{fk#Lzc;B6`muekoFH*J-basKF?qLq85G}n12GYJp$QQzYnEQU-vJ^q9XLrWhlu;THbCN{X1O{YlF=K*AA%iiwhxTS1'
    'F(?4rv1M8KCTi`(a=+WLF)Bm>}rYC_UqF2f?p~X0dJLyoO?13?DAR@2{8&ni1Tfem5S1A4Hsv>lNXL=NBexd)lmDP-1p'
    '>Lb-v;4M%~L6Zrt>(vOpna0gXZSV}-&IGui5fzb~B9r>WC4I2(5xU>l5_|$8ca9KP5onAzdQ^#KhTo+E)@#o=w32^TO$'
    '$r0$9BKLp4@qgdtjWxNKt<b2F<$rpeuQR%I$+OhLlamxWNXCkII&2>K5Z7mAs~gFVtUP>nxJO!WDFMQwQjG4Mx2FMXHC'
    '`}(VIX&!b>AnD_iio`!Y6WEPjG?(&HiMAr$-*KLo1yADl60<^RE7;34WustBm*&_Ntnfm#lkzN(_2J`MrGj{x(#h<N}o'
    '2=}EF*9Nm*s!0m$|3G)`S;jKa2XF|b8;{LU_W*ivA0E`x^bs~U%<Ycw+3dOD*Rd#B-M~v|VLRgasKYcJ3Vde2;A@Y(em'
    'ySC{&yp;!GQ!==YbdSK#TK{6!WUOfaX46I8?y<T}@|I>P%^s_b18?P`<!>KkgD|wE4nR1&dq%r<IGM^OcIF7=*c#RMkA'
    '1O#&>FelyBKq~DwXG?A}~!Z$B7B#ZL*VK-`F&{EDpwH$i=JP;Pe$taCGP;bDv9Vf8@PilP5YNM4;Q~SrMBxNfaJIz*()'
    '_d3MdC1V!8X78LSZXQ+2}WF=6p4>eGsc(m&71)n20Xtl-uM-cf1q<btg*4NSBd<i7?ZeN3B_zPX_?c);^C6<x3f;57&r'
    'e!qhEl=dian@*W{qFw$G$&x(c?#1h9T{9JvczItg5Aqg;wTg5^mSg))b9X0;(BP`+NjPS2(kBhrIoQcdg8+qauROdg#N'
    '>gw-QRJN5iZ|L|zOKuEPZc2lBG@ez)m7DKMZTP@RZFrzID}BbvIX1<92YzzEX2nynA>-Zii%}Es5`MhPOS(bjH-*6-W|'
    '}^uTXsJ3SG|ozjNMes7{^rpsf&hGJUvEJn;$ydDx8}ByKaJf(R3cWkN-#A1@;nO=^%~(eSmi4gYe~asm(~*q=9@>H})$'
    'Bv0njglQ7XfB<KH&J_HCD1)L**^E)R&v_Qlz9YIvst6)!oJqr*s3}NRW>?0^{FZLn;??hV$=o>L@*6oWn?1Q9nX3x4>%'
    'n5$Su}Z(7Bq6@TAx+p`$91flJ<*H@HNJjrvXS8)lfRRaS{+Nr{~lBOmy<Ca{PJrsp}2C~M9&=3`RNVQbkSzSkV}ugV1#'
    'UpjEW}u$d|O`({>SkkiKj?3pG&vNvi#Ptrhgd<zZFD(**t7^P9Rf@%auKnDK>QM;cWghu<}6=2g>YaVh|y!t8AmN-R(W'
    '%_>?2m(+Ro?SXCZKA!oxxTd$O_hKwt2i*C2?`LPa6kK%HtDFfB2fhhVgOJm!NX2+~twCVVT#T8ON5DB8ZgYyp-Y+vh$8'
    '(UPe}G1$ltH?zkdSld=TgEb7>EA?$j`=p9>uS|MVFcIOe8${2#-(FGBiedKZiKyp6mb9gYf#CmSKr_viD*{kAVq}K!Qk'
    'Smsk<$9gG<<3B3Fc;zZYBw#OL_GXSUbo<|2SO`|G_L(38aK8Jv4??CM1{+?(yeu}$?61VZIBp5_KN$FLn*y#I)zb}Rfi'
    'TiyL7U>Q3o;|(r3_PCc{c6dc!6ThgxwSrn;NGsDvlFo1v%T?%b{rHv`02XdP+#^JfGl+wl#s2RaCeX%;+LR@P{l5K@g%'
    'GvxN_hlY1ilacXEo0rF$9r>$v_j>5x#1i{7IV!{k%3{f|#Ei&N{!Pw8;?aijm!@t2mz*t$2CPp^}IT8mWu7hd0R7XTd}'
    '%g;QF#B20wEMT5%4PsHc&vuV?hg-uo{(b(fP}5obW*GUv)FVZ#>fHYqvgz&8^6C9J$*!)}|DqXx-2G+u;nu^DaqGY&D!'
    'R{gpUXV-_p~T@Y8JSYJrU(qWZy7WDt2agQzEDT^esSo`oHix9C!$>PJz>CNY`b-&}S|!!0PV$y2u%N<XE~M`%L(2k>>x'
    ';lc&xN^Z$u5V^1<LvkN4Uis{7pO46YLddg%j-QSldyM|&J6X5=ugzP?tJMr8(xSiQO7<6$#_kd^peM-UIbdIp!m8fg-a'
    'eIt{srQk|Ya{RvMX~QJwA_K-I4X+XFZ+AxnVVo;;vC?SJ$2CWru9B-ON;!PVK_!eqbI#Z`po&9e(YI<tYGQ6ZnVc2O#M'
    'cLNQ0T#45RLmOKoZJN;3HuI<vxOv*4YTEw<AOY|^33U(>nj6)bM1;3nZPP(VaK3D4BKz;=Ix9;;UjuQ${|M=8vJ_Cp^m'
    '2XT+05{2&L{s@hUO(w@isCLz`#&Z51CKPLVZJt1UE8-L9BoXIQ#M0$iLb^gIcDh+e&!peH78n4W`x3uJ)O)sX)>3%KMv'
    'b3aV|H}(Wj_Fb*xUW)(yv5Zl#Gardv^Y;FM9(<{J-efUB8QiOP)>HUBoa4_EuMm>n1=O0R$CZ4K-TbSHt1<n9|2@$Kbx'
    'j^?+hMZmMx5FzP~x+6PgqwMga$<3`*=qi>{1=H%_0fxFx6C+)-9*OKrcJY)(WDstt30UTGwWkr=%@iD+VX;Tj0jDz1-!'
    'OS3`JdqJ>K2W%Vu@9F;aV1%iJ4d`5Q7pb1g_z!ZK6>=)usr@&zxfw{A^IXFcUi>cMU|Z9sr0+7lRlp!X6X@|zZyi*u)X'
    'JF+j^DQ!$oe3_z>U_w?HI~m3#5@K9{vxOHxfbHH0&yQG<pP4^8Z^u|k~KEmxy{d?rK^xqfp9qCqEiUXNz%RUp^?T+)um'
    'k<-%|=%RS#hCVvCiA%}k5>CjLR$I7^5_*(gY%094!IB&Z01&EwQo>2!WEq(@0d_y0)@1K;uz!9Kw1seei8Q)!`$Z`+ef'
    'umaVS6-JI~gdOwnFyLTOiJU1VoD81km=W#YdRrSx+d-J2O)!2}N)ORywNMhwUd{!{vr=<A5tMK&Mh1JV4LA$sTsH1|gf'
    '=B85291;^p-7l2+)Eii-=CR3Ys=+wPZm@Sjq@|#9+B)ReJ*s>~R;c4w?C%b--PSEm4%s@5-_fovmCZDG>6_LO1Xxsi-_'
    '!BcZqwkwb9gXF!+Hv34pNu6fouZ0&=@VG=b1>o!lEMhqa)B!sr&lx##kh)wzr{=y4a4e;=W&QNUCv9hhc^AAW3cF6DMz'
    '-RZ6yigV$6%drP^}L^yG1clr7(omZ-v=$?3D%_E15mU|lV6tHtRa%-MrEdr~<|mNeBK`o}p>6nMw=T5&D3q|(6bARDGR'
    'ng|p5y42bU7x+caNfW7BDd==+6)!bl)rKb@y+|%<rD-iKWDgkR8ijAhy$W`<$Y+&(L$Ys>9*>By19aJGRn>YWau?mEN;'
    'Dq$`R3ozo%U9G1ad<5as2n+rOSsC1#U|)aPEq#I^5PB^9ZiWPP9%d@n6mqIn_1T&SX1Rks77N>Ri>N;+IJq-@`doOi`e'
    'phFGP&CeK4`)OPt@OI&e-Ysk^jm`hxV0++?EI$YN6PsXyei2~Wh*;RrO#2rOGPFU4o-dN<~#6`3v#lBq?bjR20r*@~XW'
    '&u_pAN$tG1fJMIT;#EHOjWC6#oDopPQe0Y^)I=OZ~hQ8h*k~gw#vm6{WS%y!LP4k{V4s~O-kE)syg}Xw;jck>l@tCk7f'
    '^-o`CwE=TSZZkE7b7&iIACmFB7w-lowaT`da9Q$?$!<LUt?JzOnz_W?;*<W;Ww10JKFP&{|xnk1|8%wql2P@fqh$EJ}o'
    'TyL4>QSyEU?o(YJXvGex&cbZgPxH(pZcByzbaR)t!*vdi><6LQl_A#*w!2hoEX%)DW%NTI4$?y+I>o8Ohn*r<WNv<}j1'
    'gGBTlF`XNVs-xdqG6VEf*~1Vqu31XYWHXtM=fGrE3p<FlnTORahn^@`XYvQ7GIfC7eQTN`J|-I<*PL=!dwdUrfYibhr}'
    'KfVSv|E5-2WH4=WE0gGjB+iNIhj&K##b9-(V<~t&S&CwyuEXGp=gz{oEc*I*eTtgH|IXqxT9$PMpL$SMy@$;j?f@1uNq'
    'mbul6Xtv!DHYmT3uZdCq90WN0u$(Xi=G@YjbtB#m?J0Vx^tSl9y38736(U{@BYHj^bSN|lY2&4Jg{Z>kX0~djX?9`Xeb'
    'xUJYW^l=(ANRrh5;d<{Tf2xmQVCE<M{iil?A8FkV$03cAjD=%M#<dPv{_ZK7&KnC4BM6eeB+k1vF9cP$E-NActOnAmIJ'
    '{fTK%an4u<9vp^s$ayQ2+RJ!ugj!uqj)LS`4CK;RRC9|<1$iiMDZPUg3z|c_ZXR}7jKZ7-V}n|un18RGDu4^3;^O=!+H'
    '`PoUfksOsf@@&WKPJ?k1gc#_03uSt>X-ykUH+EP=5MriUJAu`^K-s<{Yn3_Sv=L$f|{Dc<_0J^4#x|If=ilP+qtb58|k'
    'Vc1ypd7&u*7CE~u_9HsGHC~dTq4hspk+`urd`<d|^0vF(zu(lvr+bTf6vcmrW*H>34M-M?ID1pzb`R2X23PoEhT<)!?R'
    '9@`TNfcOeC3c5b*?kNr00U~ZFrTa(EB{~YeF<1o*VgEP1PBmFR8UmZsA#RIMdO446VxcyqEQ5-ii!dPA}E}r)(aRKXpP'
    '5I+iU0HYOS|+w!PM=Y7()4^H>XY!l8OFwo*kvMe^3#X9v)0?|<LB|M$N4eeXwi)>(V)wfEY?IeT1dYszxn`7%Rng0S;*'
    'kVr6+sDSa~1IQRu;^ZR4gT_xgUyKp4!H>tY7~_c(<`MpYlHa&)ZTVwcBVb)uzI1V2`O+?47*a|)ZAD2})af3{mpg{5ZF'
    'r$fXA;Yk{8;oZZohb;Zohbb>j>`kCCw~txf-SmevsmYkHy&W0<R{M;61xE-XX>F&aIJtNls0-ein1_sPYp%p<>@SyVO*'
    'zd+fdrDLpzL;yRu`*>yahn3;F2mCzIY|MI;4km74>h<IMVL+g3{D53=rYA>}!HMcxHYI$m+Pk1~AhSchMO&QV5oEAQ8-'
    'E!1SNrMk(8ZMzP8?s7<S2~Qe#K@+`JBQ&hb~7g<c7Npt@Yt{%O9D5nHD#<vI0u78@s;i`e%@y%J$e6CJQ_jpwQ>FMkUN'
    '^gQlD!0gC1=EgRkXF8&bf(f-gfTIq8@H&$GzgL~;ysvgYKulQ&OR`X1!!8t(G7co-f+j0;gVT$h~8_18eSWwKm_2Es1&'
    'o*W8<uG2#T)aT$y22b*aUm7Y+zcBnE{xn~O>CE1*WQaBZOhcpH#KT|9<kW)44r!EQCf2Deo*yP2*gsi6F5b;Jn^ln4+k'
    'KV((W;oH84e#~>LX?q+Ue4vngAB0CB{cAp{e9hXQrDlu|3ri;M@<EDt737dh6wMa$|}_7IMBaK0&m{Y>KQ1Lp2a@)5Dd'
    'DSI_{Ux}Nj}<29G^dWDnPDD;J|TO>Jx@9=f06wK&d25<3FJ`izK(2*&&SJaPt-p$x5b<?V(M>8DWMCzm_zKm9;C8jY`B'
    'FdW}=0cbeM(*l>7(<|;cq+E*8y&%l?J@V&w$-VPzIKPg^e`%4W_tLdWvr^uoqqhtMIx%icN^|!_DBppWIE(LeTB6BQzZ'
    'dKtT&$Ok0@fe1|58+^Xtf_K5)b*h&+5gRCldKy2C)Y)u+aszB0`jCahAEA;9G2!Ip>}Xi^~o3VVdGgnFB9OH1ktZcv+z'
    'cyxeRM3qm;R9PmtiNeedy#vwl$3UD)Jd6_X4%^c*#cirVm?$beBy#@bey%L1ctRde41)N`kts>+8Su_nO;v%nCE}gX!)'
    'n{zFL-i)ikM-{V5a27(296I%skZ1WRpJ6-9z(q#H0b{*N5b{w$&~Rs_*L2aBHS&_PhKs$a<0t`baY;=MSB}0fSVv?b$e'
    'a!_O$kP!oTw_i<DQJaqbZct?G?CNubPb*Hamv}Ud;{~XMl`HIk(MvI)+=o|CB_;%(1lS66>F)`7m8Yv8(gjUr`ykAPOB'
    'ie_m4&N7d$;>e_KH3wLiFQ})VC>b(q8ns~^tMwAzfLzB9^z=S=<CcejRvl}`kc_kNx#Lo;btkQ`{Ii)K#-bm5yN|@aO~'
    '+^DgzdSkW377;4Ncr6Zr8`kan_6b02SN78xB8n#Pq8q2(~VCKp@*%LZV9hXjLgs7*vccxY$5TqlOv+AN+aJ?ALVjCL-S'
    '=l66)XpCtvk{}jjm>nSLo~X7Z>Kq;6*_hg}5^0i4LJm0^uC`%|n=I#-Do<a9X-Pv>*Mqom%NTvDe3R--%{tBxwaVfe&5'
    '2TTJBN>{S;cwaZ9JVxxu)NOcdq3wzPFm`U3}7kZG4T*Emy&rZUwZtoQmAjBVI9v2f=R{gt#H(PB`+s(djxHX*v)h3^?p'
    '`9{hKrXx}CZ_OU2x4q+7kd_Up;iGB2lMNhYnZpb3GxL;T#+DAVD=(@S^vj*JOtbfx!I!69q+egPxe{LV0#-RqF0v7~M2'
    '~Gx%0oMq!)Mov6*+(a(;lxZ&2R9cSyXgOhee}xg7W?R{vj5aR`uRnqPB8GgTUZY8Ff#kUu#aAu{qNdGC-MGo*+-{wuK}'
    '<g+y~%30rw@iZQ%BRJD&aDWglHEBLRME!7({eOhC?mlYMk@;8@8&FUQlPCFjIT0}I{CJj3|%!XD-%nfl!7cEzVyPsP4d'
    'Q4UFF*o&)_I%kZGs{uBniYYml-`*HLEZ@%|DPzK-jU&U^hGxb%dNAkV93yqKPZ0Lrb2@c~gm__k6TO(0UXaDd$<dVB7*'
    'FU*tWGbyjuXCFsP|N1uMxM}q3h7gab>%`Vn-FZfTZLc+d*@Qm05N%5ikMlXT{IFqo?|urj%>1InDXw6;EiWH_bdl=7_Q'
    'Ea*M!ObQ6EbUK41q_Vumsjlu5Ge#N)s7@D+Day@t%Z;<j;C$HEYJ$z3~4i=T;-EAdj@q%y}dl|c}<7wt-S!*w(bbY?a+'
    '7CG=uCFx+eM$eDzI@quRPJmC9i@e<<(oEdPTMRT9!#{Q;XAhJ1E4GRd8`1iX+1t$J&<M7J9y&q0L;HPz(YQ`ok$h#{fM'
    'W^50+-S3(MZZL*i;1+P{-zDreM}N?D1iLRMcYLny5;RU&k+FI6FQL*sZJez>O)r`T~io<Fn?F9Pg+3?}ICitQzn@}4k&'
    'l%nb`Kp8-p<{0PaTki1o{m}uu@B!`pGwZAQiIe-Gp(*qOzWQ>!VVFcNWr##d)=k$chf6MZgi0c>sY2CIO7=HUME>w@l%'
    'e;b_<cl--r_ix?Wj2hsU6}v_@Sr{NEC#^L~%drF!pN_hLz*VDmRAn^euC^`|ihRrOa%UM#(VZjdt%>6*TclO~P^PL1EY'
    '=Z#dB#M^7+*^{o^pkyD-Wszq|Xl#Ya;c{Ml<L_zciQ9)E3<@z1Kj?m<{69F;!jH6EVx%549m|7$?n!8QMG3Ux53^|$#)'
    '6{N|kCELK#et|oF$0L2QnsfdUVtnS{UJqhZ5xW0LGWJ#>BS3wy@PLxFzrWjS~hVL22d2ES<-lePzJNVObLv6w}KqP&ex'
    'pIjrfH9&VhhcGbF?!CS8;nA20l{*6GZ!Dg9wOz6rIrS9?Z;va*P<e6NV`m6Gv}+s4tOxX}>s4G6f<7UAZBp%NV)K=v9B'
    'p~DL9VUVs6BnN^#jgE%4JsXH}xMH8DrgRhPfGdho-GoTG4JGwQN%0AW4<KgEJ*GA5eg>ffkLP~7p-1|bWjHn4N{%8s6~'
    '&@1!ZNhf(*(aDB#eU7!n^$rh&d1oo#c#_^M5R?kC2oR4CrHUDXi;8gP2D9P&;s%15vh3sEzKBo4X-mCc5eTFq{hF<RLa'
    'wD;931GyO|EJ)Ph@i&n?bgt-qX>s2$f4lnU3sD)#j$iXasl4Dy72YCu|eLcw8aa7B#<;z8te^Y_nO;J)q?!Ym}HNr@yy'
    'hY`|hvn7BRsLB&)C_j#x1xyvhc88rV21Q^%d5l$fMt%;O2RucQ|64%#6cEc`fh`hk87Hf=@p6>;6?Mlpa+QYbH*uR0ia'
    'A$YAJT_^MTVz%n;}U7TUZ*_e=Canz*2qo)*^gzQbKoYbprv;Fh9ci^s(5_D^E^qFSC&ib8sm#}go;rnYdD>31*3Fk;xC'
    'mA&gfv3v7Fzb85Lf(Oi<gbQAHFhUZef?rdC%#Bk?VnWQr_5dm4pa5SINlZU;B)+6(KO0u_1c@J3uz6Soa>s<UhVsMsUh'
    '*!oGqd`7Om@h3BGgpmD?%&Ai$syh9Eq<!sW)=iA6t!ROp4s(q2%LXmOfS!h>3D?P2d62{O{m;edKiV?&6t)wD3qBB0D@'
    'L6W|vOzyRnA6h4oT3xua}@RWdF<I!tctV8a4PDq%R0MFR8V}%`$peNkn@n9K=u8#B?A&|_iWnzSl6xJi;o`I*@Wi;YD='
    ';d4*5DU-v9wi8wM6oTzG1^5<NC0|*<6;8-#ZHTj&2&%9l+F;MARzI}^uP!b(A#H*(4X)}^MP0s8?o3lC-}+11zj|U^4n'
    '`FR{CoW6?WHD6m;QBXV07iJ%ytYiu#V`(8}L66@^j=b&DS_gI82CUW&h-88)mZ=m8Y!gBlO4O2vnE-tMp?ij|}I!0xzh'
    'ikyXu51g+M{{X($9*EJtIbOVB_yDfJaZPHj(HUq%9f(?I;2sKh5=gGI3fzX_^mS@|t-97cj96UoDfd7)`MT~0=y%hDpi'
    '!o$`@_fV$K&uZYni!R+c<YQ**UbcJ<KspqH#v&iNr*r&F4idDv^`_j^Cl<iDo-k$3Z0J`s#IlW0-wsh>2b6Vn5<_0y>j'
    '@cp7q5%z3IySBa`{Q(G13hzcF`7Iy3*igX_$=JJcZ1%4)*uc<KtRp~^}(YLVw6ETx8SW=lOA*yt-0=tTg$6|%9$Y&WBC'
    'O6q6s66)&zW3zDMi{hBfpn~>GzZVI@SJV~LF0vJc*HAO003bW04G-+FAUz(R+lbbXoq$Q4n$;11hdKMJdzolz8s6O@R-'
    'x7t`8rUf@^ofaOoUvtE96)OK=9B5ci8@=18PF0$a6HpkC0+ohYy^NA(nUMe$8s6fRQ~Um-`+;6qBT+eL#1=fk-mPPP%d'
    'YqLHmK3?3CQrp5gmEujCMfxN5l?K#A4Lm}YYt03fCD8HUIFwjF3fA(av`lWiM$5#^or%qata*MVu6sjOI|6Av3mpdHiW'
    'gDzfT-%ZPVklp-<z<i_}Y?sc0sxRJSLhzWkJ1XnmXjTos$bd9blPQ24;P=so*@rdc_OBnTUpewIiEP3f5fYJo#?8(8U+'
    'v61Jx}B8e3W`a^Gz3-Lr-MpMpq$0d484Bl-aHzWvu?=N1_j*?t3V{2`_TQ}AjSRpQ1*JFbDB*Ah^YK0&_>U5S4`~==fw'
    'wFnevYkwVA1jY0W-fN(DWTGx?a8lHhQICbIU!T0wk2AtL9Dx9e6F@_LenFg&^*Z|v_@jB1@wzAzL+C!s|I<rZmY(VWg5'
    'XCS}Sn|o+nlBwhzaKiV{EaPOU2TK&4BarYyH^wf^^FTN$~1TEz~JwZz>{P;B>6Y`1Cb%esYDM3D=gEqe&VP8Iy!{Q_<N'
    'NbY_<X0`j6VjFjI&P-e&Cq`0A1pJnWc2eT$mDZVVf*w9`7zX{p7qIvia=geY{MuEt4obr`wGESwEtZDc+ZF3z7EXND=r'
    'K9Gx~8LX6)K`BSFiEF*jKG$RzSlK##@z?B1wfLh}3b_xB-a_W`2)joZ4n;jLPk7YJ727fGd&;qg_R#=wWJ1&h21oT(Qj'
    'G6;Fk+M&+n(6U#nvRx(?#>}pw_32wQ^0Vjm&bEf8IoamUlrIXe6au0;W!bV^vZkC3cnjbyxeFnV)OwCPP0C@phX{dEtt'
    '04uYD4vgZ{M=O(PsNHo1+V5-9d!Ea0+TIlOeAhn=KhAChxnF;peX5%!^_8-mvcgR!8J39(cBG7S@FsZV$$m-?Sd^a4xG'
    'd)iqWpUVtbTRvHesHS{`j1k3~Msepw0w`p+%z8bFBE68^5^+12rADX1?rD8=>%n(}1<d|cz|H%0<owhjwarzu~0Z#)c$'
    'p(&kq>Kjd^X!T2)Ye&14C{ejb1<kmy11iw+?Kt2iFTm!_<Yn0Gm?hSYRHxa$v@R;j_>vQvI?hn|a*-m_k)!CjfAD2OE<'
    '{53%*5P}!_(Xiq1xtpsT_Xcq?%ZNfrGHZyG>{xjo4pm-5}q%1MAvE^cSH*Gam_3TuUtXFtn(f(tU1EszM(?>>K#IjG}l'
    '5jW*~Dvz8Ox_W*-XFWSy!y`iqq7dOyn@0T*^(JOJwvl?&1%_4%9I)`PEO&IhmCH8~KmDLUV$r@1v?>@?n8{}hS`;JO><'
    'NEkqW4qY}<)|okt}}SH8<i~KlqS2>v|s9TjXT9d=1SLfwVkXs+1yGS_7knwpw;fC{caG=4U^psn9eus$4-O&B#j3G#(x'
    'Za%P{^QA^yE4ySrmmJg#)A>+yHIGw_=DO?Fq;^JTM3AEzz<Cz?!M5B|bq{+T{P0hCc@N5QinS`5iV+t<(w(tA5m@p$oa'
    'qIkS0U4X978F-6)fi({m#ec<pv|(s<IsH886=7nr2}85_=|@D5^qmMdu~?L6>8AFk<;#gR;}2SRjL74cUMzZS$_I2~zF'
    '27yXiJ&xW{GK<nkmO48<}n}95RYT&w7|ja-BuH%F<jPV@!LM<5_4&!{ABk=&_L%!*z6?K<j+0|HkE_hes^>wCz_=Y}bX'
    'B@neUC%@`XNM#?~j*Jn4OjYqw#K}RmiR+PL<J`s)Nj&A4)8qH&y_&hoP7a87yootaWHRZ3BGORv6&azPHoW53?G#g^@w'
    'Xy2u5fCa?NG-;WSqjwnSm8Z_$bc==jUTsGCUh%-*Gjt9JXQ|8GGG7_KXk2Qqj)?xSiEyL5F^KZ^cO|`B;xJ5*gFneEB`'
    'DX6>Q+6f(*hi8VFjuXpzMA_1v|QWTpuwQyP1`VlCcUCt4pgH3lix;(b|c9*m<y?QBvsJJ}?7u%pObR~~GKXhn&dTuV~D'
    'MKOjU#$93XAYod)#7iftH{kQ%iP6RLE_AR+`T(E#77{@_y0wky9rTirdowz-!=l-~(QKD!MX|vR@69Or&aE|2s>nyqZI'
    'nUnR0$V?YF=**0Cji)&tdSK@<P(J3oxZVe<A6X;esbH;Tew$Np;S^u;<1wi8b-9BGllSja@f!z;9Qq-Al|#;DM)sXa|I'
    'ygX4|o@drg47>Q#LESN5Ba(<L)&8l5|UWd=?nHG7em!o@YzE>gcPelXV_^)!%B0AYGdZ*>MUoXsnxwS?d;|4lV#Np9w|'
    '7f;5Ien80wB<<a*IG`HfTK5{i_u>R59#>e1rKvu!~hw(6KOycI%xb8i8y-TuqQd7S$Cnkt3TiSt+-cmE-OR{4zJOcNo)'
    '!e+E2iX=83sHe^zqweyLpuHty6xd<=)Ec-`|#$jX990_Iks1^#g%!G2l^svg~iCN(ZZRh1Q@0^cAK7R_CBs`N8FhX-+8'
    '5JCh)gq42NsrU4@-gpuLZ<KyKQSQ#aks8}hlxzrY&P!~0{T(v#DOZK()T?ZjelsAcbNL}s%zxFv_c6_yM!Y>*Ox$#9fT'
    '>#R3@P~t!d2Fq4h$#-chs}8vGtMoUL+o(_eJ6ZvC6A#=ORMjK_%cV)sP0H+rlW4<U)~1lD8pM9ekX7HsvPqQnu&GZkw1'
    '6_z9zEG;RI*wh03T+7h2X=I|hq=TKXD#B_z*k0@E*hFo>%$yh2}!CZ1t_N~OfHC>eCgO(^C_b+}t?4j1!am@kJL>Ui1C'
    'L@tUnB*eM+mNdcKN(9%ODrxKMRqyyZ%r3vLF-#$l#lyYH$4{iaBJ*kNI6VoJo1=~L=NFo7g^qhTy^BhSd1;PxMURB(}{'
    'm;x+u%^mM9<hk9a)nk=EFIA>{~>@#teR5;+8ki!5(Lt~&Z;EaxA(VsXhRvL7J+t?8mHTU}x0kNdy#c-W(@u@@rcD3S5l'
    'V=@vsggGv<ybZbP*pso0Y>CAsqsXoz{;lbvEb^8pANQ|&@L1Smt+D@tlw(B3<B!Qm<Pd&vk>zd3RmY!<WqnI5E*VAkeB'
    '$4lF3OVL66NFmhL*7Kd2)RYG!MjA9lhw=-;a73s8WX!l{$<oS@osrKFmlwFIEKva>3sW7?!|mcnkT!GeGzl^=Afz;HxP'
    '|VkHb;5gN!BxK%-<HLeR7*FqAPtR=1s7+3sbamkqTBrX|aXyLttyw5!$$0g+L@`N0%Jm-+-Ve{iW=aA=m^AkM(I{$~7p'
    'OXLYpNOL}|KG}gVe?b+pV9n8{_~%j|1r%^$^XFSf0=*(r{y2-278RBO*07~rq2e<C^fWcD9#3#XY5qSU&mp1Q1>(ZQIw'
    'cqRACo<P1OqH&RN*`D~j>5GYr-T)runVM^QXW^c&PiL}LJ@lrbV@0E{Y1hKtVu_}pK7rsD#emY)?8R&*7S(8^`A+faPk'
    'QWUFj=^WmPei)*a!XFN;G+p&F9hQb3<|~V?;>NJ=Ve<<iLD;jy$RG%Mu13gkzYCR)kHwiDCnKXC7)QWZVkpEnl#C@H@J'
    'Q(XN4AL2Hht4ot?97JRO-ebep~>u742JTo+YMObs1%>x?Fh@D+~><xmRAUI(Xi14H?_uH8<CgS(FIE2A><a9!ZS^>jwf'
    '16?4J*S5-TF==iK(RiiiqX5%m9heXhsY>yWCnKtsD_sho!vj`~y2RxWe)xkc@SfbutH8i9sB}D4n!!X`3#;OVlF&zj_o'
    'H?U(n&}qG6$DK~eF}Iz@g(L-)<?KNmhr=0EQQtuaRu?x5Ez+B*_XtQUu<)S+(8;t@CtF2CT0e!OGQdEav%IG3o;#YJMY'
    'Jeqpu+P6cOT3wJ<Ynm@ibega8zJL6;y^jRgFE({4-upKZ7I{)={d@BeDM-OY$skGC}4U;bZdx>KKMy25KP04o)AXz!`&'
    'ZfVsWvbcJ!w&)fr)3sieTO3kMbQqM19Wru|%`-Xynu(kZjb=ll*>2Hn$7phAtyeV1W-1*W%RR{|MS5m?hfiiZhi9hL;h'
    'rg*In`1aln^Uqbf!ut+yp!a+NfmPnHoE?a#LeB*2|O<VrX^*@-KuKE{s>2E?>PcD@12HD1r1036{ySsfJh!%)AWn6Jii'
    'v>PU7B((ybDG1Ijem#;_55);_~Q{)jm>&yAZ3sXAMi{_of1;4I%G4+~|HMocvBNaw$A~q3m-DK)i=riS2O^_tomY~;_@'
    'p}JVsF2T}=zTIxbCMg_qKk?4wxThqXx1;9#XHK9N;BPOz>m~%pWI8CR7%=-TFEKCg6q+sm*Wv3&XX9MQ}T9lb$j(X4_s'
    'tODusAkRSd`R_7!rGUTwmN@wtx-w-WKyXt9o~1TH?t|3Xru2U?4=S4)d7D~wL3uRYDIZ@!>!BrE3XQ$+=G95iq~r~}!F'
    'h$?zHIdAlmX1Y82Lqt0pA*;PB(liy|C2@30E4mL{9f;^&4Ceu1S&vL9WKwNQYEdF>BlimLs^8V(>b3e{T7%vn*yp)&?h'
    'zg%W2XbdZXEl!Rj9<$!+k18yOXK%dcCiam6~GYZYNV^&)4hym28bM)&I7}jw(b7BdGyW1@-iHyWXu{SMfkioWK31wCIY'
    'H?C;5oZerPBVG-q6v+`hzap4vP!`!DU7}k#)EOle!@HA>nBOjwQm}8oRSTr<@cixY4asjwL>Ev)n*C3t@G3y(-4&tU@i'
    'H-F(2qVf+k){__W>G!j^^JU7lcUqqbi_=D$XP048ZSR2Kog1LiEAQ!IjSNypnxt7J|bWk2$+!|>?@~2MVOp!tcS_z&UM'
    'Um7d{2=OgCN-K7t3$kI-C$6NER)MV+!w#I0K&QCePbxT}9l5FUWMyu_FiCS^)~OJu#{1nx)7LExY3E+)-vQ9IjWV!LgT'
    'v7MbuWx`JjoX)hOG<m~AwJp)?iI<O$my)C8J7uUAJ2<y>znYxWSJ!L0VUCGWVLYGoPnTiTkE@WnDT?nCJKOQG!VfE2HH'
    'jJ(#i*W>=??yzTKq-Zk9cT3w6Vllm_syjEhUY&aSu$mxYp?;w^hXoTRRck3TC}*CkD|p7P#}}1~H@|R(Q9}6(cLdm{wL'
    'fI+}>$l80-J3zcw9$DFvXEX?afZcfk@jLu|;5#e?#P{*eeiA4#8UrThb8ZLINZ@4(w)a>K}TO$!GgednRcVnz@`X%wYk'
    'Fq%qzo%#w&YmeO`Uuz4GDoIwRg7VT-QPH!xP-q2SKLR&#;WVb$CDjwVuU-^8QAFB#vZoipSQ8M&~5DSq=3S%XhB}DoH#'
    'OwmFYBxxq8z<C!`x{chd)K+~V5i#w9>asiOFd8{Ot^CaR?7f@|iaYID;0@FZ2sUbiYXR@~cF+rpDV9^30y(Y@|kGgpZl'
    'Z#&6SDzTPtkq)Pu-4Ala&F%wc{TZ^s-PN_hJ;WapJ!eijBkplu<GIT4q%&MF@EU}7Kf<$I`ZFlzTycpO<BfN6XGC#uyI'
    'bYj?p73ENByo?kxY(GkTSz<_-nWag35LEdOs;UFgz((+{n`u1S{5l(_-2GNmjb(+vHAiqouhS(3+bQkLM=EPt47Uwz)a'
    'M1p%)?IQ${W%?V8@t8;DJJGm22rU_NtGd30^<fO5&4o^`q3>ZuDx}M}HJn1~^ji&lTQXtasq-w;@2pv<%E_=;PT%u1p0'
    '=1b`5uPN2dR#~9QTAA+qK3zNKG5jR`UAMjkGjKX@K>W(DT;q5Wod~CK*d|EXJMxg>ATJPBankLq(vB9g4fX|V8bTxG0`'
    '(^qNUZmNGz=;bHSeY*aV^b8nFjZx(e+%ITo6l-1ne)$n|z|HsNm{U`84>581(h0++xOu5x{acn^aufwX`lZ3QsvZJJU}'
    's;+nR6-&*cw;2+M_3Z<~18;g(L41GF_W926qzblof>1pVKbBoYp0;NgT=@!5I)deUMA(iY`7%rDm%{ij@lK5CAyWK|$}'
    'RFCYMbUzu2gd@PXZZ3TW@Bc+2S`AI|gOEa|8@#4m({6hMg?qr3PrfVPs83^piNp%h21o3@^z2+Y|mW@Rx!AtD-;EKac0'
    'b_+yg#1b(^HUem)IW*MsW{ezE|@ju&d_BeUj_s|^kVyQV=CWht>c;y0gRrmQjCu{82d-&p+WnN~#(c@%|@9~p2=a>yLv'
    'uySgN)_;_SE^5ujVHNge|$o@zHzb+D!GF>R%(uwEuDhbu-~;5+}gZ3cC(P`#P8-Dw0Wez1>fNY)ZP@Ph?Z{^`xsMdEx0'
    '<UuUle(vt&>Z?9|JgF-mT``c!zVjC+p%3tqNXq;ITs5=D4H$v5&6zSjP`(tcU#`-i<+sd-?|lbR*$&*s0#$SwB{|5&o5'
    '16(-7Of1QhvA@9wHx0UQvQwRA9_uqxxcg4a$%GlL!)Dwb+!Alr*Q#ymQ+T3s419acao12yUVqE1uQcn=oAuRZeH~hDDN'
    'wFfpmkscS_k$q-uFQ}#eSS*g5yS}boS~V6HJZ1idWO|q((@|u#ck{<HN!Ea^u7Bm0p>W8X5SHB3E8>UYQ;>BhZ%NzEYD'
    'AO2*|1a<e?{1Wl<y8|*MhE!UqR8T>n5<qf#wLx|5DcPrlmqAdJMj7!|>GCmAt`xqY%WL3t8VeEM0!?A@sjSuyOKE{Xh3'
    '%@o#OkMd|re_Tv1D=xUTeAo6-T=|OAs)qRXw1hIR|?t*dKjiCS%Cfe{+fPx#yRd*cwjZru`4a~hwk_Qj!e|262~~?tyg'
    '{%<-6{tg2v8yO1xuK-)MA%IrX)xf*ca3z77h<v5V$Dl`pHoBh`2U6e2grRdRmt)r-MEDE__2lZ*S)<bp|Z?eGo(eXW=r'
    '#Ih;~6Z-**L{aQd@-t9T?1479euYHExFJnf^z~(b7JV9D=172YvP@|<$0)^8A4jWf1yorkR#}M;o+Xhv7MkPEJ0vs*jl'
    'iW?qi)8;!xd}$!-K;IOWYqDgLkI)j43roACaPFOQF>fC2&)LiedYU@mr=iAxW6EL@P>&Qe@0EXQtPTns@LR5x{i_eP&E'
    'CHToz@On}ssh<laf+(o_XhIYszxuLy%8Bvvd@`iRcSh4T0MY`R>Mx7{XHUu!tN)IeM+rFs*<ow%{@f`Z`sqrX^qd^4ZJ'
    'jUP0OCZqD#7IT)TR6}Qnh~yVTpDOBuzKW6n)Oz)F<%=EZ2~^X`dVxRidXQg0ycq0@QyZ)WY{{3{s1b&!|Q1f=Am6peYF'
    '}_EW=~26}UH4O7wN%G3PmnL?09$Q^`p}^}&%O1A`*BI-Jh@(b!dC9Kt{tCh~Cy_@pwPJH+dcVMp{_xn3Yf1G=lv?Zl}&'
    'Q5#X80&lW8t$+tolBeLiP_I76$H;u)yO8K^UgDW%j`HF4LYlc<>rt4u*5k)z!befe+u*K%lm9J>Nd@-{IG+ts%ye*SaN'
    'EIc0mp(PAEU4~AKYN_xmCvV75j+hA<;|hU6xjIFriF;{z#er0=SFdE`h5CcNyFja96=y1NSSq>krHHH?;H=KYxPtEF0@'
    'S&>@9i70z)B<eXm#PACgx;@x^_nzP(;92gljjcI7{oz8s~4cI7koLBlS+*%$FQm}zVc!tQv`C$ldQ2I5bC^U^+Z{pD{{'
    'XBSB#zIS(`G(V3Hdf+cN^OJ@c1;!@e?8+OjuwfYj?0Q2`}x`on=%IEFr^9%f81UZWdB`ekxyQDFvhaYP5#>+RM$4^Z`z'
    'xc3Ts(IWu}_}5BH^+<L;OXeitVDlj&nxQNbOjHdl#v$02#V@W>a(_VxN8H@+Ys&8%kI1G@z01m0z1rC~LX@U?OV7D_S$'
    '=O}h?cZz;*))Fc1z<Z>WxpX*r=vAPLO62}1sW0f%aIxq*7D1sOA$|bF$%))u%M`bggOD}o6ANMXS!dw{OTmfFn=#O4A-'
    'WN}MF_4X4=0F#!kGr@`)ngxX}Aj$S7Yj3Mj8gw8>e20A2K&?J07!NEsHkZA7+^(&5v?;8%Kt7GE|e&*z@j!lj_o(l4ho'
    'V=>W;9u@c2Tn=vIw%HZ_1rFAyGj;+%;GK}*;u6l4M%G|=zOuZT7d=Ex#!?pqS;ihvOGDYHL0Zcqkt_Z+WptDO$>SGhQu'
    'HnNP!H1nHd`eVMxM(p*Nj*CnRn<$*vBP7902>*^Ir<m`Z+O&{Cob*8dm1A~MskXTSc5RYkFG00e^(~uJt3Z-)V4V@VW{'
    'G188_0+Q70+~yIu-o>H{XzQ&hV(q)U}Vr_&kOB+iM6*%Tk5<f=%*gvBc~WUtdnRwGWb;y!-tyfsdDs?7!Gd3^vMWKOCy'
    '7o5Q*2EANSoQFrIV{B6m{4hKzNE9(trUIKA*9C`_I6y=%KjP&Dy|WuJt8<3!f_Fkn`MVAnK`I5M6-B8XZ|3&kkaG`&;J'
    'PFv#L~%&tabLZN7KO0Fc&nn&YBL)6GzVH%}G0PK9mqETsF2I67J+0K>toGT&T~HNe9GpiR&Jp=`OsMK#mDl0*)j5ZLNJ'
    'mJS1QFxCHp!Hjx|zv8sdwIvt8lAa`4=7pEm@#GYNXV>V`@Bc9xE8-Q3R85^JJCIlvkTI1E4a&`y=GPDh*Dd+s1T(z(e1'
    '3~FKl%|48C+mZ@@Z9i{)1Bc3=UJ~<;rRsdh%yYe`Iu{1m60&>7mjUk8M}{0jdAG=;lpISnk6$B-*%WL%j`thC451(MZX'
    'iK?NU3Q$KPXF>})vO`)+70G@y-mxQ(xvNHv?4FkrMZN;gJwT=Pfn2t0W5Jb%fGYYI<B+eMNjloZ@zZ`AAmaN}VXM%KYb'
    'YVPLY8R@W0Dde;%R=h`>q~dVrFGiEKKnZD4jZ<|7F$e=OLgqxxc|W|NWv6&Si$RJvv`jSFT5f15%e`rlPqfG_1hdc1^T'
    '0svqWU~4|H$}q7=z3JHy%$nMZsW%jlie=CczW!_K*v`>&PL%Z7}=Wi9ZfCKWr-^_~Yoy58H_d{x~}GKMQzyg~=x4!yLZ'
    '+urHB>Kc;3k(KJIzJ&ar>g4=W$+-J%XHTPK`lhYU1v7}sIz8kq6r4Rlxn)}>1J`gWZF~`Zo>s8i>*H0zMfYwjEAI6W|4'
    '^yGB7j(A-=qv$mlKWvQOuZP>e)&YM!Qo|6VO0Jt*Rq_2(T9J6^DHErD5l%N<dm67(s-SnY!e2YiFa5eQBwoZcdKpYC^W'
    'WZj*{WsMKE2DlCO?Y#*eMHD-&2hVDp|Kg^xbo5Q7J%Q32ExEp-Pkyj-UEu*(Mny-os2U0yQqdX0F=TwY4>!pm!L86Lgt'
    'm!%CS)zPv{chhC%V3?<&=gG^&Z;t7*ti`K_c$qFsT|V|23GwQEyw%4;^f?SZf)5O0ic489yc1=jT)9Q+dX#OExgMoxjE'
    'G!5E#IPoC(Nvz_uNIaIPL7vNTRvfBa$d?$~AVbALc?Paj5W^8A#LYd)(nepC9=iHp?6siAmLjkw{1+D$zC*a`z&%tBx*'
    '|<A&*=6k3yrI7|m&s)<NUfc>(ET2^$q+Udp&mJz8)g3&6|<*UBO&Cx2f(nn6R%}^5ZTtc2p$b3PtIeP8|FG7Z|zXW@0n'
    'E{R8z)U_+PTqf6y-tR@tbLo|0hLPoK7<F<D(%|<52#k!_jd6NLb-(ENlZ1&Zs%R6lN~&f4W7t)PvrU=C%efVA-sRlWha'
    'b6iw+W1Qh$Os5FAR+?C)^z*%7AHHBiG|5DD9HBI`Ag3+~y`5yboVJ_Ny_e4rG?XWakszo0W*=rO0|c3;e6Y&;A#adms_'
    '7MYoAVgrhkl&m6FnCFg48P^+XPNA7MDciALuavUNOsNnpQXbae8eDOyqPUVQcqw6Qhvug;WkO$fvZ|g)>swKb@1a=dGs'
    'QaZud$0K68RYU1vhcQ|ANQXurZ?bMwVDuti7PGb+Bzhh=v$tq7_DmOk`*Y`TWyDZlxhNv<<0xEY%JFoa%+nrt8f$n6EA'
    'FisBPu{*;R1GBVSF#_i7E#f9SwUTt<b6#VOWl+g>-xWp}od)LW*cw(IJ=_iL@<VE+7L9G_}ocBXk=)q&#Zg@!f(++o>1'
    'FY89o2nJ23T2vEU%wOgMKl!!JwolpNwused=){2lZ$GLlj?Bq<F4`KqPl4AYRM^925GhAqX+a0cT4K$u(C|0fO{{Arqr'
    'fKJl9mOhi2Lia;0K@GgSghNY`(xZ6)=y73*!HZm|@*sP=_jOpTJ2fvsmpOIr_amQH>DLVgINu;R8@=~R3^cZFiOl|r<c'
    '^>X+W^o~JIW_^>dUDRJzSoKYK<In0BLgc7R;(Ko3&cS1tbF)I^@8fr^-TRlSMK9cTdmk|PaAK$fE-(BbdRP5kC0zXDLM'
    '31-)~m`vD0oS3tC7gOnNJ!`oNkw{_Q6D>pqTAsMW+w$wS}`Ga?Zned8NazC_hBb=upSNc+g1tRR+eElCo7kvrAj8yE%L'
    'wTXLuCS^e5fujIr~i27g}KgkDmQgBJfwZfCekM~L7@9)NWSMNjW#@_pjZjf!kNaspRfE)B;3KPC#Xdr5(!t@n+eDKJsc'
    '=coP(W+gB!+}w0CAQsYR-X%U>Y?K)iI;K8@bOa4H{6r+s>vbBN4Rs>9mplIGOlCzSSi;oT*CQ@J1=I>nuWNOiwk8SSYx'
    '?f?jhbYE3ugh<V@j2Ntsz+2Nh|VBs0gr(B>M<qWPFQ#SU-94o2~f9q&`LH%&D-2MbdiuJuItWH+Wc8I!)w-L$CI-5GO@'
    't1#&s-JSYd&KNmHyz+x0z0BQ)Qy`ej751suUp3--YNbJV)q%@vGF)CG#Ke_X2wWN7*0&%O&)`=oc2v40$?El$Zmh(q7f'
    '1vwL@+6lyJ;#Ah=svQH<+(gGR9>JUSHYA30*)WV}zOKFjK)VPHuoH^%urea1rkWPrV3*TX4nc<b0vP(#rK$7^A~K&8ff'
    'QkU8}iac$`rNEWLpuWK3~^Gll9pN~0jp6t%YRGTMz`j)4e$NM<CrZrsZ>ck7;i-=lJQ8{CDt~B*!%!RVniPG`nmNmi_>'
    'kP3!oPb8IK4@~x4UfSV#3mK^HYCY^2vd(?h^Lan{xHof13Ih2tDofd0yzxly-cOkHFmZy-n2K=lvXsFG3LrG1+|+uZ+6'
    'Dqv2eBITGhBm!~oAhw0?VF&TLVdnZno>SqhY79+p%}_1WlBHC}D%x|&;fk8zpU^4f{5FR9dIYfdVi8XE^QIh?ogo(XE0'
    'j<J*YfjA^5I=D{M)BOo&3v5KJ(4&OR*T@B117W&i$aFe~V*pg}NFet2ve2+OPTL?{BKHFsWz9@1#aY-rwDnYSkoBF2t2'
    'dsyl7rm6iTL7&+N1G$=694Z6+5*4iK&>netdYO4R^nW#P7t7FGD=a6ew-k_p<%-zL(9l?^O^`sBm()?<EvE$wmu22W!d'
    ';{R}X65Hdx-VBQZoGTmot4l9bofm3r_Q9KyoF-1u{KygB7E~pGIP;uV8AK5F3Q9=ByOj9l{z=i7rI4{1m=2Lf~DZ2JUf'
    'Z<#~p)6A}qvj2F9DW=Sx5m!XsTJ;rp@^a3Ne6PPEc!#nf<p{@VzmL4!4AS~f3^20S*~={p`}|SFga(qUmgDlF9<Fi4dE'
    '3pN0KNBkD@pX5W=S@?t|TVOc3FfjXI=wC7qbVNB#?Bg*hRC?vr`9Zq!wplWdwxisB6Xu;6sFE1hn}O>f!O+CwIo!LA9W'
    '<qlp%wujs<%qQ8PqR?FH9+=7n0lPu?<7HB~;GrmPPYioj#~SEW!jEmxeM`(+X42iZwo<_acxOqyqIfOyl<mVGVq3P5wh'
    '_sKT$0`W!|4=vjzBIRqHuDZ$kyTPm&w{giL+&|EuKzlAKNoz1LJWnzQ?uSBPH8Nm7<z#f>0KX3eW}B3~vAI5q3nADQRU'
    '5uCkj8wuTq9*;?b1R4~)_<hv1rfbcGBZ<DD%ZWLo@P@uWXP9*xXdfYcdWumm(#A+lF^MXhUHjsI@Fi9kt^;_XJ_D&EYF'
    'lon5GurLlYSsrmZL>;73KHT{CJ6Fq)S%ufNTbOjK$#`=6FQ4-SP8S}Rj)hI?krifar8<skh3@+Jaif8^y58OOT8i@+2C'
    'Ru8{}G?spQK-J&<Fzz0tj)va~Hq1{LaqB~HE^4f~n2XtU2IjWR)7Y>2f;Ex5vpR!595@~SPVPlO>Ye6qZSdsDEKy!cCw'
    'b_U@nHXct%&r~YqC5e!xqbGb%n_BGTpfTHUG!JM_YGh|*Mq)Xax=}+Z5N91b7A-K2R@+97cZ1;)&h=45jYl1>V9EJL0o'
    'syt6Y(qM;=+^UZ0GQ9WHGB9IkpXLLQ76aK$77}O^T8sxTG%?{EWni3gKignuA0pt^<(ntQ1KkmQr&NPp;AAn_*ND+8i&'
    'u*-UMS=4(wFh8<-Y`h#=0uy8zTQHJZA67fEGnc35kz)zN%{U<=~VRUepFSl2_8)5~IFGP!c>`2F8ypmE;f}i&iXMuaeC'
    '5v<~E=@wHOP`CO6vcQr%p%`O%#jZ!(qezkPQ{Wked%=SbOs@6C9Q-+HXQuph28n!Q5LF~B}(*liAEI~7SiG3NW;nA6;t'
    'D#fl9LaK}_5_<ArbZ<h5ubuZ2w5peL_IV@)~aG+uZeQMIjR7h<^oSyIn-j~6_@B*tidETsAIh1M`8A6q9{2NXwWL<5!K'
    'Jd<kD$rME97_8WS!gbwda1^l|6|UG}Q*1wFua^1lM@#d5mMCXZsba^8@Hjc^6P2de?gry7t_*yj_YK{TgG3nN+Qk!4M~'
    ')n^jB(B^b(t7V34t-#>Tw~mSiTaX<fpszM%lpCKBoO{Bdgfs#Ru7`RGHM_TbaO@+OI0(eJgn%E7~RH?R<m!u%uRU-}Hw'
    'QO_Uy2mznlUi!P&;QZBd;F;;pSfg2|)!ZQj+?!fFhGtpG)n5L1H>~MI=ZjU_AK2v;+y<r!W;1vj7=fTSpyv{h@nmtF9c'
    '}z3#0eB>WCIrqtI(YBf+OOkFq<A;Hpc{wAopx40Z(CCvjYG5l`TL*0|M~l$zyJCB-}%$EcV~h-xidcCjPU##xDDX8fvW'
    '&_9-IKK4jj|Loe2Ur1l$;K)4`>HTMX`HaBqP72;5e1`@x+8R}JnCI3~cI@dp<SZUDGZ;GPE;4=xMbDsUfz+YRmvxSQal'
    'f$mHIxPIV<gBu6V04@_;F}U^MJ_lz5_Y1gN;M_X`54i5&`hyz|ZaTOWa4fiDaO=T+0&W|)GH{jP1aOVu26b{L<ub3m2='
    'myKjBJ3Zxw$#Hqf}`bS*fbDoZLmptSTc<m7T+?^0>u|b8^|#6dw#fE}2bMC1)pRy_A=cH_F?4W}lQkeUkiWX(#GLSQ|y'
    '-o1dKh46DjbO-@mz<>o9R@%vyz1OJ0RA46`={M5X>jO+y@!cibaUXl;<SeiMDQ?teFJkuxd86tb`Jcjvaxt1j_O;x4CQ'
    ');eH3q2z{k4sC-n4giF&8ilqF3QP$NtL`bIU_51o|u9ENdECTxhjY<6_OFlIwL1rwKyv^IWJYUG$SvA9i>WV*~O!tdv4'
    'jXWrNsUE_Glsmy(e)XnxKjNZi|dXqalCDlIvW9iW=Wv8pU63{~>{`CM)?n~HQK=Vj$AgPKZ#l=9d%REF^$xG2^;C6!H`'
    '4|&T;Nma#0&I}tgXlPi{098tAS~8c#sz&;VHTfUqGiNd8EN_(0e-sPIo9@k~Lvu_|&06e(@-OvXnwmQ=2Z{^j7xR?joy'
    'RR&lnfO$KPP*B7MF*G3dO6+ONG{urebrG=P%3?!w&Q&RiA>5nY2st8vMuY5~^nrl=y#~;r~>QxV+>A@Kz*qW5<z7p6L?'
    'oIa(u0s(z$620`P`?~lLFLaLbX*c>h^g}nDmxa6#iw3k$Dda5cfhs&LxN=gyyLF6TJPH(|gSvffi^Hf<G3sY5N$Bm0pE'
    'z4ljRf}_T<|WU|$jV?}QsJk<=TCWJ4EhOw&g1&~PfIp76<SA%Dkm3ScU9h^95y{0UY=2^KG2HBXXL`0@>CL(OKQva0E!'
    '_yCp#-6J2g2MWduA~wKyH#UNLY?^74#D+#;HfEXeG<94<Qrn`T;W>JlzBd;UupQWd>I{HBsuF*OCgFsZpvb5JS+=fi9L'
    'w7~xqHa+9t4-4`BD@mRf_rD51Jv|lPK=HkLvL^GMQkCkK`hPm#pR^hm+rQB^TjL${-|_t#p8*}6YHZ8r<iFv)cD<8Oig'
    '{w57Qd96u^^pQ^_%~!YDievP}P*I)CH<Bskzw;l5<noL8_>%EENfo2OU;kYVOk1ltDgE;fu;jT@EifG~RKUP$n4*|Ae)'
    'T3}eTE|9oii(0y=FeAz5CaVT@KbI8dZpn{e_KBQrTMh+U{dJ$KZy83yo2&)M+t_swt$)AM6yV^AJ*Q`BCX18@Te0Q5M+'
    'O3zzsuYTcY3uvVTD$*VwN(uNyXe3ETitu_;@@X9T&-2atF%WoE7LyOP%rwc5_My9a`nqISUNIbm_>Pzt<=;(DOp)0h!d'
    '5$yrY=T;9PiF=P2e-=T_XOOBCbZMZ`VvzZ=6S8Hp$3>8_OO^?DEd8y3bGNSggd8fJLOm=R%N2?j5~Rk)nP$mXWcg+GQ_'
    'ym&eM$sh#d#mHnbv~?_F5M}x?*$l^I!M|roTK%%QtSop;4GjwmYyH&O3f&ASjM3?w5H&H6W-TXod}=bw<)#|2Mze8`ag'
    'u)8Wc>^3A+CIUKRk-rHM})n2uFmA_@j|gj3+#!e-Xgd;g5&AJW9l$1(-ET4EKEm#23*T&ZvuGigm5{SMdBTxLgj}&!SW'
    'n8AGWU#t**{FHDJz6@3t8Vkb_X&<173O^Jz`IJwnl+VHR;BmH~^s<><%YN^;97$jw|8SuWZAj7o9A|Hkuf#L9TkeZ#tE'
    'l5}8El!?KMz8QuS(ed;kqod}`G)@~9}WZloNpNNErz0pL0T>gpCDBlmpz{h-sUG~Ws!gwZs?!JKPM+U6<X<YFc1TvVlj'
    '&?@edAvJl|vl3%yld4&W)NOEc!T2}{c;b@^iPlZ_?Qwp=mHh$q4<f`J`;UCGcVD+hYtC&K@Exlkq?FNwjSZb>hmym)a|'
    '#(Z%A3mu?}bP{=(7=D_k(FOsTVN-J#!Pk_9F)z$4vSDBfl5u%hh|7{+8suXTN57C=HW@}N$lWIHXYyRr5Qr#w5s~T10r'
    'VhO`G<x7X+Bc3q2_Y37r}c2p)u^pKMk9lyMV)hc`fk{!LVsrIglL8JQn9<z*qoT!6zj(MJ$+@jQP1ac{ypUDn5hcZft0'
    '1OF6udF)tVTQZkOgwobkESPeaWa9Xn<H+j)0cy*XTg9b4Xi$xDrb~1$GvKMCOEXy9vw7hq(M@KPlMn^qm)+>IOjkeq3_'
    'wi*B8pnyy=0*LFh~HV=>l7x??{0Sy+J5~_<ojBLwq+DXQ79LouJV?Me?s9B5gN53Wc)>_yWkM<uS94ppm4GXbt;PAxhc'
    'XQMaaBM;i8)~eF{6$aJOnie7^{d?~0IFLj9vD-jm|@1ra|jLfe-Ve^rFWWC|mwe-MRrH$*v4Q1}&vt3_yAK%t)chlr4o'
    'P<Z9K7_VJ~x{VYs5+O5Bgtjph5217q8ve$wB7RVW%$Fjxy+-j|5$fV79!_C5>hDJJ>(@lSLlk}^LfxApWd1_^=Tdxv2#'
    'o_N9zfy4t0Lc(t5hzEe|weI8^wz#J%jpBqfkR(2!(z$T>TZ1uUdr6Aquxq|BpmyTSf6a3TIO|k;0)gTn~!-(s&v!i|{I'
    'i$3>|7L4>wXDZZZ4izv*da3-Zk)9}M6-kZ|fQRo!MMa*v^)SaW_q=VFdC&m9x>2Fe4Oko~{^JuuKlrM_nVU(_-{>tjd#'
    '$!+aJC$7b=oM{4ebxA;k0K%&X2sw>*Wc2f3y4qg*XjX(<L!A}HfqlfKlAgfH(~(qvBB+!ziStt3ukA)3*n8k=Ko{zms;'
    '<$hxWcv^gQ5dBY$(N_Sde@t$(}IWC;J+yxPzHp&j>P;Go;>!T@ialAgL<`;GQ9%bjAxlhk`U@6xVpmi+ee+>l5{XZv|q'
    '*$>(oeN)E>J0N}I`OCSv`?Rw@R`>pF5X5iufBEM%Hm#@owst>`9R|31)Y|iQ?F;OpFV-YNc-=S8tOzaDe%mYc(Z(;lAb'
    '$3R_@xK5i`Tv%^0!iuf9|@JXxoq4^x<;~&-~aA@a4BZ@haCgUhkH8^OY%(Ug?9x7b>*Np8t00=~-hT{x37*UOT8&{vLm'
    'FRq)VAraDv-cKnbw^>yn-^@u^h|8<8Uen+&wUE#mo6F3R*vuE#5JgR+m;Kuz4-(&s;u0Ciwro9v7ck*8Ualrr9)p2`{Y'
    'e!5^>v#Vj696CXeP;Oyt$Sj7S^Xa)BbjyI^*Pr5r1sa{dPQ&vlyB|TWxijY)NU%2`YPYnMlxGZt=l~Ils4&1jljJRbi-'
    'f2N;r2)J9_`>-uH&ykAOk)z8OnSYcJMa9doiHl-Jfd<BC1cXcsj-oV4-%@sZ4lpp9qVJEPrv#654sQ7o@^S7r|UNjquu'
    'Dn*KE6y*Qq1y`*<X<sb3899ra2zcK5!l`Gq2NsO#)o(S_r}Fk2&s;gH4PRI2A2MYy<nQFl$|XN*&t;z;{LSh<k<8*}7x'
    'GM{c4^h2H9I{zLV0YSG3vES?WHAy5}a?VK;DdxPIj%*E-0(NHu?vs59PiuW^Sp{p8YP_=e<>kAD(kc{fqXjWk3}-1L|Y'
    '$!PlPK_lwqa<jT6i%OJleMn97peNH>K$4B1Je-HUnF841yaZal`?iaj&bzi`<ch5~cuWc?m{MO!WP=8^s^Bpgr*IwzI<'
    'gw|i=YZZ@_3nZTS}7;IW%w-!>euOa<<<r5=c|7kkaY#Z*ADy2vE-um@|&+!dv|n$@)>vfAN3ct4+1(4ocRwO<d6Mm=!#'
    '2P)$3J7i?gvkuK6hI(IxGN8Lte|FL)5a)V@^snYLQ{J3r+{``OP#GX17oSYx&JoX@f4nl&0Iuj7V}_G<0sY3&@-IzfFH'
    'zwIk?yR4mY%4_w(O;EnV^B0cjE^F<l3OoODs7E9d%|^~QUe-pfsP^g<*DI2F=e6uH*2~(sIUZTg%n^~yhJPg8eso#8q|'
    '&YA;uvUON>xRA#1-v|&CkBh${~H@iyL~cxT5`gz;|Icx4r=R85aEE+Fz`(?@XC;Pda`tv-RrNo`JtujcdL;_gCk*y-e8'
    'V)z{vuvfg_X$^4w{zn4*6N(k>+WnHQ5YRe274Rq=1UY}K3O_i48sy!3;GPbGK?66AfTOQHbeJk{PnRP2YM(zCBsvj}#Z'
    'w+<B_A(oqM*KbMXRBXO?ceJ9==L(Y&u_eY_^h=*^XX47hDPmWPQ(iLrk}Nbn*013rX14OoqpYU=_l)(yVsqbJY5U%EmO'
    'a{@F#2TjopK7-NN@Wj4A1F^=GW(zn*kz(cO{2Kep#w(;4gFmxjET_(Am^M(4MBm-39Yzf|ZKkr@c#J+GNQJZ+7a+Q)WW'
    '3GpxfzA<pXY3txOx>aAv_S?(UzAg*ieabrI?~z$vZ;pWcZTRZW#8cKOZ++kFy}Up0uglU^owN?IeLDK*@sPi*Q%(=dIB'
    'ET)*fO+iYEMXCJIedv3G3#u-prR@hC=+mteLUqgmvSk<x>Wfj|Kj^HNCo>utxpbp&~aU0?K1a;pcOXTkX?+R4omRgz}5'
    '@E_~&f)z;*^`}dsTkpD%^hA)p=y;W;}C{BU+jH88jP9L#`SG;lU=sl0UO!drevziWDM}9xXe_io2fSVp}=zG|@Bcn&ny'
    'EC7M_<wluw<(9LJ<8@8v)=}JjpMH93l3V{kH#&S9}MNcA=>+^&nm2OFWyPnZGHjd>-CB1r*doLX4#2xnUEi5rG2}!-1_'
    '_3_(jH1P(QluAIw#kS%d9`o%;Nt2KqwhSMz_gmiAijH9{5x?Zf%<<~I*m>poiC;jeq4eQbEP&-SuX>n!VAFK<0H2IM=H'
    'x%~cqYwzLF`&UiX>}BS%?so_7w?-xz_Fs(-1Nk)5*CgAmC%bOnbap4m%k=&D*SBoe`s9JV-?=ym=<&0BKd@Tk=WX6$8V'
    'c#@et%=VY@hX&l|MC^;~+oGj9+&z-(!8-{;H(gFlcYO`Um{2-PUpbH@`UC0n0aI;&-!mSttDb?spHKf%-LWi2Cc99oD$'
    'd8}>B*J{kD``gz3U?bbu1yGu@eJ{agXPfaM>X5BGmR=WdZ9nhXWULQK<AJ(9&3r0=s3i;8!c%0kwt@UlwM;*Q^0C|k(8'
    '@G(wYV9}uo0^`3p}iT`*1eYfm32?j(y0;8Li=RWf8Lk#xi#eH{Bvo+Sf4N3A1(jXIwba+JF7<w1$tbsK`TG89$oa~U&o'
    'Ju_GTQudE(0(t&?Ie&GG9B?UhmeKCJ79)|fln2JYyA?P<l}f-!Gf|GHH&=?`WC;7Y$!^Ix}WuTDwZCBXa4)V|PTz^m50'
    'UllWkPK5NB<%<q@FSCZ!9F5;SAIq2d)A@nUDb53Q%@jVOu!+Kl6h5HvK81}GHc(hk;XMlLD7;JI9SZ-Tu$IEx6#h=(Zx'
    'r65&_Ur%3Tr47D7-=8bqar_@EV0zDZE1AWeTe)yhPze3NKK2p2Bk!{z73Dg_RWkOyOAyf1>aVg{SGf>lB42DLg^paSD%'
    '7c$C5;6dtDV5QPURte~)*!ZHefr0@WRr4;U`&`#%lO0oWJ3OY}uP)8v{q0OJtDb!KOP-yd`bP9D8G8Ee6lun_JLWV+{F'
    'QrqcqmZG{=0oWe>L_F=w0ToHg*plu3T-k<r%*>BL!r%!(kawY$WUnWq;v{(6fzXrJSd$)9fb^qHYuf3sH2df&?cdD3Uw'
    '4R6x!S=okAUj423o~N~cgqAw!|fwGQBV-&@veYyZXkz?lE6p{F)3Ijr8>yJsX$CPLLiwcYB5RIi!p9f`BTzwOg%M`+{;'
    'dk(^Qh?%=-#Adzrm2aL&X8Mdp`r(*`leB01f3&B^Je*&Ibv*cvLEB&U)qq<oU_8qxQ{3}owfiQ0HvO4PlQ901)8@o$Zw'
    '1VI+guCdBWCgJ-D489tIs@WuG9C$`252A&eE=|yY%Ye$>B(^KIT0~d+96vydg<ZIKOc0t)Ht6ZolKFwB9g&WvU<iEq$J'
    'L%8+%PqUWjs-|(U3m-*V%A#d+UZpQIz?U`3zNYxJ6_p;S}8qROt*~srp(?->+yIAJHco=HwgRpe%%sr+d8L2QHWwt)r@'
    'o|RsV({SH4IU`(hSx3yEYuboHx(b-G!W(6(f-ve?VfA*kF1&r<9X)9OH=PJ(vJSNdw#-5lyB8f?=Q^J?jPNL$%XH5est'
    '#7#_Yw~6)LwyfAPcg*{`4Nw?wO4G~ixr^EfOI{oKn-w6Cu(NbMoP@;dX`2k+%-Hxv|(N~^;8nc>34*gUQE<fWhbzJ~Fi'
    'xYXB=)eh)e``*AX3oqAQ0A;z6`^aQ81&;u4h_mr5W=f34z^I;s)z6XMwfZQ~GZ*8xI4%pk(OFbjnyo^fXZ8&OH3=lR5k'
    '335*L|*eaOM+*Y65SJwspP?26cxcfugH|d6e>!MNn?lGr)WycOeps114eOi$sF03mi)B&xqXT^nJA0{7wziE_WwSX7&t'
    '@G9MWYys!ePcMY^Jx5`p<+3=s~6}W2*%<kF?g~mU&0@!;AKz^>y@)yYqZ0M<*CdnGhK~1oUQnX=^m?o@ssR=IH>hh6iH'
    'TsGrv|b!LR2+S;t#4R$z<RdU9+PjIx13?@^K$hx%zHYi=X~$zJe=2K>-%OhIn$!_{mDSDW>SV^4xRaglHdA$j*?ZBxy3'
    'X8Zghd6+wKeYM%&TtgVW%_=B6P+A<>MckzKQZ4e<W?3Qj^cs@HT{jIUqIG=i0375_I(5rz&i7U&wsrfxf?52Iu5@FDo*'
    'A?U5fFoFY!oWge6hSAH~7*W6y`=DyvZ~x_<8r-KjRLh3si%$NlBZwM%3X;z9%CA_MK{`@}HT*S)EB#KbsADWjrDOjAh8'
    'Ay!i-u}JwdH8LLxcw>gJa@7duV;2dF{S4+#@kk@Kq2z=aPi7f2l@D$><(RDIPI!r+IP)SniWL82Lvo(E!h9k3qY|Z+Rm'
    'mA*0T?LoFDVlx31&7{LPH)9>*3S!$wa7e<dO0l#uK<E1JjN^TkFknul2QI1yOcdh5vAp@wZ;~E!HvMT_OtKM>1Eri|c1'
    'Ro0QvyOHzM4;dniW&Y4^vorxuy1VSHUe*^oz^XrhEdXa$%3^L^|iaV3qs1VZHS;cq3?c$PNZ5$rly&DmSWj^VO$z+w%z'
    '~oa!%O<IRQDmZS?3l1}c=!9X_8kkGBblRSyiR<P!Y>)6#!)Fx~!hwz|2;%?E@Tc0dn5*EAY$)JFNlwO3Yq{Kb*PZNd=4'
    'S?rjvGnBcQ=blHiF=A%V>pFNSST+!(^DH}BTpvBC=f;YCcCn6_vZ!djpB{Hx7=@nAOftF@R%pZU0hnAT|D=c(h-A}OWw'
    'Q3m#R-B7`j=EV1?kptew?1Hv;@brWxhGj_8~M~Xj(_9wwV0K@%BvAhl>)iv2~$@xtRWGIk(=z*s+=*fjL2ZqsDr)Qu-b'
    '|wc=wuf(zDvcH)AY<YS4#k;*(tl^JDJF&>Dz(DZfP--wzpn?<VpO|BzjKZ=bO>MIz`P3j7m!usa7gi-B52FH`TLI*S#t'
    'nBc~tEzny)8|ZhKyS>IW9%pN5Snf!hiB=6o`^I72n8JYk01K6v+fcL?a440%0qxZOc5Zpt9l!>CYCmDO3T?z-Ku=V7hC'
    '6lf>mV*Naf#d!V1A*Pd&fAkW$<FN0whBz&$E&3dt+yY|*YJD1NZhS{Qk&8=PbRaNVo|%3F;94EAnaZBk29Fg!tM;tUUW'
    '9q%9X>6o%HXvi3`5qs`>-6uB;EPd~l&!`my&sdPRXsVWO+a0WSA)@Sy#4|#3nQnl)D~?<FY@J;U#9>DPe)?2^YEhNdD_'
    'hMV(Pp`f)PD@kS&6q`3mC!|kP9K++v~Dc)2z|MDh&;@Mef!AfVDcl+#Kav(E7HfHu+JPvk}A3!^RPQ{9OuCml$n?Zto_'
    'EMq<f2-s{3?$OSwYFlYU77Kt8$jJ*U^OuzSTTB`{^CVct^&RN^haV@eX?^6#z@h@HV*W5>1!4MCWihjq-hqPweJNnUuc'
    '`Iy~RnE(-hwo|p`L{J1SK9zE$e&7WnuywP{LP&LS^DDwO}lpc6|+kLFdO)-_MS{)$xF+a12*AFaZZHWY0V@uTu6<~2!0'
    'qfjkrngc=hd@t=Bd^w#fLzIX|?;9X+>e*n*&4{e%%d^^Fn@rmK`7PVv`(`lL@t1wn8Wq^QYh-X*eR^^`t}C)f<u@i}1d'
    'sJXA<7^c0JY&1<P(-sHzz8GErFLSj$nxQt_=~VZp_gl@x%f|Y0iiuH8t<X36YQZgz=BS*@V7TT43bN31tJu|&0vX?mat'
    'a`y36#^=o0O+}bO{hfpyH*5zToG9I6Qg1TN(vkWIj8gWVz6fd4|XKt40qHK4G@+@%{4CvqZE6->vo^ad&eFukP;XeQF{'
    'xGyr%iyLafW6mY<7%P29PGW$sPVrw>;B#mut>qIEn^u&Oc&oLfTfBHI>o}0sAb0>Zy4b$WL>i;uIr>H2#Y5l$Ozi_EVl'
    'm4lj;&tsPnUo@Zpq?8g8<KM~k`h8nEHmNuFi(M&QgXUgNfS6v+D~a3a9}GvZTh$ghtUa9CQ>CYnkv-!B<{}T!2=sgkj<'
    'ySylCM<454dPRaXN8=^*?iOFzpW(nY1H{^uM@rjn0W8&e!!GT}g~E-@j1$D-rO{13^^8lK*M&_lCkg;&__zswZ1ZOacT'
    'mqx}W^Vc2^1>7Joahxf{*#+#0YP)Zo;E3XxuxECJSM8pk_FM>g2X<9Qc&@unbI)eYRD$rmqp;GCQ!6tHfX-oK&&WzAH2'
    'i4l2%&FdVCNqJZKYVjM$gIo_Y5JAG!JE+C2stMV{hS%NhueE%wV$C#YKNX-6otk`!cC^2L@KT^FDhxQ~)9$N@Zf_{;h('
    'rguOO*IvUK$h$QhaPePj8V`A!V&?;I8`Alpy#?zRu=P$Mf)~F`wv1Hk>i?shpPo9^VkAJRzMmyMyU8mN%&I`jJ$8PY&Y'
    '#{MmQIW&Na!D(A{jL)~@CRRP`Z*|Tl9}`~htw6@)3BIN#`+8y+k@7d^zeObe-n6d2}KsgZ(-CC!l5MBrpDL71$=cN`It'
    'Tc$AD@`QH%#-`K5!3)Txx21~Yix-on;K8fAFPMPqh9mh{)lav=l|MEHbCJGV{mDVD`(-<Z?;sjr<%YSw1AhHClE^5@4p'
    '+CJufm-ER^oO%)6%J{ArKw$)B|JbqjaGd?C>9nWxMotl*gj|GR6@Y$ji(G-(7Y~IhWaKsLxVM11$a+dnU((MaIZ$HP#<'
    'fI4=by2c^pohgZIJ5NRycF-`f&1v+%Bvi0`8VGW<IFi4RwEwLKu+dOQ4$Lfm~?}^C~SCZR3rZo<SXuE8cl>;uD>F9WzW'
    'u=BF1_&6JpauF*1tUtC=GVONZh(1K1SvS*{Ng;z{)>{7!%J-zh(FP8|D&c>d)p84*Qj5&;P+LHDCjzBvk9Nniwd%@N9$'
    'k|(&<|(Cs-2~$y-?p|*_>+)KhCap&kzNB#9ccO+Jx47Gg2!zEqrT*4+gDgN?Vk23npW!fYAQ(eVRQNG!Ic;mpacSR_Hj'
    '?Sg|_QWreg}ntm?HqdNa+JEM?YxPx=N>)?&IeBrMuC-Hgd9L?;mbPr$|eKM$CZwMW|IK`0o0NRakeL6{CZ$<9WRIEUp+'
    '7*?Abe7{0X$O!#;2lQBUWrHfw^m8I3MPGx>zq%n%dgN)yBy8zDX)?)^#BIOhn5|drI^<anxQI1IJIvxBSJPd$<_));JR'
    '07tLBcUAWB_}TuRUUsnRInFSQl5xQKDWEY!HxgWSHes$lJe^-I1o*v#u~W+xB*|^H-Ads`TBM4VDky?-zq40Xo>21K6A'
    'y1*I}Ilq2(YypH$0Hoo3ZUW95Vm&JIvGVNp}qDJp$K|8T(TMCENg0%6q0yAA(P<wS=dMlyJ;!s?xM-NptjsmlBPQN1`@'
    'bC-G2H5P79j4${N0C6^q@YXWlvH2$N|G-HZJB#m0e*(&>Z3+)0V5^Z0d@_Pbp7!K(HICbrMyHbj(X&;J}9Jyyh-RU^g1'
    '|h>J9_K;l`=5XT)0p6HZ^UdhvDd=ebQV8Q&Sgl0Zg9HoQ+$Lk9|~`RJKk(j8T)Rx_XAzPJ9G-$vtPxsH}Q#qcImjBl9{'
    'fXOj6A^V;c|NS5tv&uR^sa8PueTkZ|R!w5OA(rO21Ce(_fCYD7rwiU-rVmcVn7jNCiP5QCS|0Fgy{n0lgyn$XUtJ>n$!'
    '@>{?0HF|qTKlQ+P8Ae3t^_J5UJh1$&?1j$Xrm>VnF-K$yY9<5p-p7-{Q9wx2rL{>s$7mj0WQ_GbM7*nDI`b(uqC>vB#T'
    '^XL-7njl^q~K9AfHGa++A39%?VZuTtUXR|b|awlBcMZ5qisnOyNnRGe}%W2n8bvn5QlLrTmsngenHWK9vSq;JbBX@8|M'
    'WqdC;EcvbNTpiJ3wJ13qqW~cDCXJxi#a&Ml@2yp<%F4a0+N%voVBh0N6&?1@{eDvbx5k$v-u84M{<&-Qz{^30C?XDLo4'
    '|u-ltyVE$cEjAUk7QNpvj&R5(&yYZo@weWvlpnr2{j8=~O!?6dDQ&k|N{NzL{BMmdNFX+1I89_3>V*s?O<J3oe!`VO<V'
    '2gPS(a%j2?g*O=@{};gAjWDj#=Hk3AslwgQmG8fV?#$E$FR`N}iT=oh%x=!LQX0`3Oe)`j3*<C?Gt0P$g90v`f0t9BQQ'
    ')M`6sMx_!pXCU_q_k877Jwmzl14K{Y?WYtETGJ>5PhRp6!`fWRf_8jKR>8%E;HTnf%fq!W`*o_zVT$Gu)DZ_1Tjk4Yzj'
    'SZ7ztV9Pa0II6fXh;_;cw*!UA6E|HxiSt~jyd-1g8@-?IzE#M4EeOrE@^78qQlRem79boA;qD7#(9pF{+F{3lgPR%43w'
    'Ty?2rvcv?@dr?uxUAM|ShG2l-Y|@GUHA31RY{X{CXi6ZtjkDxO|97#uaUtcZ_4L!%hVjot55|m!R@v&MRdEYJDUD;_In'
    '(PtU;CUtO>X|AL6X<u+X+PYI8xR3{E)w4^BEhVH1eY!68y!ab1w<XLcOAFAAW7n}B;xY1&(nO#~NK3|ii58$lNl_2k!!'
    '04LbDQe#3?bdvk-IQAC7fIaVry$bq=0E~BwD~X0u??PnTar@9>j_8`#dI`3299~z^<&wV+#hcHC%E&aRr0+YnzA-dB7l'
    '2N3GO@}#qOE6&GZ~~s5dzy0IUxKQUbuIjT)TAV+~gfDR^TzYk?rYhMp~cjL%Pa7r^kvOa+U6((gGs##Q-=sv-r+ak{tR'
    '<mS*cC5ipdbe@|bPY+!~C>!bH|g1sJ!76d)86P8ltr^@zCZ6^eeszc9v+G1pl-W1H}x&`StN%B<uuO0=p%g#x%%)#_-D'
    'U(fAPQuLmn~q~2THoo9?@}_gv;;>;5#=Nqb~Pw0H<9y>`^q%o&i1*<YG82@#iUszJ^($0gLDi#CMV%g?`)oVH2wc#!B}'
    '+@A*B;S#%_0!VOu|%wf(TBX6q_C9VM)KP#^&55|T_Zo95vYk~C4&3r;^W)7P%|cd#EBO5@LLLDt_CO%lG#6f^lFqit<x'
    '0&l7)xYg>%Zdpk`&_EZy`5pU7D(=oqXycSF@1w)9B*A}x%@Bd>m0i~O6=KYGDlyjGm8Qpgsz76RWOawM!)nrM+rFB+p$'
    'LHay>Y+KsQu<i>aSZl6ZFuoNT23CCziaEv64W3XxmhbP0yC{?ldBCVkFU`*~#Zu3m@DD=~W(uur6^Ze5TF`O+qZGVX{S'
    '>9%uWWuW=|+i@C7oxx4osc}hl#)YcfW6hCJOOTbk<x5{<Rx$A)#l&>J?K?9M4?1<#AJZMIZZnQl%^A0Wbc+g;^gnyP$R'
    '4Q61x=%%SOVRnZSdsye(g1#HB*k;t5Nvx=?$fs<<D#Bsa&9(xnrSlC7NJz<UxSXtuaiqe{NnLh_R({$w^4SPyU_P-Awj'
    '<40~DsmWI1p^baAl~Aqf+otaFOsN*^LkX`XdqI?`j!$e8-v)fmD66s=HxSpp=h5Y%->-xs{`GTRm{PM3ef@+7BB@+l$V'
    'S|Q7Lb{oQ10EpOWar2U!d``ec`-S$PrN73Sd?US+v@4;pKC)GO(Tazkmc)^eLO_EvYgpAmSVz~X3K1T(LX{;n_bmGS`!'
    'dAL3q=@RHy+$w>S=yS%__edO!uStw?o!fkk8G$-f2=58-^=uOj$c5SuiU3J<zp_r_RZe4=EWRCe_gk5jgNfzj&F6cO0Z'
    '&`?pY#?S`UW#jm57o7tzP29Ml?zd<)@$yZ8`Tc%g~b}3J0V9hAuO5*t;aO_7UMj`8pxm@z#iJpqFT0ZnXm&b;Io?I%ML'
    '*-Lth>)-f10C!jUJ$`lM6}^BV|+`I4pA(*<p7!sCT6_e0(Hp@DGv2#IeP#{QipsZaWLag&7Q?nre(nn2yc0EOkTtc(;D'
    'm*Xqj*gj5SJDdC~VO^?#>Spj0fX`gVc_k0W8}+eStu(uOqTps8&<F*gjq`}*C9hHJfS^|`7Aa_h;P@Cbdy>ch9#K@|g<'
    '3emNU06`7z!7(M19DK!qpNH=jC?ftun3vr?m9+m~@APY4iA-YI{+_Q*HNYa*FcE!aAezY{7a^nroT!r3<F_@m`mWW0`J'
    'J(vn7C~DAxM6qfo>6IiQ;pX4=@7m$s5w<3BV-pJff2Xx&f8%+pu*bWvFzm0VQy^JE*$ADrQ2Isd6D&NewI)jW`j<o!d+'
    '_oP=QG5DlBg$W2XxQUq76>XW9bE~Mz=uM4xm5xk422QmW{YzyVuYFGE}JnO6o(u<8yh#~!RbPdSkEnRrNj*6>klqp!0;'
    'e&Ay@?wNUu$J6~Hhtfcv$VsF0rh9j9vXsds2}T}MQh`h_+RvJz1OFe@WeKHwHL1-RWm59?*Aam4cx?Ka**8JQF;`{FJX'
    'rk94&<rt0B<4>9bZ!aiuXbcT(>LJSTT092I%$@L7z&^!k#6V1(lCjwly;6YG&`j7E;uX?-IHg9$=}>2_f&^@OfUsGw`1'
    'x@=P*;G?j%uz4%5OE-;wg$a(zPR!>1bV^&(>0d*_s+h;Mtmrc9g+VwQ@TK;o>wAQUiXD=RPp05vB>pX2nz3Sffy`}VQ='
    'k-}r&bNx(S-qUUL~I4bPY2av;#+a+%ev;8dZ7^K4a*<n5S5acBuKw+QKzNjR-xYchw<UtlWAsIs67*B4bj5b6hw(z(FX'
    'ztZb_psl-XIX9xtoB~9h~sF#69(lyMX)nkbID|=pz*}J$FXWmB4sfIS{3jPna(ZZ^{OOhD(Clm&=J`IdL$TXJFMk$G|#'
    '-bKacq(}WN8vybFt7%gAZH`Nx_!wKauV|S!=>$7*7Tm5RvWs+^qpbu!^6+!OEV~LE~Mom!N}=x6nx!U#<v{gOOm?4#aF'
    '|uWXD!V4h4(hO?!uRu6|4q(ANPgBxbhGM1c^K3`KXbeFA5wH}6OY*#eI?zxx5)ls3-`cM;$N65c^~-P4hlgC4QcJbbJx'
    'ZLaz{ExE_WTEkb$b!Sb_*jFjyOKFkKuwqSWkVMz{hsOlP`7p>;`LFc?M@?5_hlo}4EXCo?tsuDrkwSMmXB1j3=f{kQbY'
    'ZV4@9N*=qNJx!XZ;9r1}n}|sHvQue?t-!ghhY7es{YHCf&_!njHCl&T)Tnsl#m1js9-29$GMG?SUc_ykupRqaFIAvl^d'
    '9Xc!rh>FYt209aj0N{~d&l5U<W4Q)<%6ez?0cO@jQG){XudLH*OV*Ncp{;Crm?o4nR*jk}Cl*$!uu%p~MFNhu%N<ztZ8'
    '~h9p*=fG^p6P#KVZN@7yI{j?b*C@_r(sl;MaLx;R)ZgtD}+zM;*R6t+Di!o@2;Mi@e8w6CHqWB+L6tftr35icDuk+F__'
    '5S&7F$0rm`ns<a^<>qE$gF<Ao&h4xX*#^<zmw4M*UPC{QVq_FYD9ufUaK0;v`gySKxtqmXRv5dOjcBpd)`<t;tf#j3?a'
    'C_opwJ|=#7l)uk9U>2EX|ED+tA>-hREFE5;M)8yN3LOMS1zgzq#VTjNgWkrUEQRFqCU?Bobu>VrJ?j)WU!m*s=9;$(+3'
    '65yzL}O<BXEQuz&KBWJAyt%>Nd;D;BCWx$ZN6YriOJ@3{DsVJC3j}haBY@2{eFg=LgZP#x9Ls*k^injGEuz65wbvU`XD'
    'zEo+y;JMG_2Xw`n#ZyusZ(k5la6e$@YCi>&86nyK4ZaXF>VK)*$snr3^1tCDgv^*}VD*L^8-PuX^Ao>7`%0auk6bqbe='
    ')q{lcDRzSS4B0yyrVM8TDRlR$AM1VbOt9<Chd>hDKqXEMLl0#M|jvLCLb!J_Dr|fMx4PPK@Dta+N;QY?F%dWIDTeXn#g'
    'i-pqZ1%=z!yySUfVLSD4N2!|kPW7)Atq(k?KumtV)r3;$Su6Auuy?gi0i46=u^C#GKDH`ul3eipT%Fubcj@w$yGc9#B}'
    'Q<%C6*BsuhBNP(jFlAy?dhUNzMEDiRo7zBk*^$@WN@zzw<pw@EAQ=@MfQ-P>w-3JY_`YUqwALe1SuGBx*rzy5#{yW$!)'
    'msmf<g_H?~M`LIp%`Z5{D_@W#7`nv2W~MK3<qZM@?NT=HbNhE4hl(9oLT~o8&%(hF9st>Ai1~K}dQ#ug2xW!|a1%-N0%'
    'ID})8^<?8K5Yt^@w*?)e(U<B3QQOn~0OI72q0Ko~^1ip{IBpfJPMoz4$#%h+TP7F*lz83g|C=%Lk1IwE=R=G=+lsE`*2'
    '(yL}Kf4#&sCT(6!Y@;-bazhzmArJpFoO%~?f2CCBB3}R6iTgL5~|Q5HLwY!H%&n0t`FZmlTWa{V+>&@((KeR$H1N%69d'
    'T>8v#{BiE95dBK^L-4-vl>BxhNTYGKVNSbh=Q&GQ&bsglhx$fXWs()dfUGUfW-sPuKi{PJH9Io_@Jkm8^L`0udEtq+ff'
    'g>W@wq;VYwVZG{iMDZaV-FTmGt9ej`2T~k>5$eKk^vUi9nU!Z%)*yOyu$|aIyqG-8hW<~rpGpT6zU%fI0+apn8{!Hxo_'
    'q2Uf6YR!Yk?IoVjNHMZ8RhR#k{F5tr0jRIX05QjMU$5{9GO)XPI`}yz12vndM&4=H{nH4n{17yQRHy=k@5!&Z)cliMDn'
    'k=nWEK7M7L@xg=rV$V<0R049|+g;+-@PTZ6{t75nX_*EI01=H3W5Uj`?HQKp-;Gm|B4$2mvWiV<I2f`Gg#>i;7NuX7KX'
    'Mo|g-Ox1lowE_H?mjC%M2^f*R#m#lRxn>%)+x+(S{eX8!&^=GGx1A5^xVt&;lgTs>OW{%Ue`dBm(LRW416q>Opr&25wy'
    'Wxx;Fe9$wq3_><CV#%q&ci69@pw=>j6~6x7Z)iWvfm3hc3-kp_b*Hse2e%h-+7j0>ar4qx|dohRoE;>C#b2WR%5acKd<'
    '+WiyK1%PL(_K16|je64uC=NU)iD~lJ&2z?z_VYwpE0lb*!&xN<%VSQIB)G+;@>=7p;i|2gb;Gw#C3g}T5_U2O9s60#Dx'
    '!Z%68|$N>^@i;n&4uoCmnoeRYUNtpl?3rV1kE<%I-OUY7K(BpF!{F_(uSR|L)GF{^$249G`S`1!{=rIUZUUKO1R(*N`f'
    'DWrjZ5JJ+O>5@8jpBBf#J92K7O2_VE}K*gR*Z#RVt*pg6nruC0j@Uss2LpGO-?rW5iM8nn70GlKrci|$JEw1K5@;3c0^'
    '}clK*#{U@t;rE-__M+d>^on4Zg2;I0J_tfuCx&WA@8hY{`X*~)S$wgGN(>A5K8WpQ*Y#edq$b@3I55F?M9%y!(Q?DFQH'
    '=uKtR&DW;HHHO2$jfj|1y2H*f$-xrm#r%J`m{Wb!X5;QTMjMVvMd4<n0y1c487`n9{xAC6|4HQM4Z?^>f8<L>M0Dv^iB'
    '&W86VKOMC(U)cJ`2rfSO<f^Kc$ANgS%umU5jrwB7F6M*k`N9sqVq4E9w&de#xB5c>>4<&p$mn!<&Grz{y^Tzcjbya3+|'
    'p$1X6JrvP8Q{`ZNAvXRB2fl;)dW`QH}VV?1lsJDGDJ4f}$WWe2UIG|IFgZH;LVxc~9-tDAP&O1#@jl!$BvYCbl(XII${'
    '$LS>_jYzzBzCZ)FerwC|0EHV6GFlM>z7xBm#lM?9=kwsYOz#Q(}NBQSY;P2E}twkHJy(pgozs5)u4EwhAwov`yoEt=->'
    'D81y_taLW62017!e)QX_C*&_t02Kv4P&$%h;{7u{i-lQrAoA9q3PH$HS?M`0}Dd5OS8JkO#oIpFDA8y@8|d&yVm$VRyB'
    'X*w%qpOR>$UIW|5hAM8IdqLvKBLrflb{uFy^b(}vA}SIB~8gODnHctXyJ{aiScw0E9D0Kwa-LHdX}Ri~h|f9)>^CV<4J'
    '3=UiRrXtq1LPx@iefOHCo|=67<}ImBBD6H<lWz^(OFvIdfl>f)tW!VRa1AT8i+;u7RK@Ns*B}tiKkzswaP>ijuvYy|5e'
    'xb}bZ-~rFx__d;O~TZtbt2Xg)hejjf`8LAp4?kKUs_}I&aJo+V;4NUt|3bFhss@*EsOg+9WLoN|KFkX%A*s7PIv@3P6v'
    'e`hnn)U%75fCL@cnAmT-1H&;-Fdd2S)Fy%_7C_0x|wz@h$YnaiDN@pSE;D<-=_{OBAor}@#b>E#xS}1QsxD;9c>FEL~&'
    '^0mo5ZBb%M!w}vo;T1B-z73_tp+F(;D;i%kAXM}t<s1)8=BGA(*RWv^76x?Pk7@@Yn+cZJVfvL$tNj+#slPgK&6ahjL?'
    'VutQ(gd$uSadEb|6p*n!x$$+?6WJ2THQe7O^}*IJYu8ybtjR_C1@k7?n5d`;1Ez}G3o{5gg@5M>e3cLv@fUhRpzF*awn'
    'SkB3}wenlC6@-#|As$zpU(d%fobB`@DhlfNB1EWQ*Xu{Hc27F0z7ki{@GfPJ;J!av)-Z5|g>!(#!K}8`p}}Ldo*X=&U<'
    'rTOK&3rp5FHpW?0^F@E}aWdN_i1)9XY)kqp!G5kE0|`;Vx%yY3#`&6>42zgQhST*TOgJV5HnzMWRays2`91&(a@K=F9R'
    'TW`YYy6+d@zDB9DGByrsI1U}u02FLs+Y2XeTDNMuiceg{O=<b+^_r3ORf;~u`UWObV49;XoPCrna4AQ7DemNDo(;80H*'
    'wAlViJ-K@L!QLrrP6gtupPqG>bq|6=)ZViiRK^$7Gd5=t%6#(#}MohH4oT_+%L~I5)}Y{=j%s1XO+~DGC7%X1uPK^e7K'
    'W<Ji4aUQkJdsdd6R#M8^*|5FVHJ%CZMN^4f^*GPH-9{-8|g1KJfG?`x3fllG_v>za>pUUs(4j;f{+>$CE$NGg6fmwPS|'
    'RJcxp5dUMC4Xw0%Oc5&%cg18C9V|K%0u_f?o!*xn1d%mDJ>qGHQuUQ$MgHlL=)(+alI4(26EpegI@xz~9*wo~JElr~T2'
    'l2{c7`2RNQY4o#;_~D>yCz~{kE$w^rXcO#{Ud3!D6o?fsMJ43InT)t$G8(M|zLU+1oKEIsP5wovT4snB|VA=};N~{th?'
    '`qTfP{M57gc<G)-=Wr|xDs!M7Mg_RA(fqDD9P>H7+In1DHw3q(hhL7wDA-;3wr$Mfi`IQ<b+rlFuN{_e|2bMnb30Fa5)'
    '?*U3vDy*%;eG$n9Yl-VH9wp3{!#IYXBz9)CxT>G&!?vt87&yr*2<+&=Zxk3rOw}<&n9&ovr<@XN|zH|l1y<5a`AUnm%9'
    'cesJ|{>uAgg}&X1z7bW}8&Ctg9|bqFAUyD-lFSAy$hRa0UPyH0;4x4dirB{h$huj?6swYZ$w3ZTP`Q`P{#2D31L5m@rF'
    '0{PoB9s(=4<FMY+J{G@15PfTMnvN-veHSxFzvxxpY~^Ug!8z-N6!9#$sMP(msxM(i{?kcLy_7eYXT<wYLs!zm@*nL;O|'
    '=8Frn@r}rZ1BtdeH!^pgz)An~w@D-!b}?7_hjWqrEp|4YdKYQ6s&bmP8)88zF92#Ogx?(*qx*R|d-e>e{+#3raAI;{NG'
    'jT#lL1;dM(1{=5|+l-NyF%<>vmFlkLnqHVxs5t{oDgr>4=@M#nE&gB?896ucMn-4QtWKTUZjXVv?uk9ZzrBtz7%?QK?#'
    'gJw6EJKTk?<`nx*YjoB!&zjYSh;ZMXWdh92XBG50CnI)9xVcuQeCk?xc<fQDq0(|*hTZ;h*y9X;NBV7SRK~uZ#G*Lz;X'
    '`C`D8~jxV?!=Tbf3K?p4RFW{9R~G126#yJktNxVVLRp+$ZYQneR2;TWTZxQVqwOiamo&HZsNu(?Eel^Y8fg@D89Z@<Wz'
    't>L#9Y3yxP_Ow+e7W1(nZl3+K{-R<;6jWRJwR-F@gu6!r1jE!LPm>Q70T9vF-^l<WpjL%R-rXbjwFg0K*4mWAsoV{IEg'
    'Kzg7#I<RfuGvgmC=?sAqg4^<Dh(rz#Cwweb{b<Z$FVL(k{e$1qOyBdwN&~&}0Y0HJ2M<l4FuWtTr@cP|%m0WVh~tE=y3'
    'Qv9wZ{lJB8FluU&ng9|)GG2v#R3j>zs7nFA7-K3A&m#z6}Y7uXzH68NGeKaP$c-$Y}IeF^~^0k5O5F6+vvaUqt0`r?f&'
    'Ezt;D6JId41Mtrmsxmw7BWZimY6V0Nb&m3pa7SN^g>nPZ#F$gdn%Lk=`6x+-sBD2xgQk=6u%B+ZwdC4**?zPGXHoNNa>'
    'q1!=uuv21TySd77;^3>QMv-Lr#fK-pk48Nk{c`rlN<mbMlmeHTOY8^=XpNRHv@`nZYJxhfEF^jFZ?<WoQBM};ue%?f+`'
    'Fr{s=Xa8MH5>3MjzC5wZu*R)jD*B|Nbt)CJn13u=YCn;LCnhgEtRp&AP&@8ABfQzMX{<zo4r?gRLbMgCQU^Z52Q9X?UH'
    'IlFjk`UF@2jZ2ahqEay&o+^bz8F;hFL#N#$;D{SUD*@AGzRrJ=nUCE(OMh*kCOX_s?UD1yw{E!XdS%HUO;SZH-qDGEkC'
    '{V#E%`8krbvd8IZZP8$BqLQ&<eh_1{;YzPl<buLn#gLg6wXJthB9Nu`-J+E=h#Uy|59H`FH7eRt5R@=Q`)Qa`v0A`DXH'
    'i}M*@fuNaKtMuGJ0$@EYP4LnkYI630o8>Ts<fheEN?w4EW2d%y0K!_etHCDGVom^gY9L#WV`<MH9|qWOFl8_K?Ve9tVa'
    '2)=FPXv_E?L|6ttGGW^9-7@9QS;^5Jn|fP*$FE<jgh|D9=mrZXF_t5Z7tT8-)YO~eAUCc_aJa-4w`I=Qq*8ITWgdgE_J'
    'kfNzmIaeNHKwK7;D(lsgYi3TVQ8Jes0vCRbhWvSd<XeU)>t~rRq6^)2cuy{>+yH9Rdg=+KY4q2D9k%fMm0|R~+1|B5$k'
    'CH$JBPxGhFCiJx8Ex2;dgJPJp9pv4{rhhT;j-PBth*Tnj_j7gtr3FuOI~!6I>b<)jxO<appR{IkwY__N+<pN6*@OQVl='
    'RYi%8CD;vqZN0t}`GLBgtZ5>N(Ji(nk<*5MwmkvO4V({Oa%T*11xX|o`!ID{8aQGJK$flGtuQLf~+STjg%3}jmVuu&}&'
    'g9*1zH@!k`CGr4p^cBo=^oeq#xOxxBV<zrM9-DheNmZ=&ro%nL<%FHywgD0K9}qn7xX$5*_KUL%E)vjE8c6*sNAQOhIB'
    'poNb<yM?wQSi6}U!Ug*YMe5$`ykooR@vR(2vhqt2~|CT>{sbs1bo5w**x)n!ds+)4lI>r2sRM}^=U>i0Ms4i>U8{HN6q'
    'Ci6qVJ+Bm)4Y&lIv~i~H;tAcpaOrl7COXZbhfVL!tU_w2%q<`!Y|macpUSc2{?>hM?K@QUPO-I@>gBXiYlT96hjTGjFI'
    'OaxsSN=5_pLRU6`+k|P*fj>vP@eI3Y56>el!egTBT5c^3g<8Aiuifl?iml+VvzG!J3WL%|qvHh1Fg1oZ$q<qQ1{N@@*G'
    '3B+L=9^NTexaFt<zQ&Wz&2t<QGI@jXUJv|29<@heXUJjH~|6g7x)9ygoYM4{_{D04MvqKz|Xi;XL(r+(89x%p+!^h8)?'
    '%Zw{f$@%DkLF0(u24Th;~{*u0y%cJm~SMw(utk8M*Nl;!)29pr}?m(EH>hh`&JL<R6vpSOfXuYA=~lkvqlS^_I0j8fd0'
    'rCs62I;h4qOr^s~*wG%sP`TK3k^Jsks$QY7&hRJHv0QIjj>DyaQ9kFW;2QzzLQs3N^?$e{>2rsSYl{MqwF#1lNmrv4%U'
    '`8HgOwJ36r*J!r`;W^mZVdGZGX*xz=3=}I%EhK}3^Os)Ew-EOxUVIw0Jp=2Wn~Tl8|KtIIj_jb|2sr~Y!(ARJsuIloxa'
    '=-Tjl`ye0CsbOMzZ@<4R@0Ey920ipAr5O_2vs5DXTX%T<JQ>^96@SA|R5OxhLpsV*5|_d&6*-Mxf4x56L1CVTe&6U*Zw'
    'O;*oRYxs_(5@aSl*v}nml;ZeTxfKgdL($a(IQkcC6Dbb*NLeKv)mr&Wa2#3zeK~M4NEmCVylfc}^xPbxf|I_nk+YzD8I'
    '}%|5;gh;!uzXExCgEEKrfH}0NCY{sl~QJCG<X$?*M)u&pTrjLSPUVc-i>^)`iG{ns1Szz)ydy+=S|B^?A+m!r^ot%d{+'
    'qSd{acNXw8C@fM?S#{S1HsXL!`#nkk=l;d=sMpiX`ny6kOhk1e*->~@UnrK-K%q0WnlTAnZdqIk++o=x7?GkqTBL>-q-'
    'JIc#TsY7CIn4E>9)edbu3;D~=K+<MG-Xt*uV^AkO<PJN&dHsg%1lzdU7?W(%hZlO}RY?}ws?Z49#a-^KdVa)%u2I_}za'
    'wO9A$R6W%r}y!F3N}$E5=&=u_B2`BjC{On8kwzp27nhb2Y5q)LCH06wZo@UP3+cvBz3t&l80DK9sci?VZx)xj0$%w<PL'
    'f2L7z(@D$upuux8dT`EKRs|#&4@+V79iF}Cj%Ef~=!GB57S0s|s8l|LRuje}<uN7d(CqJ0hpey<$c_eJHhI!m(^GGsC!'
    '|i62;c|NV()2zQcu78&<lM@Si{`E;3BzUYe;?+LPoF2i8aWTCqhVDPn(O0hind_YwO3kKY8b{RTAwR4LBVQHw!y$|nUw'
    '9=&q~~X|6dUTp-%j{<3UBzm|9%UWU|wk;XR!e41gyFH;lDns-LoWlr8Q%wrC7ePvKDT^v|@pks(TyQs`rvP1@5=T@>GB'
    '@S)5NbBoHCnM~S%yhauke?)XCmh7wl3dh*4H*E;8&RXxhieuTRAP+i1zc-wp>X2Y=QsD$qrsczQ!X1v&fE57EDk`sSK|'
    'V=H2;r@taxfpbMa|bo5R$l?j_^a|elN*?LA<Op8?%{<K~F>lBwHJpe#J?Kg2i~dlA6oR)%cFfRCft0fh(r2d{BhXrze-'
    '=dgJ{OCkT-Dv*zC3L7J`G5Vb5;?3LuSK?!FZ+I2Rxc|N^M%ZPOGP_Z}#{YRldM3I^WIexJxmWk1k5?J_w%zD#bW6V3as'
    'zC_<VZ(J61U7u15nAV;-^Fngg&z+WDifQqZ1h3~X~D)DC~eAZEke3*K3!z!!8O5R%^gEODo9s12<Rwn5i3+njvgTcGDN'
    'BuriA?nq<OyX8eK79NSW7?bn(xMB05?DqQONvRA@-sJWvB|KT`QJZ0IfU8DS8MIO;sCuAGeYc;5n_kij7uz6F1)3iI2A'
    'zD@pg-N$=vl)lCW+06`vm8`6Kg^WH!Xt0NRJv`1Kp62Utadk8!1f3dVuK32Ra`5CeFxmhkOzF<YIZ0k+WAm?eHt(?kE@'
    '6tT<me6jet<_Kq@mn{FCbO?h{6?L#7!eU2*#Fm{+kaG-<HC0@@DOR+_eQOlavvCbiL2gm#hc>8%Z*>Fsq=K%Q0RUVUkL'
    'l7xr;AUruHtI1Swdl-KkPj`bS)Sodv2qbH18w%@XU9q%Ju8|Ik(q=iT`FU-j~^gc;0z7VkS0a02v+T^Q}&<@d`<T1QQ-'
    '}fjelHy81CP*6=VHlpl3Ynm6^3kgobS;a{n}-dECQ9+uh5sw`Ttn%?X5cYkCR%`-@Z`=r%!1yOq6#FacYvh>X30Z<U*Y'
    'I;Y*gypvps7*6>a=U8U^Gtd;B$Pm>rvb#Q!6#E~zC-yPF<Q@r;X@TTaAT5aS>6l5;(Lvy}6A%!|Jn+{|N4Q&W!9#-$5T'
    '*eyqFv%h6{QQ>XjP@yIE%2zn)zK20eTP(xPzY88oL*Yfmr?*~0hdjfPRqrMDs8f9HF_5KLW}376d^tE>7HVW)=1oJ~BH'
    '7oE0?klk6x;2+M4OPRAu`Fl9Gyhr0zwT1YheAD>8c2tlsOx5=>kfzB_n)b000004@WyxhA_Sw00H&C1dG4~)3XS=vBYQ'
    'l0ssI200dcD'
)



def _embedded_aucdtect():
    """Restore the ORIGINAL engine, silently, inside this app's private data.

    Zero external ZIP/download/engine installation steps. Explicitly verified
    SHA-256 before execution. Never overwrite nonmatching user-owned files.
    """
    if not IS_WIN:
        raise RuntimeError("Original AudioChecker engine is Windows-only.")
    folder = DEPS / "auCDtect"
    binary = folder / "aucdtect.exe"
    readme = folder / "Readme"
    output_map = {
        "Codecs/Analyzer/aucdtect.exe": binary,
        "Codecs/Analyzer/Readme": readme,
    }
    # Never execute a swapped/malicious file even if installed previously.
    for logical, target in output_map.items():
        if target.is_symlink():
            raise RuntimeError("Original auCDtect component is an unsafe symlink.")
        if target.is_file() and sha(target) != _AUCDTECT_ALLOWED[logical]:
            raise RuntimeError("Original auCDtect integrity mismatch: " + target.name)
    if all(path.is_file() for path in output_map.values()):
        return binary
    bundle = lzma.decompress(base64.b85decode("".join(_AUCDTECT_REFERENCE_ARCHIVE)))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        for logical, target in output_map.items():
            if target.exists():
                continue
            payload = archive.read(logical)
            digest = hashlib.sha256(payload).hexdigest()
            if digest != _AUCDTECT_ALLOWED[logical]:
                raise RuntimeError("Embedded original auCDtect has invalid SHA-256.")
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(target.name + "-" + uuid.uuid4().hex + ".tmp")
            try:
                stage.write_bytes(payload)
                if sha(stage) != _AUCDTECT_ALLOWED[logical]:
                    raise RuntimeError("Staged original auCDtect failed verification.")
                os.replace(stage, target)
            finally:
                stage.unlink(missing_ok=True)
    if sha(binary) != _AUCDTECT_ALLOWED["Codecs/Analyzer/aucdtect.exe"]:
        raise RuntimeError("Original auCDtect binary failed post-install verification.")
    return binary


def _native_aucdtect():
    return _embedded_aucdtect()


def _original_checker_checksum(results):
    """Original AudioChecker 2.0 (build 457) 32-bit UTF-8 report checksum."""
    entries = []
    for track, category, probability in results:
        if category not in ("CDDA", "MPEG") or not 0 <= probability <= 100:
            raise ValueError("Only confirmed auCDtect classifications are valid.")
        entries.append(f"{track.name}|{category}|{probability}%")
    value = 0
    for character in "\r".join(entries).encode("utf-8"):
        value = ((value << 4) + (character ^ 0xD1)) & 0xFFFFFFFF
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
            value &= (~high) & 0xFFFFFFFF
    return value


def _original_checker_report(folder_name, results):
    """Preserve the historic AUDIOCHECKER v2 report layout using REAL verdicts.

    The true auCDtect decision engine supplies every probability; no inference,
    calibration or spectral score translation is performed by this wrapper.
    """
    if not results:
        raise RuntimeError("No verified auCDtect classifications.")
    lines = [
        "AUDIOCHECKER v2.0 beta (build 457) - by Dester - opdester@freemail.hu",
        "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", "-=== DO NOT EDIT THIS FILE! ===-", "",
        "Path: ...\\" + str(folder_name), "",
    ]
    score = []
    width = len(str(len(results)))
    for idx, (track, label, prob) in enumerate(results, 1):
        if label not in ("CDDA", "MPEG") or not 0 <= prob <= 100:
            raise RuntimeError("Unverified auCDtect classification.")
        lines.append(f"{idx:0{width}d} -=- {track.name} -=- {label} ({prob}%)")
        score.append(prob if label == "CDDA" else 100 - prob)
    if len(score) > 1:
        lines.append(("Summary %.2f%% CDDA" % (sum(score) / len(score))).replace(".", ","))
    lines.append(str(_original_checker_checksum(results)))
    return "\r\n".join(lines) + "\r\n"


def _parse_native_aucdtect(text):
    match = _AC_NATIVE_RESULT.search(str(text))
    if not match:
        raise RuntimeError("The original auCDtect engine supplied no valid CDDA/MPEG verdict.")
    probability = int(match.group(2))
    if probability > 100:
        raise RuntimeError("Original auCDtect supplied an invalid probability.")
    return match.group(1).upper(), probability


def _original_checker_job(track, ff, ffprobe, native, stop_event):
    if stop_event.is_set():
        raise InterruptedError("AudioChecker cancelled.")
    details = probe(ffprobe, track)
    rate = int(details.get("sample_rate") or 0)
    channels = int(details.get("channels") or 0)
    declared_depth = details.get("bits_per_raw_sample") or details.get("bits_per_sample")
    try:
        depth = int(declared_depth)
    except (TypeError, ValueError):
        depth = 0
    if depth and depth != 16:
        raise RuntimeError(
            f"{track.name}: original audio is {depth}-bit, not CD 16-bit. "
            "Converting to 16-bit could change the original auCDtect result; "
            "no fabricated parity report is permitted.")
    if rate != 44100 or channels not in (1, 2):
        raise RuntimeError(
            f"Original auCDtect CD-audio mode needs 44100 Hz mono/stereo; "
            f"{track.name} has {rate} Hz / {channels} channels. "
            "Do not silently resample or invent a score.")
    with tempfile.TemporaryDirectory(prefix="MLO_auCDtect_", dir=str(DATA)) as td:
        wav = Path(td) / "track.wav"
        decoded = cmd([ff, "-hide_banner", "-v", "error", "-nostdin",
                       "-i", str(track), "-map", "0:a:0", "-vn", "-sn", "-dn",
                       "-c:a", "pcm_s16le", "-ar", "44100",
                       "-f", "wav", str(wav)], timeout=3600)
        if decoded.returncode or not wav.is_file():
            raise RuntimeError("Source audio decoding failed: " + decoded.stderr[-300:])
        if stop_event.is_set():
            raise InterruptedError("AudioChecker cancelled.")
        analyzed = cmd([str(native), "-mS0", str(wav)], timeout=3600)
        body = analyzed.stdout + "\n" + analyzed.stderr
        if analyzed.returncode and not _AC_NATIVE_RESULT.search(body):
            raise RuntimeError("Original auCDtect failed: " + body[-350:])
        label, probability = _parse_native_aucdtect(body)
        return track, label, probability


def _verify_original_checker_report(path, tracks, folder_name):
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if not text.startswith("AUDIOCHECKER v2.0 beta (build 457)"):
            return False
        if "\r\nPath: ...\\" + str(folder_name) + "\r\n" not in text:
            return False
        found = []
        for line in text.splitlines():
            m = re.match(r"^\d+ -=- (.*?) -=- (CDDA|MPEG) \((\d{1,3})%\)$", line)
            if m:
                found.append((Path(m.group(1)), m.group(2), int(m.group(3))))
        if len(found) != len(tracks) or any(
                a.name != b.name for (a, _, _), b in zip(found, tracks)):
            return False
        if int(text.splitlines()[-1]) != _original_checker_checksum(found):
            return False
        return raw == _original_checker_report(folder_name, found).encode("utf-8")
    except (ValueError, OSError, UnicodeError):
        return False


def native_check_web_release(dest, fmt, ff, stop_event, workers=4, release_name=None):
    """Real auCDtect classifications; never downgrade to homemade FFT output."""
    native = _native_aucdtect()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe unavailable for the genuine auCDtect analysis.")
    provenance = []
    for base, dirs, filenames in os.walk(dest):
        if stop_event.is_set():
            raise InterruptedError("AudioChecker cancelled.")
        folder = Path(base)
        tracks = sorted(folder / fn for fn in filenames
                        if Path(fn).suffix.lower() in (".flac", ".m4a"))
        if not tracks:
            continue
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, int(workers))) as pool:
            futures = [pool.submit(_original_checker_job, track, ff, ffprobe, native,
                                   stop_event) for track in tracks]
            results = [future.result() for future in futures]
        if len(results) != len(tracks):
            raise RuntimeError("Original auCDtect did not check every file.")
        name = (release_name or folder.name) if folder == dest else folder.name
        report = folder / "audiochecker.log"
        if report.exists():
            raise RuntimeError("Refusing to overwrite an existing checker report.")
        report.write_bytes(_original_checker_report(name, results).encode("utf-8"))
        if not _verify_original_checker_report(report, tracks, name):
            report.unlink(missing_ok=True)
            raise RuntimeError("Original AudioChecker-style report checksum/coverage mismatch.")
        provenance.append({"report": str(report.relative_to(dest)),
                           "analyzer": "Original auCDtect -mS0 (not homemade FFT)",
                           "workers": int(workers), "input": fmt,
                           "results": len(results),
                           "mpeg": sum(item[1] == "MPEG" for item in results),
                           "cdda": sum(item[1] == "CDDA" for item in results)})
    if not provenance:
        raise RuntimeError("Original auCDtect found no tracks to analyze.")
    return provenance


def completion_reasons(info, export_warnings, checker, checker_enabled=True):
    """Return every unresolved release requirement. No unexplained PARTIAL."""
    reasons = []
    if not info.get("metadata_verified"):
        reasons.append("Exact release edition metadata not verified")
    if any("unresolved" in n.lower() or "not auto-selected" in n.lower()
           for n in info.get("notes", [])):
        reasons.append("Original metadata/media reference unresolved")
    if export_warnings:
        reasons.append("CUE or supporting-file fidelity not fully verified")
    if bool(info.get("physical")) and not info.get("country"):
        reasons.append("No physical release region verified or supplied")
    if not bool(info.get("physical")) and not (info.get("barcode") or info.get("catalog")):
        reasons.append("Neither barcode nor catalog verified for WEB edition")
    if checker_enabled and not bool(info.get("physical")) and not checker:
        reasons.append("Original auCDtect report missing")
    elif checker_enabled and not bool(info.get("physical")) and any(
            x.get("results", 0) < 1 for x in checker):
        reasons.append("Original auCDtect report has incomplete coverage")
    # A verified WEB barcode does not require a
    # separate MusicBrainz catalog number.
    return reasons



def _invalid_old_checker_reports(target):
    """Detect historical fake FFT output without changing the existing files."""
    if not target.is_dir() or target.is_symlink():
        return 0
    count = 0
    for previous in target.rglob("audiochecker.log"):
        if previous.is_symlink() or not previous.is_file():
            continue
        try:
            with previous.open("rb") as stream:
                if stream.read(60).startswith(b"MLO Integrated AudioChecker v0.3."):
                    count += 1
        except OSError:
            continue
    return count



# Apple iTunes UPC artwork and verified Spotify fallback; standalone, no keys.
ARTWORK_UNDO = DATA / "artwork-undo.json"
ARTWORK_LIMIT = 96 * 1024 * 1024


def _cover_request(url, hosts, maximum=ARTWORK_LIMIT):
    item = urllib.parse.urlsplit(str(url))
    if item.scheme != "https" or item.hostname not in hosts or item.username or item.password:
        raise ValueError("Untrusted cover URL: " + str(url)[:130])
    req = urllib.request.Request(url, headers={"User-Agent": APP + "/" + VERSION,
                                                "Accept": "image/jpeg,application/json,*/*;q=0.2"})
    with urllib.request.urlopen(req, timeout=18) as resp:
        actual = urllib.parse.urlsplit(resp.geturl())
        if actual.scheme != "https" or actual.hostname not in hosts:
            raise ValueError("Cover redirects to an untrusted host")
        if int(resp.headers.get("Content-Length") or 0) > maximum:
            raise ValueError("Cover exceeds maximum download size")
        data = resp.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Cover download exceeds size limit")
        return data


def _cover_json(url, hosts):
    return json.loads(_cover_request(url, hosts, 3 * 1024 * 1024).decode("utf-8-sig"))


def _cover_jpeg_size(data):
    """Verify JPEG magic/EOI and dimensions instead of saving PNG as .jpg."""
    if not isinstance(data, bytes) or len(data) < 64 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ValueError("Not a complete JPEG image")
    i = 2
    while i + 9 < len(data):
        if data[i] != 255:
            raise ValueError("Bad JPEG marker")
        while i < len(data) and data[i] == 255:
            i += 1
        if i == len(data):
            break
        marker = data[i]
        i += 1
        if marker == 0xda:
            break
        if marker in (0x01, 0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        n = int.from_bytes(data[i:i + 2], "big")
        if n < 2 or i + n > len(data):
            raise ValueError("Bad JPEG segment")
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            h = int.from_bytes(data[i + 3:i + 5], "big")
            w = int.from_bytes(data[i + 5:i + 7], "big")
            if min(h, w) < 64 or max(h, w) > 12000:
                raise ValueError("Invalid JPEG dimensions")
            return w, h
        i += n
    raise ValueError("JPEG dimensions unavailable")


def _cover_upcs(value):
    code = str(value or "")
    if not _valid_gtin(code):
        return []
    variants = [code]
    if len(code) == 12:
        variants.append("0" + code)
    if len(code) == 13 and code.startswith("0"):
        variants.append(code[1:])
    return list(dict.fromkeys(x for x in variants if _valid_gtin(x)))


def _cover_apple_urls(url):
    obj = urllib.parse.urlsplit(str(url))
    host = obj.hostname or ""
    if obj.scheme != "https" or not re.fullmatch(r"is[1-5]-ssl\.mzstatic\.com", host):
        return []
    root, sep, _ = obj.path.rpartition("/")
    if not sep or not re.search(r"(?i)\.(?:jpe?g|png)$", root):
        return []
    return ["https://" + host + root + "/" + size for size in
            ("10000x0w-999.jpg", "6000x0w-999.jpg", "4000x0w-999.jpg",
             "3000x0w-999.jpg", "2000x0w-999.jpg",
             "1200x1200bb.jpg", "600x600bb.jpg")]


def _cover_apple(barcode, title, stop):
    reasons = []
    for country in ("US", "GB", "FR", "JP"):
        for code in _cover_upcs(barcode):
            if stop.is_set():
                raise InterruptedError("Artwork cancelled")
            url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode({
                "upc": code, "entity": "album", "country": country})
            try:
                hits = _cover_json(url, {"itunes.apple.com"}).get("results", [])
                matches = sorted(
                    [(_release_title_similarity(title, item.get("collectionName") or ""), item)
                     for item in hits if item.get("wrapperType") == "collection"
                     and item.get("artworkUrl100")], key=lambda pair: -pair[0])
                if not matches or matches[0][0] < 0.55:
                    continue
                if len(matches) > 1 and abs(matches[0][0] - matches[1][0]) < .015:
                    reasons.append("Ambiguous Apple UPC matches")
                    continue
                for candidate in _cover_apple_urls(matches[0][1]["artworkUrl100"]):
                    if stop.is_set():
                        raise InterruptedError("Artwork cancelled")
                    try:
                        data = _cover_request(candidate, {
                            f"is{i}-ssl.mzstatic.com" for i in range(1, 6)})
                        w, h = _cover_jpeg_size(data)
                        return {"bytes": data, "size": f"{w}x{h}",
                                "provider": "Apple Music",
                                "link": matches[0][1].get("collectionViewUrl") or url}, reasons
                    except (OSError, ValueError) as exc:
                        reasons.append("Apple image: " + str(exc)[:100])
            except InterruptedError:
                raise
            except (OSError, ValueError, TimeoutError) as exc:
                reasons.append("Apple lookup: " + str(exc)[:100])
    return None, reasons


def _cover_spotify(barcode, title, stop):
    """Search exact GTIN MusicBrainz release URL relations, no Spotify auth."""
    reasons, albums = [], []
    try:
        ids = []
        for code in _cover_upcs(barcode):
            if stop.is_set():
                raise InterruptedError("Artwork cancelled")
            path = "release/?" + urllib.parse.urlencode({
                "query": "barcode:" + code, "fmt": "json", "limit": "100"})
            for item in mb_json(path).get("releases", []):
                if (str(item.get("barcode") or "").lstrip("0") == barcode.lstrip("0") and
                        _release_title_similarity(title, item.get("title", "")) >= 0.65 and
                        item.get("id")):
                    ids.append(item["id"])
        for rid in list(dict.fromkeys(ids))[:6]:
            if stop.is_set():
                raise InterruptedError("Artwork cancelled")
            release = mb_json("release/" + rid + "?inc=release-groups+url-rels&fmt=json")
            nodes = [release]
            group_id = (release.get("release-group") or {}).get("id")
            if group_id:
                nodes.append(mb_json("release-group/" + group_id + "?inc=url-rels&fmt=json"))
            for node in nodes:
                for rel in node.get("relations") or []:
                    resource = str((rel.get("url") or {}).get("resource") or "")
                    m = re.fullmatch(
                        r"https://open\.spotify\.com/album/([A-Za-z0-9]{22})(?:[/?#].*)?",
                        resource)
                    if m:
                        albums.append("https://open.spotify.com/album/" + m.group(1))
    except InterruptedError:
        raise
    except Exception as exc:
        reasons.append("Spotify MusicBrainz relations: " + str(exc)[:120])
    for album_url in dict.fromkeys(albums):
        if stop.is_set():
            raise InterruptedError("Artwork cancelled")
        try:
            url = "https://open.spotify.com/oembed?" + urllib.parse.urlencode({"url": album_url})
            meta = _cover_json(url, {"open.spotify.com"})
            jpeg = _cover_request(meta.get("thumbnail_url") or "", {"i.scdn.co"})
            w, h = _cover_jpeg_size(jpeg)
            return {"bytes": jpeg, "size": f"{w}x{h}",
                    "provider": "Spotify", "link": album_url}, reasons
        except (OSError, ValueError, TimeoutError) as exc:
            reasons.append("Spotify oEmbed: " + str(exc)[:100])
    return None, reasons + ["No Spotify cover for the exact barcode release"]


def _cover_save(root, folder, artwork, manifest):
    """Save cover to Destination root, named after the source release folder."""
    if (not root.is_dir() or root.is_symlink() or
            not folder.is_dir() or folder.is_symlink() or
            (folder != root and root not in folder.parents)):
        raise ValueError("Unsafe cover destination or release folder")
    dest = root / (folder.name + ".jpg")
    if os.path.lexists(dest):
        return dest, False
    _cover_jpeg_size(artwork)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_BINARY", 0))
    committed = False
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(artwork)
            out.flush()
            os.fsync(out.fileno())
        digest = hashlib.sha256(artwork).hexdigest()
        if sha(dest) != digest:
            raise ValueError("Saved cover checksum mismatch")
        manifest["created"].append({"path": str(dest.resolve()), "sha256": digest,
                                    "release_name": folder.name})
        try:
            write_json(ARTWORK_UNDO, manifest)
        except Exception:
            # Avoid a stale Undo reference if persisting this image failed.
            manifest["created"].pop()
            raise
        committed = True
        return dest, True
    finally:
        if not committed:
            dest.unlink(missing_ok=True)



def _cover_release_barcode(folder):
    """Prefer folder UPC, then tagged UPC, then verified MusicBrainz catalog."""
    ident, physical = catalog(folder.name)
    if _valid_gtin(ident):
        return ident
    for file in collect_files(folder):
        if file.suffix.casefold() not in AUDIO:
            continue
        try:
            from mutagen import File as MutagenFile
            tags = MutagenFile(str(file), easy=True)
            for key in ("barcode", "upc"):
                candidate = str((tags.get(key) or [""])[0]) if tags else ""
                if _valid_gtin(candidate):
                    return candidate
        except Exception:
            pass
        break
    if not ident:
        return ""
    # An 11-digit physical CD catalog is NOT a UPC. MusicBrainz must
    # corroborate an exact catalog, edition type and release title before
    # allowing the public Apple UPC artwork lookup.
    title_match = _RELEASE_PARTS.fullmatch(folder.name)
    title = title_match.group("title") if title_match else folder.name
    try:
        entries = _mb_catalog_search(ident, physical=physical)
        accepted = set()
        for entry in entries[:25]:
            if _release_title_similarity(title, entry.get("title", "")) < 0.65:
                continue
            release_id = str(entry.get("id") or "")
            if not release_id:
                continue
            concrete = mb_json("release/" + release_id +
                               "?inc=labels+media+release-groups&fmt=json")
            if not _matches_catalog(concrete, ident):
                continue
            media = concrete.get("media") or []
            if not media or not all(
                    ("cd" in str(m.get("format") or "").lower() if physical
                     else "digital media" in str(m.get("format") or "").lower())
                    for m in media):
                continue
            code = str(concrete.get("barcode") or "")
            if _valid_gtin(code):
                accepted.add(code.lstrip("0"))
        if len(accepted) == 1:
            return next(iter(accepted))
    except (OSError, ValueError, RuntimeError):
        return ""
    return ""


def _cover_archive_prior_undo():
    """Preserve the preceding artwork journal before a new download run.

    This matters when a user previously ran v0.3.18, which put images
    inside release directories. Preserve that recovery evidence rather
    than overwriting it with the new flat-output operation.
    """
    if not os.path.lexists(ARTWORK_UNDO):
        return None
    if ARTWORK_UNDO.is_symlink() or not ARTWORK_UNDO.is_file():
        raise ValueError("Previous Artwork Undo journal is not a regular file")
    if not isinstance(read_json(ARTWORK_UNDO, None), dict):
        raise ValueError("Previous Artwork Undo journal is unreadable; refusing to overwrite")
    history = DATA / "artwork-undo-history"
    history.mkdir(parents=True, exist_ok=True)
    archive = history / (
        "artwork-undo-" + datetime.now().strftime("%Y-%m-%d-%H-%M-%S") +
        "-" + uuid.uuid4().hex[:12] + ".json")
    ARTWORK_UNDO.replace(archive)
    return archive


def _cover_undo_latest():
    if not ARTWORK_UNDO.is_file():
        return False
    if not UNDO.is_file():
        return True
    return ARTWORK_UNDO.stat().st_mtime_ns >= UNDO.stat().st_mtime_ns


class Organizer:
    """One action: input -> organized output. No manual release-review gate."""
    def __init__(self):
        ensure_data()
        settings = read_json(SETTINGS, {})
        self.source = str(settings.get("source", ""))
        self.dest = str(settings.get("dest") or suggested_output(self.source))
        self.fmt = str(settings.get("format", "ALAC"))
        default_enc, default_check = worker_defaults()
        self.encoding_workers = int(settings.get("encoding_workers") or default_enc)
        self.checker_workers = int(settings.get("checker_workers") or default_check)
        self.enable_encoding = bool(settings.get("enable_encoding", True))
        self.enable_checker = bool(settings.get("enable_checker", True))
        self.enable_splitting = bool(settings.get("enable_splitting", True))
        self.running = False
        self.mode = "organize"
        self.stop = threading.Event()
        self.status = "Choose input and output folders, then Organize."
        self.progress = 0
        self.counts = {"total": 0, "done": 0, "partial": 0, "skipped": 0, "failed": 0, "warnings": 0}
        self.results = []
        self.activity = []
        self.error = ""
        self.lock = threading.RLock()
        self.window = None
        self.log_path = _new_log("session")
        self.info("v" + VERSION + " automatic mode - source files are never modified.")

    def info(self, message):
        with self.lock:
            self.activity.append(message)
            self.activity = self.activity[-250:]
            try:
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n")
            except OSError as exc:
                self.error = "Activity log could not be written: " + str(exc)

    def state(self):
        with self.lock:
            return {"source": self.source, "dest": self.dest, "format": self.fmt,
                    "running": self.running, "mode": self.mode, "status": self.status,
                    "progress": self.progress, "encoding_workers": self.encoding_workers,
                    "checker_workers": self.checker_workers,
                    "enable_encoding": self.enable_encoding,
                    "enable_checker": self.enable_checker,
                    "enable_splitting": self.enable_splitting,
                    "worker_limit": worker_limit(),
                    "counts": dict(self.counts),
                    "results": list(self.results), "error": self.error,
                    "activity": list(self.activity),
                    "undo": UNDO.is_file() or ARTWORK_UNDO.is_file(),
                    "undo_kind": "artwork" if _cover_undo_latest() else "organize"}

    def open_log_folder(self):
        return self.open_folder(str(DATA / "logs"))

    def browse(self, which):
        try:
            import webview
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return str(result[0])
        except Exception as exc:
            self.info("Folder picker failed: " + str(exc))
        return ""

    def open_folder(self, path):
        target = Path(str(path)).expanduser()
        if not target.exists():
            return {"error": "Path does not exist: " + str(target)}
        try:
            if target.is_file():
                if IS_WIN:
                    subprocess.Popen(["explorer.exe", "/select,", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target.parent)])
            elif target.is_dir():
                if IS_WIN:
                    os.startfile(str(target))
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            else:
                return {"error": "Not a regular file or folder: " + str(target)}
            return {"ok": True}
        except Exception as exc:
            return {"error": str(exc)}

    def open_log(self):
        # Fresh launches should open the last operation, not an old merged log
        # or the new session-only startup note.
        latest = self.log_path
        if latest.name.startswith("session-"):
            latest = _most_recent_operation_log() or latest
        if not latest.is_file():
            return {"error": "Activity log is not available."}
        try:
            if IS_WIN:
                os.startfile(str(latest))
            else:
                subprocess.Popen(["xdg-open", str(latest)])
            return {"ok": True}
        except Exception as exc:
            return {"error": str(exc)}

    def cancel(self):
        self.stop.set()
        return {"ok": True}

    def organize(self, source, dest, fmt, encoding_workers=4, checker_workers=4,
                 enable_encoding=True, enable_checker=True, enable_splitting=True):
        with self.lock:
            if self.running:
                return {"error": "Another operation is running."}
            try:
                src, dst = check_paths(str(source), str(dest or suggested_output(source)), str(fmt))
                enc, chk = int(encoding_workers), int(checker_workers)
                if not 1 <= enc <= worker_limit() or not 1 <= chk <= worker_limit():
                    raise ValueError("Worker settings must be between 1 and the detected logical CPU count.")
            except Exception as exc:
                return {"error": str(exc)}
            existing = read_json(UNDO, {})
            if existing.get("pending"):
                return {"error": "Previous run has an interrupted output checkpoint. Resolve before continuing."}
            try:
                next_log = _new_log("run")
            except OSError as exc:
                return {"error": "Cannot create a new run log: " + str(exc)}
            self.log_path = next_log
            self.source, self.dest, self.fmt = str(src), str(dst), str(fmt)
            self.encoding_workers, self.checker_workers = enc, chk
            self.enable_encoding = bool(enable_encoding)
            self.enable_checker = bool(enable_checker)
            self.enable_splitting = bool(enable_splitting)
            write_json(SETTINGS, {"source": self.source, "dest": self.dest,
                                  "format": self.fmt,
                                  "encoding_workers": enc, "checker_workers": chk,
                                  "enable_encoding": self.enable_encoding,
                                  "enable_checker": self.enable_checker,
                                  "enable_splitting": self.enable_splitting})
            self.running = True
            self.mode = "organize"
            self.stop.clear()
            self.status, self.progress, self.error = "Discovering releases...", 0, ""
            self.counts = {"total": 0, "done": 0, "partial": 0,
                           "skipped": 0, "failed": 0, "warnings": 0}
            self.results, self.activity = [], []
        self.info("RUN START: " + str(src) + " -> " + str(dst) + " | " + str(fmt))
        threading.Thread(target=self._organize, args=(src, dst, fmt), daemon=True).start()
        return {"ok": True}

    def _organize(self, source, dest, fmt):
        staging_root = None
        manifest = None
        try:
            discovered = discover(source)
            releases = group_disc_releases(discovered)
            with self.lock:
                self.counts["total"] = len(releases)
            if not releases:
                self.status = "No audio releases found."
                self.info(self.status)
                return
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest.resolve(strict=True)
            staging_root = dest / (".MLO-stage-" + uuid.uuid4().hex)
            staging_root.mkdir()
            ff = ffprobe = None
            seen = set()
            artist_main = re.sub(
                r"(?i)(?:\s+-\s+UPDATED.*|\s+(?:ALAC|FLAC))$", "", source.name).strip()
            self._artist_root = artist_main
            self.status = "Analyzing source artist credits..."
            credits_start = time.monotonic()
            self._credit_consensus = collection_credit_consensus(discovered, artist_main)
            self.info("PERFORMANCE: initial artist credit scan | releases=" +
                      str(len(releases)) + " | seconds=" +
                      str(round(time.monotonic() - credits_start, 2)))
            for index, (source_name, members) in enumerate(releases, 1):
                if self.stop.is_set():
                    self.status = "Cancelled. Completed output can be undone."
                    break
                members = tuple(p.resolve(strict=True) for p in members)
                grouped = len(members) > 1
                folder = members[0].parent if grouped else members[0]
                result = {"name": source_name, "source": str(members[0]), "target": "",
                          "status": "", "message": "", "warnings": [],
                          "metadata": {}, "checker": [], "timings_seconds": {},
                          "processing": {"encoding": self.enable_encoding,
                                         "audiochecker": self.enable_checker,
                                         "split_images": self.enable_splitting}}
                started_release = time.monotonic()
                try:
                    if any(source != member and source not in member.parents
                           for member in members):
                        raise RuntimeError("Input release escaped the source root.")
                    # When output already exists under the *same name*,
                    # skip before expensive sequential MusicBrainz requests.
                    # A fresh destination is required to reprocess releases.
                    preexisting = dest / source_name
                    if os.path.lexists(preexisting):
                        result["target"] = str(preexisting)
                        obsolete_count = _invalid_old_checker_reports(preexisting)
                        if obsolete_count:
                            result["status"] = "partial"
                            result["message"] = (
                                f"Existing output contains {obsolete_count} "
                                "INVALID custom-FFT AudioChecker report(s); "
                                "no originals or outputs changed. Recreate in "
                                "a new empty destination with genuine auCDtect.")
                            result["warnings"].append(result["message"])
                            result["unmet_requirements"] = ["Invalid historic FFT report"]
                            self.counts["partial"] += 1
                        else:
                            result["status"] = "skipped"
                            result["message"] = "Output already exists - not overwritten; no lookup required."
                            self.counts["skipped"] += 1
                        continue
                    files = ([fp for member in members for fp in collect_files(member)]
                             if grouped else collect_files(folder))
                    if not files:
                        raise RuntimeError("Release is empty.")
                    if any(fp.suffix.lower() in LOSSY for fp in files):
                        raise RuntimeError("Lossy input present; conversion to purported lossless is prohibited.")
                    self.status = f"Looking up release {index}/{len(releases)}: {folder.name}"
                    artist_hint = re.sub(r"(?i)(?:\s+-\s+UPDATED.*|\s+(?:ALAC|FLAC))$", "", source.name).strip()
                    metadata_start = time.monotonic()
                    if grouped:
                        info = release_identity(folder, files, default_artist=artist_hint,
                                                source_name=source_name, disc_folders=members)
                    else:
                        info = release_identity(folder, files, default_artist=artist_hint)
                    result["timings_seconds"]["musicbrainz_metadata"] = round(
                        time.monotonic() - metadata_start, 2)
                    result["metadata"] = {k: v for k, v in info.items() if k != "folder"}
                    result["warnings"].extend(info["notes"])
                    out_name = release_folder_target(info)
                    result["warnings"].extend(x for x in info["notes"] if x not in result["warnings"])
                    target = dest / out_name
                    result["target"] = str(target)
                    if out_name.casefold() in seen:
                        raise RuntimeError("Two input editions map to identical output folder: " + out_name)
                    seen.add(out_name.casefold())
                    if os.path.lexists(target):
                        # Incompatible pre-v0.3.9 homemade FFT reports must
                        # never be treated as successfully verified AudioChecker
                        # results merely because the output folder exists.
                        obsolete_count = _invalid_old_checker_reports(target)
                        if obsolete_count:
                            result["status"] = "partial"
                            result["message"] = (
                                "Existing output contains " + str(obsolete_count) +
                                " INVALID custom-FFT AudioChecker report(s); "
                                "no originals or outputs changed. Recreate in "
                                "a new empty destination with genuine auCDtect.")
                            result["warnings"].append(result["message"])
                            result["unmet_requirements"] = [
                                "Original auCDtect analysis absent; previous FFT reports are invalid"]
                            self.counts["partial"] += 1
                        else:
                            result["status"] = "skipped"
                            result["message"] = "Output already exists - not overwritten."
                            self.counts["skipped"] += 1
                    else:
                        if info["physical"]:
                            if any(fp.name.casefold() == "audiochecker.log" for fp in files):
                                result["warnings"].append(
                                    "Ignored WEB-only audiochecker.log from physical CD output (source preserved).")
                            if not any(fp.suffix.lower() == ".cue" for fp in files):
                                result["warnings"].append("CD CUE absent; genuine source not fabricated.")
                            if not any(fp.suffix.lower() == ".log" and
                                       fp.name.casefold() != "audiochecker.log" for fp in files):
                                result["warnings"].append("CD ripping log absent; not fabricated.")
                            if not info["catalog"]:
                                result["warnings"].append("Specific physical edition catalog not verified.")
                        if ff is None:
                            ff, ffprobe = ensure_ffmpeg()
                        tmp = staging_root / (str(index) + "-" + uuid.uuid4().hex)
                        tmp.mkdir()
                        self._current_identity = info
                        self._current_release_feature = info.get("feature_hint") or ""
                        self._export_warnings = []
                        self._export_notices = []
                        try:
                            self.status = f"Organizing {index}/{len(releases)}: {out_name}"
                            export_started = time.monotonic()
                            if grouped:
                                self._export_release(
                                    folder, tmp, fmt, ff, ffprobe,
                                    source_files=files, disc_folders=members)
                            else:
                                self._export_release(folder, tmp, fmt, ff, ffprobe)
                            result["timings_seconds"]["encoding_copy"] = round(
                                time.monotonic() - export_started, 2)
                            result["warnings"].extend(self._export_warnings)
                            # Actual AudioChecker is performed before finalizing the release.
                            if not info["physical"] and self.enable_checker:
                                checker_started = time.monotonic()
                                try:
                                    self.status = f"AudioChecker {index}/{len(releases)}: {out_name}"
                                    result["checker"] = native_check_web_release(
                                        tmp, fmt, ff, self.stop, self.checker_workers,
                                        release_name=target.name)
                                except InterruptedError:
                                    raise
                                except Exception as exc:
                                    result["warnings"].append("REQUIRED AudioChecker not completed: " + str(exc))
                                finally:
                                    result["timings_seconds"]["original_aucdetect"] = round(
                                        time.monotonic() - checker_started, 2)
                            output_files = collect_files(tmp)
                            if not output_files:
                                raise RuntimeError("No output generated.")
                            hashes = {str(fp.relative_to(tmp)).replace("\\", "/"): sha(fp)
                                      for fp in output_files}
                            if self.stop.is_set():
                                raise InterruptedError("Cancelled before committing release.")
                            if os.path.lexists(target):
                                raise RuntimeError("Target appeared during export; no overwrite.")
                            if manifest is None:
                                previous = read_json(UNDO, None)
                                if previous and previous.get("created"):
                                    HISTORY.mkdir(parents=True, exist_ok=True)
                                    archive = HISTORY / (time.strftime("%Y%m%d-%H%M%S") + "-" +
                                                         uuid.uuid4().hex[:8] + ".json")
                                    write_json(archive, previous)
                                manifest = {"version": VERSION, "source": str(source),
                                            "dest": str(dest), "format": fmt,
                                            "processing": {"encoding": self.enable_encoding,
                                                           "audiochecker": self.enable_checker,
                                                           "split_images": self.enable_splitting},
                                            "created": [], "pending": None}
                            rec = {"path": str(target), "files": hashes}
                            manifest["pending"] = rec
                            write_json(UNDO, manifest)
                            os.rename(tmp, target)
                            manifest["created"].append(rec)
                            manifest["pending"] = None
                            write_json(UNDO, manifest)
                            reasons = completion_reasons(
                                info, self._export_warnings, result["checker"],
                                checker_enabled=self.enable_checker)
                            result["unmet_requirements"] = reasons
                            if reasons:
                                result["status"] = "partial"
                                self.counts["partial"] += 1
                                result["warnings"].extend(x for x in reasons if x not in result["warnings"])
                                result["message"] = (str(len(output_files)) +
                                    " output files; PARTIAL: " + "; ".join(reasons))
                            else:
                                result["status"] = "done"
                                self.counts["done"] += 1
                                result["message"] = str(len(output_files)) + " verified output files."
                            if self._export_notices:
                                result["message"] += " | " + " | ".join(self._export_notices)
                        finally:
                            self._current_identity = None
                            if tmp.exists():
                                shutil.rmtree(tmp)
                except InterruptedError:
                    self.status = "Cancelled. Committed output remains Undo-able."
                    self.info(self.status)
                    break
                except Exception as exc:
                    result["status"], result["message"] = "failed", str(exc)
                    self.counts["failed"] += 1
                finally:
                    result["timings_seconds"]["total_release"] = round(
                        time.monotonic() - started_release, 2)
                    if result["status"]:
                        if result["timings_seconds"].get("total_release", 0) >= 2:
                            self.info("PERFORMANCE: " + source_name + " | " +
                                " | ".join(k + "=" + str(v) + "s" for k, v in
                                         result["timings_seconds"].items()))
                        self.counts["warnings"] += len(result["warnings"])
                        with self.lock:
                            self.results.append(result)
                            self.progress = round(index * 100 / len(releases))
                        self.info(result["status"].upper() + ": " + source_name +
                                  " -> " + result["target"] + " - " + result["message"] +
                                  ((" | " + "; ".join(result["warnings"])) if result["warnings"] else ""))
                    write_json(REPORT, {"version": VERSION, "source": str(source),
                                        "output": str(dest), "format": fmt,
                                        "processing": {"encoding": self.enable_encoding,
                                                       "audiochecker": self.enable_checker,
                                                       "split_images": self.enable_splitting},
                                        "counts": dict(self.counts), "releases": list(self.results)})
            if not self.stop.is_set():
                self.status = ("Finished: " + str(self.counts["done"]) + " compliant, " +
                               str(self.counts["partial"]) + " partially organized, " +
                               str(self.counts["skipped"]) + " existing, " +
                               str(self.counts["failed"]) + " failed.")
                self.info(self.status)
        except Exception as exc:
            self.error = str(exc)
            self.status = "Batch failed: " + str(exc)
            self.info("BATCH ERROR: " + traceback.format_exc()[-3500:])
        finally:
            if staging_root is not None and staging_root.exists():
                try:
                    shutil.rmtree(staging_root)
                except Exception as exc:
                    self.info("Staging cleanup error: " + str(exc))
            with self.lock:
                self.running = False

    def _export_release(self, source, dest, fmt, ff, ffprobe,
                        source_files=None, disc_folders=()):
        files = list(source_files) if source_files is not None else collect_files(source)
        def output_subdir(parent):
            for position, disc in enumerate(disc_folders, 1):
                if parent == disc or disc in parent.parents:
                    return Path(f"CD {position}") / parent.relative_to(disc)
            if disc_folders:
                raise RuntimeError("Grouped CD file escaped its source disc folder.")
            return parent.relative_to(source)
        cues = [p for p in files if p.suffix.lower() == ".cue"]
        ident = getattr(self, "_current_identity", None)
        if ident:
            cat = ident.get("catalog") or ident.get("barcode") or ""
            physical = bool(ident.get("physical"))
            valid_cat = bool(ident.get("metadata_verified") and cat)
        else:
            cat, physical = catalog(source.name)
            valid_cat = bool(cat and cat.lower() != "none")
        extension = ".m4a" if fmt == "ALAC" else ".flac"
        # A single CUE image must be handled as indivisible input.
        images, by_cue, source_images = [], {}, set()
        unsafe_images = []
        for cp in cues:
            parsed = cue_read(cp)
            for rec in parsed["records"]:
                if rec["file"]:
                    linked = (cp.parent / rec["file"].replace("\\", os.sep)).resolve()
                    if source != linked and source not in linked.parents:
                        raise RuntimeError("CUE audio escapes release folder: " + rec["file"])
            try:
                found = cue_image_plan(cp, parsed)
            except ValueError as exc:
                if str(exc) != "INDEX00 occurs after INDEX01 in the same audio file.":
                    raise
                # Cannot correct impossible pregap positions without evidence.
                # Preserve the whole image, do not invent an INDEX value or
                # generate incorrectly cut track files.
                all_refs = {rec.get("file", "").replace("\\", "/").casefold()
                            for rec in parsed["records"]}
                if len(all_refs) != 1 or not parsed["records"]:
                    raise
                common = parsed["records"][0]["file"]
                original = (cp.parent / common.replace("\\", os.sep)).resolve()
                if original.is_symlink() or not original.is_file() or original.suffix.lower() not in AUDIO:
                    raise
                if source != original and source not in original.parents:
                    raise
                unsafe_images.append((cp, original))
                source_images.add(original)
                continue
            if found:
                if len(found) != 1 or len(found[0][1]) != len(parsed["records"]):
                    raise RuntimeError("Mixed/split image CUE needs manual review.")
                by_cue[cp] = (parsed, found[0])
                images.append((cp, parsed, found[0]))
                source_images.add(found[0][0].resolve())
        mapping = {}
        new_cues = {}
        for cp, src_img in unsafe_images:
            if src_img in mapping:
                raise RuntimeError("Cannot preserve the same malformed CUE image twice.")
            target = dest / output_subdir(src_img.parent) / src_img.name
            if os.path.lexists(target):
                raise RuntimeError("Malformed CUE preservation destination collision.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_img, target)
            if sha(src_img) != sha(target):
                raise RuntimeError("Malformed CUE image copy differs from original.")
            mapping[src_img] = target
            getattr(self, "_export_warnings", []).append(
                cp.name + ": INDEX 00 occurs after INDEX 01 in the same source file. "
                "Original image and index data preserved; unsafe splitting skipped.")
        for cp, parsed, (src_img, records, ticks) in images:
            if not getattr(self, "enable_splitting", True):
                # Retain one source album image: the CUE indexes remain intact.
                src_img = src_img.resolve(strict=True)
                if src_img in mapping:
                    continue
                ext = extension if getattr(self, "enable_encoding", True) else src_img.suffix
                output = dest / output_subdir(src_img.parent) / (
                    _sanitize_windows_name(src_img.stem) + ext)
                if os.path.lexists(output):
                    raise RuntimeError("Whole image destination exists: " + str(output))
                output.parent.mkdir(parents=True, exist_ok=True)
                info = probe(ffprobe, src_img)
                codec = str(info.get("codec_name", "")).lower()
                if codec not in _MB_LOSSLESS:
                    raise RuntimeError("Whole album image is not lossless: " + str(src_img))
                if (not getattr(self, "enable_encoding", True) or
                    (fmt == "FLAC" and codec == "flac") or
                    (fmt == "ALAC" and codec == "alac")):
                    shutil.copy2(src_img, output)
                    if sha(src_img) != sha(output):
                        raise RuntimeError("Whole album image copy hash mismatch.")
                else:
                    convert(ff, src_img, output, fmt, ffprobe=ffprobe)
                mapping[src_img] = output
                getattr(self, "_export_notices", []).append(
                    "Album image kept whole: " + src_img.name)
                continue
            info = probe(ffprobe, src_img)
            rate = int(info.get("sample_rate", 0))
            if rate <= 0 or rate % 75:
                raise RuntimeError("Sample rate cannot represent 75fps CUE boundaries exactly.")
            if str(info.get("codec_name", "")).lower() not in {"alac", "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "ape", "wavpack", "pcm_s16be", "pcm_s24be", "pcm_s32be"}:
                raise RuntimeError("Audio image is not verified lossless: " + str(info.get("codec_name", "")))
            sub = output_subdir(cp.parent)
            if src_img.resolve() not in source_images:
                raise RuntimeError("Image mapping invalid.")
            names = {}
            image_jobs = []
            seen_image_names = set()
            for k, rec in enumerate(records):
                start_sample = ticks[k] * (rate // 75)
                end_sample = ticks[k + 1] * (rate // 75) if k + 1 < len(records) else None
                new_title, new_artist = normalize_artist_title(
                    rec["title"], getattr(self, "_artist_root", ""),
                    getattr(self, "_artist_root", ""),
                    getattr(self, "_credit_consensus", None))
                name = track_filename(rec["number"], new_title, extension)
                output = dest / sub / name
                if str(output).casefold() in seen_image_names or os.path.lexists(output):
                    raise RuntimeError("Image tracks map to the same output path: " + str(output))
                seen_image_names.add(str(output).casefold())
                image_jobs.append((rec, name, output, start_sample, end_sample, k,
                                   new_title, new_artist))
            def encode_image(job):
                rec, name, output, start_sample, end_sample, k, new_title, new_artist = job
                if self.stop.is_set():
                    raise InterruptedError("Cancelled.")
                convert(ff, src_img, output, fmt, start_sample, end_sample,
                        new_title, rec["number"], artist=new_artist, ffprobe=ffprobe)
                return rec["number"], name, k
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, int(self.encoding_workers))) as pool:
                futures = [pool.submit(encode_image, job) for job in image_jobs]
                for future in concurrent.futures.as_completed(futures):
                    num, name, k = future.result()
                    names[num] = (name, ticks[k], ticks[k + 1] if k + 1 < len(ticks) else None)
            new_cues[cp] = image_cue_text(parsed, names)
        # Prepare all ordinary tracks before encoding. Duplicate target names
        # are verified against decoded PCM; never drop different audio.
        audio_jobs = {}
        ordinary_sources = [x for x in files if x.suffix.lower() in AUDIO
                            and x.resolve() not in source_images]
        for fp in files:
            if self.stop.is_set():
                raise InterruptedError("Encoding cancelled.")
            if fp.resolve() in source_images or fp.suffix.lower() not in AUDIO:
                continue
            number, title, artist = extract_number_title(
                fp, getattr(self, "_artist_root", ""),
                getattr(self, "_credit_consensus", None))
            if number <= 0 or not title:
                raise RuntimeError("Track has no valid number/title: " + str(fp))
            # A release-folder (ft. X) belongs on its single *song*, never on
            # the folder. Propagate only if the same one-track recording lacks
            # that explicitly supplied credit; never add it to all EP tracks.
            released_feature = getattr(self, "_current_release_feature", "")
            info = getattr(self, "_current_identity", {}) or {}
            if (released_feature and len(ordinary_sources) == 1 and
                    not _song_parts(title)[3] and
                    _song_key(title) == _song_key(info.get("title", ""))):
                title = normalized_title(title + " (ft. " + released_feature + ")")
                root_artist = getattr(self, "_artist_root", "")
                if root_artist and (
                        artist.casefold() == (
                            root_artist + " & " + released_feature).casefold() or
                        artist.casefold() == (
                            root_artist + " feat. " + released_feature).casefold()):
                    artist = root_artist
            output_ext = extension if getattr(self, "enable_encoding", True) else fp.suffix
            dst = dest / output_subdir(fp.parent) / track_filename(number, title, output_ext)
            audio_jobs.setdefault(str(dst).casefold(), (dst, []) )[1].append(
                (fp, number, title, artist))
        jobs = list(audio_jobs.values())

        def encode_normal(entry):
            dst, variants = entry
            if os.path.lexists(dst):
                raise RuntimeError("Audio target exists: " + str(dst))
            src, number, title, artist = variants[0]
            dst.parent.mkdir(parents=True, exist_ok=True)
            source_info = probe(ffprobe, src)
            codec = str(source_info.get("codec_name", "")).lower()
            if codec not in _MB_LOSSLESS:
                raise RuntimeError("Refusing to encode non-lossless audio: " + str(src))
            encode = getattr(self, "enable_encoding", True)
            if (not encode or
                (fmt == "ALAC" and codec == "alac") or
                (fmt == "FLAC" and codec == "flac")):
                shutil.copy2(src, dst)
                if sha(src) != sha(dst):
                    raise RuntimeError("Source copy hash mismatch: " + str(src))
                try:
                    _update_copied_track_tags(dst, title, artist, number)
                except Exception as exc:
                    if encode or src.suffix.casefold() in (".flac", ".m4a"):
                        raise
                    getattr(self, "_export_warnings", []).append(
                        "Copied " + src.name + " without updated tags; container "
                        "does not safely support them: " + str(exc)[:180])
            else:
                convert(ff, src, dst, fmt, title=title, number=number,
                        artist=artist, ffprobe=ffprobe)
            # Additional files mapping to this name are discarded ONLY when
            # the decoded PCM equals the successfully verified first copy.
            for variant, _, _, _ in variants[1:]:
                if pcm_hash(ff, variant) != pcm_hash(ff, dst):
                    raise RuntimeError("Distinct tracks collide: " + str(variant) + " -> " + str(dst))
            return dst, [x[0].resolve() for x in variants]

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, int(self.encoding_workers))) as pool:
            futures = [pool.submit(encode_normal, job) for job in jobs]
            for future in concurrent.futures.as_completed(futures):
                completed, originals = future.result()
                for original in originals:
                    mapping[original] = completed
                if self.stop.is_set():
                    raise InterruptedError("Encoding cancelled.")
        # Preserve CUE text, rewriting only correct FILE paths; keep originals in source.
        destinations = set()
        unresolved_cues = set()
        for fp in files:
            if fp.resolve() in source_images or fp.suffix.lower() in AUDIO:
                continue
            if fp.name.casefold() == "audiochecker.log":
                # WEB: create a genuine fresh report against the output tracks.
                # CD: NEVER copy a WEB-only AudioChecker report into a physical
                # rip, even if one happens to exist in the source archive.
                # The original source sidecar is deliberately never altered.
                continue
            sub = output_subdir(fp.parent)
            if fp.suffix.lower() == ".cue":
                dest_name = cat + ".cue" if physical and valid_cat else fp.name
            elif fp.suffix.lower() == ".log" and fp.name.lower() != "audiochecker.log":
                dest_name = cat + ".log" if physical and valid_cat else fp.name
            elif fp.name.lower() == "audiochecker.log":
                dest_name = "audiochecker.log"
            else:
                dest_name = fp.name
            target = dest / sub / dest_name
            rel = str(target.relative_to(dest)).casefold()
            if rel in destinations or target.exists():
                raise RuntimeError("Supporting file destination collision: " + str(target))
            destinations.add(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if fp.suffix.lower() == ".cue":
                if fp in new_cues:
                    rewritten = new_cues[fp]
                else:
                    cue = cue_read(fp)
                    original_refs = [r["file"] for r in cue["records"] if r["file"]]
                    # A shared old WAV image is not recoverable from several
                    # already-split files without reliable pregap provenance.
                    # This is not a reason to throw away valid audio tracks.
                    is_shared = (len(original_refs) > 1 and
                                 len({x.casefold() for x in original_refs}) < len(original_refs))
                    is_absent = any(
                        not (fp.parent / x.replace(chr(92), os.sep)).is_file()
                        for x in original_refs)
                    if is_shared and is_absent:
                        rewritten, reason = reconstruct_missing_image_cue(cue, fp, mapping)
                        if rewritten:
                            target.write_text(rewritten, encoding="utf-8",
                                              newline="\r\n" if IS_WIN else "\n")
                            notice = (fp.name + ": playable split-track CUE rebuilt after "
                                      "sample-exact sector verification; original CUE untouched.")
                            getattr(self, "_export_notices", []).append(notice)
                            self.info(notice)
                            continue
                        if not any(path.parent == fp.parent for path in mapping):
                            raise RuntimeError(
                                "Original shared CUE WAV missing and no surviving split tracks.")
                        unresolved_cues.add(fp)
                        fallback = target.parent / ("Unresolved CUE - " + fp.stem + ".cue")
                        if os.path.lexists(fallback):
                            raise RuntimeError("Unresolved CUE filename collision.")
                        shutil.copy2(fp, fallback)
                        if sha(fp) != sha(fallback):
                            raise RuntimeError("CUE evidence copy differs from original.")
                        getattr(self, "_export_warnings", []).append(
                            fp.name + ": old full-image WAV absent; cannot safely rewrite "
                            "CUE for individual tracks: " + reason +
                            ". Original is retained as documentary CUE only.")
                        continue
                    refs = {}
                    unresolved_reason = None
                    for rec in cue["records"]:
                        if not rec["file"]:
                            raise RuntimeError("CUE missing FILE for track " + str(rec["number"]))
                        key = rec["file"].replace("\\", "/").casefold()
                        src_audio = (fp.parent / rec["file"].replace("\\", os.sep)).resolve()
                        if src_audio not in mapping:
                            # Old CD CUE may still name the former WAV even when
                            # it has been split/converted to numbered FLAC files.
                            # Match a unique track by its actual track number.
                            matches = []
                            for old_audio, new_audio in mapping.items():
                                num, _, _ = extract_number_title(old_audio)
                                if num == rec["number"] and old_audio.parent == fp.parent:
                                    matches.append(new_audio)
                            if len(set(matches)) != 1:
                                raise RuntimeError("Cannot uniquely remap legacy CUE FILE: " + rec["file"])
                            if len({r["file"].casefold() for r in cue["records"] if r["file"]}) < len(cue["records"]):
                                unresolved_reason = (
                                    "CUE references an absent shared album image. "
                                    "Exact INDEX00 and pregap distribution cannot be proven "
                                    "from split tracks. Audio preserved, original CUE quarantined.")
                                break
                            if rec["index"].get(1, -1) != 0:
                                unresolved_reason = "Legacy CUE index offsets cannot be verified against separate audio."
                                break
                            refs[key] = os.path.relpath(matches[0], target.parent).replace("/", "\\")
                        else:
                            refs[key] = os.path.relpath(mapping[src_audio], target.parent).replace("/", "\\")
                    if unresolved_reason:
                        unresolved_cues.add(fp)
                        fallback = target.parent / ("Unresolved CUE - " + fp.stem + ".cue")
                        if os.path.lexists(fallback):
                            raise RuntimeError("Cannot preserve original unresolved CUE due to name collision.")
                        shutil.copy2(fp, fallback)
                        if sha(fp) != sha(fallback):
                            raise RuntimeError("Unresolved original CUE copy changed.")
                        getattr(self, "_export_warnings", []).append(
                            fp.name + ": " + unresolved_reason)
                        continue
                    rewritten = normal_cue_text(cue, refs)
                target.write_text(rewritten, encoding="utf-8", newline="\r\n" if IS_WIN else "\n")
            else:
                shutil.copy2(fp, target)
                if sha(fp) != sha(target):
                    raise RuntimeError("Copied support file differs: " + str(fp))
        # Ensure physical cue(s) and logs preserved or explicitly absent.
        for fp in files:
            if fp.suffix.lower() == ".cue" and fp not in unresolved_cues and not (dest / output_subdir(fp.parent) / (cat + ".cue" if physical and valid_cat else fp.name)).is_file():
                raise RuntimeError("A CUE was lost during export.")


    def download_artwork(self, directory):
        """Scan releases already in Destination; add only missing cover JPGs."""
        with self.lock:
            if self.running:
                return {"error": "Another operation is running."}
            try:
                root = Path(str(directory).strip().strip('"')).expanduser().resolve(strict=True)
                if not root.is_dir() or root.is_symlink():
                    raise ValueError("Destination must be an existing normal folder")
                releases = discover(root)
                if not releases:
                    raise ValueError("No releases with audio found in Destination")
                if any(p.is_symlink() or (p != root and root not in p.parents)
                       for p in releases):
                    raise ValueError("Unsafe release folder")
                next_log = _new_log("artwork")
            except (ValueError, OSError) as exc:
                return {"error": str(exc)}
            self.log_path = next_log
            self.dest = str(root)
            self.mode = "artwork"
            self.running, self.error, self.progress = True, "", 0
            self.status = "Finding release artwork..."
            self.stop.clear()
            self.counts = {"total": len(releases), "done": 0, "partial": 0,
                           "skipped": 0, "failed": 0, "warnings": 0}
            self.results, self.activity = [], []
        self.info("ARTWORK START: " + str(root) +
                  " | JPEGs directly in Destination; "
                  "Apple UPC maximum JPG; Spotify fallback by exact MusicBrainz release")
        threading.Thread(target=self._download_artwork, args=(root, releases),
                         daemon=True).start()
        return {"ok": True}

    def _download_artwork(self, root, releases):
        manifest = {"root": str(root), "created": [],
                    "layout": "destination-root", "kind": "artwork",
                    "started": datetime.now().isoformat()}
        try:
            saved_journal = _cover_archive_prior_undo()
            if saved_journal:
                self.info("ARTWORK PRIOR UNDO SAVED: " + str(saved_journal))
            write_json(ARTWORK_UNDO, manifest)
            # A flat artwork directory cannot safely represent same-named
            # releases from different subfolders. Reject every ambiguous
            # basename rather than arbitrarily assigning one cover.
            from collections import Counter
            title_counts = Counter(folder.name.casefold() for folder in releases)
            for index, folder in enumerate(releases, 1):
                if self.stop.is_set():
                    self.status = "Artwork cancelled; created covers can be undone."
                    break
                row = {"name": folder.name, "source": str(folder),
                       "target": str(root), "status": "",
                       "message": "", "warnings": []}
                try:
                    if folder.is_symlink() or folder.resolve(strict=True) != folder:
                        raise ValueError("Release directory changed or is symlinked")
                    if title_counts[folder.name.casefold()] > 1:
                        row["status"] = "partial"
                        row["message"] = ("Duplicate release folder names cannot share "
                                          "one artwork filename in Destination")
                        self.counts["partial"] += 1
                        continue
                    target = root / (folder.name + ".jpg")
                    if os.path.lexists(target):
                        row["status"] = "skipped"
                        row["message"] = "Cover already exists; not overwritten"
                        self.counts["skipped"] += 1
                        continue
                    code = _cover_release_barcode(folder)
                    if not _valid_gtin(code):
                        row["status"] = "partial"
                        row["message"] = "No unique verified UPC/EAN from folder, audio tags or exact MusicBrainz catalog"
                        self.counts["partial"] += 1
                        continue
                    match = _RELEASE_PARTS.fullmatch(folder.name)
                    title = match.group("title") if match else folder.name
                    self.status = f"Artwork {index}/{len(releases)}: {folder.name}"
                    cover, notes = _cover_apple(code, title, self.stop)
                    if not cover:
                        self.info("APPLE MISS: " + folder.name + " | " + "; ".join(notes[-2:]))
                        cover, spotify_notes = _cover_spotify(code, title, self.stop)
                        if not cover:
                            row["status"] = "partial"
                            row["message"] = "Neither Apple nor exact-match Spotify artwork available"
                            row["warnings"] = (notes[-2:] + spotify_notes[-2:])
                            self.counts["partial"] += 1
                            continue
                    if self.stop.is_set():
                        raise InterruptedError("Artwork cancelled")
                    image, written = _cover_save(root, folder, cover["bytes"], manifest)
                    row["status"] = "done" if written else "skipped"
                    row["message"] = (cover["provider"] + " " + cover["size"] + " JPG"
                                      if written else "Cover was already created")
                    row["artwork_source"] = str(cover["link"])
                    self.counts["done" if written else "skipped"] += 1
                    self.info("ARTWORK " + row["status"].upper() + ": " +
                              str(image) + " | " + row["message"] +
                              " | " + str(cover["link"]))
                except InterruptedError:
                    self.status = "Artwork cancelled; completed covers remain Undo-able."
                    break
                except Exception as exc:
                    row["status"], row["message"] = "failed", str(exc)[:350]
                    self.counts["failed"] += 1
                    self.info("ARTWORK FAILED: " + folder.name + " | " +
                              traceback.format_exc()[-1800:])
                finally:
                    self.counts["warnings"] += len(row["warnings"])
                    if row["status"]:
                        with self.lock:
                            self.results.append(row)
                    with self.lock:
                        self.progress = round(index * 100 / len(releases))
            if not self.stop.is_set():
                self.status = (f"Artwork complete: {self.counts['done']} added, "
                               f"{self.counts['skipped']} existing, "
                               f"{self.counts['partial']} unavailable, "
                               f"{self.counts['failed']} failed.")
                self.info(self.status)
        except Exception as exc:
            self.error = str(exc)
            self.status = "Artwork batch failed: " + str(exc)
            self.info("ARTWORK ERROR: " + traceback.format_exc()[-2000:])
        finally:
            with self.lock:
                self.running = False

    def _undo_artwork(self, manifest):
        try:
            root = Path(manifest["root"]).resolve(strict=True)
            files = manifest.get("created", [])
            # Support old v0.3.18 nested-artwork journals without deleting
            # their releases; new journals cover flat Destination-root JPGs.
            flat = manifest.get("layout") == "destination-root"
            if manifest.get("layout") not in (None, "destination-root"):
                raise RuntimeError("Unknown Artwork Undo journal layout")
            # Validate every filename and hash before unlinking anything.
            for entry in files:
                image = Path(entry["path"])
                resolved = image.resolve(strict=True) if image.is_file() else image
                symlink_parent = any(p.is_symlink() for p in image.parents
                                     if p == root or root in p.parents)
                valid_location = (image.parent == root and
                                  image.name == entry.get("release_name", "") + ".jpg"
                                  if flat else
                                  root in image.parents and
                                  image.name == image.parent.name + ".jpg")
                if (not image.is_file() or image.is_symlink() or symlink_parent or
                        not valid_location or
                        not resolved.is_relative_to(root) or
                        sha(image) != entry["sha256"]):
                    raise RuntimeError("Cover changed since download; Undo blocked: " +
                                       str(image))
            for entry in reversed(files):
                Path(entry["path"]).unlink()
            ARTWORK_UNDO.unlink(missing_ok=True)
            self.status = "Artwork Undo complete; audio and existing covers untouched."
            self.info(self.status)
        except Exception as exc:
            self.error = str(exc)
            self.status = "Artwork Undo blocked: " + str(exc)
            self.info("ARTWORK UNDO ERROR: " + traceback.format_exc()[-1800:])
        finally:
            with self.lock:
                self.running = False

    def undo(self):
        with self.lock:
            if self.running:
                return {"error": "Job is busy."}
            art_is_latest = _cover_undo_latest()
            journal = read_json(ARTWORK_UNDO if art_is_latest else UNDO, None)
            if not journal:
                return {"error": "No prior operation to undo."}
            try:
                next_log = _new_log("undo")
            except OSError as exc:
                return {"error": "Cannot create a new Undo log: " + str(exc)}
            self.log_path = next_log
            self.activity = []
            self.running, self.error = True, ""
            self.status = "Checking Undo safety..."
            self.stop.clear()
        self.info("UNDO START: verifying prior output before removing it.")
        worker = self._undo_artwork if art_is_latest else self._undo
        threading.Thread(target=worker, args=(journal,), daemon=True).start()
        return {"ok": True}

    def _undo(self, manifest):
        try:
            targets = list(manifest.get("created", []))
            pending = manifest.get("pending")
            if pending:
                # A crash between rename and manifest commit must not silently destroy data.
                raise RuntimeError("An interrupted Apply has a pending entry. Inspect its destination before Undo.")
            for record in targets:
                folder = Path(record["path"])
                if not folder.is_dir() or folder.is_symlink():
                    raise RuntimeError("Output folder changed/missing: " + str(folder))
                actual = collect_files(folder)
                recorded = record["files"]
                actual_keys = {str(p.relative_to(folder)).replace("\\", "/") for p in actual}
                if actual_keys != set(recorded):
                    raise RuntimeError("Output contents changed; Undo blocked: " + str(folder))
                for p in actual:
                    key = str(p.relative_to(folder)).replace("\\", "/")
                    if sha(p) != recorded[key]:
                        raise RuntimeError("Output file modified; Undo blocked: " + str(p))
            # No mutation until all outputs validated.
            for record in reversed(targets):
                shutil.rmtree(record["path"])
            UNDO.unlink(missing_ok=True)
            self.status = "Undo complete. Original sources were never modified."
            self.info(self.status)
        except Exception as e:
            self.error, self.status = str(e), "Undo blocked: " + str(e)
            self.info("UNDO ERROR: " + traceback.format_exc()[-2000:])
        finally:
            with self.lock:
                self.running = False



class DesktopBridge:
    """Minimal js_api facade. Do not expose Organizer, its WebView2 window,
    threading.Event, locks or background executor objects to API reflection.
    pywebview recursively inspects public js_api attributes and nested members.
    """
    def __init__(self, organizer):
        self._organizer = organizer

    def state(self):
        return self._organizer.state()

    def browse(self, which):
        return self._organizer.browse(which)

    def open_folder(self, path):
        return self._organizer.open_folder(path)

    def open_log(self):
        return self._organizer.open_log()

    def open_log_folder(self):
        return self._organizer.open_log_folder()

    def organize(self, source, dest, fmt, encoding_workers, checker_workers,
                 enable_encoding=True, enable_checker=True, enable_splitting=True):
        return self._organizer.organize(
            source, dest, fmt, encoding_workers, checker_workers,
            enable_encoding, enable_checker, enable_splitting)

    def download_artwork(self, folder):
        return self._organizer.download_artwork(folder)

    def cancel(self):
        return self._organizer.cancel()

    def undo(self):
        return self._organizer.undo()

    def diagnostics(self, message):
        try:
            self._organizer.info("UI: " + str(message)[:850])
            return {"ok": True}
        except Exception:
            return {"error": "Could not write UI diagnostic."}


def _startup_logger():
    """Persist GUI/bridge initialization errors instead of a black silent window."""
    ensure_data()
    logger = logging.getLogger("webview")
    logger.setLevel(logging.INFO)
    destination = str(_new_log("startup"))
    for old_handler in list(logger.handlers):
        if isinstance(old_handler, logging.FileHandler):
            logger.removeHandler(old_handler)
            old_handler.close()
    handler = logging.FileHandler(destination, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _session_profile():
    """Per-launch isolated profile avoids reuse of a crashed WebView2 database."""
    root = DATA / "webview2-sessions"
    root.mkdir(parents=True, exist_ok=True)
    active = root / ("session-" + uuid.uuid4().hex)
    active.mkdir()
    now = time.time()
    for folder in root.glob("session-*"):
        if folder == active or not folder.is_dir() or folder.is_symlink():
            continue
        try:
            # Only cleanup this app's prior closed/stale sessions. A locked
            # orphaned renderer keeps its files until a subsequent launch.
            if now - folder.stat().st_mtime > 90:
                shutil.rmtree(folder)
        except OSError:
            pass
    return active


def _is_mlo_window_title(title):
    """Match only the actual versioned MLO GUI title, not unrelated windows."""
    return bool(re.fullmatch(
        re.escape(APP) + r" v\d+\.\d+\.\d+ - Under construction .*",
        str(title or ""), flags=re.IGNORECASE))


def _activate_existing_mlo_window():
    """Show the existing GUI on Windows without disturbing its running job."""
    if not IS_WIN:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    hwnd_type = ctypes.c_void_p
    enum_type = ctypes.WINFUNCTYPE(ctypes.c_bool, hwnd_type, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [enum_type, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool
    user32.GetWindowTextLengthW.argtypes = [hwnd_type]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [hwnd_type, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [hwnd_type]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.ShowWindow.argtypes = [hwnd_type, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [hwnd_type]
    user32.SetForegroundWindow.restype = ctypes.c_bool
    user32.FlashWindow.argtypes = [hwnd_type, ctypes.c_bool]
    selected = []
    @enum_type
    def visitor(hwnd, _unused):
        if not user32.IsWindowVisible(hwnd):
            return True
        size = user32.GetWindowTextLengthW(hwnd)
        if not size or size > 512:
            return True
        title_buffer = ctypes.create_unicode_buffer(size + 1)
        if not user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer)):
            return True
        if _is_mlo_window_title(title_buffer.value):
            selected.append(hwnd)
            return False
        return True
    user32.EnumWindows(visitor, None)
    if not selected:
        return False
    hwnd = selected[0]
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE (minimized window included)
    if not user32.SetForegroundWindow(hwnd):
        # Windows may refuse focus changes from another process.
        user32.FlashWindow(hwnd, True)
    return True


def _single_instance():
    """Prevent shared-settings/Undo collisions; gently redirect second launch."""
    if not IS_WIN:
        return None
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                     ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(
        None, False, "Local\\Karpuzikov.MusicLibraryOrganizer")
    error = kernel32.GetLastError()
    if not handle:
        raise OSError("Could not initialize the Music Library Organizer instance lock "
                      "(Windows error " + str(error) + ").")
    if error == 183:  # ERROR_ALREADY_EXISTS; another process holds the lock
        kernel32.CloseHandle(handle)
        for _ in range(24):
            try:
                if _activate_existing_mlo_window():
                    return False  # normal, successful duplicate-launch exit
            except (OSError, AttributeError, TypeError, ValueError):
                # Unsupported/inaccessible window API: show a concise native
                # notice rather than a new Python traceback.
                break
            time.sleep(0.20)  # the other instance may still be starting
        # A live lock without a GUI means a hidden or hung process. Do not
        # delete another process's mutex, launch into shared state, or kill it.
        ctypes.windll.user32.MessageBoxW(
            None,
            APP + " is running or starting, but its window is not available.\n"
            "Wait for it to open; if it stays hidden, close its process in Task Manager.",
            APP, 0x40)
        return False
    return handle



HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Music Library Organizer</title>
<style>
:root{color-scheme:dark;--bg:#0b0c0f;--panel:#11141a;--field:#181c23;--line:#2a303a;--text:#f3f6fb;--muted:#9ca7b8;--blue:#66a9ff;--yellow:#f59e0b;--green:#22c55e;--red:#ef4444}
*{box-sizing:border-box}html,body{height:100%;width:100%;margin:0;overflow:hidden;background:var(--bg);color:var(--text);font:13px "Segoe UI Variable","Segoe UI",system-ui,sans-serif}body{display:flex;flex-direction:column}
button,input,select{font:inherit}button{user-select:none}.processing-toggles{display:flex;flex-wrap:wrap;gap:8px 20px;padding:7px 0 11px}.processing-check{display:inline-flex;align-items:center;gap:8px;font-weight:650;cursor:pointer;min-height:25px}.processing-check input{width:16px;height:16px;accent-color:var(--blue);cursor:pointer}.processing-check input:disabled{cursor:default}.processing-check:focus-within{outline:2px solid #8cc8ff;outline-offset:3px;border-radius:3px}
#top{height:58px;flex:0 0 58px;display:flex;align-items:center;gap:10px;padding:0 18px;background:#101319;border-bottom:1px solid var(--line)}
#brand{font-size:18px;font-weight:780}.muted{color:var(--muted);font-size:11px}.space{flex:1}
main{display:grid;grid-template-rows:auto auto minmax(130px,1fr);gap:12px;flex:1;min-height:0;overflow:hidden;padding:12px 18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;min-width:0}
.head{padding:10px 13px;display:flex;align-items:baseline;gap:12px}.head strong{font-size:14px}
.form{padding:0 13px 12px}.inputs-form{padding-top:12px}.label{display:block;font-weight:650;margin:2px 0 5px}
.path{display:grid;grid-template-columns:34px minmax(0,1fr) 85px;gap:7px;margin-bottom:9px}
input[type=text]{width:100%;height:34px;border:1px solid #37404d;background:var(--field);border-radius:8px;padding:0 10px;color:var(--text);outline:none}
button,select{height:34px;border:1px solid #3a4351;background:#1b2028;color:var(--text);border-radius:8px;padding:0 11px;cursor:pointer}
button:hover:not(:disabled),select:hover:not(:disabled){background:#252c36;border-color:#596579}button:disabled{opacity:.38;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #8cc8ff;outline-offset:2px}
button.primary{background:#17324c;border-color:#3e78aa;color:#dff1ff;font-weight:720}button.primary:hover:not(:disabled){background:#1e4265;border-color:#66a9ff}
button.folder{width:34px;padding:0}.options{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-top:1px solid #252b34;padding-top:10px}.options label{font-weight:650;margin-left:6px}select{min-width:72px}
#progress{padding:12px 14px}.progress-head{display:flex;align-items:center;justify-content:space-between;gap:12px;font-weight:650;min-height:17px}
.track{height:9px;background:#252d36;border-radius:30px;margin:11px 0;overflow:hidden}.fill{height:100%;width:0;background:var(--blue);transition:width .2s}
.counts{display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.counts b{font-size:16px;color:var(--text)}#detail{font-size:11px;color:#c2cddd;margin-top:8px}#detail:empty{display:none}
#results{min-height:0;display:flex;flex-direction:column;overflow:hidden}.tools{display:flex;align-items:center;gap:8px;padding:0 13px 10px}.tools input{max-width:280px}.tablewrap{flex:1;overflow:auto;min-height:50px}
.tablewrap, .tablewrap *{-webkit-user-select:text!important;user-select:text!important;cursor:text}.tablewrap button.result-folder{width:28px;height:28px;padding:0;margin-right:6px;vertical-align:middle;cursor:pointer;border-radius:6px}.result-path{display:flex;align-items:center;min-width:0}.result-path span{overflow-wrap:anywhere;min-width:0}table{border-collapse:collapse;table-layout:fixed;width:100%}th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #252b35;word-break:break-word}th{position:sticky;top:0;background:#171b22;color:var(--muted);font-size:11px}tr:hover{background:#19212a}
.done{color:#a8f5bf}.partial{color:#ffd88b}.skipped{color:#b6c4d2}.failed{color:#ffb0b6}.warning{color:#ffd88b}.small{color:var(--muted);font-size:11px;margin-top:3px}.empty{padding:25px;text-align:center;color:var(--muted)}
#foot{height:58px;flex:0 0 58px;background:#101319;border-top:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 18px}
#message{color:var(--yellow);max-width:42vw;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px}
.drag{outline:2px dashed var(--blue);outline-offset:-3px}
@media(max-width:750px){main{padding:8px;gap:8px}.counts{gap:9px}#top{padding:0 9px}#top .muted{display:none}#foot{padding:0 8px}th,td{padding:6px}}
</style></head><body>
<header id="top"><strong id="brand">Music Library Organizer</strong><span class="muted">v0.3.22 - Under construction ⚠️</span></header>
<main>
<section class="card"><div class="form inputs-form">
<label for="source" class="label">Source</label><div class="path" id="sourceDrop"><button class="folder" id="openSource" title="Open input folder" aria-label="Open input folder">📁</button><input type="text" id="source" aria-label="Source folder" autocomplete="off" spellcheck="false"><button id="browseSource">Browse...</button></div>
<label for="dest" class="label">Destination</label><div class="path" id="destDrop"><button class="folder" id="openDest" title="Open output folder" aria-label="Open output folder">📁</button><input type="text" id="dest" aria-label="Destination folder" autocomplete="off" spellcheck="false"><button id="browseDest">Browse...</button></div>
<div class="processing-toggles" role="group" aria-label="Optional processing stages"><label class="processing-check" title="Convert ordinary lossless tracks to the selected output format"><input type="checkbox" id="doEncode" checked> Encode audio</label><label class="processing-check" title="Analyze digital releases with the original auCDtect engine"><input type="checkbox" id="doChecker" checked> AudioChecker</label><label class="processing-check" title="Split cue-linked album images into tracks"><input type="checkbox" id="doSplit" checked> Split CUE images</label></div>
<div class="options"><label for="format">Format</label><select id="format" aria-label="Output format"><option value="ALAC">ALAC (.m4a)</option><option value="FLAC">FLAC (.flac)</option></select>
<label for="encodeWorkers">Encode workers</label><select id="encodeWorkers" aria-label="Number of concurrent encodes, including CUE album-image splitting"></select>
<label for="checkWorkers">Check workers</label><select id="checkWorkers" aria-label="Number of simultaneous AudioChecker workers"></select>
</div></div></section>
<section class="card" id="progress"><div class="progress-head"><span id="status" role="status" aria-live="polite">Connecting to desktop...</span><span id="percent">0%</span></div><div class="track" id="progressBar" role="progressbar" aria-label="Organization progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="fill" id="fill"></div></div>
<div class="counts"><span><b id="total">0</b> <span id="totalLabel">found</span></span><span><b id="done">0</b> <span id="doneLabel">compliant</span></span><span><b id="partial">0</b> <span id="partialLabel">partial</span></span><span><b id="skipped">0</b> <span id="skippedLabel">existing</span></span><span><b id="failed">0</b> failed</span><span><b id="warnings">0</b> warnings</span></div>
<div id="detail" role="status" aria-live="polite"></div></section>
<section class="card" id="results"><div class="head"><strong>Results</strong></div><div class="tools"><input type="text" id="query" placeholder="Filter results..." aria-label="Filter results"><button id="openLog">↗ Open log</button><button id="openLogFolder" title="Open folder containing organizer logs" aria-label="Open log folder">📁</button><button id="copyResults" title="Copy visible results to clipboard">Copy</button><span class="space"></span><span id="resultCount" class="muted">Not started</span></div>
<div class="tablewrap"><table><thead><tr><th style="width:14%">Result</th><th style="width:31%">Release</th><th>Details</th></tr></thead><tbody id="rows"><tr><td colspan="3" class="empty">No results</td></tr></tbody></table></div></section>
</main>
<footer id="foot"><button class="primary" id="organize" disabled>▶ Organize</button><button id="artwork" disabled title="Save missing release-cover JPEGs directly in Destination, named after release folders">▣ Artwork</button><button id="cancel" disabled>✕ Cancel</button><button id="undo" disabled title="Undo the most recent completed run">↶ Undo</button><button id="reconnect" style="display:none" title="Retry connecting to the local Python application">↻ Retry connection</button><span class="space"></span><span id="message" aria-live="polite" role="status"></span></footer>
<script>
let state=null,initialized=false,working=false,query="",suggestedDest="";
let pollBusy=false,bridgeFailures=0,nextRetry=0,connected=false,renderedResultMarkup=null;
const bootTime=Date.now();
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function bridgeMethod(method){
 const api=window.pywebview&&window.pywebview.api;
 return api&&typeof api[method]==="function"?api[method]:null;
}
async function api(method,...args){
 const fn=bridgeMethod(method);
 if(!fn)throw Error("Desktop connection is still initializing.");
 return fn(...args);
}
async function deadline(promise,ms){
 let timer;
 try {
  return await Promise.race([promise,new Promise((_,reject)=>{
   timer=setTimeout(()=>reject(Error("Desktop request timed out.")),ms);
  })]);
 }finally{clearTimeout(timer);}
}
function setBridgeState(ready){
 connected=ready;
 if(!ready){
  for(const id of ["organize","artwork","browseSource","browseDest","openLog","openLogFolder","copyResults","openSource","openDest","undo","cancel","doEncode","doChecker","doSplit"])$(id).disabled=true;
  if(!state)$('status').textContent="Connecting to desktop...";
  if(Date.now()-bootTime>8000){
   $('status').textContent="Connection unavailable";
   $('reconnect').style.display="";
   $('detail').textContent="Retry the desktop connection if it remains unavailable.";
  }
 }else{$('reconnect').style.display="none";}
}

function error(e){$('message').textContent=String(e&&e.message||e);}
async function call(name,...args){
 if(!connected){error("The desktop connection is not ready yet.");return null;}
 try{
  const out=await deadline(api(name,...args),30000);
  if(out&&out.error)throw Error(out.error);
  await refresh(true);
  return out;
 }catch(e){
  error(e);
  if(e&&String(e.message).includes("timed out")){
   $('message').textContent="Desktop did not answer. Operation might still be running; check Results before retrying.";
  }
  return null;
 }
}
function updateOrganizeAvailability(){
 const src=$('source').value.trim(),dst=$('dest').value.trim();
 $('organize').disabled=!connected||working||!src||!dst;
 $('organize').title=!src?"Choose a source folder":!dst?"Choose a destination folder":"Organize collection";
 $('artwork').disabled=!connected||working||!dst;
 $('artwork').title=dst?"Save release-named cover JPGs directly in Destination":"Choose Destination to download artwork";
}
function updateTaskControls(){
 const locked=!connected||working;
 for(const id of ['doEncode','doChecker','doSplit'])$(id).disabled=locked;
 $('encodeWorkers').disabled=locked||(!$('doEncode').checked&&!$('doSplit').checked);
 $('checkWorkers').disabled=locked||!$('doChecker').checked;
 $('format').disabled=locked||(!$('doEncode').checked&&!$('doSplit').checked);
}
function render(){
 if(!state)return;
 if(!initialized){
  $('source').value=state.source||"";
  $('dest').value=state.dest||suggestPath(state.source);
  suggestedDest=suggestPath(state.source);
  $('format').value=state.format||"ALAC";
  setupWorkers('encodeWorkers',state.worker_limit,state.encoding_workers);
  setupWorkers('checkWorkers',state.worker_limit,state.checker_workers);
  $('doEncode').checked=state.enable_encoding!==false;
  $('doChecker').checked=state.enable_checker!==false;
  $('doSplit').checked=state.enable_splitting!==false;
  initialized=true;
 }
 working=!!state.running;
 $('status').textContent=state.status||"Ready";
 const pct=Number(state.progress||0);$('percent').textContent=pct+"%";$('fill').style.width=pct+"%";$('progressBar').setAttribute("aria-valuenow",String(pct));
 let c=state.counts||{};for(let name of ["total","done","partial","skipped","failed","warnings"])$(name).textContent=c[name]||0;
 const isArtwork=state.mode==="artwork";
 $('totalLabel').textContent=isArtwork?"releases":"found";
 $('doneLabel').textContent=isArtwork?"downloaded":"compliant";
 $('partialLabel').textContent=isArtwork?"unavailable":"partial";
 $('skippedLabel').textContent=isArtwork?"covers exist":"existing";
 $('detail').textContent=state.error||"";
 for(let id of ["source","dest","browseSource","browseDest"])$(id).disabled=working;
 updateTaskControls();
 updateOrganizeAvailability();
 $('cancel').disabled=!working;$('undo').disabled=working||!state.undo;
 $('undo').textContent=state.undo_kind==="artwork"?"↶ Undo Artwork":"↶ Undo";
 $('openSource').disabled=!String($('source').value||"").trim();
 $('openDest').disabled=!String($('dest').value||"").trim();
 $('openLog').disabled=false;$('openLogFolder').disabled=false;$('copyResults').disabled=false;
 
 if(state.error)$('message').textContent=state.error;
 let list=(state.results||[]).filter(x=>[x.name,x.message,(x.warnings||[]).join(" ")].join(" ").toLowerCase().includes(query));
 $('resultCount').textContent=list.length+" shown / "+(state.results||[]).length+" processed";
 const shown=list.slice(-500);
 const markup=shown.map((x,i)=>'<tr><td class="'+esc(x.status)+'"><b>'+esc(x.status.toUpperCase())+'</b></td><td><b>'+esc(x.name)+'</b><div class="small result-path">'+(x.target?'<button type="button" class="folder result-folder" data-result-index="'+i+'" aria-label="Open release output folder" title="Open release output folder">📁</button>':'')+'<span>'+esc(x.target)+'</span></div></td><td>'+esc(x.message)+(x.warnings&&x.warnings.length?'<div class="small warning">'+esc(x.warnings.join(" | "))+'</div>':"")+'</td></tr>').join("")||'<tr><td colspan="3" class="empty">'+(working?"Working...":"No releases processed yet.")+'</td></tr>';
 // Status polling must NOT replace selected text nodes before Ctrl+C.
 const selection=window.getSelection&&window.getSelection();
 const selectingResults=selection&&!selection.isCollapsed&&$('rows').contains(selection.anchorNode);
 if(markup!==renderedResultMarkup&&!selectingResults){
  $('rows').innerHTML=markup;
  renderedResultMarkup=markup;
 }
}
async function refresh(force=false){
 if(pollBusy||(!force&&Date.now()<nextRetry))return;
 if(!bridgeMethod("state")){
  bridgeFailures++;
  nextRetry=Date.now()+1000;
  setBridgeState(false);
  return;
 }
 pollBusy=true;
 try{
  const response=await deadline(api("state"),8000);
  if(!response||typeof response!=="object")throw Error("Desktop returned invalid state.");
  state=response;bridgeFailures=0;nextRetry=0;
  setBridgeState(true);
  render();
  if(!state.error)$('message').textContent="";
 }catch(e){
  bridgeFailures++;
  nextRetry=Date.now()+Math.min(8000,1000*bridgeFailures);
  setBridgeState(false);
  if(Date.now()-bootTime>8000){
   $('message').textContent="Connection interrupted. Retrying automatically.";
  }
 }finally{pollBusy=false;}
}
function suggestPath(input){
 let v=String(input||"").replace(/[\\/]+$/,"");
 if(!v)return "";
 return v + (v.toLowerCase().endsWith(" - sorted")?" - Organized":" - Sorted");
}
function changedInput(){
 const proposed=suggestPath($('source').value);
 if(!$('dest').value.trim() || $('dest').value===suggestedDest){
  $('dest').value=proposed;
 }
 suggestedDest=proposed;
 updateOrganizeAvailability();
}
function setupWorkers(id,limit,selected){
 let max=Math.max(1,Math.min(64,Number(limit)||1));
 let preferred=Math.max(1,Math.min(max,Number(selected)||4));
 let el=$(id);el.replaceChildren();
 for(let i=1;i<=max;i++){
  const option=document.createElement('option');
  option.value=String(i);option.textContent=String(i);
  el.appendChild(option);
 }
 el.value=String(preferred);
}
async function browse(which){
 const chosen=await call("browse",which);
 if(chosen){$(which).value=chosen;if(which==="source")changedInput();else updateOrganizeAvailability();}
}
$('browseSource').onclick=()=>browse("source");$('browseDest').onclick=()=>browse("dest");
$('source').addEventListener('input',changedInput);
$('dest').addEventListener('input',updateOrganizeAvailability);
$('openSource').onclick=()=>call("open_folder",$('source').value);
$('openDest').onclick=()=>call("open_folder",$('dest').value);
$('rows').addEventListener('click',e=>{
 const button=e.target.closest('button.result-folder');
 if(!button)return;
 const entries=(state&&state.results||[]).filter(x=>
  [x.name,x.message,(x.warnings||[]).join(" ")].join(" ").toLowerCase().includes(query)).slice(-500);
 const row=entries[Number(button.dataset.resultIndex)];
 if(row&&row.target)call("open_folder",row.target);
});

$('openLog').onclick=()=>call("open_log");
$('openLogFolder').onclick=()=>call("open_log_folder");
$('copyResults').onclick=async()=>{
 const rows=(state&&state.results||[]).filter(x=>
  [x.name,x.message,(x.warnings||[]).join(" ")].join(" ").toLowerCase().includes(query));
 const content=rows.map(x=>[x.status.toUpperCase(),x.name,x.target,x.message,(x.warnings||[]).join(" | ")].join("\t")).join("\n");
 if(!content){$('message').textContent="No results to copy.";return;}
 try{
  if(navigator.clipboard&&navigator.clipboard.writeText)await navigator.clipboard.writeText(content);
  else{
   const control=document.createElement('textarea');control.value=content;
   control.style.position="fixed";control.style.left="-9999px";document.body.appendChild(control);
   control.focus();control.select();if(!document.execCommand('copy'))throw Error("Copy denied.");
   control.remove();
  }
  $('message').textContent=rows.length+" result rows copied.";
 }catch(e){error("Clipboard unavailable; select Results with your mouse and press Ctrl+C.");}
};
for(const id of ['doEncode','doChecker','doSplit'])$(id).addEventListener('change',updateTaskControls);
$('artwork').onclick=()=>{$('message').textContent="";call("download_artwork",$('dest').value);};
$('organize').onclick=()=>{$('message').textContent="";call("organize",$('source').value,$('dest').value,$('format').value,Number($('encodeWorkers').value),Number($('checkWorkers').value),$('doEncode').checked,$('doChecker').checked,$('doSplit').checked);};
$('cancel').onclick=()=>call("cancel");
$('undo').onclick=()=>{if(confirm(state&&state.undo_kind==="artwork"?"Delete only unmodified JPGs created by the latest artwork operation?":"Remove unchanged output from the last run? Original files are not touched."))call("undo");};
$('query').oninput=()=>{query=$('query').value.trim().toLowerCase();render();};
for(let [zone,input] of [["sourceDrop","source"],["destDrop","dest"]]){
let z=$(zone);z.addEventListener("dragover",e=>{e.preventDefault();z.classList.add("drag");});
z.addEventListener("dragleave",()=>z.classList.remove("drag"));
z.addEventListener("drop",e=>{e.preventDefault();z.classList.remove("drag");let f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];let path=f&&(f.pywebviewFullPath||f.path);if(path){$(input).value=path;if(input==='source')changedInput();else updateOrganizeAvailability();}else error("Folder drop unsupported here; use Browse.");});
}
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&state&&state.running)call("cancel");});
$('reconnect').onclick=()=>{nextRetry=0;bridgeFailures=0;refresh(true);};
window.addEventListener("pywebviewready",()=>{nextRetry=0;bridgeFailures=0;refresh(true);});
window.addEventListener("unhandledrejection",e=>{
 const text=String(e.reason&&e.reason.message||e.reason||"unknown");
 if(bridgeMethod("diagnostics"))api("diagnostics","Unhandled promise: "+text).catch(()=>{});
});
window.addEventListener("error",e=>{
 const text=String(e.message||"unknown JS exception");
 if(bridgeMethod("diagnostics"))api("diagnostics","JavaScript error: "+text).catch(()=>{});
});
setBridgeState(false);
setInterval(()=>refresh(false),1500);
refresh(true);
</script></body></html>"""


def main():
    ensure_data()
    mutex = _single_instance()
    if mutex is False:
        # A second launch has already activated the running organizer or
        # shown an actionable notice. No new profile/window should be made.
        return
    migration_errors = _migrate_legacy_logs()
    logger = _startup_logger()
    for warning in migration_errors:
        logger.warning("Legacy log migration: %s", warning)
    profile = _session_profile()
    try:
        logger.info("Starting %s v%s, Python %s, profile=%s", APP, VERSION,
                    sys.version.split()[0], profile)
        webview = load_ui()
        app = Organizer()
        # Expose only the thin facade; exposing the whole Organizer causes
        # recursive JS API inspection of window, locks and events.
        bridge = DesktopBridge(app)
        window = webview.create_window(
            f"{APP} v{VERSION} - Under construction ⚠️", html=HTML,
            width=1350, height=850, min_size=(920, 650),
            background_color="#0b0c0f", text_select=True, zoomable=True,
            js_api=bridge)
        app.window = window
        window.events.shown += lambda: logger.info("WebView2 window shown")
        window.events.loaded += lambda: logger.info("WebView2 DOM loaded")
        window.events.closed += lambda: logger.info("WebView2 window closed")
        # pywebview's default temporary/private profile can remain locked after
        # forced shutdown. Fresh, isolated profile on every start avoids that.
        webview.start(gui="edgechromium", debug=False,
                      private_mode=False, storage_path=str(profile))
    except Exception:
        logger.exception("WebView2 desktop initialization or event loop failed")
        raise
    finally:
        if mutex and IS_WIN:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:
                pass
        # Closing native WebView2 can release its files asynchronously;
        # locked files are left for _session_profile's next stale cleanup.
        try:
            shutil.rmtree(profile)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ensure_data()
        err = traceback.format_exc()
        try:
            _new_log("startup-error").write_text(err, encoding="utf-8")
        except Exception:
            pass
        if IS_WIN:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, err[-3000:], APP, 0x10)
            except Exception:
                pass
        else:
            print(err, file=sys.stderr)
