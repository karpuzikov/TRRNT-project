# Music Library Organizer

**Version: 0.2.0 - Under construction ⚠️**

**[Download Music Library Organizer v0.2.0.pyw](../Music%20Library%20Organizer%20v0.2.0.pyw)**

Standalone Windows application with the dark pywebview / Edge WebView2 design language of [Music Collection Cleaner v0.5.18](https://github.com/karpuzikov/MCC/blob/main/Music%20Collection%20Cleaner%20v0.5.18.pyw). It does not modify MCC or use MCC's settings/cache.

## One-click workflow

1. Choose **Input collection** (e.g. `C:\deemix Music\INNA - UPDATED`).
2. Choose a different, non-nested **Organized output** directory (e.g. `C:\deemix Music\INNA - UPDATED - Sorted`).
3. Select **Output format: ALAC or FLAC**.
4. Click **Organize**. It automatically discovers and processes all eligible releases, converts/renames files and copies completed releases to the output. There is **no separate Scan, REVIEW, Select ready, or Apply step**.

**Missing `audiochecker.log` is a warning, never a reason to block file organization.** The app does not forge an AudioChecker report. It continues automatically when a particular release fails; the end report shows organized/already-present/failed/warning counts with each release's reason. Genuine missing track metadata, unsupported codec, unsafe CUE, output name conflicts, missing decoder and other hard failures are recorded and skipped *without stopping the remaining releases*. You cannot manually review all releases to unblock the batch because that step has been removed.

**Original input files are never changed.** Output is written through temporary staging folders. Completed outputs are checked and a SHA-256 Undo manifest is kept. Previously generated output is not overwritten on reruns; duplicates are marked already present. The output root must not be within the input root. **Undo last run** removes only files matching the manifest, after confirmation and verification; earlier run manifests are archived under the tool's own program data.

## Naming / conversion scope

- Folder discovery recursively finds existing release subfolders and flattens obsolete organizational subfolders (e.g. `deemix`, `Albums`, `EPs`) **in the output only**, preserving release-internal `CD 1` / `CD 2`.
- Output individual audio tracks use `NN Track Title.m4a` for ALAC or `NN Track Title.flac` for FLAC, number padded to at least 2, exactly one space, no artist prefix. Title normalization follows the TRRNT shared glossary. A more complete original file name such as `01 OK (Extended Version).flac` is kept when a shorter `OK` title tag would otherwise strip the version.
- Existing CUE/logs are preserved. When a physical edition identifier is available in an already correctly structured folder, the output CUE/rip LOG can be renamed to its catalog number; unknown identifiers leave existing sidecar names unchanged. A missing WEB AudioChecker log is reported but does not prevent export.
- Supports known lossless input codecs and exports to ALAC or FLAC using FFmpeg's native lossless encoders. CUE-described single-image CDs can be split when all track boundaries are representable safely. INDEX 00/pregaps/HTOA, irregular CUE layouts, and ambiguous sources remain explicit per-release failures instead of guessed boundaries.
- Every release must pass source-to-output decoded PCM or exact copy verification before finalization. The organizer keeps all non-audio sidecars where they do not conflict.

## Limitations / acceptance status

**Not yet Windows-tested.** The UI and workflow have been changed substantially. JavaScript syntax was checked using V8; full Python compilation, source-level unit tests, Windows WebView2 screenshots, actual ALAC/FLAC output and Undo remain **NOT RUN** here. **Test first on a disposable input/output pair.**

Important remaining scope: no live MusicBrainz DiscID/Discogs/Beatport/BPTopTracker lookup, no verified automatic earliest worldwide release date/manufacturing country/catalog resolution, no refalac output integration (uses native FFmpeg ALAC encoder), incomplete metadata/artwork parity auditing, and restricted CUE INDEX 00/pregap/HTOA support. Existing input release folder names are retained when they cannot be authoritatively resolved; this is a conservative export, **not** a claim of fully researched catalog provenance.

Python 3.11+ on Windows is required; pywebview/WebView2 UI and mutagen dependencies are bootstrapped as needed and FFmpeg/FFprobe are checked/installed via WinGet as needed. Persistent data belongs only to `Documents\Karpuzikov Tools\Music Library Organizer\`.

Regression test source (tests **not executed**): [test_organizer.py](test_organizer.py).

See [CONTINUITY.md](CONTINUITY.md), [UI_UX_CONTROL_CONTRACT.md](UI_UX_CONTROL_CONTRACT.md), and the project's [RULES.md](../../RULES.md) / [MUSIC_GLOSSARY.md](../../MUSIC_GLOSSARY.md). No GitHub Actions workflows modified.
