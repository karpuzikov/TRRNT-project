# Duplicate Edition Analyzer - Continuity Handoff

Last updated: 2026-10-13
Status: Under construction ⚠️

## Music Library Organizer v0.3.4 - recover desktop bridge and black window - 2026-10-13

- User reports v0.3.3 starts with "Desktop connection failed", hangs; force-close/reopen intermittently displays blank black WebView2 windows. Source audit: JS bridge probe gives up after only 6 sec, never subscribes to `pywebviewready`; periodic refresh overlaps while bridge is slow; exposing whole `Organizer` to js_api may trigger nested reflection into a WebView2 native window/locks.
- New `tools/Music Library Organizer v0.3.4.pyw`: dedicated small `DesktopBridge` API facade (no publicly exposed window/events), JS subscription to `pywebviewready`, single-flight timed status polls with retry/backoff and Retry connection button only during disconnection. Real startup status and disabled controls until Python responds, then full restoration including source/destination folder and log buttons.
- Black-window preventive measures: unique per-process app-isolated WebView2 profile; attempt cleanup stale profile directories (never other apps), single-instance Windows mutex, `webview-startup.log` under program-only Documents folder; recorded renderer shown/loaded/closed events and startup errors. Existing codec exclusivity, source safety and MCC-styled UI preserved.
- V8 JS syntax check and V8 mocked late bridge/controls interaction PASS, Python regression source added but **NOT RUN**; actual Win11 WebView2/black-screen tests remain NOT RUN. Product status stays `0.3.4 - Under construction ⚠️`. Next: test multiple normal starts and one forced-close/reopen with disposable source. If failure ask for app's `webview-startup.log`, `startup-error.log`, `activity.log`.

## Music Library Organizer v0.3.3 - exclusive native ALAC/FLAC encoders - 2026-10-13

- User mandates **ALAC with `refalac64.exe` only** (source nu774/qaac), **FLAC with Xiph's official `flac.exe` only** (source ftp.osuosl.org/pub/xiph/releases/flac). FFmpeg `-c:a alac`/`-c:a flac` was expressly rejected. Strict rule committed into root `RULES.md`, overriding earlier softer language.
- New single-file source `tools/Music Library Organizer v0.3.3.pyw`: official encoder bootstrap into product-specific directories; native refalac64 or Xiph flac --best --verify, decoded-only PCM WAV with no rate/depth downgrade, full decoded PCM comparison, basic tags and cover-art preservation, clear failed release if encoder missing/precision unverified. Applies to normal tracks and CUE splits; same-codec binary copies allowed. Test source `tools/music-library-organizer/test_organizer.py` extended with backend invariants. No MCC/Actions changes.
- Independent local Xiph `flac --best --verify` on a synthetic WAV PASS with matching decoded PCM SHA-256. **Actual full Python source tests/Windows native refalac/bootstrap/GUI real-file processing NOT RUN.** Current v0.3.3 - Under construction ⚠️. Do not label stable until Windows acceptance.

## Music Library Organizer v0.3.2 - remove WEB AudioChecker from physical CDs - 2026-10-13

- User found `audiochecker.log` in organized physical CD `2005-07-01 - New HOT Edition [RU - CDLREC 5592010 CD]`. Cause: `_export_release` copied existing AudioChecker sidecars to CDs even though `_organize` already skipped the checker run for recognized CDs.
- v0.3.2 excludes existing `audiochecker.log` from **all** copied sources; WEB gets only a new genuine report while CD gets **none**. Source files preserved. Physical recognition now accepts CUE **or** rip LOG without requiring both. Results subtitle removed. Added different-year CD edition year bracket suffix. GitHub regression tests added for CD sidecar prohibition and reissue-year behavior; Windows/full test execution NOT RUN.
- **Critical remaining issue:** MB release-group first dates 2005 for Hot and 2010 for I Am the Club Rocker contradict independent documented first album dates 2009/2011. v0.3.2 still uses the problematic release-group first date without verification, so those particular folder dates are unresolved. Another metadata validation pass is necessary; do not call this a compliant/stable build. Previously generated output is NOT changed/cleaned on rerun.

## Music Library Organizer v0.3.1 - UI, worker selectors, supplied checker and DiscID - 2026-10-13

- User requested: eliminate unnecessary checker executable path; reuse supplied `Audio_Checker_UTF8_Multicore.zip`; remove redundant **Open output** toolbar button (keep folder icon beside path); initialize output automatically to input ` - Sorted`; add independent Encoding and AudioChecker worker selectors; verify **both MusicBrainz barcode and DiscID** lookups.
- Committed new `tools/Music Library Organizer v0.3.1.pyw`. Uses ZIP autodiscovery/bounded extraction of known supplied executable/UTF8 manifests, concurrent auCDtect -mS0 analysis and compatible genuine `audiochecker.log` formatting; independent bounded encoding/checking worker pools; output path suggestion. Exact DiscID algorithm and EAC-log / CUE-exact fallback follow project's existing CUE Corrector. Requires barcode intersection when both available, does not silently choose among ambiguous physical editions. Original files preserved.
- Enhanced `tools/music-library-organizer/test_organizer.py` with new checks for worker counts, bundle setup, discid/barcode cross-check, ambiguous candidates and report checksum. Tests are committed but not run in target Windows runtime. Separate Linux inspection verified the user-supplied checker format and actual ZIP contents. Inlined UI JS syntax PASS. Product remains **0.3.1 - Under construction ⚠️**, not stable. No Actions workflows altered.

## Music Library Organizer v0.3.0 - rules audit and repairs - 2026-10-13

- INNA v0.2.0 log: 94 discovered, 80 reported DONE, 14 FAILED. In reality v0.2.0 failed required naming/catalog/checker work; 80 labeled DONE was noncompliant. Eight WEB collisions, six CD CUE errors. Details in product `AUDIT_INNA_2026-10-13.md`.
- New version `tools/Music Library Organizer v0.3.0.pyw`: MusicBrainz release-group/labels/catalog/format research with rate limiting/cache, Single/EP/remix suffix normalization, folder name rewriting, source-of-truth CUE/LOG catalog basename, checker executable selector/invocation and authentic log on FLAC or lossless temporary FLAC from ALAC, per-release PARTIAL status for incomplete work, duplicate PCM hash dedup, historical split WAV CUE track remapping, INDEX00/HTOA image split. Does not modify other apps or GitHub Actions.
- Specific external MusicBrainz example: INNA YO Romanian CD has catalog GR18186, earliest worldwide date 2019-05-31 and RO release territory (not verified pressing); expected when concrete matching succeeds `2019-05-31 - YO [RO - GR18186]` and `GR18186.cue`/`GR18186.log`.
- **NOT RUN**: Actual Python AST/real regression tests, Windows GUI, AudioChecker executable, local INNA CD sample and entire 94-release run, screenshot acceptance. V8 inlined JS syntax only PASS. Full Discogs manufacturing, MusicBrainz DiscID candidate resolution and Beatport/BPTopTracker digital catalog fallbacks not yet implemented. Remains **0.3.0 - Under construction ⚠️**, not a claimed complete application. Next tests must use a new EMPTY output destination to avoid existing v0.2 folder collisions.

## Music Library Organizer v0.2.0 - eliminate review gate - 2026-10-13

- User rejected the 94 release / 0 ready / 94 REVIEW blocker and instructed: **take the input folder and output organized files**. The organizer is no longer a review-management program.
- New standalone `tools/Music Library Organizer v0.2.0.pyw` with **Input / Output / ALAC-or-FLAC -> Organize** workflow. No Select Ready, Scan-only, or separate Apply; exceptions do not prevent other releases from processing. Missing WEB `audiochecker.log` becomes a warning. Existing input files are not modified, output collisions are skipped, and genuine processing errors are logged release by release. Previous Undo manifests archived; current SHA-256 Undo retained.
- Corrected short title-tag overwrite of file descriptors (e.g. `OK (Extended Version)`) by preferring a richer existing filename where a short tag would discard a known version. State/result counters and last-run JSON report under isolated program data.
- Current GUI based on MCC dark WebView2 patterns. UXDT reference consulted, one-click controls documented in product `UI_UX_CONTROL_CONTRACT.md`. JS parse check PASS (syntax only). Windows GUI, Python compile/regression tests, audio round-trip, screenshot and filesystem tests: **NOT RUN**. Do not mark stable.
- Regression tests updated in `tools/music-library-organizer/test_organizer.py` for missing-log nonblocking, per-release failures continuing, and preserved version title. External catalog/date/country research remains unimplemented; auto processing conservatively retains unverified release folder name rather than fabricating provenance.

## Music Library Organizer v0.1.2 prototype - 2026-10-13

- User requested a **new standalone file/folder organization program using MCC's UI as the base**, potentially to be merged into MCC later. Built isolated `tools/Music Library Organizer v0.1.2.pyw`, using MCC's pywebview / WebView2 dark UI visual tokens and independently scoped app data. Earlier v0.1.0 and v0.1.1 snapshots remain on `main`.
- Added `tools/music-library-organizer/CONTINUITY.md`, `UI_UX_CONTROL_CONTRACT.md`, `README.md`, and `test_organizer.py`. The new app's continuity is authoritative for its detailed source behavior, test results, UI controls, known bugs, limitations and future work. Root project RULES, MUSIC_GLOSSARY, global SOFTWARE/UI_UX rules also apply.
- Features: source + independent output roots, persisted ALAC/FLAC, scan, ready/review decisions, full path mapping Details, safe copy/export with verification and per-release staging, SHA-256 recorded Undo, limited valid CUE image splitting. Unsupported source metadata/INDEX 00/HTOA, unverified release names, collisions, missing WEB audiochecker.log are flagged. No source-file deletion.
- **NOT IMPLEMENTED/VERIFIED:** external MusicBrainz DiscID/Discogs/Beatport lookups, earliest dates and manufacturing proof, verified catalog fallback, refalac, full metadata/artwork parity, complex CUE indexes, Windows WebView2 launch/testing/screenshots. No physical exe or workflow modifications.
- Unit and FFmpeg test **source committed but NOT EXECUTED**. JavaScript extracted from source syntax-parsed via V8 Function - PASS syntax only. Product remains **0.1.2 - Under construction ⚠️**. Next: run Python AST/unittest/FFmpeg on real OS then Windows screenshots and disposable-root Apply/Undo regression, fix all discrepancies before declaring success.
- MCC repository/application was not modified by this new product work. Future MCC merging is optional and requires later user request.

## CUE album-image splitting and ALAC/FLAC output format - 2026-10-13

- User requires **whole-album audio image files to be split into individual tracks**. The conversion script must use the associated valid CUE for track timing and preserve pregaps, indexes, HTOA, track count and complete decoded audio; no invented split times if missing/invalid CUE.
- New user-selectable **Output format**: **ALAC (.m4a)** or **FLAC (.flac)**. Persist setting and apply to image splits and existing track-based source conversion. Only lossless output formats supported now; lossy choices may be implemented later, not now.
- Resulting tracks use the same mandatory zero-padded number + one space + complete title naming: `01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a` or corresponding `.flac`. Existing album-level original CUE/LOG metadata must be preserved; generated track-based CUE must correctly reference new files/indexes.
- Verify complete source-to-output sample coverage, exact boundaries/decoding and relevant PCM parity before disposal. Preserve source image/CUE/LOG after failures; flag missing cue, unsupported codec, bad gaps/HTOA, duplicate basenames and incomplete output instead of guessing.
- Previous ALAC-only conversion design is **superseded** for the planned conversion-script update. Existing DEA/MCC virtual-image track handling is **unchanged** and the new splitter does not run during analysis. ALAC folder conventions do not automatically authorize FLAC output inside `Artist ALAC`; FLAC output hierarchy is pending clarification.
- Updated GitHub planning/rules/glossary/continuity only. No source implementation or Windows execution/verification has been completed for this feature.

## CD CUE/LOG and WEB AudioChecker naming rules - 2026-10-13

- User's supporting-file rule: Physical CD releases **usually**, but not invariably, contain a real rip LOG and a CUE. Name those existing supporting files using the exact edition's **catalog number** as the common basename: `<Catalog Number>.log`, `<Catalog Number>.cue`; **barcode fallback** when no catalog number exists. If neither is available, flag for review, do not invent names. DiscID and Discogs edition validation remain authoritative.
- WEB/digital releases **should have `audiochecker.log`**, an actual AudioChecker report, not a physical CD rip LOG. When absent, require genuine AudioChecker analysis; do not synthesize an empty/fake file or treat it as CD evidence.
- Existing CD rip LOG/CUE may be missing, which is an absence to report, not a reason to reclassify media or fabricate documentation. Preserve separate multi-disc data, CUE references, file contents, and rename preview/Undo. Block filename collisions rather than overwrite.
- Updated `RULES.md`, `PROJECT.md`, and `CONTINUITY.md` only. No software changes or actual Windows filesystem testing.

## Main ALAC audio file naming rule - 2026-10-13

- User explicitly specified the main filename structure: `01 Red Lips (ft. Sam Bruno) [Skrillex Remix]` = `01` zero-padded track number, followed by **exactly one space**, followed by the complete standardized track title. Full ALAC filename: `01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a`.
- Mandatory physical filename grammar `NN Track Title.m4a` (two digits minimum, zero-padded). Do **not** write `01. Title`, `01 - Title`, `01_Title`, double spaces, or include the `Artist -` prefix. A separator prohibition does not apply to legitimate punctuation inside the title, e.g. `ft.`.
- The user has now resolved the artist-prefix/physical filename template question left open in the previous checkpoint. Keep earlier MCC semantic parsing, remixer vs original-feature ordering, `()` -> `[]` -> `{}` title components, and Picard artist-credit standardization. Disc reset policy and exceptional numbering cases remain undecided; do not invent rules.
- Documentation only: updated `RULES.md`, `PROJECT.md`, `MUSIC_GLOSSARY.md`, and `CONTINUITY.md`. No organizer code, audio content, CUE references, or Windows tests were changed.

## Track title / feature provenance / positional delimiters - 2026-10-13

- User specifies two distinguishable cases using MCC's glossary: `The Weeknd - Reminder (Remix) [ft. A$AP Rocky & Young Thug]` = feature added by remix, **remix before feature**; `GTA - Red Lips (ft. Sam Bruno) [Skrillex Remix]` = feature already on original, **feature before remix**. Verify original feature provenance using original recording credits; don't infer it from punctuation.
- All separate parenthesized track-title components follow **first `()`, second `[]`, third `{}`**, based on final sequence and irrespective of role. Original title parentheticals count as first. Preserve descriptors, remixer names and featured credits; render `feat.` as `ft.`.
- Exact artist/track example: `Tiesto vs. Diplo - C'Mon (Catch 'Em By Surprise) (feat. Busta Rhymes) (Extended Club Mix)` -> `Tiesto & Diplo - C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}`.
- Checked canonical Picard script `karpuzikov/userscripts/picard-tools/scripts/Format_Multiple_Artists.txt` and `Move_Featured_Artists_to_Title.txt`. Former joins already-separated `%artists%` and `%albumartists%`: 2 `A & B`, 3+ `A, B & C`; `vs.` conversion needs independently resolved artist credits, not global text replacement. Feature-moving script handles tag positioning but not complete positional `() [] {}` filename grammar.
- Central `MUSIC_GLOSSARY.md` updated, mirrored as normative references in `RULES.md` and `PROJECT.md`; no MCC/other application logic changed. The full album-file template, especially whether artist is included in physical `NN Track Title.m4a`, still needs a specific instruction.

## Shared MCC/TRRNT semantic glossary checkpoint - 2026-10-13

- User corrected track-file terminology: `01 Plain Jane (Remix) [ft. Nicki Minaj].m4a` is **a Remix with featured artist Nicki Minaj**, not simply an alternative featured-artist spelling. The track carries independent recording category/featured-credit attributes.
- Read current MCC `RULES.md` and v0.5.18 source: MCC expressly uses `Girlfriend (Dr. Luke Remix) [ft. Lil Mama]` as a separate Remix recording root and distinguishes remixer credits from featured artists. A feature genuinely **added by the remix** is an extra criterion not established by `[ft.]` alone; MCC's `canonical_featured_title()` canonicalizes internal comparison keys, not a file-renaming standard.
- Created central `MUSIC_GLOSSARY.md` in `TRRNT-project` as a single cross-tool semantic reference; linked from both TRRNT documentation and MCC's `RULES.md`. No app logic, audio files, filename normalizer, or Windows tests were changed. This glossary does not finalize on-disk feature punctuation.

## Latest remix title correction - 2026-10-13

- User rejected any plural remix-collection folder name combining `(Remixes)` or `(The Remixes)` with an EP or Single suffix. The plural collection exception **supersedes** the previous broad requirement to retain ` - EP` / ` - Single` on every title where that release type is mentioned.
- **Exact required mappings:** `Plain Jane REMIX - Single` -> `Plain Jane (Remix) - Single` (singular individual remix retains true Single suffix); `Title - The Remixes EP` -> `Title (The Remixes)` (plural remix collection drops EP designation). The same prohibition applies to `(Remixes)` / `(The Remixes)` plus ` - Single`, regardless of track count.
- Keep the authentic plural remix descriptor wording in parentheses if it actually exists in the source title; never fabricate `(Remixes)` using track count. Ordinary (non-plural-collection) singles/EPs still normalize their release-type designation to final ` - Single` / ` - EP` suffixes. This rule is only for release folder naming.
- Updated `RULES.md`, `PROJECT.md`, and `CONTINUITY.md`; no executable source, UI, build, or Windows test was changed/performed.

## Latest folder-organization clarifications - 2026-10-13

- Earliest **worldwide initial release date** regardless of country, format, edition; use maximum known date precision (`YYYY-MM-DD`, `YYYY-MM`, or `YYYY`).
- CD physical edition country slot: Discogs/physical disc/case pressing and manufacturing evidence first (`Made in`, `Manufactured in`, `Printed in`, `Pressed By`). **NEW explicit exception superseding earlier absolute ban:** when pressing country is unknowable, fall back to MusicBrainz/Discogs **release country or region**, and record it as **fallback**, not verified manufacturing.
- Catalog number over barcode, `none` only when both absent. Physical CD lookup requirement: MusicBrainz **DiscID/TOC -> candidate concrete release -> catalog number**, with Discogs fallback, explicit choice on ambiguity. Digital catalog sources include MusicBrainz, Beatport, BPTopTracker. **No store IDs**.
- Folder hierarchy: all releases directly inside `Artist Name ALAC` or `Brand Name ALAC`; release-type folders `Albums`, `Singles`, `EPs` are deprecated. Preserve multi-disc subfolders belonging to a release.
- Ordinary single and EP folder suffixes required: ` - Single` and ` - EP`. **Latest remix correction:** preserve the original release title, including its exact remix descriptor; if the original title actually contains standalone `Remixes`, `The Remixes`, or similar, put that descriptor in parentheses (`Song - The Remixes` -> `Song (The Remixes)`). Never invent `(Remixes)` for larger remix releases. Remix-focused releases up to 3 tracks may use ` - Single` where appropriate; more than 3 tracks does not mandate a fabricated title or suffix.
- Retain prior exact ` - ` separator rule and the physical-CD reissue year suffix only if the reissue year differs from the earliest worldwide initial release year.
- Applied rule consolidation to `RULES.md` and `PROJECT.md`. **Only documentation updated**; standalone DiscID-based folder organizer is not yet implemented or tested.

## File organization naming clarification - 2026-10-13

- The user explicitly corrected the meaning of country codes inside ALAC physical-CD release folder names: `[US - ...]` indicates **country of manufacture/pressing of the physical CD**, NOT MusicBrainz release country or sales/distribution region. `EU` is not a country. For physical CDs with unverified pressing location, flag for review rather than guessing or relabeling the physical CD as digital.
- User's second explicit rule: a country-less identifier such as `2018-02-16 - El Pintor Bonus Tracks [OLE13186]` means a **digital/WEB release** in this naming convention, so manufacturing/pressing country is **N/A**, not unknown. `[Identifier]` = digital; `[Verified Pressing Country - Catalog Number]` = physical CD.
- User defined the exact physical-CD release folder grammar: `YYYY-MM-DD - Release Title [CD Pressing Country - Catalog Number]`; `YYYY-MM-DD` is the **initial release date**. Literal ` - ` separates date/title and bracket fields. `[...]` encloses physical release data. Include a country **only for physical discs**; it identifies manufacture/pressing, not release territory.
- For physical reissues use `YYYY-MM-DD - Release Title [CD Pressing Country - Catalog Number - Reissue Year]` **only if** the four-digit reissue year differs from the initial release year. Keep the initial release date at the front even for a reissue. User examples: `2007-07-09 - Our Love to Admire [JP - TOCP-66683]`; `2002-08-19 - Turn on the Bright Lights (The Tenth Anniversary Edition) [US - OLE 993-2 - 2012]`.
- Updated `RULES.md` and `PROJECT.md` with exact format, separators and reissue-year condition. No application logic, UI, build, or workflow was modified; organization implementation remains planned.

- User provided `!torrent_Structure.txt` as an example tree, requesting analysis limited to ALAC folders. A dedicated file organization/naming stage is planned after ALAC conversion and tag normalization, before artwork preparation, preserving CUE/LOG references and offering safe preview/Undo.
- Updated `RULES.md` (canonical naming rule) and `PROJECT.md` (planned stage). This was documentation only: no software source modifications, build, Windows UI tests, or release-status changes.

## Chat closeout checkpoint - 2026-10-13

- **User's latest request:** move **all Discography Torrent Project-owned material** from `karpuzikov/userscripts` into independent `karpuzikov/TRRNT-project`; user then issued the project-wide handoff command **"this chat is done"**. Continue development in the new repository, not the former `userscripts/audio-tools/discography-torrent-project/` directory.
- **Migration verification on GitHub:** target `main` contains 20 former project files rooted at `tools/`, `workflow/`, and four project docs. At the initial migration, **17 Git blobs were identical**; `CONTINUITY.md`, `PROJECT.md`, and `README.md` were updated for relocation. This closeout subsequently updated project `RULES.md` and `CONTINUITY.md`, leaving **16 of the 20 original blobs byte-identical** in current `main`. SHA manifest and preserved source commit: `MIGRATION.md`. All four migrated branches (`main`, `dea-lightweight-modern`, `dea-lightweight-ascii-ui-test`, `dea-native-light-test`) confirmed as GitHub refs. The original directory and original DEA-specific workflow YAMLs no longer appear in `userscripts/main`. Two workflow files exist byte-identically in new `archived-workflows/`, **not** `.github/workflows/` (no active Actions workflows).
- **Rule sync:** `SOFTWARE_RULES.md` is byte-identical to the canonical `userscripts` copy; the new repository also contains full `UI_UX_RULES.md` and `UI_UX_ACCEPTANCE.md`. Always reverify global mirrors prior to editing.
- **Status:** Latest reference DEA **0.22.12 - Under construction ⚠️**. Lightweight development on `dea-lightweight-modern` is also **Under construction ⚠️**, with versions 0.4.1-0.4.9 preserved as branch-specific sources. No Windows executable testing, app build, Copy/Move/Undo, or new GitHub Actions execution was performed as part of the migration. The move is a source/branch-snapshot migration, not a claim of software parity/stability.
- **Intentional non-moves:** Independent `RuTracker Digital Release Linker`, Picard/MusicBrainz, qBittorrent backups, and MCC stay in their separate owning locations. Legacy DEA EXE release assets and issue/PR/commit history remain accessible in `userscripts`, but Git ancestry is **not** copied into `TRRNT-project`.
- **Next steps:** Open new conversation, read root `SOFTWARE_RULES.md`, `UI_UX_RULES.md`, `UI_UX_ACCEPTANCE.md`, `RULES.md`, `PROJECT.md`, `CONTINUITY.md`, and `MIGRATION.md` from `karpuzikov/TRRNT-project`. Choose the appropriate development branch; before updating code, verify version/functional parity against `tools/Duplicate Edition Analyzer v0.22.12.pyw`. Inspect and repair outdated live references to old paths as needed without rewriting historical links. Do not revive the rejected native prototype, silently update Actions, or claim Windows acceptance without real tests.
 
## Repository migration checkpoint - 2026-10-13

- **Canonical GitHub repository:** `karpuzikov/TRRNT-project`; regular production/reference source remains `tools/Duplicate Edition Analyzer v0.22.12.pyw` on `main`, at status `0.22.12 - Under construction ⚠️` (not Windows-verified in this migration).
- On `main`, the full 20-file `audio-tools/discography-torrent-project/` source subtree from `karpuzikov/userscripts` was moved to repository root. `README.md`, `PROJECT.md` and this file had their canonical path text updated. At the initial migration, the other 17 source/project files (including `RULES.md`) and two archived workflows were byte-identical. The subsequent handoff updated `RULES.md` with the new repository-ownership rule; **16 of the 20 original project blobs are currently byte-identical**. Shared canonical SOFTWARE/UI UX rules and original MIT LICENSE are also mirrored. See `MIGRATION.md` for source Git blob IDs.
- Three independent development branches with historically distinct sources and rules were reconstructed: `dea-lightweight-modern` (v0.4.1-0.4.9), `dea-lightweight-ascii-ui-test` (v0.4.0), `dea-native-light-test` (rejected native prototype). These contain byte-identical branch-specific files, but their Git ancestry was not imported. Original historical branch heads/releases remain at the old repo.
- `userscripts/main` now contains only a redirect; its dedicated project directory and the two old DEA action workflow definitions have been removed after exact-hash verification. The new repo archives the workflows outside `.github/workflows/` so no new builds are triggered. **Do not reenable workflows without user request.**
- Known limitations of GitHub-to-GitHub file migration: Git commit ancestry, Issues/PR discussions, tags and compiled EXE Release assets were not moved; historical links to `userscripts/releases` remain valid. Independently maintained RuTracker Digital Release Linker, Picard, qBittorrent backup, and MCC are not relocated.
- Continue future changes only in `TRRNT-project`. Any proposed version/status changes require the normal GitHub rules and real Windows validation; no automatic stability promotion was made.

## Purpose

Duplicate Edition Analyzer (DEA) compares an existing discography with incoming/new releases, identifies exact acoustic duplicates and distinct versions, optimizes which releases should be retained, lets the user inspect and override the proposed plan in Release Map, and only changes files after explicit Apply. Undo must remain available after applying changes.

Migration checkpoint (2026-10-13): this file and its canonical source directory now live at the root of `karpuzikov/TRRNT-project`; historical `audio-tools/discography-torrent-project/` paths below identify the old location. Archived CI definitions are inactive under `archived-workflows/`. See `MIGRATION.md`.

This file is the authoritative handoff for DEA development. Read it before changing DEA. Update it whenever implementation state, versions, behavior, architecture, build flow, known issues, or next steps change.

## Canonical implementations and versions

- Production/reference implementation:
  - Source: `tools/Duplicate Edition Analyzer v0.22.12.pyw`
  - Version: `0.22.12 - Under construction ⚠️`
  - GitHub release asset: `Duplicate.Edition.Analyzer.0.22.12.exe`
  - This is the current functional specification for all replacement/rewrite work.
- Failed native prototype:
  - Branch: `dea-native-light-test`
  - Source root: `native-light/` (on the `dea-native-light-test` branch)
  - Last prototype version: `0.1.2`
  - User tested it and rejected it because it did not preserve 100% of regular DEA functionality.
  - Do not use the native prototype as the functional specification.
- New lightweight/fast/modern rewrite:
  - Must be a new implementation, separate from the failed native prototype.
  - Functional baseline is production/reference DEA 0.22.12.
  - No production feature may disappear merely to reduce EXE size or complexity.

## Non-negotiable rewrite contract

The new lightweight implementation must provide 100% user-visible and behavioral functionality of the regular DEA before it can replace or be presented as equivalent to the regular version.

Parity means:
- same analysis behavior and selection rules;
- same pre-analysis unusual-pattern review behavior;
- same Personal Picks behavior;
- same Save Remixes / Save Live behavior and exceptions;
- same acoustic duplicate identity behavior;
- same Release Map information, links/connections and interaction model;
- same track/release ignore, restore and Re-Analyze behavior;
- same Apply/Undo behavior;
- same search, track highlighting and cross-release navigation;
- same Versions/Remixes/Live family inspection;
- same replacement-source explanations;
- same result/change summary behavior;
- same filesystem path open controls;
- same logging, crash reporting and persistent settings behavior;
- same dependency behavior or a stricter compatible implementation;
- same versioned-download and GitHub status rules.

No feature may be removed, stubbed, hidden, or replaced with a simplified approximation unless the user explicitly approves that exact change.

## Why the old native prototype failed

The Win32/C++ prototype was small and fast, but it simplified the product and therefore violated the parity requirement. Specific missing/incomplete areas included:
- Release Map was a simplified list/details window rather than the full graph-style chronological release board.
- Missing/insufficient visual relationship links/connections on the map.
- Missing full cross-release track highlighting/navigation.
- Missing full Versions / Remixes / Live alternative-family panels and navigation.
- Missing full replacement-source and retained-copy detail behavior.
- Missing the complete regular Release Map search/navigation behavior.
- Missing or simplified result drawer / IF -> THEN re-analysis explanation flow.
- Missing the full regular UI/interaction richness and several parity behaviors around ignore/restore/re-analysis.
- Its engine also diverged from the production Python behavior instead of sharing the exact production logic.

Do not repeat this approach by implementing a smaller subset first and calling it equivalent.

## Target architecture for the new lightweight implementation

Preferred direction as of 2026-10-07:
- Preserve the production Python analysis/optimization logic as the behavioral authority rather than manually reimplementing the algorithms in C++.
- Remove the heavyweight PySide6 + Qt WebEngine runtime from the application UI.
- Use the Microsoft Edge WebView2 runtime for the modern HTML/CSS/JavaScript UI. Microsoft documents the Evergreen WebView2 runtime as preinstalled on Windows 11 and automatically updated; the program must still detect/runtime-check it and bootstrap/install it when unavailable.
- Keep the existing modern DEA HTML/CSS/JavaScript interaction design as the starting point, especially the full Release Map, and adapt the host bridge rather than rewriting/reducing the map.
- Keep long-running analysis off the UI thread.
- Prefer one user-facing EXE; runtime dependencies may live under the program-owned dependency directory if required by the global software rules.
- Do not bundle a 100-250+ MB fixed WebView2 runtime when the Evergreen runtime is available.
- Do not bundle Qt/PySide6 in the lightweight build.
- External audio helpers should follow the global dependency rules and live under the program-owned data/dependencies path when app-local copies are required.

Reason for this architecture: it preserves the exact proven Python decision engine while removing the largest UI/runtime weight source and retaining a modern web-rendered Release Map.

## Current production UI behavior that must be preserved

### Main window
- Modern dark UI.
- Existing discography path is optional.
- New / update releases path is required.
- Folder/open-location button immediately before every displayed path.
- Save Remixes.
- Save Live recordings.
- Personal Picks.
- Logging and logs-folder opener.
- Match label: Chromaprint (audio only).
- Current stage, progress percent/bar, processed/total count and elapsed time.
- Activity/log panel.
- Analyze.
- Undo last run.
- Release Map.
- Close.
- Analysis and file modification are separate; nothing moves until Apply in Release Map.

### Unusual track pattern review
- Only genuinely unknown/non-standard version-like patterns are reviewed.
- Ordinary known Remix/Live/Radio/Extended/Acoustic/Instrumental/Acapella/etc. structures do not appear as unusual patterns.
- Callout aliases are one semantic pattern:
  - Suggested Callout Hook
  - Call Out Hook
  - Suggested Call Out Research Hook
  - all resolve to the stable key `suggested callout` and one UI row, currently labeled `Callout hook`.
- Checked unusual pattern is kept and saved to Personal Picks.
- Unchecked pattern is skipped for that run.
- Existing Personal Picks remain checked.
- Check all / Uncheck all / Cancel / Continue behavior is preserved.

### Personal Picks
- Supports Phrase, Exact title and Pattern entries.
- Pattern entries remain persistent until removed.
- Pattern rules are not treated as normal phrase matching.
- Personal Picks must never resurrect a globally disabled Remix or Live category.
- UI supports add/remove, clear, copy all, save/cancel.

### Acoustic identity and classification
- Chromaprint acoustic evidence is the duplicate-identity authority.
- External database identifiers are not duplicate-identity authority.
- Duration is candidate routing/diagnostic evidence, not identity authority.
- Definitive exact acoustic match is automatic; no manual identity choice for the proven same recording case.
- Weak/near-threshold non-match stays separate automatically.
- Fingerprint failures remain conservative singleton/unique recordings.
- CUE image rips are recognized as virtual tracks.
- Explicit supersedes corresponding Clean before fingerprint grouping.
- Remix, Live, Extended, Instrumental, Acapella/A Cappella and Acoustic root/version semantics must retain the production rules.
- Radio/Edit-style variants remain acoustically comparable where the production semantic gate allows it.
- Featured-artist Remix exception behavior from 0.22.x must be preserved exactly.
- Compilation duplicate policy must be preserved.

### Optimization
- Strict same-album wanted-content supersets dominate subsets before global optimization.
- Every album family is represented by a maximum-completeness edition.
- Outside singles/EPs cannot rescue a less-complete album edition.
- After wanted/album coverage:
  1. minimize total retained tracks;
  2. then minimize retained release count;
  3. then source/rip-quality / Existing-vs-Incoming tie-breakers;
  4. DR/mastering analysis only for final interchangeable ties.
- CD rip quality threshold behavior must remain compatible with production.

### Release Map - full parity required
The regular 0.22.12 Release Map is the UI/behavioral reference. Preserve:
- chronological release board;
- release columns/rows;
- SVG relationship curves/links/connections;
- Initial and Result release/track counters;
- signed deltas;
- pending Re-Analyze state;
- release search and Enter cycling;
- current-release selection;
- right-side details pane;
- keyboard activation and visible focus;
- release path opener + copy path;
- release Ignore / Restore;
- exact-instance track Ignore / Restore;
- ignored release remains visible and inspectable;
- pending ignore/restore visual and textual states;
- Re-Analyze required before Apply after pending changes;
- Apply disabled while dirty;
- Apply file changes only after explicit confirmation;
- result drawer with causes, THEN added/removed changes and replacement sources;
- replacement source and alternate retained-copy details;
- per-track statuses;
- exact audio-group cross-release highlighting;
- relationship connections for active track carriers;
- Versions button/panel;
- Remixes button/panel;
- Live button/panel;
- alternative-version highlighting/navigation to its carrier release;
- no loss of track status wording or distinction explanations;
- responsive/reflow behavior;
- Escape closes details without losing map state.

### Apply and Undo
- File changes occur only after explicit Apply from Release Map.
- Destructive/move plan is confirmed before execution.
- Undo last run restores the previous applied move set where possible.
- Existing filesystem conflict handling must remain conservative and visible.

## Persistent data and paths

Global path rule applies everywhere: every displayed filesystem path has an adjacent open-location control.

Production program data root:
`Documents\Karpuzikov Tools\Duplicate Edition Analyzer\`

Keep program data isolated under the program's own directory. Dependencies, logs, temp, settings, caches, decision snapshots and manifests belong under that product directory, not directly under `Documents\Karpuzikov Tools`.

Do not create a second incompatible settings universe for the lightweight rewrite without migration/compatibility. The goal is to reuse production DEA settings and state when safe.

## UI/UX contract

Mandatory global UXDT baseline applies. Relevant current guidance rechecked on 2026-10-07:
- clear hierarchy and logically organized content;
- descriptive labels and validation;
- full keyboard operation where applicable;
- visible focus states;
- high contrast;
- no reliance on color alone for state;
- responsive/reflow-safe layout;
- clear system feedback and progress;
- efficient task flow and search;
- predictable reusable interaction patterns.

DEA family typography:
- Segoe UI 9 normal text;
- Segoe UI 10 bold section headings;
- Segoe UI 15 bold major headings;
- Cascadia Mono 9 for activity/log text.

Modern UI may improve visual polish, spacing, hierarchy, responsiveness and animation, but must not remove information or controls.

## Dependency rules

- WinGet-first where applicable; check/install/update dependencies rather than asking the user to reinstall the app.
- Check/repair WinGet if missing before relying on it.
- FFmpeg/FFprobe and Chromaprint/fpcalc must be located/installed/updated through the shared dependency strategy.
- hey-bro-check-log integration must remain compatible with production behavior.
- Lightweight UI target must not require PySide6/Qt WebEngine.
- WebView2 runtime must be detected at startup; use Evergreen/shared runtime instead of bundling a fixed Chromium runtime unless a future explicit requirement changes this.
- Long-running dependency setup must show progress/activity.

## Version/status rules

- Every new or updated test build shows its real version plus `Under construction ⚠️`.
- Status never replaces the version.
- Keep `Under construction ⚠️` until the user explicitly confirms testing is complete.
- Every downloadable build must include its version in the physical filename.
- Do not present unversioned current downloads.

## Build/release rules

- Do not modify or add files under `.github/workflows/` unless the user explicitly asks for that exact workflow or explicitly asks to modify an existing workflow.
- Do not give commit links to the user.
- Verify build/output before offering a download.
- Keep production/reference DEA intact while the new lightweight implementation is under construction unless the user explicitly requests replacement.
- New lightweight builds must not overwrite the production release tag/assets until parity is proven.

## Validation gates for lightweight rewrite

A lightweight build is not parity-complete until all of these pass:
1. Production 0.22.12 self-tests/behavioral regression tests pass unchanged or through a shared core.
2. Callout alias grouping regression passes.
3. Main UI feature checklist matches production.
4. Release Map feature checklist matches production, including visible SVG links/connections.
5. Ignore/restore exact-instance track behavior matches production.
6. Ignore/restore release + Re-Analyze + Apply gating matches production.
7. Version/Remix/Live panels and navigation match production.
8. Replacement-source/result-drawer behavior matches production.
9. Filesystem-path opener invariant passes on every visible path.
10. Keyboard/focus/accessibility/reflow checks pass.
11. Dependency/bootstrap and persistent-data rules pass.
12. No PySide6/Qt WebEngine dependency is present in the lightweight build.
13. Performance and package size are measured against production; lightweight must be materially smaller/faster without dropping functionality.
14. Real-folder comparison against production produces equivalent analysis/release-selection output before user testing.

## Current work state

- User rejected Native Test 0.1.2 for missing functionality.
- Production/reference DEA 0.22.12 remains the authority.
- New request: create a separate lightweight, fast implementation with a modern UI and zero functional loss from production.
- Architecture direction selected: preserve production Python core + modern WebView2 UI host; remove Qt/PySide6.
- No new lightweight build has yet been declared parity-complete.
- Do not send another "native/light" test merely because it launches. Complete the parity checklist first.

## Exact next steps

1. Create a separate lightweight implementation branch/directory; do not reuse the failed native prototype as the base.
2. Start from the production 0.22.12 Python source so analysis/optimizer behavior is shared rather than reimplemented.
3. Separate/reuse the core analysis logic from UI-host code with the smallest possible behavioral delta.
4. Replace PySide6/QWebChannel hosting with a WebView2-capable lightweight bridge while retaining the existing modern HTML/CSS/JS main UI and full Release Map.
5. Implement the same bridge API used by the current Release Map:
   - getState
   - toggleTrack
   - toggleRelease
   - reanalyze
   - openFolder
   - copyPath
   - apply
   - closeMap
   plus the main-window bridge API.
6. Preserve the exact Release Map SVG connection rendering and all details/alternative panels.
7. Keep the production application untouched and independently runnable.
8. Add/update direct regression checks for parity-critical bridge actions and UI contracts.
9. Measure executable/dependency footprint and startup time.
10. Only after parity validation, publish a versioned `Under construction ⚠️` test build for user verification.

## Rejected approaches

- Raw Win32/C++ lightweight rewrite that independently reimplements/simplifies the production engine: rejected by user after Native Test 0.1.2 because functionality was lost.
- Simplified Release Map list/detail view: rejected; full visual map links and interactions are mandatory.
- Calling a build equivalent before feature parity has been audited: forbidden.

## Rule sources to read before work

- Repository global rules: `/SOFTWARE_RULES.md`
- This file: `CONTINUITY.md`
- DEA project history/specification: `PROJECT.md`
- Current functional source: `tools/Duplicate Edition Analyzer v0.22.12.pyw`
