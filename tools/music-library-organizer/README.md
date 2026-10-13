# Music Library Organizer

**Current: v0.3.4 - Under construction ⚠️**

**[Download Music Library Organizer v0.3.4.pyw](../Music%20Library%20Organizer%20v0.3.4.pyw)**

Standalone MCC-style Windows WebView2 organizer. It does **not** modify MCC or other tools and stores its settings, logs, dependencies, cached MusicBrainz records, output reports and Undo journals in `Documents\Karpuzikov Tools\Music Library Organizer\`.

## v0.3.4 WebView2 startup and connection recovery

**Bug reported by actual Windows user in v0.3.3:** "Desktop connection failed" followed by an unresponsive window. After forced closure, some launches displayed a black WebView2 window; repeated restarts were required. Root-cause source audit found that startup JS would try to access `window.pywebview.api` every 50 ms for only 6 seconds, then permanently reject; every 900 ms the UI issued another such request, allowing overlapping operations. Also the *entire Organizer* object was passed as pywebview `js_api`, including references to its WebView2 window, locks and threading events; pywebview introspection could recursively traverse unnecessary nested objects.

**Fixes made:**
- Expose `DesktopBridge`, a minimal dedicated js_api facade with explicit state, browse, open-folder, open-log, organize, cancel, undo and diagnostics methods. It stores the main Organizer in the private `_organizer` attribute rather than exposing `window`, locks, background worker state or native objects to bridge reflection.
- Subscribe to the official `pywebviewready` DOM event, retry if injection is delayed, and never irreversibly fail after the first 6 seconds. Change to **single-flight polling**, a per-request timeout, bounded reconnect backoff and a visible **Retry connection** action only when disconnected. Organize is initially disabled until the desktop state is available. Late readiness restores all controls.
- Start Edge WebView2 with **a different writable profile on each process start**, under `Documents\Karpuzikov Tools\Music Library Organizer\webview2-sessions\`, to avoid reusing a locked/corrupt renderer profile after force-quit. Attempt cleanup of prior inactive session profiles without touching any other applications.
- Add a Windows named mutex to prevent concurrent app instances using the same output/Undo state. Persist WebView2 startup events and exceptions in `Documents\Karpuzikov Tools\Music Library Organizer\webview-startup.log` and preserve existing `startup-error.log` and activity logs. JavaScript unhandled errors are forwarded to native activity log when connection exists.

**Testing:** Source and HTML script syntax verified; simulated a late `pywebviewready` event and confirmed disabled-before-connection / enabled-after-connection controls with mock DOM in V8: **PASS**. Added source-level regression tests for the bridge facade, private WebView2 session path, and mocked main startup; these Python tests have **NOT** been run in the target Windows runtime. **Actual Windows forced-close/restart and black-screen verification is still pending.** Do not label stable.

**Next Windows test:** Open v0.3.4 three times normally; force-close once during idle, reopen and verify it draws immediately and all buttons respond; then force-close during a disposable test job and verify staged files, Undo and no duplicate launch. If any launch fails, check `webview-startup.log`, `startup-error.log`, and `activity.log` in the isolated Documents app directory. A failed job is not grounds to delete original files.

## v0.3.3 strict native lossless encoders (current)

**MANDATORY: FFmpeg is no longer an ALAC or FLAC encoder.**

- **ALAC output: only `refalac64.exe` from [nu774/qaac](https://github.com/nu774/qaac)**. Downloaded automatically from the official latest checksum-published Windows GitHub release (pinned fallback `v3.07`, SHA-256 `1fb3ab4aa81725e2607ac1b31afa0e13d2617ee6eb5900f5070c092f5271cbb3`); `refalac64.exe -o output.m4a input.wav`.
- **FLAC output: only Xiph's official `flac.exe` from [ftp.osuosl.org](https://ftp.osuosl.org/pub/xiph/releases/flac/)**. The automatic bootstrap is pinned to the official `flac-1.5.0-win.zip`, checks its published SHA-256 `53f1500f0d6e7c61379d7fee50d4a9f7f504c650009506d9ba015530d76c0dde`, and encodes `flac.exe --best --verify -o output.flac input.wav`.
- Dependencies auto-bootstrap only in `Documents/Karpuzikov Tools/Music Library Organizer/dependencies/encoders/`. If the exact native encoder cannot run, **the release fails without a silent encoder fallback**. ZIP extraction rejects path traversal, oversized archives and symlinks. Both official releases must pass published SHA-256 verification; arbitrary PATH binaries are not accepted. Explicit direct byte-for-byte copies for already-correct codec tracks do not invoke any encoder.
- Where decoding is necessary, FFmpeg creates an ephemeral **integer PCM WAV** with original sample rate and supported 16/24/32-bit depth. No FFmpeg `-c:a alac` or `-c:a flac` command remains. Source float PCM or lossy codecs never get coerced to purported bit-perfect ALAC/FLAC. CUE splitting uses the exact same pipeline and current encoding worker limit.
- The output is verified using FFprobe (true codec / sample rate / declared bit depth) and equality of decoded 32-bit PCM hashes against the original source. After a successful encode the organizer tries to transfer common/custom tags and cover artwork with Mutagen, and checks basic title, track number and artwork fidelity. If it cannot preserve metadata it raises an error and deletes the uncommitted output rather than silently losing tags. Temporary PCM WAVs are automatically cleaned.
- **Testing status:** Verified native Xiph `flac --best --verify` on an independent local 16-bit/44.1 kHz WAV round-trip; decoded PCM SHA-256 matched. Main Windows pywebview app / native Windows `refalac64.exe` and all real collection and metadata round-trips are **NOT RUN**. Existing `v0.3.2` is historical; the new version must remain **Under construction ⚠️** until actual Windows testing. GitHub Actions workflows unchanged.

## v0.3.2 CD sidecar and result UI corrections

- **Physical CD output excludes `audiochecker.log` entirely** even when the original CD folder contains one. Its original source bytes are preserved. The organizer does not run AudioChecker for CD releases; CD CUE and authentic rip LOG are retained/renamed separately using matched catalog (barcode fallback). If a stray WEB checker log was supplied alongside a CD rip, its omission from export is reported as informational in the batch log.
- A physical CD can have only a CUE or only a genuine non-AudioChecker rip LOG; the application now recognizes either as physical-rip evidence rather than misclassifying it as WEB solely due to a missing companion document.
- The redundant Results caption **"Automatic results. No approval step."** was removed. The Results panel now simply says **Results**.
- A concrete physical CD edition dated in a different year from the original album should append ` - <Edition Year>` inside its final bracket. Example, **if original worldwide date has been independently confirmed**: `2011-09-19 - I Am the Club Rocker [JP - WPCR-14516 - 2012]`.
- **Known blocker - not fixed:** MusicBrainz currently shows first release-group dates **2005** for INNA's *Hot* and **2010** for *I Am the Club Rocker*, while independently documented original album dates are **2009** and **2011**. v0.3.2 still consumes the MB release-group first-release-date without independent corroboration; reissue-suffix logic alone does **not** fix those leading years. **Do not regard those two output folder names as correct until independent date validation is implemented.**
- An existing organized output folder is never modified on a new run: v0.3.2 will **not remove an erroneous `audiochecker.log` left by v0.3.1 in an already-present destination**. Use a fresh empty output folder to test, or manually reconcile your earlier output after verifying the originals.

**Verification:** Source logic and embedded JavaScript syntax inspected; new targeted CD-sidecar/reissue regression tests committed but **not executed on the user's Windows host**. Real-file results remain to be checked; version remains under construction.

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
