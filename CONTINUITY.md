# Duplicate Edition Analyzer - Continuity Handoff

Last updated: 2026-10-13
Status: Under construction ⚠️

## File organization naming clarification - 2026-10-13

- The user explicitly corrected the meaning of country codes inside ALAC physical-CD release folder names: `[US - ...]` indicates **country of manufacture/pressing of the physical CD**, NOT MusicBrainz release country or sales/distribution region. `EU` is not a country. For physical CDs with unverified pressing location, flag for review rather than guessing or relabeling the physical CD as digital.
- User's second explicit rule: a country-less identifier such as `2018-02-16 - El Pintor Bonus Tracks [OLE13186]` means a **digital/WEB release** in this naming convention, so manufacturing/pressing country is **N/A**, not unknown. `[Identifier]` = digital; `[Verified Pressing Country - Catalog Number]` = physical CD.
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
