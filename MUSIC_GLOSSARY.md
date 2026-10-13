# Shared Music Recording and Credit Glossary

Last updated: 2026-10-13

**Canonical semantic vocabulary for TRRNT-project, Music Collection Cleaner (MCC), Duplicate Edition Analyzer, and the planned ALAC filename organizer.** This glossary is derived from the existing MCC `RULES.md` taxonomy and the latest user clarifications. All participating tools must apply the same **meanings** to track titles and artist credits, while retaining tool-specific algorithms. For MCC's actual implemented classification and audio-routing contracts, consult [MCC RULES.md](https://github.com/karpuzikov/MCC/blob/main/RULES.md) and [current MCC source](https://github.com/karpuzikov/MCC/blob/main/Music%20Collection%20Cleaner%20v0.5.18.pyw). This glossary documents shared terminology and intended naming; it does not claim the filename organizer exists yet.

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

**Separator/markup caution:** The supplied `[ft. ...]` example encodes a feature credit in square brackets and `(Remix)` encodes the remix descriptor in parentheses. They are **different semantic fields**, not interchangeable spellings. MCC's internal `canonical_featured_title()` may represent `[ft. ...]` and `(ft. ...)` alike for **comparison keys**; that is *not* a mandate to rewrite every on-disk credit to parentheses. The universal output filename grammar/credit punctuation beyond the approved examples remains subject to user confirmation. Do not infer a remix-added feature from bracket punctuation alone.

## Required shared rules

1. Parse **release type**, **recording root/version**, **remixer**, **featured artists**, and **source medium** as distinct attributes. They can coexist.
2. Preserve the complete original credited performers and meaningful version descriptors when naming. Do not strip `(Remix)` because an `[ft. ...]` appears; do not strip the feature when recognizing the remix.
3. Identify genuinely **new** featured performers by comparing with the ordinary recording or by MCC's verified explicit remix-attached-credit rule; do not treat every feature or artist-tag collaborator as remix-added.
4. Do not identify a named remix merely from any occurrence of the word `Mix`: MCC exempts `Original Mix` and ordinary functional mixes absent remixer context. Keep its recognized Remix / Live / Acoustic / Instrumental / Acapella and Version-family semantics.
5. Do not classify every track in a plural `(Remixes)` release as a remix solely from the release folder title. Analyze tracks individually.
6. Release folder naming uses the separate [TRRNT RULES.md](RULES.md) grammar. Notably `Plain Jane REMIX - Single` -> `Plain Jane (Remix) - Single`, whereas `Title - The Remixes EP` -> `Title (The Remixes)`. These are **release-folder** transformations, distinct from track filename parsing.
7. If a proposed filename normalization cannot preserve all semantic fields or there is ambiguous provenance, flag for review rather than discard/guess. Any eventual file operation must provide preview, collision safeguards, CUE reference consistency, and Undo.

## Scope / implementation status

This is a **single shared terminology reference**. MCC already implements its own version/remix/feature classification (see its source and regression checks). No new ALAC filename normalizer or cross-tool shared library has been implemented or Windows-tested solely by this documentation update. Semantic classifiers must remain consistent; the future filename renderer will implement approved display formatting separately.
