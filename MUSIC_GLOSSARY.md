# Shared Music Recording and Credit Glossary

Last updated: 2026-10-13

**Canonical semantic vocabulary for TRRNT-project, Music Collection Cleaner (MCC), Duplicate Edition Analyzer, and the planned ALAC filename organizer.** This glossary is derived from the existing MCC `RULES.md` taxonomy and the latest user clarifications. All participating tools must apply the same **meanings** to track titles and artist credits, while retaining tool-specific algorithms. For MCC's actual implemented classification and audio-routing contracts, consult [MCC RULES.md](https://github.com/karpuzikov/MCC/blob/main/RULES.md) and [current MCC source](https://github.com/karpuzikov/MCC/blob/main/Music%20Collection%20Cleaner%20v0.5.18.pyw). This glossary documents shared terminology and intended naming; it does not claim the filename organizer exists yet.

## Soundtrack attribution in Single/EP output names (2026-10-15)

For Singles and EPs, a trailing **source-production attribution**, e.g. `[Music from the FIFA World Cup Qatar 2022 Official Soundtrack]`, `(From the Original Motion Picture "Ralph Breaks The Internet")`, `(From the Original Motion Picture Soundtrack ELVIS)` or `(Music from the HBO Original Series)`, is **not the song's essential base title or a recording version**. Strip the entire standalone promotional `(...)`/`[...]` qualifier from the output *release/folder title, song/track filename, ALBUM/TITLE tag and safely rewritten CUE TITLE*, while preserving the genuine song or release name and its real mix/feature and numbered episode/part designators. Preserve ` - Single`/` - EP` on release titles only, not on each track.

Examples: `Zero - Single`, `Vegas - Single`, `The Idol Episode 4 - Single`, `The Idol Episode 5 Part 1 - Single`, `The Idol Episode 3 - EP`, `The Idol Episode 5 Part 2 - EP` and `Arhbo - Single`. This applies to both `(Music from ...)` and `[Music from ...]` variants without hardcoding one show/movie. Keep genuine artistic title components, feature/remix notation, and album/compilation titles; never remove the whole title or repurpose this rule as a destructive source-file cleanup. For lookup and edition verification, retain authoritative unstripped MusicBrainz/source titles and identifiers; **remove the attribution only in rendered output names/tags**. The full strict acceptance contract is in [TRRNT RULES.md](RULES.md).

**Documented naming requirement, NOT YET IMPLEMENTED in Music Library Organizer v0.3.17.**

## Core semantic terms

| Term | Meaning | Important distinction |
| --- | --- | --- |
| **Base title** | The underlying song title without recording-version qualifiers or featured-artist notation, for comparison/context only | Stripping descriptors for a routing key must not delete them from a saved filename |
| **Original/base recording** | The relevant normal recording used as a baseline when comparing versions and additional performer credits | The same song title does not prove identical recording audio |
| **Recording root** | A semantically distinct recording form; e.g., original, named remix, live, acoustic, instrumental or a cappella | MCC distinguishes roots before eligible acoustic comparisons |
| **Remix** | A remix recording/version indicated by a genuine remix descriptor, e.g. `(Remix)`, `(Dr. Luke Remix)`, credited named mix, or equivalent recognized descriptor | **A remix is not a featured artist.** An individual remix may be a distinct recording root |
| **Remixer** | The person/act responsible for a remix, potentially named inside the remix descriptor | Do not parse the remixer as a featured vocalist/artist just because the name is present |
| **Featured artist / feature credit** | An explicitly credited performer, e.g. `[ft. Nicki Minaj]`, `(ft. Lil Mama)` or a corresponding verified artist credit | A feature is a **credit**, not itself a remix, version, or acoustic identity |
| **Remix with featured artist** | A track that simultaneously has a remix descriptor and a featured-artist credit | Both attributes must survive parsing and naming; never flatten it to just "alternate featured artist notation" |
| **Remix-added featured artist** | A featured artist **not already present on the normal counterpart**, or a credit explicitly attached to the remix itself under MCC's careful comparison rules | MCC's `Save Remixes` OFF exception applies **only** when genuinely added by the remix; a plain `ft.` credit does not by itself establish this |
| **Edit / replaceable Version** | An eligible edit or variant within the same recording root, such as `(Radio Edit)`, subject to MCC's acoustic validation | `Version`, `Edit`, or `Mix` alone does not guarantee recording equivalence |
| **Distinct alternate root** | A version such as acoustic, live, instrumental, a cappella, named remix or a distinct extended base recording | These are not silently merged with the original by title resemblance |
| **Remix collection / plural Remixes** | A **release-title** descriptor, e.g. `The Remixes`, `Club Remixes` | Different from a single-track `(Remix)`; a release titled `Remixes` does not necessarily mean all its tracks are remixes |
| **Release type** | Single, EP, Album, etc. at the release level | Independent of a track's recording root, remix descriptor, and artist credits |
| **Source / medium** | Physical CD versus WEB/digital release | Independent of recording type or featured-artist status |

## Track examples and semantic parsing

| Track filename | Recording category | Featured artist credit | Interpretation |
| --- | --- | --- | --- |
| `01 Plain Jane.m4a` | Original/base (unless other verified metadata says otherwise) | Not in filename | Plain Jane base recording |
| `01 Plain Jane (Remix).m4a` | Remix | Not in filename | Individual remix recording |
| **`01 Plain Jane (Remix) [ft. Nicki Minaj].m4a`** | **Remix** | **Nicki Minaj** | **Remix with a featured artist**, not an alternative feature-format variant of the base recording |
| `01 Girlfriend (Dr. Luke Remix) [ft. Lil Mama].m4a` | Named Remix recording root | Lil Mama | MCC's documented example; distinct remix root |
| `01 Girlfriend (Dr. Luke Remix) [ft. Lil Mama] (Extended Version).m4a` | Version of the named Remix recording root | Lil Mama | Extended Version remains inside the Remix parent root |
| `01 Girlfriend (Radio Edit).m4a` | Radio Edit of eligible base root | Not in filename | A replaceable Version only when acoustic evidence supports it |

**Positional markup (latest user rule):** Title components use `()`, then `[]`, then `{}` in **display order**, not according to whether a component is a remix or a feature. A remix-added feature follows the remix descriptor; a preexisting original feature precedes the remix descriptor. MCC's internal `canonical_featured_title()` normalizes for comparison keys, not the on-disk display rendering. Do not infer feature provenance merely from `[]` versus `()`. See the complete track-file naming examples below.

## Exact physical audio filename pattern - zero-padded number and one space

The approved ALAC audio filename grammar is **`NN Track Title.m4a`**: minimum two-digit **zero-padded track number**, exactly **one space** as its only delimiter, then the **complete normalized track title**. **User example:** `01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a`.

- `01` is the track number (zero-padded); ` ` is the required separator; `Red Lips (ft. Sam Bruno) [Skrillex Remix]` is the full title. Keep `()`, `[]`, and `{}` title/credit ordering intact.
- No punctuation or extra spaces after the number: `01. Title.m4a`, `01 - Title.m4a`, `01_Title.m4a`, and `01  Title.m4a` are invalid. Punctuation *inside* the real title (such as `ft.`) is unaffected.
- The on-disk album track filename has **no artist prefix**. A `The Weeknd - Reminder ...` display example describes the separate `artist` and `title` fields rather than requiring those words in the filename.
- A file's extension must reflect its real format, and CD CUE `FILE` entries must remain valid when a file is renamed. Disc-specific numbering/exception cases will be finalized separately.

## Codec-specific extensions after splitting (ALAC and FLAC)

The file-name **stem** is codec-independent: zero-padded `NN`, exactly one space, then the full standardized track title (no artist prefix). With the newly requested image-splitter/output-format option, the real encoded output determines the extension: `NN Track Title.m4a` for ALAC or `NN Track Title.flac` for FLAC. Example: `01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a` versus `01 Red Lips (ft. Sam Bruno) [Skrillex Remix].flac`. This is a *filename* rule; see `RULES.md` for CUE-image splitting, PCM preservation, verification and source safety. Lossy outputs are a future possibility only.

## 2026-10-14 credit/title precedence update

**Newest user direction takes precedence.** Featured performers may appear in **individual track TITLE tags and audio filenames**, never in the **release/folder TITLE**. For a normal recording with a known base-feature credit, put `(ft. Artist)` directly after the song base title and the actual edit/version `[Radio Edit]`, `[Extended Version]`, etc. next. Example: `Diggy Down (Radio Edit) [ft. Marian Hill]` must normalize to `Diggy Down (ft. Marian Hill) [Radio Edit]` **in both tags and filenames**.

If a remix edition reverses the printed leading artist and feature credit (e.g. `Bob Taylor ft. INNA`) but source-supported collection consensus establishes `INNA ft. Bob Taylor`, the primary artist becomes `INNA` and the TITLE `Deja Vu (ft. Bob Taylor) [actual original remix version]`. Preserve every distinct variant's real descriptor. Never append a leading artist string inside `[...]` as a fake feature credit; if evidence is inadequate to infer credit orientation, preserve the credited artist and log unresolved state rather than guessing.

See `RULES.md` for folder-feature prohibition and DiscID/media-count validation.

## Track filename composition, feature provenance, and positional delimiters (user-approved)

This is a **track-level filename/title** standard, separate from the **release-folder** grammar. First classify the real attributes, *then* render them. A featured performer is never interchangeable with a remixer, even when both occur in one title.

### Ordering based on where the feature originated

1. **Feature introduced by the remix:** base track title -> remix descriptor -> featured-artist credit. Exact user example: `The Weeknd - Reminder (Remix) [ft. A$AP Rocky & Young Thug]`. In this case A$AP Rocky and Young Thug are credited on the remix rather than on the original version.
2. **Feature already on the original recording:** base track title -> featured-artist credit -> remix descriptor. Exact user example: `GTA - Red Lips (ft. Sam Bruno) [Skrillex Remix]`. Sam Bruno belongs to the base/original recording's artist credits; Skrillex is the remixer.
3. **More descriptors:** preserve their meaningful logical sequence after the base title. Exact user example: `Tiesto vs. Diplo - C'Mon (Catch 'Em By Surprise) (feat. Busta Rhymes) (Extended Club Mix)` -> `Tiesto & Diplo - C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}`.

**Do not infer a feature's origin merely from existing `()` or `[]` punctuation.** Compare with the original/ordinary recording and reliable performer credits, following MCC's semantics. When not resolved, preserve both roles and mark the placement/provenance for review; do not silently assert the performer was remix-added. The names and descriptors must never be discarded.

### Delimiter priority is positional, not tied to a semantic type

- **First separate title descriptor:** `( ... )`.
- **Second separate title descriptor:** `[ ... ]`.
- **Third separate title descriptor:** `{ ... }`.
- Count **in displayed title order**. This is why `(Remix) [ft. Artist]` and `(ft. Artist) [Named Remix]` are **both correct** for different relationships. Do not enforce a rule such as "featured artists always use brackets" or "remix is always parentheses."
- If the authentic base title already contains a parenthesized title component, **keep it** and count it as the first title component for bracket placement. Thus `C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}`.
- Preserve the actual descriptor words, track title and punctuation not implicated in the positional bracket normalization. Do not invent fourth-level notation where the three supported delimiter levels would be exceeded; flag such cases for review until defined.
- Standardize standalone featured-artist labels `feat.` / `featuring` / `ft.` to **`ft.`** in the rendered title; keep the authentic featured names and order. Do not confuse this **display formatting** with MCC's credit-canonicalization for audio comparison.

### Artist credit normalization based on the actual Picard script

Source of truth: [Format_Multiple_Artists.txt](https://github.com/karpuzikov/userscripts/blob/main/picard-tools/scripts/Format_Multiple_Artists.txt). It processes Picard `%artists%` (and `%albumartists%`) as multi-value artist credit lists, collapses redundant credits, and renders:

- **One credited artist:** retain that artist.
- **Two distinct credited artists:** `Artist A & Artist B`.
- **Three or more:** `Artist A, Artist B & Artist C` (comma delimiters, final ` & `).
- **`vs.` joining independently credited artists:** normalize the connective as ` & ` when the source metadata establishes distinct credited artists. Explicit user example: `Tiesto vs. Diplo` -> `Tiesto & Diplo`. The Picard script **does not** itself split an arbitrary single-string `vs.`; the filename stage must detect verified separate artists rather than blindly replace those letters inside any artist name.
- Source artist spelling/capitalization and genuine mononyms or group names must remain intact. The related [Move_Featured_Artists_to_Title.txt](https://github.com/karpuzikov/userscripts/blob/main/picard-tools/scripts/Move_Featured_Artists_to_Title.txt) moves feature credits out of artist/albumartist into title and normalizes `feat.` to `ft.`, but it is **not** a finalized filename delimiter renderer.

### Required acceptance examples

| Input / credited relationship | Correct output | Why |
| --- | --- | --- |
| `The Weeknd - Reminder (Remix) [ft. A$AP Rocky & Young Thug]` / feature added on remix | `The Weeknd - Reminder (Remix) [ft. A$AP Rocky & Young Thug]` | Remix descriptor comes before remix-added feature |
| `GTA - Red Lips (ft. Sam Bruno) [Skrillex Remix]` / feature already in original | `GTA - Red Lips (ft. Sam Bruno) [Skrillex Remix]` | Original feature comes before named remix |
| `Tiesto vs. Diplo - C'Mon (Catch 'Em By Surprise) (feat. Busta Rhymes) (Extended Club Mix)` | `Tiesto & Diplo - C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}` | Canonical artist joining, ft. prefix, and `()` -> `[]` -> `{}` in order |
| `GTA - Red Lips [Skrillex Remix] (ft. Sam Bruno)` when Sam Bruno is confirmed original feature | `GTA - Red Lips (ft. Sam Bruno) [Skrillex Remix]` | Reorder by actual original/remix relationship, not incoming brackets |
| `The Weeknd - Reminder (ft. A$AP Rocky & Young Thug) [Remix]` when features confirmed remix-added | `The Weeknd - Reminder (Remix) [ft. A$AP Rocky & Young Thug]` | Reorder based on real feature provenance, not source markup |

The `Artist - Track` notation in these demonstrations identifies both **artist-credit and title fields**. The user has since confirmed the physical audio-file template `NN Track Title.m4a`: minimum two-digit zero padding, one space, full standardized title, and **no artist prefix**. Apply credit/title transformations within the title without adding `Artist -` to the filename.

## Required shared rules

1. Parse **release type**, **recording root/version**, **remixer**, **featured artists**, and **source medium** as distinct attributes. They can coexist.
2. Preserve the complete original credited performers and meaningful version descriptors when naming. Do not strip `(Remix)` because an `[ft. ...]` appears; do not strip the feature when recognizing the remix.
3. Identify genuinely **new** featured performers by comparing with the ordinary recording or by MCC's verified explicit remix-attached-credit rule; do not treat every feature or artist-tag collaborator as remix-added.
4. Do not identify a named remix merely from any occurrence of the word `Mix`: MCC exempts `Original Mix` and ordinary functional mixes absent remixer context. Keep its recognized Remix / Live / Acoustic / Instrumental / Acapella and Version-family semantics.
5. Do not classify every track in a plural `(Remixes)` release as a remix solely from the release folder title. Analyze tracks individually.
6. Release folder naming uses the separate [TRRNT RULES.md](RULES.md) grammar. Notably `Plain Jane REMIX - Single` -> `Plain Jane (Remix) - Single`, whereas `Title - The Remixes EP` -> `Title (The Remixes)`. These are **release-folder** transformations, distinct from track filename parsing.
7. Apply user-approved track title ordering: original recording features **before** remix descriptors, remix-added features **after** remix descriptors; assign punctuation to the first, second and third separate title components as `()`, `[]` and `{}`, respectively. Artist credits use the sourced Picard multi-artist joining convention and `feat.` / `featuring` renders as `ft.`.
8. If a proposed filename normalization cannot preserve all semantic fields or there is ambiguous provenance, flag for review rather than discard/guess. Any eventual file operation must provide preview, collision safeguards, CUE reference consistency, and Undo.

## Scope / implementation status

This is a **single shared terminology reference**. MCC already implements its own version/remix/feature classification (see its source and regression checks). No new ALAC filename normalizer or cross-tool shared library has been implemented or Windows-tested solely by this documentation update. Semantic classifiers must remain consistent; the future filename renderer will implement approved display formatting separately.
