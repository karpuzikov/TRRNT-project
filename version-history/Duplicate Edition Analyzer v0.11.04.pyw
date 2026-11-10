# Duplicate / Edition Analyzer
# Automatic discography optimizer: performs track-by-track comparison, reports actions, never deletes files.

from __future__ import annotations

import importlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import time
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Duplicate / Edition Analyzer"
APP_VERSION = "0.11.4"
AUDIO_EXTS = {".m4a", ".flac", ".wav", ".ape", ".wv", ".mp3", ".aac", ".ogg", ".opus"}
def _logical_cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def _probe_workers() -> int:
    # ffprobe jobs are short and spend much of their time waiting on process/I/O.
    return min(64, max(8, _logical_cpu_count() * 2))


def _fingerprint_workers() -> int:
    # fpcalc is CPU-heavy and largely single-threaded per file. Run roughly one
    # process per logical CPU, with a sane ceiling to avoid storage thrashing on
    # very high-core-count systems.
    return min(32, max(4, _logical_cpu_count()))


def _compare_workers() -> int:
    # Fingerprint similarity is Python CPU work. Use processes, not threads, so
    # the GIL does not leave the comparison stage pinned to one logical CPU.
    return min(32, max(2, _logical_cpu_count()))

CHROMAPRINT_VERSION = "1.6.1"
CHROMAPRINT_URL = f"https://github.com/acoustid/chromaprint/releases/download/v{CHROMAPRINT_VERSION}/chromaprint-fpcalc-{CHROMAPRINT_VERSION}-windows-x86_64.zip"

# External CD rip log quality scorer. Installed as a dependency at runtime;
# its source is not vendored into this one-file tool.
HEYBROCHECKLOG_VERSION = "1.3.2"
HEYBROCHECKLOG_SOURCE = "https://github.com/ligh7s/hey-bro-check-log/archive/refs/heads/master.zip"
# hey-bro-check-log by ligh7s, Apache-2.0:
# https://github.com/ligh7s/hey-bro-check-log


# Unified dark UI palette.
DARK_BG = "#1e1e1e"
DARK_PANEL = "#252526"
DARK_FIELD = "#2d2d30"
DARK_BUTTON = "#333337"
DARK_BUTTON_ACTIVE = "#3f3f46"
DARK_FG = "#f0f0f0"
DARK_MUTED = "#b8b8b8"
DARK_BORDER = "#4a4a4f"
DARK_ACCENT = "#5b9bd5"


def _enable_dark_titlebar(window) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 on current Windows 10/11, 19 on older builds.
        for attribute in (20, 19):
            try:
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
            except Exception:
                pass
    except Exception:
        pass


def _apply_dark_theme(root) -> None:
    root.configure(background=DARK_BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=DARK_BG, foreground=DARK_FG, font=("Segoe UI", 9))
    style.configure("TFrame", background=DARK_BG)
    style.configure("TLabel", background=DARK_BG, foreground=DARK_FG)
    style.configure("Section.TLabel", background=DARK_BG, foreground=DARK_FG, font=("Segoe UI", 10, "bold"))
    style.configure("Help.TLabel", background=DARK_BG, foreground=DARK_MUTED, font=("Segoe UI", 9))
    style.configure("TCheckbutton", background=DARK_BG, foreground=DARK_FG)
    style.map(
        "TCheckbutton",
        background=[("active", DARK_BG)],
        foreground=[("disabled", "#777777"), ("active", DARK_FG)],
    )
    style.configure(
        "TEntry",
        fieldbackground=DARK_FIELD,
        foreground=DARK_FG,
        insertcolor=DARK_FG,
        bordercolor=DARK_BORDER,
        lightcolor=DARK_BORDER,
        darkcolor=DARK_BORDER,
        padding=5,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", DARK_PANEL), ("readonly", DARK_FIELD)],
        foreground=[("disabled", "#777777")],
        bordercolor=[("focus", DARK_ACCENT)],
    )
    style.configure(
        "TButton",
        background=DARK_BUTTON,
        foreground=DARK_FG,
        bordercolor=DARK_BORDER,
        lightcolor=DARK_BORDER,
        darkcolor=DARK_BORDER,
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("pressed", DARK_PANEL), ("active", DARK_BUTTON_ACTIVE), ("disabled", "#292929")],
        foreground=[("disabled", "#777777"), ("active", DARK_FG)],
        bordercolor=[("focus", DARK_ACCENT)],
    )
    style.configure(
        "TProgressbar",
        troughcolor=DARK_FIELD,
        background=DARK_ACCENT,
        bordercolor=DARK_BORDER,
        lightcolor=DARK_ACCENT,
        darkcolor=DARK_ACCENT,
    )
    _enable_dark_titlebar(root)


def _documents_dir() -> Path:
    """Resolve the user's actual Windows Documents folder, including redirection."""
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
        # Settings persistence must never prevent the analyzer from running.
        pass


def _load_app_settings() -> dict:
    store = _load_common_settings()
    apps = store.get("apps", {})
    app = apps.get("duplicate_edition_analyzer", {}) if isinstance(apps, dict) else {}
    return app if isinstance(app, dict) else {}


def _save_app_settings(
    existing_discography: str,
    recycle_update: str,
    save_remixes: bool = False,
    save_live: bool = False,
    logging_enabled: bool = False,
    pattern_preferences: Optional[Dict[str, bool]] = None,
) -> None:
    store = _load_common_settings()
    apps = store.setdefault("apps", {})
    if not isinstance(apps, dict):
        apps = {}
        store["apps"] = apps

    previous = apps.get("duplicate_edition_analyzer", {})
    if not isinstance(previous, dict):
        previous = {}
    saved_patterns = previous.get("unusual_pattern_preferences_v5", {})
    if not isinstance(saved_patterns, dict):
        saved_patterns = {}
    if pattern_preferences is not None:
        saved_patterns.update({str(k): bool(v) for k, v in pattern_preferences.items()})

    apps["duplicate_edition_analyzer"] = {
        "existing_discography": existing_discography.strip(),
        "recycle_update_folder": recycle_update.strip(),
        "save_remixes": bool(save_remixes),
        "save_live": bool(save_live),
        "logging_enabled": bool(logging_enabled),
        "unusual_pattern_preferences_v5": saved_patterns,
        # Keep legacy keys for backward compatibility with older builds.
        "exclude_remixes": not bool(save_remixes),
        "exclude_live": not bool(save_live),
    }
    _save_common_settings(store)
FP_AUTO_SCORE = 5.0
FP_AUTO_GOOD_FRACTION = 0.90
FP_AUTO_EXCELLENT_FRACTION = 0.70
FP_AUTO_MEDIAN_MAX = 4.0
FP_AUTO_P90_MAX = 10
FP_MIN_OVERLAP = 0.85

# Secondary audio-only acceptance for the same recording from a different
# mastering/pressing. It is deliberately much tighter than random Chromaprint
# similarity and requires near-identical duration plus strong full-track overlap.
FP_MASTERING_SCORE = 7.5
FP_MASTERING_GOOD_FRACTION = 0.80
FP_MASTERING_MEDIAN_MAX = 7.0
FP_MASTERING_P90_MAX = 14
FP_MASTERING_MIN_OVERLAP = 0.92
FP_MASTERING_MAX_DURATION_SECONDS = 4.0
FP_MASTERING_MAX_DURATION_RATIO = 0.02

FP_SILENCE_MIN_FRAMES = 120

# Candidate prefilter. The previous pure-duration fallback compared almost every
# track with every other similarly-sized track. Real matches from the diagnostic
# corpus had far stronger token overlap, so keep a generous weak fallback without
# turning the expensive matcher back into O(n^2).
FP_CANDIDATE_STRONG_SHARED_TOKENS = 64
FP_CANDIDATE_WEAK_SHARED_TOKENS = 24
FP_CANDIDATE_DURATION_SECONDS = 6.0
FP_CANDIDATE_DURATION_RATIO = 0.035


def _new_comparison_log_path(recycle: Path) -> Path:
    """Create a per-run comparison log outside the scanned artist folder."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    roots = [
        recycle.parent / f"{recycle.name}_analysis_logs",
        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Karpuzikov" / "Duplicate Edition Analyzer" / "logs",
    ]
    last_error: Optional[Exception] = None
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            candidate = root / f"Duplicate Edition Analyzer Comparison {stamp}.jsonl"
            suffix = 2
            while candidate.exists():
                candidate = root / f"Duplicate Edition Analyzer Comparison {stamp} ({suffix}).jsonl"
                suffix += 1
            return candidate
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Could not create comparison log folder: {last_error}")


UNICODE_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", "·": ".", "•": "*", "‧": ".",
    "×": "&", "÷": "/", "⁄": "/", "＆": "&", "＋": "+", "＝": "=",
    "（": "(", "）": ")", "［": "[", "］": "]", "｛": "{", "｝": "}",
    "：": ":", "；": ";", "！": "!", "？": "?", "，": ",", "．": ".",
    "／": "/", "＼": "\\", "｜": "|", "＜": "<", "＞": ">", "＿": "_",
    "©": "(c)", "®": "(R)", "™": "TM", "№": "No.",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ",
}

VERSION_WORDS = {
    "remix", "mix", "live", "acoustic", "edit", "version", "extended", "instrumental",
    "acapella", "a", "capella", "dub", "demo", "radio", "club", "remaster", "remastered",
    "clean", "explicit", "single", "alternate", "alternative", "unplugged", "session",
}

EDITION_PATTERNS = [
    r"\s*\((?:deluxe(?: edition)?|limited(?: edition)?|special(?: edition)?|expanded(?: edition)?|"
    r"anniversary(?: edition)?|bonus track version|standard edition|remaster(?:ed)?(?: edition)?)\)\s*$",
    r"\s*\[(?:deluxe|limited|special|expanded|anniversary|remastered?)\]\s*$",
]


MOJIBAKE_REPLACEMENTS = {
    "â€™": "'", "â€˜": "'", "â€œ": '"', "â€": '"', "â€�": '"',
    "â€“": "-", "â€”": "-", "â€¦": "...", "Â": "",
}


def repair_mojibake(text: str) -> str:
    value = text or ""
    if any(marker in value for marker in ("Ã", "Â", "â")):
        try:
            repaired = value.encode("cp1252").decode("utf-8")
            if repaired.count("Ã") + repaired.count("Â") + repaired.count("â") < value.count("Ã") + value.count("Â") + value.count("â"):
                value = repaired
        except Exception:
            pass
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        value = value.replace(bad, good)
    return value


def ascii_punctuation(text: str) -> str:
    text = repair_mojibake(text)
    for src, dst in UNICODE_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_track_number(name: str) -> str:
    return re.sub(r"^\s*\d{1,3}(?:\s*[-._)]\s*|\s+)", "", name).strip()


def canonical_featured_title(text: str) -> str:
    text = ascii_punctuation(text)
    feat_names: List[str] = []

    def repl(m: re.Match) -> str:
        person = normalize_space(m.group(1).strip(" ()[]"))
        if person:
            feat_names.append(person)
        return " "

    patterns = [
        r"[\[(]\s*(?:feat(?:uring)?|ft)\.?\s+([^\])]+)[\])]",
        r"\s+(?:feat(?:uring)?|ft)\.?\s+([^\[(]+)$",
    ]
    for pat in patterns:
        text = re.sub(pat, repl, text, flags=re.I)
    text = normalize_space(text)
    if feat_names:
        uniq = []
        for n in feat_names:
            if n.lower() not in {x.lower() for x in uniq}:
                uniq.append(n)
        text += " (ft. " + " & ".join(uniq) + ")"
    return text


def normalize_title(text: str) -> str:
    text = canonical_featured_title(text)
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9$+()\[\]'. -]+", " ", text)
    text = re.sub(r"\s*([()\[\]])\s*", r"\1", text)
    return normalize_space(text)


def compact_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_title(text))


def semantic_qualifiers(text: str) -> Set[str]:
    n = normalize_title(text)
    words = set(re.findall(r"[a-z]+", n))
    q = words & VERSION_WORDS
    # The standalone letter/word "a" is not a version qualifier. It only
    # participates in the phrase "a capella".
    if "capella" in q:
        q.discard("a")
        q.discard("capella")
        q.add("acapella")
    else:
        q.discard("a")
    return q


REMIX_TRACK_RE = re.compile(r"\b(?:remix(?:es|ed)?|rmx|dub)\b", re.I)
CLUB_MIX_RE = re.compile(r"\bclub\s+mix(?:es)?\b", re.I)
TEMPO_EFFECT_REMIX_RE = re.compile(
    r"\b(?:sped\s*up|speed\s*up|slowed(?:\s*down)?|reverb(?:ed)?)\b",
    re.I,
)

# A mix credited/named after a person, DJ, producer, or act is a remix.
# Ordinary functional/style mix labels remain non-remix unless another rule says otherwise.
STANDARD_NON_REMIX_MIX_RE = re.compile(
    r"^(?:"
    r"original|extended|vip|v\.i\.p|radio|album|single|main|vocal|instrumental|studio|full|"
    r"ambient|chillout|downtempo|garage|house|trance|dance|full continuous|"
    r"7|12|7 dance|12 dance"
    r")\s+mix(?:\s*#?\d+)?$",
    re.I,
)
GENERIC_MIX_WORDS = {
    "original", "extended", "vip", "radio", "album", "single", "main", "vocal",
    "instrumental", "studio", "full", "ambient", "chillout", "downtempo",
    "garage", "house", "trance", "dance", "continuous", "mix", "new", "big",
    "smooth", "roll", "evolution",
}


def _mix_descriptor_candidates(text: str) -> List[str]:
    source = normalize_space(ascii_punctuation(text or ""))
    if not source:
        return []

    candidates: List[str] = []
    for part in re.findall(r"[\(\[]([^\)\]]+)[\)\]]", source):
        part = normalize_space(part)
        if re.search(r"\bmix(?:\s*#?\d+)?(?:\s+by\s+.+)?\s*$", part, re.I):
            candidates.append(part)

    trailing = re.search(r"(?:^|\s+-\s+|\s*;\s*)([^;]+?\bmix(?:\s*#?\d+)?)\s*$", source, re.I)
    if trailing:
        candidates.append(normalize_space(trailing.group(1)))

    # Also catch a bare descriptor passed directly to this function.
    if re.fullmatch(r".+\bmix(?:\s*#?\d+)?", source, re.I):
        candidates.append(source)

    seen: Set[str] = set()
    result: List[str] = []
    for candidate in candidates:
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _is_named_person_mix_descriptor(text: str) -> bool:
    """Detect 'Someone Mix' / 'Someone's Mix' style remix credits."""
    value = normalize_space(ascii_punctuation(text or "")).strip("()[] ")
    if re.search(r"\bmix(?:\s*#?\d+)?\s+by\s+\S.+$", value, re.I):
        return True
    if not re.search(r"\bmix(?:\s*#?\d+)?$", value, re.I):
        return False

    normalized = _normalized_pattern_text(value)
    if STANDARD_NON_REMIX_MIX_RE.fullmatch(normalized):
        return False

    prefix = re.sub(r"\s+mix(?:\s*#?\d+)?$", "", value, flags=re.I).strip()
    prefix_norm = _normalized_pattern_text(prefix)
    if not prefix_norm:
        return False

    # Explicit personal/act-credit signals.
    if re.search(r"(?:'s|’s)\b|\bby\s+|\b(?:dj|mc)\s+|\s(?:&|\+|vs\.?|versus|x)\s", prefix, re.I):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z0-9.'-]*", prefix)
    if not words:
        return False

    meaningful = [w for w in words if w.lower().strip(".'-") not in GENERIC_MIX_WORDS]
    if not meaningful:
        return False

    # Proper-name/act-looking labels: "Ferry Corsten", "Tom Lord-Alge",
    # "Madlib", "Timo Maas", "The Matrix", etc.
    capitalized = [
        w for w in meaningful
        if w[:1].isupper() or (len(w) >= 2 and w.isupper())
    ]
    if len(capitalized) >= 2:
        return True
    if len(meaningful) == 1 and len(capitalized) == 1:
        return True

    # DJ/producer handles often contain digits or internal capitals:
    # "only4Erol Mix", "2manydjs Mix", etc.
    compact_prefix = re.sub(r"[^A-Za-z0-9]", "", prefix)
    if re.search(r"\d", compact_prefix):
        return True
    if re.search(r"[a-z][A-Z]", compact_prefix):
        return True

    return False


LIVE_TRACK_RE = re.compile(r"\b(?:live|sessions?|unplugged)\b", re.I)
REMIX_RELEASE_RE = re.compile(
    r"\b(?:remix(?:es|ed)?|rmx|sped\s*up|speed\s*up|slowed(?:\s*down)?|reverb(?:ed)?)\b",
    re.I,
)
VERSION_LABEL_RE = re.compile(r"[\(\[]\s*([^\)\]]*?\bversion)\s*[\)\]]", re.I)

TRACK_PATTERN_DESCRIPTOR_RE = re.compile(
    r"\b(?:mix(?:es)?|remix(?:es|ed)?|dub|version|edit|acoustic|live|instrumental|"
    r"a\s*capella|acapella|demo|radio|club|remaster(?:ed)?|clean|explicit|session|"
    r"alternate|alternative|unplugged|hook|call\s*out|callout|excerpt|tv\s+track)\b",
    re.I,
)
CALLOUT_PATTERN_RE = re.compile(
    r"\b(?:(?:suggested\s+)?(?:call\s*out|callout)(?:\s+research)?(?:\s+hook)?|hook)\b",
    re.I,
)


LANGUAGE_VERSION_RE = re.compile(
    r"^(?:american|arabic|brazilian|cantonese|chinese|czech|danish|dutch|english|finnish|"
    r"french|german|greek|hebrew|hindi|hungarian|italian|japanese|korean|latin|mandarin|"
    r"norwegian|polish|portuguese|romanian|russian|spanish|swedish|thai|turkish|"
    r"ukrainian|vietnamese) version$",
    re.I,
)

STANDARD_PATTERN_TOKEN_RE = re.compile(
    r"\b(?:acoustic|instrumental|acapella|a\s+capella|demo|session|unplugged|"
    r"remaster(?:ed)?|radio|clean|explicit|edited)\b",
    re.I,
)

STANDARD_EDIT_RE = re.compile(r"\b(?:re[- ]?edit|edit)\b", re.I)

STANDARD_STYLE_MIX_SUFFIX_RE = re.compile(
    r"\b(?:original|extended|vip|v\.i\.p|radio|album|single|main|vocal|instrumental|"
    r"studio|full|full continuous|ambient|chillout|downtempo|garage|house|trance|dance|"
    r"big|new|smooth|low gain)"
    r"\s+mix(?:\s*#?\d+)?$",
    re.I,
)

STANDARD_VERSION_SUFFIX_RE = re.compile(
    r"\b(?:single|album|main|original|og|clean|explicit|edited|club|full|full length|"
    r"piano|harp|guitar|vocal|acoustic|instrumental|special|speed up|sped up|"
    r"alternate|alternative|chillout|ambient|downtempo|garage|house|trance|dance|pop|rock)"
    r"\s+version$",
    re.I,
)

STANDARD_EDITION_RE = re.compile(
    r"\b(?:clean|explicit|edited|deluxe|special|limited|standard)\s+edition$",
    re.I,
)

INSTRUMENT_VERSION_RE = re.compile(
    r"(?:^|\b)(?:piano|harp|guitar|strings?|orchestral|orchestra|vocal|instrumental|"
    r"acoustic|acapella|a\s+capella|karaoke)\s+version$",
    re.I,
)


def _normalized_pattern_text(text: str) -> str:
    raw = ascii_punctuation(text or "")
    # Normalize inch/foot marks in 7"/12" version labels before title cleanup.
    raw = re.sub(r"(?<=\d)[\"'′″]+", " ", raw)
    value = normalize_title(raw)
    value = value.replace('"', " ")
    value = re.sub(r"\s+", " ", value).strip(" .;:-")
    return value


def _is_standard_pattern(text: str) -> bool:
    """True for ordinary version structures that never need manual review."""
    value = _normalized_pattern_text(text)
    if not value:
        return True

    # Parser leftovers such as "Single Version / 2008".
    if re.fullmatch(r"(?:19|20)\d{2}", value):
        return True

    if LANGUAGE_VERSION_RE.fullmatch(value):
        return True
    if STANDARD_EDITION_RE.search(value):
        return True

    # Any prefix may precede these well-known structural families.
    if STANDARD_PATTERN_TOKEN_RE.search(value):
        return True
    if STANDARD_EDIT_RE.search(value):
        return True
    if STANDARD_STYLE_MIX_SUFFIX_RE.search(value):
        return True
    if STANDARD_VERSION_SUFFIX_RE.search(value):
        return True
    if INSTRUMENT_VERSION_RE.search(value):
        return True

    # 7-inch / 12-inch forms, with arbitrary artist/remixer prefixes.
    if re.search(r"\b(?:7|12)\s+(?:dance\s+)?(?:mix|version|edit)\b", value):
        return True
    if re.search(r"\b(?:7|12)\s+.*\b(?:mix|version|edit|instrumental)\b", value):
        return True

    # Common compound forms produced by slash/semicolon naming.
    if re.search(r"\bfull\s+continuous\s+mix$", value):
        return True
    if re.search(r"\bfull\s+version(?:\s+single)?$", value):
        return True
    if re.search(r"\bbonus\s+alt\.?\s+version$", value):
        return True
    if re.search(r"\bspecial\s+dj\s+version$", value):
        return True
    if re.fullmatch(r"(?:us|uk|eu|jp|de|fr|it|es|ca|au)\s+version", value):
        return True
    if re.search(r"\b(?:80s|90s|00s|10s|20s)\s+version$", value):
        return True
    if re.search(r"^version\s+(?:piano|voix|piano[- ]voix|vocal|instrumental)\b", value):
        return True

    # "Extended Version" remains standard even with a name/prefix before it.
    if re.search(r"\bextended\s+version$", value):
        return True

    return False


def _strip_common_review_parts(text: str) -> str:
    parts = [normalize_space(p) for p in re.split(r"\s*/\s*", text or "") if normalize_space(p)]
    if len(parts) <= 1:
        return normalize_space(text or "")
    unusual = [p for p in parts if not _is_standard_pattern(p)]
    # If every slash-separated component is standard (e.g. "Single Version / 2008"),
    # there is nothing to review.
    return " / ".join(unusual)


def _should_review_pattern(text: str) -> bool:
    value = normalize_space(text or "")
    if not value:
        return False
    if CALLOUT_PATTERN_RE.search(value):
        return True
    reduced = _strip_common_review_parts(value)
    return bool(reduced and not _is_standard_pattern(reduced))


def canonical_track_pattern(text: str) -> str:
    """Stable key used for the grouped pre-analysis included-content filter."""
    value = normalize_title(text or "")
    value = re.sub(r"\bcall\s+out\b", "callout", value)
    value = re.sub(r"\ba\s+capella\b", "acapella", value)
    words = set(re.findall(r"[a-z0-9]+", value))
    if "callout" in words or words == {"hook"}:
        return "suggested callout"
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def extract_track_patterns(title: str) -> List[Tuple[str, str]]:
    """Extract user-reviewable version descriptors without using them as duplicate evidence."""
    source = normalize_space(ascii_punctuation(strip_track_number(title or "")))
    if not source:
        return []

    candidates: List[str] = []

    for part in re.findall(r"[\(\[]([^\)\]]+)[\)\]]", source):
        part = normalize_space(part)
        if part and TRACK_PATTERN_DESCRIPTOR_RE.search(part) and _should_review_pattern(part):
            candidates.append(_strip_common_review_parts(part))

    trailing = re.search(r"\s+(?:-|:)\s+(.+)$", source)
    if trailing:
        part = normalize_space(trailing.group(1))
        if part and TRACK_PATTERN_DESCRIPTOR_RE.search(part) and _should_review_pattern(part):
            candidates.append(_strip_common_review_parts(part))

    # Catch callout/hook titles even when the whole track title is the descriptor,
    # e.g. "Suggested Callout Hook".
    for match in CALLOUT_PATTERN_RE.finditer(source):
        candidates.append(normalize_space(match.group(0)))

    seen: Set[Tuple[str, str]] = set()
    result: List[Tuple[str, str]] = []
    for candidate in candidates:
        key = canonical_track_pattern(candidate)
        if not key:
            continue
        pair = (key, candidate)
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def track_pattern_keys(track: "Track") -> Set[str]:
    keys: Set[str] = set()
    texts = {track.display_title, strip_track_number(track.path.stem)}
    for text in texts:
        for key, _variant in extract_track_patterns(text):
            keys.add(key)
    return keys


def collect_track_patterns(tracks: List["Track"]) -> List[Dict[str, object]]:
    """Collect only non-remix, non-live patterns for manual included-content review.

    Remix/live handling remains exclusively controlled by Save Remixes and
    Save Live recordings.
    """
    grouped: Dict[str, Dict[str, object]] = {}

    for track in tracks:
        classification_text = f"{track.display_title} {strip_track_number(track.path.stem)}"
        if is_remix_text(classification_text) or is_live_text(classification_text):
            continue

        found_for_track: Set[str] = set()
        for key, variant in extract_track_patterns(track.display_title):
            row = grouped.setdefault(
                key,
                {"key": key, "variants": Counter(), "examples": [], "count": 0},
            )
            variants = row["variants"]
            variants[variant] += 1
            examples = row["examples"]
            display_example = repair_mojibake(track.display_title)
            if display_example not in examples and len(examples) < 4:
                examples.append(display_example)
            found_for_track.add(key)

        for key in found_for_track:
            grouped[key]["count"] += 1

    result: List[Dict[str, object]] = []
    for key, row in grouped.items():
        variants: Counter = row["variants"]
        if key == "suggested callout":
            label = "Suggested Call Out / Callout Hook"
        else:
            label = variants.most_common(1)[0][0] if variants else key

        result.append({
            "key": key,
            "label": label,
            "count": int(row["count"]),
            "variants": [name for name, _count in variants.most_common()],
            "examples": list(row["examples"]),
        })

    return sorted(result, key=lambda row: str(row["label"]).lower())


def apply_pattern_exclusions(releases: List["Release"], excluded_pattern_keys: Set[str]) -> None:
    """Apply user pattern choices to included coverage only, never duplicate identity."""
    if not excluded_pattern_keys:
        return

    for release in releases:
        for track in release.tracks:
            classification_text = f"{track.display_title} {strip_track_number(track.path.stem)}"
            if is_remix_text(classification_text) or is_live_text(classification_text):
                continue
            matched = sorted(track_pattern_keys(track) & excluded_pattern_keys)
            if matched:
                track.exclude_from_coverage = True
                track.excluded_by_pattern = ", ".join(matched)



def refresh_heuristic_release_types(releases: List["Release"]) -> None:
    """Re-run only heuristic type detection using checkbox-visible tracks."""
    for rel in releases:
        if rel.type_source != "heuristic":
            continue
        first_tags = rel.tracks[0].tags if rel.tracks else {}
        rel.release_type, rel.type_source = infer_release_type(
            rel.title,
            rel.path.name,
            rel.included_track_count,
            first_tags,
        )


def is_remix_text(text: str) -> bool:
    # Explicit Remix/Dub, Club Mix, and tempo/effect variants such as
    # Sped Up, Slowed/Slowed Down, and Reverb are remix material.
    # In addition, a mix credited/named after a person/DJ/producer/act is a remix,
    # e.g. "Ferry Corsten Mix", "Tom Lord-Alge Mix", "Madlib's Mix".
    normalized = ascii_punctuation(text or "")
    if (
        REMIX_TRACK_RE.search(normalized)
        or CLUB_MIX_RE.search(normalized)
        or TEMPO_EFFECT_REMIX_RE.search(normalized)
    ):
        return True
    return any(_is_named_person_mix_descriptor(part) for part in _mix_descriptor_candidates(normalized))


def is_live_text(text: str) -> bool:
    return bool(LIVE_TRACK_RE.search(ascii_punctuation(text or "")))


def version_labels(text: str) -> Set[str]:
    """Return semantic named-version labels such as US/French/Guitar Version.

    Generic explicit/clean advisory labels are intentionally ignored at the
    current analyzer stage.
    """
    normalized = ascii_punctuation(text or "").lower()
    labels: Set[str] = set()
    for part in re.findall(r"[\[(]([^\])]+)[\])]", normalized):
        p = re.sub(r"[^a-z0-9]+", " ", part).strip()
        if "version" not in p:
            continue
        generic = bool(re.fullmatch(
            r"(?:(?:album|main|original) version(?: (?:explicit|edited|clean|dirty|censored))?|"
            r"(?:explicit|edited|clean|dirty|censored) version)", p
        ))
        if not generic:
            labels.add(p)
    return labels


def version_label_conflict(a: str, b: str) -> bool:
    va = version_labels(a)
    vb = version_labels(b)
    return va != vb and bool(va or vb)


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[^A-Z0-9-]+", "", (value or "").upper())


def _artist_signature(text: str) -> Set[str]:
    """Normalize multi-artist credits into a comparison set."""
    source = ascii_punctuation(text or "")
    parts = re.split(r"\s*(?:;|,|&|\band\b|\bx\b)\s*", source, flags=re.I)
    result: Set[str] = set()
    for part in parts:
        key = re.sub(r"[^a-z0-9]+", "", normalize_title(part))
        if key:
            result.add(key)
    return result


def _featured_credit_signature(text: str) -> Set[str]:
    """Return featured performers explicitly named in a track title."""
    source = ascii_punctuation(text or "")
    values: List[str] = []
    for part in re.findall(r"[\[(]([^\])]+)[\])]", source):
        match = re.match(r"\s*(?:feat(?:uring)?|ft)\.?\s+(.+)", part, re.I)
        if match:
            values.extend(
                re.split(r"\s*(?:,|&|\band\b|\bx\b)\s*", match.group(1), flags=re.I)
            )
    result: Set[str] = set()
    for value in values:
        key = re.sub(r"[^a-z0-9]+", "", normalize_title(value))
        if key:
            result.add(key)
    return result


def _version_descriptors(text: str) -> Set[str]:
    """Return meaningful parenthetical/bracket version descriptors.

    Featured credits, mastering labels, and generic clean/explicit advisory
    labels are not treated as separate versions here.
    """
    source = ascii_punctuation(strip_track_number(text or ""))
    result: Set[str] = set()
    for part in re.findall(r"[\[(]([^\])]+)[\])]", source):
        value = normalize_space(part)
        if re.match(r"^(?:feat(?:uring)?|ft)\.?\s+", value, re.I):
            continue
        normalized = _normalized_pattern_text(value)
        if not normalized:
            continue
        if re.fullmatch(
            r"(?:(?:album|main|original) version(?: (?:explicit|edited|clean|dirty|censored))?|"
            r"(?:explicit|edited|clean|dirty|censored)(?: version)?)",
            normalized,
            re.I,
        ):
            continue
        if re.fullmatch(r"(?:\d{4}\s+)?remaster(?:ed)?(?:\s+version)?", normalized, re.I):
            continue
        result.add(normalized)

    trailing = re.search(r"\s+(?:-|:)\s+(.+)$", source)
    if trailing:
        value = _normalized_pattern_text(trailing.group(1))
        if value and (
            TRACK_PATTERN_DESCRIPTOR_RE.search(value)
            or LANGUAGE_VERSION_RE.fullmatch(value)
        ):
            result.add(value)
    return result


def _semantic_version_descriptors(text: str) -> Set[str]:
    """Subset of descriptors that explicitly signal a different recording/version."""
    result: Set[str] = set()
    for value in _version_descriptors(text):
        if (
            TRACK_PATTERN_DESCRIPTOR_RE.search(value)
            or LANGUAGE_VERSION_RE.fullmatch(value)
            or re.search(
                r"\b(?:slowed|sped\s*up|speed\s*up|nightcore|karaoke|piano|harp|guitar|"
                r"orchestral|orchestra|vocal)\b",
                value,
                re.I,
            )
        ):
            result.add(value)
    return result


def _base_title_identity(text: str) -> str:
    """Title identity with bracketed descriptors/featured credits removed."""
    source = ascii_punctuation(strip_track_number(text or ""))
    source = re.sub(r"[\[(][^\])]+[\])]", " ", source)
    source = re.sub(r"\s+(?:feat(?:uring)?|ft)\.?\s+.+$", " ", source, flags=re.I)
    source = strip_advisory_version_for_match(source)
    source = re.sub(r"\b(?:\d{4}\s+)?remaster(?:ed)?\b", " ", source, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", normalize_title(source))


def _candidate_duration_close(a: Track, b: Track) -> bool:
    if a.duration <= 0 or b.duration <= 0:
        return False
    delta = abs(a.duration - b.duration)
    return delta <= max(
        FP_CANDIDATE_DURATION_SECONDS,
        FP_CANDIDATE_DURATION_RATIO * max(a.duration, b.duration),
    )


def _metadata_match_conflict(a: Track, b: Track) -> str:
    """Return a conservative reason to veto an otherwise-valid audio match.

    Audio remains required. Metadata only blocks a merge when it contains strong,
    contradictory evidence that the files are distinct recordings/versions.
    """
    mbid_a = _normalized_identifier(a.mbid)
    mbid_b = _normalized_identifier(b.mbid)
    isrc_a = _normalized_identifier(a.isrc)
    isrc_b = _normalized_identifier(b.isrc)

    if mbid_a and mbid_b and mbid_a != mbid_b:
        return f"different MusicBrainz recording MBIDs ({a.mbid} vs {b.mbid})"

    artists_a = _artist_signature(a.artist)
    artists_b = _artist_signature(b.artist)
    same_mbid = bool(mbid_a and mbid_b and mbid_a == mbid_b)
    same_isrc = bool(isrc_a and isrc_b and isrc_a == isrc_b)

    # A shared recording MBID is the strongest available identity evidence.
    if same_mbid:
        return ""

    # A shared ISRC plus the same credited performers is strong enough to tolerate
    # packaging/edition suffixes such as "(Pilule bleue)".
    if same_isrc and (not artists_a or not artists_b or artists_a == artists_b):
        return ""

    semantic_a = _semantic_version_descriptors(a.display_title)
    semantic_b = _semantic_version_descriptors(b.display_title)
    if semantic_a != semantic_b and (semantic_a or semantic_b):
        return (
            "conflicting semantic version descriptors "
            f"({sorted(semantic_a)} vs {sorted(semantic_b)})"
        )

    featured_a = _featured_credit_signature(a.display_title)
    featured_b = _featured_credit_signature(b.display_title)
    if (
        featured_a != featured_b
        and (featured_a or featured_b)
        and isrc_a and isrc_b and isrc_a != isrc_b
    ):
        return (
            "different featured performers with different ISRCs "
            f"({sorted(featured_a)} vs {sorted(featured_b)})"
        )

    if (
        artists_a and artists_b and artists_a != artists_b
        and isrc_a and isrc_b and isrc_a != isrc_b
    ):
        return (
            "different credited artists with different ISRCs "
            f"({sorted(artists_a)} vs {sorted(artists_b)})"
        )

    descriptors_a = _version_descriptors(a.display_title)
    descriptors_b = _version_descriptors(b.display_title)
    if (
        descriptors_a != descriptors_b
        and (descriptors_a or descriptors_b)
        and isrc_a and isrc_b and isrc_a != isrc_b
    ):
        return (
            "different title/version descriptors with different ISRCs "
            f"({sorted(descriptors_a)} vs {sorted(descriptors_b)})"
        )

    base_a = _base_title_identity(a.display_title)
    base_b = _base_title_identity(b.display_title)
    if (
        isrc_a and isrc_b and isrc_a != isrc_b
        and base_a and base_b and base_a != base_b
    ):
        return (
            "different ISRCs and different base titles "
            f"({a.display_title!r} vs {b.display_title!r})"
        )

    return ""


FEATURE_CREDIT_RE = re.compile(
    r"(?:[\[(]\s*)?\b(?:feat(?:uring)?|ft)\.?\s+([^\])]+)",
    re.I,
)


def _normalize_feature_artist(text: str) -> str:
    text = ascii_punctuation(text or "").lower()
    return re.sub(r"[^a-z0-9$]+", "", text)


def featured_artists(track: "Track") -> Set[str]:
    """Extract explicitly credited featured artists from title/artist metadata."""
    found: Set[str] = set()
    for text in (track.display_title, track.artist):
        normalized = ascii_punctuation(text or "")
        for match in FEATURE_CREDIT_RE.finditer(normalized):
            credit = match.group(1)
            credit = re.split(
                r"\b(?:remix(?:es|ed)?|dub|club\s+mix(?:es)?|live)\b",
                credit,
                maxsplit=1,
                flags=re.I,
            )[0]
            for name in re.split(r"\s*(?:,|&|;|\band\b)\s*", credit, flags=re.I):
                key = _normalize_feature_artist(name)
                if key:
                    found.add(key)
    return found


def remix_base_title_key(text: str) -> str:
    """Normalize a title only for locating its non-remix counterpart.

    This is NOT duplicate identity evidence. Duplicate identity remains audio-only.
    """
    value = ascii_punctuation(text or "")
    value = re.sub(
        r"[\[(]\s*(?:feat(?:uring)?|ft)\.?\s+[^\])]+[\])]",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s+(?:feat(?:uring)?|ft)\.?\s+.*$",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"[\[(]\s*[^)\]]*\b(?:remix(?:es|ed)?|dub|club\s+mix(?:es)?|"
        r"sped\s*up|speed\s*up|slowed(?:\s*down)?|reverb(?:ed)?)\b[^)\]]*[\])]",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:remix(?:es|ed)?|dub|club\s+mix(?:es)?|"
        r"sped\s*up|speed\s*up|slowed(?:\s*down)?|reverb(?:ed)?)\b",
        " ",
        value,
        flags=re.I,
    )
    return re.sub(r"[^a-z0-9]+", "", normalize_title(value))


def configure_exclusions(releases: List["Release"], exclude_remixes: bool, exclude_live: bool) -> None:
    """Apply Save Remixes / Save Live as absolute selection filters.

    When a checkbox is unchecked, matching tracks are invisible to release
    coverage, track counts, edition comparison and optimization. They remain in
    the physical folder if that release is retained, but they cannot make it win.
    """
    tracks = [track for rel in releases for track in rel.tracks]

    for track in tracks:
        classification_text = f"{track.display_title} {strip_track_number(track.path.stem)}"
        track.is_remix = is_remix_text(classification_text)
        track.is_live = is_live_text(classification_text)
        track.remix_feature_exception = False
        track.exclude_from_coverage = (
            (exclude_remixes and track.is_remix)
            or (exclude_live and track.is_live)
        )


def normalize_artist(text: str) -> str:
    text = ascii_punctuation(text).lower()
    text = re.sub(r"\b(?:feat(?:uring)?|ft)\.?\b.*$", "", text).strip()
    return re.sub(r"[^a-z0-9$]+", "", text)


def normalized_tag_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def tag_lookup(tags: Dict[str, str], *names: str) -> str:
    indexed = {normalized_tag_key(k): str(v) for k, v in tags.items()}
    for name in names:
        v = indexed.get(normalized_tag_key(name))
        if v:
            return v
    return ""


def parse_track_number(value: str, fallback: int) -> int:
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else fallback


def explicit_state(tags: Dict[str, str], *texts: str) -> str:
    # ITUNESADVISORY is authoritative when present:
    #   0 = clean
    #   1 = explicit
    advisory = tag_lookup(tags, "itunesadvisory", "rtng", "advisory")
    if advisory:
        av = advisory.strip().lower()
        if av in {"1", "explicit", "yes", "true"}:
            return "explicit"
        if av in {"0", "2", "clean", "no", "false"}:
            return "clean"
    ex = tag_lookup(tags, "explicit")
    if ex:
        ev = ex.strip().lower()
        if ev in {"1", "yes", "true", "explicit"}:
            return "explicit"
        if ev in {"0", "no", "false", "clean"}:
            return "clean"
    joined = " ".join(texts).lower()
    if re.search(r"(?:\(|\[|\b)explicit(?:\)|\]|\b)", joined):
        return "explicit"
    if re.search(r"(?:\(|\[|\b)clean(?:\)|\]|\b)", joined):
        return "clean"
    return "unknown"


def release_title_from_folder(name: str) -> str:
    s = re.sub(r"^\d{4}(?:-\d{2}-\d{2})?\s*-\s*", "", name).strip()
    s = re.sub(r"\s*\[[^\]]+\]\s*$", "", s).strip()
    return s


def album_family(title: str) -> str:
    s = ascii_punctuation(title)
    changed = True
    while changed:
        old = s
        for pat in EDITION_PATTERNS:
            s = re.sub(pat, "", s, flags=re.I).strip()
        changed = old != s
    s = re.sub(r"\s+-\s+(?:deluxe|limited|special|expanded|remastered?)\s*$", "", s, flags=re.I)
    return normalize_title(s)


def infer_release_type(title: str, folder_name: str, track_count: int, tags: Dict[str, str]) -> Tuple[str, str]:
    raw = tag_lookup(tags, "releasetype", "musicbrainzalbumtype", "albumtype", "primaryreleasetype").lower()
    if raw:
        if "album" in raw:
            return "album", "tag"
        if "ep" in raw:
            return "ep", "tag"
        if "single" in raw:
            return "single", "tag"
    x = f"{title} {folder_name}".lower()
    if re.search(r"\b(?:single)\b", x):
        return "single", "name"
    if re.search(r"\bep\b|remix(?:es)?|\brmx\b", x):
        return "ep", "name"
    if track_count >= 7:
        return "album", "heuristic"
    if track_count <= 2:
        return "single", "heuristic"
    return "ep", "heuristic"


def locate_executable(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Microsoft" / "WinGet" / "Links" / (name + ".exe")
        if candidate.exists():
            return str(candidate)
    return None


def run_hidden(args: List[str], **kwargs) -> subprocess.CompletedProcess:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, creationflags=creationflags, **kwargs)


def bootstrap_winget() -> Optional[str]:
    winget = locate_executable("winget")
    if winget:
        return winget
    if os.name != "nt":
        return None
    ps = locate_executable("powershell") or locate_executable("pwsh")
    if not ps:
        return None
    script = (
        "$ProgressPreference='SilentlyContinue';"
        "Install-PackageProvider -Name NuGet -Force | Out-Null;"
        "Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery | Out-Null;"
        "Import-Module Microsoft.WinGet.Client;"
        "Repair-WinGetPackageManager -Force -Latest"
    )
    try:
        run_hidden([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    except Exception:
        return None
    return locate_executable("winget")


def ensure_ffmpeg() -> Tuple[str, str]:
    ffmpeg = locate_executable("ffmpeg")
    ffprobe = locate_executable("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    winget = bootstrap_winget()
    if winget:
        run_hidden([
            winget, "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements", "--silent"
        ], check=False)
        ffmpeg = locate_executable("ffmpeg")
        ffprobe = locate_executable("ffprobe")
    if not (ffmpeg and ffprobe):
        raise RuntimeError("FFmpeg/FFprobe are required and could not be installed automatically.")
    return ffmpeg, ffprobe


def ensure_heybrochecklog():
    """Return hey-bro-check-log's score_log(), installing it app-locally if needed."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    deps = (
        local
        / "Karpuzikov"
        / "Duplicate Edition Analyzer"
        / "deps"
        / f"heybrochecklog-{HEYBROCHECKLOG_VERSION}"
    )
    deps_text = str(deps)
    if deps.is_dir() and deps_text not in sys.path:
        sys.path.insert(0, deps_text)

    try:
        module = importlib.import_module("heybrochecklog")
        return module.score_log
    except Exception:
        pass

    # Keep the dependency isolated from the user's normal Python environment.
    # The app already checks/repairs winget before dependency setup; use the
    # currently running interpreter only as the installer.
    try:
        pip_check = run_hidden(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        )
        if pip_check.returncode != 0:
            run_hidden(
                [sys.executable, "-m", "ensurepip", "--upgrade"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                check=False,
            )

        deps.mkdir(parents=True, exist_ok=True)
        cp = run_hidden(
            [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check", "--quiet",
                "--target", str(deps),
                HEYBROCHECKLOG_SOURCE,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "pip install failed")

        if deps_text not in sys.path:
            sys.path.insert(0, deps_text)
        importlib.invalidate_caches()
        sys.modules.pop("heybrochecklog", None)
        module = importlib.import_module("heybrochecklog")
        return module.score_log
    except Exception as exc:
        raise RuntimeError(
            "hey-bro-check-log is required for CD rip log quality scoring and "
            f"could not be installed automatically: {exc}"
        ) from exc


@dataclass
class Track:
    release_id: int
    path: Path
    index: int
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    tags: Dict[str, str] = field(default_factory=dict)
    mbid: str = ""
    isrc: str = ""
    fingerprint: Tuple[int, ...] = field(default_factory=tuple)
    fingerprint_duration: float = 0.0
    explicit: str = "unknown"
    group_id: int = -1
    is_remix: bool = False
    is_live: bool = False
    remix_feature_exception: bool = False
    exclude_from_coverage: bool = False
    excluded_by_pattern: str = ""
    codec_name: str = ""
    sample_rate: int = 0
    bit_depth: int = 0
    channels: int = 0
    bit_rate: int = 0
    file_size: int = 0

    @property
    def display_title(self) -> str:
        return self.title or strip_track_number(self.path.stem)


@dataclass
class Release:
    rid: int
    root_kind: str
    path: Path
    title: str
    paths: List[Path] = field(default_factory=list)
    scan_root: Optional[Path] = None
    tracks: List[Track] = field(default_factory=list)
    release_type: str = "unknown"
    type_source: str = ""
    family: str = ""
    source_quality: int = 0
    source_medium: str = "Unknown"
    explicit: str = "unknown"
    has_cue: bool = False
    has_rip_log: bool = False
    has_audiochecker: bool = False
    rip_log_paths: List[Path] = field(default_factory=list)
    rip_log_scores: List[int] = field(default_factory=list)
    rip_log_rippers: List[str] = field(default_factory=list)
    rip_log_flagged: int = 0
    rip_log_unrecognized: List[str] = field(default_factory=list)

    @property
    def source_paths(self) -> List[Path]:
        return list(self.paths) if self.paths else [self.path]

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def included_track_count(self) -> int:
        """Tracks included by the active checkboxes/pattern choices."""
        return sum(1 for t in self.tracks if not t.exclude_from_coverage)

    @property
    def ignored_track_count(self) -> int:
        """Tracks skipped by the active checkboxes/pattern choices."""
        return sum(1 for t in self.tracks if t.exclude_from_coverage)

    @property
    def groups(self) -> Set[int]:
        return {t.group_id for t in self.tracks if t.group_id >= 0 and not t.exclude_from_coverage}

    @property
    def excluded_only(self) -> bool:
        return bool(self.tracks) and all(t.exclude_from_coverage for t in self.tracks)

    @property
    def has_remixes(self) -> bool:
        # Used only for duplicate destination routing. A package explicitly titled
        # Remixes belongs under !Remixes even when it also contains included versions.
        package_text = f"{self.title} {self.path.name}"
        return any(t.is_remix for t in self.tracks) or bool(REMIX_RELEASE_RE.search(ascii_punctuation(package_text)))

    @property
    def remix_only(self) -> bool:
        return bool(self.tracks) and all(t.is_remix for t in self.tracks)


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.r[ra] < self.r[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        if self.r[ra] == self.r[rb]:
            self.r[ra] += 1


def probe_track(ffprobe: str, track: Track) -> None:
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries",
        "stream=codec_name,codec_type,sample_rate,bits_per_sample,bits_per_raw_sample,channels,bit_rate:"
        "format=duration,bit_rate:format_tags",
        "-of", "json", str(track.path)
    ]
    cp = run_hidden(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", check=False)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "ffprobe failed")
    data = json.loads(cp.stdout or "{}")
    fmt = data.get("format") or {}
    track.duration = float(fmt.get("duration") or 0.0)
    tags = fmt.get("tags") or {}
    track.tags = {str(k): str(v) for k, v in tags.items()}
    track.title = tag_lookup(track.tags, "title") or strip_track_number(track.path.stem)
    track.artist = tag_lookup(track.tags, "artist")
    track.album = tag_lookup(track.tags, "album")
    track.mbid = tag_lookup(track.tags, "musicbrainztrackid", "musicbrainzrecordingid", "musicbrainz track id")
    track.isrc = tag_lookup(track.tags, "isrc")
    track.explicit = explicit_state(track.tags, track.title, track.path.name, track.album)

    audio_stream = next(
        (s for s in (data.get("streams") or []) if str(s.get("codec_type") or "").lower() == "audio"),
        {},
    )
    track.codec_name = str(audio_stream.get("codec_name") or "").lower()
    try:
        track.sample_rate = int(audio_stream.get("sample_rate") or 0)
    except (TypeError, ValueError):
        track.sample_rate = 0
    try:
        track.bit_depth = int(audio_stream.get("bits_per_raw_sample") or audio_stream.get("bits_per_sample") or 0)
    except (TypeError, ValueError):
        track.bit_depth = 0
    try:
        track.channels = int(audio_stream.get("channels") or 0)
    except (TypeError, ValueError):
        track.channels = 0
    try:
        track.bit_rate = int(audio_stream.get("bit_rate") or fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        track.bit_rate = 0
    try:
        track.file_size = track.path.stat().st_size
    except OSError:
        track.file_size = 0


def locate_fpcalc() -> Optional[str]:
    p = locate_executable("fpcalc")
    if p:
        return p
    candidates = []
    pf = os.environ.get("ProgramFiles")
    pf86 = os.environ.get("ProgramFiles(x86)")
    local = os.environ.get("LOCALAPPDATA")
    if pf:
        candidates.append(Path(pf) / "MusicBrainz Picard" / "fpcalc.exe")
    if pf86:
        candidates.append(Path(pf86) / "MusicBrainz Picard" / "fpcalc.exe")
    if local:
        candidates.extend([
            Path(local) / "Programs" / "MusicBrainz Picard" / "fpcalc.exe",
            Path(local) / "Karpuzikov" / "Discography Builder" / f"Chromaprint-{CHROMAPRINT_VERSION}" / "fpcalc.exe",
        ])
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def ensure_fpcalc() -> str:
    fpcalc = locate_fpcalc()
    if fpcalc:
        return fpcalc
    if os.name != "nt":
        raise RuntimeError("fpcalc/Chromaprint is required.")

    # Respect the common dependency-bootstrap rule first. Chromaprint itself is
    # downloaded from the official AcoustID/Chromaprint release if not already available.
    bootstrap_winget()
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    target = local / "Karpuzikov" / "Discography Builder" / f"Chromaprint-{CHROMAPRINT_VERSION}"
    target.mkdir(parents=True, exist_ok=True)
    exe = target / "fpcalc.exe"
    if exe.exists():
        return str(exe)

    zip_path = target / "chromaprint.zip"
    try:
        urllib.request.urlretrieve(CHROMAPRINT_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target)
        found = next(target.rglob("fpcalc.exe"), None)
        if not found:
            raise RuntimeError("fpcalc.exe was not found in the downloaded Chromaprint archive.")
        if found != exe:
            shutil.copy2(found, exe)
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
    return str(exe)


def chromaprint_fingerprint(fpcalc: str, track: Track) -> Tuple[Tuple[int, ...], float]:
    cmd = [fpcalc, "-length", "0", "-raw", "-json", str(track.path)]
    cp = run_hidden(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", check=False)
    if cp.returncode not in (0, 3):
        raise RuntimeError(cp.stderr.strip() or "fpcalc failed")
    data = json.loads((cp.stdout or "{}").strip())
    fp = tuple(int(x) & 0xFFFFFFFF for x in (data.get("fingerprint") or []))
    if not fp:
        raise RuntimeError("fpcalc returned an empty fingerprint")
    return fp, float(data.get("duration") or track.duration or 0.0)


ADVISORY_VERSION_PAREN_RE = re.compile(
    r"[\[(]\s*(?:(?:album|main|original)\s+version(?:\s+(?:explicit|edited|clean|dirty|censored))?|"
    r"(?:explicit|edited|clean|dirty|censored)\s+version|(?:explicit|clean|dirty|censored))\s*[\])]",
    re.I,
)
ADVISORY_VERSION_TRAILING_RE = re.compile(
    r"\s*(?:-|:)\s*(?:(?:album|main|original)\s+version(?:\s+(?:explicit|edited|clean|dirty|censored))?|"
    r"(?:explicit|edited|clean|dirty|censored)\s+version)\s*$",
    re.I,
)
CENSOR_MASK_RE = re.compile(r"(?:\*{2,}|_{2,})")


def strip_featured_credit_for_match(text: str) -> str:
    text = ascii_punctuation(text or "")
    text = re.sub(r"[\[(]\s*(?:feat(?:uring)?|ft)\.?\s+[^\])]+[\])]", " ", text, flags=re.I)
    text = re.sub(r"\s+(?:feat(?:uring)?|ft)\.?\s+[^\[(]+$", " ", text, flags=re.I)
    return normalize_space(text)


def strip_advisory_version_for_match(text: str) -> str:
    """Ignore explicit/clean advisory wording in the current analyzer stage.

    This intentionally strips labels such as Album Version Edited/Explicit and
    Explicit/Clean Version, while preserving semantic versions such as Radio Edit,
    Acoustic, French Version, US Version, Guitar Version, etc.
    """
    text = ADVISORY_VERSION_PAREN_RE.sub(" ", text or "")
    text = ADVISORY_VERSION_TRAILING_RE.sub(" ", text)
    return normalize_space(text)


def title_for_match(text: str) -> str:
    text = strip_featured_credit_for_match(text)
    text = strip_advisory_version_for_match(text)
    return text


def identity_title(text: str) -> str:
    n = normalize_title(title_for_match(text))
    # Mastering labels are not separate songs for this project's coverage goal.
    n = re.sub(r"[\[(]?\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+version)?\s*[\])] ?", " ", n, flags=re.I)
    n = re.sub(r"\b(?:\d{4}\s+)?remaster(?:ed)?\b", " ", n, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", normalize_space(n))


def content_qualifiers(text: str) -> Set[str]:
    q = semantic_qualifiers(title_for_match(text))
    q.discard("remaster")
    q.discard("remastered")
    # Explicit/clean/edited advisory state is intentionally not an active
    # analyzer criterion until a reliable detector is implemented.
    q.discard("explicit")
    q.discard("clean")
    q.discard("version")
    return q


def masked_title_compatible(a: str, b: str) -> bool:
    """Allow censorship masks such as Ni**as / Ni__as to match the full title."""
    def pattern(text: str):
        raw = title_for_match(text or "").lower()
        if not CENSOR_MASK_RE.search(raw):
            return None
        raw = CENSOR_MASK_RE.sub(" CENSORMASK ", raw)
        raw = ascii_punctuation(raw)
        parts = re.split(r"\s*CENSORMASK\s*", raw)
        keys = [re.sub(r"[^a-z0-9]+", "", normalize_title(x)) for x in parts]
        return "^" + "[a-z0-9]*".join(re.escape(k) for k in keys) + "$"

    ka = identity_title(a)
    kb = identity_title(b)
    pa = pattern(a)
    pb = pattern(b)
    return bool((pa and re.fullmatch(pa, kb)) or (pb and re.fullmatch(pb, ka)))


def fingerprint_similarity(fp1: Tuple[int, ...], fp2: Tuple[int, ...]) -> Optional[Tuple[float, float, float, int, float, float, int]]:
    """Compare Chromaprint fingerprints using audio only.

    Always evaluate zero/near-zero alignment in addition to histogram candidates.
    The previous implementation could omit the real shift when alternate
    masterings produced too few identical high-bit fingerprint words.
    """
    if not fp1 or not fp2 or min(len(fp1), len(fp2)) < 20:
        return None

    positions12 = defaultdict(list)
    positions10 = defaultdict(list)
    for i, value in enumerate(fp1):
        positions12[(value >> 20) & 0xFFF].append(i)
        positions10[(value >> 22) & 0x3FF].append(i)

    histogram = Counter()
    for j, value in enumerate(fp2):
        for i in positions12.get((value >> 20) & 0xFFF, ()):
            histogram[i - j] += 4
        for i in positions10.get((value >> 22) & 0x3FF, ()):
            histogram[i - j] += 1

    shifts: List[int] = []
    seen_shifts: Set[int] = set()

    def add_shift(value: int) -> None:
        if value not in seen_shifts:
            seen_shifts.add(value)
            shifts.append(value)

    for shift in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6):
        add_shift(shift)
    for shift, _count in histogram.most_common(24):
        add_shift(shift)

    best = None
    for shift in shifts:
        a0 = max(shift, 0)
        b0 = max(-shift, 0)
        n = min(len(fp1) - a0, len(fp2) - b0)
        if n < 20:
            continue
        overlap = n / max(1, min(len(fp1), len(fp2)))
        dists = [(fp1[a0 + k] ^ fp2[b0 + k]).bit_count() for k in range(n)]
        avg = sum(dists) / n
        good = sum(d <= 10 for d in dists) / n
        excellent = sum(d <= 5 for d in dists) / n
        ordered = sorted(dists)
        median = float(ordered[n // 2]) if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
        p90 = ordered[int(0.90 * (n - 1))]
        cand = (avg, good, overlap, shift, excellent, median, p90)
        if best is None or (avg, -good, -excellent, -overlap) < (best[0], -best[1], -best[4], -best[2]):
            best = cand
    return best

def _fingerprint_segment_is_silence(segment: Tuple[int, ...]) -> bool:
    """Recognize long Chromaprint regions produced by digital/near silence."""
    if len(segment) < FP_SILENCE_MIN_FRAMES:
        return True
    counts = Counter(segment)
    dominant = counts.most_common(1)[0][1] / len(segment)
    unique_fraction = len(counts) / len(segment)
    return dominant >= 0.90 or unique_fraction <= 0.03


def _unmatched_fingerprint_is_silence(fp1: Tuple[int, ...], fp2: Tuple[int, ...], shift: int) -> bool:
    a0 = max(shift, 0)
    b0 = max(-shift, 0)
    n = min(len(fp1) - a0, len(fp2) - b0)
    segments = (fp1[:a0], fp2[:b0], fp1[a0 + n:], fp2[b0 + n:])
    return all(_fingerprint_segment_is_silence(seg) for seg in segments)


def fingerprint_auto_match_values(
    fp1: Tuple[int, ...],
    duration1: float,
    fp2: Tuple[int, ...],
    duration2: float,
) -> Tuple[bool, Optional[Tuple[float, float, float, int, float, float, int]]]:
    sim = fingerprint_similarity(fp1, fp2)
    if not sim:
        return False, None

    score, good, overlap, shift, excellent, median, p90 = sim

    strict_match = (
        overlap >= FP_MIN_OVERLAP
        and score <= FP_AUTO_SCORE
        and good >= FP_AUTO_GOOD_FRACTION
        and excellent >= FP_AUTO_EXCELLENT_FRACTION
        and median <= FP_AUTO_MEDIAN_MAX
        and p90 <= FP_AUTO_P90_MAX
    )

    longer = max(duration1, duration2, 1.0)
    shorter = min(duration1, duration2, longer)
    duration_delta = abs(duration1 - duration2)

    mastering_match = (
        overlap >= FP_MASTERING_MIN_OVERLAP
        and duration_delta <= max(
            FP_MASTERING_MAX_DURATION_SECONDS,
            FP_MASTERING_MAX_DURATION_RATIO * longer,
        )
        and score <= FP_MASTERING_SCORE
        and good >= FP_MASTERING_GOOD_FRACTION
        and median <= FP_MASTERING_MEDIAN_MAX
        and p90 <= FP_MASTERING_P90_MAX
    )

    if not strict_match and not mastering_match:
        return False, sim

    length_ratio = shorter / longer
    if length_ratio < 0.94 or duration_delta > max(12.0, 0.06 * longer):
        if not _unmatched_fingerprint_is_silence(fp1, fp2, shift):
            return False, sim

    return True, sim

def fingerprint_auto_match(a: Track, b: Track) -> Tuple[bool, Optional[Tuple[float, float, float, int, float, float, int]]]:
    """High-confidence audio-only identity test."""
    return fingerprint_auto_match_values(
        a.fingerprint, a.duration, b.fingerprint, b.duration
    )


_COMPARE_TRACK_DATA: List[Tuple[Tuple[int, ...], float]] = []


def _init_compare_worker(track_data: List[Tuple[Tuple[int, ...], float]]) -> None:
    global _COMPARE_TRACK_DATA
    _COMPARE_TRACK_DATA = track_data


def _compare_pair_worker(pair: Tuple[int, int]):
    a, b = pair
    fp1, d1 = _COMPARE_TRACK_DATA[a]
    fp2, d2 = _COMPARE_TRACK_DATA[b]
    matched, sim = fingerprint_auto_match_values(fp1, d1, fp2, d2)
    return a, b, matched, sim

def _release_tree_files(folder: Path) -> Tuple[List[Path], List[Path]]:
    """Return audio files and all files for one release container recursively."""
    audio: List[Path] = []
    all_files: List[Path] = []
    for current, dirs, files in os.walk(folder):
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        base = Path(current)
        for name in files:
            fp = base / name
            all_files.append(fp)
            if fp.suffix.lower() in AUDIO_EXTS:
                audio.append(fp)
    return audio, all_files


DISC_FOLDER_RE = re.compile(r"^(?:cd|disc|disk)\s*[-_. ]*\d+\b|^bonus\s+(?:cd|disc|disk)\b", re.I)
SIBLING_DISC_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)\s+(?:cd|disc|disk)\s*[-_. ]*(?P<num>\d+)\s*$",
    re.I,
)


def _direct_audio_files(folder: Path) -> List[Path]:
    try:
        return sorted(
            (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return []


def _audio_child_dirs(folder: Path) -> List[Path]:
    children: List[Path] = []
    try:
        dirs = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return children
    for child in dirs:
        audio, _ = _release_tree_files(child)
        if audio:
            children.append(child)
    return children


def _is_multidisc_release_container(folder: Path, audio_children: List[Path]) -> bool:
    """Parent release with internal CD1/CD2/Disc 1 subfolders."""
    if not audio_children:
        return False
    return all(DISC_FOLDER_RE.search(child.name.strip()) for child in audio_children)


def _sibling_disc_parts(name: str) -> Optional[Tuple[str, int]]:
    match = SIBLING_DISC_SUFFIX_RE.match(name.strip())
    if not match:
        return None
    return match.group("base").strip(), int(match.group("num"))


def _discover_release_groups(root: Path) -> List[List[Path]]:
    """Return logical releases, each as one or more physical folders.

    Handles both layouts:
      Release/CD1 + Release/CD2
      Release CD 1 + Release CD 2   (sibling folders)
    Organizational folders such as Albums/Other/Singles are traversed.
    """
    groups: List[List[Path]] = []

    def walk(folder: Path) -> None:
        direct_audio = _direct_audio_files(folder)
        audio_children = _audio_child_dirs(folder)

        if direct_audio:
            groups.append([folder])
            return

        if _is_multidisc_release_container(folder, audio_children):
            groups.append([folder])
            return

        try:
            children = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
        except OSError:
            return

        sibling_discs: Dict[str, List[Tuple[int, Path]]] = defaultdict(list)
        for child in children:
            parts = _sibling_disc_parts(child.name)
            if parts and _direct_audio_files(child):
                base, number = parts
                sibling_discs[os.path.normcase(base)].append((number, child))

        consumed: Set[Path] = set()
        for entries in sibling_discs.values():
            if len(entries) < 2:
                continue
            ordered = [p for _n, p in sorted(entries, key=lambda x: (x[0], x[1].name.lower()))]
            groups.append(ordered)
            consumed.update(ordered)

        for child in children:
            if child in consumed:
                continue
            audio, _ = _release_tree_files(child)
            if audio:
                walk(child)

    walk(root)
    groups.sort(key=lambda paths: str(paths[0]).lower())
    return groups


def discover_releases(root: Path, root_kind: str, start_id: int) -> List[Release]:
    """Discover logical releases recursively, including sibling multi-disc sets."""
    releases: List[Release] = []
    rid = start_id

    for physical_paths in _discover_release_groups(root):
        audio: List[Path] = []
        all_files: List[Path] = []
        for p in physical_paths:
            part_audio, part_files = _release_tree_files(p)
            audio.extend(part_audio)
            all_files.extend(part_files)
        if not audio:
            continue

        primary = physical_paths[0]
        sibling = _sibling_disc_parts(primary.name) if len(physical_paths) > 1 else None
        logical_name = sibling[0] if sibling else primary.name
        title = release_title_from_folder(logical_name)

        cue = any(f.suffix.lower() == ".cue" for f in all_files)
        logs = [f for f in all_files if f.suffix.lower() == ".log"]
        audiochecker = any(f.name.lower() == "audiochecker.log" for f in logs)
        rip_logs = [f for f in logs if f.name.lower() != "audiochecker.log"]
        rip_log = bool(rip_logs)
        quality = 100 if cue and rip_log else 75 if cue else 50 if audiochecker else 40

        rel = Release(
            rid=rid,
            root_kind=root_kind,
            path=primary,
            title=title,
            paths=list(physical_paths),
            scan_root=root,
            source_quality=quality,
            has_cue=cue,
            has_rip_log=rip_log,
            has_audiochecker=audiochecker,
            rip_log_paths=list(rip_logs),
        )
        for i, ap in enumerate(audio, 1):
            rel.tracks.append(Track(release_id=rid, path=ap, index=i))
        releases.append(rel)
        rid += 1
    return releases


def _fingerprint_tokens(fp: Tuple[int, ...]) -> Set[int]:
    """Cheap title-independent prefilter for full Chromaprint comparison."""
    if len(fp) < 2:
        return set()
    # Consecutive high-12-bit pairs are stable enough to find likely candidates
    # while making random collisions uncommon. Position is intentionally ignored
    # so small leading/trailing offsets still become candidates.
    return {
        (((fp[i] >> 20) & 0xFFF) << 12) | ((fp[i + 1] >> 20) & 0xFFF)
        for i in range(0, len(fp) - 1, 2)
    }


def _comparison_decision_details(
    fp1: Tuple[int, ...],
    duration1: float,
    fp2: Tuple[int, ...],
    duration2: float,
    matched: bool,
    sim: Optional[Tuple[float, float, float, int, float, float, int]],
) -> Dict[str, object]:
    """Explain every threshold involved in one fingerprint decision."""
    longer = max(duration1, duration2, 1.0)
    shorter = min(duration1, duration2, longer)
    duration_delta = abs(duration1 - duration2)
    length_ratio = shorter / longer

    details: Dict[str, object] = {
        "worker_matched": bool(matched),
        "duration_delta_seconds": round(duration_delta, 6),
        "length_ratio": round(length_ratio, 6),
    }
    if not sim:
        details.update({
            "similarity_available": False,
            "accepted_by": [],
            "strict_pass": False,
            "mastering_pass": False,
            "length_gate_triggered": False,
            "length_gate_pass": False,
            "rejection_reasons": ["fingerprint_similarity returned no comparable result"],
        })
        return details

    score, good, overlap, shift, excellent, median, p90 = sim
    strict_checks = {
        "overlap": overlap >= FP_MIN_OVERLAP,
        "score": score <= FP_AUTO_SCORE,
        "good_fraction": good >= FP_AUTO_GOOD_FRACTION,
        "excellent_fraction": excellent >= FP_AUTO_EXCELLENT_FRACTION,
        "median": median <= FP_AUTO_MEDIAN_MAX,
        "p90": p90 <= FP_AUTO_P90_MAX,
    }
    mastering_duration_limit = max(
        FP_MASTERING_MAX_DURATION_SECONDS,
        FP_MASTERING_MAX_DURATION_RATIO * longer,
    )
    mastering_checks = {
        "overlap": overlap >= FP_MASTERING_MIN_OVERLAP,
        "duration_delta": duration_delta <= mastering_duration_limit,
        "score": score <= FP_MASTERING_SCORE,
        "good_fraction": good >= FP_MASTERING_GOOD_FRACTION,
        "median": median <= FP_MASTERING_MEDIAN_MAX,
        "p90": p90 <= FP_MASTERING_P90_MAX,
    }
    strict_pass = all(strict_checks.values())
    mastering_pass = all(mastering_checks.values())
    preliminary_pass = strict_pass or mastering_pass

    length_gate_triggered = bool(
        preliminary_pass
        and (length_ratio < 0.94 or duration_delta > max(12.0, 0.06 * longer))
    )
    unmatched_is_silence: Optional[bool] = None
    length_gate_pass = True
    if length_gate_triggered:
        unmatched_is_silence = _unmatched_fingerprint_is_silence(fp1, fp2, shift)
        length_gate_pass = bool(unmatched_is_silence)

    rejection_reasons: List[str] = []
    if not preliminary_pass:
        strict_failed = [name for name, passed in strict_checks.items() if not passed]
        mastering_failed = [name for name, passed in mastering_checks.items() if not passed]
        rejection_reasons.append("strict failed: " + ", ".join(strict_failed))
        rejection_reasons.append("mastering failed: " + ", ".join(mastering_failed))
    elif not length_gate_pass:
        rejection_reasons.append("length gate failed: unmatched fingerprint content is not silence")

    accepted_by: List[str] = []
    if strict_pass:
        accepted_by.append("strict")
    if mastering_pass:
        accepted_by.append("mastering")

    details.update({
        "similarity_available": True,
        "score": round(score, 6),
        "good_fraction": round(good, 6),
        "excellent_fraction": round(excellent, 6),
        "overlap": round(overlap, 6),
        "median": round(median, 6),
        "p90": int(p90),
        "shift": int(shift),
        "strict_checks": strict_checks,
        "strict_pass": strict_pass,
        "mastering_checks": mastering_checks,
        "mastering_duration_limit_seconds": round(mastering_duration_limit, 6),
        "mastering_pass": mastering_pass,
        "accepted_by": accepted_by,
        "length_gate_triggered": length_gate_triggered,
        "unmatched_is_silence": unmatched_is_silence,
        "length_gate_pass": length_gate_pass,
        "derived_final_match": bool(preliminary_pass and length_gate_pass),
        "decision_consistent": bool(matched) == bool(preliminary_pass and length_gate_pass),
        "rejection_reasons": rejection_reasons,
    })
    return details


def _comparison_track_log_data(track: Track) -> Dict[str, object]:
    return {
        "release_id": track.release_id,
        "path": str(track.path),
        "file": track.path.name,
        "title": track.display_title,
        "artist": track.artist,
        "album": track.album,
        "duration_seconds": round(track.duration, 6),
        "fingerprint_duration_seconds": round(track.fingerprint_duration, 6),
        "mbid": track.mbid,
        "isrc": track.isrc,
        "identity_title": identity_title(track.display_title),
        "base_title_identity": _base_title_identity(track.display_title),
        "content_qualifiers": sorted(content_qualifiers(track.display_title)),
        "version_descriptors": sorted(_version_descriptors(track.display_title)),
        "semantic_version_descriptors": sorted(_semantic_version_descriptors(track.display_title)),
        "featured_credit_signature": sorted(_featured_credit_signature(track.display_title)),
        "artist_signature": sorted(_artist_signature(track.artist)),
        "is_remix": track.is_remix,
        "is_live": track.is_live,
        "excluded_from_coverage": track.exclude_from_coverage,
    }


def merge_equivalent_tracks(
    tracks: List[Track],
    progress_cb=None,
    comparison_log_path: Optional[Path] = None,
) -> Tuple[Dict[int, List[int]], List[str]]:
    """Group recordings from audio fingerprints with a conservative metadata veto.

    Candidate discovery now uses strong fingerprint-token overlap, a weaker
    token+duration fallback, exact ID indexes, and same-base-title+duration
    fallback. Pure duration-only all-pairs comparison is intentionally avoided.
    """
    uf = UnionFind(len(tracks))
    notes: List[str] = []
    tokens: List[Set[int]] = [_fingerprint_tokens(t.fingerprint) for t in tracks]

    token_tracks: Dict[int, List[int]] = defaultdict(list)
    indexed = sum(1 for x in tokens if x)
    if progress_cb:
        progress_cb("Indexing fingerprints...", 0, max(1, indexed))

    done = 0
    for i, values in enumerate(tokens):
        if not values:
            continue
        for token in values:
            token_tracks[token].append(i)
        done += 1
        if progress_cb and (done % 25 == 0 or done == indexed):
            progress_cb("Indexing fingerprints...", done, max(1, indexed))

    buckets = [ids for ids in token_tracks.values() if len(ids) >= 2]
    pair_counts: Counter = Counter()
    total_buckets = len(buckets)
    if progress_cb:
        progress_cb("Finding audio candidates...", 0, max(1, total_buckets))

    for bi, ids in enumerate(buckets, 1):
        ids = sorted(set(ids))
        for a, b in itertools.combinations(ids, 2):
            pair_counts[(a, b)] += 1
        if progress_cb and (bi % 250 == 0 or bi == total_buckets):
            progress_cb("Finding audio candidates...", bi, max(1, total_buckets))

    candidate_reasons: Dict[Tuple[int, int], Set[str]] = defaultdict(set)

    # Primary fingerprint-token routes.
    for pair, shared in pair_counts.items():
        a, b = pair
        if shared >= FP_CANDIDATE_STRONG_SHARED_TOKENS:
            candidate_reasons[pair].add("fingerprint_tokens_strong")
        elif (
            shared >= FP_CANDIDATE_WEAK_SHARED_TOKENS
            and _candidate_duration_close(tracks[a], tracks[b])
        ):
            candidate_reasons[pair].add("fingerprint_tokens_weak+duration")

    # Exact identifiers are candidate hints only; audio still has to pass.
    mbid_index: Dict[str, List[int]] = defaultdict(list)
    isrc_index: Dict[str, List[int]] = defaultdict(list)
    for i, track in enumerate(tracks):
        if not track.fingerprint:
            continue
        mbid = _normalized_identifier(track.mbid)
        isrc = _normalized_identifier(track.isrc)
        if mbid:
            mbid_index[mbid].append(i)
        if isrc:
            isrc_index[isrc].append(i)

    for ids in mbid_index.values():
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            candidate_reasons[(a, b)].add("same_mbid")
    for ids in isrc_index.values():
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            candidate_reasons[(a, b)].add("same_isrc")

    # Conservative fallback for alternate masterings whose cheap fingerprint
    # tokens diverge: same base title + close duration still gets a full audio test.
    title_index: Dict[str, List[int]] = defaultdict(list)
    for i, track in enumerate(tracks):
        if not track.fingerprint or track.duration <= 0:
            continue
        key = _base_title_identity(track.display_title)
        if key:
            title_index[key].append(i)

    for ids in title_index.values():
        ordered = sorted(ids, key=lambda i: tracks[i].duration)
        for pos, a in enumerate(ordered):
            for b in ordered[pos + 1:]:
                if not _candidate_duration_close(tracks[a], tracks[b]):
                    if (
                        tracks[b].duration - tracks[a].duration
                        > max(
                            FP_CANDIDATE_DURATION_SECONDS,
                            FP_CANDIDATE_DURATION_RATIO * tracks[b].duration,
                        )
                    ):
                        break
                    continue
                pair = (min(a, b), max(a, b))
                candidate_reasons[pair].add("same_base_title+duration")

    candidate_pairs = sorted(candidate_reasons)
    total_candidates = len(candidate_pairs)
    total_possible = indexed * (indexed - 1) // 2
    prefilter_rejected = max(0, total_possible - total_candidates)

    log_handle = None
    log_counts: Counter = Counter()
    processed_pairs: Set[Tuple[int, int]] = set()
    if comparison_log_path is not None:
        try:
            comparison_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = comparison_log_path.open("w", encoding="utf-8", newline="\n")
            route_counts = Counter(
                reason
                for reasons in candidate_reasons.values()
                for reason in reasons
            )
            header = {
                "record_type": "run",
                "app": APP_NAME,
                "version": APP_VERSION,
                "generated": datetime.now().isoformat(timespec="seconds"),
                "tracks_total": len(tracks),
                "tracks_with_fingerprints": indexed,
                "possible_pairs": total_possible,
                "token_pairs_seen": len(pair_counts),
                "candidate_pairs": total_candidates,
                "prefilter_rejected_pairs": prefilter_rejected,
                "candidate_token_buckets": total_buckets,
                "candidate_routes": dict(sorted(route_counts.items())),
                "thresholds": {
                    "candidate_prefilter": {
                        "strong_shared_token_min": FP_CANDIDATE_STRONG_SHARED_TOKENS,
                        "weak_shared_token_min": FP_CANDIDATE_WEAK_SHARED_TOKENS,
                        "weak_duration_seconds": FP_CANDIDATE_DURATION_SECONDS,
                        "weak_duration_ratio": FP_CANDIDATE_DURATION_RATIO,
                        "fallbacks": [
                            "same_mbid",
                            "same_isrc",
                            "same_base_title+duration",
                        ],
                    },
                    "strict": {
                        "score_max": FP_AUTO_SCORE,
                        "good_fraction_min": FP_AUTO_GOOD_FRACTION,
                        "excellent_fraction_min": FP_AUTO_EXCELLENT_FRACTION,
                        "median_max": FP_AUTO_MEDIAN_MAX,
                        "p90_max": FP_AUTO_P90_MAX,
                        "overlap_min": FP_MIN_OVERLAP,
                    },
                    "mastering": {
                        "score_max": FP_MASTERING_SCORE,
                        "good_fraction_min": FP_MASTERING_GOOD_FRACTION,
                        "median_max": FP_MASTERING_MEDIAN_MAX,
                        "p90_max": FP_MASTERING_P90_MAX,
                        "overlap_min": FP_MASTERING_MIN_OVERLAP,
                        "duration_delta_seconds_max": FP_MASTERING_MAX_DURATION_SECONDS,
                        "duration_delta_ratio_max": FP_MASTERING_MAX_DURATION_RATIO,
                    },
                    "length_gate": {
                        "length_ratio_min": 0.94,
                        "duration_delta_seconds_or_ratio": "12.0 seconds or 6% of longer track; unmatched part must be silence",
                    },
                    "metadata_safety_gate": [
                        "different recording MBIDs",
                        "semantic version descriptor conflict",
                        "different featured performers + different ISRCs",
                        "different credited artists + different ISRCs",
                        "different descriptors + different ISRCs",
                        "different ISRCs + different base titles",
                    ],
                },
            }
            log_handle.write(json.dumps(header, ensure_ascii=False) + "\n")
        except Exception as exc:
            notes.append(f"Comparison log unavailable: {exc}")
            log_handle = None

    def log_comparison(
        pair_index: int,
        a: int,
        b: int,
        audio_matched: bool,
        final_matched: bool,
        sim,
        metadata_conflict: str,
    ) -> None:
        pair = (a, b)
        processed_pairs.add(pair)
        if log_handle is None:
            return

        first = tracks[a]
        second = tracks[b]
        shared_tokens = int(pair_counts.get(pair, 0))
        duration_delta = abs(first.duration - second.duration)
        duration_limit = max(
            FP_CANDIDATE_DURATION_SECONDS,
            FP_CANDIDATE_DURATION_RATIO * max(first.duration, second.duration),
        )
        reasons = sorted(candidate_reasons.get(pair, set()))

        details = _comparison_decision_details(
            first.fingerprint,
            first.duration,
            second.fingerprint,
            second.duration,
            audio_matched,
            sim,
        )
        details["audio_match"] = bool(audio_matched)
        details["metadata_conflict"] = metadata_conflict
        details["final_match"] = bool(final_matched)

        if final_matched:
            log_counts["matched"] += 1
            for route in details.get("accepted_by", []):
                log_counts[f"matched_{route}"] += 1
        elif audio_matched and metadata_conflict:
            log_counts["rejected_metadata_conflict"] += 1
        else:
            log_counts["rejected_audio"] += 1
            if not details.get("similarity_available"):
                log_counts["rejected_no_similarity"] += 1
            elif details.get("strict_pass") or details.get("mastering_pass"):
                log_counts["rejected_length_gate"] += 1
            else:
                log_counts["rejected_thresholds"] += 1

        row = {
            "record_type": "comparison",
            "pair_index": pair_index,
            "pair_total": total_candidates,
            "candidate": {
                "reasons": reasons,
                "shared_token_buckets": shared_tokens,
                "duration_delta_seconds": round(duration_delta, 6),
                "duration_candidate_limit_seconds": round(duration_limit, 6),
            },
            "track_a": _comparison_track_log_data(first),
            "track_b": _comparison_track_log_data(second),
            "audio_decision": "MATCH" if audio_matched else "REJECT",
            "metadata_safety": {
                "blocked": bool(metadata_conflict),
                "reason": metadata_conflict,
            },
            "decision": "MATCH" if final_matched else "REJECT",
            "details": details,
        }
        try:
            log_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            notes.append(f"Comparison log write error: {exc}")

    def handle_result(done: int, a: int, b: int, audio_matched: bool, sim) -> None:
        metadata_conflict = _metadata_match_conflict(tracks[a], tracks[b]) if audio_matched else ""
        final_matched = bool(audio_matched and not metadata_conflict)
        log_comparison(done, a, b, audio_matched, final_matched, sim, metadata_conflict)

        if final_matched:
            uf.union(a, b)
            if sim:
                score, good, overlap, shift, excellent, median, p90 = sim
                notes.append(
                    f"AUDIO MATCH: {tracks[a].path.name} <-> {tracks[b].path.name}; "
                    f"score={score:.2f}, good={good:.0%}, excellent={excellent:.0%}, "
                    f"overlap={overlap:.0%}, median={median:.1f}, p90={p90}, shift={shift}"
                )
        elif audio_matched and metadata_conflict:
            if sim:
                score, good, overlap, shift, excellent, median, p90 = sim
                notes.append(
                    f"AUDIO MATCH BLOCKED: {tracks[a].path.name} <-> {tracks[b].path.name}; "
                    f"reason={metadata_conflict}; score={score:.2f}, good={good:.0%}, "
                    f"excellent={excellent:.0%}, overlap={overlap:.0%}, "
                    f"median={median:.1f}, p90={p90}, shift={shift}"
                )

    if total_candidates:
        workers = min(total_candidates, _compare_workers())
        label = f"Comparing audio ({workers} workers)..."
        if progress_cb:
            progress_cb(label, 0, total_candidates)

        track_data = [(t.fingerprint, t.duration) for t in tracks]
        chunksize = max(1, total_candidates // max(1, workers * 8))

        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_compare_worker,
                initargs=(track_data,),
            ) as ex:
                results = ex.map(_compare_pair_worker, candidate_pairs, chunksize=chunksize)
                for done, result in enumerate(results, 1):
                    a, b, audio_matched, sim = result
                    handle_result(done, a, b, audio_matched, sim)
                    if progress_cb and (done % 25 == 0 or done == total_candidates):
                        progress_cb(label, done, total_candidates)
        except Exception as e:
            notes.append(f"Parallel comparison unavailable; serial fallback: {e}")
            label = "Comparing audio (serial fallback)..."
            for done, (a, b) in enumerate(candidate_pairs, 1):
                if (a, b) in processed_pairs:
                    continue
                audio_matched, sim = fingerprint_auto_match(tracks[a], tracks[b])
                handle_result(done, a, b, audio_matched, sim)
                if progress_cb and (done % 25 == 0 or done == total_candidates):
                    progress_cb(label, done, total_candidates)
    elif progress_cb:
        progress_cb("Comparing audio...", 1, 1)

    roots: Dict[int, List[int]] = {}
    for i in range(len(tracks)):
        roots.setdefault(uf.find(i), []).append(i)
    remap = {root: gid for gid, root in enumerate(sorted(roots))}
    groups: Dict[int, List[int]] = {}
    for root, ids in roots.items():
        gid = remap[root]
        groups[gid] = ids
        for i in ids:
            tracks[i].group_id = gid

    if log_handle is not None:
        try:
            summary = {
                "record_type": "summary",
                "generated": datetime.now().isoformat(timespec="seconds"),
                "possible_pairs": total_possible,
                "token_pairs_seen": len(pair_counts),
                "candidate_pairs": total_candidates,
                "prefilter_rejected_pairs": prefilter_rejected,
                "comparisons_logged": len(processed_pairs),
                "recording_groups": len(groups),
                "counts": dict(sorted(log_counts.items())),
            }
            log_handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
            log_handle.close()
            notes.append(f"COMPARISON LOG: {comparison_log_path}")
        except Exception as exc:
            notes.append(f"Comparison log finalization error: {exc}")
            try:
                log_handle.close()
            except Exception:
                pass

    return groups, notes

def release_explicit_state(rel: Release) -> str:
    states = {t.explicit for t in rel.tracks}
    if "explicit" in states:
        return "explicit"
    if states == {"clean"}:
        return "clean"
    if "clean" in states and "unknown" not in states:
        return "clean"
    return "unknown"


def finalize_release_metadata(releases: List[Release]) -> None:
    for rel in releases:
        first_tags = rel.tracks[0].tags if rel.tracks else {}
        album_tag = rel.tracks[0].album if rel.tracks else ""
        if album_tag:
            rel.title = album_tag
        rel.release_type, rel.type_source = infer_release_type(rel.title, rel.path.name, rel.track_count, first_tags)
        rel.family = album_family(rel.title)
        rel.explicit = release_explicit_state(rel)

        # Prefer explicit medium metadata when present, but CUE + rip LOG is
        # authoritative enough to classify an existing lossless rip as CD.
        medium_tag = tag_lookup(first_tags, "media", "medium", "format").strip()
        medium_norm = normalize_title(medium_tag)
        if rel.has_cue and rel.has_rip_log:
            rel.source_medium = "CD"
            rel.source_quality = max(rel.source_quality, 100)
        elif "cd" in medium_norm and "digital" not in medium_norm:
            rel.source_medium = medium_tag or "CD"
            rel.source_quality = max(rel.source_quality, 95)
        elif "digital" in medium_norm or "web" in medium_norm:
            rel.source_medium = medium_tag or "WEB"
            rel.source_quality = min(rel.source_quality, 50) if rel.source_quality else 40
        elif rel.has_audiochecker:
            rel.source_medium = "WEB"
        elif rel.has_cue:
            rel.source_medium = "CD/CUE"
        else:
            rel.source_medium = "WEB/Unknown"


def source_rank(rel: Release) -> int:
    """Rank source medium using structural evidence first.

    A CUE plus any real rip LOG (everything except audiochecker.log) is
    authoritative CD evidence for this project.
    """
    if rel.has_cue and rel.has_rip_log:
        return 3
    medium = normalize_title(rel.source_medium)
    if "cd" in medium and "web" not in medium and "digital" not in medium:
        return 2
    return 1


def score_cd_rip_logs(releases: List[Release], progress_cb, errors: List[str]) -> None:
    """Score EAC/XLD rip logs with hey-bro-check-log.

    Unrecognized logs are kept neutral rather than treated as bad rips. A release
    receives a usable quality key only when every non-AudioChecker .log belonging
    to that release was recognized by the upstream scorer.
    """
    jobs = [(rel, path) for rel in releases for path in rel.rip_log_paths]
    if not jobs:
        return

    score_log = ensure_heybrochecklog()
    progress_cb("Scoring CD rip logs...", 0, len(jobs))

    for index, (rel, path) in enumerate(jobs, 1):
        try:
            result = score_log(path)
            unrecognized = result.get("unrecognized")
            if unrecognized:
                message = str(unrecognized)
                rel.rip_log_unrecognized.append(f"{path.name}: {message}")
                errors.append(f"Rip log unrecognized: {path}: {message}")
            else:
                try:
                    score = int(result.get("score"))
                except (TypeError, ValueError):
                    raise RuntimeError("log checker returned no numeric score")
                rel.rip_log_scores.append(score)
                rel.rip_log_rippers.append(str(result.get("ripper") or ""))
                if bool(result.get("flagged")):
                    rel.rip_log_flagged += 1
        except Exception as exc:
            message = str(exc)
            rel.rip_log_unrecognized.append(f"{path.name}: {message}")
            errors.append(f"Rip log scoring error: {path}: {message}")

        progress_cb("Scoring CD rip logs...", index, len(jobs))


def cd_rip_log_quality_key(rel: Release) -> Optional[Tuple[int, float, int]]:
    """Comparable hey-bro-check-log quality for a fully scored CD rip.

    Higher is better. The worst disc score comes first so one bad disc cannot be
    hidden by several perfect discs; average score breaks ties, then an unflagged
    set wins over an otherwise equal flagged one.
    """
    if source_rank(rel) < 2:
        return None
    if not rel.rip_log_paths:
        return None
    if len(rel.rip_log_scores) != len(rel.rip_log_paths):
        return None

    scores = rel.rip_log_scores
    return (
        min(scores),
        sum(scores) / len(scores),
        -rel.rip_log_flagged,
    )


def cd_rip_log_quality_text(rel: Release) -> str:
    key = cd_rip_log_quality_key(rel)
    if key is None:
        if rel.rip_log_paths:
            return f"unavailable ({len(rel.rip_log_scores)}/{len(rel.rip_log_paths)} log(s) recognized)"
        return "not available"
    minimum, average, _flagged = key
    flagged = f", flagged: {rel.rip_log_flagged}" if rel.rip_log_flagged else ""
    rippers = sorted({r for r in rel.rip_log_rippers if r})
    ripper_text = f", {'/'.join(rippers)}" if rippers else ""
    return (
        f"min {minimum}/100, avg {average:.1f}/100 "
        f"({len(rel.rip_log_scores)}/{len(rel.rip_log_paths)} log(s){ripper_text}{flagged})"
    )


def _same_release_exact_cd_content(a: Release, b: Release) -> bool:
    """Strict identity gate for comparing CD rip log quality.

    The log score never proves duplicates. Audio groups, order, release identity,
    type, source class, and track counts must already prove the two releases are
    otherwise interchangeable.
    """
    if source_rank(a) < 2 or source_rank(b) < 2:
        return False
    if source_rank(a) != source_rank(b):
        return False
    if a.release_type != b.release_type:
        return False
    if a.included_track_count != b.included_track_count:
        return False

    if a.release_type == "album":
        if not a.family or not b.family or a.family != b.family:
            return False
    elif normalize_title(a.title) != normalize_title(b.title):
        return False

    seq_a = _included_group_sequence(a)
    seq_b = _included_group_sequence(b)
    if not seq_a or seq_a != seq_b:
        return False
    return _included_group_counter(a) == _included_group_counter(b)


def prefer_better_cd_rip_logs(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Final CD-quality pass: among exact-equivalent rips, keep the better log score."""
    selected = set(selected)

    changed = True
    while changed:
        changed = False
        for current in [r for r in releases if r.rid in selected]:
            current_quality = cd_rip_log_quality_key(current)
            if current_quality is None:
                continue

            better = [
                candidate for candidate in releases
                if candidate.rid != current.rid
                and _same_release_exact_cd_content(current, candidate)
                and cd_rip_log_quality_key(candidate) is not None
                and cd_rip_log_quality_key(candidate) > current_quality
            ]
            if not better:
                continue

            best = max(
                better,
                key=lambda r: (
                    cd_rip_log_quality_key(r),
                    1 if r.root_kind == "existing" else 0,
                    -r.rid,
                ),
            )
            selected.discard(current.rid)
            selected.add(best.rid)
            changed = True
            break

    # Defensive cleanup if two exact-equivalent scored CD rips survived.
    selected_rels = [r for r in releases if r.rid in selected]
    for a, b in itertools.combinations(selected_rels, 2):
        if not _same_release_exact_cd_content(a, b):
            continue
        qa = cd_rip_log_quality_key(a)
        qb = cd_rip_log_quality_key(b)
        if qa is None or qb is None or qa == qb:
            continue
        if qa > qb:
            selected.discard(b.rid)
        else:
            selected.discard(a.rid)

    return selected


def quality_key(rel: Release) -> Tuple[int, int, int]:
    # Advisory state stays neutral here; explicit wins only at the absolute
    # final stage when two releases are proven otherwise identical.
    explicit_score = 1
    existing_score = 1 if rel.root_kind == "existing" else 0
    return source_rank(rel), explicit_score, existing_score


def greedy_cover(target: Set[int], releases: List[Release], selected: Set[int]) -> Tuple[Set[int], Set[int]]:
    covered: Set[int] = set()
    for r in releases:
        if r.rid in selected:
            covered |= r.groups
    missing = set(target) - covered
    chosen: Set[int] = set()
    while missing:
        best = None
        best_key = None
        for r in releases:
            if r.rid in selected or r.rid in chosen or not r.groups:
                continue
            new = r.groups & missing
            if not new:
                continue
            # Tracks skipped by the active options do not participate in coverage/cost. Among otherwise
            # equivalent coverage, explicit and better source medium win.
            cost_per = max(1, r.included_track_count) / len(new)
            q, ex, existing = quality_key(r)
            key = (cost_per, -len(new), -ex, -q, 0 if r.root_kind == "existing" else 1, r.included_track_count, r.title.lower())
            if best_key is None or key < best_key:
                best_key = key
                best = r
        if best is None:
            break
        chosen.add(best.rid)
        missing -= best.groups
    return chosen, missing


def _explicit_rank(rel: Release) -> int:
    """Keep advisory state neutral during normal optimization.

    Explicit preference is intentionally applied only by the final
    exact-equivalent clean/explicit release pass.
    """
    return 1


def _core_album_preference(combo: Tuple[Release, ...], core_groups: Set[int]) -> Tuple[int, int, int]:
    """Score equivalent album core content without rewarding duplicates.

    Priority for equivalent included content: source medium, then an
    already-processed existing release. Advisory state is deferred to the final exact-equivalence pass.
    """
    explicit_total = 0
    source_total = 0
    existing_total = 0
    if core_groups:
        for gid in core_groups:
            carriers = [r for r in combo if gid in r.groups]
            if not carriers:
                continue
            best = max(carriers, key=lambda r: (_explicit_rank(r), source_rank(r), 1 if r.root_kind == "existing" else 0))
            explicit_total += _explicit_rank(best)
            source_total += source_rank(best)
            existing_total += 1 if best.root_kind == "existing" else 0
    else:
        best = max(combo, key=lambda r: (_explicit_rank(r), source_rank(r), 1 if r.root_kind == "existing" else 0))
        explicit_total = _explicit_rank(best)
        source_total = source_rank(best)
        existing_total = 1 if best.root_kind == "existing" else 0
    return explicit_total, source_total, existing_total


def _included_group_sequence(rel: Release) -> List[int]:
    return [
        t.group_id for t in rel.tracks
        if t.group_id >= 0 and not t.exclude_from_coverage
    ]


def _lcs_length(a: List[int], b: List[int]) -> int:
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(a) + 1)
    for value_b in b:
        cur = [0]
        for j, value_a in enumerate(a, 1):
            if value_a == value_b:
                cur.append(prev[j - 1] + 1)
            else:
                cur.append(max(cur[-1], prev[j]))
        prev = cur
    return prev[-1]


def _album_editions_related_by_audio(a: Release, b: Release) -> bool:
    """Detect alternate editions from included audio overlap/order, not names."""
    if a.release_type != "album" or b.release_type != "album":
        return False
    seq_a = _included_group_sequence(a)
    seq_b = _included_group_sequence(b)
    if not seq_a or not seq_b:
        return False

    unique_a = set(seq_a)
    unique_b = set(seq_b)
    common = len(unique_a & unique_b)
    smaller_unique = min(len(unique_a), len(unique_b))
    if smaller_unique < 5:
        return False
    if common < max(5, int(smaller_unique * 0.70)):
        return False

    lcs = _lcs_length(seq_a, seq_b)
    smaller_sequence = min(len(seq_a), len(seq_b))
    return lcs >= max(5, int(smaller_sequence * 0.65))


def _album_clusters(albums: List[Release]) -> List[List[Release]]:
    if not albums:
        return []
    uf = UnionFind(len(albums))
    for i, j in itertools.combinations(range(len(albums)), 2):
        if _album_editions_related_by_audio(albums[i], albums[j]):
            uf.union(i, j)
    grouped: Dict[int, List[Release]] = defaultdict(list)
    for i, rel in enumerate(albums):
        grouped[uf.find(i)].append(rel)
    return [grouped[k] for k in sorted(grouped)]


def choose_album_families(releases: List[Release]) -> Set[int]:
    selected: Set[int] = set()
    albums = [r for r in releases if r.release_type == "album" and not r.excluded_only]
    clusters = _album_clusters(albums)

    by_id = {r.rid: r for r in releases}
    for candidates in clusters:
        if any(r.rid in selected for r in candidates):
            continue
        candidate_ids = {r.rid for r in candidates}
        universe: Set[int] = set().union(*(r.groups for r in candidates)) if candidates else set()
        core_groups: Set[int] = set(candidates[0].groups) if candidates else set()
        for r in candidates[1:]:
            core_groups &= r.groups

        best_selection: Optional[Set[int]] = None
        best_score = None
        max_combo = len(candidates) if len(candidates) <= 10 else 2
        combos: Iterable[Tuple[Release, ...]] = itertools.chain.from_iterable(
            itertools.combinations(candidates, n)
            for n in range(1, max_combo + 1)
        )

        for combo in combos:
            base = set(selected) | {r.rid for r in combo}
            covered = set().union(*(by_id[x].groups for x in base)) if base else set()
            missing = universe - covered
            # Bonus tracks may be covered more efficiently by singles/EPs or
            # another album outside this audio-derived edition cluster.
            ext_pool = [r for r in releases if r.rid not in candidate_ids]
            extra, remain = greedy_cover(missing, ext_pool, base)
            if remain:
                continue
            new_ids = ({r.rid for r in combo} | extra) - selected
            new_rels = [by_id[x] for x in new_ids]

            core_explicit, core_source, core_existing = _core_album_preference(combo, core_groups)
            total_included_files = sum(r.included_track_count for r in new_rels)
            total_releases = len(new_rels)
            recycle_count = sum(r.root_kind == "recycle" for r in new_rels)
            score = (
                -core_explicit,
                -core_source,
                -core_existing,
                total_included_files,
                total_releases,
                recycle_count,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_selection = set(new_ids)

        if best_selection is None:
            chosen = min(
                candidates,
                key=lambda r: (
                    -_explicit_rank(r),
                    -source_rank(r),
                    0 if r.root_kind == "existing" else 1,
                    r.included_track_count,
                ),
            )
            selected.add(chosen.rid)
        else:
            selected |= best_selection
    return selected


def _included_group_counter(rel: Release) -> Counter:
    return Counter(t.group_id for t in rel.tracks if t.group_id >= 0 and not t.exclude_from_coverage)


def _release_track_match(a: Track, b: Track) -> bool:
    if a.exclude_from_coverage or b.exclude_from_coverage:
        return False
    return a.group_id >= 0 and a.group_id == b.group_id


def _release_covers(covering: Release, target: Release) -> bool:
    """Audio-only included-content coverage using fingerprint groups."""
    need = _included_group_counter(target)
    have = _included_group_counter(covering)
    return all(have[gid] >= count for gid, count in need.items())


def _release_barcodes(rel: Release) -> Set[str]:
    values: Set[str] = set()
    if rel.tracks:
        tag = tag_lookup(rel.tracks[0].tags, "barcode", "upc", "ean")
        digits = re.sub(r"\D", "", tag)
        if 8 <= len(digits) <= 14:
            values.add(digits)
    return values


def _related_album_releases(a: Release, b: Release) -> bool:
    """Album-edition relation from audio overlap/order, with barcode fallback."""
    if a.release_type != "album" or b.release_type != "album":
        return False
    if _album_editions_related_by_audio(a, b):
        return True
    return bool(_release_barcodes(a) & _release_barcodes(b))


def _structural_track_match(a: Track, b: Track) -> bool:
    # Compatibility wrapper retained for older internal callers: audio groups only.
    return _release_track_match(a, b)


def _structural_release_covers(covering: Release, target: Release) -> bool:
    # Compatibility wrapper retained for older internal callers: audio coverage only.
    return _release_covers(covering, target)


def _content_preference(rel: Release) -> Tuple[int, int, int]:
    """Preference after included content equivalence has already been established."""
    return (_explicit_rank(rel), source_rank(rel), 1 if rel.root_kind == "existing" else 0)


def _folder_related_releases(a: Release, b: Release) -> bool:
    # Historical name kept for compatibility. Folder names are not used.
    if a.release_type == "album" and b.release_type == "album":
        return _related_album_releases(a, b)
    return True

def enforce_existing_precedence(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Hard final safeguard for existing-vs-recycle duplicates.

    If an existing release structurally covers a recycle release and is not worse
    on source quality, the existing processed release must win. This is
    deliberately independent of embedded album tags, inferred release type and
    fingerprint grouping.
    """
    selected = set(selected)
    existing_rels = [r for r in releases if r.root_kind == "existing" and not r.excluded_only]
    recycle_rels = [r for r in releases if r.root_kind == "recycle" and not r.excluded_only]

    for er in existing_rels:
        for rr in recycle_rels:
            if not _folder_related_releases(er, rr):
                continue

            er_covers_rr = _release_covers(er, rr)
            if not er_covers_rr:
                continue

            rr_covers_er = _release_covers(rr, er)
            er_quality = (_explicit_rank(er), source_rank(er), 1)
            rr_quality = (_explicit_rank(rr), source_rank(rr), 0)

            if rr_covers_er:
                # Same included content: source decides; existing wins ties here. Advisory preference is deferred.
                if er_quality >= rr_quality:
                    selected.add(er.rid)
                    selected.discard(rr.rid)
                else:
                    selected.add(rr.rid)
                    selected.discard(er.rid)
            else:
                # Existing is a included-content superset. If its source is not worse,
                # the recycle subset can never be the better choice.
                if (_explicit_rank(er), source_rank(er)) >= (_explicit_rank(rr), source_rank(rr)):
                    selected.add(er.rid)
                    selected.discard(rr.rid)

    return selected


def stabilize_equivalent_sources(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Enforce source/current precedence for equivalent album content.

    This pass is deliberately release-level so a borderline fingerprint merge
    cannot make a WEB duplicate replace an existing CD or an already-processed
    existing WEB copy.
    """
    selected = set(selected)
    by_id = {r.rid: r for r in releases}

    changed = True
    while changed:
        changed = False

        # Selected recycle release vs unselected existing equivalent/superset:
        # existing wins when source is better, or when source ties.
        for rr in [r for r in releases if r.root_kind == "recycle" and r.rid in selected]:
            candidates = [
                e for e in releases
                if e.root_kind == "existing" and e.rid not in selected
                and _related_album_releases(e, rr)
                and _release_covers(e, rr)
                and (_explicit_rank(e), source_rank(e)) >= (_explicit_rank(rr), source_rank(rr))
            ]
            if candidates:
                best = max(candidates, key=lambda e: (_explicit_rank(e), source_rank(e), e.included_track_count))
                selected.discard(rr.rid)
                selected.add(best.rid)
                changed = True
                break
        if changed:
            continue

        # The reverse is allowed only when recycle is objectively better on
        # source quality and covers the existing release's included content.
        for er in [r for r in releases if r.root_kind == "existing" and r.rid in selected]:
            candidates = [
                r for r in releases
                if r.root_kind == "recycle" and r.rid not in selected
                and _related_album_releases(er, r)
                and _release_covers(r, er)
                and (_explicit_rank(r), source_rank(r)) > (_explicit_rank(er), source_rank(er))
            ]
            if candidates:
                best = max(candidates, key=lambda r: (_explicit_rank(r), source_rank(r), r.included_track_count))
                selected.discard(er.rid)
                selected.add(best.rid)
                changed = True
                break

    return selected


def _semantically_covered_by_selected(rel: Release, selected_rels: List[Release]) -> bool:
    # Historical name kept for compatibility. Coverage is audio-only.
    if not rel.groups and not rel.included_track_count:
        return True
    return any(_release_covers(r, rel) for r in selected_rels)

def find_dominated_releases(releases: List[Release]) -> Set[int]:
    """Remove only pairwise-equivalent album duplicates before global optimization.

    A strict superset is NOT allowed to eliminate a smaller edition here. Its
    extra recording groups may already be supplied by another retained release,
    in which case the smaller edition can lower the collection's total track
    count. Superset/subset decisions therefore remain collection-wide.
    """
    dominated: Set[int] = set()
    albums = [r for r in releases if r.release_type == "album" and not r.excluded_only]

    for a, b in itertools.combinations(albums, 2):
        if not _related_album_releases(a, b):
            continue

        a_covers_b = _release_covers(a, b)
        b_covers_a = _release_covers(b, a)

        # Only exact coverage equivalence is safe to collapse pairwise.
        if not (a_covers_b and b_covers_a):
            continue

        a_pref = (_explicit_rank(a), source_rank(a), 1 if a.root_kind == "existing" else 0, -a.rid)
        b_pref = (_explicit_rank(b), source_rank(b), 1 if b.root_kind == "existing" else 0, -b.rid)
        if a_pref > b_pref:
            dominated.add(b.rid)
        elif b_pref > a_pref:
            dominated.add(a.rid)

    return dominated

def _selected_album_cluster_map(releases: List[Release]) -> Dict[int, int]:
    albums = [r for r in releases if r.release_type == "album" and not r.excluded_only]
    result: Dict[int, int] = {}
    for cluster_id, cluster in enumerate(_album_clusters(albums)):
        for rel in cluster:
            result[rel.rid] = cluster_id
    return result


def minimize_collection_track_count(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Reduce total included track count using whole-collection coverage.

    This pass fixes the classic "larger deluxe edition wins because it has one
    extra track" problem when that extra recording is already supplied by some
    other retained release. A swap is allowed only when:
      - the replacement is a related edition of the same album cluster;
      - its source class is not worse;
      - every included recording group in the entire collection remains covered;
      - total included track count strictly decreases.

    Existing-vs-recycle, CD-log and clean/explicit rules still apply afterward.
    """
    selected = set(selected)
    by_id = {r.rid: r for r in releases}
    required_groups: Set[int] = set()
    for rel in releases:
        if not rel.excluded_only:
            required_groups |= rel.groups

    def covered(ids: Set[int]) -> Set[int]:
        result: Set[int] = set()
        for rid in ids:
            result |= by_id[rid].groups
        return result

    changed = True
    while changed:
        changed = False
        best_swap = None
        best_key = None

        selected_albums = [
            by_id[rid] for rid in selected
            if by_id[rid].release_type == "album" and not by_id[rid].excluded_only
        ]
        unselected_albums = [
            r for r in releases
            if r.rid not in selected
            and r.release_type == "album"
            and not r.excluded_only
        ]

        for current in selected_albums:
            for candidate in unselected_albums:
                if not _related_album_releases(current, candidate):
                    continue
                if source_rank(candidate) < source_rank(current):
                    continue
                if candidate.included_track_count >= current.included_track_count:
                    continue

                trial = (selected - {current.rid}) | {candidate.rid}
                if not required_groups <= covered(trial):
                    continue

                saved_tracks = current.included_track_count - candidate.included_track_count
                key = (
                    -saved_tracks,
                    -source_rank(candidate),
                    0 if candidate.root_kind == "existing" else 1,
                    candidate.included_track_count,
                    candidate.rid,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_swap = (current, candidate)

        if best_swap is not None:
            current, candidate = best_swap
            selected.discard(current.rid)
            selected.add(candidate.rid)
            changed = True

    return selected


def prune_redundant_selected(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Final exact redundancy pass after the optimizer."""
    selected = set(selected)
    by_id = {r.rid: r for r in releases}
    album_cluster = _selected_album_cluster_map(releases)

    changed = True
    while changed:
        changed = False
        ordered = sorted(
            (by_id[rid] for rid in selected),
            key=lambda r: (
                0 if r.release_type != "album" else 1,
                -r.included_track_count,
                r.rid,
            ),
        )

        for rel in ordered:
            others = [by_id[rid] for rid in selected if rid != rel.rid]
            if not others:
                continue

            if rel.release_type == "album":
                cid = album_cluster.get(rel.rid)
                if cid is None:
                    continue
                if not any(
                    other.release_type == "album"
                    and album_cluster.get(other.rid) == cid
                    for other in others
                ):
                    continue

            need = _included_group_counter(rel)
            have = Counter()
            carriers: Dict[int, List[Release]] = defaultdict(list)
            for other in others:
                counter = _included_group_counter(other)
                have.update(counter)
                for gid in counter:
                    carriers[gid].append(other)

            if any(have[gid] < count for gid, count in need.items()):
                continue

            rel_pref = (source_rank(rel), 1 if rel.root_kind == "existing" else 0)
            source_safe = True
            for gid in need:
                if not any(
                    (source_rank(other), 1 if other.root_kind == "existing" else 0) >= rel_pref
                    for other in carriers.get(gid, [])
                ):
                    source_safe = False
                    break
            if not source_safe:
                continue

            selected.remove(rel.rid)
            changed = True
            break

    return selected


def _release_advisory_identity(rel: Release) -> str:
    """Normalize only clean/explicit packaging words for same-release checks."""
    value = ascii_punctuation(rel.title or "")
    value = re.sub(
        r"[\[(]\s*(?:(?:clean|explicit)(?:\s+(?:version|edition))?|"
        r"(?:album|main|original)\s+version\s+(?:clean|explicit))\s*[\])]",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s*(?:-|:)\s*(?:(?:clean|explicit)(?:\s+(?:version|edition))?|"
        r"(?:album|main|original)\s+version\s+(?:clean|explicit))\s*$",
        " ",
        value,
        flags=re.I,
    )
    return compact_title(normalize_space(value))


def _exact_clean_explicit_equivalent(a: Release, b: Release) -> bool:
    """True only when clean/explicit copies are otherwise the same release.

    This is deliberately stricter than normal release coverage. The final
    advisory preference must never replace a genuinely different clean edit,
    bonus-track edition, ordering, source class, or incomplete release.
    """
    if {a.explicit, b.explicit} != {"clean", "explicit"}:
        return False
    if a.release_type != b.release_type:
        return False
    if source_rank(a) != source_rank(b):
        return False
    if a.included_track_count != b.included_track_count:
        return False
    if _release_advisory_identity(a) != _release_advisory_identity(b):
        return False

    seq_a = _included_group_sequence(a)
    seq_b = _included_group_sequence(b)
    if not seq_a or seq_a != seq_b:
        return False

    # Exact multiset equality protects repeated tracks and ensures neither
    # release has extra/missing included audio despite sequence normalization.
    return _included_group_counter(a) == _included_group_counter(b)


def prefer_explicit_exact_equivalents(releases: List[Release], selected: Set[int]) -> Set[int]:
    """Absolute final tie-break: explicit beats clean only for exact equivalents."""
    selected = set(selected)

    # Repeat because a swap can expose another duplicate clean copy.
    changed = True
    while changed:
        changed = False
        selected_clean = [
            r for r in releases
            if r.rid in selected and r.explicit == "clean" and not r.excluded_only
        ]

        for clean in selected_clean:
            explicit_candidates = [
                r for r in releases
                if r.explicit == "explicit"
                and not r.excluded_only
                and _exact_clean_explicit_equivalent(clean, r)
            ]
            if not explicit_candidates:
                continue

            # At this point content and source class are identical by rule.
            # Prefer an already-processed explicit copy if available, then use
            # deterministic path/rid ordering.
            explicit = max(
                explicit_candidates,
                key=lambda r: (
                    1 if r.root_kind == "existing" else 0,
                    -r.rid,
                ),
            )

            selected.discard(clean.rid)
            selected.add(explicit.rid)
            changed = True
            break

    # If both exact copies somehow survived earlier passes, remove the clean one.
    selected_rels = [r for r in releases if r.rid in selected]
    for clean in [r for r in selected_rels if r.explicit == "clean"]:
        if any(
            explicit.rid in selected
            and explicit.explicit == "explicit"
            and _exact_clean_explicit_equivalent(clean, explicit)
            for explicit in releases
        ):
            selected.discard(clean.rid)

    return selected


def optimize_collection(releases: List[Release], groups: Dict[int, List[int]]) -> Set[int]:
    dominated = find_dominated_releases(releases)
    active = [r for r in releases if r.rid not in dominated]

    selected = choose_album_families(active)
    # Only recording groups not excluded by the active checkboxes are included.
    # This is critical: a semantically duplicate recycle copy must not create
    # synthetic "missing" groups just because fingerprint grouping was stricter.
    all_groups: Set[int] = set()
    for rel in active:
        all_groups |= rel.groups
    extra, missing = greedy_cover(all_groups, active, selected)
    selected |= extra
    if missing:
        for gid in sorted(missing):
            containing = [r for r in active if gid in r.groups]
            if containing:
                chosen = min(
                    containing,
                    key=lambda r: (
                        r.included_track_count,
                        -_explicit_rank(r),
                        -source_rank(r),
                        0 if r.root_kind == "existing" else 1,
                    ),
                )
                selected.add(chosen.rid)

    selected = stabilize_equivalent_sources(active, selected)
    selected = enforce_existing_precedence(releases, selected)
    selected = prune_redundant_selected(releases, selected)

    # Now that the whole retained set exists, minimize total included tracks.
    # Bonus tracks on one edition have zero value here if another retained
    # release already supplies those same recording groups.
    selected = minimize_collection_track_count(releases, selected)
    selected = prune_redundant_selected(releases, selected)

    # Quality of a CD rip must never create duplicate identity or override a
    # different edition. Only exact-equivalent CD rips reach this pass.
    selected = prefer_better_cd_rip_logs(releases, selected)

    # Absolute last stage: when clean/explicit releases are otherwise exactly
    # identical, retain explicit and move the clean copy.
    selected = prefer_explicit_exact_equivalents(releases, selected)
    return selected


def review_candidates(tracks: List[Track]) -> List[Tuple[int, int, str]]:
    # v0.4+: no manual track-by-track review. Uncertain matches remain separate
    # recording groups and are therefore retained automatically.
    return []

def format_track(t: Track) -> str:
    dur = "?:??"
    if t.duration > 0:
        m = int(t.duration) // 60
        s = int(round(t.duration)) % 60
        dur = f"{m}:{s:02d}"
    bits = [t.display_title, dur]
    if t.mbid:
        bits.append(f"MBID={t.mbid}")
    if t.isrc:
        bits.append(f"ISRC={t.isrc}")
    if t.explicit != "unknown":
        bits.append(t.explicit)
    return " | ".join(bits)


def prepare_analysis(
    existing: Optional[Path],
    recycle: Path,
    progress_cb,
) -> Tuple[List[Release], List[Track]]:
    """Run the common stages needed before the pattern review."""
    progress_cb("Checking dependencies...", 0, 1)
    bootstrap_winget()
    _ffmpeg, ffprobe = ensure_ffmpeg()
    progress_cb("Checking dependencies...", 1, 1)

    progress_cb("Scanning release folders...", 0, 1)
    releases: List[Release] = []
    if existing is not None:
        releases = discover_releases(existing, "existing", 0)
    releases += discover_releases(recycle, "recycle", len(releases))
    progress_cb("Scanning release folders...", 1, 1)

    tracks = [t for r in releases for t in r.tracks]
    errors: List[str] = []
    probe_workers = min(len(tracks) or 1, _probe_workers())
    progress_cb(f"Reading tags and durations ({probe_workers} workers)...", 0, max(1, len(tracks)))
    with ThreadPoolExecutor(max_workers=probe_workers) as ex:
        futures = {ex.submit(probe_track, ffprobe, t): t for t in tracks}
        done = 0
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                fut.result()
            except Exception as e:
                errors.append(f"Probe error: {t.path}: {e}")
            done += 1
            progress_cb(f"Reading tags and durations ({probe_workers} workers)...", done, len(tracks))

    finalize_release_metadata(releases)
    score_cd_rip_logs(releases, progress_cb, errors)

    # Store probe/log failures on the releases list wrapper is not possible, so the
    # prepared analysis returns them separately through a temporary track tag.
    if errors and tracks:
        tracks[0].tags["__ANALYZER_PREPARE_ERRORS__"] = json.dumps(errors, ensure_ascii=False)
    return releases, tracks


def analyze_prepared(
    releases: List[Release],
    tracks: List[Track],
    use_fingerprint: bool,
    progress_cb,
    exclude_remixes: bool = True,
    exclude_live: bool = True,
    excluded_pattern_keys: Optional[Set[str]] = None,
    comparison_log_path: Optional[Path] = None,
) -> Tuple[List[Release], List[Track], Dict[int, List[int]], Set[int], List[Tuple[int, int, str]], List[str]]:
    errors: List[str] = []
    if tracks:
        packed_errors = tracks[0].tags.pop("__ANALYZER_PREPARE_ERRORS__", "")
        if packed_errors:
            try:
                errors.extend(json.loads(packed_errors))
            except Exception:
                pass

    # Existing global options run first. The pattern review can only exclude
    # additional material; a checked pattern does not override Save Remixes/Live.
    configure_exclusions(releases, exclude_remixes, exclude_live)
    apply_pattern_exclusions(releases, set(excluded_pattern_keys or set()))
    refresh_heuristic_release_types(releases)

    fpcalc = ensure_fpcalc() if use_fingerprint else None
    if use_fingerprint and fpcalc:
        # Fingerprint every audio file. Coverage exclusions still affect only
        # optimization through Release.groups; fingerprints are also needed for
        # safe intra-release duplicate cleanup inside retained releases.
        fingerprint_tracks = list(tracks)
        fp_workers = min(len(fingerprint_tracks) or 1, _fingerprint_workers())
        label = f"Generating Chromaprint fingerprints ({fp_workers} workers)..."
        progress_cb(label, 0, max(1, len(fingerprint_tracks)))
        with ThreadPoolExecutor(max_workers=fp_workers) as ex:
            futures = {ex.submit(chromaprint_fingerprint, fpcalc, t): t for t in fingerprint_tracks}
            done = 0
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    t.fingerprint, t.fingerprint_duration = fut.result()
                except Exception as e:
                    errors.append(f"Fingerprint error: {t.path}: {e}")
                done += 1
                progress_cb(label, done, len(fingerprint_tracks))

    groups, merge_notes = merge_equivalent_tracks(
        tracks,
        progress_cb,
        comparison_log_path,
    )
    errors.extend(merge_notes)
    progress_cb("Optimizing release set...", 0, 1)
    selected = optimize_collection(releases, groups)
    progress_cb("Optimizing release set...", 1, 1)
    reviews = review_candidates(tracks)
    progress_cb("Building automatic action plan...", 1, 1)
    return releases, tracks, groups, selected, reviews, errors


def analyze(
    existing: Optional[Path],
    recycle: Path,
    use_fingerprint: bool,
    progress_cb,
    exclude_remixes: bool = True,
    exclude_live: bool = True,
    excluded_pattern_keys: Optional[Set[str]] = None,
    logging_enabled: bool = False,
) -> Tuple[List[Release], List[Track], Dict[int, List[int]], Set[int], List[Tuple[int, int, str]], List[str]]:
    releases, tracks = prepare_analysis(existing, recycle, progress_cb)
    comparison_log_path = _new_comparison_log_path(recycle) if (use_fingerprint and logging_enabled) else None
    return analyze_prepared(
        releases,
        tracks,
        use_fingerprint,
        progress_cb,
        exclude_remixes,
        exclude_live,
        excluded_pattern_keys,
        comparison_log_path,
    )


def report_text(existing: Optional[Path], recycle: Path, releases: List[Release], tracks: List[Track], groups: Dict[int, List[int]], selected: Set[int], reviews, notes) -> str:
    by_id = {r.rid: r for r in releases}
    existing_groups = set().union(*(r.groups for r in releases if r.root_kind == "existing")) if releases else set()
    selected_groups = set().union(*(r.groups for r in releases if r.rid in selected)) if selected else set()

    lines: List[str] = []
    lines.append("Duplicate / Edition Analyzer")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Existing discography: {existing if existing is not None else 'Not used (Recycle-only mode)'}")
    lines.append(f"Recycle/update: {recycle}")
    lines.append("")
    lines.append("RULE PRIORITY")
    lines.append("1. Preserve ideally every unique recording/version.")
    lines.append("2. Keep every album represented.")
    lines.append("3. Apply Save Remixes / Save Live recordings as absolute selection filters; unchecked material is invisible to comparison/counting.")
    lines.append("4. Prefer CD/physical source over equivalent WEB content.")
    lines.append("5. Among otherwise exact-identical CD rips, prefer the higher hey-bro-check-log EAC/XLD score.")
    lines.append("6. Prefer the existing processed copy when content/source/log quality are equivalent.")
    lines.append("7. Then minimize total included track count across the whole retained collection; edition bonus tracks add no value when already covered elsewhere.")
    lines.append("8. Clean/explicit is neutral during optimization; ITUNESADVISORY 1 beats 0 only as the absolute final tie-break for otherwise exact-equivalent releases.")
    lines.append("9. Uncertain audio matches stay separate and are retained automatically.")
    lines.append("")
    lines.append("SUMMARY")
    lines.append(f"Releases scanned: {len(releases)}")
    lines.append(f"Audio files scanned: {len(tracks)}")
    lines.append(f"High-confidence recording groups: {len(groups)}")
    lines.append(f"Proposed retained releases: {len(selected)}")
    lines.append(f"Proposed retained included audio files: {sum(by_id[x].included_track_count for x in selected)}")
    lines.append(f"Skipped remix/live/pattern files inside retained releases: {sum(by_id[x].ignored_track_count for x in selected)}")
    lines.append("Manual review required: no")
    lines.append("")

    lines.append("PROPOSED RELEASE PLAN")
    lines.append("=====================")
    for rel in sorted(releases, key=lambda r: (r.root_kind, str(r.path).lower())):
        if rel.rid in selected:
            if rel.root_kind == "recycle" and (rel.groups - existing_groups):
                status = "NEW"
                reason = "Selected because it contributes material not already covered by the existing discography and/or is part of the minimum-duplication solution."
            else:
                status = "KEEP"
                reason = "Selected by album-coverage / minimum-file optimization."
        else:
            if rel.groups <= selected_groups:
                status = "REDUNDANT"
                covers = []
                for gid in sorted(rel.groups):
                    candidates = [r for r in releases if r.rid in selected and gid in r.groups]
                    if candidates:
                        best = min(candidates, key=lambda r: (r.included_track_count, -_explicit_rank(r), -r.source_quality, 0 if r.root_kind == "existing" else 1))
                        covers.append(best.path.name)
                unique_covers = []
                for x in covers:
                    if x not in unique_covers:
                        unique_covers.append(x)
                reason = "All high-confidence recording groups are covered by retained releases"
                if unique_covers:
                    reason += ": " + "; ".join(unique_covers[:8])
            else:
                status = "KEEP" if rel.root_kind == "existing" else "NEW"
                reason = "Conservative fallback: content was not proven covered elsewhere, so it is retained."
        if len(rel.source_paths) > 1:
            lines.append(f"[{status}] {rel.path.parent} / {rel.title} [{len(rel.source_paths)} disc folders]")
        else:
            lines.append(f"[{status}] {rel.path}")
        lines.append(f"  Type: {rel.release_type} ({rel.type_source}); family: {rel.family or '?'}")
        src = "CD+LOG+CUE" if rel.has_cue and rel.has_rip_log else "CUE" if rel.has_cue else "WEB/AudioChecker" if rel.has_audiochecker else "WEB/unknown"
        lines.append(
            f"  Source: {src}; included tracks: {rel.included_track_count}; "
            f"skipped by options: {rel.ignored_track_count}; physical tracks: {rel.track_count}"
        )
        if rel.rip_log_paths:
            lines.append(f"  CD rip log quality: {cd_rip_log_quality_text(rel)}")
        lines.append(f"  Reason: {reason}")
        lines.append("")

    lines.append("AUTOMATIC MATCHING POLICY")
    lines.append("=========================")
    lines.append("Strong audio matches are grouped automatically. Uncertain matches remain separate and are retained automatically; no track-by-track user review is required.")
    lines.append("")

    lines.append("HIGH-CONFIDENCE DUPLICATE GROUPS")
    lines.append("================================")
    dup_count = 0
    for gid, ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        dup_count += 1
        lines.append(f"Group {gid + 1}:")
        for i in ids:
            t = tracks[i]
            r = by_id[t.release_id]
            mark = "KEEP" if r.rid in selected else "DROP-CANDIDATE"
            lines.append(f"  [{mark}] {r.path.name} -> {format_track(t)}")
        lines.append("")
    if dup_count == 0:
        lines.append("None.")
        lines.append("")

    if notes:
        lines.append("SCAN NOTES / ERRORS")
        lines.append("===================")
        for n in notes:
            lines.append(n)
        lines.append("")

    lines.append("IMPORTANT")
    lines.append("This is a proposal only. No files were changed, moved, or deleted.")
    lines.append("Chromaprint fingerprint similarity plus duration is the duplicate-identity signal. Titles, filenames, MBIDs and ISRCs are not used to prove duplicates.")
    lines.append("Filename/title similarity does not participate in duplicate identity.")
    return "\n".join(lines) + "\n"



@dataclass
class ReleaseDecision:
    release_id: int
    action: str
    reason: str
    essential_tracks: List[str] = field(default_factory=list)


def _selected_group_union(releases: List[Release], selected: Set[int], exclude: Optional[int] = None) -> Set[int]:
    out: Set[int] = set()
    for r in releases:
        if r.rid in selected and r.rid != exclude:
            out |= r.groups
    return out


def _preferred_existing_cover_for_recycle(rel: Release, releases: List[Release]) -> Optional[Release]:
    """Return an existing release that makes this recycle release redundant.

    This is a final action-layer safeguard based on audio fingerprint coverage
    and source preference. Folder/file names are not duplicate evidence.
    """
    if rel.root_kind != "recycle" or rel.excluded_only:
        return None
    candidates: List[Release] = []
    for er in releases:
        if er.root_kind != "existing" or er.excluded_only:
            continue
        if er.release_type == "album" and rel.release_type == "album" and not _related_album_releases(er, rel):
            continue
        if not _release_covers(er, rel):
            continue
        if (_explicit_rank(er), source_rank(er)) < (_explicit_rank(rel), source_rank(rel)):
            continue

        # When the two are strict exact-equivalent CD rips and both logs were
        # fully scored, a better recycle rip is allowed to replace an older
        # existing copy.
        if _same_release_exact_cd_content(er, rel):
            er_log = cd_rip_log_quality_key(er)
            rel_log = cd_rip_log_quality_key(rel)
            if er_log is not None and rel_log is not None and rel_log > er_log:
                continue

        candidates.append(er)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda er: (
            _explicit_rank(er),
            source_rank(er),
            er.included_track_count,
        ),
    )


def build_release_decisions(releases: List[Release], tracks: List[Track], selected: Set[int], reviews) -> List[ReleaseDecision]:
    selected_rels = [r for r in releases if r.rid in selected]
    selected_groups = _selected_group_union(releases, selected)

    existing_groups: Set[int] = set()
    for r in releases:
        if r.root_kind == "existing":
            existing_groups |= r.groups

    decisions: List[ReleaseDecision] = []

    for rel in releases:
        # A release containing only tracks excluded by the active checkboxes is
        # automatically removed/skipped. Mixed releases can still be retained for
        # unique included audio.
        if rel.excluded_only:
            action = "REMOVE" if rel.root_kind == "existing" else "SKIP"
            kinds = []
            if any(t.is_remix for t in rel.tracks):
                kinds.append("remix")
            if any(t.is_live for t in rel.tracks):
                kinds.append("live")
            pattern_keys = sorted({t.excluded_by_pattern for t in rel.tracks if t.excluded_by_pattern})
            if pattern_keys:
                kinds.append("pattern: " + "; ".join(pattern_keys))
            label = "/".join(kinds) if kinds else "excluded"
            decisions.append(
                ReleaseDecision(
                    release_id=rel.rid,
                    action=action,
                    reason=f"Excluded-only release ({label}) by current checkbox settings.",
                    essential_tracks=[],
                )
            )
            continue

        # Final hard safeguard: if an existing processed release covers this
        # recycle copy by audio groups and is not worse in source quality, it wins.
        existing_cover = _preferred_existing_cover_for_recycle(rel, releases)
        if existing_cover is not None:
            decisions.append(
                ReleaseDecision(
                    release_id=rel.rid,
                    action="SKIP",
                    reason=f"Covered by preferred existing release: {existing_cover.path.name}",
                    essential_tracks=[],
                )
            )
            continue

        other_selected_groups = _selected_group_union(releases, selected, exclude=rel.rid)
        essential_groups = rel.groups - other_selected_groups if rel.rid in selected else set()

        essential_tracks: List[str] = []
        seen_titles: Set[str] = set()
        for t in rel.tracks:
            if t.group_id in essential_groups:
                key = normalize_title(t.display_title)
                if key not in seen_titles:
                    seen_titles.add(key)
                    essential_tracks.append(t.display_title)

        if rel.rid in selected:
            if rel.root_kind == "existing":
                action = "KEEP"
                if essential_tracks:
                    reason = f"Keep: {len(essential_tracks)} recording(s) are not covered by any other retained release."
                elif rel.release_type == "album":
                    reason = "Keep: required album representation in the minimum-file solution."
                else:
                    reason = "Keep: selected by the automatic minimum-file coverage solution."
            else:
                replaced = [
                    r for r in releases
                    if r.root_kind == "existing"
                    and r.rid not in selected
                    and r.groups
                    and r.groups <= rel.groups
                    and (
                        (r.release_type == "album" and rel.release_type == "album" and r.family == rel.family)
                        or normalize_title(r.title) == normalize_title(rel.title)
                    )
                ]
                new_groups = rel.groups - existing_groups
                if replaced:
                    action = "REPLACE"
                    reason = "Use this recycle release instead of: " + "; ".join(r.path.name for r in replaced[:4])
                else:
                    action = "ADD"
                    if new_groups:
                        new_titles: List[str] = []
                        seen: Set[str] = set()
                        for t in rel.tracks:
                            if t.group_id in new_groups:
                                k = normalize_title(t.display_title)
                                if k not in seen:
                                    seen.add(k)
                                    new_titles.append(t.display_title)
                        preview = ", ".join(new_titles[:4])
                        if len(new_titles) > 4:
                            preview += f", +{len(new_titles)-4} more"
                        reason = f"Add: {len(new_groups)} recording(s) are not present in the current discography"
                        if preview:
                            reason += f": {preview}"
                    elif rel.release_type == "album":
                        reason = "Add: chosen album edition minimizes duplicated files while keeping the album represented."
                    else:
                        reason = "Add: selected by the automatic minimum-file coverage solution."
        else:
            covered = (bool(rel.groups) and rel.groups <= selected_groups) or (not rel.groups and rel.excluded_only)
            if covered:
                action = "REMOVE" if rel.root_kind == "existing" else "SKIP"
                covers: List[str] = []
                for gid in sorted(rel.groups):
                    candidates = [r for r in selected_rels if gid in r.groups]
                    if candidates:
                        best = min(
                            candidates,
                            key=lambda r: (r.included_track_count, -_explicit_rank(r), -source_rank(r), 0 if r.root_kind == "existing" else 1),
                        )
                        if best.path.name not in covers:
                            covers.append(best.path.name)
                reason = "All recordings are covered by retained releases."
                if covers:
                    reason += " Covered by: " + "; ".join(covers[:5])
            else:
                # Defensive fail-safe. If the optimizer ever produces a non-selected
                # release with uncovered groups, do not ask the user to investigate;
                # retain it automatically so unique material cannot be lost.
                action = "KEEP" if rel.root_kind == "existing" else "ADD"
                reason = "Conservative fallback: contains material not proven covered elsewhere, so it is retained automatically."

        decisions.append(
            ReleaseDecision(
                release_id=rel.rid,
                action=action,
                reason=reason,
                essential_tracks=essential_tracks,
            )
        )

    return decisions

def action_summary(decisions: List[ReleaseDecision]) -> Dict[str, int]:
    counts = Counter(d.action for d in decisions)
    return {k: counts.get(k, 0) for k in ("ADD", "REPLACE", "REMOVE", "SKIP", "KEEP")}



LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape", "tta", "tak"}
LOSSLESS_EXTS = {".flac", ".wav", ".ape", ".wv"}


@dataclass
class IntraReleaseDuplicate:
    release_id: int
    redundant: Path
    keep: Path
    reason: str


def _track_number_key(track: Track) -> Optional[int]:
    raw = tag_lookup(track.tags, "tracknumber", "track", "trackno")
    match = re.search(r"\d+", raw or "")
    if match:
        return int(match.group())
    match = re.match(r"^\s*(\d{1,3})(?:\s*[-._)]\s*|\s+)", track.path.name)
    return int(match.group(1)) if match else None


def _track_codec_quality(track: Track) -> Tuple[int, int, int, int, int, int, int]:
    codec = (track.codec_name or "").lower()
    ext = track.path.suffix.lower()
    lossless = codec in LOSSLESS_CODECS or codec.startswith("pcm_") or (not codec and ext in LOSSLESS_EXTS)

    # FLAC/ALAC/WavPack/APE/PCM are equivalent lossless families here; the
    # technical stream parameters decide first, then a deterministic container
    # preference keeps FLAC when everything else is equal.
    codec_preference = {
        "flac": 60,
        "alac": 55,
        "wavpack": 50,
        "ape": 45,
        "tta": 44,
        "tak": 43,
        "pcm_s24le": 42,
        "pcm_s16le": 41,
        "opus": 35,
        "aac": 30,
        "vorbis": 25,
        "mp3": 20,
    }.get(codec, 10)
    ext_preference = {
        ".flac": 9, ".m4a": 8, ".wv": 7, ".ape": 6, ".wav": 5,
        ".opus": 4, ".ogg": 3, ".mp3": 2, ".aac": 1,
    }.get(ext, 0)

    return (
        1 if lossless else 0,
        track.sample_rate,
        track.bit_depth,
        track.channels,
        track.bit_rate if not lossless else 0,
        codec_preference,
        ext_preference,
    )


def _logical_title_keys(track: Track) -> Set[str]:
    """Possible logical-title identities used only as an intra-release safety gate.

    Duplicate identity is still the audio group. Using both tags and filename
    prevents a bad TITLE tag from blocking cleanup of obvious duplicate files.
    """
    keys: Set[str] = set()
    for value in (
        track.display_title,
        strip_track_number(track.path.stem),
        track.title,
    ):
        key = identity_title(value or "")
        if key:
            keys.add(key)
    return keys


def _same_logical_track(a: Track, b: Track) -> bool:
    if a.path.parent.resolve() != b.path.parent.resolve():
        return False
    if a.group_id < 0 or a.group_id != b.group_id:
        return False

    number_a = _track_number_key(a)
    number_b = _track_number_key(b)
    # Track position remains a hard safety gate. If one side has a number and
    # the other does not, keep both instead of guessing.
    if number_a is not None and number_b is not None:
        if number_a != number_b:
            return False
    elif number_a is not None or number_b is not None:
        return False

    # Accept when any reliable title source agrees: embedded TITLE, normalized
    # display title, or filename stem. This catches cases such as
    # "01 - Mask Off (...).flac" vs "01 Mask Off (...).flac" even when one
    # embedded tag is inconsistent.
    keys_a = _logical_title_keys(a)
    keys_b = _logical_title_keys(b)
    return bool(keys_a and keys_b and (keys_a & keys_b))


def plan_intra_release_duplicates(
    releases: List[Release],
    decisions: List["ReleaseDecision"],
) -> List[IntraReleaseDuplicate]:
    """Plan redundant audio files inside retained releases.

    Duplicate identity still comes exclusively from the existing audio group.
    Title/track number are only safety gates preventing intentional repeated
    recordings from being removed from different track positions.
    """
    action_by_id = {d.release_id: d.action for d in decisions}
    retained_actions = {"KEEP", "ADD", "REPLACE"}
    planned: List[IntraReleaseDuplicate] = []

    for rel in releases:
        if action_by_id.get(rel.rid) not in retained_actions:
            continue

        # Never remove files from a CUE-based rip automatically because a CUE
        # sheet may reference an exact filename.
        if rel.has_cue:
            continue

        candidates = [t for t in rel.tracks if t.group_id >= 0 and t.path.exists()]
        consumed: Set[Path] = set()

        for i, first in enumerate(candidates):
            if first.path in consumed:
                continue
            same = [first]
            for second in candidates[i + 1:]:
                if second.path in consumed:
                    continue
                if _same_logical_track(first, second):
                    same.append(second)

            if len(same) < 2:
                continue

            keep = max(
                same,
                key=lambda t: (
                    _track_codec_quality(t),
                    t.file_size,
                    -len(t.path.name),
                    str(t.path).lower(),
                ),
            )
            for duplicate in same:
                if duplicate.path == keep.path:
                    continue
                consumed.add(duplicate.path)
                planned.append(
                    IntraReleaseDuplicate(
                        release_id=rel.rid,
                        redundant=duplicate.path,
                        keep=keep.path,
                        reason=(
                            "Same retained release, same logical track position/title "
                            "(tag and/or filename), and same high-confidence audio group. "
                            f"Kept {keep.path.name} ({keep.codec_name or keep.path.suffix.lower()}); "
                            f"moved {duplicate.path.name} ({duplicate.codec_name or duplicate.path.suffix.lower()})."
                        ),
                    )
                )

    return planned


def _duplicates_root(recycle: Path) -> Path:
    """Return <artist>_duplicates as a sibling of the selected artist folder."""
    return recycle.parent / f"{recycle.name}_duplicates"


def _last_manifest_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Karpuzikov" / "Duplicate Edition Analyzer"
    base.mkdir(parents=True, exist_ok=True)
    return base / "last_move.json"


def _is_ancestor(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return parent.resolve() != child.resolve()
    except Exception:
        return False


def _release_paths(rel: Release) -> List[Path]:
    return rel.source_paths


def _remove_empty_dirs(root: Optional[Path]) -> None:
    if root is None or not root.is_dir():
        return
    for current, dirs, files in os.walk(root, topdown=False):
        path = Path(current)
        if path == root:
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def _automatic_move_set(releases: List[Release], decisions: List[ReleaseDecision]) -> List[Tuple[Release, ReleaseDecision]]:
    by_id = {r.rid: r for r in releases}
    chosen: List[Tuple[Release, ReleaseDecision]] = []
    for d in decisions:
        r = by_id[d.release_id]
        if (r.root_kind == "recycle" and d.action == "SKIP") or (r.root_kind == "existing" and d.action == "REMOVE"):
            chosen.append((r, d))

    retained = [by_id[d.release_id] for d in decisions if d.action in {"KEEP", "ADD", "REPLACE"}]
    for r, _d in chosen:
        for source in _release_paths(r):
            for keep in retained:
                for kept_path in _release_paths(keep):
                    if _is_ancestor(source, kept_path):
                        raise RuntimeError(
                            "Move plan conflict:\n\n"
                            f"Remove candidate: {source}\nRetained release: {kept_path}"
                        )

    result: List[Tuple[Release, ReleaseDecision]] = []
    moved_roots: List[Path] = []
    for r, d in sorted(chosen, key=lambda x: min(len(p.parts) for p in _release_paths(x[0]))):
        sources = _release_paths(r)
        if any(any(_is_ancestor(parent, source) for parent in moved_roots) for source in sources):
            continue
        result.append((r, d))
        moved_roots.extend(sources)
    return result


def apply_automatic_plan(
    existing: Optional[Path],
    recycle: Path,
    releases: List[Release],
    decisions: List[ReleaseDecision],
    intra_duplicates: Optional[List[IntraReleaseDuplicate]] = None,
    progress_cb=None,
) -> Dict[str, object]:
    moves = _automatic_move_set(releases, decisions)
    intra_duplicates = list(intra_duplicates or plan_intra_release_duplicates(releases, decisions))
    duplicates = _duplicates_root(recycle)

    # Mirror the original path below the selected root. Multi-disc sibling
    # releases move as one logical decision but preserve every physical folder.
    planned: List[Tuple[Release, ReleaseDecision, Path, Path]] = []
    target_map: Dict[str, Path] = {}
    for release, decision in moves:
        root = release.scan_root or (existing if release.root_kind == "existing" else recycle)
        if root is None:
            raise RuntimeError(f"Missing scan root for: {release.path}")
        for source in _release_paths(release):
            try:
                relative = source.relative_to(root)
            except ValueError:
                raise RuntimeError(f"Release is outside its scan root:\n{source}\n{root}")
            target_base = duplicates / "!Remixes" if release.has_remixes else duplicates
            target = target_base / relative
            key = os.path.normcase(str(target.resolve(strict=False)))
            if key in target_map:
                raise RuntimeError(
                    "Destination collision:\n\n"
                    f"{target_map[key]}\n{source}\n\nDestination: {target}"
                )
            target_map[key] = source
            if target.exists():
                raise RuntimeError(
                    "Destination already exists:\n\n"
                    f"{target}\n\nResolve the conflict and run again."
                )
            planned.append((release, decision, source, target))

    by_id = {r.rid: r for r in releases}
    planned_files: List[Tuple[IntraReleaseDuplicate, Path]] = []
    for item in intra_duplicates:
        release = by_id.get(item.release_id)
        if release is None:
            continue
        root = release.scan_root or (existing if release.root_kind == "existing" else recycle)
        if root is None:
            raise RuntimeError(f"Missing scan root for intra-release duplicate: {item.redundant}")
        try:
            relative = item.redundant.relative_to(root)
        except ValueError:
            raise RuntimeError(f"Duplicate file is outside its scan root:\n{item.redundant}\n{root}")

        root_label = "Existing" if release.root_kind == "existing" else "Recycle"
        target = duplicates / "!Duplicate Files" / root_label / relative
        key = os.path.normcase(str(target.resolve(strict=False)))
        if key in target_map:
            raise RuntimeError(
                "Destination collision:\n\n"
                f"{target_map[key]}\n{item.redundant}\n\nDestination: {target}"
            )
        target_map[key] = item.redundant
        if target.exists():
            raise RuntimeError(
                "Destination already exists:\n\n"
                f"{target}\n\nResolve the conflict and run again."
            )
        planned_files.append((item, target))

    duplicates.mkdir(parents=True, exist_ok=True)
    completed: List[Tuple[Path, Path]] = []
    manifest: Dict[str, object] = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "duplicates_root": str(duplicates),
        "existing_root": str(existing) if existing is not None else "",
        "recycle_root": str(recycle),
        "moves": [],
    }

    try:
        if moves and not planned:
            raise RuntimeError("Redundant releases were found, but no move operations were planned.")

        if progress_cb:
            progress_cb("Moving release folders...", 0, max(1, len(planned)))

        for move_index, (release, decision, source, target) in enumerate(planned, 1):
            if not source.exists():
                raise RuntimeError(f"Move source missing:\n\n{source}")
            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                os.replace(str(source), str(target))
            except OSError:
                shutil.move(str(source), str(target))

            if source.exists() or not target.exists():
                raise RuntimeError(
                    "Move failed:\n\n"
                    f"Source: {source}\nTarget: {target}\n\n"
                    "Completed moves rolled back."
                )

            completed.append((source, target))
            manifest["moves"].append({
                "move_type": "release",
                "release_id": release.rid,
                "action": decision.action,
                "original": str(source),
                "moved_to": str(target),
                "remix_bucket": bool(release.has_remixes),
                "reason": decision.reason,
            })
            if progress_cb:
                progress_cb("Moving release folders...", move_index, max(1, len(planned)))

        if progress_cb:
            progress_cb("Moving duplicate files inside retained releases...", 0, max(1, len(planned_files)))

        for file_index, (item, target) in enumerate(planned_files, 1):
            source = item.redundant
            if not source.exists():
                raise RuntimeError(f"Duplicate-file source missing:\n\n{source}")
            if not item.keep.exists():
                raise RuntimeError(
                    "Chosen survivor is missing; refusing intra-release cleanup:\n\n"
                    f"Keep: {item.keep}\nRedundant: {source}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                os.replace(str(source), str(target))
            except OSError:
                shutil.move(str(source), str(target))

            if source.exists() or not target.exists():
                raise RuntimeError(
                    "Duplicate-file move failed:\n\n"
                    f"Source: {source}\nTarget: {target}\n\n"
                    "Completed moves rolled back."
                )

            completed.append((source, target))
            manifest["moves"].append({
                "move_type": "intra_release_file",
                "release_id": item.release_id,
                "action": "DEDUP",
                "original": str(source),
                "moved_to": str(target),
                "kept": str(item.keep),
                "remix_bucket": False,
                "reason": item.reason,
            })
            if progress_cb:
                progress_cb("Moving duplicate files inside retained releases...", file_index, max(1, len(planned_files)))

        # Remove organizational folders left empty by moved releases/files.
        _remove_empty_dirs(recycle)
        _remove_empty_dirs(existing)

        _last_manifest_path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        for original, target in reversed(completed):
            try:
                if target.exists() and not original.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(original))
            except Exception:
                pass
        raise

    counts = action_summary(decisions)
    remaining_recycle = sum(
        1 for d in decisions
        if next(r for r in releases if r.rid == d.release_id).root_kind == "recycle"
        and d.action in {"ADD", "REPLACE", "KEEP"}
    )
    remix_release_ids = {release.rid for release, _decision in moves if release.has_remixes}
    return {
        "duplicates": duplicates,
        "moved": len(moves),
        "moved_folders": len(planned),
        "intra_duplicate_files": len(planned_files),
        "remix_moved": len(remix_release_ids),
        "remaining_recycle": remaining_recycle,
        "add": counts["ADD"],
        "replace": counts["REPLACE"],
        "removed_current": counts["REMOVE"],
        "skipped_recycle": counts["SKIP"],
    }


def undo_last_run() -> Tuple[int, List[str]]:
    manifest_path = _last_manifest_path()
    if not manifest_path.is_file():
        raise RuntimeError("No undo manifest found.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    moves = data.get("moves") or []
    restored = 0
    conflicts: List[str] = []
    for item in reversed(moves):
        original = Path(item["original"])
        saved = Path(item["moved_to"])
        if not saved.exists():
            # Already restored (or manually removed from the duplicate bucket).
            # If the original exists, this item is complete rather than a conflict.
            continue
        if original.exists():
            conflicts.append(str(original))
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(saved), str(original))
        restored += 1

    # Remove now-empty mirrored helper folders, but preserve any older content.
    dup_root = Path(data.get("duplicates_root") or "")
    _remove_empty_dirs(dup_root)
    try:
        if dup_root.is_dir() and not any(dup_root.iterdir()):
            dup_root.rmdir()
    except Exception:
        pass
    if not conflicts:
        try:
            manifest_path.unlink(missing_ok=True)
        except Exception:
            pass
    return restored, conflicts


class DoneWindow(tk.Toplevel):
    def __init__(self, master, recycle: Path, result: Dict[str, object]):
        super().__init__(master)
        self.title(f"{APP_NAME} - Done")
        self.resizable(False, False)
        self.configure(background=DARK_BG)
        _enable_dark_titlebar(self)
        self.recycle = recycle
        self.duplicates = Path(str(result["duplicates"]))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=18, pady=18)

        ttk.Label(frame, text="Completed", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                f"Recycle releases kept: {result['remaining_recycle']}\n"
                f"Redundant releases moved: {result['moved']}\n"
                f"Duplicate files removed inside retained releases: {result['intra_duplicate_files']}\n"
                f"Moved under !Remixes: {result['remix_moved']}\n"
                f"Added: {result['add']}   Replaced: {result['replace']}   "
                f"Recycle skipped: {result['skipped_recycle']}   Existing removed: {result['removed_current']}"
            ),
            justify="left",
        ).pack(anchor="w", pady=(8, 10))
        ttk.Label(frame, text=f"Duplicates:\n{self.duplicates}", justify="left").pack(anchor="w", pady=(0, 14))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open recycle", command=lambda: os.startfile(self.recycle)).pack(side="left")
        ttk.Button(buttons, text="Open duplicates", command=lambda: os.startfile(self.duplicates)).pack(side="left", padx=8)
        ttk.Button(buttons, text="Undo last run", command=self.undo).pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        self.transient(master)
        self.grab_set()
        self.focus_force()

    def undo(self):
        if not messagebox.askyesno(APP_NAME, "Undo last run?", parent=self):
            return
        try:
            restored, conflicts = undo_last_run()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if conflicts:
            messagebox.showwarning(
                APP_NAME,
                f"Restored: {restored}\nConflicts: {len(conflicts)}",
                parent=self,
            )
        else:
            messagebox.showinfo(APP_NAME, f"Restored: {restored}", parent=self)
        self.destroy()



class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                self.tip,
                text=self.text,
                justify="left",
                background=DARK_FIELD,
                foreground=DARK_FG,
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=5,
                font=("Segoe UI", 9),
            )
            label.pack()
        except Exception:
            self.tip = None

    def _hide(self, _event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class PatternReviewWindow(tk.Toplevel):
    def __init__(self, master, patterns: List[Dict[str, object]], preferences: Dict[str, bool]):
        super().__init__(master)
        self.title(f"{APP_NAME} - Track Pattern Review")
        self.geometry("860x640")
        self.minsize(720, 480)
        self.configure(background=DARK_BG)
        _enable_dark_titlebar(self)
        self.result: Optional[Dict[str, bool]] = None
        self.vars: Dict[str, tk.BooleanVar] = {}

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(outer, text="Unusual track pattern review", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Only unusual / non-standard patterns are shown. Standard families are recognized structurally, so prefixes "
                "such as artist/remixer names do not make Radio Edit, Instrumental, Acoustic, Demo, Session, "
                "Extended/VIP/Vocal Mix, 7\"/12\" versions, etc. appear here. "
                "Remix/live tracks stay controlled by Save Remixes / Save Live recordings."
            ),
            style="Help.TLabel",
            wraplength=810,
            justify="left",
        ).pack(anchor="w", pady=(5, 10))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Check all", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(toolbar, text="Uncheck all", command=lambda: self._set_all(False)).pack(side="left", padx=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, background=DARK_BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        rows = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=rows, anchor="nw")

        def sync_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        rows.bind("<Configure>", sync_scroll)
        canvas.bind("<Configure>", sync_scroll)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        for row_index, item in enumerate(patterns):
            key = str(item["key"])
            default = bool(preferences.get(key, True))
            var = tk.BooleanVar(value=default)
            self.vars[key] = var

            row = ttk.Frame(rows)
            row.grid(row=row_index, column=0, sticky="ew", pady=(0, 9))
            rows.columnconfigure(0, weight=1)

            count = int(item.get("count", 0))
            label = str(item.get("label", key))
            ttk.Checkbutton(row, text=f"{label} ({count})", variable=var).pack(anchor="w")

            variants = [str(x) for x in item.get("variants", [])]
            examples = [str(x) for x in item.get("examples", [])]
            details = []
            if len(variants) > 1:
                details.append("Variants: " + "; ".join(variants[:6]))
            if examples:
                details.append("Examples: " + "; ".join(examples[:3]))
            if details:
                ttk.Label(
                    row,
                    text=" | ".join(details),
                    style="Help.TLabel",
                    wraplength=790,
                    justify="left",
                ).pack(anchor="w", padx=(24, 0), pady=(2, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(footer, text="Continue", command=self._accept).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(master)
        self.grab_set()
        self.focus_force()

    def _set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)

    def _accept(self) -> None:
        self.result = {key: bool(var.get()) for key, var in self.vars.items()}
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("920x620")
        self.minsize(820, 560)
        _apply_dark_theme(self)
        saved = _load_app_settings()
        self.existing_var = tk.StringVar(value=saved.get("existing_discography", ""))
        self.recycle_var = tk.StringVar(value=saved.get("recycle_update_folder", ""))

        if "save_remixes" in saved:
            save_remixes = bool(saved.get("save_remixes"))
        else:
            save_remixes = not bool(saved.get("exclude_remixes", True))
        if "save_live" in saved:
            save_live = bool(saved.get("save_live"))
        else:
            save_live = not bool(saved.get("exclude_live", True))

        self.save_remixes_var = tk.BooleanVar(value=save_remixes)
        self.save_live_var = tk.BooleanVar(value=save_live)
        self.logging_var = tk.BooleanVar(value=bool(saved.get("logging_enabled", False)))
        saved_patterns = saved.get("unusual_pattern_preferences_v5", {})
        self.pattern_preferences: Dict[str, bool] = (
            {str(k): bool(v) for k, v in saved_patterns.items()} if isinstance(saved_patterns, dict) else {}
        )
        self.status_var = tk.StringVar(value="Ready")
        self.progress_detail_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0)
        self._running = False
        self._run_started_at = 0.0
        self._last_progress_stage = ""
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self):
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=16, pady=14)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(11, weight=1)

        ttk.Label(frm, text="Existing discography (optional)").grid(row=0, column=0, sticky="w", pady=(0, 3))
        existing_entry = ttk.Entry(frm, textvariable=self.existing_var)
        existing_entry.grid(row=1, column=0, sticky="ew")
        existing_buttons = ttk.Frame(frm)
        existing_buttons.grid(row=1, column=1, padx=(10, 0), sticky="e")
        ttk.Button(existing_buttons, text="Browse...", command=lambda: self.browse(self.existing_var)).pack(side="left")
        ttk.Button(existing_buttons, text="Clear", command=self.clear_existing).pack(side="left", padx=(6, 0))
        ToolTip(existing_entry, "Already processed collection. Leave blank to analyze only the new/update folder.")

        ttk.Label(frm, text="New / update releases").grid(row=2, column=0, sticky="w", pady=(12, 3))
        recycle_entry = ttk.Entry(frm, textvariable=self.recycle_var)
        recycle_entry.grid(row=3, column=0, sticky="ew")
        ttk.Button(frm, text="Browse...", command=lambda: self.browse(self.recycle_var)).grid(
            row=3, column=1, padx=(10, 0), sticky="e"
        )
        ToolTip(recycle_entry, "Folder containing releases to analyze and filter.")

        options = ttk.Frame(frm)
        options.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 8))
        remix_cb = ttk.Checkbutton(
            options,
            text="Save Remixes",
            variable=self.save_remixes_var,
            command=self.save_settings,
        )
        remix_cb.pack(side="left")
        live_cb = ttk.Checkbutton(
            options,
            text="Save Live recordings",
            variable=self.save_live_var,
            command=self.save_settings,
        )
        live_cb.pack(side="left", padx=(18, 0))
        logging_cb = ttk.Checkbutton(
            options,
            text="Logging",
            variable=self.logging_var,
            command=self.save_settings,
        )
        logging_cb.pack(side="left", padx=(18, 0))
        ToolTip(remix_cb, "Checked: remixes are included in comparison and selection. Unchecked: remixes are skipped.")
        ToolTip(live_cb, "Checked: live recordings are included in comparison and selection. Unchecked: live recordings are skipped.")
        ToolTip(logging_cb, "Checked: write a detailed JSONL log for the audio comparison process.")

        match_label = ttk.Label(frm, text="Match: Chromaprint + duration (audio only)", style="Help.TLabel")
        match_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ToolTip(match_label, "Titles, filenames, tags, barcodes, and folder names do not decide duplicate identity.")

        ttk.Label(frm, text="Progress", style="Section.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(2, 5)
        )
        ttk.Label(frm, textvariable=self.status_var).grid(
            row=7, column=0, columnspan=2, sticky="w"
        )
        self.progress = ttk.Progressbar(frm, variable=self.progress_var, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(5, 3))
        ttk.Label(frm, textvariable=self.progress_detail_var, style="Help.TLabel").grid(
            row=9, column=0, columnspan=2, sticky="w"
        )

        ttk.Label(frm, text="Activity", style="Section.TLabel").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(12, 5)
        )
        self.activity = tk.Text(
            frm,
            height=9,
            wrap="word",
            background="#161616",
            foreground=DARK_FG,
            insertbackground=DARK_FG,
            selectbackground=DARK_ACCENT,
            relief="solid",
            borderwidth=1,
            font=("Cascadia Mono", 9),
            state="disabled",
        )
        self.activity.grid(row=11, column=0, columnspan=2, sticky="nsew")

        actions = ttk.Frame(frm)
        actions.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.run_btn = ttk.Button(actions, text="Analyze", command=self.start)
        self.run_btn.pack(side="left")
        ttk.Button(actions, text="Undo last run", command=self.undo_main).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Close", command=self.on_close).pack(side="right")


    def undo_main(self):
        if self._running:
            return
        if not messagebox.askyesno(APP_NAME, "Undo last run?", parent=self):
            return
        try:
            restored, conflicts = undo_last_run()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if conflicts:
            messagebox.showwarning(APP_NAME, f"Restored: {restored}\nConflicts: {len(conflicts)}", parent=self)
        else:
            messagebox.showinfo(APP_NAME, f"Restored: {restored}", parent=self)

    def save_settings(self):
        _save_app_settings(
            self.existing_var.get(),
            self.recycle_var.get(),
            self.save_remixes_var.get(),
            self.save_live_var.get(),
            self.logging_var.get(),
            self.pattern_preferences,
        )

    def on_close(self):
        self.save_settings()
        self.destroy()

    def browse(self, var: tk.StringVar):
        initial = var.get().strip()
        kwargs = {"title": "Select folder"}
        if initial and Path(initial).is_dir():
            kwargs["initialdir"] = initial
        path = filedialog.askdirectory(**kwargs)
        if path:
            var.set(path)
            self.save_settings()

    def clear_existing(self):
        self.existing_var.set("")
        self.save_settings()

    def _append_activity(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity.configure(state="normal")
        self.activity.insert("end", f"{timestamp}  {text}\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _clear_activity(self):
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.configure(state="disabled")

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _heartbeat(self):
        if not self._running:
            return
        elapsed = self._format_elapsed(time.monotonic() - self._run_started_at)
        current = self.progress_detail_var.get()
        base = current.split(" | Elapsed ", 1)[0] if current else ""
        self.progress_detail_var.set(f"{base} | Elapsed {elapsed}" if base else f"Elapsed {elapsed}")
        self.after(1000, self._heartbeat)

    def _set_running(self, running: bool):
        self._running = running
        if running:
            self._run_started_at = time.monotonic()
            self._heartbeat()

    def update_progress(self, text: str, current: int, total: int):
        def apply_update():
            pct = 0 if total <= 0 else (current / total) * 100
            stage = text.rstrip(".")
            if stage != self._last_progress_stage:
                self._last_progress_stage = stage
                self._append_activity(stage)
            self.status_var.set(stage)
            self.progress_var.set(pct)
            count = f"{current:,} / {total:,}" if total > 0 else ""
            elapsed = self._format_elapsed(time.monotonic() - self._run_started_at) if self._running else "00:00"
            self.progress_detail_var.set(
                f"{count} ({pct:.0f}%) | Elapsed {elapsed}" if count else f"Elapsed {elapsed}"
            )
        self.after(0, apply_update)

    def start(self):
        existing_text = self.existing_var.get().strip()
        existing = Path(existing_text) if existing_text else None
        recycle_text = self.recycle_var.get().strip()
        recycle = Path(recycle_text) if recycle_text else None

        if recycle is None or not recycle.is_dir():
            messagebox.showerror(APP_NAME, "Invalid Recycle / update folder.")
            return
        if existing is not None and not existing.is_dir():
            messagebox.showerror(APP_NAME, "Invalid Existing discography folder.")
            return

        if existing is not None:
            try:
                if existing.resolve() == recycle.resolve() or _is_ancestor(existing, recycle) or _is_ancestor(recycle, existing):
                    messagebox.showerror(APP_NAME, "Existing and Recycle folders must be separate and non-nested.")
                    return
            except Exception:
                pass

        self.save_settings()
        self.run_btn.configure(state="disabled")
        self.progress_var.set(0)
        self.progress_detail_var.set("")
        self.status_var.set("Preparing track pattern review")
        self._last_progress_stage = ""
        self._clear_activity()
        self._append_activity("Started")
        if self.logging_var.get():
            self._append_activity("Logging enabled")
        self._set_running(True)
        threading.Thread(
            target=self.preflight_worker,
            args=(
                existing,
                recycle,
                self.save_remixes_var.get(),
                self.save_live_var.get(),
                self.logging_var.get(),
            ),
            daemon=True,
        ).start()

    def preflight_worker(
        self,
        existing: Optional[Path],
        recycle: Path,
        save_remixes: bool,
        save_live: bool,
        logging_enabled: bool,
    ):
        try:
            releases, tracks = prepare_analysis(existing, recycle, self.update_progress)
            patterns = collect_track_patterns(tracks)
            self.after(
                0,
                lambda releases=releases, tracks=tracks, patterns=patterns: self.review_patterns(
                    existing, recycle, save_remixes, save_live, logging_enabled, releases, tracks, patterns
                ),
            )
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda error=error: self.failed(error))

    def review_patterns(
        self,
        existing: Optional[Path],
        recycle: Path,
        save_remixes: bool,
        save_live: bool,
        logging_enabled: bool,
        releases: List[Release],
        tracks: List[Track],
        patterns: List[Dict[str, object]],
    ):
        self._set_running(False)

        excluded_pattern_keys: Set[str] = set()
        if patterns:
            dialog = PatternReviewWindow(self, patterns, self.pattern_preferences)
            self.wait_window(dialog)
            if dialog.result is None:
                self.run_btn.configure(state="normal")
                self.status_var.set("Cancelled")
                self.progress_detail_var.set("")
                self._append_activity("Cancelled before audio comparison")
                return

            self.pattern_preferences.update(dialog.result)
            excluded_pattern_keys = {key for key, keep in dialog.result.items() if not keep}
            self.save_settings()
            self._append_activity(
                f"Pattern review complete: {len(patterns)} pattern(s), "
                f"{len(excluded_pattern_keys)} excluded"
            )
        else:
            self._append_activity("No version-style track patterns detected")

        self.status_var.set("Continuing analysis")
        self.progress_var.set(0)
        self.progress_detail_var.set("")
        self._last_progress_stage = ""
        self._set_running(True)
        threading.Thread(
            target=self.worker_prepared,
            args=(
                existing,
                recycle,
                releases,
                tracks,
                save_remixes,
                save_live,
                logging_enabled,
                excluded_pattern_keys,
            ),
            daemon=True,
        ).start()

    def worker_prepared(
        self,
        existing: Optional[Path],
        recycle: Path,
        releases: List[Release],
        tracks: List[Track],
        save_remixes: bool,
        save_live: bool,
        logging_enabled: bool,
        excluded_pattern_keys: Set[str],
    ):
        try:
            comparison_log_path = _new_comparison_log_path(recycle) if logging_enabled else None
            result = analyze_prepared(
                releases,
                tracks,
                True,
                self.update_progress,
                not save_remixes,
                not save_live,
                excluded_pattern_keys,
                comparison_log_path,
            )
            self.after(0, lambda result=result: self.done(existing, recycle, result))
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda error=error: self.failed(error))

    def done(self, existing: Optional[Path], recycle: Path, result):
        self._set_running(False)
        self.run_btn.configure(state="normal")
        self.progress_var.set(100)
        self.progress_detail_var.set("100%")
        self._append_activity("Analysis complete")
        releases, tracks, groups, selected, reviews, notes = result
        comparison_log = next(
            (n.split("COMPARISON LOG:", 1)[1].strip() for n in notes if n.startswith("COMPARISON LOG:")),
            "",
        )
        if comparison_log:
            self._append_activity(f"Comparison log: {comparison_log}")
        decisions = build_release_decisions(releases, tracks, selected, reviews)
        counts = action_summary(decisions)
        recycle_kept = sum(
            1 for d in decisions
            if next(r for r in releases if r.rid == d.release_id).root_kind == "recycle"
            and d.action in {"ADD", "REPLACE", "KEEP"}
        )
        intra_duplicates = plan_intra_release_duplicates(releases, decisions)
        to_move = counts["SKIP"] + counts["REMOVE"] + len(intra_duplicates)
        self.status_var.set("Analysis complete.")

        if to_move == 0:
            messagebox.showinfo(
                APP_NAME,
                (
                    f"No redundant releases or duplicate files found.\n"
                    f"Recycle releases kept: {recycle_kept}"
                    + (f"\n\nComparison log:\n{comparison_log}" if comparison_log else "")
                ),
                parent=self,
            )
            return

        confirm_text = (
            "Apply proposed moves?\n\n"
            f"Recycle releases kept: {recycle_kept}\n"
            f"Recycle releases moved as redundant: {counts['SKIP']}\n"
            f"Duplicate files inside retained releases: {len(intra_duplicates)}\n"
        )
        if existing is not None:
            confirm_text += f"Existing releases moved as redundant: {counts['REMOVE']}\n"
        confirm_text += (
            f"\nMove destination:\n{_duplicates_root(recycle)}\n"
            "Redundant releases containing remixes are placed under !Remixes.\n"
            "Redundant files inside retained releases are moved under !Duplicate Files."
        )
        if comparison_log:
            confirm_text += f"\n\nComparison log:\n{comparison_log}"
        confirm = messagebox.askyesno(
            APP_NAME,
            confirm_text,
            parent=self,
        )
        if not confirm:
            self.status_var.set("Cancelled.")
            return

        self.run_btn.configure(state="disabled")
        self.progress_var.set(0)
        self.progress_detail_var.set("")
        self.status_var.set("Applying moves")
        self._last_progress_stage = ""
        self._append_activity("Applying moves")
        self._set_running(True)
        threading.Thread(
            target=self.apply_worker,
            args=(existing, recycle, releases, decisions, intra_duplicates),
            daemon=True,
        ).start()

    def apply_worker(
        self,
        existing: Optional[Path],
        recycle: Path,
        releases: List[Release],
        decisions: List[ReleaseDecision],
        intra_duplicates: List[IntraReleaseDuplicate],
    ):
        try:
            result = apply_automatic_plan(
                existing,
                recycle,
                releases,
                decisions,
                intra_duplicates,
                self.update_progress,
            )
            self.after(0, lambda: self.applied(recycle, result))
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda error=error: self.failed(error))

    def applied(self, recycle: Path, result: Dict[str, object]):
        self._set_running(False)
        self.run_btn.configure(state="normal")
        self.progress_var.set(100)
        self.progress_detail_var.set("100%")
        self.status_var.set("Complete")
        self._append_activity("Moves complete")
        DoneWindow(self, recycle, result)

    def failed(self, error: str):
        self._set_running(False)
        self.run_btn.configure(state="normal")
        self.status_var.set("Failed")
        self._append_activity(f"Failed: {error}")
        messagebox.showerror(APP_NAME, error, parent=self)


def _startup_crash_log_path() -> Path:
    return _documents_dir() / "Karpuzikov Tools" / "Duplicate Edition Analyzer - Crash.log"


def _report_startup_crash(exc: BaseException) -> None:
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = _startup_crash_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{APP_NAME} {APP_VERSION}\n"
            f"Startup failed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{details}",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror(
            APP_NAME,
            "Startup failed.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"Crash log:\n{path}",
            parent=error_root,
        )
        error_root.destroy()
    except Exception:
        pass


def main():
    try:
        app = App()
        # Make sure a newly created root is visible and brought forward even when
        # Windows restores focus/state oddly for a .pyw launch.
        app.after(100, app.deiconify)
        app.after(150, app.lift)
        app.mainloop()
    except BaseException as exc:
        _report_startup_crash(exc)


if __name__ == "__main__":
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass
    main()