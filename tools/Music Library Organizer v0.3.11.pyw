# Music Library Organizer v0.3.11 - Under construction ⚠️
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
VERSION = "0.3.11"
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
LOG = DATA / "activity.log"
DEPS = DATA / "dependencies"
IS_WIN = os.name == "nt"


def ensure_data():
    DATA.mkdir(parents=True, exist_ok=True)


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


def picard_small_word_case(text):
    """Apply Picard's internal minor-word rules, preserving artists/acronyms."""
    value = str(text or "")
    words = list(re.finditer(r"\b[A-Za-z]+\b", value))
    output = []
    previous = 0
    for index, match in enumerate(words):
        word = match.group(0)
        before = words[index - 1] if index else None
        phrase = (before.group(0) + " " + word).title() if before else ""
        first = not index or bool(re.search(r"[:!?]\s*$", value[:match.start()]))
        if word.casefold() in _PICARD_MINOR_WORDS and not first and phrase not in _PICARD_PHRASAL:
            word = word.lower()
        output.append(value[previous:match.start()] + word)
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
                            "title": "", "index": {}, "lines": [line]})
            continue
        if records:
            records[-1]["lines"].append(line)
            q = QTITLE.match(line)
            if q and not records[-1]["title"]:
                records[-1]["title"] = q.group(1)
            z = INDEX.match(line)
            if z:
                records[-1]["index"][int(z.group(1))] = (
                    (int(z.group(2)) * 60 + int(z.group(3))) * 75 + int(z.group(4)))
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
            if 0 in idx and idx[0] > idx[1]:
                raise ValueError("INDEX00 occurs after INDEX01.")
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


def release_identity(folder, files, default_artist="", allow_network=True):
    """Physical DiscID + barcode corroboration, then actual edition metadata."""
    current = folder.name
    source = _RELEASE_PARTS.fullmatch(current)
    date = source.group("date") if source else ""
    title = source.group("title") if source else ""
    ident, bracket_cd = catalog(current)
    audios = [p for p in files if p.suffix.lower() in AUDIO]
    cue_files = [p for p in files if p.suffix.lower() == ".cue"]
    rip_logs = [p for p in files if p.suffix.lower() == ".log" and
                p.name.casefold() != "audiochecker.log"]
    # A physical rip may lack either CUE or LOG. Do not misclassify a CD as
    # WEB merely because one rip-document is missing.
    is_cd = bool(bracket_cd or cue_files or rip_logs)
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
    title = title or album or current
    barcode = ident if _BARCODE.fullmatch(ident or "") else ""
    barcode = barcode or (tagged_barcode if _BARCODE.fullmatch(tagged_barcode) else "")
    title = clean_release_title(title, default_artist)
    feature_hint = release_feature_credit(current) or release_feature_credit(album)
    # Source explicitly labels a Maxi CD Single/CD Single. This is reliable
    # release-type evidence even if an exact online pressing is unresolved.
    declared_type = ("Single" if re.search(
        r"(?i)\((?:Maxi\s+CD\s+Single|CD\s+Single)\)", current) else "")
    result = {"folder": current, "title": title, "date": date,
              "feature_hint": feature_hint,
              "root_artist": default_artist,
              "catalog": ident if ident and not _BARCODE.fullmatch(ident) else "",
              "barcode": barcode, "physical": is_cd, "country": "",
              "country_provenance": "", "release_type": declared_type,
              "selected_release": "", "metadata_verified": False,
              "edition_date": "",
              "notes": [], "track_count": len(audios),
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
            if len(possible) != 1:
                result["notes"].append(
                    f"{len(possible)} structurally matching DiscID edition(s) remain; no arbitrary selection.")
                return result
            chosen = possible[0]
        else:
            # No exact DiscID: use exact-barcode candidates when available,
            # otherwise search by album+artist and require unique strong match.
            entries = barcode_entries
            if not barcode:
                if is_cd and result.get("catalog"):
                    # The exact edition catalog is often present in a
                    # historical Japanese CD source folder (WPCR-15062).
                    # Query the catalog, not an approximate album title alone.
                    query = 'catno:"' + str(result["catalog"]).replace('"', "") + '"'
                    result["lookup_sources"].append("MusicBrainz catalog number")
                else:
                    if not (title and artist):
                        result["notes"].append(
                            "No DiscID/barcode/catalog and artist/title pair for lookup.")
                        return result
                    query = 'release:"' + clean_release_title(
                        album or title, default_artist).replace('"', "") + \
                            '" AND artist:"' + str(artist).replace('"', "") + '"'
                    result["lookup_sources"].append("MusicBrainz artist/title")
                url = "release/?" + urllib.parse.urlencode(
                    {"query": query, "fmt": "json", "limit": "100"})
                entries = mb_json(url).get("releases", [])
            compatible = []
            for entry in entries:
                similarity = difflib.SequenceMatcher(
                    None, _key(title), _key(entry.get("title") or "")).ratio()
                if similarity < 0.82:
                    continue
                rid = str(entry.get("id") or "")
                if not rid:
                    continue
                # Catalog numbers on concrete physical editions must
                # match source exactly before claiming an edition identity.
                # MusicBrainz search ranking alone is not sufficient.
                # Require the concrete medium (search results may omit it).
                detail = mb_json("release/" + rid +
                                 "?inc=release-groups+labels+media+artists&fmt=json")
                formats = [str(m.get("format") or "").casefold()
                           for m in detail.get("media", [])]
                if is_cd and not any("cd" in x for x in formats):
                    continue
                if is_cd and result.get("catalog") and not barcode:
                    labels = [str(z.get("catalog-number") or "")
                              for z in detail.get("label-info", [])]
                    if not any(z.casefold() == result["catalog"].casefold() for z in labels):
                        continue
                if not is_cd and not any("digital media" in x for x in formats):
                    continue
                compatible.append((similarity, detail))
            compatible.sort(key=lambda row: (-row[0], str(row[1].get("id"))))
            if not compatible:
                result["notes"].append("No matching concrete MusicBrainz edition.")
                return result
            if len(compatible) > 1 and abs(compatible[0][0] - compatible[1][0]) < .015:
                result["notes"].append("Multiple indistinguishable release candidates; no guess.")
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
        result["title"] = clean_release_title(details.get("title") or title, default_artist)
        result["catalog"] = catalog_number or result["catalog"]
        result["barcode"] = actual_barcode or barcode
        result["release_type"] = str(release_group.get("primary-type") or "")
        if is_cd:
            country = str(details.get("country") or "")
            if re.fullmatch(r"[A-Z]{2}|EU|XW|XE", country):
                result["country"] = country
                result["country_provenance"] = "MusicBrainz release-territory-fallback (not verified pressing)"
            else:
                result["notes"].append("No verified physical pressing country or release region.")
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
        country = info.get("country")
        if not country:
            info["notes"].append("CD pressing country not verified; cleaned provisional folder used.")
            return _sanitize_windows_name(date + " - " + title + " [" + ident + "]")
        suffix = country + " - " + ident
        edition_date = str(info.get("edition_date") or "")
        if (_DATE.fullmatch(edition_date) and edition_date[:4] != date[:4]):
            # The leading date is the original worldwide release date;
            # a CD edition pressed/reissued in a different year belongs last.
            suffix += " - " + edition_date[:4]
    else:
        suffix = ident
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
# The compressed archive also contains historical decoders, but ONLY the
# exact allowlisted auCDtect.exe and its original Readme are ever extracted.
_AC_NATIVE_RESULT = re.compile(
    r"This track looks like\s+(CDDA|MPEG)\s+with probability\s+(\d{1,3})%", re.I)

_AUCDTECT_ALLOWED = {
    "Codecs/Analyzer/aucdtect.exe": "efe47eec21c33431e7fceea4b171b8a15a729e3adcda967ee0fc165a6a41adba",
    "Codecs/Analyzer/Readme": "6dfebdef58614151cf3ff009fb2ad6212affc643325637c46d379188a5d499fd",
}
_AUCDTECT_REFERENCE_ARCHIVE = (
    '{Wp48S^xk9=GL@E0stWa8~^|S5YJf5;zVNb09^nm61-q2m(^(uA4TJFsF-39t5YFUwB7UMJF!7|K6r-'
    '5*o^z?)`;lX?&*A<6d8uF5J!m(zSXhRk7d(r5~sH}pv#e^$JyEe<5y7$<1?t*F8kgO2sxPPB<`qj5Q@=&XG!Ry2(llTmi;zIKs'
    '@V_>0*K#u_pjMqL2=LGiN&-'
    'i`cL#R5hH+A2H2%dYsaJU{($i174%<F#}_AM~g?a3xchilhH@CEN})$^xlC+kuXYZ%~QKbYVeqe7`<sxjJ9AVJ^*8YPE6_s&!f'
    'F!iQZ?rI(!H2SQ*(ZmvqVgm7i`BeOY^~-)cKAz8vf+MyCp$<u~a^y>kl$*q?w^G(u-rEBd!ZZ+<;Fbtdzk@7Z9+vhaL*{2n!-'
    'L?<dVtOx1S{<N=4{`iuoyOU;%2tRU2isvEfqCElQm!`Pv%jc>lVv*GWXoSqUq}<+rT9QDLM_K?k6vj*qwi?Iz=Kw!dJ*MSQ!f>'
    'HDz1}x|Dnbf;>F<eQ*SDkhvP^`JV}%WH2eazE=+R8JG-{5)y2^Wt!v(roKBdjd)NsJ+qWzUp`iK-'
    '1_yO3GDR4D<q*^5?kksFs5xN)^;^<&%8{vp(yyR0{<em`cgeMW7?<Gk&Rs=_TDBZHP5HY6b7{=Tit!7+Oshk3Kl#%smZ1tu(d-'
    '-|e$_-^=T5CKlAwqE4r$p2=f>XUk$Awvri*?_AGj&R~Q*VD-o}t%wM8fP+T{O@Dc&+s1Z}}4Q+Z}~OZ~R-'
    '{d={}y<UD^5Iu9HgRm{ucoo%HW><c`E0>e+7dG1vQy-O9Ac>J3j*xuo{=>Q#S<tgscL^pu*Bu>)n%ypPAbWQ0KW7g_-BI%3{9a'
    'HpFlVFX4{W@dY>*y&@V><BAJB+#e=>dmzlt*VKPg|V+`nm_V)ER$JG!KKQiOtdtD9-'
    'qcf4;Ep6UKVAg&i$RqIUIM3Y7jqjH5lkye=()SECuuyI-'
    'P^J}QFOxE@9$44+!^=7yU&C3VY5RdVkYSSvOu!^8=JDW)6O<YObxkg_b{_(2G+g8JN%UB@V#`>(?F-'
    'c~mhrtNdL9RPcW_td}Y>13TbeRw1-O&>o1^-4;&hI2w+Ll6XUM#9Rv!EBc{h@NSb_ym};dqj^#(+xQaZl??*W-'
    '6mVTNuskeY3pIMRbY<6zDe$`e~fPQ}D$0ARv{2`j!Qwb%v<Rvn<nK3IrL{T=nD9mhj^C{I5Ufx^0g_Zx$KGn$E8qb4>@kT8`bR'
    '{$5spXnRlo*nXDxK#f>D@1>irM*j@Z<Y3IV8#0>KB<;COff_*mfE8JjRt1KWymj|Y?cLk8|HSXtcez_TrHpOdwO(}~KGs@kBV^'
    'le_gPG(e!&M$aQphi_?cPHHJ~PM5-'
    '_!fb3Zy7+}6?n99l7S4Jm~Lih=uGj$MHtWV_+)mX60wJMAkNtZBnr7R==^+QwpoRN=CjE5J^m2#p&p=jp;Fqc6}`8KUH^YMZdX'
    '#-k{pz&|yI&T637YUj6J2Y>&RQU<7XSrR8jW-?C=+V=6g5PN4bF=I)lXCuP`c=djU{;~d?bP-'
    'Ab@y|H5Do;NS)LV>!J9Xh1-ozk2axe6P_fZ77oSuFwd-ZGL3ja|TSppN%q$fm4Cl;4e8V8+SKuMy{S*Yt){&xPz&!35kkiHw(E'
    '!=!|;gPdl)yW`tNxz{%{3s&hzoO9#BSefkVP$Lh38stldvPK<xc7ZT=z<adYW+BjeRl;3JA9H`=US=V1fP@~DrDRJ%cTvD0?Fe'
    'SQQjP(Uu0vSw3e|m5Q$X&^5>1DhDWPoc?EHQ#$*$~@`MWo7NW#>mc<U(IXHxYg)!BPMXd5Ga4+SXC{}4>8V>e$Jx^+EKpUTgL%'
    'u182)6p!C6mHtqvx;)_82Z4)NwZ42e|KLpu>Frt2^v|vWaWSkNiLP@3Bth9j>)KL>{5Mane7Odr6tovL_$I2XKs>ic~qBAn)Yk'
    '?Ixt9Vv-qFO;qaZ(3b7~eP%eY+;&MVVL4$vpFNOFIT~HEt{^kDU1OC8%E<1D-'
    '|9nsquaHe!5Z))pF6Aad$+fm8ijQ(&TP`WnPJ9v*Z<(bbo!kzke%U*tp2n?lPb#&!;otfpZnEVMR+|BUPlG9Ka3TM^3%)-'
    'R|pv`{AIb~<KC1xQ1ZEEQ-5W(U2-JTtv~}H0hn`9RyhDwg=lEz32&i}%vGPi(M9gL-8VuPrFwABjte3bHcgF1>PN-B_i2fzWHO'
    '|(XCO9Tu1vy?rSNX=o2f2^=3u85=NfGoB|fd->7#KuQ)-NkFx=nDj&Pzy?sgIxAU2CL-'
    '$!|_4$xs;9WoXi{yoo!R2|jmQ?No|>FYQFOgDSz9sJD|KnV|Lfe;5F3IKViI$n=WZg>B1sSMgYUPUw}&TUCfoF+E20tbkKi^XW'
    'K3-3Uz->|_Y_HtAX3D*kAs!mT}fW~5FsG;lg0_kiF5lL$28IG*!hr?V8maR1T`xosYRm=;PP*f!MfWshKM3}U&ZzhbA-'
    '2+=SxSAsUoMd)ul@MIeP4t3GzYHCE>kr^&t=Ej5fO>|E39(%ko-Bz4DQy%~R6;D7Qxi*$QjQDD*I3h`#=b!<Ro74tS6n&kZGw9'
    'k3Ai~l*c(#NV$+9}4bYbldXfy0?o8;UH{{PE{V2_$+W*5+#B}rueH;VR02>TXuhT(If)V4*$AH3e9*cs=)QdzD!*GMPzQI-'
    '0cF%S9k~(+kH8$MF+pSA=%K_$*&B-'
    'ih`IJB6#BO!Rm!ih>{COptC_#=!1;Xa<T=YWVEia*de;8YtL1oUGK#VUT){*ifrKS={QiAdjV7K_67X14b;mZj`%K+VPF&P4w0'
    ';@8Z!VMQ#awFQkGYrPP1?nkL-zmT+4*fZtQz;nYN~p{+C_CG(zW@cS9A_>wAzxu0i@pdA`AQH7n@gd`ZO;jFO(nt~a1(QJ!PxL'
    '<sIV@z_lk}fPL$62GhJ*p;mK@&SxUhoLXyyPTVDnqx(!Btw!sQsQJPZ7R38@o7tV>A;}nk{cL$9^!q(*&J){eEP8oThXwBT3w)'
    'YhDHUL{3Ns+JJChMhqMo6aziPBaHVj^Os<Jz;!W9To51fjL*^_-'
    '@YS_1pH<H;(Ou`sxq>vCxm{`6kg!>~dZjR18Gb$(B1Jp}T)YK8maDS3lq{M)&DMP?vFu|b<dBpJurfAH$6suFS%snQEWt&D|}v'
    'YH8f_iix~ml>2CM!Rn;Fx3=X5@c<?d=N6yf&s&WcPZXF>})HSyv_3g>k**}#-'
    '0TY`tUthrW?9Bn0J$%+14=%$6=T6$@@q>>>59Vax*8XAdfpbkbQy*fJJ^aLqP2S4K5Gfq_Wz+kNH<Kw$dAd?5G4b3CQuw?T$!j'
    'p7U-WJ418%lVuz~IKdgkG~_fVxgsyPkI#}V@E6~uDSATkMZ+-D;2Sv&O1vkFN}yP%Rk41CKl&};#ZvR%LDn{-qM-AHR)AHH$4x'
    '?e?U^eAZW*UEQf5Z_l?U0%%YTwiN!Tv1eCeUb4@N0<4J;pO%N8c>P0QC7uKMu8n7PpNMLw-'
    'x|9P%y@0~+Zn~Z}{kK+&=B~J2extze|0sz;&$7+HHhqQ@8y%G@%hiIDYt<y-'
    '~H+yf{s>JRM0#I^?x1<RuIlnJU05Q?B)P<wvHGw=9#sk67aVC4{1=sl|dw)rqY_~UQoP@$5>3jPWI%5=l263tjj&7$IUpy<{=#'
    'o1#4!ahXetHFb-'
    '(?4s$3UIa=e>xJp3bW>Tznv~3(BUdVox6@r!aGc_GZNShw$}?Uv95$4g+%)MkoaqPtAuhxrBNXpqqjU2V@#61KKm<O=gtO*TSD'
    '2>JhgM;8Z%h#s8~I<gehW0UyLNN!|z^$~<a882-Cg*3YuG<)sPfs{Ihgxefx;PQ97s)&Mv}#qJF71V1R(5u&+4VISN!jG>0vV-'
    'Y)Foo(@@i}XSbYIveLNsSa}##&2-WCGKpsL$3MH(Ly2RW7-'
    '|t^<^+e8GBJEZGqv5RDyBur7QEzKltlRy#%V=dFECf?ZG+N{O<vjSJbo!G^7g>4a#Oz~=|FyXvyNt@oJ&AJ`6?#SRIbpF}c#C-'
    '?A?x`y5ZBF?r?H-W-j8gpBo^6;A3puI{Zs^GQnVyf8a^s@?)_*?B_l$`h^j^-'
    'Y_C>wV(MyU78e%PZE6$h5FmMQV$fH!<@LfUzagRkO^<IDtQzVrN*$V{UpzF_q5(T}A)P@&{RD~93B(9h16wiOSHYPXu)P2nFpe'
    'TZ>0C9Izd`C#<+D5^fxgfbukd%YHB>DYO8xK%$d8p@rM#vWwtCrtSFTCX{pwyfB_1g1^^1MNEV!S7~piCalhpiN7<deyo^HxfR'
    'm=npHUYV6AQk8Z)G>$sE$gTA(aB}x@*5+QG*=4=(+PQxGum`Rq^@ZSyt@_Q7?D+WC^O2{aSeD3<TmwbK9HM02ww2~l;@vrGFoB'
    '<io1Q^gibPLjDKlr=w=c+psP~yxyp)r5nw9*IGD5(Fq&%I@17%9xx^|W$_@t;f6H0yDGRMdFmjbN%3su^3I-'
    'bW!jPYKqkhs{Ox2(}fqE17K{1fS}QYXE;?BTIS#)kppMIuXU4`ViJu$$unWYg_eiEUAzB5#8^1b=RCx5))O#sO!$+73c%<zohx'
    '~aIP6%P)3Zqtx6;KScfxFMDDD{cHpOd(smXhK33V@Y=#G_HLimZcqs5DFWdIP*scL|?-'
    'vKy5&5HJFyT?ZFr?diRLrvA#h9x>jhi~2AD7z%uo%##xRdt13zy*X=FO^;h|4eKYM2Oi?eDZV(~qs}IB{I(qM(PZb-'
    '<%*q<DuA%RVN8!N9fQ?SsZH{e2v07(j)()w~pF8DyJ%4N*ped?=(`3IhU7x&H*_7O6PUzdWBqO1MdcLME`$PjD*n?pLDUtc{p;'
    'uqEg>RQm))4SSR&$+^0N@3&X-u2OEq<y7?;HHx&)5e9h2N~kriCxJT|t})n4@Bkl?vS{sIWi^Ud-'
    '>+}4;|i!UXD<&IAw2#fshM2{q^4_dff_^>3!??Pj@hY$J#s5ID;;9^)SQxJhobs!_!VARRjG<Fhyk*9p(-Q`2a8;r4crE-'
    '@3|He3Lzy2hqNqdJzj@(v2Gw391C~0_xTbS|M}JCkqIfVgN^_a^K;!0ulh>cZU&3M2=j(_`aC1zDA`UwF1OX8j&jLkpJ-'
    '*G_#_H{b$*hfum^y&uW3LEc<-'
    'Fy#|8xdZ$k5vvIE9Y1X)f<x!Pc+2;~a}DEq93Y<7bDIx5AI0kK>)U;O=Wz)f7YzS3SH_P6_Old8%Y)edo=8t-S(&Fd-'
    'Q|HJBe+()D6Uij>z)nt6-Lo~NFaKo!@w33xprE{3o+w3Z`z#`N)+}Vs9H-g?$6HeSnQwF_LxsIZog_Wmowf`MYC*;8`lDB)m-'
    '<d{e@_eq3iDm3VZ;rHAgp7dZAjShtMPzKXWm$xNf)|r5tyTGS+*vcPsz^QeTs3Zf>K#Bu(({;D^Q44pvHbO|5iy~nND^9e_w%F'
    '=h(xvUOZUT!bFx4H^*HGY>mj?VZ_5@1iZh7-!B5P}Q~q<dace}jB)b;iXRW5}yyygpoV}-'
    'jkzYTm2N+5@^WmCJ)Kjf^G=HX4K;k1Lh9%v=DOo(;4lOSJTkdpZfcMV9W;A(TFC*%60;&Kf!r>+94mK)`);Wt<>sc`h^7foqHm'
    '~DtMcw!x+yO2=C+DcnmIxo7oU*83|1?26M8*8}v9zp7TLn||>o_NzO)RXiJYuebBe`W<1@CY?_NMY6wu$#2)vm&jk0s~H+-'
    'l8TJL7N;==%#vK_kumD6wq)+jOP1LREg>nQAW0Ir<f~8Kh-VJ;EZfW#p(povNl|AkDRpA8%HhmxW_;N{lHZhFicIe9spBPWv3c'
    '1D-'
    'nNT3c6*aQW;RT3;SD79&N4fxWs`U&baN^2^l8Kwga75?IE>+pbrZ=c|I@vT=sii@J3M@W~6aU66)`r^*So3d0)$4g2ZI6%Dk@?'
    'nW`<!yZ2Cn?JcYzty%9iqGY`DQem4PSb-'
    '`+ldTRzgq<qkR(FNnSBFn+rcVse?5@_OjPi}GRF>*wF9#ix&V_M+qnP0o1HhB8`fY88B}D)2LLqg2E|7cX$uCF4;I~oc8E-'
    'B%TDWRfgI_W2qsU+8Rk0hR`dnfm&v(J!G#{(3*gG#LGt76sq@4Z@KjyF^=+Tn!$5cl0fwxygxVcQrBUSZv7?WjXKEroA7|f=Si'
    '5uVr20EUW3V(inkqFhTuKLOQj40BJwEWt_TYw<$!f~GcV8mlJv40udmAq5_YOlGT3|pxzTjO&{bNbclTa?xtfZmdReN2^3EShs'
    'vS^o;{KY@#)4qLAU-'
    '9x$Q?c3{{lfIR>>4Ac;ee`hGXaBN{py?*Uuz0$Kax;n!C=31p)R4VwMOE~47~ptN;iqc6cOt`<)kr_!e=j4F(PO{N2<Vx?+6Y~'
    'g;XH!fuoVZ5GqP#3b1XM)Hlq6;lyFq^80MSTJ$D`#sOstrzz|`?u-'
    '>h4y(ruJa{$B3%gJ+f{h$}6Z5a_tZ*u@+k`t*zwj3KFsK4fqV9KrHXe_4+0ojnX-MwQO(udT3X`D<-'
    '|6;CARf1#ZzSYbhM~902%%*U99V!(a%nn$H@RWjgKHo`609B6B5OMT<&iDN_CB4zr{=ErPSfUY(Oq`Lw;sK1Y88U%6-'
    ';ZbLZTMoukHQrE>@v-'
    'eW0#lW(5U91Ryq|LxW?;+C_ROMpBlHgQgji;*`B_Dids*jJZVei)`NS7do!Ihm0nY%uZf|C2<*N&2zD)>S+3nwr%Ue^f#lq3V0'
    '#~%?Z&%gz9aEeJh1jZ))gQdR@4VT11@j`H$98D^G{A<q)3&TJjsX(fPi4zY{(c;FtQ<qI<_OXa-Ow2GBx0yC<*ts5S`XFZ&F|%'
    'VD@Ek|O+uX_@3tqWf$6W-;r(n<nZqDfD;UOh^@pQLV;(tNlZW+5Q_+a4#bwjaU7V1rMMVIu2y+CqdgM>nR{aWj0>N^-'
    '$=4))Ip!YkeO|L<5<RGhf@}iAEMem)iZz(QcKDkEOKjC(8$a>AfVuG5p*Jq@-'
    '6jKm`ChCu?3Y`1|lv;LD@>QceYUILZA_K)B#eVub2hsR@&AjFjAR#DIACy}u#JWsU`|d-'
    '(d~%4Il!K?4)PXxeRZjE7fU)_yUcU4@Bg38r^_Y8-'
    'pi*7rK?TLuYBqK+sskXJr<pdF{fqjM5pxxGEt*mHn`Tk_F~#%V8aKo?X(#EGALg~W-'
    'vTCPAVzlcf|J=<2&o#K($V!mmYRS6cZT}q0*>^v#~!l4Vl2FFqxp!YZ1aN-K^r}`}-'
    '>Du1|elBk3$*L@65x^iZZYP<(%(1inZ614;3{yc@%Y!DKCDqLVe`w>4qDs^mYX9`x53HBl>@6P%G<M}adrNFdI{~x80b30_qR+'
    '9E1tvgKn*7CI1e>)~5M2*(hB?PW?F$xX0{4l*d}wb^(f2zE^gh%--'
    'KTPN#r&v^f#8^KA8WVNtWLo0d_V4OrnL1w)WMfoloE`G314AWrAY*)c^VTN=`Q3a?KanjU;bmL>SfF$!18M?8r)qe1G*pat9>`'
    '#z)ZVm{#+ryfMSRCrWZKPfJ?4-Ao~d82oxbdz0jm_eSNsWN=Rd%-'
    'Y`VHj$pB9bBMIQtvWjGwVXUGj?vdna)`}a@udM&LLcC5oY_&?HF1E>MTtKVv3DXtf^y0BG8x$INzd~p;+dODGGR?N5g(3<f~5D'
    'S4`W&zam1)Mwc9nX>V)D0m-m<lf?*ha82i`N%~sbV=qR?~^I4`X<o(Hl^XxAb8Pk{`)1He(?)(afW@?_~q-'
    '~v`>z6KPiYDQ&zTI&OD5QG!9```;+XfX*k!~0}fR3-'
    'K#%0WCzVz2D9YHwnRs>NnC9t6P2*HlMuG)56&q0PZ6j4VYwm34Q{<jr`RDp29N`@w%Zt&O2)J1K@Sq`G?6vlGK{4{VVIW7bX!G'
    'SN@<wHgIa=x?wMLVX(@?~pJO&z38!MgrW>qw8mIvxpoXqS8<xOxQAV3M0}Rv4JluPM04VYLcy3J;fN>D8^vte(#dz`HeHF7Ci{'
    'xKgF&^qXe_Y7#ef(dPY#RGk*GXq@7(0`9_E;%<QSxeg^ue1u5wGI&)az2GO_SN=9NuBYd=3H_Kvfc`?%3+f;&zQt-zf#4A^+;g'
    '%LEe3LId^`$dg6O8kp?G=kHq8AnUU?WR5xd0;lbca?oX*wly3`$xPiYblLMf$)Cws)M1JWZUP~c~12V}hexqGS@5!?t+-'
    '+@*R?*{eQb=05OLWFmywuyb*iu>+j2{YyFsN*Eg^PBxfLy&6UDFB4;hq8klh3ImGTP~Tjvn+k8o+Hq6q)Y^8wsm(s71_U)dult'
    '!7#{SG(ObfSTE-$;7~G0nMA3vR><j~cFVSE<_Qijbr>LaNT>vaRGa+9e&Gn`6;gR>)vv6_vbaOFp)8yV(phJ4?SG%t;?)E-'
    '3*`YAyze1?jrU&l(j<WsC2l{RbzsNoO4k)O``m!^U5IA-fyn-'
    '{+4)Yl^MMv8**Xj9l`PMD&jhAqMG4%F}ZRiP4BOvaW<_8bddKP(AZJNh<xUg|>&8LyBjp8Er&$aRKaNf$-9E-'
    'D((|ksVmqw`Fj$M#sG^*lJXqr+5R|MJ(Ctiq^=&0WYjRx#ADR^TnYB|_qu^q#EECtH;<M}8y;8ubjOgyC)%Pk(I9o~(Ut^oPZL'
    'C!Lhnn?)(X2Ey(uIIe3kZIl?8@(Hd<!R(2S|Ea$;8RDX4kj^qfZ12XyU-s_9DLxk?yzHh**zlwi>p8opelcTWqTnTe%grf9Vt}'
    'VEJ#hw&LnK9bSBIq8%t)-D<1!6ig$~Yu=C9=Mh!s{W1LN?3h;^W1^)rOsUEj1rLzAk2ig_`ezX-'
    'd^?A^{ykN@t=8{%@#ERD+el-ce2V!#doPuq{orXNs+9McrxoGpUT=F0DEZEK|m@&Jm&uCdjf37=3;Q3OC=fLMldfuBkU4WK8gU'
    'Ne4gWrezmB16^wFwe?#}<kPOGGnLSm2P|xTc7eZ{O#R6rl&$4<i)l8Znj3(335?uQsqGQxrFh#8qHiHKBFJriKbTVrqu;9_z9I'
    'y-lJ!@>)HhThGpGihIB7Y8wvJ+tij~R5KMMj`aEywzpS}{>O5e9c{8fqNn{H`+99c14*i{qe_`ZF0==#ZY-W|B-'
    'x9$OD6|Gmt=N5&il_UV$Mz=I;bI#N`<&W!Ii4v+hK<En9Nm`G4SxTRjbR1(71%(viXn2n>U$b&rCOels$g8^}Ex|KqT3tbcX{J'
    'NFaCey>we|x6k(F!LTCV(a5nHN+5B(Ax{Xunc;e_X`dlF0t`QFk*G@32B6B_8H^I)@{yJG<AxY!eRKeZr<pT&rw2=MwkMciLOh'
    '>#g*&EpRY^KE)E2BXRap$!&@wB609aB5`iY&<y7ayTC9l^%ySPkjChBXnn_JS!{MJ)d+6zx)9pO2nbl4VoF!x@vLeH=%#qjRE3'
    'gp69uulQB%;g%imDk6y4(k4pUT}nA3{mt6#(e$3gWB#_a#9nuz;`q<+IQle<+$s7ixJbdF4w7QF7*YE7!``|?nc4p$n<s(65Wb'
    '<u6{Zp`E@>il+n$azPtKDZjZBPi22&%CjRT@xC5?x@t78(xRlUs`~ehN!YKg5jFLe7{LTD@f!P?c`;!yq`b<cci~?pW=Idf%^Z'
    'o3UJ2&V#VKUBo!B=B!Zps6W+^I_u9Jjexi)VUVK-Hz4JYJH))tCzLwtzT&#S(=lr2|}iSd|dcjDSaT`_<6Nl*e+AhmYE0Gs9|Q'
    'Bm2&CYD0YcdTZkfmLxWdpXOsMXGoa4Xd{aKTmz60HC|hffjm!tRFcT+lhiva-'
    'M(AmOp2gZnM8Y4V4>IT>86*c(;Pis1c|YHNl|SOkNG0$k;HMm(o1-pjr~I<hlzCC=SZJ7YHL#uP!k<RRrclH!=TgEuh_*-'
    'K#qhw<hfe(IS4iNOQn=4M!@yTV}2-kZ`pam%DD($C|dAk>hd}&OKd<C=mgVO8BFvlT@spiu9hUUDp_-'
    'Y5x~V>ZFm%ih>vDX^k`APg?y&NP?yhkb-?nRKN81{<i-oVvApgp-QJ6z>+q80OXkT|62wmHuKHSbhU+wWPSV*4I0`fztF9(apr'
    '8fhJs%Ms%0q)NATu=z3X#BnwJcoknxdIFc_s7UG72!?VC=0UM>RB8B@VDO@e1B&1N!g!`6P$ms<ZbMpRyG#CS8swTm%)%0({dG'
    'X8GkN;^os@@yW$ZsApCF%WV?16>~C^aDCYe=;qF)CiAngh+TqRc*l$D-juad%(siD2y^kf=5+G47Nt{nj-'
    '*8Go`P;l*Nksuh|;S^7AFd;kkiF>VxYZgW|zP9w34?3ro}yqd32s!Bd=uOQBf}lt8@A3g6X5>e0Kns{eXf&ML(3Pj{E+FcLBm4'
    'y7kJO>k0QfkLr1(BOFCe50Gn~vKilC`<qHb9vDXi1w9jcVD|W#lV&R;6c)9gKoh%|44>QwK+cARI5FZ(ZD2}UgoIL;hpDLBR=P'
    'nsVHr8k)LgU0U9Np%QgBs<ooL`=bRkLH_A^a{JI7fu?0GUvCmHZ}#hdyP%TYTUEoXU`C2>Slo{3JMxRKaS?je>-'
    ';Fg68@FWaK6t6V|%Ot*n$k*W3pwTs}Pvugd(z$FpTfea{Uq2G{giPCJ@2rDb>jXQX?Fu3-pIab8VCqk<-'
    'p=pOW|3+Kd~<A43hM{n&CEo<B;Fto#CfF3(MNK^s>c`IeRqk&CHa<UVO;z$j#;?Ls*UH^(k(*8#5%^?cyPx2;VD<Ii*Bm;FB|Q'
    'hYr-'
    '^uBSg{43x)zArgS<;ACe*TAes;vnZRs;*U*)|lfTUX693ywuf)MNQW)=@(M6o<mMo#PiqM1>@IjAqRX{UkYHfG~PtQu1Ebp7F('
    'b_2}PmFmfU2pWhzy)<lOBYMTg7!+<<Md<P`aTRNI=jrC*Jcl>1JJWE%u7sZcEoOQe$M{Ny~7G>uF*A}NxhOMDbnyarI~3SB^jR'
    '~7vtoI(dpMPAv9|HuzD}8s%+!awy9un%;O`8PajVjqcdzRvjl%pK#%HG*23S*eY0l=32!(1Y^J2nrm#0S99&Du%RS*I^ozg4ZR'
    'AhWV}Xek(JXOTG98mQZ8So=7z6BKH20i@do{ozu10lHgp@j<lS$#yckEvXlE_D;PM?aX&a{=RuQ=f4=21$;Q{$F^j@L%4qCHZ+'
    'Wp(eG%ZzdD%Nu{1ePise_}!L&O5EuNN>IoBL0nFP!R24QDVB+wtZ>0(oDz=YG4JE8!F`r%tc7v~?teiq<o0u*Iy5AWLkM$Ol_#'
    '%=Umf2N1!s1g?W@ZAB2!etW);!Yz#8M=IFqrxg2)e72<Uxxc-j1Pv;lHAQD%Bcypb|rOen^)|EiIWvSH{G2ZxOTla^3r-'
    '#?iPYQo$R<}3ZoUZX+&E_?H(yEIB~&z*zW2fl(lUgf#l5|?DDf7Iu~Sz)0_M|~tvvLMhm1w)g87Y!%E*P+m^fDWF0!Bx~3e?Bs'
    'J-;zZJdWWlPe3tG&hRDfsm^0Y0NEv{$R5>gK@%4jQ;IHj)Zklu8O{GJSs>ojkBz!qwxq(pciZ3Sq%$gDUG9|BjiC-'
    '(ghfYLA=9oBViWl9S5LjK4Tft_U+ZQRF1fpJKLF#e%zg*vOg@s1SDjp(}+Fp_)a!NFCU;_F(9~@!-'
    'xIyel$dP$DA&WVxPz8TptPn7JpDQ(=U*XMbYt%4)FjoZQ{CPzrCI9rS@;&u=aFTYMu8Nz`!8U^%_xP0rw(39k<6gf+d`*v-'
    '1mE70{ez%n;LBBKbzp|3sJ+%Yt0X<QXk=iRP2aL!_+Z52`>rXqHZ<vlmo|NAS=zi4M}mH5J1m!GV?{d^Kv2;zraRr=Nj{#d|El'
    '!qJY!tOgi=w$$Qn+&2rbP8<c|+e%qr)&52f}Tfm&ba$X$fDYYr*`Z$@x5>OG+_vA`Lu!#Q$XP{M1pkV6#WFoAlb!i!0EWnd<*B'
    'YD55+m-o)f-2KoiB}C1>MusK4-`442!q1T-+#EHJRRJcgu0ghp%RZ3x<fq#JQD0N+{_S{IL19ua*#>+CA#g9;aI!XTOm-'
    't2WTeg$M<-Giz~%@DCL+V{9a*|XyH#Nqe`JHL6QoYn5zA7>iFI(h2=dGl-'
    '&%vyrMTN%WG(6w4Ub3TPB}$qx}REb3OHXIvll(7*;$#6jVz#h61bXah^5=KNAjIZN(+nTVv5T2eDf*mhkZ6nK4I=PfhP>E-'
    '82M7E#ucgDZP}Zj4RcrJndizVHy-HM?v~0oWNnf`OcAKSWE5_-'
    'KgPJk;Z&tw?xyD3hmK52W*j_1q;Vn746($T8`L6|Tk@bliy(uk@>x|0dM4MTt?+QRPL2Jaz-'
    '2IL;=qNxj?iF@<Tdf5^L)>1|$}|82!kVfCsg3`ZGE!UJ=XlYAsfVfnTuXq-'
    '(T+V5h8?t3KyeL{S>cNJ+;Kf>a&&FUzlDjBjnq4xh09tQ!_OxIY|T<Lb4f>P{(97KsXh3Y9Vl%|4&f4P4PReMNzW93G`j!_4`('
    '8V0GDG!?dw}*Fcldj}e;Gx&rX>-'
    'lyOu3#t0>`d{SPyIOleM+7uF#BD3&>y%zD4s2dL)=5J6DY>58%CF&=t<yJd=N;>v{`q(IwJGG9{l13nmYMt~}Nj{Hz(A(iJij!'
    '!O}>{}-=pLV$04@<|}GkAqB8OAO$7TzX3y8?=-'
    'Xf=G381&u1|p0Qhey%dCvd7@s?<3=y(uaQ<+BKr1IVg(hu>6fEb^T^cTpa70X^2{6_U^pyj{#i@ccO~R*)|K9aBW`y>p<Dx|aC'
    '#0>ZdR66+98E$-xIa-m^_Z0DD=H0h@uG<nKp-'
    '|wwtZizp}MzP(b2ibR@v@YA7^at*CF%swms30f|YY@Yx7GYQZ?<6i>&9>{z}$0PLmfN(Iuou&RE5Zc&JkfcjmHh~+j<?EwtJsL'
    'wAG@QpPA4nz~q3lw@iXO$xL^GztCS-}sNM>CG9G9pG6AfrD5*Z^JmX8M-'
    '1DF6_t4|u_SIetLSE=cl~mePTzCSg7$cpcmpg1Pj?nIiJv3&}fK$$DI^m~#f#KtsRd1q?)rH6H5IlbcBpHa(|qs+`5=vmvr1NY'
    '2mFx~_-4R<O_GL>1CLxX2nztF0@br~|Wr%<+0i8_~?5uRGpJaX<@~FFukohKQ-'
    'SYezLIRs7jBzM*;}vpR2jvBg826*`=qy4^xfC;oD;pzYDNl^3$^mZyfp!U750=>oG(2{J_-lmdx;7kcnY`^A2K4>b4Ir2II|Jc'
    '6+QsnakOkfShWx*p6RDU)xGtWv<Tyl7<q={&~Jh*EEFREyK9P_}}|XU&}b#o@X~n$7g;z&0+^Jsk{Zs8f-+Yps!G=%Pgi-km#j'
    ')Gl43gpq;WMgn7PAc?|K`xXEGZJD`AKX6@KvUT8uuUGJF^Q<g(yLT*3EBE3!<9?PvUv1R%S)kBGG9YUVzUC5VpIu$?ENK7>1;='
    'zIG*KnjFF8j;MTm&fQ$}!XSd0CD2K~t<5c`r<w-'
    'wvFt+>@DcI`H%zRkHr)prGFO@@Dqygw9A<Q`{TvjH_mW>$bB@%s5}r$!oNeNPr2dW`#GIwWru;4Je6rXGYsK_ZMI!o((}Fs9Do'
    'UG?HsSOoE?kr}J<?9KjXON`Pm8w5h02i=?+e~@c3B$ErB#TMth`!UJI)wXj!aD)PHg}di;W}r*`>N1!@hZNoODv`b!IV3ZvvrU'
    'mq^OFl&2+o1@gYUr$=D8TKCu^L0Bj{J^PH}N2HZ%@q1UY^m1P**iRQa9b5_DD_&?bx6>$2WzPhr^x@tks7E!?cecOQ%>r#~xCg'
    'xx1r1Sfl^7S^Vw__$6yC5DVlz>*tuD9eKps%H<)B$#n6myTu<c)8j_@6Ea8RGEoNLpcIitWGTHSjAq)BwP$kw>TQMVR)Z9gPM2'
    'hQ7J>kyIXZohYZOe!kuoVKouJ6C$IDbdQpe%u@uYu+SB<W0(hR3@RmKi&)J;ekLIjv4&tx+Z>b7nO7z<)p#kvu663K8QC1yDRb'
    '5Q@oCxM>1$;GvXl(7Q-'
    ';kY~NuP5QKLFrNAc}k5NWRxj<+MFej2@&+FmQh#Xub^Rp&^DElSBCeZt^QCM7xl*?nLngS?=1PPqY=QZQOFcd{~eZ%Rn||_~ZG'
    'D^>8~h{7XljiIF~N2x>&U?F)_$!B&Al@Aq(46nO|)CNisZq05@0-'
    'wCsl`4(r;5`*oBBpx}ta6PxgCy>X=X9;PC9P6=L=o>mot}jbg-qMTUDVo2Glean^U5XbW!lZwMTp6aH70qf`2W4{JpHb_%hquF'
    'ZqQQEY0U<E(`O4)y(QB`3MB+M~P9@a)xs-'
    '4bA&VSKM)Ka?S?{JHc5M;cI^Bj<DB!ofHB@0qG**la8c*~dCglHLl1=Pj!ZK&F5(-P^8k2%*4_@np#H#GmM7{+wwZ-'
    'r`Z$u8B0|D!``LCmWPylxSb2M_O7<)@q)XO3XDMay-pY%OZrS=W0D!TqxDDffDVLHNI>3mk?`I%gt=m&IuNfMSN9zyZW-'
    'a&VgnOj)#ypE3fRIV2lq;v^{(GX3NZzJm31uAF8eOMI2$X0eZYBQbG=0pB`E$e==3aY8xg71B(J&-'
    'GeZ|h%LF!J>s=1FM)5rhU|UewCwzc+DK1`KE>42;xWc3aIM`a(83K_+bxm_5cH^<~S9n@TCCAX*k_jE6X?h_!Xi(cZgIDfs^l;'
    'xn3<%}|%Sa|lE3-jT)0#y-'
    '>u15Vz(t|>vJ$!P_L{jdsfmghDPAeQZ)$PBt#m6BXTlBTM&zKH2doRwYTs1$XpJ<3ky0zzqVo?XWTgE}eL?14Cvp5J%0vXS+2+'
    '&_+s&);IMM!TFQYsL)vGl-7l^7)V!3(Xw9sxU4jo|2~dnjKO<Z+xaicd-'
    '&56LO7=Dj%11aYNJ^g>i8Vq3KUvV|2}>p^%8uXNHdh)G|+Es<^Do?+&f;GOQ#|Xzi?W%~Y3;b&1Ke^5Dt%OUO0ETuAI51)bE!#'
    'Jy5(SbZx*ZU5vHWtPU`IDJ~{wOAf7|9F}3SAPONHHP`^Vf29bqy#!fWQulZHA8{2dc9V{%jYN)BcB3D^ypZa>WqHKQCcNChcmA'
    'Lrp1{rF3f5X!72#NIp<ur0iFpn8GA3%;3gq-'
    ')~J{L%vY<q%3V>?2s=t+d+ri)kDIR(gTpyLt3)XTyx3v8M=Vo6J5{Ex3ZxU<PB0*%8+?nzfvvDL455?AvYXOSIgWPU*Jsc@H36'
    'T-KkU;n8<zho7$BSxV-$i<`n3|mO>UcU@C3`w*~27*xRqQaq_63diW}q6d}8VwP@L`u56SOc*!~O-'
    '+kAXNH%7iCp{^g3y|+a1M#AwC-nXOfDlV-'
    '@eeB?73?GKw0ylHN>)4+2EYOQnR9YeI`m#yMU@ABK<M*5c&z5qV*mot+t1VtQnQF*V9=~Q}UO0M~-'
    'U%v4qOo%lPXVF#+1Jqh&u4&sqL<TzqNi!*2c2Jls1?5U_61_*C*I@61+^hTe73`4@ugs(hC^2Ko;ypXs1>!)g$wedmZjQqoudO'
    'kpe(fEf++E$2{f`#OAvN!IdEzvb^xO3-pC$z(an^VHn#cKpdR96wv=PVyQpNJ7%o@;FbA@ivH#15FTDHEbSLW%{bw>du=q~*-'
    '>Qheoo#w`q5lt6u0`P)krn(z(ZzMup1cjlRbO%2_v$LhTvq20ge@+b@&ix7C(szIIHn*?mK~bZFwD#wB61fGHgnIeSz|g5M?fp'
    'RWHg~otNAn55)!$5u10GKgpgVJ6SxLX`FxGi>;It`DOLju3JkjB$-'
    'W(KVQ;SYDtljKs^0~WSo;;@eww#?>Lo&KonXtA&{|0p^5gzqRqDMhS*F}of!4%L41q+dwsFFXICXyN!`^E*5k)cOpY*H-'
    'P@Yeh`!R2E54&u?-'
    '46i;?by%*%9We1+@r`UnQk2R<~e~D$7#Z0thUOrs4Wf@6D1@~6sGyW&Sk*4G7#+64jh@65swDvITS_XavylodS=jd&%)X6Yi)m'
    'H!WBp8`iQ?#Fl!DP&l}Qpm!VBEEja2>sU57>$sqd`J?S^Lkt!1P@Yh<QMM8vq3CLWaf3hupkMdchUDwRMpqga<25%%9@9wQF@!'
    'yU1q1U`{edJMzL6>h*N2s*he7`W+jW(<$qp5GjBR4kK$-'
    'Y?4<PYRjA8ZFs=aIt2B`<z`j60N`5c_CwAt`M70L7D!(H@qP=nn#f&R9l7k<7FfH(t)uf%$S8a^*dmc5~C;Ozhi?t9@eu5BU^-'
    '-`!aA+7(-GZKe?QsUxzKHI{1;liG?z3bD4B>2(S?W#v!&{9i&SdsW?mn^JfmQ<rSxl4Q|NhrnDR-'
    ')+;Je1zH2xOu1aNa<Kr(M9#fv3WQq{aao$UA}i2?Q43QHEE0f&+dFBEXnJoM@XU1cU0PlkG<ZcjzWq40!a*~Nrnog7@4BAN;I`'
    '%`SHeL6Mt8H$hTwCWmJ8?>9(hI7<G5|$a|)R^G%%bu~fte3iNpLt}JnB(|}E;z>lO`IS$j=Hi)jf*W3<<sOyDx&SF-Mgm<eDSR'
    'djv8)UI~QcphRb1_Y>)2q^#WB7C-'
    'TbdcGA*(qdrr<gG=dS4%{^C!|1UE67>PM%RWdJMtXkpgh%x(erp_5hcGIl>oVPjx^9VW`RsjHia!y8cSK}3n)Mn;d*`RTmy<Md'
    'i3<__zmQS2swHZ20@Ye+f$7)yOqN7o$Ch|8a`Wvm9Hs?OEh!6pQU1P-'
    '+&kSbY>2Lbw4HbWozJa3yOvgTx9WPf6>UCg56%WcHyKC2^w78>(OJm0Pdz?qvD#A12rJ=~RPMDnVenf7$kXsJTaL;?qM7ZRQoS'
    '(8+nZQZX%uNoB_dY(iQ3tXcJIsY2(Uq3}t=;EoFXAtou=0%RG#w?ZTkq)}e!A2GOMTdG;X*1BJ%jV22i>oCan3(0mDG)7f$!L^'
    'BwN){t1;Z7zUHPd(zUL}2h-qF>{E$2&AfK*eA%(RN4c()BPgs?bwV+phl^`-J%-'
    '|ZjOo{y2^sf9eb!0YiAH*yTK8yPY0eF6L=wxD*8k?q#jYGa}s~d?AefG}2OmMKu4`MP$uP0m&;3)}KGDtq$K9N{_EmN`?(#A#!'
    '6mBiOMI^gBV+!IbDhvp|<V{boA^w6;(uDra=`tkyU@^O~GtZlQzfEb7ru1&(#QtNA_Y=@BDJq?5ZQ@6CgGu)+Po@CVyIcEbA^~'
    'r@9IUb$@0PeR7nF%cX**%zB7!t#kZQY(S)kuQx0`5aQzcnvW09M30N9I!9lAN@7=9%h1yOa=gARfGAs|8_fPfBMrMc`VRj!^#p'
    'T%?kKg&g3K&<CMo+0aR&k)MTDwG<=xejF;x4RMMFt?6u^}b$Jv|mj5eV`HXx1^-'
    '_{s~(}(blaJA?#nyjdarxq+mU)KRBg2*u9y}%4@^7Gv0Q5Xr=@NNN>5o-'
    'c|{1lbWo9H7bGv3^H;qeMpI2xDgWRn?m*S${M@i6pkfk;ix4sSgvONm0VwiWGmen&X{T=2DG+2Vf`>`0r&m76{*gBUZ%Usv>#V'
    '$7nR{(x8<!W-~1?bg`x&r_ChB;%Tpg5ucC(La-tz&U9gY%CDs>0NuE1HKRtH641|FIj9N^gyI%pOYLB6ACf6VzqqLTn2hG)s*_'
    'n$h9`aR;oz46f8?hP8@7|^7Q0I#SAdnGMkU*|!^DJZ5uP`6R`<y5)5J6!je(-Is2~QIbZL6ujN1CQ-k3i-*jL&xxA%-'
    '2De<uY#G34wft4+a4sP<AK3;;#&Uz<Vf3;7&-'
    'W_Q&in%s3rG^^`OaSXq!bCUVPc(u@LMRGLW;&u@7XKNP)N6f{(i84>WK$*cge@TA9>_^wvdziuahxLfB1j(}=2EqcS^p&{~@ld'
    'SRVIcVg0-B4WP;dZm;)z5)UOS#vn?X*+dA9~9^@19NQtSb^7?&S49=%waG|iX(?M1CcddSam*8Cx3mE-'
    'R5e5L;Q_Rs+B@gfb#8Lx7c%ha@joUsB$yeVt8Po$8G$*B_f+{pMVxPFbeIp?4QmIZ!|6I>kRMEZFWrCZca4EAmjl)_&gbhd_w2'
    '-M!gN-'
    '1&acfj3%y~Fmmbz=bvE7KS+?DiTJ=!*NPFU&$c9YzhU@Kk!E!0qaXH4!)3gA&O5GAd3nLuE+;9{Dy&IPd6!=x82iJus#{s_&wD'
    'bLZfyMD6;K6?){+K^Vb8zlhpTVL4`ju&8`JeVM{9sy`cU_qzq&v#lZUXm+AE2J6A<9_uR`OI?eP-'
    'Y!gL?g0KH;((G<&G6&pr%)u1H>(4}67EupR%G9CIbYoVSpn0UuDs2yP;Padu-qJ^$LgC8ga2zLyMO*%ZOHuu5wJNp1kXLYj|&o'
    '4-'
    'rm|8y~KC$%3dL`U|0!yATKWbv*>=94}(SGDvCQ{jf?y~U#X<F`%wfC{d9D5o1Lv;#%f&<nGW%26WE$)K3{2NzTipQ)SCasBA$E'
    'V>^Nvfm5jKvWYO8%#}?drQvnXPu)aHE>uHq$-'
    '|hndfqq&l(wRGAuC(1>h==;M>O^2=nZtvpogT}&JRhMs%`<2cHZSWf8LeSN0xIzgx+t&lWhciGT#*QH83|y9pKpTX5)8!E*jby'
    'Bb#I6hg4prmaQv`t0Ko<Wj)`RIBkZN$MFcyyOo=D@Y3WD_Y4D?t2p05xj$u5_<HeWBqA30Tj;c0{LTV)!mpBaAp9+b;D$Vs|TE'
    'YS16=JY3z3dbHOT^fAX*?K&ZrX{RZPFA(=6T~~Bt*#op8742PMy7ZANMmzM!;a;Zl~_a(IKldcI<O&ZShQ$S94qbEoVm5v20#R'
    'QPHcZ0RK0l?!EsIs9^fri~eKTbn32yTK*k#FDE)3)f|Xe!PdZYK|mYy9=WOkvFfwGkgSJVaLwc!oDzn=ObAiRcSw5Lro#ua!BD'
    ';*3tnCCF`=olTyr<r_QG>hte4J{m8f0OEbarFpcpkWwvRn!Ub47u`!vb7rS`WL#426;<B&WzEHI`f+X85Rs#?9h93X1k8!}$bD'
    '!t^(*F*db2CV&&d~=&46%z_WHIPNWiM|K+s)O0@d7BP9zzHg<Puy|})Nj1iTt-'
    '#BixSw769}CBxlWn?XssjHwEv?Z9y%;Fo`M4&2Fd#)&U$dmOIDBW#7+WPZHdI7IX*D9!Pvkk{;?XbXKucXF+&|o%81RBAGPmgR'
    '4xGXK_bRDS=k7I9#tqDm|Up3Zbeh*bQ$bPj%!3pE61wk7>v{Y`@L5r6;k}6f2FP4WQN>Q(}uK}bRI-'
    '?0Pxjlev@A8+UACCx?m=_6?E4`g~Kq%!+nBWrmvD&P;L%@w?~45oX6hl$oU=Wy)Tpy9F;|GZ)<drIa<vQN=aFL9NPTMK(3-'
    '!v{@wG#1HfPxZ@vsI_UA<omHcdrYX4HbiBH%#cHA{VR}*qk{s*bBQ;v6oNWXrTJRaIVT<AzAgBhl3Xmoh-tw+#<O-'
    'Yg{04irUd&PuMHTKDZv|dyHuvz;3QVI17Nx|)@)T^-'
    'x|U`&1(9zt?P<H7Z~4d@Iv3&e$=jAlM}+8v8n0TWfiU^>%QdP!23Oj4pHrbFS?Kpjy)JZfv!W#7lwP!zwj{N)892jMP}G@e0%r'
    'FhkU|!Q$Ej9tFI&Xpm>1s}!rR-xxE<|Znmnv1yW*ak*63KRb4e@_+>Z)M|Dg9%MieBtd+-'
    '1Xe|9hrE$VfCTMs{#R~Zmg>!e(@MsBJ_XbbAa2M%*l|H&Al6S%c=%F5?;tP^e=N`)p(W`RIN<MfVz8f9_dIwLH%u72m=oH~}z#'
    'r|tP>puG*D!dlqn0HvY{yyyHGxO96D<tFS*u6XTS&S0m){M{@%?@b9X4e3{cQ1+0soQpLMrbBLj8)8}$Z_U5g1LV%o=>z3S6;K'
    'TpLq3Hx*sAcX<V9d`M=dd;D7`yQzEI)vR!Dq8Zb$Hw>5K#%LIVjD%KtZy_z$GmW-'
    '?)#Fd9T2p|G;Nz8pE0hda~o*3mfnhlKGDxnP7|GKlbRhx=^J1lK(0IHIy7F2Q;zX|$IgW{kz6z-#DnqU9hYBOc9pfg|AE(Q@T-'
    'V|7eOV`>u)@|+eTtnrW>QY`q39G06Is5&_6po_mf<CLVMNgo8k)B3yjdL3IFM6US5ZpsNhOL;(gkP%K*wkOQ6sLqgb;#pmU9l5'
    'g^hX|VaM<M;pLpf!e&;$X!X<Y^93A*VNYWJTs}0yT1?+zJc7&aN9Wft2a?+uP0B#6hddNC0<lT&1fbaKqTEDwY8H!2NALwApG&'
    'CDz>drZ67;ikd7%IohfjesiVZb`J?Mq^+N)Mvn!^4T&F6#oq8F#BCu*f)wxHE06%SMOR=>tYH%A0K)c8)metid=qUO+EN>39!`'
    'wp)!G=n^`c(m0+?_t2K~0RA`KHu&t3TsUIOH=5&8bHNo-'
    'sneAYTQ6*{i~;)I0ROu0HO|=2eb|T~J%el2XTjGV#B`>;`rm6hJ?mWYxJaObF!z0I;#BLxFEi-irrCK!>d+++xo$t~gP>7V%OD'
    'Rat=_=4_tQ(%UV{^nq`U1Rb>Q7`QZ3(J(SgC@y{X=sZ!qQoOKD=`6kr0wRmDi|<m-{O4U>rh%EN^mZiNfzMXW}W-'
    'X{oJv7jkuDWYH<e0vSJ3ye4uxy(Ihx|=>ZdtI)}i}P*|qo-'
    'OukCvgLqz7jX=k9_{RN7Q=_+_>eA1<mlWG~~Hw}LFwPV6IB{I9pR_{U`dL-mX%AE@Xt!T8)uDgwI!gAW}!HlzmYpPVfzXRF&AX'
    '+}5tPWG|;CDCvXyKY2e+EHZx?Wvjx=i^@h@zBVt({IMaQdpNguJHHRXv*TWxX5~ehyZU4S=<M^0LAbuZ;bf*;JAX@m}+`LJ+)l'
    'Ru=V|<j&c+D8VC;}QW9^q@ctlp7kNjou)N1wP_WVZD>#gX;N>d$#kIq7w272N##`G8UNc0qZ^4PC{FyLMmNie)^i%ZLIp0%t9X'
    'x52mGpyVnvK-hfqWDKZ6~F{T6ynma}=Ya(vQoS2QW1xy_j_AAC_}Gz(RZiTrBe%Y5yS(mWA<@a6>_W#h3dP86{+2oP@r=xOXY@'
    '&#hBHFf5>UEn0TPGcZ;u9K@5p=FTA6o@iBX2w!0rh^@=o?CF?JM5YP`mglS>=VwC{9a}2`5kS{z3=kIpl*I#{GmLP69w%2~O{2'
    'WP3fk62$)lt&hWMz@v9$PFC?cwHx452)>@aelJ*}HGQcV*vZOiz=yqIAJdR$_QiJzkw1@8b1x7R;&epcoM6J1~N%wXh~9&LJGh'
    '>QVDRq#=h<Dw2mVQK?o3thq++v|y6hx|_Lfj@+7fkZ<Ur-'
    'u=~quA&R44o!(zyus5*ZcUTXk36z83bzJV2RiLzY0rtu*(2C(T%R{TV!a|BlafheHTM@qdV=dkpGiq-R!m7IMJN-'
    'c#*xjQ~!ukTDHL7uF@-'
    'x2VP2&q>j_6Y46zbGJDg>2kUqah}3H6R;g$TJD)A{Hl9smIT)LE@t&yt5M~aD@wH5WGp5Yj5V2=}FQQNzM`OMy0Hx$e7q|6+Rq'
    '_(A5r>_b^)XOvQtfGAX`{WENvgOH*3I<vTUjrLL2M*|1-mJwl$#^KGe{l9?mn$k%4zbZ{uC9-'
    'L`_GKQyTsAi|1KuK^~9lxwAyq-Y&Z;-'
    'rUc~BP>|04@=A&+_n9{V^g>k#S~(Y%g{2CgFUh%IRv|0Y;hnF=_5AMRIj!IK!8zfRS4N5#)yvZ#Ne4^S(ZXD3LF00zJq4u<mY&'
    'ffz=)&JGQ1{{+q?e<|;T`lES>=2grT7aWL43rP<B)Dc{5Su^LKa+j6u&x$8)=&EJ9p2@={{1HFh4XH6GA{#|NyD?(|-'
    '{$zsoszYxWf<LP3O(YM?S^)^`b|_v`P268l1rDw_Z=d7vI%0nrkdQHB<RK8}<;!Ph1O4p_`rJx#^2)KbauGHx&Mwwv%gP$=gT`'
    '#{nUH943M=AAz_56<q1apC*&FV2K69#N?Z_>`2tUo2I(PfLAH$X3jKiCpU)0>i?15=!8k@Kf_elQ#q6HF*^U>{uEIzrkolycpR'
    '~XY4=hTE85XL4?;IBQfikBpDv?I<$S(u>f-L7^^>-Gn*N~nHi)sD#Rj?WV<U5z9p4M^GO*3*wCJDNq+O$3{*z(FU!SMuux=*L-'
    'pWO9CrCQ2AE$D!T!csOe;fZJ?Nn$+^E-+oE^88C-|zM(1W@Nu9`p=V;Y0*&gPbk}{uxx8!RSb-'
    'n1cfnFsxoR5k$s1{OTD?>bAGl_A0GzA-u9vg2e%MG$WuR!I^S`vjcLSt)EfAKD_#ft`fR`5IDrHw?WK!Q;BNYvS$?<emI1G-'
    'jc9RSIfhy~@_mx=);~3}<>qHI2TXe8IUQk+uRw$0*3n2Zg^HBkd=u?E&=?2zZJJl}>yH#j~>mZ<0Dqx5F><zcp^i~tU)6er&Am'
    'h2+^yTU*GGVu-ht(+r&Cn{Iqpd=#4_yBGpmwY90<pv<2<pjiv#n7#pe}bbY@qJlh5}H`GfK0#yeD5b1PTlrS)9yxG=AV9D7=-'
    'r(OjadAu&{y(nfZlO-'
    'P*wakQ8r_uacKshjbpi`(>8&$WaF;P}k+uf9A?U(v(y`6lf6llTyrz(sDaIQc2pFKQ?Dpuw0E1ue1zMQqXpc_?yQqfMnT?#(b)'
    '8=R%`#EP8A=nk3=rdJ7AkLMwTdnW-'
    '(Iu2l#Qy>dsbq)8j7>f0?ZZWRsB3+CPE_flA+qy)rHL7(VH)iglD0w+(@>L!3IenQx5{iVAc$>b_<Ee&=FYBl@kD8Xu?9EzUc@'
    'Zz`CKP0oBOkw<8>-$-E!k@L11I-'
    'mV5PBoXhMhbYfqs&Sy%fSbDI~#lUcFicmT<I4|v#Lx*#rw&Ej}E>NLICAp<%bDm)e3lf(Q~;iqw8wQq>42R#oyyr&{aD8ZQ5j&'
    'a(Rbjo3#;r;ETwez~1AsTtv9!NQ^3E_y1DS@qVcm?}&)p?0L0%l^<_BD%4ukS>_<soD~OTSNPjTl4BYk2d9Ly228c^{;+s0C&a'
    'un<!>|HEI#4emskgyQ>B)JpfCHDgmHZhxvi3?xb)kUn^x#9@phR)cSPW=Th{UFSVw7+P)2Mii$wEL8{iwP=inoVB9y<3#DXbd+'
    'ha0L=ei`VKVO_XqAxvJ+NGIW&~!NJugp#%tcmqomEcgs8k}tRCVK^dQ$kmB&+jAT~`{y@`_7-'
    '*OsfiB}SYOgK9sSn56YtpAK2B!I}0k>%?M3OnJz#5w8IdUrpOZOxB=Kz-F`zkct-imBk;Ky}dpIw_W)FJ{T`G;2DFHTMp^GWjy'
    'T)PM^h5M#AOz-}!`S5H9x1Tdf1$Z1ERH#NHwe>;RW7SKpnPgvvpIp+&uBywrxmsjbuQz^-'
    '?5cNoqz3oA0g7eTMwHwBmMco%1S0T{=Oz>oS4aC}EAr^ObOdn+k18_>^K3FZ=y=W8g10t`Ly@!Rc)u64ag!m`;bIQbAbGYb>20'
    '?T~eHQ}PBylHt>1HQEbVGD{PMl;kS-~2=QlFHb`q)Lf6G#r^clZtpY&;zAQvuzy-vstWQ>mIkf-Arsvt{mEgl^SSR=}z-7_-'
    '%TpHLxE;_#MVuijRg#NWepwt3Gd;cJgnYD20ab-'
    '7yTA78rc9UQ@qKluSMV83PD!#~OK*njmH`#=@FtnPw*Jm=6{Z{}f3=`PlE+==@S6+Y^iW!IneFAG+%h1X^XD+a4cSpuP(A|%ny'
    'x|~N@NU{a__fNq1|6;)r%tK15`|5j5hiT%{>o3=&4c1eykeCx{O<oFHSYl=UZ*}xGK7S~f$i=c<;YjEN_p*WxvR#75KUqBn3xR'
    '*q-jN3ymi6HB!1zv5NXW@Xhlncioyo<#?$OmfGIOSs3-aq3u6|OP$yL518jDUxJv-'
    'c!|2t_CNFU;@9Re9Igw}NH<W*l4)tgq9MEj?eH>Ku>g^*S=YMTt^ybHIL<ApSzOD%Rc02TEbKi}1N_IyyX8%I4sF#`$f68F2Y('
    ';PnM><G9aAHhyFAW~NNKq=|O>$1o$b@5yA^UE^WJll-'
    'j*+p?K%@;+>=AtVYmh$(FuhxyeI)8H}geyH0ouY2s@nWWCBQ+;;G3=;0#{U(+r7Z3@WN!<pvC9O3yp~Wnc#Za5JfUfk_d2)-'
    '$TcKzc)p=;(>GugGBBv1xy*<&7wA79g<JE75OjY(G(nbu+m!m@5`*vNdXYI3Sjbn5>#8aOX8<wk)+kf+7#-'
    'ptrJaU5YmIs20+dzBWB(y71j%dx=Q&X-_0-f}Mz+?fC-'
    '1xp(?RAtb~&0wQ$qZOpCCMX{}kB%`ZLD!Gvig=FyS)nCA;J9oh?5GuDog=(FWUaN`b`;+?^80M*84`r|i?jBj#vlTVuF~FJ#$~'
    'w+4qZtcgof*Q`qyw9ZBA07A9!GqVZ1gY3zW$E85u<AR1UbwR1`?GU9>jh;+egW+s?`RQM84Cv~(KEl9eT_@qPWKn#3)X<Gw&-'
    'TU0{gsR^AfAflYQv`z6DRc-So|;z`A+=kg+n5tf=RDk+gA?X%LJlmChTO3)bB9hhEFtH5T!f`OJvh}Ooj9*xL6W}!1kgA<VR`}'
    'Y2BGDnqg}F6V@?cL+pKSn<`_q_C!@hZ{5L$HTD1zPVR6vC|WBJ`U=Y}OAa0tDzD&iAlGtOaAEH~jf2yXhmQwrDL?wsjKTvlhX0'
    'V)R3N~)aA{G$MVp39+BqpdHEw9g#sxmyo(D2>?CF}yB=*!|wH?<H>c9*O)NvTyg{zm~Bb%kRih`pjsj^e&$*X1D-'
    'Cg(e)0C8&{x5^{t=z`Lde`tW5MH5et>C49Z@(wzHrP{JfBR63oR~-=By9#mKl~V5#|at;O-'
    'sy8Gyk&*@&q}05zJy`s_rRwRj)wf>Vql~@a1;+*+A1OjBeqktd*s^zZTsq0LU?=ybM?zW9u51DWLDSkIlx~?V4hUbil`O%i+2j'
    'UT3{`&C639U=}>z_bJ}p<=HodO>fWfIQ+wKyDF<kN`1&TGW`$TH($H>srJk}>P7ze`ozwt=dTPCM|C<$Y_1T9ogdb_;oZTtC3u'
    '%6O$sM$<PApma0gMhPigSJSWG)3VK&N~5Q-$Y=ThI6%e%tG(gow85U(q+qe}SUSEB_@_HFfz;-RRD4|l6qD4sd7c$)LJF#o-'
    'M9rHu$W~~(JFzD@lv2jmY31p^;q%|l2wM=z!Vn=M0uIS)svS0Qg0kjFdStweoSI39dzQ*Z${h1r+bpnn#ZNiLrB25L#*kfzn=u'
    '3OW41#Z&#sGP357I`K(w3PbeO3*H99yI!xKIqTlZxlzuq5J=LK-OUX_)Oa9ks@$=t#d*+GbYbqB$(kRj~Vb+pvSrEHVRI0147N'
    'S$dSzvLGh7n9oBuJvh!9al5OF_*wN%!o(JHy5tHV^yO7m&>DIt!1y9taj|Qu3V~mENR0;4_3#i9H$~9;^vRd$*{wW;F)~avweP'
    '&_6jMNbYpe@kNT-dg5>+C#c5{3>lT`m^myhefb_n^O$O0k%b^Jw3HwM<sR%RnkD-D<h25`J@C8ss-'
    'y}x2*f}#gV>eCt~7$mlijOplDHz4EasNvmAQ3*;Wz~(eKL(&e^J+79km9=NK4?lbT3`2K&JzIiq@jgTU;wHuKNit<=U4ps0Xq9'
    'tV<$Hv|@UajEAC_W|n{F^|;go%l$v%b@GJjFSbz|0$eDrQLI9AFkBCi&Ly_j1>zQ`ky2mJ}1Abv*J|5fI(o!;duID#IJhEY{l_'
    'oRM4GPgAkrMk-oP5q7sZx$YuiK%Unj<Y(9Gx+k^iF2#7yhbR7vXo^o-gtv^k1gXk{ZM^EI+lXyc3D3*_C$RU<>o%0<mZrMY-'
    '#2M-d*VD6;$>gMUHIqgCO@H#iTFncp?`1woe`#E$pQ7mzV+ZBpaA-'
    'VAt}h^@%x0Y(IZ|l?OBHOqDATlFO;eJQ?pQU!-HQOuy%I%;kOKocwn;+rsQ-ybj;c&8>(Ae6QLQ$;5pc?pZOBE{0iZ;>c6E5Ee'
    'j97(wx0Fz<$@6tyg=j3y-2Ra%U12&*hnl3vKYz9?;bD$jj+G-aSOn6A;-'
    '7w}d7H8@t&Jn`<e6c!r0uKL++EllLw!LzI7!GoJ(iS-'
    '2>G6kCIH5g*VFSUt=lK0<w<vDqEX`z)bQ0R>|7?WYWh4#mg(&aMqLo46WcYHr^bOX!^?6MTl&Du%jQ2(ewx3B{J9~3F9>6W%An'
    'Dl&0^t3-'
    'WpO;GOP&VTaTRoiO_^@^YQo;9@Y)=6Paz0gxO?vNFC?G8rzt|Ykq%C{wRI%{Mob$tDY)ueX>=Va(UT;yYiP1(?R0de4H9}(Z+Y'
    'qR!z+%MC&&jBUUSr>NvH#lK1pLq!2+vaj<=*QCHs*9(ol@M5B2CMH<FJkKT`REDEL>cG+=N+nY~G?E%r-'
    'S5TBUE!j5}IlI>=<3&Fia%pLzFcc%yuGk+EG+0Arb9b_I9eLB*$$%}o|mf8Tj1(p}k*|G#Kz=f+i~PW+6JMxJ)>BSpjV-'
    'v^S$$Mpf^Q;Y%w%CZ7AID$+(7(=EzJ3`Gc-'
    '~OOyXV(3D_Jw4EwXb1W8?N_g5xB~XCJB9~qQC$&0jcj%K(Sk&&2(F&b|2Cg@DJeF6FBK03sQnTKKQF$_Ihym*e7Y>=732k$6&q'
    'gV6UAaIl=IX?0O_6$9aeLFxhe03Wj@c4l>k|2{eL}ZcK>znq>2*I6r#`$}Z=R;L#JW#g%}${7a9Kwgz2(q?;3&??;K9V@KJ>zl'
    'nL3`#ZXX#(*L61S_3>(VxTlOtLYpA|Tx0E;5^~N&0cHqgtR$`?;^=3%Ct^0JH`Uvj}r7P2Fi{pD{Wk*NQv{U&bG1<icKIM~b9n'
    'ZYE~nBmzDWRUVekg6%x5{dao*x~Bk8M<<+3Ptuj~8zy;T=~WW_Ou%V1EGuEdYoY(qPHd?rZb=*{w!IyRB`v3$3#y9y^@;$thl('
    'VG{8W2os@zU!!dHc~^8>>3-vh(9-2&zs3xQ_2yDD*Bq3&*6bUeJFh?QJ~eX2%|>0pZca^B+S9r|Dky2raqZutiUKjJ3`FC-'
    'H5`;+lGX6ZHh9cNjI{hljXz-zyFmKUPW5!W0UlSGEwOK+%`RQ#=N3IeyLYQKY!LsMzl5~$lmBT;;G__&scTLV<3H|g@nqmK!9O'
    'tJw$e4=Q7%AcUQ`?de4EWeewZ_Cpk$uG}!&^Ttrr99mgN+?`&Njlcl6s<k8`+rsowR3YkAK?xymR>N9DBLyKv>U@6RgFZ!I-'
    '@SrMj*8z?N{MR+q?21fFUXJ7M#W$4T14xV)!F7((vDp-zF|?i0XcO%zvfa&GO%jk-q=%IQT1-d|8+FX=ICh!gKu-'
    'H1o_Slh=en|L;EUBfO3@2vKMi<4&;#H6VGrhZtRMxze^o;0Cr}`aY+<{d4XWRd&+ZgXwH)wC8(qxhfeGyw|NF0JZq|0i7tChuj'
    'L0GGdz5d`KwXJxdU}s$cBcI%}z^-YFWj=KAp%T-'
    'Lskk;<K?cy0oUlVNuILsaWrS=l+Gtu<y`Q;t*YNpuOzCvmJy^ZD_m<p6%8fr#6W5G5&-2yc6GKb&-'
    '`SrmrPR*^4KffueycMktXV;q}z#pp_SFc8a~%|+V=#)r6a!BuIVZ1x-r$5yLs-'
    '29V%DzNXq)?N#KLSZB$3EzJBg=M(8=c}afeB$HB9`8&hYy3Lpl7UA*Mjr!goNGwycr?3NGZ;?q?TG(#my6K`7<EQ4_p$R>vLP1'
    's-gh`KR%`)hZY>(Us;R=Jx?~Hpz5OpwHxy%6%<1f^LS-'
    ';N!csQH8g<H?0e~>TTHW!?K(}d<FV9KPN2}^^wMUd4Gna^!j(>WN2jdmI7_k|kh&h2#nH5-'
    '3RpuUZ$XxhN!#mx8pCXJz>FE~Crcij9o4wIy66_@<@ed<0!$29iVwoS2x1aHp(kQUnSFB;RGdUK_ON<089LvMN3HDVx;}3DuQ('
    'z2@kC?gn_j;st1-!C%n9|DQmFj|j9SWa<X0=B4PNTpp!;*z6>#p>}jdb#|W#^vdfdtR>U=1j}{*H;FYTVyn-XidhC3unvh$}pL'
    'BAOuMgkcOD?xgZ{+qQpp7kdfP`)PeTGi4$DUGl5v<ZV#<m9c_wCbmz4<F7mBi9ii2w5zR7Y0ef5?*vjllf_=w<s%A+!L2%vaOD'
    '1%Ed3pKN=>(GQz{k~9b<JP7n;mX4&~>pnC9%5q-'
    ';VvG9vkQT5whFOUe8d1Fy0Jc~+NYjm3O+J^N2%y=`=NP8QoLyJBd!U?hB(+$~xgs4C>SL^?r~I_*1eO-'
    '1c^06YG!<R?60pX2g+VeshmII^ZO)K_kFv+H6(IDc-'
    'sbBW0$u#Gdq0S093KG{zWJC`O?KeKU<F2Tu`QUTL@et3aB@mOb(%wwViav-QLv&>C(X8S6l^^Ec58V7Y_q;Y(A$*l0&@ga&!FV'
    '1uUj*R?c1KN!@u2?v2y-|PKuL<>?PgT{c{myv-'
    'G4Z2y9OcWwF{}@jszOE9wZTKKCBjrm)U{8v_;e?ToHWjIC&}tivR8k*>VrZmM>yp9i$f7*<>|yds)wfeuw`73V8-'
    'I>1YgG7xRI5qLKz#NN#X0dD|B!QbserYY-Mr?X)0$n_iYpFd3Fe0rrJ0Mw(FN#9oaH?m;`fD<9^^-'
    ';BHCO0>I^2yUR5)MG29iU=JJ?jVDzBsYN>wbHZ1wmfy$!axu&E7&|!U5ab#wkN+7KMHJ%g{*;`}X#yA^{&`KT{^*r+;*mhvZf#'
    'm-'
    '#A{?^_YdHvik;Q<QZSNsK=OqX=>3PEjYCV0vAd2Ep#J}PN_s_>8<5u6K*lbGl!v97!R(k;y2}+dBcN26G^bT7UjG(!xs2HRobF'
    'ceS-3W8u@&p(2lfE{3!CXT2zR?BY-ou;#WM65!sL)q$Q4w|UC#u~J)}*xxnczmRqt(ryBt+_QTgJjgK=;p%>|fr$nyxE7H@dX!'
    'qHM*P;5UUK66j#5#DO^PYC$3e&k+T2h&scGfP5cib?horZD>)SRbFO#`0;slg`?^Cd$eOS7BMu0h^YeB0_Nnt1il`qW9@g_hto'
    'PtyoKWxL!_JmsPfP736Z!I3;C1w*sSB8h>~*n@4M!zssKcIu=%G1&fE|vDnslBYXj-KJMXq()J;2_@~R(mN=!KfcaAuMkeeW9q'
    'j$UzlxZ8*k1RoI&BlDyDI^yJO0~RzH%?vLAyXw`a<7e-'
    '3P|>)FP{O4GIK7t>CJXu~<m8yrrw)JI92O*|U4KL0|Nzb)%LT<8z|Ro{3O~11}ya>@a>iLgmkAGe%GP@F*}6r@|nJ1qlIawBYV'
    'TadVk*%6RcaQ9>?@e&9q!j@idy!+E+tBA5{yf!MC|DGIUkYP?zaTzDvWH~osp^D9<-'
    'T)1Bb2!}|d5*Q3LoPwY7h2$c$jCauGgL&PZtkwJTL$J$e8j^AB2zl^!$OH-&y~X!Z$#v~x)^^5mh>}drd4B3>>ST-aAztSsy~_'
    'rIj8u(6le0Pb18=qqQ5EzEo!njyt24>S;JGjVY3D>JSq(sGTrNl~#_nW0`IPIA=h}wx<TPt;5lzHk?*|$h+z@GNK<)@8Q&k}g*'
    '9o@^zYlf<>tUj$<Mnab!~ED5*Rc~JVkCUYz-6O457l<u56@)bER-MVB%uK(#{Wtfbm$`6+`ADY0F-'
    'gPo)0B(WXnX|KarBz$azdWz)EDhm&Ekvv*&3tivn20sJ)nh!oFP20=tMC<hD28l!Og`fH<%ZUEPv>17fc2&5~SJ+>L3hH9^0Ia'
    'r}r0E3R9dj)0MHNQ@-'
    '5NvtKTE_f*gY8vOZ0tDSHZsu@P_kljJQD!8%!9|u#G{t*mv#qN2<?$)637dbIe{A2PV6xJ}i`jp<f{Ba?kAxqu*<dU#v@RBAtu'
    'nC0lJvofJAkrDnQcua2Wco^h+33K2JrBC=D}%6vwdpBd1(B={Fjwv40a$RNLCv3A!gC#x~^X6rr4zyshHl**n}0j5i>&8e}xf9'
    '4lbn@`Sm3xP#|Wn8o*b@6^dn%?znGAYbe9wy+uLXN2{g!di6Sph7({_@~g<hdZz4QFZ*{}>8+%|>;C|E_DgLJ4LPO$;)BFhjJn'
    '8z2uPr=0Dj_4t}_t(s?RYs@Uzfaj1K-yd*xoJee49V8qXy5*Qr~cbUntElzp6Ev~uxh^9=C-X7)eop1PVCW-UE}%cNkVI;>iN?'
    '|vs^k&(tWtFf}C$@HGs*AgC$8})@$bZ9!Tw=6ss;%I`3Tke2E(&2>jK`-'
    'q$@<s+Iqw<Bho_^(4Ts}+@Y9{u<d;2ULy+!1qgZ|uTl?{`RIgD~g%Z1D2ZiYr==%YH5hN+SI-Z>SSRTdv-VYY8o&~wk;4Z~NDE'
    '>Ri9X=-MlJH}#^T1@rB&SwO6-'
    'mkYF`#LIy{7p)*1Hg#{=_#@ioepm!bkiAvyi8W?e8`CIvkW3~><EX$K`V8Dqc+!!(?7hI_7lFG)eg;j@b{cJehnE!q(S^kmjzv'
    'M2_td4c{~5dCj52ZPi(x!VmJ))$+A0Y1M4&rc48qX@#u?cz|;>@TF;jdRBwFt;Yh|%+K2S9ke^B^z(>iAf<@|sJF`y1bRT%j(J'
    'UH5cF|upq4V+?oYUfWIC#wQ2vVWRew7l|H*r6><WlUPrAvYl0Lkf2BqDq0J-'
    'u%yhU=D1^Cf^zjjbIm;n)0b338{sDPTx1NBHSH5S#yIME86g4`srHj|g3R<`()$vbifxWXCB(+eZ~M^DwiYJGW#-'
    'D}#E{A}jw8oebhzZH@9EfLBCg;LjWYu#^VYkX#0RZd3rUedK1Wo{viNb^S{M0B}nxD8_<=nY$~OD2zpPtzaeTsrXz!m<nmknJQ'
    '?Y4cbkte&Yt7S#U09jhSR2&f>HWzm!S(^%>7{qYY8b;9WHVwQ97ns@SiF=h^9N%kf)R-'
    '|$!|1;u!+;!!qe9E|jxTh``SbfA<_j%FYMaDfDgXe8Z%M0cqunC1@rCk#IU_?WQ5{$*4c6zW!cj9Q|*yKTBk%+cgRdU!A^W{}w'
    'U>yrK?6i$H-'
    '3QEfcJe66h>?^EI9Hz<jpR{$&+U~BMb5%vX26h;c0WP8=UL*>z);vcTTb*SxA5w8au<^K5)B8zTv=eNmBNFH1je6&AuK{3XD?@'
    'M8KflbvlMQmpu*D<zPkLvWVIXeEu%UK67S%ut&p8j{HhZ$!``V@;K=O3hRsSPHMG%ByEw+gW5QJ(DG1fXj3^=hG#|Z2UeU5PBx'
    'mY~*kEnJP%?b@|e{wArHH%H35`p6jUbb!L8IYEFx{x1=nR<G{88VEApz)^<NZaHvyUPJsihroNwf=>}{Z)=xbpAr&tbb$h*v}j'
    '=i-%Ud#Ma#}B8oRRncJJ6@KZ8BT8c{mSxXkFM=mM$(-'
    '&Zx__que?sZrvBIV5HB!gMT*g<X@4{=ZxVQ?bTAZD4dHViVU0+d$1^2aXxR;u-iCI37&iGYFHK}8^RxkyxEKp`uFlY-'
    '%sR!;055#m=aBW@NUIdm<TD68gaMMA(*6C8>-'
    'C4|>LTzg`z3aR_LTC=XQI(2oIEN70%?oLO+>5X<7?r+tQS{Tm{6IKj0hZeF#y(W-&S;DmRoW-QOOXuA@*i`7?1YG=MGOVbf0g;'
    '?cJzd9qUy3i(7Ys9SzwCYS^Sl2Y$x}#9VTM}v#;()rYVqqk$}3hx?c=>-)vQ<cTV(|L2_U-'
    '7aRLcr`9|RO8yYq#AK9ckijN&}FglTUJ;`zZZ&P8W$bFO<|3b55o9tVfrXSF~oD*N{JcX|2-'
    ')#G*rv;wH=tQEG=$_2cmun)U9v$d_nnyp&`m&K-'
    'hF9c?<FoeBo(t?vXi~icr293)QDx>+hdU#9OZUb7bOVlVHq#tzw15t>&@8+!d8H(TJwSX$dCf6i1!bx<S>Z<K@Rhx?XL{?1fte'
    'opp)vd6Y6sR=SUR3bb^L7LHmR5*<EhnBihdneU^MSW)Ee}$V<VO0Y@F5O{_pZIJ3R{VsYW83D+YcQj^ZX5kH}|g$~3tK>$9Z)c'
    '=R?uFq62V+u3<UQ{@7^&1-vI&_4z9Pm?`Oh^d<xHgM|HR-Fn907+6c7Z(cEaV$T=1oHZ5SyC**nKj2zS(udSe$dAI84|MP!(p#'
    's$PgAJfI0fA1ZYm8W<G&x()TP#a*$3&Zy|-))1vh!Zpi=U+9>J-X8HQs(LE4oeO4h}VC%ECWCt_TBs8hx036i!E!26sJeh^-'
    '&JK2ktl1wbSxcwpx(5oYXe=Gqo63>N8ncpDJgaLABDIHi*E(depeTaTh_Xp4Jv@cV3hL5ZKVvk34@xRkQ7mlJ!tP-'
    '5aU^mv`AuF$UWt~z1sfHvo&Y-Q&LcUtA#|daT!enSc7mBgMCrx+w5O;mij0c8WsD)BJw{>uvv1|h@41#B-~bz-'
    'kKmA-3-1jxLjTa&u~7>JrDZ~U0J)P#rU-'
    'PP1QqX&S@n=%3Bgol_i6w&d1fh@I7zcm@8g+Zk4GfC1x>&LtGnheZ?gVp2AkeI1Y+Mn+}trKvSeczi~p}>ZLaJ84XO)N2Xm-Ne'
    'kqm>HjejB!Z$fQHEHqoHd;OO2P(n1q{mb~HYK6aCFT|%*vRNOo5srvg;XoSxs;wqaj72u@pk@5?w!Ief9#ZNR+&7qF8Oiby#T!'
    'lF|8zvRsdDQ0uZYSb^NIjcIG4RRpI>*5s>86ckto=P~IiI1sew6u;-%i2-<&jLB~Xx+E#8|9y!K6^T@*-Q-'
    'eLr`8M+P^NH1Qwj6&aSn~KOzmr-mK-4^h<X9+`C}kUX*?`^HR;<Jt@Z00mF6V7or~~~pj=|k2*VAtrXxO&1oQksl-PJy;(R(kS'
    'fESBR9eclhasRm4hesHV^YbRdKGadAZ$Qi6EbIYHeN0^`@;f~FXE2Z9_L)+5HPn^q(+$+e=5v-'
    '<1d;W$%&G!y)4#+PWHuUzqh~pGW)e!(IhBXF9kSCyrYE6%U+Ym))4f|tYJ>WnPqt5&0|+|F)Ft#2UFvXl^)tTZdM)0TRo0!16+'
    'WN<Z-'
    'TBbj@bH+kFs~rA~exZ<3|r+^`j7%dHG!lG!??~76d`7a+hhcr3J4YKI&S~K&SPKS7juVX#U~(s2Q<;ytES@Z<)&&t|rU>YaFrV'
    'nf1(n%KPfC#<XCxNI?S2#cY+SMwHV;B`Tc#|LOJoM&bwq1H4%^QZrc4LuJQbp?c0Tv&m48WPOqqrh6@wSbKvkMl(E-'
    'A{_STPp;^3cRQW>yU=t!Mum9wxH9p>Du$4ZBZ;t)MP8LSR1#Vl+|9Tgm49Idc+!kYY-HK*Xf3wM;?-'
    'lg(SdzzktyH9OyFDrN<0+VjthM7m~t7?P$ad+lDS_`H|)Tsv%LE@e_)MY$vMA<TuP27kj@?t`#Md<+g>7mn?5hr)Cr9Q7@<PWq'
    '=nQo>UyY4N-|_m(9_7w+=Qp}uu`KHZPhAV-'
    ';rY>&~Oft3Ajjg5xTYY4Fl>)hul#7w4tOzjOO%)K{#mMoU(Hvywcq$D~M7R8p@5f0?OE3k()j_qI#=-'
    'ekl8SR)FK+l<S$x9a#IpG~||fik~>**;I1B!aS2|S-X-RB+H%|xm@5n=#652Z!7cya?D|%Tdm|{u&b<=<%riC(Mlk1J#@TSb~%'
    'sm#!xxo_Fph+l#YadV0?kgrfne<=_CGcqIq)s?ugs=q)xyjM!w7Y5(0{B0+CDP3pzIGDaP>Yo`pBc0tyfi35;RFwAzvj_;aiFP'
    '`8F<<?rfbZ#%F3wRBNaGt{SL2Iz5;DBmN+=n=TxF+dPe9Du9@@~5%S*9Q{UoBqn$ARn#3x<19Nqj-VO1ZjCn>Wno*d>a-'
    'eiWVSRKTM>rpK3*$lO!??se9iuWbXKLQ?QZtMQSb>Kr|K5@#T=<stQZ^J`p2=rZTdzm-'
    '+ea7u+<$f%?ZqlhXeud9h?9Wh<b^7{bl|E+4zj3g4&<lmz2|8l@(+!g9Li3dYE`ez>(LdDqC&sY$pQk}Dw1ZwtZqxGtzEcE^2_'
    '3l`uwi`&a^---o}-'
    'aKG32g)_7;#8ovns+yh+`|c1=(}e#2X0)O@uoH4AEbbjG+xa=%8OL$qn^N|wo}$Q#4h{e<8x2e#*Yp$C_TmF#+JD&d%OwV(Z?g'
    'U)PRSI9ssS9S#mEM2i(>?(Cd5M2ft^LI2A4M4=f*yp-du;)&zF{2pkz;R1){jU*l%Frr8*sUXQ&bmQfK$0_nutK(7|eG?&RUq`'
    'f=mZ9+W(Dv7%u_-1t`OK`@DLFLIUE%8m{I*s~aOX)|Mb%{>t+j)0*-{GBSAiVFhGFtAWSUAR3B0X-'
    'yQX~I!wNPkwWg4e*t7McWQ>GCi7bS<4V^N&-zZ~@Fw^7zp&qox~6GVUj1Zb=Q-$*b)2`SkzYkBNI;KC}o#f-$8&o-'
    'U6J$hWndUA|qvOX<tSM*^V^SVz(U^|oSZks;hE*=?jLx2Qlkeqq@h8wK#0%yJ*_TcL1te8c3-'
    'vu5bG{jL;L~WW#fJOGdR(s=%k8ph*Ur}Awl?7iL2=cw#<Mz#ewX@4eZFW#^d(?Yq#fF@f34(V+^Rv~1llX}e_*bywW-'
    'I)Dd{)%mT=c4ARW*Yr5bF<LY&qFCX1^~pq;>BrS>P(?emFTaHAq(t7-O@vpm-'
    '=2Ue@nv;ig!kO3&=5%Je4)1;C(lz5jnH<<f1x1Ipm4xLCkclGWYA!wb>B-'
    'QdZ$V5eaOO61!V`0`dI(hSu2#a3IMQ!!JQaOh6jZfP0|gTNkZegF;AzYCGuTSw<9&AH!z?$jd98BaYtVWk#s<62P+D=c2QK7rD'
    '=#%LiNEBjUbD39CJ)t0fp@ifse4t`+Z6A-'
    'OElP_s`oDc)4Y%rV$e;j;{XR25tCLNkLEvo`H3K^7v#|R{NRcq#XguX)Uw0>e1=XcDsTkm=xjbT*#Glrell8hP>3Ut&|IU|gXJ'
    '5fTA9?1BpB+^VD5>3<d^O_^~QPLW?#GnKYgEAk0$`+iUdcd7T8JnESqT}+&_tMis*al0~YS=Uj{4}Ea-Eh_n_AF1sfvA!AdHL1'
    'dN-'
    '@&?=W}5(NXaUR_>vw==}~>M$WvHiGqN5m1_AGJ{o}8$)jybC4=$~NR&1C*da{zSUd+d71Z(#Bqo{6eMAl>_xJfl<empO9uwNCG'
    '5S#U0d6~v+yH?suRb_*<&G7-'
    'l=aOpCYAU=#u6)h;xjG`{k;;Di<XLD!Fe3t%_C5<jHq{G{zhj7O3OHdOq=21=%K3)+g<hLeE4n(ageX*lJ6F{6FwPMP5mvZaMI'
    '{?Xvs+T`3#FvD+z~QF+6hh8rlf_!RQBhF<f7|L#*q6JK%RU#+OQnQo<D3FUhzXq))Ot?gVQ|S|4?6rrW*Mm>*uYlbkHYVQWBIs'
    'z6T<*0aCLIvHOW&fJC;=0P!u-'
    'wc)2zP{HmK;@iNMa#48e$`{)QGL|ABo|<U~Wtc~immf&ZE*bF(yNI9`dG!}aQ%K9fWxUaR6C@&N6FYVfD1zpGq^{SX^Ri<1P}2'
    'A=KYB^n>ovnHv^YX;IR&|OQ22W?8IH4Q<MRLa83ySAE;@2R*FR*7;@(WMu9XnzZNn{j^8&|Y;hl=m!!p4q)O&KfHH4n^uu&l=H'
    '&jfcW&CIW;cy_j3vkmwGiWs0!?(7~1*S~eHdVunEC`zrg$BHA^gZhvMIzAL>gi*nYAA;#Y=+f-'
    '&9Q!u*ZguGZdIZcIi^OS9%*$B39P$MeP>n3w)!_gTWR$aY*)i_Q5840Oe-gKm$({Y3u*V+9clc`jw;tDY`nX!LdBvFGvC>d#xh'
    'ec-'
    'D=>dgxlXG^gru+Fr~6Q&7*5KlAw^AXP1l7ZU7qp!RQUs<pO%n;8#>sPl^|9qv~yk3cPAH@$CC>mh4)PGvuMpX6>=KG>P;P{|qi'
    'gH(8i>Vw5Dj<GsSrcK8@DR&KnC=eg<<)kuRrsQtUDPze}&YmD@gsuFu?&aa9@4BaFvKywq~6<vg(Jqe9KSkRl0XI7&*%!ALaB_'
    'zwL7!KbO*1Z3en$&a%BTkma?E)3I#qRIzjFYoY@Wz?I6MUYgVQ^}_H&3ljZ8--5nht=G@mJ5#d>dx<E~$LL9GNl3POjhoN}HNs'
    'qxbRFMkCo9$k9ZY=W^XV5&0$56p0rdf9AV-G^VITRuRLeucNCjY7Y=VhVB*|(=<zefu<`cj&sPoBU-QAP+O42Z8{0+Dreq#ztT'
    '=ta)AKivmWSSzTD+tfcep3(#JrAp?uhuqo!~n9+I1OHb8k)EcS$!@Q^|8im%IKG=U_}Sf$b2#g1(iL#JAQNuqD40cJdoOq)L8W'
    '0p$eWMfr1nE-bU5EfoP6Ph>5p6fK5dR$l#jcIPP0yQU6zDAQxC9Sy}o^-HeqG>f-'
    'KPslwnbSKAyw3MOK<j|Y^g<g<ZCIs~Q@u{)9ayf4l%u&n@N*{eUu7;$&RopwWPX~DvRBxv%TDZQ(Wn!|{0E6xq_<M)HBAq;MA+'
    '5(bSp+^G%Q{%wR-=J8q{QItVx%#f@TrS()(n9-;<%P=EAemXW*p_NH3&Kj^<<Ke<PajSV}eFrDXT86-'
    '+sx7skWudQX_NiKO<4zt6*HM@S95LN^v)m`qjpVAWnbpHp~8i1KngawO)G6ZNmJ*fPRWty`CLII8ZnB??GG8*>hFb8Qr-'
    'EV{iiL=pPVqtPSK?HV5`A(G#+N$a_zu>)&yQhU>C#dL??S>G5$4Q$sSu%QXp-yTMyTAoNk9?NRKf%1ohvmp58lP<#T9?#t$Z`)'
    '^|Y=PwdmpLZsbfxA>_;o&tEdWf_I9oN%EsIb!<|9zAN6VEuE#M0kGa&T2ybb!4I@}i~3GT)YyzO7u#+lx<jn6fS=}dhOq+K3Mg'
    '2Xg190V5gCt3LuCf^kKty9lWyOtu40yjZ;MBPkmV)K*g`OZbDgN^ViOcqwa`0$@L&&`;C!#EcysOr@hK?E3K(@hApDj;is=Tl!'
    '&zgJ$=&SBO03WrqBC6$N{5DfM;t?FKMsZ}X!M67EoH9&(n(i+geCmNiSXon=`D$2>S0-'
    'v!ixKB#UGmmtp{kg_+EO5ouqY}aPHG!ROf-bO-TShd7vlGU`r_OMXQyxyaI74mCVb8@_IvoWF`OJG0=e5{aL_tl$>$goFHDBbm'
    'oGk!CmwWS)9`$yurYiP|iEkBXDlc5NjG<q(<MC~MJ+4IYrf}2+@u_K&=M_%sZzg=koO|X+DqOI=2?#h@zzo#tEvV9FJ>(XecU3'
    'jZ*l3)QIh;knF=pGvd_$ze+k=A90;LU8Q`YpBtCsz(jx@qLU9}zE+-7OXexYXZ@*{xM$c9?owKIkAZ)eMO*kf^Pf-bc!VSXw5)'
    'crCT(wP7D^4%EPB0<v+^1>zR4_iNjrxp#@wX?oOdPIAuESk5OGBw4;y6+U<V8pPDwK!bt;dch8ABX}V>9hhCSzKrLYARhc>qUd'
    ')nw6kyvVQ<)_kQ(;8#Y#MKF^eAvVtb0l-uFiC=8@b#ieIbte*Es;A4q)@6vUXKhpuX+O?_CZ|U+ZjVjTie?o|u-'
    'j>NfjqFRH%HS+F{r7|>%S6CdEe;4ZgKH$`w6R7*0Z^HLUkm<?G5?(bNBjL)P<lN7hSe9SeO$dwCuSwUY;kRCd?!AcrJ%1zMDr-'
    'm&Y+C}PWJHH(Fjbw>DYJc!~T*nvFUZB;}qTU&NNmf&SoXSdMGhi-'
    'X*JMo~#Y)3`)5L$rk0gS^f9*(KaL~*hJ5(fo9zTxT9jy5md?8B;D<tC6{!|>1#cj)`oO0n|ZT$4au9+7saJSA_po0L&L0ZgfB4'
    'j5e0Zi5>$ZoKn}6l8__VeP)Ch5IuOc?CrdcsUv)I}>ugF$h>oy=md5GfhC0fE=l=^5Q;f>j+%Sk5Iia(|@m+=Ja#tU5K28@GpN'
    ')Fjnv?_d6=`+{(0`BbCO&g`ro#fV%SOIl)x(yE(m{VR^02(OO-9ntDl-3n=j=n;iPAmRrP-'
    'X~$!qT?P_tsE_hB3D{WyTX$|0DvgfN!K<YIpLVC9yhM(s5=&=vvRIhrK!n+Rz`THT7JAB*_)_Ie-fFU$vJt&+y_J@6xXX`v$;{'
    '?J)_FR;~?5SRw1l~Q2NJ|y#=d&i^kL02F9&L34P9<Tj1F%6+0tG`a9%jY@GJnWKokwoQ2b-L6c(>X-'
    '&6iZ^619ZO^_NTqs{s=Y9&Q9Tr!$s(9e~&B|DTCB9EJ8aB93ymfUtfMHW)?meV;ai?<#y7M3S}@46NT^BwUZog0bT7X?Ub?yB;'
    '~e~(3mRHJ_79qjpu*XQ*lfVXKX~s;}<opMY%N2C{ugcyAA6GFsFsud;qk7*9{c2#M(QW(-sSGn{9sft=|mC!-'
    'r7mf!^PBRs`nYxf<D%FqpZ@TOifftNrgz7`Hc5tUQ5;rQ-k-k>4KBW;#7RVfmdt?bP=<>>4P{5mIiyZ*TyUF+c2ew_dw-LJmM{'
    'RkP73-l)AK;q@#&urk4)^#x+3{RUUhQcMNwIj*n_KNJX|N-'
    '_LS{M2RAxYGdK`c~xujHLeqE_(^;nsB&D<2@n;vQG3fQZicRE7?<~Y~6%11K6n7V|-'
    '!G?hyJi6K*3rJ%S*u)9~ktZfH3?n7UFVZLke@IED_69p&%2cuO)gw#*wx0-'
    'bu;qAepf6R<nqR2j>*%%jjkT|@VC{7Dp8u#ZFj@tj8SmJ|>#aRiFeeG~13+ZH{U0wEm085ake^<K$>?<Kun4=M}@&hr$_Maopy'
    'xJv<-B9lBeSKbMh$+H?U0~Ku+wR<&~q$=*bToCnD08}_Vx91~H(dj&_(h?OJ5F;8cYxwmQfP-Opa^#>~uHJ#5O0fAQ`GiuU+vQ'
    'A8{(E^0Rv7Qp-'
    'D6d)jbT3~2$sYekAxR^z5!c&IfmtK<`%g$_SUpAs(>isd!S|v`0W7}Y`!+95m|JcKo{Wk2c4zh%TZVy!cbH*DHa3T4a0G_VV*I'
    'F+1O(jc@qtWrJ>>7bLgGDxE8kN?jh`0H)_&X5%+MqwMZRf?8Mv{rz2YHQSeg57_!uWW&C5nThKws#au}Ys0@h;^tf$TapP>DKg'
    '5>ap*g>86QWB`bEcMvCAmwy&NkLTp~wUu8-0D;ah(!DSy#nw>})l6d1H{PS@?u%&?MCyS5-'
    'f=#)VaHBuhDDdnGYHYPY);bp8_Y%Cb_=_U~XhB%wo=SASyMndp)KdU~|i=XUlAk+BAFX2G#1jA9uE-'
    '?E7zy)CQoq9N@Z7UYsuDS;5dU)W&t1W3jmgI+1FkZUco((#K?zHh#i;~6ya^6od#Umpq9)qBGVk`{Aa#JA=G{BT*w7Hg&eC-'
    '<9v)77RjSU1FS8)@c7wxxn-+)=?v40>Ny?-'
    'rH*4OXigk+iP`L*{VC@4E^9D@F8NG*}Rf4i|^THxG&7@uth+?XcSD7wynO<(ZjB-fi@rejK0CZ%o$NM?=4ZNK+c(@Tik)BGZBM'
    '&ZBo}7NLEvz4ZOnDdraq>d~u2k!fe^CnG}{D-x_{X*Hp<-mO{L%?ZT-'
    'I)5~w19Es#&B6Xt1JB@v9Il>yFUVo<1c}~CT$B)q)*NC*7jAFx32Q6KbBkUEB+?|s+L$Na&S;CuAbxB@VpOgSUSB3|S6``X(JH'
    '}tf~%C)IyhUBed1p$+tAmfGpz9<FV3^=GC`zqcxgiMV{}?jF^<+{9Nu;z@qAMX7r>zQKYkRKDh3z)vYU`bBn;TY8_qgRQ$jP82'
    'we(;Ohm&bjrd$_&1d}-6M3aaJ@WR}kWp~t*Hn_WTjP9vMIDtUg<$%5$we_IqNzT7yjeuzn<EF)ol@85-'
    'iV)07lbdEXtLI4qK|zqAM?)f;Z!`V0g5>0J+^NvRMMis><Ag!5HtCpHSh_?6u>^=RLL>O#D=r(T8qjylz4_6!z(P5a;Kv-'
    'J{4Q^{i$9T2`3ZE(oM>J{XJoCg+8_R@!W<VbAU#ED?Af-)Ao(zjsVQ{oz+sRVt=Q@9@1-'
    'zNk}2o{E7Gh<(m3z5l&jtcLCkiIkn_S9_dMnGdNgLPV!HjoDZfbeUU$j3qFbLBGTLc@{K$5j|QMJGIOGDtU0+V!yK%m6e<z(Kl'
    'E3mrAB{ruDKLrb8*Zja<c-'
    'q`j)BA;)R#iBymS$e>|@=jzCQ6?Nb?*v<&yP`NA`vH*2)`aN?CQ8@Nk#D_s1gyV;+yd6I6yBd8KDI5P}lxahU>l~_HfgtdM>#k'
    '|woofcj`$k8*CZ)z2<@;7&}gf;%9Xv1x=txuawHeBZ3vSL3t(TGHaE47Cb>Wtv=u2&MCWx*Z+SN8wilG}dk7Fk(p@GWR%LM1o-'
    'lO!J+YUCYwmiaI_=l-FAaBN4A0UMw%t}c^W-+)K}$Ng@X$I5|d>ji}sX-qYUr8xj6pX2`$uM=EmV)#XGvbJtJJjwq-WR)>3#Tt'
    '!>*R-;&$O?K(&M};q-Ep4PBD=-'
    'DNV6%>H)N5iz`77gsPS5n`GrJz^ntK@(yteM#l$i}H6wotdwqdP(4*I5W{B?8Y=W9?+=tg=gTK0IpC;A9%n$^-'
    'Y|A<2UFieBNlY&N){s4-L3kCD*Id7pLQHHQ@w&=Jg_t`aJo~{YFEIL8-'
    'W&`eCGOebntI5=eL?syo&a`)LU^Y@auH>lSxn`^omR<NvRPd2lLg|X@uaib5wNMW19dc`@!v@or`NFFP-A_v4Cm?Z-'
    'Wi!1YYpH)UfZNDq&|q>b?!at^w+`|^UFj2ZgOvl7V$7XvMNBAbEO#QA`k_cn{G6FiRDd!z<rGfBZtU3#AoCXF)@8E(v@{M#Dh#'
    'H2+`H-oGT(v(dESY-qI>L`vEjpm(~Wj9k~#YZ4ANj-'
    'l!vRttLl_H;oMzcpGlw=BjYx@c;?u^TIgeh`WR#Fqrh5*R<og?*sTRrqTGKd^o5=i3wbEg>n7<s@+SD;3)~KQnr@)7O2~h{@t+'
    '-cI|s(H7O)VG4y*9u616e;fNC=0S4{nx~%38U9C2!ta?W7hFSEk+}Hd1&G3w~t43Yrmw<dSun1{y5q^&yb}iWth`-'
    '~NG+A%#jE1(Uz|qJQd3nVB8m4`R6cX|-'
    'W6?FnSM(Au5{`5Ie>U6Kma!&uD3l*KZvgNsiplOX&=)w5Ycy~b{2ahmVR!b?JUQh&n!20~#j{n&Z7fn7#<|>!J&f(eLki#cgHV'
    'KiOut{FfiXO}>vghg`T2N<A~uS4_>fkzZIutR>{pqoX~gZiG<r@8JtXbXSnD724al1&1B7A8uBf-yUzjR=G)>gWP!5w}@(1ET?'
    'zsL?H;|k%%67z9&*=-a3nwFX5<Tc#-'
    'NWpA^KjTR?jVLlzUS*L!Uv6*;;^U`()m8;cCw=<@H<L9<{x8|dCe$0p8D=jr1F%<26W0NQEyXY9eMh561WeN7NU+DU_oZAId+L'
    '$9p&&>#gB35yok!K4IL$6hL7_2rZ(A4MqxIi&e1x?5F`8np<EMuUk`XT8^Ge?z_dZuHs8rvS2{<=J%S1QjR2~Bab7&3UJOv)6Z'
    'Y=q@VN(yjrsJH#&>rxdDIK?M#xN`wmOwG>4DP9bM%#-'
    'K_YDHpmlzkm+rH75H2AHJyl;ftXyW*WW60ze$IksZx%rM@l3td9(Y1{9JjPQpOaLHVd?}@fVuTKEeC`!TqY!W0(Y6qRZ(hCwZl'
    'LW7xds+E_tvzN!LQNc{+nk{K3gW;+!xRO2xZpF5pr8nw8GqVEQjsQ-OWt*IJU?0k#={yLjR}c)|Gg<f2hV`qZG!U2%`lplwH%)'
    '~=F>Px%uW*}M&84AH~cYsT3W`Dt6?4JU?=+@WhwR(hx_amzO|WB)M4DU{kqRK8}8n={FD{x=&HKe07>fk=D^mnXMZ14#}^{Glm'
    'VBBJy2*-BXH`~Ove)r*N>b84S3+(7|^;*gvjxvn2NKjx;=&arOy&3&e5DIrZ-I#;mW?cGYS#H9}Xv<*!BEDfz}4c-'
    '@^(8C{0J0Go3JPM`=(FG(7o-eaOEeRD^IebmNK9V89>p5|oEj@B;;8!x(ARz#3Xt6@0ZCxd-'
    '=&+eMX_o4@GLm>G6gI_a3|7#HA6B2s8})x7V4|66p)tnm%CIS3m{(5Ua}_r>>;3eOH2kb%9;)%v?>q^eiT=RfG}XzJRZ2$@R?M'
    'XLEL+W%C<nI*wA3&7+IhIjn8PM*p#*7L0X@%IEm!_>IS6kXbJ;;9zMZrp{E~VpGa1je4KPCpB@#8X+<`#Aevp=n{^6{K%SC&jR'
    'yN}x!VSJ4&;V?o_zq`bPlAfS)cp^+A%-HsQ15WziAMQh3n5d>5!jH+Y}IFwhO9UmZ)3Edi-PJBfoqJG2G(~C$XI6q(FM~#JgpQ'
    '0aba1Fc**#WJITLQl#Xw^S%svRl3BYNww!c>qykfJSifpm6Db#@SRHg)64<IQaxY8K8jg*3blcW7gqu|{8A?&p%WbG7nrxw03|'
    'xV&jZhs6$8C%m{dn9xZe7RFN;@Wxok)>7(W_oYj?NbBlAV=iro2)&43l62^`I))1)piF$sW~*8PE|2GoWL7xbm?>aG?7(QunVd'
    'T%OHl$RFk;*}!>P&WRE<-lmnNMi{AV6NGV|K>Ii_9`U_afxFQbD5YUwTENX%;X43KRXsvW2yN$hUc*$PB@uhiiv?p^lwE-'
    '{AvsaekE_g{Fsyr9I|x32dJa;FonF6zjoP}1v&(;k<0?nH952UrIax>tMj5!Ti5J{zCre&J+{&=f`J$YmHNOyygp4g}mxIUVql'
    '85^it)LprS^csC3+lHLl3v9;W%=rEeL@Yg89kKX})`xRc?36U~lQ;fkjTG*Ns$H#E@8CkM7c5cA7Q0a%-'
    'Y!Q9#N`bBIRp3;PBKf!}SeDftoZD%sFVolU6R)b}mvP_+;FVptiNu#Gfi_sa2S5Dw_$a_whq%6wlS<<7V|kak27sWaQU_l}|w@'
    'FHFcR8cH-eLS6eI%Zv@_M=~~nGcVp1*%YV#Ee$@h!Cg*#b%tS{zWM2Lh{BwGA4IsX%U?O;9<f3EWxyKwISV8%R9~<El{2=q8(#'
    '6o0$CGh3>T7{~gLeC=!mcv=!;Ma@6~N9bOA<Pew{caI*2kOEZk&4>`>-'
    'ERHs#mUrd257>MCQXWMvH?%O3)}@WG52~AP*xg6nRa_2LJpXe9z#dtI5U~mUo|uH`c+7`Fc-Bgdj_!_1mITNX^_45WIX*Tk8<s'
    '%*s4K%RDwp81Ifo8BJ>_Xhrm9e0ph?radgqML6w(+~{a=NDjPkfeY(5YyM%Z^Vz(@n>r8=-'
    'JTQwyN0@(;UyqAr~10DHbCk;p48c1e!ERSZfARVTAWf1x0GH_ejU5WC(W2<XH4?91%MvDtjMDRLT(3u#qE-nPI20*+fVdNk>64'
    'BzW!e;wDT^|89_7h2x)M3aj6~DS8Iq04{T4+Mn2ELf_n{rz~*emtGNtbb$^!k$Q8Li_K=Bg1<?MA0=34YMtF+J@PF0vbWf9OGu'
    'y_wYpEYqe{;nKze?5%dKoIFBah6;}%^TH0^KGm2M%5;wsT_*ihEh;jYy9W!6UV$8lG)Fh#ql^;^b<K#V?~51Na+8n=fPMEwq=('
    'V`1@cE)AU<-g_0MadOKXMGauEDFV#*XFdmzk#l>S^$qd$iove1{mf@1-'
    '5Zbgq1Wks+Grw*fT{Z`4KqlJOvK)4I#=ah9Zwv@QtFGR4-pz=b0-jVN-yxK$IuO2dYuI**c#gqxoVHuDPG7xAi6OJij#gmTK{U'
    'woXQNdExKF!_C$4Xxktv+1fT}W{OpL<~W)@KcqfHS{fi`Z~H@)SldBQoO4&ax;-'
    'rIHUet!j*XU>OJgV1Xw3Mc15k1FIfs(*HEOu0hxOUnamMbo&5%=*J314;9|aP{^G{X9IYyV^2@CjG&I3rsU%Xwv!@p8Hu=u%4W'
    'wUJ)kecDWy5OFug!#*pA~GmhJ(aJ$r=>ZT)(+JR&~cNg6Fb2gJao2EW~A<?CyIIl|(pcf8-'
    '*c%npSDHA!CL~@V8TX*<wn(pl98c5^u%@>TTjEr5q-PG{mn{0~JDWzhz>-lJd1)9R61W-'
    '7afhwdnMc2mV;3aCPp^84C*?Fel_aG3#{xlxz^9+77?B~ofX>s$YKVNYki(%#2MF}CiW;+*@lW7O7{C|&1O?qI4X{T)z<w0gyi'
    '+sy^hm7~+ip2kW6DRT2QNI7o=a8Z~J~H0XWQ@ORmZWX2wM$t&T4J)Jgl->JrwpddwRg^vRJ~L)t8#-'
    'EFaG8&<n7(<mU=!JIWfP(6aV8`=vwUZ38(vckJ8;e*~x!*<kT5zw1Uy*VdiFD|Mf-vE2)y4C5S-@Y|&0dVrJ*@Hln)u;-'
    '=i1!7_AIcY$0v0B?kV>P<#N=4l(2QU&^Vz<u@8U)MuU*#!9o2nh3EUx$T)QHUUEU)tQ=rN}H_Piw^$>kO}(93RrDpOwz_utAQS'
    '@i_xJ^ev->utE+WO}#|oFlO$3eCw8OZ5cYqT=Y{?r_3d_!CWwyG>c=`WtPy8alEU=tS1|1uAIEH(zO4VyKh|YO{n-'
    'azG!q7UNd1Ru{6TDCca<c5w?zF09KxWei%F8EU~`%97RSsZ??Q`3?p>#9ABLOjbi4dwh+=b3T(u4k%1vX2^b7;eg$*u<|cWPX*'
    '@cqfCeDXtpr4aajF#1lcCV$I?``~o2IhS2w;l|w7gHe5*X2@a02`<RcKR4axq88k&3(ykHppyRWhNc#6cC4t0srA_6yBTNu*5a'
    'gJRbGp90oj=YXQ*c3Q_gJ*D5>FK6|VvZG&9)8_ejSIP-'
    '<+LD)~&^(i5n*5~6<GnNJg#rxB@390r8L5x<li6OmAaQmeOTMWGc!zUw2aMQYq2i5uM7VVoU<jp9NQs4EI^4Z;ypQVX>S*>85U'
    '0Tj{AWF|f~dw(m_ty$KhsTm;~<H8H31#M?z6XLnI;+TyZ-EvwU#D3C&$O-$l)UCTP0>!Vp=7F!-'
    'I*i1I4x{?OxMEQ$Nu6Lh%G7Czfu=I}4|OjX`kBg^yJ@rH`_5Eix;HcY)`M)chzC4saIl1&A<Qu9d^S{pXB`8yu0^)alq3D$hP)'
    'yZMXnp-'
    'V(mS(TJ~pu~)CJy8TJ{}`z8u_1c{B?dgta;Aar)b}!SE8YEG&JagtcU7Zbf9T1}#GHR=VBgF^T_5}F!6A{wa9$s@rJ1@}qWWvj'
    '3l_i$FyMup%u{cq#mX%Q;8PwTWq+@r^`N?WTQ1f%OZyoRZ#04BNeVb|$_IcLV=2+APT{mCtA)gkig1S1GG=Z6M4c#c<Y2W`Qnk'
    's5n$fD6sFW+8m0nBPs`?yz;nw$`s>Eks1){%==CdW<$e^i1z&+^X43JQ&7d$F!Y5WyFytq*c?ArZ@N1FkJ>VY?g6r<H%Kkj|(A'
    '0@GXnfo!$y+AC4X>i;mP!rtfVhw^9c9acOfr~j)_F7ulmUBF<Jl|{S2sqc-lOp!W@}ceu8z0e{%=mF0!@D`pF-Oq`4y;9cfl;G'
    '_0eXQhAGHQxxThMBFTa*sRyC?<3${*w8CGOpYYNIor%L7YO=z&f5g%C=r9KP*%JyBfFvlD75%3v-'
    'pD~)n9bEfDYDW$ZilsIq$AY3?E(P(B@X>2tAmR`Ud^$MKEt#TH|Iazq?SpxlnyzpP(|M#{zNnZxw4&Q0SBheooAii&S41j}O~p'
    '85fX<lW^Yu3)t=n~4nQk)F0gMN4ke9??bVFg7VOXbE8*S=qFx?qnv-'
    '`>*&a6Q97Hc;O$Lq5Ag|It3&Grh+>>D4j&wbFsVMY+iPZ{AQQQ7@x&Suk4&QreKu#)i3K4<vG>@;pA(>{3<>x7B4G%}jDL9i<='
    'U^sls9-'
    'xU``=;r<T^)PmGchCOD_sC{B*+>Ww_*;;Bk8x(dJ^AC$G9L0OjHCnRW3b>djk9`|ClS(G{1Bgiz~w>?*D<m&JA&!M{FQ@z`r6u'
    'xutb5XY96C!Be$7L@(>Fb26E)1oED$-'
    'fA*jd#xta=Y>LCR)NtBLT7{;7+!6aIaY0$u0v5F<mshJX(;!B)BHh0Pxo({51f}$1<F9{r@Bv#vqL5^or@}A{Wkt{w`piTKjAp'
    'Ml=^s<bL}nk&eB-ct0>7&?EJxYLZ+(HMaiO#nPl<FS`%}(`VQ7M6E%G;xJRc%qP+K-M~SI&FAsW)U}*-'
    'P2xD7y$Eyw+Wg=L##Bj#Rf3dl^0ri7B`|C35zwZwn{o9a()I&JuzreUz=TO5?d{&QfIE?ddY6}n!x;+wM(Y-!o+-'
    '?a_sdq!yU3g>y1A`v%gl*9e@_(qZtS}vhZwqK?<oW9la86~CB}X>PKb`LD>3|wal|<?*MnIN`i)D`xzBr-dp-'
    '$+?H1U|5l$%kH2iV|Qk9z&pNOsvy2JX2W>|VL&$Vz`rTJs_Zxd^$?_akLSOU5bfE+_kWlW~Lo_mHmr);QYH_&d*2jetEzM+^}_'
    'HKRRwlJ0-'
    'Mn|Z#J?y}<V1mOGdQ$B9JwNP!hsW1y#j4DvDIcN<x_!;k4ay|Gi<05e4ap+i>v`!r7<#Hfn#MFO+az9LG0AZ}aINoVGk0l-'
    '0bhIlkoyzTr_h1Ci{<#TKI_8uxQZ3%qsF)Am_sDb414gN&Tp72hCgmT>)YO*pbTtTRf!|IwwJgt$-'
    '!$WH<D6to=O+i(D)w)SLgM}yz~j!Zo~4T0jsUXzdE0h$keU+ZB&p1C+EutMFMqo&SA}NtTQcI}lDP?rfxksUbf}6i#;4;S1?j)'
    '=aY?C&RL&od^t51B_VM8mtMq8qHx#z07&<V$F?eB@3s(r8h8G0{91!#&`B<UmE2O8`>a$=qhQXN9sag|BFI=U}VTKRratSScWt'
    'K)_Q~vtJJGE|gm1CpFSou0Te=xsERvUs1T&6;O;rK=%lj=(n3x}+9$xqT}e*Q047o-&Zl<WN1Rsud7w>&@Hc-'
    '*ovz77HSvc^t_tQwd#=;Y494af-Ke@QJ80z^M%*e?OWDV08rk6E%_=}QA>BViG2tKjjiJjaEC&p4kke!g$;I2n%tT^9lvBI{`D'
    'brG@lBC&ZunZ4QPY^SK17%z1B=EV-;3HF8Ot;qJ&Bba%)lcPP#)=`pB832L-'
    'Cnxk8bUB~;S#{gPOF^mkq693L>2oF4n)RT4Uu|r#PLnROEo8n{i-X6aa*J-'
    '`UIsv%rz5gn*61^bPf_l9VuEIP^6Y)8L<t5>y8g#a11HB{*2sVLzlJ;uo{{jzW>w%{W4sAzH4yvRAAKsofJX;i+f*KI0mcCM*~'
    '!^9THF3Npy#(4bFdlB-bKrYca);?VNyF*qDc6INLE=}w(jBJsDJm6fpt{tuULVbBmbmaXi-*}sERcE-'
    'j8P%!vwRTP7rw~1^lMwgQJ#_u#+2*>+qBNDh>6Hix$XSP%IgFf?gGofb(-'
    'o+XPEF@KFUH`BwxR8hs>6Kp8B69O51C`)RL5WoLLI!Mi~X&N|&Rv$~CWJ|{>Csa97^+E~7(=_B~an}K~5@p(Q>Qo+aXxsxTxA('
    '6JkVFZztkb;u1e!sy)B<b%|m6Qxc9&IUwR!)zI*lUJU<!J~0HLcR^bO`N>6(^$=U)4IfZ^ut+QrcfTXisV*mIcDg65DfbAvx0('
    '%9_h)<Y0UcP;EByC%aRGez3hingay_lK}(Z@u+|4%KS+(6|V^nhWT$w*IwxPe4bq;hU#N$I#*xdE3TDEQgj;fV^(fH_*(M>=2D'
    'A$8b%5l1tPyQF=Jfmf96bUz0YWdYyA7?iGExT*ruC`1&!S0^QCO{;o9<oNjX|{2f^u}>({;0_SAhDb6dD4f^M3Zkn1ch7NwMEL'
    '-kp!>(-rYm++DXz$oFj(!R-A@cVEc9Pi~!AvSUbB7PrIz+$GW58@C-Bs_G~rtCu@e@$K_DC_PUXS*yV<r!kKI4NU-'
    '{Dq&4L+nc1lQGo8jcIQFjNO^-O3=T_ABC-pdt!@YOMP|ag#Qn<Nd1IAU%7N4JiB~Tg)lE@H!7{wTGqxJ1ourZQ=py)Q0K8jt#7'
    '7#ou`r~vaD2{t$n@n7VM!~Uz+wJy>qbZKY@_MW8}%VzCP4k!kGboaQMLRW|~eRzVW7;|6Vz@3vytNY2Rc5LPJ^<UmljW3kdTb='
    ')9sGNH%j%f$%5c4e*Q+Em!6Y4;{-'
    'O4MDWM8y(b%Q>F&yxLJI;QlUqAUj7u<7Iw{JKuWD@Xm?;GY&i*zTa5w*fS76B?cij)w|1Y%OQaXe9m)S|wI+Vt%{srwM~Oa`3A'
    '`J2zZscHh|ZQb=x2v!OBCvjeh@4yl&M!}rhIbg%ZfkvsD5Wyu~L{C5;Lq7ih^MXipudE2*y>ze4Kn4bLJPm|DVNG@IZ$8q<$tF'
    'e_`c(=9j$-ur=PQs*#k0&E4Fo8&e@z%#hSZ7PT1aB1%dBdo`nYVK4TDCBb-'
    '~=R<;X>qcNJIZ0Ch@pps!d(y{jOu5^~m}DI9hen~&@FUbz+<Oc5aDc7=mpEx}&$3)kI4VS(D5;6K`}Cg}LQR<%BOeO+puvi>N-'
    '51XFXw`H!$C;pQJ4Pib9Sa%;rI*&7pzh{u&YqylN{EpD12iE$85QKzge%B>ybUq*WagB!V(I!>{6ZN`@f<m8=Vu*)&B|$7-'
    '5>(iTKav%Gj~(Biy5L8XTSA0@16<mRzg{$5aCo?Y1A=>9iz-FZyrBd78K}q_8fGffqEdL-Su`C7_mFuWOtBXTX0t-'
    'a@f9@I*uNivi&GNA_=`paha)j{sNVN#IXLY{y#Zeafegs_idgsM1+IX<?U+OT&EHsrA}}k6WryzD2O>3NqBPAZenW5jGlU8%v|'
    'LQ+Yq+woE7swcxLKIXW(<HX?0H0IBaVUc3g{^&IM`0~-~W<-'
    '%&uM^l=&u#d*M6&L)=b`z8{%zunjK*ovkUe@n9`+Oh3=v~#(Jcae_FPvHOu2vx20$-TBv-'
    'G4Ub9kBF1+*^AFl}OgT&aW2#jiDR|5Ja*JD8@0>Py3e;-'
    'p4?ynD73c0`Q4GT^OE6as89k4&3A<WP3OSiXNG$!USM*x9bCpy!L#h4olsE-shl-Of;ec3Y*i0ek8moMv`PV<!ewv@IhOePuD4'
    'b^7Iu5@UnmEm*)@J=r}JrMS7O7chSH9s)ghrkq+XOF&&4q-'
    '+kLi@}DW_j*|sqY>xj!t{)hCJ$H)FN}2KquUKea{4ts!J_2K>~$qxuto_&59n(gf3RCnKQ;StC%Wo_WgtWiMxttikBGd`K6G+d'
    'U2BUk{BBat6LRu)8AUIbzv&RMRG+_ExQgE1hCmb5z{>8lr#LW=%XCpkWiWDf5mH3pH5c10=4gP!(1PtIclQzsEs4~`mM!VHS%e'
    'u6a&-'
    '{1(OU9IRh~>OYou%kbnrkR`U5(U#KFd!S8rELzoABch$H1=wSr<R@NJ3=loW#cti$7plJfXy{u!x$+u3bbtFP>p*_GNtkBd#O+'
    '2oB$OPAHNw7z<-!fs0i-'
    'Keq%qYrIy5VrlLx56BHXWiQeD^8wR`|~t~>664kHVGJ}`Z@<#+Koo=;m~MfT>HmFp|PD<ToMDXj>jX*SZufcD`so!M$D8)d7U@'
    'REH_;wc$Ny$t5)uW9g_<$@n%D<v=2cxEA=nh@1zWcIb|`F7}kJM%0kRuD4GL)vz6cTzZvK8%tW1Ll43+%2|)ZmcAG_==0=@e9V'
    ')TpU=)5bd7ic?!aBGG4`y#MF@J&3>pKti1<_;DUibn!y_87a11zKer5`SP-bqf)0~4Ozb+5x-{1V0O$BSlz%d{x;wfsh6Oj-jY'
    '`dogdM63)c@+q^y_VO@EOL{N8jdM;4E6l}dIe+M?0gEXKUJ}Vca%6!ja|6!7!YK;O1G~1JU*<vx@S*$K^uZt>7Pq=l&c^+G+MB'
    'Xd^|XzqJ&WJ94V_)wux*ip=Jcq~i%qHpJi5!8fx*weJ&_hTx}jRD>*InbiX=xqO17EHJtIF>e4;B8!bn?(WdF?DE;xY3G_|f{M'
    'dV9whs06+!-g7%6}KKE5H;v?P8mc`{kR|wl*9G+(8T?^z^@#+$^kVY7|0xWuZ;cq88y{8Msz-{hAVN|ax(m}?hzqo-'
    'ogb1xfzx3dPJv+GEmO}ih$PL_TFc*76w-FHTLA`444s`6WhhGpn%bli-Yw}PF@R2N^Lf$ln8~Br$k!YNtcvLxa@75-'
    '(iku{3A=)n=bSV#pH(x&L|@jDqzyKvAJ|$i*z%2M8IWPuhB5yCYY)SGIJMtH$N5dvugg+;m{f8oWWP9`2B<&?>yNTUGq<F3O9t'
    's%X(7mcBM_8>Qe?4p}#?{&yE$mEfwP9<sNbnq4yfBlwf3%sI=b`3<;IxCf!H^aC;gSrs1exv-'
    'i{$({(uvuVGi+ksL2(mCqw~4*(KiuHOs95nFR;<@1aNSBv9JQPe^jWwr+ERvAZrt4AR?Z@jyB=_EZ(L)RYjef?IgpGME;D@281'
    ')51JK5(dEcU9?h21Lz62yye|kYjXT#NeSM*3~LI9lMj6|Ixzhjsap^_XC8@5l;tLG^w|;EQ7D<4*#gkrw+CDyxPYiqS>|z<j%G'
    'Xt&l;_#T9I`X#F*tAmW@N-kt>ea#;poL!6--Vx*?f~uXj}nC|HAr8e#)Sb6MGtL^y20>n;{YziMiQUg2DFm21HUX=Jb+@Z8n6E'
    'Ol3_k)Lq}<syq8#(^f2VZ+1}xV9;?yc4ySmfJa(CI%D4a08T^dPaKuT3hLGS4^8bKuM(vrtH2mfev;HcsyLN3$#`shXKD0ZkB)'
    'Wx^+KF-L6fr(tfp@7gweG(IQkIzZnI=W3A~~fF{6F&>>tKp)xf+myd|~?rSj-'
    '+$w@vD8yp3&MKrf%8mvIwAWZ3oa;(02F#H_p47fLwZYjKCCGh#@^@Ib`7kwJGj@&AaF&=Tn|%BW$%QFw32a<)E`)bUji~32>eW'
    'MZ?u(5h6D548&zxALCD2<QAX5ex5ps_@jarJ)uy7E0i&WRot`Lt_eIO6?JTEi}6Hnp!mN&`xlvzqQ0~jDKmUoR32A?jpcy#unV'
    'mK6twdQjQ$?rgTBS@OolApXiutM;63Lbo}wO-kAA?q(sI>?CCNhx8+qMYZ*89pJO;~VD{tAkY!$;O-'
    'cWle^F1C;~;B*M2O#*IvCrbP)4rLuew6aJnFjg&V?43cUANjF&+W1_`okOYV5r^XN}@%QG%sv?yz?3H1^Tbw^hvq8Eu@>=WUiN'
    'x_hT+*?^V~v4R@a?6?BbuixVkHXovqXy${Fi<5c2Oo(I^;wcP-F<}{^DxDtenQH-LWmb2FAFLvu^Zwxcm(#eq2afTthAKa2~O@'
    '{)vT!@Lge+6k4JF1(R{2S4zq?#x4d*2fy2)Di$|Bg#B;Ih*-N-'
    'N9>?hReGqjMmGXXG1bOou(_HO8<$y!O2e6^<hlIK<cvT|Z<2u*r2^o@)J%XC4Zh5=eP+RNgGXA0ofCtSgG(+)j9Z89@y3^1Wy='
    '6=h>$O5?m)Kx#CwsArxuC;^QL1l`LQZeln`*i&t+g5ZgPqtb{8)tO#%}fjIA^A`&rjs^K&A%&53LLA+>MpiA-'
    'v?LqFnSmp}u++#1pMCW=AQ++SPH$>($Eip6ISbcMujXkq`h@_9f*C>T3KS{&cFAtEc+9)2juE^`Y(v@Amo5s^wbT3Sc{n$y)N%'
    'j;*)z}ZtQ4R+a!q;)sq3IZwSNRPmI+H9_pMDxAR6EcCXo>eF1LJoM~1IkW;TK17%z6sE|o>Uuuan-'
    ')a3<C&Xt2G3xsK?B)p7_tKk@(wAl~&-XsyF`TP0bqio(+y#^flFmM@{?L{HN9%xGs~tvJ)HKs;><df@r70zt~-'
    'szbEuQ*kyM(Dsyl)Ec)2NlkpbB3oZrOw`g3C)kWp3X2pMloyv!|Peo(%ClBq$)`xAa&T`~<*g<Fj17KYKT{)(S?RF{%@TVl_77'
    'wuQu3P0xezTap&N>HTV8{M5@o%st_W};i-byj|cJu${`B6qPHB9Q(05L2E+^6~oDsT);Gr7kV4|%TpQqM-'
    'K94yqQOOkE<7N^DpdJ^~YHBjvNo*Rv`-`WPv`G=955~>NWHEiRXJj(2|ytmAkq1&UxEkn~qFtA8v-~2-'
    'BLH<}sLf@Jdpaq1?zdhB2YIPm#3W--'
    'xxNTejf}Iy~Lq!ie;zi0_X-9?12>I{P0ss+lav@=X$eI8GlSEw~v9JKRQ*7zN>{esYKX-LfKAu)eJFikA-'
    '?p0g%9i^8Vv+2U_grIJ`0{k~@qUg`9jBP@(zeh-T>Ba<{n0}qpyms<LjL`3%Lwz3sXV%v&X{^S8jw~FdC}sZA|PhImLIiHG)-'
    '<CHTs})e^o_eZiAi8c3rux;pg)t@mw-'
    '<i7VqteAn58uk`y0x;w=VQPfp##sTGFQ%oR0H<ZOczSL=KtPZH{`1QdUqWU~{l(f;f@l`376XiKDiQA34^d}6;GMmPKLNojvEj'
    '$3r_5L<68>k$omyu>liT5{^rflWg$Ob*_Zv+iK5)aZ@vClEGvqI>K8z7rRAr$CDj~JaIhf7dysj_Uj6oL6^Jf)5&Q-'
    'o!SUcSHjY9mKIDO@z@QgRXeJeZN(?rJi%%0lBKo#w;I<Xw-'
    '!dB?~N;37ES`la)2iyY$njzXG!ayZ8XL!Ya|jUpS6_M9fhG2Fnga0x{ZH@`$)Tvru7;{}~ORk-ab<8F6yCOSqC|761y&C-'
    'U^PV~iGR{?JiHG$A*RA~17kGG*cp~$-'
    'Q%eg?*mLtxujGlIfZtW`|o0Tv{!{gPPu`G@=8$!>!9gOWdhn<u!)4Qgi(CE<;z@Igov|E(Z4M?ZhPsI<CQOFA*+Hm_eWiOK=>#'
    '%G3PTFhQ565R?D61xa4G{|zTY}*Gff+<f-BcD?*hetuicSbLy7H-'
    'UQTRgi+!qmqeRW+H+i|xOu7)~Z$dA)DWr4##sR;q}bwfjg4W3DXh1f`3<Kt2_26s>GrIqw8AfN-8I-'
    '^6?dYiJN)k(4}Iq^hKp5-'
    'x2JDg*ia)YxQf#`k!3*|!Z?@gwS94ZM<{CPdojTq0UIU+{QRDh)`lQYQOQ#Xt`);_W#A~on0LBeK{%x@BAWQzz>dShP1YO@TYB'
    '3+^<W~f-!`+`fXGho%ZnpsF!90^O-2gO}#&*@O&73&YJSX!oKYw^m{b#?GKl|AJ&#v#zp54wg*-'
    '~QQ+LF7I_%@KBX#88;u45T8lR7S&a#(^v;uXemE$YfZ@Xe$U*b1Kx4?s*4rd(D3BPH1^N)pl*(x}cKG{#L<?=H$XQ>hD!*X1)8'
    'FBpRGq6kek_#+-'
    '%IR0ry?t9UWJC^tO~4~v16H3;c$*l)aUHkV;A4hlVxxd5<APQ`ABN?)K64Y<svN~fLnM`*9Q$b82ih@*z}5$=m#2&k6;#_58W7'
    '@>e>mpR#z)s@Kc;K5aMtHlgUdaZzgNkRCyEOxM-'
    'j;4~3_OXlIrS~Oo+T9u@yA_(gVK$JT;IduvL_%5S%v3_878Twd(Fd7=m)b}S(L3rf`zSMT&RR#tcys*X{^3ej57mgq&F)CjS!J'
    'iPKNXRFn)ENnp|z7=X>z(6dQI@azRCD>4xcVw>b%MT@=LNOn$$RYH;~9@C>ClYvHz=hzan~rtC6rkT+Oo)b#+8*&rO`zj<74#4'
    '#eJ9X6_YaG+M-'
    '3Ch3PD6=T*UV=$ytda+7Sg+ioF<mLXN(?AJw64<;b?Y{_KA==4p($LtmjLZagNLgvjoUZQ}@xI#s!Ok`Y>ULSokb3of^eWM51B'
    '~-'
    'FY2+t#$5wkz`ra*&3&N35p?MZTWb8=TjBo2asBNOI=B?g%fM1>MjzgHwRGD>PMS{#RD*75604Hy!(!#gZYh?}7I_4g!>>#4Xju'
    '3>TA#cx(DRWj8xp*W<FK#=DvLI%|-ZBDrY3E>+mCBPgSe;``tJ$;Oipf)G@^g7v>`tX~ZKMY`f-'
    '8mf4>J;GiO#+p<Ow|cySmt+P;1VSs2)V6O{emjzI2>>u9*fXqgrvP*bX642_V^Z#MR~f_Q$FXbr>ZkK7u~m<-3dbbW}l_HY-'
    '9yEmPqm*=AbkO}A?>P=mfiq0`yM#Mr<rI4jZ+zP@4-'
    'cVUg=a%~0q^|90w;DbQp+_U?HqfMjn?<G#m(Ll|!HDw&NH|j|lksa+s0z6svGNB|`n3s-s%Pf2I-'
    '>@f0e2Ar?4titjlBFlse?YQ&L7`~5SQ>alokKCw-i^#$rGX$deqy`-'
    'oPwqH{KBwNd~(Y8<6iG?<{+hPnp;jcD0AeE6LC(EDps=H?8j8|`GNaBP-5>TN^`F`p@5rGKAPubrQ#-'
    '{zw#oWW_WjLwvRXi*!lrjP^mZ(Ove&cJqGG9xGQiQ_VAz6?rTyb*YkbQPAEz~OpO|DNlLGd|9ER*{%C>Y)HugZrJjK!e+#<L`L'
    'w?wZij+h^Om;KpJ%ju)BQA%g;cTmTh^J>Mz)P}=vC3#B-}3_?z+<dZPS@p8?0r=!gJNYqfYwbj_)I7G5%-'
    'uN!jsZ9p!_!WDPrFzPW3>OdCT=D>@&2z?MS*<us-f+d6_I0OT!Z?3Z-'
    'Tf6=OyC*VVIcxm9zQ6(>CDH%HMO;lKHD>JoRihIe7WRa70C#_dU&N(L5=FopXrK1PqneB8D$e%28w*eQ!3Y0}6D?3~nUE8@elx'
    'w3h53qy(Ej}krmZ)Ah`BVOhX@?S=bFZZMG<T!>y!^tCb->(L>rzc7*v5`)nGbXB8;^fG2xAFivX!go<F~Xgv=z_|<_e-HJ9St8'
    'XYew=t6^IBrcefIEwu^2e@}-'
    'tx>&yMYz&0uL^1O7msnWbu4o9AV>55LY~1;BI`P33#+?r^lCovTQ!Fx}R55m1YR(mSG)506s|hpFH5Jgy-'
    'ej0FPlHq}MBDC(pRjPv-hf`bQ2<4AtN{g1(&>nZ!dcArYl<h060veri2?nBbe~@CqZA<c4z5}U*tJg<da6F<6RO|NV&<1m%q`H'
    'LcCji&DSH&Ft=9<b<3qo_$m@XaAfz2qi>Yiee|qPX;|ZIEv=Cyo7>=6*)d4QEYLr{{rG>O8d3M(Z8Zy<y;Q!?g$wgjf8+<9&Q$'
    'c6m(eZ7CRztww3#3Tmad_fbQ9S|4pw!9s+5b5ROxz;F(^`f;9y`8|mD`Cp>4($j)Qi%ctL!!Ex)WxqYM)Kmz(u;#uXem1$Uo`^'
    'dn%5vOEe$x2}>dY0x^wPOSz3b3}1!Qjvhgrd+-RTa*qVT5n0GUNBr-'
    '5WIEqvQ{1n_{4`ssqv0r537v~l)Ja|`<cCNOxoHA_5%JFrs~a9jE<ry#OS}vh;%2d>0=ovAn8*VPO2OCngP`3|b`Qyg%yDMDQJ'
    'xMUa)oha7&YKR+GN=fNtaG^7*#Z9RR>~`M{Qna3Xpn+PrY5<E7cIl9CFkJ6+=CU+!cqft*R;km(=LH=HCi*q7ezyL7<09WZu^|'
    'qn}hTF_~DLGD2>_Nu^Irn88Hk=cu<5)f8Px>+P|fuxz)Af#D?Z8#z(mzXam$PgmDlp>D1dq>*K-ddqOw*!zN}*A+PZbw>xq3-'
    'X*9P4#dDQ4^7l9gNefZ^qmbm2Nqc9Zkyk{&ahE=GK`D#8R>SHu5VTel4KgnIj!9!AH_Z=xBT)S0PF4VX(b=zQXlUPj59wm(f{P'
    'mrPnhonM@)oclqTCbd|HsN}FrN|%jg^(nq%c>O<@^oOo(E?V3ZD_?9(R}~cw&zt_rffzhAp|uC?3q$<K2bo)H@~<;L4hcn)sen'
    'Vpg&e4-'
    'cUGParUB|jk7!x^_TbrLyUsA*5xlZbE_(p5m1CA(!9C&>GR4U`unD!R&Kg#I>p9P}1drD6JIhb!xKo&;9Ok<B6HA~&qNJ@eHfv'
    'S^2L!=|@G<Jm+|9a4D-S(9HHCCFAGi%tjunRUJCtw8KzQ6pcj(&)25Jf$?3YG=KGk}c<{fNkZiKq`e^1sTwHyy5;My1-'
    '4=F(%An*`LiI9sv(B|=5TRY8JUfBA_Hzi`g5Q_ZMciVyqz`1`C_*)W{%*l9Gk=dHcES5kaqZJcvmT)|FH4G191Y0+EsDZApWA!'
    '@|3OBesr7D?bPXAV1u$T-C3QdsBVb=C{syAttCp`4w@{7v$;ySvwg4j7mY=c9GPmv*wDjLX|(f3mU?j-FkawcV50~7V?wCL)(-'
    '~}4T_21mV!>yZ5WuNxf#GmoyFj~yEi&U3g2M>>jQ9W|FJWD;e1sv{I%*9@xGmb;el@V%f<nNA0xisaiK_rt_>iA+1vBMM@TFu2'
    '|V|;xtnn=BO_ZE#cp=v;H@B4YAj|3U~5pga&EY$XrOF0WA0>4iRLYd)uJmo;bgr5QK+Gxbdzh;NyOx4(+1*6H6jFb_yiw}OAsx'
    'k-4zfdh%z|OIUyf)8sGEmUYca9l2lH5;xf}m~m70My#BG2<(v~1Cv`#-@ZCDABPcXyO;62atORVf$uN$vgKz^1M9Fq-'
    '+DT<`2RM1mY`N;=tc&RXLo#3{F1HU9;{26wqX^RZait1Xfi?`o5#wO30qQ)_-'
    'lG)*|qTecymsrFJZ(#GwGYmz%{W+m91YSvYXSd@?^=O6h@cArvRm2tL|cn5<vmL<asILO<JAUXaY`8I03lEvu*pf$)Lon!@6r@'
    '2i90~!M|ms@O+?rH}vbdlwXG3>Xl)OI&ZwUj5~#McY){ypu7F@w~ejo3*!5V{7Bjy$O8J7IC;YZ0wDpB`ejiba@qu_vp95({ue'
    'c^@lC(mIs1u`+_Ce6;}_bp%bbIM(uANaLhs-$y7q2Y@ySU@EFC=0Ss>0^D1rULk@XaJ*Vo-ThCQw8f*p(o@Mw8j87MJ<ZW8$_V'
    'seYY{bp5$w<EeJpGH`0W34Q6C*5gGO&>y~&t>&Axu^qb_<vbf}}`V54ATxOQYBGT7`bVOQ<uUpmRQ$gCruPrOV5FXf$wx@JNS('
    'e*OGdP1h~ANE==Z}?mJm)xl!=sUtV+d%~3G&EK2))@0L;=$H{(F4wH5OBdbq3i!F`KyP|a@}Uw0LxUDUBP&U{%z>~I3B2l$D)u'
    'DPQOs0ZJDqZOvq4i;cZ`kHQbka2pgV$F+M0zra@3O@!bK-KQ*0gK%8d0nM*%$D5nF=QrR+VRb1|#$w&`*S&d%J+^aKbL3+6^DF'
    'tnpcQZXoQKF;0b){Lu8$*N3s|Y1blteY$9#59yuu$hNRQ?P=XF+@?s25uTdfQqG7VX7$<a#iG7Y|D|M@c`XGB4!RiHOFyX0BUE'
    'Eb$6>)cCw=lsRQ7{jN~K5fhA3clLoIt~ke5BS-8hCzA`Z+dJ|{1-Ww|=<Ooo{evLe<{=*Osz-AU+1U=Q`au=sEhZ}2Os|t*c+E'
    'pCS(3SYXl7VBy+Zf4?7#G2XBxHCVX?Mpt`6e{U>2FgkUO?mToAu0#Sjf(YS+!NTOQ(*#jh}(P&d|s;8WC6cod{W__|8e2iJ35d'
    'UW#lf^0PbKdrAd408KFB>9E!Ws)&LwALOFq>WtUQ0xh{Z2!dNy_TjgFFYhN(v@{C@-'
    '|xuuF&O*GsH)vu&!a=JCbAu)l=I*+}i}mSbj>hQoUtqGe&x8NbeX8xIs|k6KVhM1+AtCX}}{8^sGN{p2LWtQ2RNXFZ}fknb%!f'
    '@p?K}uRL6?WZS1{^otfE6=VZJ-_$ir^JcSO@BSbsCppsNKtap*EkZute_@D+G3M|7zCQZu11BZYn-'
    'q?1alVB@RPvNr<B(#FzR`Rr0))`q;&{5O-'
    'zr#1wh;}mOf*gNghx~<nB8O&t>dZeD>CzFcC#=}V*E_JW2lVH`^^?G!;}OqM&_e*w9}Wm7<9y0+7|MK@mH~UlHI-i+nAe-TqvJ'
    'gX*U{%@9OvP@rk*7^eG4R_~ju>1t<~|<+JbIdIp?_6$8|Mp<N9N9a@q#hO(|cG7GPt={ZFHhk~1X?+;QmU>~FkjR3KcA7G`a7V'
    'KX3BF#soD2puykB<P`E)(|YiU8mZnJ9X<k4E_|=gU3uWn4@ZpTv3l`0<Z0I_|hs){}$t%H)Hzaa0w?)4&{-'
    'a%YCVes}d&I`xlTh6<;k=Zh^H4S`^{6g^ws=z|^m&FqqO<1fNzqMHC;A)PGmm=lvmqrv)w3rxQF*<<<&Ax)vA2Nh@LyM=st72r'
    '!**)H<cD}lsNi`TwBd~`u0z+8jVfp5>@e#W7vi@v?q2f!aDHnrU6#I5y<9y5Kikw7_047Tb(X+-'
    'elY*BDn{8SZgx2HE+iSY{(k4d2D_VbS{>0aD^$rc3nYbNX$EY>HJuz>YI{}!-'
    'sHa~f6En|$OB@@LDdGTl;#a9(W*Zx#QQg})1QkS(&2r0wc%C0iqo=RrW?ikYf>f#Tl;0PCd5W&n-B@QFZu|7Otr1Xg7-'
    'FCWCi?ST~6cJBq8g0L`W7W=YJq>O{NeMEYK1BeDh7VMQMz-'
    'l?WTn9LV3f5%IN7m?g7r;K2SvvhE??fmVI`6lsl{P`9U3E~3qA9gpKg-WWh-+V$)8`uvw(uMsQZi`b|zgDZna|vL7-'
    'j!&guJ6y|?B!L7Z#MSO=$0Jg*1<#VR_*zdDhhpjv7R{vNe^>Cot{s7`I~6%>PEoXb4>)QBP(w!*BuwPQED`rI2Zh}Vw`oIJ8kJ'
    'X}kZN#Z!v@Mg#CWcgLS`HSD9Ch-'
    '_{lf^KGMneJ)v$zxVNQaYt%xxUIjwt!|?MNr<UmgY@`2@KCIvh$GAFofBbCya5t00N_>yE>bNQzs%Pnf9{L_j3o^6chdj4~oZ;'
    'pmfokBxS)UqxAAj(OBE-FW>FA>`z>eal8D=gVY(luf>$B;d!yPc8;q_lYuZiX*=OTc}llX2duvIG(*jq63K&w!Yuwx*0J)IQ}*'
    '4Q4Evie12-)m0}lvJ(vd|n;xFD;vyhaOZIhq;w~8s@Y72@Z_tu09}AD)xZE|_aEHO|gborTjylN>MD7W#R-'
    '*zQb#cN$5^PF&+Q<zk{vlfOKu%hov;@b*r0&(cVdn9ahdpY{;L??a7Tn^pGGf7FbY?IMcnh@uCOp(63^43R0iW#DjJ?(q#2L$C'
    'W~5pRgViHI*L7*F*10#3UQqN@->UA!XmjBYgopu)-yGM|CgekKl%MtnHii!u2oW`Jg$`LRzU_SOCy-'
    'Z)t*q{hTmMYbyS|R)#PGCaG21fbC{DZMaLEB6;<XC!V;poaH7;~PAI*)AnSIxudpIgO8tE&eEtOZpe)KX97SsJFb+&x90U2c%N'
    'MnUUPPgLpfE~>?2>d#N0ob4|)uxQ4Tmh+Mofix_kBQtG??=Z|?un+6ptZ7Fv*p%!fL20?$R<1^OK(XTw!A@XcjjTZ?Bfjge2_9'
    '>9%P}o=?{viCt`0Bv|SD;aw$QWF^Vb>w-'
    'yo2^5>qb;w;59bTUm<WqB)&GYUFV1PzDz06zI*fE3AD0;UYU=rk~;X*gz=P=0Qky};CMxjCzzHuM_%|9zZ-'
    'E(v!}90*}uZ>h8}4fQseIgj1D+F;Mu^`)GQ>I;iXT-!?404)*ZR;`v{WldGAo&cWNP4IJw{yI?zX0_ZS$<`4}F-'
    '7O0=yT&a1bB!LYJL~l6^i^7?zbL~rJoBwX;QN){cFeP|K_yDd0f2`v&p<m8(!n_PR2u00ulgp_m6BPDr(K@V;as(Td+2xd5MLs'
    '`|#rN>Ps$~O6@Nq`<1S!vrBmE4gQh=>|g2JQk@!Xz#3nOChz@_ShYrPnMCt}A8ZNFxe`3$KTEtp2@4&yHk`4)X<xL%pA1v7KQ|'
    'qGGpIKrrlum*eb>T|{x%`*pfam@>wJXQU~MN>KvFT>8zD{z`x?FpvE^VQui3>DkkC9f?$aMbe9;=(SPigzU_R%XPe^{}Y@dBQR'
    'xwm19LDKUs~(HJNl%Vx?SX%-Su6wf2w_rQh{zt)u25ue&6;T$@!liVX&KQJ&dnPL7w^;?C2h&NoN^;9*BdwZioko1$N%FGoQ{-'
    'sbe>>>*Vu5M$LFY-VUp1>R(3PE2+gT1*@HRXuR4eUT!;k?w>Iq98-'
    ';pj+(e(&5l1}juQujn9{ddtv?QILnO3y;27z_YpNKsRsc2V+WAjMLRS}9MwQ)0ncBiDerXhvR^$Sq~{li7nurRg=J;+w`cNHt#'
    'IWqfT8isn^oyecf;KbwO_wzZ_(j|GqYemlgd?vDfN!`vG;7mv&U_F-w(r}vxmYSgz+|nW=H3z$79-'
    '|LCB&r`ay{#leVId2lVCie;{*{U%0PedHLls)Jp$ZM*<ivi#8**w17{Q)cC9?*H0o07J1DTXh-tKQdd^FACE9oO_R%W5+T`-'
    'te+8~M#DSVB47xjjZOZIwD9z;jQOjEZHT<UyzWVMpyine#Kz{p)-|5VSH4nB`1y-'
    '(1tA%~q??cMI9)4R;74oV`%>tRg}{nFU$Z_lIUpt3V^aIxkwq3pHZ+ol+otR{WjmTNs@Ggo>LX)+V7Qb!<Qb)wvJg+{L6DAT&U'
    'Ux2UzP|G_qp`KB%TO%AUjZqO9y-g5GUu^!~Ol3&yzY%&qNkab42aK|lWMw;-v+4i;zyub86>}l=FFYj5$tR$QM2BE7y)-'
    '^&H%QnIz&oN@(W@{~5QVYeTL0BVhZ61H$m8}fP6!tab<fl_;-'
    '^Ijs$i1J_8z+w9iliv<@KuFm%efHBABwq|HkT$E0#}D5Z!w6d&noT)KhGUV}K=Y5%s__RJ6m9@ub<T;0qJcN&%)@Sf}k7oMhG<'
    'GdzaUxlvP1MOIsMGp)^9%YwlaGpP;K(g-'
    'X17K5S$zihetNO}{UNEr7=#W|g~88|G1t))#%3SHa;?%oO6E1~qS;sj&LK1=Vq4No<JplD1HbM>Mu)N(RWPCXluh?%uK{u6+Yg'
    'i4Z&0VY<b)@E9P$FP=yd9zC<`Ma`tTd9S9@Ucw6xZY41Pj&x}MbK_|jb2>s-Y`<Wm0Gp-'
    'x?&J6K;A2;799rh<cX|_;Eiu5vk|aM$E{>c>SX=yfvn|3$^=!t+M6p-'
    'Q@b4gd>n_vMMP*qAphwZBLRBldd??S5sz%!H$vULkk^DA=rl+-MvWiKcqd_!c+wP5R;w?ft(a_Bd8uEPw_IDA`C-'
    'BId8V3cx2GzIW|*7h3DAo3j$uiI@{Flb0!bam(<~VvgSDQLC0FLc{3&!Q&F`f_96h=+5mlxA<g6;~ElH~b<ACPrm;6#cY-'
    'j7q7cyQXJmRIf)3X!Q^AW&}r(z2!GQ$uWEKqs}?W?s{)o1`oz5!Q%IawYK%63I+`373<uZ>)TYEn*d-F2jXQK4VT{=(I-'
    'I!<MXU((nrka(>S!O)rqgrs^Ql21~Ox#a7OPC#Fy0;tgEAly}kh!8TuOV%Z*oo@qDyaVYb*Cn!yYOU)!KTQB*dkFTCF$gcni&A'
    '>5UtE)QgAm+#fBNk=k0;-'
    'Oxc1&#LJ+nBZ9R~iToL0e@b&YGTBPYfs<A7dyl4Y6j(0`m#Q7T?jf9H#K=^?P1c|f7ISAa~K#IS1t@>)J#YCNnt~KvDC&UTtYl'
    'iUlxjWMD$YAv_Uh0+I2jPO_E&u=GeZn{KM9|v^BI?34D{CLun5}!>#Z{|cyq@AB(>eUMUVj>!m|GV4>70mHrm-'
    'U|B|Xk42;DLo;T+=67E9~;EzvQo;yL++UyZ*GrvVh*>x5gs3>X~*jlRL@I_62y?%O_~{R^uw^B%tP+xUWqbT9b_G|Q+4s<};x('
    'I|-2R-FsZeC@Vs_t2Ca^lA_Hthe~aD#(xCjWGPx&oxWo-'
    'DrdZt0Qtmy^e<E!!nGfR*HS8?r@4lnGwADg~v^?<A1c@r^i?gPrX+7;LnW^OY-U-h3Dc_%21G`&-'
    'h9@&uH&e?5j9Syv1*W;S=*#r#GWp#eD|u;xxF?!|ZP0is)57o4dx$zj=)s+P<A;Vy6=;)zuD<JOG>l%gHvt$Fn)q>TST@%KcX$'
    'Ljd8+XZjGLpci(pNWyPD;mDa&guR$0_6G#ZSUe`L09T)#HmGSwU4b+GrsSc_(wmJOHXogazgeMBT;@;1JD|_>A~z~bYw8buM4X'
    ';VHbePIc{PeF`%ci#VuNv4I`;jeJ}y;``izQm2G(em4yFx!Yd-2}EmAZgn^Yv^)we*QBp!5GBM;w;nfn<(&q-'
    'BqgwI>Kp8TGF)%_-'
    '4RMQ%tX;V@tR?GBLqEN${zn~5oVM~GMUB@neY>l$DT<|sE0w^s2C(qjfMCqo}66Nt_@4A9bRo>zGQ=vRM6z?zH=Dm-'
    '@I|FuwYU8w!uaPL=^$)B1VPcbhT)K9YYuIwx9`3t710B?YgU!lhfCJhuorcyL-cX&*`8|H24!JhB4(!%~{VGC@*};Zvwsl&m7f'
    'HneXU01GP1lRKOD<j%pF}~apX>UE*wdA9kM++UIk~7fB}q!TVFCF#{NQwTr%`GmV_L`<G!<Qg)of1T&#^apn;yHl$jQgM*Q!A!'
    '?)P|B;7dPdALUwyy1EYypmio2kx3DUQ{;eEyeUILWQM;C{{@Cs-{}yE%54&wwA*zPSGf-TV_X$Sp_jo4dDrl)FBW485H0!I>JE'
    '|@Gb20Ri=NTb>OuH*)@)o6qNa4!nyv~+S!|iP4gN<EY5;KEhiIh^KN3|*3fv`d@G)XKk+F#(<F)jDu9mt8*>9E~DJ38Tk}{C^S'
    ')SlvJmh7Q<5_q7lP;)()xZ&J4q29Ncxe-yo~4TwZh^1d7x!ibYda;aYM%=YqFuK1=?1ExyPMc}4Q}8zfX6GSKH-8!sopwd9v>-'
    'KPZ8TV0^iJAwQa2p<3_V%&io_r81w;2#L@F5oy5k65Ynl2F1G^__yb|!lSWaSb$B<T(om17V!3T1qZDx9Z~%PVTvx98^{_18Vy'
    '~YGux<zb+@l250YRagQu-'
    '{6dc~X0|0MpNhF`DPmy$}?M{~=n7&mM&ZueGep6?jlqs6Z7{^DtJP97RErYl7os+Lp;N$Q;zdt9Ij1upD<^{*1{JY6(GN-'
    't}3I!UMSn?+D$L=~-'
    ';eEkAr7<rpAgb@G34xA@aXW^WIiAZDfJF3Z(+*+!n&QB#;bJkpc3$NDcL_mZ%`059HmqrhACp(4u;n5c~^hJYbSa#d`e>86|3l'
    '#3S9rg(1P9sjDUNeERu^mAh6a*ys%Qi8w+q?8Dd99#IEn0-`Quh=w5adF+6lN>mIF9xdFs7>MhC_3&X=z!>GU%-47($)I4CH-E'
    'x1ki9dl1QHv26BpmrSgH8wVN>Q4gTHIxpPA{?zIT>mp`3izIGPw$kslvs1Upz}rLuy`>CDF;Op3?~G9EHSF<CwvCg6@q@eMVog'
    'H7^70+qsD1X2<7QX)M{L~MzK>0bMlzJocaz?9NE}eG_<)sEhvuxyaWcIgbv&VE2`KRmwfVH!e!PR9&+>SV3|Xrn?NVg8RZ-'
    '#zDOLsff@KL&D&T>z9xzFs&;@I}wqv5*TLM|GOyj6oPc}Oe@=(GtFh^sl_Z*+Kv?Vc_6|{5kVad8{M>3O)yFmlS4(gu5#qqe<H'
    ';_8PJ>kBQm3lnF=g!|DqoD5(<ZTRK!y(7cTC{h3M}h!Zh0WmjR6$ysyP~8<+N_%r`P&Q~BK#e;OnwgUvE*z8{A52YQ5ACNl026'
    'qG-cEo+y5c@7FADLiN;toHc&mxE_8w*+f`?i=JM3nKVaZKSBnh`&EBF6WXr?djzF9q=AA`;6((RaWU+I;8H`NSzEz?OHTceYkt'
    '0+2fPNa1Qc?$gI$3wUdOa1>sTqJwzN0DUs$aep#+G?zLg7z%C;B02a$pu<Lgu6zIdRR2UMEZDaM@iOU<{9KA2Ar@VM+i1BU36T'
    'O$WisYTx09O-'
    'WnJ>k*p^MzWz{F0)^h=GmGnLcHnw)YQT@Dak@OZXwIhC9rkMw<I}{>~xXWPJufU3D<^p!vmLDC*gX?mbrDtU~mw9CSZ^Cu-'
    ')SFSBFpv$4iE>Uv)s(2`HDQBXBUbjO$xS>kQlv#o=Sl`*YgXZRTn=$Z3TQX)}rrgu`<$1}RVX2m-'
    'Ms!fiC6PQdaPCJHRy@gd@>Md~ahfrjoLLHHT1e8sUb(@3r)*GbP^V+Ke=h*r1JqbAgy_di`W?iI$albBG%94KHsTO~flC%<^+%'
    '1l0QwUTD$eQLv#cNYJ<@FaV@d9ib{kXn)oZ!E@@PgyldhAlb0`(!fbCU367R_$JO6jl92?h=U4EhPke%VFEXOJi@eSAeFSB;W?'
    '2$ceqpgIxcsaiQ+jDs3;C1oRot)B8<P9s;126EqKmIZ!Vf#_n-T6=&nZdjnN6XP-'
    '1k$13~?3fCWnRjTs#O{M15+EE|b6zCkpg59g{f-_rQjmTShgQJ9l#^~}+f$#Q$-'
    '58gxXXg3gZQ84ADiJ4pWH8E%f~Q8O_QA+L;*t7x0;;YLRx{UAis88LzYvbpU(Gd&d**3#Xztfs;qUl}Tp4WFsc1^`q2G!MAQfX'
    'kD*a4Xo+U)t!j3R3K<fO$SO4=fqbjIfD~;stBU(Mhe+A{4xf+iZ?WlsP27mm6xffxy`TXRx>Tg5?|4UwpmBtDQVSH=gwk=id_='
    'mdw1WP$pMw2pp@?fQ|$x%1?Il3wWVtp>mNB?uTSS!WC=*QkQY7#>-3z?*BOMWEI3BlJh@(4q;){OEK8D5($3ZbP-'
    '$+J9Gi}u@X3zzL6aL(hr_`+}*UB6Ad69mZ2D0R4`7y}mWyxZE=;2yOgnGeS-PKScU{JVroSbPsI6j4s$?p?67a;I4On}@}1<Ng'
    '||W*qiU@FkrQBAm~xnDCV!-Ui=yN|}2XsK>s>-41n(HEnD|B5g}|=c@kfNwWYR1j?4KVw)Freeakh9VnV`TG~r*l4>Q=__aY-'
    '2+w6f;42mWS|WJ^q&2Mx_?(Pc{QGAqWtet;toTs{|5sb2lKEiV>$Q(wBb3`$m3KjFlIl(IM%~^C3Z87gk>euxf<FwH?f|~=_wO'
    'z-CI9(Rkh~@D_{8xaiIpyfoLHY~>+hB0$fb&I>xw}El^;OH1?ma)T=X7annolP$Cmn{{sM{O(KRA#HE(7AlZvw$E4%_<K``bwB'
    '@|Rd7n&c{@GM`svG~kju9xL@_(zE8fL9>0O4_Xrp|te%Acphu+!DUw=F+ANwv59WbGRqgC+Is8hiCwc!#BJmz{Dn@VO%7}`MSw'
    'e>0(YsR`obTJgaEe`m#+_@ll5qA_;IYydvmK@+BjN4yNoL&$Z=r<80<giF9&_`*o+Wx@=^ylrYK5^+}+p*fQTDqJ(22YUeO94`'
    '?x>*vnY3MAKx)(U=XE<BCzcAD@jVMlA4OiquEEf7VVQ#<`^-'
    'C6WKOyEsOtr#Y!steNIgLyVV=dcc!D=G874B0IZc6<$XHv?AF`_ip1a6+pmDhS}s`MqFwcZfJe^{<qP=9s!00r{B!in0hw6^q|'
    'ixLHLoDXn)vOg_+Mw2gb-'
    '~av})qy(!>Gd6d?u8fj%lycQ4Lo$4}wwY`R~!*DU>I|hGb&UM|5OH^E<PGPDIagpq3{qVG>0`LhiUUfy+7}6MpKHY$oM-'
    'z6|$n-ylcPdf<utbloYh83jnT1$L0AU;GwI{TUjhG**yRE-zEUZNfLtZ*IAJbj)AvM=OfuSzau)+O^Gima1C0p0oasogXvP|jt'
    'X9Z<?*mQW&vabPwRV!LBg$h+CHzVc8?kV}`CYE{{g1T1IE|!&OJVmhrDpZ{|<$ySvMK+!JDsB3CYYu;H?hv!ZNLad<skg@3-'
    '5}x*S>|xJl!lDlUtTR-=ofUi#iuDZe9*NEtyv)m5S!l$_K202nx3GzhFlTlk)n8~)f8n5FQv-a)P^AI|6*>8wPrX)9yxF<-'
    'SH4!Y4M4kKHZHEtk+-'
    'qmq@#o^eoKhqH?kE#xHz;mA7y(`3KUQL{fP+z6m5X(c+h+DwXf%cjpSvA_)~!pv(w{k|}^3>({1_J1^b5Z+!r`JDu?jV?z03zL'
    's*=XvGLv<^e}XSda+C2Hqga_ohvDvLbRZB=^V=kL<<o1Vm%>{AkOYQMCG2$+tFrJtZ>)4#7;&8bT~75#vB1T8AZk<(|I5-'
    'Nbl4%G`JhlBv<_YP!rqi$l*G$IbvG#IeH&F{wffAu~miRlNpjCzk-'
    ')2j<3W*EwP8k~z(d!Gvb1GpMm%jBI!1ySNzD{A17FBz7O25~qun>GC22aMbGg)`nJv&1dUQW@;GOPE0!(H|`)w5aS05CgpA)y+'
    '2{0Vn&2}-g<QpL>mi66hyN6dA+gbLlk98R)bFeS+kIYh0ukh`f`M0rxFJCcQN*Ba9*t4pVW$gx0J`{`u^w0LwVocym0)Z^(!4-'
    ')F_V0j9+Zk!)D4Jqy4;vPW6}7WPOewqxR-FZQrhMl&YilRG07@Xiazr7_83fD-5Llmy@6+QB-?W`-'
    '0=N4pjd5Tkmpy;ndl2t6VVi4Rr-%_w%@m3u^~|cSD{G1z9rDORgR`yZ7|K4Yo2TW*Mca9ls<8aUvY!Idi9xCxT&LCyElEb1|-'
    'q59aW%Uh(VX%lnPep2l@!_k)uiS7ei8$>0Vg94i3TCvcvjFmy?@IUIdZIoijLddmS6ehN{~g@s05HDnL3fPLPusyr);%pvUcjD'
    'J!1R!(O@p_0zr+y&8CXSH_vRjM3OKQU*-Ve3Jy!DRL|Df+$OWnBjUQt<b&k;DZR$zbcrK`t#26-'
    'uFyXDqlSxj~n0I<LcGz(6){{Z|QOKxf_j?}5;RnE)DfIVFJ!_8XLl4Y`7Ha4rL&7(w7=yLvpngVGl@EyE*TYX$7Ps@2Os0udI7'
    'M25e%G5+8@thP>DLQVSB`T5IHR!R;Mm!hMMOI9V6qn^YNQ_^FsLkzpnrFyY_NNHj^$!oJ00WJP7-'
    '=@r4TYM(+qs;1?D{Hj+W`P>&yJ9>ukN`E$6PMs~z=p(*6GG56$5~TMInEj;m7PF-EWRq}!ZM7l$i7L7(VWftU8?U<KK!P?q-'
    '9mp+29siKLwR0Zi#8a5WC@io<xzD>M|al)NJlxJRc56d_~tD8lOW8%w_l)<kESZDR>g8Clm;8Qa-$53NIC-jFYKPDI7jDLzPpr'
    'Z$uAqc6PoSPZFUFz1p(=vZE)+U^`fM^g@!HfDWtsg&gZu9VbQ*3|E}}0RRQoeepj#BM=*C?O(ur8HjB{(mpc-'
    'iE<>|37?fCo^vA9O%hZW-X&HO-ofX(HssP{Hj)Zq^d&T<h^d4zi@1HiWeoB{M_=<?Rlj8!OW!?7z!;r48^KgeTVJPa1%T)k!?e'
    '1_a)=c}?jIEo+LbpL8wKS9>fF;}b~M<hNHJ9ZP=DKneEEA4z7@IX!xf~saMI9}VTy~Ar{lKz^FJ9*5BzOBQ!FQ)FNafc3{PJ2;'
    'aqsvz=<GLoz3k2(i?Z5cAj!^+=|cbik`**A!~yTr21@5b^hyOGL=)c%EVS;u)LwL-xyf)$l!};P%+<cm>3BDam$1cKSh#J#3CX'
    'W5lAMdySuZs^<6E-`*Y$=oQ#Vl*TlC1#K?k~VF9-y_*Eo=rFAA?n5@1Gb|yVDVEUHAX9$xN2x4TTXD{a#a*mXEFeQpNcHr-'
    'W!Eg2gB_8IWz?hJ#_*N*_ecud(gbXVEU9WgUdnn%h&9u+n)BXYg%P__k3J|ln5DW8EGRa4V=eU6kZR|8<e#Pc<jd~)Qpusp-'
    '{=JeeHHpdtz^+5r);!|B6`G7-'
    'n+su#pcH!QFE(bz{@5ZTg>PXoG_1CLhnwVKpGb!Zg3HZfO<=N$`Y!L8JK*0GrFAHm`%C!@Y%R3t+qP<y^x~*oz^YXK0ybSmPS+'
    'FlbU=f8<X;Xw9Lss+brYYQWE{?{vx_H#y(tcEW=A8<Jk1uCw*5N%-WJvAnY_yau9Kf0)(-'
    'jH29qIIjvP2Tw;$TsUk{hSUl$S8lg8ctYUV1of-iyNJ?);!L~=kSvm=o<cBG7RpN`k?n5`EYQ4-'
    'ya(iJH@RGOn%%7*OAwZQQ|l=Gd5&;$B4yBt*G9VE{ICFnfrqHJz%yk|@T^3kFq-VJQ8gyh|&9y`kfJ+LATe`NK>G2-'
    'Wzpd0Twg<JDsgv1(G#HGVU`UM8O<+TVvL{nwrWN?eHVrJ)yW*2_7S8~gmr6l>?zjy|XfIP*UnMSgY^)^9Y_f0V%s!;#zXHxe!y'
    '0$qkW8R9e&7A;SBEVbYt3>pp^V`ws<t{W)2zU@#8$tH6xHmRn9{kTXlY4|3CtDHr4Y%COusQqm&9T_WGE9Q3c(mY_y6fQ{I<LG'
    'E-dc4Yo??3ozc5qSp?SZ$ei|B@`6s4U57`4sIK?SIdHedQ_llacV${!Zz{1~v%*YJg#{_6G|4KJheejiynu7T=*0BeyPe8rhQ*'
    'Q-Q^_bgFYxwkV==o2-x|qV>v^gzn-JSy;M+qhJdjR#c&McAx_5)&KkeUUZG%xrVR0xVNACQX;&SZ;jK_@~6g;%!eYHe%$O`Pa`'
    ')ufFYK;dpO$3<K)OG>%JT2#HsU1yP-fl*=bjcI1AS?q5N!-'
    '*=h3>Oo;1y!fxgFC7Mrf6zXIG0SSEIj6o<8Eh+!>%`qJ%S*186$4;AfOhsAvs_`6ojjP_EJTIF!72fpGi+h&H)nlKgaf;E0$HM'
    '!blH~InoXKY4qANQsj6r)^ocww|{9;%=5|i7GYx%tzz4qOi#JhXlnafi&z@C^G-'
    '_CtBJCtEe#=_u4rXviRar^!27R(rS!6aaSp<IF`(VIP(SQAlLtMbpgboE_7YS46Q<9s+q@=Nyh~i}38uQwQX^q0W37g=B>GRzY'
    'q!wPGHELIC?sh`8u&2G7z|-rmUb&dnCj8s&G-f<=2x0`X<~TrOD!)Jm<VL~6t_mz%a{XWyxq>TqnHKp6P<lP<hq%UsK-'
    '7U6^wk%%27~<a4&u@KPsShv?>qt(J8u+5kRN~K|>|Rg<KVdVsxTrHwJqRLzA2^z9S;0;6YaIkb=B6{6JK$bO(-'
    'PS4}pWcL<x*S(#|_JwYj|$BA7O)l54|M!X0CE!=M7^<W>qUNG*?BCQdXeX=@r1PMRU`kt#uGxmTxRLaR(73OKXtRtpyjxT(kAy'
    'sn(`2EUrcv_^yk4|P>n7fK_E*@SCMmM3x(f-I^>!srTgC$)_v~m?#;eE4ByxGgl3r7LEj?@yLbb^-'
    '963tbbOj?%8uB3y(jCI0@m+P!`9x<U@Li=6Qj>j;#+;Rn$?3E7P)1EioYvY@qwm6c)F@+Mg-'
    '@Teu*2qIN<_j&fAMHF{B4fuCy1gqZLFK&~(M$o=uREGez<BSeKk~6MF0337mHoHp@Rp9xZIG+4yh+c8?X^c{we<H_%k7n7sRPq'
    'DN*hDhg?9%`5ACDz`?sNpeFh6v$~0MlF7WR)FlS`t+5iXpbp8wy%+5!mS?32Vh#%uN{uXS!%~9oDMs|1A=gJZXjo?QtUNPgev4'
    'J{vElhHA?P6_hElnPb^;K|--'
    '$Gd`_I?p^ueY*(qJG8@&UOX$HEPXQOV$rdm|oBzt+ofzHum{@8$tW+Df6Q4`8WkwysM<p>zVmyTC_YArY}(F<8B-'
    'v7g*D$s@cxK%z=jCY~T$FherSZzPFwEAc{bJuvL`WjuqetpI>ia^IhaAvY97WGL3<Pux0Rqc~ufl9`%utgTg;*PmU@}C>dm$Y5'
    '55Nx7dcJwjfa#{=@kMZzQ_>&}K#QNk9Cr6YPFUw(ln5T^2KR&!&cDxl@Cwsp<LNCe?fF1zzlg@L!V%1K4@qw3vdA8+PF6{}`rY'
    '!X}4E_Rg*zr$~e=#GIr3CG|#dv^AIwac2_>rccXXzar>4@sg0#u`V)X_LK4bpQSRVgA6og9<8UvUwwK|6T#&6);nRIA~c7i<$_'
    'f%rO2IUJKkul|47X|hHT|^dV@qEZbZZn3Wf)OoAYlhFZ^}n1fR=T%91erYDRVp1S>ZVS=WAmozrixst}tA9*!Yfvw|E;Hqa%c1'
    '|8Xk^_CC}JbUegtfC-'
    '<HN}^>3*>kX*$0RPu}Xz5(`jz!`6T`(kMCR0HzgOL95{n%h6V#@Q#em}bqg$(p4?GY;7udwu=XG)6L#+^F9VJ9EFu&tE-'
    'Z{6GICSJUhv026wp^cTsf&t?7NDZDBD`d_?|C9nDn#S)(c?w?@+-'
    'Cu2vDRsY8H%&O44v_R!oalHUU5Nb)wFn$9Mbs1cswu^7_d=8>_~7Mf>YscOb;57_PS^s!Y)?ug{)Y(5JR<yLB;63||J8#;IuK@'
    '}4!<X7R0Am1g~7h^evgXTBz7IuOWt(22d$IC1G1W|r<-'
    '|DH_fi~Kj&HdUWnPO{RXCr?KGt2P5RkA0)eLs$8Y&%fAGLY+_KJ%NIquWs982Ht<f%hF~5SPZS>AbMImwf-'
    '4Uj;?@(OXS=cRm`e00u8)NQ1;Vw%X*ZCrAj2%61ZpVn&JIPN0CVBzVGnR^<y?i;WcME8}VSkrk+kZhmB-'
    '?;XWSVD5JtT@E{8#>EuOnm4+nb+*<ep;5k~MQ-'
    '8_TfX>B{^*SmyI3AwGGY!1MkM3rTw;ZiVX1dQnTs6{o`z=q>qS{%wXFUlhZeW;gYl{qZ4%wcF(5YJ|7&f+t#|RGszVU6CE~X1u'
    '<3FR$PxG%+nIS?W?AR6gWs1d<K&<FjGenR>?2`Bj_o8(xj=i^Rrxhym5$=|<@*WceH$CbY<)A3;Sqgx)@g{kQcs8izV!6d7tFi'
    'IvbzWm1lsov6Eup%>jY+{RQ2GhN$zmzB%!fBa(`s8erzvUsF%t|i}qg{ZZ)V)JO8_u!w|Wouiu|4Zj?RlyS(*UyO$cfIlpwOGB'
    'w8gJhq(>sLI=r$hR4@0n@=|eX^P3Gz(8t5{3DK2tmlS8vqM~cbS#KCyYq9He@#YbZ&+2we3O~i!P!lsYqkr+?nbW6G2O7E|ah1'
    'rUc`;3_3ERJjj0`@L^O1YpM{q1udyvv;`PETrejfCw}29{ySHo50#}t>k0U<$fb+JbE;N^nrQ)0*_1)4zvQkhM1s{JjnUp9ti#'
    'z`HRR5l=lE+(rv~sXSH~>G2{YTo4k4XxCz;H2P!&ZKxur907{<+hzb8E=L`*&cW6^*qR(8pvj|8t+4&m+r5NWI{;EmIl6L;<I;'
    'xH7Et=yha_K5I>W$rtHu0Mic%G%kO?TB@I5iG0xT%uL+s^Miq9cf03<+|ls28MmHUf$4wNd`lYp#k*FHnZccr<OELRDx___x26'
    'jk|r#PDLWcd(E+C=ZLnns>RBJ3=d>oZYVWaKT6<Ktw21e#o8>2FlMHyl2ZOV{XJTOPKHD&uNfpkDRpXfnA@(}DkE6e$Q3tn1_h'
    '~ins&l>Pl|#;_(J|pgM?FD(3>V0NMFw^OEV@+ugFVdciWkz*X%+I``&y6<m!)<zmlZMTlDtq+Z2e|*5Y}t%qBx(<!&T^vpo)`u'
    'g?8rJO##Nha?#0Tbv2)VXw*t0zM0(LwX$2wdnak9{{4&?1R3h-'
    '4RN)Iz6vWdW9)cVp@(U%j4qv3y0A#QIv{h1s0$F#AF5NyzIGNaI8Q7&M#+D30zg4NnTBCIF=SBraGI1rx7MhuU$2cDR)VrC$-'
    '7jmy?WO%1FHHuKdv#J6fGe1-+d<i{5U$1Q3sbDbsglAo7=KIC7X?BiZH2{d%O)MYQEZCCdSURKO8b91-'
    'AYk?8pp0yemU3VZ;5IJ5P&?K>2m$@g1Z8kX^LEZuk>6S$X(G7ne5;g@^0RT{0i=P{E@xL8L3WLJ%A`>r)r3ACh>*D?)SRiU4t8'
    'uP&)7t}&P(d0v@Uv=VhM2Cp(_As&BDP|H20et7P%uJh2hqIl1g!Q-'
    'MWjL7Z+K@hq7<=_3Z6v&(RJXI|QYuy=0=ii;JGBXrEf*k6+*>68*nweP&@CvXEC|SW+p~6gP&1%gqQ!4EIQ5EOrt%F_{NMB-'
    '+Pf03lP==)9vq?x-_?9r;N+U<By;A48oTX>MC-E>Mfa-oTf&zO#QN&WiNE8kWL!#<A#9hvIr)sEw+Zi>KyHdc;0ZnN9vq;U%@K'
    'B9<f=u;8*I2qAKhhpZ9xzwTQa6pzDxVG<KZgtPv*xN~W%iM-iSJ!NeL640u=IqCanq(=&eh43C2)CyrYm#G&3vaSBwo4KrM@KT'
    'nkVV>=|*Ri%sr>Lp#P2>>CsYr3Yzm?2>hfA2dWg+T_welqkGTqfn(R36W}YrZc=BIoNuJHA?tJe^AM*FP`5-'
    'VcI8zTHZj%{t92=TeaGZR`vrQ=4W6SNB;86IjVPiK;#a8oKMYlD5OFscSUy$%1f4Xb9b)Q7F!fv1lC=O>Eg}1EVEi6!044+^9C'
    '6l|YhwKZywMk-mFH21wts+})FOYbPUy!z)>Tym3-dpv4}x&82s^cPyK5J9*dNQKL;PuE8SV0n{6v<CV<E1eyL`^$L2-'
    'Srj)W!dNoALAeTRc_>YY?k_A@eVnQnWU_@Nbi`?c)Zr0he9K!!#kI|Zo6oeJMy7eIb94RHsw!KDn+D?Xyq+v#$-E-'
    'jR9qIaWLGoQ>iLyzdNiS2I>ik<sY<RcoOj=<<P%JWPU2Z)1EWJL=UDx__^GuNsOJKNoV!|)j+`Uve+K?=C;0megp2|akan;dfn'
    '3g2P?U6vBeQC&?ilJyJfW4CSHpf7lEK|tbzoCb6Kt?NoRi}Kjg^7A?>o8yGy{xqTbSK)_1pa+m3Lk*q|NvF6X92>|sym9}nN>0'
    'MV-RUmo*>1(4UD)TN8(uaIk{;{*97aB_ffPSw{iStYA*duOR`o0p+x~Hd6K=#5P-'
    'Tz#z5^9u`u7o}J!!ToCb5D&<?PTcbuR_3xt1@E?_y3ul&N$seluMIRKq_fnm-'
    '69dX3gGvXUi9AbsY%i(do{&aofyRk{k^*S#L+JaM#Hr?CH`+9MTFzn9x>qP!oJM2C6nG6}865z%h`h=-'
    '&Kl95hzBC$X@dbdgEcxYYnIOZDA+FP{=tn^{|a!i|wWIPz9jYt!X{bw5P0LpfQH?|F)<YF)c+?E|%CQbuBcLF~a)y7NS#NDd~5'
    'n*-|l|^oltIq~~13xY#!-'
    'Q%@mg`L|nmr*`<#Dh9sr`hqsqX`Kp%E^rdxc3BoXWPfMYA>fSHIX!{NlSD+ydo!na1fL$2$R*$Sq^i_1F4}Z{xj|S<eU4H^X&{'
    'UK(np!So$k-Qb3i6sut0db$`mcGt5`N!O*GtcSDdHkgh3fpLS38Q3y$9Txnw-'
    '1Y>%cdwUlz+r;N%2rO0F2cQy4!P1wgXe0>Re6rgrVYw9MZXTsfV^3k$BFaM)Vv4|V+ma#MW&rasn?D%o1SVcjPT^|luDe|H@nB'
    'WJO;%qqF^!xhP#~(L)cvXDeRWgt8BP@-'
    '17Fc_B8!$PA=opHjq52y?&!g8fy!T<KWZKHp7;7al~dLhda%O2soLuMVfARY2c<faNhs-'
    '=i17q>y@WGs3gd@njN;QFg>W4xGs;Xp%F6!j(dP@RJ|Nura4u348(hMFKQlHha(Ta!<WbjkAUBp^YTVwpi-DwanSHaWvZfupXT'
    '*+mBB#HA=p-Y*RePQlc{$ALzgIyPALAx7L7ben?3n{l*X%0Ie5Tg05lnNa*}V>6PZB=INh-'
    'J7Ko2b)SoQS7H_<QfB;$p0vs65L+X>e^(m4gdq7vW1FA6%=&FVn;u+G#>oyY`7zG=wMq84S8EJPFA)adA@8W9P!2DpYq0(i&Nz'
    'AtyEb_y|J|k>a8-`1Y<IRD%GYCa>mtrvRD@Y7ZP594*Wx)mZeK6NoE6~@y@ij$OjVNK<O}Lx*yxFP@I95i0pA-'
    '06Y?USRvFrAIjMHLIdj{>TT*M#w<;BaPZQ)qJRGAmggd^IG|96MO6O&#U^%_^p5P5Q^dPr&f0*1P)(3Mp0Sgg|Fz(=RWG)iEgm'
    '>X}bwKyg7<Mfri3<uY!R;~W`bj7WC@u%MOqq;+}IIe|rkU1M8m}SNG`+zCnV%_@}FhsXc-'
    '+`L+GB>ch5tNenIKv@1jWFUFBEVzU4_Od8BH8I%s;AY*#gYzYMxYFi-'
    'X}5*0Hu!~$*5rxXOE=5o`6C&R?1OKqOd3YtHEbry$c!I0*HQ>_33GH+njNw%-'
    '^VdC$TFx*@hOR_YkQAkH+cm2@6<gDIX!9M`7J(D?eL8p$qO5LFoPURJd5fp`#?dFVYV<sF49H1g%9n<7VK!y#E|K<D1kPBSgXB'
    'a{@qNn8&b)mKm3>X+~jHXhHsgRaS^xgI(_2SUka<DcX~jnabomlK^qvF%My)<gR<Ws6u>2e-'
    'B(%h$H{2Vqg$95}l3LGY3QIn&!x$vi!Oos8_3+rxTbD_WpE@#@9GDwUOBt&Z`(UCIq~kcu6HWP>JjPRVZN^5!+HR+0h!bR!5zN'
    '`E>E6WBr`O(%94LhrmXc>3iH!m^XEX?cYWt+%Af?>yhT<uFQ3`$W=9ERz~bMvOV~TWw^j8N7JB&U9nv~X5@}K@NrsOM1p;;!%U'
    '{Oh@?QHt*L*yjcVoONHVcxNto2rTgklXN)Y6{m8Ry`?baY#H91Lsxn-'
    '8Fz9`N`1Z<eGf<2|YWY^m1><ej}#(3|{j3Vx=ggesVRr%9sE~qaAiV`#9DE}9Vu0f=F+$)H%=?ryvrJ1q7Z^4zi{@+(2kMfssF'
    'GLAGakJgUzueso_sD2!Pq(XCoV(C@69&@o2zdo=4}fmPedG^|v8L2?o%cX@^@nE$7nbIz{KxyEE<<}wt;c`c*O_PMr@@P8Fd3-'
    'iM0uE3*2Vn`v@>JG+W6$AyP6doc+Pp)pMSeF>VJP>R@)&;>(r{mTEJ!wF`pNX#u66WR!&yr7pOtVxJ_$C&hXNP%IFmSX{{%o{='
    'tN2Tz-#dGI+Cgn&MNTE*LGkgTB_<wwQlIf-8kroa-'
    ')I>4`|9Z3x$!7uHTDoGAl3;qb3AbL`3^2PtUhPztR=1(ZFx?&xZh;&c0cqNL(j14eF-'
    '@~#KChQx3RE5W%5%^fVY^mU!SlUNhtL*NR^5xVGREe+&#YZ4?>p_0rYxB^QbU;H{Xb`36AP$xPbs0^O&g_6XG>{rN+?wLo6vlx'
    '7XG004w948L+z;ET(4)q(WbAB<mbZ$>HO^oV(woBSF$Jjx=qQ9$F4fyiXDvS3sQ)VBEL;H{FXbj=?&R6(6iuh@ps6#vBhmHh4H'
    'e|Ad=Mwo*H@7L+9VOQ<jT{U`+O~XOt4^t846D%v&{7ZS0{&hX_e^4`&UcVJJC++XJvBL0yUE>kc;kbDqRWaIC%^pv6twz4=MDg'
    'RF-g)bl<g=-9|`{DBsfAk$&&?UWc4`L>Lh{xwz%=u?+o}ns~-'
    'Z#rN!oP@LeF)L?uk%tIS*2=8FUv%(;?!Om?&m7G>Qu$`yMn3<3G)OWlQ$Bf~N|M$Si3n!TgQv2+2tB1gjqRfGRxZmTTRjNaF2a'
    'wGZb?!TG^RNLt!0vrQ<X><y;z4f;Jjbozqm80`gU^?Sh5ugR6o%Y{#Nt;e5OvzJw1?viTJ`b^usWeDVLo^flgUd>PpQg|d>|aG'
    '@NKbWuO&nY$99MDY@H!z|Z0A8peq6r|tWY2USc$uxPfPR!U+TwQz|;chP6vPO7?}yC`plKzP~5MhRz<Wk2J(1jZ*D|<n<{Aq^*'
    'lzYPx%6=SB^YdZqYtY#9>uRM(hdm52SWKu#@@hz<WV8IoWrGyOOAMtT(P!npGB*sY6{sY%C;qrGiw?qfCFquw-'
    '+<8Ss%13iC^7Yio^@ktS?ASPlded!Sm06g`Xz4$yuLCWzbdiJZ%r_Y1`C<h3xX$`NU9oD7S63+0hx;{jI0M1AHQ6)$`@6uK;a7'
    '(vSTxBX^5Do83nur|_D`A_mN2k{DeIs)7DNISqThCrQWMw@=V?j5g0t#^R@(xj7tQNOJXluVmB(^=D_cYboMMO*#7?mQqRN|1y'
    'OW9)5w$Mfb9^WBwQ^reu3asF-b?W%f8pI_KU^fh9pNAPe6tP$FbSeLd=$Jv4h0e6PGq0Gf8bzjY)s-'
    ')=T!a8Fe=_odD=NbqsI`DrZeNr}TD}t5}Y8Aqee4u(U*qq&6uQ>`{AXjsDv`cqYtgh3H7DrD?Aja3-h9E4X44Qh=G7g-'
    'tao*9o^R||<EblA8SZ`h1+}ajL0m440$rp)q?RYl1ud*H@&9$2=PYEG{d<JBFDVJSj0TQn?&=A{TvKW%K+4?V-'
    't;uo9!n*)2C%1N<qw)$dK|G~7hKcKGpjg@VP+8MR&3*M4y+<C3_U!5db8wERr5%Mj%2010tdehL4nUs^sh9-'
    'HCrdG&&5YTM9*Q6rk)ll|k>z&=;9m`wQMvi(+5bdQk$}6@LCW8uKz`+JogigO<1ivhH-'
    '~|R#iA<vhn(&K<@aIiloN_ByXU1Hz_2gNEOR^Iwk}%efAx#)VMD#dU&rx6E}HEQ3MkwI=Jm86%5t_hUIuTc!jYvaa|X6=Dsp!`'
    'fJv{wnE>bvoy`QH9kGDl5z%B>;NG4^P7Nwtqfy|lVA;wod6I0oEgULi?Bc*(FwT`&e0u1Of41?)p_7PZ26RBnb(uC)U7JvwjT^'
    'nB4gLI*m2Gki&_b8TqezZI#3&@Rtfbvk${?V5eOhAxIw==+6EaYB{&X$<JG2`@W@bf_fXAQ9jbtp?`TRP@nKVaOz1~|=Bn5gXp'
    'WON>r;%(2?e_re3V)bQ1*bRCVA-uukbybp^ap&)ooKJdi10rWHV}Je#+00L($*She-'
    '{3}jWgJk_s4*bk_A!v!DuyeEpy6~6rzI+CZ&)fb4#L2-y5(e(B(b9Fbs8zi5Zx9$Yqg&2lM5<)1of7mdw0LUhXWgz!X3ULg-yv'
    'i9Cq+?0Jf>@am2Q8d1{2M=sSlr3){W+NJo9R&mtLGdPOcLJ5i*C;_o93v(){3pt{U&h;m6?=HL^=4LHC7f5Gk#>_O#IN^B5yNX'
    '%b=Dci(c=^0L+I%=J%`@yaY9D<p-RnrecVv4x7M-'
    '@$zOv!Fmlio^JqitsCGs@Hf4*kpt1l)gPbPYh(N0)lU^rC&mUC>Ekwj?;)7HUIY)C4W{~=8Ajr*!Y@Ve5YM?;5`wm>|mvj!Z}f'
    'zh?{O{OUcZEMmpkLas5hA5Gva{S1kRH6hpc_O}r+?FkP@GD|ugF@#i!$SgqoiZ@|99S)y6OdZ7kMY-'
    'Y`8?*Qbo(oy{q#X+r1S^U=}eflRqj3g=KP-'
    '|FS6L$kDvGVQypKK_1D)^r~yf7KRup^lp935Crpr?<LB6dhs79O5!?6#+>Z=5eELrT1el_On4E8+fj;M9)yNZ6-'
    'PC1@dVq=6{8RWFRgP}B)~;*M-NCV4#w9cL7l#4*5&*!X^lz>O^381P1_;4-YJiD&%}S}&VVn#35uUwC-Hr}U?Rs%;HSW@Wwj+0'
    'u?U(X-K0@N|^3Fll)Zc&w4*Ak{H+1o$!lOU;0g-'
    'zLS7J$ipXIrZ1PJwSHcXf%o9S0QwUONKd*PV_zp{^I0yHTCw8SV%^O+&~q_^w1GI(_T1AkV(Ub<VCxUx25YXiNgv{<@wX0e}tX'
    '4@g~6Pxq>)|2Ol{%><?>(>oGTd!^CfK*p6{^@gL*y5zwx461IZ=HUdf&@1lQB6)2{pMX&igWqrTX)A{j3@rp_a;Mb1`gkJ93CZ'
    '0JBOXli0Q;NCC|tCzd`W2jsizUp6bMD*gpgz8ExoFtrL1kKA3q%TVf)iQ$Ym$Xi01VJ7e_Wn0U{)El1y#dI^aeZvK_2|3_>}t)'
    'Bqf7MLuIxX5%|iqD#|0&prgqt<8EpFiZ7JH!ney~q(!u>H41j&VdJ`S7x4q>Y%gjHc#4+`<~48aep=oy*w?8uA!^EW^btz;j!0'
    'G<ha-mjbL1!H514J$?BC9y1#U78RtcN4VdZ(cOrplDl{z?DvJKM5A(EjjM3$H4qU+_cA#~Ri(EsxgVXdxPo%UV0M&Ojkg-kX@E'
    ';WQmLG)#U$F9V$V(El7X$0Y;J|do9ABH7l%gAZVP?O^1ipY_P^{n7MU;}qF$}*RH1+&P_`Vtx5_5xAF@4qgEzxSkZqKPHJAir$'
    'ups_m&}(|!asQtHLoK1v$=V?g{Nckrr!bU)ucgSRT6)^=N)Gy;lD-F#{|AQF!|X-'
    '_f2%%W5j9uC33*oU{_XmBD0c0UE4<o2h~QGt27f6X$+Ofw+Y5rV>)JDXgA5`Z=Xor>3Ytu@)_ab^~11I?;JF|j;&o$aJmQo47|'
    '`@6IV2wf(xYGq9~A&^8+@+4wZh-Ed~g-'
    'KwSb!&UrQj4aX9YDMyYN<XXN6{(`X`+SsoCQeY8pGDUxymfIbY%Gd!f5RJ}9QCQ`Jj+SZrrdz@xd(Z@IE3oLnTA1i5oaqWlAd2'
    'uf-X<2Hy3G^-ZCjaQHL%Tah$=mg-'
    '~!?Zu?JpzSSS_suJEEX<68YtJt>NjK3sA{L76P#=69&WjGRiC9GgzYg2kC?#RDyxygZV$F<|{JEtJV`t_UkbVs09o&zo|4Auyw'
    '6U{3<?T>{^1YAY&w(Ct=#1}~p(|L`}NdiwTjgT1rJ?a>q*oF@lGw7D?94>v}UVS^J~DCFVyOGsMcY~23DZIo0OjB;9~i&)pGP5'
    'HbaT|jk>jr`F(egVx|fqVFd1tOD%Y;Fzn-Ier;*x1@=*`3^OyE0{+ZUL__l#H8=C|Kh@JX)O}qNrf(fk4PHSUW~;#FjwBR#NoH'
    '`KWXq+*FG7T6n0+%-~Y<2bkLhv?Pm8UlNrvBE?Q0@g%8dqmRJRLN3n+*I)jfXCvq4KML(^#}AcD(Q>GHWLA$G0<^+T;v3-'
    'bWa}9LN<#pa$L3eRkqeZ^Kv%NQ$>znLp*LPZ4Y-'
    '1+x2vv8VX)t>*$5FHDDLlu$JB+A=LXDV9!>4Y)=fM9_JvD=#B8(B2&szD;Y>(vZHW_f+}$h~-'
    'SpavY^h5`jZS1S8&aUc<v0Hy_N-w`QKWni<fI!UJ1s}643|}Jjt^}qcrf|kopqtO5?c^f9r^-'
    'tx`+RRiyP#&>vH#XdlP=EKmrT9S2%-fakJ0CvZdtx^<5|;(_3YAOZ=r&b%G>^s@@To&dTyQ5LuExty1R2v*tyO-'
    'j@SpoV&6ui<o@>@>c6ldEv>iKDt|P4L69XL#V-'
    'Z<_OD9<P|Z2!b_~NlS&NCIAtj(=|5z^FLG3CQUc?x*1&eOcAR~#QBYCnL*_|<jIXi<xGDt^Cn*Qm>T!VdmO-'
    'd4V&W|DL^t5Z4yFJ&$Q$R(^muKyH0x{s*-'
    '!;Mv{(NT?Ww;lTvqu4{E|wj|Ky+C>6ECgi*jA2Fc1TMo&r)%PCxU7{8GtJbF*p*exjX9!w!;=ePOSX%L)nzcifDQ{=o$HN4IqR'
    'll)jBGMit(iX**oqxFIb+vUY&RA)EmVT0_v@9t<sZw~!~B%@dCzxI6qt}VqN!HwEtV<OL)(%xvbPUETqwFZlZG-'
    'I;8&DDAxqF`cU^tbMnG?r2q+u}Z+8)O;SQF#jE$B{8h^LdhUmN4a8I_(`^QxM&EqQ0s3jrw|u2buv7^@kOmaX6BHVz7N;k+g^`'
    'M8GFXnQQX_QQljghh~2B{l<*@HHt!6Kj(n=gChZ^`q{VBg?;MOD5j^}q=m}zD0azihq15x>8CnCtixU_9ye;g+L;(xLa#izx}#'
    'bzq$yqb+pdjH_wq~Q_dHmavqZJz8UN(5Z~s+{6Gvxz>>`1<SnFmi3pU7QCO?WyqIt9fm{~jwe8$T#bEwHNDRdm*d5m06f1NaOh'
    '$09qy6qN(Ay9`Zc?UOWUyoHmcic%XhQ=VYlfK0sWSI{Yu_R@}IO2|LEoH4j^Y+goZ1a0XqTK8sG6{npTJH1;&`|}oU5$D=HY#;'
    'os;Lq1)a0PR+Uy&ZkcQ0Q!oRn{VRLa1%G_^U*=%rA%1Hm+v=yFX?BXmEp;YXT8cXFy-Wfp2Jg70qz^BMUK-&O%59GRT;+05nm-'
    'mV}NX-6HWtNX~0^iav1ZSPn=8HgUXnV8SHz6ys#E}+Toc#$3<hbx<lF5w`ay8kGIgzVl=W{?#ZvDtQL-'
    'Ha<@n<gW*wHgt17Sm(ib-_hJX+o7{+C=$2ytvU$`hpdg60xG#ANWyZ_D$iO-'
    '%RS@<Ncx`o?UEmkA%VY6K2FJ8FyAq&|^@{nhWVVf!YK*OEGJpDQ3JZ;&?vq^cW6$IK{0Z9)Kg@w$UK%h7TMv3;~Di_oU5USwuZ'
    'cU5L8m6Xx^|Nf(5*e!8PT*>|QtR88M<-rUcgQO-+azJC6lE=Pv{Q0P!?k1USF&nk_-OK>YB5xeY<4>b#M2keZ1C}H_B)`6x+Mk'
    'zQAY@v)|8jTb`+LQB97io)KAghU#yqSCMvZnR0`qj{qyn($=mS@kbhmL_J^Y}T0&{FKBa-swgufcwY>zH>@RiO6EO%p3UBJ~oi'
    'sL&ch0C&jdICKgDO_F9XGW&Gdx>VJ;5cH@5WAGSAkwTBHi*2QtrNyt=2owL@T9ncC~xon03nTcH|b5MRg`@DAILC;+FLRFG`tO'
    'xu(GTHO|m|i%TM9ix^h+P>F~PZ=A7z=GRFlC>D$pEel1a^{CJ!sWg~|@RHl6JieqM9eB`?99*Eh8bDtb6twlz{1!+-'
    'Qd!p^RcbFP`ZtLjwwzYu-UdTbkF8(_DrxPiDGHLP|9TUuWydU`90emp*TjA)m_nFY2VDM}S@#UWi!Px+)RtypLlccR{$Wftv%W'
    's@aA|hd(P*kP_!O@th4=u2+Q+iSH(n8b)42)zOIn()H13U_(X37DEXJx|GaILdS=G&C^Z-'
    'tUHM!oo$$6F?m!n$^5JJCfNYIR2NrQORKHRns7HVAmNVB#`?oU6!hvbNOiddJ+;4ZHv@gL>_7P4z}E{ofa%lxAQEm=F%g<MpoD'
    '2;G<*CC@)K`idQOMP8wx6mUBl1BnJa<CgO9`Y3H~MgL#_!HN672L}dHRl7%4L-sNpPc{;=iH*>sL@(eOW6^C=6LE6-'
    'K4(rUCeCg2e7VHf$;&IS(TKrs)MnCKzLi*tfh68tF505ArBmZ5^cvgko*5c73`vxjOJJ3Y)+4mIpn{=NjNbxlalICYbCCWe%I)'
    'Vlt^lJsZ1w^nfWyk;HmDZc{Qqy?RE>=$=kw9E$ZdkVvH~Zb5JS=9fbpg0)3&DYceD3ZO_EU|Nmzyo_?&RV&6~Zq1tev0aVtbr&'
    '*!}gn#M#`wZF#X>R`7+9Q+%*KNP*569WnNyys#Z_$Ag@ViktqsC6tXwdrYum8u0k{m_TpSI?M7tS7*~+vmk<59bg(3kibZ676l'
    'nLN#L!UD)fVPC7q^%4Shp>A^VjTfvfE4;dcE0|R7cD-dcSgi`EHoHk&9L*J-bB%$C;EA9jxiq)Vs2&kg0Ty2RtsCvvT-'
    'p~iSORoV9ZgQb%e$&xhmFO%$oD3#3R-W&>&)FYo5-'
    'XuQpDz*YT9VbKSUI+qODlfX<nYZhV=)fReCNC~2m;!nk<`(`h*nKB$B=)&41O(Cz(fsz@i_q~8x#~%_YLew5?BHZ!50LKU?hUp'
    '8EgeQ{gafj3sz?E3Ic?4oBHRavEjM#e8maFYJa+n<FaAf;9NyAE*qQPTOtHOXB)gAJTlb_{gC%W(eEUz@6x@hjH=*~9N)<?6`t'
    'q-'
    '+4|E4cU3WD85~|ntQJBHU6k|!Wyd$V*P95HDiNqm1Y_{?yy3QGRMzf@#N0(^y8bWlFC<>jDImGzdI%A;+&le<mUAdco(V~Pk^9'
    'kPxEc3hM&+n-'
    'fK8I~vYH0FX{7>|z9XNDOByz}?sz&Et%LJI%H+R_396m|{|i=fu3?c+=I@dAN#ea|0nqK_VWp0`XH#r4;K^HVA-'
    'Z+Bdtuz3s`07+(L^z0Fw#C3#5IsVUHsvVV1px7uIR@hR%Pvh5S*{i4iQp>&Id)@zca{0)Xf32o9P7g;)LX;Y_y2dBFu`Kjx0BX'
    'd<Kac(eJJ&!BBj}OM;z4@`+3vVq0)XU4$G}&}?scK9hhSb4@H8a8&cc-'
    'o$#2c8cI7c7|Dp8tHOr=ZG}s3Q?5xz}bzN0b}Tq=2>rUR0*uut(CMdOvnrO*Rl6v;C3kcy`cD8x08FXqAFlA>FsV%g2h?MN8X8'
    '<Crd}d6RZ|zh&m(VS@|zkj8BgBMNkS9y$Xy-s?!bVSaVq-N_Gte<1{S*-'
    '1wsc0icq}TPaAP$^saO7rOrMSYQU|7*Wnl++~&)*{VQl6n=i{h|Llzy42)FgA7`2)(tN6do5Y!vl+wdR!Ta;jCr#C*1ob7>$bU'
    'e$=Yi$tHY4L&WtJdz-(<pRueBP-N3qxLf{M|G8siodV-aaVKA7{WQQDwu}$jbz^*MW&@<YJK`PbK_lHl{qjr65WZki%e*Q)q#$'
    '8d4+%n_+jE$&7Zz8<6jyH0I35I3m)kM^qVm@Zq)ZmP^6(_}zROFhnnUZrl$A5d1)Y;A6Z}OIZoJB~#eBWf(7V`URF+N;kV$)es'
    '4cKq|PEG5rZmEk<`9aP|Sqp2TRvmT$?_Vpv?kN~(@fKJa>c)eKrGUx1s+_vEK<K-'
    'OU&sLQsg1Uo>_~z+rcuVa{uHqM0oRF%EIQQFr$x@l!Hn#Kd!xM+#SoJh_%M%x2Jw@c98w1u+=l+HF9NO8+lf*p*OAZU@mTD_qC'
    '6lS!9GC{ulwLGG?k8MLpH9MVgQ~+d7`i=UOe@_A#>uBxilQGIoJZlid~!V{*`FQ*@g7JJ-Azt=pA-'
    '@YSow{c(1a1r+KCLCcC*{P3e<4lFRv@wtt)tscT1Q_zc!@+{}FYOLpOJ*TJ&oHKDg4(e3{1kD3;2IS-8nXxMH*4N-'
    '0AQ^9`qFPQ4Huv(!iO9Bi)eVs>jFvCgt7~L`<LsS;=6e|U!IHe|Q5Mr&$`2kW`R?K_fLoL$jskAc_U@h%pt6D^hjWl<ol#+>^{'
    'UOXJQ=GWFiXHn97!AH`J13d-d!^D}B!z7MoaF#)a}!zd8KfETLjN>!T3G|owFsVT&mbz&o^q*N)5?wUVP(-'
    '5;_xqLOPhlPe4iK{KFJI^hEn!8gzI(_vFZ7$7y@z?+mguBV7NPn<i82Xv22<f+bPBK3Es8m)YlHb={7NtxeUsGpI<;0$?CTzR&'
    'gP^Co0we0WzzulN4xQY4Hvr^uHe#9YKc~Ss10!oLwC|JyDe^tNHsH@XH2UkF{tVCnVmU(VT~Wfo%`45W-'
    '6&)OxX!nJ^TgtyF+~H8pkuI#cX>4g`6rQ0R@=hqt3>U!hDE6WTh!3E7IdCwB~fIcv+B<D&gZU}DP4s+UD@ng^)N)~l8CC`O?1B'
    '|3O)Fd=VAd0}{ngjAxSZqY~7JSaZRnd*N8)NLt0OnWI#GN<9Bd~48PY72eX<T}MG03?wT^8i5-'
    'TG(J~ZutqjU5IIX#fq#i=B^;UK*%eMC7h{T2AwTRwxExmtK?&u50Gb6>r&}#z*!$mE$I<p|3Lj1STh4Kez^U@4G8krqe|SS1Yi'
    'Ec=_^tnA1E0Gi%JFK3!cin9($ZjUT>{MhiP=HL@QelVEqD~)*G;3@H;dRiQYx4{N#KrA@2e?jXCUqgF%rN_s*dxEk5E`HbO~ik'
    'b@VF%8V$CCRf~PLkLs-oO4(@GHURA&mmC+EUP(pnqFKKz>|4M|3uQ4nCLP9&`Ld-'
    'V1|!@fZjuITibgYMm|n##Pi>B!?yyQ_VKO)ZpkU|k}>`-'
    '+2p0Z#ZJM17vA5h5o?!mjRo|<Agye8dWX<Ic11KRAk5P+Ze6F|q&sJ5(ZAp`J1k8Bs=aX8e_^fZ5zNfQhYZ8M{$DyA2<r5l!kC'
    'a`wmY^iXJ79kyZgaU27D1L_)+IaR{HWo3N93j$x+-gs~i>@OeOrf)FjR=*O5LLlyHNnw-'
    '>NlUSUX_&;Kwb1f~Nzj)KProVXu7e9d&vMAKz1FUv2_1%wNeoNh+A#P3x(xMRI-'
    '3JZX^84zb18AJohh7b7gYK4KE{(#1WQYdBl`fnVC$NTn0lzCBf#?iPKzj<<z#>t`OV3Kb{?te5ip+`?#T+xTsK<d=Os=1>uT`|'
    'NHa~6NaBv9>Zpch|4>hB%EdF)jF8rl{3O~Pf*GsdM!x{(<>U`ZZY&skf9m!1MS5-'
    'bLWp0DLf6tGxJPoc|OeQ5kc%(km7M3r@Ucg@C*7RF}=EmAeKch3%L%(9xc(`Ctz2b)UrXb1@O)1*JcDK4j#z7@V-'
    'LVLP@)RH{*ZOxav<Z}+6J3Cmj>=+ta5X%6m=QgF*t6gTsJU03_EuKWC6GQ=^QoHuh-Mz9?1Ne~(bW`pnQSf5T<=?&$XAv{r9X+'
    '7Z=1WL9C1W<gIjL};?mEC9&mkL!qPvBwY#H%?cBlcJFd(?AnqAH0@lWL*ud?0yL>I>q3N+_G@+hseSkEDr;BB7Jw#F;1uv{PuW'
    't_m{*<xc0A{oP_qk^6(Z83$;-uBzse8Hy?T?AYj;V<G}#X@L)OPX8=z&Q)BMx=*sz-5SrIg5b?Vi{PA^oeowwV(h+(-'
    'ZdvIkbV$hTM*D!$_Nte>cNzAA}L;Eo?FFA&Wj541O0(GE}N)tB@P45;<!2^=^pGzB6K0<L@w6C&GGM(@)JBvO0Feb&ZBgCcyGH'
    'F-@v`&hCi~lt>+;CB%A#NLQegn+t6&5d4irrdDU@@ysj)2F3-'
    'NuY`A%B65^5;jXo4`ajIE2t`K0n=9GKXGt@Pbus*vU`wGnwmA4Oud!X1K_seY6bqeXxP*N0Z`O0ryOmT}Mpn-'
    '#2#YgrbT2=;nP}6Kc!uq~(*9cg^7z5x@|DA3FGSzpQfzT2Uv_&AX^u<o@JFfGtB0){TXG5g<;R!EoP|g;AWD`wZDu&uQ0kN-Uh'
    '?xy(EEW<G2oG$CIYI1v3Z>2wu}k)TvGcDA!zi@%)0YkM*$8Ll>RAK>J00h(Uwi5Fa$m<R+nF^Z@62}<6y-'
    'P*i7uEDL)FY*9t*7bodbKUgG9~emsc|j#2Z8l<-'
    'Pl?opku&P{C=!iu%j;&Kk9c$nEk6wN)Zr1RBkMysX2Tqb=y_xd2kGguiWErbq@y@OW3!(JTnyp_mG^L8OG4(EE87mrd$kK4W}3'
    'z;3IkTVyU=Dz}!Y?%>GwyOo^fyyv&X1rurc$Bra!s4z`qOC;R9Pje~hNdAO$ev;a4Nrx!rkveccN>;LHoN#8i2W}vZai<;YY+l'
    'kXEg0yE~1P8n-'
    'JhP7CyBIsID4_5UHb#AXghpbQ;FD^96L2_ZT+Rm_&2i6knzE|J=S2OZS=L7B)#!ysTRqrkliYo9;)`hZcE2PFc|ex}fY#sbLQj'
    '{~q)=tlLXtLuFJd!EywpZVgHcQJi0E1U?%O=7FCYELUrF&fb2ya>BjV2NLo9^kz7WtgrEhauu$f-'
    'Xt#KmMGlZQIKIFKx_66PlTc!Z^(Po&50!z-'
    '`RV_9Iw6p$`U!#pkp%KWf8w%o^LDkg+522Kt7Q8>^!_S&_8};V3BxsYKJXT{DcYSQrR=$s<vRuYehsSPzqBNR)eM6J($!v#>IK'
    '3WC1ZH<}S9_x=GvY-IeGuhI3^79DodKNH)6jdWz&7ga%6hJ5~fpJ-iuvs$h3f@MoT!xU^1oTTzg;k#FNAV7FhF0jPCmOhUt}0~'
    'Lz|(k*47QYK3xI=Jk(LiL9AAiT+AumEkYhVAd_=ofrHqzO?ggyBi}bH<6FKR71Rg6jwIb<M%rZed_RHdVZ)23c(D$0M2L5nuZ2'
    'LgV6Tz<o3%$u0n426Bln8VguvhA((l3BSP-erGIZO(JvMc#E|IhM(OlpRa-'
    ';8f7N<gFleNd~1?d2!d<POXfF*%BAW>5a@~?fVsK|zS8jX!%;>J9U;vP9=H|k|8E-'
    ')kIR3k4v!Sv>)Q1Q5D}u|NEm8L&A_>dL#z#(zUQi;3lzY=(@Tc%X`26L<mj{JW*cS!Leq~=-FN!2109-'
    '<c~luaX^r3~f;)C5O6Esj#L5uViqv2s-0s$3Cf{h`mpf-'
    '|ypk!B=JN|z0p!`H`@^n|wka~}nkSz<hyJWWuK!7FqKgAQb5Hbs#h6g_6=b}XekZ@bb37n&_C&fd08C>|rnHlsni`3a=$RE;2H'
    'ppc=(x;IWzBSB0F+F{N>fO|G*&p=GsE`&jWw715{C|`4&dIlggb&q;}F6(H>I+ZXNKznxiIH7DB|dpYjBZm^?FlQPuGj)wO{a9'
    'TmN{xD?fyMQ;n~~!lmXJ0XY}fLAh(MzVxrQ$ueVzCO?{o(6B4bQFU=1=`}TT3X6Ao^{KiIrBZTH=AS26Z@d8Fa#8(<aS0ok04z'
    'Y0Gz=E&3C>Np&U1}suJacE+>hRLj}m#a&G5`YO?h$D&4=%pYjPueV9sr_NaV5(``uOC7m=jOJEW)Gr-'
    'Q>{L{PpokRGFV_*#p${t}Ra4m3v6CXe?m;eArP5{`08tLLH}rwH8+?q;%b8*@ghBI`($1$CXhOy3<(=>l1Hy=_ScnFV(D1lFOv'
    'w?EaY{vdtCycfobpi&eUiOd^3XCLFNGICuH8Fabc1&q(Xn;CJs-qQ6?yGE69j*BpNt9AJNBm%(p#t+kP)Mttvl9)?yx!XS_lyz'
    '?l(q@D$w=+mAUZi~WsV{~8aA9Zc$Vt|MFlR20TR71iHI^(2bNF)AK4$9N-2-'
    '$TXM|mmDz&e{z+e$UK_tpQ0F}&?HDoNSFR_qy(8u~S45V;z(MZEphrvD4iP!jD<^5k9cQ+wmz=#t=ZGV<yLgt$)Gi=ls`Yr?iR'
    '&-Y>P1p5sHX%OSs)Etby_(bw)I1?H&@F9j96*6nHR1v8yZE{IvHN`M@xFQqNwsHvk-'
    's0j8k~Uz<4Tf0uW_wwSpqYB6J>dB;Ec7hw`j<teKI7FaeIAa!=x!{SUc)nm9pjnVOYHsNm0@cnd`veAWzq2fef+7!`)q1%4AK2'
    'a7SPW8|<%4V|YSFDM6wh&#VYryP|HF+rU6$tf$zRi}!hW|F~<W>DLbfrjbWr*%2o%aG$?|nA0PDbQL*p>8<Sg;oi)hsa=KNeu2'
    'P@G*ko;p<LYl-H(DWIbKdl0zMGXz&C9Zl&@mg3yOM;1*F}OJwPbbnXDb{!nP@fyPOZ`cN%N*w|-'
    '4;@<~p51s;827EYk6j9dGs+p^NAQYk}$>Eksg?T+hfdjmVJn%6-'
    '?%5CRTp3EW*bkr%5b%KONC@IkT6vJEoQ(wsXX9A>I03q|E!kOB&Mw%+}&2N`RB{P)MC-'
    '^U|sxdQKr%J;5Oh2^3zhiYGTpLH5{?iFlRiuKs(jb5A!e6Kz^g2BV3zg#K?9ih#{qEacz5YrWN0NBpjE$0k5>@f5N-'
    '(9nRVa#~UJd(w{6$O@U8D{vnq`rvzNK69BOG{|%Zmehv4Xi<vmW69wv$uXlf$FN=Scmd_vRn_M2eiR1SodtbI*>6Q-'
    'f!Y+<vP}LCY#HA+t%8_m)sJZ9Jo+j+Y)C6#p|~W8Z43#J-rE)ym*mCk{?)X^@y*QSja-LC}vo5szR^K^!59V~ovaGV*uF?`*-'
    ')h;`%UioK>0(k)Azbjd%%@Kyk9TASj||379iy)!Yj%skV)a41Ylw#;>M13w!c+WutQ5JrE7txoYW?U-'
    '2(%qfh=6YZ##Rk^6V18u*wx}g;bUUw8k_5eq*szlofaq4rUJhySv4v2OGh^I5F%Y)mG#KFR5Z&yrs&%&2(2^$*qrGiXvCiIVOJ'
    ')r<01$%s`2i`?^AfQ?4!mOnTOhe%e=fF!6WCIL@>ruZ%&Vi)dca@ml$O~7#`)zF_DmD?O=%$WvXKhfSIJZ37ilyx?x*qp8N9Ys'
    '!>8D+@%FEVOL?%3o&>|v0up!--sJVYJ_1H%E-'
    'c$HN$o#jZ={6esR4BZrm782rEbvttQxo_`V&5gE9o4^dTO~{T&D((iR7g=a=U+;$AMfXrZ}07>71Mv(;mO(!_!I5kNBYx7;*wl'
    '}pGAa#v4ovo90d|s(eT8J?l-bne6#ut&f{ws+x^)9-G;;88*sWf6SDb_@yHwa>n3jN;B?Z`FmxF)L#$_+a)911HNzMUC}-'
    '&MdXabX?A+NdE_GPD2Z(frb5I}IVP*;RSQ~5u_3enHrwX%BbWI(G$nXA)8~=bcVpLA*{r+~S%fSK4+~7HCC!b_EY1)b1oLJ-Bv'
    '`?%9?qWul>cWsGkc;p!Lw)-'
    ';fCnG<(H_&TW8;^}%fE7OrTHT>5E?hM6n?BIthizNTii%jhY)hFMIMKM%k1#N6$37{Uie@#UG)T)BnhjNo4`>3^pBVn6>&#sB7'
    'oCtYdm3pIpzMe5>8sDsmTYu6tpH+y#_y5%Dpk`l&pICQzocE`>4G0dC$Jx?O3Wp4+at`o-'
    '?igm~I@}%%rH0{yX*G0Oo66@Kh^@dRg*H_N6Ju&5Cq_H!mqT93FNr=8PwiSy!u0Po*Odxc<2|KBz8;k^8o{fb|{ZJbA~;>+g^+'
    'l#}g=!jE<?)+`gj4#XJrT>mBF3z3e5Q9!6>VtYY?)V59g#&7vOh}cos*mY4W2|nVKit@2wMDxzTaW+UY)bIF=pj?bKg$pA^-'
    'T{yD&Ra2TlK%v8+dKs3)fjq6WRtG1EW7ZK6!<{1Xo+%dj1}6p&oY4`Mv+(Hp3;dU2Pg6zD)azYgxkldb38cJS=fmZx}FXc8ieN'
    '^3k*T#Xd~Ao4>eJ7GIai{u@cX@5c@DpcR6FvI!FG)eA#X<yB?1qGT=;8Q)sU2pMQv%H=w|vpo1$UJ7tH1;wj(_{0OW$0|1k71S'
    '6@cAs3Hy=!S&cc^U-'
    'WdW9r7=13_<^>khGywwjmVXz250`)b{rMIlkT|2JU^X7`plIbZGEIP6qwnyfDG7*56Sv*T{oRst$|N5f~;oTSE8bdr43O{LtNR'
    'F)1JJ6B6bs&W4-'
    's3<*n8zMVLgEfZ;!M`^;p4tFRDeoyN}hFrurIX;R^+hfHVk#;5BQ+bIx`Iy)$bf5sooK!Db03{wV!`03xAZOzJKN=MJR-vqa)V'
    'jK&L=jTbWo|Q0rvC8)-TwU}H`q*1;BzSO?L-xYvhlttnyqYLs7$lJeA=+-'
    '=L2G170zwFPwa<Ep)X64+XgTOecQt}CZd@~J!FI{0d{yYBA~N`gGNWO44WSTaDjbAsGe($pt#I*CS(0YCnLVkf|JRGl0kA*P4X'
    'i&qD_>_r7d4EJ3Pt?xi62{Oey&V_xCoZZ|kjA!#Da?RgpI;&sH<;R=K@$`J>!n`p!Rgd7~fawjifuiI+5hoj5(9zS~_5(C3huW'
    'Ef7_2!I#Mkzxg4-RoV-y(ZBSq##EV^&cY*#4aNg!sEm*-'
    'ct{iOtx;zEj%RqDF}msuA8ID`+(Z}qfCIzUvJYW1;pYIBPt@Ql;nS8M0%lL<j5Z)4cd{FgV-'
    '86E^_MG!x`1G!bh5^hWS>h^>wPfa(qD9DNad8OfNm4T?OE8Xca$4iC1djEKM%h+5$tVL<_(wwUMISW(R*{@16?6(rIAcaygbq-'
    ';w#f#3<7$m2`ardg%yblL^e1(DwjjORN5#sU^IE_Xm6NpU=l-'
    '8DjBCK33j6NFh$Vp!jp7tJXxMs|H_;QjYg!%74dJG;*RKTJR0IkUlP42vk`nWOxakWYTS-Wj0^s%%0tylG0>muAB;Q*C0vpB5<'
    'qX`TsI2s9>j^Xi30v~$8_FM6IhIo#)RRmfFOs$nFhYOL?qX+}Vsf1}r+zAz&mnWrg<fWoQ=eK#j^0X9r6~oJqR`S3P`YAi%n1i'
    'k<z8-@b?q@@WClDhf;6XPgYxrO+v3tYKUZ`-8R`GS2`ZbhL=*{^IcUFfbY|z5T&0-'
    '9^bq>kXCH;+qQ>}U(u<|d_+MqH=H$1r=0}HbSAE_WOY$@vfPt-;|q`BnD0;d00*sO&6lJMz21qR1ka`i0L{gLkpt_g5-'
    'FGRDVdvE!&8JWMsT`_4im8<0ZJCUrN4g+a?L*k&?;{tctCutGtW50I*g}8aQ&WQRyFa+S|1<zr`&<q?94pbS7K1b=yleDmk@#1'
    '1#4SAWyxnBiI@S=4_iOQ;u?6nMf${#(-zx<!<=_O^KQlMEFNdQrqY=j&y^lEX4DNvUgnxhZ4kIZl>_Qr2ks-QE7{%!N?pN~u3Z'
    'H1=#9#s+o8sI%y#6?I4u2njSZ>c*i15wPRF`44$!h%|>N4patkcn||mGK)*&mjZVkL-'
    'Z4loJsNaHoa<!kttg4$4qt(yHyQvm$;S?}hg7JyoRidZ@<yA?Yq+aMN7GFz4blui4Z~l4sae^Ay5<(ySy<1P|A{yzD5$$jV6i>'
    'C=t+ncUHJDy#qXo>(56HFfCw$J>S~7<-5RU+S2%g6R+_HR^wNa;gN9GUf(3ey@&Bh)D!#{ajwA{1#k$TPlxS-aRsN(6K<gV(+b'
    'MkC6ITOhX;*EmVVEz%)3Szy?&~9s|DWPek2A&*jBU*<6)d)**L|d|QsQ2h73oOQRln+qnyR1*qUn)~kM;rsu0Y65>j<gW(mBVW'
    'eHJ*JDK5ShJTtIY-cmIP|80(aT0R)GF!FBjqDcGL22yhCnzKjCQSetK#8ZNs{@+h)*sh8@?7FY{v%j4hV$@w0XqVJg5#0!D|?`'
    'jYLMlb=~~dfjO(dt5>ubOv76bR@5ASdNM&lP}lFxg*=0EOW7a5Cx$D;*znhr`LTu%oQXmWTs7~uYQJvA0;4u`wCEZpZz@t%`UA'
    'k*N;=G5SYu})7bh?^X#Y*|j6y6BV`&f3MN~AQ>{neVbQ^E{dQd>JI0-`5yARMvEq+YqGZHOKWri75zHXgO@-'
    'IKAtZ@S2+;7_Uwyc9)P<tj8M!o>{0Kit^C*w>v?vv~m@awg3_5-'
    'bKuHj&h6Z?vP$LCiS)TBseujJb`@!A;;Nw**1w&a$P$yXv+qa|hlP%l%3*b_w4mI=4F%Q7X<(O@(q=`k4(qwSDf^J5IbsvCQ!D'
    'r8`!8dyCAKKs73HSgh3kv89>5Tu0Fa38Abn!qHYrn_(`Ot;&=xXmR@2z!N1@snwelBlBe6&8A$ysk(D^<74<qH#(FNcX8UpkCv'
    'skjn*efJ37WCm6@t``Q?J(=uNM8v=8YgL_`^Y1_keS#7rd8e}nJE7~JYiP<90W1+psi;f}=^lifF;I;cphS$pSu-'
    '=qc?Fc)r{i2ml+j0D`Vk$VYi?&$O^%PBd8XMBEXlfpUMRjVIwl0HtbjMzH>&AKhyuP`pP`H=_9T~h}Az?xOsWS>fiZS>a{1FTA'
    'Ae@zG)ePerobzi>c#7tbU*#u6?WEXUYwpp?1F582d+1V2&&9FrNHq6YgO2xhmlP<0s8C6SMEZG^$=Z4SyCwxQaUTfd)~sNJl4E'
    'uhAnC;LM#LIjQS%4;k=Ko1dTH+{wlaW^&vVTbf^?NNqbB%!3MZ;6QY4vh>nnyU@IHIJob}oLm?LVBf%)hs3GkE?iN@~eFmPn!6'
    'o5x(N1(~3wXV46yfJ_LTr#ggW|D*}tfMQdwGxH>^)TY&{#z`sR~b&-jT-'
    '~l_YNZr()rE!nyCm05;LNsGYFDd)Ha&}A+vVqNQfY!dJH})=#g12e{<f;f{Ogm!wHJ%m62%fHxj>ulWC}tg4F&Cr~%?1-'
    'Di5qo41SA^ae_105>5FaoTvkQfWx1hOnC%i3)3Ctc=zDGrWyJu*6G%4l<^J_uNA2><%>;T~W4sMpgj8GoSm%jKfOia7{2)8};^'
    'PD)OGh)s+6V!hPG=%Ia$2H*fVbC)Cxzhi%9-bP=H)8{yyyFnZv00(&NXoF^s{N*U=pRMGWy7%x@%04-'
    'YH^8N!&RQxhuW(1Z1Lu!GGYhAmjS}k~y;AF9lBi?FdT8IK?N4^AJ+Dp$<7deVJAeX41N|h&nexFx3L{}#?Zszslm127TX-'
    'M7L*yk<hWvI6PR6parEkt^Mc|Jc49@i9eFTgotf`se;f}5uQKJ*k)KF?72ccFd1!a&h>`I{}gSZ)37|A|VXBzVFL8yybb9u<72'
    'DKtHZ?CpJ*=*xi8o;EE9&wvJG7SEsN=G^!)D>9<<mvF0>H?Q(9tdIHo>`JZ(v7ld?n(|@jXphqiPUhi`MmgD!+DvK5|EkJ3U(-'
    'Iijj1yV;LFm5+nT{TK9wSAYV_I?&;;98G|V0Q>`XCDo|=-'
    '@E_BsLErZyyDYXEUCW#0&J)gFhgry&1MxqPX@Kp@~6O|b48}sk{N|^Pg2u5OxQ#on(`{WuuRg0g_+AL{Yg=>f^ll<kC#VE2kXo'
    't{NzwRb%$I|>;KFF9TM4*gOMc<=mL&;^*Z?>Gd>_<mNu9(MNs`lmqm7~)aC<^EhMTm<3uEdK|%3B$R9<nMxobbbRkW0^%KWl<H'
    'UHY)V0vA*%nMBRteZ$@k3~^A@(x&d9MG<*~g6p(OvBU%uZJQ)AvE2t48dQ{Xig`p>mXWxA`6`vvE%V$<`;_6y7c8rBg@WN1-'
    'S2|>%Em?X!qzQO>8BTq-JM(1cGxGvihuTAyZlP^9sd1vDqX>cd@C@rycRB`K~<(;Xg0!_#MDjSiQ?OEV06e_{JivB`<$*y^#U7'
    'p22j6-'
    't>Q6E5IyVO2Y}g_{wtZ2+h|)n+79W#;YAv^D1Kkyq%O5r2S!>B?=WZr0&IA^#<<@YdALVycSF8j<b=4YCgb2BBz$$;l=YOb08='
    'qvjxeEZ8A@@8v5+(dALaP$INlYlVGS&#IHlPB-SI%3gAduci^9MT4k17Pq`@POe<V98KCSI2>AsgHBmBqfz}NP+Q`-@1QX-flB'
    '|KFT?n8dVf>TN(bon=}_B6$HtijE+THzs^^Kl67)<dX7+xzk;ei)|rFh7FYdglKE13OyKy!P0+RLpXn1i+yx;$aCpS-'
    '=^<?zI4GVWMmteLDmNHxcf)GuHZntT-'
    'Z#m9C65Z0hX@cQlRt<UP+1XYU?tU62G*21i<kvK2v5hR_=%P?_2~iO|+Lqa?D|h6=_DHaQu@jL^{9#4^jZ8~w3H`dNi*f<X;Lp'
    '|6FMo9f9V@+843^AuisP@+)uCT2+_TDJCj<2^9wLz(=sQV6!7PWf&kU5r9>C)M1~9II>pZmS$JUtrvD#SjYmIA}VUHDQW|Y;k='
    'IE2s3SbO2>CQ*$l^qB7aN368~n75KV>p9UiB&_o?B-'
    '6xL*cwpY|?mgo)GMru@l&`+D<<7YTvqNo@My2G)l>`&8M3p_=GjGoVe*mhEs>hCeNn4d>3?AJ7BHe|!0~3qjYD>`YVE@p0Iifk'
    'Em5vrEn<fLRMb<%@ja^gN0iX9u@-'
    'w6PKnoU0m9N`Oo_kX+3Vaah1eMupzwTzVrFg`!E2r%##Jsee*7=VG0jxW*dceGFO6Gh)<ER>;^dpG%MDR6ty#M9uM>{$wHthVP'
    'kfWS#fw7DZ3GQM8Ho2q!F(h_QN#lH};5vcH!s9_s6Ux8C>`rPX@|=MQ7N6XIYNlilvG6_67mIKMC@8=Je`?-'
    '`>gIiL66yc2XI>0RQ5J!B5++hN4=~$U1?kRUyN(}cPhD34LE!+9cmNv*DT*zml1gtfP~l!ipc%(Yr9Wj(p+LI$1NL+x6Rog*q~'
    '5f4<9cwE6HlCK3mIJMLdfm4Kb@8)xAxK)xr6}Qp3UkM^{M3}q9xnu%*u!igTvs!sO|R2G-'
    '*3+9;I6ob#N<V<1079cMn>qVULq$TluTh@&|+OKP07<$La*)00o7M%YEcbHbN&ZV(z^_aT!IZsoDSpH6tgGXVgc=`XM4CI84P0'
    'xVuOOtn}0%`=qtGYlO?}=zVd-'
    '9KCH?ncMnykE@U1HY}36TSLq25WF<ckqBo4Mo&gfB91$il8sNCypjsDkA2z25mBJiz~cLP<mRgJ0e61lz=ku^ZLC*gKQiL}70N'
    'jPJr>w=vX>JPJ%07q#9(ZNy?Z$Lmj77L!7$}tdWSydeDz>8A(c=xD*AIrL59uTrI#`d3#6MKdgF6pUfZ3;s(~eZs^qPV;9nU;x'
    'n~A{NR~rCyUNJDe1OIOO=PY|8%Q#v6=$o2<bA&C6GyB!gqImI5)EARn?bfaBOP$Vl<hZ3f&Z)<5qG^;zKIr6dE0Adf03jkCyk}'
    'O^4M4kZl5|dS|Pd9y)EaPHSDiRJwAin5Ws6IPfFAj`uKvGA*MJv;;Z8#0{3R`SgltSv4lw*-`OLkY_Xy4@L)WX-'
    '};?Bd#X<?%cGJHKu`)@5%7ne`{4PXab`kG_mBJkFIS!=Zf@e5Cy~-'
    'K@Us~Y5VpOPur5{w(FDbnUrO*F65z2PKd=zU2t!e$zb?gz7G$7_T8{GcOYT!mqHDqpsiU&>odQ<LB)`YP#iQnYp(|MU^;>LF1*'
    ')CH3)R_$D#*i-'
    '*HWk=SvpSAYXCnI(}M+3@sA)1h*t=Ud!1xTFTs}0=&9ye{FM^)k%~LS4CAv+;s^n<KkAl{q{5%$9y;cl5w#3<L=3(RZ7LaJ6Gp'
    'NBQ1vr1=OwtlwDQEhmf-vn((QDQn%WPGOwF0z+HKCRzR@wROn73AL>;Xb><B)K;c<61c_eOEJ7}=5#0Y_oWxxxc<>R4*IX$sMS'
    '+u4)A9ATQWh~)mU^G7~I+85C5W36b=-'
    '~qbrw&MHnU)EyvSY58fQ?K4ehghhO22>++@JLON<}v%ap^0RY_Z)02>rUV<P#Fg7bY*K8Fp?jJ5+Ps=602ckBJ6ui7;i}bBe#9'
    'GttYmtRs=;HOtRhJth7kLt|~j?SC?L!NHXG*;B%e>^!c<*m(-_{X&S=03-'
    '}lqIY3>(&<EU=Eep#GCCK#)rD1ntI0);wh|yFK94!*qR08(Nq8sZKHDqF3z+LvBz`@x_GN`>VBSJpcv{1@&8&hD8g<**E*LJdQ'
    'X~P${@qC5FjtCky#IQ#@&}3_Rc#+)xA8I~sFp-'
    '`|N5Nrgo~?8$5N)`SOkiWRU|UCH_KM9mASJ$rJhx0Z!Y1TIgewp_{Z;_Rjl4$^Hc_ihYh*>W93<+7^`j_QZK{`*UhGw+U#g-'
    'u!!kg9iojqu);kBwup?OU8Y_pt|-'
    'cF!6`ibJ1jlkSu5l2ju;Mp02_1*dHOoLLzKmT0+VeXz5FfMiLyB^Y!}nQ8$DO`@#9z}JDu8TvSY|fDIwk;498P(78jI(7Of0GL'
    '%2AWm23*%PBD!<8)*Aqg9HrDy99zq!>`#<2Y;=;n0pxFD!*u$ow)c_CIgy~aQ3yP#&=6hJCYdMOjldoSeTi~$0d?}n{T4J>$I7'
    'xYql!|2gx`F3{7=M>LwE5yF!cr$F+-3a2s4Tc*SL-!90zA4n;OH%P46W5w(;u4uWPDxU=0ncv`egOK8K^kB6`r(@1DvEcr=O(-'
    'Og@U}&R@Nh;7OnGv2yf^Rvj3Tyisg@;CZ4opxd<heCE7MpkKlz#Tl>zxh0Br1$jCL85FyTdWxqv)m#qc8mP^b3K|l6(C(`&-'
    'beYMc;eEgaS*wXc^{(mO$D%d&r@7TD)uXKMCMntiBaxnR^JOaJmvDv$~?Fe~&(3G#(F$e(!)$nfX$DO)UTb=Sb&%6d}dvhDgRb'
    'aAXKaZ<rqNW=``&FN~Dv8ypwtEUp*zf4Jbkww<-'
    'lfPJ%xIGPFrfTSTfYk$hTVc}B747i=F}@=aO~+Njmj%J!9_lRigZtgB)jdPU&WQWA9aSb!L6gV1sdvw>JY`>HMESn{5jp*kpL#'
    '4OVQsXA2u%Axs-u5mcA7)e=-'
    'GN*&4HowyVN3112OvZwOg)n2^hDasE`ae9;hgD!=bTI3LWl4#n2&%h&|hPV?p7WQ3)+YiaAk+Jyz4^*s(%wSuw6)N=^`oBgGE3'
    'VeTAf5Uu}&%xQcbO_n4QC()M*`~ULiLV)sYHUmAB^OAHMPzw|DZ?R?#DqsxHQEA9AX8f+2#Up2508^=8_QaW4U>sw>Va0GFZ7>'
    'ynq1<4d>y!tOZ0<-$h|?@e%CKY^l%)`c9N?&2&+_Ib0-'
    'cGmx`zn!H8LVJ6@5cgikJvQk;4qD&N%hgqqXJ?gsI7*9w9~(?c;ES0|oebfy^&wzM*bh<K9W>Bn*&jdTJUNAUra{Cps%_%rz3^'
    '-DT)~MRiv!wc09QrFCq?-GUa{2xBxg94{^w-CBE-i!w<=;Nz`9_BYpK5+L91-'
    '~r4oxc6`ev8I$NlxiZa+hJTVtesQ?+s!f2X!CzYy9`kE7c|PCNOrtKp`3At5nrgEmTE~Z(<F0<DZ3jO1N~S(vgg+u;0f(EHEv*'
    '<+Q~rh8@^l6v*L_XkvPR=0&)o*^AFs0J&?h%OC;3l?W+FYFU*pV{ed-VWZEi%GKU^d0y3O=bG4f{Aj-bHi!q4H%+D%L(?>IBy!'
    'Uz`xb3rgHI2SzzDe~AZ><Bi?7_5XMQD|ld`e0UU+F8>;Mw`o${(R#@eb`|j4vm5o-'
    'pQ`3m5<2K1S{Z$5)2tazKUsTR{7q5~+dr!&I2>RZ@bHA;&%}hse>B;O8pQLD1$2UDucAi+7n4uLthzMPy1O>0Tz%!o7nV!K<y+'
    'zYe-<s}$WlW~G%5Bo6+h;f;(b@=f&yiKJ1i;Sqfis#K1kL<~o-'
    'L>XBnPt8xO6_l*edzRf4L^}`g!rMn8JAvkOE`8YuRcx#=3BZH_7HS+WAgp6l(*S(-&p(!c=3S<Q9dvwt8;Qi-'
    '$!xyhP<e*sIVFwM)KQ9Oar=Uzcg*W{U|e8!^z_<!mpX;hJLn9eUi(TD;HH+7padqELV9ALK=RY|TruLrETm^{?A!A%+OSxr@VP'
    'Ccx%|muq(mTp<@P4Jp3%hZD5hGqYJs34tcfNuOu1S$Miau1_N>WG;+)?~#7OIpDj)E&20WGlae$*JbF_`cb+Nf+@~4LQPShgbx'
    'yo>aC4PrRz4)n};osS<Z+~-Y^4Y|T;yd-'
    '~+njD&MkeFTB4&pRUaF%CCI{m8M_2WKwHVXskBX$>>4oqCLInQ3oHA$#(D$cyq4l!N3L}qFW<}-Xbcki@i9(5h-'
    'w3=U;zkIF6?6BECgn()GoHA}vIa6n8@JJYgHqD~ZeThTf#O$`oY?B^@xeIpqlyip?CZK=(KTLL)k#i@!2EXS^Sa2!t=S`<I`k5'
    ')H7Ck^6n0Yw?m*9G{ALiWN0TSQ02KmZckZh16~}gTNh9`;H;oSpW>*)%gm#6f6R1vmQuya4gIyQDM*-'
    '<%%7ZY%^kbXBCBRR)W1M&5-tJ1tyfd%FrQ&@YE1q$(<p3RnRu}v+Te9D_Vue&!zSAVO2p}!uHo-V28|6hg1$IUzoiZ-'
    ')Ag~e$%YrdIy{4-'
    '%5$ANP<SwmoNo%MXRy5*}bJ)O>IAmc(HiTX{tOKuBd#<TjV`As{(d}Ay9at$<KX0$6LGv$eIqBj+D2fnyRrnx`n~Yys|2EN~%A'
    'C)PN;ZDu?5q7Qc{8$HFMK{7g*XcHiuaq^WA~}6LI3sKgJFFO^7XscjB);6{6U9v3U$3UqU}Xgpkxz+omB-'
    'H!t=+I@P}YW1V;ud?L8YUtznwPG~B)3I|BW-'
    'Y|@vNjju08PdE0J!YwWAKu3lGtS^{nxqGaXUzuM=d(w7TYq6B&E8Pk4%w6Ns^o(<RV8wobfohEbkBn&n0BwDIAm1pHM%>whv-'
    '#-'
    'ewmK)gSJS@u^ZYJG`jJJYx?^a?uw5#*K)C(*Rvejk(9C^hV0&6FL7%wKL#c(F$mip*56WBF(^Fhshh)EXeo`CWoDUJLvt{C1$>'
    '#*nCDv~&T_A((LAEPvp6!o@FaUFDTTDa|=NX12zw7=xD{=7r{IX?6(A04b$(2$i(+x#GW?7Mm$udX18IxvgtUO^`&;H}7w{NB#'
    'kiZ~Py@~zVTz?|$TL&7qm_1E7%c)^aFAQ{h<~6tK^K8C!4{@m9u_(Baf(2O-HU0<W?)M+K9En?r$~W@>e$Z>2cI^q15!-'
    'C%?dyY?p?4uQ?cIiVgc;6t_SEkY(v*eP@h4=$ml36hs_z<9UN~v5mM!JN&kK?UwK$aWX;(%$kTb7N-'
    'woVi7sjT<xZVe6yNZGsbfb_}_HRBnHB0jW3}XwBNf4m>D&f!g#cC3x^vH61g|0RNXD!l9RZjtfM2)u_zFd65+B+=;`SNU@!Jt~'
    '1eJD7!OztW?ghfe)pD1Sjms<p4T>&=bA5`K#=Vv54YEFMIc6tCnD5?k>4v=olZ(q<GLS7=ClyjSYRg(~BekD+Sdd%&9xvP2~;g'
    'A~R0Vb92hjJ*Qd#(XZmat>w7lpQbwTdBZ(qtw?=INdxSHc-ooN6HD4Q)VaEKgo(@NVvt+F{jSpfGCC&H(|}z+~ae-'
    'J8lmn_anSYxDArsA2>_fBc1`v^i`gJs!jmm5Pd`rZId$M#qqZ0G=ZTEUtUfVaE#_d$Ho5;n(<5XiiYH_9(VI_RaL%RwYe2Rlit'
    '49ap@W=RjX11+VIP=|?QP1xc!ywq3t=BYmuy&zmYmFO)O#!VF%C88`-'
    'gviv$W)~D=5aadCv=Tl_xvPac(jD}Iidd<p8vzviq|4GCJ4YJj=_RPY6P#>TFt>-1Zpzer5WB<AFXw@&XS-dqiAS`C@@JPi`$Z'
    '+sScMqP!ERza4RRDRCTSTZ%UiR9@9(nP~{ka4ndPF!*LVUeNXc|e1&r2~)a(HbeIGvmN>J-'
    '|KQJD^?E9K1h<cwH%=*Lf(9>t?7%)Zy{;UUv0^`#7Bq2rs{4}h7xhw|SaRMz&klwIFVxep3bqG9)R#^xl|lYnCXal>3otJjqFw'
    'L9%f%t9+Oamr~fdW%y(>hJkA$>~ne@E2n;?YxL`b<|X7Sg*MU0`S>*e;8H^WX9Joa;GS>QrpCOblP!-'
    'Wta%0nx+`vvxdQ9dv}Xw<|QbguLcobuO<nAL?O>7qGtIPP@8E1jCqTL04_Flg?#I0rR<wesS$a=WM;$Wz#o3@gp@5UAG=KReF?'
    '02Rbw{M;FcWKM94*yX5l)X0=9DOb=$D~+azCpEw1dr7~Qh;NR(jz7Bh8j07E6vS+lbOq=H&!ktKH3C&7CVQlLE!(<xEybLO}V%'
    '=reXHye#5RreM~1?^g>d`rD-QO+{<6SaXa!E8OK#+6Myv1`(sXIvUi*L0CACeFR8$Z5dq-'
    '3$P6Up#}LJjg>)1>{;%0Ii4w|CBFxc7{lfrr=-JwV7Uir3eX2o=S>lxGTgr@{gtm*00>xF*8rTRKoay%2-'
    'L!+lok(?Hh)$y*cfh`?x+`IMPOrt=IT&ut=|T6G{VjLvX4|Ip`oMj+#0XbQX)b^61bTh%*~NlQi)mqz%=QAw4mt(7VC*$xDw1<'
    'uTz-TRAmH<nAnj1tJCwQ{*zY6=T!x1M!`q1lh~I!(=Wf)V2dRvG!^GBxu8zM=8qAi;(#pi{~o8z!4&g>gI2{ko;6mWZjFMKCV3'
    '4wXZLuHG|yd4*hi@#b>!;cq;EX*sg^Bs&W(HW-JShygL+bK_D*2B??EOcnQqA`^^`aEcMnl(rs`V|7rrY3%yGcuWyoTgY~x3m+'
    '?IBye*C5jU5o;jAUb@a=KjYA;ea}Loyv&Bg3Cdj3qejYqtxs`3K*hD)85<T%n~Y2i=r<$hB(cc>vrX*Bj|h)f`c{5q>VB(4GCC'
    'EnBLA1$8j|qis!4^tJ(098z&t+>Z|&O%&5q_)XZN!!uREfjYoT90!+e`DG&Po4gF;Hso2H*FHmC<BQfDxaJc4Q<K4?k1BFf+e5'
    'LkPS0nSs+pUBY}bS7g8l~i;2T!Gh8l@^5O_?k_=O&)zJgZmx<_0@ubxdCo~qKCi}aRLQ$L`XQO{u8W~jAFWESA|89@)kdwVh;C'
    'PV?apgM_+t|STlge!|j)H|;MX@+Z9@;pN*6THUN?bM_5x>vlTq{4hgV@B<`^HmIyD%|AQpx|a#b2v-i<-'
    'hy|H>AN2Qvb@}t)sCrp8id)J&diZSkY{UjDnpDS{e?$UfV6ygEr5}KGlb=#x8~nXD|CQZ1u1=oJ&y!0CbCr_D}}N=+2P7Sb#p7'
    '!?#BxxM3+%ppoa1e){lSK8sp>?VX$Wj4(*QS*D=;vmaj%#3y}Z06}Ss*9NRlH5Mti+!p5yO*i#i0#@Ym=H3RJ9*%c=pCbh-cYh'
    '^NI&A}H&>xkFYbd4bA3KfF!3@J7DJDu72C_ch79UHObptw*|3Q?|<wQNJrP$o2X4a3U4;0Qp#9`*v5m9tc^6+fjB4NS(xnN433'
    'Uw72aigfpB+2P1b|_h)Gcth~o7sRa)0P*vWYG2@D(GXI9|(!|Z}ER<3A8tf#ej2FAKFbdd0B~SU^LHju_Q)gZwrh6fP)bk?Z-'
    'YOM$uX7;2crNyX9Y%nWBmTOu13z91|1$bgn+t*-'
    '=iYnzV(!olix+Scq32bvh5CcE>js#u?a=!VvZ%wy8i%t#UzTwwfxY9|V0u?y6wu5pQBV8kP1I_B7YZ0Fd0Rk1~Hsjr*J>4w__4'
    '+M2cc!2dVAdoz8ociR*(o_b0|8jvkeT;|IMNnF`#D-hA=A<m|B!|+V@_beO8?SuLfy;F?|MVcuW{r@mxFGi{IO8QU)$8uaJ-OP'
    'r1MLF@&vsE{vW&tACUPqr%Cm(?sF3|P|>7?S0zB|-'
    '{9cukCySZqjMA9>!ecXYz{SZHH^qD5oT~$d?<05BiI(Go15QV>|DV3`pR<Jc6A-'
    '7ZD;@cl@6L<CstP<i+udw;v$z5((S(9(?>ao)>`M4dd4rV4(DgtWJq}P9u`m<7}5LOs6KVuAUeL(cp{j&l;L8oo&1RRTeN8J7F'
    '-WYnoliIfkb(xLSqD9=-'
    'Oe7VgZ9lA&=k<YHjqnC!*v!#fTO|koWKm3yL<ClX9=JH;PpoDaM_WUE=L;=C{LcV%%Uq{OO9h^!UupQot3rQXr}zK})h<T{1RA'
    'D#8Q5MN@n?PLqxCXpRKXF$t@~%|y8&gE)O*tNyG8nmxVsq@9vXL-'
    '%7coLE9L1ud)Sc29~@Y7!`xaI7E^DUdy7(Yvbugrv(YfNrBpke54s?}D`Rkwcqz?-'
    ')}Ybk!%}3rtvrmElQ}xO$+tnT_fx^9=1)xj(jKw1KO0-9vJo7Vy}l=j-W07|gDp{Q2Ww*-WLnA@vgpEnDLQi^+2*-'
    '#8tMaijg124;7;#$RYtox+Jexgb)E2O5f1ZX(wV)^yZj_tvdng1c<>^Krq`bjFoJ_0w&EH|J90ylSM0CuS-'
    '!a6Ucb|obpOb;w+&=dbeDT=O}hw0dK2FXqf0qTurYJ5cpGVgJyJAwQj)H!>J|FLHsdl_7kN(}#8-'
    '&Rj}0}YFxZMehPhv$*dGEU69(Td$WD6S(!2}{QMY}7Wa)ePk~w6JDG;vmj}!bzdCAQ;d3uZ@`{_<uEsd_H(9^a1Pv;F;3BCWB9'
    '%OcsbS5C89hkE<=L4?Xot=FK^#$!*Hb2B5x+<&QM$oV#$ERMx)p`w0D)|y5PXxc0Mg>_*X75Q^T{Dcp-BDLOhmn{n+ukOVc3YT'
    's2R+W8bJ9w7?!yTm<j(FRK7zI@#LJW%c}?FC)h35mE0yO(H|*)n;e5GXJ)ZxMM+e-MA27-'
    '&!xs0}H3J~=S#60HZ%n28C<p)X6TV&;qK;4*4{5_F=>i*tT|I0p;+yH|vVmfYRuZYM5RUzx7B&)jVx=kUEd4a0rg~V><`i%%;z'
    'kgbXIS72`w1Hm=mJ!J)l1{aWN{GVSE*0^AWi)7w9zNk6Aj%VgFM}0FM8EN*R-'
    '}qg7z}H1%QXT3$0&)Z{on~hMgz&DGN)!^wH{W?`Oywdj9w7@WexKO4G8!)u~~wM$u8dc$)RNOT)hb>qik*DQn%+AxsIvqAmAFr'
    'jaXtm((9+dUv;{h>SDwQK!3`MYaL0AK9J*<1*K4^_UgV=M3S2uh>I%GS!4$)zfXmDP_0c4$O~lP%o(uG$4D`n?k~lr&D})Y5^C'
    'SWh`X-+N8Fh_PWvo?O~H~NoawA;ocl`Unb7RC1*z}NX-'
    '|7I%#Zsy%5bo25qWDVW1ui7j;wY=6>|VoZx)IKvVqcC~zpW0x;J|rCPhJ+&zg+vh)A~_8qak74KP6a_Z63dQr5Pm%xITD8)No0'
    'X@n?hFgsy%O3$HFrUL2uPLhu0TVZ$AVhe-e_kRkPd6(5BNfy7A?8|M2CWt630<A5WeT+dt<J;a77<7cai3-'
    'ajXwlh<u`PjA0uYQ=z)}n!vngS<jp2D|9G(^Z0Kv4^z>7VyR0R#RI&A*OFkUAVeeB*Rcd(~P_^pYqoG@LW1Zd7I<vikmZ?wA&S'
    'EBgMgA}|mwqE;NKoI1sJ3~|dqZk;wggokH2llN7+tq8H;A|d)@n!zyW!_^{9CSk%DBGEXy?V|O~HeoVU56<K4C__t(hy8)(D4|'
    '7HQ)dXLtS4<M@Np;pV)Gs@N^47kM=lf?NVoh#2Ht+{n^<{Nn6o%(a%V66I{L^K}9bfw-mKN41?n(L&{kwFWcOuJuv1$L)46&5{'
    'kiLjbgv{~T}#5$s=t>%u!oeQBQB4U+0TdQFZY%kw=$2Lld?f63B&Soj48irU;EhSd=}r2ud-'
    'EUno@XBN`ZK@lL>ACVS&DGb4XIhyR0@opH&YpE}3WravqOuzc$tPJ+T4xw;I4wB>)l_tUhhk-HU4{x;aL6a^;dfu?jIgKyUls1'
    'rG@O=ow+Y7LvW)x~3;|j#a$H=l3D;rGPbZ5i#6TWHItG;5*6Rz8w`c?Z_^9ET=#qJ2C)gW1(r-U)!+H%uvKL1fdM!Hvf-'
    '``pK8*<|O?-x%N{aHYyMG1Ylz#<uZlc<YN7+!zZL_Veo?A3pc03enoBTKwULkWEZen*EehkBp?Hb-'
    '$!$j0vMfv*)Y`j@Qqqni%@y9YpodPX@N?(ZXQDOw`!m`G6B7+8S(xqG&TJ(n;@g#SoP8%;a4t)}Uqf*qDDV2W=|jX`O@BvI^+d'
    '>;m8BvI7hj@HPsD(EL1cF5g?Z(O@VFf0J}JS=}b0=pB@J2?J`p7qHh%HbHY!uP5Nv=6otc0{gBPYwoX*T9^#TZCxj@a#fXrOGE'
    'OKmG33t;}Hl&J4qM8GF}*-'
    '?0A?8cig@&PADH4Gcm#OPYJhvp)eCY}I4TaNBq1v781a!Sxq4p?>Js@UIz2g5E7^I8iOL9qF~QPyDKC5C|{t7Dj{{5u&sJcsK|'
    '+f>={Th|ap$J;hGSj-mwRQ?2Lfz+JGEtV+)^69<Nz)#${#HTRFenm)e}bNjLrR2*6eGw<Oahf$d3!kKmdcB&Mzf503qI!XNhTj'
    'Y}_X&PLxr^!|&O10LeT@7!TdMEO=_yihnG3D(2_0*m-2yjyrQpqT(;#)}kh3wzRVPa9XlhdXaI^%qyE9Nd<l_5TI-XrWGYQ2t;'
    '_aSkL1RyD=@c%?X{A@haJGhbArnLi72HCqVHPLy>T8Z)t-M2cDnt*H~>v;@{;77y(W@{FiXS81U)6rPuEXW2-'
    '6jJWv_$fsyo@Yy`Zv}hQWGp~)?*Ig_W6;z|@ce2YTQ|tmxuPl#Gn)_1$O?`=Go8xig_CODC|z&wOPkJZZ0u368W(o?Q5I&Iwl8'
    'S>f%95mWPb+@GGD_aj;qzAmw$0kl{rY8p8^Lo%pNb(fuP1!u;1HkT*8-'
    '7q$@*lP@Q}<Bni)+l{~nw8*0^%k@)&U<$fuCjUf%MgmG-<HBgd?_nBcOKdvBT)xUwV`+JIoCOa!Wjos~8-'
    'hB}SG|7l_z~S6@<aG>tg>^!tEf$SB49h|aY64vX3?{}z0}$Ma>vaR}G)pHH1h|`4YMv&zuBKjmx;-'
    ';7Iiu$zdx%(Rlrj4)ep4Keqf#$)+?&#okU+u)JAg3I`<}M{`%lqmcRHJz@-'
    '$U~d?#umAbmD&r2qQ7lfW&$)kE%yMuS4TV?mmBZXk1dn?k8weT^8m0YgebRA4CESb%+Bomt?3fVo~2uIID!o*kWnF5U;h!4T?R'
    'kcz}*bf2j+(S>b6jMyX{=w4iG<Bdio5hWiSk%+}rno-'
    'S!^2h)V8s(^hCjJ+5mIp$!R77giAewg%=9c<?{3@LKabD_$)CjuryiV6N-'
    'HXS0lZ2(mK)7M{l&m${x*!MmGCW<k2WO8S0!z(3K&@+37NH~Adm55*`dDuVz5}B@Eo)gsa`3Gf=hdDfptBtF8}advVa49>Y{W!'
    'BvwT~~!y04lKHtX6F4^`jtl!T@rU8K*Q4|IM>qCjxYIF)(E7abmON<Vlt13(^TV-bD<^f1}=-'
    '9tqllnnnPu)LQ8z9R9Iw81@ZF2@b3NjuHsu@_c&&K{0aBZdRx>a7J#ml$2tqtD8|A7t|GVnwBMBz%EbyRm(*^lO^w#94zKR1K%'
    'S%*ZcDV-#5{1a5&sw0rV)P#LTqGw)uQy1U{#t3(837??6eyG<RstBj0q8Rd`-'
    '95P>q?2ndMDol3<j3&AgxIV5l2cZQaMwL_i4?p5I~l<QcsniadG=qIn6Kfp55*e5xdU)PS`sFg|Jz6LkI3jn|9tRj!(cd+?A4N'
    'vPsTpNcCi>3@1@KJA1&zSf`Mw`H-'
    ';Tr7HP0rRH*^*flWdgr4Q%8q<KelS{ka2e70_Z=)$A=wG*}+j7P<UqXQczj>Hz}l0LsD4qqmUl-uxYF3kY==7Q4n$qlN9|LT(&'
    'sa<0%BB_B{v3?_5bV;4K-'
    'HEJ=aTLNh!)n#yLi&{#JxNMXmfv|zxNQ{7#w}5#1LwP<8xy52UP0$g=q5gk1$S`WZ%xPq8>%aNFI#kMF|Y-'
    'EgsSFBub|31BmmkTY?@)|jP9hxwMs?LL&IR9;f6_;tRTpl5rH6oL-$m-;j{|I%|&$`{SIP;+jyjkSIWIJn)?_>-'
    'EicREshP7DMuFxZLVOxrp~T^$l)gms=eJ8tSjac+fffmd<1#D<5{lw)oW4cQyRtC9!osU`?VJEV$vA|jtT6}Ba@nh_!G~7wzby'
    'mHX{RUXX(R^4-nCYU;kc#$b?`^?A~{Xo9-'
    'Cqw(j09&JDrLI$SP#DIoO~1pEHHrDzkz;_=<xp*+K&Gm{t4CJSyF6XBS}HL*$m)=3%BknWD}l9%%e)VSrob!V5n=fM)rx6f1iM'
    'aPr)vpf1z75JZvUbqNp8J6sFx|ZU)VPtJN4SvTEX(LJ~54Gh|@if!GGyb*CZ;b9-'
    'Fu`ph>!)lU7B7(W=~@Fj%*8n;2kCG0Uh+lVO>lXO_W<#QT`ply%DXnS8aC6xA^!cP)TL}=+w^C9yN4dl?J_;cNE;XNzfhQezih'
    'Tcs2CYd*_Fbx1PWxBcCm#IZrXw_vJdd+6M?+zWlI|!pbUuknMA-'
    '?C!yQ*wk*sMqw;^0iv?C$m3Kp;k2N*UE>(}Q^1qXEeM)e~c}*of#R6Sj7M16W4*D1MuErPF=wR5XQM@Q_$QJu#OCA<+0TMuvys'
    'j<-W5VT^-'
    '()xZAhqjydX+56H7X6&OPV{+U1w<>Cy3h0+*LnZ;O1~pGG1SKCX{xw&~)2A$@3Tl!nSS7!ULb{!me^4#mBp{bid;+*qeD9h6Jb'
    '~dM=X#wA>g4NW0(P_of;BWdpityu@1d7U9NAH+IMaMmZ0_h&)htQ>Hj?6sC1Q4GPLypJx=y&lL7HjANiaWnPPtQD|W36Zya2lo'
    'dZMji5HeMH2>g403EUf|MWMTS2<+a&)+M&8Z&-1H-iJym|~ZAo_}yul--t38KYfmNx1aftcupIAR-'
    'd#Q*#3S2W7t)ZpeSC_P;@O@k6QUB%7WuFgA!MI)(wGp?|4j_PYPXQU-'
    'e)^RgJi^dG^B6k<2planzAT}E{p@;>t9?eaF)<=kA@4$nQXyfysebdKCr7Q;WRrwwv&aE6ELUTG$n0~ua^FNf~eY?QByKJChq9'
    'DR!dtqk)epLnBBJ1n}(eu~)qnl|-'
    '{;257UR%k2?qtFTy3{h4r~*h&^smzKGu^BA&4iN}bTj4H2;Ic&pD6DTPYd!3;y)9Kkq@b~-'
    'RcOIimAY$pJ(#!hhXtd@L$>Jw~IXGtOdsCUDu=lChFCp)GXbhe;Th|@khm0MR?o=!%LuhpIzM8#J9kA)7ai?yHtE+@NxApgv9S'
    'KJvDPCI))Y{RCZllH-f8vI-'
    '?)Yw~C~9yYiVjgo$Sk&dy0&*(gyBh%*7KL_jz5rGD4OaFcU4Gz=*2>_}c5RScI1T$V<nf2O&sZYLCaQvcCO>ISBWiDbG*`_~}X'
    'Di^e_cIOoD5_$u)g;6gX6YmcHjKh-QFFwMVzm4G7nlA^n=x3~Q*J3u<TOvVcWO#NAO#GZPWJ8M0Vdc{dudL?Q9%R0vR|QLhZ@@'
    'kl(*&~?aqVl^vH}YJ6Y*z1R){7KoIbd-'
    'lNErszPj!s%rhxMR|R~Xfa05e)6{>DTTFH)XFh1bE%qhk4Y|Sr=eQ_eYM<#D<_$aPBhV`2t~n?&A(jeP26R=tfD0Lf-WmGY@kQ'
    'd~&@4>u#H%S2HpJU+B#8C&g$05U`6r*VX`^Ir;Rb}-'
    '4tj1@M6st3iB8k+@t4X!;xV^lM(pbkNs&ZYarXsyRx1GLLfIuHiwoCS8(gQf&pf*H%}?wS51sbDydqJt=_W(dUmv+lWq=p&O4Z'
    '8j$yDnk8`2!c<!0bAXS#mj&P!FcWor<e<c|qx_wIo#i=T`i^;z08ukpML+_b4K1uj~PD_L&+#sb=!FHXphE8fBEqZ@wleK)97n'
    '!isbIas&9)YOEePDsQW=Nq!2Z1)aY*Y0z=E)~#`hA7*8X(C!msJJQ!Rz@ccsaNCcwov?O+n}@jToihKR6>IQxjE^=8n=-'
    '*iOU1#yQjkmeNkQuMP<Mk1Z{GndP4vhmrDU)l%^x5Gcw%F(M!oAuHb5{QfoW0eWs)v<!-uaOcX-'
    ';(S|tw%U`rLw#n5ZXl^J^<KIny<oB_n1}!E!iAR8z0`(7YUj7)=ZLtfT7;4(c-'
    'M5*pq(b?6WB!hP?e1z<K`URqaCs?7e_LVB0xznm%|U?8T<}-'
    '@e*CZmVQMv?PaEGFE(`z}H=?%>s2Ta&F}GqbmsNDpz~IlmfbpTkrhJd_LrDHX_@M|=OjfI~&Uy*4!U$w!1fmC75ah2?c|X>$S9'
    '=mHmmsli`gFr^)4Iv*n}^;`)V((bhK0<S)8kXjDd1)9o#Kkw%t!*Q<ods&|0TgG)j;ZUBL92AkUfDJOOO!UZzJ{`%;)S8%A&QW'
    '-78`Oj59j!n1pj;IDH(&^rA3}#Kg{&Q5>V|_$a7bs@FrDpPr@>yh(sEx*D~&R0@%_6cVt7;)8C}(RJZzEU&(zDoj2v|E7}4$hn'
    'Az#H8|`;@5#l-~=;Zqt)v66RZe&Pg(%qNq(Bn3hX^;zt4SPYV!P+DPPaXPOiM4*89(eg-'
    '`;cUG!mntN}X27e&ZuQP|T}PID&!mHYOFQ>F@F_Z3Tc%Fm(&9AL|jN>)cIrBN35(FL@-i<vN93ak2zG9+-oQjjJ-'
    'o5Qtm2A18+3}p~4x4Mr^+aQ%v@6UGQzPZgPQAf<J=V=*jiW7`1dqaSWE7}spj5L(?4^EH`*K}BQ>k9rSDtvU|rSE`3NBc)f|2o'
    '51z8OgjpOP+vS~#L|{s8(yqq%PO>RIa^Ph>#{5r&A$D>FEAhU2T*;(dmO!EwX00CENDh8T=hkc7WE9XCjcbNVjS1NS0L2sk~L3'
    '!;~qY36ylkHO&dPmeJCx5?y!6@q!lS9%MOc_rSIzWIIR3DnRCwbT;DvFeqSlNmrP&ZRj2?VHm{CAeGJB)a}1aBxRu-'
    'a)^Jy?SOxLe0^Bd!>8WxH*o3>_xe0HXwCaZKKL1(Xw)4>3u$VFkX^Dr2qbU0}(g|`ujV^{*&lSO^K#z0Zq_minKY(;_SUYGY-'
    '2)R>&gKKj!l5&P>>673_sws3!T<nuwd#S%{H;TS3Pn)Cxmf0?2aA&le%g5EHJ1WBvzlv!I656V^(nP$LlHqy6Y*?eayrtOU(IQ'
    'OHTv`Xi)~;TNTi)5_HSMyF)BJ2vj$-Z&f>Gc1p?3&B>DUZTdkxBp{h4BQFiEk7S5-Wu9j#eOdofTQ2T-'
    '$oO|2#_<9=@;pPnZF5{ZiF%A58mHFMmVFnb8mCTgN{;<UafS)M!z`q1bhW9aJ_a>b56cCDFzIdv$w3`j>1^KPnalMpjLmeBAJa'
    'F1?x8;az-xJriyx`>_<=uHTCOR+*ix+)lbvqbvMOfym{U$Ux~dgxZ;!iDmOK0GvCCZKsLt^qhI6+J&h7G$ZYPGLt-'
    '{`KpyGKw13<fyi|PWEDwZzvjV;Pzf;i`;kMYu(UImWp=(nb@D%j>r&4bVjTe;lCb`y7N1f>!&PMNlU#1bFB1wn;IY7~2-'
    'Q4aN9`8Q0S}W&OEr0HcmdYwL@&Y{}#t&J{)=q35e{Rk^9cffo*)~U`kv|_!rqN5sup7Kj+~B(IQ8HVdiR3Kj@%J-'
    ';^+xE@vfl0c9Zy<@c8ui<oI7eR?i5E?@GdQHFTE_$0B#;KYGJ^a)SBHw6C})MJr#+5s!d*(-'
    's0wQrw%g|%3Keu=pPz@__pVtT4JD-DUDCr>DiQD4>p_k#QTF2!5gx}Po{w%eAh=x!L-'
    'F}rR(U}0&D|p;hRLTLT1s%p_9ZkdkXy2=Ag@7k`eCFAO{_^JNtKV#*uvGk9lZyuS-wKEOEYjC1tOLpvH^qbJwNN@stVyl-'
    '4V`+;gX55kBP$#rHLy&C~k1^}<H+6Bf$KOVF(Y^oOOP4|dR%Ph1d1o%4ZR-'
    'W*GF$^(rrKUH)<`nak!H7L=kGUuJU_Oa3mo)tbhEmKAkXs{u%1j<?tW9+%^nEOo31-=9T9(Us8q_72S)PpO;j*BE6-'
    'l`JVtb(aoC`K2>MbGu;fnn+z<G3Ij)$ffW<UppuFd%_x@QUp^(r*ev|GBmNC$cB^#G(EyU|xY-'
    '2Jq8^zry3lK>#x?7T8hVlBI#7c7MvlIX0$RiAn`tbY-Zth~V7<ZVdtEij6$BKCK?smN(715o`aRm$qX%iY5-'
    ')bet4)TNe{}*)?@vI6_#e7M7wvi2!Whs{^$OJ5%XPCMd*;d=h4B35bOdA&2qn_I==?D|6MCl!4-'
    '#X)!R)gHTR<z~&p*9<;e_oCnq_e)RM!fts=5N086*_FK&?9d{q5-w&s-'
    'sS*@b#*AG9kcDS;hM!_7=MXLqZ_AOxgx0s4Lk&pXyc>ACGlF(jlLEvypSO7_SiIW;vyh7UBrVlfgCsSpZ@)xrDv-'
    '<sR!iM3D3X6*VNk>)qV}$KDM=5%IwRdUhLkrf+L-'
    '(SjJg{6aoN`f`67?KwPb^$7tT|0*?(J1Aq`|yQyKByVvh~oe9)(Kk0x~cJW62^?%$K2S3KgR-'
    'x@{F2MKJIN$D0$805?;&@^BYXyWjgwF{RW!(cPgu&$j#UTm}rnGHa1%{r2KeVikSzq=qT6uz7zebLv5c}d@9UG7Q<(rk4h9p0m'
    'u7tmt1VBcOh-Yx&UILqvpWQfq?;;W5FP#j>zx4KW`5+C$Z3<>BQDm9emYC5qVh}a0}H>-'
    '|zdv!gVfbqrn(vgix_NR3kP|%jL?6G)`Z}_KGNA?j_b1C5Gov42sTK*rRbXX2Fl1A(Ps&`Mi)4iWEXGp9SADV-'
    '<cPk<t7;X79jV>Ri^%}<#0&NqLi_n4J$LVo#tslgBH%*wA(0)h;%}gWv9|uN!OlJh#rVjb)B+``~tXWXC(UlT*S}+D<lwXnF#I'
    'a_(n?H+cyPLF<;7+1e;IjnFM0R==c>uKhjR9K|PL_zMEdKDNj(IOXWk>?MS&sp_8C_^@o^uLCxP+;7r(K5x!3He@KG&<0Th?DA'
    '<gRb;7|?S#-?~BwnQaSFDT-sEGYD_q^XOI7lJc2pV^9o;kq|mgeq9TPcGqWCr@sgQ3o}K%z8_+Xb4$3%R3i(Xmr-bvd#Up*fhx'
    '2$;J6Z)1C+@^PBtq94<q8GSJp>+&VsrF75`z(S_xi-'
    'd|+)hMbMbSxOCv3m({$IE|9vwX&{vp2JN!KY(!E}TWe57*ywCA*$ykhMwJhODES}YF19?8C<l&bSPOyK1T*tQS}w<(<zSDW>D='
    '6(iGxt9{_5AUXuUWod<jYmuI?*=qV?jQRJlYYJ;p5>hTPs1UedfQ+>A1<i|#cKMlMv8#SvCUJCcIo$hEAo`UP9IB~y$!d+|i6B'
    'RCR#=-'
    't&3A~<N^{EM#w6c}?4i##aBl#$`T3xJ|`Wg0f6EZjj>$$pFQN>*!Yb6`1(IVPF_CW$pAcOFu8Hnz{%z#Xh)DP#FDWTKZ;hy!$#'
    'sDm{!NnJv{W`u;5-6}=Qb%t(ydjwH9hnhHJiX^tVONT^h-^5dEpb3o8(WKt~k55)Gwt5u05bjzlCn-v-cDx+S*Drf^%*Vudu=r'
    '?`Hm`!DX%12h>v3?Uqj$q$9E~PamK~A5p-0)erLqUsgm|t-kqRL3(y08zV@%Y1*h-TtcR0-'
    '_(^<4TD~Tr@0UW(+Wg!A?j1R#UAFAwP+8;=cjnGrR68m2wS&pR~WAl}KS=Z<v>7AI^6Jg4_$H4SGh};FNF1AQ-Di82C*?g4<?G'
    '0#h5=PL1I+r;YO~&bkQ8bnSWGt}1&VZsy(uWxY=_RkEdY?3r`aV7*T}!aL{PloRKE?j^ED{X43%GH4GQRixqt~n09$P{z5Uoc<'
    '757=<+!A$x6dE7auqV;d)ua-^$*9B<Yr!`Ej6N{*ebWgW4!5%50R>u4;+0Leaj34k)*(lW2re2tKWX-Ot!FtjF4U93Qy4fbQFO'
    '8SbAO<-Jt37C3^rWD+@tt}xv=|jc4;~7r-'
    '!;7rQt__UILLsdXVxHElV2f1*wzhH{oL{M@<lsmCP<>WW;20jnLm3KRHY?0G&rfI(Sg2I?G@X2(fgCB71>8;iP{6rk|=wh&vAg'
    'B0$7Bq;~!wMG8hCK7LTgc}@rvqFW&OQy>y(Q8MeHX2BuV_A4F4oLO1gsM%z9dm5eXO%tBGEr0K{Ky=T+Lfh0oqrOW3O~jjeU@B'
    '>#B_HF~anv~3?vO?<w2p%!=6&cwT3y%|!s=S6=!h*LfU{HW;pB(+(i$cn@Mxw9(MzQlVBBT7&F<xBV-hc{{OK*m)72@ZJ2k{*R'
    '0iHW`Ch-s`K-O6{;^kuQ&{h6SB$BPXf`YOYzVZ1?`q3DKIJl0aTg2)Iby@LEW$<5=AFI4yZ2iq#hj~tV?83{!$JQuc-mRG!`lL'
    '*b%61TpO@?TMC$db8p7b(iirnFGkX_8L59=n7F|%{sZQI{dwQm+r!&^JK{)Y7-5m<<Lsh2a(-'
    'I^2FqMRvBsjq_s@`7ZZE^ZH=AMs)jvM?VV9i`&Rh=q!)MDs3ho`t3v0|R#2`5Ir3qe&Kez%1MFtfDI(vmroO0mJYWAET2UM(xo'
    '<TrV+9mho+C08~G$ro<Y!?qA;d0zG<g1`B+%}(I-'
    'WC798Q2p&miLh(Qw*OhpG$VXP$dPXYWxhW@v@`L;3U0oyGIgA?G&A84tPt9VRreC3rTYec6d_A+!#`g$Rl}F4j#@>Qhrmfxj}b'
    '*9!woR~Ra9{D5GK;=lG)ZnJ`ZLFeJH94MKBrGIOe8m+(_WoJvZ(_C?Z%V8gU`$W8ru<e<{!P7WuPzR&tmkj4S<$Un`{T>Zy1Yh'
    'WY}ujmjD}aaNwj;tC@aXM&d4VGwHa+24fHA0cy{klGi&HAjj8W~yN__0ar(`Q)>lkWz$J%-UFr^-'
    'mBH_SUF=dOt?XYQ&Ds3Pc>u42UhUFYEAd%d{_B6Dj@+ez^Kq2lae6cZ$XQL`j-'
    'B{=PfmhVjwD?Hec5yVXY5EFSBUv2&^iL?Dmw2Dsoc2igX|id-'
    'IdX<|w!UFZw}&V!Rh<sc_hD`S;3i9{@upc;sM9P^}IyV4ET=mox6>I}tH`dBxAKdse4y1IYQ#MR!kqifa)V%ixk3vgJ-iKtbJS'
    'S7^Q!%a6wrqkMnN<}K1eaDI6!%7j-'
    '^@0nN%=9q^bns4owbQCTdwr$3+Xq_ku~hIV6s6yPmsd!cmEgwb>OU%cNYXm36%r<>p`xN6mWKM0l)oLaU76XiQ3S|+f8xRWo%g'
    'AscN)9X*r)0!S12^*`L{-'
    'shlpnuxiO_yklr@SG<+QRagPI?5=)j6C4BaLLc#jVONKb~oL<F`GB7ulzg>Nxiwhwu_dt=1%cM82#hPVQ8EH3_w5gZIH$4Idf#'
    'SBenqNj4f?!KFu)RP+37nJGe0f~4yTk;wSfqwltkZ~4m3YNR^~LwMM(=Nawq#TNL-'
    'e>@7TH$J=?ZBkebA$`f3zKc?S4|KrspRzi1u%3)RhEA+7Zeh_q;Ja{>Vw=baKtYmy~9ExD8%1{bg2i`yli;np4iI>%=y7p1mWt'
    'LnD`m9CA4q^^5rnpdC`s>#9VBR+z61S&;1R92^|<=6wbWCx!@j8TneikXB^0V2h+Ev6DJ$t-'
    'u6l`pP_RN7+$Q{u7LmdD%U0n^rCMZv~dZ3f*Ns!8X~zyaTopuIU-'
    '~Xf76jEiBnDhl4Y1S^f{GbO4koxP<=4o!vn_loz+pa9!}z*bM%J<9TvP=&lxkD-'
    '<Sk>tZWJHa^@dYbkI6pgAWtj8Fy5<Y?|mY(sh|<B^xzh+-c1J)O?a$m}OZB1_HhftN6FF?E+Qv(iDJ-tmg=hW<~PZsZcR80O_Y'
    'XU6}ZLhzbIYSU-'
    'l8i)#mdrPm?zk*Z{E5$jWG2i%~0DkBhq?13QPRz(ztu25#A=VZoq@{oj39q07hf(DaZ}?>xU)`wsiqq4e)&txtw(Y9FwbOYW@O'
    'dV#&UHtuBoOB(Qw=7c(gt=_J73v}_j3s4QgaNYa=0BK&nZV=SD}?g?BTX-v*52UT|nnp-br}LsRJ?~+O10SWXR&vCZ!vWeo^!?'
    '-9ja*$$<G^*AUA6q2@|ByQIkSl|!A;2@L<mnu<*&q5m@MGgG41@F6%imd@NjSJJ{q@Jk%{+0XyM;ReDy6`@+h6=u@HKH4fo_M}'
    'y`f>THSSZMp@xW<lM-J&+8ZJr+Y09hv|H5LiEc61QV(`IiOo#ALTo>^agk8bX&T{%-'
    'Y$;i$I@ueG3lFVEjUb`3!u(9p=Ot1)c(ElX@Ypy^*_fu_ITg63_PuWy%hoe`~GLcESrn}VQ<Es&=44X^rwXX1gOF*9uWwfKYxs'
    'e&~+4}#tY#0_fo?2t7*aoR3vcc&nqk_O<4!>R(Ccs5|l<bUF$7*DBaURmD+lNGsI?8Ay5vM)R+G;Q*|9le0Kn)Dat)=o_mMB+<'
    'c}j>fWZ%#hfw2Yu0!o^Ge*ZYY+!{zorq324gX^26TFgz}=kL#YH4#s(QtiqD5vZY6Amtgeq;loUy_8~ust#+>S9IQX^v3zS0w#'
    'D`N;#Yrr*&_#Zn^8d!bmK4vRP&s$-'
    'AE1e#0B23=Sq%=Al&5u~91E!e;U@hvpK&U%Ftb)@x27=L!)py=0BbWz+%qWGgEw3pR^*(}2tazRMMUqy+66sOx%x;uGltwxpge'
    '0p(%xh$7O-'
    '<oNF8iipLB@NLWCmjP2vFTdBFK2k6twjV95@Ddhj0e~A~h}M{KNJ)|5T;17Af1X_8Fmnvv9KzP2T4p`kJ1dxc4Qkp8fX(AGw;5'
    'Or#&2jx(__bAk;4>5Bxv2wWaXMJ{pO4}vE%M-'
    'L34Hcoy(XBvtRm|7?cMLyk%MQvwdMDo;Ab^MxlBq4Il*AAWR^jX2md<E#^9M3PL9=G4{L1zt|sfyx12waFGCv?!5P+iB|$MzWe'
    's8Kb3cA8#D8YmDTXL(6zTXLl_yCTktxMOL=hy-U1Ez*-rNsh6}AGF7nEgCtrHeM_NDI-'
    '2!ob%&0MRb0?6+DM`>_2^9ZCS7nM+7{rjGE2h`NqqEEnVlA@EktzMpj&Tv_7Mfgy*oOfw@x(Dg_i18n<&QLUMW~mc?7)Lg<7sM'
    '{kib%9Um8WjXPua-<%L8$NCoa<8@eG~aSp-~q)YipE#x8fSWs3yKWN$kc`RXoCtC?88i&Z~u<HVnM2o{`T<@>4-'
    '0#!>y*3^&&zM`1(B~xr17c~_Tn;Ta2#u6BJT~3zf<rUTy$$aBYvcF)AQw>kN5>@u5L3jxc|0zW<V-'
    'v+3E`{BSmYmkMsL4GyvsvaF>icl4X>0ih|k6(GRfl|XKIFlwK-'
    '6f4Mhysv}z$Lv~|=?qW=*IAym7}$D0k0xHR<uk+4A+Vs3wc3cR}9qi&|Z<XGRKMLSg{+V^gF?S?EPLo2I=Q;*YkSR3||X9m;fg'
    'cPlCcQsKVAtKJEKu1rQ1wTLEH`vXRq|sW{JT%6NZg-=rUjwS~(^Q8-'
    't&;>|%(98+&ZG;oVx(ke%kTuRPUp;`K6$B6W@6E{rzo`K<OJcYF{I{_On5d#2vWJTnLz9qm0MvxsJ4_S{Fvwdxi*y#ZKMI<La{'
    'vay&5(1d`q8DSc>lrKR9*v+Gx)_*R-=~Q^s~09^VCkUa@qTUWup)|5>cH5?|cCP;-'
    'RZ^%OtHlnU@t?=TW0h(h7exI}O4jkeXcgU!iN6PmSY^tcnLnUlLM*6Z$;jJAnSm|??Fr_|@Vw#23QGhGA^fc`g|JSh(0nG+9ib'
    'B(KUjSx9-'
    'u@}uw3yuPr^A_Spe0zD1`PTaqmAs&SP{%G4d$y+r=L@3S1=#Srd{5+!l7TW;`!acsDdETe^<V4HwxVj`Y7)vA1>eZUi(sJka57'
    'a0P|Y9#TTB#_elTuzs5zlgF+xDE&>)b^*1H$lTy|_uME9@QhgZFsC?mj<p?+oVvtqoY?0|TE_8k;{3<3}3zf6W`wD6$9^HT|#r'
    'fJ%Dw+FUH0P$1@-'
    'Dxpqs}xZ7S<%GrctGM>q%%z0yiIy*izPX|s>^{F6W$nnbKX(d>XNH#MRf%Zm*2`#SGHM|p%D(SXeA4{`aN|Z6BQ`m`>q+PxDO1'
    'a7+|G?Rcgg+^^3)&111Q%3Nl<J3Yal=xiZOb=VFE&*sp1D?ol@o737An^6w509Qik^hjSf{HFN^$`cs8bx3a`~3%hN5{4}VGv?'
    '%-qQ>#qP`zOZtAoRwFE0)n-%1TcMoXpptcl$`6yPL~l+fr}t|1s0pk_OBQpSTYgm~-'
    '{U+aIPaP$Xb=6N9YnLO9(zh^b`PM96<cNKq~Om$H1rmA@R?;D<<Ch9tM5PCVVP?>yA|(nq$6qUv0ng^7Glaj%wgwqkOvmksLjB'
    ')#&3GiNsS-YpF6whF|c<pDD$n;wPFSmJZbBca`nC$ITWAot7N3|ML3HuI@Ykv?So-'
    '3@g3hPWSSh)i%5oSUz~4iZCdgr78bOPSJzO7~cC@-pqA0Kv!N=C8;{Rh)Y0trT1(iU?t{bD8~85$W%`yG#ZOT;aQ*n-'
    'lIgnX9!4GVUeKw)K+j3}0L`j6O6y40v!uT|5a8!2|`{p$<AZr(5?c=UpC-'
    '>$h^o_&>Jfu{)>kTjB<0#zUYt8AFTetc5M=3Be%O6=7e5uKehcUp1s{&;G_gKcEVMalw6y^ow)iEpwHKbzV|N4SQW=am1PnF7t'
    's>xnLi~2WvKnED)@I@GjzgFB;)c+)l$1gYh$4U@O9HO&LyW?kt=uJ*go;9WE6dc?DqZS%U6c2HB!$6bMPQ1uq(KEBb@<Z+tbdu'
    's!Sl-LX!!*S&01tiJ6z3v>-i*oXZW7b?cMICXEc>D8~-mzH&(ZZce1oA)>!pVw1BI6=5l!VEU`nQ09RBPx3|cg(&wd4pP-'
    'Mhu3NJp;eH+MRQKrG|;H-7)GoZJbCG^zpe;!+)Tn&Hmt-3{cc4XCgxyMJy`YS%>wAooM=>cKX@Y&45ugye6?(vVAj-kse1Z8I8'
    '{m7dcPxukmVLd$}sB2VZMQ-2#aXddM%-'
    'fX?i=qMoeAXS4Jn@D_&$K#b4@8&K?CrlPg=34Vr8%9miiv0vR6Nf~(Ixj#I*$$vguM2ZYu6}lA{Yi{-'
    'NvQL8otBdSUzPa^$AaAM4*5)3jtmytTrTCPF|NUoY@;?XEVEMwnP=XnXfNZQZfpNDis?OlFEkrv%L}EhT<*5%S*!rHaRR)L5ua'
    'eIL1${jXX3O$t)I9S9_@j$=kvv8ujMdjaL|ya=qO9Q5w97FNsIk~dtr3M#+-'
    '39N8PuuF9d}C|nOIXxMi}*7s>YOH+oPQQsw8=UqQe@7Oe4|j?>U}E$~_0BZMK6o3Z;n-'
    'qgk5f0&oGtS9$Gpmu5nDPbm4ANRag_aZiq>&Y++_F0eU<+Gnopqpv6QZj>iosfyFmYKkH(RtOFkafNVGsVTJQiXP2hL_&Cch6t'
    '$sHSyfWYxJ+~kTzuRe-'
    'C&H^O2r>^fEh6Y>+{yXs&r@Rq!?Nfad!|B;(^uy(>Y`lwSdhb5$H&`G>c`b#Oe40X{<;H5f8k{7WN?R08A<foQe*m^+!rMp#fW'
    'qYmU+uT{)A%rX_6Jl8Q>T06oN74BlqTDUjaN{e;Kq&Jwl-'
    '<a2{f6B0`V)#K(++4|<`(Cr)C4~3THlf494_Q|XAyY#WG$LJ=H)0+*L+r6k>}g`H%p-'
    'p(Cz?8^XD1_5k@@QG4o@Dc;3%^jFaBat^BGejon_w2XZ{h$(@l)vIYrm>At_R2jfF<wosaBg(-W?{MP2~xBfY-'
    '#HWgeP>~_?2w02fmz)`s0IRf&z4pIDsT};-W(iq7aF7L-'
    'Rz0UwUl#0{t8Ykky=YRU{t76jGAJkYKt$rN2mY`NDZ61x|G905+YaO?dayEcJerqNU_s7$_$XhfG<R4W^nwHb_voewYSZiN9=?'
    'Zv-GHnC3n)I&P-QH9oHkOgXX{O5FX)K{jmmS5dGzMbuPu?2l@A<|Fj?fR#L<G_BBTe}-'
    'Ma$#w=sjbP)GVJPy~JcoGskR>5RiynoxFLK-!o7CKXMU#9c&(3b+#wUk+VUc=o(TI5L{KLg=57+JN@gssrYuwzL(a?u;|-'
    'kI%y@guIrqnif}7hs^1uJ^&pG>Mv|1nrUh-?@+C$@eE+r3MbaV4-'
    'kf%C<6mgTU8WBOrMp3#+nr&hAvDHoVsV4%_rGLU&I2D8wFSSz-+UZV5C0dN+-'
    'q9SA(K%&&1Oym^Jb4<Oy8+zn!pjU<$kU1?FIWeRpox7Ch&yiK8PIIx}ERb(S@oA&LCYra0qTx3b$Fypot9h-'
    'gE9**oEC#HcPj5fLS`bjZplmBbFIigLCxeNb}?dT^h0_#zgl7-jlXWXscjxPgT`#$D-@#@-'
    '(J2<Z;rQR}1^bZD$F+aB=qhTHJ~@p^G*@=P|8wX4`vkf=eadpx&wawLHprz2Lsv12AZXVQ)k_xKcbfsJ!z*WD_RUKVtYCKuyfR'
    '(f=keU|wgtINx;v182l-'
    '0X6SbOCZxL;AP~P1865c&n~dOA`7@#l3y6Tg0UBY0E9`fva3Ki;N0l@kVo5SA0>$_Kfu;Oe*(2xB{E>MlO>>o7G)Kr`{%3UN{Q'
    'JH-'
    '{wZNaRw5KCjvyavjp|`Ow5j!wz)nkRmc*kKL1e`XucpiC0YSsPjbMpBo=IQ#l^67jKc&_DURmjL$>A`Bze~?%AMn)m~BZ0U>J{'
    '4`+<`m2q&I(>&k4rCE69AhyZD1ie~~11A@FJG$Ii;FfU8KVto=hn-'
    '?<`fM5ZwQD5}NYeUeuuLffkrNs&xjBRyS@)9er!;$>f3$=e@`B~o42`Z5xEj*dx*rqw&qwa4zq>MJP2+1frwFY9}?}`jKovnv*'
    '|2jy)-'
    '%Ny<5EW?)6t=N2H+*u|=nm<=TOXw|z>w_;KKGa7=w>fR2DVLsyzoNkqkNt`3vLx%AFQ4)8(N??57;cd(_vH3curJFX=_QC=fqY'
    '>g%v3i0Q;r`BcQklw$mbupHts#W%Umz@A1#{K{`A8w!u;0!DZ+dG1uK{LA?JTA2o-'
    'aoP{?Zx_s8Mp89)%|Cz_3oLQ{BBgiL5pJX%2Yr>PlDc5eEMsQ>*%)J^xKWL5BvRk%b;<K<K`U4+}*tNXnP{L~Tr~nJ1i)}YtjZ'
    'wSHkV2r)K$EEN0!KdlDX0^c;<=Q#)o=ct=y%ugBZnMBaS~o09ukacz=>JLh^27rb9Cs993ODnQp<jhJsev`?p^lg8rkJgwbIzZ'
    '((=wF{NNo{%bOS8a){l(%VaEr=r{S24^WC_f#@GpXp2rgfb5RU6FxlN>z;tPPTHCj`dR4bAyV<v;1O8Q@@~l*Eh+Ru?zgB&Nt$'
    'hoCS~SlcuBHsiX<CQ#7plOQ@;75HqsvcsNv#?J&Q2J`!`Ktfp)#A8R<36eKHLd1*F(CUlfFx4fknO{idVd9bRaA|C+A>P|W5Te'
    'OS0ODe+~MkeaJ)17)6zab5Q7pbj`w3N+Aa`kx>O_Wy|hHV#FCSo`PNw3H$NCj!sQs=qahl0|STbKjJyv+<gnT7U5rG(|l$&3ig'
    '{V1R{k_kAL!TS8gj0}4<$amC~<&swPD!x`sG=CYu+&-'
    '#xLo<p#ExSAmm42b}4_iGHh@_MCVHR`)c{a`tjdKZP$yvvnR!y?}5rJ|_SsWV1Ya4UAl76bln3F}0VY~TBv#GKaIt&IaS^{8m1'
    '%ypJSt(s7oPzU9^a>PX;u~T&7xRCFOfKXH&v_;8zdKbt1bhstYsKS)k>&r@!qwoD?*=1xV+@+_N6CL_cC<W|5Ss)}HISR_=d`?'
    '6P&7F|qA_k1CC5VLT6nw22yEFlLD7`iS()Y;ve>~!@{(sj;BnaAFth~MF$C*Uh*ua>kEt{@cwQA9B#L5LH)tl?yHBx`#-'
    'BBPi_%<P7$pordcN>)VxWB^Dn!#*kT6z>#26m)|!=9*>AsmN4JJkT~ePaT_c?Y7_<fW1k68$Nqml00kMbk34E$C@lJYwb?i+ym'
    '{vSSE_5CkCSvc(@&%@jW6<=)g4de|NI8uV2adaH;b!dVP0Pzj8`b%-N)lJi5a_iE<V<{|NQv$q#(97gutL+4-'
    '`*HUMJmHgSB7~=%jx&^Vyrt$I@*vL3aT-'
    'P12fDky*e$ejM_ifZruxCkOiuS8jKfN<1sXCB2L5dNS55LSTou_J4tv~N$$`eJF!KSsChvS0p>a1nT`Q8n<*;6v4mw6rG22Ojv'
    '#Twp9oS<xaW(e0@wGfC!{$S<Xi><@8P4}hfI3`>kJL_WMBc4+u7fJQ6c8QfYvRV#1|06Zmhs%vEc3`5|KUz^6St33)Cy^PN&E3'
    'H+TRtidv$;!;t$Ha2JZeB6?2j0;K~&{ucxoS8sq64P>u3;|j`*3fw@Yhu!;soziitQzX%|OGI6lAo^rt|D<2_xhZ_aXRn*z=**'
    '>jYN_ovDtyjCqBr%G=1MAJm?`o9HFxI6R5fOHqp52iR3iKM&n!TUE~I2Cj<4=Dfs`H<GaRo9DtJYNV6K5-'
    'Ny;1g<tt#=V;;XtT2KXH8Wljz4<%w7nHO<!brg<(s-Is;~C?ge2CHw5Yl-tN`yVQ+pIoi2l}!1T=8%@mm>%exiW1Tm}~b_6`5;'
    'a=1c4Ktu#5-|SMg^br6E~G$qzk9n6;(0lUY|>9+?StGTl)Kf!#WBk}*TfWoJ6zKln^Tp_$=fY?0NIVhRJc@Y(A>k&N?M-'
    'HleQmV*;$AO);|Gxpd8J=dHTIP4^To4L(!j2Amw`yj@f0JunxZ<+2fKp3-'
    '*PCy;F2kmYEz~CP<R}3+!8N_5dK^82#G6>hzm;a7S!nmfAl>wO-'
    'd4E|t0oq7@K`x>S8%K?t};jTQYh2ij?#k&MlrZunzKbE^i#nmgjgb|#K=`RJ)+xJq`{kG}W+fBV+@nhf1LNwa6j>~uMm7wL{O1'
    'yrx4@O+0(uX4xRfwXOc!Mk5(A6LcVN7);#28t9vnW*c0V4r{W>~vp-'
    '{Q_pqD$ZC&MjxLGdNg$+!DgP~X}fAsty~5R2AWn4oA8z*TQ3KIW3%k7CjMUcXF{#sGJ(NiT#{|_6<Oa0V+Ya$STf<1B=d>{sww'
    'HD6z=A8*;s_mn53LUGBVv}FeXC=6g+7VL#%@1mKE$=<Q->OdYPJ!Vk%Ddj$*yK8-'
    '?vDnlhZ>0z!}*Lda_<>}&~fj8VRsFH*D&N&g!FtSXaKt4QC*+Nkm{q-'
    'p3p^43pNOP@xSif>}yNLebgX~o!4*;$Sd@?Neko9`5DI|hC;x%LD_(@SI2wO7Mtn<l!usdYJ-%|aoOR1@_+T8-'
    '8UuYN{}BAkwVuJ*&@Be{)a{GaR(a-t)SbxnVm>2fD5mm~(q`!T1`qckE_g4*C8s;miwM{X85mDZM=5HGiT_G|+cVP-Nm2V^U2U'
    '(d%yfdZyR$iNG|O7lE<0;G>r8oaHAsT)pJK+%X0Pqrne`#hYxOd8x@Y>}Bhikb#A*QhoBOg(uf5oAIv*+e6!SMyym5lawf!gKl'
    'Bw^%C3W{Qqn8?+49kM2toDm>?Mvqi=O2@Bxhv$a9r(*2c;p}iLaL<*=_A`R|%Gr`UEayP()G7g$-'
    '*DG8?l7XPx%5frKw5D~ew2IVjMTV+J!0hm2{hrd?h<N%=r=qjuRsf6r*t1&u>C3FVST%)}@^8<DXw=%Pv{|!QDNUR8op3)V=oJ'
    '=feLeaS1Bfg$nOW>*t;%T6z7fE?0a}W73$B^s_W#x=MJai}f!w&rzY%(qr+)W4Ba*2lj6vS?R#>A0q!g-'
    '3g%9qhHtbeLw!3afZ&CJgwg^KNpb`*gGd~~q8&>Z}sRS-'
    'q8kS=1Fxji52lWq~Lqfjrs9LRRNg9<bc3Pv=|D>+16x~mZ3b|v#G%L$j_yy(}{cKTlD5HDM&gii`BLTqk=x*g&8Qb`CCfO1Pkh'
    '~^Gi|lLg#Y?hJsg^YPhO&*k=?`_=e^3ynhV>VNH@OGKz;@4F%|F0@61<&xT+CwWtoSP#QPP~bYpm0nu3!F5SlGl&sDp_VKC%J_'
    'rmW)vfq1ISEB*vbh+V^M?DIH;HBaol1G5o<g=|Lfcd>?4GqG9B-t`x$A<kD}1=J<El-<#TjKG-'
    'h2N$U$RQCDg1;ZrlT9!FK>6O_r^0~smNN1M1g9fRmgCLN5$PJ=nb!%7CDV>(#kF(fxBP-'
    '@hGMcJ?x~Awc8BRe~f+KRt!O*>ySs5I(`C4f*<a2!AW$g3hh?Af?Z$vQq;O|`v?m-'
    'emEcU)9%CEF%lheMx7_s3F4subo@3Qd>Dh_Xmzr!VDk85%MFJJF_P&SH(ma5H0U+=M84oVC8<sOjNdEKB+daizJe%Vz@gLZX3B'
    '=fFjz`=}_Ri3E8M~WYV39F8O^da#!KB0cPaq)xPVlVj8&ns>@>`ljeL~ZDID`<Xu>eWRHJYl8ylTG)-'
    'v#9`er_9#CgL*x(yYAfXH9s@kAdN>HT+PfIWpa(KaL!dARZ}HnD+!Zd$N<yj9P=7_ipH-'
    '78JKK0P&q!9Po3b74i5DKnh{8PT7VZ9)I^}pye3AhOrynL%6$W_yS!*;wKKVBd$H0j!^s>5BMIrwwNNfSEAkXD;Z{o>p^6`v)4'
    '5}<T#f^EDwyp?rij-ls3Zy4sHHXm<OIQcr)1Ll+mTJv+BrC5_gl+Qmg?x*0n9dl7Gd=!Hk}uXoaE#Gg6<)rcwGnSqr$8RUvm6W'
    '-P%*5&J_IYnMa^&R^9%SME~F}CEgjPV0H`E7@Fu0qw>QOp~$6$C6+2xR$Lynyv(Mp{u1ba^_(QlCQ#D|GaAMk`C)ZMh_TzC_&|'
    '3bL7VYM{4F|oaetc`G_WiC4~H5h6PJB4&_uhN7}IZII%7n+pSzL}+UAig4EgCJWYBCbTGfaHH;1$8wEOmW5D`6guw;s9-'
    'zI!pO}_@Xs{(7O))$KtJ)Eam$t3Ie-sTa%#=6WvB?b+Rk>78?b%*!kq6o@Y)H)^OC3m$Sf*4l~d*mVLh<V&5uJPsvH<`cW9VKS'
    'c*#{0H@U>y~Xk4-'
    'jL)$QA%TaWkAumR{&%!&Bwl3><h3awvVr0qoU)}~&LC$$AEvc79e5mS3HMe_JNrJx*Dh?Vn_LkIub(t9ivW}+e8{nh#MS!Wzx)'
    'i4sQGA`Bj#2OW<jJ7draK2cv@!-p&8q8u1;^@5yu-M(kcV_~9pYf#HJVk8aDpcZ@}kqcXFLS7-'
    '|;jO_?=MB(<3PYkgf0Lc4|)g5Hr$f9swi}BGeBU3D59z*AyecoaK#7;D9*@XUPVq_lzfdOx-'
    'x9iOq7%#!hA==l_<)DjPT5dkz#eTGg@!pa89#sO$%I{#43%O^3@-'
    '6A7_^!{=f}fN8Tes%I@+*O|lu0_5QB>+1nZ#7*BwbyFI1?FNqgR6tV0tW%?dS-'
    'I~Uj6QtE5KJ$4r!caRgTYv9rZSyXWK^d%zTuON!_8^|d_j@45!(4>R9HGd*4&bhM>?7XA6Ur~D_TKkE*S9SNofY0CR6mo;g>xf'
    'k3tZ5Z+?U}`B_rH)E37omTy&7{8AS+>W0VH&RklSPrFb4(L1w_tI#cv%;j3u5II4n$#!3-'
    'uQeY$^0?q2T>jS(vS7!W!I72v2IF@MwMOqxDNbWrXhE(eDuJZ{+uRVLcKP=EhowUfuAdegZy#UTpsM=7kVJ91Xi)g^WG;zV-Lu'
    'c$ZK?Ra`Yh7(@uM^S$JCU1{c~I)TcnyDC@}hA-'
    '6~!o365<)rlgezo%7Bk<5Oj1(~dkt{ngHw#IiycNgqesg3@?GghMF^F<ydpP2ULwknLS*i|w`gFu8^+mERA(e%#`3w^=8y7MUq'
    '&AbmTfAMl@CLk2guHCm4m8$a*v$9i}RjWq|H1;ReG7gH-'
    '}AH~@A3sjo8lu@scg1bc!+e6_JMPk*%$L4(az&{VX01%)(&sE|Q1j;6s2J{FAr>U@p2l>f?(kL$Ck-nM|-'
    'KKv6ezN?;EM7QVsYg?|Lr{pNcihwotez+UwSrk8+#W=AY~l~b0}GB)8kuE57-'
    '3a}VB2WIHnpVIUEuntj6T6mV<W>Cv#E*%ed3MR5<itWlN<gR+)}0grRJ)mY9kHg<#7~xbp0hSD5Le$%gQtBwujdB?{+TY?PF7*'
    'oI*;3b|ilw&~fPRDk%DFsvv~AFR<R>78L7lwMU&M&NK{Cj>{-'
    'k_C)7|_>*P3y@n*X?=OEy{hH*h*0M^i4BeqKMrD%WME9rwtm**^12+wu*S@6N+?eS=SOr_`o&RZ(e94*R^)Nmo$;;9&<2gIVCV'
    'd9K<WP~6Y(kCUQDvUg=<VAqUFcAE@@%6$wK%az{`!ZK(=e_NT7V@0P8Tv7a09c-A!f;Z)EtiS^-8j{{S`s7A-'
    'O{4mIvVm;YG?$5(`WfqcS))g2BUZJ>H3mCk1(ubQLjK_*T(1P(ZBcE5Y>OcT~N+X%_LV5YmCpQKP-Xx(WVYEdCgsJJs@d%l$7='
    ')bV#MV6AUm9)ogDyx2WvNjuDnT07NL+lX$ff{_bO`y+ok(yGf9lEE2KtX{uWwpU}t&G*3Cd1pf2OMXL=?Tsct>D62pM^$j2jSn'
    '^HMQQ;I$tY_SnYzUTpS{9daO~m(paag<&~nQmpaLyvvf{)2xd)ZV`ZM)?A!OC*ivc}z*vrxplW^-'
    '`{Qi}BFalWjBgc<e70x}T9Fv}UzBa%^lP)-6fuF$AuCrl>!70OrtpS)ElDo!-4M}fwKX<c#ZFao*qpMC++_4g$O3lD2EJl~|-'
    'f2<ua&4_2-'
    'G6ZcYb7{!uJfm&=A;)?BRsEV)%iRcUN;QE?U*Kz0zCk{iS0FI1UR8VoWLga$8T%j#XE{T5c_H<9|5S4eXazNk2XAFTwc4Nse!R'
    'XK*YvKZC+b{tg|aB`EDrf$aD@7X(JRtSiu^w6>n>x9#RD(E4DyFwW+Nu;>zB=4Ud0*%k69{9$HUB#hr;sIYoaG-FA)=;{O26nH'
    'm|MmuG`L8dqC(eFwcdo$x;A0RSGgHOpq|l>tPe2I9g(m~x$YkQ_BdmuD#C8-nziJ~B?JUAoi}EQ2j|F~(VdO~Lv_58-'
    'v~<PYq)EUE)DJLSvFPLv>qvKleZJwph%jaF^U^<0G5-'
    'CbZMZU<0C;SyBv*Ivol#EbdDX*cH(s_4zS92qanuMe>3PY8JjWy&jN8IJ(FPuAiS@emSBIwM+tZ^>PKdajN=k`hIi$8+!B=i;f'
    ')0ATIDFtq-'
    '5*{P0Bs>8<0;Q!7ijA+N=0IfN*r$8GajJ8yKk649^bf#l=4$JKH728DnTqti#H_754y70A=3!OeKY|Uf~DFO`M#g}pYZlwa(88'
    '`SN|DeG_oKbV1u2h9Hek+SgUBvGRE!2#*?Vjo$a6EhCYI_~AH^?mePv6kr_0$vfatD#TTMFfDUQT7Hl2Qt1+YMjfI<71=Oc2};'
    ';h=`Zw95K+^}-'
    '_UYS>SIW@jW6l8O2U()>bQDZj}doP!rLV<cflaeFdbY{aZ2wJ?FMH(xDZ`&))B%GmlOe4}4<{S;*rYh(I&>}<B};XrQ*4pN{TH'
    'TC>OS?RmvdiT+Qi1m7faWX|ByxG_9ZRx%?Zx12HWK-<F{#ez&bp`F*-'
    'q5ltRW+Xpw`PamYivv&F7)K|#SQWf8b@33`$cR0i>&Zfsh$@YXBQDHPI&aoVO{&|nNzhwW8$!{42#PD3OrLka~8WP6k<?QoAtf'
    'xPm}2m;Y5m9tkcn2Bw2vj{nR<+0?6%E82DMRl`#)2(dN!t*<a2nV!vS_6Bwf#CMqXE-PUL;(3nK&vkNE`@$txVlz2RB+-'
    'r$2T%ZfV>SKVL>b_A&lbxmU7&+%;E*1W4YCq*l_^@P2A1-pZS;9S2xF<oOt+^*L0hHXS31&g%3g#n26%q{u-'
    'bR(4=Y)5Z9~>D6SW(k`?hS@DUWX8!m?wYF38V{fE3BA)v#>Z)Z%@_`3?0z6cWciyc_x1bCcsvIMRNoqS(CU)o_W;BmWYgY9T*0'
    'rWMQfFNZlz*6g~tkwJ`B1E*9fzv?;w@nRqc^jRHCNzjfE<LSmLcr+UJS=GUR9qjX8}T*;Dg>QlEy+`)y1WD0Q0xy1&s<{b47s='
    ';Lf^A#{~C~?;g6wpTeBmoBYuhwvI$?354gF(lMKbjtlX9Hv-DUyi%Z^qO3a8dUbBGK%(z4oV*8A9GPqez5I{2sD=+rYZAQLe0G'
    '>WQZxeY<_duCx)bg8GGu(%cs(xBgF8?KJxgRd1+LU;^-'
    '(xQfOdrO^2(KCgtSILVm`<bbaY>H;Gnd>&y7D8>M>PX|z=D;M)ReUPa2b~tJz7MJ>vqN#m&KN3l7QR`GvT!I!jq>4Y-'
    'LBbfPmxf9iUcCR;bX$vvs+v6?(E2$VMaMf4)t1}Bj5SAdo<aY%V?n}L9?Cb&GEUV-'
    '{tTeUOwj84zWEXEL)8{R=_DLggsz;No2$yxj}opH&<flO*_z^GV&-hJDw5fs1fE)gW^^}XhcyVapf{Er^|%I7G;c`-'
    'px5swyMyr4GVI1(erTjQnI^}Vw+i21Z_s=_Ft$az4U&-'
    'kO8h=WkwSQkk<GGY${8mUC){=uVw$x$x$<Mn2g${WLEHN{1F6gMv&J?0)rcb?7Q^-'
    'p$|x{IWQLmk7yjn;Y8|l~@t1?1pF|b?G+iF?{Ev7}U30}wfo8W~YkNHLjTO+ikqxyzIF8c;7>>wrJX!fz=q}SXwNjWs6AX>zc>'
    'B3L4O8;ZCjR_Eww13pLzY;Ft$)3I7rMqG%D{F_58m5M3gn*Pj=F(~jejp1jM?2Oi}Vyg&={#rPfo%`>v<2F`KrG)Y#)v?^bjO;'
    'F#9wEVUCWGXq67t|2pN#81Okn1>E`T@y3g^9T2?n<yOPLbo^OlwEeoM?R=_n1LtpHk>H6VrvxBK8h_}&c{Pa!CgyYKf3XozaLR'
    '=T2^la$+(noE1Pyj^j>D>)BY^I{AbPREyy+(9-'
    '&pNf85psVad2*EZWwP|I^d_`^z<+9n>9WKxAp(yPx_Cm^8nEBJ0~?!(0=<e5$a1GmWjEzzZSRf28m`3OcI&CL+956BAhHm+!Nb'
    'n7-'
    '4n+y2V|J%>r@C8>S3A&S>aQxwx!9NrcK_Y+Pv1c729Ap{GSTX8mDr)hoEo%kJgGnGp5wRu&uwWHV_Q;6;dS4VSUX(b7=}*4X{N'
    'tFP3{m)S_=m^Hc#Urk(@uYgxLvAvVsDy-MvF`DColRYl>QBs*qixvo(|Gh0n0@{LFxa~%mn>h~pV=N*`htE<0zs$cJ@-'
    'B0;#}DyJe2lM5b0W}FR$Umou>_oYK7<b6hVj5bf6T%uFd+*s4LMq}`@D>#I>$X!1x66!1EfcTK{-'
    'X;9aAjDP(HJ9yj`1<&)E`5XMY7c<;vg$syev$GW3Nt*wX0Ntq;@rUqx2M!B>xz<QLetBIq6XG;BYN66O-'
    '5+Ba;=be)OPve9|a_-k;y^F0hVm!}y)T7a{5v@YU%@`+o>{77OvcaWm^f*AJ@bYK;L7KX18U9j#gS0F}iTUu-'
    't*u+B=8aW4$2+5UQpz}Pj&#!C;E?9P+6;T?yO=xEoU3+pG1?!x)Dea}&tZFd8>uuSOjXZ8_)SV;=zL}lnp~+G3*mswB!rf1bWq'
    'sX5iFiowlB~vOB|RTbsQ;F0O@XtR=Uq+-'
    'e$Uq_To%7N3XWL0C;24}sFqzi#n32ZP@Jn!!#YH0b<Iiz`Yl*3%db8Mzz*6EpU5l!Yf|X1L=+}+tE7;WDv)?O6W`^#oYx#PE#D'
    'yt_OIeiCVB@~`?A{LTK=WlrW_%u<&+}7bxPePK@;*(5F%LWto1FZbp1szS#Re}Wz|n-ewb=s`|pjv5Ay4s7>as8UzBetX4D-'
    '~Pu)5LZnTM6H?3+N9nA{f8@M_*|1(KazBkmh)P5WJ5uC_)!c8I9i}8pSFQ%Fn0;NL8Bx&cfG5;10cmb6~lot*8F>2{nfz6eADY'
    '4N#HEId?N*u?^MtT#7Rz<R5pWEc{7!X(Ew_q<^UD+1)=BCZ9)ityVJW#~1yh`q@G`kJ{U=*6h+RI39`E$6(OHxMVUnlT7pv>PG'
    'y2x=vI<EP5xXqaM8{8qVoq1YJ2K+UL`U7(nyY1#(S)5G>YD8Yx>suam{lM^zxgr^YnUfI`Q%OWf-NEvd9N_B;pzg!51ZVDZ`L%'
    'JoecsUBOk3BMQ{yEt5HJt|N<g^nK_K&LL$&brf32clgfNkDFhP^-'
    '??;Al*!9bHf2nEyzi4wjn%ELv2rUbb4YQL+^}!Qnq<iJGi|m7>1ZNRJLZT%g_2y9GIM~B{82`!aYJip`?l!K{{s--9(yE@;N_V'
    '+jF@PwYEK<Yu;^Cpj>F+-3x!O7ezR||cixB2s<7OT!9{WF6fKA`$J)vJ5gMb_cI%?&#e9<yt#$r5r-'
    'nINW_|O~{3)W2|%s$p#^eq_g@~Zp76;uvgPxML>tf?yGj)!+dMX$se<?Zvp==+(xFx@eK)%wB=M<CFlh>}U22c9Ko0e~50_~fR'
    'aN1v!~N=*eEkHgb>_m#OengnZy&ZY%Z<`rKP^PQ#tiv{;EhV!U)Ezr3a)eWXCUH!r1pM@ewn6(4Sgnb{Q3GY7vUAR`kae<IY>Q'
    'L)aD{AN*I_3TilU4^ABUYWKY5ki=WXgah;aMQ;v;683c!(I$(qi@-'
    'pCk*68z6EBXe&UBY)|7)+^7oUi`TVrNyO7_vq>TlHFOBuSo>*aw*|k=FO+5kfTa&EQLzTRg15jRi?3$i&gpwg&TLx8*B2@Q0~_'
    '<!4tQ-Sv#d^){eCPZ*Op0C2kyuV#WgL9&?BU6tdUt3vvr%GJ3f^>m0xCcsNh)PiD<w(?~ObE4bl4aKX!!D3r#-'
    '8Q{e{V^&6QCq&`I2_)bWp`!n@b6kw@i32@!V!r?XJWRavI_^9LTun=z~218e%HNafhEXg^WzO_^Kru2I>UA%!}6oel@4DmX9Dq'
    '=jbf^Yz&nX{Pa2#Dz&CL@FLrLDPI;XwN<JYMniyMqY?nrA69la>RRjB>lKz@u~@XI`fm_?<+bz0>p>DaUuSZ4)9Z7(ve?@diSF'
    '3<O{H<K_C!tK3))D?7KCk6MqO1f$8Q%DN1jhn9|40M)mHoYG?Z^0ikpR9ta>?~}9$<$WENwwz93d1kspLEv{iZ(ga_6C{=YrJ!'
    '{BXs{@OpiTXm9d1oFm7LG7!&FGyjlHa$(f`Yo+EWv>+eH|T9?@<&Uf%i&H^eou??|l7Q|=5mxEQQ{<I=QoRHF<)wYZzZFbD2N<'
    'sqpY;LqU5s?I@`q}{ymwOfp}%ppfu7u9rYWxm0UDU?7*yZwYDJMVriFHs^8l<~qhk;`gp#wn-KRYCO#HyyeDaWTH-gc*B5aVCu'
    'O%r1Bo{Q?$G6NK%Hkbc@m>4^Nma6HLG@v&>ia8y|y?Xq8XWFWgf3jMY`UuLf!j8qjo(TrWvvrvYg+Keq9clsFm^S)!f#ve&gE0'
    'w$UgF|uT5f2NmEh)<d%-0Bq-'
    '(aRH9&39AlDQomyqP*q?2~O`Q}LNdiZLd!D`{Lk$!5fy+Hx7Fd_8C+qgSiaR_4RS%TvCvzI_Gpru+UqN!Dr7Oh-'
    '|v+Wg;tbFi(d0b!yy8wG5AR_A&~2W^bz71AaD9geFdD~`7l!Kg=AZxFj1J9B25>xJXUDJI&Vg_&38WE}i(KS3qHM^TuN{{B60p'
    '*cRM>*Uc2;BfGbHK;T<>J2s9C;OG|D13VvKQC1tp%OB7-'
    'ET{NiVX>D@r9JmpMYekuvOms6gN7ahh*XYz)Hl@GQcmJj6^?4o+;Z5RbU*HK1kQP6C2pwysg&#lDC}9HGNNx1EGGwS+|}g^EIG'
    'TV292#t)EWytSF`lg$Q9D9_71zZjsecTCNA6fw`a}ge~yuV0l0EQZWnBNL<x(VLO2qAR#t>NEb9_9ZZ#~K(@^ue?k}8ZcV3E8B'
    'j>xjR^`2&w|CwWu3ZTtTB6rXUU{SiO>x*hLl~ErwS&k1PRE#n>U#|&N8|0$}C^`GTD1ERE>)lsfpr?S^?`Blvg3va^J+B(J{|t'
    '=d=OyP^raaJrJ2U3j=~O22J0~?QO@|$>A+rRWtG}V8smA8BTYxHu=4)9-'
    'suT>we@smb`#>m%M#^V1$(if06Ir)Pty?^u$#n4i>@B$zXku7N5+AzNQ4nQlljgkup8}*9W+RJs;EI;9!Q|1Gk~~Kzk5Kt?Wr`'
    '`9D(-jF<;(=u=;jTP);(+0v=`zjOsJj0Nw+{C`|aXEb@b6wM`NB!%ziQ?Ip|SzU1?>j8yGU8Ch@n-'
    'WEl(!tpX+hG#MCjHY772@t-tH{8)4&-(37VtzhN?f_FY8~n2y6^2CY5fMbx?e<57t;KKK<VKEr-gR<dhn5dOnd8fHN2>>M=o;9'
    'fv$@xX5$(K=it4MrV=tkN^ySg2|(`~5V|VT%%jF(BoN!4P}#GOl>D~Lx7_srS3T~#|Hepw#&{{Gewp=aNR=&UE5WCfU1SDtpB~'
    'GvN*l0=AfX7Dn`-'
    '_P+iyX}J$ahT{)0~J50ul=Gv;a|Ym?WRhKUzr5Frn3j$<C1UnBo6BSh9!!Y$sH7rU;T%ZLAbbig9l446;Y*KOB<w63<sK@<J1w'
    'tvKsb9gW4?HLEfbJ?vHZ9Rw0U98N-'
    'hbib#vYq@TU)0<muK&gUDWhwqas|T4#jj$;O#WwjR)Oe{1v7?)QHY<cOFd$$#W4==gUl#TEh0ErW;d##D%GF@<-'
    'nhTL}BIb!ay(fAksZ?ki4tiRmb^*@1J7-tx5aYxf!rgCi;TF;fD*z-yH#eo9C-'
    'SxChlgTHsU>_Q^v)CJ~t(NwrD2?fwR_WUaF+bjy$k3O@vlky+H@OXm>#-'
    'R>@}Y%KSSk<zB_CjS7~1Rpr|Hr!VSF>?;vQh@Gqh{8#klKXtJu=HM>VvYr)Sua%Viga~b-zqcysqnVcok8;TsQ-DwD$)OP0L6('
    '>2ckSdXh?RLEU_D2C=1DN9Sc$U|8V-'
    '2hNd%>rUq9CKSCXzfl8*tlN6%sx%i(Z3OYabSM!SimNgSE)p*G6vr_d$k_6ilte4(=&$BbyjL{wsLPBC(%#A7l3&50llo?Kx!<'
    'lRRT6W)L?o*w35zza{4eTt%j_{rQ2$c{v2G!`MrP>)O@9jb}aW6`yA%PZP39{NZpOrYfC?M{z2Zg<efz05{!D7ndhlY~o2h>1A'
    'Ty<;QXCm4(cBd2e`-0T+dH_D;oz|9}W_Lzu7H}SIH+WGJ+G5u$G(t%-fHXa`=GS_BJ>VaU41FMx-'
    '8N}c(p%*G!!nR*xA1qbv%#g&o|(U8AV&mw{G$5vV#UfHyHCn4<b&kA7{?aC0{w<nNm;9y8Q=HqX1w3^?y@T9j>brVaQ8hZDgQl'
    '-dY&s`FT*=s3s-swe5I@qfP8R<;(h8cH_fug4ASLB;+2S}NkT!vN$8!?cRCMuD<-_uA?)Fq8bG3=BTf<-'
    'cR4j?m;d%n<An3j5UQ;He<$;BRk$Xl$!pHl4o=pbI2@K|!C&k{PKudF)(a~fwbPqVrH9hsXv)t5TWvg3EcWkeJAxZ3Qpx0@JB*'
    '6SZ1~HnU@2vtdkaJ`jbzH2XXr@6A^yYGMYDN<1LbBvm09ITQ^3YPr;=aAz6=6<`C+#fJ5H*)WTM~t)T#?MJZ_@v^A*{uz_&BGI'
    'lS#Zy>0=fK8*=9y0d4(BuSajK&MoHG8{E$*Fl1V7k3S!G$!a`9Ni5J_$@PAg)vQ4Hcw#D#d?U>(gt7f9nK;UDj1>zg`c+m!tpi'
    '#7IT%BHg~VFn7||Y%OMjQ2Ypuc_y~fK6B>7^mHK_~V(ZajJYAcj=sr*!(SMaA4eB%yq+F&0#kF)iddSV4$ah4@>CgD>oEplJyc'
    'A8u^wvDeWaymR!@vbM<~U7`z{z@-TxH+3iX9BVK9kYb7Z9+`gHvD15__=XI{vO;F4CAJyl1I)3KNl~{r5UsVT4tM3?U`*eNS-'
    '81{SqaT+)sCc_w%=*B=mZWky)SnMMlriU*;PszeQ58z4)Wx-'
    '7NyV)OV0gB(|?kr1Uy7I=abYNupscxK?faaTg9b4vozqx%4s9=-'
    '5Q`pOCJ$=40jz`oK}=G=!Pg5X&4c9e8Iv;Xx@L=mL_nZn|<DA>AMeFJ2)^raTy79`gU(%|6A&)r0{Q=IG%$YN{BTpuiXLbx-QW'
    '~al2pHxT|xOC(=<8ee*?-bFW=KF9Q6t<3crc)^JiTlyIw$8QoJT=m`@p&5tECh6BCl+^kT5}B<qrs!0{~z%WvYd~Ek1g88o=3-'
    'nQIq(;(={#DNN4d{a;VY*j@u@uV%Qa@p)2R%2M)6}bEAmM#CyaZO3d>CNMyF10<*sj(+|_Z*Ngmm7D%XB{YwUU9fACJqIC-'
    '1i4>{#ba&Nuj#&+SNibn>9TC{w){?`KrpXlzFlep6hrMlv*C+t&pFRfjNcZ&J%L#FDPcB)+KkG=RMGzs~R;_Izn_ARm`MqnK`9'
    'iG$=&LWx6dtx@jrKa1U!Au&yNKYlN3%W~NbBp=T#|tMVaRUD(nO;fDULWyEy5{OW_xK6*WW|D<a)A6FkI2lf*PbbG76-'
    '}&YEV?Y@lG6MkohNr<*b`FR6%dZdxWV*yo#>QclW7X1`CkUi?k&eKQN<#$bgg(MqEdRNssCGI@Y(pWz4Qyt&D7cW|+|r59W7&G'
    'x$Xg4SLPmddqdSjhXCH<D1|7zH0b`6{$HOTzwmc0pBSwWL6sNQg2TcD6Vx<bc-#OWrW%!*{HyA=V<y0AdEqpeFZ*)wy6U9G-'
    '<VI7kS;@khP)q*?oC7xN<3H)ac_W10Fwm4LW_HD;8Q1mgt?vU%?sL;*>kGuCVFGA`P!4mBT#<2{X$EVB>$n<vFJ(K^Kr0gL~Ub'
    'Yk+fH|F2WN%L?s8D>=f&oqZQkNG?|Y;+@8O66h&?mDJSa{n*3hVqG?sYlp-STMr;OEnk8#>IW3r8^3XM1s&=Q8PV*Q-'
    'i++?u(cSA^utflnjPsZV2R59|rrwV}mmFfK1V)e9-'
    'xXQd~DwGiG}xu(~&r5?tHr1{I&_(C%)EA{ex}7_6#gM3C#^K1aADE4%F@(#j<%0}g-'
    'TB>)hkGK^)Mr*JWsQ?q;Me(a5g9q6XSNjwqj#=)Thx>I-R4Vv4yd$8+Tt^2cL=9nD}&OoNXXZI+D<q-)hK*nJNR%kcKP-'
    '}+L>aIG_4Kc~z?2W1EK1HQ<B$m1)yArirN|?}BD$Sh$dc>J_B*?QJ-AtBqd<#@%&of2^z3zC9i3~`cI-Mx{=YxQS7~+o_E(Q#g'
    'jTAMn0@AHkTE4mq#F@iUof<^)BoYaut8S*$JjPc405AW))TvaPY&7s`tIw=_c8k)*k1+4fS&<TBtEPQ?0md}PvNW+-'
    'T?dGibi>^`gFtAqJTjbS)qM*PNGIz0#w4VGXJMmDM@>&E7Ij8%ml~5rEMrq=@%CTJ1T@^tBI#wtfcxqWh;VND05SX*&c_T7N)B'
    'JK@6coCx?<>|UcrNS6|7oYLzQ$Ood2p|QR3x!q9pLt0aGFQ@f+HPIr>RTw!id*xX^O1lCaE(_6=jIC(Q9Tm%%QIA%^}ZgVwq^Q'
    'b!F6#Oqj+WcQU`m6Hlo7(jks1YJri7Dx<uUIT}1yd0}ro2z;o+sJA48sJ^VSfTYpRv^drE2lDUlfJS2AXU|HgVg$-'
    'j(%hwuEfig(#(R-+tgLTh6@#$HQS-2Vb940V^+s0v+8aA0DYiOS9oi5VH^fKT@PnMCKvDb_Bw%omgE-cmHt_3Ne<p!F_5Y4<@t'
    '(I882TEWHeMgL&Gj|0&gb06Bv-A48z^tLeO-;Y$dPM$JCx}<9FnXT&4P>FC<fpN=P-H8HIx`N~Nt-<Li7+I6Ausr{q$@-'
    'm2^REk&XEL7}i5rL0<QKMQ7Bw29BAP`Nhbx7paYRHJl%ErZ?%4|p<|6T{fb$eV?eOOJ}8*VrPeEXOMM=i~!{00BOx!)j;3bBqP'
    ')#~q{;tFU51HCZ3$19Zu^Zn&|^@tl=u=%5y<s1(}xaRj-'
    'o0sSsB<ViD8;V2Dsx27rg0Zd{GZbJj8qdD<u75ewy?bYQz|Epal0&PtirsC)~Tys|{6%la!C}8nW0U4iiA2@u3vi`bjWoC7cNO'
    'X<5TuaHjSS!M>s8r%Lbd+82m<J~lQb0?`Sto@aag8qMR`@Pk?^I5yAPJb*q@ra9W~fylcW-PpOX`J%e{YBqYhq?l2nM~Sk)m_E'
    'Ya#YvZxj<&6XC2X%U~deR-'
    'y$^+AE!<ArGXg_E}>wCR&Z`S5k!}sx<u<JFq`Hj?PIRk9~;U1OlRZn#A9Vf882JztLC6mMlY*`c76=a{uVpxrUpRN9)tom%VRs'
    'fmUiJd*Ai&2a1-UwBFE9Y)^^>y|>xdeGT6O@HX)kNIMr5JfNP^XME2m!xnW6#F*!#4uS(vME9CYG&S%EU<?%0k-'
    'DnKW<QYq_$7*g*9NUY<tySI_V=k?{4y7j_^?4;C_4ShtZF?SFqPP@JUB$STbwc2bEn@IqRP0oEF^Gwa0)Lj;<^a6oiDTxqe8M>'
    'HXoFIV})tMGc_jXaPd0@yxCt-'
    'A~$D^Gg+VG<}Qp{CubQB4OA}M8WKo02dWlR`y^oRq%Ni5b%y`GG#m=Jx9)*w04T;TvenNP^v8pjjwD02fgdL78RZ+pDvX<j37#'
    '5MFn4s4`@h%cuU94K^nosAvB2-'
    't`#Qf`|1wAU(RcJts&Z`c#&xx#ec&_~cP|_fLtK3nAf^O&a}52P+ID$W@dEEyCS3%JbyV&>l%@cjDp9mpM=#<se)SBm$XJQl5P'
    '79RUHrUYXg9-'
    '2wchby4}*G@8V&EZk4HIb%_(y0l9;jeT>TXaJtBmPoul2avP<|;PsD731%TDk!Ta{``{MN(%|fMl2uI9UVIa3h<U}DAP(Myf=h'
    'MszK_T7`&|$=u<KJ<`lgmiVT01q1Ag`Rc=yrD}k-'
    '795ZX{0SFX}$c@o#X24wdiii}JHB3lZJNAOXYxZ|E~qx&j~d+5(*#=cQxpS`*;G#f7ODCR@}RXDJ@j15fH|zDFBD8|PMh@ZUJ2'
    '7;?b{U(X7!1Eme#PjV7w7$YBK+LWZQG11FXEWY+oi(k1*93gjC!qzxD?FxDdOoVafJ&|@bkE9m3QKoa*W}DbL1HfmcAudM6pBL'
    'Ej&k$>h+)713zJDpr1UMp5b-4auxhTJJIXH7S-'
    'Cith!&`1+eB$@<x{}sl6oA0qB2&O>eQ;vG%S2=)`DgP5#Qpl;mqw!m{6A<k9yR`ztw(L#?0SHc_9n51O+JXGqEm8PAHl8?Q3M$'
    'lqXM&Atj^hOA71pPI}&AWMif!;?0tejT{(2E^iSC-w@+SjgQ!gZn-HO6&j{#`^$Da}$&&;RO6{Q?m2a6SvG)#Mzt{D%2b0K;qN'
    '$7l<R4D!<34eL&Ru_F92PS&HftKf^y2|79h&11Eyp_jBHv|AV)Q@NNQh4YmO6+LH_csFeFR|B>x{7aAYku30TML)aadsnXr2+A'
    'Nb=+uX}h3V+efYIZhQ91qHJA??~q!8Oc)z^c%?Fb`?005#K90J8U@IG*_jGO6gU=C>9v&Zd;&BLD$bF<&a-'
    'vif=JKdUfOEl)RUhF&dwL4;V|!3X)C}DfY2DF+Hh%NEN^=CzKblxwV;#a+Y0E|j?R%3v6amvDO@C>t$bt(=yvlyDZ`pzXdSW?<'
    'L|Eaa=>WPq9CY>`fwlijRr<K#L0p+!;A<+J^V9feR1N3dG7!uzu`^xHo1hcIXc8T+v2sA0ba8u)C0Tiq*(uCveBDTRlANDyUJ0'
    'kGMfI#O;y~#YI+3F%d=l9y`|c0a0OZIa9qV}3Ds9sM5(M_YnU!82|Iw7G3gsQ&6|}%Cg!N*A$rzQ;Eb7*J{6uvMkdp!Vl9_?a0'
    '3;`c?nWOT#s9f@7&CDP8^>8W*P}3PPD$ym`c2^{9X`lQ#c3dy=0^F<&Gkxv7gNLy9o5t?!Y1E!qb**tCN^mLs78922$)Q{$_Qc'
    'h-PujX8@Yn;@6qjGSVeF%72$IW(i`E-'
    '#RV2E9<XaQxB^Gh&rl=<6PRZi2<{94m`I{YS($&TUpm5XK_MU69Dg6axcrl>xUE0n^&6KdR!g$&p^I(0KColZ{;0QTC3PybziI'
    'wCb<Oe@={+I#p}$UrpJ)mC&VbB-'
    'n1Y5+qa2z)Q1$97Ok+}L9BMU{?M$MQ=i7VH%@$k6cDdCrtClHq1a1=gM2<76eUY~2b6}M6n+^~Y}b@Jq!XuFrqx}l(;%NHSD;D'
    '68(M$(A_ME=(La@3Uw2$F7P$Y>$*^e2-'
    'D<9W{7C2YKAa!c7+C@OXV$Pu{IeZ78WvQtQA+PPQ!ivX&Xx&~YW4}_ee8T3Hb#&4zq;y_NS#UNj?M&UGuLeCLHGmJ1xKTwl);9'
    '3-'
    '*u%Cg($vBtS*iwLS+zJUBP$tE$4KXa7q@0rC$l7A*L%!*9P<v23N8GZReWbE#iaq9$?1?rRhe=E8AhijXygmxLux{lEP5Hq&Os'
    'P-JcIwcx_bvgKeaM;qfk|TCq^*=rORA86yT~8M1|Xl>76X%3$_9?)mACKi?Wen4BLVp|4f|8yj&7`~;Mtkw-mZ2%4DW|86U)53'
    'Qk8aMWORCV6jsb#o8bStJO>RT{mo*dUSUCJ)aD%N5JV3C66tzF15H6#nY@55BSj0m(i2$t(8oT4-'
    'B*kO#HaBT)sl9nYmi_pB5#M?}C&k|H3!q@BBs&?qsqte)lr<tODa4s;}&sjbL{z6F9vpYQb8$-'
    'u*cJQaG}D`pyNN<8&NhK8W!`_!JXZsxEW3eg`agLUY|T@0t`)IQw5|AFal9Hk|{J(JBMh8J`KLh|eaQJ?>LsY4K3k@4J$(8;~)'
    '!6HyYv@Ck0FVaZKQFK`$c|oT6sv@_Qbi~|^6i$e^79Bc5${V^)66L@KEk$g!tMv>EDx&W`Yt)z6ejRwZ$o3H2&>^@RQLkt5x_Y'
    'UztkwX~BUW)IoSK$Qh%r<7<IOYQ@(pQr@ZA+0Zn<yKB8XCIL6Dfv?@f}m;NI-T_qH0!4P9^8E*g@yhX~!&*#Dy65=!MZCme18('
    '!31#b^?+h4J;&w;hE`L`Do~9t#NAa7yZo$jBPC9)!rpQ;JLFLia#+?W1*-oy=G1DyO^>AE5^Hr))dM^^{Q+e9A69$@2-'
    'uqG$s~!f7NF|GPrgPt<=7u9apXC6_FPKuoYJT3*j`^eS)on<>KYAeH(_gLk6r;uSzb;1#+-{ddw&tMHcR`G-'
    's#AF~PYm451eODULhQa^2|FnXp&x*iyzuA1>*9P07gt92F23;i(CyM|@;OQXZFqD|lmnGcF6vXvsX>h`?nvO)x1~PnGO4t|d=g'
    'u)MD|qUu|GNs*1*>&^Rc7lOC$)>5dOTb)4$2ULWnTmN$3Fa5ij@lp;gu+Y@lFPRd@Re$!#iU2MeGgD^7RxY0eV+j{^?V#mXlnc'
    'JI7)%GypzvWYxoF%dh?Zgz3&yTLkRT}t&5ga~9Al>Ie9t|O_5tBVo9xpGmIKA4!ER)trSLgPf?UKPjEk2NVcHICjZz0k1{E12K'
    'HSB@UyGCr-FT&=d`df5HqPy~{&<_{&uD)81DNKK9s7~503#;YbNQ$cOufq;Y5Cb^C3p*tatHD~;mNB-ndCYcv<}Hf8otX=Yd?R'
    '9L0nvs3qVA12CFQXLiyuZBp{2kqbp9}xX`wvC<kI&fBtgE51q`W$VMj7SqY>e)DQoD*-'
    'aBXgQTjBg{d$&lup*zp=m##Tz=U!D&{Vk0GCUln?@Im^H)QA;y5!cHu-4d&!^rp-u4-'
    '?LJX)gZ{vMLGNg8CD3gV+cnAb<j}jK*65wmO82^t7D~5f)trMO4@1ZWevstB-'
    'ZftCOWqj%CchF}QF?QqVkrT+N=VA@?`sM`zA*1Ok&G7w+w0vSS6L%Y!(j(Z|y%;)GJ2^~3mILT}yyk-hrW=@+hoqrb8%F7M2;2'
    'Yh6y{)NY;?O8XpjfAE*NP4SqMD5-DJ<DKM?xCvUs25-'
    'z@ibwXjzZ5*)rQ_?T*N8pD~Mi}4bht`=57no7@v7KCwkxeqqk{XCUWf>UkKuh!8(&C#&u1c<x-'
    '4moO3{%etNs$<uFUqS^*E8pZL`mtOaYQ>-d!R#{6CFKfA-c>696uhf~h26z+X|?%9<|eRWdOJ)PNLs-'
    'Zt55^TSJj`IGf>}!C8nuvL?3(x!PK?DDmC*P;LaK*pI-INdy{}o_B;>FoqnpnC@g4eQGezXzu~kW;b;XIC{Tjj&!k%umO-'
    'IFZ7chc%>(3(igVcuq{+W|37_VvU`Ajra!x*+s7B9oLZqC2VBf;{xcvuV>pLP%<w$i~xgp-z_IRNf&VXju<BiNv41Iqd{4cOF!'
    'URXYlkfx@&*74$GUCl{qX(TgHwUoEoCS5jHrujVk})F8zc2j0fL&5H9XMTAz>OGwl;LqMkwc(8myz-'
    'um>lh4EkV=(rSig}5|741n-rPq&wI-oglhOdzn?)IfBKPfZD0IGAApL)I>=$%Y5)0oV^UO2?OFvz-nza+<l?O`4&Vn&ML^*6+('
    'UDz4$Y`($tM#7Gmz4lcdhQ%_P!pLx=Q6bA@ohfRYfH6!n;^+27&jDgf3a7pRQr@j4J!GW%K7h=1!@d{=0NKFgX;~UuEXhNd@N}'
    'x;{%z3*bE{$SDq<K|yI7R1_>mfYnX8?oLSpP@T2N55smxyfy?mqqvClGr(n)Q$GgUcc+&eo<zZ%Gz0QobwSY9Jo|q)0%SSaGcG'
    '@mO`p5kc&4J&sh1s6@Wpr8jKSlv!XBjX&|;VQMlyY^v0J=h{FFBY{gX|*5-'
    'EA?!W#PZQ;%EE@C!2kXqFel;YLITs25mW>IDa0^p<hqo<Nx$m;WNKJl``Fdulst&UGn8vS|en`2$Mn%BDq+@3SSk%K2$Vr8vZV'
    'fNVr^B&M`r6p()7i-'
    'X|iq!+XWN}$T}HM=0IY6y{$GMCn8{k{8R5n3E$O=s3Sl(7;FaeE`|2<rpsOSR8@UL4MVF>L8H`CRy=ia7!85ogv7nTXT1)D%wW'
    'zDHFnHi1z<9%6eUbn3VDO%VUJ{9!h(r_pcLG4A|*J7YuJrr&7=x7?V)>W4>s?W$CvG*oQklj;48Uu@apGmw)*8aieFB!iap7E*'
    'O0LJ}k|v|C}RESjzits+55!a;TVhZDdrHwgTUm$NtoU7|E<l|8G4szwOtIg+qhG!CEiDudLzpiDEJg`y2k3}b>(;qR24fNC*$4'
    'X<$*&1+n#czfZ@CYaQhr00#2BkJ}nP=y;guRj1(DTj;<Ivb~YJ#>}uwFjdii<qc_pvs*058!O#(WnzVPD*~U+tp9Bpp{P#$L4('
    '!W4?=~XbB0HwKEK3sGLJ*7}oiN3WHzr(b9=MCtyZP<NQ1pQ;)b&by%t`WHp0Z4#xcC1#2@N3>Y|ddtG4TngJPW`Np_3{5XgKpI'
    '5wYgBi2gp#8A$gBoBDp{6Vsux8vl4Ls#GN{y5k(P-<pnq@S)Gr}BQtR#VmNh2lOJ|ezqEOk@UT4obN;B4m6o$Zd5-'
    '$o*`s*`#zIrQS};TB&Ji?Bj&Iv&w_6|G<Xefj4on!2?sBlOY0`}xmL#y}&Q`Ptn^cld9LRW$f4gCvgWui?vb;f1#PiG`?ubhCE'
    '*##404gM6p&mgV$tC0zc!Au8#obN`g{`R1f2<@m&l7x0iD64_GxZS*IgM{2a3a8Tl8w0ksgxJTkDM~{L0?QpgLVKrPyGwLr$^w'
    '3K>V9P>ynfR+KLBU&~mm%D7%9-'
    'wb`z31n!xg?0HIZLo*6TknPv=1YvaZM7aq>XhYVMiBbXRw+K7^mgCK=<h9qIB>Ch`z|XlB}zE<!!v<SLJ=pIQ;hEBoB8iC*kB!'
    'z=ij$NHRN(cAXg2cp$JPqfGq?sZ0b_Zaoj%GPz!a8XdjQOpbkBWNGTdGt9F*9i4??Vj!}^W)7~6Rtef2LmfN^%&GoQ(ZUgbEp4'
    '6c#!E^+VpGZh0zs&*_ki@|LvCv-uW_)-D-Prc3tx+Rn{Zmz&(W_m2FWfC8+#1f$9FLg)|nVb>W~=pK5FYgNUSl?&dbV2HV}b6-'
    '#K1pt1Fa<1#JyK)VTsV-'
    '@*8r+V~EJ<OrLa|?Z_Ks<r6dL}g%B6C3L;ipJO!XN&U#0WCEWeVuogJ9;;lkYsM*f@CXu8}r<h8Y1`O1EqtNVoQ%GXv1F0~UF5'
    '+YxK_ROg0}(HHL^F&vu8>0ISMVj8S|w%6G+{2E8|N7q;g!(Y3E{$}$}xn$d!1{(}I(i{*U_!ZRW;P7?%-'
    'c`Q`V!1noh;2NvmHM;E@!3u4d(60;FEj;uURc)2luSv>D@;mGOfied8RO-yNWUHW-jdZ}#`WpX0xaP!yQ6rOcW_i9r7UiEXIr$'
    '_s`X~qmd|7oJs|K*dIYvSalkt24I$C4)(9BKZZgu8cd^h;n`yN=#K`zR7F%}3=FHG8L4RR@U8T}AuSWgl4@f@i9B&o|*ic^rxY'
    'a7@e$J$46VY&gN4a|ru(Ln*0P8Wy*gGSpnvy&7o{H;M27YCykquAju4kcA(=upON{QW6Q+WAEHV1;&i{ZEb7`2IPSU1zSP*Lr-'
    'kEmrKb&q1!qzGr;&PMO-'
    '5n1*RV<yao;0K^*(dG$KHFWj&us<_)8d<?QXd1HZpTY^VEZG>|e#skHnmY^9t6?z#9OnphaGFjc8$C_E@ku0pYRMo2>wVd5ceR'
    '$Wb;Zr8PYXr-ewMtCH)B&<)(Z?`C9~uIyERaPdu8Frix`8qYqoSXtggRiDjhG?=kl6=uNA?zq*^q%-'
    'qX=Mn2aV&l4XF6KsCOf5<Ts~3YA_*+TzOo?bK+N))d0Uk}f4s%!{ey4<{tUa(mm$0Q|H9FersW+yd&U|2-'
    '+6+s)dT*Q`YdsYhJcSDj+#hn*(APheI}FR16AWj^b;>c7$D7C~gJ`yUNwe^M=&s!Tq_dV3Bbe%Wb(3r?S!^DIbk+%8(CPT7<X;'
    'Q0`{7^l=syIeAqPN6ua>8>#lYTJ9ii@@hdWig0<^lE5bUTtHU4kY-L>TJjl7YXm!mYp?WKP-UtMhYl$C_t=lw<4t#9VQo2%un+'
    'J&#3sL@&*)a!9HEO_3s`2n$-'
    '#HMXzh~WQBl<`=2yd!JT7qY<3|Ubic#no2@;m_lpCLJygrhS+j&uK(s^qKclt}4MvkijZqt&O~3X?o^Es@nHb*al8c@Nt$H@kD'
    '+lxtLy+C(ZpoU}4tXo_JSVHiI1mbhDp7-Q{J(mO0dkS5P<1Q6sO6&&SKKrc!rweZ6)J#GK?gI&j-'
    '`w!)WK{r=qA^rF@_C}kF=VIE9%n_Dpo0={yqmKA>_{kDwq@X`d1y6CV%O<!Cp;ScDj-'
    'm)IC7sG;t1J&h}sw)OXbFmyt&d_Hh7!1(uia`GDof($W@L1cGehwv)&9XTIJU=UFyL7A+Og;Uk!bKx^(r@|w3W<ezE=N5ALHdj'
    'NMd?;H7*8M?HiRud&x+=h+K^e9}yQOO?G0J&JUXhVt}8-Dq4!Y32u<$vcZVr^svKHl=6QiqTi{HvUnBNf+nVNG$~0_#_M-Cj)8'
    'G4CZwULGhsD_aVsddgS)OP(^+#n{&*w6;g)@0&^znv4&2BP1o9^v&G`BT;nj3-'
    '~Q)ZBm;b*btxNtpPNZbWxbYn;)W_l96Tab?0-e2Y@DPDdjDJ@O^H60rf>*;D-'
    'n?MI`1EM$XpI*9>11_!NsIM5QP?PzR4dx%fS34m2wO49(y1oBIc<twS#%IM?uP^Duy(LiO8OwXg|$$VuSJRum1Zeq!Yx2-'
    'q)Ved}(cv{9om!afPeN^?NT?1i8%ivq3bW!pxa+F4?DX-'
    '>GEZ4IPz`Fj^f>{uVjLkl%1n9e<Kk!1Pv_ns|uEyrFKfO~R4mq#Sf+`pzjplB*ZFd|)?0Epu8q_%d~(l6XxNzK6Ol+cpv|8}iL'
    'j}tor!yMrzq)H9c>|YQxnzwwavAlcil^)bna1%?`JWmp6JIT^+PASnga6mtLYFa)aif?XSBZ9twR&cS?Da~&><rg0*g_&|z&0w'
    ';!sWzl!YgG1OT%Q`fl-5yynqPv%Hyah-'
    'x5&0bx3Qap8Nr6!fMC)6>l+VldJq*_TX>^8Ly%Q<ylcX={Tfr6!N>J@V@i;MRAV2j2(vY)o1{ddvckxv!pkBtpa9+5&6N}gFPS'
    'ao<V$&4EjtnaJYqkqW|PC*no4a`<uq9N3_lht^Q>o><+P0Sb_I8%Z)VkTD$JdElZKhGygBbpF8$%MwE5GsLdZG7{cL0m)%xz(E'
    'feW&aF!ok&&63*sm%%+Pr7oBV{B&l@yxfZH4p+o7<+4Nz#Jaq7^J+m)tOFWO1g{yUh;&>D(#3J<rx;mKWd3FKmtQqL!V-'
    '#loV?-hbl<bU_l<PZE$^i2!NF^^|BjZWH|-'
    '&!+?uX3nLA8o}juv4ZnS<ZfOYLWBonrKQy!VvtC=#*cwNKet?it0R==UUM`8D8~yWq|62_`+$DTlS8}EPFrxqwR;J;lqhA7`En'
    'c-'
    '|WPwf;e~@Wu4JN$bt%iHVt?tB~_R{(o3Azm97e>mAOsJPUfqrO2?(IU)hq1$>{2O;x8Tm<xbBBjk7coug1z8NQcUmL{_SR~5DY'
    'uNilmlQ+u$s*QZqY`YYJpm&SHWX$urMR!`7W}BxrNRA!w6Y%jWw`ldmhy`v>g<@7PQm_q^l=YryHbf{0P`^`F#i^z>A4E9?nk3'
    'ZGVJ5s+3_FqX_hG@29n5`a7fTIII2U1FuF?!#%|F29$q@;2^pHt2V1c>Ot3-Kl7}E_^Jfmg-Q62XK4vp?o1J@-'
    'o8HWPI@TDHk<}d?nS*tqInEl&$O!}Rgfpi5DVM6T{Cy)QaAvKCwPh;x0_Osv66v#A@7aC=+<g0daOIpHapxsC{AJ#5ED~hYSRY'
    'mpP?(HgKqVVVA8Dl=uyW2$bfms?kavsK7TA5!9~pZjrD}!M~iIihQSk@PlZ_R6HV9#nBBT-H{ro3YbUhM9n-'
    'JPX(}m%t`Jt5SF2aZ^E&Lo0c=JsuhzfVxM?$`M#2*FGarc!0A{AYj0cJX2PKiLmI27g)%CXzq5po&VXGd6<RJUqWdUn_Ls9lj<'
    '4*)ERyE)N)d7Q|O`*ln;9AN%K#ZCidS&Z}&zM(Tc;A`!ESe$X78L~(<rP&Sd-'
    '?tJX%Y*+d_b{tk)Z+>%6x~Gsl%l0cFJ0UUjJ(fHUnd^Xi93>IB*lk`!8lDD?$Y`zhr@;(~voRw@{nn>#g4N$>hVa*9YrIOpC>D'
    '<iP_hAQpGxdN*$c>JMI2$;DHd-;PxMfnK+fqInVtA0=)fMEq*`bF=9m{zObEPn-'
    'f+{QKBMFu2&KNX2_r4~_j1Z9XzOFu6F}IFWcpkHEvQ@>NgZH!U?bZyCGQ_4<Wb$>#n0TU}?;TNYqtpE-'
    '6O^p>yV*5N6!n~t{f1m0)K<AAS^ZG=Dp#&auB+a|*f>A0+PMj@7bexUiysh16Ni+yM*k=;z;+zfR#UU8BhLZqwf)atGCRuTx6^'
    'OfCqs;MhY_ZtsNT=asU6fSo4$G{8Xn|BzM5JZOzR1*swr-'
    'Z4cYGxwgELVjv2*VJppml2;uf%V?t<%^{A5qgHCVO8t8hOufltJmG<J2GtR*N<3X*?;BuxPMjsJWb7jiCiyzet`fl{(A%y$kA4'
    'Bu#TU=ax+dOMD#TdWUsg9&-'
    'Hgqx>uNpDVD}kCOZi#*e;OJrcx58R=(GzA5vaKpY9Sqc0>VdFH4Ge<d}0Rvm~@qU713%WN<V!PvtGJ;Tu4f>t3)KgwGP#X8mYD'
    'V{>V&d5-{MmryXP-N2L#Lc+xc}*pr>3px+%Be6|>^)_)^3YXUxeCakiGVaQ0pU`tTUt1-'
    'ZSaxDarE7rsF5rGQ>ztouSlROEi%y&N%n#U-l1|ve*f@v4bzv>PqLf8EoXbAASH-kJ@zn(I0u?7P`3|W)M+<-'
    '*fqF_ECOWVORL#;hEV-'
    '}P4Cn=vAtzw!GC=n`!&p@R7mQ!s~LmSTAeLYm7V>jEb&gY7nn5OJ_pyme%q*1cp2<>oa2it9sO1FlPeHkFfh9o&7GT%2PTbX9L'
    '-yRY+WHn`D*igRK_an=4pmK&Kw>(Ud>BIE>hLQ&`fw5Jj&B6S1$=_@b9A^4gwOQ@SP)8TP=sshHi9ciYB_5goH#%pUdqtCkn-'
    '^M7!Y(!3tA8>chj<&St?*I&jMP@^W&S0KYz`Ri*~S>6$hT0MEpw#<nX(6v+p>?7f36%C)*v*1OK;{*lk1n^89DP8l=G_^Ulfxs'
    'zN|Qp=!ovn;i>j~^gWZcEII3h^{bFRN$L?}aZx^L{omWnLG5xTjC`vJe=UU=fYcV06XV@3UGrrZB;#_+!(1FB$h&i<SHeoiS7O'
    '@VQi2$j(;KMOn*fW;N6zY5-'
    '(zpf!UApgxCS16?w^kIvjo;l|M>xrR@P)FYP6o&L6>!<vuedE|LLDN9a20Vl&EOQX13UXIIHYa|JXa_44WNsxQ2;qEL%RHVgjj'
    'D&i>c#*W6%RlkmFfrbYNp+o}^Xy{=J4Lqb<s_gnF)x>tFq0DYQLg_~h~mAcxtQv)2h8j{BxwAFo&a({|76H$*o^>zGlo3cPlpb'
    'h{84|k_spc_z0S{#1pPYKLMm)08}F!qENJV{%fjwM)E;2g;pg#)8dIJ=-'
    'QF%Lw+h&9O#;KDNIo!9k^V5ZtD7VqSL#ZH?W2d>KJP-@C#vAlC(0?fuM9#!ns~0QNvCA<gx%d_G;H2T#nnHlq$N2#S{9{#z#-'
    '^g2EiCx0AXkedCl`%&1cX|c{KURgRrT~MdxEtPu^^FDQqyK=aJ({gdw2vZoIRx`L~hly@Z#8XygxK&E=sA?&&-'
    '+2_TSPQhJJfIGUU!7LveLHH+>sD#3e$tOo!cZ2GwtUnaRWT%)TZWy!mQYx%BWqu_7F-'
    '4w*gc&;bmMVRHSv1zu9v5x(JMFnBP>wd+M+V)`dke&nL6t8m-'
    'JAPc}>?(?I5JjANBF9lSaNq6cSwb1nXDuC107yJ!GIaVDrV?;rnMHAA<7GaOO$2*KEUfoo6EIW5i^rbvy=bFm3X(?<>;Po0luG'
    'JzDUFqMMQgi)n_`1yYDnU37oC~aDJ=(R@KQh=(eB}Y=v3CM5MHDae7RZeOQj5rHJgEt6DHriYCUt|tq!dYj2D3ehTTH$G)LcT%'
    '!xWuKUD2lr>Q3=MHXNQDSt|oy^hg6NFsfcl1d@GMKeiXQ^n&YW3XRhV+j_-Jn4Ea&TOUQlx@IYw8+t6WYM9cQnr&_{M7%qy4(K'
    'u`lbOjKp6(2SH}vFQzdP}^xp=M@W#$?R%WVCg*X$abp_x4_&?jmI(H5&<~yzd?KGa*Y4>TozC`SJPLI{x`X5Sg2j6*snR-PGQ2'
    'DYrEwCwQP8!I`T4@9_rdi0vNPttE^ghjVRG@Xet{*}?dM_<?qO<Ct<ghlWAC`^|w+qZi@7X;0lOb?~)H`#5ljW}Z9v&$30=a;?'
    'UWZ!<FILUg0O<mXG86bfvkyve0;~#dvJLYgx&u^g*>kIN6yA$9Bz)m74;z;VRP#)c&{uU^*2+Duo90qODXmjl1i`LL_CZ+%R6f'
    'OzSr`<b)Pj9LI>w<3)AaM-#Po>MB>Lm|W*NZZAgqtj>-X)TgW6rT-'
    'f>nUpxiJ719z>jA_p8%$i&oSb`tY%W(39*r3)jMa;*3O27VCZKe%;rD|lSV5B{K|$2I$H+$SIIcdE~SRYT!2nBMF0drY~6^puT'
    'b(}xn2T@WbNuLB-_wTp%pxS=O+KM{5E3=YPLQrC+m<V~Y<d;rw_-Ml$q*R+5-'
    'y4=xspGXSAFd{t?%`}A7js(ubn%jH>Xj23+`Og0V#~+pH@HQaF&f_ix8YKqkthsp{8fSszt34)}lGUIh6cUvmrQi)q!*v%PW1w'
    '<1TvUoOjA8e1bJGMn|69p5PfR{kpnEXxGMnI*caoXn-'
    'K(*tGW0Ij9A9&_uJv^HK^&Yej(c&6X=IpVphjc3y*>N0;q(2|KIy7d7i5qj{O=YT2}?x-'
    'j#jInlXIue#}atVmdo}Tcr?HS+amX)<y+di`r7yjd#GJgW#9c1_0n|`?%u0Z*(rJrTYc!sHf&`n2FPtd=r{t7C1xn~e^alWwYY'
    '%cHN-rat$yF7t@?dAITQf0m+*z1m(m*(aL~o{Sb$z7GVAiDtUQ$zl!AYDl8b*+G%-'
    '7zY^@VSMt|wnx*NjgF4xdCn5?T8;9fZgiD|!r^o$|R7S4O#o!acZep4m7ekmO^($Z9tNdV?Ld~u|^14twueoCnZg*Vgq63#=`&'
    '<CGf{<QK^FtkUuWcs-nG32wPrG{iy5W!!Amn09I&mkd%*7orQ6B0*HqRkwjwHleM8*skNn{q=O)@PY^j8eUR6f*CsrAyuP`J!@'
    'A$7~7}v>+S{4C9b1)rJzfO06WF+{)QPfSa7Hgwu<g_flKn<9=a8=WXr3bqhU=z_l9Q+T8jv!n}7fbv*|;&8WI!lxOe!dVf!s`r'
    '-mDSyclsqzEiEEs&nF^*Pkd6j|09O(@?Cfgeb<g9^z#OTWCbi7nm*6u*gc(-huuN03aYRT-(XWAo=-'
    ')4yG(m@P2nKdzThqmy8v>e_2{n$G)v3D2nS0ov)Ro{?rDG^};Gtg$NN?DuO34bq|Yb8DAspH*L*!Sa*sW}OkSP}D1!ZnYSUO?5'
    '=022)EDPYcT_kZNZn!4JJf@tu^emyqOx9?+#piwO<U{_EIFG?dgD0M>;4wB!4BEUHxe5C-'
    '$<TINi<rPjw5QRC*TE&~XV99BTI38}?WZ9ug3$`prx!&#I;wkrIhF=L#@*;V*vtdJRm`j=-'
    '^J6}sm?4{&0)Y!`?m0kp~o@$tw>x0xGZs^4Ai&J`?!%Xt~Oa8X6Fl*Tbdy8+>@{-'
    'Tf@MA3IpN%0th?#99aphUEFqHV7B^(9&Uyg4L3w?$U<3cZu#QDKgIKq`in>5!SD)*F3Js84TctIn3Wj9X$-%(;K^XU0l$N-'
    'F=2Z)58X@uxssj3!4<#3O;9N;HHNJmGGEo3-'
    '>9FP0vVSS4mSnwJRL{R)FHrAwioo|&_Z_8#ozH`Z7|AltSCC0RcWFp5;Lw=_n(S2~9o-'
    'VoBAO>(ZB#9E`7>oA+MV8kK%88Oi3rcBSgob<6lt>X^#UrGw1f{kfPbP%`s}ooUtNt7{l}P?fH{C|$&wN?z>0GK;y-'
    '#GwD<DSqhfiDGz5uPp#!<u$TVh(^ks@rBrn@tpe-7%$!KK9tz*Z92jIHLT4D6_?;P<}J2p1pU@R4ud-THO9e5$g`ljTA>6W~&Y'
    'MnnE{EhS7wUSBd+49>w{C2l~c^hk%0TEjtkgJZPi!4~?M8Fv)4jyjfZh%<Y#pXPvZToFfgmg^k<#5Bx?WG1DkweRZd=cSfVHxD'
    'uJ*Jmde(LQ`^hs`{TI&0kkaLog60u(38p1<fz51@DP%?bl1y6(#BYJaSc*-'
    '9}?xdId~C6@(!PIU2Cm4PbHwz`g{h{^+4Olpcl@#{thaD3ooAS@S#ck2aqhG14GW#NNJzCX;73D!Wzo)L)DD3b)_f8{2u;Dm`W'
    '=xNK5Yt2#QT0_5%8sc9OL_{nIN|JFC@)H*r;9z6h<!|lr`XF^QarF$rE9!Tl>8g(XXcu{>;CXitjEznwK!C=X=VthMtNAKgOaY'
    'yCKH@42Qnd}1Ea&bsYV#0NxC8K}KM@zU-VMp!i)woH4*<lUX)sUtpQI2Hno}-'
    'K(j^D6)zg@T9C3jdh!V;!@PjBIfNZFS8UHLnMgibA=YO7w8$<|}a|X6T$`Ju@_^_F4JV>#SXDqF<vjPoZb7YPjWWD~2w)>3`2b'
    '`jPhmIZ<K&o9;{oSd#Z&Ixm<NXF!QiF7M(@Sa{vQr9_i6bf`=L7etdG6to{^iJ!ZK^-'
    'PUoOutL<Rd4TFqe<DRXV&Y!<((`Ym%l?Wg#i<&NhLGJNV$&s$oVB3PIBPAgSBf)q1J5izQ|DyWV}nZI~VJ<MCO?@k|NuHWnJ*M'
    '=~a8kr*qoNaz1HL;A7RMP*k%=g-JEEB=TP<G+cEWx+5xYc>r@Te)bnv+d){PD%8wAZ5>N8k3Yp0i!3>RjhKzuHrkWD)kvrh`-'
    'q8`CZ2_NKC@J@iM=l#Dq#s&Z$KHlt7?wt%*SH+p_YFWnkN*6jS6G@n6`DHN74_isDyh_j@73!T)wz4am`R274VRUL2}+<HmUDS'
    'mIg3+JQ}Y%cT1d%LaP+CdF`{v%_g8AuE#*w@THhxVGxP{9U)>P_eW%a;Y3;Ar-nYi&WX$)k*jCpyVomdzeEx-'
    '>sl*8NSPbMIB{$j>?N$wUm{szjoqD^~fu%;V`Dx@oZP0a2H3(aEu6<;r_oR07TXMV;o$*&_e!=Cw}|k_e~=W-'
    'tPo!OvQ5{SeQ!sqJcVBJnHny2kxpWGK2Bxmbl6h@3VZRYBcP>*kTWL7=m~F40r)ATNH~Tr0YXHnM|(SzwOH@k*Jky%Zbco@c%)'
    '&{jOGDzE^;yGK&@AZw@ynGJc2e2n=Ha@Z=AN2%9z&nlE%SXyGeLYrQ4Fxh_Rz|$Ti>}|pGPPk<*UzQq{dYlEqB_7pS5TDvjEcO'
    '|h>tEM8^C~Wd??y^htjdayS)e{Qe3Tw3V6+!tocgSlIsOw8Pv7MtDqoCWH>7_>MFZv*7<fF*`aK+L83--W6pttUnphEzkvF#yi'
    '=d^t><KGEKhDXNNCZNJamtR#nJL0-$e+3-'
    'zZVr13f{YZLTW*<?XvC{nn`$mX!2jBp1k9D*@Z2~=jnl>qO_oFa|!b?^KW|wqm)vo)z!BkW)B8humi285s=dUz!F*TuMHhAsV^'
    '9xRgYD*f+~PrHJBndMHoCcO#4?NjzU}N5jqOqp6rDPehg3AV=Em|gyTfKNcptPV5}XyJguRZB_jTXT+i>wO=y3{at^MGJ>gjAx'
    'M1$Cetrg7w8k*Z`-'
    'B_wk{|t`HhOyG<=xNO_**2HQX&ly?W}WImCYVsF{W6hZ%@3knpR8fejl<?b>)NrT+>KDIKC&%RcJ%GB0yvSgA+48z8hm59gP^_'
    'C2qZ7aF!gv`}|uce@hC$-v2LqvVpWo8VSUU>yq>wym^piA~;lKUB6`WHdn4fvjGygWbKQBl2G*n7JPsKpUEbC@3^-Nu-'
    'K)HVSs53fzLKn`{|$m=DtaeUB3fWdFhSFzM192@!VenpLl?J1pw6QH#&Yzv4|WI$9*A~Q3Wpwk&;VVt`eIdiQBJ4Ag?=#ppHP2'
    'ZDxcFcQl}Rf}|sfrZMdX@3xB!tCwO6ulZ{SMX$IWc(Z*oIFNTahOCKx@HK);Sa>9czDdw2RKRFx^|P#C*G>S;i;lRTmr56_<&~'
    'L`vd)(@kwGh#9oF4$d7p6=xp*SIMMLdeSxW_qa{2Sy<e|8t6c=0-'
    'rFINwwvHx%$bdfLe4vvxxba5($<S^nD#qf_&c3s40BY~q6T7e)X^+A;jU{2#NK0@1RCVtQ4UPme3TnNdeYdiJ<#7w&$7K$|G0h'
    'r`mvMIjk^6{;cS_4Srktp?3TZB(98*I$A-Eno;&xaap$@z0bBs<tqY4ck-06VUTK?{(Hn~vDC`??bzYZ5-'
    'gt6#OE_fUZS6=$V$etC>NOA31Qu%n#mIs9Ux*)^kjD1g1OxHb)t1rLqanCmD(G2aeQ<S25Gz~^Z*8%Ze0faeUIN?L>vgmI@Lz7'
    'Me&0KP?`Q@Y}_p(#sHZ$xBZT&sP9FJD*dwN8-2^bWY2}=FlFFFdhtBkQ0WRI_2oOsxqFjKK}$l#R0LTAu%UZ_WdRknj~-'
    'tcWUwH;Jzw5`3psuE9NnC^NrVhL8?Sm`hehfrNI>4J7-'
    'D*0Af4b$YNz%cnj#j^XU?Nl4Xr4?3^cX(2Ph4=w5WVSG`=WwrqC#bfYxz-'
    '^tr{}FWCJbuMS$*yKF0Z}s|4wyBR+3taggRQAo$`Py;%{S|+08mfzudSgPky77P(zwj7ql@KhN)V`rbR<Eg<7!l3;$o*Rak71s'
    'jO^b$arcePU!Yer^b#lJ^OEGqx^}#XP3cTHVU6FG~rVfhc~r=|7J<9e&!l{-yF~-'
    '@}QQ)*;j&2e_bfhRLjchs!#BZ&tQI|m0wv#9-IvkMd@MNP+RKWA8Q1-'
    'VAf0&s6#^~IsLprrkn_F>@Tp%D+t*zaniGkUJyakRL`rxu?)^-TV--7kvU-oGi3D0$TqW420`>}Ic6Urd~A|wz1j98mWD!?^0N'
    'cqG)w!RodtjtqXSQxHZF^vdA`RF6Y#Tll|V*Tai^x9DZsJ{Y*XX+3BH`s<P})FS++k>j-'
    'Q?LReym&16%mbuWf@;SjSAE&byUP^t^}RU>}%CIMGgR$(B<zI3iTmexi>e8UKP>bGRY!lgwiE-'
    '4MBX*}Ri1{nJEH0OY@DRN5f>sE`Me``&ia!j^Nh7FxTiPIysGHhGOTy`oQ%$$L*rtu-'
    'EDT_c7<gsxOiOYgUNB4Sah|9JNZMQY0wV936z@vI5TQuc)Qk3OGEjD;#;)iTNwbt_+ypIr-VkrdXRU97z-'
    '^^jpc3>YUESpm)D9DDFmP?29Y2YPaAn4`K0sAeJylWCiSeD6rtT=&%5IpXmV$mkh4`=l*O77^v%`*g&>P&1C7ln{l3<@u&da$T'
    '_A8S3Q$o_Wl20-yStA<fp23TYnLY&~b(iVO{|G_T8+`<%^eU?R6(wk$jhLpVM==xX1j$!kLc;~K1Z3^T>V44THe8R*(+)^-'
    '?0fVF-KMF&65n^<ybM~$>QlTVZo<MO8{Xob+HO@$j^vAbsL@jck>g&Qe-TmcmT^F2~j?uzM`mTQxt8j!)M@WN^kaCB~QzneQJ='
    'jAI-'
    '>s?XE^><O*p4t|9Ku`|!@@h!5Cu*+%gKYW(g=3_4IRv<B&Vm&)t>EGpr~9hN<VP*69S_|fSEbErElwA(Z|Yv70%E~rDN<uWLcJ'
    'dNEA{g$-'
    ')kx9tK+7o{|Qlb=5YJDpOL0Bl$_&?+jBc74%e|>5%Z_QCxT}2*yTqRrvSsl1$*62jcYu#YIfpvYFQg2&K_gX0*pq9tn+WX!H(z'
    '_szo%sKH;5ZXQjtO?OQ+uAjY_KPFbXwNv<R8_3q=x_TY6}sR)?y8Qx@@!T^!DVH6$vAMXcnNb&_5i4|dgCR-OvcTc9exNV2`FF'
    '-(%IQ3`cRz9|&>carXv_s*VxRlUQOX8#X%+2wnt(vbxD}nMCs!|1s`&L5Dg=8q>E|mTNm1s!{Poi031`3nkogQr`H1LcY4;z;6'
    'M2!oz=dHgq!vcHS&E_bMjjGfwut-bXGD;w&d9*HSfF5>^FztaQQ$PBD1!yYsWqNbF2JwR-Kr3iDWxl9smlt8SCo4@n5%%!gcg7'
    'p;DLqV@?WSIWYXAhsh>3++JRDR1(KjM}h9uM<`UTgSy2ya1)p$9>Wa<P2Ufd#3Ny6wZnp#oPhLIgu5rSovEF;~FN2nL5cE0Hvq'
    'D0Yc`Hg)M1VTwxmn9rkk8?49F3AGa+cm~Ikg=hv<JDp+!@H}2?$~7B^@TT(Y{DR>3sxi*pjAf0>L=fq<d@~aa`HG&j=5sM%Ye4'
    '#E=6#juPJnkRn2Qb(9A4-mQSmi+JMAe`O8fBhYtkDxtqi{QALD$qL0j=femBAW9Rv3pudH&455-=$*KpZPJ}i5zcg6$#sP-HSj'
    'd5AeDU*#@|U_nw&lShU{s{Auxop}Ad5-0?vE+p%h+wcL$Sf*V#|I7b$R*Z)sdt|edIb6eEQe9J+qcQ94N08N8TLuFkZW3n;-'
    'j((%>w!_QVhMln#Pao#`<o5ti!KvXx_R^(aLm%qq0M;9|f9GR-B8{RvHC`Vn`sk@<dqV~SEovak-'
    'kJE>)pc)E##`m4bt;i`StATOslM`H}iSh_oNMp-FxDB|~9m`FKebw6)(ec13AO<Afc#vFQ`UUXLB?8~VYU(+aB-w+-'
    'I;x`8c6p`5cLcE+5>Vm7}rwV<@t)cqQ-'
    '%Xb#cU}kytLVGvdLW@tT$pgQtdiyzJwg@8pDL_!!gBI|anHkKf$b}BB}s^cN2ijKFoH?kJ4&A-'
    'f=I|Ywf$Vt@Dvy{C9~4E)G#<acxNoX<wEsdH8?5?M{6R;v^K}`B0g5hZkwk*@8K?FW(qy2+AX|Z{g`{S-'
    '0&<p`xoz}W}Fn_B}g-tXEXJ^y4c#DYqRdEAy^w`VloA<R70b}usCitxtU#=yJ0y3-'
    'r&w_L}byTGB!S2BXkV6rEpg)C)g6}moqZL8H4N-'
    'xSId4TN{lQE~w50p~HEsUvgnVx<kizGgmcE?hm53C(f5=J(PFn6h6o11XY*eNNxWz=uxA>LRI^3Qu#Y~NRYn?goE+jR3_ho1W%'
    '_uje`GaJ3=9^G^#AlT)TbD_{d04brL=}3Dc>1xCqK_D-'
    'K?3p9cBfQv)aw5fQ&slbUm|t(~rg_JKKkNznm5%Qw9tx_2#2&oVbetN5(mX%QW<*Q6x$Q>=tsLgQ(Bt>-'
    '<mlU1&cWK1(?>yZlVh<sh+Bn}?n(T%TD*t7m4eNV>36=yN`MF#8Z^Wyp&nSh~j%_YXllgYy#sW-'
    'A1+8L^c+@F!qiTF4zUR6BAagZgUjz{t<_ODIYTqLzfD9t2iR>X6lZbDWV;$e1Z+A`pb;5UzT83M5Td(Nu~&^Q&!Z#@;-'
    '$A?cC<HpsRGXv3jy_=K0G}<}V^gr*bhscghlp1HEst(L`b9kymJeD|d-'
    'iU>R_ETS;Lp~;wtaljpB$+D`a7!uQ)^%`fze@}+P?}pQem8k}E04F~5jcG?YwR^7*-'
    'of8q^(^NR=pW&_@(X6oboO0xQ%EVe`0lytJ@^xQO<{C7e4`k+WQ@10*4q&h`K{PRMC4rC5q}e$;ql#LUaY4?MwBqp(tnMVHKs*'
    ';xsX-EbbUqbT74Gg%E^#2W|u~?q-=qXrce|En>~fnsz9OP)XtXU~h&8AUG?|Sl#Lle}C-'
    'R0=9LqRZ#dr(_G@83)j@jr(p^a9P)_8p(i$_`9uzp$~aZ$#fV1Q$Wzj4z#P|MG9qzUGWN<6_^pXF&CX`~AB0u1!6D$H3vb5=Ba'
    'B#Y%Dp0vW6VIq3*m)>(5HSx#!FS3eNIMo?iuEMcJfV!(5Rh$HZ|ykvoJ_~bi`~M5Oqd#R2*qwG5c1v2EKg;mdE>tg41!&Ie=-'
    ';WpG7#u;MhQJRavy-26z)Px=7Ooa}o>G1uSLx84St?&^Jg;E}lK%!TOvo$b<58u2l`n8IGyMR}LTt~GuuF*KGIH-IpZlP3m(L)'
    'rma=4v4)MUng$Ybu)O<gm*=r*n&|@tcx>UjJ{duJgC^nG2O55`f9pU!`#YMJZL0iXyDMmr!VGwMR~zDGx-'
    'G1q+t0csu~)bo*q~I?Yy%0dOFN%zRtFo)~$19!B#6@rDp(ZyG|pkI~_v*;_VtvH(q)Xo!EGD(${}au{#Z*YVJM6K8l+h}8vyVu'
    'Dl<rkX*}5=rX>U{<kh^q8r|d7)D)c;^++6z^g8jG9QcC<cJ3nCF$40KW0}3mb8xf(A(us5=-'
    'Pm*{WqjkxbBY2kQ*;iuSZ5Ko~z_{-p&F(?FPHv%iS0>-'
    'c@&<YmC2cy`e)<!yIa#61#!whdSiHbhCGBxzTYl>X?t;>p|aC~E2c35v^OSipYv`vfGTvvUEGGa`X{I{Yy4)5%10?I+8rV~B{r'
    'y=N2F9~_YaY0hf@W)3%vi9l|c_(HDR)Uy%t)%3h*!7dQGI}&2oYKV1s0*%hVKOc;U>43fYk<#_;HQt)<%!zQ<)IR?Q-'
    'qOhqHY$Ihm$4NGFU0hDOzJWo6EXBfE<MtgjStn@P}om46#{ra&g-'
    'lMPWQ9uEkhO4hlfahI=xeChto@p0;HupynB1>Un<b#i9FJqPPX4lN0LA#-F8x)$5{=384zyhk<-'
    '>Du~RIlJP(_o@b7fi=RzvtuR^3p+lQV(-g>QY)NX;q16obh17j<C(L4O;9W47#-'
    'FIbdyN)XjD<vlyQ7(sMe#b#ae4rgeyW~g(evG6{g^xgVUE)h-'
    '!R<Q|C5%4f)ot5Ja>GLR|_nK)>Wi=&+ewZ<*CQr28*X<jX_DO0VL6`za*n71jvknCUG=)p_vRQ-g(b)%4vx-FqtZcXyCa^GQqH'
    '}r|_&w?Ig{Vh*)Mav<qMK=ij-4#@-'
    'sC&TY{va#D7YSdgxB1V`Ipe!Bfc3qZaxLC5eebkDBq;_OJC1=?ovz&%WHV;ao!9lD_|;fp8c5oI_C6*-6zFoW2_(FFU_-'
    '27|DYpvZRVz4JpOP`?gBn;$)R$5pC6zMx<`j2*9?xc!E-h8Hh2eM?hU(DUawt=}OFGp-'
    'ZVT`^S{F8Dvk$tH<A{~&%e#vbN7g|Tpc`HHu(x}B%YgNR>JK}{`DY#?^v7Vo;#vdkH-reP+To`J_+MgxE8xy7kEu9&E<P+A*O4'
    '3~%V~TD5Xym#K0Fc~M;2YTW^EG2qji>25W@kd+3&mEcY&u@k(}^(9{ENLa(&_!D65AuO3yxb;9tY@V?_^J`pd)<K&035l%PdcN'
    '6iSB!%Q@pUI;?P^$|~9MKr2;tz=%%eeIy>|OYg8)&-'
    'qa?9e71O^v&34e|71>yGGaxj)q;(X)y)#+kVM{Yci8Cu){x@qPbRsCgULGoHRn_bB@sMpEU;u6acnK2qB?8&!l4ahYEJIH2@B&'
    'Jq^SX?V<F=Ierw&{<`jqJAS$hUB}Mf)0L+lI{pXC5@o*}dt~3(Q5EjlZcxaBmLaAV%%9@+J0lEw{mo7Es_wjo)CoZKBg#v^wn@'
    'oXcL`v}aWeTt+!Ttag_L;Q1}@PX*9m1*67mAwz@13_-laBSthOK`>umFqc?<y)t2TpUcKd%G8Q=(R95-'
    'KKZVP{>7gtX*$2Ul+H{4)t$&+6Z1xaWc3)lVU7(YO#9?4``!NC+oT-'
    '}CkOpoHY75tv}+~|R+0^V815Ja)w(V0~b=y@Q8B{ICWpZs_+J$;;0<iI&gW@5$Ig<!l^m`N<LpY|6Q0$tcTdCY*L9{9ger<<Sb'
    'A|3B@rKagH5%rd~<8@BzmbD;~91BF)3EQI)3*K|^xu%W~1fJuY8o@zCd;)hln*(=sQUEaBvfiG<l1KiYg%pqtZOk`Es0!V-'
    'YdqZg_jQSFdJgtxxppyPnQ!S&3TU~qsQYDN*9s>wYUvNag+SD;KbH5>+?j}@WTkl2EHK4Z`m^M^JY>@uIR^t6J4ld1Oc8SY;DH'
    'UT1mm2{Y<wRCA4?qaAe9j!Z%dyPlQMGZ;zu*ajnJFV96y2KS8IlczB>eErS8O-sy1BcQ^W18nnrDv|0_|OqIK0NLUz@45SE|7-'
    'D0*GYs_i28;^_@QQ86*I+U}z1d~@<wI{2}9bPl34aPK`vh(uWU>_x+BP=74@bsEz1egg-'
    'H5LqmS|4gGVbUrt?5!3bp%G0$3GMh!x8}wjNAsh@HVAOuJ8C$hG4MgLdx0<(JO9oTpWV?`Tu1VRN6$i;DigO$zzmT<xXll3O)R'
    'x!TyPxR=-'
    'f4s+q%pW<p)Hi_A@j)36$FLomxHl&qz3RPbrl5Yh>`(jIBR)uyOvqPH)ZLo|L`5O^hT!da7Qo5Gz5HYDr&rNQWEhg-'
    'V2KL9AFO5qlnzT>lC|3<VXiyKuB+9fBWQPgbS{>Go^jW<cv^e&r}BH5r%ExXKH;?#2SbXUR=FYI1Ll)5|aHmKT&%8hkYR2i>ev'
    '#|<xUOUTi>hk;+>p^3?|C}WFm<GYWua&lPtN4#8x;Ly7m?)|^`Avd9lf(8*@xbu>xZutp??YDZh4nazHTN-YEzc?N9NLSbnHd-'
    'kqiGwPaAJye_;r9mjtL@V>W><j(I<BE_s=G>>**}IvDk)@*3fKciYaKr3lDXA?JE`4Y8&_;ULj5xo9Bktr>3^dGS=+-'
    'o47%jlL#}6Edwh4?{gde39fM4%9}xr+VmZe4X;6KiWNbYLL5l!gE&1r)pb?0k<;KxDc`o5r7f;3!E!6$W2S>-'
    'K_$qBNYJe6?%`r6iF!y|?vEK@6qPAvUp`>7R!rJU5gI9br6|t<K)6l)Fu?~l0IZ-'
    'c$tckcGBX>&n>eGY6&4y(WT8<VyK~&G>l3jo&@R(To2zVaSK`+s_W9nBvCfmx+%D4T<zJ?oZ68J%+A#o*ge@Yl2i`9S^^$!)*z'
    '_S`knz{Ys7IlAx6YPqwM;a=#PHGQLe2Yo8ozs~+<2jUMO5@&Gx&oDD(>RL6Jc)nyba)9juC}ry%fl9{I?_<G(>_Dk=D^nFxy<8'
    '8wRZY!f;2Bc57@Gs5kU-&vto$4h{dQ3N}L+5p-6;+vV%oZn$$Wq2L-'
    '_yHeJ?&(8|}YR7kb!C70l>n(Uo$;d<2D1NqI&J9aYb(GAY-#D8bp*1aeChT@>8=w7?i87O^Io&IjUv{h7>^TyPRD9~`<2<d6{-'
    '}@Ww!E59T&5+8OlkFT6AAl0_943NH_L%!1q7z?N*%yc)w}(?ig`&aW54%7*95aKbUy^Q2@=um~GmKU=Oix@b-'
    'U`{GX<2P@5;zE~6(t6qv|`nP-UnvT^I{8V4XZ=@Qhv6dPGX0#uC``>!!Hmy7-kX?S#YGchc8-?y}AeF@y5jK-'
    's27wHUH6p+T0|H7r1NX%u4A`duZ~SZ2BCO8MQx$l-hanKN!LVbn>|WO-'
    'Y}x=r)W391?MN9P7t=BC504@O4g|tL~eof~Vg{%kY3*=&ZWbyjkpkk5+tWAtt$`n<NZOy7hZBC*j)BMzaXJYBB7r5;UD9^Mcpj'
    'e9L+zdc<bGgEt>;J_rV84ApjPI);K7kFR70!oL(p_C;P#BPLyUE7=OHGd0#bN^lnyTS?%Ht>hY&)8^1iKXBo}@v>=Ebv%t2UHw'
    '@H%zB-=K^Ebe*-w1)nCtQ9frCI|_>dplE3JHtFU&&pggUBCxxGH-o))dDJky9(JiH~@hwM5O$9Y_O;m#1-'
    'fQn5u;86@~gv%mEIOZ%efgBR+ZbF1}jrO~4i9Q9D0;l8EWY8)j9Nao%^?a7TMiU1#5Kw!9i&rhW?ly~nCJ?IPJAJpfFW?DiAX9'
    'tSwa}?=zbqkvz3JBo6HRJ+r1hQat4aSl#qa;z{GQ`a`Q*1<DHH*#DX>nCD<R4D@(UHI4$WG6B#s@NiibHGiq2EAto;oH+WQIo#'
    '*mUsH_E24`r9IM=nzU!M&OE(-'
    'E7?0(<~&`%>)ohM_i6qe|2f@K^o>>1Wh2LMTzNCBB5XM<Bxk<h2so#g5wXUSMop_08nmtt17ee1Qg-jW=t?jS)qa5h^W$b(t9Q'
    '&SwYtuv+$Y=cpT)mU**m*pQVy{;M_Ejyktm4!I7v~C}mtqueM8)zmLLxqL+bkb2Oi&qk?yx>|NfhTv#&;#=*rxUIta)TAQ=oqF'
    'vt>_`XtM(*Ar8K4<8VSRs8q^l`S~q$v&jlx&|X;V4$!5bA9h)s3K=hAf7XluPW=cUs55<9;<GG5KZIFhsulD+wQF5@l2Mbyj~T'
    'd=a!|@0FSK0j0d+6yrO`!m@H0m&J$GG6DDDzqoi8AZ3;LBbYW2Qh0s8UWq#6R>h?tT9x7O4s|@0SeQFdV9VJzlZea@bSes$T)u'
    '01Qj=M^6RxXCS2-'
    'ZY3a=q(TIMYcY_b}Qd~m23g%+Jf><>JYc*@HQp_u41fq)tM?8lqe{K5$qDkV1c#R5wRM=^f+iPp_(MP=$>Pz#%^c5q|KClW9np'
    'k1^0)e?s)Lzo0{w^P1)7o&Fyzp$GLef)33q?+3|Z~I{&mH<GKFYg|dP-'
    'ubA4cpsZ2eez1c-}yB;iAsB%a2ob<MZf3Aq^@<*hS=Hjd1z%?rbp10>$988qN-CX>|`1cJZ_k18pKzCV|QcLu-'
    'z=s!}Jc{(cxK7fIZ~EWhMUF9ruOXT3Cm1#@$guy3M2-*lU|hDU*Rk<e|oMkMa0qIiJ$A8$oo;Nza{ljh>&yqI?`YZ2?-'
    '77t__xeSxvhzwxi%5~=xqc`5X3g-9_)ys4G5TuYr!<WaHi7E*h6V)WFOHxH!YB`7RTOc@R-'
    'mBWN*dc_iW~~W8gK0JHho+?8s@1q7rX|@%hcmkKOKmU~YesT>8APh{qRrl=F6yNU3Ll&8-'
    'OwM!N!^8_LtMDGlA+DsK$9LA!q&b9x?UR5W}K+Swg#(DDZdQQ{;hz<55%BM0MpNpVdFlhCPTYGmY6(O(eMwbXgx4P%g=9Q$iUT'
    'e5t<6w=oe5)PZ1v;%0>!8m%pw*mUm#|vScqbjWjDPo!zxS>4IXwV3R&P>#sFeSOvSTAOK-'
    '3A}{_B%2Hj*T1(R{Qp9K{9B2AFga&)&?h1C)Lw`U+B9k6nhDl#B;qtw2A&TP*8g^ifhNhfYJ>{`8{S-'
    'D+<#<2ZGp<cKZHNUtyCTP2H4)}C?lci6izuqvBtYXPVgNj?x79srJM@J04u)uxLa{?K>dY_B`SIhz!|icz_={-~(U+9-'
    'eMXgJy)X(q1e^Vohre^-djZjAKrZJM3jeWFi|zs;W->&~adb8cyh{t7o$#Ya#s<tq>j9O|S*G&pxM&-'
    'tCyeTdVtao(q3NoSWUjK!n0qy#bJ%yzOV);*V`M(|iojN^KGJT3=M>k`<>J^5xonqXA3@{Dv7aJ_EO}VuCBd*SNLlr+ty27|X<'
    'HyPK0j@bhG>UoXx%zE026y0yUr}wm?6iy0;u&P58BSRo{776z~<r^q5$g49f6XYns}rVD3uW2`^xS=4wttzBfs4(^V7nruFLVR'
    'YpuMLs^PZ;atj~A--$tJZ#jIZwUxE_MO}`Dh%zBSspc;70h+-'
    '*=%kgN*{Db2=?AuIWwn1m??6Y3X+DuQPC5_NIgkJ$=whNie)_Ddb<Bl|Up6P(Fm?IGA|e4w=7l@I7xoc-'
    '%)kuB)2}z+p79J2t*bTTakMDCy6fYraF=B0@{J5O_yNJi{Mh5pjULV952Csrnq!|s35;`;;+}G5&F{sJW4^bcHEi($3B(sGB-'
    '@s~`!uVQgk>ZWzCIoaB92>CfkeTmXYF@Lw5fnQ&pn9C)6};dtm{=ib>6oRdbbfiIUp8WHCqGS(>l&nlkQlR{-'
    'VqEAl6)Nw*P2A6dRY?>_0I0-us%Dd)UEdjh7-'
    'b&NZ0u2FmN{Y`*tC5&T8R)B^Y({|WOV9eC+<yLZVBr^}boYft{)AjJmpirIExGm-rec#iQkGzj#*ApZTOlQ%ReUZwsQ38J`W4@'
    '>Zdt=gO>%7aaFcH=C4?loIKQO*5r{V4q$HAVw;vUcyYcS@io;o2^8ll`5l3v+HG4@;%JgE=5JT4!T76;2kBG7@<6j}H1zx_PgR'
    'HY2);w@wu2=SUajyd7Or#ui$qed-dy1?CCqBhmAbU(~OOo7j1rPS=)ObArG$cdfNXewm}UQ?&FnW`!9xqW|fn;NlQBeGL!6t-'
    'MsN@c$3}rrlB7+oON@4~&~6VZN#bno+bK7(4Eh3h>fLvP~%&BU+aYWq2ZnK~Q0@?vfE>yZ49p+d;?p!p*sRMwfI!DjD2&`}p0B'
    'C`p2v2yPlo#TLx@c|g6cIs_YhB98AIb4c<$#h+^Z_L$0-hC~HdXvhS*T--z~7g3MG=-'
    'G~w(#i<DR{C$3qWRbs=2LI9&Bd96V(3EK+I~kCY*8mrJ=kgOCECJ%RvS1%*GyqM@wR_J1RG-'
    '<9b~~PP3|kVV1@X;R8hsxJVFCv*(BkC;=Lu5x<u+a6kd(jDgvcFQ$#qr)zBrbU!iqLXxP8lW2U){3)IY!D$JR4;PI+6rjX~AS@'
    '@lE`Dg*45gD^K=GoHGLODXDG0oY~6i1kiqsRCW%zE<pbJp7};+Mt14D+7d<v`~>{$rR4@a!VQ`-'
    '9Tznz)tIxT@t+up%g{ue(58U%9KgW74oGK%q3AlHrxYn17N2Px`Gx3NY3tY-'
    'r&({pV0cX=Y^xkSRiphl3*lm@t+}>Wxx1%YWABtzzUyD!Oi7nnrs6ci=u^peaYufB5t=aH8TyQ4^H`LNb8ygs~ckh_{sek%*=A'
    '{j>UU<b)yYIg_tyrHxSj+y^~B$sB5@^H(9qs=Bb=|4nwX_p14b_}{~l&|;dvf5nk!=mQRjf0&zi8s9r92H_K`D>yLFUq`WM5^;'
    'KEK%8^pNo5RbcyQ${9l8|RzbHJ3j6oJ%x<fu?r+a@NpX$S`AfJ)s4-'
    '^_vTE;mfE<=$Ku(XGJG=qh1DM@e;p1B&&?;nRkcnM472k7t>apyXCEx(<Z4j+6oa?vk#9Buu_$9eUoc~RdJeTb>uAGTP<|Fldb'
    'H>H*s1lev_BZv~X3HG&XIXjfeRX#(W&t%fX>=N-cs>x-'
    '5Jcmlen~}+@G=$%LEtGeGP?l}+`LEgE>*2`(^$0C&yEK|kO#Gf+6^rN)5R$vZArIDq^jsJGsTl`;AudsnJpW89wOv|%)L|YG=W'
    'n(ZH-$xyU|JY>6#gnaM^mOX-'
    '&C=|MY1mve#XWF3O8Luim*&^DV#&}ng>>2dLfR<s#F3pWr_k#qlibibTGg}^YM3wq0}(!kZJi|H|uk1b0K=3-z-'
    '7Y_y$O_QaIG62;2wnjkBWe2BjDCOLCmgeb&HId(kqDz|qYq<kz5t;!*{6C~w^ovKw57#;H|fG84v%vby&N4;0qn0I3z5O~!`Ks'
    '*u!2jUC_OFrvNe<BKSsYw=gmn|2NZsie38hq@JM)$?Qi5x=x%jM+%#<mNn=78W6ue^rVTUP^OeJFzu=ZqMK%28!f1MRn6MqdJX'
    '<4g6K2A3RpUd0d_pB8TU`Y(l*Nt7&`Q0Komv5z1FkUN&x)_|W297U`}X!WC!W7^LYLqD>&aH$m7<)9^{vC=U;qORjo;=mU$M*c'
    '?TX&-w1~c{eVF-bXCpTF3=v(Zo@OeeILoxdW^oeGJzGS!!W_BKaqV9BODS$JcPC7eP>(L-=a*R}EaT>bcv`kS+4H*!i>-'
    'Mx@t+1us`QFr!=K4EDR_b5G8UTx`3ZI|1h#M_+csN*B46l1U%nwSF<+axVo&r!%TlgxF}}d)x#wtX=GP6D&zvM8c^V%^DatRz|'
    '#KFwu_+2jO;abF;#MhNJEYAJbP|YU9t*NXvlHL*z!Z2_Tm0QEhNkdT_J*c#Zo0#>J_)io2^LBxb5yS_J7$xsoRanhy3K2Ci}f{'
    '3&%9*VRy@+jvQg7263iY_!+#+S!4$Xn9VQ?`-'
    'sjtLNO8%XEZ;M`>tZzOv^l>(nQ8iWrH`zb?#eIu36u0$BzR6i^;~{$zq3k~%R?(3P1=4X%VR)FAF59k6jXynBwJ@sd6DOrr&;9'
    'sP`YdnS0pKv~6V3N5$Qs6$24#sfCxpd}1KN~D?vCty}J44w>ppbVWFcHlpY0hqCE2)S2`5gLCbd;%@asx<=7)yP56rXQ~oS?6I'
    'I7j)FJ@w<b}a-'
    '}f?v@DVk{F7cWUNWOKo#n@iuTl@3Y5&{V^*p>0`ftsKD73Bo<P;0swlINKXZ?)Vc~E6Kzo&p1ORlRo`iy%)35d)3Mm_U{%xS%>'
    '9hO~OT7mmI2x{K~USy6_)tTbuj$ypsmz|k~ORCI4#t09X4i{y#W0R;Ry+B)mO-'
    ')WY1T0?TgfCL5<h6e)TKCObqw7q)X*Y+&qq+l6lq}&*3yVMuiJ*|)dQQRf>~KfE7UL(q8h^+tK5*YN(q92)*iS9(Jd{EBO&C5L'
    'e>~U9SuT+n?GtQVx|kgxMneT@BK9GYR}VH+RsIkD)LhF5`0n{&NX{7)_+1m1IVii~9ya0-'
    '9RbYqa~j3COmZ{0JsP1;&(hN;R`BudXdr;JwPYdSY@);OW$^`W!>+iyivD3h!*&=F9N*gRscKT^EAkCi?@)F5=C`buunjk`r3f'
    '2}6XG!p7cv-d2oiTk|8EHPxHe9OE8O1PzqQ!89CW-e!SNSpC6l>>cr$*-'
    'keacqVQn#UQaM&uCe&&5QE}cEW1E%O+8fi=0D%Xt@v+wt>45_&qUB!&9i{9U7=r>Q+IpcVODsP!l6@7}O!e8^!vrI?JY5!!$y;'
    ';4wN3L0f>Ch(GI1|22a^DT%rx);08;Rr`ky6Jc$mv*8qGkvYJlUE1IrF&ha@U!EM6J~+p>d*#+=-'
    '89~umQ5LQ4Xc1$B?9b^Kq8f>^?MPW4$<r(`n5mh6w6@pnlY?cx08c{gC)pS{mO|$E>1i3_hOlNsJKeO9m*F~mloAjP0n5S}Ug8'
    '<7atu$0Q4HIrBbC!4`bTQbAE>^Gp`AU*aNN>{HpCGT)FpGK0x@6yW*U(8W)sb7Yk?3y4E%!9%MmpSduf40E{fIJzi%Qd=sH%A$'
    '3uk9`17yWy*Q+Pc2z`DavWu2Xx1#9nw{AwAZ&k+ue6rgShc`e3aB3nMIRG1xZ>4R|*VM)|B!Us4h7nWb%gIcZR6{uYm*H{dv!9'
    '+q#+vsyBWZ~|037bysWJS4FSD;gt!&MorVF=HhN1e5h^qY`$IfGvs{nVi3-'
    'B649ZiXQ1qN#C>1aDq<Kg|Ljx*78ja4PqW1&Edy=^4Zrqg&5QDP8)C3E(6ISI33tn(AV#*dN@h>N9ut7X+zLFPZBB6Ai4TkOKo'
    'atFj&G-'
    'U^XWtQTkOIu)}MP5$NuQ4uhhzNUaq;r)&&+|_ZAVXkvvcDtM744F#UxIbtr<8|=qOXZ1yBx+#3VuBxjz|ivRuM8Mrf+ckLICVq'
    'ut*>M2yQMDXyc0;XVU-<b|?T|=Ay$bp6O<8^x>>eujqP7DlJuJo*jB@lHZ=28Kv^MIDJ>~^iL|-'
    ';vPLR2dB+ehAO4ra4wOJw2)gKF+(fF>U_h|dxvCQi$4|Y3$!SR^SClQc}{GVo_N#WIyw&NcnyaK^$QA=OGUugr$xR><eDYl#m9'
    '0%Ji2)+7N2Cj$7<zBw*3x5N<S(>m?%w77Ykqm93fMp^Sv}|C<w;utpOS!*71pmml}R#1bUo$0VHiJ90A0$OA!@Jb_NVgtvqb^^'
    'JKtkNVe1s*YmWJipR~y4bxJmvqk7!1lsM9-'
    'Y%dad8s6k6m<Y>W_JhQAn~=3pRr!WZ*7JO^O%DU0G56Zfx2<Ygm>sYi>jfJ!$kxCTf93{24K&BjZLZ-'
    '{xo9(BuAlKozNy+cz^K)(r}&2?lWU9a;MGVdQS?_xwzRUA~89U3U{${N8Zv18Vw<}&H{|KtnMP;2|MyWbs<sA(m=kWoPDJ9JEN'
    '`RarN8pa2r}}Ux8%9YsEl*HqIyPULB$Ns*<jB$3c+W1RcLLqL$j?>SUwf8Tg1(7go>;?;XR}K(WEt(9|V((y`>ONe6z@_9NH8R'
    'NsnL8?1Ps&&lK~@_5)ZoX1_v<w<Zn7jvmGFtY%KpC2+xw(_KEPB>};0k4P|Fbx+P8t=SXd$SGoa!pz=$?FZ}`{MRJQ!SflOjXD'
    'KO9y-EiFx{-'
    '#_@%vA_3a;$w#r&$^#88YU{>G(6~*n*KVxOqr=_>*EaW#*xTclRl$#gDuTOFrw4wiK$l}`Ti<R&m?lEj1}HW=>g!Ww8c-'
    'sPvchy%<>zwJobrkpq4elC@UUT1F2&}>rTR{OXb0ClwCHF%A^()tO*RgJ^Sz|+kjFOi!`JI&TR0lkp=AvQgp4u+z<Q<>PKfyRo'
    'WHVnS-CBStf}3D$(g?kmnBc?fKy=EclbuA<U>oIB{aYCQXy}EhH%5Wqqz5cvs(F|%pjl_OSFP~gr|NWT6l?c7q8N?g#C43zq%d'
    '2vqP}eVZG?NZ(p`s>W7uqhdg+QAO=N?acO6|4sW4!9@s2<00@z?jp{oLxOXc<D|a|tQULC(|0*h|#k0a}$$U*q-'
    'UAl#lOSUTJ51Z`4f+dbKqq*(+|V#~hL#^LURro!Fu(!KxEzdsAe0*uFg)|*$0Z%p4?)E12%urB!>{hsQ0=BzG`l(-'
    'HITm#ZNKSMvRLq1LI+2r91_z&EI$~RNo8$c|6J9stZ4vmQ@2WXYq?8>WaQv!oO!?kP%*1a2i>4uk{o_6W+a2QeHfH{?1$;mLB{'
    'Ky67{WRL4nawQ=4LOoz?r*6i-'
    'p^crdk;%r!nCLx%5wZWV1BcnAxJ6yc;G7dYMghd_+d(JX0ozo4t90KPxVi#Rd~<l*V0aByBLdx!HEhRro~5bjTWr$)53v`d5Wu'
    'rb*ygIV)~Vg49d(lcPR0_Vd|y7Pv{@IpKXd|E66>rd`DN(sZO4RA5IX~XXqdF~gX^-s*36ypDmOJ4<|hCx~&u-'
    'Ej06BMSVZi_YGs|~=#nQfbdZlD5)axp}U9Ps~7k_=W#r~zq`Pg2M1@)F`AZ1BINrP}%%prQ!_IGM&VA`w!!ZTee~COEtLkwUl9'
    '+-'
    'kOKvdP_ymZOtZ)KRX%r6sWiHAQKE=b^bwoF)=X=eXi|+(+SQ?!#dwiF{>E*)3d38ZvV<t=np*kQi6_BAP<iXa3lg_N*<V*%0Sh'
    '6>33ULt1;8SK8@WJw7izgK|<a7?OQ;b>Oim^gTdtoNm8z%eEw(QZfOJgm*~ebcc#3@ZT$6eqmPfj6{iaPETNX{l-'
    '4kvsn73k{qFmvW2_iOETK2xsHevvwsdiCV=1XS>zqK_*KICa%a6D`p@IAbV(zWjx*_pVtMI4mv^TxH7w0@Cs5@dCS)CB23gaNC'
    ')NXgrh~ay5#E8@p7Vg4IsDYrJtK6U^Lh=yY*j#%9d00fwi+LB+R3iP{l)+}@#I2#wZT6+=*9`x3lQ?_6Xs3v|A0J*Al=ANz%Fd'
    '^Bv?%zL|Ac1S++-tOZ61ZE(y;@dNWba@A3fnyt`~xh<C$c2WS0%B7C-'
    'R{)t{++P&m`wi4SIJ3qJyyD4I^mf7X}YWdnGE;zRFesND9ww1aSV=)}rHU@n(&`ucHnzKBAjJy-'
    '!`HuPx>jakn28%*toEyVPzeq4zKx-'
    'e`L{99iHgjtws)T%ik^BRN{2V$`!estWS{*V;bap=Y34F0Hm6)BpQ?5!UZ{l^!X++b(BDeFuYw?N;bMfl178;>boaxSM@S<C;T'
    'f|0zGf9GyP?@5U&DfG#4rHx*3=NeRrgLoIb3+jC9Dry*p199-9*ttQ*04j~#QzQbHDWulOtU4!Xarb32OP>v_d7v6nMz-YjY%-'
    'wMHgN*B@zPg=nCZI=rLGG?u$YEVslAmfr45BD6*rvOTB8;KSVgFjYRW#ygZC7)QEhPd&jtISIHyTYD#-'
    '}uGJwgQd&F{dMTm4%UUbG#7L9^$d-'
    'E5Qhn_F4<YhVIdYtRc()6&hUY<(I)KB1ig!g`J@Un&o<ihM82&*XPWh0U*HyF!A&90hh%~xubG>fX4T`q*1Fib0Ci=3&nBnlJ<'
    '$?Vfvm=(oKA>Nt7XmkYr+ft$Il_L-8^>617uaB`r18*`ND_jU>U*SV9Y&b}PHDHqs2QNeIo9;sI6|F9fN+^0wguCk7-'
    '<aP3g21O=iPkrX}VWqJB0YsKe`uFox1*`X-'
    'Sfpnx<A?1uVz2QVA$}Z6Xrv?6@Y4*~P6qc_rs^S^|7bxnJ|^>k}M98P3}1R%Lm~#;lwJm57ePlmXhcC2sGqd47;2m2ocbw!=U!'
    'Dbf2)2Wo6*>ueqfd3BZKIl(xa7hyHUAmUxS_1Lg^O@FA*Vd<{YRpK|{{2g`t%^edjJT6TAzd<tkuf{qv#5KqGQor{tyQ@{@Nz0'
    'l&<BrNNm!(Xvv_FmH*UMLF*GWbp`M&(^Zt~L;$mkjetvepAJZ2y&CIAxywk@}o<v}|0#qyRm)#4{Dv^A?w(PX!P-'
    'yR3Fgtw|kk;t0+1hVJnSYU@&x|pv-ckw%^D02KE;pRM@c{r+rAk}|Qs81#i^Bau-k90BceEuidFjb(MuZ)$0Ycd>Bh48-'
    'omPLb4a+a>2@dC|4q>I!VLYa#BZF7p;$mWkQa^hSO+>qS#wE)}nUt6no2h`I@*g|shK#aY!HLymd+g|rvRI(vQU+>ITJfs7s5D'
    'Jb(+4))&Q2XGM8TKNIt#@IXN?&kXMl0>r7z-xph3h-'
    'Q9T{$ak4It@MHlExHy~%&3&Hy~WNSsA>Z6bnj19Q<0Byd8dw~%vD}y>RYpq%#Ag40BG^i(t*Y&Q{X)b4d_X8#D!P#9T)8U)Y=&'
    '*0>G+Cu7T#1aI!<m*>bX0;y@khh$9Uk^V3FLl7)4IehDIy;ft`_e@<nn17V*?eP|5=fjp^Bry@-'
    'B4tVshtv3P25Fgxerx%69pODfMhqT-n|1Dpv7#{`cE1XLj;ih!FH(`_aF3gGN1lgd!zMMB9x!E-'
    'Eve3;7kriY5{&;=fz^LKc@6%+y;g`f-'
    '^8i5wo=`y*T?swRdvHpi7XmpD)WmY7^OOIhal5;{8Z*~fKA>m=92I$i;PcqwMTxW#ugN$O7+om@q_;_Zo9&Ae_tNkUh>O3?OoJ'
    'jo(%*z9Do0Wrpue>56cz6eklT-Jh>e*YS9kF6ykiU6Evdygh+MM>_A|7@_>`Jx<@niu&P$?h4{Q!^~0fs4k?Ehf+FK)zSRhL3!'
    't-t%@M&P|%7Nt(}OsLt=cg<mRUo}6BNIXg)*cd^}f0S3O#;Qm$4zY1{QDBI)CPq8$do(B8eSY;b?aLK8-6kqUVqZEb-'
    '4jMRt8r?&q8<F~|DK)vb&Tc!RbQ`bYtu6zZ9D%HkZXP%Q<84vIMou5;pli7=<wDU%ho64>LTQ63>qVERWYe}<a%yh*4E~+AyE`'
    'FK!_IYkk;K9@!%gzQD?MU6Gn0DmVlfo9IxYGCae(GUpvvhiZ+So2y%n(LgBB5IV2Ygk2s&q$5L%B%5G~#baUORBI04wvd^y~Xc'
    'CC~Jt2Nv_(=sNg0Wo_9&v(LP)v<S#Ij>x)Q@k5W=C>!1gry&J(G1G|q;zSv4z?Vger`X=qlep$1hVOU2Vi&LzY+x|(+RRhAeeY'
    'NSpEIIL6XW#13jFR)al(imrF&L>&n)Y7$AN(c?2!Px2RxPj8Lj*aFE)+vq0y9`X;4n32ZEMwWx{vk$uxLoloUH{>v52wot9ZVU'
    'd2cJM{W5#yG%>bO@9$vvnrcVtcaf0irQV!LgBS=L2leb75s=#f8-'
    '(rlWPR9w=SpBdiXX@_lyi;BDoKA~`BD0&2gFhP@D@S4o*}uZsb9*yRI{E{bGr|M_aF;rFUpHK3YVN!cJj#m`zzj=Cd@4wYoh5)'
    '!3!PM(Y&ZeYu9QLi&HNhoc}xZHI<)eEuH-_FkuN-*g1C%RxEl3l55Z)01)iqVH+&nBsZX*xRbx3_QTQ(-DzZbY_-'
    '2aAL^Ag+esVE3W+LYN++8sCqG+UOFs&J(=!r6N*dfrST$qabpu{bZCy%#BI0NX=ycWi(PtFb)o$)uxfABZau4pIP1X8tVQQ`K#'
    'A^M-=-k7vN#F$E>;Z)av>^9uiaM?*`9rnXworRM4|7>q?uWiU@Ms<2zA)65F04!;C8-'
    'g(XoaD^RtE(8R+7G5Hq?IrZu{#zN*8h0-bEc<`X&X(L=YbEipzSbmJ5^9HEv$h_OjCjIr+0~Q;j)p}jOw7-'
    'LQXX?eagWEEFTY=4N*SuAXQD}B*eFO~2m!@}Jf6Mp1_nyVH9k?Tu!$dnEdT}73s82UT_Yqa!?p$h%ZOmU_>%HR7MAahrKe#5{F'
    'e*nHDpKw)tCD62@dVFpg)GJXuJv_x9-'
    'MZ4Lr6xs%h)sbOt_DQFyDndp9W40>DLEJjBG7KoirU~{Vn_6on<8MWAQ#*)2K|OYWR(fUC+TBsr^w0dwJ=7|IofboeFTRk*5j-'
    'AN#3G$R=k!+;iqSW;2(Vm;pP!gD9Q@@!ccb1>MvsjlDMHF~6cy>cv{zP2A@R2`6$awb4GVWd}QsCa|aS0Z;!9HcDnbiq{fqBhx'
    'njrlUP|z#ntRjo$P9`01^>SCK8nuQVp6YR$GEz9ZX}!egG@XgJ{O?pB;PjzvlLW#=7cp}3a%aILCMC_E27!q99K)o?4i`Qo6ks'
    '_zoH92EmbVvFvZ@i^aT$eY9z#*E^PV2Bo>5mjb(Zt%&rfCR(opG8I#z?P^dh1Coh0(JxkkO>(Hw`<THH`9r9)Vinb&{CCN`fjb'
    'hT0i}qQ|XPMBUxPxH7qe{?#0!@?5D;k%Vfj$WHhHK>1>aHB|lJ(S;;sRKNrOycKbL#U)s4;%@SlP`&8_!n8(GoyU?-'
    'V9*i!3_DBLYOFm5*T)PLcwA6+VjI<9amklyd3l($^A-'
    'vIL&uOUgIQ{k8cU;9;;a$dB(kbwk(hSB_)bvL)#Ixfnlnjah#NWVSsH>0$tfF><*c*0(eBfB!A@M|uw}b>bwJoenmLU38?78V~'
    'E~+k2A~009AI8m9DW;1#VKl=!SDOBjC244Auf4(D-'
    '1U8q1mDTk3z(Lvyk{3I%9nDSWW>8?VH@cEfKLNd++OhkN$rwbEAdT4Pl7@wd&Dfb+b%wsHww2ohnX4FH3duY`4f%?<>bqd{}@2'
    'JP|T@cG7N9F(l({IYHrdqjbA!u60F-{>Oj-rdt?H%Dk_Szfq75Xs-nPgZRCWewgvqm6cpVNzf!MZ!4uX<aWitw_wq}oEjpAAUH'
    'wQE%xH>%)Vt|bF<?Qz5&89U+%4QsMC%u!L;s^_9;Ct>V|t)+L9fb<Em{;Y0!WfxQ$0^{DTm*LqB|cq4t{=sL$uyAPIM7Pn*<wq'
    '5{dRS)!rQ_=o-N5^DedCp^#BUDL$(s00h|hCJR8yzRYIxfoDGR!T&>o-'
    'GNQ~jV2syb6bI+{2clDS;!FGJ2nhw5H+4Gu5SiJ!NEA$t~&(#RwleBdaO$zg(%^dvf*vn9<`DGUijXA8uBOVcwnpAhIXls$Bw&'
    '(S52vi7w#Lp(P0_N*TM*`Bp!sJ8;}`3bDv4yIe9nUOC=^7<{uQZv?Ocho?E0H$IsVRex!9fFGD;<ba{h03hPB|msvO&ILPX)b2'
    'pRH_s_C(xApjLiArwjU9BiTfZZoqgMQk-vF5I~iI(w$3!5Qtm*~65<aC%mucK&tgJ~apDt?aaU=ol?;WoGIM_O%9f3+gU-KNZF'
    'u%-'
    'mM4kcEn;Onj`33amd^=vJG#oF)L1#`QNv}a#euDcIVWJVjl;#h{T|GMSLN{iyZX1`FMD=}2(!qaKb{pFXJ0ImL$IPHgptRf4MG'
    '!vddeIKlF)G>jz5+zcnf0X#)rOCpPkE@zF^MQGXoezJPmL?5r))M+YZTkBQxqiL=d--'
    '<pL^W#uA^cc8j8s_ghg4SmYu90QUYpByEVs=?1*4WFyY4hwi2XMI=}|T0tGuB6SRGj?nCzIBa|$AEVsPmMWij4iOHULbc!^Je<'
    '~-'
    '#r!UqBBmGzoK&y`JF8)2@~%mlP|vA%RRV14FQf^GGB69;@_7{rmF*<?%l7>G@$K2dKusmO34eO6MmbO{m797t7uVH<S+Yu~!9q'
    'D=b4l){248BxI1H&inH543pI2S;^1+crZdxhnr|2#8WLhC?GQ*^0MGq)TfAD@;U$uLssMO5cA^oq}zgWJB43R~Z77o|Kwfk;9l'
    'jhDWr)G^-=sm}TBj!wq5h7Ih51RBY8bI@h^n0@=|nsEQWq29}q;w{J8cIU((;QhSKMz<<y7V-'
    'jvvdT}U42qBLDD6Hs7vw!p|E&({_m<Ud152Ae{qSMi`md#H}P5X45<WrV^Ce;?(dS#X^wu&2h8ah`HK}>%G;NY?OriiMd6=g$8'
    '*aBV?e@*N~Ur%(jPmvob0}WlYHRZacOediW;^kb<)eT%5g4aF8c7=Nr0nGvo-'
    'qbxt)ag&ocm!Wak#c$hUs=IO9}ve(y=?D#r|>ffBdS-~DzheZWB-ijZEIc*Jr1eTVnG|fTdFQhSQxaORlv>0&xh8MjiOD1f^gK'
    'dkLS~Jzp4JsbO(yuz+WY<Wv_W<RnQN%NM%@JQzpiXz`+=!`Et3BOa^_($yOamLE%KZWZLX$i0Nqzyn9^Nr{f>B7H5|9P_qt<24'
    'qp2(E2fX6JPdH%{qdAl0X5TtOx=fhh$0RIWo_w^B1jorq26eE>QOg`e$B2MT?nN7+v7cV~sr$0?(`sTP92@Z~AoQnfG<<Nzp&}'
    'O^-S#PZw{&%-|cVDTedlg$P527Z>$A{`p2&YNGq97TDT{q!Iwc|IH<PKvnXzYHMeU+X-Q1Hcy}`3#0;kc-Cs6YuD)AbmS~&RP$'
    'AAZ_hikusuw-Z4PLzFs!G1ZoTJc+WdgcQA!D0vPk6+H|CYEvFHz5W*n4S8etsvyWavTBcJ2e4(~(FIcZTNU8TS2%n-'
    '<}zR!!UX2ISpTrH~yBFi%cFn-t-jD`W$Za~k7k5UKe!3Dc2=0O-'
    'yyU%5E9P5rTQNj!eNU3%^(%7ervm6qh_cFpqo4T+LugV2e4Knh6_O;kHg|(K*SA6!WmLhrw%f@}mCp~Dl?UmLbPxY2%eb=a9{+'
    '%?}!xTf|kzuTFBxw?M9Z+2mB_nEs!ccIcz)cuMmlYSsP;IWY(v3i|Ja;-'
    '<^r)_ojzXjjA7o&?;drw_9(pQ@XtoUm(eS+!$8d>kp38PagHi2s5rjmt#6C@=)UR}F<CAF-'
    'Y*$K~rND>I)GEVi<?;dy6HlwO%>3mG<sZWY*5Xi=*qfCe)16xKkWwDD|6jkld+TZ|FY4=c(PANuLU**4Viw;m6W=5@_)zSo4Ja'
    '7J&%X;wT8d*;>_v*_q?LvCxX&qX+y;wRTO1vtUVkg}b>qhbhpXw3I^bKPiJ)5cYm%NVm)<X!C9#SE`oC}qvz$SH#Zug>xJznT^'
    '`qS|ps?Whb*JhYr*kfy8(^HZm67sfMu52#_8vkq6Pf68`hzq(6qVNN4S_lnI&m@YigSm;*XC+YNHmLs^h;~u)PR;gDaIUE>`pc'
    'RPFs;8&#ujAT^`Pq77Ov8&dQ@PHb0kt*Cku?gRo=w6Wp~oQt+0O)5W`*Fx45S!|!bujdc=*or5Q?Q!0mO0AanvUX!bmG4xD$n?'
    '69c)`H+o?&`L9%9P32mjb&#8m!_J-4&h~P{j#!@Skap)C8sAF)3~x79}m=GrF-'
    'k4@kdsM~IEyb%yLULn;A;)0mWKB(>XI$~Sk$*f}xf{eWi~cKEC8Lc+s|8(rzD5p(chlR#MaU|Foc{IZjk+Lc9PXIC4RarY*v=j'
    '6)uhD1CN78j=#7D@CUOt3Otr17Cb=ajouF;E2#1Au~)acD{6R4eb<EIa8*Hnad1h2$xnSpa=f7gPE>A20?4iq}o4B!h&oGHp`n'
    '_5#6rtv^Yxz}{~kJwDbuXh5&KRmRZ6D&xy|R;WRo!xMLnYwd<|j0^~QPa)UwSUG{qRrJ}4<jo+O>UsUw7@&_BhmM)MG92Bi13U'
    'EDvJjOIsk)EN(xfrC!B67u{MZ9(;2SCCo8F{qF1+gfYI4PQ1uDBXcXA&}$t5uJaq6*tkTJteLrUlF+(KBHamQ;~M93LXS=s~;+'
    '5qr3PK}4Ia*NqJf<_VP_5UN4Ynh?2C&3#b?>*P@Xkt|nGO}Dub<r3;B89`e5h+eP{DlA>P#Yi_+;sui5bt7I#W-'
    'B{LBkE0LXCAY6Hl%)FKsAZ;^quldKEgWu~e^5O~gh#=Srwf%-'
    'YV}>aJQ=(P3(yVSu^fWp)GYEPdy4on3xz*)uzJ!2>7?Ec%#NqNm%YnU~aj^v+KbNv4G3bhvAO_fT+$L-'
    'O5O$y9QANft1k(iB8v$NI(cA=A{GxU1y<P_F2V0{wCsM>E$OH!r-'
    'Om?r(8bMtIyj(naf1UJ3v+yaka79&Z6yxuWA@WK!9Yfl1MfxsAUT%?gyf0Na19zeai9PkcCKg()!C5qR6Kr<0|!>lugtq}hL2q'
    '%uzC+$zNjME8S1K%=~f^J+&;iX2^aWRI{PzXYPRa-9ob7RI1d-'
    '*QPf(amT#}k!Gdu1;A8HfNTLLvUW2hIJdtScCbL=P6ueIifOHetF~!B+FU9GnPtMRT}bbLZcxe85=s-'
    '=)1CWMruEr3Gc?hfIf!r-+jT{MOk`giG{>sSJ9sHy6$#jsP46VxHv_DFQ9O%F*f#70Rq^Uil%rR0H_;^b;ATA5y#9W-'
    'q&Crl0BD1?QdSrUo`$kMZg4get&1&4jf{i9aG%#J{bDk-'
    'J=T!c}bQS$5+qh1=r6pRQ$ZUg>xvaLV`abi7lxbh;V1Pa3n@_Tu99il2uAQ)!7QC)k^L+TJ(EzH}f(Eg^eIlBFi*Rx;>rA|Mx<'
    'vn`K5vC3N{zZa=+K(Xl~aq9491OU^qtc)tjV(`#qB2DhyT^wc`wt*WpOuPYh`s__t8wY?jlKqV0uU%X=R5<#^i&?3PUq{>ee3o'
    '9DY#T{w=kV1tiD}B3FpE^6<lKOqFnw}oxaI2p5QrD#RVUlIUEBEyF9~!p`b7IXkp=I102*i)Kh$N2{r|-'
    'ILpn$p`&|OdzoSE1x9dzMQ53ocXIleJ<1E@rN`z9zG-'
    'r5|l2zqOO*o0=B>Bu63j$J{F6kuNFBk9ur3pqbSmx;uST@^#Hyi4g<LYwIzLIg)714)6&Oup<+6-'
    '>ZSfFFLzrM%xO_k0ymC_hF**^cGR?=7P9lX0lw$7thM;=;c+wtd%0d=_;`KdV?%FbR#Wn?1p37QV#@g`V|xo~qiM$8ND51#H*W'
    '1PV#ESKYW4QujaUTh8{RU0uT-!qu?Al?D1;i!PDZ>d!&J{tBH$}qZVUK|r-$+ZiIBd9t<7Kj@>ATF!7s!f%XOlh5}>!W(C(<-'
    'AXjsN}2fK}dUfY>`Qke>qo{n*8GKo70jo>4s#MpEzReZufqlJ1w>$%5N(d*}!9`;Mc_U$at-$<jQ-ml7^cvr!&CLsY;X$$`T-'
    'c&eQLMnh$gcq}s@##pZ>;d@|Pjq3|}CJk!o;o&@_DDI%DT2<ug1z3t;rjRFB(^gCiD|*u3ESjAAvC@Lhd6)Y<FmQ49$_MjKSVK'
    '9eZ9EAC*~NRPsy<&VdktWgB{eKu2RgmOFz|R4dvG1BKO-'
    'i*C!+}b8V}`e2mn<RT`B?N@cfTR2~;mdVWYnU*sB39<sn=}?*&UWOQaS#&swR!-'
    '=e4ZcML1`f4&oG{GBkRKJ$#`GlASY=^U^bC9Ft0<nyVT0TT0m#vBTX36iFg7qQAexpVY)xAwN!OX$yxDDUEkC0ht?-'
    '^BJFvCNoaEYr3K$(dnFIbTLv!kBgfm~Xmh^8~c#YCw|eWkja#k0e90?@^$$4+ptn5D9Z!RyP}HT+K3hS}rl|WwGDGtj!Eqq0%_'
    'w=gXr$9p}O9%WHfZ|C}I0pjDw_yj&XW9exzQr2uYSkxlQ|gdup_Iewrm)$cScCD4%G^OY9ws?L~S>)-dB8?vg-'
    'aylIRJ+e!rbU5Xd$_4HX*i(C7USW(E@IF>FbBD*L<cmir-cUaA-F`-'
    'QfjWIKxA#{RY4L`edknli|JacFT=P2#Hb67nDDZUft@vv;4V*p;=t%m%BX6E6c=|ISzuaskIks5t-^s-'
    'b7_tnXRr}Fv9$&2akDEST!N|N=;pIk++IlRfouG{0U&c(E(!{-'
    's+6~=_%jIRuw`sca?z?<WG}gi@UL#>cf37?DLk*{@NNyYdbxkGT?m)tzH}@GFtZX~C9H5BE(Y-'
    'x58gZ3sHof|k#oh`a;euAQIwRK)7GNHW_qO4TjmNWibFj3pbxPmY1q}&%I4GdvH7jkz-enu#>g(=7Rx5A-'
    'utv$c2%W61j{9vZ%!R-|F%8K&dNF>h5D8nF!K>Xh-5{ehO5Sr_<U+KtEIJu*!J-'
    '}V&Yv0bVhC_{RG1_}AU61MEjTp&yENFDL}qS82l2nD&`w}4=F?9qH#j^Subgem|9ZTdp=s;kARK6~yWT5uUO2Z>oFVMVgF5_+3'
    '<QikhFUKWXoBSQ-'
    'JY<RZY4nUehheyl)%gho=lc5mcll5w`P<u<mdu~ee~-e<qx6XtT$F>w8g)np-+jl#y7sGFDcWcrh#wD(3-!4q4F8Nh-HNQ?@yw'
    'YfoVHVb(J!Hq7CPT4Twve-'
    '94Q|y>I`cYm5#%@9+}pX}OW6GpKyP)6Wc<M(X>cXFW9?=@!1n$_O#COAQI%O;)j$ZtJqvyFZZVy?!SRq{%=}QfwLJ5E$J9_$R&'
    '2LK_UzY!&h_uP=UF!V$FUl-n~qNG`6g<*YM=iU$3xa5sBCj39Vy-*Ndg=eb1KSQBF&-Q_B`R*DZ1GomCP0_25kX(R~k>o-'
    '+1wx{Nl-rvihdJBtESG#%{oPGJ|uNt6g00q#up<gFflU^tR1(p-'
    'xqV$T?q@*ESI5sjHz3{KF;wQGX$3gGhHZ|#g!Z%g^X!O#nPI0Esr6isQ-Rdw;!CTnXHM;y345X$8VBcmDN-'
    '|@OSARQ<a^Ut`tvqMebiE@tnNHHEYWk><=uPS%64*sUkqntA$z{PmY<=_7!%1fIlhI$Ybm)c+yPnUcR<FgHnir*oICI60{p(Zs'
    'cE(3qO3!<sXt*KI2c*gTt?j4*#}wHp$2<JG%{JiUL<YL9is?%zOI8^4<AE;rWA!mq7*KV~D?_A}X_bUKY9cYCJmlQ6)E@xgrc*'
    '6z>~kFCmC2(gBbxd#Q+a~)c-h=?M<#gcCQmAQsxe%F-$0hk#)aTBdoQ+j!mIbdokmrpRB-'
    '&st(g|4gjp$Cq@3~qGvPUSEX6){Pi;P;{Z<8!3LFK9{~26frpiDFulYo`uALpJ#9qIA+_*?KLwaWz9>zjvCoEvPyf5$@8Q*WHb'
    'MHrOn|Jxdzk!5f{2dyIfZ!<0s$8Tr1UFBznwK{RghUfsg<MgYr@cGabb?S9$N^-'
    'aR$a#NTgI9Ry*)wcBKIf;Cs#tqIJ3Ek8v}GLMH_kAE9Mi#B7tYh1%*qm@)!qNk!L<3EYAqO`ZEHy2^PL;jAx}Tw<=yI$D#=R;r'
    '&CtaPjK~fgfeh%I2yXP!$I+wnPrjC?V+*$BKe$sDVFSTcGmNd6$&3Vd9^Rqe4DV6@Q2nh98}T-'
    'w3WND9zP1@V(&2eUIF%e!y&qvnu~kLKe^|vtMQD%taCbF9_%O8#)(NMo|F~N26dOaJH~8H=J+1BPzj^9-ZPv-'
    '%@#6)w_Nfa(_oCs8gZ{(0dm+UdlSOW5qj!q*TLrNR5Vs7kR7j{vOq=Ey8v&(srB>vtR~R%Zj^Ci{>#k%ixG2G;j<Cr&m<nlpUm'
    'Wqg={ABQ@Z|Ub<Uzs{iH@AMAQm7R6Wvq23AejraB<2Z|lwvYvtz;m@#H<ZPQszZk5^7jPQ_EDfZN|MtdB_yPK0FPbaI7noB~F-'
    'A+v@IP?2NgTI+G!~^ve)%!~_WHQW6>@n+#O{!9Zma9H@6E^1egm7!Vs@@|^wYC9@vacK**;ANZ+Gb3=mzJT({^Y5Lf76HUOT(m'
    'lgmkda~5HL+OAFph4Uz5G%KPzGGeuMcu3ajfjPKVoDZ4C?z+2p5=4XQ&}qREe5%j8F7l+M#DmrftyUW}Zo%nqU=wE%%H27b+lz'
    'INPhKP)<i4<WW}Bx1YT^4ycoi*w;f|4Piyr)cD0T56$Mqp6dDK2fXX_G7;Yfx9$B{If*EfQH%cmOIMwbD2-gFjcpdt!s--'
    'C_7s`{?7+ok10#FPfvOcWU-JdSaTOs8FlH-NKN0EVgq$9<J{C40v5#T^<zw%8Z>HfEwqoF-mmcOANC`?&gkdkMZLS`Z#Dj-'
    '40a#O^u^*cLn-&7^ozQOq6o5<ps;7)xzf8*KR$;dy)O?t7(oJmdysa8oBq8xlj!C;cesT|Oi)s^`tN0BWJ5hXp-~AP>1pCsI-'
    '8iH|?FS`Q#?%D?y|{GQ_andk!b2LibW!9%HWEB1c}g76;UJ=+*i@J2r_YeK`gRx)Dbw58IRT(rnpa%=1Eto4WnalB0blFFbZL`'
    '(YNId9;SXP%O2y@-jW<8WKK!3!zswh=ru+|Z4z&rwF<gm4V%q_-mG9#6Y7V~|XWz<C|7CQ%TW*1kO+ydEM-'
    'K4Qj{=JQBqe#;TojEWLw_9907L>cJwMlj-J0u4aB(>$>^FM}P?=QS4n26$`Yn%^|1-'
    'eAKG53*Ktyv&ViDy7waMgiQR#|H0$I%nmE@kU*wpK5Wvy|>Bj8G(CwvKuE^8%>GO%B!zY1njQ3LNq)qdA#mYv4pG4PfplxM*11'
    'jXx)xt1RqzxU2uVNqM1A$B}=b?(icO@nHjLW?-'
    '1mT@pg6$jA{lrp$46iD8mw3`}&?zW5AT$bZfm#nbH1P&t#X5CxOr3Hxv3&P>UY1ZhhdRQH{!q@=-'
    '9Gw`NlWej*x`9j+Lk=VqRSe)ZB<q2d*U(qT^{+Tfdkh3PPjX)ThxhX%hVIE=ra$4@ZViG+>CTWFQlIVRXhiB0X|rdb9L0><8bP'
    'o!UF)kH7f0vi?EG{20>1mJR~O{UZzlzNJN_tW-YIC?<%S?rPu3L04^^tPkM%tYqpD3@6!-'
    'Cm++br?DS8TLi7@8Uk;J65SpuD|%6Xf>JLkIYGpv%vu>y8;)-HxjG70_#mkEiim)Q(JIEKDK5-d-1cimC<-'
    'o1R;)fhcfeWlD8P^e<4_d#+c3=nuUc@(1e8{P$BU1YB4W%tenv8fs`z{&H2lB^a&>#iEXY2Z!`UF<2u0m4#DnfRcO1SFA+5jE0'
    'GA%izh=bQjaHCY;q@ua3QD@S?^XwoJQA^27Xk{+z+1#%BR&fU`Its9`9XtM0LxCnoCI=4&n0~UZV5Oq#O<Zu=|x|1zCL&bU4QA'
    '!GV)26ss<A;q~ov>XJ{<!^a**jx`Hvcn~?;U5kTHe{`0G`8@D;R;LNR(h=6*mmMulh2~rfjiI8WdS`vo;!BX3A4r54LcY(=&3U'
    'wOWzU~rxl3A)_DUjURv~PX=3^fnH`5LHe|A{r+RxFJg-<yb4*+uY*Hi<ptOxIn#RY22#e8OOh#5?V%2gaFyhIy{(En}NY-'
    'rg{bkeG0ley%HgfKe+9`Yz*mdSvWg;+30AbEOFcL7v}#;{cl%co4k71%YV%uAobx)&J<od->a?Q6SAUeh0G+J*!cdIo-tG`qK1'
    'V5S`EQn;){o{B3}9JodLdAKn?^^|G^93=ln7r#dOM&Zb6QT!LzPlrt{UKIVoZDhlgwy}-'
    '8^d@(=35M85Uo}r<u9qE=V_2sh7<~cvA3`8cpYjyo?R{IGUXBg|cIw+&N&k;%n>LSCUHkV2sm0q@U=4lqt{lsk9vWSm6gmH|v}'
    'cYqis@IrJtmHzV~YR5x{rl1BEF33Vp?)Zoz(gbZUtINM3$mEb>C->1JDZ2Nx-'
    'o`T~*i8Y^K}sW#u<k5z=Z7R>_Usx+bJOPbn;J?vABL_w*)5&P!((1er^CA<i2=Bu0MuQ*Td1-'
    'gKhyMi`r3!Xke+UgE%_@1?f%gO*E2JTi}5XAj(7+_Ebxfy;kQ1i&>20spO0C$LyF-ZuhEIvnd&fBm|Upt<nrta_}Hz~=sxrXld'
    'v)463H<<$E=jr=!d79`$TK+o*4tO+@Z7vKR@b%;^SOtm6;XF+3zzoySUuhAvFFt}s|0SIe#>-'
    'ahHNiI2njSrXG{FK#$UOm@Uh1WwMdc0F_uHU)iLg#$s4L1AG(dA7ftFniuB*K6dxnZ5<e@*d20%)t_@yA)X!4+F&3cYWFB>qza'
    '4yU)C*oW3TDkP$>ke4j`Y@N#^Ogd^G^ghFLh2-{6;IoG=$hDhqqz!?LVb%LNZC-'
    'Tj11q_e8h?dH+qWL21YGAvwWDT5#>B@0(^#?nm{nn}9?{+sU}9O$%q_#l@9xMd-sBJxd^ay9NqsARJ=5U5E&=l9)(ZTl!TZafT'
    'h`B%(P{T#SN%)r-'
    '&&NpCNYH%+!&eJKxH0aM4V?woo$LCs$6&9Sz23VELe5{2ShwEzI#HOFdvG&0({Lx?o~I|bLK!zzLCrWB#f!`*iPrR=tdSaH#>8'
    'Db=*AX$#{1lpAF=bQFt+EQNl5L2Gy}F+Cj~9+?fGfQRb4C3E94u=~$TF*A(`-'
    '70c%HAylCXBd`B^RRc~5S0NiK3Q`v98k43CrJfi3A6}8hP74tdXnY$+>&!rbV!{kT7An|>H_0&4B5ZBH96vKI&m)-'
    '<u{l?lnRcVFNK;&-'
    'FcB^`<#yKACyu1&*XB(6T&zDhA9}Pc<VGXPcy`EXczv<SE#s|OXYJF;7j{10Yd)w6IAYEpSWkFY6?9j20{h+6EZzR29rNyEi_`'
    'o?`5FDy1fVQ59HPM&dcjvI%0E6uB|vm02qfj}3QR8fPFLLOM8Mc!+m63*L$j~oU;Cty9`X=>m*A0v9{|&xt`QzC@vem7M^)Ioq'
    'Aq3Q;f%;QMwh_ln@O7z6d4k?%&C+CZAp)R!ejm>KY%h#{5mbN$+;Cb*Edvi5E0_geK1ir?Ck0KO*bIiPcXB3^;lOQg9#F46m`4'
    'S*EN#A;qVAa2Xmj#aF@1OBc;{7cO=SFDV2_nGSqraX>>T#zpX?P<7V_sAeC;Vjup~u4nLj|wHeuT74*hw5!YvuIyRE=N;yr>wD'
    'DAy%!R{>rNHb4>;6Rnd8xTEQ3*rsCcG{sdSq*4&~K}d`>ZKbEK=c^&aqliBDcQYdHx?wcl~N8(aVZUj9PuH6CNK0)_8O@K<&B0'
    'T$7U^&aT=uiflLyN@mqCieb2ZYWA0Hz9QD?#XJv36_nHiZxz_<!cA+yS!3J%ylV#rL<|$Mbchq!FGrrUQP+PbvlmwAqh1;@X1M'
    'gPw}^6+CT}+Oi=zatW^Vs0F>!Ra1XK&9rTK$otDZ$Ycx@qbiE|qA4HGVd9SH+PCz~8LDTo`@8IMCU+=L_CGs85fbECGL3mR=+8'
    'Ml2AT%9YOfzRSSpRXjL_3lhh`rj<YM}89p2?fZgODL`f``(ZoIL1W!{g;UbI8sgy<}7+yT<~>&dGpsJ8+ZSWUFIBc$1R9#F3*s'
    ';7%`ZbU&Ndi47r-szi8B18d(Qg!(aVuYPc>1a;}_oOAg4TvS3-gUgRfQGF<_$^KC`^Ih-amagI^zAsCU>VNE0tb-lOI4JC}XqO'
    'dcbym4RG{*av|$~t}ZUx$JB4-O!<9Vl5*^O27}_7EAF(!|ESZ&Df`X1*mc6S-L|wh~;v2oJ7_HYB{NmW&PKM9V|Vpvs}}-'
    'vH}E0=w7fFSP3-'
    'pvfE?gvM>x*6#yNvwK}H0)zp?a1^S2d%>iI@j}x&+emZwDG(O39wx!Wn$JwOK^A&`D=FsD_L*F*iGy9F`O3b*|4HvSq?!y+VB!'
    '19k@#nGd!+kf0=t>)wI#r+839~#k3q(gDdf>Dr}Rt>Tab#f`lBT5%cx#3;yor}S`?H!L9S01X!HK-CwS1Vpb*CSt9e}e?YsWZM'
    'A*O;ce7?evMpp9IQ#wg?`Y{aJ*3UA+#fH&O11$<0>OfS1|uj1<|=eTU~sLMhv+qLicfbXw_dL9AwFRBqey-UaWA@<`MB{g(gn+'
    'Lw}Rv%LHw`lR_kd^DD<P{9#T7T53|k+8}2Ur{Q^79ID02qAC!IVPb;=3$P~jKY<}(*p2;@^|DXv+`%g=$SoN3o?1&BY!`LCW0O'
    'xP_=OAbmhwxMbM%1Hv=2#VVZdoOUD&WWTJ>-j{w*>|jlgdG>ZSU7V(MffIgNo67Ef1I9GQfHWG5NNU_?1#g5i4Vmtk-'
    '&qyjTlVOCX*tV|YWK&MGLAy0`N5F*5$Flh258B1vqeg$8lL<$Yjh&9=atg)ro63GTjZV0uCMk&QsBko3u}03}ht50Pi%>lZ2Yu'
    '?R8XE#pbtDl>d^df=2Y{8*4OxFvM&RRp^cJtSW)S$x+1w~Rlx%kiE(N2YV;R|nhuk5P3AD(BO(dPQ?8SvaQ1fs*N;8hFmaAgC8'
    'Jc_Do=z8xquX0v;b|FEuFV>GjnQtfLf7>-'
    'jj{O(;}UupjHrsr+6h=f_7Wo#x=+4c(`CJMEaROEF7w<xX`EizBI?^{qrv82cMz@(Pa2YoMcV<c4ZQ9UogrVEX)jm~K<rU;G`I'
    '(=6T&=Jq4Z9uKM^nq7AnY}9&0E5R+E{zYcCX(WK8&<p3dl*fedspygg3sacogj>;n)o8K>gJm{{H_k7O6T5}BgOvh04gSLLtgk'
    'VKre4A;WvDu20SjGA*0;y5Q>5}=OEixY88@tV}9Rcx{KfxGMetA4Y0VjotNf5H|KyeYzHnUKv(y@HN)p_C)P0#&9^=Q!b2ChfV'
    '8M`aTpf4>@&>zluHAb|E%9q|8WXFk|rf)10)bD0!RkONTgHV9@06o3H=>0bJJgH)V{tGAN6^AOG@UdvdnyAgi1FTg=uQ_%}I4r'
    'Zy9-'
    '~M`l~fA(k@?cdpC&KhCVN+vawM|5Q717oo~3$~fjnMi9pVaz)M&iabqnPW@9)$+xI>wHW3^17OOaZ?wqK8#j4J_koJv0V@q<zr'
    'IbhfD%n>YVLj(vaok*$Zy43O_f9xSA4-f5X7MEFJ%}FX9$#*LYq!vL6F0>ASJM5LBVaT-;7V)c$ZOs(Ri^ZtA<pNz+-'
    '+$CJIrQ*OV{N_F_x-;{4#<fgX{@cEx(PWy~N0_ubdgv$dq+u#)Vh5~3m7LdP6<k&}L-'
    'qh6j6?qU6YB(PW)bLV*JKknDC9f(JIbG)Ie5UT<fe^B~>$4bNul=so^*vZ}T5nvT&WJ|iO;Jo2Nez(>))dA9kfT9YO+e^inya<'
    '6qFSlowWqUK&>)R_IJ56lk%L5i3<LVYsk4`PP0crw2KJn|$d-VBjFZkh(EXAv^!KyAPyXmd{e`!>+k~ePSLp5qQy=eA50!-'
    'Er5;b4qC&Nf7?=fQv=JM-RS{?6QnFkgBl01SRev){su@wzM3(2%b&)3I|i|yzUcNZth6`m2q#SI^@?j-'
    'm<V4PL@3R4@UwfH{~nhGXa*cM}5W~tjWYSQn?0bPonFPWui8IDSLMy4ez$q%E9k#Rqp0LhT(S<dD{zU`_$6w|CKyiqvMWeG$VJ'
    '`RP@>iZ8J2iiacK9d+8i2B0~W*(6889;NA%XVUwSWUlr5xB+D($ly+s>Cihgs#sk)DQs(B8>BPf1VLdpnIts^s-'
    'Nmu3{#J<CEc_vr2o=N7uKImg<fS8Y7PpTN2UM?iFX=H1M|K{RM+mEZk#g#lM;DHL<{JUwI}fJQ?zy)Z0q%NRD+599l*RHWRIWd'
    '<Vq)DO?rVctL_&J-'
    '(^@rpxM`pMSQ;s_5V3vSCD9i7vQHZk2&8UC}MwYDM>3+RIIB)uHA6NXumBDH7u|$|v<OK~IxHj)V@<@7U$=_>G%bI*MQ}JQZ}|'
    'wpb&t{#q)PB{2k4$7iZmgwpA5yE2+R=<!yhHtCn<$+psl$?=d(gkna@^d~XHW<0Qi=lNzV61Q46U1%-'
    '^#nVnPeR>pyrMrknXl8!%RNrSJ*_&jZRQR-q<ZjpeX|4~kf5SqB45qW7vyT-e{|((p1Y8EcUyK8of{-o%9Y}-X?i-'
    'Kc!wl@s@sFgH&v<S!N{^MtrCe~rN8?8lz{?>ggPMwOraJ+_6WMJ|GSln5*Gr@Q_z`<ZtgPQW(=X^Wtc0N2F%a3Rr_a$lKcY;;j'
    'Z@1TrIo<P(%I-'
    '4ZAuohtDi^lPTvvU6k61}i(?1e?$zK#aT<YNgC9s)_P(%pA*i)BYEuYd23T}<xU186Em}#$Vz71+AWGudm3lTo5&zq$$tG<P74'
    'N~og)*`9VqLDSVi=?`b3FP_yi<ycc5=1!xk|07>hm~I72Udxzh#~Hr9WyvR}+*(t1+N_OH@Vr)_>h8HC=q(BDUy}1n-'
    'PhOL~Fw@Pu7+2J3{RsPcRYSKra_VdpzshThMl4JMGCavto3NQn6bHvV5(|AdMwS}b?#`0=8n)7k1w&KOPrzFwGx3tNo@X+1Sz+'
    'Av@$omqg4JIn&)(!F{^xH7KP^Bg~1A-'
    '~CWYuEHl>wpt(>QQTG40sF>?GU=;J;@R(C%PsqBaNoQhV7a@_a}(H2CM6ZlMXcs8;4;3HpzriI@iL>h8S$_Pr;WA`}h$*=x&xk'
    '{M$SPaAfgpPy#z}8L-'
    'fT<=_%aR&QB8PP0{+b8zE}T33X#>yE22$%eVhBu1AJ&jzUl!7PM4KkgK6^Q{SYFK`TKqi(unnqJOD`B9T(%>C8k%wvSF`q5K2g'
    '4z{?_WO*#Nvf2rBzhfq0@K(BH|dsf{YU}Yjlh1g4dnd5vuOW)&6wPW<P7al<UWDHv=E<1>3&85pV;_+=0_3Z|I4(e2SIhkLT@@'
    'GAGX^H9~?j22?f=gVlvKjFK^0MNqK_(RiSCVUqauc&oKV{k`y;)#W(#Bny)A^EcnXg&OOqZR2%(f=I#}?>xa2e10U`;uvsoneJ'
    'b!#e2zRrFdOo>Z{JD^o|me{88%Z$CCYrZ%tNM!6Gzf=G4mX|KQ|17m<AH69q+=$+Fy4KnKY)dqcsvoeWa>Ks3!*22_5H&nU7~p'
    ';Fn6Qme*+cL)t8k;HN=~GgQ9lErU)huK3Fbb4%i{Mn5jh5-'
    '3ZTjl|@UF#)B&<c~agcM!QXYRL$Kz#%|=3@5Rn_@$*Wn`pffIqe~*j$^m=$T{1+7eNlhR*e0kmhQ&+i#gIYKBt<OEVev<QR`sm'
    'D=LyNQkJIROUV5+Bd^tgcwW|nvuK+M$YI_=J)+y7)Wle}54R!tO7AG%;q$4Ge_la5aQw}J`p(ga+QSy|SAi1kREI}bNVJy7C0x'
    'r(qssx0HZ5fUnied~ok2I1y5{8P?V4KdiFl<yNGcWkf5ereKHlJzl020G2uylpD9L^{M)sKwGaM;E%0%i2i)b=ShhS*2W`c*58'
    'jC}83j&NeT<+?!){RAu0Ei#W@Bu&%eCxDtMMrL-O(QD9S5V~$93Vy3Tb;hqLXyXS(2C2JttCAFZR)QC!@a)1s<*-'
    'U#PObON#2UWS9?|HNr!q)XA@RC&BAF~%Qxi^ESxZL1Qlp{hOHv&Ke(ID=3EHw9-'
    '^KxLw~ovUFs80;$<(;7lzn56c{h1Ry03m0KkS}POeIuFn*L0%Xv6RnL>db!(`5J5rh#?^8kZVQ^fZ2v|T1RixDaWOTzDrZQp%k'
    '^W}f-'
    '`)|@!HFO`%Y51N>(f{vkUnHZGH!O}?TgE2XE**u>96?28z)N^`uhis%)H|5o7C2=6y|G%`UHf4DwG<hH6h^hk!bE`MRTTMXYC`'
    'Kms@5=;U#hCRp2@PWE^JHmwAkW{cBFbhN}RdE28_vW!Pucq1kQj^HhctDTC|G7&rD57RK_*#VafJ*idp(L<O9=gWj66PtmtkZ9'
    'oz}bCW|gAF5J?R#gpO%Di{{4lKJ*s(W^Odtap8@M+*lv61<cEW{a)k-'
    'IMefNXtXttd!^Kd`a)ZtZ`Sj$V8)3=pNTO4t3qym14qnprps#=?m@PNnWFmh_|CdHMo`nzdan1%>C1-'
    'd_i7_Le*&zS3NXh;oPqaW`mt+OsJ2xcL91&cpfLW1O8JTL*#N96j+l`L{b05i{BM*VQJm@TI(|q!@rKIcO>ZImrEYya&we*|JH'
    ';U&^FC%e}M;Tm90`<hmz#=dTh^}yj5Kq0Vkk-FwzT4(Xd@j+t4>;-BXV<$mAs#ep}1Oo5n~B9UY$Fdu_3lQ>6GV4AC-'
    'G&6npwR=-7&-kNUZ)7%b3!0X|eI&?NHgjg+Gb&^S_5S5TQil|sRgZj)wu!1!|#I`%opPR@*g<~W<r4)XU=MnwAVgz-HC6jwSP6'
    'b;-V;T(O#Fe=U^2EL}Lpk+CxR#M8>OJ5^<8w!WpHQvqP0-'
    'ZqZ)Oe;l*te!5Bj4p%Nh_P31a#P6<J7tLRFhN&j&!fqoJ2{yfe8NIA(B{e_UsRti8%CV>2`9ZByrC_i6p!rFZk9UAjl2;}uM9o'
    '+3d|Q6xKPci0GY#(0m47UL~@ULt}i=ieQ}3*%uD%audhYBhXeoR5j05c-9-'
    'Q$bGgv&DlLmu%=1bjjDao+4m>o<*J|h$vWP8(N89u0sY(&Dt5|9rGvD>a5Dxf%2%{)LcONfz2Q=XbN5W6=7kEPe+=1GUwI_AA>'
    'KPbce$D0dGo@CWn>(=D*VZ_{J?R3m(u$Krj>C#x07oO$Gv9SXjVFJX3ZJ=~fp;CIJFbYx;MEdGv_m?X22_)7IYLj_xgZPp@85X'
    '(K9<;xd2PGt^>rAO&v0DG0x2lcX~e(V;zT2|Z%f1;xl(?1}x|?CfxSTLH<x2Zmxu`U9x_l##VPT7i<vPbYYBX}@U+Lm__J?Xq;'
    'PLERg#1kmy#E52dZyd8P|WfJa(4c0Rzup#xvz24t|s)~6+^YM{2Yo3;Q^hJnrx0a}nEPiaUx8?4kZOrr7fr9xq&alL0)5gOkyA'
    '<e9+tyxl2cQ)$Y1e+SSd5#b$hxHbDb8(1x!_=^h8>0{&=unFW1Dt*>*h?r@g;U@P*CdkLyYGX;`wywVd6p{i>T?KdXhg6?89ZW'
    ')IpZhdKcp8&?@q4u}Fmj7Oao6TRnn;m2DCU66dpVor~_5?{bzI=;xkcjsBPPCTz$eU-'
    '8|JSvwkGj@VSo)%LmOE?Ka~B{5?Zzq{zEiH<!QVxq%B^Y8YU1bV*{Y2xNe4HO=%M5_~rPXBB<dhWH7ZG|C&26vP9{&jA$!rcvc'
    'tuGw@Xh%kpPuw!bW-DB&$o}$UU|_5mOiz*2D&_v<6m{M)pZB#N(`YLXmYDox!JsnEt@{yTe-dN?Q};2jR&NNn&@vRj3#5J#rMh'
    '%hhLu70a<symEGcCZ7^wv`6Uf!Rd9zMZvl}m4e=Ub%C}k@m34@S8Sji@w`>O@F6g?b#(+Ol2SL#p4v~yM=(_{g<>Pk@snGOgd_'
    'RSG@JPW-'
    '9@v$QF_v2|xAovyKIBeCu&k@Z9L4JJ}U;fUyi$yzc90VF?6{<St4E;&hUn={50_>Y=xSrKu7YR{76wqOACq5r{PZg0{qL<K%6p'
    'f@or%ko`fB%J1EsjN)w!H}yax7AlSzZ=8H14)pf2*exHH{9B;1_y%@MOV$<$(US8)+s@ty0tJ{iPifY?hlN=7(T~X+zS3Gt6^z'
    'Y|M{B;zf?}VAv?B>;gI)Bhcjx9J~W&<dfs@_fCiXS1ayoJncDxamq4RM8MxMI;jtx)BD$C_j&90B~RQ5#oDW1FJ^8E2bo{bxvl'
    'x0fiO;d<}yC}oL!0erD_*Cn|NOEpm=vRR!2jZZxEN4M|Ddz4`X2vuL}rSlBc^S$TJV$7eRM5PLNh-`Ao-'
    '9?&NGgnQrg+rqZ>e0@G5hV)8kT-'
    'f^~3B^;aVcUJd`oz#J1IJFVxA8^l`sAFu{^WH9DR>#Ulq<#Ee759=HV<>Ah75+cRTE6_Qa`2zYnFfv!=eo2TukQVtGN&9}6ZCo'
    '@^i77daQB6sk5@QZ1KYx+S<p0>2ZKJP3oh%Zt(;mc&E;`1XEkY+%fno}P%I8-dl4y{5X*qI-Dw`qYjD%d%L6-'
    'vn5G<U{bqMaYPA1e0)NKKJYjH{TRG9@xY%SCRdXj^h-'
    'Z+cV@@Loe$7!e5#4bg+;I#ILb6p4CG(Xrq}Db359$klREJItZ|7`?jR=!*mbO76n#R>UG8t-Q3^m|&L|0}95`Se29lzA-Nz?38'
    'Gn#{V(UJhG@9?t!Lqt<0&jS?h>R3u{h%*(DIife@>E#~EEpQA=GK&Dn+T20|MCDS5tr5yrR#84&jV?1<ET*XOR@m5n^$Y+eL|)'
    '(pW-|rPZpC<TPxP(g-*-'
    'W~M;tb?l0TDL@Ld|m3U(t^G`Wi{*Ec59K(ep<?wkp2oi`vwi5xT1g6L~B!Y3nrU*oX8zz}Y{g)|5p6Q9a?nKAf9JgT6!P8{j@3'
    'QP^!6F>2}YbW?Gt%cxS`iyJlfxUeKVbBp%r_1oH7Uj>b44bms7_qe#+q_R6A!a<`h7D8PkYRg}V-'
    '==(Ug5WFC)s=3EDZpz0Y5njCb`=^rH}kFc!_=R0J#;{WOz9zzo;x<c&*<FLDQef_`M8f2Jq6l*P?-II8tH+^6Y7o-I3Wr_b#I-'
    'Z)%wiDE_6$b=$J`<KciftD|=mV)#xk3`L1P!2F8Yv19SJh#-RK`wI39)j>n-uDxlxfSJThu9Jx-'
    'u6Hpb$){?CUv61XU+aN`k}x=~Rye8b*&CefVIPHvJ=-<;R-RuI&yfnY^03ET-'
    '6&8Np;o_(6PDcs<61j>tQ1@$LL(4hJ~>Zi_^jNWfv(xh?C)IGl%T7zm~9<*;~!}dCWizFj93xA<%e6Mz1{tQY~70K^$jRoS#!c'
    'a=vqS0)ve?oNx{TjKSIl#`io=BDdD~wp7}1%oVZt*kc}aJzd!~F;$$g_)Y`l&xn1iw1GV*8-'
    'e!m2=nS_zHN<ZM6ncd3bY6)gGI&JsWdp!iGg2>ct(}uwU);pktC@=2tM=|^r(8MtkZI4(8>aH)JMX`h_9m(%l$&iG)vpowlv(`'
    'm3|boV1@Zcuw+MP?3j+JQG2;FR6Ksp>RXb+oH65Tj<nP6WI(1_o%!ULBHh8d%$ICkJ7NE~zrLzTmA+anI?CYW^(j&~cqlklUg`'
    '4%|i{!62<LmKBu=iAjgn0g$^VWdf0ER(mY!C=_LqoVCoH-}ggudhUQd#uNPNq*Lg!LcnaniQ)DezeI5+d}D2?I_mDlcWZ-'
    'u3li{Yo$;w^Iq+igHk;6*BQ$!8_RSczHmrDUWgDZ$P|zBBezj7*DoGi_H#<0|9nL0+9#sJ%of{f*G5b#O8I3cqpeRiH?E7k-'
    'vI{3tJyMT3|+MQ;9`DCRFpVuF%Ft@(;x+`q7S(98WP9x-'
    'msk?$yDG{Ukq{z5cM;snqa%dRcLDVj&`VD3pN5(=50c&w7q{cD1?1bsdtSe)s@}`;ATQ7M-'
    'LN!prgh&gfJ7hQRsT(2qcFrFw7Kd@K?0UEhHJ2>TU`1)bAc(>=${#=sr5$6#KYvbqFew~deL4V7xWyF~Sd6|z@xi2|v|=#QzmS'
    'rtkW&Q?DKMVs(m_p!T{c?7!?56@9Gb^jv)yh*e@4Q`qug`$BOtC->(rN>MFJa7P$oeCk`r)Kt57Db_`wPz$tpjQQtR-Oe-gu^X'
    '+-;HG)2&B)liN0+92=&;FX6&3mw6KhQqKHC?cUM8_-'
    ')$uNo<WF@U&Iir9w*nNalz7}8ia#rJ$v65M3$v(Q_B%?mnKUK$;p)8cw5?43b>O5iETxeN#-'
    'jw4p#yz=b(YXJ^h(cwGQY*_V9dx3n0gh?a-'
    'TlEY4XM)&M>Yx~r&CqmCP7Oq7{9xsDQWe4pdO^$Q&KdyiOX0O!Tt1&q%V%3;&Y{HJu&VRg2X&@cQYe=rzyN?U!a;$KV5l}L)2S'
    'hp@NML&-Ge9ZWGgx2z#Z-'
    '1P?b)8q?h`@y0>SURY(>_u|W#vILQ9!P_1V_dZB^)1x4%7UO+^*cFd*CGipxi>;G9jw4XpvSioAzaY44y;&TI2U(a0q$kl*?8o'
    'tsQ{hdd<vC7^(#(YSRws-i!%VdR1VPjWe8|)nA*D^(`hqwv;}X2Mkwq5lrY^IF>bJxNZDjS1w-2DKK{c-'
    '#4CEXp`5i+Xh6@1n0whe*Z_dg%K`h$urV)L=EWB%Ou-Fs*C3m&=S7w{3e4@)yH!Jwd<ezPZ)_;t5FrZO;|G$b)T|`qmdn59D1w'
    'y)HQFV!L>RyuWgx|`AGcIT7-'
    'cUh388^UY6q+;y65W+bo#pg)){OyaQaihmnlBs1R3!rk6EW<1WCt@fn(VA!v?i_$L5*bxa%+p2+O&BZ%t*ya^CU|0DUb(kbvm;'
    'XrG%-IU8bndH;D!~Ke;gK);Ea6Q>2wh0x7dqp4AflZI7Ol<&7j9KJp($-'
    'U=sD)vs)>5Tl@PX3ibN5(OGr#QW5?by$9xSLP{c$#*Hv=WZaBli=CecMZyoqs1mOKFViQ4u9)G#b8Sy0MznXM?_G{)aj7p&L*^'
    'GW-qnc8Wn#UwTid6G)(&$1kEd{gSIFlv@^rAr0K<%+pqVMbT&w6)A@eo%azBWlQuIftR!oAafO&FYXQssuq;P-'
    'x4@3Bd<j<}Q;QZb@0~?TVJ$$Z&EBdXJ{J3#y^x&*`l+<X3N3k|7%vv?@#C+WxhA@X@)yWW5a!e4=bzEc@4ksCM%Sqo}(WAv#QA'
    '2ub#<b*|sM#YJTQN)R1Wk#{obe^kRRY?$JMlBQyV<WBj@(6^T9!gYyts9_IJ!?nT%G@gWIp1aXM6M!1S*00`;GJM2LS=s=@D2V'
    'YNZ6^LTo#??YK~d7vJ>UcckCEMUefJ6@={ggy>zR(JHFXYf-I^8&MiN4StBL|B_|xCX(t&4%tzu{p71l~WYosS~ulj_-'
    '5I09;R8{I|>M%G2|Kb}9j#*0H9fg93RlFi+#+XD3b22`QvhBCFv{B~+ESnXG$Trl}VGUtWe=WFa_ZhGy%z@VVmfz(R_m251p^8'
    'm8%O2rNZMndw;4i55tz5~YG!JDp#kWTKnnA&xq81&P08nTutkW26voo4w2pGGZifOS{27$g1YCww#-|f`g2_q5RPRe-'
    '8$LXScIr$dk6<hTPSNT0+dOIVIfPyvYSpA@7hLJzTQ0nx^&%umJ@|w*0T0=DsA${1B@|)FkQ4U|#gdNYYaF4?NrTqtKIaoFUz^'
    'hpTrcd<otH4qG>894<6@TzIyZ#xi-ON6;NusIFXzY6`H;x;(M!s7dL#4{3Ifm?tNK#G&<NZ1^9L383^N6L%Y-'
    'Q^mH6bH$zVDe@wjg7!8bb%5dy|Ib-'
    '7MoAK*t{Gk#+S#yw~RTPyL?_Y8}u*_|_j#KD<7QSo>l}CcJh;Zx#MdDwrjW#BmR9ClCtNtRUdfjV4_os!riuB6{vSP<IGIXIlz'
    'b`!OdLKg_WM|G$E2YHdGES~%f5i?>yh!Xxe40~Gqz*(2&(c<<794ZA~l5+BM@RKy@UYkMIuxvm`<@FCLREz##MUW3QfCp>hPZa'
    '1b!*&|@Wh5h#YI)5+XsY9+KJ@JJ(ZpmLDHP3(fgvm2<$h=71EO-l$7~r}|{7&je6L26;cm~0lu4O1Dp(iP(T$V&BC1WT`A`-'
    'Lz(U0A%zWO>^W2$R$TJ1{@aXT|cpjeyS@*H!y=gRECHvAoXbAbme!`T>eE9ZVOrL^s%>Wb$<ys}VTg-'
    '}Vo4C<~T*S~gW@T7ZSRTpqRDt<BE^xm>%a!ixA8s;%If{4oz?_^1WQP1e`N_`RfIWi8>J?9u3g`Hu+{NZfGD`dTFA+r1&%rj&+'
    'GHf=NPHNaO{!l(5Y4dz}BdvTj6xG0J>ukJILR6r}D2o3=wQC&ilKGjr{I!h<Zm9cHdTbv^ix>htiBHmXkpC+>jj<|VQN6#+`m+'
    'kK2#}SE;k-'
    'zr*78?cyZiQ6yCP)T(9(J_?20reNy3NOahfEgR2)5awe~T4p~=Ud3)3a!R#u;jpTs#O1o0WW%x9DvJQ5Y(r1NHw5JQE`p3Sv!3'
    'V?!U99AueHrEc?dV}@l5k|nTAA*j(##3x8p!6JosX3LwsEhdS`~_WJmmjBtEdSo}1ZzUqBr}72CCA6+9dY&|%4a*Y_LRyy-'
    'F!nK_D^1a_6>MW;m&HPCc#eW`#Rqtk?nR-'
    'E8NEPaIo_9os47<rg&_zX*A7H=Y)Nl7y$SsIEqkLB(?4D_j0+(veU(9apv05e=GQ(DG%jfs>Q+$7+m%{KNY?BYe>mTA0Do!fU>'
    ';(g00j}6rtVw`Xu=lM)=)X7mC!MrWUX){VDFNL8#ibXe+ieC%t^&{eWlUO;L3sDsqkTm4R6F$)Ee_3QRrBGi-CLN9LWuF`WrSC'
    'NJA|P{toI`4b*9*X&h~fHf6*#$DO*EkxCG>c^L2f(gBX$8tK?CJ%2HK)T#1N|E;*hHtRg!86cDL(3-633dzD^#0-'
    ')0ghD6$C5Riyk-'
    '>Ovvm<8a1ju!aIBfmMrjCk$%fu{s(JMbk$&^6gn1<(#uDj7QqJ%a(~PdFWQUguY+CCVqz0>rN7&7Nl$pVeU||}2T2Gg^W4=gv3'
    'V${BAd7QEj(Zpq2AtW+880zoKFcu8ErNY5&N7&LAp!qK%_%seT|<auQa`$8$LqWGUy5B-lT~dI(3#=FUvB)!Qq-'
    '!k8G`4@nepO^#)KMRX*z#zYXKQ*Y+j$^nSD~E%NTzK){JM=J~-'
    'HRIj`q6Rkw=kC)y_?cJ*vOX=iU+Y3K4eK=MEKl}Op@uaJC`w?1M`Q6WS&wNCg@=V?%J@gU(b=4S|%s7@N6cNmpF@Mt6695?dFe'
    'eyl&c|JNpeMJSFQSbnU(YatO%(nQQgoS5ns4C^M3;K#6>7AxrczrVK`^%kV6Y8BAk<a)775l*Uv8R)8_X~~x3_qMPec#liD+3}'
    'kx3j~v<6?q7yuF1rdQdNjRxAQ2p4<VX*=b`x!E>t!YF<N&6iNz|bl&ray0Gm$Ufy~EQIU&6ZUX*ZT9;|up@a5fgXe!Lavn}qs&'
    '`OB1M6Ra5reMq>(A@ufI5b8G_<v{lE!xP&=A;PAw?}(?fyNnCyvgveA*#@t<A(c!1^oY*A!4`5DHddoqJBjLKNcuBOw~Op_Wys'
    'w?zvK_y3RJm?AtGTmj(xTkS9dm_`;-'
    '6CjHVDP2fgUMQg&kMPJ`cl8}#X7&Ue421fW8E%M9(KJH1S8HtzJ`p|nZ!<wQDK09ZCQB$NznyYkEou<(#H26ipPgsx$k=i3Br?'
    '#5JL&UOZeB}T3vXlx*wTST{n?cq&m$g@E=H7XV@6L_Hto*)1ya%BLy9v1h?|#QxS1LeV}W-'
    'kNel?~1gr<iaAhJ<WeuK1s}Q;~;4Z!EbhN&O3Rb=@AuOhDu{^f|QB3qN1W*7ye~*1fQa4t}l#)@f1Pwy;N*pC+&14~Oz}=97lR'
    'b!-'
    'YrKHyY4x~8LXh?t*zk3woY&|%t@34TLc|&oJ_pMohK)bKD9uj=5+F*W@Iji1%cTc_o)U{vj)re;%5*qp<mCt}ueMU?57Tm|*B{'
    'KR5jkgnUE!B!Qlyiw5$OQ+oYe^Fa}|J)bi6X<c=dv93DFdZ@z?F&9P4C9QSIMX#`@SH5wc!U(dFJr%y#1x|6b*mIl}#LFYkd3G'
    'NsmFKGWt8`4nVa$7m!SLax98%-T*^c8QQ~s<hdh<TQQ+NNQBK{m?xTU)+^`D6fVe@8Opjql-'
    'a$^}lhxG~n(qF4}oA?o3z!bXU6CRw)iYM0RPEtMtN989pUS$a^H@Hd`PyJx=m>)PMsX6^Qpg|3kIncr2Tf0g#@DBCXr(WzXjMT'
    'w$v80xT@R1>vINO3$d?sl2?yY#dU_ofPTo3xl{=soT)S3@L6XbZuhjk6Z_5X9A_}V_fAhFxx5($iY9V2G2DOul1D-'
    'mpTI`r@59aclj0tWyuQZ9=R2<yU~gxN<W4$r%NlKnDRyW7x(jLDR6x-YjNNat^Mb;C+9)fuA;FsKDKG@6Y=-'
    'bueu3r#D9AOF8tb{-S3)gO^+-'
    'P&PIOj#xuDwmB6omD?~1`0H)cgEh%)PM)5j~&63L;ePq&s2b5PnxLh6#%C(xYwpWqG<cn&8NpCeh%RK7)YKhO>;ZXxS7EBpt0K'
    'cW7)sJ8vrE`hFBd8yy?IoiiANw#CI}h>OvFOVwNi<PqZ^V-'
    's)PTmwmVx4%nWn#J8eR9h$LW)^iK#_RZGi3{vmTZOW14!*uTO^2CpFG@DH%FcB-'
    '>xy^prfQE;0CIOD$E)L3pHqsmAPHtLQ||^8f-'
    'H=E1De^Z*;IG8s|C?z*u^=|IeWmTSm|3doXkIx4Ff{#r`sR5=4T+Na*kAAjZ9e|&Dh3t38W(^Ig2zR?rlsa)jua^T-'
    'm^(<7+42)><Fut#{o5J9l44h|}A9m8;H4h9*5I^!p!}iwGH788976sV{yKk*q(Ek-FZ&27VF{;V0Z}#9*=d>x4_VlFUZ!2E|I+'
    'f-'
    '45QK^UTz|dTR&$|g8V{l6&TrVt;A%!>5unH)K|W{ZT);&CA@&8`>0o1F+bE{v9TBPk&X7a$wKRE7b(;$#_LvMAFZuKQE9JQ@X9'
    'Z+Z8s=TcIfLEsC{+)ldJp|BgM=~G*H@=Pvk{-'
    '%B}m18`itTyC<g5}AVCt!CkeSNg2)p#!GezY1XDfDu}cO+(S#Nc_IiS{h&QPal_c1@>^_%iD~a=2_@hyk#`YSS(l|ZD*?uaJAu'
    'G$Qqe~!m-j3N9A2cZfqBTL-OzRQLqf7OABw1#8)JeYJL%3Gxi$=b-KrCDY-tzN`vAbbKz&B5vbD+eogdfzgqw$QMs_<zTGKp-'
    ';3Zd>X7smQX#Td3<b*PSJe6F&)re^|H0~KqTIXHCxTT_P%(=A|ol&y-'
    '2Nw^GJn}{Qx1Y*9aNpDcayL+cBoU!5De(M{!ZtH&QD0zmr1$xvF-kVd~z+umkkA~FDW1}rz8ShlF7sQ#NWn6zld#Y7*-'
    '&%{TNggS8{+@>F3iJRygmb^Z_mpa#I`7Db7&k5dhd)QNG`&8!ILqHBOaN;I79X+XqdpN*9l5ovH$Y-'
    ')m1emdS_ikHJo`X&<C?MTA5s|nIQe;C4mck-'
    '{j(5=G+lapMVqbb={j)(Paj;mXItKs@CrYrg1hcIN1a~vbtwK9SEH?|dKB4cY{Tnr&I})jx!rXb+YLU0>lQb?zoJ}nz@InBY%k'
    '3`jnfG_{ng;f7qWJQtpko>1Y7may@a?ywsN28@v0MIF!rv!2STnWt+4Jstheg$!_brIihqxAVn|l-'
    'bI6voZ=1(8XNIDSQrF%IchwLe62=M<lkfPcSVyp&j&IA%3%<sIHhR{kK_cv`7vzncYgo#jQ6bUeRe|cQwp_Y41|J>-'
    '%_nVfKPaErzSFpvOdTxWaXz;q`^e0%R0F+IVWA&c5i<!rxvQ+G$~rs6y@DOW#d?Y#&R2W}3{U_<Lfe;v1LO<AduO3)yL!+jf%Z'
    '573)!iJU!Sr21n!J3t<mNj)WAzFxYTd;eOU2J_QfH>WGWsh?Eu9D|CT=3C4E)bUzJwn3l`4L6fnTZ^W7eH1FJxV1=1zVC*Di0P'
    '&@>lvR5S=-'
    '?GqlQduk@^d*s*E!2J%0AP~##k}M|n>lbgIWjTuz#7+!VuCu?=jTv`%VJzrJnA0h@dfh;wj<g|gw4vHR_!>~npy;N0WXOun@0l'
    '#G+W}d-rWt@#m7+xlXXW!Wt;cjm?q8h`yi8-K($YcJZ6ha!HqeVQPrmq9J=mN5@_PiOb!Dt#DQLB4jg7>9YL!71>k-'
    '2^=$w3`L`Wl=UrWsl`h4@-_-'
    '`S))q%Qoy9^h_S#fGZ>g@(7=K3H*_@<^mI8?ElW>Y=B|Q3WrRJ1@OXF8OjYZ<$^X6hn{LwkS&rq+{$;mia_3nC!;6&pkw&mJ`x'
    'xs6-d8+LCZy_pBv3v#OnBnr_@1J-'
    'f%O@KG0BxHV_25~*+>u?q39YB#sFr`Rhl5&{EUo2H*~emS<qI=b2!*Cz^<m(oIJF1;+U;fgJTSbVYFU&e^cO&Lv(O9q#%i&Dvi'
    'TAR927GAPgvhAyXl6~AsllzT9=kZEuo82W7fgj7d7=w$X9+hljPm-il~VK{s0AdGcAKToA5{@lA*(&3PwA#W5MgM38v_xMc8E('
    'V!3JzcEE=YVQ2$2ZSwD<xm7Ny>7ACwJRZ?@+wevv=wfqefJay)#<_Os$iQlb?zDMo^PoR|@jcK_-'
    'HxmKpT-2<TX`+ZW3^Dg539O$y>GFw#lW{r$#O0k+U|Vto?u@|mg-'
    'pMi85-=AfIW_F(ueJ!o|ztZQHeW-26Y3v$S`V%$od_9CAVdi-nYzJZ2{+#M{mAzy_JcwW?%#yj}k;9yzsET>wQLC6Fb%V~zT9u'
    'sWSIbllii*b86zyQ<piLY&m1J8xwtm9H=04z%BXqNDTUMT<E<0)O8B3sS}=1<qyH9~o#G8RQz54$y0Ej})fcWq-'
    'd`;=#JN#lMqAWX^;tQD&j+d>Xe7YcL=OUbLJDg-_EpjY5G-'
    'j5O^E<naVIdU^r<WlH~&=ODp5@a)|PoVe>DEFy%ip&!pLxtQ{Zmk%@v{X`X|9St51L)5d3MbCJLKM&ZxZ~7QcNE7pK!`MVh+B9'
    '5^EQ`2RSVp5Rmn??hDm!kwxP3D`CRh&_5z4u8jL|CH=%#}Pd<E#!i6@o)E%c5r8%#3lk-3Za_RdS-JA)oK9W>WPgSMwaRb6)V('
    'l01l$!ArQ%syOy7hV@S`f!g-'
    '8!f|d{MKVF*K|bIII<LwW`Adxf!)L$LFuUMd(KW!3%Cx(?sSZmvS|>ZAC5xe3<ZObLgL!9y+=EGZ8+U>24k}J0tgjEte-uiH}0'
    'qj(t|Sr$WU=nnC)<gsxG#y;A0<*V{8&<`J(~YuKSIUFlYoWwAV#t^}HTAO-'
    '3jQZKk_$@#*8VRI(o(iVpi($(<QeuA_&ZBFlM4f0^|&If5L`&-'
    '3+N)#9V`apN{W@<e)Tz5;sHM+J;SMc`}^g5aT@D%)!GSO)b12BE9KJkN5?FAhl{Anmib_L;5G@(KzdN)7OdZ&`Sj{A{d(oj>J*'
    '$zX?&O^9h$Ml|Q`LatWz-JwUeY1=+-'
    'FoC=QMLS81s9d0bh<ccuE6#R8{eJq*MnDF@hNMAcyHidJVGx0ccpsaF1gN#uG_Z#9#b*g?b)`r(y&RKeE#?#y%gn2MZ6vW&irP'
    'MBs8^x1U!f}TZIJNg?x`7(5qa5c!?xMk1n)D|;{w@>)~^>QlVge^T;-'
    'sJ*Bj~Og>oPWb@;4aCbb_j3|wIeC&n&W9eUnL<Ia5U^#>&~e2p6Go-'
    '>BzB|k%R@#{{XBN7CjaPbevbr<3@?rmA++{Nrpjt`@QecHWnomU1mnZk|$RX&s=VGF#R1nE)UaF7T%G32_jCj_v7SZ|z<Na00y'
    '$TaZGUY<Q{iC}2;xsA})J_osv1jiJ#y{?nN!P2ut+I5aRWTU?@;1)#|Qu;X#9EucW_HnY6?OrC`IZw6p=phCc90N7ngzC^pk-K'
    '-g+ZzTsPqStS{tf!>I|GPFI>M^#wf`EZPhm@T+qiDdx!=XD#8a@DV(@z!%wN}gSzQO!bgn0|wz7oU*sGbL=_UpFNtbdyq3>5G{'
    '5Q85V`3C}%)N{jL;1{Ka~WC^GOv=it>xQu+(gXS$kzFL1B;^WX3Q?s?T3aZOg5;iBJNCEJR3->Zs6W-_G9G6Dh-'
    ')dQxrDlU|}jDGCJdgJ3uFlGpOe~Z9E)n?+hR#3TWm2N)zrLC7d96YJOi}QntE);}=#}gv`08CI``z7S0QCD#PvZ773WbQrF=b{'
    '9VBF7Qu&Qwd#}?2_gAe9@J<ewd9K8c6ni+*Ghbqi+rBEW<<dU`DH3pUs_1sdD|~ShJ_}r(D1@jN5i=8qzZe8H|CXW4v2hMWKR$'
    'nKn^5{KNg?0H0sjm)=U$qvN-q<f5RY|-`=4@O73t+xP&8N=^hK%xulx6BKo68`vo-o9Cj~Hh&M`e-'
    '4<KIr4pyCvyNZgAL!b5uOYiC8&E~$oq3hkB+Ufov;19@_F<5ivruRBkE}@-'
    'd)97QT4m=qoe6P~P<0JPrn4b3Fu?TYict;(SL?t;Bs=6X_F6cyl%IRd>Sv>Q;tQ<T(F(9#KIV{{PV-Bk#}O)M<m?)TIKN`pm}0'
    '@lm=Vi>*vzAvP_{=1Ye(GBd7r6Q^wC~%o2>%}7Weq%gIRBEU5&g@%(_$oCvIy{Ik9xi{DqXDh{x-'
    'whwsbG+N2v~<Af{HwHq!IfwTY?;CMq@A+QvBb50p`j)1&K!|8-L-3W#u--xJIog@FVv}mLw1&mqnqxAh$)rY1DMG~n^je4jZ?('
    'GYn=t|1gh7nCs09@mzUP<O$s{xKUSVl_Sbc?64iQMXc5_v1{om{&r=!L}wRR|1wR^^K_kiKE;te-Jj`e<oEluu6eJ(i(3%jPb&'
    'GEC#-3-uc~4)J#oaFNkjinD5`y%&W>(_o4wQJ9a6ZYM9IB63_glAm*JAqTD*#>YaALG0WWeSHJVV4e|^>bp&_Psz`-'
    '*@Z1|M<|FiJ@;Lu?6^X1xm>#!Mw7!q;fTuDLX&>kyUI_kCPM@@5G!vw_0uX4?WBoP-'
    '}sZ7R}M6I5G;eS#ygz3x5;yIfIQ4id>Sl@U_|y|yPrI@!l!J3WtHgvW{Py1AhT0^w>UQ2dAf!4WFSukFq*fE3Ka_~n8tD;$-'
    'Mjcv)1;jF3gGW&>I^tS<M>J0}=WuvM{*(-'
    '{T<p)9N$;ECa(#5|m_!0mbjaE#e@PTzX@SYValjB(^fSdECYG^eti~MjJ&bx>)IFC|^8JbN=}j6;j-*-'
    '=5uYH4R#|sG@(IM{*H|fEWc4LCFneC3u%7cg$B!jg80ad;OyqHQ+eL`r6U3d@}JNk^7#w$x=K0W>$96&;pkad%EmZV%4l(3})@'
    'aXcKfaG}3R>ngj1;qU=iPUtyP2Q;CYB4Jye<_Hb~CyKlvdbY(j3f4SK=2e}M`a;!X)VO#J2qLIgVSn3?Vpd(`Bxg!V#ZfH6`Z('
    '8u!7`3oRFc8xu&Z>vNKO}S7x+!`lf@(^82uKp1P-CMkS*yJYZ-'
    '1tdwMT_{zp3aJZ%VkvR%)zcc7sims@j{fX~7Uy0S*y!W*pQ<<zd)FiWW@ZA5UB4dWaxi990#!g|C|9Xm;Z7?T>~61Qtk;t^p$('
    '74r1VE#Mic>fiUe&`@vgh7i(x*Mrfcy>4S3;VMr@?HQ<0CQK?kE7*Y~!{+WEduj}v1vTVxq5DXoJ9xJ)$vEl3*sn{56?H=&VVa'
    'zH8d{a)T-`8RPS<xCUE7|U50qeH*XTHJ+epqC??$Y8i-'
    'qaPz0i_8u;U{fx@YqD7AMJbgv`@xtut#2s<FBU5^2r*&{1x6f$~v5xPD4?h$z3y?!hgDXwq=(m)os07}`G`<rd-g_{3tqBaTcD'
    '7+@z6yaZUQ>~n)(gE(8weWDy#jzb4ydojET{lr-'
    '`aiqHIg<=Q)qFtVDjUI_>9jRM}>tXV8OPYByHr(I4Ch~rI1agFr6)7WWUhWw5A_PeAsQad}(gw#xo6m16_;n9?MU#m^fycAh^&'
    'KjYmb>H-M37EiB_il3qY6CMwgDdPM2|4cxmpN|#|Wcnz@Nt4*u)_7(8q@<sO#aCRaijYahH_u)wn>QdlP1s-'
    'gHCjbT8?8*lceC-nYQ=9;IkW_)-'
    '(4FPnNmdE3lHjLWgfPtZ~2`aUsSYx)GzM)(O8^DFYQ?f#mmw12~8``hquUxrP#^%6`NK6~oP;$HXTvKtB5*XN#0oc=5g@!z<Hy'
    'C;M%v-'
    'eVx_e4pD_5q~l8@54+mB9|zje~U)5M1TEX6ZA1+l$?}bSiXNdSM~RTsTWai@I}1FoGI&yk7)3pKA6trr6p7Tw~e&0_S`Xf4{Jr'
    'NuY6B0L)cY9&vP5OtP(#W`8Cj`cVBSr2J^==D>p3Y`uOupmSVSTXBX3Gh3~~0QCwh9n`AU<)D`*?RuENDVn)Zk_WAH?it@?2;-'
    '#vczW#z8r5qARm*!(9wBkIB!`?USn2@6H!la(x6FAc7vH9b#K(lQ*IvyIUuv7G#sV@E%62gIN`6PHZX6cT8@I#(o}n<7@g)+kI'
    'SjK&B`Bg=?ptPPKgkQiDR^IJcH+6Qo9)s3(*D3Jtd=q1=y|rY(mXatJtHq>;X5Slu3z<W2(pmlw3+XnN^dgQ{XP{Y_iH})0f{m'
    '8jJlPk479uX{Lq|fSnEiz3BewQ<<=V|bi0<7V^zd6gYJnBX^hwd*4)$0WY)-;Fl8uw$j=ZdwwsRQ3g^_a2h6GK-_zK$F)Ex_sa'
    '#<2rbK<Im?sAFY<Q%?8`h8Dh;u7H_f!0%nf=8ful@TPpWwBUGDy=_8uK3<y#@ALQ^6r1&Mc*aI^_HbU<mD@sXEM-ddt^NdcswF'
    '=rCC>LdGSi7Z~v)qlQ#m0@etX+IU1HuA5Y>K)4g>xxhCzJVl$9yR}4_EU$C3$<m{#$~FYynbmp;-'
    '!PLt@RXH2y~6+w?)ht1$|oZvmD3_uXP@`CtEeoHh?{1rqE}P*Jk+${Ia-'
    'j3>yF`HAnH*I?8htM=!q{((WJ{r;%F}cK$j6{3dP<0zD?zlsTE?|1w(EtW&-'
    '_<uk8C?OeBmJkBQzVtcswDDFN&na6WT1X?l3g&|$?!&%Bm~9xKd#$@^OWlVNAG@ja#9RN{yUp?IL{{K+xX@cH<kp)R*s&*!)Z='
    'wbypDje1r=s|d&xc(4Xg-~Z5pc!)GH@~^G6>4wZvXxD^sbq{<W8`=Pddfvf{@-'
    'Vi+@qknkNYB<CpubTdAP0Pj(0%BYpPjOXj8n8o*kLyii3)o*-`z&9C(Ii1U+0-'
    'gxVGRE2i@)=OopsrW4RlS5D`bC+H#^ICP@TagecJQ?Oiyj={dg3v_XGO#ORG!y+PTGIK3}={_2yoU>(-'
    '3r43!|Hi6SFzdUw1C@+eu>u1yMatzyM7B%f`uGF`S|l-Jtn%%t;*qu^%;aa_L-xv{k?)8$+j>$m73&6*5PD7om$<*oeqe<=?#@'
    '1>LzdCX<|i?+Xhqm|$_bc-'
    '842F}3|sckW(G12d&!ztFWo$0PGD{8UgVTk!3r)m)*hf6z{;c{v8l6?>|Q|&jRQDn)Jp{ig=G!3;XJ-PR`pQCDERsY-'
    'fD~MDA<6qhvm%5s`ll77lv<=AR`zyPU9rcM3=_y)s!h-(4v{Wzm8iNoJ%Y|yaft^N9BuU-cm(};qh4UzcVXey(UJKlYBsUoTr{'
    'DduxZoS|=db@g4nwv+tD}6+CY77)rxNU*0V(w!{Zol73wy2?qkjl?vG)H4Cr((Nv+^rWLT*1a{2Q3&7G32dud{LVk5dzs{3)QB'
    'Dx3LxpnT=r%o8rl@ki{mTqwQs*Iho8$ueU|^E83ilGk?AQB&EwmluyVWnz`z0|VfxIa}c@|AT&^%wdPG?Ouja^lN`T&D<ZyxFr'
    '-a3TEjw(2~c_d<zV`QUFJ`trrb{Wyn0PZ}LQo!><tfi9M877}y>+Wg$Qb)RDIzP={9b${;3=0(16@a(WU}yXS)^`P0+_*;?MeF'
    '-Gt)0g4MB!7m*(ToL@pVUajCu7k$CXdI9>O@#L!i$591Z)4s9fIjJ&XU5Pkb^H%5aXarM|~mC8lamAfD&YYbR<=H_Bfx&N)xJo'
    '&6J3m|*LOWfyP5>u8u>`|31G`rbFa8f<B>r`p{-n?7BtNP<UV0xkD$%G79=2N1cVzEA&3XF9n{{XIF?z31?&+D-'
    'pn=OExbeL?+P_a;<9Q~H0PeHXpfTfGJWv&%8q+kQd=IAx9y5*p4(2RmWvJqzPlp#O4${1as%yc;r`_vfP4z@A<SvUSlJHy=OA*'
    'Ux@=LD{rH9bX5vkT->>3(~5C<e&)N+OYw8)*XL(rJm9FYG@8jT4d38ap3*u+Gg@opR93GSEGG!A$rv-'
    'Kid$&HhvPwa$FTG6#mncksnbd?wqt<oV|z|uTnQ$67g(HfCN6{sVDlFuj9g{zXuqhWY-fkJD&x$QE;VrcAwz<Uk7S7GSTvmX(x'
    'o5h#PZ{|Mmk<b??<@3Q>-'
    '~3wFIw^e46!`=C8z29Wsy_`PYJ1$71OjLmcj`y3oQ&J`~&g~RG5&TOoQ=g`s0Bv6>RIU!~5C?PR82@8-'
    'kGHMf9ERkD~M3w?ilQ7vap;mq_{qQrlbAy7DX8L5Spas}G>NLc~?!MX18PoAX@{a>K8q9ynx<@1HU!UTGX;FoV;`)7o_y5gg-'
    'z*@*zNkAgB)|9B_XB@f@K&zz00d8htOc4SxG6wnFA7jYE%||~f5Fi&bG5&!;N3|jN5hSzL})UJW_Ew684Gnzv8*Rdv6wAx33Wm'
    '8R>7Xn)d!B~lzdO8%hkh-z6zpp{iET37ARi$@6Q=XKYdCv#~qdBbQT{MENJR1fqNiN3^v*X-'
    '0~D@QV9lfs;j|<7~tWU(DMeTEO^acI?#SW%5lLxS{Ok%zd-q0Vmt>{%9`ar9Q4WI%@`BmT4PAr-'
    '>_O7TF${rz+X;I{K_>d_}x81jaPotT;b^V$l=yAYTfbS<%M&0qRDGfA9%auA3zanu}aLzcY#TGd2&Q9S&^V4o4a1!jOZe&vZeQ'
    'PRK!{&Q)HuEe4WvQGxk}hYrLhYPP0<{3Yg-'
    '>atHh#qIfPaNE~d2UAb|K65+ZD5lrm5egW=&XXGRh>sa(N(isra+``v0v3XRG{vR1!z>9!9{1D9s!}Xm=GMw{Xg$_~SU`%a}6P'
    'dB0=(fXO8M-tA5eBDXdWl&|ZuQiubFx536wQ^b&uhL$(NA8h@UEy{qeb>Zi6||*jyclLL-'
    '|Y=X@}W`S#o7di=kV3(g%fD6ghKAc@-'
    'm9H!g#8Jr%@DUrjLwX5_k(B{i`9|7nmIXrG38<faW=IiV=&&puCyA)PcU>c54czzxKbB@?6g(&ehs`Kw5w83YL(Xyj<LTE_rs5'
    'HU0q%i$$Tk+dbJt~>pwUY(z>g>2Jd`(h@>lA2aY?EInYh9VOfhiqedv3p?Dx)z510L&%mhw{GQk5-'
    'kA(u7f>Oor3#@r=)EZc|)ZbcZQx*JxNkLcuXOn70uyCwW@DgZ3<(q5H-'
    '9;cdbFqIJ26zfC39NWxvUnh1t;4CH<~3b$EDb*Yr~)ju;xT}a}$ElzkexO-PW(Ud~0!%s5W7k3Hmw18t-E{O$g`-'
    'aBKpWRqmcJpihc6VvV@pd&5CRl}i&%n}l(x`kA1)BgNT72m-'
    ')8k(*UtDl(6;ved8EhcLQwfYZ;YZ9snmFnD!fm8Evj74eki?N3?Nrp5+knoJYdRG-'
    'o{)7fg(NcD@b$)l*SCc_ngM_>EnJ_gci+%cV_Fo9iZ#Rh7c>?b@C+~+20kZ<r}rvYxAQMcn$Cw9-^-'
    'EZ5wgdAEGPSMVtTP^h$QFDES;8zeaB0up5C5*B#7jQId~5x^Zn-'
    '+lvNkqB{YSAtmK~9h{3P!XWsxx3^i~!wmMQ4j$$&sjGWBkKt?0z0=#dfxI`RPLmAle-VBAgeKC?#tO%0&Zs#qROPlCKae|RC_h'
    '`&0qS~H3{&jk=;N@j$!DrnYA+0(e2CC?6Y)c`#52#iDM3(p}MQx;@NbpP;lL8}mvU)&@O{K^^EEVgd53bA7tm8()r|bk8W;dPL'
    'psvvSgm{B~KIVIk(XUa#lyjV0U48e;<mK@A#*SISKcyuvJ6LK`Z{YWAa&w!>gMf&x#!`H2o~JaTm6JBst~j4)pD;E7lksl1Df2'
    '46J$LtS)a%^t6yyQCBo?qL39xUiDCn76qsJ`Yfy%;IfBER3Y%E_85(h(b0RUlUbc*lpB3R$9PM+RzK<;wqMGO$p-'
    '6+<sB`xdx5nWaSAQ&|=CZkt1cNS5GkBtQzeXp#NTlItdRDY?s^O*WfOw&JU41i_?dq{}^ySk*IkCeU@dJX<g{ws0h!93cBX36X'
    'o2cIiG{vmx?lqC%2n5+aoG5*Dn9rIa?te2V?Om%v&D>L#W{_AUY#z<Zh@3WQfto7xpG!K+o_XZ(+e`mBL&kjS?Jg|>3q3a`nRt'
    '~xgq~R}mVf8dh_Q|zl_O%b@F2Uifm$TqsD~weOY<j7eu@0S4@Zx_q#m0kAKH&w=>r2@zyY6;Fxwhsc`3b|91znY%X5z0O)6b02'
    'v5hM3+L}aVp~aWW$J{fJOw}@d(PD#1!h#&gsvKTh5dvhGXUZ%?5(WS37};1<Kl+s^JAsM;O1XxXqFzK`%PTtURKX2Mb5>OSV}x'
    '3!yH*F6{1eoWy33}Y{P7gjP=0VV?Qd;<*KhsB{tF8S)d-SHJ^S73=rsEN31yxq1nBB_U7yXWvqGV`byl$R>&-'
    'mP4+_20!j266V=7vYz{WtiqgXwYo*gnTy&OJO)E#Y?ydctpI!InL0UQ|GiGArF&KCsT4Aq1PD|!@GO-'
    '2Q#L>A9*SLP;<pb}1~lZq}V9o2nfi?cnTuXhc@93$%Wo*RqExw05W+h~oe9HjZcm-'
    '&R6B+*QC^{|15RZrof+jqh%#?}Fxk+ef*WIjcY4bl<?k(m}hK<OhvFmswEbjtkBaTM|YX<+-'
    'q4tRDpt5A|ZiQw{>e;tLPmVd65-D3@1ZIEL}WZ`Gai>U)XPj<o08}L--xN&0K_@|9ah&WwqYJ&5H#~DG>xmCIddo(Le_&ceLp}'
    'N3>OKJ%_O-'
    'b8WtL<!&6AiOxWGv;FSOx%urr`FHR{m#f1#mYmhf+_`P#{D@M_=g(XuqD%+|<0X9pLClS{qg1Q$(Ge9HZ@O1&B@k%b2GiYsVjK'
    '>duH!&4g5YdX>=nZN`m9gLbY~Uf#{?1@#5G4HFZJhOom3;zi@*XVw)pX0ewTXC28^8TPhiLa=Ay=?>Jkt2WR<5hH&oCQ@PHMv_'
    'y!@QG3%evBe?&sm7r9v6}*;40l=1iM3q6Ce6@>&t*SL!t@zqZhspu97=FGt9Xx^y4CB%(MIJ+M5E#>~N*a+F;vR{Y0#Ctm@1DC'
    'B0c7n9jSp0#JgfU=b*h1_`7@N!*lBy1p<>p@+RLJIPtMCsksxvP?vno#fiH(+R6>Bc!XrH9`g!C2Pm0!=on;WATSxx7`}dy0Xj'
    'm>OZ7ge=`%w-Q4OkNt}%DVxS}VWsOj!UZ;Z<dbj#!KLURl*B0-)#I=-x4q)&K{Z<kY<v6n?Xgc-'
    '7<g72?9^((aL}rC5L6s?x*0^I*s(kiNpU~z`TRY4(Wl`13G_lV%-4?<a@(ilksKO)2Nec<`f~7CI?oCGg-EWPHhtziPDamLcxv'
    'G@fX5_D#28TwS=E}eio-'
    '7W}t@Gv@n}wd}l)3_$1qHAKpI!{JV=lGf|0}e?x9HTbh32e8HrUS%pDLM1dF)nQ^e=88#)VRQ*G34LLDJVU%a+bu+k!?vM8v03'
    'hR#PRsu#x_)v#MVADxX%BKyIG<Ilx}o}78MXGvK=cu3PlJQ8LUu8_U_T#@)1X7Cf}IxQOf>_p@{S96*`F(PkV<=>l2WNxFCzo3'
    '?#+iwpBw;9!MgN}t=6uecCoO}uRH2aNQhQ|gbJ%=~$P||jS1VpPR`66E5LW&Z0BS+=Od%<+I$!+r&j>yp7KjqcojM_<d%9Fx6V'
    'Wo|ge!Am6wqESk>HTh2T}h?wVaS$7r9~<;Q1E>2e+UCo_20bW`c>QB_0sndpB|qBwt6r1<gF+`&Iy@GW494McuCd@ewy)<2(7G'
    'PlMZ(Ibkf?9sd(OK-'
    'e4ZR%^8#s#sGNi{9EjKL;BBEqNolO6!tDr*4VG59x|aL#C(z%p8ejMj)zINE+;QU6&3;1n4+hYqzO*kAz(<~O1e0_4<W(g?UH2'
    '0^${nq4{Ee^p_@Rgq8=4I=uGRi^iE~>>jsx$J+`t1JS@12I?o>8|Jj|uXxZ6(=XQiU?qd2qGs1{*UgGmTW|#-'
    '@$W??=Y{5!;yXF8)go=HO63YLE>5#oHVemSfj<RsIEJq#$Qy0=A>?)>xbq){G@HSHmvGxWSsdc^|!vA6Hc^x6`3`M7Shn+6ktL'
    'kAS*(SBTTVJq#xT+eN?C=?VHrwgv(xK+$k@ybOKn@QvvNkPjtieX5Bh_n&5NylBw0q^54mj@Qcd^^Yp)h75>b)3p)YsbL8MN-'
    'gEIa%M+}MGL4Ch<AX^9x)U{zbTqH$odP=%i2v-BkC4$TKsI!6ZLE}Lhm`oRf4Mm-UgIv>H%*gID}4787`=@9FezjP4hQ>_?sbI'
    'R?$J#<wvDp!9!0^tGl@=p-'
    'L8VSq<dykqWjHpUp(vwAwTogwWBSN@;q5a>AdJmnv>e`!%q}Skp!m$YpD`48SJfTX#Q$vL%aR8_}H1w*9V<%lQKF33!7xHB0?m'
    'lgvDTz`GQ-I4&mbgQa$(Nh^PP^oAW7CK-'
    'T2G7i@$sl<Tw0n)qLtVDvHY|OemETYP>w4f&v}ZoLWpaHCbj2PQUV>8hN}WO?EtLBDw4NzIZ8>XAwR#I<L+(PR3MBE64Qd79>)'
    'Y;HxC5A80_y;^3G}x%tww}-a*0Qks4J?MVu!S?6_nKay%>3hGMj4r973W*Kbggg~#b!Q}5(-NK+(R-'
    '>s0t)bT&i(@vE3sQdN|lvoKxE6GwD@cce%<Ja~7gLp;OXU~lQn|@T?3+-'
    'BCs^(ED$_8@{bs&2Y%mB})kdPpwLWBK@Ho@*=rh*&f9?v|<?aQM*^wpgs2Et)BKTeo%L(J9I&A8=jbh}Xh<Ur6;R8N0qa`hweU'
    '46vtHwNuva$>X~CbtH2x5iY-'
    ';Uq|^I5Fjr5<F1mt&f@aMhFkl{HVN4;^X*#tyVa#6WL7@kMQ@vu7P64RUb2xyD)&xHqKe1A_jFckTWIzi%wkqZ=lg64@Np9jQq'
    'H*N`9m2?6Y5MxGP|1fAIDnw^8983^E#8l7%dwR-2KV;^GO7o+Rlm?PPjFzADu!;pN$g=}<K^jAw9n_kYISp%u}Z>aPb58&)5-'
    '*L`!klhFKRVh<NEz{xjPg5O-tV-'
    ';bESyjZ*?C`QrC7}d!ADIT+u|MNslgNyPknu*rZ?ZQ83(%1TLOn1~_)xkxt=>QV%6oVIKUw^Q-GH-'
    '<Uh;#F!Ew#!D5<wwl6fcEIXlxe#TBhp4kIb)$6u^QZk0E*-~fWc07OC%)U@?9P{l&(Uk$iH79sT)LVUS$?`0wOM`j>GrB-'
    '6}yR59=&SR#+^sk_1C{irzddwzcGIt@4oQ$FIVaBrFaogYx(MXn#tFIbRv#I@TJK%Lx?Er=}b(!!1)F0<U@1G|<qU?3P(%M596'
    '}r)DYEW%&j~dx|MpmHyNk`!2KS(ws*FBZp49X)p`TasJ$rwB2NLsLjPl(^ceWM2`%c3o)rLgN!BKO!1)+bd~wB`VE#C?dRnBpj'
    '*zb_TqT?5?(!NJ0rWljyHF_){UO#eT&FB==2??>yI<IK@2*lwd`+t>0l%8*1!Ft7}7A&f6_B8@T({+3_IN3?poslO+#f*jUXxf'
    'k~AFdgQ%KSfwXV5CS5WDKGHreTY#VQ|7*3g@DzVw6K=5U2c0>Ju?ib0SXqJq&;jMu(d)FM;LGQT!cUI%>OVaOI@#|H!7iONLTx'
    '<$DFuVM@R0$$>nW<aZEN!HHCYCe|aG*2&+|pjUhbI~1f(?_j3U8P6#W=ht$9`|0IB@)Y7pU{O-'
    'unuC%kBPvFsz>EYC4m^hIq_K3l42jPvgIi;iE@)rR0^VHy`ksd7nbLv4pWL5gZD22Tb4XQgvE0r{+hbkCFahL%q-'
    'Wj|yx*n7OPCsWP?a9nV4};mXwsZ`&imU9i(R;Ad1}TUf;7k++aH)_+hkWGBPOrkQe0$<c@=56>GCl2Dz>6h)jtiY&Hg}49mqWh'
    '%sn9IOI6Ixf*}qOsVH1gJPqOd8bO1x=8ku-'
    '7As13QBh!fcC7~9t}hrNHwV4@5v@mWi60ZN3LIqlgAn`>GQ9SG44`e0{o^D{xF``d*FrA7#Bsm2ueY8#b%(C%6U>o!9=c3mUA!'
    'Eim+^PFV>%DK?QDx+UY9Iuv6(nr!abN0Mw090A}Z`|R)D|A{N^V`+pg4V;heDUEP^*_JCXn!<{4-'
    'y_A81lp=>V@9OL6GTFBw^;#bspsm{cP=RDXLPwVzX^TT~uPe?Bo$2?;j-'
    '=aP+(}%P|WaPb!CQBjto<5zQZvq}fy{lF0voOXED2GBSI38UM3QJNbyi*QHspDW0uUzx4wY)(>9QRTLGseN)fYEOWi`(@T>S)6'
    'a#gwq42ii{FW*-'
    '^h{lIBvXZQe)cgV=fa`ySc@z4WKe+f@h#K!S3c2vh+0<Q2v+NA`lM@GtXibHYYv`eN)JX%1xmud=Sf?XrDdE99Qv$MlqGjV=AW'
    '$7YVt3HS0kan}kqYPdYyJ08u<-pVHvMb1}F-'
    'L@01_*KwY_T$TW*m8>Zsr5ye+J~7+=Vo`E5HF`>Klm~5y44Fpj!Ks{Mg`g;HO=!rsI0pK~y~PNVmBcLI2AA2R*g{hVf^1G{uA;'
    'c7LnT7p5F41yt`5Fk-TSBPYoJ`oEiO2bu8$q`Si$;dEYNN>`TjV)AX43#OSpU3N^~0st9_K>0Y4b!COQTGh?WcKF4kox^HlCK#'
    '#3il_tS{k^GCyHnYv8T?3~l%L>uQq~AbI<{y^4J!)k!zQX-a(mKI_llL3hu6n4*=9>pGJ<heYjzBMHjs28S?-'
    'z$=_NhNOdrhRTBo;{e@N3_BTPiv>-'
    '$X7n_pKU5S$y8co#+B$$(WBMCT8cehAQ36<_Lw5h%|II3DSL94Sy^oCj^%a!GWVaA=J;KBy~3*?&7V<=h5H!}+#B;0=>hs`xEi'
    '2*&s*10bpS0Huyjh}4<%KoBHoN;#N%eS%=?t0!{VC%2uAqN<)Te?(DF-{;LQ-'
    'U}j_3a<}p4!+^7G+Xc49eJ^<Zf8Y@j=EpuUH_OC9I2QW?q9@FmQX;Bi*9}>dHxmuR+IV=Ck03rhrCBugD<AHTsLcK4FR=}<h`$'
    'Nx&A>$9b@;Ihra<9@?wSKY;(;DjUOZH9@nL&IqVRJC7VB1R)s7$;mp}*+HNJx>n97t{9MiQJxy_HL<MO=L~Lc_dFB*JiWffMW!'
    '0Gs(O*Y7KYS?me-$T-&uy&%-R)WH;HW$q$sgV;c-'
    'gCzDUY{!7+RB$TS!D_AHc{ZQ_sD_cZx1Y`3ctkZEHMC<+|>yNtk1#O?)v>^<b>f+A={^<Yk##ED+(EdJwZ<HZ`xqKjbD;$mei&'
    'Gk0u__Pk)dWpzYF0M4v8kn_Nlc|CvtLK*SU<L`8ni>a|P{ID8oAkQ^pFex+`KX2sm%Lwe_&xFy;9s+RHVn{Ms@x#Pyr1WB)j<<'
    '+YO_wlaqZgm#7rqb)L)J)PN{~cS+0YrnzhLP4^f6{;L?%5Z${{}!s!J?T;OmcgiTp6&MqhVDpo!`55}>880Ekn)py!}%7U*)WW'
    'EO7*o(Ax8HML>Hrq92>r%oB8yd!)GoUI1L9vvtBM;I|(To*M~4?5ym=R!q$_KL{#Hdol3idU7zM6CdgK6a378{M9KxuC1GN1*m'
    'Mg6a{lz$OmOC>k>09o6B9zYRGPx#<#BiZH+vj|rj>Sk^S_Gmj0H$>=DrggP$^2=vC~A{FiI1!mkyvAt7+Y4IeXePXD&`8?-'
    'AbM4wFd-&Z3#LE5kwo^rUa@5~V6M@;uyHhD2B*Vb%asf)I^B3Lx;K&-'
    '$;*OElqWvvAs%I|XXLx<v+#(>dXdc%t0I@0&F%4>vBBtR#AF5z7#`IeWZlW1B75@B@M5^6kB&t^5I@aVOFj51yYY<(|g~8wH3A'
    'AD;54k8Xfe3~CkwE6tT4Ul8`rp0S*`)TdNLGdR_zenj@&PoWa{K%T=Ih!ejN-{`@aD(w(`x1F$3*q!j6-'
    '(|xten6So`tL^?RC#S^pv9`nC`0a1~!6Sp1FOk?l`%dgi-'
    'e!hot&ngrPt+LwH{ZA`3zo&Gn_2lMvh9?uKtMx*ESv;e@8pdEClHjU5;A$a;<eeP|&uv%HzO-'
    'S=EZK^P+1I`#LxYkSA4mzsD1dLT%#|mYLcK@8jX0oP%TJ+s}yy}-'
    '7N;^k=5E#&W0mSGc4pIuf8~Ta=)PM5I5;}V~K1pfDQAFLtNG4pZFA43(=D6QT)Y=anvItO=U8{dCxzd8)2-'
    'GEMaTwgtZbY!SgtyJ23aO-Pxg#QR>xjsm06-{P5(BEsDUu5FQc-Zy3%D@Y1`>p`o~X>)vmgoIN?<7020O-lV*^5-'
    'RL5sYB7>E$eH0)MC8cNd#RJks@ji!3%RM~P$sE#^Uf#9HYhpMO<-'
    '~+Yib+BdT1~iPUFS$y+DMK)?@3+b3``?(_mCYg8OG<9wnY|Jpm>r?>^hE)mEZ}{6{g%^`{lP5Q3hp=u(ocH!xh*Ta&zMYr3|69'
    '5emCcz<g?75=effAQ-'
    '&{fQjUaH9I8x|4aqSAsCSIqFinj^LqHHEd#!%$hY?~hoFLkjn;yXN6g+}R_qd9bcg8~Mg37E(ba}q=1EVSSQm+(4G?oBGWb*bk'
    'kUe3X;0Y)c<il>Ey_$#%$CeQkJ!I9;a*zP_!c1u<5_5c;@@4Oi@M}<n&;vc-A)6sLZvn-bw{nTt6i|7Tzl-5-iFXm9^23uuw#-'
    'nsNyEv0N@DAR*_NF!441%6E~t(J9v@03N%SJoPKJE*T?xIRmv3gki>gU-`|6J`;rwWmQ@-'
    'Nmso$pCJ$ZEIoNzvbRMirmCsMU!u7oizN&D_+LqB<db1<i@m;{44QA|fh)OKb_!a4vJ+~8}4q5Nu%Ru_bZHA^k+JufLIWPFkK('
    '+!<j9R{j7Q|B^ok4rbU6vmGMbFt286qmLkBu-Gu_xx~O@dn(wAF5uJC#MxtAs-'
    '5GHkD@DK3Aoy1P<S2$vGEFLGrDo&hdn_&9QvwDDQ|SP5*EG|KX_){h(#EJdmwDhO`DV#rb{BOiQ-zFx+7{$O62rlcKv$xyO@69'
    ')(z0+lhm$GWA@R%mTo;WDLom*hV0;(7XexL~WUXiyn);H>MrwIL!%)HHK;9UBJ;_=IeR+p3Lia%?DHjB;vR5x4zaNzJExCJv%F'
    '24oeDFKk*^DRzClDQqy988UE>VDrE2Nj(!7IF$fqx!;wm3PermTe|%6tS;nZpM=*7Av<o0ZDGSL3Or4RYjrE?%{wgU$6Dw_%Y9'
    '(A{@T5uyz5_MV5Sr>NBEsV>T6jT?@nIEf4LCu8pRdMxw?oF?^fq}_$=x$+;^lres)UH$vr#Y8eLn798sG(XrEW?%UsC>Vk~NwU'
    'QRHEE?YYoB!Othj>=_s&IFJek~31z=P;t2IIqC`<}+G)-+-'
    'WwiafH_e0kE4DvbPl4d>@hY51qm8T4_AaH(F(S3}b==Af_eOEI8K0fMFD9RNPLwiq}ezx82Rq!601Oz@#ofhM%S<UjFIj4Ep0('
    'RY?6tTQF>Al*LG?jTgYA((eQh0-'
    '+Whlp0x=e){d2(rf~EXC8RkjN1xyZxJ?CHd8E>4+qiMvRQ`eIJ|#FH3u$`f;`VPa9S%N55jR4vwZ_3e%@kSc@mYoRs*+vej9_I'
    '+$6|6s}UN=*|*H&C<a?!i}VnhAgMBLqO-sJ1z;Nl<0#5O)Wa^weAoH6t8@WzVm%liiN3v+G;&!6p$*aAcD8h!g;{s(t_ge)5ZV'
    'xI{0#5Q5D`$Dj9>@oVcdd7seu%0MNADhm^IYcQ`6K#Y#&2|KZ5mKxLvpt&8_BXexTHIochB8u1uvM<&HZ#|M}Jb-'
    '8s5EfaQ9x8*WVR8D5TR0nW|sVZ$-'
    '`uEZ|_Rpr7F$X$Egr{z$C`Q7$JusFykGt<MmE&AMJ(^}!C<eFgz$IuA`MjtMyIrH51|#gIpGu3Zoi$HqIcb}q`Q}ttYD{=+Ud)'
    'mBtgR340y-'
    'akg<i_P8@~XNIR5`B1B_tm)`M)Ogwc|o3~KdIO1~SHjCE%0i1R&ukHiw)U6!B<mP3qCeTj8LX%Z)=;WgjVvz=9SI$RKd*xiF;W'
    'WL3?H=_V*KpRI^v_=alP4ObQTvQ|3@%3D3Y*@r|FbFiv77XfAmO~;X=aa@iTOFlU!uq-'
    '>4!mhsddixVHSoI@m~%3elQ!B<JqY%rmgAT4YzlMcgHV;uN6`9GqaeA+;%J5^mVcx92fv<C>+4xcmNl8#?5-vH=-'
    '0KWd|wXZt<*kz7mRIix-}#5nPg(&cM5t{68>^idKW8zQemmJ7(5nH6ANAIq=Hl(-'
    '{d&lbTX}eWh9<X<osneX=Fsx5>oTzyviJL)6s|kCj=3nG*W9Kqo*IFe%}J0y^C8Xgm}i$@nP@kyt-aChN07N=K%1*O<o?oCxX*'
    'R>Eu!t>-qV)S=yy?K-'
    'Yvc_?<R<>pV>GX+tryeosqRKI;F|B172j&`eEcX#CH5rVZs9L;oB`;<pRK4<aF<Xa(b@(X|qv)cZ`5E<}80=zWl}m%}+8Ft=hG'
    'UbGn_P~}u@Fp2bxo%q{zh~9kh$2IJSV@8)QR+;$+57{Rf4C~oynC<DUpY>f&_9V*5D_BoeKf9SRYhbda^g=XWja{Av<x&d+;>8'
    'j+JLtz`S;yUNOD7X>pPqwJeA|3BP;3%h%5Y2QP43SfN?u}!7MviaFqlv<h8N3Dvw62ObYESIvRhm&Fu@uYK`s5FB#%-'
    'R4;C{YWc90a|8CP+WB_4o7@fUVrVi|R*yIQm=vIx-(d&TT#eUGHgX~l!nEeQ_0Sp-'
    '6#HnN2rrn@J6BPJDDM%P1uWLb!>wKx4&RVc0*RhqZ9u$y0NTlz4KR>F{$GW+kC;6egl@{`_LZJ?b5P~lXE=kuJ$o<w^fazq@16'
    'M}jPC(3UOS$s$d>3T}hNVF}%pI{+fYj;P2Q1|N<JQP^p~Ee%t<2R6+oZg|1aE%`(Yp&e7nN4}+_gs$-'
    '~d=_<~2VA7pv<@guIH8bIPovTv;xgWd5i};m=rJIX%*L?y>Cf^L=kEXZ+-$R(Ly%i0o)b2|IY;%3I)G91a=P;FSfK&0M-'
    '^`tcm;Gs)v?;TRj1#a2`&Y!n7aJQaQ?U$_Xx{IFcv)^<Bn?7x41s}g#b{3?0X5rxLFA!)QWs(E&4(@&!5TGznu0O@&OE6)=(mU'
    '-~MiBC-'
    'i`=Ca$Q5rV)tYc;$(=p5F{XD1m9JKZO=(_S8+#;`)m2ukM*SQ?z)0GpzvWatD^eU^s*d7>XlW&Clr(!RPudhlL8HXVrHm!|WIN'
    'j9M&e2in-KD9KbpXd~(!Rm7)B{~RvVrHdb=VQ`DG8$m)1ar+qd>*t%a9Q-'
    'gB)&IJgj3)7wJD}Sgyg3?>Fi3%_EJ~{?wH0CB}#elDVw>-'
    'P?@sR!jwc2ZX{_KxKqgV`sf5cerM~c3yK!aG<Tw!GZs4jjs9RZH3E^c~45WKIJUCv2b(7O9p7JxoZf03nuq_o_tBTsy|N^nlKE'
    '$qVcsGC9BG-B_Fp-p*kg?eBGk6i-'
    'ksQJt#cDFUKq0Ue`$dv)PHo_k54afVF#F3wUM)c;R#RG_GyB5*@fPRcv_(W4%HXz>`XPW>VGp_5P^Jh3zTK9k#Z|4Rvokv)+$}'
    '`5<Jq9E_OI4yVu8gzL%h{xZa3#Omus{b4Z19Z;CvdePhylJT;w8}`4!vfa0E>RtQQDC~l#xxTLjP@p`gHR+m{g5;p%xqzpLFE4'
    'xCiL&ADb^Dwz;nF9}HMUZCBjWarBX*}EozUH)b~b+W>L?G~g`pvy4HM|0xj*UF<zr;xAq+rA(tsw@!AdcG5lQp+Ag0^yCLG0S&'
    'UcmC%z_UMsJ(bh@d|Zc%Hv3uq*Za#>#Wk$@|Le#ZXGD{pSGAA*Cr6%Q36bh_RJ#S!UpM>1+bMXc5*7nk(;Xw%fTw7@_}SnC%`+'
    '`or?#Pl_`oO$@YZlpr<aJv8RO$dQ7W0-L4#htlZdGt!*DgD0j^9dz7-'
    'lP9jLwRqisY!#_Oug&(iFKx@oT=FPn{I=*wXiJU2r0Cnzx2AXIbx<qUUMXXvG8Jq754UdcP0BxB`x=m&r3(?p%qVed6#BlrOP@'
    '#=J)lV%j;X(B=vS)pHA-RVF0uPGUM;B_q=ZtkVEZHL+Mt)IwibCS={ONJ-SW>M=WMGG@h;eAU7Ro5@9`7xa$9op&!{OgJ5V!Dv'
    'Xd1vaz3YqTjLsOi_~r_sXhKV(D|n=Mraqg8{5GQ<aB;9*^ROTo4P+XE3~T^aA2+7|xcL<kkme`%aS#QVP-PeBX%)@qmhyKMguY'
    'oW3T)7)E^M0gDeki2*#dp<=oztA!ENrD$S0lv)V#s(x)X!HaG=k>)X|j<I@Qee3Rdr6w~;0B@JTz@36a<;d*l=Qmwh0j+<T$6H'
    'CWVnYp&>do>#knY9&ZSwO_`<z!T_~8(mTEJJ!Gd($$-'
    'gJ7MR_6?GkL;jtURn)5FSly9R6J&+hff?O*P=!y`Nl@{8%dk~M6)$w0|8xgk12F>uckNcD6nKBaKd>lWZd$p`8iZwxaSoq%)=H'
    'Kxwa6<3Gh8(n=Q*}B}#4vhx_*;0sHA$HDOHunZCQhXLci5#uR$Jk_*cV9lFS%bIJIbIM^vs)MLaqtMr!c)iWGk;EqOGy`^TCuX'
    'kJ<)_pd4Q!ciHAkHpp<2DF3e?5d{S%&K-'
    'BHpq1Z_w69a{^}wLY@h30xSsKqBg~x}h=%L}K322RpN<`oTRwCITPIT7y8`?ayHtd6s*R%AfKqsg?^K4R=(K6iQn%<gYX=G=&m'
    '~KN}%&odPD8_V!{CT8-<sPrC*nq}lfDo#7|7QKhup^WqT|fIyTloZ~gbO5qpE8A-'
    '^aRcvC&0IkM^WAHp2ki1uDiZ8bMa&cG_8v6bo6nakPtV6QpFwsmepge`ptFLGg3xNnqYBGo+Me*mOL64u{jK4=UTQGX#3n}q5M'
    '~Qt#bT>A>@Tw-2|b-Wjw7UoFN4-?mguofbekbc2ms_tnz?p%DX5DJSk-'
    '7OkMv$07tj{y%pa+_x3k*i=rWptDBjz(AU}(!)2=a>ev6`<spt;;~a)v&?|40bwKd<RaNb=p%!I<H1m<iP{!TBO{DgK0>)9bS)'
    '#U;SgC{WVF4XsWKs-'
    '}*X`v>pWw+u)a$)^_}RlH&M)rVX^_0GyF+&#U%urVHXmVr2m@9YZBNEkhGkWR__U?#<pQ4qt2eJ5M5o7$6`!-'
    'R*~W;O4Jh#`2#Dw}*HOcKS!F*b8zXx*yN`k<u<RkCpOjo<oS!&kNF@G8?p(N^K8iBBJ=&fZx?qmE7x=aoxanxRg6QB20Kf|q^F'
    '-I_2XG0e$yzBrUO&o9)U1ZOFn(WFEW_fUr`ncm)h-5FQ-%YR!sX|(t-'
    'gW@3P>8P;U`abu@~R0&Ud(nuTxu=r^QI~{(PL>*L0p!XA3w`g}WUlub(iEJN-'
    '6EyN5u|aEWgwP?7#r=DKvf5!Uz#;zs6Aj8SR=2_8h0Tl%T7Sn{|3G`q$lw_NnAsY;6s7YPzEOSo3$N64{?w;z#TXMOYkvz@P6k'
    'zSgl^Kd;|e7~fVN|hZI>Q4>n-_B+%%<I)}@$$9Xb67!R+y31QxDb=pYdB>&9R>KcZJ84CFAX-'
    ');w|{}+XcV(M|ob{PA}X7<x>jjm9HMZfKI;h!B-'
    '<3ZqfoPn}<K$UgN8E7R36)B;qGH@g)i)lvqhnPTOrB7<PF+Z@Ki<&k6GXgm)rHOs{X27O<5rmZEvC2&6o+_6eFIE4K_`4XEtg`'
    '<HFse<;qV_5Tez8{Ub!*IY<~P*~%0V65b_a<l?kKtwT7!b$F4$Z6Za?so{w1>i0oo~@x2xyqokZ&QO0sxZ&p6r7`b92qIdRm!z'
    'nDr%}~Mo{qY*rT=##ZJ%dwZ$}K9h&BA0KcD*kcVQK?tCLq?!n|kc*Y1??L&<sQ$H}{IXhk~(Fy;rm@otp%)UlZMXXCi0VfzQGZ'
    'uMwtuxG64Z|q9@{zxVKHH~x-6-'
    'uGg3sb3nqEKjhV<OX{@`ju>oO`3ISO2V)1_rvXH_E%=LT=C!G&_aRDHfj7>lOxgNl>ZOfkmCZ=Z}C($mp9aoe6aLDc);NyM_1u'
    'rFwaDM&=FLhc)Aq*nJFkvDY@a{Q3tPav9C$7!S)rMX`w)|Fjd@n!8rwK!@kdSW#X4_Ckrj9cPx)cwb4WJd20M<r-'
    '$YP6<<hcttZEJ{g5fPr)m9iOI5<4WTmw7cQ5QvOhE@Z~ABB_!_l*Oh@~hNsShsUCoiA7>5%An#{eOe~G4Mc#-HDHY{c-'
    '>a`wt==9@>FTcz3ow}gssbhem@%E6DohEZNY=v0Gc#8weq`a0>Z5M`T^=arcmQIws9II!Drnx+FU_gkQx@Q%^m`M?<m7Ix3ZS|'
    'OwHpW1ggN!hW63{kG?S0Ar9<+9B})usZ*|xN4^<tg6MG^kiZ0%*)-(x)b<gl)^cQSX<b<n~!Hm4xx0cF|C<9n!mDyG4;4o?-'
    'TVcy8G2U@i)zS?~_<;+;9@Lg0x9k`d{AV0r1d&(v|1eI{?j7qC^sJ=>>N=wb_Jd#!z>cdOhrN(4cMN_zz)qf6m`!9ZMH)l9K|6'
    'iZz8{D958&EVW<u_{_9(&;xYaA5OBr0Y_8GHy6)L789Pq4{_*^gmsW0fIVQu)<B`hdwR9p~KtDw|m5J@A`p#yj%dtE&a?f_smC'
    'f4Ol8NMhiw-n5*L_C86?T~Rd_qFK}>$w-'
    '5KpAa;gKdtU@PR~kETH@O_qx%z&)1%tCyA+s^2f{#@aLA;m(Q{z%ti#ZAYxZ;ye`TfY?%TCu=xr-UGxBNksJ|J^iagF;3_a;f7'
    '`$*?f{658%tHn+DH=Vo2B|2u%cmW)=p;#wSt#aH+q{mX_zI>0QWFrE-'
    'E6xr;y;Mkh79poJ<WLeG}5UdvpV2>kvi;*UB!$=#gQ;E0u&Xw!%DxZCpmMa>|ZB2l~x50cp4iRfj9(*u!^6L$TRO{*{b_Mh7&%'
    'yw`$T@%KSraUzSMkJM*|XtM89oiEQvYUn=(b?6OG{^FY`{!iN|sj(E|&8>EPR}#$}t0h^VAg&cerU}bXDvm}!QVl55ca+A+2?r'
    'TtZ3=DSD}`2S9ateyS$VicTL`Z6;}NidSHJzPkl*lr<VgX)gW8Fw>+OY^1iC6J{2pSA&(!U|q~|}J0B0?g#thhoL#$H_Y$@;W^'
    'Sb{!102ncyT#aD7PHF9GT|K&7EV4;QHA_&oJVr-`chdqEMPjeCFBkB9ju(9s(p-'
    '%X>@`1R0Lv)ZR#eo7br1RL5afJ(u%U3Uo^OBw-*$l;v1<@{@TfZ&EBLbH`w!2bq`qpLt~JJVSQqQv_3Jv>UrtEvx3}$SNsRZ-E'
    'd7i!Q<P2EK)AIdGY@_^5e+(=gb`#6GihR{IXN;bWUpNvNa>aDxm^1<1a8AhbN2iZ@#~>lrB}+sbA#ihhT`g%DyuJ<1My+0^AO8'
    '3zfO~Z*%W$@C`f>3R{ycxMCeiszi6o7;v#3*jd3HXR>2BVomPtsyPSir3dbs&8hk%9sF&iBFcMDtwYEWcg3Ak4}ZJ=!PlPGUQ6'
    'lEhf-'
    'q48v&)My|%3;Pa*vC4t#C6$Z|czJiPeLE|(P`bR0t`cRe)bnJtXg<;;!;A@MktUe4foNyEWe(5cDbY+nT4#4z$5_bD0;P;2g0!'
    'FfI1lR~g!4GQp`UIEC*qpjzALjv+0J6O&T#1|;n{F^_Jhrp8-'
    'd+u5zEyP*1`$AR4G*){3SA@S4sUEfss7lE;j=#L!p;WYPfbvP}(*`HN=MT#E%;W~thoqvAV~u(gF6q}%gr004{k{!@GHcP!COz'
    'J^wuP!>i~(p1?S5u5!%hAtd^lzXbBQV4v4B!4(0vKOM*&!E^~cp9r;nj^sK+*&m9&DF(Ph>-'
    'IL~x4=SwZ@O#^B7|L@K6fr1#!Twe>}5dYLnNx(*CGFfb)AVnHfzt2)7&0CK(G+6p=i#|D2yy=<x)z3)QdH|bkR0O7#!N9W#N4>'
    '^H1(HeXIH*$1&FTeSnW3D2zcAe^VZ<=?6kROzTQxjYi;$d=GP1T5PB(^w%uZS2aPK(d;~Xe@5&*2lvI?B>Ir13u1}a|B$osX&J'
    'W9nXW^{2q`2BLSe#yvClrcycD_~m-'
    '%g?0iDYP9h3i$a6?ML#gTdLW75HPGUEz%>E%jm@dDK|zgKtY#!hgZ8)>h9lJnQlIl>G$lIg-5m!9Q9w<Nv@$V)r(VECdU-'
    'Zw{G|~9}Mtl#C0zz(8^}i$Fed2i}$jK=tCZd6|H~f8e*8ahE{5wCQq(4(~+a{6rC>WX+L#CHamJ-WVpaZSQVvBBO!mCpk41GKn'
    '7+$Q{sjLL6hgwf>CPyn}(8}O|h{S>4T2tS`rSI!xp)IoXt0;46MNw>u=ut5s_^dg@g384IpY~YkwsZ3)`39qjZr?vHLqFuLr}~'
    '>Roc|Uu{aUD$op5WOdjlNUbkLsjwWqC8LmoGgHc7GmddcF~s_|U%8;l>*r<MAVNl}<Qi8M=yH{t86I2lSS-'
    'W0nrJ4zn!sV)C{zoWYwQ|Ve2lDd^q&r#oz9Rnw1ai{oNqzBk{cHnv3?g;wz8)sZEB6xh6p=!PmMnOp(ejY9eun6ngbH0dG7a5h'
    'b`G(Y>K}Q)9;u!QY-hO5J8Vv04M#58-'
    'swxZI@!K;tw~V+ri*@KZ(e}mmCUyvgLB8YF)3l4P4u&FkmHKIePEUYCSwn;xk!><Q@XBfyFvCCPX+Tq(wB7n9c-cTnW!NBR-'
    'x=M^i2_JlUl=@){X3^0-_mwJZbtS3I!-'
    '6gYPlpl=;m0&q(&+}$~D*ipys(^*nV9l{q(0DSs_Ro@*E@n*8n_pSWpCqH3b;JkIl%~oa%m;V@*L$3P;pdMuFY6xp=jUX|u#T-'
    'ZET*7{cS99ij=E=k`>IFo-uK6E!1Z?^8N3bU?!MdYiG^K2FP7zmIzfL|6Kdi>~ueqjc0)TtrQN7gt8HEzPByy`?c(94s!dPcL1'
    'cxNLOP$xduQr>(l4P~t<!Qk5RX~8g>_VNDHZJSgqst@`yQjsI)yA0GF8f+ox_4aWJn30GDYGfn;pTB5RHGm%ZTfeJ(pG~#4)Ob'
    'I%+<K}^R{M)DKZI%FhfdUk`^b6)hqeo{GwFaLzQ)h4+}mgFFnQpvD39Px;(PTIfiW#<vbef4C-'
    'Fu&uA3B+K$iEYJpwJap8C`7DIYg_QD~cMKD$Hti@W2vw?Xi%kl&z3_y8b8y`y&{t#_BV+Emp5=Gc~brm;Z()Uk)Eo2S+d4Z^!u'
    ')RKK;p4PMMtK%>tYZE<NU|>cOzT8xqvj@C&`c$}0S1@Y%!akZmQ>SI*Xc1?#7~{S<UPyoYCR;?6q~0RVRho2h~7U;N{frjA(ts'
    'yV!}k76K_i6VX&hax+rh48!BTi9#!c(YAOI!Afyn!UNE!*>77iX_ZJqyG8=U9vY=aTVQI<reQ@k7`8_Ko7298zed=jyk@qphh{'
    'G%hg?5_T6}QSZw;-bc<-'
    '~`*`8!VYS9xZ5_mj`V(oD^zDJ!uWH8w%Ghi7tHW%){84&{o1<L~$po?B@eaQj{0#;|=}7*#U9f~HTT4c3Xb79@lGxq!6%er8sK'
    'RH)4o$(MZ62a5AFgoL)m?z^i}L0pKp;!<m+{a_FOo(vAjkcgOECDAZc=o2jK-'
    'Uw~skmyj74HWJkLh+<b)U_#UKbraqFLrVncoplc(2Ha|Sc){M-gs!eqvb3yF2--5KjD^z-'
    '0*LM18{GA_Ba)}6?{r<qnPrglf3xOV!+y%bUw@O!p-`}gzCh{@1tANhh(;``Dk)=pF#AgNVpO-'
    'Z2_i2!=cHoPe_o%!ZBVv+^QBzZ(tGQrz|uQfzJ=_Tg6tr6)}es(ZAB%GbDKl_1z*6-'
    'wS(6)QWiOZPM6$nM5$su7pfZa`(RKO>Xav91#C=VJ58%;{Hug@`HnI|6cS~C5fge`UCQz#>V=ZQzdDM<73xQLA+yD*y{^(xBxV'
    '`5}oSL+u9?;{)OAFl*0LXraBEZa+0mVS5W|vg#DDFaF9BmpABdPWvnCeR$A>hbX^9?WWd@X3yb;Om<;}f>~8|-'
    '9jG8s+dM?fnmd`NT&2I<PFhBYaLbn*jC65H+wxJfBOva~a{>!@^x=v=@g*CMgKDXs4NMjQ?$aUaz>@;4yQ>*9Nse{QEb|*LW0>'
    'lwxHkmAne(DrFa-%WHEX#p5}mSVNKa&G{z~#Jm#tOL)iq_*7Gj!)w?*xtrJK^L$x_`U8-'
    'IQJ*w5vdSlc3mR&zl}VBd~v(Re!SVb5XTIi8?jPnY=Vp*~AWfSD=B=D7FAUkzwF>I+D$qO^32DFNzLzN&m7$=^pG0PuWY`;uG2'
    's^z?<8oNcxEGE1FZ)&}!59QEd$gUEGih5ICq0Rwz1DWJ5=k`k&HMcJ4b|&=Q38&MF;`n@*u#C(4l6>q~GkBfH?x4kW>P8zoq|h'
    '$shxmD@k&Q;T+r-'
    '&34L`~pSaPC<I%jAYtmy$B#sTM_Ehe+$+zAsINU86|%b`~q?7nWvAGG0#gw%y!B^5W+C7PlWG%jz_*(MMUurq2~&ga7E)vPb)x'
    'M}P^3{e-'
    'Msxj(OO!ld7da@&F?A~*!&)i>KOV1e4{*@ug88!s76uRf3>l2DHGlu{M^>P5^J8F2V3|FuYt*O*tT7rr*!2@wndeJQ7)Qu){HY'
    'w51`scuyr0^tqya#GYq=md|7g6X|5TvaQcWOw0rM0CidVG)Z_6pWcp*-`1UQfgZRTvRa_Pfm|CKbQT<Y;BHWY0HWivY)eSMWV-'
    '>bJ`ibF08^64}3!$Ktc4>UVg=_?Wyv9tdg?r{{PjpKs|MU}(E7h9pU&_*uqZs75%*fW$plD!8`Q!|*b_A(*GtMx$t1j+%N!{*5'
    'Gt*IS>cXzLP%Wr-'
    'Kxx>&p9(ntp<2k-R&7<oj!muaWp{yhVo<Bm<3=psdWF#KseN|HXDro%1A3ZE5P#vn=WE3bd+TiMI7K_}{q>{wuC0L$<jV^mb-v'
    'maPXwGo*cW&8qMRq2%3iJJ;CTlFN8e^Kax8m8+c3WLPJ@_P1r)yFm70aitAg`943I$HRsF*^j5lic;m=V%!A4c1bf(TNt28ucY'
    'zt{6pe?+wgYv0M_n1B|T|$QNy6-ws$1Z0Cf=)0Sz~F7I0A-'
    '3fU7oAYlmkB>Q?ku<x18*xtF7NEF50%f*S@ZKH=PkITZY>0?$v!T`e-L&dt&|w5pYOo<_KySD9s7aiMc-'
    'I$}cVWh5UxIkq#%cqj-PDBtBbrLj6zL&Zjd!xn_^EuC`s;u&n&XK`{s6C7@2(l|1Vc;h!q2wkk*}X=U}BhDmE#6q`rp>+oF61J'
    'X}BvsAe}FhJbi?C9+2a|;ez9Neh0QYr?U<k<}u^{?aNamrJd44(=DqvY!Xuq61FAxH^*cY*7J;GE_$*1v6LXS=QHN%=1~A`%z+'
    'Tk@CTRIN=7$|VD^kFz)y|B*`=aBaPHQuxZUWfN4r%%O{|o`=*GIvUYTIb^)CrC7tC+a2a|xGJJoW?lbapHPv)01I2qX-'
    '99i}ye`(-'
    'F2v)pJG4|?n>kgLW*j)R~@IqjCOwAs7C%H@JX;W(qfk&uq`>M~jLqt=s$dFj99~Ety15uNl?E0ghh_BKL<<E?VCHk?O^aE<TU<'
    '$URId_*^uHbi-'
    ';=J(S&3!>aFEX5e_qGRxAWD`c`DV*}HoRy#h~~3XLQ44Oh(^(_ziC`p7AzKBO!TKj`P<pF_^O1e)|3VySfybKGr&u<r+A0RZ}#'
    'kiy|8uET2fFnc(XI1I--%dmDIw;<uW6W6I>pm#;KpDAUfK9MSQP-'
    '1E7f6k;SsTSf%e%r<Ouui7T95pO9fupX+(H_{Wpc#piEp2bk`)8tFBV^Yx3im-`cgz{u19yTwLv2mg3i=vKm-'
    'T!{sX=g31`z^P_e_V1cBAYE}q1UW{J+4&?Wvt&sBft5`J%4YufG~dR<aVR?@Byq!(&r?Di2t<rr-YPaUd(eX~^ep*~KkF+HyYG'
    '9B5@3J<Ij(eRRMAWA?0_WovU{z_RM9Uh&sNmM*^Gk#0i6U<4a{Spa#)zUs_i{B3Hoi`Nz^garLfbV7ifvS&d0|rBZHkn69fi3I'
    'ZAFMHF12bV;IrLCcHaZ@gms*UIJvoio!9Ty*5M=jk>r_8dfxQ%4593D!(r<RUXmx;ye-AC+fwAgMqexfstbdJe3PR&m7Uj4Y4Y'
    'guEE%uZ2LQs<U@>J5KSt<ixoQ5%dfEVu_>`z88IF}H%x`0+U^p>7KGu^jv5$w<+ycO1r$n(=+6KOFyjlyP@H6ARLzIff>@#d`i'
    '`P+{$}`1#jk!<W?}3{!xYZf%t-R_U7RxZRj`p;T!hV&@JtPs@Vs;N<jHtC;03tf^_h;%^R<^KR7PIRMWU6R4p752U8c_Bf)D-'
    'C5W|up)Iuhh$_Z{wnyx&^Za&YG73GSc5_@O~%E?_9dK&Of0wh*yYdQTPH`F_y(9-x2hli3;GFVxV-'
    '`+g3V<}Uh55`hwv(;$&e%e@s8-!NLibpCRYDw4OU5T+#a)B<s-dkP2-EX>7-AW%3U&f+g)2On_wBB&5(NQquj;u~w-'
    'SIgFd2je5PZ(tWmD^mQ1GonPxyS#Q{5Z1{9qAzpUpG>7aY?=}eMWcCawVAC@iG-hECC=lNN~#P!_hfgC<7Z{>nBh`WEte*j9O{'
    'Vm=<<aJwn3tVMPXfAm6K%qyG$Mtg6bk6B5`{x>>r<Q^g>zrA`m!>;#Pci0);E*URhx#Ek+VpYlvwcGs&3OE_93B=p@8uACTCo{'
    'H_P1PHXWC8c2K3scnK^Dm7X6C4S|Qx*Cbkms&jUkiN&>dQyTAs`1>$m0GX0xuASw5Eo`Xu7N!?K#s{qC0)?bUEhhq&uj00z@=5'
    'U@<@FKjUgr9RYH9gW_l7a`)0DVu6yhg^0qVtj8#Zhb=MvBB(Yf$1i@a+5b<m?S32?WcdL!es>^J3;T58yXtRaNS!5l*23wkmfD'
    'Vki@&<GN*Pu}K%8=HtamN;Qe)~-bV8s~W!R#DA3poJVFFIPbE(|UK=!SzqM)Rk!z-'
    'c{Sy2623CYi!Q=73;=$3b?=g}G`NB4BZ{OCWwR$%BY5kd`xp(>aZT(=u<nc|zVPm;Tz@~)9{sx|+d)&o+*q#Y3F&yv1|@n^w&&'
    '-5ETnC^cY000RdL9&RhQG9hvdzBLiaz)93@3Z5ma25$Pl?=e3YmC(Ha3F6D>3NqU8!x2Odv9DgE+f&nq(a+I=yolo5FeH8W)F)'
    '2PB`2q`(w$wl~BrT(x9mRwmvTZ;J$G_EHBp6i9Z1TexEu9@sWw{fAIZ`p`88&!A2CI-'
    '(c;WOM0Zw(|7yGU7<h`W~gf+{S%LNMi~){hFirfVNRCnL+(P!$wgQg&tr@k(cDmDGb;0nfn6=JNkGTVSc!3m#5hV7lD5PnNprM'
    'E1t5_rvg^`ZS&Y)t0#Yd(F}OCB*}L8vg|<`5ZoYCr7y_CrC!|;cdI;%peolL%AOr!00jN8__8IU_%j4TvYXFiaPVK_<ho7A1(y'
    'uR>Iw^wR5Th?0jAq&0jq!4+_#Qa-'
    '={Jsx+$wZw!_ajI(K576`3_Eg{&hE7LLWi#<RzK<PMYNpr8<tO53abio7r&htH0hkco`H^N&78#h64U_ex{m-'
    'TX9}|ecgqX%W@T8{Qx71gUsRP#4Hh`h}Zm&U)|3{z5N1k;fe1KQVQRkkTGp0LM)CRXiTRE^3Pe@(cOU_LO(+oLgOo^9UyJQL8X'
    'G`t_#u$x{2IqiJbz!gG_uF6&v~DcQM$P`@+mh%|8c2$u8Bso~g`YFq+1T?w{JZA34?J&mrvolnF`H6t>8vs#x85I*@c0NcjK>3'
    'VpuJgem4?QfdE7X8pOvb7V31Lxb@rEq+&lGyn=*ZX`%Gq$2g2RQl>NZ|@6_%y5idWt2@AitmMJt3PadDzl+Mcf0Og4xps>V1f^'
    'DBqx8TO3OM!QXTe>Rw0J|DY<w70o_4@PLz`gJVP`2Tc$28R00|GORze!m#Do}+A~sc01rb~%7NBb^eu?!mOEYagk7NS0<`c;-'
    'wJD#a@97>hV?MbaoFiX%!;(USj=~(h<}pwJx+Bm5rC8dXC=GOmA%RwQvJhK97Y|wWS6y5fTIho<d%udxr{tKyMJn-uO?4XGDv|'
    '~v;R>wQ@2;WluO-'
    'f+G(hvNXlclhSp#u1BbGn)BKdM>qwbjxkvV3jQ2&VJFvq~*!9ygN0Kv}S0m`0va#Krsf4SfcCsZI3?x%2RN>4Ks#W=E<`52)zw'
    'U~G0t<3Pz*sqpApy03KK-lCr_i@<z^E&zP~t9V-'
    'r>@y)`?Hc(Qs|k#==YN3Tpsobmlm4?FtZgIfe%2Fxam4B%pt$b7!aXrTq`nw_MsZD(3rtlBUky-4Cce9it~0a@k2vOT-'
    '&QYcJRkQd7+c5RIu`F>dKdMA9nfbcaZ@@LoTMRvfjD2y*nV2a#%(kUNE^rhB5+0z7v4`1Uxc%p#i9<tC@sHz&4MFmvEaLhekXY'
    '^H1J^4WxspjKHrFakYDp{rQ8e(~=cPUzXneOSAxLT_Wi)A?d#Mdq8Cdev%z5l#5z*O<RWwvx0c68g<;kLxe^-'
    'MK5RUpZ|Dhdk4uFRM~Sb!Pbkp1-'
    '5*>Pc_yA52&tU)(XZZxc6eUE1F4Y~ii<?dyeRJE=~@pF#U%{_rvbE~yEK5mo=Am951G?GGKtYuFPmqK1kMm#ib_s^+ztK12D=?'
    'rlt2fG4iK^?y112~rl!wRvkFlCp@F0UKEMUkeOAcS}xd^DU}=jyep=R2fCbb%5+dUr4uMbWjC9i-'
    'pbayB{jFd#j?MerW)4!8|)3svz_T47QH$Fd;l_@);h1zA?E{?w@PiBFJ}c*Fztl-k3y;2&-'
    'wbu3MxWW)C{jTjiLl+S41Ji&}^M%z0P*CKo~oA&~du+NoP5oE3=4pvA35ZMgce@j>T1w5y_C`R<9)JN+HbSwDZI{4PpfR0r>c3'
    '+B7$N@{RVM5x=yxo!Fak`!UoXG8lKgOq?&Z)I^X4G%7shLbwxqW05{u1*z&!uh7#RbY8>Xbt$LSZ}UO-ZYxHgP8{o?u=MEvFMv'
    '+h1^@7+~SCLKEGvg*j#WgEejDC&&E@LQ(e8tNL$^$hq)aC8}kLWGB1fGHt+V8DGgv(usBYLXtUpCj37aHw6w;G93^#uDekpPFm'
    '}g6@^hu4c8{N2-H0~!?|*dExKfH^b!smt-44PFRnz-'
    '5MQO5a#bk5}7x!3@rA0F?>WP<FuWGGfx^XctH@(IuzcYg)y0d_SSMX#M%z4LxmBIy(q6WChR>+H1h~N+ajegX;z#X3`5xTL*Z*'
    's*g!WA5P@UW|LidyxE8kVTSVgOM)dyz>6omcB>#T@I6=IwvsCD#B*k~iV^1(B#SArYbkoF^tfNP}=54k!7@tUTkSn|k}w#Q7`d'
    'X2XM*F!_qZoPBoRtsGK2>Wid9O%aQ=sF-1xdbBCI1AQj=Uch|n$=En<zF4_aQU8Lc!3%A9_ZzQ`oSa-XPVr#hrSn*_Oew&;6<^'
    '_QEhGWw6tgIKXOUcN2lVCE<G9W~TFcx0r45b0;;b)S590_}D69N}aY57*f<HCpc+S*pjm>{RsOyI_K$%2XMHJF9TmrQ#=`P^&j'
    '-c^vbl_f}#=U+iC_c&AA#LF7jGJ3DnTjJPCXE>yleO(d4lF<uK2isHgMI4kO&Z{0I(KPKjo-VKubzDUz}|`uk98k<G2?_Y-dLV'
    'mthg8BR~X>}|3y8;obn@Yc2+|Wz9`hhlzWO<(Sbcnx@q-'
    'OU0prNTnHm+MkRCu(V}qZVP0lVmnpqw*lx&kB*8r|$C{I;N(;lf3nOzGbjtYj`z6T3Iw9;J$58Z7iUnf%w9hn;*G2r3(<ozsy2'
    'gR~+1dZYBL)h2xR1q!8}>~MTjJuKo>A%nK!dJNV+@mrCZsM8E_4{Jl6C$WX$yuDMHy0iW`X?{FC#(@_v33x8@~m#MR+sM0aKI4'
    '*EVcgj1`eWfb>%^h3O;6wRhKie}|O8{@Fa*|Mv8FsRtK7=J5wq{W#{-'
    '0=J$yOAhW|eJ_d+r}i;LrY+JcWOF*PEVC}U3prvC=!o;?I2qIJ(h~{ZZS0-'
    'mrQQr3l5we^aZOPz7TQURJceL{ZvU{2tJj}=9A&SJgsa09U1O0yAW~8L(;VSverYR9&S673EhV0mY`>%6%Vvh#l_w*(PB+)}5j'
    'dAuUECSPJE^oHAWDoj;uR#Doa`jUyl+whu%`iN3w?z%#3<J_=rx3N_=v?opX!;7(aj_c!0>E>W|3n%hd(@Y5h&AE4+ea5ad3k`'
    'NMUE>i`VEDHSVEh`yej|k80-rv%jdX<8s~~SdY+5N_q8!aX(RV481+NdfCXgLO#4*Ky(`ybXE(;=Fno{u-'
    'fYQ`X+J^cY>(T<9s&-(Woi5jwCA43}mez)Abs`$2GoQ451@GM^1;S_7-'
    'HqgJB^haCCtUF!o8rz;y<LRY$xRcgnKKBAeeTmFaeiK>qxP{OQolqRxyPaP}+_G)(0K0vO8634EMV$timFSvT0o{2eq&di7hfJ'
    ';icBIE&xar}E@gw6wlvQW{*N%D8;XD0JIQoJ;c`IzLzVOod4S3e?WNQ%_;%0GRnnE@JUGHWK7Q9<%rQ0t1j~|HlGK)Pz$4U7ot'
    ';K}+AX>dA$&n``}koPsX-'
    '5W6{iCy2?oG?23Q%6c9|4xz^U#JK5v$TPxFGmnmO4Gq$ki?$SdNm(3OvRm_riU<N2)fcNQbFB0(E#CReg@s}eY7{3e6oh5OLUN'
    'v6o(C|6OGARpXYuSXh7>`r$+P@xq{yHwOga@eTH3RRYp72lAg?@}Ooa!7?OnS(Qq|+G*F})(m9p35bvO5^1MINSEOas+I+|N{0'
    '!aE1U{$3tNRpQ<!m>Mc(XRoF%hmERwfSnjDt*pk7>YIW6r`3Zbn1R4L`b5Y3WBLy;XGwL0Z)4%fDa0~LBV!o=xj9im<;5b8iRk'
    ')w`h%a;^p_Q7IkC$mqvSS_o926dF+8=@?&5fdyHBx*qS_S^AW2hgxRQZeVL*>C~)^#jD6tHr|?~sf}6l)OivEA)p1%(NbE6V5m'
    'Y+uV>`x>w6Tfy#rpQNooy;$=x^EkGcN6wcpnL2X{t9pomJ@VBE2`uIb=Mc0Uu+AkRIaMBTYAzKa>7gkA5Vn#0~^pn=5G4k`ilu'
    'wO_X%<Z|-vZCTHkH21T_1sQqf6ppHk6b8u~FQuZ>$Ey+(oRZJ{4JZ34b<jQu-wwjPn5DW*($tIrefF)zk#R1^|IU|zg;g-'
    'P)=l`>wy?kwxnzqAkY%sj7jXl1)j=kKaj$QDRq1QIiL`(5*agata}zRJ;*~#V9ZJJzmO|=pSVdX<4}#MDnzs^*c0BXO`xV9xH4'
    '}w^qtvs;Xh+aFFd^vXCI}*>IR%~XPr?1d$3E&;nfA0SvKVy#ZYbh#*ySzg-KlTre!nbuwI?UZ3mGGb@@Ze-'
    'j>=@oRa`GV6VvdYpjE^(4=BNbyiGB7x4wX<uQvCx?QKrgFTebQz0=+qmj67<?{v8cd)r!x2V=sT0Zn>dLIaq$?CFCwzvSXIdb|'
    '~p6Zi8GNWz-'
    'bs>iM0#BZO56<q>ScN_swoaL%)Ub9?9?t~<MCxpH3!Rdt+VhU&@2s~m@gPPR2_%4K2urgK5IxLq%B?wluhK2@Y&G7o6fH_Z-UC'
    '8tw&5(m}FcCGlMVBNrVy37yXlY4MX7?pKz!pdabY}5dL93>Glg%rFrE7TKK>^V*@(z{CM87nih3=l3v9iZQrA{&wpZ9L3n{P9y'
    '4HlHQI8?N4_OO#54xJ2=@m2T^V}g0&-v*1L%pTaAmLcFZfF(!4d_#U5$RNH#bJqjwL&NE2)hD<>@`u~94Ua=-'
    '4*z}r2$GW+8A{E>)2G(ggM{PbDJWqC$B%i?%^j|XoTT&qikT9mP}0pG&rjUtwX+!lSY<NY*Sccx$N#HoaWC#?9BqSACZW_>UtS'
    'n<gHkP%dF)D7c0vmp=kh%2(xY1jX;<f`d92+|Cwp#P9O(@74PoN_gPv;R&dgUMC@N)(Ql0v`>LGQWguFJwo%G|2bxeE@!>ELDR'
    '9gFXcP#;Nj(&0ERo=XTjd2eY)yiQXPjhw)i|s)WmATb%7RnZpUe7|d!)}P-'
    'XL?zHTeVq|;;(IUceN0&C&R8)C@x>h`M2k#=$^!#SS`QiZkPi#1Wii47-;wImgr+7mZ`z83kta_#|1-'
    'y&sVFZFvhTodyn;|B6hWPmC4#^_WXTiAPz39LpxR<lb8=ba^?!be?aMFIhXe}8bxcl5acYSI>ayqGr+XvMBib#eq{hzM%<x^LH'
    '=k~T=|E?)Fmv`(1W(vr;>WtqQ&ySN@e(X)lFPPN0T=+3jKSvj{G+KlCmWa?$I%V{CUCbS}PdgOT?k@i*yhd^*YQU6hTrLw@K;C'
    '*cJ{eI>^fmRyZanY>8K7sBatO%h8=j=FZLhe%$8E5;EU4y}j<4TTMqtXu{|_(NoSr3mb9Ib7k%%0nf2vtR7P*x1f*>a9LcHpik'
    'yB*{7|>dGbQW5Z)J;s5wJY5kuB2$4yvE7XrWVyJ4Skss>V#Y_?5wue^NdD{QLN7pn>o_<>Is60Sc#xKns?!L&T=sg6@l7&XD5G'
    '9=TF4F?`!ih#3_NNu)8<KKoROf@fpRme(g;E{w2(1JRY0&6k<2+B_QUaCJXIP)<FwInQyOtcKLn%zlj#A7KH{dDJ%Q~6<OaU|}'
    '2RVjCWRT<=l?W=7WfpnRTr2mG(cqpx}!^}tXmDS#Bi9n1k+fBz?@@c{!s!UaRMO!HD3aG5Uhc3QUCV2F8Xx5JT5{{a40s_`isw'
    '^%Gb!7_EG+j1-IMwLmaGF=vU-~ISiV48&=wZ#|yzM%PU14zl=4x7x-4J$+W`~Es0WX5V<2y-'
    '7%40tMpywTEUog<;f9gNm0tqZFyM^qRpEr~rf_BY(ACteE*mw#74f?e(<Ljwcl=6`bA{U0&Ej0MEFHyreG9<u}!X9;}Y|8tduS'
    'j!cR8WIPbL`JDX}Gi%dKZy4MMqYOI*41vKl3on8WwzFUrmx2B|^H_25Twk{Y^JpJ{8qP2N#W4Z=Rj%!txPTCK2;k1TM;Dn06GC'
    'Z2D(B(N52jy-=^4OsJ<8#3bQBK}gpZemnhSOw7eO2-E#BC%6oa<Q`0gQ8o@MTGKtxUj72+-'
    '93RZ${KK6@co0}^Y{zcOYQ#LvQ#-lzJI#?*2<`^36N3&_Y3Hpq&AVIol0=UG*wd$E1`AweBpPE(nU~L-'
    '<)QF&W;2rLs=5?AGYTuaGv4LNQYrW->HrmklfVugSr6AC-gg3NKMCN<3!~NyBGK2f`sRhjU1EMOhwQN^apN6HOOg2ig44W2fld'
    ';qh^1G_>zN+8L63(pLdV*0N$geb;@wt$cgd3Rz>kzo(LcE5z9^>e*F1>O#u-i+T=pjqQ5UKZE`oD-'
    '?bsZ3M3zbn*mHXBb@P2Ssxk;Rgy`>&2*Yp7l|9+90W!{1ppNK%0WVzt+3;9lW=b{)pH}($YR7z!KAsmkPAK=2N7-'
    ')!=x$g(f7$$m6PlzBjy?w`c!)4V|tFM&{XhWgF_e;vE4y4fa)fSP17umOIQN}y;i;&)ZoBbTx&>gzty@f&I?u!r0DTGUmiJ`i>'
    'XwD3jaUt&H%1cZ~(CI=>IP<ud>PQCN19oEC3uAO)ZHp$2Wdy&7-m(n9ff!I`Z{C4D<IGmH7r3DTy6M!f{-'
    'hNS`%LU{xc;AidprCL>c&RigvCc0iU8nr$JW7kn%l+WH2{pTC<RH?zGxIy@;IQH}W}Z;IBNKns&$eN*B{hD+w(Wh}_`8=%{t0<'
    'QV*&zyu~s(sl0Bh&>r#VBG4)ndHY#(FH$9|k&Xm4ZxxTmP1-'
    'A2T!8XKL_*4INV8ET*^`3!y6OH8;bR3CU?#Iio5niFoa5AM1h%`>`{NRV2JtsbS8_8c!e<M<wb0QXsIqMJ&3^0UjL<sQ4hKZm@'
    'mm0WcO}Xs^1?P^eprH0QS+b@Gf8i_9_DKpgi?$vA%4X4~W^6T4g1QHl-P0%6W1#AK-'
    'L0;du6h`qxHfurcU2_c0@%3~sJO#<fuFg&k+*(cO(y`bLW>dvJ_wb<(!ae5?7DHXx1e!?~4J<ZV5qI2+(nEqA_O!QS?_D!ANKj'
    '1;%4A5#f9H-'
    'vfG<RxN^tmCa=dfITVDD#2=h`h!%Hreli4sNyA$6B;mhvxUbNtNN^R_ns=ZByL7yUvtFywL_ma~{(8vcihO@u`5!AD)rZVilL;'
    'Bf>B=iCx3R)zZj1TqGmAVuY9EtSc2{rhD&9OTos0$MvL4`cPvxdNP+cpf@0g^^``Fj;D!=zAic@r|xcT%lvTo0{Q?-'
    '>n@J4*3CCWzmeeBpzw*Ihds!amb}U?T7r#AW8z=-a>||+HNK1S6Dt;KfhnkB|zg@cENlq8}Y!l1P^L1-'
    'WJT1)HJhFFp~)`Dun=sF%^;2onW7gbs8YizIXal)o*6+7qBC}U$Fm`z)L_52^DfAmZ|;377@MRB+@6D(}9Hi9+*<^&&v9)imi?'
    'sbAWf-cf=(h8VuGPYP&lnZ(s3n%sL*iaV5+3Ooa?VUR2G85a`?=x?K-'
    '+i_LUDRKJ!m%Sy?5NqgiiepO(a#>*)(jF&rPSf;EGZ;WF$-'
    'Pc|(H{n@n+KeY|r717eOoHV<##@N|!Yo?wl+`BiOrdjQuQMkpfDE!MYZtaP*=6}@mFVZ6vsTl;(AA9mzjMgor0a)_IN882zBr{'
    'w5;1zZXP@j}3Xc*$qYW@{{(H>GT*W5F)^ay+^qP|K(IWxc0&%qm7PB3#kER?5E$-'
    '`cuD{t4dP>+b!u2X133xaeQ2?^#P%*LRY2*U#<s|+h#LDZ)bE}}c<rNZSeiXr<X~)byyYk~x;ztKOgsf){E7(ygRsFWe$jmw68'
    'p5`PFV^DGgK>grtlc>t-SGhbh&n*eNXs0=%74p0?UA+ReD@UiExRD<Nj3pl&Eq=c=2DOF;r?I|ohKh-'
    '+qDCJ<SBF4aX`DPwVs464uh9kVQSiulD!{kz}_A(rWu;x$`(4!8-?jX-LuA-91E=~Nxd-V!Nrm%TN<Nk;0z8-'
    '9nz?XpVb)wbGQPxaXexm`sJN+lRN|=w({Xz9!EJq9PNiH!Kz)*XEk?(q#AKG)y2r{{U7g5H-'
    'O4lcqz6hm21d5W}OYB)ev&ExHEw*s-?~kZ3S$bHp%>k6<CTxImBX<#LF;!>G(!DEmkOM?^-'
    '`ra04nSsS>!d@Z}UFRV5^M>Uy%?TUuRqeeut0S#0^md&K${;htrpVYck|mDNT&^sWXmG98r@*oiV$gUmiF4gN#2b@l0KM|wXyd'
    'EN&A+_6n&W_orVZGj|fY6JzWK_Pag4%<rO5`@0pSz$JB$ke_Mrs^V0P4BKf>!g;(Mu;HO;sdZvVptm?i^PeP><VUA?dU$~sEJb'
    'J4LUqutxI;ELnYehB-F-'
    '9W3(S8nM}n;?0B1u88~zMu5>IC#8$mT!_W=HigY}#)k^X~dw|LKe2U*Kg;9a4FA3zl3c)rs$bOYA?6bHCrKRY5!QN{)<Wvj!nC'
    '3TB8@`ArNB%9en%ybngtIE+lcmw%#3!Pipu1YApwoUeJ(;V8L2JOV^Dj^j6|cQnE;<?WF7^(lxQzmMsNl)Xva1W35^_Vp$Pjvf'
    'mrMpIbdJ8>u9=TlOOHXgj0ZYxiIJ(TuI@u00t8lMu`i$Ge}J}anQvXF<%8m7hi?9jVV%Sd)Rz|^dS02L+-'
    '08?tCJozWg$qji5#v(3ippKylRG_CN${8YL+p?gvt{QcoI3uk2(426qEf(2o9*Xd$|aDoSwA`pGAPa<vpyrgQs5x8<>}&idKeZ'
    'K%jldhVetb%kcXcpW>5Y!O>3@66@^%HC}%rGdCT@QGk7jULwqE*Pt8j$fE&{13*4TqN=9aI4_Yf_v;K7!qzZiS)Sh(xfn=Zl#y'
    '0q#CEcuTv!$}Wa0n)@>uBABj8O9YMSeUFn2;GbwF>5OpMLy(Sp;BxYbiPI-n-'
    '40GdLm1pQ9yq~h_Obe1RivkHcton(jcPFYT*`9I05KYq+5C~X_GZ!bqn?Upaau{;};JZD%+9zVnYI^BK$z`ek0q{lXf7sh0UMr'
    'HW=?H=mZCy`B*o?D-'
    'yptyLU4xy8{EU$(6qZ^gfTbZxt8+z!T_V;a0{qH4t!vZ+UoR_*rGpI2>PD@BwDbH&;<nhs~=+DA_fu9epn40Btc2|Dqmto%jm8'
    'r3GHFwWqcn-7x3{|pB5}6i;NXyQmNjqZ1?OALR8ABlh$vC~IEb6^2I$`mUmw@%<WH@NnMt)>159vw;m$ml?p)%XvcY_*n3cg-'
    '3s>532zXW(8SCeeXHR+-y%9~kU!|!_+IOnaA6&>TM*}?bjdU`6A{gJh~J#5z7&&YWg%B8bQOyA6}rU<f2lD<<;Yd-lO)HE>^wt'
    '!uY&Us+5d*w?<<Ec0XvtT|TaQjG;l=KT(ewi*AoM3nT29WQi``=Z$%95{6Bs5X}<ZK>~R`vL#pnfM195iE-'
    '&laMXAZ)UQ8UGA<21Y*8Z@tkzQ2d`COnjZZM7<0GkuEe)Vfd<Kw1PzHOST;$Ujwl6fiB~&!5;hJJvd=i4Ismm4djYVo2Gnixn|'
    'ShW)<6mCljQ$Oh&l&#zzLc6d2!<M9M)3oWSmVm#;P|q5wTGUFkCC<q_mc2e?ea+p>M%A7b_iBadJz@q=eS4GahR+sGS5szdC`9'
    'NYUxbxyxmY+@EX*akX5=*)9Thpy|?SSqqt$0ai3A^rk;s6T0vy;*Da^VwYY-hAD&13{xGNuVmw?N)D0Rvc<iz<;`VFa<KaCoF+'
    '%bb>CVYxkHw6oYSQC5<^X9?lI=6FAfVsvI1e1XtWaE<C{gQh)6nrv<PVwq1$|u8y~eJqQ6kKHvkiH3r0gK+mx5_pZHzW}~dNLb'
    'X5#T8{=Jdyz4dCPz<EDgJPp>o&vQb|jDDH;7ydbsJjbtM^LWXcY%^zNQL45C?lb)ppT?>jTAiFCC375ojMgkM1h^i=duCC#*da'
    'nl@<U8rDj;f{~G~<y|Z}#Fx4!dRcSS?L1eIpX&wN%)ts<!nT!dM?XsW@oWPWi0aaXglC-JotpxSA`^#W=s&lE_%Z$om2FxBeF#'
    '@ZdSo-'
    'k82Z9IZX`ruNKu3&{ghe|>yhfo9;QlnIdT6LUHHrkGr>fOX5(!V1TL}%5^9HB2Ev#0Im?K3JLKHaSt6PO9N6#hg3NhD^hq|7mW'
    'e9XrxGU>)oQw6!RZN-1nYK_7SXb%aPO|D<pa*W%;`)(s$^xpUXk9r9l!FMj~1pm8hIMrGo>?^s-A^3jDk*wweR`%)JP-'
    'f3nyCBXB7_=02Vj8uuLVl7)ooLZNd46><MqMCy&L&>V4>bUtL^jRb6}QriiAyX(<i~wfOD+J_faddt=l-!N0-'
    '+$PJ%)`01rc4jRHMrHi)q1@CEblBqo;yN|AYZ4_zLuZ|J}p@4%kD;L@U>%&HcX)I$fCkUM<`0}x2+kDS%hvA!CXju^q9&B)~XQ'
    'Epn-waa9O%{H0na#PS)5xU*ItaU3gP?moD`*mDMXyY}Zn~G0XfebDkgxb!!ZF1h(VwFNsd!d4EaY(Y%t|T4s!oict#HvjEtDi_'
    'H0>1gGH(?<cMjJ{Jc#_#jO=+xZ-'
    '0>+ZOe{Dek{23xV$}7L4=H%OMEwuge(Dmj2CIIz`zH>>DaV3e$p$8W{ARG$2&O;4lZwT+nR9eYoh6a#j4}XrCn2!2!ZZ^8~J<$'
    'x~Vwk^-z)>;l__kjP)3Oy;9}ht>Us|QBG?3SX(5a*yRspSE?%Js!3xI8n6G$d2#4_VV%0-'
    'pO_>Ri0Rb3&%rS_4KQk!bWTpa0=|O*Gokj)BWs0c(79vbV9m|qnH+dby+?Q4L&a#F?*qxcEDPAVza95;xMOO1GqS$_a8bg7`d4'
    'ha#C$`Vw8ULA`7>6GvtdjO(P9}^Ta2e7RfTZ>VJLjPv7f~)xe7=^F}VC<W7Bxk%I-'
    ';vQY$a(=eSlFnb{1&T^xp;DOU*vrV|j55MN_7m}~|)ShIm9cjOMdhvIL65`au@+ri=EgEUIj;;JV~7QQh3WEP`<?KRf(TW4J^*'
    '10JAcxh~#I$VT{&+;DAC7G0Dd0zQ|bUqtWcF{VJnE|;x+4b8t-6ontVqZ*-Z?7906-'
    'Fkdi;^2yO_mokXqsZ(&56S0;rR*vui2r*#VeO3gukw-k=Ty^x`-'
    'T|=1In0SiOF}9Hs`gVbZmK=tP$p8VPDeOJRH!T(ix@iF$u)Ug}~FOqv=@apijzt@7hq2hkllOfzswwJS*$Hv0uSw*oheHu|0}a'
    'lPw~rou|>Ipu<T00*pWO)1}dZrxQJRj+BO@3Lo53P}(nUT|i()s8k1=j}Y>Z=i=U;h9xH3}GK?xUmbTt)TN>i`@H)P5gJB<Btf'
    'zTUg(4O}`o!wL;Z|;r>HI>c_nb#0-fD4@HIgDr-'
    '%&?{MpB4x=y#Bl}FJJ`8g}jT^_=BAGD9KH(3>^JjHxfct*IB2Gv77J*UTF<gIF_MJubkS6iuun}RLQqA0+Oc#`R-'
    'coRe4P0+=4p_I#>@bzuEC`)porUNWRC_?riB3=R*f^U@1iQ<EYb@l{^wmuZBnES<0#o~Cr)e~?kZ>YM%0obWT1C^553Un{Y1$0'
    'K+u+xF+yFgCr3SMpyxYf!25Sm;f(MDYL!sLiYP}4x<?mmJBf^l5y}A;@0e(Zb#+()Q+}M3_9Q8+KNw88YNJQDbzJF)P8>(=lDX'
    'BMXf>16pBWny`kCP20Y$jGT{k0DbinT2h))@X}`)2=&&A5t2jGEu>&OP?{{^5X}k&)czf(CWmY(YlVi8AnaOv})iAay52Z`tNV'
    '-b+8#k&B;Qq=dfu5vNPurx2Uxvqeh|P}F&6!_CtoZ)IHO>i;J7G2g{T8XZl>IdyZ>F~z<ezj*8pnS1nLh5m+D7P+tQ>lW6|9*Q'
    '~977};<_K*GwR(Z~|0V4W!$>n*u@Fsb6U@b-|^L-'
    '7YyO*?TDyB{&NY3Dev7rY}!J(b`tcQrQ*?qpei8&Sry{AcmypyT(>q2B{A27wjpFBFtT7dp94+7qeW|iyY?g2S&?x1>c9>ALO*'
    'kvcDiD>|v`W)-JZ<F|InJo4<gIqemA-T4aLIP-Q%%9gF(k17Kow=81x)>@OcgY#BWZqH;*G_gG?~yqxXU@a+<%CRm>dx#+9O-'
    'eQ%Wy-%eJrz#^Oj}SX%MU{jgo{S<buVyjQh{Sjt7<Ptky%_Mti`QTaxVnk-CoQGu-YhD^41Zl<~asB3Gu+6I&lULMjF3*u$ko@'
    'VB|UIAoacyWGB!j2ILVA??7>Lg;Yp_M1Wa^}I39e{{mT;?4>DPx)3iOqcE4GS_MMxS6ev&Y0z%v(=ymb8CJO;;*C-'
    'q2O`vZD>jWO3uH+Rw*Pbf%5~_pqm-208bo%5{}bQA1Ooi(*Mm}!pIeijuNcwi5-'
    'qkA4}+=FT<?i6P(tvwa^0e3O|;<{ZC1gYU?Yy7ZDxG+FE5n856mRjwvl9(mS3CXYegE4r{`?ssBby3_eFkCq8<XkJJ7d$cS&;u'
    'm>cdyekX6wL=D+_~5_%`Hf9|lf#aeoJhDjZ?rBkw2K7rO+X;!=iVFOKt;$W9qtL!!P+z6Un}=_WDq3MfL{08QSRWh9%nlAjWgF'
    '2&3cSRL#%`Xhb$u6_y@+828e+ZlB-gHFRp!a#VQZ(^r{VyG5-Rx&U;=^;3xvL`F|_a+KK^o%s4-'
    'g6gcv^(atI=kH119uE<WHyC{&<*EA*l!l3a>PCOU9H#bbimV1&M?rqL&!E)CtY}hV6;P6m6Sa7zYJWW69;ICt>dma=rKo)m<2y'
    '13{QPybaM~<@b24>(_M46pzl!7jQ-A!#zk;*4GYV2Oc7}?Gbhr(2o8<8eYVf|RUCJ+JRo3#x5Hf=w@=09<Cny>jd<N-'
    'Gp&z0qgar<wTR`IPI=99KU-wiLh5W4$T($UKX)tibPL40XaaQPRqUeE*hdV1>ZzJsCz#_G3+JL-'
    'VjE0ifZ34$(Qwm_`p8$&E*jenUB9cN%D6!~=QsQx8gFD-'
    'HDMfXskBs#fV35!l>UV(9~BiAhva4~@9{76YoXA(VI=AuF6Mn=}w-}JBTWxXKboKBbl7jvlrRI$7UJ5RLo-'
    'vF$IWehv1L+(94uo8{sn(WWFJtxePuPWyp3Kt1H(b5Tgm_-X#HV-iwCep@uA7?2WJCzd8>e>ACW~18<;k~_M*25H7%xS7b3)+0'
    '{7X19T6P+X{dqI5xaD|_Kip@23yF)-v3L(^Mc&z2NpIx+Y?l#_cFIlt$?UJ;VATNQ93^v>yJJbHV&-'
    'lp#Vm3UUe&K{OcKrJV$Oa>tWu7U=gVO^5PL~fs0-'
    'Blr8@&N4bBi9XO|5smkoAtTLSdAu&(b7P;R?G^!*5aa4`Z?kw3={D(@9lI44F|Ar6-MO{k$GAh3a94=(9chp>mXZ>ZmdH_M!H-'
    ')_M2g^YDoH6)BV@Dylfm*^}HCDU6{*4vo{8=KcOP%bz_p_fi9l{oB@SU0L9!0mBbo2xbcY1se;s^f3_7v$Udg>lY!-'
    'TFuBFmO%AzA@uF5)qU5|7J4XTL^^b5^Y1%f*eXb7I7R#t>1~|FpCetGFNx^U8&pOSIx3zzdnm7H=|q1rpX%`nM-'
    '&YR#M5=m!J0_&J+8YmW%_%!C0K@!#w27n;Yl0YNM`0BD)RXMb#LUP$QkF0hGFChTI}pNu^|aDMCJBcVgN76$55jE|D8aMsPjxg'
    'r(eb@%3Hj1rV_We0lGX)iH_m8i(@pRSzoBBryoTYpp5M<?aG-'
    '!AS5j;!mHED`8b=a2~vjpv)toD0VdGaU^g<q_hLq{DFqR&S!XrAs?N|_&XjMIl<Ca@jLyEIYaJwBM~^Cvlmb-'
    '(l=bJ^5NLtEnluD2O#EPfq0dlt?^=^B^*95o``8;WOA)`2Y$cae@-'
    '3g|k7%OnvFy1zJHSLh`A2IXeE9$TP+qUOpb}d7on3+@;sumbWE+QD{`$Z9s@@R?l7zwBF-'
    '2|WvJ&?&%WC$z9y74xSr_mb0E6H@SPWbQVjt}QBpN9Hiqiy0=MjF8KF-'
    '_=K|q}RF&=qr>b{sV&IovK(s)id<KoHwDVO<r8)mp3E58bF1v?33X@igq7h^L=^X;-'
    'fK=epZTK>?N+A*LzJSj5D5}GWurq#)rBfe;E&fF`~qKKimx-xIav6CKtvBS3=n*GsJYNx=76FiP}Y52VgHeo~gV0$Nr!9!QYzk'
    '11~r(#d8(cYF6{>W|9Zv&)ulICXz`GnQ`bCEXa<?0~j1MJOUO}|2P(89>g*xV+}M@lrKY5a<vaY{>)Dxb=z1_CV^LyY$OZxu)-'
    'ASOG?@gwK|ct#P^%$}+FG(zR`z29&&61VQ@_mu_12#@-'
    'WHgO!eUMdyj%O(H+M1{|@Y<|<(_|)y2F^w<fKMadMx8|DX55{am8w7a6wN35k2`bfqHETRqp#nfZn7(yR+>=-'
    'R&lS8Iml=;C)sxKekFXaY?55|X)8IEz5%V~4qhAm{rFAf~K&(Y%)(gd~Q@Fa}WS&23p$+9X<qQjc=yt_^&0@(UN6Xp)sYZlWrF'
    ')j$`jc*7+FJHkK2}Tc0Yg^#)LI&kqRTkiHF*pE{a-'
    'K|hbY7h5QLfb721{{Ld~E%O~Bp14TU+6jicIXf8nAo_Pc`N?op<os(M3}I)zsynDNdq|B!1b$IsN5y_{R)^n2ZDkO(8kn*T^U3'
    '<BXok5HhQ6pH7D`%{`spY8+YN9gCJr$9}<3D`%n9G@vqu=vGgTTPWAI)l`&pWbNnZn~EbKc=UD-0+&~KWiuD&D0smCWC43-'
    'wp@Pn>9y7j*Lvla;_lTzFzGirO*~wgbxH#){q#DULtK53a&n{@J(X!I1bW>I&+V^>fNbi3F6)O!k)mc<_+Gb{cnF7=j;^PQ_w='
    '9?Vdq&``LFyBi>v8PlYy1qx6<rR!%fFbADBSa%GfHX{={=7%t%kKxwEBo4{To2~YO0YN>c+x~)MM0DGdhuD%ntM$Xj=<EP4tOR'
    '6>TknpKGzh^;iGbHB>oXARlT8~MHS!^Q?`;)S{3o_61X&57|j)odl-'
    '(^ceefE*Z*0Ku*Sbbhnn9;@wVYDfJOgqEEPFN^}O@h{X(BNsZYmv#F5!vZ`u#TPM?fblFJ;J(Lc{oGTzzwRbZ~y9ke{m=4JW({'
    'pdZ+MX&}!MKJ}X~$Y!YxpktsDtO^NXC<`1}`2`yPq%zm#!J*N=7HwTR~{I2ejA-0dd+dV1EDCU`9M36OoBjN*WvjsFkZ2-'
    '7h^l;uE2?hUlZ^?#S|DzJTT~#0$^l!3_FXTlA<KGV=Z5tHgtK)-'
    '2OsM<aJG~<tujg{wqR$H9I!w43NR>sb{kslECTrLK6iIr6v5;i8eD{JlPZ|qxi<Z^n@YSWXFNfv$caM$(@LGHfDDs0qhl3P!K-'
    '3kLd1#gt3)}g5XD4q3=tPYsxfTI{2v+eU%;un7%fi`Z=aRRpMYr|i2hHPQf$+?Zp!~r7M64AQ@o1;Q(5bFOrQti>?(Gj!Eo==('
    ';N)7wA)E+YXHI@R;Y-|GZ#H7i=L%2aR8jjc50+spW&={LZh<-'
    'h6fbA>Hg<ux;|{?ZGsZos`Uoy0h{>v8^5M<=^3&7zRL=VvBXN@YB4^NC+joAl`5V=Zvz64mkg_I{ksdT#nl}0b@2M4rJAHK~73'
    'H-$sI8B7uGMbX%5U&FQ|x!wbh`%$&|CiZ>t-'
    '47kmH;95bR9PbGK8{qSP(b&ac`96S4HJbkKdf6sAr}ERo)9!k?bH1)afK80Ka!tr|DnYHCFYrpT1X++%e}8Lbo?-'
    ')L1$#PgrhHRxt4s2O}3B;M*8akgtc`dw9X1Pi|dxC+S}y|sl!`C*rFoX)qGA3>!#&pE#(Fz#Oxr%kRs29Q}R4$e8<ssz!L8toX'
    '0H)@k*?@n}^b(l`SQ4Vh7mzkpjFK3d2LlfyE%f3Aft<jKGAsh>1mXn_=*;aOV!tjw7(#+Az7N9};aM--'
    '*;KjpcF~c0BQI!oXtn*Eue%!n$=TNJ<d=b&fp9x0q-'
    '<Dnd5Ac9>X@EwZy*QF`@;Quu{%f*aggXheribPG^zP7+LD`HOFz!|>)m0&@98JijDFghzW0U+Sapf^a^WQ>aqMZOhTMSz1B=(='
    '~8x(J#W5w&n*F}I9Mqeg%<$TX;_fx!h8HI<OOR3o1S0UK3RgTP8XxpeH2H>j)Gp})N2N^Kt^NFg3VaRZiO=c^{D-'
    'XRMbTAoJMVz#1w1cb$FT40GHQMoyC%iD+7^|1-'
    '=qC8e;aLWtOem$aznpxdhZ?LvSn93MFQS278JZ_zfw7RB(>3$MfbWG`w_TXCW6>B58uQPQp;jz;(p~L0Grur0+^dM(cwB6v$4v'
    '`(PGP&5m<YJ|C)XKlUs4|ze-'
    'b?yRGz}G^H7CsrrU1W{SpTn&z!2gMn$WH7qWefT~*b890_0zPZ+P<dJlbl5CY5y(I9qF+Ne2U$1L$WW7gYAwZ6fN^lpb-'
    's$j?i;|v#bCyCVlOt>o@(%#i~fQ+jW1pguB(TlfPX}fjxE)a$MDHGHP;)%Uh=Lna$F<;EI^-'
    '}(KK=4~rwq6nh!v5ygCvq!=je&pQKCRe=;V3&{`5@F_+zW~6b^{oah9S`+B-'
    '+q>1&ELX{pLV<PvVvK5*ypXF8f4bQ1!=}Ia~V%v1yvqSF~d1{;~x=d|w!8)qF9=7&rpbrBeL2!VDjM;ov_FeS%%lmc{Oc1@HOj'
    'va;s4He>6tHU}#OJc{J(fJ3ZxcmRx1Fv&A(<sYA_OeU<qUCz%JOTM{`>#kDau3m0z+uDAo5H+}(k^`za*Fq#dk7HU-'
    '5^(JxVw^AR`za02I3(BywzpB|1q`{j^~UaDM?Rc{zG$c3m=aUt6P$gvI1@ZWjcjtMA3KE7_@p2lkjl1F?WcD@lxgB+Mxg47Fe@'
    'avk>6WHA^@zo;%{>1E8@G33H$=z(xA~$^z4X{x{J0F7*InEyO{Frbwlpo^43G5SpAkV0rKt41dnFMUjTmTJI>bogsgSIfCQC#p'
    '*ad?q|F9=+s1iQT<S(Pc|`q4>+PJf334a{W37#F{gs2$gSJJGjA#YEkj1nIs&-'
    '$ZT1yyi{|U}_$H;ZD>4oA8C}ke1ROAj}_2Qh)W}uS6ZTUvE9105OVz63^i(YyQ5N^4of`p;GUf2_^9z~e=uWT-uDc<#Z<Fcjdl'
    'LByEtSI6dQ40QU5pg2xlFP{dz)qOX=2As<M=ZL2$ayku+UQ5^)ib_i#XiKnN0t(6_6#mbART#E5nGyqqBA(65`~TNua>#w=b!>'
    '+eIaro4;yxkg+wzE8Mk}i)BDM`l_#)v^ilzy3c{ZhF@FSf{%vA?v9X~dYbmxt@#ShR(<$W;r3vG=*1NL#TFO%~q&W@tirR!7Eu'
    '_4cK`A_m{(kMpY|@FhQOw4E1i{)7>duv&_68Yd>X1zocG3D~&X2&b)1(ZHKbsRfXWx)R0(rspf>+k7e}_2j$+M+$o{>pnz}eto'
    ')ES($T4>=rUu<8w^S8enNi;ytd4~+-46YIc8iLp-$k)NcG(!v9u#ht1meJoM+coJcE<0}o!{)SWsj7jnFh-CjL)4*5hDcT7fPd'
    'sW#ACu8swiwWA=<KuER~*1+kRaT4q$S%#(ni$LW|M-'
    '?h<WvL|E+9)3U>(^bL$6g6%H#P9ynX9E(58ibpqn1&UIAL}9!hgBdNDM12Fq?BFZImu|{HCPI3~%tXIGw@85@w(0vn$PTB`2nP'
    'KBRb=p#1p$%$v1$~n?zS+Ci|ac-bjzyYia6ZUyUz(P9`IiZU351rnE5AAO~uD)yqn#P3)iy{8{Za9v<M>&M8|tek`RtH&8tT*n'
    '}pD|MdAFR>ogF|ZdW^xc#}Yd$rDB;%Ts4cXvTL0YiN2(_Dn)lKrrq%)w3SJ$;Uvqgkq@VCURJz9F3cn!K4sxpdOwuia5bl+8<7'
    'LlksT`fHb4QmRT{iVI@c)oj;Y_<zN(E``Hs^QRrr+)iLqR313xT1rg<|%)A`}a9)ajxkk-YzcX$uqTNhW0yG#^V;xb_nKauvtg'
    'U^#x`d<@p^2vmMMms*J~f_`0=KS9o$F&8lGRPQr0{7e0u3u*43yu6?EUrlk$F6J7!vrNKj|R6O1K*O$hLTlH}X$@F1QSNZP2e@'
    'B+*&7%1`Op%#P7Zi}e4`G;(pui80R<h2Z@;O8BrnR5%dMKXQcrn=}l(jz~6HB!16WFoL}AVdM%aOv4yky=UWI8&<d1jrh#ESXZ'
    'k^SvIBqF)1}~s?hafwwIcp0;TGr{|5oJ`FvlKA<OE5xGwxcnu+x0DxyWfi=s=G^mg*ax-'
    'ur4F;rL9MW>xd6@@$g6DS5@tUp<1hb#XPvm<(>81sA_Fd-Qy91B=?Rc)a<JS6i4uQ6PpLl7g$1hN$W2CnA;-'
    'R+mUtwM2gyt*$(Y~>N}L=fKT5#;`pmVZ+Ue3@+lEd5n%6t2oAKXW{Q!Rfs+b8blkdFL7_;UJ$=_t52EhI2Py>tSR+2PIQOCy2I'
    ')Elh+VdWvK)yyT(6<my<IRo}GGY4R9$539T&Q~f<OTjgEb$U6D8jFb6K-'
    ')54RRq52!Dx9s@&M#BKt*8O+;%nH}_sLrU#wd%jzyeeMFvW?=F0=dZiX5)=TgNm*vbu>FXtM=yPmAF`GyopNjcVi7j$=WMr~6>'
    'R5SlXXLgTFTGeEYI+-=;Kqrp5@=b#)L-NF+B7-L9D(nbHYynM-Q%<u1zH!uYkfZMWet<jh-mKTVe(Y0|u+sRI+su91}oZ45=G6'
    'gy-'
    'P0dcFENlJ1=n8e0JkQ+TUG#8c5*8>^*vOY0+st?RP61&E(~_fG=<?e9#O}+xD*cHKBiNPSx&!9F{q;+T)%gHF=z)^mx2ip}ec8'
    '9|fse2PE)}n1+JAgHgRj|pDTxi8>r3(m?mC<w&E7O*M(!I=ngxRoVd=_E7!wqeA0LCc^XQub#qUp532IG8yeDlV0Q+pPa!<3vO'
    '{J06-a8>dv0WWRD`t)rRTEeeiPOJ`L{8zJ^Gt>s{H&DQf)DTdBu~Iq;dEj%))+y4F%=7E2-'
    '@Si4hrv<P+k~_g`=?!>Ug=^J#Z1Qy(GZMgx<qS$a*XIVB?gkR%*(bzYsbnXr4TO|GFmFP!VyhSOBNvFD8>gKSU-'
    'N{T5nPwLo5C^|c!82xxphr?$ve+ep`A|81IqauOSeu@r-'
    'mTMkKA1D#S$a$PGj<jGf7AP4>K7_t{}I)0tW1~d7)EEQ2Ni}$ZQyHF24{+c|u@BL^5VgDiWgUDYeZfMKUYic|Eh|cB|!6B}ugX'
    '41Y`34zGQinmFkuim3#Xbhqc?(k55Hv9@r85VY!re3#I>K7TohuZNPeOUFQD2-PtXMTiweA-pgFU0DT=V~*o{ck{9Pv2^=h1IV'
    '%4`$k6Hm^sH!MPfFsAjw9jLtG_;v8Tm}#olCH{ecwFcc8F_mi#DOL!>Al>pX{6uXmIPR04ZZ@yEw$_gv9=`_Q+^ZP7<HbB9VER'
    'ZOYaUNt0-N!4Za@bMIU!>R38z!%xnook5?!@3eom3!$JJbfcUi^mD9Zk)@z<Q)iN#19^E*=Im!_At$?-'
    '_`2|CC)2X6Dg|I*zNkyrGpuy(1S%q5z2#V$yN(TE|^4kfuZqhxM5yTL|61EBppj(+xLjphG@zrfw59+`qL|FiI^{LU}(!(o&pb'
    'pp~9?S2dBE1n;kPz~feH4Nz!;wp`CrjbKfE)nRzs2i~Ue;Atf1fEd19`*|p+L_qmMNl_+{;Q4uURNxfPnzubvaVq~63WH`=sJD'
    'py>wuS0Ma-hQ%eU4dE1Lkx*~`3yaFph^JGOySFZ&%eWKv1_`|<tl8yHr*Cs;iU7W$M+5H#dKu$MEz=Bi8K(WO)WFaU=zlMSuPY'
    'k`I=W6Lcn!OWUSYk$`tPe4fiOq@c&m_LL!BN?L03l(+`Q_ozZ+&>Tkd%R;58lR>_crVPNDH+<DX_yW<HU8xDpn!CCK$LPoD-'
    'U#7T5%<XYwGBCN~fgEHJ(YV~EL>!7o&N6xn<xE3FO%hrcj-'
    '8Tx2ZdND(NXveC=racvH5P5Kz{eB8pd0QYe)JmC;Z$Re5U60mhW}_2~|BH+Snkofb79@8j4`BmBkm>uO5Yf@P-'
    '_cr7skgzI0g?yKCWbJ{6||gNGILIB=Oj~x25lLq+HTW(X)7k5)-<=UvjcgzaPKCTuN{!)WqSWoGO7_5-'
    'hFD*T|_4)!~8_>4_nh<DY9DnEQZEtvqZV7{s=p_PXMrb0~AD$2q>#Oe_Uz2ZEOwkapfb>g|eCHDz#Qa0nhs)Qbk*wX3R4`-'
    '31ulYO!psE;DmzntN|hnuFS8hMDZYLmSD=NXfz-'
    'f&$DKSaf;d2|Uj#&P;IhP%BdFcdLb@_Y4f3SxMx7Cay7VrbB7SyOEqv@u90rnk{6cG5?_u@&f*B@cqwJ%RYHkm_VL8FgL@wAKq'
    '=LFy1n*ooWWEA-'
    '%S<co8|1X$tmXC@sx*E(DGjXl|&Rvz&mNOJ2wsS^tJ)pDtBVdVVuOQt~exYV?x>+Rkir_WaGe1`Ci|h*P7^o%5hbzXq*_D=42m'
    '*WsLom`Bj6Beq$SLK$Op5AsBp{3jr+v+@H|7|V9J^As~&Uu!`82=JB)2dX!)b?28E+7ZO-LSRdv)H*nz@-'
    'zIFD`%ZgI|u^Qe;}$$H<e*|!ZV_)oK|=`Ax5x5pBL|~omyDl^O%itPVtm!e}gX9PGf}%7j8<&#gU6I3OjL58`={xZZgl9*O?4r'
    '+O$QJ2ECW2D`Krv_A0U}0SPT%bG6vvoCk?OD?TT0C)~(%*0zWkD)}3^1nmZZ`AB_T@zLS0QpDe@&|M6oqj=Ma!np3(87Se`5ZF'
    '=Dx8!#^Ar}Nx(#c~Bcz>f>v{t2?o4z~RSDK1@`xslpMgyE#(NI(3@5ZEU8esL1<QOJwtJ%Tf31*A(KV+AY9W${#cM(Oh8WlXVj'
    '}%gAPzF~nPVBrdxH#I$w&n5^1t*jHG`~?%oDDLq(mUZukJTW7bdOAwG6+WpJYGq}lnQDEuqBTjY-'
    'B{aHQm&_#PIet#(D!e>1Io9-vjYtSlw|ianOms?z)|hA>589kRdmp7bCgZ9Q#9`FiiJ3DsjaAghhY-QgL?dAq~t(Sj7!AY{`#b'
    'R6f%b!*f0iiHaN7R<GPA$z6bS^`5>Jh^nltNp4u!g?5t;Eu!7v<l;Fy)xYX$XQxsoifYut@~I)pZhPG{7=&F`_VTB>UChuXG5S'
    'qhBz^cYk{!03{4jcO>&YY{Y&L4~DbjQvSqcaFA#-C4-0)-ERU-QKtz~hsDG2nHCMi!xTnzff+ekjU4}Vked(zbD5@O{bNnAOgh'
    'Ty2B-'
    '@Za!7doEfJZkdK5wC+BZaM{rJWb|q{(CM34;@YnO+N%(O>OGa#05abpYUMm?iFejr=Wt0v<T(+qX;q{nUw*MLUfuT<X8xd2Ze8'
    'Xhc_CpJS_${Cfnr@%-'
    ';lH^X}*RN2$?kgo!Dcx1>oq8WacCns9$YJT1k8;AFVt)D2S_rh_Q;p)uFkR@~EzsYz_Zw2EXZPjss<&|rnApw)J>NSE`g1p1wb'
    '-TdGg$^riFd_Z4#sX*n9*sG5m=r3~t5mf3sw??T<#OJ~t+EOIdT>^E$+3~_D88{KYR1OqlIKMkqId>ShX943@X#`HBYZLD;B<B'
    'wCY##N6W9p-m1q+-'
    '$1&sFs`mvWWty{HjK!}p^#W>MP4<l6f(ea97X@Gb#OS(@g83$XTAvldBb~j>wU+|*>Oi=H~{AvE@&#6@&R6(n&m&(4NusC{Kti'
    '{Kk1`L+|j@f(=GNhE553oueq@m_1M=8>T^aJ6RZ1qJd?;4l0-5}d#Z*|hhZGcB<^pk=z-'
    '2ZEBiGB_7@BvQl=4C*{y9pD|6C%3E;>eK*%inUS^w@gpQ~-'
    'imN$>vvL~Cyy>=8+!FlIv1Ju+bw$`I(Hw!RJFp#e1ck5Zs!&?lfGrG^v>WNz)+*RCF;xD{(yyg+mSMExwt?u;Q)sS;u|FEWrt4'
    'N~gXicGkjjqm{}$G?=_PB<eb{T}9g-Y%^SqYTmg=N{dA2%)d;`v2X)(#(s3UrXNdr0@qtHf!>bqd-'
    '0J?pVaI&f%?{y{1Y_HZd)<UEK&y!rGQ=IFDZZz@)zKWBiAadPOd-ZtJhNuIRJU^ItKUkeZZ``$p_+AqLtS5(n&rGfrV+&tnM;k'
    '@2MmM~YbaD)@|ILvJ`c8%-kspi>-'
    '43j!^pw{M)Bf_15LnTNb=49z5!f##*XO7~@<TbS>_P9NVcNt8hmi6?|3w31eb*~kO79WaSTJOZ-'
    '}FTk*M+hR9J03CO%uJ>`vsjpu?B4MJG%{}({M#~Yh&q}zUj7lG8vx8mkYFGw9%zP8HXaP=in(5U74pa?*%co>ndUEV&i1lU2Bc'
    'L06IFC~H;&rvtcb;d8jYzxz3Jj{e#2E6lHYm1j76-'
    'nZDcQ)$v}JS?fjufKrtgK3o7S5x17GyZQ~2`_s|GKm9sH*KtnBUktH<T5xDv6nVM=@2N>Y+v;+jlUL$9YY;i-(yXg!SG4-'
    '`=qR`Y)NMD@IBl<!%szZ@UOgkT!MAX1CkIMoV22GJ(O?_eSra5#~ybux-6Yx5jKtqsjT-'
    'e40Nw)pcM6^~1$|4Dln3mcV?|L$<p+8Mt>#6=)myzDq@GrAD;Bn2eq@>pM9B07Y9)QL_5!Z-d%P0BYAxB2K4F^Aiz3#YHiPm-'
    'M3Oa)`ON>mk#aZFFYVH~Jk;)iXzF5$I@cnG1t&N^_9_7WuILa_sZez8-Rl|6<EIeu-jitUweQ}HhmP4>}{G)or@c4t6Xe6K!(8'
    'm*w;uFGBO>2X($9Vkl$4b?q8yrsqcnS#Qu<Uccu3gEP3p9r@2>yuPaNlEbd7G%$RtRfdLF$`S6kTil3#Y8Tt&A7hwDo6FI6-'
    'Q_CxT4ORvHPoEA|<lfXJqB}4nz*UT`Br=V%jh=7P)WXe6cJqZK%aMJ*xw2Mm>4q_t|3)rMvZ5dlPK`)wUq2!8YKo@uamx4qGxJ'
    'k<(6{BuG?U%7;j%cQw#F32=zwYX_8DQPbqPQB!k93O2lqS{12{7e7Q67@dK}iaYlxnTVU;uvmcK9#Z;YvL1#WFq&1EmVh)6?K~'
    'OT{7g(v9<#qpBf_JRVrlsz@Ev`5RqN@<kt0ZPD1pZ)v!Ka43D5svI0X2^*d(Xw3tL{GNSMcpQUxoeO=vMrZ<H%=qhh@_mi`~~{'
    'dlabc$y-WJ#)wG>zXqvko&wcE3HAWT;uF{65GS>R-'
    'L5(QwXm4pVH6m{Ni*Qo)nayqLHZ?;PpOeAqAo!UZ4#oV%Q2ku%hLBJsBAKb18KmT&R5dasq=xH#)XNoWcm>`3MoSH{K;PzL-gs'
    'a*(#Q6UL$k=Xz`qFioPSwayO_lM}#O<D<n&p%NW!A+OaIn;PrN=}QvSgi_Z$T48MrAH&Zh@JE7l6xgB>v$xgB?cpISr8y5@H~='
    'lxg`o1xLYSG*vxO2<0T%&u{YR6#BZu{ROwR}T``-'
    'ZtICXnVrbRPJt#hsbbgZo9y*MWA%}sU9H+ZLL)>T{ceJ}pK+~$h%!7M!(H&8X`{K=6x?#wxtV|~Ua-`~cP{kReB!C#CgX-'
    ';F8c>*cKJnjlP|AsQLK9+2i>FHK4t2$*o-9)Am;JysxPl}oSfXIUI#pZ$-'
    '!LeDf_^11zK*_T=<?@^bJO+nNNN__;63m~$K#VkjqqT@^=P?m^Y3t`_HmnT_f<j}y&RR@Z@XYopum!g;-'
    '7}(+Wsv1AEh%jZqZ~we>hmf`>JfWY3-'
    '9*0X_fSKCV>wr@;EfA2=ar#lcEFk&o*Ptt;7>#0Oc$X4WX=IkT~7QfObbo%}mU0*pw%0tsofW-_hoFr1l=q)4kRry%bBUD{SrQ'
    'S*s0@=UKL~WUTEj<_TEg=~)7o3>s?W7*Jo(FqF3oovtAF@*#N>ScNCMc5`V|aO*AWd8w>pXK8sIBS@|h8+e3r8{*b*>v0A%e#_'
    'K>8@7aETN*O`sU74bWjc7q=lTC8<@ZfuNr4KY*{Kn@_d>CUN7>ey&(;;R@-'
    'kP~{fP&2ET7X2)M~mAhbu^D36Z9=Ti`Ec`;_WfNa&3@WDjO`&H8#e;zZl|N?W4}5b_A))le(k$ZTdbZ8sNF0nus-'
    'P<f$jlhpu5ifm8ofzbOrUE$gE9^w?y|9T3cY}j0=OXA*W3-'
    '}v;0YmAC(O3u>zx3!53FW<_>)X<f^tLRuy(!hlP4(waA|BJR#fF5>U+%#@WnEv7m8qNsM5YCMF?5+3gPTo_%0zjjCM@j-'
    'm=#@;cpJifGTi;K^rQ?x<bJxPhl38grRecIzX~CK!?Z=xlhPFDe<9d|Fzg|+UjACdHGLqd{87XH)Vn&Q%>P?yM>+bW%Zp#zOK)'
    'GjX>dW=uY@X1E?`&o5nXFaHy7dLN({!*Ly(oN?4}Wxv17Ym#a2G}{(Gn8l!9EZtaajS1Fwr_+Cz@d9fE#{FQ<Km6F$318^+7d6'
    '@b^260~|DVII-QjZ<)&htbXj-'
    '@E>m;|6A8gl7WTT?h3=V*lAP4_HoG_;<~*%oIhYTn*WLQ><_WZl){r6w~)Ir@JAx?6~qUkg=XbH({*AfMTh>|6w=U!&)M&M~1u'
    'Dmh>nRiv1x5C3dEiy$aPtIR?QNKSSn9?<X$`{9Qe;{mwQtXh(Nw$g&4wv-ZGNW&Xaz-&fyWmlsaY_;T~sR1<+ub3i-'
    'r?#ez(4j)G`RoPBhwsr8<v%9a<kDYXkHC;|28;=}!eK*<q3L)6k<5*>1?AX>(k#+9eBC)P*tEHLK^mNVH>nByce=9@i)elR~NY'
    'R9LAsj`$NX`w|Gb0x~<y<OrfRjC6NhHPkLn=WU)uvMJDyWe+7nw%+M(AFDXU+LY*swwiZA<pL8fvh`WEaus9pb}GXQNK9+%u1n'
    '>4A)H^)06b^u3Wwtn9{SwYN*76R=t-vb$JV;b>6`4yb;lM0zD}2?B<L`p}Tr-@~(gAZ$x>>6|!3{W2lRR#V-'
    '|EMk*y@do?iY*T`DNgFfWHAh@R$#B~2%}58VdtT~(f;#V35vSQC@wXbEjx8CWzNb$M+SdH+?}qILt{(VGg;?n~!GrKZP&s6rDS'
    '?IVjU88iL-'
    'T1)LjdzEgKA*u`=~RmU=+z1Ltdr@bb<|YtC7M&CKqSsJ0r77j{S<G#>hPx+FF=M$s=O0ZG54J_9YBfU1=jdJjXtii!wulq9AmQ'
    'K+Yeuw8UmRd+WlEvBT@`8mnC1y*-)|7N_>d_7gYO$PWs7h-'
    'g%mtvhd&l`c#}EYKS}d{I8+toKwpe2!>Zv!=}aT)CgcbB;lpS?t(T7I_)CuHM%~R&@|j1AOtkadPS6x@HA)eu}mD3+!7db-'
    'K&QfQ&H!e%_;swP0KMprMJHdj|l}E5?f#o!nu7LQI1ZlT~;%JzV1;*riDk{|Z+kZd0G!nb}`RzJy~K0DW?3lC7whW}gI*RA|A4'
    'p47fViG?<jfWxFE*eNcqJS4cA!ztBz9^;DJ9tI{^c<jAPtu)9{TsWSN*S?Vm@N*p+g?v1%!+U$+m!<~UcO?H;u<5*^Dl^SqNId'
    'H;LK1h__fG?wzvoF>B$}N8D&}t#2&0|CwaxJS^qxrkvhZpzB=_r!ueH$VdYK-ic67Iw9hz|QdFt?9wGg<~z&2F1*4~De=#gOA?'
    'q9v2CClEne+eO$=~dMg*i%mgA&I-zGV&($AboKU4umrIsHR;g9D`C9bOk9OqcK@h#W+Cbb6<3V3mpz~DEdu{?@YuG-'
    'r2b+wlWM-wU*ivF84#pW=n^K-'
    '_AU*fP>&yMiRHJ#<%rPKNHh7ZkL{Vym(9DwjdmrLlozGp5QJpWmL+T$Piec^iB(@BSgj<wN!AlZv=tk_@KuRvk5KL=e;;rwkX$'
    '{$P;RiK2-'
    '9J=F~T#Lk2*IO_*g=2x6q11%ce4gu@m#Sk;zvWY^VWV@$fV)jH>`Y_HV>g?zC{wk$fT*1l+C^QfWT@0vW}HVH%c5~?iT2jj@`<'
    '&i`#B4T(D6COX$7t!(rV<H2o^Bu(g_7G@tj0>9GMa&8t&i^~K5Tmi}D5#Y3d4U!V53j;>M|sL;^*r+|hebmtXdl}4ImM?$X%qs'
    'hLO)HJ#GkcKSH1Vl^NrCBD4jS_P!9ppG_cxJOfjZn!pm{Gv^U*Xq-'
    'J=MDAdU68a4A%jmE{_%Iz;W`y#*b7P6vq&ugEy<|XdZwN7T^t+5RIGf${{yK;1kJTcQ_{q#XO@HxFpM%306AYkNY-HaJzFULL1'
    '4LV0FvK0kz(^S1Z)lU=q6q&?$PXbWV3^%i9($Sg=KfI9V-'
    'VR4V@`CS14BFlK0TSnL*Q4zJl%75KSD5jclPbV}cOU^t{jKp!%;3~V+NQ8`;3nf>qxTC3YkS+&ql5pCGQL=3PUWfp(R{~ZnE0s'
    'j0`H{{c6wLr+zE8l<f)J1r97?|aKkQe^ceNYZXNJP2?ee&7{rzo8q9l``J|~-'
    'UEf&@*Ja{BdVyi5+XY5NhRGoe$)%?I%JypaWW`b*-'
    'b>{jF!so#GwMMcE29d=fNT5!YJYjsq$InS2A)L+v_1_Q2_3B%?P7zj`HVAIJz#+t-!RL^;fz^_r+;=mgGYfrQ8qcJyiO9L(|-'
    'N7L__#T=z%qh^JA59M+Az)UR$+EBkz&`Tlo<{uaY&I5+k5DeS(>#4PPocGgpx-'
    '{B<8S!$wyQ5b|#n(L70{@}UucUuvDXzy)Rvv)+NNqpE&)!hQAQ_FJ9OMsa$*LHR^KyAp2`Qkzx~-'
    'igl_q4b4qKwHm&ly%L}po8!jIzT+Q4ZXNa7(MCT(9-8mv$y}_k$<+kstViw<B<N*Qz6_<slK3a?k7#-'
    '*5)^<=OD@qdTa1MF$2@HW%s(^BrEk8Hh2(V0AhbY3V@{;9xVc$EGSzJA3o`=u;7!=#;8eG&+?DB8`W{}<JA+_`)9Px%djj}A~_'
    'C~y-'
    'RKj!4ybBf1|RgxakV^T2ZTePt)bSxs`+M9hH3m5T6)sO6g4=1#BP(_m@#*1mIQWQ%k<nn@{RxWgy!Az~SC<_=|9;bYK{p{m)yX'
    'I|*`T-'
    'b<K&j}4GF=lLF2uVH&cff5)BRIqm36rni?{h@T12gJ@o|3>5{OuZCf=D*7&JNl}p@iu%(HD30Gh9F~Vf|zEEHpf(VwfZai1>RP'
    'EIf9X7E~bHtJ$s>>$G~s2BlvkJb!)ZlS@WvTBY?U5mDnbdp|?c44o=^S-@yMb67?#eu&E^TX~kp$%X<{-'
    'g+UDU&LFYUJgG5X$MiB;viL-CSD0ZQ<eYAL3gaEc!{sAyhpqyGO#v{=H=0vD40E_!PJCu_hxf}R=p=G)w45rFf&lGpx>js51hQ'
    '(~h+&S~I)9~rCkM!i8}c6<B)o7nFL9KnbAmO7BI)Z2U%KAJ8wdTGT(wvFOr8<RkU-Ht>AhT7b0Ds1%TrGk&wI)`Nch(c#?p(x+'
    'P<UHpN+{VK|Hmdt}bdfAFBcIap64aS<XP;Lp8A_cVg6mqfvSuChuKLEJ<(PaP@jg?`hm<;|~Wm#Po>XVYio;jgo(w0)}ZhZ<dT'
    'fMKKoi<wM%1|Hu7%d+S^+l{Y8Bu@37zb<R5M=dS>;LLsp~ZF^qpH=q`;VPi$WDK^Tz!6t;j(~m>R{5p<YZ2+g$#XarzqD^cEI*'
    '4V$aH7k5*?RF-gN#f$36gn<T&zEyrEd6~0c>bYq~uq)&S@D91^@Ub?6xlm0uS(Iemt$;ET=~}K+8;1PGm~fTL&@%r*2-'
    'x0ZyxWgjE1&1}VpE=VEu#|GQNF(v{=RK)<&ng9tr6ye>)geG(VY^uXF6ogVnWx`uBx?dz^Mc(z<a!TEk%T-'
    'UaHhd>j61hWg508Yfn(w`4t4co8v50IdKgig3*m=whhUi2K!a8Ba3b)iO<*i_-'
    '+)4a2#&{A@^XTVxy$em7s>_ScoKADj@GUxmZp6-s(HEze`%9Gw;S@)X<7>Xi9V`n9*SA7?>k;AK9pDy|Sp5RxY0&2|P-'
    '91FyKhH4?R)(aS@RvDalELU=zdHZgFz?mT0Pj1*BEU3bptSp=Wo4SM{Cj@Ww2bO#izP{<8;0cITuytxHcM%0=XJE*XUFzpR16_'
    'E<$qbR4KD8L_5$bAcc#K6`BONrWSiD86JuTuGX9MwC$LvS!)%$+vLqN<_yOw_G2ThhYcp7PRs*^J9iT!VOFH6s%Cl_Is)DAv9^'
    'ZtvPXf9w-'
    'Yb9)<UcxFGOHQG9;=qI$;{Tzs;%5s0pM`Z8DG~U8fIN}Fw&PEkG6}7y($h<S!y6a54i~DlN`6|IZ6$wI?$?$>~Zd9gOUdHTW7G'
    'o8g+fTE0GOVfSp;;#;nMY63)MabEqr(da*{^m^#TYEU~hyu5NHd;Lq%$H^z(6)htlat#P0Fd3|SgFy%l&_M7ZdN(i;$E~&uHkz'
    'S?~B5cwC$A*ozbC2n480Y`%$a?)0eO_e_{rF^WCnm3bdD;LMKlU)_n9oBV5ctxbve9R(_=zF@Z)e#7=O!z7;CS|Mob#GrqH`B#'
    'skqscGC~AfldQt9(|4uM?+7$#7IA=8$)_lS-'
    '7fpcEdR87$chjoLAP2xWz#~6h|(!3O3T4m{Hn7^#n*Vx_1oz4TujS4YGds}un^#Ni)e?g#0TI!5l~zO5AR#TwS+gmJ)-'
    '&gqb&JRxfSaxaS+Rr>Ghd6As7Z&rMl&_VPRO#3jfFPb9m;<klEA}qGu&}^#I?zRZ(DE`fu+_z~VK(UL0Yh-'
    '4t=he*}j1nq~pD8Tm)FY0esNVI3?V)3*5zX;_e8Dk-'
    'i@csslkWtE3T8PcY3UCuW~rw(rK;O@aw6)Gs#yQQ)50h`*Hc2v=y3qz|%5U~)*q@hXom;uV<dS`wq$5l2vg#6w5cl$+tAc;Kx{'
    'IMfQ<69V~fd9MzrqTtoUAHV0-'
    'P`vWaLxeRDi4D&JxB{r;lM?+{rO9u2UT&qc+0+adF)m^y)$Jko<Un8wD)GlZ%rIlv7YNEEB#Y(cSl=8noK^f;omwFiI_?+1KNS'
    '2FC*Mw&(dWepVn*%P%AB_VI=fcj6EA~gk6+T4;@|sEh;M2-b2)k0{%k>O?`9k@ef$jN092R0Ic-'
    'O7g!nu6vnhN^kYIHRgeD4DMN?&)Y3gE=(+`8AvMU%Mk;!fe>D1sn;s~N1xWXm)q@C+-'
    'llY7s`znM|AK}!ZVGZeI<Og0?$|8=?4AL8;L08lfg+?fti<9*o^X4Ikfh@nzYiz!ZaQ|#KZe*)Zg~MHsGA)}!wCwMb1_a#ZxMF'
    '+ByAnaWa@D?H4rzI&L4C`?kHyH(!xF~4-4AfIUvBJhhpU-k}8KRE41t=SyQVY?}8JI^r*02#WiPLqMAC$$75Su$W?xOGpe+X-'
    '<VckgrANdvS3c~+WWpd#I+VK<a<!+jcyc21cdBG0O4*O@w%CMoykB&(>Xl}Qyi0L#s3KBzGE{<cw>u2BNI+)1Ba2X`7DXCyI~~'
    'x<prAAHY=Qy1iNI?-1Qo|CX*^5gWn0)qafRY-TbncpHFu^&q}&L5dd!>u$C}~2%)zRgSM`j7{AX=&1h8yMW_xtB5*gG*K7oux4'
    'jiLE5zlFYMk$2XFb@F2#wCC?2+Nyeakx&L2tCwbAWUW2Fhjf&~0ZS28yHnZSkGzfQq9&;2%YTwJ48gml)?P{mNaQ^LavSGihnZ'
    '57Kcsb21HWb=g|^SX`@xyJ^vLv?sBJroA=wZ~w$_-2^tf0S5b9T|y7NqzWS~BSdU-'
    '0Z8l4@FUiL)rGXADJy6#ssQMZUa!b5oG1W;Vsc0#0V=`TwTNN;O7b~%HV+a$%s%?6AZq+9Ta`Joe$F0@c#I9Vd1^u@nkz9@2jU'
    'Bu8$-93bbNne{%pm+GPl9bdoY@G9`OAKC3$Z`H>T-#@q&DXuBT%RrKnLK+<Za5VngX;ymL7Oujcu!Y-'
    'oRK#y2ov#tjGsD9jf($4!c6g>tu+eZp)a!B7cl#l70jmKP{i{Jnp8b)m#tBZ+1b;@ki-vX5D4GZ20C1r^RFlbldnG)jf@aeuO>'
    'tD)R_W_u-T9vw*Zph~2?N08oTYAIZj{>q6P@9rDvtx$XacZyxikGZB$9B_o=8-*g-'
    '=zUz%5dNgrQE?QzK2>!XY`talN%l@`Xm8$nyI0l=$1C@A`bcT}yjy%L9(7p-'
    'W`QxeKiZ3kpTCH4?8;iPVZaq?o%Z8sWulu_0(AttMwZYrSt2iO53Ma~@v5G!9v>=?gB;LF;c4g=$$)fTd8|T1R!f3HdG3q!+ga'
    'XB9>$#S(y8|x9pkX7I~$5b23Vo8OWOu?VFs1AU)UeG#CVY<)tKX0VDQMQ@wYkOkmw-'
    'Nz~q{Ex$h9E0{*SAa&0VW6)pFoJBHXs8c+JQP=&-dOq1hb-pmesPhjb<zF34jQ?)=y_E-'
    'y>huM<%4K9M<WfuOfd?hAW^=@8PIXjejf5oP>0W;m+-'
    '+Q6{9^duMJ7kT!7JxtUdv}SZX$FZ6ATp{=9FF^904MK2^zpi~JL485<611rtzkP*#riS;89(}@o8i@k<dkU_xCxmmKNl-'
    ';pg;53p_33q<$I&FfBqQCj%Z&#^b@hJbl~D$9fYHRaf_cj>MScMB|vy2glZRTVNaXCkdgcNW_%l%>2*7>-'
    'V3@JRj?M$=Y5b68+JR5xbZZjNNS^LiFn^hv9Nl}+A(k2#@#1tqhRe_+Gb$NkRtImS-Cc8#+bRr-'
    '3KDGYGk84Qd#O<bd1ndcJH{BT9=t>Mo&0ye|F2$GMK%S@2CItWg=@&SOpy)icK!LBvYy~bCb&lFJFe50$|Q`VSw}k*u}4G>O0@'
    'Jb$&N$ryj}rbi+(+zc26S$*v<MKfmj}omJ4IF=pepXi8KTZi%N-'
    'Ph1y)7NhLwe*a%(azh<{*D@o%wb%DR7J(!aB?=7%0qwQ}dLdm}Ps7c-'
    'sqA;T4wxHorfLgsi|@z(FQ4>T@_zHnT2odIzmdRU4IsiWoAVwqthnTV?AkA{FAkR4n(80_J<?TR%AvgRnYYYHbxIK}zkPVLGSI'
    '*LnoR~L0D1Xqh<q)2<B~MX@uJoO`wAvE<xC^dTrwoeJy_GivNN~B$-'
    'J@$<z6T*dMW19enxTu&;$~NDT=FJ))ZC64>P^tKe+%M%{5=VF3#Cv1%vt2-'
    'lqPN+9LFID$jgNV|zO&8slThsGwbMVqLfzhOX=3%ZJUI7l1z5KUyp08>|!VB@1YA<KxGm8D|QIKwEan5kD&!dseTyyldSkQx(L'
    'lLJ2l=y5ywb72?G5A3)%f#IWqgSQR*<2aU_Wq=qU2`Oa*bZ(Bk^Sy7|34amR+09WLDT(3?K0`us&-'
    'xwI%ua&K2pk^FJ3L?4zhf^{6?hcj_Ds=8qKiO<JEV`SK{S}93opQ`PGVWCKw3r2vvWte}7^bEg%D73O0i_U--fsV1xGPmy(i)I'
    'rzM*1{iam?Gspvgbv=+w)&k3hD`J4v2FJD9E3<>2p`IcIW_U8*A*zz83X`3FqJl6YOeJUC!J$$d`8d3IgP=4f|@7mPIUXg;>n$'
    'ClCphtm4zo^*k)s*~V@<{449*SQrU&)I33NlJv`!s>Mm#M}%(>OXzrY6a8cAL<JX!yvn=U3(g?(fx%;ZG$+38_0_Qq^PPADW>^'
    '_t4LC<&hxHvXnl_|HE!)_Gy;jcs@Vh|KXqkiz&zh?#P@E_~4Li(e`V0%jlfQR|B_m3ssI%GEked8d1ZX?HK-xRHv^bg|RsAri`'
    'ZH0($L8FQ^CGA}?9^#d(f1TQ@Vrv0WF%rRO2S8m11|%jxAI_C2v;ru~5yQr0m-DDQ~F2fzQVW{(SBqMi(d#W|a!cAD~o;;Omg-'
    'SZmXe9zPOZ~dpJK>BAq)KOYJl}$LHr}CrW0Z-'
    'vyL?k$RrXtaknX$VBn%g#u3fSn>h2QXYe4$#<Fyrq?SoiTBYjbvuc)R=$QD2#{zFej!o*T+G%{{g{0fLgE7q<6z0?8-'
    '&v9pUyK1?%kc{IPpmH3?Rp(|LJ`X;`CuBB;aAw{|Wqjf;Vyd+%H!0(efhlt@3lhVg=U*9_T&`j-'
    'TfR+qnfHRUL(xWwSsTUP)ZT#nSRgns{Do5}ncjpYlP?%|SojH6^-Out<rcd-8eB61Te}2x|uUw-4Mkm(e-lDmuLd`ELJY)`%-'
    'eH4>amy5W4%mj~5Mur*(bbKS3s<q#(PxlI!{;BLUM3ELRKD+`>Ol-'
    '(Oaql>z9j`fp9D_DwD5=sJwj}#%ozsqExtm*J!g{`naoj1(#j0!+mO#UfdP`YD<DpPyalCG@xypquA}Gs8xD#J<4p_faAh5)8;'
    'DL2UX)2`Q8D*eK)C4yX|)|>1PFpGs0DtmU@^m=H0f{9;cdu_&R@qPxR4u8p}hN)3005?-'
    'jF&G==I`klk8ZBfQ9qR1C`h`EUamY64r3(``FQ6pZ3GDT;0-'
    'hbB6R{L+jp^olN06v+rN~|MA+#4pN*s#GzN#((^O|X1UY9u&up_IU&*9L~<PgsNskS{=L#<BNWJvZNrkEJ9F=-'
    '+Hg$cInrE>_bS+(1IE{@3dc={EsSw_6VFWEZXN;sn^O9vCWvoeMR}cBwn^YsP3}(%JyBG3HE8QsOpuZJHeEAJXgMJN|3?bKRlW'
    '1qg*cBDrXwAXx$R+5iQ56GEj&awFk#6Nv~SWwop<N{-'
    'qNwBVp9xei!1VMAHKU1Dbc@)Vyy<!VYPhCu|{C$xYaagF?&<Xv5ho2aO-SwdE~ZFDf`HD8n<|Xfd%Do06!sxTa=tzNhH4yPi!R'
    'A()byC`Zu@F{8OWEGUcA$SNga5f$b9bBD-'
    '!};iqGwQHY7h_$BAsR8a8x{c9k^*ykkAqC3I?_8aH^(}2Xe{SI#k3p;)MF_E;Q*k6iiTQt?amEtctph7fP;n>`RSG~eH81nP1K'
    '8xw&b|9F6?jYQI-A>SbpJ^5j&wvM^?<5b21U93$WLSjp#Q$`29^^QG_b3wB`-'
    'd7RG3K8NJVo@PpquZxk740loo<w<$@?m1@%z6j(5tZ%0=5bw;8ue5vBJF2Lt>-'
    'TR9K*OO^wKJLQ1V8IghY4Kjqyljd1_kj4vtP-'
    ';u_D?JMOD03uBnnvDz*qpDy4khoCZdltbE_NeIxQ?2W4D1yl~x!3W)6;K2qphEC^k@Tx&OsoY<3)zj}5li>4OMv$znR}|?oox<'
    'Uo$6rO#<9)paHX#rsg;4DRA!k2lIbv{eE+Bce&-'
    'OMZL~&Z)}N@M^+Q;dRRK!@Q8W7^x9bl_7fBPQ0ZbV%LHtAuU^LyekQlpD!Q<{76jMp;GVnbrV9|V;mmFW>`pxw4#R%@#UdlNz$'
    'fL)HDY&pyJW1#|-9!2E+O4X>O=FO=-'
    '?l!yw0V`aP7wAEKrRTyKX^sW*?y~C?2UuhP{CzAW(y43ao&xc%|LDIoh33bYrL3f@Z@`H<aZy%V=<3*ir`4rId9vzGft`4<%k='
    'qUiY#;mqwsePzHe7V#aO7Z;3kQ*k;LkQ0<ctY%G!#nW}40C={nZy$!<>$E_KBelkCgaQ#{+`rei;8#N;qR`b`cXgIz>my;3w*;'
    'Fsbl<{8G5JMso{$N-yGH;cC^T^xOFOGTB653$$p;;GOSxeq!r*Sz<1;45XsBC}4^cepEbCaBR1oC8Al`cT-'
    'J~|LtV5YH#`}u(kIZd%^YdPsl2|+*lQ^#cH^}TvO3v-@Tp-'
    '2AQNQmbgxdCA?w%>L~ko1sM;A%47N(MCAC<A|=w?gnuv^&`@OvMz{gFQhV$jX|EcI4{>GC918=pt?^=@DH!HRWj66=hV#vAA{q'
    'MCN_MBDh#JMCg7d5!*Rwlcgpnt6d>269c88@isz5x}txT&{QQoBy*FrvM7Ew_A$0T!Yx4^5(1i!Bv7_|q5^VQ(XYb-'
    '7yk>~twhG3z6~9H%AX&*6oV>7XdqrFO24!eKQVhZ*;#&6h14^wZ{ttcoA$(Nc-Ch$@Mj1Kz@WR7bEGQr51wd=^(5E(&Rp%q48R'
    'NDznV24H)u6-'
    'E+FE*rSAD_hjVbTGN=41Snb!@=<{Cv@ZYu@BPk|???NriKjD*AkK?2v<a<}6&iTz!DA|yh>c@`?Vc$NvIV#a>?+j%w(@e*O>=U'
    'KOL#gp3jXmq4`r22Ab<qkHw=4mUJ`rlQEcg^$2#l))xdgiBxJ*AFJb<m`PjS_i&1Kx$-'
    'hbLnL7<ic)nwYnUCCpfvihx9Toa$D2u@OQ%m3F(s;;0W_EYH-'
    'X`zw^LamO()>ncl?PCMrQlyhjnj}^J;cXB~x$BzE*smt+)b<oOE-oi%a{CQJf-'
    '`;rz==4lH3<sbfHRrxm{k7fIk{?%oO7DN>0&#Ay|Z<cDd2-CR;a}3>hw|)qO#&-'
    'FEojYbfn#$;)e)`uHi%Ktv~3Dm#IT^RSV@i2%Fq&)2M)q<m=yJ+R0}v?5&jATDzf6POxxBN90eekfwQ4P4%^!I2eqVEj(vMEho'
    'j0&O@OJak%vMF~h~7J+Jj&N*>ej;<&<>(2@{$4+4uc1N`E@r0e$KL{Lm#m}|#)*xiK;^zScuizh^NXDk|?^G~Od8n2nBB3>@@K'
    'G}8seM=!8_u|&dwMMjuTh#klg0}yX3RO`TTjjpwlC~gYHS<CxUFMug0e`M{8q<TI>LW>#cQ7OKRMi&HP9`2}s%Mx*be@Anoutq'
    '^8yb_+rf7>BOMQlxk9|sqH1;+7ecfmX6BLAW!r4agPzssbWG!@sn#e(paL&IF<_3nL*oPy_%NcRw-to5=Lih^avY**Q1%XZQvI'
    'h0Q-L>^|DDlV~WP@sDBhn!r++;_(@E{P}hsqFyCRO_{Xvxb{c4=En#H~8z=Yxy0Oj_-'
    '@2ADeVmY&|87`XNJ<c9YsN3qd5s0}OR=S3~4D3pT`MSXn{I~B+cJ%@Ln_TsEhF3}WY>E}+lC5?*~u76?RvNuhA){7`L<RI9Xf@'
    'pNGaEwHr#H~>-u-'
    'vBUWW8|lC&auuHoOAD`6I!f;#ZRrs8&4h@&nMP_(Yu}Pqe7#hYfk<@1M|$(hmG7ChtXttfNs)8XmEQ0RMJq+6^zruX5kTYC?zx'
    'HX^&SSMk;nmFXP;Jt_6w5bd4UP+l@W0Rc2uPBzMqo}WLUsrVg~0mCoIl@j|r?yfdQ4v1Q^d)_rze18S-+5w@D)NG$AP1V0H-'
    '}uJH=wYjO`Re%a&yr#R%jLRaeDP;C2-$kc16b3hquXvEhM#Y_)Rth)A$Qa8`w~F@irw2iER46RFGCc17PR`<4)E`b+Wp%A-'
    'DqldQcxJu2D`6GM$v(E>d|xi6gZnV*9#GK6n&n)XiX(Vee|z2U_2FuXHjEvTu-'
    'f9l@eA&&cN43pyO_BE?O7gMq$islIZjSzMJnNEc6rqFLesfLni=~=_a2)tt=ANkQ%(8(saXp9Lx+k*o!7K_07oHx<?#4LJtCtT'
    'H6-N|EM`?Xo@cXxPMdR&8h^cPBIHEe-'
    '@lw;v;U}+cGG1s{A!T9WsB43r=FI;l=taBEi5=Kt6b1=h@r1{9sKYZ+p$sK*SS4cR)Tx9#Q?0RL*ch`@yOkIcFV^j#{?lIF_T+'
    '^j@-Vk_DEOtGVP(*mq}((5+N&+F*>RQ96?JE>&AmD{?%Y&D#fh-'
    'I0i@YuvqR@a0boTghkRA70G231$B?$q?oYk1Ep!vPkK1tIUgs=PjpOEPLxFbqLPT_YEn7A^dC-xV|MOTv<>U>*;`sh#jx`H@ul'
    'opf^?50GE4*bcWG{)e5JoPo|`Ygw}q#DQQ}gT0s{zkMo!C2LpDO_Ucr*M)mb`zy-'
    '%)f!F2@=@dS_vGZ{0b6C=x5_>RS6AJRHiA7$hU+^^Hhbd|OYa{boToEw+!YXI43rSd*6fWIFBa07i`tpWk$*SQ~%><iI<>craL'
    'y~g6!P=OgUprsKqV|Q63#e)q7%})hiAA8f6({4Mk;$osFf(M+WR4)pPQjjWx-v@;{z?YohrYG`;fXoDzN5oT-'
    '0!ZwjmpM*dwRGMg2uGsc+Lk1(R6HvzxTRKTXfs?--'
    '&sXn&0rjuv(+Hj6?Z=EsL0E%#Do@##;tno47(e$tSLaBx(b2Gh@m3y_6jT%*7n+0U?GkDGM*?oLtTJz>$!SUH!(MyUTvY3Av$x'
    'a?r~fEx#WIirowZPg-Z)qJqOE`QtN$EwVBaMJ#uj#{GoS&oDYd#rG5|s-'
    '%1yp>W(PxrfSK;MYJ~Z=Yr=XO77*5Io%0Q{BH{VOTYmOWT<2p?kz4+2y9Nw^HqG08nfpd<ck<(G3phb2k$DdhYrI&v<Ie>GW`h'
    '*WC*5M|S_iatxSh6xgWqV|<G&*1}DPRw<v(11$O7d>pT5PT^feZkV%04O^U78MFqQAC0FTYME5emw2W9(pB~du^jVM23pqiI#*'
    '={Dm?lwaMnW!1IOOwP#<zp0|cFlSN*5CcE23;dP)+UoErFf?<GZk2TapqM;YUXmy->Q8bR``wr*u8ANIbv-'
    'KA5A>fN@%z#r|5NscDw0Wa%qb-Zr2`pVkMEua=-'
    'TTe;*bz9f|;D6=I8G`4>8UHOP@waFcE59qbMeKV%7~aFLdrbqX>LWfdp~D0`>}&P>`z5+X2ESpMjN5aY1KAXEi}Wf<q%d0Aflf'
    'K|&3qx|zytE-<aLjb|9W*Eyvi&v-'
    '8wrtDs!uTO@+OenvV5aAVE|i!nEnXY3J{lqM4Mk$iR(}iJU}N6qgSk!j@t_fhdfi1oZcUQksu9G^q`H!ozUs1UQFEJ8lDI`F;3'
    ';@@zoKNLOMbwe!OFHB@$Cz=FyDtk`ZtXQgz7%mkXM;b}r@;{v<`eZ;D9-'
    'NVA?1zPgGXJe&Go?rpN@4JA%PJ88{y_zYT2E)%Az!-Z;8@RXLqv9zgxui!kD1O5ku-'
    '+!#vml_mbe_4Tb`(qv8Sar)Oqa3muZu54;<k@V<2f@(-0@-'
    'E(mNrnf4YYPq<h$g61Z<L9xLw+c%^v4pu=w2&Vmbeqa%6ex*u?QE@?5)XpZ>LbIhourI<pmeOjZSca^-'
    '5ac%N=yi1x53d@Y!To(+*!kLD(j88z%fzVMZo^<{sBfhJn^Cc|Em-OELyNCsmaHW=S@bKgk+?&$7tCmD~<v$92e}-v(d-'
    'e%$F4f9b+{WZi{Q^4Q)mkv^&cikepV5oXCw)d@kz!I#XB+MaCP3>!P&Hfmt!QNj@sBCouwdj{mKYgUb@ie@6cq9;<jwg-srI(_'
    'M>(?tzL%qVCH(3GV%CNU$6#W)G)ROCCn0+b_z6XmN&-'
    'aSgfCI@+4MNa^qvyCOpISVYjI$C=Gn9goyJf4M<#u#fzb+ON&5`+$=7;e4_itFf2x8S0uhX>yHpXhpj=zfij_pD-Wtn*D32vX+'
    'nk@)_C3?cL-Z0s1QV!UuP?(t2%|NJAKdye2<a!5E35h*s+$-'
    'LjAH@;SxplN78JA2>qz^Cfz1cu7zA`qSLguZ@%vtEb&P2TM$=29g`;xeUEDA_X1O#x-'
    'POO;l)<T^wR*1*zzpsj*sUO_@z!E7uy|(mqZ#_rv9Rup)Gu#RvWV2TbOoh;9>TM40rP>0$u4Xm8wRk(qdULHqG+>V@rKwN%zOP'
    'S!6gWL<Sxk-_%V|1-'
    'u@|wOpn@?w0xB1GH&b1aqa_tORP|{cr^CdDMgJian4!pIU3yqG00(;0mr+!RZkIL$wY&+k*dy&`@;Wtx<`E~E6i{-'
    '4gtlX1Ja~#|0)l}v@$hWyRBrBNGyc`s?Ht9+YfL*u^H>qXDD4AKy>ShTtM^$PB!rpBg@BS<tj^g;BVHa{ydj9zDJr-'
    '95|VhU$5kn+t_pE$bJa0E1dY&v!o}2o7Q-f+qfqgCgijsT8s~GwwU`&?@-'
    '0T8O3=u20qPlQo8M9CiKZsZTjiRxh6I+1+l7TUlnv~j8^#_m%#EkOf(wBiJX659y(m$r*1Al_>43o#z9{EnshCo<>-'
    ')1v?BgxjA7R{`Gvidhmm|Z5T~&!6($?Z9p1<LAW|)_Gko+*ITSo!Tm?aa{p^W}kXDl8v4a;Fvy{6%OjV7rr_MJo+oC6;q&Ev=7'
    'Jkdh`yy((Q?CSI$i0^@kl}o--R?2#k`|^cYlDf>8Qk<VI(&Rrjw(amZ{!n<PhjszHsqEmI&LzIlgY_ZnsCE9xIly|?&l#nDyyB'
    'L4i8nXxIeR9p9^bBYwXDtUuvA7&f7q>FsO7V&Egw2R(KA8keMMn#Q0K%OmzUFd{&{w6L=XqW4J&Bgo%O<dfyzk_K>E1<e!C{*{'
    'WmjDENoUW6`O<eFxZ2eNu@w<t(R3{)|Xln7<_d0HDKHZA~bp^gizKHeTlxiXR*8dq1CE{yt>s@Kn0w3%eZA#JDytxexz})QDV}'
    'X_!KinS%lF{z<mlPR8TId?b|+>SYO2mWdnpKq`L%$Vn+Zs8II%WR_K(S=HK6uG2J0A2Jai^&XIZRiAy#&$S^-'
    '9{v_B{?F4>_111tuHaz1@;BdKL=1fQh2`H{#JJsxRLLqY))S*uy`8}8bJkbUh|FI_m}s!2@uq78RklNE{Jh3Z9#4iHvGS02xz8'
    'woJH$P#wl<;*__l7=(8#pr-'
    '^w2>Zo)<?5Dt3k?(>4a*ozG_98XIl&y|?8EthGpH#U43Gd4yi2j{)HTuComKH;J&?!RrQ>;2bF8#lYNE9QMY?OYhXHhEE&)6Xu'
    'UaidlRo9`EY*qAG&nRi3{`6<jrVH)j<W4Ys8V6*Le+-'
    'H`~XEw>NGSp2g7g2*StB+1$6A4P20OW)#XG~$Y<%r$*bZVN=7J<3RkaOv~JCdSNtT%j2(f9Z^uj<Fn^AFBS*m`Yzx(+t{R8$%p'
    'sEY$2S6Di5pgL3LC=?lTFY;<8{{P-'
    'uNVdyD1b{TUSrfVueBYF4Zez)JAt=(~6QKZz8p?{6vBr_mFrgtQS7(S40tW>b8*J5wpL<aDVK~_ZZ@v2PGRoRc-'
    '6O5Puh)bM)$b-'
    'S<V}lDluobXk84ol=vMI9Itfbsz)elPNkMxgB@ELu+Ob%3+&5t<;9_MDGLYEs(}f}Y^Tg4aD)A32H%j5FWj4nN^_?_3Fc5gXQ#'
    'EpX`k`4oam`LE+%zxa!YJ9%)zwIp{SdREHMKiS&bAv-r9$mN-'
    '<#=3S7MOQUfL6LzKV*v_*K%s4Jr>L{x<pUE*c*pk5p_sv`i32FiYC#W?sfmwd_W#14^C$km16ia{2gky~6%t{V}R!C?4&<rMwW'
    'ur(t&@p8kOtuP4^`8!xI6LJIG&&VC6y0WA^GdbNr?vs#vQR#SuV7<64|yLEiJm;oYbH0qc5sK$Px&SgAlqdBhrtmq}DddYGjB&'
    'ix=q(S3$b1R^4g2rq==F;9CQLGwWw_JO(!<i&Y1s#YAaO;-'
    '9)n7QUkPCOY&}I5322=T$BtwB;e$H|)#&E7wl3x+5hVQPM?RL`VD@v|;KrFut^=IPGAX0#sk{5yMu$yc6$SZf^mlqw&@dYyt{P'
    'B^b#3T(SLu2KB1hakB$x2M(mIwgSG##acjMw#mMni@1uH6dzD^L`ziIW?GCB3UC{MxEx<<Q_pXgrDxAasVmb==U|yK)r8D36%!'
    'R{pIQX+c@Q3@1pa+$tuRlXZ|X<4WE$1y^U8YVo-'
    '`g(#d$6F`<$RXk<ony#waCz;B~TY4tN;6pE&>qjg}v3GC#Edol>(gm=>Pux-NMwKetC?$CrIOZ}*tH2Y$hGRgff_DOP(k&ko=1'
    '+_9zNkWul4<1e2+ybhhYjIR`zXUU-'
    '?n$xk*K%AIU%D+1H4D0GKOC*trdwp=n|^jooH{#YAl(%Yp^EFYIL5~yj}u4Gl*wnAEj<xnE5{Y0o<SZEli8N!G{VOK_^{A1pn3'
    'lB^ZY3!JO?bS-SxV)Aj<Jxvg({DAYPYDd>VwIn-jZ08XE~E=!k!`G+x$Zv;{RX1&EGLZt3xx#KdubJfmgKA`9r`wLLkR5M#~=s'
    'F1rnmegrp}oyWF3Q`*J_U%&Gn_T!6(qZq8D&1rmI(mRvua>hy0W;Xre5=P%GE^~PG*{}35eMWasTc~5PqX>7of|@Q$=H7rE&?+'
    'JV^XqR7k(_**SOyF5NmO;V=QS4=MtjET<wO(H)o?kQZ(W)5?I#^HMq}g`=JwchZANf0k*qsufGS+dfjKkzIXe?w+mPA&4e<(qE'
    'JQscUMg^hbQ8@hyAlM!XD{n4V$;2;UEyh8Tt#CWI3I-cIC}1Nvt}nW4mooboNMq{no->U6J2*g7V9gBvq+UA+O-'
    'UT^LZ`i!1O9Vd<)tYHY-lD;!>ZYmM>_hVPG9vD)i%>9w;tOdm|InVD|Yp={E)!Re=#uHs92?BOz{h`{~kEsZ@qItCZv~*=wGy9'
    'gl%Eb8QG}MQh4DLH<Gg?5tKSF9Xgs{8}qRf0Dd~t1~TzULEGZgMy@s$1c^kSv5DlAn7RrXN~G=drblH#_Wu2QInKSv(!f~yfGP'
    'pcH}7K~-gTOPwylPg8<KIFX`4;AK`3xW}uA*#_p+KPQud%@}M4XarTJ{WPw*R-ND!JuK#y%YfG<Ra-ZA|o!A@>*1r+?hKNW5Dx'
    'yA~!E%qJ<DTE0>}LPAORGf9NM1n<LR+eTFZ4fcGv@aDQB<Fo#-'
    'wM59K3zt>RR1t7Mt(bR$<s$4fgOP^n#KPoE}x0Pp3+!C$M{^B$^CWQ8??zKZ2*fVsuf_D7n?S0`lElckNNExDiq&{NybcL&lVr'
    '`fv3XIyx@&3rtS)*f*7=)wrf(>|}3C6~Dw>hn8wB0(}do#8+Zy2bcWJ>6|aKz58Be1L1?Yzi+D4w*%sMd;l_M?+&r1MoN6~Z>y'
    'i1aIvk?!H<X-'
    'YvmJ(lDUwaP0r7ag@ig90&BUvEuUd}t&YOTV5*xnt)QqJxX5x<81vp3kr;?IEfc^48ws&*!4^M9W`FM&bJkDHmR<xs3looYDp}'
    '4X;2w$t7jfs|KD>+y{Gt@WfuSApH91c&$12fglN(V$6W~=~*}XI&-'
    'FiiSj_Kx&Q#b5k(qh8C_whi1B^$glA^c7MpOTU2&R)^9lFm<WhQj9CGh@AvSlYjj0tN&^CV}2|l)jf7EAKw&`?Ad1|YBlExKf2'
    '?P`YMIFe|juPb+HhbjLl&(>J2b|WER|%xtL4b*>uRmnEnuKo#XVE6+1+{?O=zgkc1O<=h6Le7Fz7Q_jL1gQ3b;hj+AN+I}0yye'
    'fgY6cRmS06;q+McPi38i|cA4H&t6j=(D{dR)kvS$n2La40x2!v+ZjNp&+M<_d4dBj@nK>wli68gt>c62z-h;zl!o1H-'
    '5&HH0tz8);Y<}1xW(y5e<9?q3Ld6up1oKpjo0?HWnHv$my};z2R1jU-'
    ')lv5^O4L=5r>$ESXzov2UdP#)BEkTWI}@!)5oXQL{K63o?_W8=XgJjLTR&9DZ}`t973Ywq<W$aPiAc(}=X}hX7yrt4k6D6_#e#'
    '7Cs`LX+;pjoj@T+%p@sA+$?`b`0V>a9`T*Q(m5AD4t&}l;l-'
    '~A*=r$?f6Trz+Ei`0b`QUQY@7u|@NRje$aX7(YHri(6dA@D#v07_abaTF(1esJcCzBsC1Y9PnxwXa!jz2(wbt~E6{VRv&R_Xg>'
    '-;czj|6vCE;3qGmr6F8|s@e0K&i}pmt_KbVI35mOg65RQ+>C!T0c{)XuMa3&*Js3n4?mrZtOkcu(fP5fgZvySt!h@-'
    '72FvUxmN5_T2c%BS|Hr?rgF4*PE~>kGZLeD>^nHtM^mXY}u-'
    '2K`(Ssa~_4cu8fIE4~@6RxbD|*D|Js{5*Jn7e2hRHD#m6BIT{XKj>8Y3Q$2_z^XQ5@nu)7i|epB>3Nav>0b!lRgj;T?~R=J1pn'
    '6R2xb7`1V?H@8q_^-'
    'yb;p&ErXX6;+9o~JDi?T2aqv1tRUiZ8$)S0XKvO9OZY#5Kc#qBnSG=kGI|atEPH`xOC5$G8K9px3NVQ0{QyMN1B<5qBiFimZL~'
    'It*;WWb`3jp@R$n(GQ>^691ALmhLd_$!5LI2{J?`RfUMxNNQ9X2?B`om0|AitFzq-'
    '8<PQbLiYvKzvd#Nk24q{EcpTSALnfi!}H&PM*3Nry4;sCY=@9ut(||hfQ!9<yKo8>197iz0OSBT!=Lk}?8M+WzQ_)OX@S4^dZ6'
    'QJGh>aa3Qd$6JHY4Jao0KhD)v8m()cUMM65Zj@t7*Qk5Amt>yD5msXUn)S}!?%{mLQR8I{KM*kEi)Ggs#%ev&P8{{_qso$ViW*'
    '?IREAQ=_uVL~4;zLD(bzVJ#rR|x$clc-'
    'u)1fl5l0&7+6#1FUk0P;j`DM$G)$~#AYBu9c6!gM7IEZ=UG#NDCMI;r)!hDL7O_;E)t`YTYw8BZvO?4%#>s+c#O3-'
    '2BOlkS|%8$c0f6IER$aAaLhVBuFzN>xv^fDFXU6yAMlDZ$7h9`*vkmo0ADQWv9uKmse}1%s++=!-'
    '763dQ*xEbR}T)9hx>K$9lFP@dZZO-g^jpL#Mz^|Ow*m7orW2-0~d2R#-A%KEk=^2O8#48^c3N36gGz5%PEs$M>C$CV-Pro-_gd'
    'KAw8Ccn2Y;BZIJ;2MeitzrR@^IKY8(mJ55q<%ELgaAU~{c2ZlK;EBHJb<YWG*;MapgAbS^=R2wlWu|6{umX601lSht~0q8)%*W'
    'X_!c7oor^ykGlntycrqGHxM%VNkIK(EC4X>^%uENjIGv8(U8iT)Z`8yfHL|U0Ek*3*NKoi`nse??9tJ?T5#l!S_3~w@(S2w%8s'
    'rgBung&jFicZqBW0&YP%wrhG*=a5e3(NaKjP;0*NHWXe5>=2Yh9S{qNOjBuE#>-'
    'h!Tob6H8)u(2jFqWKq>3Ts&AnJHiHu8h={6LW_}z*#lGb$e|O`0;w-'
    'i*@JALfO!UF6mKqGMIrzk90O<zcX*=&Ci)_!#b>KBJL!idh~K0K<T-@;Uvi|ys-Te)Nh}Xu7^6_Ji1~k=`VsQH(0P)hYqB8NcC'
    '`9{j9A&sMAVi=sgI2Tck-'
    '=PEh*g83ho9}a8ep9?Dr=90T7n>(_2q&$B3&u@}*|sCW{4yF#Uj6<WfGEf2>LJY;LSwS$e~|;h4HR0NJ&&#7Q!m%mFT=gO*P?&'
    'XObwNyv^nJSa)j$QBEuEPn3jFuQ<WH6KX&jBKr#k`0g~N>^CCDj!U~&jXV`RvFpI^Xh1DR+D+--'
    '1FqOLFM@}v^h_VOE;wwP~muTUY}7M-POs}Dgq}Bd!thd1v>VlgA3%-'
    'p|WrVSKoT1?4eR{OIw|2G!D4sZ^o>P%&i;x0R_y$d;`bT;jJd5v6b-'
    'pu0ERKcookL1RL9*a#%1bpPBT7jKPpdQ8B8}Bu$8NJ37lYM}z*Md2}#|nNFhaM8Z<t0e5<bQ|+saIQ<^<9sN~fbYhI*c2?v7K&'
    '-+m`jMa!w?q;sI(f+tj0s@g!h)Vn^`rafbtCw1w%xs1qUys70~a_u<z9^@bPCn%-'
    '$JV70FZVE{pWuGoaoP#Q{Od^y){?~_sMqceG`C>J9Hm4u&CecF;hix-(`*7fGa-'
    ';zGE>^@}+$Ip>f%9oJc;SzzA_G{dA?tW?Cs27Z|)Ci18pdU~zbAdNF0U+AZuv`Is5h7UdqB$UIi`&2b4k{TW)4A+Y6&d&^xt`J'
    'MFi0`HaAucV<}TKjx;AiN=_zD3_k4a`WM^{Iqc;mtA-lo%q$&v`CM*vVYG#)OhF>NsaeIv^&B4MjDMlDTj-FvV&plhKO-sotOy'
    '{CZ(A*JG56F8TQ$QE>*g%uU5{>hqXg?yMii|A^d#uXXNbR+7>)-*iUZ#9Or{V!(rDCjqKz>5M)Bst`;n-'
    '#DI5RI~G<JUw8KKAlsHIMD4-'
    'Uu)e`?OdCd24rrO%G7Gvi2j0ZRd=L`r;53YWRp?v=PJLxUh!BNt^7$qqOzCBt}$Pp0<+{3t1oI>>i3nTEXik((;r#B1c;Pd=y!'
    '6M1yCH4jRyJN^ckKCrN2ex_MV#>f0<F(JBPK0p{`puX747cb-'
    '>|<Ea35gd9cI6{n>RMX@$g`+m^`WaSfzL?KoBoIe07_iPnI*YBER8;8Q+IBy^W!cc`Eld&pSYok=NvyZ96SfVZ-Wx2%`v0d*i9'
    '*RTD`HmkfW0rS7sgLi{I^-'
    'F}J@>DW5SVvLDT+4*_R1;*RaTah~^O&Oz24uKEn@+KqpGqC(0$BjM&MwOf)i)i5rRd^!ZOLqDI!7{Bym3n3J>MH#R%&{;sO};F'
    'k^)Au-)O=R(}MCddGp8s{rl9KBnHe4Lkd`*4iDdO|3&<=_IcLf#jQF-'
    '5<P50!j~#yygsE&oXsJ*yt>#KU*rNU1f@Xa>wNbuPQGKN8%{Ftfj|J)5{gC3$fBbO$<X+A)ipR{vue^U<ed0;$JwUa4{0`X`%j'
    '=`Ky0SmAdAmU7}vkwc2H*)nm2gl_?d8WZdzVY+T)P`s%405caF~at7m9{CKjLkr$ljZcC`c^S<GA0<gx)du#4z+yHrOB0xKTuQ'
    'Owp|)fhPOQ~bZLcwu`@0_#<1iQ?>}M8|QO(i`(mR$0i6w#s02D1N_i70XShk|E7q9Z-'
    'tgb<P67n5cvV4+Q+&ymOCM6b+5c3dZL1@Y|#9Y-'
    'fI_>YtbV0yU+WZ(|e}5PH_%+n1H00j0#@(WasT)lr@c$(INYA*Hc&rY)arlG2FPVli0s#3KI3L5(2bmn|x@cHAMjZK$^AkW@>x'
    'vOAH2n|gKOmbRHBh60yI%MXs2MI=o(SdY+_f_@oD=#!aa@EscFzr<)rMyf!pg%DLeYb2-'
    '02{=9HFpu9>4{(BliI~B^z}vaT5XkQlx0}7B93MqI+d+M~dEEj6|A0ye@Qy6*Wj1bazYvK`h9Ok%H!*0A=EuxUsw!j<K3dWP#M'
    'IMbXBjMbgiS0zqt=9W?ANuoLzn^BVNecmkkQKo99^-'
    'd7HrKT6kMksZviD?0to=C$DG<R!O+~UfYzJG5xADT@R@aw3)Q7~T~KuPrJk$83W4)POIoS-'
    '?<7b*up2=yZ0y;v$=!=n(PnP0LTsOWdS_P#_n)5)KB<uc<Q;PbgUMuT^Wr0LdsUy^8gx+uELB~lv(`?XBZiJ|>=OYoPv#IN0?m'
    'LRvIvpS8lZ;V>-'
    'NZt>6M}=Abrg_&LLdK{g}N^1y7_hwaa7Lv~$TLm%5CG*EWNO#(D3tbYpp`VO404gH+2!SZ?Txzg!%)I42&6I2V?B`nR4-l7}-'
    '|2kQa_b-'
    '&YE$!4K(Xjn?zHb_L8@sUR?s}#cl`$b!)l_ww<8WVN3h%)H0Y!CdD9YG=+_`eyC_^QbJW6XhRF!h+1G@f&CVgg+W)L$^Y9=i?f'
    'AZlE8{|rc(2WySD{4<p2^c13!BBY#mWH%S=6|TGmCSXrgDS$>L$6*6%0jE0zoA7*;-'
    'y$2eHZ^bu*McAp4)IQviQvl11il8fDRp|ra4h3x`&o^=OPsZ3Ndo71%}RO`2FL0D23a@X5G|M}WSyV!wfFyr5C@MhD^`l^%122'
    's?xcc^R}7AAb&)I?S{e+W*o*aoIi@x&LuO@{ZP)7>y7<jMj5_7`ME++<(REq<iKD;Ar>!`Nzg^pp4%6)aD&z8z!>=N3%L)(p>f'
    'ZC5aO3fakD~ywW7zN6UF}g?O4xy;v;j`IFnDjhFA(QuD={pdra&~}64BNr%WRL%NC!QJo#)KA*p6Cu>}k;%=QXs`w>#3ms||ZB'
    'cTQF9*c+l)!3>XC@v;q+xXETxAkANJXJdr#A|%KD<^7E^=h}M(je%%SZE|}07mVBbnX(&Bz?^Cy;D8DNozrC}<^afERIr;Cr{a'
    '(vYX_Dj1x!$a`RUo%l$PkpiCmKRfh7$ZqGz9m=A}+USP=gmG&Ij*TWf-Xh#)yVxSN=Ka6La-Xkc5jf}Yf@Y-'
    '5P$r1j%lwhZf1w|W{;vlSxCDn<QTDku2VsgJd|pKrhIvF>0eiRq5Cll(Pc8WWm;mGxSYxK=U8s8H(HZ+wEF%RQ8e*y?D^fmds%'
    'S=0zyDZ-Qs*tDWJ=^6N(us!~m4JjyPS0Q!*hyxyf^jBJd42WjNXq*MtklLuQjz+L)y`-kifNPx}Sfg$0)V>K0b9-'
    'JkW<U1?JL*tz?4r_;0k``9+>IjUt3Y@O$&@z7^b-'
    '?qB`fz;+5vb}ieoAWp?K;HlPQT2s&h}RCd#deD?OmB$V5(VKFVx#dcI4l!WsMkvwUzU_ztlxpP$w!3ADOh7%`4Y148WY&x}7ic'
    'Fq60o;rW|Ayf5|)Pq9EAV?#%Wp<TPVK=0myMeKBAg*}o{j=kCg}@**a7({8?=cDzo?}beDx1?nW|-'
    'd+#Aj+dL_&GE{{lR)>UJu{ehiq6?t|Ld;h5R_VtP2rWrjy*ma$P|<e$-'
    'Q9sFKI;R)ScIgxZQkp()X07;REJny<s0ZpiPqz3nS+${1f@Y@4%thYb_{#IP-'
    ';_p!%R!WVO+Rc4mz)qA;j+rQ5{m{2x5GyY{SDomter01c4i`UrqmNk%vV$ZF6C5)!_5ao0l3f$SW=QqrlujrBlxd#U*Q-'
    '<w65OW^ew@HeSY~tz)hQ4w!J7&qc`i|K%WqXiW14W-'
    'ty8oNBBpY6N+Bq6s@md_(7!+1W%`$OqgbIOxS9N2H~Gc}@N`V~TV!@R6cDHw-DaAFir4B{F6(Mf3%DtE)H^}UeVIX*RGhZ7QOn'
    'x4<tNzNpw{0Ru#UdhSxnKJdOeefZ?N3#3<IKNeIuNd=@@N)7g_pGq<%~2yNcYzQZuDX(jo!V+@@KFAIbB$Mpv*{9WflkVDksX$'
    '{co@bwdnjGDWihWQ-'
    'q&&^QG3uAxXrtU6aQebB7OYiXrO(=w~LK6jmD!VY9RV|I%+e?i2A9E=U2Bqx;Sl@2Z@y9t3Yp&x&TpR7Y&q!|V9O+36!xw+w=o'
    'wfj(gN!qs{o<D;79Q$Dr%BVKUPmm5{trXCu!_9Ll<)r~A8SQi+;CBAk|(a?m>~d}!V!?y-'
    'efI5&ll^B>n~@UVBHJKH$Lt<B;qafikJC>+P~1w>aGaM<;RGJQ1R(j@DLwTk(-'
    'dx%O<tngN}?u)`cYs=9#1l3F55(F{+wrd<0<2YmoI5UCvck@VE3kahkxByDOsUi@&)mU3e-'
    '0lN?b8kl^&APHARK)wW7o6^9LTLcqs+3Z9o17K5g^p%nWJ+*^>yxrqffH<Y^wS9KJuA`_39seI^8=}-W)>$TDhi0)wH-'
    'Hl#1UmPiTWSOg#KTb$Y(>WKTI8>Dlmy0PPS1SBqho6&3DIQ|qSR|s1lr6^zYh-S_P_Lg|2e!=55m+Xb5qj-;(Jc7_-v%S!-'
    'lQibz14_PtR*i50~W(&!ynOci2OIyW3y$HwFbNwJTW-K9;o^>asO=ho7V1T5Pz}6YhnP@`F=4J8HAB`D6Qk5Zdd@k{sTZDn-zj'
    ')zBDr!e7KG>gb1!hdG~D}clSgFCNeBq-'
    '};*|wa4Q^LcM%$DeLIB$~i}v@;J?t>zh>Me1GZ75cF!E>ZjrL2N)5MCXlwH7eZPoD_75PU*1CMJJ@u#xM6&k<n$|DY@HhF>1oN'
    'O_zDE#g3&N+W{D6R14K)mHSUa?aSB!!&Za9*i+!eVPRqp(w<N;M$8mvm1%<NkY_rrT9lE7Tc#5p*u5-'
    '$a!C9;au(8l1KXo2&?7VH2h*F+c$b~O_urfpm*WCzCFtwqFiw9YjRbBy{`QZ8L+J|~T7VdzF=j4IkE)Nr1vGqw=sM`OsyBfe~h'
    '*k&#Gmn~q4ItCZ+vz^+ictsMYKzPP2Y(q(rb7%!kCQQka(#n^8jF<m(Fgg3Cn9BH%{BwXAI`#d*+Lr~Dl=*8pv}EI8WqMP`hK}'
    'L2bZb)uT3h;M&j`OZSDVR;#rQ(n%_PsgLgq!8lr?_AoTiu4ae})(+q%eT+oaSbbx<Dg;t4i(?O()D3~ogf;~@Hg80b9#OH9X>V'
    '0@#<qa0tyZS59Zg?1{Fgbsl^m4fyvlFX@3-'
    '3DG5erHHwuuJ)spZx<)}8bHKF=OCg0g6;i)Urau7j7<0$z=Q1fN$oLd!(BQ9I%uQQ||<NsIM>BjEi(Y!RGXiont>N#xiTnD>nM'
    '68pW;RBOLm`lv(K@mtwAOfk)e-K!R0@3mX4dn-'
    '?+yI#e#yOzE|xI?0(j62=U!{1pGJReJ%&xYM44*o$<z~5X5f}>2>oXdNd<gn*1wRVgFstJEb1267~$VjvqoBl14G^grINwhMV;'
    'D+|Q-'
    '2S|kvzS$U_I`|Ar9s7$n3k<WR#cFVz6n4qWr%`2<Kr~k5qa*jKw?vrUh;B)S`k{{O$c?ZIsq<Ru`x68^&znUZ6&&UwkrVD*`XD'
    '(e2IUX$=>lM;Kk5|%{x-'
    'I_P`&75jS;OFsw7w*Dr{=8vPI|z{!%ikrs}$OQ#0QHCAEX8T*jD;Ni?_Ua5S_ZNV<EteKgjnbGtHlMZQOs$vDyTt0#7Y4G)FY_'
    '0|WbPeXI7rt1i1jzSTo!n_YzZ2=6@tt2`x=$6>AR|nl#a^4251H!fNYe=O@W{40!CvBaLP=B8#W=1Av%4FOVQSMm8u^XZ4++<g'
    'h-oU{NNBNX)b~nF4~9MO@C`F&YX$GcL*~GUw1C!DBEqh4-'
    'gBDlQxOd<`Izbdo_I$qdUg5_EQgDydMu17_oDay!g3xe;Tn3fwPr3$9azd=6`SEY`xwu;I<yVRigXgmpqP}BsJ97tL3qV86W{+'
    'dOKDftn;wX7Nv!ca=vhi;$!u(gDp?QAc&0&~;^Gz$Tm?${43z_JMmb8!{LxYzwMm@+CjN~vh%CQnXR|~{`?_|{Vk~eCN8XSQ0d'
    'FoGKs{wGCEm)Y0ih&N3K8PKBo>^ias8j-;Bg-5i;$IF&xNQg)7tYThLx_YF;>-'
    'zhnMYOftJ0Zn!`>mA`~Rjzl5VtS}Y0l;}B^DTxsL-S!jl9PA)u>54f`vv1FF$a-'
    '>UP@M$k9l6<5I1;n5fL<Lh>TM?H_6cd*0B2Zl7#W}R5-6r9CmT1ucVSmc-'
    '(JUMz2Zo;V*eCqA00FSdWUQS6GB>2VlBdWQx!&81A-wi(;PWNp|JU3c3%|4~-'
    'Z>_7NE^Av0z1w!Y4aNOeG;Yc);c#~sy02vyb?ZSHn_yC+0gbe;7DCTmxAa&2!A(ZaB^_ivtYlKTN}nuUF?c{R4ZHu97Q=Y1K_S'
    '2#I>=djm_f_-YsGy!}S>obMqmWN~bl1TH@s1B0AwjI2RGqsL_D1oCtt3t+e0im?Uuo*Nll8Y{ygTQpAWumX!p7X7O_`^ww;ewD'
    '!^0?rY7>|0{p;0_9VA9)xw}2;tD732NWjPHB5i-'
    'JRKlUJcDqb5pFwPPh}jv;N?9wXmrhwSw&_fE1IxaH5vmVf+4Kex>cl8zN~?hYeBO|N9>^$ms!e^>Y43piXV@M7XOD3EYWKl5OA'
    'CO!Fpojm2Jvj7zX8g0883qb>I>Dl+;HICv=9tE@SfAWtFw{Gw<fzE06)eZWjVs^OV;K|s;tJ2IT<gCBOxDMp@yCGcO~A+IqN{2'
    '@C1@+&}@S5Fm&X`Mue!_aY-'
    '2h#Wj9EeK}QUrcCrNtYQPNy9UR#D94%u>2$6E6S1<4L4or#yT`gj94aK8#>t{l($m_IoWbc_nExmqIO8h)w>yC!$2-'
    '6T*8Pm^e|QbrgikZf^KJdVlNX%`*Jk-'
    'CaMeZb}{kcXawXA&PP#U~6a(+<SGJjShMx5L_R)*41E%!ssZXzk!++#RCR^wf^IW&~iC*ZHu-}Il06K6VO{`SD-nY!LGmosyr?'
    'SUNMeytOU2_@cPcwt1wEhh^DBP>CVT8));OWnxIMWkd(_tXh{g9@<gxSXqKoqqg47NiZcN%`Te?+%zKil3hye*gk?&v9;oWM2f'
    'zaIr%v@x3?Z<9A~<26S83;KCkdR9QyAur8vKbL^t#sRo~3P%2LmViKHme7;WC|H14IU#?ChM5?r$ZgQIeUGF*+UK*@nT$PgcP`'
    'Ng2m)m=WHns-tslQPiVwwQsf6{WAltG6K1xs?6(Eb^sX$czZnt>y1d=_=9Ems9IE}46!cDHxNI6Dj;m3rPL=4dv>P50nFY%%LH'
    'khR&UZs3z@P(ZK&~8sjax+Mxc77m!H#ct$=0&pzklFJ+>Vj$jR(crJC`u_w{TvI9mJL#DDBPhP~ki`F?T8MqorEhl8-'
    'K23)|6WIS*?*ds;|Wj(S;4^RYJc+*Lw{KF*=U;o@EBCE7p>qL`9P9&?u$kUaNN3dMp+lD7qULYBBt!5BmM`Gbe`HY3OuAor|r1'
    'U$PxkJ1;*s4!+_k;ub0p#jiqh4=Ug!!&kh%R<<2%x!9h>lBKLJwK)Og2z8=5r=fZcNPSW2clo;-bv4=L&GHmO#T4W<7N|-'
    'Kq`o3#dPk7w$dvE-s^xwT^V=6_&712m13vl%%DeZ?uUSw`w$HcJXj++`^Hg4QA?j<@dyj@bWw&miFz&%ojFPe`sk-'
    'Z^cN%8+@GnYRT*8ycww2)^`h-4_=-'
    'XD{j@@R~hUS`3{6KH!y9Lgd1H7w?CbQ`$`dJvAZ@%P6^0y3L6&bgAHQWvubvX1GPRJ;(Z5XQ(Kx6w)f9fLF}#szE}f@?oN{C89'
    'EoeG(~ug3oK~<rM*%;_QW*eri${j){Fe)^j?!fE>X|kR%0tR3#GpDT*D{a*4Nmxin+gR_RzEke`C|NFQ7xlHEg|CjbJg~g81=V'
    '5?W$-YVuvIQEl7y<Sww3r?u!8F$T0Ar&014M>pg-`Rf5^;9u-'
    'huV95xdu1xrv0<!p{9;cYyscbI&@sR5PY&A<VH!Hn^R()|1_o`;2)|DAG%NeEwTfO(y)4)}6^0C?D`)S;Gg)vG$Mhsy-'
    '19llp(B+5?9BFjWq;pUJq!A53T!{fvP7kh_naqsQiy8#P`cYI*(Nhz9HF-'
    'lk75l^G+LFO*~|*PT>Z&!k#_+E!ss?gp+WEDNa{gDpq}@qNnhMj1IZl*0JZ~b0{$@0#!pxsrYej1x%ebx_-kn2UWwdCOFgsM_U'
    'bT`xpOh?=1qjd3mO?AG?|=;6zO4w+}{&y-'
    'U~c%O5)4SIcXTeX15aL>837XXO2f*U^b~kJ+qu@!*g!7$}3X1&EfQ@*qt@%3?(`aVUtkZP3A9FY?tifgcBtfis{=zcDL_~Yvej'
    '|YS$!I+pvW4f30u_#Y^+9UA^-siq`>nPky|NJmxOsGdG`Wu~y8t@^TjI&`PNO)70QmQ1J^0Eo=^zO-Pf;5Y>N)blJW$Nz4$%7S'
    'uqs*7btMEmln~Pp);KJE~hSI&ZM>9nu|o%@X?{XZ@1G<%3j>ya}P>=xxv+%Goi(ZQzi?IXU_rRr7_Jk#}Mu(r!Gv4#(_ke9+6$'
    'FkP)d<BR{hFsbw<Ki3jw{cKG~$rLaNM~+<W!Y(%{%IHNqzkH39lI^#q&Y^P;L&G~RfrNG#r5IYkTqIOHYNMV=tI1y*V!Ldco>%'
    '<AvAD(o&TI>k=I_7wm}=4N(?txjetf%w*pR6Re;@32y!Nu3;h?0Pffq!}!<}i){SF@e4M>rJ4VM%D74~zlUp~2olgCLS3>BE@*'
    '1kqp<lNmh>Qj5!E}<>kEzA^(hNX&a<6F^PQd4~VwbcYaQZvNu%G5~PC-'
    '^cvhSY)ND{{b~K9fR(cijH1KfJ4mjLd_Q0#Q~NTt5*G4fAf<dbcf^A^I@Co|K^<Rnzdnrd?r%kxk|XOH_9hWjLkSXrI8TdbQFz'
    'TayI+ZF4UY!sGNjSR4^uK@oh(0MmkBFE8Z8ulfr7j2*M(4{#I&A7(5GHbf{qSX@UP^^>DPR2tN)h$lO}B}7<c2w%@7Z+yA))K>'
    'fsS@VB|N9Avk19Tyml%0o3cEVPXV-HR`Qj&YIV0$c>`3uBv5s$}si2gpjs6-'
    'OPd+}r$wIoPKPop%KxGKV6Aai`DhF~l2>8+shu*y(Z@z_KThcTN(`Kq;D)op}-'
    '=4>@<r1RJrkqVV9T3Q(&`IXF?JV^ho;qjZ#x~Nt!M6Xka3a}6KUG_n4OR)urUWkgZ=>@sK83bXua@N^UnV*4<BYG3`Lfo2r&$5'
    'H;TVMrKEuwf7x-'
    'vk#s2r{{<v&1e^6VcC0vRm3dnDd4wRm9;`7vyXNJy!fPhVh@;gn&V4_g*sV_4Pf5TAE$)RxTFB$HvR=DNXo7&^GLu*T2|x77Vg'
    'VMz68HL={vg&<^h5nG>~SoCc&419&zIr58LT6WS<jQkGGN(<V-LGlqp??96dA+$c6uC>Ay=;t&z>em+-'
    '19m&ue*{mWg!ym<g<2oymj53bJS;17j7^v?r??#<RF%+$ZXlbf;f<}k&bnK5PdWNnQwUf-'
    '3c+bJL{pmH77rG0B@(4Jq|fW!MxEf<5<gY<E11j9(LbKEHm7wQJ|p`k?wW-GJAN43X~?VSEWNJ~=;@DBhs5usb>)o&YWJZ<8ZC'
    '7Ur6sr`D7cCDgoza`SUNaV#s;<1j7iNI-U&3d_!l1w?qPj&R(#;a>Q}W<XAojlIfJn}-7bOkPpy`=Mx8F14kZkES}=(GtbUTs-'
    'sRXQ65s1ce8zx+Uf^>pq548ZA??<D>%BZ2r=+%>wal<29?W+TiL0^J#Vj_5+J?kS(H5tFpYMiwlf8M~1QIq-'
    'M5kU;6~=!W<5<nWFw5tt?bX=?f?3%=?();Vx~H0Ni2LjxezWYIDI3Nje##ROLUCFBxFmQoO?9?hGCEs{L1|k?ZISOZ92w<a3$F'
    '=I(*mvGtg1d?dj6?Bp@ZHEvBXG@*SDw0h~k4acm|a09Kr&IWj=}U!ejJNoJ&R^C3v>>PsPS4L<Uh{#*u0d`ZQd)=bkRvTK<*#='
    'lp237i~zSQsqBnb8^)X5UjMuiydzX-CS=u!v;O8_f$mWZLlc663`%&eAVbK+|K(;Kh=(i2Sw*M%`?O(C0N&a2wfrk(Exvgaa~D'
    'Df7Y6yH4WRD;n$@%c3m{+F8Ua6X2qgy&P{vQ@M&2Ce%R)i^*cr-Ln<eT=rF&1-'
    '!a5^aP_iShR`Hr_Qkx2Tq;&v##CKYV3Qc~WoVfJE{cb2whVB$_kQu6Z3{<0>?a<&b-'
    '{Y^4EytA;WTv^IG%pcF==+`5U2)TiRiv@+RAiC{uv6~s#ysJ;E}zoD3!u!&?p8JhD|%zj-n?}87*E38}ZKHLo(Vf(;O}clCes-'
    'A3JMZG#VEuzoX=Ye4KfU5jbDU(u_m0xfW8CK4cAGnOC&`VYBXFRnnX~k2WH`fGw|VL(iY>%2-'
    'eWkIc!sE4l)N+VnOL4KATBW&&hTC6%a5nTvOJzE=ctnRmM|ytp;=aGY*%Y`r?HN&5%vh}@`;P8=hIj6SKxlc`sj^~p!nBuq>i)'
    '58&IH8#e)A%#=lx>mm=_5ZIG)q*vD-LzidjCYSmLu?ZJ+7Go%wH&AlcnNpC)sWlzfGmJ?yZV&$WXuKh#gxe4e!8y%-'
    'AW1gfVR2E<m0)aGIvZk=ZpPp()~Dr;Jr?}u6Ic`gk`6|Y)eO-48Jccf~^jy1D;kKr+E-'
    'toHiVL=x{tybva728wYTK@Zd~4x_4;qVcm!F<hXYZubksVHj0WjSDcH6ot8KJoN~!N)f5hpxLO+cR?2|bO{NFpTw4k!-'
    'yDZ2F)`m|&W~sHuha_em~mf>fdF}9Jfwdf{@y_+Y6PptDpuI=+5f!d(#TLRtrr_>!yY~4mPygdPgS5DM#4JTU=4W5?PPL-'
    'L?EcR6KOuW4>8veaXrhk!_|H9ho{kcw)~Rx%R^sGDAp_Mc1=!TaBjO(MlG*^o9_Ym-$j|6MxQYNe-'
    's7u)sJ1p#g!l~UvBLwB$;K)LXbh%iUYTMrno;(jv(<H9y0T#BB50yP~YNsaIFT!ki9GmglOHtR}J?mjQ}?l+8lrd!s|KydgF@D'
    'd;zN5n<Bx1%vjNS39U1KVlK|$-zv3Y;VCnuA(Iw$`sHE$imlzI6`|F+-'
    '=d=s$6kD|%WC069%>A><+`k)*XsyJ^kgYKGOt$%ADAhekd*^*Q+vSKyZ!o`uBuK?_dl0d%U||}#mx@UyTKDmz;+IjE56CU_=0#'
    'CtAHwwm?@vKt2Kq6{>Bw3Xi}Qe!T3(`6B0N%UVK4n_h->atw8UfVG1ry+gbg#0+<_<(p#j4#8tS-P7R8}a|<kHI3%DsHw1;LR-'
    'b>=1bZ9AIPN@k5OvWcsv{96<h*yOP*(XoVw&igHrg^SQ$MX6#c?S*A-H^QeY%47n|c!iRrBVggD<bf7CA4M{9TA8-'
    'i9NW{P}#}O(3o}9<Baf-SC!L3pEff@YRQxIEpgj>GZx!Uo=a4zEMB|c2>c*GM407hFtc821FU79rv>3!bz(M+3k!x0r{=WCSuW'
    'TIU2!jDmr2M9+%L63iA+*Med&LdPk37;P*vY8%KN622sXO^iGe`4_B?neUgIwlTmxKEHw=zI(g{c_j$OB)r`uEL};C&jAc^N{-'
    'G}7#;h3bNdIsR-'
    '{lFTpjyP1);N$!>jq})g<5x4NW2qvdX(6$@T+IO{Ea^`iso%s?6d{)qrs}z;t#Tt6q6jk5fn2aND5bWY*8Agoq<vBWV8W+-'
    'ihs9%o-'
    'UwuUdx!Wqge&MaKO{)bZe92su>pgae?R<IN<gTDb%~UGEQCvy*CLl<IIi2DMxH&)o(JduC3Yp{K6%*}QgkV_}cH;kk;w?<20{5'
    'e;qjOP=449uoBu$p#T&H44_w0%a%4XFyrH7LDFgdHy8!msj?vZxvS87F95s@I8H=eR`$+kO^o$3|G?yzT9Z*zW}qMZ}7W`v*Y0'
    '!r3qOuLu#2Ut8{*cnprOg@+o==@G9Hn;(<)f?>5>M+xjax-c9rcYPD>CGtyZ<^%d~`92Za=d;<)n=`|(-=-'
    'UAP#r89jx<^16nH#(b0?rzoS@rFHcBzzM>v~OYV?>zC5JYl^?8=81G=mq&>wWJV2I{dbB0Th0>jrkwV2!dSdvumq+u+&oS2s}|'
    'NQG7CF4q@8m=7{r_^F8g1%5uh=_NWg4XWZDzHc4JUKvbo$*omfwcYy5EjW~S5$mLz9Zyub@nB)#qL(c(mQlZvIykm25J$soC^B'
    'ipqs5awEHTo|KD-'
    'N}40q$uMGw`9+|BK>d}F(gfBL8}`F1R*{t>j(SB(SIN476I4i|v!AFP%ltGTHxpNoJQX8~a>NWl6HSf8QO>PY1oLtn+4X#EGY#'
    'j;J2*rFwY??#x!Sp`8Yh$!Vl5bNaJ5Badsm%p84yG*ix;JD3kEh)^1WN)te84ZZN^zw3G^kf^-'
    'W~~33Bq~V>;nfBD?l5=3KdY@{3#&Vf-'
    '=cEGQ{&kh;HcSnkW1B>dspa7akQ^WN{f?RHbx9mf6$tP1bRM}*|Gc%cq!}5lySheyNWlR5%|r#$UT!S6T@<@g+L;UwO0??U{Rv'
    'lVCA2$qR$`FZl(j}d9sb3e$ZMyvPuG9w(N836M<&mo210xPx+);N}9sX;f<|$l1W1YM}Hr3S%Y#lyKWJ5IP70Lt04coRBG!Xj@'
    'F<WW09+2IER4Czy@1-DmLWE_lc9ElFhs&L1|DxkC_^2{w$kZ``9INdLVuyx7TxT7A#IYxA~t{v&FQ&-'
    '*@aCxGO<90=EV(qAKmACPt`N0pX6tg`qe!605Bya6(H+wFb%S5kaMyMblYKd~Wt7(eKsmEZAI0aHu2@7cTnZQ0ISn$_dz@>b^H'
    '#Rscm2O15p~(kWVQp_}#Bj#f*PBvNJ7djJrkc;|x|ek*q?MKJl4NToWQ)jusJFaj6y5`U98TxK99R;h2tXw?${f{Y51Rs3@0CP'
    'YM~tV2%Fe_{E#NGd!fYg&HA2W^tY47o?H8Q>eTOF^MbcQNB~Y|~G_{g`=c!7*F*RpEUoBy4vg<^d==XY!g+z(}|JdrVI>xlF0('
    't(mqJDF9Dg->HF{u-hl+y&#{eQ1~*e_lEZ2=h8$TB`53C8{T{p0pB-tvp2$%!1?4I%s@+E{n(!FYgxe5ll12-'
    'E@N^ML?M`ACyZCSu=h1hX)()P`3S41m9gYAMG>a-s0pgB25o^^WYiNT5y86{YMbK&)-'
    ')oaZwAFDQMqDZKxsUgpBUql&hy)f*<yPz75gPA8YpY)EH3Z9J<E644qQ^p$i)du$)&&avd*%3>oYDJpX9vmlFG-'
    'WSF<qLS2p3d^osmBMo{X7BxW=p+ctYb-Yl_pihAoDjg(K3%_o>>zqiHEEh?L6QIEFt;3>?KV;P8kiGjCxS&BXQpejl^s2Cu_{p'
    've_|0DE;X#lJoTWVA7C*mrt7+O%#v^pJ<lfy(R*kA|?&izH2dhWW#1Y#V#(5)^Qd}(UPBC$on$8>D@24h2;RAwT~#A&Xf;7Se|'
    'pte$T`q&5VpbiB4)FQ9+P%Fo8J8E0=G(x#RAfHYCIIK%Gf3Rk^fG9;Qd;COXIGRL%6=}gBA!N!HP`Kj-'
    '*GCH@|J1A`;UwBFie5z_e+yWYJ$D=)JMTz%1aGzWGdDO?HF9o6*bhn8!QVU4?(ea_$TrCDEz2n9Qq#pb%VzI@fSumR^W4|7KP('
    'UE<seBpQCiS<n&V8p-x{IdU|DIjftW}~82fk)ge+8Jp|%>Ch<+bQZw@s8EJ^7<sxE-'
    '82B$P`y&ob1ode_gJJ#UwOXc&pxv)2;%Is%-'
    '?RH6bj`T`kkP!#3r#U=&kz^IRE@^@7;VxMZ_1i^%I~col?u6es{s&irYq)arBtap<+6*<eMaS8zK7JeNLs})JAEAhD&*q&pou~'
    'jiU_{6LFJ&SYj7n-'
    'f9~ec`+OJxZWabR=F&VAaHP<wgfK9#tmVlF6XLqQ#2p1hd=2Nm$1e<B;g3v#7)tlKmAas>>HT!@K;(kd(>joa+2raG>jjD-'
    '!H?-'
    '8?r)g5iDHVE(BsR)GMt7dQICU)*&ynEaXSzM;R6pJTfA%TX&_H&TKk_r4p#eUVkX7Q(b1ilG*#pWdG@&54w|~C^Mpe1?XKRdBm'
    'Stdl%Xq#y#m#)q!DUX~FIko^r}aJbgMXF`a$M$_zD$qG7DJG<at0Xw|Dk}pA*YW+^^-'
    'vech|(6*d*RtI$cp=5(axZ5(0%Gn0u+Q<kLYzLktiu^B0_$8}%(=Su!5p@znfKW_2P<1?MzACNvQ5rIDp+VgW{$@O~_%V=#RVa'
    'u^shaB8Ffkjej+Iv@sjW%bekJ!AwyvRT7v|Gle-'
    '*s{B;qoe$M;j={fSwF*!z4Yc3wW5lDkMxRfW=B2vb;1Ti)1bxvwJ^q!UhB5Fq@vaOA~~-640$^AsBuU88~N!-'
    'L+_tqMwP|WNFdZYKZLmCFdIpCQ;yghREmnqeVsP1BeV#3Z3Lm7+PsSU7Jw^>a-#^4=<Q<@nM6A2ej4-'
    'nHW8@wx$ao^h}qc{2DO)0F^v&oUic`)d3sms+lCOaImZZ=VTfz$`sI8ETdTLpsqe9{s&oyZ7|(j_o0fRf<?0~G$cW?Bux`s&nl'
    'r87onq49M9+6G`$jWBkWA=_A%dl#&BdljMP*4~k(Jygl5NUfFEdP!O9QG^C?rubB{MMeSQA@use35j2W;VIuEki|j62j!E17ah'
    '08u!dHR2w1ss9iN1nbJU_Z_#;B8l=Y<bJLXiHmCEk<A}lKJ*NBK16PJ`(mc;v1`E6OV}ko2+~@L39bX#-'
    'J{>|iS!X4S;M|GNojW1qiRV55#w0H@u_z*h?~DHZ;d2RcbOoYG?|x6y>YNLSvuUG5m9o$$()y2k)J}*oq{pn1X@@y{DNw)pXFV'
    '<^_opBBTw!5>Yz0X`ciQYs@t`jKajhj1UlECl;qpB0hnU;F<|2gj&f}ynW$6bIKfshauRFiL?6*V6OsM70;wzR<6ggdM_CS4zW'
    'R;uzlkC&oY<V2pllZv1uQ4{M$56Gbg+U*IWMK|lx+Zcj)g^o0<{LS&<)UQ4)P7@&=!X)Q7A=1ecG~MTV_QHA8rrx=xL~D5BcjW'
    'WY-dyS@Z?2v_?Wz^ayU(W&=8sio27j%tZ<`_n23Z&E}BPtI}MadYZe?frMrt@P%dQQe&MbIhXb6adh~28q0B~pnMd7vPLRXsMv'
    ')Vj^ri!Q}iFp0P!ePPZe%2^qql5PPTeej~;+EM8C26TK%b3sRH_xkNAgo5K>0zpjZ?5L$M9yHZNI^s>^7+KS?_{_`P(~s5bBxq'
    '2zLs4jB6L=NJaKI<38}Fn~@&$BNMA*_6QnnZIuXT@5`^@+<jDDng;|`lz&@u6t7?1%19&)u#dFb&Q-'
    '_m`%hLd~*6*5ogeC|NCN}V9TcX$8rlQYE02#Y3lwH`P7vv`JdMNt!fxd$)bT`cS_Z`K-'
    'fX80K0UB%{(3JQ0VZE0y*21Mj6z!UB|LVdPs*qm|&$FkAP%*2y&LCI8z+TQb!cR3asDNy$c8s%!73rW&@zb@=81Sh{UZ}XKpcG'
    'y0fE7eS*8l0cBGx6j23wz$n$(v({srgRv^$${UFM7Q|E5NX6W`AE2%BrkacNoOf<^i+Ior8N1(sfa}<zu^+!Yy%uj)q%)7d?>)'
    'k<R2jxO!9GNmDfC4peQRhv_;#jWn#43y*cZD=vl_daE8a_MdRUjT>i<4G78q_2*+`WB;nRFcKwm6keR?le#_dP@wK>W0Ng0l@m'
    'LRFvJTG0#j7ypzp~N@W`Kad;%q7`#B6Gu1C{_xuPn^0)38!z0CgFoNFY93h=k7roX5iTsZ|xR~oR3Ymcna=xy&IN&Xw}Jk9-'
    'h{Wn7+!-'
    'wr($HHDPA9k5mG)bc5iAA;_kjBM%41X8zK8=llylX0(sr=MY#%58mI7tjC_O97I!kE2QyeS~FY2H`1(JrDXiLrR=Vb(u0a2#o6'
    '4Z|A`~ai8Txf$0Y6%cK#t?RES{EQiY?VSQEubT^Jf1YJc+w`>C~ds&$th5`?#Hu?Lhw0ic2cq7=|q%H_tionhLdTfwpo>2C`o_'
    'I#2LN0uJ_EPr#`QN-'
    'm@@fS%3mc4{0OGUoA^ETyA@)Z%=tM>h|PFFVdDlgYyw*Qq!=V9Gwy!^7HQUl0!$!oNNjd%xC>{ZyY(2PxQ;)s8Sy$e*dd45<-'
    'HDd4@wc;orh56AJ*!S%wtM~3`D|t)JT4tCO`ar_`pon)eX<HXVvI5j}><*B8wDBp=rNdI4SaCLb$Nga59Ii-'
    '&?1&^@0ZH(j^t;nd%AD3n>&&wnF5;Bum5_ka``T8Dg=E}`%@W?edLv}CSjzIg%wFVH5`}rs?3nC^y1I3jgNi31rHOWSiSbQ{w1'
    'NfOS%9;uw9xZvb7K5SDOK=H=jbw(K6v8zsIgNGwi6kJ#G;5DKXrK$-+BuDF74VbtkhC3u~?%J&TyHp-UKIF<fnKukUE)@qv!Sp'
    '2>jgpAlW@di+zRAXwg+oiz?kJ8^{oiX!hY=;GM2K&Fmnx$i=M<_YSZBOrB=vCq-+fiig7<IcCHt=u3~AGh`}2FJWKdjh^-'
    'V^<6(M>Mjcb$4OWg#VZVJv`L!_9W@jZ3ZVI6(LDvb@}eEKOhD@W-'
    '}y64<2v@c#{6@wGQGZ3*jNzm1^?**!XAj!`2OxRRozxnB@!}jQ_g;lYNue&qynj*vYao<%h1xg{bu6ANb5MjGEq9oYqZofiubn'
    '3f20T0E=@^v=(rU(Gq%@)xLor%Q3zjm5)CQb8jp`s8;?X&^#Q?Twu?^1L(Sg~4|cFj*AL+A0rTDu<9zX;z?<ioFa10|mB!%Z$g'
    '^Ak!TUl}UdOYltIX9l;vSZDJl(+`D|Vk8k^X|aGqESS^|tIkhLV%E{&x4^wfhv`CQrhWAb*13l>^0>g`3c-'
    'K${OWH1@lM!0!sPg%xkN7k$3*Z}ydU@oSdNFVWm4M^ry9yc<Shy!2FSh56{9v)nUYh2Mj8o9Mu_cEIX~AZ308FLfFIZWHltZWO'
    'b*Y}&a-DKFxB$Tg>pvY&4BwicK4K_P1i#((0ra{G--m$FfA#3Fokz5aqfajJ2)kt|FUJ@28f&g1ayO2k?>&K~wIjNM%+8z0!!^'
    '@y*9vid)8HsQ^-F-'
    'AHuizpCp$u4Z*8oe|Qj^U*{Peo|Z8f=^5)NJnU*RR`a_C>cNU=lStl48iMjg4OLeuQwvR5Z4EE?<NVj@BrZxd<c(?-'
    'spj83O`b8xSyw-'
    '(XS>m~yC?(K_bH$1NS%vtI&EbfU~ph+XdLc4r<JJamul=s0HNs=p<&qj8IUkMT<1K4CS9@Znm6*_aJLlUf0cuuXKH9;=KvMB+s'
    'M_9?o_*~|}R{<+i_QHbbTIjcwlbwDk}lr9>z_q8pPO%Nrw#6NztLN2<I(df70|ATJ{9{=i0x--vtg5d8BV-'
    '%>yffLDPEKL@b>q70fJLLbpwpK?~cgs-c38-'
    'S;nz1n0fp{zjtlZAaX8HNlk7dwUZ~)3eoh6qiK$Ir<CUfR_FVy@dHjY9{41K&1__wCcV9|cXp2$|<)>Jae3SCC9w#uRkW^a8Wn'
    '#5Bt)KH#k{K6rCcgsuN6v#)h)58mfZpjiF==CPcV|B*i8o}mobdFmqYtbb4X5;qTfcHxoOH6bxAhCQ+5orwtyipsqC(Z#M+4)y'
    'l=AySY&5+*{?LW>{SYur*v1sz0C^_LGtLAXEu{!$5+8{Qv$S$N1+rEhT=V{V-Y&+n^HlchnYCniZh(z-'
    'giva$Moxhdnx?G_HZ|{=FE9gfYv<T4_^cr+X?ZuO~ybPJBOerY?U6Yb?h2eR)&*(4l2D#Y9CTV-lr!OG*PYe1`{T^bMH%>4e_C'
    '=n;@YQo*0VV2OKQJw{=f7H<)5u8jA8-'
    'mo;HjUN=i31U*jW9(R8JClGs##JohyV?e5xtMg?j&u6;JC*Qy#w}o9Oo}IAr4U_MyVV)2x=RdnGLMW<E^R306+?%tc~pXK&)A#'
    'Otubs_}QVVT1xz8biF^Ki!j!Ma|%3X(5N!*J(%_B_a2|2V%73MdfALBT(j#upa@)?hhu7{`%1(_fLLsEUw9aM#CltV6R)^Iy_I'
    '=hF}FbFn3@PYv*53-P756@&u-CYKwji28rh6voLjZn35g+Q~Nd-b}Bnb3h&V>JLT-'
    '=K+!g+m^F48`+blu*+<WaKg>DK1qpnpKSbNA!j9V~{?<wSI>skp8Re(EULvm<|Mx;H$?yaytDT%rM)0|i`dr#3>L+nS1R*H~hu'
    'H-gZJR9VRsyJ}6DSn*g|o=hFGv8we*c+?)kAqqayx{`FJ1UNPE0NcQ}8l?9SXW|lF<h}>A`R^7KBm*ehN9IUqQwbZ!f1R{7EBq'
    'z~EMgyie9o#<rMaa?UB%Us2^)!j&D4d@S8q0Yo8hXCt$uM(u#%>yDN!g@bLM)PTim!lX0v_N=Q7fu(2#yxOWkxq@XF#C)7gnmi'
    '6pTCbOzsJ;0K*kcOQY$|beDgs@(E$0(KD0gafk*J*H<Gps@&ZoKNk<eOF-Q&ZT#u{a1r)2!ivM7N^_2huwU@vW@`jf-'
    'lB>DPNmUZYtMl<Ak2uvK%*fqzVVbv|}wcPx-ECsyyC;0`d(5_;%4S`c#Bq+lS>3xsPUf(H2;)O})HAQDBznczSE}B_JcN8W`$_'
    'Jg>kq08+E9q4dJ=j3l(+}XdAIny0;?vws$T4KFgvv8%VX|7(*s&<5tyoh@?LEg+hG6sYYj`9H0pBh1^PHnrW_labQ(ys~P22-'
    '&fKWsPp`Wdj-oav8pHK*sTIZmdZ)Cm)eU(U4B$ni$&v$GQW0sZ7skzh!xhASZ_k?I~HyDMC;U2%+l&l$0m>=WvZlZPK9-'
    'w1c5TH2#F8GTyI>X?P!J7~PNs`I{Py8-'
    'ItVf#|z<;oTY7N3vok(XGq*F^$LKKha3tsC0CgE6BOs33oVaOiRBE6HBG0yoa1#O#oyXejnft1M--D$qd1*5>#Q!$5Q>zBI$yo'
    'dSV;Z*1zMYN)q5$B;U)Ilx35<dDs>vTl}1qoNJ>k6p@frR+4Vx?#O56{2nhuWlBupWh|$`g5|dKxc0!LT5VNeJ9PR(n};Wbm8Q'
    '2%4UnrjeGi3BXKCw0<Yxzayr*!>J>C>HxH1Y(bXwZx^Lz4wn#66uOP-'
    '7h6nh0y00zgu|w82YLcoRR8z<C{Kvm#V^G}|1js^v~TQyNZL<6Q)GC7uTf<vDr>5QhdFnyCo*F4%%;;Q*!ll_<|uuiJRfqL$PX'
    'lV>2}(N6AQpwG=l{J!mg`7pHVYadbpSG;zLzp1>abJ)(EYe*i;U<dYKXh5XC3Lb^iLT%CrxWq}OOi_otal5jb%V0N$_e`gv(Qt'
    'Baf5Y07oT`g!k-P8oN@Ujjr0&=;6OWNLzf0q#l-'
    '<>f+AQ=l1(lGGr6H|abj6@Us2#GUWIvL9Jg^5FUO=>%Xz_WhicE7Y|F9NiH}TH|S-G`<-W`}&gv3d(CyzAZ$~gA_-qae6Q?>W('
    'oez1@pgdTSlnIpem1C1|)pfF~f~^%tpu(fzdjOf8V&(Njx{;T5(sQg;VsRsdfSmuk5=^Ol65fOu8I{>TgeZTU=N>cTytM;9hT3'
    '$IK4j$)gQ4wRH||2NVblsZw9j=~T*nyV)a8-$%at=DdyEm~_LS2a`VVa~HX^JKU#A$NSZH(PaM@2;U;0aCQr3<qYf!OSAm#-'
    '_(l$X~~r=`ICY#`G3^?A$Kj85~@p6C^8qC5b%WO7H7Ox>4Ok?&!wQ#)poFW2}WjVv=%k6d3g&<!Idhu-e_ObG>$KFs4W-'
    '5(au;w~-'
    '=rFk40Q@P_PBi#nCFointLzw_YCu(|&!A$VyiW!?`k)m`XHyANanh@Q4jL?ZS=u~1B+pz52rL^cH9WYFHPijs~4&bl^<!CinXF'
    '>w}m2vN--G`y^kN7rP&;>DBJn$QN17Rh;vk9Zuf$;Nm>`aS)#rxHEpID~mMWk*DO85ji3Kj6RN>D9}QAS@^F%IM}Kf%dk3CI4}'
    'VyGKjj(DSpSs$%x8?)Qsfjpb~S2k3+<wwj)>c<1==zOtPO3)B16h&7#k{JE2@mURP7P)v&JXV~42UgLrlR2+o19-'
    'TIeQxGSd+pc1qJ4@)@hB=g3<IXVdBH~tq!j}6g1^qjRB5ECXGVB$T0e=S@402{cbl<&VxcqN9P#ubcKMwRwiTXIsEkvz<!fxvW'
    '7B1n5twg8tkbv+`LbG!fUGJ|IGym3r6zjFeO|RX_8h(xBZgCsQD$Uk|?RpdRZMW2&!tH3XG}_DQ?*wNm8!k-'
    '>dgLHu2D+2^6elPggul89K2pWxh$wai<Ny|lK^~d6Gq23BrYRYVv9C2a_T@1PSC|g5h5ZbZoaNUsh9Ig9?0!8q;3PMsP@tX<4t'
    '7#V&4lhT+>Ua28ce5IicZU%j?z^oO;JP;T)pEEB1gJ9kVTu{Ri#7oKR>KUdNG%75vGQnyOn%DGV-^}p-mI2-'
    'v$lc)7)Nph_(14t6>*EjPW0#aekJ^!NUOCw%o=@+V-'
    'aHgRi+#GA4qgV%6as5B%mGzRMnb2tzJld~a6UA&$Er>YU}%<;0n)OzgMywG7flh?CJd+0*dQvUYo?ITM^QysV7R4YEy>QOGC%_'
    '1q6xzG#z{N4g}jKM%$F`}96@?Zt%S*!|lbate!%{|oiu!F26{r?O#+&p^|f<Pb+aDQHes%@qZGcu>|tYE%~~CC8EU6&oL10b0G'
    'I+v2;Fe+kfL%)SIN)Vs|krRDGGY%FYWN*8IwupLUY)!9kFNp@kaW7n!U69Scx4>0uZw*})4G`gmG)78on6ZniHTsHxPsCz1I4w'
    'aU}jdZ-7<<6n0xncy_=+GmT)z&wcJ-K?&`!2vsN<pM_iEz8_Ukf)!1sp_bq3GKQobuViGH)~jQjF0&Y-VW9iV~R1vhA-'
    '!ylZHlC~IAxc=i{El|Ka~TP7rzJXa?Vaf{LLG3#}aFUBYs$csuOJ2Xn}{VSW-&Lg(ydGWtXr_k%-'
    '(U`42IO9B=#o#<0L>Cv@?e9R(tL>N@@;K#=UZZ9Zu|M6c<40g`58n$CvS^Uko8TfWwGZ7@KGFsF-'
    '+N1~i!u93YsJbEySU_4V99Nj>cMbGU_NW^q^OuOd`iLPDq7#HS#Y~Ivvzbid(LUfc1{}T+2xE@BLMCe8u-'
    'Y4aB7P_Ve7BSWJcJ9Nd&es4^3SaYn{bRnry;Aq7!%JPL;D#uDhi`zWa*ebfqh>f#>&1L`uCCz7N)m+a#X?UJ{Db&vg~AFNKK@^'
    '#FYN`qB!uW=bJ88_(9N&2+~JI#?kbOXVl!asqBC3uV4xFyitdyeX|raMyr{TJ#FzHUcL9gF<l)Y?N8%S4H{bu&`ElvI^|QGTL;'
    'B-ebRxIY9l4SfhxiW<xi{O<RUx(@KKD?1U4E-'
    'oNReYdGAn<(`sNf@+N6B^Mw>HGs_6tdi02<DF+)uOf|>+7hq`)R8WIGc+$wN@rj&kEj6qWP&XK)=+@%r7&fmD(lV$4t~=+!Kez'
    '&eC7!R@jcf6a|JCneg*4Yu=R^%yO5UqZ=F^G<6l;I$^wPvCB9g8MsL<>b6AD~g8Gj6^zE*fBz!kO!7OJv3*04i45KC7Z`w9m7d'
    '49$VG=-!r$Jf{G?^y`dn0w%z?QG@4Vrj?4n4x+zu+1_Z#lhl$Hx|!piUIg@#rvCaB)VO8iHbJk|ZD@-'
    'L2Q#92WuZI=H2n@C50F716tT6BUL0;^6jK*EVwevf|B);{2EcejPONVCnm>;$w<ylO7>0Byv1W>~NbqQmpDXsTlf+XEnBSjT03'
    'BC0gc+_r1WC9Uom!;4ekFpT(pH#g(8$W~q5!Y;nn%bLcBjew(TiYOY=r7~fn?DlGW+AR1S0^oPXHA|&_uelY(lsHSJmG~t{ZVd'
    'jjvMU9@=9<nnMffq|D^DpZOA~g}T(~K<nFbY!(cGQCi-nMVS5;KrJ&frEdRm5M(G)h+uzo%>j9wVzj`&VZ18P<1km*-'
    'C&W?H0(2WL9AyO#c&ua#M3LMi{jeEzSf@9mqmI_C^_m=?6aQYR$zGOPCN@+UQI;_X@Ow@g0<{YmF66-}s@-'
    'u+b+3DR}27dhrkSi#{J5?ROeu?i|v1hZ7xQS}?!Br6+-'
    'm1^FTs?DU{<biQSznQG)dtOJCoAzVQ&_Z`?i4zWjP(V?^E1UbjWETl@lFS0M@o!iOb1qN5YhM)-Ic~==Jq(092csi;-'
    'QRRSDh6=yIo$%57w_qFU;_Md5f93D;uxPV$XzUuK3Y?-t{*O4_hIml(+eZNW{!Pgj%gK&V?$Vsi%O94)v*-'
    'y3pR*JZ^h<55ASUPagT7M0lifI4Zy))v`6)!ufa2Fos1;rpA@1urE<Uu=Uf*o^OGnGL$B~S@TW@JAyE^~s;)YR5r8SJzUxCHh='
    '$uoRvEG2W^guiO}81oYNM7rbGvX7awHcJe=5GS*^5K=cPA7Rb(%~Vg=*R`$!B<?%2Y=urgwE6w=zL;_8pTnjvZr>HpXE?=V{vc'
    '3#wl2mD~~lQAXJtuL^o$Oo4J%h<UZFrEwdhs!dlgp&}-f+QEsgCAL>Mug+IMA1-Ms^jxz-'
    'o$*JQ7L4x4lgjb!`%jPRDT|eHzau2ZArK%+1{1m!$e<>dm%Z#3ytNaU_83%cb{qFb?H*EzEvU*kvW<1M>G;iAnD<%gVWCg}cn|'
    '4^_?f<@p6mR$)lHH`poMv@2mIc-'
    'z#$z~!0r*%Tk(q~hw9)Vw4ZwU87k;6w_7qOLc#i3>{XLV?RHVHq`Aa)7UVuQl6a+!F~Z_5m3{}gcPzUzLX5vP`IDN15fH+8((7'
    '1n@~;LYiJO%!^|FFAF`zERN23Gs*Zv19DPH5X^@ZTi>ZW1pxh|9J;=_9jA9_Td{)|pp5bCEJr4VLzG=R=D&UL6SC(wiNRa9fZx'
    'Ynvv>2p^7?vP@+q!CgMH@Y{t^>$#WcO45uH1H=i-rw+It%Al+BaXVn$q6q~-'
    'u7M+VSu60r{Ee+Po_%HnWDEZH%~iF*J=CdRS_lGzC|@}h?D~^1mQhx(e~A<927ahA`n8akT%^SGxlopm=RK8H>xcvAw*o#9S!4'
    'L6;+7;wyC@7Tq7&LBwI({0RehZvqR;98*xD^_&}CY3Q4Q6S#1GKMC!vBSu1Q&elK=K$2~JLs<;86-'
    'ZP6>)|dr$fl;m}VIJz&JB}0K-'
    'UFg8sq$<dZ%NGrZm#p>d2WzFmF$vX;zWtD9(eB`e3lnL>VWbSU#^o3SjQU+#N77~aA_7PpSO1DS;$a^?cX?VBSaFthfzGgt)7Y'
    '?)crTTj)wvC3cOf*7j;GZ@d@$Q@B&(c*O|wxH1>yddkAzQ&}S?ZqG6}=4GsSvpXbL|Pd_u$q)zQVQ@^zUB(a|bjpXy+gzgR>@<'
    't~da5YKNP!M8(J8P}n5ghcUUUaku(R|Z7XSr_Tz;~NWauaSQZAjToH3=Qx+4reBuAKZg1=~u@mD?qG0E8%K;5m3`i5V&N*^smT'
    'vXwg#z{ttnzZ8a>3}$GPoeK0HwBE^jF0kLBEf9!hG3}K(V~PkkC^Z>?x!F7`7N%>{r>18d1w=;pN{i?x6TMIC!m`BXJ!Ps#PfI'
    'kiKX20@lzG4pL2CI*Id@L0s>yY2+X4}zIPiKh8ke2m3VMWJl`kQxFr^hevLI~aUgGW@rx5$ToDY!WX<Ou82ezwW-'
    'u+%$aVFSG+a1OXNlYk-nE)#DN2o5Qsq4YcNiQz*Ad3@q3>GAz{l`HsOzNxpDc;?35CBZ7)TAGt=CIu((Y#!+^3u#Vm@9S_{x)S'
    '@;Zu-F#_CapU3sEe#35&lD$`<+LhX+FdAA~$Rpo^dAUB|z4tB?-D5qZlW<H+qeSrTup}aU$K9ZC>zGFbVyc}I>T6^;No=R1Dt-'
    '!NqK^$l;&Z2<_)(|0Jl1Q?E$TYhJ<0&s6(_wBhet*=nkU-'
    'N}|K?>&x}$MLvwYwDmykjNId9z^Jh|}Pf~_djDbWK+da21E5MnQ!pj@iFtA5#2sv8Ux=PvNc`JEc`ey9!@WhX09#6?9!8?Ovz%'
    '3i83ZB%-'
    'Vl(P0ES!loHq_T5Q3zxQbfKYLX3@wp?y=5WyX~WDYF^M=Ps56O`wFnDd6W+IXqGZu0l34{`E>qqLwh}Nrr3u}CX$HwHve%Yhv+'
    '4!dkq2_586HE$1&Tj|u7Kwu9mZw9w;D?6d0^qR=Fp=##!0%6D_aaDh^on;^4?tyiYNbwJzuTA3H8ex8xRWKP)nVORz$6bCr-'
    '1B*fh>*mFZxfi$dM?+3t}LV+!d^OJkP%#+P9^z|UHS{L3FUFZFp~$r}Ks`>xbxK_t1*^od@8#O<&W!tM#DV{NVD8n*3!Dtu-'
    '@#@>}_@dI3ldU4;gRs!JpCJKO%ij5qQ_RdMNC<YsiGSl2ZA>mV4OZ1gn@*F7+!_pPB1e^fUc3P|Uh;$_OD-'
    '`2yj90dG&}Ew;P}K4aA*qw(x8><2n`ZqNHh-fByBmF<fzNlg6okkv>m-'
    'LA%tDaAN1`QMt9N_M?0AY0PXo#WN$4u}5_S0)r4gerxLrTY_{d%egvHnRGrDEBA92F<3Fvpf5*+d8aq4yIl!l?`)vIcv-'
    'd2<@tpSd(A|$fj;KCZdkO)VRxb<Wy?*++~Z96vS)h=`2D7A@Z1HopvosFO0zWx^z)p{Gr{fKD%4EOY=W*v{7rLmEyX=Qc&WWK}'
    'jtZ@5*Zah!^>GwUiAGi<3A6-b+NC0}#-'
    'N3s+%pU9FN6b@_pLYDtLV7{EM1I^IW3K$2Ij=#^${JwrR0GQjm9h|QS!(}e%(@@qfsSvjVkAz`Q8?>K0uMpX@+d|8`_1NhWba>'
    'rD{PI3+5h4>Lbr=LC9xzE-'
    'o)41U3bJ%XV~EM^(E3B`Zm@#`CWzeR))oaFqtX!QF9CkBjz=u&zKW~bq7Jx1jYe6NN8}%CIcsOGQSa<%Vh*pCP}ycjn{2TV5}N'
    'Pz>y{sm#Sf3YlWo8z~Ew%k~BuVTMXZ$(cFShNYpI;Y1yLmXb}f|F%&g^?Z<UKOo|4SXjIyZyRIYIZH_<&(SI0b-'
    '{1(>w(+Ish;cU7Kfw9aUK*S=qT9~FspO&Z;bv_VgtaQW-i)j#gDNOZ@W|O2{l>4Ap5iA8L~k-'
    'oEB#J(u;zhv9%G7CH(Wx0LlXvEqOwmDu>3>b@xj~HJdA+UY=_Y*rs~aWVGvzTLx|4JTrw=w5L`aI!R<xCaqKTDo=jB}F=0h;A2'
    'H%HUBmaz9H<dCkB2pW>lF`{OohJ6CNst^5^`iPRgyLuXzRH^+Go(ik?lOx*cUkr2k!^|GkhpH?t+0ix(Pza6d#aBh^H|#^)?Ga'
    'YHKpa@;vl&j1;2)W?1cnknN-fkHLNI^>}gB7Ne7_r~-'
    'Cw|FXa=N@i;_6f}ojF;eWX1#)dWvfFhkjjA;79epJ>1fJ_GZ4%cdx#5Ppd#J<)<#D-'
    '_L=Ih20@B4pb|5`@Dv0Ku9;RUuh*g%%(GL}k+9(-'
    'LJ{nUqRAmJ=H_paqo+&h|BbktZlg7Bd6@NsXaG({!j!{2)&l3$Gbo_vfALV}n1CJoJ8$NUTMZpf|v4g7a9f@9U#})m@QabHmhl'
    'zHj>iG-?l}@D&O5`OQn^Hq}bVZJr!0Xg~iO0Fn_@@?8=TJqx!0)=J6-'
    '|w%4(oo9BKf5aSdB%Wy3+mBh*j@KK78V4B(i|H_;V}6m7v`Du!_9@)ub&lVG!jD@0v4v7IS6Ksu>@tIoSAe-=Y1|GyPKIvrC-'
    'EQ7b+oB8&;R>XBKccW^5f?e-_6dDx1kAH%d$h++0u<T{!^w<m|Bv#!tB!p~Kxux-Fi5`jVDKw2rm$zdJ9X(_ln)*c8(`V;@0u5'
    '~3oFilcQSMuwLGZd@bVSKDr<41uYBPiqY%#w^1FH8YlTV9U{VbDIKFkOXcFXr{eJ}*1=@lt(VM>OYcpF|l0-'
    '>9J`hU)4LB5S3ZUc@Je&;gB4KGl(^4RzhhQy^I}5__IVrINz8SafT${u%?-jUmRb5#;OExduE-'
    '9<L2>6dNb{WEdPwl=n?w{iTW+^gAU+uMg4HHO4J-wVOWpe`L%;cEc6eoU{e{h|0WtEH)0<{dZZul*4_-66Vs-'
    'VE@DSe3k*V7B+yWP7am9NA^wZq-88E=%85XM-^0i8&;a?Bs1+}nz{ZRjrJ1YTz&mba_R+ey%$p3ELnr9mV8;(IPn-qd5MO@=L#'
    'Sv3|rm^LkyB|wYnxGzBD0fIWNY3C(yJwM0R{ET>2du9=%h2;E3|#(HU#o#lgV3s2Rg9nHE7&#lb*<NhV6Fx3+7s@|oDd3}fA(s'
    'SXi&!=x+BD+CwwfP77^S@Uvj^w<(gSuF)r9QBcqgV7ngth;<`iR|HHt+vmuHzUxijs}eaBh(o@)1AFbR4km6Ugab9YF0qAog4|'
    '+_HGc>dprOJAnj+@_?>YWJS+OL1aBIlZk5~_uGx3ODPL=2xZ_OdGmos}qPHw$AE^i)@~Ry+>}HJ@sZx*Nr^0=kkt%PG@pvymiB'
    '^t=09pg7B+0nUhY@_4z`So}qmO_3?+0Tzk}Gew!y)GOHaaQql)L6l34g`P0wG|eWG@H)TPM=))=2uyXI#7fOyT|f;f${wMF*)e'
    '%rJQ9rO+>w3Xeb5*nmG!Ycc5%wQ*1u*rESP#ChN@<ao@l-'
    '_d8Bqs%QR4O&<@b)|5^x2Cb%78vDT5X_nlG*d1o05~&){$=&)3%Y@^o~^KWMcw@POp*Tf^g1qrf2*6VxW{O>o8qDg?9cLRRZ~t'
    'tri1;kc%VXL$t>6JA0m=l<Ednx@3|GkVO<;#`|v}fZoEyVsVv39Ccd;@c77$wwY@_WKIzNC%|U1$MvLd;$$HY9Xf7(J%y$@83%'
    'jr#Boe7KHg#(yc+Xg7OJOy5kab*8PPOebmAx)Z?0J`8gJR1ZWdKy9+;8ax6^0c8ZkIFg!c4oAM7ZYed40{OCcZ+k3hZS`br1tP'
    'LdgKx^Z8NBH%3GnkXuG#>&v{ThMcpy-8JyrJx4N&`}s*=F=zF$k8PVZ$Vx(Cn*X!<U01oXNWsF17Q-'
    '^Ru*u6+f4awwE7{NrGo%*EKKTgTh_A$qNpMYVY^mUr(0}wDdAZh=D1o3OI6u=pf-'
    'e~bIlXXSs}u5VApuSFQnKeJA{E~=?Q~Z!#EcH{506XR!HhZ`kHpei65$BLk@G=B)UV#FT`_^Ib|7u9W#?MkoxN@_zjv($b4B4)'
    'osPAF*XBZY<6s~$08Xh|{Lkdd&Mru(|8RPS#@b?_sQ~7=dpsXQXxG|FN+J20gQqUYM)qBsyc{*w=pu!kpqQ%=Mu)Akcb*?tykD'
    '*N6xasw#9<3|c`w6D(y7=pw8)?XCw(^&8-zxN*o-E--zj1}LpK`Pez4I1Fu}VZ))b9yw*iA=f&Rg9)-'
    '&asv3rJghRM&*%Ijk9e~9p%29sm3EQn-5uFx~yG7@0ga)@%p{v%+ww1P-m96#hXy~Q+bhHDa9%3z|dJ1&Vt`-'
    '5@)^#~t`|DRdZ$ye?F-'
    'DIaxb)_~fUC0R!`;{4#tsq&P#dLs9@5U7(+?_}>^!s_ARF+FkV2*n46H(V=S3?|uIE#+JP?DD8H_S9ZAd}L>KA!9(DVTtH5(WD'
    'I&eMqL^Go2P3_3G$sEJQTUCf-'
    'o)iB`Bjddhf_gTP^$pYgvj2Q<%7ivvl+k|?&r7H$DgBJvyn7@aOVtteJ(xp@rIbPqyx*K}Qiu;}ng+f_DlXbY-'
    '^R0DVyG3WCCWELH#iPh>Gqqo*xO&xbsh!Z6Q}7*=Tmj{)q}G7DV&4ZDu<|DLzuHa+5dQv9QwcwPoNCoDYy!3c|MOk(R%K7qh2u'
    'n77f@h@$gT_>4b!?y>c0SzP2G%%BW;&f;6sTaOjA)j93!&mUEK<FCI_PV9_hp4%eIyX)WGn9K~PgM$xPQb?$3!MpYb9%K*<KUw'
    '>S(H>7SM_<<o`CC)McQZue>n_CQ`K)vlR$^qRZIo%v@_><ZGmXM4sp-vGk7GAli-CN*>exkuD>rPBHlYueFM5WKm9(HkO(9G3R'
    'h$zO1<@zCiCV{VWvCSg|jwZgcUyoWt^x{<j%z*ot?bj$II1@7udp)rWJ-'
    'H0Yaj#aWbd>uPSe>deY3<b>YHlGw>1M5$+gq+lUiyo>Ez34UdEi=E6gU{K*;_B+<Vv1}a>^vi?^rd|QX#2-'
    'fQ23ZJpP@AyOwq==19iV37g^k$(}8kB8&JuNYXjcaxDJ5&EGHCRz?QlIMD58|w*-'
    'MxmekJI*P^2Yl}EY5)a6|qdiJx%*UfI;rd51azF70nQt3Y|shTF$!NBLq8xL2@O}|$On=F0(6)#k8C}QvyvY^38-'
    'XZ#wdQqpVrFLmjNspv|sVGL`D61x#t&bTp8%V2wX3}+!D|J=l2iBjI4&p{lLAK>F(;jQ;8du_E`yAgZ;hYZ<%h#i)1)?JeHA)>'
    '_Y@j^ID?HdCI)*s3W?0m{7vA9LU;r|xC=kz*t&#-'
    '9_s_U{4gSusy$T7Gb|1(wiX}!xQy7afdT~=2xb{I5XE;0!If?VB27XiMm};iaav}^ezRXa49cX~??Q?B<fai@CO|LlN<~s=>I_'
    'YE`r-'
    '6rM%0tUL%WnJGv6&Mw1H`1a{NhW!e(5XAfwEEub&()MOPy}kr4>+0wjaX0X}w%7O6COnyir*36ZhVS(G~qYzZDGC_?uwI1Cw+*'
    'DEQ8!E7*!Mys9LuCc5}pm{Yk)TuUF-9j`Vs_r%LUP1YuuLz&upQMTHavQ7`q(RbDt<m-'
    'd#_He0E0*sKv)Pdi5)f#Ru+uDS8ca_b~0FYkdYe}fRW?Ql+zHW<Xnk+LzOt8jHMmAFMVf@cd_XuG<t0Jx#+9wN8A->|-'
    ')A1Jh{;vZ~c`3oq;^ne{Gi&W<$pI*Hb`D|y4(pYVP-'
    '>wAR3b5d9=M8};WiuNPg79(DaTW?ZR%;9N8K0$(#!ovKx62j`phrbSx<W?U0Rg+_4dae^FR(#mIo7!H9x?Sfc}^$%0{Zl8ZUuU'
    '9zv0=Vs(rmt1K95Z092|vEi+scvED6PC9?<QejJh(WsL|3Nag$X|>hU?ChF;^C1+T5)Wezl5seO=?S&2DN3`!6m}>E5zWLcC-F'
    'X;6A762y!L@&!WX!fxavb;7XWVwd1OZ~=nEkG8J3^=<pRTAR&#%M=GN{xOB-'
    'q`$t=g@AJhank)<|$#@ZmCWG4<ARZ>PigIccdak&mPS1`=E$i!PMVC5T|cgWV8Z1~U*0*G1?`%H!0)g)5p5$?Ja)aV1U+jI8JA'
    'pHyO_pn+71Hq!gfKE@7-'
    'pPU3n`K43^0tP<W>OJimpLOs+iIO%>P%i_!|m}%q7~eo;&1@I|K2NrKX0}f)F%<25+FAI90yaMlKI{Oznkv%gO+H5VF!Rv$)$e'
    '&V9CaZz7LXb+L|F1bWwwoK(h;-'
    'WOwJ1gj#Kx<f;wUjjaXA;Cydd1cL%Vx|DwF7@%W&EUMneB`ex!nYxJl<maD^sw4O*A*%K);=SR5#@Gitw+wJ)#}{4})(d8Yod2'
    'nobgl~h9WT7WOfk|R7ugTbNcb}D#AbsV7-EQ{j`B!OL&{4)J6koG(fE5H7Pq_E>?<9-'
    'R6JcU?nQ#eV6%rVH+Bc|FyiG5>lS1GPQxAwM`_|!MhC+*8`N)p@jiJ&>WF*QqOF<+Oiu3WzQuGcO*3YX`jHor$>xZ+9gNskBs)'
    '>Qb^no=c(ESo1;eYVS+mhKo)4KhZ!00LF`ox?-'
    'VR97wkqrf9iD8EK{FG5&oz3DeEFoO`~7Mlp@;d~|8G7tT)9Jh9v9EaPXHrXz+LxbvRy|vuO=IAFGgFH04QrWyj-WjH_GubAL}a'
    '5wHrDi!T)aNVh8?3oAJ%|D#jl`biXC0dzAK!oq@9IsU)`>UDxAc^o!a7SLRS{Aw2d+>XhH_9vatT86MCMSabTypi_6Wwf}#8Ln'
    '&#Gx1eWpU~h8SAZs@WNZdx_cl3PD>|lHUP>Fnl5&(yn+9u{swsbFoL602s4|;>^xn57I)7x5<yAR&OsB{Z7iFtZKQfc@Ad%79W'
    'y+xaDr4z^8DW0jPBVBTCSAtKy=7zXRle@b+JxueCb)w_o0pX}%^4E%lLUFJJKP=+E&FCuO9>YFzPc0Xf=)#KW@^%^i+8p19EVE'
    'v1L&hhd_5(NoTINkuy2Ys&rBg^wrXLQKdgZKEz&6UQ;vX7nhO9JqXa~b=M8nw_-'
    'Y2jin1OSGWYMzvawTA+BQ%3GPK|_r7L^3(^@Yj{FiX(XXv@?94q3yAIdCnCL73uK^ku))YfeX`rkg7wb25Bs84s5n;?7~PZ?&{'
    'iV1c~el>uI@ITvU+9jA+xxaO5a{J5{rDWMX`!`|as4@7w6NDn4ZIMNV|6MFYpjdTEKF|qsa5%*uX!7df2$pAg#Gfab$nNdbGza'
    'vM78_N1lkCwZa?_^p?9xAUfSEm&}0n8RH=?K@769O>lm5_di-$t<|Q$Cta3AlXq=pW<o6qH(qf&-'
    'n50127aDM9?jE1dN`)S0;5HMP)I4}>u-'
    '4=%O(YYmP6{03Nh!Wo+(p1g!&Jb(b5^TUt?1yi9(+V*zQGsNneX`=&!70`?(m1RE<RljweN~CI&4}M_gK7S+5idm5e2O-'
    '7Ot1lOQfc+J!Se;t++v?+CTh1OW?0H^xM!c3kIV9&8BDhaPU`F9^n0jy@Hg}LrWL1+N1%yMZdTfh3#w0d48S#?SoPk*zN+TqwO'
    ';=}N;D)LEof{gtV!WFp0kIjh@f8J9T;anWNmTT_Kv_Q#Z?MZP=xs^iyKC=&mx8gU`ORpiB8XWv=6*f?S`2zVuQ!HPkVLBbh#)D'
    'ips8rJh)uVd#U2N=-)-'
    'kRv2Km#Cv#(pZv~0)N&^~=RD``k$u=?!ajRN$_Qr~y%Y^~Y?{EW1d5DCBbP4YF^GKmGnK((0U#qe?#o|WQVT=)cu~N*>8Y|EJY'
    '5B5%801YaXS(R}D_pbn9EwYVyZ@@uJXyTBEMAd6*WxT<yajEd?bxU+Y(rv_r(0NbM!BcPUA?@nc-hPM1qXGCG-wBlDf9VpOi3K'
    '!+vpyUM#Ric0`gU73$@vRe9D3|b@kJ`{C@2gb~a;g5gsSL)Q0{w9BK`$C%jR52Hivlvol5=S!oPPC}y&f6BK(^yIaH6=7420xv'
    'oSiP=`izu!(WRkd7%|%1IR9VA}Z{)79fRUv953@eNUm`PJG@xsB1<W1v`Mq;as$e$t6ET>Ug}7B5Y9Q9C%k*E1eZ72TQgz%oVw'
    'qOW0~r-s>&CkS)flgOxWsvC>!RYK!n&<0XN3_8Q(c|GY(3#Sy@G$i<n13rs%+j-'
    'l?PAT=E#n2NW>s4Nx=*Da3dx5hNIY(Ec=)i^cHLipf`mMk^KPc55gfZ_g1@+4C`~hu4@$Iz$J--c=Z^}{ard!cf>^a<#wUWd4E'
    '}DDA+yg_f=GxGOK$Ph<j%hxJAx$)(OV`BY3GY-T-'
    '1Rm{0^d6s{>WjQBFyRRctShDDG+UU1V;y@5T?K4THn)}>Y}yjlWqUf{uA2y=(8cIY-'
    'l;<#vL3NV={Ej>2NP+z&?xi{BxAX3kPf0HKV_-'
    'i~{U*F{}Ukh;;VlF3goGf3OwMt6><UOVRpl&S~IPiAL{J6%mRDTog)n<tQc`7Ow(oR{Sh%k*FzXv%QHjmU>|vwnxt*FjrG#G%e'
    '<JZA<IZyiR>1jSI<urva~`0`hq3=<|EnYbnkAM6;%&GWq6=zJ3(Hs(y%U<7pNv7$J;$X!72U`&fh=H684*3U(-0&p-'
    '%FogSHO*~>h_zREIvJ53Vb*JwvOIFDQ7r;rTcItR`z2P51?@fwi$%kxD5LF;eBa{J$8P3$zU>WL13?jL+jLGk4OnsMiO38MkxE'
    '9FT1jai1iVX9W9!sm--iz^vAp|gb07~fR6_6%!?UEWNL8H{nHh$2Enh}^}e`_T=u=j#`tFyaHG*`F0Wy!94u{#*{X(vZMQu4y7'
    'ZPaUyxH5OS(le!P${RJ`feOGjlMZ6zvuyIT4L-+_dxNJewu#kUSV=vEks`$6eJLL_+B-'
    'i#+>bOH5_j9JF#x{Umw6K|>QcBCy&gAp&yOq952q+sR1V^P<6Hff5oOUG(jgnQ~2)9sKBaRk_C<_vPI}KIuO+qWHXF(+lmF80y'
    'kl6_4BkaL+aNw|ZB}YXZT+nJ#Jort9vy85l!Cc(wK$R~;oqgr0uX^b~KL+ld+~P`-'
    'O?(%jg{flXHkP`nY|T$FF3*JnyvcKAP6Ndp+X<$fLALcuz)roB4{>mIF0a*RPeI=;Nk=5%%xTd&<Oyc%Q_+PxAAkFwbzmip5S0'
    'l>Atz6j&z#~V0BimOf-CR$pd3HpbVwi9;DKIQm6R#W*co^#CcAfn8t#+Hv{(*0%P;Mg9bCv#-'
    'l2WG!g?Qy*7Ks0J3zYBV2_j|D(^UR+&M`1PonqDA1Eh@mkMr2`)segpuzCQ{=Qu}qDhx5&LZI<{SayN?_q05t~eIjm^lh-nrQL'
    'R#g=nE%q_HX{!+8ye|^v~GACU&$v&UH1}I2Z3)cePCq1+q;H70d%U{|F-'
    '_VH}fhfHS7L{P~xS>!k1Br34EWE5V;#MG3#p5s#8L^dP8Q^P@(Qn`2e}eT4V%=?Eu3FjzQK~rAz6Dx>#IliByXsM~gEVM=qsHF'
    'R@illtdmMzRf&}IX=#D+s%&{%a_p}SGwh%h((0LEFta48_e9<ijQG;48!5!j}V^FVEF^%`qq&$Jx&>~kbl(sNWDOTmi>lli^%5'
    '?aptNk0Qw;|uFM9n$kS9X3}a~q{fgHnsGIkVT+;S?DSHr++39}DrdnipL-eP4~~lcJ_}&2br$!yiq9+ju1wTX>@-'
    'FREGzh{sDNevC@cQhfQp`C54!wPWo&kzQ+ebFXKzXf3@g%eQ81nx-{0eHyn#-'
    '!}dm!C+t?DRfIunK;&@ckLm$(f!Rt{J{23r`MqsROOLG>mNTjY8XUUC>dB&tD*Rhx&NdoL~t@);aJPVzs4ulx{*2qd%hS#H97A'
    '&%CzY%ly-vVU%SRWvx5~I{>gyQhw!?+5EWAvZg&jTr#`d<?VbxK^fX#%nk&?4CC6i2ei;lDmnjM4ZR8CS4?$gn-4)B^a0(zN#-'
    '@?)4-'
    'A~fkDTVv*z`m`e%|=pHH!uUz<Z4<tMw;cr@=i&Sfq3Z(b#D*<>kiH!pU{nAegM+8UrjaJ6@}Igj9f$<%W=~T%V3au7{&6Ma5(Z'
    'fif1GXo<+|jJUn7soFFgE(Jk$5)Dc{<4!pEdY~Z4hcHZa5pd9xg><`o^OW`(mI0;_?rnIw;GegkM?E%P_2x@y600C8BaQqij5q'
    'Twf~qS7b6@k`MweL6OLOQzJc|BYOy15x`u~*Gp$+Tcr(IYOZyAx$79G)HctM*RmOjQ%q>xP<B4CjhJ+4|AEhjl%!T%fTxBc8>='
    'kg&LHmm+p>lEzc8wvmW3vG2UGjvXZV;8178~36G^Z>4$&!h*023)`it_ZF`g5zeU<`eDGrT>^jH51?`&5Ed?MA_cUB~qZZDX^~'
    'sqIsf_teDcc_;#`$fUm68Vl7mdSfD<}+J-KJrWU;;;tHqlajZ+R*&6)#Vob<zDs(!<g2$<SmI67P-'
    '2Ye}UC|+o)ol^^(@e1jsKCs6OQre29ko>W#0V|go7W!`EmkSuL#%OQH?nKfaGOQ2R^+vty_)G@pzu~2w<_;jPX0gaapVQR$jZ;'
    'U!S$lE$J)hLwS085rCCk^7yZInvh=7yUgGQ6x3K%9NDK%v5ChcGMEF3=a@5B&XLGFDDZuSNP*HB#Jc24nvHn>;JT8kU$Fj{BJ>'
    'k>iE=5N)D%0tL{sAgTLN2W_N&~;$eRxkQKv$5yYYY5D;E6+jE+Go~Bo02f%R#+oSIxk_gUDrxosm>Qj4u3ognBvb%E^y>$(rpg'
    'nG8n5f<HEK11GrbqWUPTc=CU`gZJO6veC%bUhk&+sbg9yBF|Gj#z@lDqXt&j2!l4zI!8@gKyrL5)PInQiyyV|uab@@yvtx*hO$'
    '(zKhR@Czh7B*9tm~eT-'
    'Q$!AOD3y_$#bo36vEh_pJZ?V|h&Ovo`qdgc1?drFrVRmP!C8t%47jT#3a&4;Fv+Fi2Q%9`(+IYi9sYp;v|R8YXaG+hfzHKOfvf'
    'anFGXe}#zn(-L8-x3hJ2Fy;5xyW&f(26Bz{PV0d#&Q0x0Ffb$Snr+GAxjo|-'
    'R<U>MAYj%+?|~H8Qj11cs?36#1GNhpw4)$T#INf}s{utoQI<nh9~MZ=2b_bi*^#KFpn-'
    '6{mKSf@I_;+j5@2_Rv4=4Z1;l4wagFeBCP^XcMc&qoaxL}B6ST8`wC{NmP$A$4#P+b!Jyf+X!j~4E3F2us8RWkQj=A7-'
    '{u^GrGe;j5vQWk}9m|vf#hwr^F0b3`nVo7JjDj1yy@+nq_B}AiPYz4I1)ZmhZ~ARJaMv#~f~Bhti8}u|I}sPsOgBg6R<R@IOSu'
    '-Sg{0$j?JFjDiE%8sRN79%t0uB&`K_0Kc{T8KZ<0*Vh$`8~7RhufvD*BEgrBjB3s9-oeCJ$AwRvy%T_Z4-RcftSCwQE^-~C-'
    'D?u+X_Mr8e7Iny*}h=dVYOWXL+JRwXa0rg=zA|0FVS;oD3S5b=|?9@zu3dkA3({O`oa{IRJgQ*mXF7pvK>g!TxI3*wQM7cLcp<'
    'CsRk1!|-H4zeBVB<<{x=w#I@jIW%(Vu07@}V0F2W=|h92*RF{A=3CNbOi^j%{yRzn|M-'
    'nIB`eN{3)SEYh*i1w*I@9)c!8D)RGC5&U`7fkN<F&(@|dHZJ#$XnBd~!iImFht*OOIPz5N-d-'
    '17uH`0ZG0aXSW|hO&<$D(ZV|RbN-ryiHVg3QXx3Z2VWy0R5)f2wWz+T5Mn3cP@q$DOez2h<OiCE44bw;5xK=p9}M0&X~B&L_?h'
    '?}~a^3Q%`tkE@)nPZI4q)qtB$U@_1+KE1Ow|)V6_j*ss{{sEn4l?n>MHj8Z<?EFl8UGR%ZHpa;lN)fJ1dy;nwM;IOZS?6L{X#N'
    '6F_I8)V&e>WB8Rn}0!HsKFjkX?<AiC%)DU=rZl&+YN2s^e#|6ElE*cMFlmuJp%3S0cakw7yogT9xLQ$}^rr0Wl;pB}0aj5ySo<'
    '?M3pSKKdz@r4Df)_Fom}~gyka&kNzU#p~Pa?ZhFJ;#dG@Y?}OoFGQO>nXXf0m_$F19EGW}E+EpjY1@%{Gj$tpyuJuNvBnno`1T'
    'NxU~wa!c8f#py|&8KrS~&>1u?GX9hkUG-Ow>M&9!y`BB?-'
    'PT?=v}#GPj3P__&oNNW=)A)ZN59D;+rzRAq=`WYmip=GPfvZl&CN6YkZ>s7XdoFy;Fs5;y!Zo!J*?<SSZSlht<dieAZ~xp_h`#'
    '(^xV~V=9jsuUp0G|4k#?FYfYX_6{QQpXxuTxi~q9!uu!MjuRkPl?X2C*2$DGQ_^;asLC>#w2{P4|7>~r^hc0mrw>V-'
    '8Q2LmBBo#vQ%l~W3G`j9N&1}(6ND~}8gao|<;vQX@%V5=*2AI+_OsOc9){3aHQsDsnsq&{hO*XHnu>~TmxoS1pR+@;-'
    '+zzfxP}rFP#G*9pjNXgq@I43{=;sib*9iyZF&AsF8UH8&RtJHRf<;jN(GT-'
    '<yn*7z4AKHH=)&**I+MdS+q1ch1=*qQvRxfzJgKv|J1b$00!i^S2n=}qQX|yFb$0tCY&AZVY`~XS5(|$u=!(rWl+z$U0^nd6wp'
    'qo7Nnto#|2P^(a)j{Jj4*LV*$%Az_b|zWVYW;*c^E=N@%220JED4xFiVtqAExw5$ur1aX5wLt+P+?7JCKa9H0~@w$$+;!O~KiI'
    'lTc-(%;NZ&lt_%k#FwMtu|j^iA#_;2bz(#@zjqFV$|^f>KP=z6WyNlus=p!pKt4(VC-dTSsHixh7Z)D<@$~nyr0%lOZ?Sy*H;j'
    '6;e)3B<R{*pckL@)qwZ56Uf|FRs6Ek0UWq2-FbW$e!hm*GeRTi5A654n9;;#1E<>)=`=V?53b-'
    'HxHNyIt%QgbtB6~xv;QB^Xq)Uoui5nJ0V95H-o5|**A^xT=U1`K<WpBmcvsu*!|O0T-'
    'JgN$M%D?CDsF7b1J$)DBrW%Nq>{ftccfNX9R)i%~)7eTSRh8)$;7&A$aCw$-'
    'kCiT&k0Rs5TEqN|3WIQL70sjyhRpsrnHe`y4@)o=Go!~GznAg$RJ(C9}nLPJ=6%~_seR$}P0l#B2;DFPNm7wA=)t}_;qsQIy68'
    'WmVjqPf_y1R&r*}IopK`~^}`6Tk?n5!CYhwXJiXt;a4c}d4(xcaW-eoK_r`A5NMm{Ykw`OMyd{bycy;L5-'
    'Ib{H8ZZ10r?3}yh&qK+d8B=z%aebMWb8ZxjCrhMaK?#?_r)Fnj5kYcE{^{54tT*sCzhB{!DzHXGG79tJn-Ir>4oOC}0Bz>6+Z('
    'w87bl`(DKXK$$`{cqp>xAR%)b*D^{7;67ck?KML+MLg-}$K_j)K}!@QtU@#F>g*P3vp~F4-'
    '30yl5Nm<a=Bv8`yn?TV|+_^eo2E+a5B2^!j^({I5E1c@jStV7Q+;<sbJn+ata=3z!E!{C-'
    'qxhEK*jQdvWmmtO^wd*++#NP>VdlnQ4+U_{AOWw`mUcdT3q(mx9n3w(?>&<5}v@lE(DxpSzu*iB#@i_@EQ+@r0+5S=x#L$0XMm'
    '7Wk137y}`rpR)gSl~~mm<EVW;Snd&T9ca=m`$wme`e0q*yvBesNnb~r=Vw#=cD_Tzzx23?aE-'
    '2{_#Nb7tGAHEre^WrzAqbDqJ+MJUiX-Cy+LDHHqPG)9hNHFc1zbC2WgH3yxwvZ0^Om)FJvO39rfdH`_}2ygD0LaqXrgxCt-'
    '1hqOY~!z!f9J?=0+lMfeV9<=&l@u&&+azm1S3@#fW2KcDwl^~*Md|TIAGVc0CKN?4S=8e9u3#bHo?b6>|!43vdp+n_{^X#*`a4'
    'A@enbp!@@FVx0dDHoY=+n^+P?_(THb|9lP(7fH`T$nvFPL;U99f3<F#ab2`u^+|I0pO#=>86V4Yo1`BH!(aV~QN{GJH@c?x|fK'
    'or+H<g)at_dQqZ(6QAxp{PFS1`>Uwh{H}?sZz*n=1U4xx9%m^lLf`Q2^atS9J~I2+J%~qkb?`F+m+E&v?T7@5ZfSzP;cRbdmp$'
    '=HOYmj@clUvU!y-'
    'WyW_XwrgbH&F(pu|K{mr41PV}l_Z2#b^7ve}_g&NvUse?SI;e)yV{w2CbcuPnn!lt*rEnKGd%69WFx?_Z2Qa8}4ofTghqGy+Q&'
    'YbY;%G#qy9F6H$<d}jq)OU_yg_PliO=N8udx7rG71eAh+@e(t?6r)&bpK&FR-&y`VIVN~l|@Kt%#!~pFtiU`Dbdo{5UHgvZ-'
    '7b#R-XFq+KnE?9-{oYco-e0Rk@5DOTbJ!d&*fTh3HqKcZI>!iSf68a`w*zZbyQcYqo9lcsq`Wl&xCca+m(-'
    'QsAE4Cn1}15ImXiH<5=b*^og}cG-6U#k-I8i^Q#DU5KUowAhp?7Rn%&sn-mmTly4$W~l*z^l5986?*+^IM&Wpc5!*WRDDe?Pdc'
    '8IPL&zl6R^`q@)7wnSRGvsfOc^r*z+m%%nJwlD2p^*d!hNREh8tDc6fF!7wvq8;RB0%x_B&BUMZe%&tVy^m`VGPaBinpfz273A'
    'L-F}kIxIMaMbCOYc@mbs*~-'
    'Kl}!?5jr)}C1RpdVy?mn$FLR50_F9H3K{^88q)J(<7h#l@L1Bzk@vDIyvcJKk`07|9V`~QA47yqw&&6l9X!k_Yl}oy0qkJPKz+'
    '5L49DpPHiqSdLY!Gov2^E(PrENd<Ajkp%_qT9{Ng>1v$JhJ@@!Y7-'
    '9Wzd1wb!Qvtl!$e8lD$kgkp^#x*f~_#)Qep&e~{AFso%LA4;iKUKy>nH&lo+MX@FS0mX-J+imyleYbF211WD1t;GC4--y<o9K+'
    '^`foYzd=xsr`;@CWb@i^EF3zy$5ZO+IU5j@JIQw?R<%WZZ}Qcekoy}%Ns)1+Wx2<8*7USHkhc{4L3a3pY)Qjdlzvy#}^y0;XwS'
    'lrcY=R8mDa>&mnOXYNF9_SAGKoU6G_?MccLQ+$kRhX{Hj|2`}P(=V`&D;szqS}w$ix(3SmF9X+B8<k(_{34{)V@7+G6Rehxj(x'
    '10*Ac%`AIb?Qp$M&=jEB7$J67P-'
    '^FIqm5tv%eTgC_3Rb}tWWl&$p$W@SZtQaN1|7xh82C;Tz5I2icIy+M)F33Oq=50h2Q7D$A$cOMEXCnn!j7$oudjZ2A*8+KK^GM'
    'LXMhAa;0ndZKAM~^gmIQFGrai`s~)gVqd9J<U>5k~vd+cAZjtj6k7NVS4?p|Jus3tHNhu9|&<E;W--huJx{t8r?6XU-vAhY58z'
    'OfrW*3a|XcB0O;O$OM^p<q2FfdXwDM&j^%qL>ddGg|F3yOR$p$_qD(gk(-'
    'LH>YV?(%I4A4L^bd*k=TT8l8F#8#sUd3M}o#Ps(F{KjP-rfdrkLEqow<(f`pkQYp#F6ak}h8f-'
    'j{Ge+=TFEuBs6;Zol_CTQmqWKrK#>i|;9-'
    'KX;qcmN6=fAkT(_BUJ_V5u{v{G9oW^#Q$Vi`yg{BE#^jT%M(DtG_cwu*@9dg6g6>QiIALs|1d&;}(;Ubo}(27&npE2hgI{`=Ma'
    'N3}*Dv`*o0}9#^$Ao-'
    'A5)_Vfs0f1m%OG0UZg%P<&%h!n0Qdh)qo?H(UD+>h8(r`|7wVHjNCe&i8Lzu47Rq2P7Pi_AUMe>VNKX|@xTO_zD5*#GB2W%Oeh'
    '1qP{6<j{Jwm==o!GWD$TgvHE8b5H`bu`pVc=8^SDQp5yQ92;t+V<Mh;eAZzC|I{<R2H+&RpbZpJ`>&6R$1MYbt*W#BJZXzRN1?'
    'Xm$?vB%R`Lux@@lPB#R$6=O-'
    '$QR7){)6m$*N2ZIMbsL|tOyU^oK$L@I=*8mjyGCS{^wl*m_k!M{QMYx+NXhN9TkRc1KAFvc7Oi)!<jyr4QtLyzh@+2g$0DB#AH'
    '?17VE9w7k^Rp1Uj1fWR-oH)QGu`BTzvzRR0Ly^CAB6G4{zdkRhIF%1y(4AEJi%0rSAm16m%W8H|z(G8-?f7DR%|@gi|et?D-'
    'RXQm0gV5~A!W%{_Rcv2AJ3^QJqR9>4R}lSiLzTRl8L{f>5wJSi%l+P@1LTZ1<Kxt^!+fkU~z8=VM~VG+U<n>&=idJ+Q1@{c&tY'
    'GQF$XZ_h=(!px1jP#~-`zA!PB-kJs3jU{|h4Yhz#<<eezyMW?*Ow~W!;-(FAwnmaMh*Sc!_d}J`MV2Xs-'
    '5zICIP8!WObG>>SNZVR@co58E}kZo}q@MWxFBwI~igDMa0{E0nVu(hs_R>O+jeKQ=xD6Tzm8f0+aV|C_*N-'
    '^);QoQEg^?P8X}s)nX=U8YTl~GuN5t2R&=udjJt><y@CEe2$2H2tmUyz_{!b7;EV0FRLOPYHIOK=)8fxeEGx#y3FI}c8E~nypQ'
    'Y9ejx)R1yM=r+^8Y_<U+L+%3l*em<%yJnuijNn@6-r-'
    'k+Y)g}@jV$lY3H7Ontfu5B}%?3Z+IrGWr~X@$9;M{MS3eSoBn7o=C8)P4$6OP}#6_RSs-7w}kcVo`WZ%CH%-'
    '+XBd7{)`F%glTBp*NqX-A2E1T`MnqPY5_rg1xOo6w3offc9$v5!G@#1%Hu*YQt1(t$sb_<<MPF{dMlKN`_Y8oDj7?I6-'
    '@{X)~Xn`VZLnt8OK`tK$}dGJ{+xtMZ=nHk-(NcM}6y<3qweJmR7t-'
    ';IjYGj+ud|2F=~}n}GR2J$8OvC9SCvTgXVjL)5iNGqf#;VL;Q@z`TUIe{w{FCPYY!<-8VNxFUFS-xef`7^YxQ52~vxj){87_{`'
    'O2a4HuC9gm({m|F=wp^QW4qJK?86Q#z2b^!*ax$=6RVGtADtO~Rz=3l77Kk&ho)3ClM=R$$KsB*b+P|#?vrA^jN(DjRY60k5$4'
    'NY#|9q0BIQ;!{qy8MGJEfVkXAH?f-Nor#t!~Z`Fi4~jx(wi)sk4PL%(jmiB)uj}^3`bW^@Fdh6`O(UTO?59R-'
    'QYpp>$qfa`>KKKZ=`*@@2e!vVGWuDz58IsAq!s~2_p$+p<UZx#3<p`O^gv>Zy0}gl8_}P_cFnZ&7k|vvl)FSTs&}RI-'
    'hsa*EIj{8qUCMh(MEtw^oPp!E_>BB)!G4j3u;XR0*N$0i3QafW|2o2DJn+z~knJ*UkRsC=K0Be%pE+J^hv-6i*me-JB1l59JW}'
    'dYa)lx$NZ6+dDD#SHiD`=X8r8*ZT%+?dR3nqp_bdL{R+NN9KZ25PEQCyDK64eej>_UKio&<G5{4=&@>=F(5Nh)5QNb|L(KIZ}V'
    'sQX6Pe}yFFt552`-'
    '%ndeM}JZfV`KED><yJ9EXl|t4zg}qA|)@?3LY;^W&{69a|g0v6lf8m@NQ&m8Mo#SgLey90LP?=}a`NSF879<ag;sUTVVkcF-'
    '*I1zBLld5Z1vQAsjE)@md<EHKBR<{re|gG#zpq&m5tjz?skWWM404GK!AAI|w}-'
    'mI(2V{V`O*pz6^qSj`AWv1z68$ez=J%{2N2HGa=qu7La7)Zh|HH4{%yzqAi446N4{lqXg<!wzxoPczjG5BXVU{B+F4E60O9K_u'
    'E|XXv*Yubw&&a<Yfk$L8nSz;fe7~QHXas5`;H2qz^jp>>Et2SiC|0f4Yqilz1=V9A$$5??Wk~I{yi73L&<#PE<1=E!_DBH{>Lr'
    'Zt0<^Scwa{Tk^$-SR!Ie>Wz4sxB!q~P%^?3LCfpc<VuE9bA#~-;-'
    'E^~B{H;hR)r*i(eVDU4$T_o}HTFssz6&`3=E58j16n*feSuwV2@ZIrEfM~?fgOf>X^EeqpXbd~{te%-'
    'exiGK5|ZU(T(An!XU){rXa^@(S}!<MZxml7T*Pecpun6jJ_|(w{BrRG)v4{SoP3b+7YaL}o99dW5TJ)$ex8OfR)4|2THoz0dJm'
    'gnJL}4aLb1(&&*lTup{Zh7uVe}T$J-sH<e7(pe!h|--'
    'aG0^eYTyoR_24=Y7R3@QjhNDj~L%?NvX^$A|dZN2v!9dv>iIYboe01$_X}6&_+%@z*Q~RPK%FSZUUR6%sH68t1a%Lp;k&H(N$?'
    'R`4_Oz-9oE$bec4k)RbRXP$tT!INY?-nb@`g8=Hk8D-'
    '_}ro|gMq&QD+(H2~G@A!q3n{xq5n20Wd(8?MU03^!L*5n>@*gU?&TEoR-'
    '$3wvC@449#&xPxD~mDtXW^^QC53f^e~Q(UQx5^qWp1>ZaFITB}{8b3<+&-'
    'aV^*rhZu&i^dJ6^6|BF%NwuB+DvBcW{%59!Mg~c?lZ;U(pVLXktKgpg{A&7pJedmt>-'
    'm8kAo9W0x_o>k}mfoQdXyZt;Ahp?Kh3_Y*DMG-+j>;j|4voRf2vRpf6H!GMi9p9P6u!9{}PZb_VGL^{i^-'
    'Kleom%0wNy!Qy{YQNk??Kvy4ko1_J!I9^n9<e!AJxh&SH`rR-'
    '$9(4=%I^7rZpr$oOncVa{eM|e>Wl@+S2BkgkJuaSa8LHTy~O(i%8n5ND~%xCt|jq1p)$tzACbj7t8WMG?E*trR3lU?4vmMZDsi'
    '1Fdx|<u)TR~)VS88S0liM?ykG8tZOL6gUOd0|OF<guQ)e8xm!OFTgJ7BV=Q0B=?Ebwc4-lz2<F#-'
    'oQZC3!XXDxfE*3&Ws)p(BYk?tjW%_FkizvH$N(xuU1>f-cdHCp3-8?Yy7wgC1atO@^>ohdP<i4P(5sF`2l;Y}<Y${Bs;)-'
    '9mdx5+!&QPxUnC#jZNngw00#j?h105+^H8<(E0=MUCYIsb4x3K;!pjR+Nk0bWUw0HyME!JRbvpq2kAl71{&P%D}!Or5Yhv+h8w'
    '3_M*t*(xCdkl*oui=zr{cb4TF=YaH0=ePDDXCyMf6~77+JfekQj@cGF5S_dH)+EGfEqQ;8+3Pq)7VUqHE+$onmU9*bZyFRzA>|'
    'Q3xFsI#uFH~t`}Ti(fucL+u;FAft;O!zvcC=r;zL>u+0N%@#DxKm0E>mToZIO?sRwP6d4eM?Yz~2imjV_F6V7i+xL@-'
    'YK<A4;vcTrz1Ac~qC`;eg|2r)xaf!ES3xT0U|)yy9wcG?C62w;-}&oJ;CVGf-'
    '$9J$Qlc#WtH~p7&QjM>u}R1lTX&rQ{!(L9k@<eP0i-qL%B4zjn(so!o;Z@dcrRl+L+oz;4GCzdM~XN{HQ%e-'
    'eWJ*JibE<1JM`xa7)9~$<bIok(8IWQwY5s|Rm)U%E|gIMtQbN!p<69YYt}UNevS0?CpQkA%nzz^ZoG^;lLV3Zw2(K{JqC_~xUZ'
    'aPL0A55mHijQL7Y3SWjY=q)FzC$lKx)~d&DC5V$KrGke|eR9;7XokSrDEnxyKO9SeyWQZ~l5<t|Qw_g(h7TP6TfS*mF<m_<l;G'
    '#Gfs;3InlS4e}#&9Jx7RbLvX`dxZvAp1{<+cC!Zk~>yHii@0haRNhy$CjFnojA(*KildVoGrvzayr`B*>jq)q^BaOGsCfCY!IY'
    '+HK8PQStiSHyAEF3kPKP%yg-x;Upq3M4-d*-'
    '?JWi!lm2Bccqwd{puUAJc_ftBstD47hzHMf<e+)V=l>f=LrrL58!a^uS@r^KL`Z(GC@#Uq<;k4FPZ7;H0x0E&m-'
    '}X`4i^~QD`?tEZ!1am;ow}GYEq%162$P{=fp;k)Dyg*M_y-dt(@Epd%QGPfkX??j`$Djaow;6ZLwefKt{q_y}GVW@C=ycbcX^2'
    'M-o5jXl+oeJ1kM}{f&WFW^@B6{)i~r4?DT^7U^$-lW)S;JOAggEY5ma^BVh8*~bbLb+-'
    'DuOw9MTmvs%lPZhs1rT9&|O8TG@uP%|Z;bNMJu-'
    '}z!*JEA_qxOco7#GIT<Oz{01!M$T35f*mpU?)83vAfm^_t2kvpJ~0W6IiNc&<3f)HMM0aUt4;x@srfOm2YRAO%kXiQbyjlgmrn'
    '{4Y96@2>itr}puHG(0KY5nz0p(cCAO8JB2EKE{+Qg6?UVg3fc%at|!V6;bP9Cd1&DSA0k7Ka=}$O0Mglg&Pw?h?2k10E{MeZ&g'
    'S3a%oDhBMOB5pc6d$Ek1-V&yu;F_F+-#4-F;@>~l<`*NYaZIlfVo(oes0B?@EiPD?dEQ-'
    'B14%WvTc(*4rUNDTq=w{i4BIn>#F2m`_(dQK^2gK*uhR#sPhL)i)j9e(>X<e}VOsWCJ~u2W&8)*I6H_wuP6dr?4_03xE@-'
    '{Bj#=*Xavm#Z)GzaXA8LFVi9KpYwRk^khP!yqNcB`bOAW9YpyW`YsK=<&LuE@7jW2mQG<&9e!9sjJn~PGbuvLUYm7&f``;s0>f'
    ';o`{dIM2^9nu`6!N-=|!XR|W>V=4ptpqmN^^t=bR*-'
    'I)W6nFNN4X;X%5E<u}geG``oOaB9TWUS=4!!_UjJne7ZBvY#^gLj}zUH20WVd*O}niI~)r`n-sC{C88D2!L-'
    'gS);>;7G6x6yG%)V2y1Ys?JtbF64}m^rx+=&lj)mKu7QjUK}VNs7o6CJr4&n-PC-'
    '|zz1Yx=zThY^C0uk|7Vvj`xb^Jj{Ag^B$|19YMki5gNc>sV=^?OQ(NB0+zvlask`H9ivI%9VmSm~d1NqJ@VVa6unFr!^S{Pej>'
    'j_J7B~q7scyhduh-Siqm$q;IKh{{-v>=}kVdAK+3%+cHq7Crv$K3-'
    'Fe)j$n+;!^+KZ3&YqnR<R#bJ6)lCxHz%xZ6z%AoUf@K^AX2M_imUgRk+^jogkICs?8cvyDAve6ravZ1&!$Dw;2n9fj?@W&}Lui'
    '``>O+FqjFB&SRAz;uHYF^1)16YPEq$3+P1STm%Iu_g?0`yH1g{J_=XH^K3E`dSXzYM4UEREN<)P@BZ9=k{##i}h1!)_FYcDE=#'
    'Xo#SX7@;FPaCdH%CIns)VrBvZwHXI>4ZBbB2MhUo|5zFVja@NZ)tGaQYo{5<z;nh%Ny_Kp0y!Qv`Qjlqf{AM|7~?PHDV?i^IBH'
    'VyUA3%^@m?$s{I^`Q3W=X?i7b1j@jLE7q4(cd6%q0(umk<!WgnyJrOlQ7o$IYts@dJXyyo+kB#h)pM;KUyRy}{TEeK|es7c1zx'
    'TOGHDd=um3YZ4Jy{CpG!C2mMRN)dRi#Dv`OU9zj{Cy0ZP@cEoIM+~fCN7yNoA~OnKaq*Vtc`exMNp!XCQSg#MG?6nZyMs7sV=('
    '-vjpITOMt|`g9cdTbdm@{=!;XKfYjM1hET|_}fBo_F*5*$<HHl^ZO}e+UYZM8yvo;Pp(>p$(*$QIv<mG^MLLh`W@LpCwdT%2J5'
    '^vMWrYj<nLPto-'
    '!Yd#5X9<VfPLX%>sdlKMtLzD>hKo{!(X^hMScwbyF8qT7p+Bd3ZiSG<csNu^ur91iBOnodI%~8FDk2Pnkb{x-'
    'bd)74#pBayU$Pn*#<=8%O~`-'
    'C;_Y2~~fdmFkbM5G+>&`B|@4G>=_4(jc($l^v3n0<iOZ9bS!my7})3*G}d5m|rJbj>htzIP|OQD^UKuaHE@*2QorLxc6W3{)Mn'
    'J6P;=Y-aP}7aT?q(bxH4rXI>1)Doxc1j92^-'
    '6OG+F_5D;pu04)6VeMknBd@Au&KSgV3>t${)Vg~q9RG&{d4|9qKN6ST_Pa|fsoLF-'
    '&~ukNvB8{wGC*bH@d$HHhNqeiH^eS>w+9|$yGl2*95{3TZGU-f*F$>R<q|R&hk|;;F|4ZvXoJ;@;-'
    '3$+>{r)JWW91HUFOQ2wR9L)4d0DWt{lK_BQ27IJJZZ}+c6a&!%f}d*@r%HOdBv}PxoyP4HhB%=lsgeSMN%Gxvn}x0FO}RIS2;h'
    'SmDU9i84tL2BM@EU+eE(uO9yh38YM&9(2p5#Y{e5fS77X1^@^^3F)Tb54DGZO#D}Xv>{RBla@Cb+$G~ulj}3v5mKV~vLZy70OQ'
    'Me2>GoIP`>!@5@V6M0$gX7(H-'
    '|x{ocaa?Y<IJ@&sP*P9#RVY$K%ERX5W${!~~|LOxf*jyd5C+N)BA;;($m&%oBa3dWjvH#4AD@@?A!sK9xs0K9Ug7Tu!Q`VUhpE'
    'I&hLY~;i(1qz`xT`@BS4S`E+mP(ar$@So@HWH8*SIegQw!+FKYcb{7pR43eu@=`H%m?{t@M4b$AZ(Y0R^$&VTAy50(shM=wqmi'
    'QHSrEhHhF0QW*LzILP;LY4eFI}BZ(}Vwom0R0wq(ZITazt^u@op%qrYu=?HPAMBFKqD;1YQw#bCg<`nNJW9*agj9mz>dPHt@>Y'
    '|}u<K3mrpJG_LLely0PUPt>TXROx`nxw&H>y%#KK66z3S)%e3!K8`UoEf=fsaNujN?Nk05N6@8Pc5!>U!p?g$)RiaMLz7vBAsH'
    '=lJ%cTaA)`)_I^Xy2h#B&9kfw{eA<;KDSHjx|}wTR5f~Su!j%6%twi&arJ?+gu!zjM(D+a+Ss<X1q~a={#P1SEDI7B=mVHaL4e'
    'aswL<XHQ!?siRQh62QfP>o%wzIUpZ}3XTG=uHMvF7rm@z#wxQvUW7nlDwnF+*-5kgvQC(l5L@i(k4)HRh-tgds$&GPO+@|WukC'
    'F4i#GsudkEwoFs$P87C&xq9#xG4nlyec}6i&sTV3s{#ru^M*EZj@gl8~K{cgm3EPt?ln0Sx+K6!?|!-'
    'iD{*p$D3x)ogt?`Okto?CKye`A)_M^Up__jbpUt<KadY@##AC|YV_h5gRh}Q!-'
    '{!k;K>+@fTqoQ{s4i+4xbL`RgL%=TlVW*Cc9vIv(!P~dbKq+M^Uc4#0F3mv_`!5!eM8@b2_fB_&CuZX#VK__ARN_f1B;lc7ne;'
    '7ILJT4CQW(yMt00HdxP&jj81kyv=2B{qv6Vp-'
    ';bzOPf}Je4V*Ww30N`k_Za)S9%VBTjL4A0v1tDDR)V;wL<wJ;ZFr_v+7ekvL8;|z;h0#1IUg)mSK-'
    '`LufuiwN04`Ud%?wu|Vuw@x{^EjO9P{=m+1dStKVnQGHx<C~ZOym8KBscRl1be8pRtjZzC}pAU3dSK=j_J~or3t^mb>%QX84$<'
    'r_6#5gI))N?-Bot747O~ABI<nXgU3|qaf_1riQ4}Z@+a1MZTGbtFaiis56V?F9BIO9ItFYv&-'
    'lQF(g1mqioo~O4!?gN?%Z|<%;#KQ}QhAeDQYOF7xXtMg5WAIXxhGX1dJP+#*y3c!CNQJV8!ceFe;#p38d4bt|FbRAF_G<zY8&n'
    'ScD49cgItcswq%HSZ{R<yloLkuJ&Ff>2Z#<cH4nn-hPaCI}nvX_}r|P>p(Xc{x60Hs9Msl_w5teVE<dTti!FH}pP*!eTtECv*3'
    'i`E@mlMu8Qlw+&9{?-8FY}M;5wUq{wuY|4CKGcicX)Gk{rpPjaqLZ1TYf#YfrnvDyTtX9X}3@Ab}G+-'
    '*EY_5_eX2$%t2RiFd`}XKEj!9r=$z2b#+p*#^DC|X3m|rv2V9M^uDC3zrT#78}ExkwLH@E(}!)H@m-'
    '`n4KV7yA%b^=9c#bqgrI7ZkC&P|@<O9t*n4IK`(Cp^>7~wLji-'
    'gbUMWlvJQ^wi;TF#gjIRvqHyL+KO@_i-@TvS4OP@p@MgIKS*l!<Bz8`*Uqnm-'
    'peRn8$wJLICyC$%WPtZ~lM-76)6hEbn8Q_3?mrR;k4N`+vCAW4<5S^ENX|LZ<H53qTA};Dbd!ly64!{x@pV1y%6ThUK)AWDY-L'
    'Po7(hWdGC{gVMY|r6B!K&D3Pmd|YVg3q*8Y4Q|eoDTNfBxqho*_T1BJ=}u+$!Uo5I0QWAEfCrZ1l1P6mg*OCQbKYV(v7QKT9Dt'
    '9YGdi!PoaR_?C-N3%R2xrDUb?eh5xn&jUz|PA;PiC53H7YUl(9G-'
    'F4Q;@W_5jUfJkjGrfg3Yd&P8Q7e82%%Z3e`oIN;%JFGwtDpzJj7vDwp98s(nc@feIoO4kw;bstiLfSbsdZJWwxy9dj7PA$;GO0'
    'eT^NQ5RJyHkcS}yC#FS==$ya?5TpfE0T$AuoB@4cT=frC%3ck6%UyTa(Fm3aLcGyTrFSc17;g5p9}OakErtQ$&w~@QABfqohR$'
    'a#H}}QW!%_ZthHx8v9keQhDi9y{nz~rxTRD2*JT@0lZ4MfYwbi;$HZkL!f6&?oufzxc9Pv;Zyc0&Hc%`+{UrYo@fu`Grt{a^M>'
    '=ZTi9>cCHfuCAr6%S~md3RC<YMOi_?X0D-WrRc!UaqeG(=ouXL2-!Ab&q1C_>UHcXSP-jIow*k6AYhME1MhrTrg?}XA`#b%2vh'
    '0I;&B_(WFKjEN@GIghZEKD+e-XCR-4Qi*bcW7-'
    '59{c3HO8_l!7iO~~nbtc_Mk<*o9KYr%`J5$pGphy$5^VN6<(Pr&GYXi*XyxP{U{DeB4NFe>LG1biIbP1*|z2bqgz<2U1j_0MmF'
    'Md<_#`N4v@U{cbUipYB?&al#=8j0pU8i1!8&US~7C`(-'
    'xClK&5wl!a4AUzl}1fG)(1T1uyuA_$|(ani7Vwbj57Wxb~kM<fOOJPJ!^`%=>zy32;AL#XMfId)`Bwc%6hR0927GpsQ_T;qYF%'
    'oQS*Ew}z65#fWUt#|EHE;k8WC~1533k^$I_(d^UZ)H>s+D8SROJj=XTJAbOcJ>t^Kq$Ro#xaTF{4sswUX8-'
    'v=N5pb&7PJ@d)VU$Dbr!eym;f5y&zgW2fgJZbAt-McMIQ{wrNYM{b&M{LQ8b&Imb3Hc4Z2>(MBW|1#%kjrabJ<TyMsP<`cCwYJ'
    'EsGukn=6yyFmW4vP45+uHg6Ll242SrMI>WO^{JiMe`zE^+#x_q^g8vNXahkYKL&pv_;k;1ZiWyO;<4UblJQ}abfKnK`mU&r6Sx'
    '7>Yt2SOwlC{AYYM)C<3SPas)%~9m($*W8Qba+Wts`4Xqc7p5D?nfE?VBVMUYa|d2L~WX~c!Ekzxy>_7nYLav0_}T2IBAORb5AP'
    'pK7{RL{h!H(6loGT^$4M6`OOps+D^_kz*_E2eM!lQXH_pVzXQNN)OXogry|=kkw3Q}>!WwEM9TsSB-'
    'D&wChI~L%wWnMXuW?QaEJC8=h+$$@<juy)p<MmNMAM*u7e#FpTV(K;6&+I2VC=GMk@@}SBseJJ~-'
    'lr55!@QRuq0x>@r(6s6cn}GVDC5>~D>iz^KyBs3nO^hnoXR#)fSC4_|q*`J8A#hM-_GEN(zAr=zS!wxCuqZeixW9~Z}N4m+Hy0'
    'e`Ki#r{4<N#NIFfdTYNk{v;GS4BZ1g~ilWP#2NPP*Zp*-g&nR_yd`w4L!%L>=i$@iH5R!0N-'
    'a_<?Zs<Lw}fmX%(JzjcuA!%flDx*Ye3Iy&NBKW(ho`<fc$_x#0*mNE<jKy)sV1Wv<luXAPO3EetpOcYDpZAyPN7Gp}7wa1?KEt'
    'PMblh)Lqd#D7(O+P;d=z2yJzj&r!J`^FRsx#F?xC(Ue$k_rZQVsHwHVf1AIjiD%^bxcGJCg?z0N-'
    '%t9VB`+}yq=ks*Dpb}_h$XwK6Ay3@!f_?w+>e7E~DH-MI~(GQ)nc=<0<84`^+K>n&%5~A(T)nx7cYH;&;|mCCFzC?gIb%xbnu|'
    'bgq<NOCtP|Sf`Sh>XLZ{LD^;`x^2`7$lWB&MEH{CW3t8YK)abAqflyFLc7TmbVYA4ZhJh0s8I}G+E-'
    'UaUJ35&i;jQS#Ay6$+Lt7FyW9yRzF+F9j_p!C!kShZCDh!NZ6h>diBb8*K#4Y7={PDE8O#>XJMN50R3sGkJW4;}@%~I(_S)AmT'
    'oPnLYfYw?;q$sX%1?KT9d?P^NLPHH`HKZ@g+jzn3|B%9d*ps9OPr0rFGSG#uMlOUEc*m0kw^;x&1H)+$l1RABJ5#oboV*S(9n>'
    'E&x=t{`JzG@0#8<;UuS*R`+J}EG`lEZXZUI@G?fYSYkrUeV#{cjQoFu-Sa@w&*hhA#hl{5K*sm_e0J$@4!3XZbjd|RY%IeBY-'
    'G@S~bx9o)C0Fk9;&{3mASY`-'
    '^;amVaW_&%Z^u@_*DsjB1_{Kr%n05Yx7XDcn2>Xia=ErNjX`5elM?k6kQOKm$JCeQf}p;bW#Bwy3xKISfPm24<)9N~Zpcj<IR;'
    'YLs7Q<vY+8}ho1Rs3MbAn+^l^7v^f}xS1XzF93S!}SzB|36e@Yt)s46W@VL~gbgtcNP#{FM^?Nkla!e`TMBP>SGV-'
    '&KcDOF^VUrPe%N0)8D|L@F;E}d<%tbsa;YtH_Z<}caxl+6zN$HUl%8A@yP&%NB3E?$?zIv-'
    'a9Zx?z0@J0WLW9Q*D&A1WGNo`w4^*ed;dO)S=o?ATS0UIKVsufKyfVxwP)Ga}+&@PO)ctOS3cYir3i3nrXMI~O3zHcpC`~HQ=2'
    'gUtvc}7m@&D?BJKiT26aZp`}n58X|D@&s%E?%p8^l$ONupZRTfM$QFfV2esHDL34o4n)2&(JvUZon)sR_J%~b-'
    'nln^w}1?mp<T6`RAj#6%d+YLjJ*TBVj6E=}%7*172wfHlhmvAKh{>6t+A2R!~-'
    'y=d`8YT}NjnUc!|B#vEq7Fg{QPNlX!<3S(00Oy}o{>q`ic@V0~-W{V)6-'
    'Fc*D#>&0KePximFWb=Y!K7mo4O)1bSKW@spd4q{eXYsz8+OId8kGb5d7<$YNH&ms%lEhn+}n)rdjvzuISB5p{s+WSlO$-'
    'G1o&c;aP)ZL?K0p<0$?Fvie!4>2DehPU)~GrJpkDi#KnD(^TiODroyCNV)vgimo<(-wB18KHCeQ-Yw)Cz`xwQV#VEm-'
    'y7AncUH2SFg=*=Bj+hxqDV^p#tMLjtlq>{P!da>pouTYgoYZj5CL{?pZ&C485Uz^%m$zL3DOrF;0Kd6m#Ya#h0E3yEe8Q5ynSv'
    'yelyCq|ibOWqRRg&mp8WDHHq8rCp95X_PjO{lbOrTdEVv69|8-bEmW6D_cU$(?#W-'
    'M=)nQWh{;f~y<+4EHze1t}LZKZ<+1l)<EJw9}C~cpRzIFZ>_{;;l>>D9FPa?{FiS;>t5u^`vXdwZT=O-'
    'UKR9mD8(io8d20x1EC9(9x*}~_unhdT;<lgM|=ss~9V8<@Y-lsL-ORAWO2O!JRoZUv-uB~LaklKanR~V{9A5M0tiB3Bd96Ne~E'
    'eT`7ID~Yx@^j$=NI}U@9+fv^>PGVf{l9MfB&_|RRl?k07d`|d362tGv$))~+q5w?#)Z8E*L$bTefTIqhPI!it4QpJDJ=hI;Uqi'
    'AD_dkC)o`lReF`wwgcyRRO-O12m(J3EYly9WZ-d%ZOzXb%{!C$AscM4Q;e6PvQFq;=bpt^coMicxZ0JPR{h*Mp>HpY!U5~p-'
    ';F#ogFw_a;Lzb@(R+ll6dI3cz>N5F+;8gMEn0?yHVJ>e&m+WMMID-'
    'C@h0t{O2_%5tM&W>j)uW8HcctmqvU<ZM=TnoVC5_az=VF^9n7P^jiIjl8n3t~c&xz&W_8YN`G#CR=b2~OdIPv4NX)!BYMHKm6y'
    '7tP(O%wRCtc&Y`NsDVs)GD8-'
    '+f%8GuTGKOu6y@GiwDro7q>qN&S^}?5(&v_S@p$)6Afyr+H^Q3MI>m_(7B}!FnkEG*AW#DtV|Q{u=_2Wf|k(GxW{z>e=4G8DtX'
    '8RJ=*vzB^M1jC%nC6p&kP3cb*&bHUd$SSpp2q9)4Gy_wSn|8pd&RA}UE$M>k|{@@qYDE<SC6s~avJHysw5;9Pj4!=<ets5)8Jv'
    '<2i%8nY2VA@QCNl7+ko+RT$R&*Kl;Vcy!8IV+o{Lnwj^24+p74;2QGj;!S5ME}XhPMys{pIixuh|yL2d1B9;9bP-ht&SIvp>#X'
    '^uG_+5!7i5iEBLIB@AMhhl{EYZAg1`Lsy4OkYcb*97*4r!NmYKNT44V3>FQuq?T+sO50F&ffp%WtFKP1ZA@y`&>3(eT--FODY8'
    'EAeVoOWUuXg{ebCeY`Z0yrU`Me1zzXF>5RNA<kzLnQ~k~Ha?PfmVe*`h=l&nV~R+|s1|-)Lp2+v(&WZ5VDR0IO7%%-+-'
    '$_tnV0n{fdQeA{_~ILqKh-'
    'E;>&UJ2PA3dLQ!|G5A1Y6LMY4&YwCyG4L&%n7&mzNs8D7|<JlYPD^xRSU~!fD%z0$#T!w?t|1iK|4*)Ob>!PH1k%fhq*(3S({&'
    'cDjDXx=}$<ku1eH+7dCN_ae3e3pnMiRRA7L~AlZ3vhN1A!(1{9F?2wlJ$rqGr2e9@$nXO{cfib{aTLQ)keH9GQcGshSw!^L6c6'
    '_%2BmCZvyj^)ve55c+>WV1Dx%S`UCneRbcc%J-YNGdfyiwvsxvIrUnwKh*fV^dG`R98%z=SZ%F=6QPdF9Tim7Y{Mznbhg-'
    '{6(mDa{kmWlUQaqzfn<Fcm*K_us*erE@TF=%#C7z4!8K`vk0>NeC&EC$#s69I!h2#9~G#Ep3KsL_4Au7{V4)=$jG(&rW}#zFwG'
    'BGGu1n_2<n$<|bo#oV;lyR;|8m)m)qJVHWUeT%<QWlIwDW6df(wd06wjseLu~3!7q-'
    'eXAA~M#Rz^5Lf7I^spi~7eP)i7vR_MAoF`2z8=@t0Gr#vBj_cjmC=}Y1!0WKF-CGXL(MoS^6;-'
    'pT2m~V9sv9O_s1cP(xIN%>mRSz5IL5}Zb=z%RrqDLLRaCl;BkSOSEop~9+d!q6m-'
    '=!F@>}t@=l)I=+ax0T>8!4>g>GGjwC)em!w(M<%`Nz*0!j@Rj)dvlUbNu{@wmrjHPaL4bBO$WiylNwD>&8u2^Ufs5L$_$}zrsp'
    '_bobXNOB+a`a=gg_OT!1=8y6frrlY$rcD0y!jjh4y|ek+rpf!u8VoiV+;uT%F;8O4+f+L@`Cl>z6Nh7nvpVXHP5>_=xoA?6e)h'
    '(T3j}L$uog*Cyl%J|EW3X48U+M<CP=u3-sB<FvJfFf_XakI!t2xcy1Ebf2!R=)F=16WSjL>^=pC0?^bWs-eRL0<@Nmun`!us2|'
    'X&vVYF&Xu?&f}_jz$XWFz9ovL-8!np&WEg>Ta%jehvAzzs~rbdukL|AN@Az=$89-'
    '~@VW_Q+X%Km7TbLVfAAKGQV9yLPsJo6AEiAiNny9YmiH(2@gO!phg=tI?R|ikAH3gClJJ9zCP)4RXqMGU4#p)H@PhT)Vl*l_Kp'
    '$#<>Si#t}jVhIrCpCN#S&Pi})|uMGXx2isW(doh90AV4HI1aYdof3UMpkiE^%4CB<0=2WWQGZ&_m!&1F)l|q5YVia6o4(<+yv|'
    'E)*@JZISIFA4}N|YsdWq`k}ZM27Q2NF?{Xi`3-'
    'UfTaFMS5twi^ynaz=~k>Le6P~g)q7IWl&gq&Lits=xMs#2OrJSBE?}&y^1&sC`Ns+{#@T*e#1Fi_2ZS7qTH-'
    '3T9BicOlllyd#_2Ctjv8^s?0KX#s<+%ZriDpjQ=q{p^QB@0v08aakZaRG5Qizpmi64I8rPv>J#e=;0EPEkH_C~D<<i|(40m`68'
    'OEohasNWg}j*uEfbX<fIIMLn$Ceb7awQmug1rza#qv>x_v$vUCR-'
    '*e2=cYVWrw2i`b;G>4i%MPtSs$Gw6AE!+qH4lNSG`d&5+R5_8QJ&|uh=0Qk!D_b@TTJ<+>5N_u!6(P&rL*g|XOQ(AQd4|_0){W'
    '0xh!@;`uW#XO+(ou71Pj?DAKs}bjXc*0xw_bhRlB$2WXz@vEzNL?w4*|+s^5z1eG=$%rm$XaYL-JZUaU?4hx+PWqHi5p;<9KID'
    'X_ML1HWiY><-ohmFFKzvY5wDFEH<?~bXeqZw$#`-'
    '<E=0`cV>sQ2|16~6EmO3^fM52eSO}4Kg1q63^q0M<<fM#`Br3iz&R>XLn=~o8|m=-'
    '3nJ1{_!(>zBB*HO9zJTAq00k18g9nTIB=D%43}3hL7-5eX{h-'
    'D%XOVsk=+i}+IBl!&gP`T{bu^cIqbF2CmBJ3N%jxh$Wz{06dO5*O_ga+qbDKt=FKX0@M!IhhfYkcETwa=vYFy{d<P1!eJx(;uC'
    'J+}(QWsFiRv2|x@*n;joAF^wF9_9NKt=eEn%Da!B^i}%C{nrvP1BI0aQFvT()AJG(U1$8qQ;$mDYDQU2!87@YRq=*Pp*uY9}iV'
    '4L4QWAWKU@bnoYI_VQ(IK^o?T45NH)LwaJc@EWH)&1)MrViuH6{+$w_w8KOOwJXy`Wiphwue1{TyYm85B*OprhSON8XTzSm4GL'
    '7xvZm*8g?Z%*pE8#_M@rt-'
    '<h0nyp9QRZR55ro?M2?66t@_Ykf@)$EWG@MyzA4E!8o<if^+g$Y9qmNNh%So5qm}NwffqoBt(FLzb)!ayq+CdAZ90X*BZ%_3-'
    'od0qlNmO6|c>1(k>aX%Nz0<6+F*@7Ru(g_uH-wh7j2FDH~u-'
    'aR6MCaYx0+MD+}kfU!(7o*$>bA0RA+Hd6mOE9IH};cz#{bpL>+{+NZu>x)atXR5=K;lhh*hF<LT085{I`T|cXQ)DT3m&DH!NfF'
    'O&eC=Nssc3rZ?Jfar>`==4&N`u;rdC?pkTAB^)@Tr^fypb4J@6|n{nt0_lVG$|9H9YOS?Eb~BJt>HVo}Pb&^T%eyB>(%q+Wk86'
    'ekByI=(yv(~%$(Cx#aBk2XqM&t5Yds1*48h1Wky&oER{62zk+uaf-;5L0pf_qCxQ6)^+-'
    'ECD9GHXwDka}|seV(Mnf(EM0+6zaPOE>UCRo)t+`-'
    'kB&tkQ~F3yMw(QS*9UMk+<9u1MNx!0k!j62^ldE`_{L0Y`>dy4byJp<=fDKQZ~KIc0a8n|3b5Hl60FF``GB>r3dofbgbajlIJ{'
    'xDzj)5H@Su<hiz92=RsN&F=ylHf{fF>pJ>gM;@SPS;WLZx`;719iU}5SU&g=ADnp2K4ch-'
    'P!*$z0Tf?}S`z?+&W~3dZtFDLT*K>m-'
    '`YCYcH>#kABPT?*yebd~#q>aIdnUE9B4*77gi<Xyb1;Zcu652+xHTu06Chsk%pLllhyy|Frn1^6_<lHFrgNX=JIF&-'
    '`lVj~GPNm)n9Ok+{JT?I<fDMge|7OJ%=BqE;24OZ_A+KWN%1!RLt32OXDFpXAsWoTmEyv>81B;|Oe27oWdsBiWNqqN>O&64WrS'
    'IGMQNAq$`x<Kvp2BSJ!AY5W_&fM1hM*Ctn~)|l0`X`^}?`TF`jQc-v@b>I9P^fGUZLwpA?P-Sw1M-Cy3S<JZSjAbk*kp-'
    'M$g0MVC=91T^MkPPsD^q!Ssm>i@@@s-'
    ';b8nf~B0EF(ad@8up!N3wXp^MVG<d_pl#6jKiSKs5GMcPmz5kfwH+{7!jWw@ad(Jmh3^nzSIbL|!N>>Btz>?Tt<pug)ufeiH9l'
    ';#bC+w#R~ZkWeLMy$trB$lX8~#KZD9h0%Mpck*Ym;!FIqq;WJgyEpQ-'
    'N!5igVmoCtHSG|nh^%izb9^Qf0bs`eK)*_+KgMo(w*mVv@Biob^@TpLxaaY7Bu*YDvAXE6DH5P+2_cz8xCA+zA$kUgUdhe4zB|'
    'B%2<zp>0guztHbLw6Vg)oQbk}b^^G8R*oV)?nccngyq=!YxOSkLPcW!o#J^2>sF9b-'
    'o0wZs#T7KOd^HDK64a!(l#gjl57gK46OLGV-vv$?_HL>a9s<Q^sFjA1LFdo5j{B%v0^y2@#+}z2?Ig&BEV^lO9_JQNQLm3#nN='
    'Q#-WfNy@UP|z@XxGxwoxyT0(2L*&z|m~{6%IJFh_iS-'
    'aq%SO8-b^u$?DbRlNDc?-tbu_50eMnEp(_Hz^bv!OL_9Ja$ZY9)0%x3_Lt@L)G9LdR-bZ%OQOqwk~d(*+DxouV=KkQ8FspS8D_'
    '<3d0(%IvNawEr0$tIl5KOYbFd_YiCmdZV{50m5niI>q$XLAC*)FM@6-'
    'H~IPoeq88FRgc<Ke%7^D5fjTD(M0U!t^xRZ`RRS$=mdbOVc2|BzQ$t?KRTs0wxsUC^0BplnUaIw62En({3cS1oDaoemZMyx06D'
    'h^z;WyzIqo!e30vgTMARnBzMXsm$tM=TLXY&6Q*ivYC>766vGMhwS{%t5HIba0c0QBf(v6!2bmIgfQ-'
    '6+u57K9ECBzbMj0N}bmW8{murGhM<aP<T~gx7RfmO2Rh+`pxqv9{z0<j0yn>^>atBe+TGEEEav-'
    ';x#d#F2dhXZ<VPbOE{#MAkN6P^&ClFzQw*>SCi4y2-M&@T6uWQ-_a9eOeKa?R5T!u%jYya8^*#;W_y0xK{8KZm=_N3wqaXhtr$'
    '(=LJ+ZKX<E3ziCs$%9J`jXL_AX)O0UAQ@8)AoJ15|et01IUe5H!GSMsF<WU-^qi#U7Kin0SCo}FYx^B{1qXEqHJS-ANjgG7rXh'
    '1ZApEX3esxC%+Y*)oijsSN=Z6=P&ImG38kVei`WR%01qjOq+%O-?JIXeA?oZ1fU5ZyX7f7L^N$qT`z}svN&~A+7=8zK-R_W+Vx'
    'i^Kp|~cQ-'
    '{qQJ1(CW#RSYf}KqXduld?`!De35fm<n@>;Urf<@QS0Jph_9>ywb1{$lrc{$m6AtkwztGVgE>;Nom$Z)jgZ<S4a9>Xpr%16Zge'
    'ua6=++8vhuCLwh9fmotkCkhw*vD>XX#Iw^nvkOkj;IOJPAPy+`p?;Be`@1R<vJHZubL}L(=kYf;6ww8KT9jOmr0PL8#`rQ2MT}'
    'F?kLfW3H%y5Rv+QzazxB(R^V&^7>Us~FNPn%360eIQ{F#Zy54XEDDkmwn}Gjr7-`sq{UQr9sN&WH)i)6;^>yBy;n17*^jv2-'
    'JK_U{yqV{xNEXRp^J*aog9AS@Nm~Wg&n9UYr|d!4PY3A&Vq)vzIv}BZo#R{)=%ok(lSA|SaM*&v;Z_2u-'
    'YO)t(j>*N<nLGozG7v7{FcY7Ng@9m%-'
    'pw!%@PPo1U*2%wPkC_hPqOpQG$sGfI4Zp=V%5|w6%J=&bK8E3N*7hcjps||0BUIwFRXFTUsl~>iZWR&XlSbIMw@wAd+hDGL9QP'
    'BQe(q?9jXVsdjRzb00rogfckAC1(@BqVK2JJ}<T#I-'
    'h<bIw(*RSoXw~I`bSdl<<S20423g5zq{$vEMag>I3HJlmp%wh$`%l{($#EFECsX1SUTnu2M*e5#8X^hyl-N{s?g!l3#z{H-'
    'U!%IvdF7bTBp0P~Xf26demD$_BBWlpSIdCe;XN0!IB1GM(-LV5_~7q`|~fD@FJWcW`Rq@D+i3bAM*ujHN&N-'
    'dKCOyvuC`Qdwab=p1f}=Nr=tAQ1!8rKs{Cd`J1J!II5-J%e*!2&fWOL@+eh)O@nI)xa4j8)kGJTha*pSZETi-'
    '1qKD$Dt+ib6p{;1OR!sn1{tq*ctIP+E64?!`j=*6Cj;+)+gZ+=3Df7bsj@!Cajsec)NHQp(Wzpc8Pe979`ZE5{X9ey+)h&XqUc'
    'tpsscYrln$fia~Sxu`+gV7`N|e3YN0k#4IR4?(PKdBnP9bOFj2D`rt=b*b}{6Qf0o|b^$R$8DgSOg(h7U^9&H|5GXN{X4ZB9=t'
    '|4Y_S2ac-'
    'q{NKiNCe<v3I06r)PPRL=SO)%r5D@+|(p)E$d>NHQ0b%wVQO?`K%y0*(Nw8!@7@8nHe#$0D&{P%n`>1K51L?#c|aBxMKrHSBV*'
    '!Z-'
    '7Uv+;yk!nc^!9%CIJ|^8xnt&@c~j;Dq+b4V#H%EnOOVrlDXX`f+HikMhUYKmlv9RERFi9S2eJDc`T|RTN4#P6)EyaPdR|d(Jkq'
    'D^`rR{0TtiYKJi?8B$3pEz?6USDnD=S~3SC&SyDQylc9t#jg9VI$Q$7YbhlNB!bh`v?RXLlB<uZqLG_d@%7gmI-'
    'MTd1LJ49b47_gjNRcA?I-qU?QC}3(SJvd9@%P93%pYv&W+TzN>qcT<c_0j7+=b8la-jBgZ`iba2QyrPLYR4Ml`o}2dm#;YJ%9&'
    'g`XuS81_%rqwQMUhT2<$F#`=ibISP4QtjYVW5yWjygchH5xxvCF-*W4c=EDV2tmq+`5|gD9$5L&Z`YeLgE-'
    '&$YR#hP&>yOqI5h9!n5Ra?lpD&Tj0&<fDlQPXfHFB7Q$7<FSBNi08e-p@%tY^=)PvZ!xLqgiWo`>lvpkKDer$F6G9+-'
    'nAyBc2N4ICQzTVROHuj#}oE)&%EULzc4ue(Nh;sQw_Rw|YVwJoli=(Rx4g?()D-'
    'Qd%+Oh<!kHi#QZk^Y+)!fpzcQt3pr7zOhk=F8&$?C}iS#mU_Q2Ts&kyq824bhr%!KL{b^5wz>J>~Miij~kwe9oTjP>)^+nB>yi'
    'S5k8d)20jy!fgKd)rS|w$dEKk?C8EuO;xkgo*mhb+zr|aMP${G;#s{%J_0ttutqca{ivI{8nErG$<jL<vOd`M%H6enhnCROx*j'
    'bU(_wTvMf=hgg9%5z7-xXIp6u!TI4^CTyLw^!4^VPmkZiH<L^Vs2BdHj;HC3u_{?g#_-`FXmL1)GA@)F!L_ti-'
    'D%JOL5f0kkL^{?Gew!A&#RY1AO%sW0DdLqY$e*-LEuEoz09~^6^Ld<%>tL7YZ^lpZc=m|vuzz!1M43NtmkQxp|5-'
    'GgR!o}1Ti@Ee{D%Wl0H#dy-N!-'
    'ExnukfC!`zWQ6TyFX_KzCLXtjtJp(AIGvwf!YgHnUEkMy%0BAR{lCDWkV=2ekQ!=U|3g`mR_fV39|b@-'
    'mS`LH1u@zV&?Jp!x<&I&FfLTg+X{U*}>vs{pl-lo`5Q(ntp7F%CWK}I2HZlw(masAM$?QC`7_K61X@}-'
    'flcwc+T)u2BI03EAKO3zrG2&Kdn%kjE}%x$#9Gb@(ho_xDM_J~FAyO=J>jrNdu1BDgsU^oYU0m0=d0k|V*hW0q8Godp}kK#n9r'
    'Q|$lN<H7Wf04%$`7)Pb91C>#glxy;N^PqcK7@DiTACC>czw<c5h6{C1mUQSivs)6sLoP+_7iL^ET?7_TFF}6X<E=6N5ib!^Ual'
    '-9+F9=)EFDnh9-pjXgWkV5)+EAjFaG=C?jwgL<#Ra#Ali#35`1Kn4I29?$D?ts;|j;lWol0b%k?3x7-'
    'T;5^j4sz3rD1J#)u<1(HQLc%fvemtc>>zf-'
    '{F?JxRrB~;aXq?k;G(?U!Xm5dMf9_M~DLVbiR!I5n^H%Q@^Z{O@53iE1sKhTaCfFQOn7kh8;6|1QUbp)ko1STo_K!|`hb-'
    '}1>4=gObMx46)xD)}ycegT~E#5n#Sw)oH$?8Uu{WKd$^n}?gLClw;ZTRrrm=@(K;h+V~*mka_3}{oklKA&tIX%p{Q!PVvhF8fZ'
    'Tx+bkfG}I~F<U!UpYk`dQ^Xt)-'
    '@Qi1*BXe^o~B1PB}3*4a`X5b)PVmPqg1t5QXEFy|1n@+DICw>XbS5RlgJ4I7voCgi4qw$#1tNeMJEz%fan|sm#j6p`Hd2%V2N;'
    '*2SrEy=~wUE;IELCx1o=BJ^D)HN66Oql*MrQQVFR&pqvKKKV_{;sBC~QvtJHyv&Yy6><h~cr1XM*hJx)C4VO<!o3N4q9WOiIE5'
    '0;_cjopAe^v&g1RAPH57(V|Dg!n_WQi0)U<LTq)QgFvv?m$D#9}dNQ5{BtDsM$lzU|r{7KiJ2hwcz_9X_$#)E59(t&|6fovFGi'
    '_FTV&LVD<qQSoB1_;@4PP&7mb?seDVkGe?JqeMql&$K94p;A4v+E`VA(B{r9^}8^N4#RE(N3hhU_SZzjZ48TKFxz;jFx^ECI(@'
    'Z%I!RQ~(G}s{wHjLqND?*?NZ<$n`jYl-a}XWKq-uV4XOs0Q7H>wOW)&%3imi_tJhHM8>HxOQ!)JZ;lNsdV_|KvlG*=dD6Yc|WK'
    'c~u0{E+c<$rOwA!+&Hx;+)yNo~41O>@_`4yc2vSpPhY@GsoGG;NaRtr6=iW6$p8!2_WWCivc@pQJ)U4Evh)z;<1TY`h1(TB(L$'
    '0h6JI+&4?f69ZaF^#}4U?_4F!0)VnSU%p{sb@z3eQq1S*jql7;PyGY?`KBwhtkjXq>B4WGj7X_4hH`OnhX~#k@Ug={FU2JCCDA'
    'Ukox;4Iy;g)e><?wbuZIJ6xC&&gI>%%}XP-^!}q1Wqe$01g5Dx=dsi!2(CA!RclN57gAAAwbEU4n!5OE-QslE(TMRD|f8A?R~G'
    'vD4ZAj!7;r0ykSY3iShW^&G9O{HOi8%~J5<f4PRo173Cuk)6@o*;?4v8$D!D_7wK9fVvjP*jegaHiizfQCLOQ>y1$WQi(Hz1GU'
    'L!0J;?!6oN(QcIfvu-'
    'l2R&Z>U@PgkkN^lQ{q9bZ8_&M2Z==fXQAL(^vL^u<8HMt0}hBZWf1S9G)Ek7lf3&X9o!P%~fXyDj($mOLJNG9wG}CI*39+RZ*8'
    '>9kPZ^$}lO&BsFP?bRk%{f;ETJOn+E=CU~hiN@$EFd$4R>0PRzSGgHwRB*HChXgL<|6M>I|<WiBE03riuQ&)Cbx2PyN<aU8tIj'
    'UYy-'
    '6`R)XF~xy$MzGfp6Asac^Giv@*NpFTPf{|4$Mh#)BOM$7;cn3>Lml;aC2lMEWG{p8(;s{dW4*0m1WMJY(uXvXoWMF84u_(pw6v'
    'C&}?<bD@MRhI+wqaCvKf4dC$bZywJ_f`!|MZI)=Kd?>XqD);wNhN7PkaiHTuZo7Hzdcm7(6R&*w^9EF~B^2A3IM@j~oI-Bktp1'
    'zgFlzq9Zy2NZ)GdV>jQfb$dZX{CKw_mXM=-Y{{EQX(h^HBJTk6UKCc@U#S;?VY2cSpt$C)o$_e}I9*2v~-'
    'Avn$2=^4P0yhYVX@Fot7U>@70zu2M~zI}D*o%7Sqn=FEetdUYOa|M>fm;}baZgVXamm%y>KH@Rlkc%Uf(GrE(ukO`e%VoByJ3k'
    'javZT-'
    'U|YzNd;dCwWuNUkl=B$%Mxt&TkfMvYBRJWg>shcQ68FcA~luYCAiW`_WB%jMnwFiu%<jk^bWK0Yt7?Rz#{$0;XIH0jI3WZlUm6'
    'bvuV@b0Xrly?U4JIjk<H!r9Re1z}gw?I<b1uR?a5h2zOP++lOk3uPe!sa$Hu-eCT9^C6<T!7OAws!-{^XWcVh2{)C-'
    'YK3Y!3<Bgd8NFHnfMH`P@A;$3GE$dZUJopSdnC(CWo!{;L!<cK3f<IKc5olf<Tsz!lI0;x+w#(Hu(xA)Sd6zMjCl_8|V$6&xfb'
    'DEvmFVCGeSaw3>Yp)glGe=t2DDt|O0bD(-'
    'F?sJZMiKlcIJWapcV(^WeqY0U(}6g|TTABVJ)(%hWKCAFF{Os4Vekc!^~6S+1FFVVOqt@BFFrWhI>@XMiEzGV<8TqMb+23BF|;'
    '0cG|Ua?+`h{PlA%={6`*gg?emI7>sAy!Vd#5$nU>FS=Ik@eOsL2WmUZ2@J=5jmY~+d}c1Zn|pY-Ug9^kzbUyesXbT1(pd%l{C2'
    '<duey}ycPrgwaAm#u)8ssDw9997+^L(oh>Nt|7s)t!eGhHX9Q53gxTVSAuiJkFvdnAAr{J01(8I;Lqo*xSN3ZSf~$WfXkKS!N#'
    'U#6qw0eed+HK5*KZw^-!SGyRBG+JQHtNlAZ@q0Z`qVN>gQJT(H>dK-zp|`1YlCE!RMTmBcB#HmaksXCnU1r>N>aX2yxdvMj+FR'
    '0!nAt+9!HT2qod_vm5(+{`+4NjQs&+5w(*T;T{jQ&fAVCFJxapF*Nft8?V-$hHxVx(3T3lGYg-J-'
    's`Mwx;`}k7^XbHX<lQ$JG1of6aiTBP3`|?5Z}Jt*n&4p;g~QmA{;>AbEpF59l-'
    '5X6Bz@2NTq{jQ6ZxqUj1C9Y#}Rp!&{)thHgdB3D)>YGJKUvM1d5%bL3OIFgl^x(wzD5YakmG+EJfW&S~tln7RZ}E(VB|gZtw}-'
    'V$kpj-'
    '+`f1V#y2lPO{AaZNcAk0CxG^>t1V^G5n6&XPNd<=Mfe#)nOpSoHV%gi8V}1@F|K$d&b#ntI~tPA2l^@@pA2IfCGLHfoR_6PKW&'
    '5Tf{0Pl-'
    'H)daJ?C2yO{(6Q__Ee?Ot?66^FKx9p5ny>DzxAOfhLzu!`=s|<zKQGgVoKRr*`m<Xr)5n>JDRF{kL1pwTdAGRldrTNj$!oYip@'
    'bQnHVmp$vJ(>5_cjvlszI_%JGWa}n%<xND+gP%`O39Y<y6qH72dBzid#TDPJA-'
    'A8`L@5mrKSx__XG=VvF{d<1>6_Y3Dux2>)@NS24om>*@V~9A#ixeoQ733I1wj`e;%R%3`|`p%%#+cba1Hsaj+~krQHJax~q16&'
    'x`j+mmGlQW0=mb^pQojHj09|vv|O(NsmMlnV8`f@?A3>x~J%C=*XUzO!Ut{QMEt1CM6w@53)QU3@f+i%f0k@OiX?p(PwW9)j$@'
    '1i6a)*+&HH_*Q_ZNf{(8ET&r0#((Jzz5|2w2NFJRx5=8=tEjs@}__^`+WdGKb5nZ?xt?Gwb8YzdT>;$C_G++FiC}EJ%U^NLHSl'
    'ul$>958d^~b#t>7CDO4K+c)^<eSh=no<mAihR#hl<hz?zgW-'
    '7<c7?H$#$VL;wgwG`<eMlyqR}@q{E{PN>?jBda5Mkcy6dnce3<fs^OPgC*J#y!)ZtPUO0#b^vRdmO}QwGDT3;s;=drdW=dcXc3'
    'XYQ*sSp)U(?!s1*Z$=k_#2ppZOgym@uxN+Kr&C9!9F1_kk+gVc-wyUW#`<F;;ct9SZ$z%zh++ZeT)6VLiU8893+|CzE-'
    '2SbmGS3x-'
    '7qAD~MD7diI%XGG@goZ>~D>%gNznP)99c;MuUsRao^cCc*I)x8trwW}`xvIBhUl}AFc|RdA()#Vshoiu+jB5k78)7h`HMvjf2?'
    '&rZxky9S^3rg;8Jw~Wzg~kudfmFzb+rFaQv`*+?&8cdwZN~6BrMK(eC8O|{xt=+tG&^gqB}kmP-ghQ2t`>8%<Skx)JFbXCCN;l'
    '$n_C~{p5KCXTTkZe;1n&UlWD5-'
    'U3nD{imuX158b|YNIT;Q1Vp%jh66hM%Mt%)cEux+B6%6Q>!Kd+>E(j2PR4?g16MDRH=xME;If4S5n29l_ZPTr_{702&Z*$Lx+B'
    'nZRUsGmI95_ky)^NX)|JfMwVe!vA)J{DsnoghlBhzs*!wcLb6%b`dMquJ$S=ir2O2?LzSJk4H~h;ICVbauo{Eo;fL}1N2HkkiL'
    '!PW6iuPCL{4ngf`w|rI7HCh^?NBA5*P^~-'
    'b@MZhO7Lizq1V4?HB~eX>UMjm~r1sqVpn@r&F}n@Xmk{UE5vLGqHhrx_6e$(%~LqK^4hU61d~1#0nBVRLDlY{)PbLlY<M(S0Ug'
    'X7_}D7*3TI4{u0u;oUd+kdM>VaLvzdz$(Dv}x84uYb`moXZus*il=;7Kpio(~(OLG!3MS7KxceS*E9yk$O&;i-'
    '9U*mBDvg?<HCB+D?%5QcHo4QiS2Eno<{|4>j)NIMOB*m-b04lkJVBKJPfCTzJ^C@o;%@@0AdT3|?|vU}50;O)PW6m#-'
    'aMz;chd(YSrKW}*P+4Woh_<1FZfbx9*ZPY6`_jpDqx}|C<*Wq_A|26H^XHxih{(M%dJvqE8u@qIglF=6fsls4?9l+bvD$D6mud'
    'vXCh_%1E7=xK-'
    'U*v_$IHoYmc*xkT8|76(Kn3=!{z7@PApD@i+lo2o*&I(*zok8wW+*4ghyi8rf*#AXflR;an+EiuEg<l`V77KGmBxQb2Wj$%{mk'
    'Jdy!&xP;0lsQ4g*@wwr6>OJiELs>&RC5NkVJjA+$#uRP9(AiNwXh6DC4Crw#+>S-'
    'itirTtmH=CoOYr8q79POISHR%Cj++A)|2tt)4(T1`n7K`(c|Z4XQe=gJx-riDv@~(iBd@C7JBn3EXktn~9rvs})oW%^H`xmJx8'
    ';ojaxah)HU<lk>*Y;3*`agQzd7evTZh+1jfPhfJ#@U<ZoliB0xiUA<#w)N^<WuOln8Ywua}qn_jr_h=A;dQ+$`M78axI%M?uG?'
    'gdAKmSiO&1%rY1puP)rwu9URypSIcW9AwHlxuaHOM!K~I7`A}~Ym1K~olfUW`-'
    'ytWG~hZH{uxW2qrO3XHPDnp($LCzd(o7KL+ON0uhF%IW|OlJh2{yAICVPVBr{N?kxx)pG`m>O^fS;y?KD2xEQy^REREnDDO2#g'
    '*F|WTK4|)8K*t9iLgi0#v&H(G8QI-}$}_jg?zyKQ?1L3Xu5sH-'
    '7zed2Fzz854YQ|t0~;Kj`88e12!w8xPe6^JeDW;CBQA5hke%~h#W~y-'
    'RVK+vN<C?DgtWlx=PG@|Mp*)+xN;vpua2`oy&f)QJoov^1)W%XmKoZIpdRbcDFoO#Lwk2duTr}U5w4#(sSO-Xv-ZEpd2DA*<kv'
    'kei4Malwqp#cUUb)p@oJ7=EwRgJEFj7*jbl^|J?F#FX!TP`G{Q%()?hc3{v}`U;@SA}bl$uMCGBX%Ww!u>?f~2`S{cmCKdx~1+'
    'R?O(qSm=i)5}LDK-)Yj$-S|$PA^X{mQ7WbXbKwzoUb<u#yW{_{s;2a!mYijsOFS_L-'
    '2j16sya6lOG)edSD;><fmX#E#RA6_B{`mf%AvXkMvZ&Y@8u(9T(>fR0g798()X*^N6CagLvR2fU{SB7AKffV@o;1jfNnS@ox=m'
    'dm1BMkNDm!w7}4p769^cYYvqf)ep7Vm6o&?$bqYX0R7xQD3g3P$Y0{QE|x|;$#_oZ?HJSZuERYj<sX|J4Pf5Xz408G|7H|$5E>'
    'vBqaYk8<d*c>tMSaJ2bAzhf?Xpy*qAk18+>Ad99X!_rljIZ(*f{S@>;P;vsd51buKaU>^Tceql3gkD&GggTEC<ZMQ@a^@Qn0Cs'
    'ZVT;HY@6ZTN&xE%*s#Ic{nC$t$~;s0l5``yLsQ_2H)LMzS6kbAFpp}?p!Vn7eMXkoZOXxzCHf)c(m$9X0!Qd19_N=LFT~C6UJ0'
    'E>&}CF)&{kBIVhE1QfX)gOh7mGNhDQ<9Jx|_@2>!wOkp_;LIB#Z1<4y;*rg8iv$D>Zsi<0>8NCq2uNh_#p{KMX(m5^p(>VKn#~'
    'XHrMGp^-'
    'mifHSTC)4|$mWLm|G~HJqe9~ydAF8W%&saO|C%#p6`Ht@#uW;ENJfzOyq6P1#iC?vOmfG{h7)Qb;qJIx{&B0kv8qh;+Fr7io={'
    'cD`bdpiqt7UZf+JB^U<c^97o+HArkJ_V1Lujde{r>{h80Oz<Z5?$eoY#%WlYqi{YUXeWJ7eVsRk<ARwX6K)6oi~Bh48@xbT0|%'
    '4k2DF<-'
    '}6X^4O7;QseLH^EDXh{f=WsNuj}nDYXcpL8MjB3F{!F+Ej(I>Aa!M&JHqU};@uodiRo{0RV<WCm5?+LW)}>TgQ!p%QM<=gAnJm'
    'nI<v@%i)WuVj$9H}QTs2+Lrl47GbeezDAHix4WewcmGF#}F$pYS|}~G{I)wxhvKnyyxCZcCA^e*f{P@_`uQQ?%(wua;Bs}thNo'
    '136hYQ2v1fuN<dMNbE8mHPLXK^Gq#0nG9VA8a;|#<mwXmvJC_F4cJ^YPBNj#&BtEhxa<q)o?Z~+fp4^0Rh+svmh9|F8($Uu;L^'
    'BcHXz}}nJ$W)Z#8NX^vBAd3Hx~bqh*6<i+p@Vv#A||r2N8XTJs(|=ule*PB2gQrl?hc`FixHG4pYV~><McN`-'
    'ReaWI;cZ;Pt#l;qzihW!Ks<0DhN#?bSKwJj4j3)l-0NzYcWR?J^1u8Bmji9>a-'
    'iQC+b_^t09rKnvtdi%K2iNHby=N&2Z0XP#|3VayWAGpmzFUeEUr^3d96634;a<HxA|lt>e!=I5*6TZRsEgi8yY3EZlujsgLLOX'
    'K>v031QTU>I<conYWN1Dc<BZic&>F-'
    '<p&j&hn#`b*jMtO%cWuubu5TKJ0J1BBPzo<PeZFt6=*K7`^ndy$JA{g|HzuVp1ope}2sJc!x?u_T-'
    ';P5z7a921&Ro&6zVcH>q8rMBo^898d*Hc~1vT}~)3qV1UT1+<UHwJfruTD5KpxN9bA9&HT}lK<!4Ffq&>{T=aAF6FCMj6x*@pl'
    '4qzi^jSm;@2Nx;Hiw#71Kd!1V`~b^LT6jYtrrKF=&g9x(lJ?iPU=Kk(j6Wi?CyE!bu$Ph`c)Z>tg&EUMCS+%^&8m*<AGouwu79'
    'Es8SGeng?K_36YOc&l|t4{^o#Qn9vDS_93;6Mel$ma5wqn{jT567BedV{0R3(W8K%NPLowuHWrCso--L&4xc$AmLjooUeK@cIu'
    'jieXBwZM>!x|N)&)UZJd-'
    '1Au`Nzf(S6_Bw4_bT>L76UWeT1&99Y1@IcJS4r%IHV<8H^wNMX_F~Rb#nfE5B%Jo+&%iAz3zj@G!4F&X`*w1s96Wlb`OGL2Rs2'
    '=uL!wDJTqJX29NlEhev&iyb(P)aH(?EPVrs~O5dZnH4V+&;Xsc&1<U+SM70V~QAg98F;EMDiuqhp%XU`#<9MgPhJq_LX-'
    '$<n*zL)3^h`1<*72-'
    'Q2S9RPJlO^t@tOlG3wo)iEp_kRpY8H!FLXYbF<^KVBg>bOA(p`)5AM6902=v;g(>bx*NDyA;VHGf+=uF2j$vNDCJG!IdfCYm5t'
    'FaUvty4^v8z16yXYkMF=knIW_RvBZ7Oj=?}0azvcaiRG6bcvrfyk!buCR=$8BCG-xOK7kh8olqGr(7rn1W+rE8W02x`1kFakq0'
    'z5C~>;%AmpWT$l~VzG8tT`bZ$~Exp}X|-'
    'NEw2(Q*f0pSRj11!8P{m*=hZYfum%MTVY%wRfS`K`V|TEbkKg{!7+9!Xa?qw6clRcu#_kGuqw#v)VWX<ear!r$peb&V*wGhn1q'
    'T-Tj!R7I}IVY`efQLHN}Ja2_WQ6pv(PL>^LZa<G6?pOMI>wNA0ND?Y&GGd;qgN0eHI6lBt`YxoOwuqFEN=bX;?m6O29*7(njFJ'
    'm+C9^FDqums(QEx7Ya;zM!8pv_zC{U?9(>!!}mJppD-'
    'N+qUhMfW19vO&&yqw}7_Q1h>7v|l&(H6gvJnqS=)W;i|Rb*9<fcZK|jDE`FNupNaX)679X*-'
    'e(!9lFGeg6Vm~1d5F|#$GCalhu5Ymyai_yO%2|q|e}*M-^$Y0T|8xatX-'
    '>cLC&YW3%FfmY&r8N&l;L6|Z9ha8>v;o|<TV+RkpHHtt)-'
    '`Y^Ktv*Xd~?#=iA(?A`~5R>nDJ@cvdV_3YqF4~JUOVME>l)s@kfS~($r?q*6#T_9D3mLt3L~?p<8I1M+aEWvRH-c~>gNPXH-'
    'V@?Uyn@jh)Q(sTLf#Ku>p^7-xJmD6W%(jn*b2z27z1_$Bf`i7HmSHGysw_wNE?qEnqXE0^7$ibbh}Y-'
    '5O0GRpzr^1Ztq^B@bp5U%hHw8bit6JVme=K-lr-g;S<=Q{__UeNai=FUlM@dvJKJo=}*68wQv}x-'
    'Auc{dr^0U%oGULcQs%Po%ag@jJ_D=P>#mCc^+Z7y9=$_M&g(!oRIsir?GNgRn?RV$zK_RyyI~3l121qmj#$G<_Yn|5_sn}eO@='
    'Ea89YP(QdoHrD*JH5`GjhgpK|ycf1)Jh2tYc83X1WYO<Bp+E&NJXK9DzM!BDkfx3nVZX-BLwj|P!QlADq&uu-'
    'KI1OR0)3MROb^(t4bjlRZ6aQdZmpUZuHn-TVNVlm{mg(pLW!}?Sz{l1sv?>9|7mSo43A09Nt*Yu$?@CWhOz%)UHL0eP23)|2^`'
    'R3-hKlZP=G-vX)P+q-'
    '>W9#`iXi}0A86(2ZIlh*<mF<U=7hpL(k?uk7UwFxN?`u18+#pP;p*F3H7|f0N_5`7+`DfaW*+frrLmT+S((M@ljZc116L;qNPp'
    'pEw^E!U8azKJdq^RVWymNE=>n%19Wy0QH3~xq0h?18!XGTQ-AMOVJz*kB&5qPA-'
    'z^Tppdo1hNb+()k79=Tp#A^q%4h^nzAMDTR|2<o^-&LUUg%s<tS>KY9k{;LZCL<|l@{zywhpLLJ(Uy9F3`*@ghB-JqMG#OxWPW'
    'q5V7TMN!+*d&&b)l)!oj0q0WadN!)mVD8(mcvGQ#3i1@x1ku|d@dU6?SMrx1@O1k5D6B>}B$j9VDz~mM2Dpht@$urk<#aC*{G<'
    'PmzC?Xv5pl8l80Eo#n9|e9#(P(Re?j3)%2YS*zVwDskTLRL%3a6XkqWr1~jBvVO&Y!p1e~w9WGinA#m77_wnE68eRlEdv?}N?i'
    '<4z_#{Joq`T&3`F>_MgN&_pBKKIan+7?*oS8$&~JSKieX^EMpFI|j)A=Qh|8(^$lMcnhMd!OC7h;Z=U8!i_wily1X6f!M1fN+f'
    'xUX!+PpCyJh{ehJt>xjC9Ttr=o51q@ppSMS<#_g%o8&x&^uMj?!ZuJ#UA1?lL62ozCh#4tS~t#<S8-'
    'bnK%r<!Ssd)GADZIAzW5SFJsfB{7siby+L)IR=(Wxz&q!;gPX?3&*7$gyKIr5-'
    'AHP#|jOZ1wDn*DE{Mc1L>tn2l}|@`?ZWmggqZkhKNBp*Rv61BsHk$1j>y>Iukf;zuVEobaBP2Rg?Q`i5A0z+_o^=zcX3S)MPOj'
    'reW(@*J_}9&K;4$Xy)3oPbCxZP}lutFWY38<hj5Eb$u9=9_GPsYTTGIdGBb0ADMJd&OgRGlL=-lg5|~3O<#Mx1&-'
    'q9KuuwVBFm(UbLcVG4HYC8x-'
    '8}X2kcE&hY|H0$&biOZf$v<Yf$0I!rhwOwDrA_C9q0%a&BW^~9NDd?V4L?TmoU3r##AqQ?J=z#^qP{a^r&wOVl-'
    'm)h3&?%I^jk!Pgo8ALG-$lsxet31rhWPAChTsOMmxdW!kqHlw=@g{_WNa&U3l%{gf-'
    '}ZH_n#E<Ph}C=E*x00+uIfjWmdK*~2k=kU@R@=ogzm5VV-'
    '}_e0c~`xui;cefXpf5`tEFgPjeo?4l8rM?G(Qu)VSw;&)2ztKxvM>vfp)BIw#cc0AZK!NhULNy~y-_ZXq)Rf%pHx#GH1RY$O-'
    '7)z+Wl8^%1~pF9pnSe40S5(*x1PlZD1(dNcUUCPHY9^KOwzCeCtV?yUsd~f0}Y$eu7%8{_R<(PD%W?Gih7r3waPjt4ebPep1c$'
    'sC%8cV;`4k43rAb<ACMTR$vIr2q`L7#^9>vs7-+zTV7{ONRYM;GB2m`vazO8Rapz=KSY1!_xx{V3M;-v`qBU{!VT<$AeV#p-'
    'IzZz%ske@(etc%|hXKov-'
    '_`0=SEz2Ov^u@4}~6ZG_oo;H=Xqko1GT7Q`3Fc1#OH0M?OF@SIPW7P4e;&rEif5?ks7W%t8;7SrfVPqw(&zC-'
    '~LMaSDE2C3Pz*UiL?`!prYgxoOs-=iVT~(fRFEN%<v?-'
    'g$wdhAhBp*P4O_755=@kK~DuHy|D|Ly~(<ovIDXBY+?BY8eMemh>=H<{O7&Ks=KMwrSV<n+i;*8p<xzAVQ-'
    'd~j=iwB`L6M*ynCOY?~1_6!`0H{b>;*qNg_e%u-w1Z#~N-pY$Jhn-'
    'S%6K+)dNOk6dTt(Qd=>;Rdh$&{j*ROGU}`U<G9gpjC~J6BvMP7ulL-'
    '<84j$1~JPsCfW)L99<0dbgUTjwLA?(mDiq$i<q5WWNb>2|~26uf&tHIl24A3!pZ6{5zdKN1WZrYH<l9S3HXjxN#hO4X1ZRIEv^'
    'fr^y8XwOB?b<tzj|OqE0APK<wqP9P#)i<g36Mt^f2x3I;9G2(HlKA2gG*>R4*~aBPmDhFDVlK>b3_yl^MEMT`>SF#J*&axb#nF'
    '{r0kl<N7{oi@cRe*jJin=?sS5XdJBQK1X+2Mj&_c1NHRN5JEpGKq{-'
    'u_39tkhZ;;C#zhD7Wb!wrOq$Qe)!cJ`<>KGfVpYvdS*kj$qxfI$!vD9;jxqg{HbMw~<^8A@)U#8s2%#MCuju&^EczD#msu=owo'
    'o0>)R4=-8+MeJ=%1a<-AzXffu`yBK{7SngaehnQvZ>XG=nd?T9du&@!rlEcd%DdAZKnrTcfFQbh)b)#5FOpqJ85Z-'
    '&<}h)`O|T+e-'
    'IGnFk$>tG5(E3$kkHV&osVRnP!RUYIEgOF+!tuRsr^@4oXAokzd>|F5*OK6{imW=Eo~TuO_uIz&oKsdw$PqJcBrW6^#4N95Yex'
    'lxX*&xsq8@pe*OKAE-+T)6W!L;iaqQ#`S?WmMe0b328+}>SoGHnIA*BYNIZM-iE5Nf<>2g4|e`b@7i!EGe<Hy>E>5#53fLM_u*'
    '6Q^7!7%*3k(22y<B0+Mwng+o(tuNeI;FAuNl1CRiJm^V6jQACnqfM)EolRKQsx5@U<1Jgiq>Dd!U(zJbo-sjW??3Ysw=ZRV|!C'
    '$PYo(0hHV<G!(t$aMUJQ_#vJNS2$8D*sN)_eC@v?O$;2k?mgns>EG`Jf7go$xT@sn(jUNydEgC|I8BY6j|GhJ*Fxau)y;PU*J2'
    '^{&H%S%=^8ZMKVqlg3#l}uKt<NcnN!n2>xmOwPK7vSuKBb80s9npTac5r037fG)RBm36>8@nwXJ4<n8>Yqe?1uI1TM?GXY=h)v'
    'FUSvpY73raR1-'
    'u4~~x)awAP>B&w>e?bRaZgTe|Xlpf=1|r9gy!GVq%T;iz4@;|tBt|4QLCS_CITHvvVl0m)DhW}O6MC7fBf`;iRnhTbr($E+L;%'
    '3koNFI&hL<Oqb7V&~u3aGU52ac3)S!WG*2>^m1kJjbeacSrF-Yi_T=T<juQFuqMd?$S9o8UH0iY{fhx&(whF5n&-'
    '6>j`F2OfRBPE^`V)}Bh<44@4#uC|NPpKy=G^R2b{0NJbIoAmmG?vhQ^!<s29;2NUoz>Fm@?cZ#D3$uK{K*({10^3*gqxzv^UeD'
    '?!E;PVJvB*%Eqo>p+KU3cDA7mBR}S=c1>k<{FJS?&jr%qbs{vg8-3;wePrZb}D3L))E`z!^a_WWz<PHR-'
    '8Ol&7ZUgjk)v>p9Eb@~uZ*8y7B8+r%Dn)HhRmq|7epG;}<Bx<<Ck+dTE6HTvMuIM>w_6!WfRy1(C9m+RM4oHL3(Xze-'
    's#;ffcOUg#^HH<L>9-'
    '@z+drE&LnNFtZlH7)BVB(E<$0QL<=dn{=YO@>)9O@1<6$u{{)~bwls_<C9Sf#9pK6i?E^i_B*#0=b!J$195Ei7elQDr?>%5z9K'
    '*xz%?HRzhAE)tcfqpu0fdaSAw{WyX<?T*8#+0GbH6Y0$rr+z7{U@;M{f>U`411Uot9EkFCsvR1%|?k_YklIc<nR$BRDGv(y-'
    'lYawa?|H>b@8oF*pzjV}tY9~jn3(UxD|uc@<0845$i%vYF2u8wJvHWhHUswp?%pY?BGQaNx_kB9Q)er!r3!Ay+h!xM8#iK#9?3'
    'K9n^YV!hlW|b6#5w`S$NF|^YW#QojmiE*wnpyR45m+*r`BF+GGp9f`d+mG5f9<_Y3R{hhJ%W*3gdHp1d><I)5#m7<rQDGEK6!1'
    '~lTdngb4AYCS4PrFkR&bD3%@k6gqrc*CiA^dtZ~uAi*@fN;FJQGHkzmm*GL|OiC&!z8SYfXc+%<024mY0K==`DsPWCl+T*KhO|'
    '+zj4gdqwQp~5YeogV?mi!?sPZx9`^(S8EUZSDCHcx|Br|Oeq+hmw@hB$B?KqO)5nEL#}#lx+Tp*@5{8Zht83d@pbCC=Yi#K!M<'
    'ta%G75ZN0z^f{-'
    '=j(r}(ofyk<vFOfaKoaS`p0xOFWUvbl0I$G@IM|w>3jaYQIxqVrf}JT2S8{O|fF7=ffbW)txv!fH*|f$mw{U<&wzkSan_sYohg'
    'puW5c3B?y_00Ceh#F(^a`@r^9WL3Zs9<Bl7f0a^C8E6C@U`=f%&FGZDR7eN57Vc$8X0c4RS$q*3dqs<7}YMU`_CL=3DgRJW_TZ'
    'OusB|AEnFh#CTt-WO!PZZa?`w4rdY*{XME%dtb~VB!=|~2YXl8zvw(kh%RRrGm>2o5~(C>jr(=J?%#Z8z);GRf94n5X#bF7B3%'
    'kJG<19*EGZZIG;tfL0wJ`j9bqj%NQ90WvZ==o)B$V|wy=Plv5TXwTNP-GR)b`x@kb#!KJ__1_J6HA0bF)69Ts;iOM|qJHzfy_`'
    'c>K*yJ%wL)6-u{IRgO2H)@3n!ZE4Z5hGw`&A##G<dPbnS@u}1hM)Jmqhee}4J2aLXrE~`fH0S|<+;jTfi&K<-'
    'v=dA%gm16QPpgE)IiM}Hhv=k&%o(%K1;&)5Wa@qI@Hr8vl~DMJ=!HNHbF>9WERHY1Fx0W(J_S4Qw`_20*HEzsTF@TmVfd^zFs2'
    'PiWS<kaY9w24uyih%fwrAUAo6brE)}XyH{q$TL^#Q#x9Q1fQV8}CMU4a*Kw?`UF|1@K3Qy1h@?}a9|zXD@1s*lCSB@jJ#|qIa8'
    'D2Qa?{!8E*oLZ70BGzUSD-MVhybMz!guko`=rsr(YOn1^A*O`?k$&n2UI}V$-E7S|LU`k$G{D)-'
    '#3PG&;~aBkttGbwdHl_fr6l<?iMXXPqiurwrF15{YxzTPdA;DkyLV!C#FK+~C(OS+Li7p}(SPcT5Z9wQOE4>Hw2s)d1x24D|Yl'
    'RhF68WGlLuRaD6=S!LPuEheDjli9;{K8m%-'
    'AE~(a%r^X*gvTubyh!jwraVnJABCUD6RydOpU*xxLE?6uck)5(o)Tm2Za274cGrT7wt6}2K0C8cM%mLgvD!V-Qb;wUo&qX-'
    '%dG#2^B~50I~zd4fYIS|sXh?gu}qiEV2W)r%}?$_0-q0s&czEmAL`M|i@~}L;XJ$<R_n@SO~iK<M04C5lJc@#h&h+7Yha0&-'
    '>j>!<E`CV`46i7A7RYWW&ua4<fIQu*~><Yc4#UEOsfV2$=MXIs~-=vmMNE-7X_c~hTXO_aY7**@h0Zw7-#oZY*(rJ-TBL~TP8b'
    '}lyA;9m}^StS$enao+10g-'
    'vv2J=i}|I{Is?vrONMxJkP(bXtd&kNN<KpRT0;|dZReu^o8FWtNKbR)mf&axqi;saa*N*Ayrdz`?T1-'
    'e#vWGm;5h#f$ZSjvqFOW8r4voD^z=64#90V_j}`yh`ep)KZ$_^X~$4sl9J#fAIn;eTh*Ha@TEjJ)k1#(SG4cCnGX0vesKg{I7}'
    '4q?;s{?^7P`ZQ1u@4x-PaM)F(6xMN=fdg!DQoZXdmJ_re_VEAr-l(uRJ0GSDO9R;}=q<9Tt$98{FK;gHGth5DI1-'
    'V6I`>B#i45CknJnAMV5Ac2Fb%O|urs(7iW8l8$)X$@##vM{a!f&b@kPP6NJxQC*Q<$w6eLX^P>#Lj+1s;SfB)yiQ2LguPKo7f2'
    'yFs3UW_{KkRR+Sn83-d&ov$F69GBYhzl)V*kE+k>NHYwxX8v-=mYc4D?q-~v@FAB}t@PkBdg4-'
    '^z8x|&|XXXPD`98@a0Cm&BcSS{YJ-g9ylfAYAUO1gL5Yi@@76-'
    'q@Wlu`^D)y`iBuZXMDgL1nK*OZ0*f=O##P%vCd}m4rQ&?{uk=n?{{A57<K7HizO1f!nR>s%Cz?A}yO^1<6_BqDg-'
    '1Y>`O4R%TO4(~c;m|tCJq_&3eMj#2EP~P;K+|~7KvkM%ZD<kiW}i!jUREWiq}r;lk>0mbd!2)8dY0>R!g$(j{A%UBhMqB(ei)Q'
    'JJB221vDNG36q|Ue{QNxbKW5oFrP;VHQ0}m#qh7ksSYOL*C=>lqS6G_@&^2J<vKiO<oP+3cZCEx_k7qum?d)0l0Sn<M<%XiVWv'
    'HDG9j%`?OS231mmsASzF%2y(DBO|JvIkxV&6@j3c+C-'
    '_%5Jrb#Fdgp>jcPZ&PrEeyS@OSIp4TOow)b9dI9yBpMs}BmrxDu9W9P?0yN6Y8NO{LflKCaqB;-'
    'U7K0MBOl=G(o#&rU6xZ^n5b0(@!NIM>$TWlM(lQuP9*9tj7T1<&Os6^HxYN5+6)-*-'
    '2)mk)}@ZPBnw?q<kOV$_cfGTkm?#S6Zq|NI`Z%^!tz&B>jS7qtKIFnW*4Qc1v%iX87(a_(o1o42-'
    '=@x6KXXF2G6e*8`Jd`<K4FM>%vDlVKe)VSM!|ErgJ-h%32OdEuUnkh;i1c13_hL7HE+_+ZwbhSIc|r05<RX5V54c=)%+Y-'
    '`X^A$|_wWWIztNmvy(Bv3D`ab<)Z7N0X*Ny7yWtW8KxKT8*&S5#G18f+Ms)R<#}wrnM_OeKu_Hzuf8Dm|y-$CnUmej%*fZ|I-'
    'GTEiub@aa;W3J%8|Pzb1as6{I_UnAvG9fH{#3HyOxha%&zTF`m%yI6%UJ2nx0$!-'
    't5zV?|6l`~dDLJ_%@x_eelIZ46dv77np5@1XWcn-nBqtNw&4r}2J{gJtE~vF??EMwBA<4sk^2A-'
    'Z5Jjw3ZK0y1OV;+}&SwRq1|U|jTiB8b|GMD6o%ZX`FD8ID65DGQ6mm!zT<$$0*=!3T)=h~R_Xo*AE`NfT#8?Oq*y-'
    'w6GyxSNP*y!}9ZW2;;fd@6;WC8vyhYK-m(jkYd~Q)HzOGl7RM-DcCMlpdL>R|jnqmXQ|-'
    'xLdtf4ncLbplTE@9dZ|UM#>b8Yaw#0Am~Lsv$xzA7DIqld%za_ay7~D2#vps)x`al)+!QcbpL<6$3Pj&Xb2@tdWb*x*#y0z(g#'
    '2-'
    'SekWMRyh)9#tB*fWJDfxhBLz0mDyOlL(|a3t%dk$gIiQ~w*s@thDnZg{S4UvUI~<05kky~t#@0u;XC`7TgD4ff&K;+$gT{W05n'
    'Ur+a9jB_(;hLZ=H$v3|q381rfUB;uf|`l@&I=$18&7q85f8n7O;7RG^U*d8njcyKJh+boYW8zI|+l72CSX7~MWr>r;??6t-'
    ')*tFqw?6^O;nb4vd0p+hya_TXIgk$LctGS2yr0%F4m!G_eWV|V~X+57Yvn(Fq}{WLr>nx)OC!C}58z$jt%ZM4;-'
    '6okszX4GxwSU1?O6>CJ|u;_GdG|>(85tW-=Bxq9J;qXTH^@7y&%{GV6z#hoxNMWg#j3LnhH&hbs*R!@}N$92Po-'
    'o$BNmocmrw?8qR0%b2U<iYU#kMS9uKr6^7X!JAD%1?{pS??z{*_kmB=YXLL@OZ7cYv7rKG!QK(<!rB^TKdW9YI<$XzAUeURK(_'
    'D<OuOPJM|CO+qGF%ukcaW#Duf0IuQcKm%MmA;As7k%6Y~Le3SBbK8_O*rz^8*B<A^a*=*U1M%_YzuLdQXafWEEO+K{!*%%a`=&'
    'P%eDq<3;*e4bFBnH~X4(3=1e&lqve^jZ9BxgAV00H!GiI=W$ge_~Zg7)}wm3197k&*5IOFJz#fCif0TY0`(7jUv+!oI%E(Ysct'
    'h{r5u#aDIiqsJ?+8<TS*@7G-@RbL{U2PWd7&?5X(H)s9@emHX6&9)c%JxVUlCSPDBkT2g^KV<-'
    ';Pjk<lbskXfK6?Mmc@vgv;O7e*H9+ChYM5#adwd=Uy6Ltf0pSXb_m=jeDt5<HLPJwc1}3azYT#6XN)V%PVo<{uQ;^>B$qh2S<&'
    '}s<?o@I9z9scs(M!_iRiNTgxT=wCk15bs6Z3p{OiFHyoNaZc#@-'
    'lb32H6p2+b1g_j5}rI`q>{d6>bA135_(A>}WGEp)MSpQnAX;;|Z0H}h;HR3@SfbKsmzNG(SPiqTGgx-Y6e{fP1?1(~M6{aK-'
    '{ARb_OMZXuC7&1B1oeSP$&zX`%l084rC7g@*{ImO>DCSi#8mfedw~HhsS=_Im&X2sWO0R1?LR|$ZemP1SN_LZw9<dF8Pa~dw{!'
    'SUd$jXlTo9BtKv|3#O8azV-'
    '+yVhx!!<&kv!@>bH}G*XT@isOcnQ*zh22+V}2CsFX?v9c>C*xyaZzLoxae1N02ky3MI3+BWL(3UuPidDL``pgpW39w$$kLO~|a'
    '}s}0vcCsL~us-E5b<=<XO#?35cjR}Bck}!a<VgANvkS%6~13@~{??KQP-NbKeZexm*9Z(yOos01!@7;;EOhoSRyK6334eI+*sI'
    'CUdn|5aT*tzy_&#Uq2k%~T<b}X-'
    '3MP@}Ps!@IdJq>iLlKomb)Q{3hV{zaSaBKDuVU;k{DEOn!h~NztkQ)cfsi^q%<5C6oj<i;9c?!ZirE_7vyjf<eWgo^;aR4T7Of'
    'rug#KxzGPf@#CJ~Xhm*`u)x#frRt?wf~E^j~Yz9X?Dl@i2$VvpJMD-'
    '9^D0HV~>j@<0gJ{8hSElAJZNdM%VqK=2{<kuQltb*uY}pZPHA&;13>?IBF!B~}lpuDl~kEr^#;i<UQy9sp7F%SITzU0&qRrZ6S'
    '|sGNR^o4g#14<fAeuQ9yCt<<F?8?Ox|DyU}2Jo<NDVCXVlESqCjR-'
    'O=G;9MX58+J_PL(+sZm{zKK98xQ7`pN&AZ2HpMo38=Yk=!|bP)2o}0*KmZaJE^h>XS!f2p4@lQq>FL8_*yrnXEjxj!Gq$D)Ol?'
    'q2A=VbA~r;<j=|Tt-L<DVQj!4YL$@On5Nb)n1-aWipJss>5QGxNQbP3#dEwm?ntQvVYbOsHx`@<opUIL-'
    'C<ZCzc0fYJ@%=WYg~2QV|wERHAV>%@Go)+#*+<TiHq!mU7u)gj3e9z;^YBJno)sy4Z|h3v<M;*{!a?R(}UI~Iy}u^=6X!yelxC'
    '8Bsv%cV`Sd*mvq5?NpDRUvjt56-'
    '43=5stNUT=oVLnCQeb~)+eF0AN<II=j=3~AEP`h9@>~=OeuD6l|bf+Mm`;lRgonngNCML6!8b66|IANVk|nhQ?<t~A!^UOIhC#'
    '4)~^mYnL<*NUG)QUbJBI7Qzpdb@D^u!1{!PST5(o9o&E>7fJGq|!8uDO)Kk$v*ssHvaZr0(e~mrL57I*^mnOUtT3neO0dfB@pY'
    'Jr^kA0Ky3A=^QTC|o5?AyuEP}MW;!V#!(aG#xn0NsZD7TDy_W;XHu+K^D^K36QDIG6?yle(QTXoryv-Pi~2JbP|EjF=NpeA6qb'
    'e_;hgQkvDR3*5pX<TG5izehBH+J9}6eA@Py=nQ|E`t6YJY!)EpG{*~oVli-'
    '&2x6g+R>?*anGoEZ^))wDq%A=1ef4HA)uUpqssGwlW-ZQ8z~gsvT+u)muj9Tqka62>#G@O*m<-'
    '|<WsL8@#Hr(Iiv*PAoo7Mz@c(ALk6b!&Ry&I2dmGfyJEm6x95llQ6ejN7+z2u?JES`3wfn!d&{~u69?tS*ATd)c_so9ociiB{)'
    '0}biRP@wW`+|ieiZw9kFXCTvoNr0!8UP%63<P%1f;*|%L#w2Ky#j+pcGgajd8Nt4uf-Lc*kBGH2{?>*tC1&enz_p*kHN%$M=w2'
    'qMJfa{Mk4hE)iR@xKFz{mW#Ezg?cc)?wAB`<2)I#s3!?U>3m&99eW~&ZLMb@DQkfve9w_r!%Jp6YZF6Vqt&g_K?=7?t6jy4IXB'
    'mQCHQl`v-DF3$6FT-'
    'F2DS^cRw?0I3o5CP^`v_#WKc`HUL0yc1o!8waMVi2F@Ub1wGU*Y>hbR|n?V*vq&wG5Wqgb3ze73zBTnZQr3<ocgx_{^hO%(8El'
    'mlFJaU80WCpMn1<-'
    '+V`?ZZ2kvQWgahJj1r*uyz?_6l>gc~1;d!irtqcCSm1j?=|?1?fsA+f9x^I)XOxu1^)WjfRH7_yIcCrJ1l10q0kN!;mxeI)bT%'
    'L>am9K8b5s7C9#Q)iX0ke^!YE5<(rFM%Ztw}sICMwVjZM7D5^slR$Mau!b$h%%Hl#l5AuAex!f`(%NzJ4-'
    '1Nrq&w(y7LF2_AuH?@OEndnGg{FqN|xBy@zooMuHut&996d9s!JvIyq<=y-'
    '4XVYik$#D6G>EWCA9;)EF0OjuGH%O8Kdchw0v2Rs?~K@BvLx<H=s(ZO&pPLpuSGf-'
    'r`!h!T4x?6&CENPHZ%u8JaYdm!EX^^@tTM^6B5aDkPMupwR`<{6y6mL9$<7u?Te()h)*&SgW20;zYWa6O=XKMRVA;MNWya;(%5'
    'ICKZ3bIMCI$)sL^zU=qx8(#Arl3{ggqO0M9^SkrLk=|=zY114i2;C3RkxG10(k!|JGb?P8H~7;n`e5@)*P|1LjXHj*<*p*%$+9'
    '<~n+urYyA%|}zJ}+BzYK4K##{@p{j`mi->lXK{GODbfwW|9j~iTdj3Ea3eeSc!-'
    '8Sx$&v}nfoxDzsfnn_PFK3YgsB5qVI{QX*u?rS680zJp_UbA28HY~m{(oTa^IuuP)KJc%7w{gobk-'
    'O$z0Z(WhrP1gLUN_OfN>vsL7p2?uYUvHP7(HI0-'
    'wo^BEYiF0iLigO(BlM$*L>$5OHMYEpH(55$^9Q@Lj9e54?gA?`|233PD1K+_?Fq6pTWC?*0wFIHwG7EX^Fk1DqO4z+$7RS~f?I'
    '1|cOE@$(*bBkv$YxZuuzAU(7uqDQK#!=iFW6ss?R?6w@H%&vifxDhB7__b^?U>|f+9j4*d<d=jI_5<VNV;J8LaeM?~JM8KjFDi'
    'l^@f)4VxD$H8%dk`i`(6O?*~FX(o;dl7wo!ZC$xo&8_C@K^thu^Dl@l1#t1P0fPiY&zo-S81FP05RRAJ08qbHmSf-'
    'H}E;y9t{emC5+SbzEBk0$mV0i>kV>REF^C^vpUc{z06AWfS_Mr~r#Db1o~5vH;YBW+Hc;?ebwZ|M&r%uLSduVkcgL(p7oZ@Cus'
    '3e6Y1Km2sSt>g&4BrNa`oOzwkpR@}u>2-'
    '&z)UJLY?a)JjTkEi#F&vTld%P;N%xdVmxw1JT!y2+LfPM{R4(fi^n>4%f)C#8+i^VcKENE=e2^H|Zn%BO2=-h|j*>u&$W9Q-'
    '?s+7!@_kQGigMnh+sHf6i+R0y{@XtY!I8bp+Wi>`j&MFG!VWcM$-'
    'Mw1{u0GY_FXQ^nkKcVLy(O8|v^od2p{;mS?Zu^0S|pC(_~08l1N0Z~WC-'
    '@`!0w~*Rp7P>T=3p7$iA_{t9>J$R1WeRF$@7D2QQC8TE*^l_mM<nHtRZMEbXe>vi`_5=NA&vjPN7#N>wSD#bQ8&>@5$jr=}e-'
    'nRhy2?wUH>wwGafbO)BiJ^5uZ`9^f*9XAHFY)_uLsab`PW#p@@0h1s!X<w!>kY9%TkK#KzF2Kig?f5h&B8!$CQ0+ef?%5SXOCp'
    ')Xup{FZ>c<&ikjx_*F+s73B#^s~k8F?I;!JYt@WVt?l3smKuWgyoh6bm`1Du_qT{3rO-ZAFDR`ANZEZ09kOe{uY$P`s~F5L0PR'
    'hyG#^jsvlzytJVDFN2d@>LA8fRg9suBcC%;RJZ@(t{{nE=_}#(7JSF_T?jHz3E^~j>20!1ctNdSdTa%_abUNu`Pbc9EF|VW@GB'
    'jKs@Yr*8^rlLX|Ym<e`>Xx!!E@VM#;^N_2aUgqWpyz+RAA0rz_9?xJYsord#5+U)GJ#gT6RsmPaJV9_W|wR{t!+ts711iT%$qF'
    '1*olFU_QdZg!!k1%SSoF2zXBSEQU+wX&G*f~tLj^%wzAz$grmJX21*jR^D^?a+Ur4Z#sum|$~$9eQ}$-'
    'i%cM9iJFMF%QYMQ>$tDDALAaMMz46zWldaO~Sg*WgWE>=HazFQ_)dZmb4Iv&y5eVE-gYK4MrhR=Kb;xrJiH87`4<5L9k>NdB<d'
    'XlyKsYNSV17vmCb1#zo%V@Lw7C)!#>*97OH(2~!Z5#h;mz9AkVYfas%oidwrl_j?vL3Uz6(S^z{Zc`MQrZg_{yVq2b@f$DzK0Y'
    'UJUrv+ep63on9XY|JHtWp4GaQZl67fj&B4DN5b3bQKpRBOZ<WX1WK|b|h!>U=rD(nY=`NkwEH~ii+5w(CDA}~ZF_#JqOg6rbnG'
    'Z5s05M#01J%Z-'
    '+&u+`ut}2Q3=M>p@g9oSP3rd~A|J(ukq7oI^SxI7EYqo8fxv^<uSD&r$l}&{Es$akcF22)LIWoE=eV5GpEqKe<7X-OU-'
    '0QMrm$>cqX_s#`K<FLd<QV_KrV~GPP~LV3-'
    'B^Z78HJmI|7|1+YH4wdfckdkqNPvOe3YBSgRjc0nLM?9eOZ^XExq`+VVq#2?o~M^ks=mG2eY{=`5iPS)TV0*C!M*F9)TVQt1|o'
    'M>Q1wKfj4kYBa6cmt3O_bh=ybVrBx*WW<7(524_DHgO6qfi~mD06lv+!-P#q9HG5YtgUm{-'
    'p>PDIMZ|Pn7n4l96e*utG9xmYQd}ZI7c;mSsuYTr-F(O!$XQv3E<q&UL2U@eWP}NcYCWe@01r&*C`f@1VY$##{wJSf^u{T-'
    'pAWT93oj@%8CR+UQxTsb2ZM><ZQaFE!r5*s645j)d&WeOT*NG)+~DY)O_@0%($2g27tqP3JlG?P=jWs#(i^A$gm}9P{qCp#%1t'
    'T~6e(a(2xBq%@I7;#;aU3@Obf!NVls3fsQl7M?9e1G?JrB7A*D&@Be2s)HXX@a&!p|H?^?N7t7f0<*716x{)(FJj$pw+U5Z?LQ'
    ')J%&Ro}X5-Z4>ZIxdZsX#(mm`IP^C5v4~O+c0^VECmqlSwy!7W1+j0tmnY*)T-'
    'XmA}NuywO}c2TVq|rfyAVV5{srD6PhGZJ5a`cdAq5aH1{hIblg->8AMNL9kME)8Z5O|?=nNNZ9O-Q%TGKx_84<-'
    'jUkMc1l&~a=<Awdbh^w28viD?EquO<+@!it^xd9@+oEtVnvHaJ*zo^Xxu3{0nj3OmNDUE{k~D#YEtI@^^e^>S@E9b!>-'
    '5bv7a?NhzAp9O6iL8$J7ne<b0`D7`RWvVu?`e9pCzqV%H&Rlx0Z+DZaS`VoUPHeH2p^5Il@N?n=iNEUM|<kK&KAz0Z=i`BjdK5'
    'Cu?f*MEKXhtTNF31m#4=#qeY*sc)A7Kz^GlfaMG|)ON|pC4w6vE9$77$V^mNFM-Lf5ge9fc`nX71Hw|?ukS7emJ-'
    '*_PJ`eFeLx_4(9sfq9>_BHv<}No34Z9J{Gq6%NAw57feD9p$eclRd+Nnwz1dC|B(81Ds+)YK<#Wm`35x}!!X^&c^>yF9PnMBqa'
    'n*#De<^JP^T4qZo1%#@gY8NHE%VoKm+v{h{;Yrr;kEoV*}3K$VsH|DxXU0^;%gNy;)xZCQA((6t~8rv5ZQ9zV**jiBx$+(((=|'
    'v3x>Hpcj!#g^iVgBL$-hy3eX;I#Onm6()KU@geKT+#{W5Zb&VClNM=xAJ-'
    '5YeJWaF^^sr;JxiA|KGc{sZi7{Q!IrIPbzMH>i+?zl8<n}~&v>pZfPES$(5eb(!)6&G>CuH~*J7D7a1In@z^gt;IAngR(UV6p1'
    'Knj^ZTa&rX-)~|Y*lK9Vg!-'
    'HS=BzoPV26E?YTm;$V5EpqGEq8JD(WUmIyZ=j&oeT^B{@|vt1agW=~iyBs&USjSP4}WnH1M{XHQ9+-'
    'eUlLV%C<RI+LbZf!jvZdWBwYD0?V~zcl}@v)xubTAkR%zl0^v0gW#FfvTn02{H46E)nT*yW%c`M&$Z8d1(~<tHXh^)9+I3+Fh-'
    '#q4D7PP1u9ZN#ia&-{}R!^vv^kPl`&--bzXm^7#D-05rz$gmhuHgMK-~ncCbE*})^GkH()l=_yv`tu$-'
    '6L&9L24D`!Iv=ZfB)6!4Mgn%=e&J5IJ6N)!DnrbG+{e_*?-'
    '4poJ*fjWDOBI)S*wS}8m(UPYac@$hL0;zr%DhtCkTQz6m@HAPFA93@u>EKgR4`zH5lkR)>~<w=<BYMd>kt0&F+c2TSdg8mzxRC'
    'r&QVUIR<wBbjuaoV+WeBSney&V_pO1k_Y*b7d~hx9i@FM}0lh1y59|ay^(4`euBS}_U9r*si9y0r68eepwwV+d!z*jOIsB*kUG'
    'Wv2%u`lB6X^z&d^V=0S?pfjNDm?I&W-'
    '4lZ(m|8{Ph|;{>a8=vC(qwX*}aZ(U7;2+RP6)pC1yDkdnlrE4SN(RLCBiJav*{W?{K%g`JD_+&OYJQzklht&*Iy0G$z7Aaej@c'
    'v1JH@`b^_QK;bLmxM4uL#5@jPT5;Ru=<B%c1M1>THGSGK?}`wU_?UCI9+6T|JqhYE|Ke+2qNA8aQP+RW(YAAaP*YlRA(S(OA}Z'
    '`CMSA`GU!b(5>QC*1(v&cui;+Y64d5hIa8<HjT6A%kwP!=eP@Zjs_J<S`E)4s_~eA7=W8ZAO@n!kjt@tRE=B71Nyw7PMLYPYnD'
    '%@{rbuVCbIsFh{G5>@2AC{>nlX1hDw`JMsS9&&j!F=M(vTQNIYwY`Iwjxmm=|2FIxSOQ9m%1`m6NS$!al;NKf}^cli6G4_t$75'
    'n03xbBXkPGwaUg+pK3~zz%R|7(<S-y5k|+U?I_zLanEha4-'
    'AIr?=Ec}l{137tLo=T6TI7mdUK80asUITpGOs0y^cI9n+Jci{*KN|&dmSM1;0>n<GPcXoV%Rqh(YHENii&<apd^+wouZL`V|Hc'
    '^bw2Qwo7GO1X0j9EA?|A%~Tmsl?LErFsv}pA3b9u;gQpc;1Ln-o*~-<`j&?_hKFq|#e|8w*{hW9U;}AQwEPCzP-'
    'h>nr=TJiu$7xnX|M~ft0A1`t9FiqewF*uw@JjEsn6fN3moI5Y1mLIeF-'
    'om9qQt}tVH55f**QIL(UD;Zts~|*24x)G5<RO)q0n4?)JD>4@_B3cvu&|9q3%MN)*}urPz@FIE^{>7!%ZrEUG-JNND>8PwCib&'
    'DB%Bl8-ath7-eaT9d$6)t*0C=sXgX?wG?8899gW5kkw)nA^s1U7vCnW9FdvbiK(~be9sW?#!lw<-OTGt-'
    '~r`X8hh5e10fE!SfYu0)I6fZtUfZe_`t-AN_w%fRY+D=#a1^a-'
    'io`t}3j9e$P*GP5@&>40TSlAzV_j>9RATYihLAipcEuYY%(y-'
    'IeZ9RTIKRvDw+~Q}**8#)tqN%9{x3&>(X<*BDl@&rr3WMQ#jD@dl<#Tkh&Kr0m%I6=<jddNO6y5kNpdTFof0QRunAm_=EvEU1r'
    'M0DVJvn3iqDbL!g^CAmXyA$%YL_Mer)F_jWD*g#pWA;_YI%0pUzgBaipNZo)uVCm7poKq&ImavZ|8RErcpqDi?<u(xVf#hB9zq'
    'mp&vP|Z$wu3wE7=`D^dQ#qHNMpV6wq0kE4}wQ8YSUNipz!-'
    'kD!Ey|8JcedCDi3qvZoo?J0(0vGR}z~fDx9m%D9hq<2r0FmjlK~u%!PL7gV7;9;OLIUW}msT%JV{;JC3Nr&h6m;iC|>9+sz317'
    'Os}2EW2pZ6Z~UX3X#XX)%(_%<kc5jGqdM?NWZ%C4n}h$)FJM*^pKu+?6c}g{b&}rCw=Y<=mv>?q_kng~`3dEtXV+I}o-'
    '&<O^OXW_q25hR&Q^CvRHM(DsQn0~dYSZL%KccR#TOE%g2xHK`<%-1#d{P2AqAGDK+lR0+)wC*$51-'
    '<C;vOm>s<oh^nIxiD<BW`4AsOW<PKhNotKeLg_oYFyc(_@Q#|iKf?jTVSO9Rs)O)g4TUcAESX_TkxHqx`?TpPJy)oI5N?y^|$_'
    'lBRLas-DdG`qSfogekm-EE}HeT47xyikOl`U2u?M$Lj0J$BA>Ogw(u%%PUb=YF?an;=@hc@Hrwg29O0a24q~xfJBSvsdah^n+$'
    'U2fgrZ;i*CNCaynCO*cBb<jJsGku&j7Lk1p5Am@p2f%pHTOoGpx(WX_lA}<NLy6q1~~rRLA_fT5Nr50V-YlG_s!&_s^H}WU2F-'
    'E8%an9f9@o8X;5qkpXH*g*}gb=s%49oYuJ`iG|Lk#8`Gt5N*N@V_s(*@)2Jps*!H)BPB`RG@(ZZK#r(D`kY~Ez7f|xLZ7AEOM='
    '=}MBDYLcG@7^yS`y!q6HT~sYO8_hF75auDJBlQQt=>o$1}T&C%|r1`XaJeGyy_X>Fb@9x#X!!>acW*q@RqwSK?35|fV<G{xtG&'
    '~RQCxi+4Pa#LWBbGSjY2xhJdCL&5}@g$Arm%$bv-?87}ue|-0SB!R~=-'
    'T@pb~bY7$z{V4sn~b*S}KgB_s4L#0SsjiJVWCxZBrRw5s|S?8*}4kPG?Ym)-rUpQ5IAD<fK={Er4U(6I-'
    'je@$0_mQTgfC%jrDs`*f(Cri}$F`UL|l4IhkAfZZydmxf;QhMhIcWW2csAEiMR6_JUWm8Ja>Rffk2YxlmyB<Q0FJ-'
    '0<YsgH;XQw9rDH&d)juzGY4E+FLoo_nl0xd)_BgNd8zys93KXXt_)lU`+oBJ~;AZUaE9@24(oS-1X9t~-'
    'iFo^>TmfAv9Bp1gY@kyK{AfWXk3_@lx{=G-'
    '+ygRk$0SXf={4gmrgYsvf2&f>qP%Mr21aaz;yn;MrTv&go5pOpW`$&o1OO@3N3vGX9#`_$aS1AGBEOm1xE4dSwDT?wKikD5qRW'
    'VEs*b~!dMsqi#0rXj(YlA=wO-_lD>_!Tu~8Gmc@D@w*LPnuM?_?uSIQ#jyeXO8v-K}n6u=51+P0F-'
    'S^ZFp3OFOYGxw$J;^1^OO0eE8}i0R^8BpT|;92?Eilf&}nsGv_K@I!{N-6=ZiuoM6^ygB=EAmciU{JM|J;C_GO<%-'
    'Q)5TkAanvokPy1cXPgYU|9xx>k?@jX31=?Mr41NT)_b;|<>vp`y7!*jk6wpUDvq#qRQq2vODfm4|P46qcT<|9M0QK_)6?f8k+_'
    'Dzd{lz!k$jYbv*&pc~%w5=3CN`dH78x&TY!q+B|pA{k@w{Ue;B%8OES7el-ctF+Z8s!Y#+ym(z;ECFpl<W@+?mp)Mnl}7lNV4K'
    'k&-<o=brOMDx<-'
    'OsDScZ)yZ7$S~zU=zn<t{+FjAJi@i(2wPAp$R@j`uPKc0#ivrG}8Ss4oXV^=cu+dZac%%R(s#<pvdhn3N;Y(&28^1)oiRPMny8'
    '719elYXOrSbYf@9L&^Eg*rT~NRp>3HUB<rL9iqM?R%o$mk92$ctOJ^VM^<m#QfOZy!C*5{D57_E@7#>m3}m&H@j@M!MI#h2K`*'
    'Izuf#<}PU<@5H_Y@{wF4??urXHrqb!Ha`INAw((wx`tt2StDV3tZFD>_~Si1m0JA!_GuABthKpd$UQ!Nxd-'
    'x}mm$!s2IOnL|Le%5jLsTmM!WOa6l5TZ>3Hqc)z00|ZJ_Vi6^Fp_C`3CieV;HUXYo~N(B#icjjV)W%2)BtA=kG*UQvUt1zf&K3'
    'M<OyMkzciEO*U673@J+*zs3e`MHr2lMUVrXJUkPN%<0k5IO}9x9UqO;z0Et@?9<@Ho(h|E4<%J=3agAVB+#73hnzi<(GYe}FAZ'
    '3oJyI!Rz2d()^%N!luO=eEYS8K*l#5Ez<TDMCJOGWHckq)JF8rRGboq$k$(5m>9W?4rP5R~p?@ai2wu~MyvRnX)W3_oI%K*(&<'
    'DL-'
    'r^rC}MY3KiZEnF5iMZ2Z8$mcm9ZoCUqN3;1{S{gCoAJZFr@kITuc4rfN|T6qwR6Cpn)6}}sBhjq!ry0`q68(Db==`daH79aVRv'
    'qOAy`>DCLJG7R5E#avpaCtOS`W`?7PBJiT+Mc9o?`-EqIzpP!gZHJgw`|_i{e`jj<!^2)h`xkhWP-'
    '<DcjMp12}6_1QED*&;dJE?tE_7uf3Iu>0O;YjGY{x|jS4mkm{-'
    'MIlow^MA9K0yWn(up<g<C>9~lP@&F(ROE>b*oWCW_=zg_ehcyNSH-!l4NnsSGZFc<$OH5jz-cKD^rYyKL88nT~WxrLAh6G)<jP'
    '28iS2eV6o{|NidCYtYAR1`2YNJyo(Z;wruRS>`T5Wz>Mqxlca{V8|`jC}U)LwKzQ63;w?!FauoAP!2vJLd+Xchn)}1NROl96g!'
    'R4i#npXsbATMQZyk>zuu^C|BJjGy>&N6OxT03@X<^mjr^nM71PT%o5C+<6s{Xjv$_xb}hs5642NwO&tMM7<7zR!4Ly4yu>+U%S'
    'U<a(U>rczH-vO$#2K$rx1dg%G-'
    '&;M3u;53U@M#0`w!Y=mT64*lg1A;Ls(4g0i2wOx&YTv#9%$@PrejS;TFBodv8e<FrR5)ySOrB}k5%zYV0=da;?JR56QMK#r#9@'
    '4N+F(pb)2yIP$>?)w`=beO1=^yV1gxoqXJ@0L;m9EweVrh~EZO-;w<HR!orL`zSL<ClrEkpE*vYJ`~x6s1=7Uhqc-'
    'w9g#AU_N1ri03%a@I^>ZXT{AuCj31BMreNmWA53j;>&UM<7u;!3h8Fg(XQ^qQZ2KxTlA0gG#dei=)8uVsf8e(g)e;x>0taQSXj'
    'bOH_x<n-'
    '(Q<?7x%Em1F6(O^2DJq)CN+KhvxzMxP~X;Y<Gis%)$Z{2`LLe_6SDKLy}vvf298hYe}NJC217zz+@$lrpEVz2Q4?RF6x)R1Sb;'
    '+Cz%*edECZPk+y7wmg`RE;|W`l06-K{vEctEy#S$-'
    'hc<wfuv<6SrAc2NKV97qtK_1CR)>wqHqH1({=;2<#iaF+b8oWCpo?X?!97k~MNL8#EkW9hvJG`(Qu+yEo0bz$cD3TZ&zU%2qoL'
    'vaHl$^8dskrC-MZ<PpPjc|_`O2SFTcdZ<VQX4e0X5HLN_RF-Oev$6EhmmaA&7o>lhAxB0+%W=i8IjozwS+bJk}$sEMrUP5>Tc3'
    'zTJm+9Y!#r4tYBR6H7xfmHj}S=PkGk53RKLdMA2=X87?>MowOUz8S<<yaa4tUpd>0d&bc1dn7ElknO`19WxQ1wQ9dqMbRGbS)0'
    '$-99UOi;T$-M&MSloN)eSf#m;RBkcq_#81^<4(+R^WMbig+NKXR3Dn9UnZ1o5f6xQLp}JAu>Zqk3A1)&)^Z5|`Zoih_%-'
    'E7~W~BBDn#1!_@>$p1wlN46)r?_ye}|NM|Avr8DN5H?u}(Qkj|1MaJV4lFlK5T>t(zFb8)tKF(5U<`TBxsnrfO?IE>#DD$gHA^'
    'sa2lo&|V-i0261bte`IVy!3k{=mQ|F4Yd)y9HyiU?N_6rcn2QP70m`N-'
    'UlHgp&Ivgje#i>2@r=A!i25v9!Sg&(6<EdM^G9AR%8GITH7KLjqCS?)%+um?w`px<-'
    '8W0i0YGz>(a)HZm|(OO2BC8Ikko<6WO7F)2!0EEu6y$9?>&0lmy5vw|b?4yG3Znz8D%C6lj-'
    'm{r|I`YAESSi;D3+TkdRXXyr@F9(i{@<T!I9GMkVs+bd|7#fe2uh&&p!z|~igpK25>#s&lp85mRqqq9_U)`X0joOycwbE40&s9'
    'afYqqt$D>j~-{YiG=IvcJYp2X?`k1A4crlPcW`P}~L4Yi@1W+(c<!ir*mf-spW0b<{RL4C`1IoDKNK_oBdnyM5K@oFa+P$$g-'
    ')(5?~Kh_(-'
    'h0a&&rC86i>5aOGw7~UKQS(On9jT}@L4wfi{v{Vu!ZyB))1bzXOopEdDWcz|W76H_6`UA=@+a*WAp2p)S@Q@a_V0w#>7@!R24X'
    'G*SfcrGCTHU6RnlNKX9tW1Xl)xZ9hRTe;+px(c@6Kxw%Rlr5bb4GeJR=gsO%;U<l@<JAK;=2ROXFx!BiA-ea`o}Qw#U!`JJ$5T'
    'b@|dL)p0)b4qw2=#PX`TvnxlSkN3x)V5hESCT<?uXzkf#LW(E3=$KYC)f%fJOt_7E4o3Ro;hY6DC@YbTR?v{ttRZUB63cv3%fV'
    'j)P&M=?79~!2<ktidL^w`)A;;WP`Vf>X5?xRjW~u-'
    'PEiTAc?+<TF5(W0D4j4>&vhhAPdQ;wI=<iv0TQGh5D8`P&!o;Yg$*M}vrXAHF;$faI<HHb6;+DyWyy7_`BbffO6#8m91&uC5Bv'
    '`ZGwsA@PQ3*#zeUt56JQ$xS)Xes;Co__ss>xPhEsSiD%hs5Eug}r9YU~gFPbgYnCESV*0mKpldxow0l6@9K%jm+hxU1Q@zbejp'
    'd7~)-h-@>^>GYoOh6(wlh4@JGX41NF)yN&7%Aiq6uFo~#d0Y0TJi8y9sRoPGYh><6jRvA1zOXeApW4>kF-'
    '1I%$gBME1VfQv@Jw7nxiH%*uJ6+J(9ES+uY-'
    ')E^o`dz(o+!$9=GPR!jybYH+XhBm0;o(|GG94bNL_bh^>#Sf7_{g&#Z`E91`IU34T|pz=;`1h6>Kd&q!0pCQ<U#JcB<dZ0PEJ`'
    '6xmp88_NG*;(>YUP=R(WrfOLYc7n3%^J3?oUIDjp&~Degz`nkb>tn;DIAmm)E4@brle@DM9WH>iTrJKghKjGCED)mv>oi|OHb+'
    '|vk&n?QB8GmF9IRFld%dFsGvV}k5YE;8t}2Kt9ysfKqoJ{?^%z%rQo4y$840@IZyxxL#)=p!p#7PFm<%szXTMxFR9CsxDmizwk'
    'V^)K&eIq{~veDj#&k6>zFHlQcVP<CYO~`-?|5M=Rg@qRk50nT`)MD#q`&^lQiY+zDYJ7YLq81dfBW75(T(OzL5-'
    '~QDd)!IP#syQ~V@1&xvhr%xCiBlC&|tr}l|DO+ri^HNR0CCBLsUXvFG09-)9Zc)O4cD+Ix2zcgwgm<4-TLcu)clO-HcJ3Ut^4f'
    '11~+NH3CeR$EVnVAi240qwKL*7twNz)6uL~AZS=)~!<M^J_awnRST+lA?&HSwvd!MLW2n{HpqHSL+T1p^ocesd3bG>&&<RX|w~'
    '@<wwtMYK;x4QL%Qf-'
    '@CXN1#GLzE*FO@dGlbh}+z+q30P{$~4j2v%Q7A&p?g%h59zqQxtt^bM844ZYa?S%lio)c=YZ=2+y_eNoX+6H657iCc;0QL?84S'
    '?KeW@Jpc&)8<Kr8(w*mN>|#u2#^Q<Ir<C*jAS(#@=F4d7NL(p-yj|5vHOAo&M3n{TGFmbk?BFD>7g`qbz___!-~C5N%#ha%h0-'
    'SJj0$g>X*jJuOv0gAW;my}b>9_1CXe=ET?R{A$mmG9b%|tC{;=G?B5vXOz}VNst5fD)nS63040^s$xVg5eRJ$SyM463TeX)dEu'
    '(!-'
    'kFv%6&h0HzEU>XxOr&lS>mnBOI2#88Y@YesM)yu?)VQoYmSB+=UCt3A7Mgca>@$0DKWb3l6GS9^yjMy44r5jI>p4Z%JYO45tE2'
    '6SQG<XY`a<N?2NA&*fTGkb-<<FW&lz*xu3>g!M7ZFOS(&myrlzsUbCY4f$kTtf|YVH=K{Z=geH-RqM(@V=L;bk)vrlcA=Gn|^%'
    '5k_cdd<Y84C>a*=%Je0xh=PwVY7u|ov1MqfRrkZ_;gfh(?mS2Lw}IIui}n320^}SNe?#NIc~L2M_*0ml$5k;z#&jVGZ_hk=u!e'
    'T`^(&$+3bs+Q0spTq^9#uqT*$_=X-9$a@LTU;Y!0j@P7KoB*Kp2sJlf<q>Nzaf6*!6}q}u-)-'
    '?J5u>uZ#Wzf8VXGXJv6MK+A>GMZl1^HMRWQgFD%dnc6+Faf78`As4{46t$e`RiMDhuO#e;GoGq{fJ8W`|!y4EI9O1#T>e#Kk_T'
    '$v%Mj;L;$z~BS@&A0n(+Rj+KPLW;LAl)Ouvq35kP$qQz<Waj!Iuga(F4$n)UV;$oH>=@mjBa3m2AO;on|(T$*HGL^y8jtDU|rp'
    '~GYj=le<&2|~X2EmAMmnaleu@G)vX=ZO8v0`{a`iz7m0#HLP(N}_^nGnZ~#Zf?6W)jXI=9}D<G35z4c3q~8Pbb9&<ix-wisyJC'
    '9w5izVfZ^Novjtk%dEfMzb<4|ven(f8rKd)$l2UI_u=~S24+=Q7>SqZ1;I|PR?_k>wg7vQATsdrvxR9<`|J-'
    'wQ!aO*dC029PT&At&F%oqEn2j<s4-6y!i;KSNrA^m6^a!{z2QxTXT#+1w+81d-'
    'fh^n0!LH%<YDJoWXioEa;r;b@TxsnFb^Le1pMm3_#rA9D2e7>Ao!Crp?(2}+2>XlFt>~1<U^y~&?6u1H{!Zs=?UEEXafPFDJ-'
    '=a(BQabWtM<31xW)H?&7|%R6u{d6t%Tp!FC}H!o@ZlTX`;se3Do(oN?hPx$==Zl}G5Du|21ijLtd8`m;{%bs+upZq+`f4TSH1R'
    'v`8W<2EfKg5yhdF~9cK#Ldtutr#s)@hU-mQl~wlJ}HXM;wl_)RVU9JXhlgLMvFOF{_;vA!%zxS`l49=JfTTYURvA$Dl}}L(w<z'
    'd(nMFM33*p(s6|2^w-1p%Tp?iOd_x_kv4^CRow;S&8Ec^~-'
    '(8W={z6fttxrPe|3Z5`$e%v4;mf63%|9#|gdAnVEU2t=&o~t0`_>yH19v_uD-14#I-qOpn-'
    'FD!Z!)TtD~Y$Gm*Z_A?XxeT{}JIzqjL(Rwy+EjF!uXbf1c;zqp@Yc#EL-yKG2oP5d0a;X(x;~hDL63aXS$WNYGI>ZA)ijlL?8L'
    '@>(Php;N?_8QB@C?!_KJFV9Z00+{6H+8iy95BBJ#;1cuJ?;>V3L0hs*l%E9Jf%`Pm17qT0W|NY8Fs6n!mh4=*j`LrCyp=9Yo#!'
    '9ER%@j6l6kNnjdU>J)=kD)$UIVyOkPL#%UMnv(Ao>O-'
    'T_#rulzXYU|!U<b^vH8A(G$60qlM7C)dK5#fL=Doo~TDwu1L<;#jqMO!w4n{VU6gpO=x{_#=o_=N$qE(22xm$4s*zo-'
    'UPihM%3;h3B5f95ptZn($64XT(Tpe6=a6{-12h47`=PX{c0bj_S-XQkPu=PP3-'
    'M>wK_(24#5~Fe*&0uQSg@vI?tHi|`&{jB4IqSREDwSE9ilT&D=it@;g+?N1vw!1=`-qHA`O5@v^7ciV4!{MLb`<mexd){UC-GI'
    '#0l!Ayu-'
    'q)ZL1z*})XYWRCsHhm85z_ct?S3HUwm6PsHelt`fUOG6Zo3%#QgR;&@kzh0Izhu!lcRXns{jE(@q@$0M=N|q&)g7@<@-'
    '6OwewQ}PRKcQ_8L&#_==rIoOUQ)Pa39uejD6ogRjNmMG^R-ko`=sP&=U}i7IM$L2Ja;$j{V8wS()G|EH+M=-'
    '_!*TxM89)7WV1YMzp45SC&}10R8G2Lj+MxPo^Lv^3aKb)Uk&;H3I!{;FO2NE$>_R4OT-6E1qV4C(^&nwjoQB&ReB&RF~c-'
    ')A1c+(ZuPn!ur5-u}DOcN?DT<2o&@htp{PSHA&f~hpA;EztaIYxrIn5&pzk?<rVTEz?-'
    '=siud%$9a6q(wHLwt5tE1E+KEnPkK<dKA)+BzpS&}HFkp0FjoYQ?3fFYn2sTBFIIE%0fxyTHOAK6^va3P^w?fZvGGfNLJ_+%BY'
    'YBXm_e;C*+sJa43cV%Nkl?iNJlS!$Zp*N&9U`y*_MO<GXB<7V9O|O!p=J?XwEfl9S4p5$fpY?fQoeM+v}qO+yTtOym+=X(dZ|x'
    'bo+>^?hQg}ITYWi>1uUo}u)j3ej_ouTQg67Ch!VJMDsShuCvoVh{;9pkh|v-vX!X;-'
    'tZj6?iWsWk;TJBi0`FesJ@!>zmn0fB9?aqBcqe@BRaa8h)*5Q*jVA)`iMLN-'
    '$&fTg=|JDT5+h^r4D*CuZn*Z{PPB1Jl{XBz1`0g&XcQ;EmvAGW84}z+>^#|Gz0&5ZC{I22?hq&!o&+Dl$o0QOY(`Cv6Qi=4?J7'
    '^3&ZX#4YNn;$?|%@Ae0l81{o!=1a7#dr_buAPyi?18IVwC=qz{UPk%Y-_DnZJ{CHRY4w|rh-'
    'H<e)3L${8Zws|ak(O)6g_})>AX)D5wRg3JX!;p|2`4LwpLYR#Q4)cabXAV2n!Y|2vX+Nb)uO-nL0eUADvD<x!YsIGOStMhFtdr'
    'tB71k$Q9m>sM-(Oa!6)(9Tl-'
    '3MvBrj*X6U1U6NQg`efptm*iHoQX>+U51;OitqRX5UUX3nr1Rrv!o<Vh3)`Tj)swc}_B^txFyoyUUSNedvx=3R~W%D42LQ2(B6'
    'i4`->Jv}e&_u(Mh(1o36&f(o%op6Pv!PUHV8PR%uz1zwT|E=%eQu-otTl{bQB0ISEdhzy8yMT9QAlFt1UQlfk6Z&56=@)9Z21~'
    'aADWGibl0+hCAkI}XZOkJw8l@{Rsn*tqv%HOtia&#JCK6tmYRbHVVq~`K5U78uP4&*pb6dn{8q(2#jI@RZPrIDIkV>3Lt_K7;f'
    ';);Hc;h=3SZ7aJI@BDj0S+<Ia#@6h*_KGHcNL+qF%WD~9OvE?FG^62pGEJfF#)u9hOKHJ{wX^9^GuYEalBl|1hZAvn(D5Eroub'
    '1d8y@GU+oh_nA2{VL~n8FJ=r^%)VNp=IYNAZ{uOJ#OblDAz7YG+p&0tON$DbWo_~O`zhJ^*XK&U0L8DW;MRkqNGxVdguL$N2w|'
    'xjs$Uv$*ke{Xn%W#W20rhg@Ou3as^N6pN?bLXB>8r7C3`mffOV2=`HVQ&toK8UFaeIunl&Zq*d39gvw<_&-'
    '8v*$jA1G1&|LEK>&NALO==58R>2();c3wZj3ZfB(dkd)43pVJM?7GsA@t|GgsqJ1laV2GXrTFCMHm)}?19Lj67hr;!HP_QRT;B'
    'PvTMQxy=W_ce+;&(Sq>35SMuDZV>JF`&@oH!Ytu!(3zm%mvz-A!-XoD!fdLTb0H-'
    'tsoxryZq19_Bi*qR=RTtb)RMS!r!$OiDG8@w-uxK=l7he#VjFt{Pvj&iG#-'
    'A_$jb6GD#@k#8JckoX=9E`T&NZzMP*euR5>dRa!{C}@+Q+rPB?Mvii=ECz(I;9004@7Y1yzXBb<ph<o0)H(_$NeGJ1qAFLm@7H'
    '$f+7How=OQ%`cD`!G_#vve1`CrY~Y3=KzW=RN8_2f5-'
    '5>I#(IIJm_9<dLvaAfcaG$+ZRaySHLA%_#Yb%9$!R@OZk3Tvsw@H5MUfOPj0K1wKtOK8!dA>-'
    'j$>Wfl;GovDN?f&YD4X09Pq2A1Y-ICBGvU;I0n{_@{i!bJ`Tu(gq(dNFO)g4a%+7F-'
    '!r8tLxBvVcSHSJJR8AUjN$V9_8EoA16Bb0h^QScu7<cgW;dUwBb@Q4F{Gc@gpKtJvlq8`)SWFIFL<w%E3jTuiT1zHvLZZ1`;e^'
    'qqo>Yykii5-'
    'euql7(G8WK&z5W=1dDU~zSVH6Mijw`TprYdJgl0&o3O1`s~hK`#$@cX>Xds_Fl*O`a>LP9qRH}(cZs|%T9M`b*M#M&AaBOhw}>'
    'N!xP6c6cU}6c%6Z}Mlu5WfvJ&Z8_D)?!y~}tzp5}FM@hlz@wJ5ODyf%37kU5(dSo?K(Xk9fBIMA6A(kSY7W}?m$DF;pGDI!c%n'
    'Db~l+I}JG;v<HuUBt`!u^*CMobJ*!Gz*@+IrK?yQx~$IU`@)3A%;3=F~f*g^fRrR+Y_C=9)Tv1u1bZb78_?Xn?@=M(l7-'
    'cH1x>H#C>|R(MR0_R6}5JnJ9QGObls6^d>F~tu6Ga8ofXD0pVg=P4dHszyUWkaaY9fpW}4E*=&Njfrf>zrV5=w+l)L3lLxZCee'
    '<H$@?*t*@Eap#W1wW}PYV@=CIx;4Ku6s)9Uf(<sfY@%qTnK4{CHfv$p4zOf#>^Fa=yMjr9NjgNm?~h9*fG34iJw5qvphYd;%~7'
    'vwtI04x?~bRhnY2!xEeI$y`X|tNoH#RMCx1HjMSs1%0EE@H-'
    'epWF}t0p+p?m8qcN)x|Ff&PF<m1x<8Lr6<|gcpK+5KrB0lR;RgT$wQ*5J>f(edFUjHd^6)@<1*rl+hQS4~1w1EuJHY<4EEJYeC'
    '+rkAWj#9y@`28<cB)I=&xBMFHrj$>Gj>H4c~BV|%b);NnAr65PhHbUo*-'
    '^j#Pvv<7b{CcG|v~S^Bjyi<LG_HXVxgb{NpH`*el6A)NhMUKd3~yG;0U>8MkL3g^#!P7sf?Iig5(BHZ9Tdnx3x~&p1e{-'
    'SW7tQk}$pa>+f^pUGP<g8~RqSQw%rmhI7D_NI1uMA5vZfDZ_J-'
    '9~#&2A?6YXv2fmKX?;mOU7_#c3=_<n$s{^BiIaswsd4l`0dMJY<4T2$r0**GemvE4ePWBw2>U8b9E?~p526jN~kibrf^*B={v&'
    'Wn-'
    '=(FX4Vof4A=J|;+g6B1O0W{;1rcWAXd4%OqP1yh^|ELt?+k=E5Q)`OAeVLq#>Z1(UCC>?NqQxf@o}+V_gv#eeKOkek24ZS<;$9'
    'Wuu*@4KWTwcI&_<&`blhOSxAjNhw2L&Va2a1RDAruO=w0m`>bI(Uvuil&>BzL1~;l_$dg{WfM=(a>HXB-B(NU*gw-zP3x=STz8'
    'J1%^lLPinfs&O|fPNDkpN}QiV56U%Y_sv%|@*rT$NJ^{TTH3(&As#*-'
    'kSmESG7PfaaLR1LG$xp|cqqu+NMd{KirPi?{e?&%K>2sZZhbwVJz7+{Pr3;q?5QZ9w*o}vD7?)S$X8)~tJYd>>#8!}>T3so0fD'
    'CtaKIU%=>F1NI$robz$c7k7cMV#x<5aikn3H7$#`5sQQkPCS9y!ZM}2GC@fC+^Eui=h=Wmritqe&u|o=WYoq+bW^X$AQj$X1a&'
    '=LM_tx><{9X^UhkW8C*cUPalTRCux152?;1x8)1%_IYXO<F$+d<V#G8I+32@D258zK1`rWMI|JFhD4yJ5OXOEz{Ko0>EPujnX|'
    '{>qPq8B=`zbm)P|`cEOj#~Gx9cSieh)V?BOJk&_A3^!hRuZizSk!*F5=VATHJZkLzoY49TxL-t4gm#DKBQQEi!af&L1fBd5-'
    '<$X1RM}LNKlz2P9?pu4+X3p>F{SnUJZ9k?wM`PZbeMe*KwY4tcrVT*TprinA*O|Jx%pW6+a#T1NdpL=eLQ;&=|0X^Q$Q3-'
    '4)c0;7gv(q|k{NQi$es39IDTFR>HID42pXSOjn;QuQm=*^W{)3^w?)@aUeH^FVRN7lX)0=@18sGNs?=YE%DZp3ldE8EVrrG@<2'
    '4Qg|eh8<YDW_=bV3$-!t7UoJ6Wz^dETGJD>qm2Aw`o^E0{^i$3LnicZAP6OOEJ82Kt-'
    'f!wy3Z5Fn|UXo>513a3^>J9sLW!=&csl)tQMcUr520~Q<weT{1orCFlc;0kBl=r6tm%tdLPL$LQ!&<4us!ESQhKE<8c8<63&Dh'
    '?*eod4`XSR1^`BR^`$l3QKqmTbu3jZ2uU^BfWEwP@EVy*@YKhI0@$c`bLx(M1crD0lYwAsqO=SrlwH-tP6|-GlGffbuD-Lc8%b'
    '~pselE{;}MgwQhuC?N@2_#k9dGj!mCQu)Tdy_q{GSXn?U(6G)d;Ao&KwnVE_HUx%CxyB!`{2<}yVpZ+)wI<HcH>YxrxH%Yx}XN'
    'v{(oSTN9v)W$dfSX)4b4URAZ9JNs0>N2ueo@=tmgpY8>V6{%Nz8{_dr%rKNw49OEjICK$R$P-'
    '8Vpo|hK2VYax%0AMmnF;XVE&gF=zaVZOH;&o(mDc3kE|UYT;|j^c@aNp4cl+@EDA4DwtS5jtnh)TWLNOX>2<EEx+tk!Qm55GlX'
    '605677tM%8{j7OV<j-'
    'I`{=^4y*;<P!QwN4v^wa|Jkt0ETX5iSlcFuZzU(jkHHcC8+VcvOPSlSeQYGziGuxa4Hb>dk!wV1$gZiben4-'
    'g5rRE~pD23m6aX!A&;!Q4LV#JSL{#?WDA+T6*uB^)pbx>Ed>7)`7DJ$AH0ZWH!{;AB=QAOWYSl6})JNT)O2a?D-'
    'U+#7R@qDW$ojbJP)!b1>O0{>7cccc%PuvilZX&c?>8KvoV@{7VHD&OWImezps==E-'
    '#w(EW1TrhX%KDT3Nu|NR?9GSS6P7?abj{k-bL@qFy7WWJ<n*+J7w3F$L2f^t9x+>6I#Sy$D-'
    '_&EoAf1zpERKFc?zj%OT&IBR?3w$UYq=&XR2QyorlCPb27Lp_r?~gsO-LsYBT;n(4gG>Ta6)BI(4|b}G@B&kqMlfrc@ErM=I8*'
    'pdq|6CI+!2?^FHvg3!X{DB05L1VN&jKmK1Jbrl#)46xL#37rasChw@_W7WKf*mlat1!A9zbonBo;|73X&fPIOmcVI)Tj|ZIhEb'
    'dL^m@=?%RIe7acNu@&dtn220PqZ&pMk4KbVn5}VOzOrTCdC6uC1rNC0JU-'
    '1WAxK@JRuL}MSl2fy<!n=l_vdBknr}BoGj|YMaj<V`q+$Hi25B41#*mp7qlP!{lQ2jJl{Aa3g&J)G^-'
    'fLq3ol<4!hBTtBm5T0U@PU-'
    '`I0ajje`*_2^<)KGx8@q=DQ^>z4Kze+t648E2b#Gh)3W&%JIFqWlS%r|D3wyJgXwm}i1vchs<siMmyB`x>AcfKN$hq&@OLu)jv'
    'dFf6~<5|C&N$aFRBoa4h^?|<~mVhkc+m(c3`we<_Q2Eke_S`HT2~^0YBE#r5X9MS=wJfR3YPlauH?MO?sy@;;vRXK}6SswwikE'
    'j0|Rfz0jR=dNu5Y^u>a-'
    'iryBJgXVm<RA>FD4s;HbGGof_bQ=H}BZKL}tmTw;J(Aqz?uI+j>8AS^3i3?5;a@q~e(x^V(N3HCBd1t9Q>p4WXpJV!?c?J4PkP'
    'R-'
    'm)nIiSv3Z55h4gb+8>p6W3sZRUr2NPjJlk{^0Q=t@cS9#`_ayS;`QAIsAL*N(7ZMmyk1cm?%o^X0K6aG$t87V!MeMjdT7y9y$D'
    'G&rZ>4vfG27fr^l4MePH(BDWKbUqrJb{AIid_TfYZDZuWUa!C7L+=QwRi=ZA{gY6=Nc!Pu4{T>LxwCj)^U{b6BSd+zK`gOPSz1'
    'rO?Dmyk!KD21SwuXRG#WEkhjL>o{Njc{HuYVOZ7sC2F@Qe3i{#vM3_erov1JvnJjvx1Bi3C>o#b!BhVhoN$>{oXTd%p3h*U$#*'
    '){E|(ZBa*1`gh!h>*c8L7t&$(8x90lhna`KRCcj*-'
    'UX6%?T2_cP4(Xf`=<XQf=PVb3&a$L%x00gf!kB!Jj)9*6MzvQTmKflz3zO`!>&hDV)?V$bt3TUwQ*D(8CRQ|afb2UrA<fXx@|S'
    'ny`${>Ec$U$^I0>9>Cg1Y4<~`ze`4@WV4vbp_L-$Y9uUO{#tkv;xr74+c_F|vjpl7p0b2-'
    '9+n#$`GU^SiL1ryk=P^B!T?FR#3NR@-'
    'HEk^tBV&alz=WXq9OgebFVF(NdlS`bfc)DH3(tjt7z36!9d137*?Ff<(GI_@>t94LA;Rabw9`E_CORqT~h0DV>jUTpV#lJLM{='
    '&*0b^I2pk5gyHwuQIpcu%j{lh;$<er)LCELz!_^Y_9}@ZO^{&mX^#wDM1f=wsO9Z1Vy(;jL;2PVRbqSW$ihtvC;`4*j>Iy3!g}'
    '32x{E@K|%1%?(da7lGf#{R(WOVN-'
    'Q!*VT<c?A=o3zEi!^qszCbN2q7{;~{O2w=H2jbB|#=?M=Du5nQappAR11jw*P|NrQ>8gIUw^GMXIcO~SFJqm)YoZF$;xKQv(K0'
    '_mM3QW(0Ytt_cwEHEITc!E37>*$-'
    'oEy|8O4@akFOM$!1DTCzEXw2rw+LK9y<V6Yx=0p0pgcUiXqbEsa#aC|6J|`%}#QrV>6ZX_wuHQ<7NemQq#*jxS68-'
    '#nLet?XDOlybq<?ld<UxsDzbGp5(1gBfin>;=2kzaM4_&W!&oV4;i_!%vwv!Dpyn4BLH4Ur9iJtjGGvYpH(0^Je@w*^?dzuom7'
    ')|^ZKCrbWLc4&4xQ4gL@9Kr8c!9_glJnz2q2+2^!wl%{zfN_Y44e^UhL-fuMNBsz2`s2=nSp&92`)vB|C8Ofy8)M?Kk2v@*l(F'
    'uI!=?s{}Li#pO#RAcrZKnMawaVmx+VvqShK2%`$mV+Q+Xia_k-'
    '|vI<)@yszB`#^NE<;!V+?{6uiyf`Y5+nmRc_tiS#5THOzu0rGzNAMd!_Wb`B)?tC#fOz35v`e2f_<TB~WKW^+4e;U6G0uuLWBB'
    '510TO8mTC30Jv<{8^AC}J8&r9qacyGed8L6z?Jg*o;z`)DcO)8e6a9MERk(ldES&_H8fQ&csi_Kn|?q8TNN!$TMotVhZMjUI0~'
    'YSK?w`eK7wV;2NwBJR!pv@WrEmJFR@Ol|wlOf4^TT(R3f6!YTZ2+<q~mj|<2FQCPA!*<xJ*rAR*rwaH>Xo6YbS%k5)J&?F%yKt'
    '3zD8sT2CcmRh0DuQ_%E7e-HLh=8qunohL+1XjcLemxU7RAB-HWQxa(<AoT(>QN+sp}YwnkkGhxNY5+>#YNN6CUu7gk!gv%!04x'
    '`EBs++Bp@%Prl}TH(s$qL6hAZ!;K0wIUsgN%8h+x8mhvG;6oC!-'
    '<}hOjV`*G=Wyf!LoYxyRfExI;8D!_gl1Nt&L1IDMX{ZE~C#h?Js>R7o^Cl8E@RFW*hH%b_Nx(C^rjsbhzqRmy?bd2%-'
    'NH7!EO|4`ARUK1H1`u~G?*D>^0IxvUQNF*i|iNI&ADc06~>D^JiAznFbAA5XsaQBg6dUu7!k8A><I=3ir<y9c7S`YU>FVM#&Od'
    'mnR>`Bb$n&Yrgw1BN%^I0?z8V})g3&DH&`D70>he-<@(HN}Rc5~Ei<PIF>Hb*Ps;`Wbc^EPwivmDepZZIxMyR;}FKHB-'
    'M~L1t3V*<Cu`sttr{!BIqbRB%R0dB6qwRx!4I&($PJxYL{*mq4z1l7NF-qJ<4TOe0je3xsQ|OQ;)a$Pey-'
    '7dx^mBz&!ZtLycFC#w6N+U%AA*4Cg|l~bG@(dI3JHtCJM{{@-WjZWj%I3F?nUsdTPPb_SOB+HjzP?d)xw>cYm-sY&YRD3q_X-'
    '65#3zz@v?g*4~t(`JBtzRpUPk|vLQLZ7826lK=(O#Zwb&}rz(Bi;V-T$Iny-'
    '~N4TWvwDJJ%HQjr>+qtiyV6l7e#ovFAKUFkdMiKoiOoGNAI|*HIZjWW2<)d%N=M>=(&=Hdf4MulZa}1j9voS1|~RL|lC0!yQ%h'
    'a(K<c4jliNhd_ex!1^OOM2<=+i1e<OJyJW^n^H!>eVRF~n=3CmPkT7$!;R-3MAeMxdm`@1-'
    'G84H{P57A1aQT2qwWqei+WKbhRY{8QWZje9CZby9`0)#N4a9MJyT*T&o$y(k>-6WXW+e9+DlpSsfA-'
    '@q)2(mDZ=b5QlNdo8<&dP4ZINo$qgz>;rpdBMbFJ;{^_@dL$@}JoI(Km7G-'
    '<a(!ylK;==?t%+y7@5^*L*604I%?|U7;rEiM>eFpvvt~TRgE-qlvsv<2_FbWtQZCyo3<&gNk6GG9J<^%@23kbs#Jy~?__LA6lg'
    'NT07gs1Lm2sB3VPrPu^n)`3b2dyhhRN%W$ZBu3y0l>YA5djc4VL;a)oYf`>d-Ldttb=AbNUVzJ>72kxD&F0_3-mqmw-'
    '_U`#HMxIHamp|jjJQoURa9#TTL|SRwM*(utilF!m6U`D7y&8G=(k?=vV9*-l|>^`^$vWe2a#Ai4%uJ3vU#1o8}WqIo+Vr3OhO-'
    'tDxobFgbvkg+%gxh@5YLo4JB%1ZzrHI83rh^FPo}#To0k>(<cs%?HVLpoGLV(+V9vP_=;YdAn9C*3#k>@QN{h+*s^Cwe*$Vq)X'
    'K1f{?dvrhSk&9Jfc2`}yd2F(R67vfz;>onBLItb0mmU+xLJRc3(BRD1B-@rZ130=N2_p}Nw=rKAf<1AExvWP>Wo<)!s-'
    'dQ}|p2FtTy@y9`E6CubGuX=8nRQ>96nD*QR`KlEkQ@q$o0vKK4x&1A?1(LSkJe0udzT5*%GILdL2yTaoZElox)KD;hd@r%8c&Q'
    'TX!*AT)5h7w-#QhH}zDvydg|gma?86{G*WmbePkR1{D{8B0+QgOSg5+&PTCSRY^;m^nc_tpULC|mnNq^y{#a-DxkiaP+G$#7^-'
    ')_h(qIbRG+T4EJ!E?2LBS||uQnCeoMA6-?vO>dkLEasl1NQf-'
    'EU9em_jZ{T^Pd-q4iUU~)^>*PJ~owO=~!u*+9?o44MP=F&U6XnYcFpu6B>uJCtF5#?nxVxn~dl?DH_oA9uTk}eLWAG_ec-ge*&'
    'VTwyrpS<p>a6Td;y2wK8M^BMBw_3V?cl$^A157KEZfc92%?hq3eJw4;aO(@^8XNKVFzWOqH>=a*+_j1~5qZ&s5SukiQz`t?#$E'
    '4s=GVQ(6-0A|bb=^aA;LjeUWCkHpul@dZJRiB-t$(H{a-'
    '5X9q#tU?+S!*5<VSwbTL;~kW4WcrmC8p<*kg4ByRX2Ha@3E`_rgTD^k2W`-'
    'G7|R4y!}UcU3`(6in1PMg~2W5)@h2uzr*35SX|z3(tT3T57GI`?{S&<@pQ;yKyn9!p*g_Q!`f?2jy%LQ<7|9q#$aUWpc!i}zZb'
    '&yU5G7rd&Pf;&B}ZMBF<Z^xvlVj9jq#zSDQ01{ztZmnu2ub7Fo@9HzPYgj0e$~Tt3X5=<xC`#s+kiF+;AZXd<i=Kfg$#!cHo!O'
    ')}`B&2YI0+T~oOJmj~d%ax5}Lx<e)S>Y#)jkr_LBjdL&=j}-D7-'
    'v+nkKnMW5r4z_bB}tqIJI9N&0^(cQ?~6|e>skb#3JXOPgee^MIX(q$1V?~r#;twPtg(5Od$qOa$k!W9IN}MVIM01?MFqMxWz(#'
    'yK0zqn)@<3=YT($7eWMw4OFH($6_FDyad9sc}F|pjGR)=O!pV-ZB_F0c#<XnBt=G6Haupc6BF5^_2~+oV^5^azmkf*Fz-'
    '&6Bk4va+5qEzv8Xr$;gdCc7y5eMB|w1+>ZhN12K$;xyw64kv6<{Sjiahlg4sX}{UXrv^K=?=k{e$TTu6f29cX{SHxw}^g9xBzX'
    ';DI%7v02V!0|@Y72!kZRAvNlxW2Mb<Tg={^S|l;#bqjL({ax3;4K&G!v&E&W?t*N;}kuz#WeODU*F(gZla3L07*u{4GAh-'
    '8r71|zH7FY+e>?2GfGUD3^rs9F~2exT|;9n;dkQai^3xZa>*Vj5T7p>b76O84-'
    'UsB(^GqYzUrUwgW!(l$=%;bXL|Xx;G_c(Puf`uCeRV$G*M+2{#+4OOCyD%pP{XoqXm@(EW?0sXyR#zNOS2Iy%B=fgd57e(pKic'
    '`cOKA1K~;5=~X#~7X#K#gC2NoQ{R($hl5T-AO087X$HGTzRZUwsNY~8Ez-'
    'qXZ7!`{p1uW1iLoM<%ESe;P?9KblraeYY9%%X2gt+(52O>F=1FM59K@?M2?HJQe-ZSPl33-'
    '2t6@^_Fci_L9VQQoo6hiyHHh=ES;J9*&munken0^)l%3{KW4xB+eJpu`32WsFB!GP~u))5{a$hP~w$E$o45s-'
    'TSMvWC;i<bL`MFxN*6C&;hs66tic@v1x_xI3$E8ffIk&f=Ss5#a6|8I|2xHdUHPht8It!lu0qMLw@Y8;S6CL(M8oQ8rf`kt<5}'
    '@p<1c#jYB2;J^!)acE0ey(RZUM4*qSX(ra(B1LP(Y-_)BlfOizg!%l+t(@*E`5b64pQCT}R+TQ-<Eo()LnJcTh-Wl44AC!zsnx'
    '{}z7FhFwNu(A%a_SVwOQD2=>S<1m%)1*yxS84`vDDq}Gtoldddq*-'
    '+!vX2lLY8`Hp6W}8Af8>Z|UqM#wF_n?GRv~vf;_449;(Z-'
    'S0+B%&*$g!YY*cBU7zETyDO>wZZ=o=AJu?an8;7FA5<Ln?jxI%m#G6&HdS#~ROEF8SU~Bzc2f3ULMtaeS7gu-'
    'fu^tz=4T6BsyZobwf3Y`OqovoaC+o3?{PKE7xY-bpdewGY|H~$eSzS+#x|v5A0UJQ=OmZ(eiS(u5C(V=tXsorkl5rE^Lottjh0'
    'p>%TtySpWzGj7os;Yd4QdJcYPfKdXzr_3v&po8dghAD16=YkQ;BuYI5ySo&e<n2y^_b)A^jZ)XtP(WXUd(wIUs7kd0uGGyNVk~'
    'K5APBgTDdLh)al9x?LBuZF^8N2}D#Jv&Fk}-Hy{iXd8d=w!Bi!Ohw55wQu4vI!yJ~qh0<UThMG|j6Rm#zk>$-'
    'zqaPLu@I;`GOtutXpHsu@4LH8sep}-|4--'
    '`Q2+#P*UA+vB+e$LkKDiqv)ycB?T63|fssNubELFRYY8cEZOYUVp;2<cKLLEk%|$tJnHc<1IA`g(bc8#8&(S^{tTZHI>TyE4e('
    ')M$D^BsY23uuY6TXc3ntdGeOrjGn#Dd9XE?#p+??dDjB6+u>CI{xwbzN?phQ&5M;eqgl6U4@SYI*h0FxJERdQ&@0@6Ds&M0j1-'
    'gbOOb&5gt%))n_BZ_urC;pGI8%KF_1kS`1!Hv5UOG$NO!=f@oA2!CPrsO$L{RJmlB#9|;a4QC8B2;w?P$_ehlF@<@!IndoSTI$'
    'GMk`H4gVv1pff&046Z`ze&M1KgldW6QLvNF#OTv`bMo3$*iadACCd781X5xVf+i*9rU(J59-'
    'v>@JNa_KcC^K7{WFg(qFi_a+ZR@D#DNI7-'
    'e?B^3@eIKrX;$L=o%;!q6JeR118NHsp8veUX!SU=o>)K_ibjdCMH(|;HpS?ahTE+rZhKBpJ18k9zy8o&WNI&c=-#-nu;-'
    'HJxk#eoZx;}3FzY_;y&0O!-{1DO?zv*BN&%0WSQ6c8mS>s8$rJ;@N(pRYn9>XgY6Zt2ar%is#rG(4#xr;k>ta-dG#N+qi>=dEv'
    'R0d38!3?`K&C$a1d^DD0PAZnU_2|RcVL_XTzY}I_B&j;yraXRv!R~pY^X6Abk8l5StvBB!#z3O2S`B}bifjkw;PPRV$=H;*lB}'
    '~mmR9lUxgV&u4tNCJ552^Ap<uNP$0m&zSDCDAT_ZPTfw=$usEpWW@udS3o>d8>)M!z{q4247ESC>Zp;23)M!?>$K7xbLe6E5bz'
    'fkG!O}<xucMc3$;)10vw4O1}S7--F?DAavjAF<k{)6*aN*Bv|^cYC(SwfzX2-'
    'd~S3pzZS7{VcQ6v0C<+qJ!5E)dE4>2+xL<qs!gjy}dE3opGT{4}1ZX)k$9nC3Id8$426d$CotBnJ)}a)j!Q1Ke?%!@3Dqa#PQ`'
    '?(+Grx*JsIwIuYH!J$&+*mRiD*Dx%vjBqL;aH~t-'
    'DJFOtZ9i>D=4R$$+A+(MuBH_~m|WoOU&^tO?mA6bSM!2g%p0uusSss;QB2c|>35RX(NXC-'
    'A4l+xXJ7B?h1bgZ2xFVy3df%B()<tSlj^#z@QJg`3r4eMQdsQhn7MZpG%SPUiV`)#xF79JOzTib4$1K!5uJ@{4Mt3A>rXQ;B?2'
    'VSz6Vc)wi)jBuZ8+H8qIf?bJEySKKQLn<fmt@$s+>pe-MHDPE<w@fpsNK`ni!u)i))ABtYlg<|fEe2A~N|8njN3{Q-'
    'IEbN;Vhta0%!sm+Y9A*7-'
    '4U9|Ft$C}$%xz~Kz$*8l<aLzbfVVN|u{P1iC&nHdyVmcM>QI<MaD`CCVaU{pMOq0H_0)D7;Y^&G04YtC}cVvL_&CEaciFSyA(O'
    '(x6l(oY~q(x~K^<c{>TR%?1!K1=(s-'
    'k<Gt#w+f)+NKTGwtc0PRv>={pBHE9`|IOfLO5)5lt2Vhaxi6E^p#N1BO<C=w7ELe+8WWS#z{x^#W4xS9heVTR&gz+xho=0R27u'
    'T`aI>l5KgsFU2Ji-Sp2tU@{5*y4xNJJ|Pg$1@Do>weXqiLbQq>?f$(eba9LczSZtzF(C{$l(%^S3dD4^L27|v6-'
    '{U#Pe6!y!!u7P^oTuHUFDYFb(YnK-9Qy0qKno&L9!8KqVwx+E#_M$jvYmmjnnsuMqwkl=@LOm#v>~E<ks0{M0=R-'
    'm+Hvw4cX|ZmqjTpe3f6Wc->m09GqdM#P4f0{YJoB=SwW9PG7Xv`fq-'
    '5IG2vr!6(OGXwMfuzS;EseaxTa*u~19!4lVH%rY{vv$mX!%Xx_c;XlCasEQro3iKq~iFXWnN%V98j1MD*II4}mc9l5qWC4}cJV'
    'wSGm)q8`LM*F`ZLO7tn-R?LC`-{^ek)MvAHk;-'
    '+)<oc!#k!P4aI>2yQcV9a>?Zy=n_2RtU)8mlQn4N<b;x~l1nR+*9Eu2gTK~Jrc4i_O9~y&1`j?afO5l*v(B#Rg@;LX7HxaQg_3'
    'DBA~oLlbkA-)leiRXf7s?_hH-'
    '{!7#DrOMF9~%;7T=+rX|6hA8WNYtuUT4S>G_U*t9mL$^oN*<J4x(lDBX{$T;ih%ckJ>db9`pr~J)S=m~63F~H#A@z2p~<N;;b='
    'v9r1WF~_f%VCm3n3iM8D?0ahpg5<eX@<J4edB@zta2nctP;o1lJk|%M-'
    'Bgya`VME`xb3wald40PVSDZa_C&Jti)fcYTi05dkPG10zyi!^>=%$ePQlUJ7)6@&Bw#ghAt{C#Pm(Vu>5(RDuFi|(6Z#ncpw@0'
    'LUJGPSRf20YqT%NHhtDeyW>KHb-'
    'V6;nl@yI)8TkPhsLikj5u{F@AVQ+PlBb{dv1~w^UB2;%xSJNPsY}mPym4ef#hg?6YKah4_i$K&0<)o6Qx$woNl<xPHcA&<wkka'
    'WNsxFK6M2U{y;>NgnFe6Y%2z&!$T6d_pjo<w0UamXMu0z19~sfh&=Z}ox}u#;EVC9<Hkbt#>hER2u&jC7H>3fY|1Bu65bc1N|B'
    '=#<G~8_z`k^n?QqyLYZLV56`v|t^aZ@Y38cTl4b2;0{lef5WzFZtY#;>8*BG(s?NsVO$J@eM7<fj7yL{9u{ttS>uqj5O$l|gSL'
    'DCDZNj&yAD#=l2dSzkiGgYn)Lm9hf36~C7K>V&a1=^rJm=Jp<ZHgOwu++VRrmJF<Lj`T$f04cK>YPCMYk+Eg3*ji-YxV*O&Bol'
    'Z(j)YPG#czqr`cWcg%rkauXwu+sL`1esq1D9j`}oHZUeB2HoR!TmC(lXE3{al+KjUq$>lTgxqA{wrO9<pA=&^?ixb|iUz`5Uzw'
    '#d<BOJ|n4doR+kG^mCXcj_B!}g)g*37)9fz&W1?B9uzkn<7te$sEdm2cgst&U{KILu^)1|)y{rCd!oh0XNqWVZBS@Q3-'
    'c(H~zM)>gaY4C_b!By@TCjBH<S|4LyHeno{zW)Gp(Z)IV|I_AcjWExM6d+eO|tbfIqC&>Uk5jC|Z5N82jtZwasn3PgRu?G!t<7'
    'zItvOz<pFv)x0@jS$u{m2Q>cZy{%E!nI>#FUI|p0?<Kv6}5H3%aa6xcpPlXV@QpXW-xsL%ws*=V~B(+O4$C5${7`K<mT%Od{vE'
    'I_)nbhE_LBKk7D^9GLDrI??MLH9OwtUWP(%%fWV;`79ks$)F3|c&q_DPJ`Bs*d3M*{b3=rW^$I1lnB=!_MZeH5!!4l;*24d5!*'
    'i8wx*ev??%A2h7G7<UB25GI<+4@iR$JJIr?h9fSk<Q>*At0{zsb4CxYfXxsZq_3y&QkBQ{68jfrDcs=YPv;Grf#MCmxZ`uHM-Z'
    'Nii%uTW^b4qHR{+Lv-'
    'E=M#Ut!FO^tb)&HE$^AvtffU3E)c?&oP0gc?^4(fp#5)*?kLtQU$Yb;KmAHDz#F(V&i1l$dNs)Th^l^#?ao_4KWMfpmybFSC*h'
    'cDhS>5d$3Zl4iaD_2+sA?<eWoQ;sY$DCP1mM<}j|6eI_-'
    '#WgC(y=)`}N*!jTI>c*yj{%42!IkI&Yho($4M*ned!QkXKZ1kbA#@6)^R(5Z(IhC;fd|1QtkzID<rXJBZ)R1%0@&&;HRzgq;Nn'
    '4PG?^KKj0aQ&qAc<-'
    'H8Yc)7R%aL#ulDP{o2du!dhcA_jZavYIR4B8IRiI2E}lmF_{eZH+X06K?5kk1BGmWtkq4XgQeu?@VHWg^KC#+~eeZpPbV5~qK-'
    '{24m%i_!q|)dtqzw2lZXy+MSh8`lBo%M`52ih`<nqkGnVUrkY>7f621)7e4nL;_Z&LV_Z5NkdLor0n!j3FZSJs7#59J!+sZ_t~'
    'P&`hDyoj5W4*0@0wGRny$8Gq&*qU#M_^g_cU;LZeubEZWm2Ou8Mz6tMU{SI=l<5b=X1<Ffy<62ZT386x~6i+C?RePWUC!y0@-'
    'PJXe5Qsh3@s^A`N`M0Z3W*)cx$<dT}I0~*Arr#(70Wv0D9W)JOe&@Q_g+nVfla;^xTOQg?BsX1EWf?f&MOCFqzDib|uBC<6nkj'
    '-8!9yNVqt<CpFapise<vBJP)MEm7+k4}Yr=or=MVRGV^QgRV&vC%`OM;=gygPXCLDET41#=KqF$-'
    '_42|;W^c?8Qa*?Sve@o?7ylE2@xAVHP23ZFjO5=@2rpu#+z8)~>y4ZAsfS;s`QS>U*7UYg)<SBY2|32v+q~c?L1st3orjzv8Cd'
    's2OGpqlPqXl=0ye%D`7E7feU8MCO{oOjlY_;(yiv9LUFY4&a<|6F9JNp6-'
    'BaQ&EZ>{+O;pNJ%^TbeG+bMdFU^tEIpG*x>`HmjRh_!1SnKACS*?q;Kn5!ZlFD~W6X>}c1yii*FpRN&%n`<A<w1uWZ2{|96KYe'
    '$k+N|vE9*l|&FLH6d>M`DY={7`lbWsAooXi^)Mn)$)LbV@Ido%jCN4l7b&%iSq>q>_$%JjZhnw_rPutkxMCNuJm#h^t{nivtj%'
    '16fFaldG5bA|3)kHme6d3`0KL78Hpg>*&-SFb{-'
    'ovdB)j+TaUBx$mZB%U+!P*>FQJGOy$BvlYPh}T%|1QDMDa(ASMXGLF|PsU<Kf4Oi{(_K-'
    's=ji?^Qyr+m!jh{9k(e@E(ytv2QDOjcfpCzkZc{6qkkbBTzkPUoi}MVnui&&cL7oIk1<;1=z<qNOre%Tdb|%rwhJJ*28%OK>D='
    'NtnwaWzT(uv~5=l;un#sn?Uh*(BJ=nY!@_~NS%&3f^18E^0<os~Jp6F#~sW6O23j$-'
    'h$8#xblocfOODrD8GAWmz|X(g9DR$^7TY79PjC~SdOdUFvHrFz4HZU{ZHTvo^16ANF!fl0sjWa!iQo~1OEF`3ac<yIE7_CmO+9'
    '9D3M;0gm9I;Rx1S=xVI;#zK$F5mT4Z*-h0)_&B?GKFar6tPONgDxHqMXIp-`?mVjOvE?BZ;?-'
    '1BPixBxw<XKV_A&%Bjs}8nJP|hRgOj*D@M=0@@pWvS)ST<Ln|JcXU$I9?4}`E@bZ8%b3>5P;U-'
    '49!w=uQ+Q;vit*Az)iLnDd^}@`LpZ?A>ONbRais-pcZFP$m)pN<j+$pbk&Ng*Qd(})V1YnY36<1&m-uZPf#P}=8_-'
    'nrjV>k0Boj_myLmb4Y^WrHJu2W{Xq#Kxi90ziz`<X11Li?O}nEVp@=@W5O@?X>v4^w8H)AQIAOV^o<ZXoiOnB4jjk~hQrwa7XC'
    ')ZAv)f4;V!(TcrRik{@;!G^&#anV*GpO8Of4)BR_=S|es`;Eur02(w)6GXYeC{Jna`Bwk4N^!zymJJVpHI$-'
    'm2O(bZ4P|?k1_=Y<^29<o59dn>l}mtt)01nZ@w6}rRQ3TC+0e^@*MM%b?3;p{KCF7K-'
    'WRxf7c+HG0eNOfi?($r<nYe+TyZcH7v6g%7IyNIR8AdHDSoQcL#a?6aGOtx95P3@V1`8-'
    '<bK6gVN&oHE_zMekUxSajdbtPBZdERxn?mU@riW5D-S-'
    'q5km!!fA9vgV^w;=RkaH%06+FcO;<ie)BT`7L)*_Fy<4b?r*74pJ>oWOSOddsOk@3`ls@gRg|Q*z-'
    'c4HUdL(3NS~f^cCU7XATLS{@6TAMX*fvoMJ(9+wiR>v{L6e2gKmsw%oG3tZ(Sh3f1Gmm{;a0J=s<OxBG(EOek&YD?V`x37Wwfx'
    '1p+;y(h#^-w6>i!nO5@=No`C&to+vA#Cv99I)7DO}3w;`XUJ<RFCc`{FV;<6BP=*KjjFIQ%Mu+rTC`I8d*P6-'
    'AFUf*K9Qr{bVQuB*fgHqg?Jo9FX2bk2v!><d_<iFAI_5OG?4aVJo(bqP{wgFyreYJ&3G9eu$3vou3p;Py)#IiCco9?T3+Zs;+E'
    'O<{;tM~Adh)ZMd@KgvCT&Eu;L|APa;wk3dNP@%sAU?*o+TT{;f^R-'
    'EZESQf&@OD^Ee5BmA4x|*6VqUCJKNw^HrHUk3;fnEIUIejY?2nw=@4Or6U!=ZXknFY7Bmbq98JQk9^^F@rnbk1<rxXbz)p-'
    '8$Gnn@<(=3cJUP<-3pgquQ7C{V3^23sBAV6R<;n6-'
    'w#64mVX<~9KPY>&KBZoSrX?;7cUuM<rrpMLOD{DkWpf{mh8CcYcg$%$(4;)P%r|dK=>{=`wI@XLVuJ1Dwd1?$vzzqddLr|8EaQ'
    'eL38wzFtM!;<D?f--6TV+Tbh&2T*T{q@6|Ia(zn2iBAOut#^~MvPZz3Equv>u!pESrhawTInE3Xe+!X)Vr+#X<x%lyVrH*Ts`%'
    'ag|<LE~Lb7FZ2)8-NeQRSjjsjhtN=9O4AP|LCehK5Vb_I-'
    'y;&$EWkE>{b=;sz2Q%xSjIpfMK`W=#g#tuUj07x!y(6R~v6&fvu@{-p)YXa4{r>-'
    'Ff@*Ee2Tsj+dF*zDQH%;c?&y;Kwi6A>yYKye-b$fnZDElDm3*$|Vl$0-'
    '%fo{Bnd4pekNdf7gcYfrSH@H|3dGO(%&hMFL{%(Gq%TpIv79~hY13dRkns4KSGY%M+%8XhikG7v4^<R3`y#}}jsIQN}P;<Sc<>'
    '4IS^o$8m*j<5`p$rA0e^#Sc#+iO%~dioi!#ykKWX@K{X&S}5BqbA?ircm^z*)emU=?YY&*anHDqAFrBK!4%PFr!}yw%b(^cpY_'
    'AmCFP;r>hj6TyI~ahLe#(?bm-ALr4orq%ds(@S8mr3qb|eMdx@|t=>snqd{%L@WM#iZXf-A(3*O%Svt=80o-'
    'tIrVW%{|5_0v_p%I1qVDTLi?;=JU>vVZn^_8%r0^d)AJ^QOmBj=e8h^jh)IMmjJE^z!gJL+27?IZe*|%gszCrIqpJ`3032Q+^*'
    'VIt(hneswT<pbab-~VRL*jW9aEkjwWFvkfzaevR{&vBi`RWdGt4TBegb{kUsxiBH!OSE-;nA<nwkst=D4>-'
    'rtoL~JV}U`2f4f(_$RW{A2g|}Z9SGn>CnJ%!UWuojcBoh*P|rpPgc|bN?2$Np=;z%}hdc_ediI%JWg6<~V(Trwm^I3Rm;t^&;Z'
    'Iv>S<SvpMv4^l9*Drd`2Qk-3Gx^-QuU?87eWx1#2(LWco296#Wu<RmExbau?O)+P9X^bOM=dX2-'
    '(3D0MJBOH8kjWyFJ)f)=ApxjsN<KCTB>K*7#_{Xe%(ez05f}-'
    'wx0BJd}v|l~yq8A>=fzc2vUh3sTvs%uB}Pgrh5qPt8sq`xd;@kcl9Icu9ru>c<EbOAm*Z5a9!(&|<1Esu0TCquNQJM^X~NJcPW'
    'Kkn&GX`R_7ojw^JH7oM*xBD-9I#fxV*utTB2mH8Q?PMX2-'
    'NEhtk<>w59MDWtHBoxqR+<%8gMt=}WvfnM}vvJ#wRZ=$rvC8tBGF?d>u{+o&gWk%MvlY`7t?7Qn9PU1<WWJ>UX(0FpeR9R4Z4w'
    '>Aqq&+<kWqfiH6Wme+7g6wUf(cW2Ttx~w+Gkg{1=6003o?nwZ<5fWx%mW(Lw-'
    'm!DStQlSi197R5h99<<^5?j;WwgT1w4R9ZL^u&#d?ES-H=a~xT~T5=bknuF{wF5-'
    '+_Wx1%d+Nxl<L9us}zhSl?*<S=+C<?9Alr$$apW@8QFW5n~x)q>kr*n<W7gAANU$cja`X#K0I-'
    'WuF1$G_j+aOmgKS=QjtX=hiJQ_*9@Ry>>vClwG6V&phEsjvgJi}Bv1&0cBC7MZgwZ8?1&*+?)UH*gv5ERk{l*vznfW7=IzmND9'
    '91&79n<p=`c=f?_a)o(DI!uLEsRR}!TkK5Nl#NDWA=vLgMxCu$4mr(V^v9ZrXJotR>EY-G3S~;-9$&5X<DnUhfey{a6b-'
    ';gc!2;wHDkW<JNV*B(Jo?AWk*aP0qB>!%big0#U1XMf74Qd=!*R}$XV$@+1ufD5>9YzevVS*Djwa7$D7)?{=he?VS;orwpt`vn'
    '>v5Ge<`@Uk!-tZ?%u1El;c+!hifbw1-'
    'YCZI=LsPz^<da$Q3NyaBR+h1wnH@;Tl+@)rxXQKV^sLfxL)roFP8!5t3A<`N*X44<CecWvQ_|_N%&p0ydm#onLA(8WKY(J#CYK'
    'S)Uoky%@+B@?w=WfXT>`j{l`h;{8zH{y7HE>s1VDu5gvg0sfqA6?%$i4BTfxHNcYkSUDqzYEAM+k>;Uq!vbK8Z02{8$?|zhwKA'
    '#)`l6io@c=(2hSbF0^?_NXOI8|oQnJP&K7b9k5KT7!0ObQwQqW`@SgOW$)Qn85&*1Y2ndd>`j8V(fjC*uqZPzWRK8RvJG^vBfl'
    'UQjhI@?||pHg-'
    'p<Xo4Fg)K%`9>;~W=I(#CfA??p(M<&`@sn+~SEpPB1(B*x#q{%<y<pF6VIWh`u9n}C92q!eas5POuQ+M>H>kiU8ks@EW22`wad'
    '@{4rr`!ngQu#S*JQGJ=&yC!#0dez^jA<agv#BZPj3ZbGA^|ARk<QfqACY{RzCy6YZ(b4IK5*3lyLv(%%R6kM^lkFe0)vSctSfs'
    'E}F*f7^s$R#wpJ}lqzS*NM1t=@7gi+(YnqU;d6AxD1PVjf8McC74L=^s?BZk&D#}ElD#0rAuX5tu7c|0neRfy8Na?^lep6c8R@'
    'HW3Ic`?rFF1VtX*@w0+RAG3BE18m!a2u_eLK`+<-=hr~<r*m<(Zjna*g#)vErb;|Gn98jAl{ouq%q&)tb+at{M5|3K!~HR-'
    'Z6PpG>zAQ{BXJ1R#9ekv)JE36|y*CS~OQA;?8!VKHUL@_&8*lq)a@?g>`f!aNfnZ>6heCYcSQJ=;Wgtx~#wJxwCkave^j`;0<<'
    'n+C0bGyv#XTTj`k&oq~k81n0X&91%>>{&^J#wJO?2^paJR|q#AqXtR70Qc^d9@+KV$+BIyBUt}Ik_}EV5U3^9cDJnt%wH70ILC'
    '~kT{wdY5G#jp6bQ8Oun3nY#GFL-&;v2@SmpPx7*f!oOgff4;ofGvd;(22b#l7v4fI2AfiKMH-'
    '%weu!?|RN+;_rM>gYQf>}FAtPmZ=Ok1vl3I@Pmg9O{rWX9n|?fKMV{x~aqN$J+WN_!e(6N-'
    '@WVN0jp|78lVt;!D5ySrAK_a{;I%?oNr&nBW$ZfAFI__GdCN7C*RKB_HgTDSUR8`efjiqmq)JaB&n{pa)XJg2tsYxLHtFxHvtl'
    '<<<#SgaC14DQ(b+&mAc0`Xhq=>7Y84tz{dDhew{(_-pjQ9)7qOWgav{cRyGS+-'
    '=nM#cBWMJaM^i=udQJN`W~F(eGl!IX2E2hA5>9^E)^)E(qXqsya5zB~9NfLs>?stUM3^<TqJ#JN3SO7|%q2cYW3^4KcOt5gs}v'
    'gj)Q#(q^ll#FtkVty-qd=fFo#k`SH2o>jKLEq8@=UjF(VoGE_rDcnz2}q(mJY-T|vlEo7e>nZdqoJjFBbkDPXG_b0VOA-'
    '0Wy&v=JZc0+iA1?Eq9T(<Z*(AZ$gPEXrK++EVFp9DxTl^O{CRKV@d*VcP3Ii?u6AXJ?tH@Z1BEE=)wv{zHvhHHnQj<z^E@zeYr'
    'hbKDQJ_jjR23p;WT0(i3ErPVK`&lr)zFU*>wz&dhY}TgI=k1!whspC@6QZ2ye$Vy;@wPxNfNY97|6PwLQdUV8zoAjyO=|#Ff!^'
    '={K582hi#z7CS}G%}B!#B&YSWdB8H>9xCab)wL>r2V~`rm;()pdJV;=!9qH0jgC?4Y>EL&3pjN`FjA3pxfgX6>KY$U&krWH1I='
    'W9SA*7JpI7_hC`|SJGkM5lqr#G@F{5%(Epga3`#+h@b$G9_QP7jB{@n(9rbIdlx4T(~ZN`XCV7t{_$>5*dXNLS^f2)e7h&$ZYl'
    '=li6?h_E~gf6$`@5l=y&ra}sya0z6tEV*^of&JLd-'
    'WX?w|Fn{)pboUK^3jJORSdSfurtu3%hR!=iy9m-?Y^Cy8nhqE~53{_q1wCYR#z{4cQyo-bAcg4i{uI;{T>Xu`WFit*@exLmc8o'
    '4vq^oGa`b0$OXFOR^PPbl{cCC3bx!uR^^@^hifyTg-'
    't*ffgAn_h1|!Y%{9{F%DQ~NDJ+Zu{#w83yfx_XCYwglz9=t1UB~H*3LkfOW1m;ayF;W)`h1nv@uaQ^dLx1Tko4ySqIw$B`k^&g'
    'rn5!)5`dMqZlMs~oW<yiG3Wgljqi^$w{zuoOC(&l%%t#?PT*ucfVGQ-RjkYmMfjej`dr1HJE98kq)FQ3syO;tbk=-'
    'dZl{flXi!^Pm~3w=^ndB#5fHW4z1BRB3F2k>6s+_z*3E6>AZS#gZ=s%kpeutV*tgCA<GYq!XLo2zDq_*t#vwrBq;r}pNvHyrnH'
    'l^|_foDM`{_~pxx2A_hbj-'
    '}A^I~*>=_#)6(8j`dg(qWP{ctxZ?_zGn$7bh9lX5!+&sKTgb2hxM|l*i`x_WHr+<z4V)EZr@(#6<<$ciU{#sTWw>S@6m$Qq1aq'
    '50`C|#m&cIz6sNl;r=YX!U2>3^L^)~%|0E}jYumGwqFvig<$jIk^XGgnBWcuzfgBZrrgShCw)+!>jnF%b^Pa$J`h?GDKSS?`6_'
    'oWIBejH-9W{Xbp5-W{KC;~-bN$>r|*hbzb?f}isDx9o4_F90{<)y8#32;$5P-'
    'rJ=Bpd;v(koxfP1%$1Dj95WwCGp==y)`j$x)eKA5Y$1icDJgYz_MFNpyGclczx5T$}-'
    '^#AVU9O3NOKhKJ)y5hLlXH=*|cQ;GV;z8@>DQ4(LPYT}taC(gduwa^0X|Ij2`VPVZoeAz>I$@G$69WLZsIgexaxLnXLz$<iM2Y'
    'CJgabhYtJ?A(W9YFQ4}A4V)4IEBDYX91r>)q)Zyh}4Z11RUlIZ$b2gNU?)|E9$LZ!-s?^9#IpMhaW98HN#-'
    'QP`slE($1`NWhGyyP$py5c67R5Jkr>T8^f^^J&{=c90x8&yjRohaMcm99#qEBYttB=Jt0vu;;--'
    '!iJ)GldeMu;Ro#W{HTe|hk1o3=8q^y*B_Z$dk<$UvCYyiaU5Qj?&a%D?Ne_-'
    '!VmrH;SBQ_PUlzIJXVX}_1#F;(IoRpM=*O%NIXgFZRLyP0_kHV(HHYCq*V`B=fy77~Oi2UMEVpRAUaZkO#oR}!;p#UzCKGFLEZ'
    'r=O_(7D|LRyz*$d7mO$3b&L8?5w3s8JRh4nFJSrR_QX`q=)SVYtZqW;{;FZ2hWw)LaqaiFF+?9wK_lY8&N4#Q~NsIx$nOwSdT)'
    'BSoQWa^E0>4QOnx@p$bzU+T2rmT{SIogMSly6~U0VU%D(z-'
    'J1zqJ7WHB`LF3fVk^1EtK61oPXHXh<k`}@nX@*xS49P+t97e*cL;FBh?1*c<Z#O^tzb65R&uC>1r6WXzJ*I^=92AWcFHp?Q0*W'
    'g0pT<kh~+^=UD^MXK;WJ1+J$gsjAmv<f7I*(Zy^V$L*B8!q6|1L2v|7D<VQjU^#=HWS(9oNGQQ<R)n22*_rf8dLhJ6h?#h69QU'
    'h!mzz(SALj#R7_s01#n}rwC0jG7jq?&dl~#zw^0%RNFRGJxS2o-<-;#4KXfj{)`nupE=4%(I;te6LRLY%}*-'
    'XAC&oBZ&lZa(e7Dab`G1yy$@Yy@@6Js#8I?Q0qm2lQ19X*Pe|KV@)O-XoAYQY5!C@m;ns|H^Q@5Uqig~o0~_W%Ag-'
    '#AXmY_qh<nnm2sSMUv`dkz3?V3|3;;;|HW9`s`VU6YLOI1;LImzZPeFDugHrTac<<SEH9Q)J%^39=52^k0EDUZ&#RII5rY8-'
    'Q$(*Gp;{(>wY$l?j5IsoThM{(aC5JK51JcSWLFpc51+whjBtZ;W6zXp8h%<0W@IW>g9*ZnJ4CZ!ozK#vSX*L!yb|z%GjmKcu5^'
    ';Lj0#cu{*;nq-_mD$As)?%f?zkOf=KaepDSPX4_kY%{m9OfQ)b>me~4hob#i?dL+qE{CJ~Tem3DpAJP<I>^tfDb3W`lu)+eP%Q'
    '{GTr0wMElEQ29RVcng``}%QQ#`G?xMG*7QRJHu(W^ZI4t}#3^OUCyBg}KS+j{?g<crYI)VZ?<Wd809nl`ZOVf){iojVxChu<b#'
    'jZ4AUb1JPgNXzU4ZjCc2tqtCbnU{uzkS4~{V!81(>jU5BgZMV&OV48?Tv?$GJyOEPm@^yIM8UpSCugmtT5wl5yYofz)^c5qE!h'
    'rIiJE4EChq60pf+B@K|&u1F-'
    'C;fs^A^`4dJA@MPf^*%@)u4haGPNg{z)V%2zJ)ZGM&{MG!7;uZYj3DV?6qYn3w=yHE(z_$opx9ep-'
    't=!+gBhbLCu7NbCKl2bklrf(D0k!|+t)TsL$wygLo{VVtd3<Dalq@c14cgsi-DN{~q_sKa-'
    '{dxRMiq*BtNpR0E#HnEp?q~wHMkru|H;4yqOjPjqP3{ME;$!N{5EcR>Ztc71!c!y`l2!@7kMbHA(CxDU2ZxP`XEnfToP|v63-'
    'Ort+(h0`4R5`OWRo+EI(|j#io;|Tt${huZib`4VF@J(g%VG09t4-;_D1-'
    'SD@q(M0%t4=7ksCS3`1;^a^MlHTxSzu9CGX1n$|mgfc~&D1Kjy9Y4l~_f#5X{wq#(`=q}WR!sEbhgm)lFpawzxrC$1Q;zj`nYn'
    'uN71~P}tP-f}s3K$UX8ckSsmDFd+JJMO;KePUN#qkAty5dByzP8aFF9in`u^UJQG<|&-'
    'H>F3cw*j^t@0=v7uSa#PA7uLbZXFy1;kl(;i)b(^|-'
    '0G$j@m)dMt1lPyuVwpOeic#53n@YKh*!(u=WlOvKIyxw=+y0a9$s6NbUY+r2d1DAWeN)(xL2op8BKZOe~IInJy!@7Tcfx0j~&w'
    '8wdHGc~e~?02`hSqP#`2g`|w)AzkJEU#!!3G85ul3t|&P&g$RQsU2-'
    'TPf2ginBM+@8Wp^T2xhg=0CR$ZsG`d{D47m+u_I}wwG7<R-u5xnYoQU)J{{$^X8qEB!`R%@6fqwFD&=*kF0E*a-b4}G-'
    'o^un0WiDHcsn37Cybsrid{8Rgc8J6Td4Zr(vO%QfIKq^uH0@Lb>7c(zkfiloOMMSP@c>{sye))VX~h@!N4SbuZD%KIHYeOpH5W'
    '1fK-$=rL=St{yu)YerWhuCFyY^b`YdubhHsnC2BM?foHclqEp(ixXvO-yFvAGP+KV|FQRN_@yDxe~pVaT=)OYLOYz16$pyOg{J'
    ')3g)_tS{|t0<eDvduql!bDqc(MtL^<Rkqe>rU?#aNHnWnHZvC%pbz;?02N5?3IIIP9~$e7`uLK473XmzHC*=vX(gLgIK#g*Bo0'
    'khCERCeSIU{M^=1BSXFo0okmit&TQ^r%g1x+c=|qtsj)gp5d+UC*NgkEX1INaha+Q#7dpjuOZOq9clDHh>V+4`vNh;F{s+n_fk'
    'Qkf8%^i~5coP>6w#M*h}=3&n9sJuB4aHNCNFad>ZDboY0Iw$#x9l1nVu{{l6zm0|~pp8WtNIM^$MQ8I9+`A`j`qF2V7-'
    'rU3<vV0_S7AbVozr3K;@)Nrx59UAiT>clI*+yQXQy+9^Y6#;4*do7S?f1fI$4@_Np+BZgd5wjq<fXTlKf(FSgit*o9|}4QP6Qd'
    ';f=I$4qITjeM;<!YkF}hE)>}REEkyrS{&VEXG3^Uj*I3q^>%TbD1+H$rZJPf~Jhx@wJfoj@I*z~`4J39@@HANk8q!O?oIq!nIm'
    'k39tqMF;gl35@k3mj#BDb>TtO>;rcOf~R7MxN-T2Y8M(~HT%m{I-'
    'lEIp0OR0k|y&5oW0kx6~|mz+as><?lT;iE#noRr{{C(7~;B%uE~!s+R%T2f*S<_Mr2qdcesGYlyZXReFFq)ixc?knEFS9N@CI2'
    '!gxmI7-AHDbn=_E)$Kq#zFl-D0(v#UJ6$$Xmv4VK3=Z33z`cI_-n3%-'
    ';n6pWm0k6zQcLN9S;T8#)A~Glwy4^^Uo_@>_Rs+f2=Sz}#<tT6l4f&5K((<%U84StCoew!~UqYtfZI0w4S>5coi39^H?+9^kn!'
    'ack1UbBGymp1^qwCDjXu(fyRj1d2LG6k!E9sx=e_%;Q2N#_?J+%wCLMlG_t1IHSj6MheY;c{q82-'
    'uZ_Mtn;0MXmqCZ^{xB?LRN_%SX6xk`O~cu{q|N^%h226vRmrIoN=Q)bXJLxxKs1TpXlL~n+F_fCTM71EY2+awALvj`7A%8pc%W'
    'VvaKFx^~~gxBu<Ks%9RneHQkp5Atp>x&!m|-!hBp?p16yeM99J|@CDG6C4h80SG0qB4>`P>Qa@oEB&bW_-'
    '`nQ0{sX?GhXZ17Q!C4P9}^(J3d}X!@pQULyaDja`3fERoc+ON4d~+AU<55E+5Q$jMxJH#R`;Qv%hVj>eecg_`JE*;ixOE~)3Pe'
    'S^rqm|>1pOvpv8!tiq5guy?)HVYSAer*guA})Zp)v&8N47W$DAJ(hJk~=g#rx-GWzZ3%4bHxrs(F0IkPk$_6skJ3(`rp#bX%x8'
    'Zt6`lJdmE!z4#W9GP%z4U34D+>DSVe=~nCo(a$7Rr4L0aKUZaMB=Kz3~;R;Y|7gHd6iC8JbqnmTX3ACh-'
    'NggYW&l`%u+~qhtw>C9Y_{#-phSq*%(9%5T-1n+8ake)cu<!Dy2rsc6Mofo<tO?Fw67@(GX)NKn<$8*!wa<RvcC-dgNpZHIDJ+'
    'cl78GUTn%Li@Q-'
    'f1tWLWK`c6hV@zR{NLD1e$d!JhAC&z%UQ2nnX{cE*$0BQkp)0FwM=@C%O+_T6h~Ne=<{fx8CvW2wcRmi%Jzq6-'
    'gdPng*hCn!U;j~%B1nMR!3_(Ia80M6)qcBidDLkPgm53wg$BiI>f5cNuNalCYv-bvg8*M`OAw}WMTc?W8P#6w`#bzijEM-'
    'x2oaC?X58M(eE=xXs;QmT(UO!bMe&OVu-6%qcNPz+aUuL6CoOQd+t(=j4u^i-'
    ';9^;#oE(SgruyfFWu7mPVuk0DCGs=6gF%1h6{tKIujD5P^KP1X%b~rul7=kgg#OvX$k@!PWT&VXq&`}Ur&zR{0{&-'
    'he0Smcmw7Bd05rHEW~s$9NQAx7m#R<(ZP_rO>R98D9G3kQiv#FQ_Xzd?A_$SSXWku&K`;@a~u7ZM|0ZFyN<kk%?<D!%&|g4=&='
    'n<gAq&6<At{euKp}061+QQ!J0LBF(qkaC`h|Mvkbk-'
    '{&CSWYCK$%33e+?<viS5x++#QxYk>Hrv1X|*cbW%dVni$Wuh{`EruOI4}KSeb#qI~92_|$e|&I4>X_Hw$dWKcKIdOQhf0thDOe'
    'dHyTKwSt>qC@y0mQIS<5KC7+(eKs#`lguQp_`Ur93~MI+@r2jn!ZFRQR0UMQ^IpBi#upwZ?#S)?xJXO9g59zmh3hG{(Mb5r2T6'
    'sUpzQCvu87O+E?9KX{EVaIHp5hTPwMrFAZe*2zTg0)f`KC6b4o_gS<n_Kb~FczIn{gY{(AIlvin9(bgdLmf9{b+008>r@&`hh`'
    'c62Vt({K6h7W*;jVo_)IzAA*u`_0`Hx(Fb6q=F6BkKr)b>ic+mORl$H~0SjARFNlo`&v{x<)W3~y<iA9LU)av<6)j~JQsWU<Pv'
    'mG{LZhHKWSHIl+Q!g}R`h~PW3j`>srAEuc{r=jgm=Bv@Lcu_rSXCLN$sE=Bk{V>*Q0d@TxJ!&NAc`qKv{>;jqRKKrqCsgyNmfA'
    '6}y~FnVkO4vf97Ufxgf+E*ym^{&1|dGLCv(q9H{yCBLNo5#$(y_~me$e>cDS3R~PRy#{f429<iUY@W_g8%QPvO^hD2uZ3n>a_{'
    'Lx`Ykp&=6VQG;h{b6bS<!uI#!LmzJ#g52@$y3hnNzIZFg@TF8v*gXg|rRSAF|GZgddI?Xf^a(1MLG$8Z+m+)qMQmI4vtkn@P-'
    'Sv4|3r`Wvkxb*|MgG2P8q7MewLp6rW(4)(<pq5$+?c@DFY=L}rzshLtTLm2-Xgof2-'
    'cp#tG>F+!ezC4T0*nF+%)p}Q#Gi$)p^l}+E+KwnCtRtN1#IUJNf&6<Nlpc(Q4hxWDxV@zhB0&H?lK~l!jR!%!qPX8_hm!<txm2'
    'Y*1G>~Jx%oM-_I^dKoUJvAe@F3q@$O=GG9%|glPF7YkrgqD$Q@%{A?T+`=w2gOuz<9rNW)^jxb~-'
    'zAXnkUscSK6xdT1j5XK$SqndOuvYXl;ltR%2rvBr4Q9g;&pjM81AqA`@C-9FSTkrU0Mv6!O_3T8bo2_A7g#$xTE6%2p8-'
    'P(ijFCxjayl%4%mwaBBdgcM~=hD=aD*`oJ36h@hq8u$%NQ83FD<K!{VNE&1lQ<>7n*(Vic<L)aWbeFzR<n%dHEhGa%OpmEfwHE'
    '=s?J>G;k!l|1u8d0hVDVE?r4?tt0vyUqAQF$lIwz<h{2c($6f!i%IsPp$E7BeXJmrbvcbY-jCZv+HC<Xi5jE@`$h(gc#&8^J)W'
    '|S2>~DkIoZ;uFqv>o;t!m$)3-'
    '$fk9SPBl5EUy`%fLrrwpSeSxaF5pJ?7B*nR4^&=qsVwDo!gT7H(&w8%V$Q%C`T`h<p|M1~0&fX@11~wF+Qt0`URk?V=vd*#DF7'
    'GUgKTJ4b={dT`*}Jxpz(HL~Ez`p4JZk-fss`A!X847eWyS_`(7ldRB$U<VGYAQ!!I(mYnUyg>A)~cJL(-'
    'k)AIlP&#&Rb~5`%NlC1R@Wq|(Y!{4l~cKCB!?EljOv@pAC7lN_mg$GA55nIwcMpN)7TEhC~+upXOL))2L8ekt><7Fqwl^US}y9'
    '<}!j>0T1>QHl@1tcu?oKR<CT>qy0ZR(wGuxAx#BFfHum6}4@;N12gF-'
    'g|!h&(w_Vg<`3<F1GLEa+nxo*34Rd3DtAequF0GgPHBx!W&X|eJ8=LV=J$G>|ky!;r+3}vCe@ny>QUW5W!ee1J0Cm0zrvP?;O?'
    'Ru9CE2Lh1Z8fxEB3_wfslRA8@NWIE}CUoX_pkza+HP|bNSwzmk8KbrK<HRnBWz=~YJXO!y6#@HLJQmZ>OiV)|0P!*Zpw@~ra<;'
    'J$+XLhUNIwLYo9nFdCKq+ouAHS+8jkd<d!-L__5JJ<mL!g$EF*MiPdnbHxvlmNMFhV-'
    '*(yP^{c1AprbC@n^H4Gs^3%f~vlp!2}K|>e#7?IAt!%s+Q7<v%e4#9DGoPC{e#LNR{23LGlUZ`6*kQhuRG0U>r5v_M(Y=(){D%'
    'UA;a+OY_>-<C8I!oke7LdvhLcq%BqZQh}%@e`ym73e~?3{w%N>3#uT<VS<w<B0WS*4x7{K?C?U-'
    '2IsDDuluw0yi@Z9O42rY_N@m7GI=J?;C1V%Hp4VMHCRh<r9s)03z5IZnf8tq`e$@X31~E&Uhx+{(6yRr#vRGZD>hZGo#lP#Jzx'
    'Q?(70B71|Ujy}#ZYKiKZ3ISfF;b{Ggd41+o>kdyWm$6m8Hf}vy`)lH318!9|N0N3190rGAALDu{P@1%*3E#fozQD(Xh<*&#lI|'
    '}%vUhAeE<h78DvdvwxGH=mtSs*MxL#@;uM^D6=(T>!0Adj{cBNruo6{=L;WuudYeRKDm<O?rCKKGjBizX}leAEf8p!33`HS#y0'
    'gOap6qQ*m*;NUnM9}aXm}$5|SzD-8#%e-yH#*nb+gDJpje!cf6-89&u2BT-!obmn-wxgYblZOZ&x{G29dpm>Kr^wydQ-tUl6M{'
    'x?VRemTh;0O|1<e~?m}u7m-'
    '`?ThyXmOk39;dVF4ifmR7>FUJ>}Y%6snN9#Ya}W5)9Gt2%HKhlu8sU=%{xgWo^@uzvTZK0k31;6TE;vm~SAJ9JhW-Lj`$0fpS^'
    '<VnbqZaaJExU-'
    '+#^_}TRhNA#x`<Lzt2gVzoe!2PVeOhZQ^E|e~_;I45+~ks%{u<ynW5*PPx%r;SRs{&7vllTiOYgXi*enN%^${Pt3$F>VJcU*@{'
    '=WqGSs=&?&$l1DTi<FRP$2ZkY|q7p8*$=HIbzAijG#`O3=GtRWC4XoSJ5Q_pP2y{SFu;z+*n;Ys>hobR@l4uuOv+Oa^;zWm~HQ'
    'Qtk&PRlTCeqkbw25IG^#eVkp-at}36FHjG7O>MfBEjnCSj0Q^Z}$iJ+%Xu2p5>}q%Csk;dIizk4ND2_T$DWyB5JW1h_P{&1y7K'
    '|t;%wnR?A|6XT2v0|gZDdiZ(h$ai!Twg3h{!Gt>S`j61len!ShUi#yAf|EWP7pV!Y$g!2Y1D<rLltPHy8XceDUFt2alP+#UhrW'
    'nQXOV(QR_9a|!OWsFR1xz3)=Ai`Jd%>@A6u1MB63y@iGR1ZGW7ui;*GibXJ`0I_O9sKxGODz#?Nlc&X{Nm>r42UueTTp#2jZpC'
    '#(--'
    'iM@oKB+X+WLkt8shaGM)Yqun?+)kT9w&+CFXT;d9NwQ*#Cn=aw%Zz+Z!*5tY7;}SQdsf(0zMv>MvtfD8MjJ&s3m)F37@D@*Gec'
    'sHs1A>;@||cMr=v3p$QlTXws%L5KT4c7xU$m4sZFUt$;p@1>`fxyIKiqo^UTLF1Ql4CX5*qLz>0rBv$ui+=ry>z8+_y@)6aIpV'
    'xk%R9lF;;^E>wHYjd>fu@14qunovS0r9(DJ>=hzj-'
    '0OZEZ}IM*a~S3Kmtm(#ADAP_;*cdtR~!M0;*qqBUbombpF?Tp9xa<oLcCNjHdf75PGQ9e&eYibNd{MPCBBB>MY)kIgX`{0h5Gq'
    'N7Tv19<+PW{|>lIX%lXf(F4lMx9W8`gizN3)Q?@N&>u8l)RE%QmDm7hil}V3?Ce!x=<&*`-~W906)loZ+pN^d&5-'
    '*&toY=LzH(f1I1ZF@+#}RFPijZ1A|xB{NMp4)mL?$<2E(8UT=ag#1BCHi7A(sV*k4s#Iq@2#L215&V_OPnDbHbrCT41m~wgNGf'
    '6tZL}OF0u>qP2X4IZ$(Z@P)|p(x>2%0(_GR<{@xDy<IKBI~b49#)Ov{rbFD@<O$O_$8nfegkFy9Y6xm*RbUS^Yg7M>Hc717MB@'
    'K&?X$&Wr71L{_Mjkr?J=IbwLUw{bu%YDyo*9%Bi8xl8msAQPWZkUz%`wEveDid>{YLY@Z-'
    'Q!!87n%~Qc^qrR9~F~dJv`GMGhsGCF3aHT3&h(Rw~%JPBF1<i$-'
    '$oz5`|*yfN{o@kX9{tjGxXM(pQU~Nw|BRqQ&0?w)B~k&~E2F9KdGZ?ciER$?!OE@wJI>br!S~g}^iY>^`7PX6Uwj+LPi&xfq3L'
    'AP&0@{Xi298mG2`9>Y_<^;<%sU_*%D{Rm0}<-'
    'yk=1)6Bfuf@4<wNuu<nO|NmI5vOOU=}+}GTfYREkQk;*t>7+{>J&MZf1)@Zys7N|G2W7OO6bH4?gr#vM)`~FgO&O7jpcZFmH`E'
    '5^$J?AXPNE3U7Xn5)T>a!)VwsMr`4k+gXDHlYGa@pFtakb!nY0PxJ=gz#hQBOZ<<zJit*?`OM0`a+)99IKfxLD4ZwC($21as}V'
    'BIDt9|l-Eh&pTXk{#f4ybTJ83R{YA<(GnYSG2>+|+}Ekqz_WioP2oy{c_e^e$at-'
    'W92u1Vqir6}r8IT!H{gfJ+sfBZo&my@&UT%wI3{$FU@h|bwi|IAQth?*(vHI*GJ=wsQ3+gv^Agr-'
    'A+!VHNtN~3K+U%(V0WkU$7{nwJ<NjkFj)O8-'
    '&PkTXN43$_d=79#c_pWqA0T`_dI96jY`7zUP;g3ExQ?$F!Wp@Aam!nnjXz#sn?^z&gDIVRceyl-^)SN@p?VurH3FLk5;Fp1?-'
    'jC){UTkdj$Zcl%r}{%DuRI9Ne3#%e3|M!A&V8R(Gp9t;W=@uNsplWlkSRM(%X0g>F8F;2VuwsX7^uAZp`|S{Xe5$OX^(AHn;~x'
    'E#@XurAy8T_2m&%`Mj_NIYN$sH>Sarphx$Ep#lIZvp&cJO`Q*QUIhIyX7KjQ?6ccUYYES37v5{t*6u<MIH?JR4tF&CYiDk?f%*'
    '?jv*esq%>Sy-@9nCRm&Dx^{Y>9FE@O!>K;?q{Z;}TD}SKBrK*oJ9pwtL#Lz+d?~R?-'
    '{iDo}Fqw#D?vIzg>?W`H5xugJPq4w|?Cuv28ZS5+~J;3&EEx#~zBDm!`2D85iqn+c)w=e}uazI;!Nhy3FK32~YsTcoAM6vj97P'
    'q*!CJ4&|%5fo;Ho7+GwhRu_|1D~=yjwv^JX+#^Je78v_g8zutju{UY+`52q%HKqfK9Ud~T2>VavW}x@s!$UfXv(d*+XJsY1!`e'
    'PFcZSkd%JNg(7dLqi|YBV#6x_5QsGNF`u%BXq(3dRw-n~d*C*t%h4jYy_$zgk@-'
    'nnWuO~g_8B`w*#kPsU7ivF#l_e)hFFHiUUuUCe3bTq#-'
    'mY>euKj67+JgL%3fC_hTVoGtXB$|4Hx#Az;HgZjW(ZFpDZ4WS)QRsZaMyg+OsDEiz~bLu8&-'
    'qsqh=K)>mY0^Uu^+oTAHD(`>4i@SZgKptkdvK%Zm#{jbAz)Q=EQDesnIw8!<u%^9>p)>snBSfkFVQC{Y~O##cB_oR=)*SFHoq6'
    'yNbdXJ$9IXKapx3QgX%5YiETgp@pz{ybY6V5l_TyT%_M-#QSRSZ#p->__vt3{vp-'
    '&is8@_YRumNpnF=a5Vz2Uj&`24q(iRQ~hCH05W%`wmYF@GmOfady!O8Tki1EYj^j?<c?<=mtKgHx#AfRM2%zx{8fw5q2;ETtDL'
    '0;vu!z42*{EXsa{%xZk)_2;y+2(xcx^>UEhf$4%oL!7>()6g!|_-s1w<;*M?YELyv>^_B+#KjW-liRdrT(z?F}4D}nPZPbjGc('
    'Ukgb_&a;0P>^J(d;GlL4zPzj)}|vU-O`H&N3(@Ceb?mYuS<K6U$AHC_*RQF5}VlKt)WYt8y4F+GvuYduGJclDeaT@BvL|-'
    'naTeplMX?YBBmXYqI)`sRUibKTX4LPjwAlO6AcK>ez*A<MUk=(+Au?_vCjBq>k6F<1OK<L5q{RNr!hhbgG7b-'
    'zku~z2}?C`ob^wHnmVncfOZIU1YzxI(*YHzJ$i|Vlf1L$PB@fPLf_WzV^B8Wau;P9aqIT-'
    '8k|c2u%c`2ox00&=rYlr%7p9cgv0q{^rM@vRzmFm1h>|jIgVd2$h$Ik-o5NyP=0zX-SA%LBph!QuYw)FUX%sK0{y8+brFlyHB|'
    '>vF|#<+LaX)S5g8PdlbZ!2@<D3O0F~L}JNA(+d7;*8)9T0TU{m&VR@x0`*xz)S;M0#~j{g5DE=7J%lD?Q2H_pk6Dv3EX!|mpd0'
    'Ywgu>vLbo!bGYHJ0zj-+wqVJ$Y=5pW4id+)yn-JNU3!hl9){M2+zL`qA~-'
    '7_Gg@hoRw)^@`o6PWKE<#b)GN`Y0c$2&15}R1J6;1O?IvM{}7P<*i(H0+KyV!e7XIE(Kl7~hiCdgVbJi8^cEVTcXRjULzzyg7!'
    'ycK@S(eexzOcJ#oJii)gs<9_^`(_)Jc%YlwABA6zAxcQ1O30XVEB9ievrkblcNX?BP;0P~9kQF&<y9mkf|(R5)IL6rIKg8R2ic'
    'nMvfO>r`bjbDPLZ@w4N?Zw-'
    'm@E8p5w;Y{|P(GFJ@z07bzl6bP%GmGYA?h=?)c>ZjK?9r&eM`0YVl<|8vws`?Hy*o4#fv}dO74rF-JTBIi2V}^<gN2ANiFcE75'
    '^=%3b4GuMqZ&D1-'
    '!$t3ZN#c*o(rhnoz_AaaB(}_{G$JqoT0kPIWE@^D_n>}|2Mmapd~{Fb=kaUpbU*rrI4bo;Iu6Rns|$k!XA2FT5pMMk*L7ZzS?T'
    'B!6>b3WQr3NfLG0sg)O+By&gNnjv1jrxV6j>D^MPbv7yhdncx5`ua+~evi_+<#Vvg{O@qT~Gl0ub+UC*bQIOAbYAy6$`i@yH&z'
    'G;PVrxGs2ORG~fRT;bZy1{U_oD`|U^bZy+y<euUF_oOq9V#@H#rjJ|J6gPjxa6mu!MJ1q+);VplMjv(P91(E{IuC<;X+WD9#df'
    'LA5qORI@l5tO0v<K)ZvIF*Ph>3u@}Mn^-'
    'LRe2C+MZ@t;$ne!;D+Z>MSYs;<|b4DtoOh}rP1_@C2UgC2d+NGw8PIbHGu$&l>dfw65$mNXE#U_7vYDz}N8NqEbngbxoDrb$9p'
    'hL|sN4Ek8$s+B{Qna6_?ZaoTZBv9VEc7-'
    '^WQS1TY==m|lvV?tp!hkB|F#~iX3xy694=E`;((mnzCL!DE|1WDcTG9}E&UbQnRxX;sYUo@;@-'
    '&iM?Hd9>CG>wJMLbFqgXj0iWJE)a_wrA#^^<K(dwClWsV!^)mB&JX(dG|5axKlUn^VNCR%?15b<VMVvy&A_1Y<B72gYus1dnRQ'
    'C383gD&bT!C+2sx!VMV_$<%v)6_58uqtdZt0x*^%F(7L6{4#r;FZ1hr{a*bCgO#C{@cC@yfpTHlb{QYEBthU@d48XAvLDVk(85'
    'mv9P8ncmqG({ys&(qh|dn3$GUfQ@M^<;Bu4bpCxocgqrMT=hg#s_L^~{4`eP~1~*ZJ8p0~kBciTAGW|HmZ>@he)c8RjZUDl6or'
    'EyAKiXo)#yVs3QSt1PyR7epi@#yueUS7@2zvyHOUO@WT|W#W9l;Mj`IprWbcL$=vi-'
    '9S?w8xF7V^{`r0azJqL5ha4n?(tJc=9=lF8?ny-'
    'PKyh(7`s6?(?7E^z&HJQ%T;;?9`&rWc?1Uof+TwQ}%&J5$;)%(jT(g|&M682}q14o2Pt8|tjq027DyO4=U`uc!w30w%*Mo-Rt4'
    '-(3(7h&AjO?Sx#UTWNpA!QrX|ALJklKbfXoKeYA7P>k4d&8}2;p3NPJp?~aoB2#wsf@g1|T!2mmV<7x4-'
    'H5qK1nQcikdYo3#IACooD!O~cUR8g@90))0zBh+pDDhUVJdS~LFg-;Rrk2x4oB1cKeRfcG;T9!Cz&%{#6jt~;&2`Q@-'
    'ikJ*rl$GzG;Xcesf+K3vAbW36;FWZd)W3UY&{3h#=tb!8(;J3Uj3z^Yn7}e^(n~JlJ@{>Mv<BCcTO``?62V$qR95kEeT>K!i6;'
    'Es@mn%}*+GvdAcwY`<QQ1j4`onS71jQuExesRt@EX@Nj^<r+||EGlN!!>(%0o3+<YHCg_xz<R@yg3dF@#=GBxqB!t<bi>ESzf$'
    '^N3Gg@oz1H&v(kbr3_+WS#L_1$7oDcd4Z-'
    'r(CHJcXi=Ffg}nM_*RF<{n6=u>>6bwT<Es;*KvWP5C*zJ=IZ+y60Oy<dc56E1ZRxh_ZxjPk7j^ZHE#x8XacyjzXu4&^e_m`t&}'
    ')s<qCf`rtoNVT!RJjz?Vmz)Bg@7Q7`ZH~2|j3?ZSdEDXbk04`KtEcspYUmI=5@De{x+OM>o>l3u>Qal*@V3+1Y1#SL=O`7UC$U'
    'K+BVcVZ=wMUk#rEY~va)nACOQOBp%=hf_{JrOc9ekYJ?X`Td)U2`Uv1AlklBhk5c5VQMB4Tu2zTk5EQp!;AF|OcDsr<sZYix78'
    'o+$Z**3ep8lIa!NoQAh9m%{jKz@*70VXGnx_#H!JQQqzap!{TX7>m)6hEdO9AoSQapNl{VAOg##&#Ze5})<bJu0NtZLFN~oAD5'
    'W-IwPD@i7>Br~CyjMn))1GC2)pJ<-~gr)}*F`$-'
    ')&F*AOwfc>ys=%}m`LW}OUIy{n(<0jr&*%>&ac3UFbt;pz2HPbeX(GN$V4#q0`Tfn_xrNw`s-'
    'X@!q3}UlV*wBHEOB&!QnG%^XKuPZ<(lg!P;?iI+eaO~EUt4!XWoBq~Jz@KTK8}Dg{mTZ3no!V(@xvv#(t=p&@+{e*l{fA<S?Jd'
    'THsYv~@LdDQoTUw`-'
    '27@TUq#0y{}?ZcI5*pzqiY%Go^P0g@_9CFTU*IWM=m8em7E_~@9orT+xD10HpI`*ldBPps@1lsVU;Ue>vjpa&_u|!7pk&yQ8MV'
    'vAYEWAXC-Bp<t@nrsDx!9Y?FsPi(k@MSvHZ+X@*E%?SLh(bbocTUi3PH!kg)J77$#vq)a4a-k&M#2+t2pGQ|hK3-'
    'S^;H?5M`{ksBzJ;I*R`Oawt{hW&7@d9ZulKK2m0>lLg+;ru<HXw{3j@>zzc;O=?_42~Z+lc5}-'
    'n&nmDkDVV!@No`sN;qSp~en4opX6fY5F4Ufi*CEkW!h!)mw@?6OZ<z0D>jbc89pjk;mc1$eGZIK>a*aJBep!lNJSgauUf#X2Ua'
    'Zz>$X22Y;GB*#`mJW3rYqPZ(L3j_c>3fvn`5R=a3OGI%x6v$ueD8QOti)-'
    'E$&t}&W6ZBCBiDQxPudRBx@VCO@j+8!hlDVE*PhA*l4hAI_{0}*&ams;vtRQnC6Mv^Gv#Qu}B=!}pe3aLE!;||nGlpLYvOd_LS'
    'w44gG0Ch)Y`G~ItR1>(>h%Lt9*JI;`vSXBm!K{D?c_IB>Wwjf0#)28^%?Alust00&XIO3nUM~5bIIkYzV`~Vp^BQfd!fV=a+93'
    '@OJZ-'
    'a1+(Rt7Dj|vu`L~zwF!5EJ@V4C=Xv(dvLOIxZ^zIk<ABAl$meQ5D8D*CV(EwhS83579A{$CNoi1v@Kq|<e0TL%k?h&b^J3leOy'
    'b=V=mzom_nD`8lMfstV+n7(36Rp+g-|P^F)-E8M2;(tt=HOZ&&ZTdAXH(p}i#-h27#C$z--21<JSx{`Phs2u_u&lIcR5Y<uM(N'
    '0YSW{LZh<WK4$KDXXs5_%qxEEa8x$foBC}?}$%xo~zk2N@;;K-'
    'k38y%dMh&|^`&`mR1i1ANa|_5asl6e*Cepu1#!cMBUg0CqIlXqT?6$Ari9$wPyrE-FjE?r)&Sf?9PS?#r9uqcM7LK?G(L=>%(j'
    'G#(oJ}h`*<G>#rChL7xk%W=)Bg(WVI|U{^|)=OvAOorFdp(w0|~5omp_|8bEQ@wwOktD+{M|S2Z1Fl8xCiQIUajf6*Wa@T!vr)'
    '5Z?RAnALx7!&haLjUa(ytI%X0Y2p$Apde?<T}pGe6E8_Ahp3&%BwFDVZKL4ZgW+wg0Hv7$8r7>GMRnzs^DE885wE0^RreObBRH'
    'XIh;RnwQpTaluv0C1y;zuQaQ-K;iR}96g4-'
    '<l(cGn9&?5bEUA1f&%snd^rq*gNp%zCZK{xp&sDNB>+7rfE*mfOYojgt~Qc#&{KM01&a$jjgX74jmYTz}>P*~|MHX#8T;9}Q0B'
    '%9a!43J^{S#CTH9(+lUC_s#h;c@k&?vI_aJ7!j{_>qJf)V7?t2pMTf^c3VnZWRaBO#_N6-!-'
    'pF0AphaJkmHT>Trl`DB@@B&$T?J`3XRyn361@uCO{oOvQCf#1pXluRw|)$>nMq6Ej9!g1cwC4w*xCUxOuyHD%;kHgU-'
    'Z0j5{v4aQb&?GZ<0d=^%BBe_$CtYJP52@QCm9>+z4nCfObGCUw#H<>-'
    'x1M@EPFILH^O>0d*oZ=bKHGE``F_Xwn&zrx0#~;O|)PoEvQB;a?D)ONodu8jY(4>7j<CB%2<we4r)nDxwjT@BK@MsXFAD|%La9'
    't1ZN9-'
    'jgMNnGE6)8H2@sSXXz9>u}gkF(feP_#EK~MQ<{N#D&E~p=&U$KQYRH)w?86<OCm1{mOs_BQb`wcoP`4c0WU3VFFm(3VU)V_uGx'
    's?8M$cHSETyiK%H5?8D_Vod;Oqqh9(-'
    'A|y7T}h>N}V1z*H@rdIRLu=c)!qsYilYicH3vH;HrB)aZ?%_^{|;l?DiE0DulRzGa<*-'
    'R}i_z$LiieB3*iJv>QB3)sUDOrFm)a$(X@a=(vLnY>Y3K{^Lnttju^$cZd~;IF3Ear}?<=v=-'
    'r5HkA%dfoeP7H@8J@bgfhFrGcH$%mD~G9#}xoU)C(TqumGfINI%F5K+KSVm%|89#Nd^Qr2wSW{`s=AeOZl{cXDdp@RAtaQey70'
    '0Eej5{RradTNz>vBYQl0ssI200dcD'
)



def _embedded_aucdtect():
    """Restore the ORIGINAL engine, silently, inside this app's private data.

    Zero external ZIP/download/engine installation steps. Explicitly verified
    SHA-256 before execution. Never overwrite nonmatching user-owned files.
    """
    if os.name != "nt":
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
    if bool(info.get("physical")) and info.get("country_provenance") != "verified-pressing":
        reasons.append("Physical pressing country not verified (release territory is not manufacture)")
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
        self.stop = threading.Event()
        self.status = "Choose input and output folders, then Organize."
        self.progress = 0
        self.counts = {"total": 0, "done": 0, "partial": 0, "skipped": 0, "failed": 0, "warnings": 0}
        self.results = []
        self.activity = []
        self.error = ""
        self.lock = threading.RLock()
        self.window = None
        self.info("v" + VERSION + " automatic mode - source files are never modified.")

    def info(self, message):
        with self.lock:
            self.activity.append(message)
            self.activity = self.activity[-250:]
        try:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n")
        except Exception:
            pass

    def state(self):
        with self.lock:
            return {"source": self.source, "dest": self.dest, "format": self.fmt,
                    "running": self.running, "status": self.status,
                    "progress": self.progress, "encoding_workers": self.encoding_workers,
                    "checker_workers": self.checker_workers,
                    "enable_encoding": self.enable_encoding,
                    "enable_checker": self.enable_checker,
                    "enable_splitting": self.enable_splitting,
                    "worker_limit": worker_limit(),
                    "counts": dict(self.counts),
                    "results": list(self.results), "error": self.error,
                    "activity": list(self.activity), "undo": UNDO.is_file()}

    def open_log_folder(self):
        return self.open_folder(str(DATA))

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
        if not LOG.is_file():
            return {"error": "Activity log is not available."}
        try:
            if IS_WIN:
                os.startfile(str(LOG))
            else:
                subprocess.Popen(["xdg-open", str(LOG)])
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
            self.stop.clear()
            self.status, self.progress, self.error = "Discovering releases...", 0, ""
            self.counts = {"total": 0, "done": 0, "partial": 0,
                           "skipped": 0, "failed": 0, "warnings": 0}
            self.results, self.activity = [], []
        threading.Thread(target=self._organize, args=(src, dst, fmt), daemon=True).start()
        return {"ok": True}

    def _organize(self, source, dest, fmt):
        staging_root = None
        manifest = None
        try:
            releases = discover(source)
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
            self._credit_consensus = collection_credit_consensus(releases, artist_main)
            for index, folder in enumerate(releases, 1):
                if self.stop.is_set():
                    self.status = "Cancelled. Completed output can be undone."
                    break
                folder = folder.resolve(strict=True)
                result = {"name": folder.name, "source": str(folder), "target": "",
                          "status": "", "message": "", "warnings": [],
                          "metadata": {}, "checker": [],
                          "processing": {"encoding": self.enable_encoding,
                                         "audiochecker": self.enable_checker,
                                         "split_images": self.enable_splitting}}
                try:
                    if source != folder and source not in folder.parents:
                        raise RuntimeError("Input release escaped the source root.")
                    files = collect_files(folder)
                    if not files:
                        raise RuntimeError("Release is empty.")
                    if any(fp.suffix.lower() in LOSSY for fp in files):
                        raise RuntimeError("Lossy input present; conversion to purported lossless is prohibited.")
                    self.status = f"Looking up release {index}/{len(releases)}: {folder.name}"
                    artist_hint = re.sub(r"(?i)(?:\s+-\s+UPDATED.*|\s+(?:ALAC|FLAC))$", "", source.name).strip()
                    info = release_identity(folder, files, default_artist=artist_hint)
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
                        obsolete = []
                        if target.is_dir():
                            for prior in target.rglob("audiochecker.log"):
                                if prior.is_symlink() or not prior.is_file():
                                    continue
                                try:
                                    with prior.open("rb") as stream:
                                        prefix = stream.read(60)
                                except OSError:
                                    continue
                                if prefix.startswith(b"MLO Integrated AudioChecker v0.3."):
                                    obsolete.append(prior)
                        if obsolete:
                            result["status"] = "partial"
                            result["message"] = (
                                "Existing output contains " + str(len(obsolete)) +
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
                            self._export_release(folder, tmp, fmt, ff, ffprobe)
                            result["warnings"].extend(self._export_warnings)
                            # Actual AudioChecker is performed before finalizing the release.
                            if not info["physical"] and self.enable_checker:
                                try:
                                    self.status = f"AudioChecker {index}/{len(releases)}: {out_name}"
                                    result["checker"] = native_check_web_release(
                                        tmp, fmt, ff, self.stop, self.checker_workers,
                                        release_name=target.name)
                                except InterruptedError:
                                    raise
                                except Exception as exc:
                                    result["warnings"].append("REQUIRED AudioChecker not completed: " + str(exc))
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
                    if result["status"]:
                        self.counts["warnings"] += len(result["warnings"])
                        with self.lock:
                            self.results.append(result)
                            self.progress = round(index * 100 / len(releases))
                        self.info(result["status"].upper() + ": " + folder.name +
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

    def _export_release(self, source, dest, fmt, ff, ffprobe):
        files = collect_files(source)
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
        for cp in cues:
            parsed = cue_read(cp)
            for rec in parsed["records"]:
                if rec["file"]:
                    linked = (cp.parent / rec["file"].replace("\\", os.sep)).resolve()
                    if source != linked and source not in linked.parents:
                        raise RuntimeError("CUE audio escapes release folder: " + rec["file"])
            found = cue_image_plan(cp, parsed)
            if found:
                if len(found) != 1 or len(found[0][1]) != len(parsed["records"]):
                    raise RuntimeError("Mixed/split image CUE needs manual review.")
                by_cue[cp] = (parsed, found[0])
                images.append((cp, parsed, found[0]))
                source_images.add(found[0][0].resolve())
        mapping = {}
        new_cues = {}
        for cp, parsed, (src_img, records, ticks) in images:
            if not getattr(self, "enable_splitting", True):
                # Retain one source album image: the CUE indexes remain intact.
                src_img = src_img.resolve(strict=True)
                if src_img in mapping:
                    continue
                ext = extension if getattr(self, "enable_encoding", True) else src_img.suffix
                output = dest / src_img.parent.relative_to(source) / (
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
            sub = cp.parent.relative_to(source)
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
            dst = dest / fp.parent.relative_to(source) / track_filename(number, title, output_ext)
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
            sub = fp.parent.relative_to(source)
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
            if fp.suffix.lower() == ".cue" and fp not in unresolved_cues and not (dest / fp.parent.relative_to(source) / (cat + ".cue" if physical and valid_cat else fp.name)).is_file():
                raise RuntimeError("A CUE was lost during export.")

    def undo(self):
        with self.lock:
            if self.running:
                return {"error": "Job is busy."}
            journal = read_json(UNDO, None)
            if not journal:
                return {"error": "No prior export to undo."}
            self.running, self.error = True, ""
            self.status = "Checking Undo safety..."
            self.stop.clear()
        threading.Thread(target=self._undo, args=(journal,), daemon=True).start()
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
    destination = str(DATA / "webview-startup.log")
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == destination:
            return logger
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


def _single_instance():
    """Prevent two active GUI instances sharing an output/Undo journal."""
    if not IS_WIN:
        return None
    import ctypes
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\Karpuzikov.MusicLibraryOrganizer")
    if handle and ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise RuntimeError(APP + " is already running. Close the existing window first.")
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
.form{padding:0 13px 12px}.label{display:block;font-weight:650;margin:2px 0 5px}
.path{display:grid;grid-template-columns:34px minmax(0,1fr) 85px;gap:7px;margin-bottom:9px}
input[type=text]{width:100%;height:34px;border:1px solid #37404d;background:var(--field);border-radius:8px;padding:0 10px;color:var(--text);outline:none}
button,select{height:34px;border:1px solid #3a4351;background:#1b2028;color:var(--text);border-radius:8px;padding:0 11px;cursor:pointer}
button:hover:not(:disabled),select:hover:not(:disabled){background:#252c36;border-color:#596579}button:disabled{opacity:.38;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #8cc8ff;outline-offset:2px}
button.primary{background:#17324c;border-color:#3e78aa;color:#dff1ff;font-weight:720}button.primary:hover:not(:disabled){background:#1e4265;border-color:#66a9ff}
button.folder{width:34px;padding:0}.options{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-top:1px solid #252b34;padding-top:10px}.options label{font-weight:650;margin-left:6px}select{min-width:72px}
#progress{padding:12px 14px}.progress-head{display:flex;align-items:center;justify-content:space-between;gap:12px;font-weight:650;min-height:17px}
.track{height:9px;background:#252d36;border-radius:30px;margin:11px 0;overflow:hidden}.fill{height:100%;width:0;background:var(--blue);transition:width .2s}
.counts{display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.counts b{font-size:16px;color:var(--text)}#detail{font-size:11px;color:#c2cddd;margin-top:8px}
#results{min-height:0;display:flex;flex-direction:column;overflow:hidden}.tools{display:flex;align-items:center;gap:8px;padding:0 13px 10px}.tools input{max-width:280px}.tablewrap{flex:1;overflow:auto;min-height:50px}
.tablewrap, .tablewrap *{-webkit-user-select:text!important;user-select:text!important;cursor:text}.tablewrap button.result-folder{width:28px;height:28px;padding:0;margin-right:6px;vertical-align:middle;cursor:pointer;border-radius:6px}.result-path{display:flex;align-items:center;min-width:0}.result-path span{overflow-wrap:anywhere;min-width:0}table{border-collapse:collapse;table-layout:fixed;width:100%}th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #252b35;word-break:break-word}th{position:sticky;top:0;background:#171b22;color:var(--muted);font-size:11px}tr:hover{background:#19212a}
.done{color:#a8f5bf}.partial{color:#ffd88b}.skipped{color:#b6c4d2}.failed{color:#ffb0b6}.warning{color:#ffd88b}.small{color:var(--muted);font-size:11px;margin-top:3px}.empty{padding:25px;text-align:center;color:var(--muted)}
#foot{height:58px;flex:0 0 58px;background:#101319;border-top:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 18px}
#message{color:var(--yellow);max-width:42vw;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px}
.drag{outline:2px dashed var(--blue);outline-offset:-3px}
@media(max-width:750px){main{padding:8px;gap:8px}.counts{gap:9px}#top{padding:0 9px}#top .muted{display:none}#foot{padding:0 8px}th,td{padding:6px}}
</style></head><body>
<header id="top"><strong id="brand">Music Library Organizer</strong><span class="muted">v0.3.11 - Under construction ⚠️</span><span class="space"></span><span class="muted">Automatic · MCC-style · originals untouched</span></header>
<main>
<section class="card"><div class="head"><strong>Input and output folders</strong><span class="muted">Choose folders, then Organize. No review or second confirmation.</span></div><div class="form">
<label for="source" class="label">Input collection</label><div class="path" id="sourceDrop"><button class="folder" id="openSource" title="Open input folder" aria-label="Open input folder">📁</button><input type="text" id="source" aria-label="Input folder" placeholder="Choose input folder..."><button id="browseSource">Browse...</button></div>
<label for="dest" class="label">Organized output</label><div class="path" id="destDrop"><button class="folder" id="openDest" title="Open output folder" aria-label="Open output folder">📁</button><input type="text" id="dest" aria-label="Output folder" placeholder="Choose separate output folder..."><button id="browseDest">Browse...</button></div>
<div class="processing-toggles" role="group" aria-label="Optional processing stages"><label class="processing-check"><input type="checkbox" id="doEncode" checked> Encode audio</label><label class="processing-check"><input type="checkbox" id="doChecker" checked> AudioChecker</label><label class="processing-check"><input type="checkbox" id="doSplit" checked> Split album images (CUE)</label></div>
<div class="options"><label for="format">Output format</label><select id="format" aria-label="Output format"><option value="ALAC">ALAC (.m4a)</option><option value="FLAC">FLAC (.flac)</option></select>
<label for="encodeWorkers">Encoding / split workers</label><select id="encodeWorkers" aria-label="Number of concurrent encodes, including CUE album-image splitting"></select>
<label for="checkWorkers">AudioChecker workers</label><select id="checkWorkers" aria-label="Number of simultaneous AudioChecker workers"></select>
<span class="muted">Worker choices and checkboxes saved.</span></div><div class="muted" style="padding-top:8px">Encoding off preserves each track\u0027s codec; Split off keeps album-image tracks whole. AudioChecker uses original auCDtect when enabled and available. Missing engine never creates a fake report.</div></div></section>
<section class="card" id="progress"><div class="progress-head"><span id="status" role="status" aria-live="polite">Connecting to desktop...</span><span id="percent">0%</span></div><div class="track" id="progressBar" role="progressbar" aria-label="Organization progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="fill" id="fill"></div></div>
<div class="counts"><span><b id="total">0</b> found</span><span><b id="done">0</b> compliant</span><span><b id="partial">0</b> partial</span><span><b id="skipped">0</b> already present</span><span><b id="failed">0</b> failed</span><span><b id="warnings">0</b> warnings</span></div>
<div id="detail">Input files remain untouched. Any genuine errors will appear below.</div></section>
<section class="card" id="results"><div class="head"><strong>Results</strong></div><div class="tools"><input type="text" id="query" placeholder="Filter results..." aria-label="Filter results"><button id="openLog">↗ Open log</button><button id="openLogFolder" title="Open folder containing organizer logs" aria-label="Open log folder">📁</button><button id="copyResults" title="Copy visible results to clipboard">Copy results</button><span class="space"></span><span id="resultCount" class="muted">Not started</span></div>
<div class="tablewrap"><table><thead><tr><th style="width:14%">Result</th><th style="width:31%">Release</th><th>Details</th></tr></thead><tbody id="rows"><tr><td colspan="3" class="empty">Press Organize to begin.</td></tr></tbody></table></div></section>
</main>
<footer id="foot"><button class="primary" id="organize" disabled>▶ Organize</button><button id="cancel" disabled>✕ Cancel</button><button id="undo" disabled>↶ Undo last run</button><button id="reconnect" style="display:none" title="Retry connecting to the local Python application">↻ Retry connection</button><span class="space"></span><span id="message" aria-live="polite" role="status"></span></footer>
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
  for(const id of ["organize","browseSource","browseDest","openLog","openLogFolder","copyResults","openSource","openDest","undo","cancel","doEncode","doChecker","doSplit"])$(id).disabled=true;
  if(!state)$('status').textContent="Connecting to desktop...";
  if(Date.now()-bootTime>8000){
   $('status').textContent="Desktop connection unavailable - retrying";
   $('reconnect').style.display="";
   $('detail').textContent="The WebView2/Python connection is delayed. The organizer keeps retrying without losing your input.";
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
 $('detail').textContent=working?"Organizing all discovered releases automatically. Genuine errors do not block later releases.":"Originals are untouched. Missing audiochecker.log is not a reason to skip a release.";
 for(let id of ["source","dest","browseSource","browseDest"])$(id).disabled=working;
 updateTaskControls();
 $('organize').disabled=working;$('cancel').disabled=!working;$('undo').disabled=working||!state.undo;
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
 if(chosen){$(which).value=chosen;if(which==="source")changedInput();}
}
$('browseSource').onclick=()=>browse("source");$('browseDest').onclick=()=>browse("dest");$('source').addEventListener('input',changedInput);
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
$('organize').onclick=()=>{$('message').textContent="";call("organize",$('source').value,$('dest').value,$('format').value,Number($('encodeWorkers').value),Number($('checkWorkers').value),$('doEncode').checked,$('doChecker').checked,$('doSplit').checked);};
$('cancel').onclick=()=>call("cancel");
$('undo').onclick=()=>{if(confirm("Remove unchanged output from the last run? Original files are not touched."))call("undo");};
$('query').oninput=()=>{query=$('query').value.trim().toLowerCase();render();};
for(let [zone,input] of [["sourceDrop","source"],["destDrop","dest"]]){
let z=$(zone);z.addEventListener("dragover",e=>{e.preventDefault();z.classList.add("drag");});
z.addEventListener("dragleave",()=>z.classList.remove("drag"));
z.addEventListener("drop",e=>{e.preventDefault();z.classList.remove("drag");let f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];let path=f&&(f.pywebviewFullPath||f.path);if(path){$(input).value=path;if(input==='source')changedInput();}else error("Folder drop unsupported here; use Browse.");});
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
    logger = _startup_logger()
    mutex = _single_instance()
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
            (DATA / "startup-error.log").write_text(err, encoding="utf-8")
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
