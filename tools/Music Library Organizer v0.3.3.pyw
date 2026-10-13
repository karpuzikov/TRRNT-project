# Music Library Organizer v0.1.0 - Under construction ⚠️
# Standalone TRRNT organizer. MCC v0.5.18 is the visual reference, not a dependency.
# Sources are READ ONLY. All writes target an explicitly selected separate directory.
from __future__ import annotations

import hashlib
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
VERSION = "0.3.3"
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


# Mandatory encoder backends. FFmpeg is used ONLY for decoding WAV intermediates,
# probing and decoded-PCM verification; NEVER for ALAC/FLAC encoding.
_ENCODER_LOCK = threading.RLock()
_ENCODER_CACHE = {}
_FLAC_VERSION = "1.5.0"
_FLAC_ZIP_URL = "https://ftp.osuosl.org/pub/xiph/releases/flac/flac-1.5.0-win.zip"
_FLAC_ZIP_SHA256 = "53f1500f0d6e7c61379d7fee50d4a9f7f504c650009506d9ba015530d76c0dde"
_QAAC_FALLBACK_TAG = "v3.07"
_QAAC_FALLBACK_ARCHIVE = "qaac_3.07.zip"


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
            if matches[0].get("browser_download_url") == verified:
                tag, archive = t, version
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
        # Accept an existing exact-name binary; never silently use qaac.exe,
        # ffmpeg.exe, or an alternative codec backend.
        existing = shutil.which(name)
        if existing and Path(existing).is_file():
            _ENCODER_CACHE[fmt] = existing
            return existing
        if not IS_WIN:
            raise RuntimeError(name + " is required; auto-bootstrap supports Windows.")
        ensure_data()
        url = _qaac_official_url() if fmt == "ALAC" else _FLAC_ZIP_URL
        try:
            binary = _fetch_official_archive(
                url, _FLAC_ZIP_SHA256 if fmt == "FLAC" else None)
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
    bits = int(details.get("bits_per_raw_sample") or details.get("bits_per_sample") or 0)
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


def _copy_audio_metadata(src_path, dst_path, fmt, title, number):
    """Preserve supported tags and album art after WAV -> native encoding."""
    from mutagen import File as MutagenFile
    try:
        source = MutagenFile(str(src_path), easy=True)
    except Exception as exc:
        raise RuntimeError("Failed to read source metadata: " + str(exc))
    # WAV source may have no tags; native encoded outputs are still valid.
    source_tags = dict(source.tags or {}) if source is not None else {}
    if title:
        source_tags["title"] = [title]
    if number:
        source_tags["tracknumber"] = [str(number)]
    dest = MutagenFile(str(dst_path), easy=True)
    if dest is None:
        raise RuntimeError("Native encoder produced unrecognized output: " + str(dst_path))
    if dest.tags is None:
        dest.add_tags()
    common = {"title", "artist", "album", "albumartist", "tracknumber",
              "discnumber", "date", "genre", "composer", "comment",
              "isrc", "barcode", "organization", "copyright", "totaltracks",
              "totaldiscs", "albumartistsort", "artistsort", "musicbrainz_albumid",
              "musicbrainz_trackid", "musicbrainz_releasegroupid", "label"}
    unsupported = []
    for key, values in source_tags.items():
        if not values:
            continue
        key = str(key).lower()
        if key not in common:
            unsupported.append(key)
            continue
        try:
            dest[key] = list(values) if isinstance(values, (list, tuple)) else [str(values)]
        except (ValueError, KeyError):
            unsupported.append(key)
    dest.save()
    # Cover artwork must survive FLAC <-> ALAC, not just tags.
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover
    raw = MutagenFile(str(src_path))
    if raw is not None:
        art = []
        if isinstance(raw, FLAC):
            for pic in raw.pictures:
                art.append((pic.data, pic.mime))
        elif isinstance(raw, MP4) and raw.tags:
            for cover in raw.tags.get("covr", []):
                mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                art.append((bytes(cover), mime))
        if art:
            if fmt == "ALAC":
                output = MP4(str(dst_path))
                if output.tags is None:
                    output.add_tags()
                output.tags["covr"] = [
                    MP4Cover(data, imageformat=(
                        MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG))
                    for data, mime in art]
                output.save()
            else:
                output = FLAC(str(dst_path))
                output.clear_pictures()
                for data, mime in art:
                    picture = Picture()
                    picture.data, picture.mime = data, mime
                    picture.type = 3
                    output.add_picture(picture)
                output.save()
    if unsupported:
        # Never silently claim a metadata-complete transcoding when source
        # contains custom fields that we could not preserve.
        raise RuntimeError("Some source metadata needs a verified mapping: " +
                           ", ".join(sorted(set(unsupported))[:12]))


def convert(ff, input_path, output_path, fmt, start=None, end=None,
            title="", number=0, ffprobe=None):
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
            if pcm_hash(ff, input_path, start, end) != pcm_hash(ff, output_path):
                raise RuntimeError("Source and encoded decoded-PCM SHA-256 differ.")
            _copy_audio_metadata(input_path, output_path, fmt, title, number)
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
    result = {"folder": current, "title": title, "date": date,
              "catalog": ident if ident and not _BARCODE.fullmatch(ident) else "",
              "barcode": barcode, "physical": is_cd, "country": "",
              "country_provenance": "", "release_type": "",
              "selected_release": "", "metadata_verified": False,
              "edition_date": "",
              "notes": [], "track_count": len(audios),
              "discids": [], "lookup_sources": []}
    if not allow_network:
        result["notes"].append("Metadata lookup not attempted.")
        return result
    try:
        disc_candidates = []
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
            possible = disc_candidates
            if barcode:
                confirmed = {str(x.get("id")) for x in barcode_entries}
                possible = [rid for rid in possible if rid in confirmed]
                if not possible:
                    result["notes"].append("DiscID candidates conflict with the barcode - no edition selected.")
                    return result
            if len(possible) != 1:
                result["notes"].append(str(len(possible)) +
                                       " DiscID-matching physical editions; selection is ambiguous.")
                return result
            chosen = {"id": possible[0]}
        else:
            # No exact DiscID: use exact-barcode candidates when available,
            # otherwise search by album+artist and require unique strong match.
            entries = barcode_entries
            if not barcode:
                if not (title and artist):
                    result["notes"].append("Neither DiscID, barcode nor complete artist/title lookup available.")
                    return result
                query = 'release:"' + str(album or title).replace('"', "") + \
                        '" AND artist:"' + str(artist).replace('"', "") + '"'
                url = "release/?" + urllib.parse.urlencode(
                    {"query": query, "fmt": "json", "limit": "100"})
                entries = mb_json(url).get("releases", [])
                result["lookup_sources"].append("MusicBrainz artist/title")
            compatible = []
            for entry in entries:
                similarity = difflib.SequenceMatcher(
                    None, _key(title), _key(entry.get("title") or "")).ratio()
                if similarity < 0.82:
                    continue
                rid = str(entry.get("id") or "")
                if not rid:
                    continue
                # Require the concrete medium (search results may omit it).
                detail = mb_json("release/" + rid +
                                 "?inc=release-groups+labels+media+artists&fmt=json")
                formats = [str(m.get("format") or "").casefold()
                           for m in detail.get("media", [])]
                if is_cd and not any("cd" in x for x in formats):
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
        result["title"] = str(details.get("title") or title)
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
        edition_date = str(info.get("edition_date") or "")
        if (_DATE.fullmatch(edition_date) and edition_date[:4] != date[:4]):
            # The leading date is the original worldwide release date;
            # a CD edition pressed/reissued in a different year belongs last.
            suffix += " - " + edition_date[:4]
    else:
        suffix = ident
    candidate = date + " - " + title + " [" + suffix + "]"
    if any(x in candidate for x in '<>:"/\\|?*'):
        info["notes"].append("Identifier/title contains Windows-forbidden characters.")
        return info["folder"]
    return candidate


# --- Authentic AudioChecker runner (never synthesize a fake report). ---
# The user's Audio_Checker_UTF8_Multicore.zip contains the original
# FLAC decoder and auCDtect binary. No manual achkgui.exe location needed.
_CHECKER_FILES = {
    "Codecs/FLAC/flac.exe": "Codecs/FLAC/flac.exe",
    "Codecs/Analyzer/aucdtect.exe": "Codecs/Analyzer/aucdtect.exe",
    "CDRip3.dll": "CDRip3.dll",
    "Codecs/FLAC/flac.exe.manifest": "Codecs/FLAC/flac.exe.manifest",
    "Codecs/Analyzer/aucdtect.exe.manifest": "Codecs/Analyzer/aucdtect.exe.manifest",
}


def _checker_archive_candidates():
    bases = [Path(sys.argv[0]).resolve().parent, Path.cwd(),
             Path.home() / "Downloads", documents() / "Downloads", DATA]
    found = []
    for base in bases:
        if not base.is_dir():
            continue
        for name in ("Audio_Checker_UTF8_Multicore.zip",
                     "Audio_Checker_UTF8_Multicore (1).zip"):
            path = base / name
            if path.is_file():
                found.append(path)
        found.extend(sorted(base.glob("Audio_Checker_UTF8_Multicore*.zip"))[:5])
    return list(dict.fromkeys(found))


def setup_checker():
    """Read supplied archive (not downloaded from an untrusted mirror).
    Extract only known files to this application's isolated data directory.
    """
    location = DEPS / "AudioChecker"
    exe = location / "Codecs" / "Analyzer" / "aucdtect.exe"
    decoder = location / "Codecs" / "FLAC" / "flac.exe"
    if exe.is_file() and decoder.is_file():
        return {"analyzer": exe, "flac": decoder}
    # Also accept user's pre-extracted checker in the script/Downloads directory.
    for base in [Path(sys.argv[0]).resolve().parent, Path.home() / "Downloads", DATA]:
        for path in (base / "Audio_Checker_UTF8_Multicore", base):
            analyzer = path / "Codecs" / "Analyzer" / "aucdtect.exe"
            flac = path / "Codecs" / "FLAC" / "flac.exe"
            if analyzer.is_file() and flac.is_file():
                return {"analyzer": analyzer, "flac": flac}
    for archive in _checker_archive_candidates():
        try:
            with zipfile.ZipFile(archive) as zf:
                contents = {}
                for name in zf.namelist():
                    normalized = name.replace("\\", "/")
                    for suffix, dest in _CHECKER_FILES.items():
                        if normalized.endswith("/" + suffix):
                            contents[dest] = name
                if not all(key in contents for key in (
                        "Codecs/FLAC/flac.exe", "Codecs/Analyzer/aucdtect.exe")):
                    continue
                for destination, internal in contents.items():
                    data = zf.read(internal)
                    # Size limits guard corrupt or hostile archives.
                    if not data or len(data) > 16 * 1024 * 1024:
                        raise RuntimeError("Unexpected AudioChecker executable size.")
                    output = location.joinpath(*destination.split("/"))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
            if exe.is_file() and decoder.is_file():
                return {"analyzer": exe, "flac": decoder}
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
    return None


_AC_RESULT = re.compile(r"This track looks like\s+(CDDA|MPEG)\s+with probability\s+(\d+)%", re.I)


def _checker_job(track, tools, ff, stop_event):
    if stop_event.is_set():
        raise InterruptedError("Audio checking cancelled.")
    with tempfile.TemporaryDirectory(prefix="MLO_AudioCheck_", dir=str(DATA)) as td:
        wav = Path(td) / "decode.wav"
        if track.suffix.lower() == ".flac":
            proc = cmd([str(tools["flac"]), str(track), "-d", "--output-name=" + str(wav)], 3600)
        else:
            # auCDtect analyzes decoded PCM, never the compressed M4A data.
            proc = cmd([ff, "-hide_banner", "-v", "error", "-nostdin", "-y",
                        "-i", str(track), "-map", "0:a:0", "-c:a", "pcm_s16le",
                        str(wav)], 3600)
        if proc.returncode or not wav.is_file():
            raise RuntimeError("Could not decode for auCDtect: " + (proc.stderr or "")[-350:])
        check = cmd([str(tools["analyzer"]), "-mS0", str(wav)], 3600)
        output = check.stdout + "\n" + check.stderr
        match = _AC_RESULT.search(output)
        if match:
            return track, match.group(1).upper(), int(match.group(2)), ""
        if "Could not qualify the source of this track" in output:
            return track, "UNKNOWN", None, ""
        return track, "ERROR", None, ("auCDtect error: " + output[-450:])


def _checker_checksum(items):
    # Matches checksum implemented by the user-supplied AudioChecker_Multicore.pyw
    entries = []
    for track, category, prob, _ in items:
        if category not in ("CDDA", "MPEG") or prob is None:
            category, pct = "ERROR", "-1%"
        else:
            pct = f"{prob}%"
        entries.append(f"{track.name}|{category}|{pct}")
    encoded = "\r".join(entries).encode("utf-8")
    value = 0
    for symbol in encoded:
        value = ((value << 4) + (symbol ^ 0xD1)) & 0xFFFFFFFF
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
            value &= (~high) & 0xFFFFFFFF
    return value


def _checker_report(folder, items):
    rows = [
        "AUDIOCHECKER v2.0 beta (build 457) - by Dester - opdester@freemail.hu",
        "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", "-=== DO NOT EDIT THIS FILE! ===-", "",
        "Path: ...\\" + folder.name, "",
    ]
    width = len(str(len(items)))
    cd_scores = []
    for index, (track, kind, pct, problem) in enumerate(items, 1):
        prefix = f"{index:0{width}d}"
        if kind in ("CDDA", "MPEG") and pct is not None:
            rows.append(f"{prefix} -=- {track.name} -=- {kind} ({pct}%)")
            cd_scores.append(pct if kind == "CDDA" else 100-pct)
        else:
            rows.append(f"{prefix} -=- {track.name} -=- ERROR")
    if len(cd_scores) > 1:
        rows.append(("Summary %.2f%% CDDA" % (sum(cd_scores)/len(cd_scores))).replace(".", ","))
    rows.append(str(_checker_checksum(items)))
    return "\r\n".join(rows) + "\r\n"


def check_web_release(dest, fmt, ff, tools, stop_event, workers=4):
    if not tools:
        raise RuntimeError("AudioChecker bundle not found. Keep Audio_Checker_UTF8_Multicore.zip beside this program or in Downloads.")
    provenance = []
    for base, dirs, filenames in os.walk(dest):
        if stop_event.is_set():
            raise InterruptedError("AudioChecker cancelled.")
        folder = Path(base)
        tracks = sorted([folder / fn for fn in filenames
                         if Path(fn).suffix.lower() in (".flac", ".m4a")])
        if not tracks:
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = [pool.submit(_checker_job, track, tools, ff, stop_event) for track in tracks]
            results = [f.result() for f in futures]
        # A real analyzer failure must not be rebranded as success.
        errors = [str(track.name) + ": " + problem for track, kind, pct, problem in results
                  if kind == "ERROR" or problem]
        if errors:
            raise RuntimeError("AudioChecker incomplete: " + "; ".join(errors[:5]))
        report = folder / "audiochecker.log"
        if report.exists():
            raise RuntimeError("Refusing to overwrite an existing AudioChecker report.")
        with report.open("w", encoding="utf-8", newline="") as stream:
            stream.write(_checker_report(folder, results))
        if not is_real_checker_log(report, tracks):
            report.unlink(missing_ok=True)
            raise RuntimeError("Generated AudioChecker report failed consistency validation.")
        provenance.append({"report": str(report.relative_to(dest)),
                           "analyzer": "auCDtect -mS0, supplied multicore bundle",
                           "workers": int(workers),
                           "input": str(fmt),
                           "results": len(results)})
    if not provenance:
        raise RuntimeError("AudioChecker received no tracks.")
    return provenance


def is_real_checker_log(log_path, tracks):
    """Require authentic footer and coverage; decode Unicode/Cyrillic filenames."""
    try:
        raw = log_path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "cp1251", "cp1252"):
            try:
                body = raw.decode(encoding).lower()
            except UnicodeError:
                continue
            lines = [x.strip() for x in body.splitlines() if x.strip()]
            if "audiochecker" not in body or not ("cdda" in body or "mpeg" in body):
                continue
            if not lines or not re.fullmatch(r"\d+", lines[-1]):
                continue
            if all(track.name.casefold() in body.casefold() for track in tracks):
                return True
        return False
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
                    "checker_workers": self.checker_workers, "worker_limit": worker_limit(),
                    "counts": dict(self.counts),
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

    def organize(self, source, dest, fmt, encoding_workers=4, checker_workers=4):
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
            write_json(SETTINGS, {"source": self.source, "dest": self.dest,
                                  "format": self.fmt,
                                  "encoding_workers": enc, "checker_workers": chk})
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
            checker = setup_checker()
            if not checker:
                self.info("Provided Audio_Checker_UTF8_Multicore ZIP not detected. Save it beside this .pyw or in Downloads; digital output will be PARTIAL.")
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
                        try:
                            self.status = f"Organizing {index}/{len(releases)}: {out_name}"
                            self._export_release(folder, tmp, fmt, ff, ffprobe)
                            # Actual AudioChecker is performed before finalizing the release.
                            if not info["physical"]:
                                try:
                                    self.status = f"AudioChecker {index}/{len(releases)}: {out_name}"
                                    result["checker"] = check_web_release(tmp, fmt, ff, checker, self.stop, self.checker_workers)
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
            image_jobs = []
            seen_image_names = set()
            for k, rec in enumerate(records):
                start_sample = ticks[k] * (rate // 75)
                end_sample = ticks[k + 1] * (rate // 75) if k + 1 < len(records) else None
                name = track_filename(rec["number"], rec["title"], extension)
                output = dest / sub / name
                if str(output).casefold() in seen_image_names or os.path.lexists(output):
                    raise RuntimeError("Image tracks map to the same output path: " + str(output))
                seen_image_names.add(str(output).casefold())
                image_jobs.append((rec, name, output, start_sample, end_sample, k))
            def encode_image(job):
                rec, name, output, start_sample, end_sample, k = job
                if self.stop.is_set():
                    raise InterruptedError("Cancelled.")
                convert(ff, src_img, output, fmt, start_sample, end_sample,
                        rec["title"], rec["number"])
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
        for fp in files:
            if self.stop.is_set():
                raise InterruptedError("Encoding cancelled.")
            if fp.resolve() in source_images or fp.suffix.lower() not in AUDIO:
                continue
            number, title, _ = extract_number_title(fp)
            if number <= 0 or not title:
                raise RuntimeError("Track has no valid number/title: " + str(fp))
            dst = dest / fp.parent.relative_to(source) / track_filename(number, title, extension)
            audio_jobs.setdefault(str(dst).casefold(), (dst, []) )[1].append(
                (fp, number, title))
        jobs = list(audio_jobs.values())

        def encode_normal(entry):
            dst, variants = entry
            if os.path.lexists(dst):
                raise RuntimeError("Audio target exists: " + str(dst))
            src, number, title = variants[0]
            dst.parent.mkdir(parents=True, exist_ok=True)
            source_info = probe(ffprobe, src)
            codec = str(source_info.get("codec_name", "")).lower()
            if codec not in _MB_LOSSLESS:
                raise RuntimeError("Refusing to encode non-lossless audio: " + str(src))
            if (fmt == "ALAC" and codec == "alac") or (fmt == "FLAC" and codec == "flac"):
                shutil.copy2(src, dst)
                if sha(src) != sha(dst):
                    raise RuntimeError("Source copy hash mismatch: " + str(src))
            else:
                convert(ff, src, dst, fmt, title=title, number=number)
            # Additional files mapping to this name are discarded ONLY when
            # the decoded PCM equals the successfully verified first copy.
            for variant, _, _ in variants[1:]:
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
button.folder{width:34px;padding:0}.options{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-top:1px solid #252b34;padding-top:10px}.options label{font-weight:650;margin-left:6px}select{min-width:72px}
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
<header id="top"><strong id="brand">Music Library Organizer</strong><span class="muted">v0.3.3 - Under construction ⚠️</span><span class="space"></span><span class="muted">Automatic · MCC-style · originals untouched</span></header>
<main>
<section class="card"><div class="head"><strong>Input and output folders</strong><span class="muted">Choose folders, then Organize. No review or second confirmation.</span></div><div class="form">
<label for="source" class="label">Input collection</label><div class="path" id="sourceDrop"><button class="folder" id="openSource" title="Open input folder" aria-label="Open input folder">📁</button><input type="text" id="source" aria-label="Input folder" placeholder="Choose input folder..."><button id="browseSource">Browse...</button></div>
<label for="dest" class="label">Organized output</label><div class="path" id="destDrop"><button class="folder" id="openDest" title="Open output folder" aria-label="Open output folder">📁</button><input type="text" id="dest" aria-label="Output folder" placeholder="Choose separate output folder..."><button id="browseDest">Browse...</button></div>
<div class="options"><label for="format">Output format</label><select id="format" aria-label="Output format"><option value="ALAC">ALAC (.m4a)</option><option value="FLAC">FLAC (.flac)</option></select>
<label for="encodeWorkers">Encoding workers</label><select id="encodeWorkers" aria-label="Number of simultaneous encoding workers"></select>
<label for="checkWorkers">AudioChecker workers</label><select id="checkWorkers" aria-label="Number of simultaneous AudioChecker workers"></select>
<span class="muted">Looks for supplied AudioChecker ZIP automatically; worker choices are saved.</span></div></div></section>
<section class="card" id="progress"><div class="progress-head"><span id="status" role="status" aria-live="polite">Ready</span><span id="percent">0%</span></div><div class="track" id="progressBar" role="progressbar" aria-label="Organization progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="fill" id="fill"></div></div>
<div class="counts"><span><b id="total">0</b> found</span><span><b id="done">0</b> compliant</span><span><b id="partial">0</b> partial</span><span><b id="skipped">0</b> already present</span><span><b id="failed">0</b> failed</span><span><b id="warnings">0</b> warnings</span></div>
<div id="detail">Input files remain untouched. Any genuine errors will appear below.</div></section>
<section class="card" id="results"><div class="head"><strong>Results</strong></div><div class="tools"><input type="text" id="query" placeholder="Filter results..." aria-label="Filter results"><button id="openLog">↗ Open log</button><span class="space"></span><span id="resultCount" class="muted">Not started</span></div>
<div class="tablewrap"><table><thead><tr><th style="width:14%">Result</th><th style="width:31%">Release</th><th>Details</th></tr></thead><tbody id="rows"><tr><td colspan="3" class="empty">Press Organize to begin.</td></tr></tbody></table></div></section>
</main>
<footer id="foot"><button class="primary" id="organize">▶ Organize</button><button id="cancel" disabled>✕ Cancel</button><button id="undo" disabled>↶ Undo last run</button><span class="space"></span><span id="message" aria-live="polite" role="status"></span></footer>
<script>
let state=null,initialized=false,working=false,query="",suggestedDest="";
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(method,...args){for(let j=0;j<120;j++){let a=window.pywebview&&window.pywebview.api;if(a&&typeof a[method]==="function")return a[method](...args);await new Promise(r=>setTimeout(r,50));}throw Error("Desktop connection failed.");}
function error(e){$('message').textContent=String(e&&e.message||e);}
async function call(name,...args){try{const out=await api(name,...args);if(out&&out.error)throw Error(out.error);await refresh();return out;}catch(e){error(e);return null;}}
function render(){
 if(!state)return;
 if(!initialized){
  $('source').value=state.source||"";
  $('dest').value=state.dest||suggestPath(state.source);
  suggestedDest=suggestPath(state.source);
  $('format').value=state.format||"ALAC";
  setupWorkers('encodeWorkers',state.worker_limit,state.encoding_workers);
  setupWorkers('checkWorkers',state.worker_limit,state.checker_workers);
  initialized=true;
 }
 working=!!state.running;
 $('status').textContent=state.status||"Ready";
 const pct=Number(state.progress||0);$('percent').textContent=pct+"%";$('fill').style.width=pct+"%";$('progressBar').setAttribute("aria-valuenow",String(pct));
 let c=state.counts||{};for(let name of ["total","done","partial","skipped","failed","warnings"])$(name).textContent=c[name]||0;
 $('detail').textContent=working?"Organizing all discovered releases automatically. Genuine errors do not block later releases.":"Originals are untouched. Missing audiochecker.log is not a reason to skip a release.";
 for(let id of ["source","dest","format","encodeWorkers","checkWorkers","browseSource","browseDest"])$(id).disabled=working;
 $('organize').disabled=working;$('cancel').disabled=!working;$('undo').disabled=working||!state.undo;
 
 if(state.error)$('message').textContent=state.error;
 let list=(state.results||[]).filter(x=>[x.name,x.message,(x.warnings||[]).join(" ")].join(" ").toLowerCase().includes(query));
 $('resultCount').textContent=list.length+" shown / "+(state.results||[]).length+" processed";
 $('rows').innerHTML=list.slice(-500).map(x=>'<tr><td class="'+esc(x.status)+'"><b>'+esc(x.status.toUpperCase())+'</b></td><td><b>'+esc(x.name)+'</b><div class="small">'+esc(x.target)+'</div></td><td>'+esc(x.message)+(x.warnings&&x.warnings.length?'<div class="small warning">'+esc(x.warnings.join(" | "))+'</div>':"")+'</td></tr>').join("")||'<tr><td colspan="3" class="empty">'+(working?"Working...":"No releases processed yet.")+'</td></tr>';
}
async function refresh(){try{state=await api("state");render();}catch(e){error(e);}}
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

$('openLog').onclick=()=>call("open_log");
$('organize').onclick=()=>{$('message').textContent="";call("organize",$('source').value,$('dest').value,$('format').value,Number($('encodeWorkers').value),Number($('checkWorkers').value));};
$('cancel').onclick=()=>call("cancel");
$('undo').onclick=()=>{if(confirm("Remove unchanged output from the last run? Original files are not touched."))call("undo");};
$('query').oninput=()=>{query=$('query').value.trim().toLowerCase();render();};
for(let [zone,input] of [["sourceDrop","source"],["destDrop","dest"]]){
let z=$(zone);z.addEventListener("dragover",e=>{e.preventDefault();z.classList.add("drag");});
z.addEventListener("dragleave",()=>z.classList.remove("drag"));
z.addEventListener("drop",e=>{e.preventDefault();z.classList.remove("drag");let f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];let path=f&&(f.pywebviewFullPath||f.path);if(path){$(input).value=path;if(input==='source')changedInput();}else error("Folder drop unsupported here; use Browse.");});
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
