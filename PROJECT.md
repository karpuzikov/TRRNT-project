# Discography Torrent Project

Last updated: 2026-10-13

## Repository source-of-truth rule

The GitHub repository `karpuzikov/TRRNT-project` (repository root) is the canonical source of truth for the entire project. The old `audio-tools/discography-torrent-project/` path in `karpuzikov/userscripts` is retained in historical notes only.

- Every current project-owned script must be stored here.
- Every current workflow asset used by this project must be stored here.
- Documentation and code must be updated together when behavior changes.
- New versions must be committed to GitHub when they are produced, not left only as chat/download artifacts.
- Third-party applications/binaries are dependencies and are not vendored unless redistribution is explicitly appropriate.

## Goal

Reduce the manual work required to prepare complete discography torrents while preserving the user's existing quality-control decisions and preferred tools.

The long-term direction is one controller application that automates the glue between existing tools instead of replacing mature tools without a clear benefit.

Canonical decision rules: [`RULES.md`](RULES.md). All duplicate/edition/source-selection rules belong there and should be updated there rather than duplicated in project notes.

## Current workflow

1. Download every available release into a folder named `recycle`.
2. Remove redundant material:
   - singles/EPs already fully represented on albums can be removed;
   - keep different versions and bonus tracks not present elsewhere;
   - compare standard/special/limited editions;
   - priority is the greatest number of useful original recordings, not remixes/live recordings;
   - source preference: CD rip with LOG+CUE > WEB.
3. Run AudioChecker for releases without an appropriate rip log, including WEB releases as required.
4. Convert everything to ALAC using refalac through CUETools and/or xrecode2.
5. CUETools creates release folders using:
   `%artist%\\[%year% ]- %album%[ '('disc %discnumberandname%')'][' ['%releasedateandlabel%']'][' ('%unique%')']`
6. Fix and standardize tags with MusicBrainz Picard and/or Mp3tag.
7. Compare equivalent recordings/releases and standardize titles such as live/acoustic/MTV Unplugged naming.
8. Find the best available cover art.
9. Rename master covers to match the release folder name.
10. Run the Photoshop 500 PNG / 600 JPG processing script.
11. Put the 600 px JPG into the corresponding release folder.
12. Use MediaHuman Lyrics Finder to embed lyrics.
13. Run the Mp3tag `Мой стандарт.mta` normalization action.
14. Use CUE Corrector for DiscID/MusicBrainz matching, CUE metadata completion, and CUE FILE-name correction.
15. Use Mp3tag `ALAC + header.mte` to generate RuTracker BBCode.
16. Upload 500 px cover images to FastPic.
17. Insert each direct image URL into the matching release's `[img=right]...[/img]` BBCode.
18. Create the torrent with qBittorrent.
19. Post the torrent.
20. Run `CUE LOG 4.1.pyw` to collect LOG/CUE data and post it as a comment.

## File organization and naming stage (planned; not yet implemented)

Insert a dedicated organization stage **after lossless-to-ALAC conversion and metadata/tag normalization, before artwork processing**. Normalize only ALAC collections, following the supplied `!torrent_Structure.txt` ALAC examples:

- Top-level folder: `Artist Name ALAC` or, for themed collections, `Brand Name ALAC` (such as `Assassin's Creed ALAC`).
- Release folders: date, title, edition/release descriptors, and available release-specific identifier. Preserve meaningful distinctions between original-release dates, later edition years, and distinct catalog numbers.
- For physical CDs, `[Pressing Country - Catalog Number]` uses only the **verified manufacturing/pressing country** of that exact disc, never a release-market country. `EU` is not a country. For WEB/digital releases, `[Identifier]` has **no country prefix**, and manufacturing/pressing country is **N/A** (example: `2018-02-16 - El Pintor Bonus Tracks [OLE13186]`). Do not conflate a known digital release with a physical CD whose pressing country is unknown: flag the latter for review rather than silently forcing the digital naming pattern. Follow `RULES.md` for the authoritative rule.
#### Exact release-folder components

For a physical CD, format as `YYYY-MM-DD - Release Title [Pressing Country - Catalog Number]`. The date is the **initial release date**. Use literal ` - ` for date/title and bracket-field separators; the brackets enclose the release's physical edition data. Use the **manufacturing/pressing country**, not the market/release country. For digital releases, use `YYYY-MM-DD - Release Title [Identifier]` without a country.

For physical CD reissues, append the **four-digit reissue year** after the catalog number using ` - ` **only when it differs from the initial release year**: `YYYY-MM-DD - Release Title [Pressing Country - Catalog Number - Reissue Year]`. Never replace the leading initial-release date with the reissue date. The release title may itself contain the edition designation.

Examples from the user:
- `2007-07-09 - Our Love to Admire [JP - TOCP-66683]` = initial date, original title, CD pressing country, catalog number.
- `2002-08-19 - Turn on the Bright Lights (The Tenth Anniversary Edition) [US - OLE 993-2 - 2012]` = initial date in 2002; 2012 physical reissue year placed after catalog.
- `2018-02-16 - El Pintor Bonus Tracks [OLE13186]` = WEB/digital, pressing country N/A.

The precise handling of date records where only a year is known remains to be specified without inventing month/day values. See `RULES.md` for the authoritative separator/year requirements.

- Track filenames: consistent disc/track numbering and titles based on verified tags; preserve CUE-compatible multi-disc structure and supporting CUE/LOG files.
- Before any filesystem change: preview exact source -> destination mappings, detect conflicts, require explicit approval, preserve backup/Undo and update CUE `FILE` references consistently with audio renames. Never silently destroy data. Final category-directory conventions and the full filename grammar still require explicit specification and tests.

This is an added future workflow stage, not a claim that the current tools implement or have Windows-tested it.

## Automation strategy

Do not replace CUETools, refalac, Picard, Mp3tag, MediaHuman, CUE Corrector behavior, Photoshop logic, qBittorrent, or CUE LOG logic unless replacement produces a clear benefit.

Primary automation targets:

- scan and classify everything under `recycle`;
- assist duplicate/edition comparison;
- detect releases that require AudioChecker;
- route sources to the correct ALAC conversion path;
- enforce tag/title consistency;
- automate cover renaming, processing, and placement;
- automate CUE DiscID review and MusicBrainz import;
- generate final RuTracker BBCode;
- map uploaded cover URLs back to the correct release;
- create the torrent;
- generate the CUE/LOG comment;
- maintain per-release state so unfinished items are obvious.

## Duplicate / edition comparison rules

See the canonical rulebook: [`RULES.md`](RULES.md).

The Duplicate / Edition Analyzer must implement those rules and report KEEP / REDUNDANT / REPLACE / NEW decisions with reasons. It must never silently delete material.

## CUE Corrector replacement subset

Original program supplied: CueCorrectorNET.

The useful subset is being recreated as a standalone `.pyw` tool.

Required behavior:

1. Select a release/CUE or batch folder.
2. Reconcile stale CUE `FILE` names in memory before DiscID calculation.
3. Calculate the MusicBrainz DiscID / TOC correctly, including pregap/HTOA cases.
4. Query MusicBrainz.
5. Never silently choose a concrete edition.
6. Show all DiscID matches with enough information to identify the physical release:
   - artist;
   - release title;
   - disambiguation;
   - date;
   - country;
   - label(s);
   - catalog number(s);
   - barcode;
   - medium format;
   - disc position;
   - track count;
   - packaging;
   - status;
   - release MBID.
7. Provide links to the MusicBrainz DiscID page and selected release.
8. User explicitly chooses the concrete release or skips it.
9. Populate the CUE from the selected MusicBrainz release.
10. Correct CUE `FILE` names.
11. Preserve indexes, pregaps, HTOA, and unrelated structural data.
12. Save only after explicit user action.

### MusicBrainz request lesson

Do not depend on unsupported DiscID `inc` combinations.

The reliable direction is:

- use the DiscID/TOC lookup only to obtain the associated release matches;
- fetch full release details separately by release MBID;
- retry transient 429/5xx responses with backoff.

## CUE metadata parity

Comparison was performed between:

- original CUE;
- original CUE Corrector output;
- standalone Python tool output.

Track-level MusicBrainz data and corrected filenames were effectively aligned.

Known header requirements:

- `REM COMMENT` must be cleared/removed.
- `REM DISCNUMBER` must come from MusicBrainz.
- `REM TOTALDISCS` must come from MusicBrainz.
- Disc numbering must count only CD media. Ignore DVD and other non-CD media in the release when determining CD disc position and total CD count.
- Preserve CUE Corrector-compatible quoting/formatting for fields such as label and catalog number.

Current MusicBrainz -> CUE mapping includes:

Album:
- `REM DATE`
- `CATALOG` = barcode
- `REM LABEL`
- `REM LABELNUMBER`
- `PERFORMER`
- `TITLE`
- `REM DISCNUMBER`
- `REM TOTALDISCS`

Track:
- `TITLE`
- `PERFORMER`
- `SONGWRITER`
- `REM COMPOSER`

## Shared title/tag normalization rules

The same normalization rules must be applied to:

1. titles/artist data written into the CUE; and
2. tags inside the actual audio files.

Source of truth:
`picard-tools/scripts/`

Current relevant scripts:

- `English_Title_Capitalization.txt`
- `Move_Featured_Artists_to_Title.txt`
- `Format_Multiple_Artists.txt`
- `Unicode_to_ASCII.txt`
- `Add_EP_Single_Suffix.txt`

### English title capitalization

Use Picard's titlecase behavior, not Python's naive `.title()`.

Then lowercase ordinary internal words such as:
`a, an, the, and, but, or, nor, as, at, for, of, to, cum, mid, per, qua, re, via, with, without`

Capitalize them again after `:`, `!`, or `?` where required by the existing Picard script.

### Featured artists

Match the current Picard behavior:

- remove featured-credit text from `artist` and `albumartist`;
- normalize `feat.` / `feat` form to `ft.`;
- append the featured credit to the title as `(ft. Artist)`.

CUE and file tags must stay synchronized.

### Multiple artists

Use the Picard rule already in the repo:

- 1 artist: `Artist A`
- 2 artists: `Artist A & Artist B`
- 3+: `Artist A, Artist B & Artist C`

### Unicode -> ASCII normalization

Use the existing Picard replacement table for:

- smart apostrophes/quotes;
- Unicode dashes;
- ellipsis;
- full-width punctuation;
- non-breaking/thin spaces;
- other mapped punctuation and symbols.

Do not invent additional replacements without adding them to the shared rule set.

## Cover workflow

Existing Photoshop script behavior to preserve:

- 600 px JPG on the longer side;
- target maximum around 170 KB using the highest JPEG quality that fits;
- 500 px PNG for upload, with JPG fallback when needed;
- 500 KB limit for the upload image.

Known bug in the old Photoshop helper:
if no JPEG quality 12..1 meets the 170 KB target, it falls back to quality 8. This can violate the size target and should be corrected.

Desired automated flow:

`choose master cover -> rename -> make 600 JPG -> make 500 upload image -> place 600 JPG in release -> put upload image in FastPic queue`

## BBCode / FastPic

Existing `ALAC + header.mte` generates deterministic RuTracker BBCode from tags and folders.

Desired flow:

`finished releases -> generate BBCode -> upload 500 px covers -> collect direct URLs -> map URL by release folder name -> insert into each [img=right] block`

If FastPic automation is unreliable, support a fallback where the user pastes upload results and the program maps URLs to releases automatically.

## CUE LOG

Existing `CUE LOG 4.1.pyw` behavior to preserve:

- recursively collects CUE/LOG data;
- prefers `audiochecker.log` over another LOG in the same folder;
- labels AudioChecker logs as quality-check logs;
- nests release data in BBCode spoilers;
- splits long output without breaking spoiler structure;
- writes UTF-8 output files.

Long-term integration:
run this logic automatically at the final torrent preparation stage and prepare/copy the comment text.

## Duplicate / Edition Analyzer UX\n\n- Startup failures are never silent: the analyzer shows the exception when possible and writes `Documents\\Karpuzikov Tools\\Duplicate Edition Analyzer - Crash.log`.\n\n- Release discovery is recursive: organizational folders such as Albums/Other/Singles and per-single grouping folders are traversed, while CD1/CD2/Disc subfolders remain grouped as one release.

- Remix exclusion exception: remixes with newly added featured performers remain included. The baseline is derived from non-remix/non-live versions of the same base song + primary artist, so the normal song vocalist does not accidentally rescue every remix. If no matching non-remix base is present, an artist-tag/filename-prefix feature is not enough; only feature wording explicitly attached to the remix/version itself can preserve the remix.\n\n- Analyzer UI uses a unified dark theme by default, including dark Windows title bars when supported.

- Release discovery is recursive through organizational folders. Internal CD1/CD2 folders and sibling `... CD 1` / `... CD 2` folders are treated as one logical release and kept/moved together.

- Existing discography is optional: when blank, the analyzer optimizes Recycle against itself; when supplied, it compares Existing + Recycle together.

- Source detection: `.cue` + any `.log` except `audiochecker.log` = CD. Equivalent existing ALAC content wins over equivalent recycle content unless recycle is objectively better by current active rules (for example CD vs WEB) or adds unique included audio.

- Do not show a per-release review/results table as the normal workflow.
- The software performs the track-by-track duplicate/version analysis and release optimization itself.
- Uncertain equivalence is resolved conservatively by keeping both tracks/releases.
- After scanning, show one global confirmation containing summary counts only.
- On confirmation, automatically move redundant release folders as-is into `<artist>_duplicates`; never rename the release folders.
- Create `<artist>_duplicates` as a sibling of the selected artist folder, e.g. `...\!recycle\3OH!3_duplicates`.
- Redundant folders containing remix material go under `<artist>_duplicates/!Remixes/`.
- `Save Remixes` and `Save Live recordings` are persistent affirmative checkboxes, unchecked by default; checked categories count as included coverage.
- Before Chromaprint comparison, show an unusual-pattern review only for non-standard non-remix/non-live descriptors that normal rules do not already understand. Standard families are recognized structurally, not by exact phrases: arbitrary prefixes must not make ordinary Radio Edit/Mix, Instrumental, Acoustic, Demo, Session, Remaster, Edit/Re-Edit, Extended/VIP/Vocal Mix, 7-inch/12-inch forms, common Version/Edition labels, etc. appear. Named/ambiguous plain mixes such as `The Matrix Mix` / `Tom Lord-Alge Mix` may remain review candidates. Similar callout labels are grouped. Remix/live remain controlled only by their existing checkboxes.
- `Mix` alone is not a remix marker: Original/Extended/12-inch/7-inch mixes and Instrumental/A-Capella versions stay included unless redundant for other reasons; Club Mix is remix material.
- Version/language labels never create duplicate identity, but strong conflicts may conservatively veto an otherwise-valid audio match.
- Track duplicate identity still requires strict Chromaprint similarity; metadata can never create a match. A post-audio safety gate now blocks merges when strong contradictory evidence indicates a distinct recording/version (for example different recording MBIDs, semantic version/language conflicts, or different ISRCs combined with conflicting titles/credits).
- Candidate discovery may use exact identifiers and same-base-title + duration only as hints; every final duplicate still requires the audio matcher.
- Large fingerprint offsets are allowed so long silence/hidden-track padding can be ignored; substantial non-silent extra audio remains a distinct version.
- Remix tracks are excluded from included coverage, but a release containing remixes is not automatically ignored. For equivalent included album content, CD/physical media wins over WEB before file-count minimization. Explicit/clean is deferred until a reliable detector exists.
- Leave required recycle releases in the recycle folder for the next processing stage.
- Final UI should only show a short completion summary plus Open filtered recycle / Open duplicates / Undo.
- Technical diagnostics are optional and must not be required for normal use.
- Every analysis run writes a detailed JSONL comparison log outside the scanned artist folder, normally under `<artist>_analysis_logs`. Each compared pair records candidate-discovery route, track paths/titles/IDs/credits/version descriptors, durations, every Chromaprint metric, strict/mastering threshold state, metadata-safety result, final MATCH/REJECT decision, and run summary. The log also reports possible-pair vs candidate-pair counts so prefilter efficiency can be audited.

## AudioChecker automation

Current stage after duplicate filtering.

Required behavior:

1. Use the filtered recycle/update artist folder from the appropriate per-program persistent settings when available.
2. Recursively find folders containing AudioChecker-supported lossless audio.
3. Treat a folder as a CD release when it contains both:
   - at least one `.cue`; and
   - at least one `.log` other than `audiochecker.log`.
4. Skip those CD folders.
5. Skip folders that already contain a valid `audiochecker.log` covering all supported audio files in that folder.
6. Run the existing Dester AudioChecker (`achkgui.exe`) automatically for the remaining folders.
7. Reuse AudioChecker's own generated `audiochecker.log`; do not fabricate/rewrite the protected log format.
8. Remember the selected `achkgui.exe` path across launches and versions in that program's own `Documents\\Karpuzikov Tools\\<Program Name>\\settings.json`.
9. If an existing `audiochecker.log` is stale/invalid, preserve it temporarily outside the release folder while regenerating it; restore it if generation fails.
10. Normal UI is automatic: one folder, progress, final counts. No per-release review.

## ALAC conversion automation

Current stage after AudioChecker.

Required behavior:

1. Reuse the filtered recycle/update artist folder from the appropriate per-program persistent settings.
2. Convert supported lossless audio to ALAC in place.
3. Use refalac for the ALAC encode.
4. For normal track-based releases, preserve metadata/artwork and verify decoded PCM before removing the source file.
5. For CD releases identified by `.cue` + real rip `.log`, use the CUE as the authoritative split input so image rips and file-per-track CUEs are handled correctly.
6. Preserve CUE, LOG, artwork, and all other non-audio files.
7. Skip files already encoded as ALAC.
8. Refuse lossy/unknown codecs instead of wrapping them in ALAC.
9. Remove/replace source audio only after verified output exists.
10. Use up to four simultaneous conversion jobs.
11. Reuse that program's own `Documents\\Karpuzikov Tools\\<Program Name>\\settings.json` and remember all repeatable paths.
12. Bootstrap required dependencies automatically: check/install winget first when needed, install FFmpeg through winget, and obtain refalac automatically when missing.
13. Normal UI is automatic: choose/reuse one root folder, convert, then show only summary counts/errors.

## Proposed controller

Working concept: `Discography Builder.pyw`

Per-release status columns:

- Source
- Duplicate review
- Audio check
- ALAC
- Tags
- CUE
- Lyrics
- Cover
- BBCode
- Ready

High-level stages:

1. Analyze Recycle
2. Compare Editions
3. Audio Verification
4. ALAC Conversion
5. Metadata
6. CUE DiscID / MusicBrainz
7. Lyrics
8. Covers
9. Final Validation
10. Generate RuTracker BBCode
11. Upload/insert cover links
12. Create torrent
13. Generate CUE/LOG comment
14. Open prepared RuTracker submission

## Current immediate work

Duplicate / Edition Analyzer and AudioChecker automation are complete enough for now.

Active implementation focus: ALAC conversion automation.

Current target:

1. reuse the filtered recycle/update folder automatically;
2. convert lossless sources to ALAC in place with refalac;
3. use CUE-aware conversion for CD releases;
4. preserve tags/artwork and non-audio release files;
5. verify output before deleting source audio;
6. skip existing ALAC;
7. refuse lossy sources;
8. process up to four jobs concurrently;
9. remember all repeatable paths/settings and bootstrap dependencies.

Next stage after this: metadata/tag normalization using the shared Picard rules.

## Important principles

- Never silently delete a release during duplicate analysis.
- Never silently choose a MusicBrainz edition from a DiscID match.
- Preserve source CUE structure where possible.
- Back up a CUE before destructive modification.
- CUE and embedded file tags must agree.
- Existing user rules/scripts are the source of truth.
- Prefer one `.pyw` application and minimal file count for the final tool.

- Chromaprint generation auto-scales parallel fpcalc workers to logical CPU count (up to 32) instead of a fixed 4, and skips tracks already excluded from included coverage by the remix/live options.

- Post-fingerprint matching now shows explicit Indexing/Finding candidates/Comparing audio/Optimizing stages. Candidate discovery uses an inverted fingerprint-token index and similarity checks use multi-process CPU parallelism instead of silently running on one Python thread.

- UI labels must explain the effect of each option to a first-time user; ambiguous labels such as `Exclude remixes` are replaced by explicit behavior such as `Ignore remix tracks when deciding which releases must be kept`, with concise inline explanations.

- UI uses concise affirmative controls (`Save Remixes`, `Save Live recordings`) and a visible progress/activity area with current stage, counts, percentage, elapsed time, and move progress. Detailed explanations belong in tooltips rather than permanent paragraphs.

- Duplicate moves preserve the source-relative hierarchy under `<artist>_duplicates`; remix-bucket moves preserve it under `<artist>_duplicates/!Remixes`.
- Empty organizational folders left after successful moves are removed.
- Album editions are clustered by strong ordered overlap of audio-derived fingerprint groups rather than edition/folder naming.
- Chromaprint candidate discovery is a bounded routing stage: same-base-title and exact-fingerprint safety routes bypass duration, while different-title candidates use normalized/IDF-weighted token evidence inside bounded duration neighborhoods plus a very-rare-token rescue route. Duration is routing-only, never identity evidence. Very common token buckets are suppressed, high-confidence candidates run first, and deterministic shadow validation checks/recover sampled router false negatives.
- Audio matching always evaluates zero/near-zero fingerprint alignment as well as histogram-derived offsets, preventing mastering differences from hiding the correct alignment.
- A second mastering/pressing-tolerant Chromaprint threshold handles near-identical-duration copies of the same recording without using filenames/titles.
- A final redundancy-prune pass removes selected singles/EPs that add no included audio when equal-or-better retained sources already cover them.
- Named person/DJ/producer/act `Mix` labels (for example `Ferry Corsten Mix`, `Tom Lord-Alge Mix`, `Madlib's Mix`) are remix material and are controlled by `Save Remixes`; they must not appear in the unusual-pattern review.

- v0.9.8 classifier refinement: hide standard suffix families regardless of prefix (12-inch, Extended Version, Full Version, style Mixes such as Ambient/Chillout/Downtempo/Garage/House/Trance, instrument versions, etc.); ignore year-only parser fragments; group bare `hook` with Call Out/Callout Hook; treat `Mix by <name>` and handle-style named mixes as remixes; repair mojibake in displayed examples.

- v0.9.9 classifier refinement: hide remaining standard forms including 7-inch/12-inch Version, Big/New/Smooth/Low Gain Mix, OG Version, Single Version + year compounds, Chillout/genre versions, Special DJ Version, regional abbreviations such as US Version, and normal Pop Version suffixes.

- Intra-release duplicate cleanup: after release selection, retained non-CUE releases are checked for multiple files representing the same logical track. A file is redundant only when it shares the same high-confidence audio group, same normalized title, same track position, and same physical disc/folder. Keep the technically better copy and move the redundant file under `!Duplicate Files`; never delete it. CUE-based releases are excluded from this cleanup to avoid breaking CUE references. `Rmx` is treated as `Remix`.

- Logging checkbox: comparison JSONL logging is optional and persists with the other analyzer settings. The current schema writes track metadata once, references tracks by index in pair records, keeps full pair details only for diagnostically valuable cases, and aggregates ordinary rejects into funnel/route/duration counters so routine logs do not grow to multi-gigabyte raw files.

- ITUNESADVISORY final preference: `ITUNESADVISORY=0` is clean and `ITUNESADVISORY=1` is explicit. Clean/explicit remains neutral during fingerprint comparison, coverage, album/source optimization, and track-count minimization. At the absolute final selection stage only, if clean and explicit copies are the same release with identical source class, track count, included track order, and exact audio-group multiset, retain explicit and move clean. If the audio differs (for example a genuinely censored edit), both remain distinct.

- v0.10.3 intra-release dedupe fix: fingerprint all tracks, including tracks excluded from included coverage, so retained releases can still be deduplicated safely. Same-track safety now accepts agreement from either embedded title or normalized filename while still requiring the same physical folder, same track position, and the same high-confidence audio group. This fixes missed pairs such as `01 - Mask Off (Marshmello Remix)` vs `01 Mask Off (Marshmello Remix)` when tags differ.

- hey-bro-check-log CD rip quality: integrate `ligh7s/hey-bro-check-log` v1.3.2 as an auto-installed runtime dependency. Score every non-`audiochecker.log` EAC/XLD rip log. A multi-disc release is compared by worst-disc score first, then average, then flagged status. Unrecognized/unsupported logs remain neutral. Log quality is used only after audio/content/source equivalence is proven, so it cannot create duplicate identity or remove a different edition. A higher-scoring exact-equivalent CD rip may replace an existing lower-scoring CD rip; existing wins when log quality ties or is unavailable. ITUNESADVISORY explicit-over-clean remains the absolute final tie-break.

- v0.11.1 absolute exclusion semantics: unchecked `Save Remixes` / `Save Live recordings` makes those tracks invisible to all release-selection logic, not just fingerprint coverage. They do not contribute to included counts, album/EP/single heuristic type, edition equivalence, CD-log tie-break eligibility, clean/explicit equivalence, minimum-file scoring, or later tie-breaks. Removed the remix featured-artist exception. `Session`/`Sessions` and `Unplugged` are treated as live-performance material for this option. Ignored files remain physically inside a retained mixed release but can never make that release win.

- v0.11.2 checkbox semantics: use direct checkbox inclusion semantics. `Save Remixes` and `Save Live recordings` are direct inclusion switches. Checked means that category participates normally in release comparison, coverage, counts, optimization, and tie-breaks. Unchecked means that category is skipped completely by those stages. Mixed releases may still physically contain skipped tracks, but skipped tracks have zero influence on which release is kept.

- v0.11.3 collection-wide track minimization: remove the pairwise rule that automatically preferred an album superset over a smaller edition. Extra tracks on a larger edition do not justify it when those same recording groups are already supplied elsewhere in the retained collection. After the full selection is assembled, iteratively replace a selected album with a smaller related edition when source class is not worse, every included recording group remains covered globally, and total included track count strictly decreases. Re-run redundancy pruning afterward. This is intended to prefer the 25-track `All This Bad Blood` US edition over the 26-included-track `[4710 084-7]` edition when `bad_news` is already supplied by retained `VS.`.

- v0.11.4 tempo/effect remix classification: treat `Sped Up`/`Speed Up`, `Slowed`/`Slowed Down`, and `Reverb`/`Reverbed` variants as remix material. They follow `Save Remixes` exactly: checked means included normally; unchecked means skipped completely. Release/package routing also recognizes these labels so redundant releases are archived under `!Remixes`.

- v0.11.5 Redux remix classification: treat `Redux` as remix material. This covers titles such as `Overjoyed (Detour City Redux)`. Redux follows `Save Remixes` exactly, is stripped by remix base-title normalization, is treated as a semantic version descriptor for match safety, and redundant Redux releases route under `!Remixes`.

- v0.11.6 Personal Picks: add a persistent `Personal Picks...` rule editor beside the remix/live options. Phrase rules are case/punctuation-insensitive substring matches, intended for families such as `Live From Capitol Studios`; exact-title rules match only a normalized complete track title/filename. Personal Picks only override an otherwise skipped remix/live track. The restored recording then participates normally in global collection optimization, so the analyzer still chooses the minimum-track release set and does not force a specific release. Rules are stored in the shared settings file under `personal_keep_rules_v1`, and matched rules are written into comparison JSONL as `personal_keep_rule`.

- v0.11.7 Personal Picks phrase detector: add `Detect phrases...` inside Personal Picks. It scans audio filenames from the currently selected Existing and New/update roots, extracts parenthetical/bracket and named Mix descriptors, filters to live/remix-classified phrases, strips year/punctuation noise, groups recurring `Live From`/`Live At` venue families, shows occurrence counts/examples, and lets the user multi-select phrases to add as persistent Personal Picks. Existing rules are omitted from detector results. [SUPERSEDED by v0.22.11: automatic/live-remix phrase review system removed.]

- v0.11.8 automatic phrase review: after pressing Analyze and completing the metadata scan, detect remix/live phrase families from the already-scanned tracks for categories currently disabled by `Save Remixes` / `Save Live recordings`. Show a dark review window with phrase, category, occurrence count, examples, and a per-row `Add to keep list` button. Existing Personal Picks show `In keep list`. `Continue` persists newly added phrases and proceeds to the existing unusual-pattern review and fingerprint optimization. `Personal Picks...` remains for manual add/remove management. [SUPERSEDED by v0.22.11.]

- v0.11.8 storage layout was the older shared-root design and is superseded by v0.12.1. Legacy analyzer settings/logs/state from that layout are migrated forward automatically.

- v0.11.9 unusual-pattern false-positive fix: trailing `Artist - Title` / `Artist: Title` text is no longer treated as a version descriptor merely because the song title contains a descriptor word such as `Club`. The trailing segment must itself end in a recognizable version/edit/mix/live/remix/etc. descriptor shape. This prevents clean titles such as `INNA - I Am The Club Rocker` from appearing in Unusual track pattern review while preserving real suffixes such as `Song - Radio Edit`, `Song - Ferry Corsten Mix`, and `Song - Special Version`.

- v0.12.0 CD image rip support: recognize CUE sheets where multiple AUDIO tracks reference the same physical lossless audio file (FLAC/APE/WavPack/WAV) as a CD image rip. Build virtual per-track entries from CUE `TRACK`, `TITLE`, `PERFORMER`, `ISRC`, and `INDEX 01` data; use the next `INDEX 01` as the segment end. Probe technical quality from the underlying image, extract each virtual segment temporarily with FFmpeg, fingerprint it with fpcalc, then delete the temporary segment. Virtual tracks participate in all normal duplicate grouping, edition comparison, Personal Picks, remix/live filtering, minimum-track optimization, and CD rip-quality rules. A redundant image release is moved only as its complete physical folder/image+CUE+LOG set; image tracks are never individually removed. Split-file CUE sheets remain normal split tracks.

- v0.12.0 CUETools integration: use an existing CUETools installation when available and locate `CUETools.ARCUE.exe`/legacy `ArCueDotNet.exe`; install CUETools through winget package `gchudov.CUETools` only when missing. For album families containing a CD image rip, run the console verifier on every CUE-based candidate in that family so image and split rips are compared fairly. Positive AccurateRip/CTDB verification is quality evidence only and is applied only after exact audio equivalence is already proven. Database absence, no-match output, CUETools failure, or unavailable CUETools is neutral, never evidence that a rip is bad. CUETools verification outranks log-settings score when choosing between otherwise exact-equivalent CD rips; EAC/XLD log score remains the next tie-break. Temporary track-segment extraction still uses FFmpeg because the documented `CUETools.exe /convert` profile mode is interactive/non-terminating and is unsuitable for unattended analyzer loops.

- v0.12.1 mandatory development-standards pass: apply the newest global software rules before further feature work. `Duplicate Edition Analyzer` now owns `Documents\\Karpuzikov Tools\\Duplicate Edition Analyzer\\` with `settings.json` plus isolated `dependencies`, `logs`, `temp`, `cache`, and `state` subfolders. Older shared-root settings, comparison logs, crash log, and undo state migrate forward automatically. Analyzer-managed hey-bro-check-log and downloaded Chromaprint files use the program's `dependencies` folder; CUE segment work files use its `temp` folder; comparison/crash logs use `logs`; undo state uses `state`; dependency-check timestamps use `cache`. WinGet is bootstrapped before dependency work, FFmpeg and CUETools are installed when missing and checked for updates periodically, and the hey-bro-check-log source archive is commit-pinned instead of tracking mutable `master`. The tool remains one double-clickable `.pyw`, dark-theme by default, and `Under construction ⚠️` until confirmed complete.

## Mandatory software-development preflight

Before any program/script change in this project, apply the newest stored software-development rules first. Newer explicit rules override older conflicts. Current requirements include: dark UI by default for GUI tools; one `.pyw`/minimal file count where practical; dynamically resolve Windows Documents; isolate each program under `Documents\\Karpuzikov Tools\\<Program Name>\\`; keep that program's dependencies/logs/temp/cache/settings/state inside its folder; migrate older data instead of losing it; bootstrap WinGet first and install/update dependencies automatically; publish changes as in-place updates for existing installations; use `Under construction ⚠️` while unfinished; pin externally downloaded source archives to immutable versions/commits where practical; and update code/documentation together.

- v0.13.0 Decision Map: replace the final yes/no-only analysis confirmation with a searchable visual decision explorer. Every release receives a persistent explanation snapshot. The left side lists all releases with RETAINED/DUPLICATE and KEEP/ADD/REPLACE/SKIP/REMOVE status; filters show All/Retained/Duplicates and search matches release/path/reason/source. The right side draws a mind-map-style graph from release -> outcome -> main decision -> supporting branches. Branches include optimizer factors, unique/essential recordings, Personal Picks, retained releases covering each duplicate's fingerprint groups (with counts/examples), skipped remix/live/pattern counts, source/CUETools/rip-log context, direct replacement/preferred-existing relationships, and same-coverage alternatives. The Decision Map opens before move confirmation, so the user can investigate any release before allowing filesystem changes. The latest map can be reopened from the main window and from the completion dialog, and its compact structured snapshot is stored in the analyzer's own `state\last_decision_map.json`, not in the optional comparison log.

- v0.14.0 interactive re-optimization: make the Decision Map a clickable release dependency web. Each release snapshot includes the full track list plus all other releases sharing included fingerprint groups. Connected release nodes navigate directly to those releases. While the current analysis remains in memory, `Remove selected release and re-optimize` adds that release ID to a reversible blocked set and re-runs only the collection optimizer against the existing recording groups; no rescan, FFprobe, Chromaprint generation, or pairwise audio comparison is repeated. The new plan marks release actions changed from the initial solution, automatically promotes alternate carriers when available, and supports multiple manual removals. If a blocked release contains an included recording group with no non-blocked carrier anywhere in the analyzed collection, the affected track is highlighted red in the track list and the release graph shows the orphaned audio. Applying a plan with orphaned manual-removal tracks requires a second explicit confirmation. Manual removal can be undone before Apply. Saved Decision Map snapshots remain reopenable read-only after the live analysis context is gone.


- v0.15.0 Release Map redesign: remove the side release list and all always-visible factor/reason nodes from the graph. The graph itself now contains releases only; selecting a release redraws a spider-web of releases sharing current included fingerprint groups, and every connected release node is navigable. The selected release opens a separate details area below the map with a draggable vertical sash, a large dark high-contrast track table, exact current-plan reason, selectable/copyable folder path, Open folder, and a collapsed More details section for optimizer/source factors. Unique retained tracks are highlighted for research. The window is a standard non-transient/non-grab Toplevel so Windows/FancyZones can manage it normally, and it exposes Fit map, Maximize, Apply plan, Analyze again, and Close.
- v0.15.0 persistent per-track skip: a live-analysis track can be selected and skipped directly. The analyzer saves a rule in its own state folder, records exact Chromaprint fingerprint hashes plus available MBID/ISRC/base-title evidence for the current audio group, reapplies it after grouping on later analyses, and excludes the whole proven-equivalent recording group from optimization. Restore removes the saved rule and recomputes the plan. Normal remix/live/pattern exclusions remain separate.
- v0.15.0 explanation accuracy: the main ADD reason now describes why that specific release is selected in the final set (unique recording(s), required album representation, replacement, or global minimum-file selection) rather than merely saying its tracks were absent from the pre-analysis Existing folder. Track rows explain same-title distinct groups using credited artist/feature differences, ISRC, duration, and an explicit Chromaprint mismatch when no visible metadata difference explains it.
- v0.15.0 metadata-veto correction based on the 2026-09-30 comparison log: a strict audio match is no longer rejected merely because one title omits a semantic descriptor or because two descriptors are wording aliases in the same family (for example Radio Edit vs Play & Win Radio Version). Strong metadata vetoes remain for genuinely incompatible stated families and credited-artist/identifier conflicts.


- v0.15.1 validation fixes: saved-track-skip application is no longer reported as an error/note; the release network centers on the selected release; already option-skipped tracks cannot create redundant manual skip rules; the collapsed More details panel inserts correctly above the track table; and the summary counts persistent skip rules rather than duplicate copies carrying the same rule.


- v0.15.2 startup hotfix: restored the complete GUI class tail after the v0.15 Release Map refactor accidentally removed `App` and the support dialogs. The published build again contains ToolTip, phrase/pattern/personal-picks dialogs, App, startup/crash handling, and the normal analysis workflow while retaining the v0.15 Release Map and persistent track-skip features.

- UI migration requirement: replace Tkinter Release Map with a PySide6/Qt 6 desktop shell and a Sigma.js/Graphology graph rendered through Qt WebEngine. The graph is one persistent all-releases network: every analyzed release is present simultaneously; selection only highlights relationships and opens one flat details panel, never a sub-map or nested navigation.

- Release Map visibility rule: render only releases that are currently relevant to the retained plan. Omit duplicate/redundant releases (`SKIP`/`REMOVE`), omit remix-only releases when Save Remixes is off, and omit live-only releases when Save Live recordings is off. Keep hidden releases in analysis state so changing settings or re-optimizing does not require losing their data.

- Release Map staged-edit workflow: retained release nodes show color-coded unique-track badges (1-2 red, 3-5 yellow, 6+ green). Clicking a release unfolds one flat details panel while the entire release graph remains visible. Unique tracks are highlighted. Track-row hover exposes Ignore; track Ignore persists only for that exact physical track instance and never expands to the acoustic group/base title/other copies, while release Ignore excludes only that exact release and never blacklists its tracks. Either change marks the plan dirty and highlights Re-Analyze. Re-Analyze reuses existing fingerprint/comparison data, re-runs selection with the new constraints, and is allowed to promote previously hidden duplicate releases when they become necessary replacement sources. A bottom result drawer reports exact IF -> THEN plan deltas, and Apply highlights only after a changed plan is ready. Apply alone commits filesystem moves.


- v0.16.1 modern Release Map implementation: replaced the Tkinter Release Map with a separate normal Windows PySide6/Qt 6 process using Qt WebEngine. Sigma.js 3.0.3 + Graphology 0.26.0 render one all-retained-release graph at once; selection dims unrelated nodes but never creates a sub-map. Node overlays show unique-track counts with red/yellow/green thresholds. Release details stay in one flat side panel with track highlighting, Open folder/Copy path, hover-only track Ignore, and release-only Ignore. Ignore operations mark the plan dirty and disable Apply until Re-Analyze. Re-Analyze reuses the existing in-memory fingerprints/groups and may promote previously hidden duplicates as replacement sources. A bottom result drawer reports added/removed releases from the previous plan; Apply returns the revised decision set to the main analyzer for filesystem execution. Duplicate/redundant nodes and excluded remix-only/live-only releases are never rendered. PySide6 6.11.2 is installed in the analyzer-specific dependency folder on demand. A GitHub Actions syntax check now compiles the latest analyzer on every analyzer change.


- v0.16.2 graph readability correction: Sigma remains the GPU edge/network renderer, but release nodes are now compact rectangular HTML cards synchronized to Sigma graph coordinates instead of colored circular nodes. The release name is high-contrast light text, and unique-track color is confined to a small corner badge. Relationship edges are substantially brighter/thicker at default view and become stronger on selection without fully vanishing when unrelated. Force-layout world spacing is expanded to account for card dimensions and reduce the pile-up seen in v0.16.1.


- v0.16.3 live editing controls: fixed the embedded graph JavaScript boundary, made Ignore release a prominent destructive button at the top of release details, strengthened the hover-only track Ignore action, and preserved the live editable Release Map session after the map window is closed. Reopening Release Map from the main analyzer now keeps track/release Ignore, Re-Analyze and Apply available until Apply is committed or a new Analyze starts; only historical saved snapshots are read-only.


- v0.17.0 chronological release board: replaced the force-directed circular/card cloud with a Windows Explorer-like multi-column folder board sorted by the date prefix in each release folder name. Retained releases and useful duplicate releases are present simultaneously; duplicate rows are secondary and marked DUP. Default lines now mean only duplicate -> retained coverage, eliminating the previous generic shared-audio connections. Clicking any track activates recording-carrier mode: every visible release containing that exact fingerprint group (including duplicate releases) is highlighted, unrelated rows dim, and bright SVG connections show the alternative sources. The unique-track red/yellow/green badge remains on retained rows only. Qt WebEngine stays as the modern UI shell; Sigma.js/Graphology are no longer required because the UI is a chronological DOM/SVG board rather than a force graph.


- v0.17.1 packaging fix: corrected the duplicated `_qt_release_map_process` function boundary introduced during the v0.17.0 Release Map replacement. No workflow behavior change; this is the compile/runtime correction for the chronological folder-board build.


- v0.17.2 Personal Picks phrase correction: fixed analyze-time phrase extraction that could treat an entire title such as `Club Rocker (Play & Win Remix)` as a reusable remix phrase in addition to the correct `Play & Win Remix`. Supplemental trailing-descriptor detection now requires an actual title/descriptor separator, and full-title `... Mix` fallback is disabled when scanning complete track titles. Personal Picks Review now has per-candidate Copy plus Copy all review text; the persistent Personal Picks window supports Ctrl+C on selected rows and Copy all.

- v0.17.3 Alternative Versions: the Release Map now groups tracks by display-only base-song identity and exposes a `Versions N` control for every track that has distinct recording groups of the same song. The expansion lists the alternative title, duration, whether that version is currently retained or merely available, and example carrier releases. Clicking an alternative highlights every release carrying that alternative recording group. This does not merge edits/mixes/remixes or change duplicate/coverage identity: exact-audio groups remain separate and the optimizer still relies on fingerprint/recording evidence. This specifically supports decisions such as ignoring one `Sun Is Up` radio edit while confirming that another `Sun Is Up` version remains in the collection.

- v0.17.4 Release Map search / physical-file optimization: `Find release or track...` now searches visible release names and every contained track title, highlights all matching releases while typing, reports match count, and cycles matches with Enter. The map omits releases with zero tracks that actually participate in recording-group coverage, so excluded/non-counting releases are below DUP and are not rendered. Coverage and disk cost are now distinct: excluded remix/live/pattern tracks never create coverage, but because retained releases remain whole folders, every underlying physical audio file counts toward the minimum-file objective. This supersedes the older rule that excluded tracks were invisible to minimum-file cost. Among equal useful coverage and source priority, a 1-file single beats a 10-file package containing the same one counted recording plus nine ignored tracks. CUE image virtual tracks are charged by unique underlying audio path, not logical track count.

- v0.17.5 Release relationships / Gem / Versions vs Remixes: release-click spider-web connections now include same-base-song alternative recording groups in addition to exact duplicate coverage. Non-remix alternative Versions use a separate relation from Remixes, so a title such as `Alright` can connect to a shorter/longer alternate recording even when fingerprint/length-gate evidence correctly prevents exact duplicate grouping. Track details now expose separate `Versions N` and `Remixes N` buttons and sections; remix groups never count as Versions for Gem logic. A retained release receives a 💎 Gem badge instead of its numeric unique-count bubble when it contains at least one unique counted non-remix track whose base song has only one non-remix recording group in the analyzed collection; exact duplicate copies of that same group and pure remixes do not disqualify the Gem. The old bottom-left map legend (`duplicate -> retained source` / `selected track exists here`) was removed because it was redundant and rendered poorly. Example log case: `2012-10-19 - Alright [5948204006396]` has a 269.54 s `Alright`, while the WPCR-15062 CUE track is 229.67 s; strong fingerprint overlap exists, but the ~39.87 s non-silence difference correctly keeps them as distinct exact groups while the map now still shows the alternative-version relationship.

- v0.17.6 Track Gems / closable details: preserve the v0.17.5 release-level 💎 behavior even for albums, per user direction to leave it in place. Add a stricter track-level 💎 beside the track title only when that exact recording group occurs in one release across the entire analyzed collection and its base song has no other non-remix recording-group version anywhere in the scan; pure remixes do not disqualify it. This track marker therefore means both no exact duplicate copy in another release and no alternate non-remix version. The Release Map details panel now closes through a top-right × button, Escape, or by clicking the already-selected release again.

- v0.17.7 Exact-audio identity / UI cleanup: fix metadata-safety precedence so a literal acoustic carbon copy is not split just because MusicBrainz supplies different Recording MBIDs. After the audio matcher accepts a pair, an exact full Chromaprint fingerprint with effectively identical fingerprint duration is definitive identity; a shared ISRC with the same credited performers is also stronger identity evidence than a conflicting recording MBID. Regression case from the 2026-10-01 comparison log: `2024-01-26 - Cheeky [3617383011416]` and track 1 of `2024-03-01 - Everything Or Nothing #DQH1 [3617384238799]` are both 142.731973 s, share ISRC `ROGRA2400031`, and produced perfect audio similarity (score 0, good/excellent/overlap 1.0, median/p90 0), but v0.17.6 rejected the merge solely because MBIDs `79004bfd-4abb-4579-8aa8-4db4ed89cf8f` and `aa17fa8a-b5e8-4120-9a4d-128ef488bd6c` differ. That false split caused both releases to be retained and made the UI call the carbon copy a Version. Also implement the pending UI notes: move track-level 💎 into the same alternatives/actions cell as `Versions N` / `Remixes N`, and gray/de-emphasize the entire row for `Skipped by active options` tracks while keeping it readable.

- v0.17.8 Acoustic-first duplicate identity: per user correction, ISRC and duration are not reliable identity authorities. Remove ISRC from candidate discovery and all merge/veto precedence. Remove duration from weak-candidate gating, same-base-title fallback, mastering acceptance, and final identity gating; duration remains log-only diagnostic metadata. Same-base-title tracks are acoustically compared regardless of duration, and weak fingerprint-token candidates no longer require duration proximity. To distinguish a real alternate edit/version from a carbon copy, use aligned Chromaprint frame coverage plus unmatched fingerprint-content analysis: when aligned acoustic coverage is below 94%, unmatched content must be fingerprint-silence. Add a definitive-acoustic identity threshold (near-perfect score/good/excellent/overlap/median/p90) that overrides conflicting MusicBrainz Recording IDs. The `Cheeky` regression must therefore merge because of its perfect acoustic evidence, not because of ISRC or duration.

- v0.17.9 Gem consistency: remove the separate looser release-level Gem calculation. `track_is_gem` is now the single source of truth: a release gets 💎 only if at least one retained/included track in that same release gets 💎. This prevents impossible UI states such as `INNA - Body And The Sun [Japan] (2015) [FLAC]` showing a release Gem while none of its track rows show a Gem. Album release Gems remain allowed, but only when an actual track inside the album satisfies the strict no-duplicate + no-other-non-remix-version rule.


- Permanent recording-root taxonomy: a Version is a replaceable edit of an already-defined recording root. Radio/Single/Album/Main edits may be Versions. Base `Extended Version` / `Extended Mix`, Instrumental, Acapella, Acoustic, Remix, and Live/Unplugged are unique recording roots. Edit/Radio/Extended forms inherit an existing Remix/Live/Acoustic/Instrumental/Acapella parent root instead of becoming a new base-song Version. Release Map counts/connections and Gem logic must follow recording-root parentage.

- Permanent taxonomy clarification: `Chillout Mix` is a **Remix**, not a Version. It must be counted/shown with Remixes and must not increase `Versions N` or disqualify 💎 as another Version.

- v0.17.10 Zero-badge cleanup: remove the neutral `0` unique-track bubble. A retained release with zero unique tracks has no badge; only positive unique counts are rendered numerically, while 💎 and DUP keep their existing meanings. The permanent RULES entry was corrected accordingly.

- Permanent taxonomy clarification: parent-family precedence applies. A radio/extended/edit suffix attached to a remix-family recording stays Remix. Example: `Gimme Gimme (Sebastien Radio Edit)` = Remix, not Version. `Radio Edit` describes the edit of the Sebastien remix. This must count under `Remixes N`, never `Versions N`, and must not disqualify 💎 as another Version.

- v0.17.11 Permanent family taxonomy implemented: Versions, Remixes, and Live recordings are distinct in analyzer state, Release Map alternative counts, expansion panels, relationship lines, and Gem logic. `Chillout Mix` is explicitly Remix. Contextual parent-family precedence is implemented conservatively: on a remix release, a named child edit such as `Sebastien Radio Edit` inherits Remix only when the same base song contains an explicit matching parent such as `Sebastien Remix`; this avoids globally treating producer-named ordinary radio versions as remixes. Live recordings, including titles such as `Live Version`, are classified Live and do not increase `Versions N`. Gem tests count only Version-family alternatives; Remix and Live families do not disqualify a Gem. This fixes `Read My Lips` being denied 💎 solely because `Read My Lips (Live Version)` exists.

- v0.17.12 Initial vs Result impact counters / logs-folder shortcut: Release Map permanently displays the first Analyze plan as `Initial: <releases> releases / <tracks> tracks` and the latest completed plan beside it as `Result: ...` with cumulative signed release/track deltas. Initial is immutable across any number of Ignore/Re-Analyze cycles and close/reopen of the same live map; a brand-new Analyze establishes a new baseline. Pending ignores do not fake a predicted result: the last computed Result stays visible and is marked `pending Re-Analyze`. The bottom result drawer also shows Initial -> Result counts. Track count means counted tracks in retained releases, so active-option/manual skipped tracks are excluded. Added a compact folder icon immediately beside the Logging checkbox that creates/opens the program-isolated logs directory under `Documents\\Karpuzikov Tools\\Duplicate Edition Analyzer\\logs`. Also corrected the main UI matcher label to `Chromaprint (audio only)` because duration is diagnostic only.

- v0.18.0 WebEngine main UI rewrite: replace the active Tkinter main application shell with the same PySide6 + Qt WebEngine + HTML/CSS/JavaScript technology used by Release Map. Preserve the analyzer backend, runtime dependency bootstrap, settings/data paths, one-file .pyw packaging, analysis/review/apply behavior, and automatic Release Map opening. The WebEngine main UI now owns source folder selection, Save Remixes, Save Live recordings, Personal Picks editing, Logging and logs-folder shortcut, progress/activity, Analyze, Undo, Release Map, unusual track-pattern review, errors/info, and the Apply completion summary. Long-running analysis, map, Apply, and Undo operations stay off the UI thread and push live state/progress through QWebChannel signals. The old Tkinter classes remain in source only as update-compatibility/rollback code but are no longer launched by default.

- v0.18.1 Release Ignore inspect/restore workflow: manually ignored releases stay visible in their chronological map position instead of being filtered out by manual_removed. Add explicit Pending Ignore, IGNORED BY YOU, and Pending Restore states. The ignored release remains selected/open after Re-Analyze. For every included track in an ignored release, show the best retained exact-recording source from the current plan plus the number of additional retained exact copies; if no exact retained carrier exists, show a red NO REPLACEMENT warning. Repeat track-level source assignments in the Re-Analyze result drawer. Restore release is always available for a blocked release; Re-Analyze reverses the planning constraint. Pending-release IDs now mean changes awaiting Re-Analyze and are cleared after successful re-analysis.

- v0.18.2 WebEngine JSON serialization / Gem styling: fix `Object of type WindowsPath is not JSON serializable` after Apply by returning the duplicates directory as a string and adding a dedicated Qt/WebEngine JSON serializer that converts any Path/WindowsPath crossing the bridge to text (and sets to arrays). Use it for both main-window state/events and Release Map state so future path-bearing UI payloads cannot reproduce the crash. Apply the pending Gem styling request in the same update: remove the red background/bubble from Release Map 💎 badges and render only the diamond emoji in its existing location.


- v0.19.0 Combined-knowledge exact global optimizer: apply the compatible Coverage Atlas optimization lessons to Duplicate Edition Analyzer while preserving DEA as its own product. Fingerprint remains the sole duplicate authority; Release Map, Existing/Recycle, Personal Picks, Ignore/Restore, and Apply-time duplicate moves remain unchanged. Keep the previous rule-respecting optimizer as a seed, derive source-medium + Existing quality floors from that valid plan, then solve every connected recording/album coverage component exactly with branch-and-bound. Required constraints are every active recording group plus at least one active album per album family. Inside the preserved quality floors, minimize physical retained audio files first, then counted tracks, retained releases, Recycle releases, and finally stable release-ID order. Re-Analyze uses the same exact solver without re-fingerprinting. When detailed Logging is enabled, append an optimizer JSONL record with component/state/requirement and before/after release/file counts. RULES.md is aligned with the already-implemented v0.17.8 fingerprint-authority behavior and the active absolute-final Explicit-over-Clean exact-equivalent tie-break.

- v0.19.1 FFmpeg/FFprobe bootstrap fix: keep WinGet as the first dependency path, but do not assume a successful WinGet install immediately appears in PATH or Microsoft\\WinGet\\Links. Discover ffmpeg.exe + ffprobe.exe directly inside the WinGet package store and the analyzer's own dependency tree. If WinGet cannot produce a usable pair, download the official Gyan release-essentials ZIP and extract it under Documents\\Karpuzikov Tools\\Duplicate Edition Analyzer\\dependencies\\FFmpeg. Report the real WinGet/direct-download failure details instead of only the generic dependency error.

- v0.20.0 standalone dependency bundle: publish a single Windows EXE containing the Python runtime, PySide6/Qt WebEngine, FFmpeg/FFprobe, Chromaprint/fpcalc, hey-bro-check-log, and CUETools/ARCUE. Standalone mode never invokes WinGet/pip or downloads a missing runtime dependency during normal analysis; bundled binaries are the known-working fallback. Source/development .pyw mode retains dependency bootstrap behavior. A fast GitHub Releases metadata check runs at normal standalone startup with a short timeout; no update/offline/error launches normally. A newer DEA release downloads the EXE plus SHA-256 sidecar, verifies the checksum, schedules replacement after the current process exits, removes the old versioned EXE, and restarts the new version. Release Map subprocess launching is frozen-app aware. GitHub Actions builds the one-file EXE, downloads/checks the native bundle, launches every bundled dependency in a standalone smoke test, creates the SHA-256 file, and publishes the verified release asset.

- v0.21.0 dynamic-range/mastering quality comparison implemented: exact-equivalent release candidates are measured with bundled FFmpeg using EBU R128 + astats. Track metrics include integrated LUFS, LRA, true peak, RMS, crest factor and a deterministic dynamics score. Measurements are cached under the analyzer cache and reused on subsequent runs. Dynamics never creates duplicate identity and only acts after source/rip-integrity constraints; a conservative material-difference threshold is required before it can override Existing-copy preference.

- Planned v0.21.0 unusual-phrase review cleanup: the Live / remix phrase review must show only genuinely unusual or ambiguous phrase families that normal classifiers do not already understand. Ordinary remix/mix families must never appear simply because they repeat often. Automatically classify and hide standard forms including `<name> Remix`, `<name> Mix`, `<name> Vocal Mix`, `<name> Club Mix`, `<name> Radio Mix`, `<name> Extended Mix`, `<name> Push Up Mix`, `<name> Convertible Mix`, and equivalent normal credited remix/mix labels. Examples currently incorrectly shown and which must be hidden include Hybrid Mix, Armin van Buuren Remix, BT Remix, Libra Mix, Maor Levi Remix, Mark Norman Remix, Sean Tyas Remix, Sultan & Ned Shepard Remix, Junkie XL Vocal Mix, Adam K & Soha Remix, Cedric Gervais Remix, Digital Stories Remix, Dylan Rhymes Push Up Mix, Ferry Corsten Mix, Funkagenda Mix, Josh Gabriel Remix, Sander Kleinenberg's Convertible Mix, ALPHA 9 Remix, Andy Duguid Remix, and Brothers In Rhythm Mix. The review is for unusual/unknown patterns only, not for browsing ordinary remixes. Existing Personal Picks must not force otherwise-standard phrases into the review. [SUPERSEDED by v0.22.11.]

- Planned v0.21.0 path-open UI rule: whenever any filesystem path is shown anywhere in the Duplicate Edition Analyzer UI, place a folder/open-location icon immediately before that path. Clicking it must open that exact path in Windows Explorer. If the displayed path is a file, open Explorer with the file selected when possible; if it is a directory, open that directory directly. Apply this consistently across the main window, Release Map, review dialogs, error/details views, logs, result/action views, dependency/status views, and any future UI that exposes a local path.

- v0.21.0 Live/remix phrase review fix implemented: the review is shown only when Save Remixes is enabled, and ordinary Remix/Mix/Dub/Redux/Sped Up/Slowed/Reverb-style phrases are suppressed so the screen is reserved for unusual/ambiguous exception phrases. Existing Personal Picks remain pre-checked. [SUPERSEDED by v0.22.11.]

- v0.21.0 comparison engine performance fix implemented: replaced giant ordered `ProcessPoolExecutor.map()` chunks with bounded 32-pair batches and completion-driven scheduling. Progress now begins as soon as batches complete and shows comparisons/sec plus ETA. Identical Chromaprint vectors are resolved without full alignment, and already-unified acoustic pairs are skipped transitively without changing final grouping semantics. A bytearray tracks completion for low-memory serial fallback instead of retaining millions of processed pair tuples.

- v0.21.1 early Remix/Live elimination implemented: after metadata classification, unchecked Remix/Live tracks are removed from every later analytical stage. They are not fingerprinted, candidate-indexed, compared, grouped, dynamically measured, or optimized. Entirely excluded releases also skip CD-log/CUETools quality work. They remain only as filesystem bookkeeping for Apply. The sole Save-Remixes exception is an explicit featured-artist remix; feat./ft./featuring is detected from title, filename or ARTIST metadata. Featured remixes still obey Save Live independently.

- v0.22.0 commentary-driven rule correction: apply the reviewed two-pool examples as executable analyzer behavior. Remove external recording identifiers from probing, candidate discovery, diagnostics, and persistent skip identity. Remove AccurateRip/CTDB/CUETools verification and the standalone CUETools dependency; hey-bro-check-log is the only CD-rip quality scorer, with 80 as the acceptable threshold. Treat explicit semantic Version families as unique and do not cross-compare Radio Edit/Extended/Acoustic/Instrumental/etc. Make acoustic duplicate identity fully automatic: accepted acoustic matches merge regardless of title/artist naming differences; non-accepted matches remain separate automatically. Apply Explicit > Clean before fingerprinting. Add Compilation as a lower-priority release type whose non-unique groups are discarded when regular Album/EP/Single releases carry them. Optimize by total retained track count, including excluded extras physically carried inside a retained release; CUE image layout receives no one-file optimization discount. DR/mastering has no material threshold: after source/log/track-count rules tie, any measurable better dynamics score wins; Existing is only the later tie if DR also ties. Track-based CD rips with a real EAC/XLD log are recognized as CD even without CUE. Remix-only/Live-only releases continue to stop after step-1 classification and never enter later comparison/optimization. Under construction ⚠️


- v0.22.1 zero-manual-input acoustic identity: remove the acoustic Same recording decision from the user flow. A pair that passes the acoustic matcher is merged automatically even when titles, spelling or artist credits differ. A pair that does not pass remains separate automatically. The `Voigt Kampff Test - BT & Nick Phoenix` vs `Voigt Kamff Test - BT` regression case (score ~0.1152, 100% overlap, 100% good, definitive acoustic identity) must auto-merge with no prompt. This supersedes the v0.22.0 manual-review experiment.


- v0.22.2 comparison/DR performance and audit pass: replaced the near-all-pairs weak fingerprint route with bounded duration-neighborhood routing plus normalized/IDF token evidence, common-token suppression, rare-token rescue, high-confidence-first scheduling, transitive skipping, and deterministic shadow validation/recovery. Duration remains candidate-routing metadata only and never duplicate identity evidence. Expanded semantic Version protection for named `... Mode` descriptors and release-level families such as `Extended Versions`; normalized mojibake/tag-edge garbage before routing; preserved fingerprint failures as conservative singleton wanted groups. Replaced repetitive multi-gigabyte comparison rows with a normalized track catalog, compact diagnostic pair rows and aggregate funnel/route/duration counters. Moved DR completely behind the normal optimizer: only exact interchangeable releases still tied on every non-DR criterion (including Existing/Recycle and Explicit/Clean state) are measured, so ordinary runs may perform zero DR work.


- v0.22.4 semantic-version false-unique fix: the 2026-10-05 0.22.3 comparison log showed 168 byte-identical Chromaprint pairs blocked by semantic wording and thousands of same-base-title semantic skips. Correct candidate routing so exact identical fingerprints always bypass metadata, one labeled side vs one unlabeled side is never a hard conflict, and shared semantic/release family context remains acoustically comparable. Only two explicitly stated disjoint version families may be pre-skipped. Regression cases include `Four (Original Mix)` vs `Four` and `Tomahawk (Original Mix Edit)` vs `Tomahawk`.


- v0.22.5 remix exclusion / edit-matching correction: the 2026-10-05 0.22.4 log showed 203 featured-remix exceptions, with 178 carrying only feature credits already present on normal versions of the same song. The exception now requires a feature added by the remix, keyed by base song + primary artist. Contextual Remix inheritance no longer depends on release titles or same-release placement: remixer evidence is collection-wide and scoped by base song + primary artist, so a Remix on one release can classify the same remixer's Radio Edit/Mix Edit/Re-Interpretation on another release. Confident possessive remixer labels such as Simon Hale's Orchestrata are also recognized. The semantic prefilter now uses only strong structural families; weak named Edit/Mix labels are sent to Chromaprint. Regression coverage includes Godspeed Radio Edit vs BT Edit, Remember American Radio Edit vs Album Edit, and Remember Edit vs Single Mix.


- v0.22.6 Release Map interaction correction: track Ignore/Restore is exact physical-instance state rather than recording-group/base-title state. Pre-v0.22.6 broad skip rules are ignored because they cannot be mapped back to one intended file safely. Track/release toggles now return small UI deltas and update the open row immediately; the expensive full map payload is cached and rebuilt only after Re-Analyze, eliminating the multi-second UI freeze on every Ignore click. Release Map track titles wrap to full multi-line text with a full-title tooltip instead of permanent ellipsis truncation.

- v0.22.7 Personal Picks / unusual-pattern integration: **Unusual track pattern review** now uses Personal Picks as its persistent state instead of a separate hidden preference map. New unusual patterns start unchecked. Checking one keeps it for the current run and stores it as a persistent `Pattern` Personal Pick. Existing Pattern picks appear checked/locked in the review and can later be removed from the normal **Personal Picks** editor; removing one is authoritative and it will not be silently restored by legacy state. Unchecked patterns remain current-run exclusions only. Positive legacy `unusual_pattern_preferences_v5` choices migrate forward once into Personal Picks; negative legacy choices are discarded. Pattern-mode rules are isolated from phrase matching so they cannot accidentally preserve Remix/Live tracks by substring.
- v0.22.8 album completeness / replaceable-Version correction: every album family must be represented by an edition with the maximum number of distinct included wanted recording groups. A strict same-album superset immediately dominates its subset before the global optimizer; singles/EPs elsewhere can no longer make the smaller album edition win. After album/wanted-group requirements, minimize total retained tracks first and retained releases second; source/log/Existing are later tie-breaks. Semantic recording roots are corrected so base Extended, Instrumental, Acapella, Acoustic, Live/Unplugged and Remix stay unique, while Radio/Edit variants can be replaceable and an Edit/Extended form inherits an existing unique parent root. Regression tests cover the `Let Go (Limited Edition)` vs `Let Go (Sketch Book)` failure and the track-count/release-count objective order.
- v0.22.9 universal-compliance pass: apply the global release-blocking software rules and current UXDT baseline to the existing DEA UI, not only new controls. All visible interactive filesystem paths in the active UI now use an adjacent opener: Existing discography, New / update releases, Completed-result duplicates path, logs, and Release Map release path. A shared path-opening helper opens directories directly and reveals files in Explorer when applicable. Add keyboard-visible focus, labeled inputs/search, progress/log/dialog ARIA semantics, keyboard activation for Release Map releases/tracks, and focus-visible Ignore controls. Standalone self-test blocks release if these path-control or core accessibility invariants regress. Release Map now labels the destructive step `Apply file changes`, requires an explicit confirmation before filesystem changes, and reports missing displayed paths instead of silently opening a different parent location. Error/info dialogs are path-aware: detected Windows filesystem paths automatically receive an adjacent opener, preventing future diagnostic messages from bypassing the global path invariant.
- v0.22.10 Live/Remix review gating correction: Personal Picks phrase review is now strictly an exception UI for categories the user chose not to save. `Save Remixes` ON suppresses Remix candidates; `Save Live recordings` ON suppresses Live candidates; both ON skips the review entirely. Added a standalone regression test for the both-enabled case. [SUPERSEDED by v0.22.11: the review was removed entirely.]
- v0.22.11 removal of obsolete Live/Remix phrase review: delete the automatic phrase detector/review stage from both active Qt and legacy Tk flows, remove its dialog/event/submit code and detector helpers, and keep only Unusual track pattern review for genuinely unknown version-like descriptors. Known Remix/Live/Radio/Extended/Acoustic/Instrumental/Acapella/tempo-effect semantics are classified automatically. Add a release-blocking regression check preventing the removed review from returning.\n- v0.22.12 callout unusual-pattern alias grouping: `Suggested Callout Hook`, `Call Out Hook`, and `Suggested Call Out Research Hook` now resolve to one stable `suggested callout` key and one `Callout hook` review row. Preserve the historical key for existing Personal Picks and add self-tests that fail if these aliases split again.
