import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

APP_NAME = "AudioChecker Automation"
APP_VERSION = "0.1.0"
APP_KEY = "audiochecker_automation"
SUPPORTED_AUDIO = {".flac", ".ape", ".wav", ".shn", ".pac"}
LOG_NAME = "audiochecker.log"


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


def _settings_path() -> Path:
    return _documents_dir() / "Karpuzikov Tools" / "settings.json"


def _load_store() -> dict:
    p = _settings_path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_store(data: dict) -> None:
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def _load_settings() -> dict:
    store = _load_store()
    apps = store.get("apps") if isinstance(store.get("apps"), dict) else {}
    mine = apps.get(APP_KEY) if isinstance(apps.get(APP_KEY), dict) else {}
    analyzer = apps.get("duplicate_edition_analyzer") if isinstance(apps.get("duplicate_edition_analyzer"), dict) else {}
    result = dict(mine)
    if not result.get("recycle_update_folder"):
        result["recycle_update_folder"] = analyzer.get("recycle_update_folder", "")
    return result


def _save_settings(recycle: str, checker: str) -> None:
    store = _load_store()
    apps = store.setdefault("apps", {})
    if not isinstance(apps, dict):
        apps = {}
        store["apps"] = apps
    apps[APP_KEY] = {
        "recycle_update_folder": recycle.strip(),
        "achkgui_path": checker.strip(),
    }
    _save_store(store)


def _read_text_loose(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def audio_files_in(folder: Path) -> List[Path]:
    try:
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name.lower())


def is_cd_release(folder: Path) -> bool:
    try:
        files = [p for p in folder.iterdir() if p.is_file()]
    except OSError:
        return False
    has_cue = any(p.suffix.lower() == ".cue" for p in files)
    has_real_log = any(
        p.suffix.lower() == ".log" and p.name.lower() != LOG_NAME
        for p in files
    )
    return has_cue and has_real_log


def valid_audiochecker_log(folder: Path, audio_files: Optional[List[Path]] = None) -> bool:
    log = folder / LOG_NAME
    if not log.is_file():
        return False
    try:
        text = _read_text_loose(log)
    except Exception:
        return False
    upper = text.upper()
    if "AUDIOCHECKER" not in upper:
        return False
    if "CDDA" not in upper and "MPEG" not in upper:
        return False
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines or not re.fullmatch(r"\d+", lines[-1]):
        return False
    tracks = audio_files if audio_files is not None else audio_files_in(folder)
    lower = text.lower()
    # A valid track-rip log must cover every supported source file in the folder.
    return bool(tracks) and all(p.name.lower() in lower for p in tracks)


def scan_audio_directories(root: Path) -> List[Tuple[Path, List[Path]]]:
    found: List[Tuple[Path, List[Path]]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.lower().endswith("_duplicates") and d.lower() != "!remixes"]
        folder = Path(current)
        audio = sorted(
            [folder / f for f in files if Path(f).suffix.lower() in SUPPORTED_AUDIO],
            key=lambda p: p.name.lower(),
        )
        if audio:
            found.append((folder, audio))
    return found


def _find_achkgui(saved: str = "") -> Optional[Path]:
    candidates: List[Path] = []
    if saved:
        candidates.append(Path(saved))
    try:
        candidates.append(Path(sys.argv[0]).resolve().parent / "achkgui.exe")
    except Exception:
        pass
    candidates.extend([
        Path.cwd() / "achkgui.exe",
        Path.home() / "Desktop" / "AudioChecker" / "achkgui.exe",
        Path.home() / "Downloads" / "AudioChecker" / "achkgui.exe",
        _documents_dir() / "AudioChecker" / "achkgui.exe",
    ])
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "AudioChecker" / "achkgui.exe")
    try:
        where = shutil.which("achkgui.exe")
        if where:
            candidates.append(Path(where))
    except Exception:
        pass
    seen = set()
    for p in candidates:
        try:
            rp = p.expanduser().resolve()
        except Exception:
            rp = p.expanduser()
        key = str(rp).lower()
        if key in seen:
            continue
        seen.add(key)
        if rp.is_file():
            return rp
    return None


# ---- Win32 automation -----------------------------------------------------

def _win_api():
    if os.name != "nt":
        raise RuntimeError("This tool requires Windows.")
    return ctypes.windll.user32


def _enum_top_windows_for_pid(pid: int) -> List[int]:
    user32 = _win_api()
    result: List[int] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumWindowsProc
    def cb(hwnd, lparam):
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and user32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return result


def _window_text(hwnd: int) -> str:
    user32 = _win_api()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    user32 = _win_api()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _enum_children(hwnd: int) -> List[int]:
    user32 = _win_api()
    result: List[int] = []
    EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumChildProc
    def cb(child, lparam):
        result.append(child)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return result


def _click_start(hwnd: int) -> bool:
    user32 = _win_api()
    BM_CLICK = 0x00F5
    for child in _enum_children(hwnd):
        text = _window_text(child).strip().upper()
        cls = _class_name(child).lower()
        if "button" in cls and (text == "START" or text == "СТАРТ" or text.startswith("START ")):
            user32.SendMessageW(child, BM_CLICK, 0, 0)
            return True

    # Fallback: START is normally the default action after files are queued.
    try:
        user32.ShowWindow(hwnd, 5)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        VK_RETURN = 0x0D
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_RETURN, 0, 0, 0)
        user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def _close_windows(pid: int) -> None:
    user32 = _win_api()
    WM_CLOSE = 0x0010
    for hwnd in _enum_top_windows_for_pid(pid):
        try:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass


def _launch_one(exe: Path, folder: Path, tracks: List[Path], status_cb, stop_event: threading.Event) -> None:
    log = folder / LOG_NAME
    old_log_temp: Optional[Path] = None

    if log.exists() and not valid_audiochecker_log(folder, tracks):
        temp_root = Path(tempfile.gettempdir()) / "Karpuzikov Tools" / "AudioChecker Automation"
        temp_root.mkdir(parents=True, exist_ok=True)
        old_log_temp = temp_root / f"{time.time_ns()}_{LOG_NAME}"
        shutil.move(str(log), str(old_log_temp))

    args = [str(exe)] + [str(p) for p in tracks]
    if sum(len(x) + 3 for x in args) > 30000:
        args = [str(exe), str(folder)]

    proc = None
    try:
        status_cb(f"Opening AudioChecker: {folder.name}")
        proc = subprocess.Popen(args, cwd=str(exe.parent))

        hwnd = None
        deadline = time.time() + 25
        while time.time() < deadline and not stop_event.is_set():
            windows = _enum_top_windows_for_pid(proc.pid)
            if windows:
                # Prefer the window whose title looks like AudioChecker.
                hwnd = next((w for w in windows if "audio" in _window_text(w).lower()), windows[0])
                break
            if proc.poll() is not None:
                break
            time.sleep(0.25)

        if stop_event.is_set():
            raise RuntimeError("Stopped by user.")
        if hwnd is None:
            raise RuntimeError("AudioChecker window did not open.")

        status_cb(f"Checking: {folder.name}")
        if not _click_start(hwnd):
            raise RuntimeError("Could not start AudioChecker.")

        # AudioChecker writes audiochecker.log when a run finishes. Wait until
        # the file is complete and covers all source files.
        timeout = min(4 * 60 * 60, max(20 * 60, len(tracks) * 8 * 60))
        deadline = time.time() + timeout
        last_size = -1
        stable_since = 0.0
        while time.time() < deadline:
            if stop_event.is_set():
                raise RuntimeError("Stopped by user.")
            if log.is_file():
                try:
                    size = log.stat().st_size
                except OSError:
                    size = 0
                if size == last_size and size > 0:
                    if stable_since == 0:
                        stable_since = time.time()
                else:
                    stable_since = 0.0
                    last_size = size
                if stable_since and time.time() - stable_since >= 1.0 and valid_audiochecker_log(folder, tracks):
                    break
            if proc.poll() is not None and not log.is_file():
                raise RuntimeError("AudioChecker closed without creating audiochecker.log.")
            time.sleep(0.5)
        else:
            raise RuntimeError("Timed out waiting for AudioChecker to finish.")

        if old_log_temp and old_log_temp.exists():
            old_log_temp.unlink(missing_ok=True)

    except Exception:
        if log.exists() and not valid_audiochecker_log(folder, tracks):
            try:
                log.unlink()
            except OSError:
                pass
        if old_log_temp and old_log_temp.exists() and not log.exists():
            try:
                shutil.move(str(old_log_temp), str(log))
            except OSError:
                pass
        raise
    finally:
        if proc is not None:
            try:
                _close_windows(proc.pid)
                proc.wait(timeout=4)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("760x315")
        self.minsize(720, 295)
        saved = _load_settings()
        checker = _find_achkgui(saved.get("achkgui_path", ""))
        self.recycle_var = tk.StringVar(value=saved.get("recycle_update_folder", ""))
        self.checker_var = tk.StringVar(value=str(checker) if checker else saved.get("achkgui_path", ""))
        self.status_var = tk.StringVar(value="Choose the filtered recycle/update artist folder.")
        self.progress_var = tk.DoubleVar(value=0)
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self):
        pad = {"padx": 12, "pady": 7}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=14, pady=14)

        ttk.Label(frm, text="Recycle / update folder").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.recycle_var).grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(frm, text="Browse...", command=self.browse_folder).grid(row=1, column=1, padx=8)

        ttk.Label(frm, text="AudioChecker (achkgui.exe)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.checker_var).grid(row=3, column=0, sticky="ew", padx=12)
        ttk.Button(frm, text="Browse...", command=self.browse_checker).grid(row=3, column=1, padx=8)

        ttk.Label(
            frm,
            text="CD (.cue + real .log) and valid audiochecker.log folders are skipped automatically.",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        self.progress = ttk.Progressbar(frm, variable=self.progress_var, maximum=100)
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(14, 5))
        ttk.Label(frm, textvariable=self.status_var).grid(row=6, column=0, columnspan=2, sticky="w", padx=12)

        self.run_btn = ttk.Button(frm, text="Run AudioChecker Automatically", command=self.start)
        self.run_btn.grid(row=7, column=0, sticky="w", padx=12, pady=16)
        self.close_btn = ttk.Button(frm, text="Close", command=self.on_close)
        self.close_btn.grid(row=7, column=1, sticky="e", padx=8, pady=16)
        frm.columnconfigure(0, weight=1)

    def save_settings(self):
        _save_settings(self.recycle_var.get(), self.checker_var.get())

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "AudioChecker is still running. Stop after the current operation?"):
                return
            self.stop_event.set()
            self.status_var.set("Stopping...")
            return
        self.save_settings()
        self.destroy()

    def browse_folder(self):
        initial = self.recycle_var.get().strip()
        kwargs = {"title": "Select filtered recycle/update artist folder"}
        if initial and Path(initial).is_dir():
            kwargs["initialdir"] = initial
        path = filedialog.askdirectory(**kwargs)
        if path:
            self.recycle_var.set(path)
            self.save_settings()

    def browse_checker(self):
        initial = self.checker_var.get().strip()
        kwargs = {
            "title": "Select AudioChecker achkgui.exe",
            "filetypes": [("AudioChecker", "achkgui.exe"), ("Executable", "*.exe")],
        }
        if initial and Path(initial).parent.is_dir():
            kwargs["initialdir"] = str(Path(initial).parent)
        path = filedialog.askopenfilename(**kwargs)
        if path:
            self.checker_var.set(path)
            self.save_settings()

    def _set_status(self, text: str):
        self.after(0, lambda: self.status_var.set(text))

    def _set_progress(self, current: int, total: int):
        pct = 0 if total <= 0 else current / total * 100
        self.after(0, lambda: self.progress_var.set(pct))

    def start(self):
        if os.name != "nt":
            messagebox.showerror(APP_NAME, "This tool requires Windows.")
            return
        root = Path(self.recycle_var.get().strip())
        exe = Path(self.checker_var.get().strip())
        if not root.is_dir():
            messagebox.showerror(APP_NAME, "Select a valid recycle/update folder.")
            return
        if not exe.is_file() or exe.name.lower() != "achkgui.exe":
            found = _find_achkgui(str(exe))
            if found:
                exe = found
                self.checker_var.set(str(found))
            else:
                messagebox.showerror(APP_NAME, "Select AudioChecker achkgui.exe once. The path will be remembered.")
                return

        self.save_settings()
        self.stop_event.clear()
        self.run_btn.configure(state="disabled")
        self.progress_var.set(0)
        self.worker = threading.Thread(target=self._run, args=(root, exe), daemon=True)
        self.worker.start()

    def _run(self, root: Path, exe: Path):
        scanned = scan_audio_directories(root)
        cd_skipped = 0
        existing_skipped = 0
        todo: List[Tuple[Path, List[Path]]] = []

        for folder, tracks in scanned:
            if is_cd_release(folder):
                cd_skipped += 1
            elif valid_audiochecker_log(folder, tracks):
                existing_skipped += 1
            else:
                todo.append((folder, tracks))

        if not todo:
            self.after(0, lambda: self._finish(0, cd_skipped, existing_skipped, []))
            return

        errors: List[str] = []
        completed = 0
        for i, (folder, tracks) in enumerate(todo, 1):
            if self.stop_event.is_set():
                break
            self._set_progress(i - 1, len(todo))
            try:
                _launch_one(exe, folder, tracks, self._set_status, self.stop_event)
                completed += 1
            except Exception as exc:
                errors.append(f"{folder}: {exc}")
            self._set_progress(i, len(todo))

        self.after(0, lambda: self._finish(completed, cd_skipped, existing_skipped, errors))

    def _finish(self, completed: int, cd_skipped: int, existing_skipped: int, errors: List[str]):
        self.run_btn.configure(state="normal")
        self.worker = None
        if errors:
            self.status_var.set(f"Finished with {len(errors)} error(s).")
            text = (
                f"Checked: {completed}\n"
                f"Skipped CD: {cd_skipped}\n"
                f"Skipped existing AudioChecker log: {existing_skipped}\n"
                f"Errors: {len(errors)}\n\n"
                + "\n".join(errors[:8])
            )
            if len(errors) > 8:
                text += f"\n...and {len(errors) - 8} more."
            messagebox.showerror(APP_NAME, text)
        else:
            self.status_var.set("Complete.")
            messagebox.showinfo(
                APP_NAME,
                f"Checked: {completed}\n"
                f"Skipped CD: {cd_skipped}\n"
                f"Skipped existing AudioChecker log: {existing_skipped}",
            )


if __name__ == "__main__":
    App().mainloop()