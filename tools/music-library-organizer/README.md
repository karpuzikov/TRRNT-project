# Music Library Organizer

**Version: 0.1.2 - Under construction ⚠️**

**[Download the versioned Windows Python GUI](../Music%20Library%20Organizer%20v0.1.2.pyw)**

Standalone prototype inspired by [Music Collection Cleaner v0.5.18](https://github.com/karpuzikov/MCC/blob/main/Music%20Collection%20Cleaner%20v0.5.18.pyw). MCC's visual design is used as a reference; MCC's executable and persistent data are not modified or imported.

## Current 0.1.2 behavior

- Compact dark MCC-style WebView2 window with input and explicit output collection folder selectors, drag-and-drop where native pywebview exposes full folder paths, and manual paste/Browse fallback.
- ALAC (.m4a) or FLAC (.flac) output choice, retained in program-specific settings.
- Recursive discovery of releases under the collection root, flattening deprecated `Albums`, `Singles`, `EPs`, etc. folders while retaining internal disc folders.
- Scans candidate release names for date/title/bracketed identifiers and flags unverified names for review instead of guessing.
- Preview of every proposed file name/target path (Details on a release row), including image-splitting output, metadata-based audio names, catalog/barcode-named CD CUE and rip LOG, and WEB `audiochecker.log`. Source-change preflight checks detect files modified after Scan. Reviews/conflicts cannot be Apply-selected.
- Copies files into a separate **explicitly chosen** destination; does not move/delete source folders. Writes each release in a staging directory, verifies copied files or decoded PCM on lossless conversions, then finalizes without overwriting existing destination releases.
- Splits single-file lossless album images according to a valid CUE, using verified sample-rate/tick boundaries; rewrites a track-based CUE where safe. **CUE INDEX 00/HTOA, additional indexes, missing cues, mixed CUE layouts and uncertain source metadata are review blockers**, not grounds to guess. No lossy source conversion.
- Durable Undo of the last export: only if the entire set of generated output files still exactly matches the recorded SHA-256 manifest. Original sources are untouched.
- Preserves separate settings, logs, Undo manifests and Python dependencies under `Documents/Karpuzikov Tools/Music Library Organizer/`.

## How to try it on Windows

1. Download `Music Library Organizer v0.1.2.pyw` and open it with Python 3.11+ on Windows. On first launch, the program attempts to install isolated pywebview/mutagen dependencies as needed; Windows WebView2 is required. For encoding, FFmpeg/FFprobe will be checked/installed through WinGet if absent.
2. **First test with a small disposable collection**, not the complete music library.
3. Choose a source folder and a **different, non-nested** output folder. If selecting FLAC, the output folder must not be an ALAC-labeled collection.
4. Set Output format; click Scan / Preview; inspect release statuses and click **Details...** to see full source/target paths. Unresolved releases are excluded from Apply.
5. Click **Apply selected**, confirm, then verify output and use **Undo last run** to test reversible handling before expanding the scope.

To run source-level regression tests (separate from real GUI acceptance):

```powershell
py -3 "tools\music-library-organizer\test_organizer.py"
```

## Important current limitations

This is a prototype, **not** a metadata-research product or Windows-accepted release. It does **not yet** look up MusicBrainz, Discogs, Beatport or BPTopTracker, identify earliest worldwide dates, dynamically infer CD pressing country, or automatically resolve missing catalog/barcode fields; it trusts only well-formed source-folder identifiers as provisional for preview/export. It does not automatically settle whether a featured artist was added by a remix when source provenance is unavailable. It does not yet perform an independent embedded-artwork and tag-parity audit on every transcode. It uses FFmpeg's native ALAC encoder for initial lossless parity verification; the existing project's preferred refalac pipeline has not yet been integrated.

Do not use it for an unresolved CUE containing INDEX 00/HTOA, pregap audio, additional indexes or ambiguous multi-image layout; those scenarios are intentionally blocked pending a full fidelity implementation. WinGet/bootstrap failure requires investigation. Do not mistake source-level checks for Windows functional testing. A crash with an Undo manifest marked `pending` needs manual reconciliation before exporting again.

See [CONTINUITY.md](CONTINUITY.md), [UI_UX_CONTROL_CONTRACT.md](UI_UX_CONTROL_CONTRACT.md), and root [RULES.md](../../RULES.md) for requirements and acceptance status. **No automatic GitHub Actions workflows were added.**
