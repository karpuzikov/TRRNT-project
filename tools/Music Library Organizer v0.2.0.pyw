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
VERSION = "0.2.0"
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
    groups = {}
    for rec in cue["records"]:
        if rec["file"]:
            groups.setdefault(rec["file"].casefold(), []).append(rec)
    images = []
    for group in groups.values():
        if len(group) < 2:
            continue
        src = cue_path.parent / group[0]["file"].replace("\\", os.sep)
        if not src.is_file() or src.is_symlink():
            raise ValueError("CUE image is missing or unsafe: " + str(src))
        if src.suffix.lower() not in AUDIO:
            raise ValueError("Unsupported audio image codec: " + str(src))
        ticks = []
        for rec in group:
            if 0 not in rec["index"] and any(i > 1 for i in rec["index"]):
                raise ValueError("Unsupported CUE indexes - manual review required.")
            if 0 in rec["index"] or 1 not in rec["index"] or any(i > 1 for i in rec["index"]):
                raise ValueError("INDEX 00/extra indexes require manual review to preserve pregaps.")
            if not rec["title"]:
                raise ValueError("Image CUE track has no TITLE: " + str(rec["number"]))
            ticks.append(rec["index"][1])
        if ticks != sorted(set(ticks)) or ticks[0] != 0:
            raise ValueError("Unusual CUE first index/HTOA or repeated index - review required.")
        images.append((src, group, ticks))
    return images


def image_cue_text(cue, new_names):
    header = []
    seen = False
    for line in cue["lines"]:
        if re.match(r"^\s*TRACK\s+\d\d\s+AUDIO\b", line, re.I):
            seen = True
        if seen:
            break
        if not FILE.match(line):
            header.append(line)
    out = list(header)
    for rec in cue["records"]:
        target = new_names.get(rec["number"])
        if target is None:
            raise ValueError("Mixed or unsupported CUE FILE layout; manual review required.")
        out.append('FILE "' + target + '" WAVE')
        for line in rec["lines"]:
            if INDEX.match(line):
                if INDEX.match(line).group(1) == "01":
                    out.append("    INDEX 01 00:00:00")
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


class Organizer:
    """One action: input -> organized output. No manual release-review gate."""
    def __init__(self):
        ensure_data()
        settings = read_json(SETTINGS, {})
        self.source = str(settings.get("source", ""))
        self.dest = str(settings.get("dest", ""))
        self.fmt = str(settings.get("format", "ALAC"))
        self.running = False
        self.stop = threading.Event()
        self.status = "Choose input and output folders, then Organize."
        self.progress = 0
        self.counts = {"total": 0, "done": 0, "skipped": 0, "failed": 0, "warnings": 0}
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
                    "progress": self.progress, "counts": dict(self.counts),
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

    def organize(self, source, dest, fmt):
        with self.lock:
            if self.running:
                return {"error": "Another operation is running."}
            try:
                src, dst = check_paths(str(source), str(dest), str(fmt))
            except Exception as exc:
                return {"error": str(exc)}
            existing = read_json(UNDO, {})
            if existing.get("pending"):
                return {"error": "The previous run has an interrupted output checkpoint. "
                        "Resolve the pending Undo record before another write."}
            self.source, self.dest, self.fmt = str(src), str(dst), str(fmt)
            write_json(SETTINGS, {"source": self.source, "dest": self.dest, "format": self.fmt})
            self.running = True
            self.stop.clear()
            self.status, self.progress, self.error = "Discovering releases...", 0, ""
            self.counts = {"total": 0, "done": 0, "skipped": 0, "failed": 0, "warnings": 0}
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
            for index, folder in enumerate(releases, 1):
                if self.stop.is_set():
                    self.status = "Cancelled. Completed output can be undone."
                    break
                folder = folder.resolve(strict=True)
                target = dest / folder.name
                result = {"name": folder.name, "source": str(folder),
                          "target": str(target), "status": "", "message": "",
                          "warnings": []}
                try:
                    if source != folder and source not in folder.parents:
                        raise RuntimeError("Input release escaped the selected source root.")
                    if folder.name.casefold() in seen:
                        raise RuntimeError("Output name collision between multiple source releases.")
                    seen.add(folder.name.casefold())
                    if os.path.lexists(target):
                        result["status"], result["message"] = (
                            "skipped", "Output release already exists and was not overwritten.")
                        self.counts["skipped"] += 1
                    else:
                        files = collect_files(folder)
                        if not files:
                            raise RuntimeError("Release contains no supported files.")
                        if any(fp.suffix.lower() in LOSSY for fp in files):
                            raise RuntimeError("Lossy source detected; cannot label it ALAC or FLAC.")
                        cat, physical = catalog(folder.name)
                        if physical:
                            if not cat or cat.lower() == "none":
                                result["warnings"].append("Unverified catalog: existing CUE/LOG names retained.")
                            if not any(fp.suffix.lower() == ".cue" for fp in files):
                                result["warnings"].append("CD CUE missing (no file invented).")
                            if not any(fp.suffix.lower() == ".log" and fp.name.casefold() != "audiochecker.log" for fp in files):
                                result["warnings"].append("CD rip log missing (no file invented).")
                        elif not any(fp.name.casefold() == "audiochecker.log" for fp in files):
                            result["warnings"].append("Missing audiochecker.log; organization continues.")
                        if not RELEASE.fullmatch(folder.name):
                            result["warnings"].append("Release folder title unverified; preserved unchanged.")
                        if ff is None:
                            ff, ffprobe = ensure_ffmpeg()
                        tmp = staging_root / (str(index) + "-" + uuid.uuid4().hex)
                        tmp.mkdir()
                        try:
                            self.status = f"Organizing {index}/{len(releases)}: {folder.name}"
                            self._export_release(folder, tmp, fmt, ff, ffprobe)
                            created_files = collect_files(tmp)
                            if not created_files:
                                raise RuntimeError("No output was generated.")
                            hashes = {str(fp.relative_to(tmp)).replace("\\", "/"): sha(fp)
                                      for fp in created_files}
                            if self.stop.is_set():
                                raise InterruptedError("Cancelled before release commit.")
                            if os.path.lexists(target):
                                raise RuntimeError("Destination appeared during export; overwrite blocked.")
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
                            record = {"path": str(target), "files": hashes}
                            manifest["pending"] = record
                            write_json(UNDO, manifest)
                            os.rename(tmp, target)
                            manifest["created"].append(record)
                            manifest["pending"] = None
                            write_json(UNDO, manifest)
                            result["status"], result["message"] = (
                                "done", str(len(created_files)) + " files organized.")
                            self.counts["done"] += 1
                        finally:
                            if tmp.exists():
                                shutil.rmtree(tmp)
                except InterruptedError:
                    self.status = "Cancelled. Completed output can be undone."
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
                        self.info(result["status"].upper() + ": " + folder.name + ": " +
                                  result["message"] + (" | " + "; ".join(result["warnings"])
                                                       if result["warnings"] else ""))
                    write_json(REPORT, {"version": VERSION, "source": str(source),
                                        "output": str(dest), "format": fmt,
                                        "counts": dict(self.counts),
                                        "releases": list(self.results)})
            if not self.stop.is_set():
                self.status = ("Finished: " + str(self.counts["done"]) + " organized; " +
                               str(self.counts["skipped"]) + " already present; " +
                               str(self.counts["failed"]) + " failed.")
                self.info(self.status)
        except Exception as exc:
            self.error = str(exc)
            self.status = "Batch failed: " + str(exc)
            self.info("BATCH FAILED: " + traceback.format_exc()[-3500:])
        finally:
            if staging_root is not None and staging_root.exists():
                try:
                    shutil.rmtree(staging_root)
                except Exception as exc:
                    self.info("Temporary cleanup error: " + str(exc))
            with self.lock:
                self.running = False

    def _export_release(self, source, dest, fmt, ff, ffprobe):
        files = collect_files(source)
        cues = [p for p in files if p.suffix.lower() == ".cue"]
        cat, physical = catalog(source.name)
        # Keep the original rip-document filename if its edition identifier
        # cannot be verified; this must not block unrelated audio conversions.
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
                names[rec["number"]] = name
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
                raise RuntimeError("Target filename collision: " + str(new))
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
                            raise RuntimeError("Unmapped CUE audio FILE: " + rec["file"])
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
:root{color-scheme:dark;--bg:#0b0c0f;--panel:#11141a;--panel2:#171b22;--field:#181c23;--line:#2a303a;--text:#f3f6fb;--muted:#9ca7b8;--blue:#66a9ff;--red:#ef4444;--yellow:#f59e0b;--green:#22c55e}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font:13px "Segoe UI Variable","Segoe UI",system-ui,sans-serif}
body{display:flex;flex-direction:column}button,input,select{font:inherit}#bar{height:58px;min-height:58px;background:#101319;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;padding:0 18px}
#brand{font-size:18px;font-weight:780}#version{color:#9ca7b8;font-size:12px}.space{flex:1}
#main{display:grid;grid-template-rows:auto minmax(180px,1fr) auto;gap:12px;flex:1;min-height:0;padding:12px 18px;overflow:hidden}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;min-width:0}
.head{padding:10px 13px;display:flex;align-items:baseline;gap:12px}.title{font-size:14px;font-weight:760}.hint{font-size:11px;color:var(--muted)}
.sources{padding:0 13px 12px}.fieldlabel{font-weight:650;display:block;margin-bottom:4px}.pathrow{display:grid;grid-template-columns:34px minmax(0,1fr) 87px;gap:7px;margin-bottom:8px}
input[type=text]{width:100%;height:34px;background:var(--field);border:1px solid #37404d;border-radius:8px;color:var(--text);padding:0 10px;outline:none}
button,select{height:34px;background:#1b2028;color:var(--text);border:1px solid #3a4351;border-radius:8px;padding:0 11px;cursor:pointer}
button:hover:not(:disabled),select:hover:not(:disabled){background:#252c36;border-color:#596579}button:disabled{opacity:.38;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #8cc8ff;outline-offset:2px}
button.primary{background:#17324c;border-color:#3e78aa;color:#dff1ff;font-weight:720}button.primary:hover:not(:disabled){background:#1e4265;border-color:#66a9ff}
button.icon{padding:0;width:34px;text-align:center}#opts{display:flex;align-items:center;gap:10px;border-top:1px solid #252b34;padding-top:10px;flex-wrap:wrap}select{min-width:115px}
#preview{min-height:0;display:flex;flex-direction:column;overflow:hidden}.tools{padding:0 13px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#query{max-width:260px}#counts{color:var(--muted);font-size:12px}.tablewrap{flex:1;overflow:auto;min-height:80px;border-top:1px solid var(--line)}
table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{padding:8px 10px;border-bottom:1px solid #242b34;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{position:sticky;top:0;background:#171b22;color:#aeb9c8;font-size:11px;text-transform:uppercase;letter-spacing:.04em}tr:hover{background:#1a2029}
.dim{font-size:11px;color:var(--muted);margin-top:3px}.ready{color:#a8f5bf}.review{color:#ffd88b}.check{accent-color:var(--blue);width:16px;height:16px}.empty{padding:24px;color:var(--muted);text-align:center}
#bottom{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:12px;min-height:154px;max-height:210px}
.prog{padding:13px}.progressrow{display:flex;justify-content:space-between;font-weight:650}.track{height:9px;margin:13px 0;background:#252d36;border-radius:99px;overflow:hidden}.fill{height:100%;width:0;background:var(--blue);transition:width .2s}.status{color:#c8d3e1;font-size:12px}
#activity{height:125px;overflow:auto;white-space:pre-wrap;padding:4px 13px 12px;color:#bec9d7;font:11px/1.4 Consolas,monospace}
#foot{height:58px;min-height:58px;background:#101319;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;padding:0 18px}
#message{color:var(--yellow);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:38vw}
#details{border:1px solid #465262;background:#11151b;color:var(--text);border-radius:11px;padding:0;width:min(920px,94vw);max-height:86vh}#details::backdrop{background:rgba(0,0,0,.7)}#details header{display:flex;gap:8px;align-items:center;padding:12px 15px;border-bottom:1px solid var(--line)}#details pre{padding:12px 16px;white-space:pre-wrap;word-break:break-all;font:11px/1.5 Consolas,monospace;overflow:auto;max-height:64vh}#details footer{padding:10px 14px;border-top:1px solid var(--line);display:flex;justify-content:flex-end}
.drop{outline:2px dashed var(--blue);outline-offset:-4px}
@media(max-width:760px){#main{padding:8px;gap:8px}#bottom{grid-template-columns:1fr;min-height:120px}#activity{height:75px}#brand{font-size:15px}th,td{padding:6px}#version{display:none}}
</style></head>
<body><header id="bar"><strong id="brand">Music Library Organizer</strong><span id="version">v0.2.0 - Under construction ⚠️</span><span class="space"></span><span class="hint">MCC-style · standalone · originals untouched</span></header>
<main id="main">
<section class="card"><div class="head"><span class="title">Collection folders</span><span class="hint">Choose different source and output roots. Apply copies and validates before finalizing.</span></div>
<div class="sources">
<label class="fieldlabel" for="source">Source artist / brand collection</label>
<div class="pathrow" id="srcdrop"><button class="icon" id="openSrc" title="Open source folder" aria-label="Open source folder">📁</button><input type="text" id="source" placeholder="Drop folder or Browse source..." aria-label="Source collection path"><button id="browseSrc">Browse...</button></div>
<label class="fieldlabel" for="dest">Output artist / brand collection</label>
<div class="pathrow" id="dstdrop"><button class="icon" id="openDst" title="Open destination folder" aria-label="Open destination folder">📁</button><input type="text" id="dest" placeholder="Choose different output folder..." aria-label="Destination collection path"><button id="browseDst">Browse...</button></div>
<div id="opts"><label for="format"><b>Output format</b></label><select id="format" aria-label="Output codec"><option value="ALAC">ALAC (.m4a)</option><option value="FLAC">FLAC (.flac)</option></select><span class="hint">Lossless only. CUE images split into tracks. Undecidable cases require review.</span></div>
</div></section>
<section class="card" id="preview"><div class="head"><span class="title">Proposed release changes</span><span class="hint">No files change during Scan. Review rows are excluded from Apply.</span></div>
<div class="tools"><input id="query" type="text" placeholder="Filter releases..." aria-label="Filter preview"><button id="selectAll">Select ready</button><button id="selectNone">Select none</button><span class="space"></span><span id="counts">No scan yet</span></div>
<div class="tablewrap"><table><thead><tr><th style="width:44px">✓</th><th style="width:34%">Release / output</th><th style="width:15%">State</th><th>Preview / reason</th></tr></thead><tbody id="rows"></tbody></table></div></section>
<div id="bottom"><section class="card prog"><div class="progressrow"><span id="status">Ready</span><span id="percent">0%</span></div><div class="track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="progress"><div class="fill" id="fill"></div></div><div class="status" id="detail">Select folders and Scan to preview.</div></section><section class="card"><div class="head"><span class="title">Activity</span></div><div id="activity" role="log" aria-live="polite"></div></section></div>
</main><dialog id="details" aria-label="Full proposed source and destination mapping"><header><b id="detailtitle">Release preview</b><span class="space"></span><button id="detailClose" aria-label="Close preview">✕ Close</button></header><pre id="detailrows"></pre><footer><button id="detailDone">Close</button></footer></dialog><footer id="foot"><button class="primary" id="scan">◴ Scan / Preview</button><button class="primary" id="apply" disabled>✓ Apply selected</button><button id="cancel" disabled>✕ Cancel</button><button id="undo" disabled>↶ Undo last run</button><span class="space"></span><span id="message" role="status" aria-live="polite"></span></footer>
<script>
let state=null, selected=new Set(), oldSig="", busy=false;
const $=id=>document.getElementById(id);
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
async function api(name,...args){for(let i=0;i<100;i++){if(window.pywebview&&window.pywebview.api&&window.pywebview.api[name])return await window.pywebview.api[name](...args);await new Promise(r=>setTimeout(r,50));}throw Error("Desktop API not ready.");}
function err(e){$('message').textContent=String(e&&e.message||e);}
async function call(name,...args){try{let result=await api(name,...args);if(result&&result.error)throw Error(result.error);await update();return result;}catch(e){err(e);return null;}}
async function choose(which){let p=await call("browse",which);if(p){$(which==="source"?"source":"dest").value=p;}}
function render(){
 if(!state)return;
 if(document.activeElement!==$('source')&&state.source)$('source').value=state.source;
 if(document.activeElement!==$('dest')&&state.dest)$('dest').value=state.dest;
 if(!busy)$('format').value=state.format;
 busy=state.running;
 $('status').textContent=state.status||"Ready";$('percent').textContent=Math.round(state.progress||0)+"%";
 $('fill').style.width=String(state.progress||0)+"%";$('progress').setAttribute("aria-valuenow",String(state.progress||0));
 $('detail').textContent=busy?"Working. Cancel will stop at a safe checkpoint.":"Sources are read only. Only selected ready releases can be exported.";
 $('activity').textContent=(state.activity||[]).slice(-22).join("\n");
 $('scan').disabled=busy;$('cancel').disabled=!busy;$('undo').disabled=busy||!state.undo;
 ['browseSrc','browseDst','source','dest','format'].forEach(x=>$(x).disabled=busy);
 const records=state.plan||[];
 const signature=records.map(x=>x.id+":"+x.source+":"+x.status).join("|");
 if(signature!==oldSig){oldSig=signature;selected=new Set(records.filter(x=>x.status==="ready").map(x=>x.id));}
 let ready=records.filter(x=>x.status==="ready").length;
 $('counts').textContent=records.length+" releases · "+ready+" ready · "+(records.length-ready)+" review · "+selected.size+" selected";
 $('apply').disabled=busy||selected.size===0||state.undo;
 $('selectAll').disabled=busy;$('selectNone').disabled=busy;
 if(state.error)$('message').textContent=state.error;
 let q=$('query').value.toLowerCase().trim();
 let visible=records.filter(x=>!q||[x.title,x.source,x.target,x.status].join(" ").toLowerCase().includes(q));
 let html=visible.slice(0,250).map(x=>'<tr><td><input class="check" type="checkbox" data-id="'+esc(x.id)+'" aria-label="Select '+esc(x.title)+'" '+(selected.has(x.id)?"checked ":"")+(busy||x.status!=="ready"?"disabled":"")+'></td><td><b>'+esc(x.title)+'</b><div class="dim">'+esc(x.source)+'</div><div class="dim">→ '+esc(x.target)+'</div></td><td><span class="'+esc(x.status)+'">'+esc(x.status.toUpperCase())+'</span><div class="dim">'+x.files+' files · '+x.images+' image(s)</div></td><td><div>'+esc((x.changes||[]).slice(0,3).join(" | ")||"Naming already matches")+'</div><div class="dim">'+esc((x.notes||[]).join(" | "))+'</div><button class="detailsButton" data-details="'+esc(x.id)+'" aria-label="Show all planned paths for '+esc(x.title)+'">Details...</button></td></tr>').join("");
 $('rows').innerHTML=html||'<tr><td colspan="4" class="empty">'+(records.length?"No matching releases":"Nothing scanned yet")+'</td></tr>';
 if(visible.length>250)$('rows').innerHTML+='<tr><td colspan="4" class="empty">Showing first 250 matches - use Filter to narrow.</td></tr>';
 document.querySelectorAll('input[data-id]').forEach(x=>x.onchange=()=>{if(x.checked)selected.add(x.dataset.id);else selected.delete(x.dataset.id);render();});
 document.querySelectorAll('button[data-details]').forEach(b=>b.onclick=()=>{let x=records.find(z=>z.id===b.dataset.details);if(!x)return;$('detailtitle').textContent=x.title;$('detailrows').textContent='Source: '+x.source+'\nTarget: '+x.target+'\nStatus: '+x.status+'\n\n'+(x.notes||[]).join('\n')+'\n\n'+(x.mapping||[]).join('\n');$('details').showModal();});
}
async function update(){try{state=await api("state");render();}catch(e){err(e);}}
$('detailClose').onclick=()=>{$('details').close();};$('detailDone').onclick=()=>{$('details').close();};
$('browseSrc').onclick=()=>choose("source");$('browseDst').onclick=()=>choose("dest");
$('openSrc').onclick=()=>call("open_folder",$('source').value);
$('openDst').onclick=()=>call("open_folder",$('dest').value);
$('scan').onclick=async()=>{selected.clear();oldSig="";await call("scan",$('source').value,$('dest').value,$('format').value);};
$('apply').onclick=()=>{if(!selected.size)return;if(confirm("Export "+selected.size+" selected releases to the destination? Originals will remain untouched."))call("apply",Array.from(selected));};
$('undo').onclick=()=>{if(confirm("Undo the last export? Only unchanged generated output will be deleted."))call("undo");};
$('cancel').onclick=()=>call("cancel");
$('selectAll').onclick=()=>{selected=new Set((state.plan||[]).filter(x=>x.status==="ready").map(x=>x.id));render();};
$('selectNone').onclick=()=>{selected.clear();render();};
$('query').oninput=render;
for(let [id,target] of [['srcdrop','source'],['dstdrop','dest']]){
 let el=$(id);
 el.addEventListener('dragover',e=>{e.preventDefault();el.classList.add('drop');});
 el.addEventListener('dragleave',e=>{el.classList.remove('drop');});
 el.addEventListener('drop',e=>{e.preventDefault();el.classList.remove('drop');let f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];let value=f&&(f.pywebviewFullPath||f.path);if(value)$(target).value=value;else err("Use Browse or paste a full folder path.");});
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state&&state.running)call("cancel");});
setInterval(update,750);update();
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
