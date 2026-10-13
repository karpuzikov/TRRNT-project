# Music Library Organizer - Continuity

Current version: **0.1.0 - Under construction ⚠️**
Repository: `karpuzikov/TRRNT-project`, default branch `main`.
Source path (planned in this checkpoint): `tools/Music Library Organizer v0.1.0.pyw`.
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

## Implementation safety, dependencies and handoff

Global `SOFTWARE_RULES.md`, `UI_UX_RULES.md`, `UI_UX_ACCEPTANCE.md` and project `RULES.md` apply. New software uses no Tkinter, no GitHub Actions modification, version in filename, dark UI, and must check dependencies and use program-specific isolated directories. Read `UI_UX_CONTROL_CONTRACT.md` before changes.

**Acceptance status (initial):** Windows GUI, screenshots, real click/hover/keyboard/resize, Windows output, metadata lookup and actual disk behavior: **NOT RUN**. Do not label stable or say tests passed without explicit evidence. Write exact results and known issues after implementation and unit tests.

**Follow-up:** Implement source, add regression tests, verify GitHub file contents, run on user's Windows files, compare UI against MCC 0.5.18, and address any warnings before stable status.
