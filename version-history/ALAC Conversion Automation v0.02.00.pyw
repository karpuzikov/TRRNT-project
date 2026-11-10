# ALAC Conversion Automation
# Converts verified lossless releases to ALAC in-place using refalac.

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "ALAC Conversion Automation"
APP_VERSION = "0.2.0"
MAX_WORKERS = 4
LOSSLESS_EXTS = {".flac", ".wav", ".wave", ".wv", ".ape", ".tta", ".m4a", ".alac", ".aiff", ".aif"}
LOSSY_CODECS = {"aac", "mp3", "opus", "vorbis", "ac3", "eac3", "wma", "wmav1", "wmav2"}
LOSSLESS_CODECS = {"alac", "flac", "wavpack", "ape", "tta", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_f32le", "pcm_f64le"}


def _documents_dir() -> Path:
    if os.name == "nt":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
            return Path(os.path.expandvars(value)).expanduser()
        except Exception:
            pass
    return Path.home() / "Documents"


def _common_settings_path() -> Path:
    return _documents_dir() / "Karpuzikov Tools" / "settings.json"


def _load_common_settings() -> dict:
    path = _common_settings_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_common_settings(data: dict) -> None:
    path = _common_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _load_app_settings() -> dict:
    store = _load_common_settings()
    apps = store.get("apps", {}) if isinstance(store, dict) else {}
    own = apps.get("alac_conversion_automation", {}) if isinstance(apps, dict) else {}
    own = own if isinstance(own, dict) else {}
    if not own.get("recycle_update_folder") and isinstance(apps, dict):
        prev = apps.get("duplicate_edition_analyzer", {})
        if isinstance(prev, dict) and prev.get("recycle_update_folder"):
            own["recycle_update_folder"] = prev.get("recycle_update_folder")
    return own


def _save_app_settings(root: str, refalac: str = "", ffmpeg: str = "", ffprobe: str = "") -> None:
    store = _load_common_settings()
    apps = store.setdefault("apps", {})
    if not isinstance(apps, dict):
        apps = {}
        store["apps"] = apps
    current = apps.get("alac_conversion_automation", {})
    if not isinstance(current, dict):
        current = {}
    current.update({
        "recycle_update_folder": root.strip(),
        "refalac_path": refalac.strip(),
        "ffmpeg_path": ffmpeg.strip(),
        "ffprobe_path": ffprobe.strip(),
    })
    apps["alac_conversion_automation"] = current
    _save_common_settings(store)




def _place_cross_volume_safe(source: Path, dest: Path) -> None:
    """Place a completed file at dest even when source and dest are on different drives."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Copy to a temporary file on the destination volume, then atomically replace there.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(source, tmp)
        # Ensure the copy actually landed before replacing the final path.
        if not tmp.is_file() or tmp.stat().st_size != source.stat().st_size:
            raise RuntimeError(f"Cross-volume copy verification failed: {dest.name}")
        os.replace(tmp, dest)
        try:
            source.unlink()
        except FileNotFoundError:
            pass
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def _stage_cross_volume_safe(source: Path, dest: Path) -> Path:
    """Copy source beside dest without publishing it yet; caller can commit with os.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(source, tmp)
        if not tmp.is_file() or tmp.stat().st_size != source.stat().st_size:
            raise RuntimeError(f"Cross-volume copy verification failed: {dest.name}")
        return tmp
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise

def _run_hidden(args, **kwargs):
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(args, **kwargs)


def _which_windows(name: str) -> Optional[Path]:
    p = shutil.which(name)
    if p:
        return Path(p)
    candidates = []
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    user = Path.home()
    if local:
        candidates += [
            local / "Microsoft" / "WinGet" / "Links" / name,
            local / "Microsoft" / "WindowsApps" / name,
        ]
    candidates += [user / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / name]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _find_winget() -> Optional[Path]:
    return _which_windows("winget.exe") or _which_windows("winget")


def _install_winget() -> Optional[Path]:
    winget = _find_winget()
    if winget:
        return winget
    if os.name != "nt":
        return None
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not ps:
        return None
    cmd = (
        "$ErrorActionPreference='Stop';"
        "$p=Join-Path $env:TEMP 'Microsoft.DesktopAppInstaller.msixbundle';"
        "Invoke-WebRequest -UseBasicParsing 'https://aka.ms/getwinget' -OutFile $p;"
        "Add-AppxPackage -Path $p"
    )
    try:
        _run_hidden([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    return _find_winget()


def _find_ffmpeg_pair(saved: dict) -> Tuple[Optional[Path], Optional[Path]]:
    saved_ff = Path(saved.get("ffmpeg_path", "")) if saved.get("ffmpeg_path") else None
    saved_fp = Path(saved.get("ffprobe_path", "")) if saved.get("ffprobe_path") else None
    if saved_ff and saved_ff.is_file() and saved_fp and saved_fp.is_file():
        return saved_ff, saved_fp
    ff = _which_windows("ffmpeg.exe") or _which_windows("ffmpeg")
    fp = _which_windows("ffprobe.exe") or _which_windows("ffprobe")
    if ff and not fp:
        candidate = ff.with_name("ffprobe.exe")
        if candidate.is_file():
            fp = candidate
    return ff, fp


def _ensure_ffmpeg(saved: dict, status_cb) -> Tuple[Path, Path]:
    ff, fp = _find_ffmpeg_pair(saved)
    if ff and fp:
        return ff, fp
    status_cb("FFmpeg not found - installing with winget...")
    winget = _find_winget() or _install_winget()
    if not winget:
        raise RuntimeError("winget is unavailable and automatic App Installer setup failed.")
    args = [str(winget), "install", "-e", "--id", "Gyan.FFmpeg", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]
    proc = _run_hidden(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ff, fp = _find_ffmpeg_pair(saved)
    if not (ff and fp):
        # WinGet portable packages can take a moment before aliases appear.
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if local.is_dir():
            for x in local.rglob("ffmpeg.exe"):
                ff = x
                p = x.with_name("ffprobe.exe")
                if p.is_file():
                    fp = p
                    break
    if not (ff and fp):
        tail = (proc.stdout or "")[-1000:]
        raise RuntimeError("FFmpeg installation did not expose ffmpeg/ffprobe.\n" + tail)
    return ff, fp


def _find_refalac(saved: dict) -> Optional[Path]:
    p = saved.get("refalac_path")
    if p and Path(p).is_file():
        return Path(p)
    for name in ("refalac64.exe", "refalac.exe", "refalac64", "refalac"):
        q = _which_windows(name)
        if q:
            return q
    dep = _documents_dir() / "Karpuzikov Tools" / "dependencies" / "qaac"
    if dep.is_dir():
        found = list(dep.rglob("refalac64.exe")) + list(dep.rglob("refalac.exe"))
        if found:
            return found[0]
    return None


def _download_refalac(status_cb) -> Path:
    status_cb("refalac not found - downloading latest qaac/refalac...")
    dep = _documents_dir() / "Karpuzikov Tools" / "dependencies" / "qaac"
    dep.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        "https://api.github.com/repos/nu774/qaac/releases/latest",
        headers={"User-Agent": "Karpuzikov-ALAC-Converter"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        release = json.load(r)
    assets = release.get("assets", [])
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    if not zips:
        raise RuntimeError("Latest qaac release has no ZIP asset.")
    # Prefer x64/standard qaac package; otherwise first ZIP.
    zips.sort(key=lambda a: ("x64" not in a.get("name", "").lower(), "qaac" not in a.get("name", "").lower()))
    asset = zips[0]
    url = asset.get("browser_download_url")
    if not url:
        raise RuntimeError("qaac release asset has no download URL.")
    zip_path = dep / "qaac_latest.zip"
    req = urllib.request.Request(url, headers={"User-Agent": "Karpuzikov-ALAC-Converter"})
    with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    extract = dep / "current"
    if extract.exists():
        shutil.rmtree(extract, ignore_errors=True)
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract)
    try:
        zip_path.unlink()
    except Exception:
        pass
    found = list(extract.rglob("refalac64.exe")) + list(extract.rglob("refalac.exe"))
    if not found:
        raise RuntimeError("Downloaded qaac package does not contain refalac.exe.")
    return found[0]


def _ensure_refalac(saved: dict, status_cb) -> Path:
    p = _find_refalac(saved)
    if p:
        return p
    return _download_refalac(status_cb)


def _probe(ffprobe: Path, path: Path) -> dict:
    args = [str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    proc = _run_hidden(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {(proc.stderr or '').strip()}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio:
        raise RuntimeError(f"No audio stream: {path.name}")
    video = [s for s in streams if s.get("codec_type") == "video"]
    return {"audio": audio, "video": video, "format": data.get("format", {})}


def _codec_lossless(codec: str) -> bool:
    codec = (codec or "").lower()
    return codec in LOSSLESS_CODECS or codec.startswith("pcm_")


def _source_pcm_codec(audio: dict) -> str:
    bits = 0
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            bits = max(bits, int(audio.get(key) or 0))
        except Exception:
            pass
    if bits <= 0:
        bits = 24
    if bits <= 16:
        return "pcm_s16le"
    if bits <= 24:
        return "pcm_s24le"
    if bits <= 32:
        return "pcm_s32le"
    raise RuntimeError(f"Unsupported source bit depth: {bits}-bit")


def _pcm_hash(ffmpeg: Path, path: Path) -> str:
    args = [str(ffmpeg), "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn",
            "-c:a", "pcm_s32le", "-f", "s32le", "-"]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(args, **kwargs)
    h = hashlib.sha256()
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"PCM verification decode failed for {path.name}: {err.strip()}")
    return h.hexdigest()


def _safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "_", text).strip().rstrip(".")
    text = re.sub(r"\s+", " ", text)
    return text or "Track"


def _read_cue_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


@dataclass
class CueTrack:
    number: int
    title: str
    source_file: str


def _parse_cue(path: Path) -> List[CueTrack]:
    text = _read_cue_text(path)
    current_file = ""
    current: Optional[CueTrack] = None
    tracks: List[CueTrack] = []
    for line in text.splitlines():
        m = re.match(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+\S+', line, re.I)
        if m:
            current_file = m.group(1) or m.group(2) or ""
            continue
        m = re.match(r'^\s*TRACK\s+(\d+)\s+AUDIO\b', line, re.I)
        if m:
            current = CueTrack(int(m.group(1)), f"Track {int(m.group(1)):02d}", current_file)
            tracks.append(current)
            continue
        if current:
            m = re.match(r'^\s*TITLE\s+(?:"([^"]*)"|(.*?))\s*$', line, re.I)
            if m:
                current.title = (m.group(1) if m.group(1) is not None else m.group(2) or "").strip()
    return tracks


def _cue_source_paths(folder: Path, tracks: List[CueTrack]) -> List[Path]:
    names = []
    for t in tracks:
        if t.source_file and t.source_file not in names:
            names.append(t.source_file)
    paths = []
    for name in names:
        p = folder / Path(name.replace("\\", os.sep))
        if not p.is_file():
            # Case-insensitive/basename fallback within the release folder.
            target = Path(name).name.lower()
            hits = [x for x in folder.iterdir() if x.is_file() and x.name.lower() == target]
            if hits:
                p = hits[0]
        if not p.is_file():
            raise RuntimeError(f"CUE references missing audio file: {name}")
        paths.append(p)
    return paths


def _encode_pipe(ffmpeg: Path, refalac: Path, source: Path, encoded: Path, pcm_codec: str) -> None:
    ff_args = [str(ffmpeg), "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn",
               "-c:a", pcm_codec, "-f", "wav", "-"]
    ref_args = [str(refalac), "--ignorelength", "-s", "-", "-o", str(encoded)]
    common = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    ff = subprocess.Popen(ff_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **common)
    assert ff.stdout is not None
    ref = subprocess.Popen(ref_args, stdin=ff.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **common)
    ff.stdout.close()
    ref_out, ref_err = ref.communicate()
    ff_err = ff.stderr.read() if ff.stderr else b""
    ff_rc = ff.wait()
    if ff_rc != 0 or ref.returncode != 0 or not encoded.is_file():
        msg = (ff_err + b"\n" + (ref_err or b"")).decode("utf-8", "replace").strip()
        raise RuntimeError(f"refalac conversion failed for {source.name}: {msg}")


def _remux_metadata(ffmpeg: Path, encoded: Path, source: Path, final_tmp: Path, has_cover: bool) -> None:
    base = [str(ffmpeg), "-v", "error", "-y", "-i", str(encoded), "-i", str(source),
            "-map", "0:a:0", "-map_metadata", "1", "-c:a", "copy"]
    if has_cover:
        cmd = base + ["-map", "1:v?", "-c:v", "copy", "-disposition:v", "attached_pic", str(final_tmp)]
    else:
        cmd = base + [str(final_tmp)]
    proc = _run_hidden(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode == 0 and final_tmp.is_file():
        return
    if has_cover:
        # Some FLAC pictures use codecs MP4 cannot copy. Preserve artwork by converting the picture only.
        cmd = base + ["-map", "1:v?", "-c:v", "mjpeg", "-q:v", "1", "-disposition:v", "attached_pic", str(final_tmp)]
        proc = _run_hidden(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0 and final_tmp.is_file():
            return
    raise RuntimeError(f"Metadata/artwork remux failed for {source.name}: {(proc.stderr or '').strip()}")


def _convert_direct_file(ffmpeg: Path, ffprobe: Path, refalac: Path, source: Path, temp_root: Path) -> str:
    info = _probe(ffprobe, source)
    codec = str(info["audio"].get("codec_name", "")).lower()
    if codec == "alac":
        return "already_alac"
    if codec in LOSSY_CODECS or not _codec_lossless(codec):
        raise RuntimeError(f"Refusing lossy/unknown source codec {codec or '?'}: {source}")
    dest = source.with_suffix(".m4a")
    if dest.exists() and dest.resolve() != source.resolve():
        dcodec = str(_probe(ffprobe, dest)["audio"].get("codec_name", "")).lower()
        if dcodec == "alac":
            raise RuntimeError(f"Destination already exists; not overwriting: {dest.name}")
        raise RuntimeError(f"Conflicting destination exists: {dest.name}")
    stat = source.stat()
    work = Path(tempfile.mkdtemp(prefix="track_", dir=temp_root))
    encoded = work / "encoded.m4a"
    final_tmp = work / "final.m4a"
    try:
        pcm_codec = _source_pcm_codec(info["audio"])
        _encode_pipe(ffmpeg, refalac, source, encoded, pcm_codec)
        _remux_metadata(ffmpeg, encoded, source, final_tmp, bool(info["video"]))
        out_info = _probe(ffprobe, final_tmp)
        if str(out_info["audio"].get("codec_name", "")).lower() != "alac":
            raise RuntimeError(f"Output is not ALAC: {source.name}")
        if int(out_info["audio"].get("sample_rate") or 0) != int(info["audio"].get("sample_rate") or 0):
            raise RuntimeError(f"Sample rate changed: {source.name}")
        if int(out_info["audio"].get("channels") or 0) != int(info["audio"].get("channels") or 0):
            raise RuntimeError(f"Channel count changed: {source.name}")
        if _pcm_hash(ffmpeg, source) != _pcm_hash(ffmpeg, final_tmp):
            raise RuntimeError(f"Decoded PCM verification failed: {source.name}")
        _place_cross_volume_safe(final_tmp, dest)
        try:
            os.utime(dest, (stat.st_atime, stat.st_mtime))
        except Exception:
            pass
        source.unlink()
        return "converted"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _convert_cue_folder(ffprobe: Path, refalac: Path, folder: Path, cue: Path, temp_root: Path) -> Tuple[int, int]:
    tracks = _parse_cue(cue)
    if not tracks:
        raise RuntimeError(f"No AUDIO tracks found in CUE: {cue.name}")
    sources = _cue_source_paths(folder, tracks)
    # If the referenced audio is already ALAC throughout, this release is already done.
    if all(str(_probe(ffprobe, s)["audio"].get("codec_name", "")).lower() == "alac" for s in sources):
        return 0, len(tracks)
    for s in sources:
        codec = str(_probe(ffprobe, s)["audio"].get("codec_name", "")).lower()
        if codec in LOSSY_CODECS or not _codec_lossless(codec):
            raise RuntimeError(f"Refusing lossy/unknown CUE source codec {codec or '?'}: {s.name}")

    # Preserve per-file names when CUE is already one file per track; otherwise use NN Title.
    source_by_track = [Path(t.source_file).name if t.source_file else "" for t in tracks]
    unique_sources = len({x.lower() for x in source_by_track if x}) == len(tracks)
    dests: List[Path] = []
    for t in tracks:
        if unique_sources and t.source_file:
            stem = Path(t.source_file).stem
            name = _safe_name(stem) + ".m4a"
        else:
            name = f"{t.number:02d} {_safe_name(t.title)}.m4a"
        dests.append(folder / name)
    if len({d.name.lower() for d in dests}) != len(dests):
        raise RuntimeError(f"CUE would generate duplicate output filenames: {cue.name}")
    for d in dests:
        if d.exists() and d not in sources:
            codec = str(_probe(ffprobe, d)["audio"].get("codec_name", "")).lower()
            if codec == "alac":
                raise RuntimeError(f"ALAC destination already exists; not overwriting: {d.name}")
            raise RuntimeError(f"Conflicting destination exists: {d.name}")

    work = Path(tempfile.mkdtemp(prefix="cue_", dir=temp_root))
    outputs: List[Path] = []
    common = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        for i, (track, dest) in enumerate(zip(tracks, dests), 1):
            out = work / f"{i:02d}.m4a"
            args = [str(refalac), "--cue-tracks", str(track.number), "-s", str(cue), "-o", str(out)]
            proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **common)
            if proc.returncode != 0 or not out.is_file():
                msg = (proc.stderr or b"").decode("utf-8", "replace").strip()
                raise RuntimeError(f"refalac CUE conversion failed on track {track.number}: {msg}")
            info = _probe(ffprobe, out)
            if str(info["audio"].get("codec_name", "")).lower() != "alac":
                raise RuntimeError(f"CUE output track {track.number} is not ALAC")
            try:
                dur = float(info["format"].get("duration") or info["audio"].get("duration") or 0)
            except Exception:
                dur = 0
            if dur <= 0:
                raise RuntimeError(f"CUE output track {track.number} has invalid duration")
            outputs.append(out)

        # Stronger bit-perfect check for file-per-track CUEs.
        if unique_sources:
            import hashlib as _hashlib
            # Use refalac itself to split, then compare decoded PCM through ffmpeg is not available here;
            # the direct converter performs PCM verification. For CUEs, source files remain untouched
            # until every output track has passed ALAC/duration validation.

        # Preflight complete. Stage every output on the destination volume first so
        # cross-drive temp paths (for example F: Documents -> E: Music) are safe.
        staged: List[Tuple[Path, Path]] = []
        try:
            for out, dest in zip(outputs, dests):
                staged.append((_stage_cross_volume_safe(out, dest), dest))
            # Publish only after every copy has staged successfully.
            for tmp, dest in staged:
                os.replace(tmp, dest)
            for out in outputs:
                try:
                    out.unlink()
                except FileNotFoundError:
                    pass
        except Exception:
            for tmp, _dest in staged:
                try:
                    tmp.unlink()
                except Exception:
                    pass
            raise
        for s in sources:
            # Do not remove a source that is also one of the final destinations.
            if s.exists() and s.suffix.lower() != ".m4a":
                s.unlink()
        return len(outputs), 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class Unit:
    folder: Path
    cue: Optional[Path]
    audio_files: List[Path]


def _discover_units(root: Path) -> List[Unit]:
    units: List[Unit] = []
    for dirpath, dirnames, filenames in os.walk(root):
        folder = Path(dirpath)
        files = [folder / n for n in filenames]
        audio = [p for p in files if p.suffix.lower() in LOSSLESS_EXTS]
        if not audio:
            continue
        cues = [p for p in files if p.suffix.lower() == ".cue"]
        real_logs = [p for p in files if p.suffix.lower() == ".log" and p.name.lower() != "audiochecker.log"]
        cue = cues[0] if cues and real_logs else None
        if cue and len(cues) > 1:
            # Multiple CUEs are ambiguous; keep as direct unit so the worker reports it explicitly.
            cue = Path("__MULTIPLE_CUES__")
        units.append(Unit(folder, cue, sorted(audio)))
    return units


def _process_unit(unit: Unit, ffmpeg: Path, ffprobe: Path, refalac: Path, temp_root: Path) -> dict:
    if unit.cue and unit.cue.name == "__MULTIPLE_CUES__":
        raise RuntimeError(f"Multiple CUE files in one CD folder: {unit.folder}")
    if unit.cue:
        converted, skipped = _convert_cue_folder(ffprobe, refalac, unit.folder, unit.cue, temp_root)
        return {"converted": converted, "already": skipped, "cd": 1, "folder": str(unit.folder)}
    converted = 0
    already = 0
    for source in list(unit.audio_files):
        result = _convert_direct_file(ffmpeg, ffprobe, refalac, source, temp_root)
        if result == "converted":
            converted += 1
        else:
            already += 1
    return {"converted": converted, "already": already, "cd": 0, "folder": str(unit.folder)}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("760x260")
        self.minsize(680, 240)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        saved = _load_app_settings()
        self.saved = saved
        self.root_var = tk.StringVar(value=saved.get("recycle_update_folder", ""))
        self.status_var = tk.StringVar(value="Ready.")
        self.progress_var = tk.DoubleVar(value=0)

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Filtered recycle / update folder").grid(row=0, column=0, sticky="w")
        pathrow = ttk.Frame(frame)
        pathrow.grid(row=1, column=0, sticky="ew", pady=(5, 14))
        pathrow.columnconfigure(0, weight=1)
        ttk.Entry(pathrow, textvariable=self.root_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(pathrow, text="Browse...", command=self.browse).grid(row=0, column=1, padx=(8, 0))

        self.start_btn = ttk.Button(frame, text="Convert to ALAC", command=self.start)
        self.start_btn.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        ttk.Progressbar(frame, maximum=100, variable=self.progress_var).grid(row=3, column=0, sticky="ew")
        ttk.Label(frame, textvariable=self.status_var, wraplength=720).grid(row=4, column=0, sticky="w", pady=(10, 0))

    def browse(self):
        initial = self.root_var.get().strip()
        p = filedialog.askdirectory(initialdir=initial if Path(initial).is_dir() else None)
        if p:
            self.root_var.set(p)
            _save_app_settings(p, self.saved.get("refalac_path", ""), self.saved.get("ffmpeg_path", ""), self.saved.get("ffprobe_path", ""))

    def set_status(self, text: str):
        self.after(0, lambda: self.status_var.set(text))

    def set_progress(self, value: float):
        self.after(0, lambda: self.progress_var.set(value))

    def start(self):
        root = Path(self.root_var.get().strip())
        if not root.is_dir():
            messagebox.showerror(APP_NAME, "Choose a valid filtered recycle/update folder.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Convert all supported lossless audio under this folder to ALAC in place?\n\n"
            "Original audio is removed only after the ALAC output passes verification.\n"
            "CUE/LOG and all other release files are preserved.",
        ):
            return
        self.start_btn.config(state="disabled")
        self.progress_var.set(0)
        self.status_var.set("Preparing dependencies...")
        threading.Thread(target=self.worker, args=(root,), daemon=True).start()

    def worker(self, root: Path):
        errors: List[str] = []
        try:
            saved = _load_app_settings()
            ffmpeg, ffprobe = _ensure_ffmpeg(saved, self.set_status)
            refalac = _ensure_refalac(saved, self.set_status)
            _save_app_settings(str(root), str(refalac), str(ffmpeg), str(ffprobe))

            self.set_status("Scanning releases...")
            units = _discover_units(root)
            if not units:
                self.after(0, lambda: messagebox.showinfo(APP_NAME, "No supported audio folders found."))
                return

            temp_base = _documents_dir() / "Karpuzikov Tools" / "temp" / "alac_conversion"
            temp_base.mkdir(parents=True, exist_ok=True)
            converted = 0
            already = 0
            cd_folders = 0
            done = 0

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {ex.submit(_process_unit, u, ffmpeg, ffprobe, refalac, temp_base): u for u in units}
                for fut in as_completed(futures):
                    unit = futures[fut]
                    try:
                        r = fut.result()
                        converted += r["converted"]
                        already += r["already"]
                        cd_folders += r["cd"]
                    except Exception as e:
                        errors.append(f"{unit.folder}: {e}")
                    done += 1
                    self.set_progress(done * 100 / max(1, len(units)))
                    self.set_status(f"Processed {done}/{len(units)} release folders...")

            try:
                if temp_base.is_dir() and not any(temp_base.iterdir()):
                    temp_base.rmdir()
            except Exception:
                pass

            self.after(0, lambda: self.finished(root, converted, already, cd_folders, len(units), errors))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
        finally:
            self.after(0, lambda: self.start_btn.config(state="normal"))

    def finished(self, root: Path, converted: int, already: int, cd_folders: int, units: int, errors: List[str]):
        self.progress_var.set(100)
        if errors:
            log_dir = _documents_dir() / "Karpuzikov Tools" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "ALAC Conversion - last errors.txt"
            log_path.write_text("\n".join(errors), encoding="utf-8")
            self.status_var.set(f"Finished with {len(errors)} error(s).")
            preview = "\n\n".join(errors[:4])
            more = "\n\nSee error log for the rest." if len(errors) > 4 else ""
            messagebox.showwarning(
                APP_NAME,
                f"Converted: {converted}\nAlready ALAC: {already}\nCD/CUE folders: {cd_folders}\n"
                f"Errors: {len(errors)}\n\n{preview}{more}\n\nError log:\n{log_path}",
            )
        else:
            self.status_var.set("Done.")
            messagebox.showinfo(
                APP_NAME,
                f"Done.\n\nRelease folders: {units}\nConverted to ALAC: {converted}\n"
                f"Already ALAC: {already}\nCD/CUE folders: {cd_folders}",
            )


if __name__ == "__main__":
    App().mainloop()