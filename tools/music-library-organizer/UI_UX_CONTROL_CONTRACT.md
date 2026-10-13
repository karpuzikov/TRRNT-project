# Music Library Organizer - UI Control Contract

Status: v0.3.0 - Under construction ⚠️. Reference: `karpuzikov/MCC` `Music Collection Cleaner v0.5.18.pyw` main WebView2 window `_webview_main_html()`.

## v0.3.0 UI amendments

- **AudioChecker executable** input (path) + Browse file selector. Auto-detect `achkgui.exe`, persist under this app, avoid changing shared app settings. Disabled while batch runs. Invalid path error must not be hidden.
- **Compliant/DONE vs PARTIAL** result separation. A release exported without genuine AudioChecker, with unresolved MusicBrainz catalog/metadata provenance, or without checked physical pressing cannot be reported fully completed. Show resulting rename source -> output, metadata source/provenance and complete per-release warning/reason in the run JSON and activity.
- Preserve v0.2.0 no-REVIEW automatic approach and MCC colors/buttons. Follow real visual testing gate; **all Windows test slots remain NOT RUN**.

## Active controls

The **v0.2.0 automatic organizer** control contract below supersedes older 0.1.x Scan, Select Ready, Details and Apply contracts retained historically later in this file. These old controls are removed from the UI. The one normal action is **Organize**; the results table is not a blocking review interface. Real Windows acceptance remains NOT RUN.

## Shared visual system
MCC source palette: `#0b0c0f` background, `#11141a` card, `#171b22` secondary, `#181c23` field, `#2a303a` divider, `#f3f6fb` text, `#9ca7b8` muted, `#66a9ff` accent, `#ef4444` error, `#f59e0b` review, `#22c55e` success. Segoe UI Variable/Segoe UI; 58px toolbar, 34px buttons/fields, 8px radii, 4px spacing scale, 12-14px text, responsive compact cards. Buttons have hover/focus/pressed/disabled; icon buttons minimum 34x34 and accessible labels.

| Control | Purpose, side effects and keyboard/pointer behavior | Disabled/error/acceptance |
| --- | --- | --- |
| Source text field | Type/paste source collection root. Editable on idle, Tab focus, Enter leaves field. No filesystem effects before Scan. | Valid absolute existing directory. Invalid path reported. |
| Source Browse / folder icon | Choose root via native WebView2 folder picker / open selected root in File Explorer. | Disabled during jobs; open disabled without path. Dialog Cancel leaves original value. |
| Destination text field | Explicit destination artist/brand root; never implicitly choose source for FLAC. | Must be absolute, separate, non-nested, not an ALAC named root for FLAC mode. |
| Destination Browse / folder icon | Choose output root / open output root. Native picker cancel safe. | Open disabled if missing; permissions/error surfaced. |
| Output format selector | One choice ALAC or FLAC, persisted independently of MCC. | Disabled while scanning/applying/undo. Losssy codecs not offered. |
| Scan / Preview button | Read-only filesystem scan, build per-release preview, validity, collisions. Keyboard Enter/Space. | Disabled during job; no hidden changes. |
| Preview table rows | Per-release details, source, target, status, files, notes. Checkbox decides which ready release to copy. Search/filter keyboard accessible. | Conflicts/review cannot be selected; row does not implicitly write. Large scans paginated. |
| Select all ready / Select none | Scope explicitly which unblocked releases Apply affects; keyboard accessible. | Selection affects no files until Apply. |
| Apply selected | Requires confirmed, nonzero, valid selected releases; copy/convert to staging, verify, finalize and write Undo manifest. | Disabled when no ready selection/job running. Confirm dialog and errors; no overwrite. |
| Cancel | Signal running scan/apply to stop at safe checkpoints, clean uncommitted staging. | No extra action while idle. |
| Undo last run | Delete only outputs created by last Apply if unmodified; preserve originals; native confirmation. | Disabled if no manifest or while job; changed outputs block undo. |
| Progress/status/activity | Visible status, percent, skipped/ready/review counts, explicit phase and last error. | Accessible role/live labels. No dead status buttons. |
| Filter | Filter preview by source/title/state; does not change selection. | Empty result explains no matches. |
| Per-release Details dialog | Open full source -> output mapping, all files, warning/review reasons. Modal Close button, Escape and keyboard focus. | Must be readable/scrollable; never silently omit changes beyond first 3 visible summaries. |

## v0.2.0 automatic organizer UI contract (replaces mandatory Scan / REVIEW / Apply)

User explicitly rejects manual per-release review as the primary workflow. The only normal required user actions are **choose input, choose output, choose target format (ALAC/FLAC), press Organize**. There is no Scan-then-Apply, no Select ready/none, and no 94-of-94 REVIEW stop caused by missing quality-check logs. Invalid files must not stall valid files; errors are recorded per release, shown as a final report, and export proceeds where safe.

| Control | Unique intent, actions, side effects, states, accessibility |
| --- | --- |
| Source field + Browse/open-folder icon | Enter, paste or choose source root; native picker cancel preserves input. Editable only while idle; path and hierarchy validated at start. Folder drop supported where host supplies actual path; manual Browse always available. |
| Output field + Browse/open-folder icon | Enter/choose explicit output root distinct from source. No content changes merely from editing path. Focus and errors visible. |
| Target format dropdown | ALAC or FLAC only; persists as own program settings, not MCC settings. Disabled during a run. |
| **Organize** (primary) | Immediately scan and organize whole input in the same job (no separate Apply). Destination writes only; source untouched. Disabled while running. No per-release approvals or selection blocking. |
| **Cancel** | Request cooperative stop at safe checkpoints, no interruption of already committed outputs; leave Undo manifest. Clear progress/status. |
| **Undo last run** | After user confirmation, remove ONLY output files unchanged since the last run; source never touched. Disabled during jobs; audit all files before any deletion. |
| Summary table or details | **Read-only** automated results: done, skipped (already exists), failed (actual conflict/codec/metadata issue), warning (e.g. missing AudioChecker log). This is reporting **after action**, not a review gate. Large outputs scroll/filter and are readable in keyboard navigation. |
| Progress + activity + Open output/log | Live number of releases done/total, encoded tracks/files, warnings/errors. 'Open output' and 'Open log' are direct host actions, with disabled state if path absent. Keyboard focus and correct accessible labels. |

MCC v0.5.18 WebView2 color/typography/control height/spacing remains the reference. Remove misleading preview/review controls; do not create buttons that have no behavior. 'Missing audiochecker.log' is a **warning** only; log is not forged and source need not have one to organize. Preserve realistic binary safety by not overwriting/replacing existing outputs and leaving a release-specific error in the run report on true collisions.

Acceptance for v0.2.0: 94 WEB albums without logs should begin automatic work rather than show '0 ready'; mixed good/bad release input processes every good release; missing reports are warnings; invalid CUE, unknown codec, filesystem collisions recorded as real failures without stopping rest; filenames preserve meaningful original descriptors; matching already-sorted outputs skipped non-destructively; Undo, cancellation, source safety, and WebView2 screens require Windows interactive confirmation. All Windows tests remain **NOT RUN** until actually performed.

## Test register

| Test | Status |
| --- | --- |
| Source/code inspection versus MCC 0.5.18 palette/component sizing | DONE (source inspection only - not GUI acceptance) |
| Inline JS syntax compilation (V8 Function on extracted <script>) | PASS (syntax only - not GUI acceptance) |
| Python source AST/compile, unit regression suite | NOT RUN |
| v0.2.0 missing-log automatic run, per-release failure isolation, source safety, Undo | NOT RUN |
| Windows real launch / default/minimum/large screenshots | NOT RUN |
| Hover/click/keyboard/mouse/drop / native folder picker | NOT RUN |
| Read-only Scan, empty/unknown metadata, collision and symlink fixtures | NOT RUN |
| CUE image split/FLAC/ALAC output with PCM/gap verification | NOT RUN |
| Safe cancellation, crash recovery, Undo/modified-file check | NOT RUN |
| Future MCC merge/parity | NOT RUN |

On completion of a test, record host OS, exact file version, screenshot paths, PASS/FAIL, and fixes. Unrun Windows acceptance blocks stable status.
