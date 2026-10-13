# Music Library Organizer

**Current: v0.3.1 - Under construction ⚠️**

**[Download Music Library Organizer v0.3.1.pyw](../Music%20Library%20Organizer%20v0.3.1.pyw)**

Standalone MCC-style Windows WebView2 organizer. It does **not** modify MCC or other tools and stores its settings, logs, dependencies, cached MusicBrainz records, output reports and Undo journals in `Documents\Karpuzikov Tools\Music Library Organizer\`.

## One-click UI

1. Choose **Input collection**.
2. The **Organized output** field automatically proposes `<Input collection> - Sorted`. It remains editable; the input and output must be separate, non-nested directories. Use a **new empty output** for a meaningful regression test.
3. Choose **ALAC / FLAC** and separately set **Encoding workers** and **AudioChecker workers** (both 1 through detected CPU logical-core count; default 4 where supported). Choices are persisted only for this program.
4. Click **Organize** once. No mandatory Scan/REVIEW/Apply. Real errors are reported for individual releases, and the remaining releases continue. Output folder icon beside the path opens it; the duplicate "Open output" button has been removed.
5. Read the final per-release **DONE / PARTIAL / SKIPPED / FAILED** result and check Undo. PARTIAL means files were exported, but one or more mandatory rule checks or metadata sources were not verified; it does **not** mean fully organized.

**Source files are never overwritten or removed.** Separate output root, per-release staging, CUE reference repair where safe, PCM hash verification, conservative duplicate handling and SHA-256 Undo are retained.

## Uploaded multicore AudioChecker integration

The organizer directly uses the actual **Audio_Checker_UTF8_Multicore.zip** provided by the user, rather than asking for an `achkgui.exe` location. Put that ZIP **beside the downloaded `.pyw`** or in the normal Windows **Downloads** folder. Alternatively, a pre-extracted `Audio_Checker_UTF8_Multicore` folder can sit there. On first use it extracts **only the required FLAC decoder, auCDtect analyzer, manifests and DLL** to this program's own `dependencies\AudioChecker` directory. The supplied analyzer executable is actually named **`aucdtect.exe`**.

The AudioChecker report is generated from real decoded audio and auCDtect `-mS0` analysis, with the same results layout and checksum algorithm implemented by the user's `AudioChecker_Multicore.pyw`; no fake report is produced. For each WEB output audio subfolder, independent files are decoded/checked concurrently using **AudioChecker workers** and the actual results are written to `audiochecker.log`. If the ZIP or executable cannot be located or analysis fails, the release is marked **PARTIAL** rather than incorrectly DONE. This tool is not redistributed as binary assets in the GitHub repository.

## Metadata lookup and filename rules

**MusicBrainz barcode lookup** searches exact barcodes (allowing differing leading zeroes when comparing identity), then fetches the concrete release's labels/catalog number, media and release-group first date/type.

**MusicBrainz physical DiscID/TOC lookup**, based on the project's [CUE Corrector - MusicBrainz DiscID 1.3.1](../CUE%20Corrector%20-%20MusicBrainz%20DiscID%201.3.1.pyw), uses the real EAC rip-log TOC when available or CUE + sample-exact lossless track lengths. DiscID is computed from CD sector offsets (including standard 150-frame lead-in), not guessed from track filenames. It sends the DiscID and TOC to MusicBrainz, collects candidate *release IDs*, then fetches the concrete release. Where both DiscID and barcode are known, it requires the exact intersection. If several distinct physical editions remain, **it does not silently select one**; it preserves the source identity and records the ambiguity in the log.

Confirmed metadata can update a release folder's worldwide initial date, release-type ` - Single` or ` - EP` suffix, and catalog-number-first bracket identifier. For actual matching physical CD editions, existing CUE/rip LOG basename uses the catalog number, barcode fallback, while the documented release country is only a **territory fallback**, not independently verified pressing country. WEB gets no physical country prefix. A release may remain PARTIAL if manufacturing-country evidence, Beatport/BPTopTracker digital catalog fallback or other strict project checks are missing.

Output individual audio tracks follow `NN Track Title.m4a` for ALAC or `NN Track Title.flac` for FLAC (number padded, exactly one space, no artist prefix). Preserve featured artists, remixes and version descriptors. Ordinary FLAC/ALAC inputs transcode or copy with verification; CUE image splitting and limited legacy CUE repair preserve indexes and pregaps where supported.

## Remaining limitations / testing

**Status: Under construction ⚠️.** Actual Windows/WebView2 acceptance and whole-collection output have **not** been completed. Source has been checked for expected controls and embedded JavaScript syntax; bundled AudioChecker Python source/log format and ZIP structure were inspected separately. Full Python regression suite against v0.3.1, execution of the embedded Windows binaries, actual AudioChecker/ALAC round-trips and real MusicBrainz HTTP lookups on user files are **not yet verified**.

Remaining: automatic Discogs pressing/manufacturing evidence; Beatport/BPTopTracker catalog fallback; user-controlled resolution of ambiguous DiscID matches (currently preserved/flagged without stopping); deeper tag/artwork parity; unusual CUE layouts; performance validation on mixed HDD/SSD input. No "stable" or fully-compliant claim until verified.

**Next test:** One disposable WEB single + one known CD with CUE/LOG + valid AudioChecker ZIP beside `.pyw`, **new empty destination**, then inspect filename, catalog, `audiochecker.log`, workers, Undo and the run log.

See [current continuity](CONTINUITY.md), [UI control contract](UI_UX_CONTROL_CONTRACT.md), [prior INNA audit](AUDIT_INNA_2026-10-13.md), project [rules](../../RULES.md) and [music glossary](../../MUSIC_GLOSSARY.md).
