# Discography Torrent Project

Tools for cleaning and organizing an artist's discography and preparing it for a torrent release. Windows, Python 3.

**[Download the latest release](https://github.com/karpuzikov/TRRNT-project/releases/latest)** · [Version history](version-history/)

## Current tools

| Tool | Version | What it does |
| --- | --- | --- |
| [Music Library Organizer](tools/Music%20Library%20Organizer%20v0.03.23.pyw) ([guide](tools/music-library-organizer/README.md)) | 0.3.23 - Under construction ⚠️ | Organizes releases: edition matching, CD/WEB naming, MusicBrainz identity checks, ALAC/FLAC, artwork lookup, audio checking, Undo. Native Windows acceptance pending. |
| [Duplicate Edition Analyzer](tools/Duplicate%20Edition%20Analyzer%20v0.22.13.pyw) | 0.22.13 - Under construction ⚠️ | Finds duplicate recordings and redundant editions by acoustic fingerprint; interactive Release Map. Source only; the [standalone EXE](https://github.com/karpuzikov/userscripts/releases/download/duplicate-edition-analyzer-v0.22.12/Duplicate.Edition.Analyzer.0.22.12.exe) is still v0.22.12. |
| [ALAC Conversion Automation](tools/ALAC%20Conversion%20Automation%20v0.02.01.pyw) | 0.2.1 - Under construction ⚠️ | Lossless -> ALAC **in place**. Single files: the source is **deleted** after the decoded audio matches. CUE releases: the source is **kept**. |
| [AudioChecker Automation](tools/AudioChecker%20Automation%20v0.01.pyw) | 0.1 | AudioChecker batch automation. |
| [CUE Corrector - MusicBrainz DiscID](tools/CUE%20Corrector%20-%20MusicBrainz%20DiscID%201.03.01.pyw) | 1.3.1 | MusicBrainz DiscID/CUE workflow. |
| [Folder Structure Scanner](tools/Folder%20Structure%20Scanner.pyw) | - | Recursive folder structure report. |

Related: [Music Collection Cleaner (MCC)](https://github.com/karpuzikov/MCC) lives in its own repository.

## Workflow assets

- `workflow/CUE LOG 4.01.pyw`
- `workflow/Photoshop script 500png 600jpg 1.04.jsx`
- `workflow/ALAC + header.mte`
- `workflow/My standard.mta` (Mp3tag action group)

## Repository layout

- `tools/` - only the **latest** version of each tool, plus its tests and guides.
- `version-history/` - every previous version. Old versions never stay in `tools/` or the repository root.
- `workflow/` - assets for third-party applications (Mp3tag, Photoshop, CUETools).
- `PROJECT.md` - workflow, implementation status and integration plan. `RULES.md` - duplicate/edition/source-selection and naming rules. `CONTINUITY.md` - development status. `MIGRATION.md` - 2026-10-13 migration record.

Third-party applications (AudioChecker, CUETools, refalac, FFmpeg, Picard, Mp3tag, xrecode2, Photoshop, qBittorrent and others) are dependencies and are not included.

## CD rip log scoring

Duplicate Edition Analyzer uses [hey-bro-check-log](https://github.com/ligh7s/hey-bro-check-log) v1.3.2 (Apache-2.0), pinned to commit `d3192ad2764f2682cffce4db2abc419f8ac68c69`, as an auto-installed runtime dependency for supported EAC/XLD logs. Log score is only a quality tie-break between otherwise exact-equivalent CD rips; it never creates duplicate identity or replaces a different edition.

## Release Map

After every analysis, Duplicate Edition Analyzer opens a release-only network before filesystem changes. The canvas contains releases only: click a connected release node to move through the dependency web. Details are shown separately below the map with a draggable divider so the track list can take as much vertical space as needed.

The selected release shows its true retention/removal reason, source, selectable path, Copy path, Open folder, and a full high-contrast track list. Yellow tracks are unique to that retained release; red tracks warn that a manual release removal would leave the recording uncovered. If same/similar titles are intentionally kept as separate recordings, the track status explains the visible reason when available (artist/featured credit, ISRC, length) and explicitly says when Chromaprint is what differs.

During a live analysis, click a track and use **Skip track** to remove that recording group from optimization. The choice is saved in `Documents\Karpuzikov Tools\Duplicate Edition Analyzer\state\manual_track_skips.json` and is reapplied automatically in future analyses using the recording's saved fingerprint/identifier evidence. **Restore track** removes that saved preference. Release-level manual removal remains reversible and re-runs only the collection optimizer against already-computed audio groups.

The Release Map is a normal resizable/maximizable Windows top-level window (no modal/transient grab), so it can be positioned with FancyZones. **Apply plan**, **Analyze again**, and **Close** are always explicit live-analysis actions. The last map snapshot remains available read-only from the main window after the in-memory analysis context is gone.

## Running the tests

The unit tests need no extra packages and no display (the analyzer tests use a stand-in `tkinter`). Run them from the repository root:

```
python tools/music-library-organizer/test_organizer.py
python tools/music-library-organizer/test_naming_table.py
python tools/duplicate-edition-analyzer/test_dea_core.py
```

They do not replace Windows GUI, real-audio or AudioChecker/`refalac64` acceptance runs.
