# Music Library Organizer

**Candidate: v0.3.19 - Under construction ⚠️** (all artwork directly in Destination; real Windows acceptance pending; PR draft)

**[Music Library Organizer v0.3.19.pyw](../Music%20Library%20Organizer%20v0.3.19.pyw)**

Standalone MCC-style Windows WebView2 organizer. It does **not** modify MCC or other tools and stores its settings, logs, dependencies, cached MusicBrainz records, output reports and Undo journals in `Documents\Karpuzikov Tools\Music Library Organizer\`.

**v0.3.19 additional safety:** When a previous Artwork Undo journal exists (including one created by the incorrect v0.3.18 nested layout), archive that complete journal under the organizer's private `artwork-undo-history/` before starting a new artwork pass. Refuse to replace an unreadable old journal. This preserves recovery evidence without touching the old nested JPGs; the active Undo action still handles the latest run. 122 uniquely named Python regression methods are now defined (not executed on Windows).

## v0.3.19 - Artwork saved directly in Destination (corrected layout)

**User correction:** In v0.3.18 the artwork was incorrectly written inside each release folder. In v0.3.19 it is saved **directly in the selected Destination root**, with the **release folder's exact name** and `.jpg` extension. Example:

```text
GIMS - UPDATED - Sorted/
├── 2016-08-26 - À Contrecœur (Pilule Violette) - EP [886445932034]/
│   ├── 01 Ma Beauté.m4a
│   └── audiochecker.log
└── 2016-08-26 - À Contrecœur (Pilule Violette) - EP [886445932034].jpg
```

Artwork lookup reads identifiers from each release subfolder, but its output target is `Destination / (release_folder.name + ".jpg")`. Existing Destination-root files are skipped without overwrite; old art created *inside* the release by v0.3.18 remains there unchanged and is not silently moved/deleted. If nested releases have duplicate names (case-insensitive), both are flagged PARTIAL because flattening would collide; no arbitrary cover is selected. Results' Open Folder action opens the Destination root where the resulting JPG is located. Undo Artwork validates recorded names/hashes and removes only newly generated Destination-root art; it can still read earlier v0.3.18 nested-artwork journals.

**Verification:** Four new explicit regression methods bring total to **122 defined unique tests**. GitHub source/test content and static checks checked; real Python regression run and native Windows GUI/live CDN tests still **NOT RUN**. Under construction until verified.

## v0.3.18 - Artwork download into existing Destination (under construction)

**New separate `▣ Artwork` button** beside Organize. Select your existing *organized music collection* in **Destination** and click Artwork. No need to run Organize or provide a Source again. One release folder = one artwork operation; audio files, logs, CUE and existing images stay untouched. Result progress shows downloaded / covers exist / unavailable / failed, with each release's lookup link and artwork dimensions in a unique `logs/artwork-...` run log. Cancel stops between lookups; the existing Undo button becomes **Undo Artwork** when appropriate and deletes **only hash-verified JPGs** created by the latest artwork pass, without deleting a release directory or audio.

**Lookup:** Exact verified UPC/EAN suffix; if a CD folder ends in a catalog rather than valid GTIN, try first track's BARCODE/UPC tags and finally the MusicBrainz *exact catalog + compatible physical/digital format + title + unambiguous UPC* (reject guessing). Public Apple iTunes Lookup API `lookup?upc=...&entity=album`, testing US/GB/FR/JP and UPC/EAN leading-zero equivalents. If a matching release supplies `artworkUrl100`, try maximum Apple JPEG CDN variants starting `10000x0w-999.jpg`, then step down through lower JPEG variants. Reject PNG/HTML instead of misnaming as JPG. If Apple fails, search a MusicBrainz release with the exact GTIN/title for linked Spotify album URLs and use the official **public Spotify oEmbed** thumbnail; Spotify fallback only works when that exact release has a usable Spotify link, it does **not** guess across different UPCs. Preserve provider attribution as a source URL in the operation log.

**Output:** `<release folder>/<release folder name>.jpg`, e.g. `2016-08-26 - À Contrecœur (Pilule Violette) - EP [886445932034]/2016-08-26 - À Contrecœur (Pilule Violette) - EP [886445932034].jpg`. Existing same-name artwork is never overwritten; exclusive create, JPEG signature/SOF/EOI and dimension verification, bounded HTTPS downloads to allowlisted providers, SHA-256 readback, separate Undo manifest. User source audio is not touched; only a new cover JPG may be added to the chosen existing output folder.

**Verification:** 116 unique test methods defined (14 added to baseline 102); GitHub source readback and embedded JS syntax checked. Actual Python suite, live CDN/API responses, Windows WebView2 actions, real image decoder and Undo acceptance **NOT RUN**. **The previously documented Single/EP soundtrack title-stripping rule is still pending implementation and is not silently bundled into this artwork change.**

## v0.3.17 - GIMS capitalization, cross-file CUE and 2-track Single metadata

- **True Picard-style title case:** previously the case helper only lowercased English minor words and left ordinary source words unchanged. Now case-standardize release and track titles (including metadata) while preserving minor-word rules, Unicode/accented words, acronyms, CamelCase, and protected artist names: `Best life` -> `Best Life`, `SIN TI` -> `Sin Ti`, `JOE PESCI` -> `Joe Pesci`; `3OH!3` and `G-DRAGON` remain protected.
- **Actual CUE file context:** `cue_read` tracks each index's source `FILE` and binds `rec.file` to the file holding `INDEX 01`. Track 04's `INDEX 00 04:09:65` may be in track 03's file while track 04's `INDEX 01 00:00:00` is in its own file. Preserve original CUE sequence and sector indexes while rewriting only filenames; do not reject valid split-track CUE. Reject inverted indexes when genuinely in the *same* image file, preserving original image evidence.
- **Arhbo (barcode 197089993382):** normalize square-bracket `[Music from the ...]` to parenthesized soundtrack release subtitle; tolerate a missing *descriptive* soundtrack note in MusicBrainz search indexes when the base title, exact barcode, digital medium and 2-track count match. A MusicBrainz release-group type `Single` is authoritative even when there are two tracks. Target: `2022-11-19 - Arhbo (Music from the FIFA World Cup Qatar 2022 Official Soundtrack) - Single [197089993382]`.
- Seven added regressions, **102 uniquely named tests defined**. Source readback and targeted static review done. Full Python compile/unittest and real Windows, MusicBrainz network, CUE/AudioChecker, encode, Undo and GUI acceptance **NOT RUN**.

## v0.3.16 - one log per run, never mixed with settings

- App-generated logs are isolated in `Documents\Karpuzikov Tools\Music Library Organizer\logs\`.
- Every **Organize** and **Undo** creates a new timestamped collision-resistant `run-*.log` or `undo-*.log`; app session and startup diagnostics use separate `session-*.log` and `startup-*.log`. `Open log` opens the active run, or most recent processing run after reopening. Its folder icon opens `logs\`.
- Legacy root-level `activity.log`, `webview-startup.log` and `startup-error.log` are preserved under `logs\legacy\` where safely movable, never overwritten/deleted. Startup exceptions get a separate `startup-error-*.log`.
- No changes to source-CD EAC logs or per-release `audiochecker.log`. No extra GUI controls.
- Six new regression definitions (95 total); static source validation PASS, full Python suite and real Windows test **NOT RUN**.

## v0.3.15 - no traceback when opening a second copy

Windows permits **one active** Music Library Organizer instance to protect settings and Undo. A second launch now **restores and focuses the already-running GUI** (including minimized) and exits quietly. If a live process holds the lock but has no discoverable MLO window, a short native notification explains the condition, and the duplicate exits without killing any existing process.

No new dialogs in the normal duplicate case; no lost running jobs and no concurrent Undo writer. The original dark WebView2, authentic embedded auCDtect, catalog/CUE corrections and three stage checkboxes are unchanged.

**89 regression methods committed; full Python suite and actual Windows double-launch/minimized/hidden-process/UI acceptance NOT RUN.** Draft only.

## v0.3.14 - correct numeric catalog, exact two-disc track count and safe CUE

The source `[EU - 88883792232]` contains the **catalog number** 88883792232, not MusicBrainz barcode 888837922326. MLO now validates the GTIN check digit before treating a number as a barcode and queries the exact catalog in MusicBrainz. The supplied [Maître Gims `Subliminal : La Face cachée` edition](https://musicbrainz.org/release/ffa45ff6-45a8-4e3b-bc89-a46e8ff0652c) has 2×CD (18 + 6 tracks), catalog 88883792232 and barcode 888837922326. For a CD image stored as one audio file per disc, MLO uses complete CUE track counts, not audio file counts, and verifies each CD medium separately.

When a unique album-image CUE has an impossible `INDEX 00` after `INDEX 01`, splitting is unsafe. Rather than discard the image or invent a corrected offset, the app copies the whole image unchanged with SHA-256 integrity verification, preserves source CUE indices in the staged sidecar, and marks **PARTIAL with explicit cause**. Unprovable CUE situations still fail safely; originals never change.

**82 test methods committed; real Windows tests and the user's exact two-CD rip/audio verification NOT RUN.** Current version remains Under construction ⚠️.

## v0.3.13 - simplified interface under updated GitHub UI rules

Adopted the latest `main` [UI_UX_RULES.md](../../UI_UX_RULES.md) and [SOFTWARE_RULES.md](../../SOFTWARE_RULES.md) on the feature branch, including **radical simplicity and minimum-click workflows**. Removed redundant tutorials, helper text and decorative prose; kept necessary labels, explicit statuses and controls. The source/destination picker retains direct Browse and distinct open-folder actions. Paths already selected are remembered; selecting the source auto-generates an output destination; Organize is gated against empty paths, running and connection failure without a new confirmation step. Keeps all three immediately visible independent processing checkboxes, selected format and worker controls, Results Copy/Open log, dark MCC-style WebView2, keyboard/drag/drop, Undo and genuine embedded auCDtect.

**Checks:** embedded JS syntax and mocked control-state/source=>destination interaction PASS; actual Python test suite and Windows renderer/screenshot/keyboard/workflow verification **NOT RUN**. Draft only, no stability promotion.

## v0.3.12 - exact MusicBrainz catalog identity and speed visibility

Fixed the catalog search omission for digital editions: `HAB017`, `VPRVIP006`, `BETA021` are queried as **exact MusicBrainz catno** even if the source is WEB. Candidate verification checks normalized label catalog (e.g. BETA021 vs BETA 021), with a second spaced lookup when first results are only Vinyl, Digital Media rather than Vinyl, matching total audio track count and compatible title; ambiguous candidates remain unresolved rather than guessed. CD DiscID ambiguities are further screened by exact catalog and source region.

CD source country/region `[EU - ...]`, `[AT - ...]` is retained when present; MusicBrainz country (often GB) is stored separately and never mislabeled as actual pressing manufacture. No blanket pressing-country PARTIAL when the source gives a region or MusicBrainz supplies documented release territory. Genuine uncertainty about edition identity continues to produce explicit PARTIAL.

**Performance:** media/track screening precedes expensive MusicBrainz full-detail calls; same-name existing destination skips remote metadata lookup; the original inaccurate FFT logs remain flagged. Activity logs show `PERFORMANCE:` lines for initial all-track credit scan and individual metadata, encoding/copy, auCDtect and total elapsed times. Audio verification/PCM equality are unchanged. 74 regression methods currently committed, full Windows tests and real-user benchmark **NOT RUN**.

## v0.3.11 - genuine AudioChecker included, no external setup

**Original auCDtect now ships inside the single `.pyw` file.** No manual download, file selector, user ZIP or external analyzer installation. At first AudioChecker-enabled WEB run, the program automatically extracts the **exact original `aucdtect.exe`** (147,456 bytes, SHA-256 `efe47eec21c33431e7fceea4b171b8a15a729e3adcda967ee0fc165a6a41adba`) and original `Readme` into this app's isolated dependency folder; hashes are checked before execution and tampered files are rejected. No other codec binaries are packaged.

The original 2004 Readme by Oleg Berngardt and Alexander Djourik explicitly permits free redistribution **as long as no charge is made**. This software and GitHub distribution must remain free of charge and preserve the Readme. Only a real auCDtect verdict is ever translated into the traditional `AUDIOCHECKER v2.0 beta (build 457)` layout/summary/checksum, never approximated from a custom FFT.

This packages the **actual original engine**, not a new clean-room port of its proprietary signal model. This maximizes per-track parity without asking the user for prerequisites. Existing three independent processing checkboxes remain; AudioChecker OFF does not extract or run the executable.

**Verified:** Source executable/Readme SHA-256 against the user's earlier packaged AudioChecker, PE32 static inspection, slim two-item archive decompression/length/hash test, same embedded payload committed to GitHub, WebView2 JavaScript syntax. **NOT YET VERIFIED:** Complete v0.3.11 Python compile/unittest runner on checked-out source, real Windows executable launch, all 55 real source tracks compared against the original reference, GUI/encoder acceptance. Status remains Under construction ⚠️.

## v0.3.10 - optional processing checkboxes and precise digital dates

The original compact WebView2 UI now has three **independent checkboxes** (default ON, persisted): **Encode audio**, **AudioChecker**, **Split album images (CUE)**. Disabling Encoding copies ordinary lossless audio in its original codec and extension and updates staged metadata where supported, without transcoding. Disabling Split keeps a CUE-referenced album image whole, with valid rewritten CUE paths. Enabling Split uses the selected native refalac64/Xiph encoder even if ordinary Encoding is unchecked, since a split must produce new audio tracks. Disabling AudioChecker skips analysis and its log requirement entirely. Enabling it still requires **authentic auCDtect**; the binary's fully legal self-contained provisioning remains unsolved (no fabricated verdicts).

**New date rule:** For a DIGITAL/WEB release, January 1 (`YYYY-01-01`) is only a year placeholder. Output `YYYY - Album [identifier]`, never an invented month/day. Physical releases keep their actual supplied dates. New regression fixtures and isolated mock UI state checks added. Full application tests and real Windows acceptance **NOT RUN**. Existing folders are skipped, so choose an empty output destination for validation.

## v0.3.9 - original auCDtect engine and historic AudioChecker report

**Critical correction:** v0.3.7/v0.3.8's custom FFT did not reproduce AudioChecker's CDDA/MPEG classification. It is disabled and removed in v0.3.9. Genuine `auCDtect` output is mandatory for a report; original-style 2.0 beta build 457 report formatting, comma summary and checksum are implemented. The exact checksum **145533892** and summary **94,76% CDDA** were independently reproduced from the user's 55-track reference.

**Existing output:** Already-organized directories with old `MLO Integrated AudioChecker v0.3.x` spectral reports are shown as **PARTIAL - INVALID report** instead of quietly SKIPPED. Existing files are never overwritten or removed. New empty output and genuine engine required for correction.

**Not fully self-contained yet:** The original auCDtect binary is proprietary freeware. No verified redistribution permission and the publisher's binary link redirects away to unrelated content. The program can use an already present user-owned binary in its app-private dependencies; it cannot automatically safely install a missing original engine today. It does not prompt for a ZIP, scrape arbitrary mirrors, produce false CDDA/MPEG percentages or silently substitute a homemade classifier. Missing engine means **PARTIAL**, no `audiochecker.log`. Real native Windows scores and binary installation still **NOT RUN / NOT SOLVED**.

## v0.3.8 - specific INNA naming/status and CUE repair

- **Visible PARTIAL reasons:** An otherwise verified WEB edition may use barcode when its separate catalog is missing; it no longer gets a silent PARTIAL solely due to catalog absence. The result now includes `unmet_requirements` and an explicit message. Physical releases without verified country-of-manufacture evidence remain PARTIAL with that exact reason.
- **Picard capitalization:** Read [English_Title_Capitalization.txt](https://github.com/karpuzikov/userscripts/blob/main/picard-tools/scripts/English_Title_Capitalization.txt). Lowercase internal English function words in track title and version descriptors while preserving proper artist names, acronyms and the script's phrasal exceptions. Examples: `10 Minutes (Club Mix by Play & Win)`, `We're Going in the Club`.
- **Featured-credit parsing:** `More Than Friends feat. Daddy Yanke` -> `More Than Friends (ft. Daddy Yanke)`, `INNDiA feat. Play&Win` -> `INNDiA (ft. Play & Win)` in **both TITLE tags and filename**. Preserve richer filename features when TITLE tags are short.
- **INNA diacritics:** Correct the specifically documented legacy `Déjá Vu` spelling to `Déjà Vu` for INNA releases; do not globally rewrite unaccented Deja Vu titles.
- **CD Single inference:** Explicit legacy `(Maxi CD Single)`/`(CD Single)` source evidence may supply `- Single` in a provisional normalized folder, while unverified pressing facts remain unresolved. `(Deluxe Editon)` is corrected to `(Deluxe Edition)`.
- **Legacy CD folders:** Clean provisional title even when the date/catalog/country is unresolved; do not preserve ugly original source-folder syntax or invent an identifier. Sanitize Windows-illegal punctuation in metadata titles rather than abandoning the whole renaming operation. Unverified naming remains PARTIAL.
- **CUE:** A correct 16-track MusicBrainz DiscID release does not by itself verify an old full-image CUE's pregaps. When the image is missing but split tracks are present, reconstruct playable per-file CUE only if all track numbers, INDEX01 chronology and actual PCM CD-sector lengths match exactly, with no INDEX00/HTOA/pregap. Otherwise preserve documentary `Unresolved CUE` with the *specific reason*. Never touch source files.
- **Tests:** New explicit fixtures checked into `test_organizer.py`, but full Python compilation/regressions, actual Windows WebView2 UI, native codec and real INNA 16-track audio/CUE validation remain **NOT RUN**. Test using a fresh blank destination or safe Undo; old sorted folders are skipped by design.

## v0.3.7 - integrated AudioChecker in the single-file app

**No AudioChecker ZIP, path field, Browse ZIP button, auCDtect executable or separate checker download required.** The program automatically performs its own spectral audio analysis for WEB releases and produces `audiochecker.log` in the staged organized output, with bounded AudioChecker workers and a SHA-256 report-integrity footer. Physical CDs never receive such a report. The source is never modified.

**Important provenance:** This new analyzer uses original pure-Python FFT frequency-band analysis of PCM decoded through the existing FFmpeg/FFprobe toolchain. It can flag strong bandlimiting, find no strong cutoff, or return inconclusive; these are *not* auCDtect's proprietary CDDA/MPEG probability scores. A cutoff may be production EQ rather than proof of lossy re-encoding, and no cutoff cannot prove original CDDA/lossless provenance. No invented percentages. Flagged or inconclusive outputs remain PARTIAL; processing errors are explicit.

**Verification:** The independent kernel compiled with Python 3.13.5 and real FFmpeg synthetic fullband/lowpass signals produced expected distinct diagnostic classes and a valid two-track report. Full v0.3.7 application compilation/regressions and actual Windows GUI/AudioChecker INNA workflow **NOT RUN**, still Under construction ⚠️. The input/output hierarchy, exclusive refalac64/Xiph encoders, DiscID/CUE logic, result selection, Undo and isolated settings remain.

## Historical v0.3.6 - superseded checker ZIP workflow and Results fixes

- Correctly install and accept the supplied AudioChecker ZIP whether `Codecs/Analyzer/aucdtect.exe` is stored at archive root or beneath an enclosing directory. Reject traversal, absolute ZIP members and conflicting duplicate binary entries; no third-party executables are committed.
- For tracks that explicitly contain a featured performer, remove the same redundant performer from exact two-artist ARTIST spellings (e.g. `INNA & Marian Hill` -> `INNA` while retaining `(ft. Marian Hill)` in TITLE). Avoid guessing for unrelated credits.
- Prevent the 1.5-second Results status poll from destroying text selection before Ctrl+C. Keep explicit Copy results, and show an Open folder control before each reported output path.
- Add Open file location icon directly before the remembered ZIP path. Preserve adjacent Open log / Open log folder actions.
- **Only WebView2 JavaScript syntax was checked.** Full Python compile/tests, native checker binaries, Windows UI/clipboard and INNA 94-release acceptance **NOT RUN**. Status remains Under construction ⚠️. Branch `mlo-v0.3.6-checker-results-fixes` is a candidate, not a stable download.

## v0.3.5 - INNA naming, DiscID, AudioChecker, results

**Based on the user's actual 94-release `Pasted text(4).txt` log (0 done, 93 partial, 1 failed):**

- Release/folder names no longer include `(ft. Artist)`/`(feat. Artist)` credits. The track itself retains featured performers. Folder version descriptors such as `(extended mix)` and `(remix)` become `(Extended Mix)` and `(Remix)` while proper names and diacritics remain.
- Added cleanup of source album/edition strings: removes leading collection artist `INNA - `, trailing `(Promo)`/`(FLAC)`, `(Maxi CD Single)`, duplicate date-year and catalog bracket from the **search title**. It still requires authoritative album dates/catalog/territory to generate a fully standard final release folder; an unresolved match remains PARTIAL, not fabricated.
- Track TITLE and audio filename are normalized together on both direct same-codec *copies* and native refalac64/Xiph FLAC conversions. `Diggy Down (Radio Edit) [ft. Marian Hill]` -> `Diggy Down (ft. Marian Hill) [Radio Edit]`. Edit/version names are never replaced with another release's remix names.
- Contradictory `Bob Taylor ft. INNA` credits are parsed as separate ARTIST and featured performer. A source-collection majority over the **same song and performer pair** can establish the canonical direction `INNA ft. Bob Taylor`; only then does the output change ARTIST and TITLE accordingly. A tie or unverified credit does not trigger fabricated role reversal.
- MusicBrainz DiscID candidates are filtered by *exact medium count*, medium format, track count per disc and available DiscID matches **before** deciding the edition is ambiguous. This addresses the user's 16-track `Party Never Ends 2013` case where one of the two DiscID candidates is a two-CD edition. Live local TOC/media response still needs end-to-end confirmation.
- For a CUE referencing a shared full-album WAV no longer present alongside **valid split tracks**, the app preserves those audio tracks and retains the old CUE clearly labeled `Unresolved CUE - ...`; reports PARTIAL without claiming the CUE is now playable. It never invents pregap offsets.
- **AudioChecker:** 93 WEB exports in the supplied log had no reports because the ZIP wasn't found. The app now also searches likely Downloads directories on drive letters and provides an optional **Browse ZIP...** selector to locate the exact user-supplied `Audio_Checker_UTF8_Multicore.zip`; selected location is persisted. The real `aucdtect.exe` bundle generates checker reports; failed/unavailable checks remain PARTIAL.
- **Results UI:** rows explicitly selectable with mouse/keyboard Ctrl+C; added **Copy results** for visible rows, and **Open log folder** beside **Open log**. The log folder button opens the product's own isolated Documents folder.
- Earlier hard requirement unchanged: **ALAC exclusively by official `refalac64.exe`; FLAC exclusively by Xiph's official `flac.exe`**; no FFmpeg ALAC/FLAC encoding. Source untouched, staging/Undo retained.
- **Status:** Code and separate GitHub regression test source committed. Inlined UI JavaScript syntax PASSED. Complete Python AST/regression suite, native Windows WebView2/binaries, DiscID live response, real AudioChecker job, credit-vote/metadata parity and the 94-release batch have **NOT RUN** in the target environment. Do not call fully working/stable. New full audit: [INNA regression (2026-10-14)](AUDIT_INNA_2026-10-14.md). Test using a disposable collection and a new empty destination.

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

**MusicBrainz physical DiscID/TOC lookup**, based on the project's [CUE Corrector - MusicBrainz DiscID 1.3.1](../CUE%20Corrector%20-%20MusicBrainz%20DiscID%201.03.01.pyw), uses the real EAC rip-log TOC when available or CUE + sample-exact lossless track lengths. DiscID is computed from CD sector offsets (including standard 150-frame lead-in), not guessed from track filenames. It sends the DiscID and TOC to MusicBrainz, collects candidate *release IDs*, then fetches the concrete release. Where both DiscID and barcode are known, it requires the exact intersection. If several distinct physical editions remain, **it does not silently select one**; it preserves the source identity and records the ambiguity in the log.

Confirmed metadata can update a release folder's worldwide initial date, release-type ` - Single` or ` - EP` suffix, and catalog-number-first bracket identifier. For actual matching physical CD editions, existing CUE/rip LOG basename uses the catalog number, barcode fallback, while the documented release country is only a **territory fallback**, not independently verified pressing country. WEB gets no physical country prefix. A release may remain PARTIAL if manufacturing-country evidence, Beatport/BPTopTracker digital catalog fallback or other strict project checks are missing.

Output individual audio tracks follow `NN Track Title.m4a` for ALAC or `NN Track Title.flac` for FLAC (number padded, exactly one space, no artist prefix). Preserve featured artists, remixes and version descriptors. Ordinary FLAC/ALAC inputs transcode or copy with verification; CUE image splitting and limited legacy CUE repair preserve indexes and pregaps where supported.

## Remaining limitations / testing

**Status: Under construction ⚠️.** Actual Windows/WebView2 acceptance and whole-collection output have **not** been completed. Source has been checked for expected controls and embedded JavaScript syntax; bundled AudioChecker Python source/log format and ZIP structure were inspected separately. Full Python regression suite against v0.3.1, execution of the embedded Windows binaries, actual AudioChecker/ALAC round-trips and real MusicBrainz HTTP lookups on user files are **not yet verified**.

Remaining: automatic Discogs pressing/manufacturing evidence; Beatport/BPTopTracker catalog fallback; user-controlled resolution of ambiguous DiscID matches (currently preserved/flagged without stopping); deeper tag/artwork parity; unusual CUE layouts; performance validation on mixed HDD/SSD input. No "stable" or fully-compliant claim until verified.

**Next test:** One disposable WEB single + one known CD with CUE/LOG + valid AudioChecker ZIP beside `.pyw`, **new empty destination**, then inspect filename, catalog, `audiochecker.log`, workers, Undo and the run log.

See [current continuity](CONTINUITY.md), [UI control contract](UI_UX_CONTROL_CONTRACT.md), [prior INNA audit](AUDIT_INNA_2026-10-13.md), project [rules](../../RULES.md) and [music glossary](../../MUSIC_GLOSSARY.md).
