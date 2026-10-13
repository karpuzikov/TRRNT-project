# Music Library Organizer - Continuity

Current version: **0.1.2 - Under construction ⚠️**
Repository: `karpuzikov/TRRNT-project`, default branch `main`.
Current source: [`tools/Music Library Organizer v0.1.2.pyw`](../Music%20Library%20Organizer%20v0.1.2.pyw). Earlier v0.1.0 and v0.1.1 sources are preserved only as prior development snapshots.
This product is **independent of MCC** and may later be integrated into Music Collection Cleaner only after separate user approval. Do not modify `karpuzikov/MCC` application files/settings, DEA, or their data. This app's personal state belongs only to `Documents/Karpuzikov Tools/Music Library Organizer/` (settings, logs, journal, dependencies).

## Purpose / scope

Preview and organize music release folders/files by the user's TRRNT naming convention. MCC 0.5.18 is the exact UI *visual reference*: dark WebView2/pywebview with Segoe UI, its color tokens, source path rows, compact buttons, cards, progress, activity, confirmation and clear undo control. Use shared MUSIC_GLOSSARY.md semantics. The initial implementation should provide **copy-to-distinct-destination** so untested workflows cannot modify the original release folders. After more testing, a separate controlled Move option may be developed. Keep default status Under construction until tested on Windows.

Desired workflow: choose source artist/brand collection root and separate explicit output root; select ALAC or FLAC; Scan; inspect per-release preview of file changes, conflicts and needs-review items; Apply selected safe releases only after confirmation; verify outputs and keep originals; persist an Undo manifest; Undo only unchanged outputs created by the program. Flatten deprecated release-category folders under the supplied output root but preserve CD N folder structure.

## Mandatory naming and lossless behavior

- Release folders follow `RULES.md`, including earliest-worldwide-date, pressing-country evidence and last-resort territory fallback, correct catalog/barcode, reissue year, Single/EP/remix title rules. **Do not silently fabricate missing metadata or select MusicBrainz candidates.** Metadata enrichment via MusicBrainz/Discogs/Beatport may be incremental and explicitly noted if not yet implemented.
- Track filename `NN Track Title.m4a` (ALAC) or `NN Track Title.flac` (FLAC). Number padded to at least 2, one space, complete title from metadata/CUE, no artist prefix. Render descriptive version/featured roles consistently with `MUSIC_GLOSSARY.md`; when credit provenance is ambiguous, flag review rather than rewrite.
- CD existing `.cue`/`.log` basename should be catalog, barcode fallback; WEB genuine AudioChecker report `audiochecker.log`. Missing evidence/report is not fabricated. Multi-disc collision must block or be handled without overwriting.
- Single-image CD rip with valid CUE must be split to individual tracks and encoded to chosen lossless ALAC/FLAC output; input FLAC/APE/WV/WAV may be supported where a lossless decoder is present. Preserve track order, indexes, pregaps, HTOA; if not confidently preservable, block and report. Verify finished output before commit. Use no lossy target formats for now, and do not mislabel lossy input.
- Input roots and outputs must be distinct and not nested (prevent recursing/overwriting). Archive original CUE and LOG; no deletion of originals.
- Do not infer a FLAC naming hierarchy from ALAC rules: output root is **explicitly selected by user** pending a future strict FLAC root convention.

## Implemented v0.1.2 checkpoint (source inspection, not Windows-accepted)

- Real standalone, versioned, single-file Python GUI using **pywebview / Edge WebView2**, selected to match MCC v0.5.18's existing UI stack and visual language. The app does not change or import MCC settings, cache, release data, source code or logs. Product isolated under its own Documents program directory.
- Two input paths (source and separate, non-nested output), Browse/open folder, drag/drop when native path is available, ALAC/FLAC choice, Scan/Preview, per-release Details showing source-to-target files, ready/review states, selection, Apply, Cancel, persistent hash-checked Undo.
- Recursive release discovery and flattening; source folder title/metadata used only when adequately recognized, otherwise review; no external metadata query yet. Detects CUE image layouts and parses track/index data; explicitly blocks INDEX 00, HTOA, extra indexes, mixed-image/multi-file cases that cannot be represented safely. Converts lossless to ALAC/FLAC using FFmpeg native codecs, with 32-bit normalized decoded PCM hash checks for each converted track; copies same-codec files with binary hash verification. Source release untouched.
- CD CUE/LOG filenames use provisional source-folder catalog; verified barcode fallback or automatic MusicBrainz/Discogs validation **not implemented yet**. WEB requires real `audiochecker.log`; missing log becomes review. Destination-side source path/mtime/size snapshot revalidation added in v0.1.2. Existing original CUE, LOG, artwork and other support file bytes are left untouched.
- Output is staged into temporary destination directories, validated, and renamed atomically into selected target. Journal saves generated file hashes and a pending checkpoint before finalizing each release; Undo blocks if any generated file has been changed. Interrupted transaction with `pending` requires manual reconciliation before Undo or a new Apply. Only changes at destination, never source.
- Current known incompleteness: auto MusicBrainz DiscID/Discogs/Beatport/BPTopTracker enrichment, earliest worldwide dates, country-of-manufacture evidence, verified release-specific catalog fallback, actual refalac use, lossless embedded-art parity, exceptional CUE HTOA/INDEX 00 handling, complex mixed CUE layouts, rename-in-place/Move workflows, real FLAC naming hierarchy, and broad on-disk content testing. Hard-to-prove inputs are blocked or demand explicit review. **No claims of full functionality/parity/stability** until each remaining issue is resolved and tested.
- User-mode test steps: download versioned `.pyw`; open with Python 3.11+ on Windows; select a small disposable root; explicitly choose a different output folder; Scan, inspect Details and review rows; Apply safe subset; compare actual output and Undo. Auto-installs isolated pywebview/mutagen when absent, and attempts WinGet FFmpeg setup if needed. Has no bundled EXE release asset yet.
- Source tests: `tools/music-library-organizer/test_organizer.py` contains unit/FFmpeg integration cases, **NOT EXECUTED in this environment**. Inline JavaScript checked as parseable with an isolated V8 `Function(script)` syntax check - **PASS (syntax only)**. Python AST compilation, test suite, Windows GUI screenshots, file-system round-trips, real AudioChecker/DiscID lookups and WebView2 interactions: **NOT RUN**.
- Use `tools/music-library-organizer/README.md` and `UI_UX_CONTROL_CONTRACT.md` for controls, known limitations, and acceptance handoff. No GitHub Actions workflow was created/modified.

## Implementation safety, dependencies and handoff

Global `SOFTWARE_RULES.md`, `UI_UX_RULES.md`, `UI_UX_ACCEPTANCE.md` and project `RULES.md` apply. New software uses no Tkinter, no GitHub Actions modification, version in filename, dark UI, and must check dependencies and use program-specific isolated directories. Read `UI_UX_CONTROL_CONTRACT.md` before changes.

**Acceptance status (v0.1.2):** Windows GUI, screenshots, real click/hover/keyboard/resize, Windows output, metadata lookup, real file conversion, Undo, full tests: **NOT RUN**. JavaScript syntax parsing was checked independently and does not substitute for real GUI verification. Do not label stable or say tests passed without explicit evidence. Write exact results and known issues after implementation and unit tests.

**Follow-up:** Implement source, add regression tests, verify GitHub file contents, run on user's Windows files, compare UI against MCC 0.5.18, and address any warnings before stable status.
