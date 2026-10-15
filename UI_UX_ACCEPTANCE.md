# UI/UX Acceptance Register

**Policy:** [UI_UX_RULES.md](UI_UX_RULES.md) and [SOFTWARE_RULES.md](SOFTWARE_RULES.md) are mandatory for every user-facing screen, panel, modal, widget, context menu, installer, browser toolbar, and interactive console prompt in this repository.

**Status as of 2026-10-12:** **NOT VERIFIED - Under construction ⚠️ for UI acceptance.** This is a documentation and source-inventory checkpoint, NOT a claim that existing applications have been retested or that published stable version labels were changed. No Windows/browser interactive visual run or reference-screenshot comparison has been performed in this audit. Existing stable releases are not silently altered.

Before ANY UI modification, record a control contract for the affected workflows: **control name/ID, unique purpose, click/right-click/hover/keyboard behavior, selection, side effects, persisted data, cancel/error handling, disabled/focus/active states, icon and target geometry**. Eliminate duplicate actions; share design tokens/components.

### Required acceptance process for EACH UI application

1. Run **the actual application**, on its target OS/browser/Picard host, with its real GUI renderer (not mocks).
2. Screenshot each screen and critical state, using at least minimum/default/large viewport and relevant DPI scaling; record image paths and viewport.
3. Compare screenshots to named reference/source UI. Check content hierarchy, alignment, 4px-based spacing, sizing, icon strokes, buttons, image loading, scroll/overlay, accessible labels, and platform expectations.
4. Click/hover/right-click every control; test tab/Enter/Space/Escape, selection/multi-select, focus, resize, scroll, navigation, settings persistence, confirmation/cancel and failure recovery.
5. Include representative end-to-end tasks, empty/large data, missing images/network, interrupted processing, and safe restart. Test any file-changing/destructive action against disposable fixtures.
6. Record **PASS / FAIL / NOT RUN** separately for real rendering, interaction, reference comparison and workflow validation; never equate syntax tests or mocked widgets with a real PASS. Fix all FAIL, complete all NOT RUN before marking UI verified.

| Universal acceptance gate | Status |
| --- | --- |
| UI rules present and required before implementation | PASS - repository-level rule installed |
| Control-by-control inventory and no duplicate actions | NOT RUN |
| Real UI launch with screenshots and reference comparison | NOT RUN |
| Hover, click, keyboard, selection, scroll, resize | NOT RUN |
| Error, cancellation, persistence, restart, recovery | NOT RUN |
| Real host/OS end-to-end workflow and final acceptance | NOT RUN |

## UI surfaces: monorepo

**Desktop Python GUI utilities:** Corrupt Image Verifier (`image-tools/corrupt-image-verifier/`); Duplicate Edition Analyzer (latest of versioned `.pyw` sources under `audio-tools/discography-torrent-project/tools/`); ALAC Conversion Automation; AudioChecker Automation; CUE Corrector; Folder Structure Scanner; CUE LOG; DriveV AutoInstaller; Priorities. Treat older versioned files as historical; audit the latest active version of each and applicable active development branches.

**Browser and MusicBrainz userscript UI:** Instagram Full Quality, Spotify Hidden Releases, Genius Lyrics Copier, RuTracker Digital Release Linker, SimpCity Media Link Parser, Beatport - MusicBrainz Importer, MusicBrainz Toolbox, Harmony Toolbox, Apple Music Composition/Lyrics Importer and any other scripts that insert controls, overlays, menus or dialogs. Browser Debug Bridge's `popup.html` and related CSS is in scope. Inspect remaining scripts for controls before excluding them.

**Interactive console/host scripts:** ArchiveMedia, Windows backup/install/requirements tools if they present menus, confirmations, progress or paths; Photoshop scripts if they present dialogs.

### Source audit findings (actual files inspected)

- **FAIL - framework policy:** `Corrupt Image Verifier v1.1.7 NATIVE TEST.pyw`, `Priorities v0.1.0.pyw`, `ALAC Conversion Automation v0.2.pyw`, `AudioChecker Automation v0.1.pyw`, `CUE Corrector - MusicBrainz DiscID 1.3.1.pyw`, `Duplicate Edition Analyzer v0.22.12.pyw` (contains both Qt and Tkinter code), `Folder Structure Scanner.pyw`, `CUE LOG 4.1.pyw`, and `DriveV_AutoInstaller.pyw` still contain Tkinter references. New GUI work must **not** add Tkinter; migration is required to satisfy the no-Tkinter policy. Preserve behavior during migration; no blind framework replacement without rendering and interaction verification.
- **NOT RUN:** Actual Windows/UI reference and real operation tests for every application.

### Project-specific acceptance focus

- **Corrupt Image Verifier:** multiple folders, drag/drop, drive-aware workers, cancellation/child cleanup, image classification and confirmation, preserve relative paths, Undo; visually compare compact native reference.
- **Analyzer:** interactive Release Map, filtering, worker selection/detection, preflight safety, overlays, progress, Copy/Move/Undo and collision/restart scenarios.
- **Media/audio:** file/path picker with folder icon before path, job queue/parallelism, error feedback, dependency bootstrap and process cleanup.
- **MusicBrainz/Beatport/Harmony:** no duplicate import actions, responsive edit-relationships controls, ISRC/barcode selection and warnings, keyboard and retry behavior, unchanged stored links.
- **Browser popups/userscripts:** injected controls in real host DOM, overlay stacking, conflicts with existing site controls, tooltips, focus and responsive behavior. Test real browser + Tampermonkey, not an isolated HTML mock.

### Music Library Organizer v0.3.13 - minimum-click acceptance (2026-10-14)

| Mandatory updated gate | Status | Evidence / outstanding action |
| --- | --- | --- |
| Canonical minimal-click rules synchronized from main into development branch | PASS | `SOFTWARE_RULES.md` and `UI_UX_RULES.md` now include radical simplicity requirements |
| Compact dark WebView2 source and control contract | PASS - source audit | Redundant tutorials/header text removed; source->auto-destination; one-click Organize from saved settings; all distinct stage/checker/worker/results actions preserved |
| Simulated bridge + keyboard-enabled DOM control state | PASS - mocked state only | Empty path disables Organize; source suggests destination; running/disconnected disables; legacy focus/drag/drop hooks retained |
| Real Windows UI screenshots at default/minimum/large viewport and DPI | NOT RUN | Run exact v0.3.13 `.pyw` on target machine |
| Real keyboard/hover/right-click, path browse/drop, settings persistence, real audio workflows | NOT RUN | Follow project UI control contract, verified native checker and disposable output, Undo |
| Whole-product native Python suite and acceptance | NOT RUN | Run py_compile, unittest and full end-to-end before promoting status |

### Music Library Organizer v0.3.17 - title case, CUE and MusicBrainz acceptance (2026-10-15)

| Workflow/control | Status | Native acceptance criteria |
| --- | --- | --- |
| GIMS 2021 Best life, 2024 SIN TI, JOE PESCI | NOT RUN (seven additional mock tests defined, full suite unexecuted) | Folder, track filenames, and TITLE tags use Best Life, Sin Ti and Joe Pesci; preserve 3OH!3 and protected names |
| Actual 88883792232.cue and six cross-file INDEX 00 cases | NOT RUN | Preserve per-file INDEX 00 + next-file INDEX 01 references and original cue bytes; rename FILE mappings, no false FAILED |
| Invalid single-image INDEX 00 after INDEX 01 | NOT RUN | Refuse unsafe cuts; preserve image and cue with appropriate PARTIAL reason |
| Arhbo 197089993382 | NOT RUN (mock regression defined) | Exact MusicBrainz release, two Digital Media tracks, Soundtrack + Single -> parenthesized descriptive subtitle with `- Single` |
| No changed UI controls | NOT RUN | Existing Results selection/Copy, Open log/folder, progress, Undo, keyboard and dark visuals still work |
| Python compile, 102 regression tests and actual Windows GUI/files | NOT RUN | Execute on exact v0.3.17 source and test real GIMS fixture without modifying originals |

### Music Library Organizer v0.3.16 - per-run isolated logs native acceptance (2026-10-15)

| Workflow/control | Status | Actual Windows acceptance |
| --- | --- | --- |
| Multiple Organize runs in one session and after restart | NOT RUN (source checks; tests defined) | Different files in `logs/` with timestamp+unique suffix; earlier logs unchanged, full errors/timings preserved |
| Undo/cancel/failure | NOT RUN | Separate Undo/run logs, preserved diagnostics, no accidental overwrites or source edits |
| Legacy log migration | NOT RUN | Old `activity.log`, WebView2 and startup-error files preserved in `logs/legacy/` even on collisions |
| Open log / adjacent folder icon | NOT RUN | Open current/latest operation log and its dedicated folder with native keyboard/mouse |
| Startup and permissions | NOT RUN | Separate new startup/crash logs; log allocation failures visible; existing running instance untouched |
| Full Python compile/95 regression suite and real Windows GUI screenshots | NOT RUN | Run on exact v0.3.16 candidate; mock/static validations are not real acceptance |

### Music Library Organizer v0.3.15 - duplicate launch native acceptance (2026-10-15)

| Required state | Current status | Expected result |
| --- | --- | --- |
| Second invocation with main organizer already open | NOT RUN (mock regressions committed) | Existing versioned window focused; second exits without an error dialog or new Undo/profile |
| Previous window minimized | NOT RUN | Restore original window via `SW_RESTORE`, request focus |
| Existing organizer processing an album | NOT RUN | No job interruption, no concurrent write, no forced termination |
| Existing mutex with no discoverable window | NOT RUN | Short native information message, no exception traceback; do not discard live mutex |
| Original source/webview integrity | PASS (source/static) | Single dark WebView2 and native auCDtect remain; no new visible controls |
| Full Windows UI/UX and Python suite | NOT RUN | Execute installed candidate and regression suite with real Windows API |

## Next action

Fill the control contract and real-world screenshot/interaction matrix for every UI surface above. Fix all FAIL and NOT RUN before declaring UI verified. Preserve software behavior and target-platform compatibility.
