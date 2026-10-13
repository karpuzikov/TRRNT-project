# Music Library Organizer - UI Control Contract

Status: v0.3.10 candidate - Under construction ⚠️; native engine provisioning and Windows acceptance NOT RUN. Reference: `karpuzikov/MCC` `Music Collection Cleaner v0.5.18.pyw` main WebView2 window `_webview_main_html()`.

## v0.3.10 - three independent user controls (manual stage selection)

| Control | Function | Disabled state and acceptance |
| --- | --- | --- |
| `#doEncode` (Encode audio checkbox, default ON) | Persist setting; ON converts ordinary tracks to chosen format using native encoders, OFF copies each ordinary source codec/extension and stages metadata. Never mutate original. | Keyboard Space/Tab, pointer, focus-visible; unchecked disables Encoding workers, and Output format only if Split also unchecked. Real Windows tests pending. |
| `#doChecker` (AudioChecker checkbox, default ON) | Persist setting; ON runs only genuine auCDtect for WEB when binary available and generates original-style log; OFF skips analysis and removes its report compliance requirement. No ZIP chooser. | Unchecked disables AudioChecker workers. Real report/Windows tests pending. |
| `#doSplit` (Split album images (CUE) checkbox, default ON) | ON splits verified CUE image to individual lossless tracks using chosen native encoder; OFF preserves whole album audio image and rewrites staged CUE FILE paths. | When Encoding is also OFF, Output format disabled; when Split ON but Encoding OFF, format must stay enabled for new split tracks. Real native splitter tests pending. |

All controls are small integrated checkboxes in existing MCC-like form. States persist across restarts; show settings via bridge, expose processing choices in activity/report/Undo metadata, lock while run active, do not trigger actions merely by toggling. Mocked WebView2 DOM checked state restore and enabled/disabled interactions; **real Windows hover/keyboard, screen size/accessibility, validation and complete codec tests NOT RUN**.

## v0.3.9 - correct AudioChecker availability status

No ZIP browse UI. Actual auCDtect engine needed in program-private dependencies, not a new external user-facing setting. Automatic WEB analysis runs only when a verified user-owned engine exists; display explicit PARTIAL failure and do **not** create `audiochecker.log` if missing. Remove any UI promise of fully integrated no-dependency proprietary analyzer. CUE, files, tag flows and MCC-style WebView2 unchanged. Windows availability/failure UI validation NOT RUN.

## v0.3.8 - transparent results status and restored naming

No new controls. Existing Results column must display **why** each PARTIAL item is incomplete, not a generic `requirements unfinished` message. `last-run.json` records a structured `unmet_requirements` list. For each unmet requirement, show an English actionable phrase and retain all original source and target paths with immediate adjacent folder icons. A WEB barcode satisfies its identifier requirement even without a separate catalog. Physical pressing-manufacture evidence is stricter and must be explicit if missing.

Result rows stay mouse-selectable/copyable during 1.5s status polling; Copy results copies the full visible reason. Native WebView2 test of this modified content, hover, click, Copy, keyboard focus, narrow/default/large window, actual INNA records and reader accessibility **NOT RUN**. CUE reports specify exact sector/pregap reason when reconstruction is not proven. No spurious new checkbox or review dialog.

## v0.3.7 - integrated AudioChecker: no ZIP/file-picker UI

The user explicitly rejected the ZIP/file picker workflow. Remove `#checkerZip`, `#chooseChecker`, `#openCheckerZip`, all associated input and folder icons, and the `browse_checker_zip` native bridge API. Do not substitute another checker installation screen. AudioChecker is a **built-in automatic stage** of Organize; retain `#checkWorkers` (manual bounded worker count) as the only checker-specific input. A single small passive label clarifies `AudioChecker is integrated. WEB releases are checked automatically; no external checker or ZIP is needed.`. Source/output path controls and result-path folder icons remain for real displayed paths.

| Affected control | Purpose, actions, side effects | Focus/feedback, states, geometry and acceptance |
| --- | --- | --- |
| AudioChecker workers `#checkWorkers` | Select 1..detected logical CPU maximum for concurrent *built-in* per-track spectral checks; saved to this app's settings. | Existing 34px select, label, keyboard/Tab, pointer; disabled while run; validate output and persistence. Windows NOT RUN. |
| Integrated checker status | Automatic, no separate click/ZIP. When organizing WEB release, show current stage `AudioChecker`; produce integrity-validated `audiochecker.log` with evidence/uncertainty; CD never invokes checker. | Descriptive warnings for strong bandlimiting, inconclusive or actual probe failure. Partial when not verified. No false auCDtect probability/compliance. Windows actual file workflow NOT RUN. |
| Results table / Copy results / path folder icon | Preserve prior v0.3.6 fixes. Mouse/keyboard select/copy, folder icon adjacent to destination paths and app log controls. | Native focus, hovering, resizing, polling while selecting, selection restoration, horizontal text wrapping, error handling NOT RUN on Windows. |

The prior v0.3.6 AudioChecker ZIP row contract below is **historical and superseded**. The current UI MUST NOT include its controls; do not revive the `auCDtect` upload/selection flow. The entire MCC-style WebView2 design tokens, source/destination controls and error recovery remain unchanged. Run min/default/large-size screenshots; keyboard-only, hover, pointer and real conversion/analysis tests before stable status.

## v0.3.6 candidate - added/changed controls and selection stability

| Control/state | Purpose/side effects | Mouse/keyboard, visual state, geometry, acceptance |
| --- | --- | --- |
| Results text selection | Preserve a user highlight across 1.5-second WebView2 status polls; `Ctrl+C` copies selected text. No file side effects. | Standard pointer selection; Tab should not steal focus. Re-render only if results content actually changes and selection is absent. Test during a running 94-row job, with updates and filters. Current real Windows test NOT RUN. |
| Results path folder icon | Opens displayed existing release output folder with host `open_folder`; a failed/nonexistent destination produces actionable error. No duplicate global Open output action. | Small 28px square icon inline before each path, native click/Tab/Enter/Space, normal/hover/focus/disabled/error. Test many long paths and narrow widths. NOT RUN. |
| AudioChecker ZIP path folder icon | Open the location of the selected existing ZIP in Explorer with the file selected; no extraction on click. | 34px folder icon immediately before readonly path, disabled when no path / disconnected, Tab/Enter/Space, hover/focus states; real native selection NOT RUN. |
| Browse ZIP | Open pywebview file picker, validate both auCDtect and FLAC entries whether archive members start with `Codecs/` or a wrapper directory, remember selection in isolated settings; cancellation leaves earlier selection unchanged. | 34px-height existing text button, keyboard focus and error message. Actual Windows picker and genuine ZIP extraction NOT RUN. |
| Open log / Open log folder | Unchanged adjacent controls. Open report in associated application or isolated folder, no mutation. | Click/keyboard, existing standard icons/sizes, NOT RUN on Windows. |

Use MCC v0.5.18 as the unchanged dark WebView2 visual baseline. Colors/fonts, source/destination, format, worker selectors, Organize/Cancel/Undo and recovery controls must retain prior semantics. No confirmation or REVIEW gate. Mandatory minimum/default/large screenshots, keyboard-only navigation, mouse selection while polling, explicit Copy results, dialogs, forced close/reopen, real AudioChecker ZIP and status transitions all **NOT RUN** and block stable acceptance.

## v0.3.5 selectable Results and log/ZIP controls

- Results table: allow native text selection and Ctrl+C (mouse drag, keyboard), no CSS `user-select:none`. Expose a working **Copy results** button that copies the currently filtered status, source release, destination, message and warnings. No review gate.
- **Open log folder** (folder icon) immediately adjacent to **Open log**. Both are real native actions through `DesktopBridge`; folder opens app-isolated log directory and does not duplicate the separate organized-output path icon.
- **AudioChecker ZIP** optional, read-only path and a **Browse ZIP...** chooser. Persist selected real ZIP path under product-only settings, validate bundled `aucdtect.exe`. Auto discovery still happens without manual chooser. If not found, status identifies the real problem and remains PARTIAL, never a synthetic audiochecker.log.
- Earlier worker selectors, input/output, ALAC/FLAC choice, source safety and WebView2 bridge behavior remain. Real Windows interaction/screen-reader, black-screen, clipboard, native open-folder/ZIP dialog, true reports NOT RUN.

## v0.3.4 startup/reconnect controls and states

| Control/state | Purpose / pointer / keyboard / native side-effects / acceptance |
| --- | --- |
| Window initialization | Immediately show dark full interface with **Connecting to desktop...** status. No false 'Ready' prior to state retrieval; no modal waiting loop or frozen worker. `Organize` initially disabled. |
| Desktop bridge | Subscribe to `pywebviewready`; isolate only allowed API methods with a separate `DesktopBridge`. Retrying after delayed injection never corrupts input/settings. One status-poll in flight, deadline + bounded retry intervals; no spurious duplicate work. |
| Retry connection button (`#reconnect`) | Hidden under normal conditions; appears only when desktop connection is unavailable after initial grace interval. Click/Enter/Space retries the bridge immediately without launching a second app or job. Cleared after state returns. |
| Disconnected controls | Browse, folder-open, Open log, Organize, Cancel and Undo disabled until connectivity restores; connection state and next action visible. After recovery restore correct idle/running/undo states rather than leaving folder/log actions disabled. |
| Startup telemetry/profile recovery | Fresh isolated WebView2 session per app launch, no shared profile with MCC, and single-instance Windows mutex. `webview-startup.log` records load/shown/closed/errors; `startup-error.log` captures exceptions; app data outside shared common directory. Old locked profiles are not force-deleted from live renderer processes. |

**Tests:** V8 JS syntax and delayed-bridge mock UI **PASS** (not real Windows acceptance). New source-level Python startup tests committed but NOT RUN. Real Windows screenshot/black-window and keyboard/button acceptance **NOT RUN**.

## v0.3.3 encoding backend requirement

The Output Format selector remains ALAC/FLAC without an added encoder selector. It now determines **refalac64.exe for ALAC** and **official Xiph flac.exe for FLAC** exclusively; a visible per-release error is required if installation or validation fails, never a fallback to FFmpeg encoders. The Encoding workers selection controls both native encoder subprocesses for individual files and image CUE splits. Background startup/analysis must not freeze the GUI while dependencies download. No UI-control deletion/addition is needed. Actual Windows process/UX acceptance remains NOT RUN.

## v0.3.2 Results panel correction

- Remove extraneous explanatory subtitle `Automatic results. No approval step.` from Results header. Plain **Results** is sufficient.
- For CDs with a stray `audiochecker.log`, the log is not present in output and its omission is explained in run Results. Never label the CD as a WEB/digital output due to that file; don't show an AudioChecker job for CDs.
- Prior v0.3.1 controls (autodetected AudioChecker, worker sliders, default output and no redundant Open output button) otherwise remain active; Windows UI acceptance NOT RUN.

## Active v0.3.1 control changes (supersede v0.3.0 checker path)

- There is **no AudioChecker executable path field**. Auto-find the user's own `Audio_Checker_UTF8_Multicore.zip` beside the `.pyw` or in Downloads; install known checker components into isolated program directory. Missing bundle is an explicit PARTIAL status, not a file-renaming failure or a fake log.
- Input folder Browse/paste/drop automatically proposes **`<Input folder> - Sorted`** as output if output has not been manually customized. The proposed field stays editable and is validated non-nested at startup. Input changes must not silently overwrite custom output text.
- Keep just the folder-open icon inside the **Organized output** path row. Remove the redundant **Open output** toolbar button completely. Keep **Open log** working.
- Two independent labeled `select` inputs: **Encoding workers**, **AudioChecker workers**, range 1..min(64, detected CPU logical cores), initial min(4, cores), persisted in product-specific settings. Disable both during a run. The respective worker counts must actually limit parallel FFmpeg encode/split and auCDtect tasks, not merely be UI labels.
- Same visual design family as MCC v0.5.18, no mandatory review gate, correct accessible labels/focus. UI JS syntax check PASS in isolated V8. Windows visual/keyboard/browser/download/bundle and application tests NOT RUN.
- Earlier v0.3.0 "AudioChecker executable path + Browse" block below is **historical/superseded**, not an active requirement.

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
