# Music Library Organizer - UI Control Contract

Status: draft 0.1.0 - Under construction ⚠️. Reference: `karpuzikov/MCC` `Music Collection Cleaner v0.5.18.pyw` main WebView2 window `_webview_main_html()`.

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

## Test register

| Test | Status |
| --- | --- |
| Source/code inspection versus MCC 0.5.18 palette/component sizing | DONE (source inspection only) |
| Windows real launch / default/minimum/large screenshots | NOT RUN |
| Hover/click/keyboard/mouse/drop / native folder picker | NOT RUN |
| Read-only Scan, empty/unknown metadata, collision and symlink fixtures | NOT RUN |
| CUE image split/FLAC/ALAC output with PCM/gap verification | NOT RUN |
| Safe cancellation, crash recovery, Undo/modified-file check | NOT RUN |
| Future MCC merge/parity | NOT RUN |

On completion of a test, record host OS, exact file version, screenshot paths, PASS/FAIL, and fixes. Unrun Windows acceptance blocks stable status.
