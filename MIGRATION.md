# Torrent Project repository migration

Migrated 2026-10-13 from `karpuzikov/userscripts` (`main`, source commit `dfff49a38e49b2c7a733b17175eb90b92c9be4c4`) into `karpuzikov/TRRNT-project` (`main`).

## Scope

- All **20 files** formerly under `audio-tools/discography-torrent-project/` migrated to the root of this repository, keeping their relative `tools/` and `workflow/` directories.
- Every tool/workflow source file is a byte-identical copy (same Git blob SHA). At the initial migration, `RULES.md` was also byte-identical, while `README.md`, `PROJECT.md`, and `CONTINUITY.md` were updated to identify the new canonical repository. On 2026-10-13, `RULES.md` was subsequently updated to record repository ownership; **all original tool/workflow blobs remain unchanged**.
- Shared global `SOFTWARE_RULES.md`, `UI_UX_RULES.md`, and `UI_UX_ACCEPTANCE.md` were copied from `userscripts`. They remain global standards and should be kept in sync with canonical policies.
- The former DEA GitHub Actions YAML files were copied **unchanged** into `archived-workflows/` (outside `.github/workflows/`). This deliberately disables automatic workflows and notification spam. Their original paths refer to `userscripts` and must be revised if the user explicitly requests activation.
- Legacy DEA Windows EXE releases/assets remain hosted in `karpuzikov/userscripts/releases`. A repository-file move does not move GitHub Release assets or past commit/issue history. Historical references to the old repository/paths remain intentionally in historical sections.

**Out of scope:** separately maintained utilities such as RuTracker Digital Release Linker, qBittorrent backup, other AudioChecker patches, Picard/MusicBrainz tools, and MCC. They remain in their independent repository locations; copying them would change their update/distribution contracts.

## Verified original project file hashes

The following Git blob SHAs represent the **source-side originals** and allow lossless migration checks. At the initial migration, only three project Markdown files differed. Following the 2026-10-13 handoff, root `RULES.md` also differs intentionally to reflect repository ownership. The original blob SHAs below remain valid as historical source-side checksums.

| New relative path | Old source blob SHA |
| --- | --- |
| `CONTINUITY.md` | `b505037f4597a72d1d6ba961a2db800754ee0f28` |
| `PROJECT.md` | `cc4dd0765464c28e66bc7c01c2daeafe164f166d` |
| `README.md` | `113f874692489d5922609350b26dda704416ff19` |
| `RULES.md` | `c1b65937f80c0db25614d5cf48c07acecbb1154c` |
| `tools/ALAC Conversion Automation v0.2.pyw` | `e16c73b2151c5d3dce018ace47b5b5129f3cf48d` |
| `tools/AudioChecker Automation v0.1.pyw` | `3f75cca2c8ac432442eeafe9a0f401f47e0cde86` |
| `tools/CUE Corrector - MusicBrainz DiscID 1.3.1.pyw` | `4c39f72298b021d28c6027a547ee8aaf9a7a9099` |
| `tools/Duplicate Edition Analyzer v0.11.4.pyw` | `71e283e2db0b23412455ce4d9a6b495d8adb2acf` |
| `tools/Duplicate Edition Analyzer v0.22.10.pyw` | `83a861cc817e0c5a2c5c59d60a242fd847f769ad` |
| `tools/Duplicate Edition Analyzer v0.22.11.pyw` | `1ac72f342cb874f1aa053d173ad099a6694256f0` |
| `tools/Duplicate Edition Analyzer v0.22.12.pyw` | `ae8bc4fb89c3afd939ec45365d068d461893d226` |
| `tools/Duplicate Edition Analyzer v0.22.6.pyw` | `ca2391e7afd352fd8da71cccd5a13c6d6b860eff` |
| `tools/Duplicate Edition Analyzer v0.22.7.pyw` | `01fbfe746c7d6fcfabc36f953d886039184eff57` |
| `tools/Duplicate Edition Analyzer v0.22.8.pyw` | `c28ee7374250283644b68a9436a80c0c6cbbf53d` |
| `tools/Duplicate Edition Analyzer v0.22.9.pyw` | `522251dd8146cf67cf43e31222c2ca089e19a713` |
| `tools/Folder Structure Scanner.pyw` | `57b89528aabc9ace98d5e3b095926daf8088693e` |
| `workflow/ALAC + header.mte` | `f1040df2366e232dcec88d8c7742f18628a9cc7f` |
| `workflow/CUE LOG 4.1.pyw` | `4a30c62e6371c112a45fd1e68a1697c390b879b2` |
| `workflow/Photoshop script 500png 600jpg 1.4.jsx` | `1f30de522476e0c902b3da0db8492f3b7561b186` |
| `workflow/Мой стандарт.mta` | `099d9545c8f184389b04a622379afbd6029252e9` |

## Development handoff

- **New canonical source:** `karpuzikov/TRRNT-project` root, `main`.
- **Project rules:** `RULES.md` plus `SOFTWARE_RULES.md`, `UI_UX_RULES.md`, `UI_UX_ACCEPTANCE.md`.
- **Project state:** `CONTINUITY.md`, `PROJECT.md`, `README.md`.
- **Current legacy DEA reference:** `tools/Duplicate Edition Analyzer v0.22.12.pyw` - `0.22.12 - Under construction ⚠️`. The lightweight/native branches were reconstructed with file-exact source snapshots; see the branch preservation section below.
- Preserve existing functionality, source-file names/versions, local user data/settings, and the current Windows testing gate. Do not modify GitHub Actions without explicit user approval.
## Development branch migration

Three project-specific branches were preserved under **the same branch names** in `karpuzikov/TRRNT-project`:

| New branch | Source branch tip in `userscripts` | Preserved project files |
| --- | --- | --- |
| `dea-lightweight-modern` | `9a3454bcb8faba78b6840dda97bbf56df3dccfd1` | 12 exact blob copies: `lightweight/` versions 0.4.1-0.4.9, branch README, RULES, CONTINUITY |
| `dea-lightweight-ascii-ui-test` | `c59b5671626c44206f4f9db1244c75fe756326b6` | 5 exact copies: version 0.4.0 and launcher, branch README, RULES, CONTINUITY |
| `dea-native-light-test` | `7d43add67a280e5b6134cb2e9eaccf31daeaa405` | 8 exact copies: native-light C++ source+README, native branch PROJECT/README, historical build workflow archived inactive |

Branch snapshots have new commit ancestry from the target repository's main branch. **Original Git commit history, issues, tags, PR discussions and release assets are NOT imported**, and the original historical source branches remain accessible in `userscripts`. All migrated branch-specific file blobs were verified identical by SHA. No Actions workflows are active in the new repository.

