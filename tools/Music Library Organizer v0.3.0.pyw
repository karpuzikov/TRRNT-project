# Music Library Organizer v0.1.0 - Under construction ⚠️
# Standalone TRRNT organizer. MCC v0.5.18 is the visual reference, not a dependency.
# Sources are READ ONLY. All writes target an explicitly selected separate directory.
from __future__ import annotations

import hashlib
import importlib
import json
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
VERSION = "0.3.0"
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
        return importlib.import_module("webview")
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


def normalized_title(value: str) -> str:
    """Positional brackets only; never assert unknown original/remix credit provenance."""
    value = " ".join(str(value).strip().split())
    value = re.sub(r"\b(?:feat(?:uring)?|ft)\.\s+", "ft. ", value, flags=re.I)
    # Only normalize clearly separated balanced single-level title groups.
    parts = list(re.finditer(r"(?<!\w)[(\[{]([^()\[\]{}]+)[)\]}]", value))
    if not parts or len(parts) > 3:
        return value
    out, last = [], 0
    for i, match in enumerate(parts):
        if i >= 3:
            break
        out.append(value[last:match.start()])
        op, cl = ("()", "[]", "{}")[i]
        out.append(op + match.group(1).strip() + cl)
        last = match.end()
    out.append(value[last:])
    return "".join(out)


def track_filename(number: int, title: str, ext: str) -> str:
    title = normalized_title(title)
    # Windows filename reserved characters cannot appear unescaped.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .")
    if not title or number < 0:
        raise ValueError("Missing valid title/track number")
    return f"{number:02d} {title}{ext}"


def extract_number_title(path: Path):
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
    # A stale title tag must not delete the recording version in a richer filename.
    # Example: filename "01 OK (Extended Version)" with tag "OK".
    if file_title and (not title or (
        file_title.casefold().startswith(title.casefold())
        and len(file_title) > len(title)
        and re.search(r"\b(?:remix|mix|version|edit|extended|live|acoustic|ft\.|feat\.|instrumental|acapella|a cappella)\b",
                      file_title[len(title):], re.I)
    )):
        title = file_title
    return number, title, artist


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


def probe(probe_exe, path):
    p = cmd([probe_exe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,codec_name,sample_fmt",
             "-of", "json", str(path)], 100)
    if p.returncode:
        raise RuntimeError("Audio probe failed: " + p.stderr[-400:])
    streams = json.loads(p.stdout).get("streams", [])
    if not streams:
        raise RuntimeError("No decodable audio: " + str(path))
    return streams[0]


def convert(ff, input_path, output_path, fmt, start=None, end=None, title="", number=0):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    opts = ["-map", "0:a:0", "-map_metadata", "0"]
    if start is not None:
        value = "atrim=start_sample=" + str(start)
        if end is not None:
            value += ":end_sample=" + str(end)
        opts += ["-af", value + ",asetpts=PTS-STARTPTS"]
    if title:
        opts += ["-metadata", "title=" + title]
    if number:
        opts += ["-metadata", "track=" + str(number)]
    codec = ["-c:a", "alac"] if fmt == "ALAC" else ["-c:a", "flac", "-compression_level", "8"]
    p = cmd([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", str(input_path)] + opts + codec + [str(output_path)], timeout=7200)
    if p.returncode:
        raise RuntimeError("Lossless encode failed: " + p.stderr[-700:])
    if pcm_hash(ff, input_path, start, end) != pcm_hash(ff, output_path):
        raise RuntimeError("PCM hashes differ: " + str(input_path))
    return output_path


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


def release_title_normalize(title, release_type=None, inferred_single=False):
    """Single/EP designation always lives at the end, except plural Remixes."""
    value = " ".join(str(title or "").strip().split())
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



def release_identity(folder, files, default_artist="", allow_network=True):
    """Resolve only unambiguous real MusicBrainz releases; otherwise keep source."""
    current = folder.name
    match = _RELEASE_PARTS.fullmatch(current)
    existing_date = match.group("date") if match else ""
    original_title = match.group("title") if match else ""
    idtext, physical_bracket = catalog(current)
    audio = [p for p in files if p.suffix.lower() in AUDIO]
    has_cd = physical_bracket or (
        any(p.suffix.lower() == ".cue" for p in files) and
        any(p.suffix.lower() == ".log" and p.name.casefold() != "audiochecker.log"
            for p in files))
    first = audio[0] if audio else None
    album, artist, tag_barcode = "", default_artist, ""
    if first:
        try:
            from mutagen import File as MutagenFile
            meta = MutagenFile(str(first), easy=True)
            if meta:
                album = (meta.get("album") or [""])[0]
                artist = ((meta.get("albumartist") or meta.get("artist")) or [artist])[0]
                tag_barcode = ((meta.get("barcode") or meta.get("upc")) or [""])[0]
        except Exception:
            pass
    if not original_title:
        alt = _ALT_RELEASE.fullmatch(current)
        original_title = str(album or (alt.group("title") if alt else current)).strip()
        original_title = re.sub(r"\s+\(?\d{4}\)?$", "", original_title).strip()
        existing_date = alt.group("year") if alt else ""
    original_title = original_title or album or current
    barcode = idtext if not physical_bracket and _BARCODE.fullmatch(idtext) else ""
    barcode = barcode or (tag_barcode if _BARCODE.fullmatch(str(tag_barcode)) else "")
    result = {"folder": current, "title": original_title, "date": existing_date,
              "catalog": idtext if idtext and not _BARCODE.fullmatch(idtext) else "",
              "barcode": barcode, "physical": bool(has_cd), "country": "",
              "country_provenance": "", "release_type": "",
              "selected_release": "", "metadata_verified": False, "notes": [],
              "track_count": len(audio)}
    if not allow_network:
        result["notes"].append("Online source-of-truth lookup not attempted.")
        return result
    candidates = []
    try:
        if barcode:
            query = "barcode:" + barcode
        else:
            # Album tags are preferable for legacy names such as "Inna - YO 2019".
            q_title = re.sub(r"\s+-\s+(?:Single|EP)$", "", str(album or original_title), flags=re.I)
            q_artist = str(artist or "").strip()
            if not q_artist:
                result["notes"].append("Artist credit not known; cannot safely search by title.")
                return result
            query = 'release:"' + q_title.replace('"', "") + '" AND artist:"' + q_artist.replace('"', "") + '"'
        params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 100})
        page = mb_json("release/?" + params)
        for entry in page.get("releases", []):
            value = entry.get("barcode") or ""
            if barcode and str(value).lstrip("0") != barcode.lstrip("0"):
                continue
            found_title = entry.get("title") or ""
            sim = difflib.SequenceMatcher(None, _key(original_title),
                                          _key(found_title)).ratio()
            if sim < 0.78:
                continue
            medium = [str(m.get("format", "")).lower() for m in entry.get("media", [])]
            is_cd = any("cd" in x for x in medium)
            digital = any("digital media" in x for x in medium)
            if has_cd and digital and not is_cd:
                continue
            if not has_cd and is_cd and not digital:
                continue
            guessed_year = existing_date[:4]
            actual_year = str(entry.get("date") or "")[:4]
            score = 70 * sim + 20 * bool(barcode) + 12 * (is_cd if has_cd else digital)
            score += 4 * bool(guessed_year and actual_year == guessed_year)
            candidates.append((score, entry))
        if not candidates:
            result["notes"].append("No matching MusicBrainz edition; existing identity retained.")
            return result
        candidates.sort(key=lambda x: (-x[0], str(x[1].get("id"))))
        top_score, chosen = candidates[0]
        if len(candidates) > 1 and abs(candidates[1][0] - top_score) < 2:
            # Do not silently choose between different possible physical editions.
            result["notes"].append("Multiple comparable MusicBrainz editions; catalog/date not auto-selected.")
            return result
        rid = chosen.get("id")
        if not rid:
            return result
        details = mb_json("release/" + str(rid) +
                          "?inc=release-groups+labels+media+artists&fmt=json")
        release_group = details.get("release-group") or {}
        labels = details.get("label-info") or []
        catalogs = [str(x.get("catalog-number", "")).strip() for x in labels]
        catalogs = [x for x in catalogs if x and x.casefold() not in ("none", "n/a", "[none]")]
        release_catalog = catalogs[0] if len(set(catalogs)) == 1 else ""
        if len(set(catalogs)) > 1:
            result["notes"].append("Multiple catalog numbers for release - left unresolved.")
        first_date = release_group.get("first-release-date") or ""
        if first_date and _DATE.fullmatch(first_date):
            result["date"] = first_date
        result["title"] = details.get("title") or chosen.get("title") or original_title
        result["catalog"] = release_catalog or result["catalog"]
        result["barcode"] = details.get("barcode") or barcode
        result["release_type"] = release_group.get("primary-type") or ""
        if has_cd:
            # MusicBrainz release COUNTRY is not evidence of actual disc pressing.
            territory = details.get("country") or ""
            if territory and re.fullmatch(r"[A-Z]{2}|EU|XW|XE", territory):
                result["country"] = territory
                result["country_provenance"] = "MusicBrainz release-territory-fallback (not verified pressing)"
            elif physical_bracket:
                country_match = re.search(r"\[([A-Z]{2}|EU|XW|XE)\s+-", current)
                if country_match:
                    result["country"] = country_match.group(1)
                    result["country_provenance"] = "original folder (unverified physical provenance)"
        result["selected_release"] = str(rid)
        result["metadata_verified"] = True
    except Exception as exc:
        result["notes"].append(str(exc))
    return result


def release_folder_target(info):
    title = release_title_normalize(
        info["title"], info.get("release_type"),
        inferred_single=(not info.get("release_type") and not info.get("physical")
                         and info.get("track_count") == 1
                         and not re.search(r"(?i)\b(?:album|remixes|the remixes)\b",
                                           info.get("title", ""))))
    date = info.get("date") or ""
    ident = info.get("catalog") or info.get("barcode") or ""
    if not date or not _DATE.fullmatch(date):
        info["notes"].append("Initial worldwide release date unresolved - retained original folder.")
        return info["folder"]
    if not ident:
        info["notes"].append("Neither verified catalog nor barcode identified - retained original folder.")
        return info["folder"]
    if info.get("physical"):
        country = info.get("country")
        if not country:
            info["notes"].append("CD country/manufacture or documented release territory unresolved.")
            return info["folder"]
        suffix = country + " - " + ident
    else:
        suffix = ident
    candidate = date + " - " + title + " [" + suffix + "]"
    if any(x in candidate for x in '<>:"/\\|?*'):
        info["notes"].append("Identifier/title contains Windows-forbidden characters.")
        return info["folder"]
    return candidate


# --- Authentic AudioChecker runner (never synthesize a fake report). ---
def locate_checker(saved=""):
    paths = []
    if saved:
        paths.append(Path(saved))
    command = shutil.which("achkgui.exe")
    if command:
        paths.append(Path(command))
    try:
        paths.append(Path(sys.argv[0]).resolve().parent / "achkgui.exe")
    except Exception:
        pass
    paths.extend([Path.home() / "Desktop" / "AudioChecker" / "achkgui.exe",
                  Path.home() / "Downloads" / "AudioChecker" / "achkgui.exe",
                  documents() / "AudioChecker" / "achkgui.exe"])
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(name):
            paths.append(Path(os.environ[name]) / "AudioChecker" / "achkgui.exe")
    for path in paths:
        if path.is_file():
            return path.resolve()
    return None


def is_real_checker_log(log_path, tracks):
    try:
        lines = log_path.read_text(encoding="cp1252", errors="replace").splitlines()
        body = "\n".join(lines).lower()
        if "audiochecker" not in body or not ("cdda" in body or "mpeg" in body):
            return False
        if not lines or not re.fullmatch(r"\d+", lines[-1].strip()):
            return False
        return all(track.name.lower() in body for track in tracks)
    except OSError:
        return False


def windows_for_pid(pid):
    if os.name != "nt":
        raise RuntimeError("AudioChecker GUI automation works on Windows only.")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    seen = []
    cbtype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @cbtype
    def visit(hwnd, _):
        process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value == pid and user32.IsWindowVisible(hwnd):
            seen.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    return seen


def start_checker_window(hwnd):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    children = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def visit(child, _):
        children.append(child)
        return True
    user32.EnumChildWindows(hwnd, visit, 0)
    for child in children:
        label = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(child, label, 512)
        user32.GetClassNameW(child, cls, 256)
        name = label.value.strip().upper()
        if "button" in cls.value.lower() and (name in ("START", "СТАРТ") or name.startswith("START ")):
            user32.SendMessageW(child, 0x00F5, 0, 0)
            return True
    # Some AudioChecker builds use the main-window default Enter action.
    try:
        user32.ShowWindow(hwnd, 5)
        user32.SetForegroundWindow(hwnd)
        time.sleep(.15)
        user32.keybd_event(0x0D, 0, 0, 0)
        user32.keybd_event(0x0D, 0, 2, 0)
        return True
    except Exception:
        return False


def genuine_audiochecker(checker, folder, tracks, stop_event):
    """Run achkgui.exe on real files; require an actual complete report."""
    if not tracks:
        raise RuntimeError("No FLAC tracks available for AudioChecker.")
    existing = folder / "audiochecker.log"
    if existing.exists() and is_real_checker_log(existing, tracks):
        return existing
    if existing.exists():
        raise RuntimeError("Existing unverified AudioChecker log; do not overwrite it.")
    creationflags = hidden() if IS_WIN else 0
    proc = subprocess.Popen([str(checker)] + [str(p) for p in tracks],
                            cwd=str(checker.parent), creationflags=creationflags)
    try:
        deadline = time.monotonic() + 35
        windows = []
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise InterruptedError("AudioChecker cancelled.")
            windows = windows_for_pid(proc.pid)
            if windows:
                break
            if proc.poll() is not None:
                raise RuntimeError("AudioChecker closed without scanning.")
            time.sleep(.25)
        if not windows or not start_checker_window(windows[0]):
            raise RuntimeError("AudioChecker window/Start action unavailable.")
        deadline = time.monotonic() + min(4 * 3600, max(1200, len(tracks) * 480))
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise InterruptedError("AudioChecker cancelled.")
            if existing.exists() and is_real_checker_log(existing, tracks):
                before = existing.stat().st_size
                time.sleep(.7)
                if existing.stat().st_size == before:
                    return existing
            if proc.poll() is not None and not existing.exists():
                raise RuntimeError("AudioChecker closed without generating a report.")
            time.sleep(.5)
        raise RuntimeError("AudioChecker timed out.")
    finally:
        if IS_WIN:
            import ctypes
            for hwnd in windows_for_pid(proc.pid):
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def check_web_release(dest, fmt, ff, checker, stop_event):
    """Authentic report over output FLAC or losslessly converted ALAC tracks."""
    byfolder = {}
    for base, _, names in os.walk(dest):
        folder = Path(base)
        tracks = [folder / x for x in names if Path(x).suffix.lower() in (".flac", ".m4a")]
        if tracks:
            byfolder[folder] = sorted(tracks)
    if not byfolder:
        raise RuntimeError("No output audio for AudioChecker.")
    if not checker:
        raise RuntimeError("achkgui.exe not found: choose AudioChecker executable in the UI.")
    if not IS_WIN:
        raise RuntimeError("The authentic AudioChecker GUI requires Windows.")
    provenance = []
    for folder, tracks in byfolder.items():
        if stop_event.is_set():
            raise InterruptedError("Stopped before AudioChecker.")
        if fmt == "FLAC":
            checked = genuine_audiochecker(checker, folder, tracks, stop_event)
            provenance.append({"report": str(checked.relative_to(dest)), "checked_format": "FLAC"})
            continue
        # Original GUI AudioChecker cannot read ALAC m4a. Decode to a temporary
        # lossless FLAC working set with matched PCM, check that set transparently.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="mlo_checker_", dir=str(DATA)) as temp_dir:
            work = Path(temp_dir)
            equivalents = []
            for p in tracks:
                temp_flac = work / (p.stem + ".flac")
                convert(ff, p, temp_flac, "FLAC")
                equivalents.append(temp_flac)
            real_log = genuine_audiochecker(checker, work, equivalents, stop_event)
            result = folder / "audiochecker.log"
            if result.exists():
                if not is_real_checker_log(result, equivalents):
                    raise RuntimeError("Existing report incompatible with selected ALAC output.")
            else:
                shutil.copy2(real_log, result)
            provenance.append({"report": str(result.relative_to(dest)),
                               "checked_format": "temporary FLAC from output ALAC, PCM-verified",
                               "source_output": [str(p.relative_to(dest)) for p in tracks]})
    return provenance

class Organizer:
    """One action: input -> organized output. No manual release-review gate."""
    def __init__(self):
        ensure_data()
        settings = read_json(SETTINGS, {})
        self.source = str(settings.get("source", ""))
        self.dest = str(settings.get("dest", ""))
        self.fmt = str(settings.get("format", "ALAC"))
        self.checker_path = str(settings.get("checker_path", ""))
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
                    "progress": self.progress, "checker_path": self.checker_path, "counts": dict(self.counts),
                    "results": list(self.results), "error": self.error,
                    "activity": list(self.activity), "undo": UNDO.is_file()}

    def browse(self, which):
        try:
            import webview
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return str(result[0])
        except Exception as exc:
            self.info("Folder picker failed: " + str(exc))
        return ""

    def browse_checker(self):
        try:
            import webview
            value = self.window.create_file_dialog(
                webview.OPEN_DIALOG, file_types=("Executables (*.exe)",))
            return str(value[0]) if value else ""
        except Exception as exc:
            self.info("AudioChecker picker failed: " + str(exc))
            return ""

    def open_folder(self, path):
        target = Path(str(path)).expanduser()
        if not target.is_dir():
            return {"error": "Folder does not exist."}
        try:
            if IS_WIN:
                os.startfile(str(target))
            else:
                subprocess.Popen(["xdg-open", str(target)])
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

    def organize(self, source, dest, fmt, checker_path=""):
        with self.lock:
            if self.running:
                return {"error": "Another operation is running."}
            try:
                src, dst = check_paths(str(source), str(dest), str(fmt))
            except Exception as exc:
                return {"error": str(exc)}
            existing = read_json(UNDO, {})
            if existing.get("pending"):
                return {"error": "Previous run has an interrupted output checkpoint. Resolve before continuing."}
            self.source, self.dest, self.fmt = str(src), str(dst), str(fmt)
            self.checker_path = str(checker_path or "").strip()
            if self.checker_path and not Path(self.checker_path).is_file():
                return {"error": "AudioChecker executable path does not exist."}
            write_json(SETTINGS, {"source": self.source, "dest": self.dest,
                                  "format": self.fmt, "checker_path": self.checker_path})
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
            checker = locate_checker(self.checker_path)
            if not checker:
                self.info("AudioChecker executable not found: digital output will be PARTIAL, not DONE.")
            for index, folder in enumerate(releases, 1):
                if self.stop.is_set():
                    self.status = "Cancelled. Completed output can be undone."
                    break
                folder = folder.resolve(strict=True)
                result = {"name": folder.name, "source": str(folder), "target": "",
                          "status": "", "message": "", "warnings": [],
                          "metadata": {}, "checker": []}
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
                        result["status"] = "skipped"
                        result["message"] = "Output already exists - not overwritten."
                        self.counts["skipped"] += 1
                    else:
                        if info["physical"]:
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
                        try:
                            self.status = f"Organizing {index}/{len(releases)}: {out_name}"
                            self._export_release(folder, tmp, fmt, ff, ffprobe)
                            # Actual AudioChecker is performed before finalizing the release.
                            if not info["physical"]:
                                try:
                                    self.status = f"AudioChecker {index}/{len(releases)}: {out_name}"
                                    result["checker"] = check_web_release(tmp, fmt, ff, checker, self.stop)
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
                                            "created": [], "pending": None}
                            rec = {"path": str(target), "files": hashes}
                            manifest["pending"] = rec
                            write_json(UNDO, manifest)
                            os.rename(tmp, target)
                            manifest["created"].append(rec)
                            manifest["pending"] = None
                            write_json(UNDO, manifest)
                            incomplete = bool(not info["metadata_verified"] or
                                any("unresolved" in n.lower() or "not auto-selected" in n.lower() for n in info["notes"]) or
                                (info["physical"] and info["country_provenance"] != "verified-pressing") or
                                (not info["physical"] and not info.get("catalog")) or
                                (not info["physical"] and not result["checker"]))
                            if incomplete:
                                result["status"] = "partial"
                                self.counts["partial"] += 1
                                result["message"] = str(len(output_files)) + " output files, requirements unfinished."
                            else:
                                result["status"] = "done"
                                self.counts["done"] += 1
                                result["message"] = str(len(output_files)) + " verified output files."
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
            for k, rec in enumerate(records):
                if self.stop.is_set():
                    raise InterruptedError("Cancelled.")
                start = ticks[k] * (rate // 75)
                end = ticks[k + 1] * (rate // 75) if k + 1 < len(records) else None
                name = track_filename(rec["number"], rec["title"], extension)
                output = dest / sub / name
                if output.exists():
                    raise RuntimeError("Two tracks resolve to same output path: " + str(output))
                convert(ff, src_img, output, fmt, start, end, rec["title"], rec["number"])
                names[rec["number"]] = (name, ticks[k], ticks[k + 1] if k + 1 < len(ticks) else None)
            new_cues[cp] = image_cue_text(parsed, names)
        # Copy or transcode normal track audio. Map old paths to newly named paths.
        for fp in files:
            if self.stop.is_set():
                raise InterruptedError("Cancelled.")
            if fp.resolve() in source_images or fp.suffix.lower() not in AUDIO:
                continue
            number, title, _ = extract_number_title(fp)
            if number <= 0 or not title:
                raise RuntimeError("Track has no number/title: " + str(fp))
            new = dest / fp.parent.relative_to(source) / track_filename(number, title, extension)
            if new.exists():
                # Multiple source codecs/exports may describe the very same track.
                # Deduplicate ONLY if decoded lossless PCM is identical.
                if pcm_hash(ff, fp) == pcm_hash(ff, new):
                    mapping[fp.resolve()] = new
                    continue
                raise RuntimeError("Different recordings resolve to the same track filename: " +
                                   str(fp) + " -> " + str(new))
            new.parent.mkdir(parents=True, exist_ok=True)
            old_info = probe(ffprobe, fp)
            # Extension is only a hint; probe codec to avoid passing through a mislabeled file.
            codec = str(old_info.get("codec_name", "")).lower()
            if codec not in {"alac", "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
                             "pcm_f32le", "pcm_f64le", "ape", "wavpack", "pcm_s16be",
                             "pcm_s24be", "pcm_s32be"}:
                raise RuntimeError("Source codec is not verified lossless: " + codec)
            if (fmt == "ALAC" and codec == "alac") or (fmt == "FLAC" and codec == "flac"):
                shutil.copy2(fp, new)
                if sha(fp) != sha(new):
                    raise RuntimeError("Copied audio differs: " + str(fp))
            else:
                convert(ff, fp, new, fmt, title=title, number=number)
            mapping[fp.resolve()] = new
        # Preserve CUE text, rewriting only correct FILE paths; keep originals in source.
        destinations = set()
        for fp in files:
            if fp.resolve() in source_images or fp.suffix.lower() in AUDIO:
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
                    refs = {}
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
                                raise RuntimeError("Legacy CUE uses shared missing image; no safe split boundary.")
                            if rec["index"].get(1, -1) != 0:
                                raise RuntimeError("Legacy CUE has nonzero indexes; cannot rewrite to split audio safely.")
                            refs[key] = os.path.relpath(matches[0], target.parent).replace("/", "\\")
                        else:
                            refs[key] = os.path.relpath(mapping[src_audio], target.parent).replace("/", "\\")
                    rewritten = normal_cue_text(cue, refs)
                target.write_text(rewritten, encoding="utf-8", newline="\r\n" if IS_WIN else "\n")
            else:
                shutil.copy2(fp, target)
                if sha(fp) != sha(target):
                    raise RuntimeError("Copied support file differs: " + str(fp))
        # Ensure physical cue(s) and logs preserved or explicitly absent.
        for fp in files:
            if fp.suffix.lower() == ".cue" and not (dest / fp.parent.relative_to(source) / (cat + ".cue" if physical and valid_cat else fp.name)).is_file():
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


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Music Library Organizer</title>
<style>
:root{color-scheme:dark;--bg:#0b0c0f;--panel:#11141a;--field:#181c23;--line:#2a303a;--text:#f3f6fb;--muted:#9ca7b8;--blue:#66a9ff;--yellow:#f59e0b;--green:#22c55e;--red:#ef4444}
*{box-sizing:border-box}html,body{height:100%;width:100%;margin:0;overflow:hidden;background:var(--bg);color:var(--text);font:13px "Segoe UI Variable","Segoe UI",system-ui,sans-serif}body{display:flex;flex-direction:column}
button,input,select{font:inherit}button{user-select:none}
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
button.folder{width:34px;padding:0}.options{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-top:1px solid #252b34;padding-top:10px}.options label{font-weight:650}select{min-width:110px}
#progress{padding:12px 14px}.progress-head{display:flex;align-items:center;justify-content:space-between;gap:12px;font-weight:650;min-height:17px}
.track{height:9px;background:#252d36;border-radius:30px;margin:11px 0;overflow:hidden}.fill{height:100%;width:0;background:var(--blue);transition:width .2s}
.counts{display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.counts b{font-size:16px;color:var(--text)}#detail{font-size:11px;color:#c2cddd;margin-top:8px}
#results{min-height:0;display:flex;flex-direction:column;overflow:hidden}.tools{display:flex;align-items:center;gap:8px;padding:0 13px 10px}.tools input{max-width:280px}.tablewrap{flex:1;overflow:auto;min-height:50px}
table{border-collapse:collapse;table-layout:fixed;width:100%}th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid #252b35;word-break:break-word}th{position:sticky;top:0;background:#171b22;color:var(--muted);font-size:11px}tr:hover{background:#19212a}
.done{color:#a8f5bf}.partial{color:#ffd88b}.skipped{color:#b6c4d2}.failed{color:#ffb0b6}.warning{color:#ffd88b}.small{color:var(--muted);font-size:11px;margin-top:3px}.empty{padding:25px;text-align:center;color:var(--muted)}
#foot{height:58px;flex:0 0 58px;background:#101319;border-top:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 18px}
#message{color:var(--yellow);max-width:42vw;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:11px}
.drag{outline:2px dashed var(--blue);outline-offset:-3px}
@media(max-width:750px){main{padding:8px;gap:8px}.counts{gap:9px}#top{padding:0 9px}#top .muted{display:none}#foot{padding:0 8px}th,td{padding:6px}}
</style></head><body>
<header id="top"><strong id="brand">Music Library Organizer</strong><span class="muted">v0.3.0 - Under construction ⚠️</span><span class="space"></span><span class="muted">Automatic · MCC-style · originals untouched</span></header>
<main>
<section class="card"><div class="head"><strong>Input and output folders</strong><span class="muted">Choose folders, then Organize. No review or second confirmation.</span></div><div class="form">
<label for="source" class="label">Input collection</label><div class="path" id="sourceDrop"><button class="folder" id="openSource" title="Open input folder" aria-label="Open input folder">📁</button><input type="text" id="source" aria-label="Input folder" placeholder="Choose input folder..."><button id="browseSource">Browse...</button></div>
<label for="dest" class="label">Organized output</label><div class="path" id="destDrop"><button class="folder" id="openDest" title="Open output folder" aria-label="Open output folder">📁</button><input type="text" id="dest" aria-label="Output folder" placeholder="Choose separate output folder..."><button id="browseDest">Browse...</button></div>
<div class="options"><label for="format">Output format</label><select id="format" aria-label="Output format"><option value="ALAC">ALAC (.m4a)</option><option value="FLAC">FLAC (.flac)</option></select><span class="muted">Genuine AudioChecker report required; unverified output is labeled PARTIAL.</span></div>
<label for="checker">AudioChecker</label><input id="checker" type="text" placeholder="achkgui.exe path (optional if discoverable)" style="max-width:420px" aria-label="AudioChecker executable path"><button id="browseChecker" title="Locate installed AudioChecker executable">Browse...</button></div></div></section>
<section class="card" id="progress"><div class="progress-head"><span id="status" role="status" aria-live="polite">Ready</span><span id="percent">0%</span></div><div class="track" id="progressBar" role="progressbar" aria-label="Organization progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="fill" id="fill"></div></div>
<div class="counts"><span><b id="total">0</b> found</span><span><b id="done">0</b> compliant</span><span><b id="partial">0</b> partial</span><span><b id="skipped">0</b> already present</span><span><b id="failed">0</b> failed</span><span><b id="warnings">0</b> warnings</span></div>
<div id="detail">Input files remain untouched. Any genuine errors will appear below.</div></section>
<section class="card" id="results"><div class="head"><strong>Results</strong><span class="muted">Automatic results. No approval step.</span></div><div class="tools"><input type="text" id="query" placeholder="Filter results..." aria-label="Filter results"><button id="openLog">↗ Open log</button><button id="openOutput">📁 Open output</button><span class="space"></span><span id="resultCount" class="muted">Not started</span></div>
<div class="tablewrap"><table><thead><tr><th style="width:14%">Result</th><th style="width:31%">Release</th><th>Details</th></tr></thead><tbody id="rows"><tr><td colspan="3" class="empty">Press Organize to begin.</td></tr></tbody></table></div></section>
</main>
<footer id="foot"><button class="primary" id="organize">▶ Organize</button><button id="cancel" disabled>✕ Cancel</button><button id="undo" disabled>↶ Undo last run</button><span class="space"></span><span id="message" aria-live="polite" role="status"></span></footer>
<script>
let state=null,initialized=false,working=false,query="";
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(method,...args){for(let j=0;j<120;j++){let a=window.pywebview&&window.pywebview.api;if(a&&typeof a[method]==="function")return a[method](...args);await new Promise(r=>setTimeout(r,50));}throw Error("Desktop connection failed.");}
function error(e){$('message').textContent=String(e&&e.message||e);}
async function call(name,...args){try{const out=await api(name,...args);if(out&&out.error)throw Error(out.error);await refresh();return out;}catch(e){error(e);return null;}}
function render(){
 if(!state)return;
 if(!initialized){$('source').value=state.source||"";$('dest').value=state.dest||"";$('format').value=state.format||"ALAC";$('checker').value=state.checker_path||"";initialized=true;}
 working=!!state.running;
 $('status').textContent=state.status||"Ready";
 const pct=Number(state.progress||0);$('percent').textContent=pct+"%";$('fill').style.width=pct+"%";$('progressBar').setAttribute("aria-valuenow",String(pct));
 let c=state.counts||{};for(let name of ["total","done","partial","skipped","failed","warnings"])$(name).textContent=c[name]||0;
 $('detail').textContent=working?"Organizing all discovered releases automatically. Genuine errors do not block later releases.":"Originals are untouched. Missing audiochecker.log is not a reason to skip a release.";
 for(let id of ["source","dest","format","checker","browseChecker","browseSource","browseDest"])$(id).disabled=working;
 $('organize').disabled=working;$('cancel').disabled=!working;$('undo').disabled=working||!state.undo;
 $('openOutput').disabled=!$('dest').value.trim();
 if(state.error)$('message').textContent=state.error;
 let list=(state.results||[]).filter(x=>[x.name,x.message,(x.warnings||[]).join(" ")].join(" ").toLowerCase().includes(query));
 $('resultCount').textContent=list.length+" shown / "+(state.results||[]).length+" processed";
 $('rows').innerHTML=list.slice(-500).map(x=>'<tr><td class="'+esc(x.status)+'"><b>'+esc(x.status.toUpperCase())+'</b></td><td><b>'+esc(x.name)+'</b><div class="small">'+esc(x.target)+'</div></td><td>'+esc(x.message)+(x.warnings&&x.warnings.length?'<div class="small warning">'+esc(x.warnings.join(" | "))+'</div>':"")+'</td></tr>').join("")||'<tr><td colspan="3" class="empty">'+(working?"Working...":"No releases processed yet.")+'</td></tr>';
}
async function refresh(){try{state=await api("state");render();}catch(e){error(e);}}
async function browse(which){let selected=await call("browse",which);if(selected)$(which).value=selected;}
$('browseSource').onclick=()=>browse("source");$('browseDest').onclick=()=>browse("dest");$('browseChecker').onclick=async()=>{let x=await call('browse_checker');if(x)$('checker').value=x;};
$('openSource').onclick=()=>call("open_folder",$('source').value);
$('openDest').onclick=()=>call("open_folder",$('dest').value);
$('openOutput').onclick=()=>call("open_folder",$('dest').value);
$('openLog').onclick=()=>call("open_log");
$('organize').onclick=()=>{$('message').textContent="";call("organize",$('source').value,$('dest').value,$('format').value,$('checker').value);};
$('cancel').onclick=()=>call("cancel");
$('undo').onclick=()=>{if(confirm("Remove unchanged output from the last run? Original files are not touched."))call("undo");};
$('query').oninput=()=>{query=$('query').value.trim().toLowerCase();render();};
for(let [zone,input] of [["sourceDrop","source"],["destDrop","dest"]]){
let z=$(zone);z.addEventListener("dragover",e=>{e.preventDefault();z.classList.add("drag");});
z.addEventListener("dragleave",()=>z.classList.remove("drag"));
z.addEventListener("drop",e=>{e.preventDefault();z.classList.remove("drag");let f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];let path=f&&(f.pywebviewFullPath||f.path);if(path)$(input).value=path;else error("Folder drop unsupported here; use Browse.");});
}
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&state&&state.running)call("cancel");});
setInterval(refresh,900);refresh();
</script></body></html>"""


def main():
    ensure_data()
    webview = load_ui()
    app = Organizer()
    window = webview.create_window(
        f"{APP} v{VERSION} - Under construction ⚠️", html=HTML,
        width=1350, height=850, min_size=(920, 650),
        background_color="#0b0c0f", text_select=True, zoomable=True,
        js_api=app)
    app.window = window
    webview.start(gui="edgechromium", debug=False)


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
