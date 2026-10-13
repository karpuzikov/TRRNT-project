# Music Library Organizer

**Version: 0.3.0 - Under construction ⚠️**

**[Music Library Organizer v0.3.0.pyw](../Music%20Library%20Organizer%20v0.3.0.pyw)**

Standalone Windows pywebview/Edge WebView2 GUI with the compact dark theme of Music Collection Cleaner (MCC). MCC and other tools remain untouched.

## User workflow

1. Select the source collection folder (original audio and sidecars).
2. Choose a **new, empty**, separate and non-nested output directory. **Do not reuse the v0.2.0 Sorted destination**, because a revised release name may produce a second folder alongside the old incorrectly named copy.
3. Select **ALAC** or **FLAC**.
4. The app looks for an installed `achkgui.exe`. If it cannot be found automatically, set the **AudioChecker executable** path using Browse once. Without a real AudioChecker, WEB output can be copied but is **PARTIAL**, never falsely **DONE**.
5. Click **Organize**. There is no Scan/REVIEW/Select Ready/Apply gate. Inspect per-release final results: **DONE (verified requirements)**, **PARTIAL (exported, some required work not verified)**, **SKIPPED (output already present)**, **FAILED (cannot safely process)**.

All input files remain unchanged. It stages output per release, hashes copied files or verifies decoded PCM for transcodes, atomically commits each release and keeps a SHA-256 Undo journal. Errors are isolated per release. An interrupted pending commit requires reconciliation rather than an unsafe overwrite.

## Changes since v0.2.0

- Added MusicBrainz WS2 lookups for actual releases: lookup by exact barcode or by artist + release title; verify digital/CD medium, retrieve the concrete selected release's label catalog number, barcode, release-group initial worldwide date and primary release type. Handles rate limits, retries, cache and network failures conservatively.
- Ordinary Singles and EPs get standardized ` - Single` / ` - EP` suffixes. The plural `(Remixes)` / `(The Remixes)` exception never combines with EP/Single. One-track WEB releases with no type information use a Single heuristic, which must be checked against metadata in later parity testing. Singular `(Remix) - Single` is retained.
- For WEB, prefer MusicBrainz authentic catalog numbers over barcodes; label empty/ambiguous remains an unresolved data gap. **Beatport/BPTopTracker lookup is still not implemented**, so digital barcode fallback may remain PARTIAL rather than falsely DONE.
- For physical CD editions, uses catalog number (or barcode fallback) when matching MusicBrainz edition unambiguously to rename existing CUE/rip LOG. Physical-country data remains explicitly provenance-flagged: MusicBrainz release territory is a *fallback*, not proof of pressing/manufacturing. Discogs verification and full DiscID/TOC release selection are still to be implemented; CD exports are PARTIAL absent verified pressing provenance.
- Supports new naming of legacy release folders such as `Inna - YO 2019` when the actual edition resolves. Example verified from MusicBrainz: **YO Romanian CD, catalog `GR18186`, release-group initial date `2019-05-31`, 11 tracks**. With country from the documented RO release event as *territory fallback*, expected proposed name is `2019-05-31 - YO [RO - GR18186]`; sidecars `GR18186.cue` and `GR18186.log`. **Match must succeed on the actual local source; this is not a guarantee of physical pressing country.**
- Runs a genuine AudioChecker GUI when `achkgui.exe` is present. For FLAC, checks output FLAC directly. For ALAC, produces **temporary lossless, PCM-verified FLAC** files because classic AudioChecker does not accept ALAC/M4A, runs the authentic checker on them and copies the genuine report to `audiochecker.log`. The run report documents that FLAC intermediary provenance. Never fabricates a report.
- Detects duplicate input tracks converging on a single output path: skips an extra copy **only when decoded PCM hashes match**. Distinct content collision still fails that release to prevent losing recordings.
- CUE image splitting now accounts for `INDEX 00` and first-track HTOA where represented safely. It can remap an outdated track-by-track WAV CUE reference to an unambiguous numbered current audio file with zero-based indexes. Complex or ambiguous layouts remain a per-release failure. No invented split points.
- Prevents an existing shorter title tag from stripping a richer original version descriptor in a filename.

## What is NOT yet complete

This remains **0.3.0 - Under construction ⚠️**. It does not yet guarantee full TRRNT compliance or 94/94 complete outputs:

- No automatic Discogs pressing/manufacturing evidence research, MusicBrainz CD DiscID candidate-resolution UI, Beatport/BPTopTracker digital catalog fallback.
- No complete metadata/embedded cover-art parity audits, no source-independent title/artist-credit semantic comparison and no comprehensive title cases in the current version.
- Some complex CUE layouts and unverifiable source data remain blocked; correct error handling is preferable to silent corruption.
- AudioChecker depends on a real `achkgui.exe` program; auto-setup of this third-party executable is not implemented. Lack of executable/report produces PARTIAL status; no fake file created.
- No Windows GUI, Python syntax compilation, full regression suite or end-to-end 94-release INNA run has been performed in this development environment. **Only embedded JavaScript syntax has been validated (V8). All Windows integration/visual/actual file-change tests: NOT RUN.**

Use a **small disposable copy with an empty output root** for the next test, especially `Inna - YO 2019`, one ordinary digital Single, one duplicated track and a CD with INDEX 00. Check the resulting filenames, `audiochecker.log`, run report and Undo before running the complete collection.

Data isolation: `Documents\Karpuzikov Tools\Music Library Organizer\` (settings, AudioChecker path, release metadata cache, last-run JSON report, logs, dependencies and Undo journals). No GitHub Actions changes.

See [CONTINUITY.md](CONTINUITY.md), [UI_UX_CONTROL_CONTRACT.md](UI_UX_CONTROL_CONTRACT.md), [INNA v0.2.0 audit](AUDIT_INNA_2026-10-13.md), [shared naming rules](../../RULES.md), and [cross-tool glossary](../../MUSIC_GLOSSARY.md).
