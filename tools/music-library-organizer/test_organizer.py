# Music Library Organizer v0.3.8 - INNA naming and CUE recovery regressions.
# Run on Windows: py -3 tools\music-library-organizer\test_organizer.py
# These tests do not replace Windows GUI/interaction/real-disc acceptance.
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import re
import sys
import tempfile
import threading
import unittest
import wave
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "Music Library Organizer v0.3.8.pyw"
loader = importlib.machinery.SourceFileLoader("mlo_under_test", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
mlo = importlib.util.module_from_spec(spec)
loader.exec_module(mlo)


class UserExamples20261014(unittest.TestCase):
    def test_release_title_has_no_feature_credit(self):
        self.assertEqual(mlo.clean_release_title("Cola Song (ft. J Balvin)"), "Cola Song")
        self.assertEqual(mlo.release_title_normalize(
            "Good Time (feat. Pitbull)", "Single"), "Good Time - Single")

    def test_single_release_feature_transfers_to_track_not_folder(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "2014-07-15 - Good Time (feat. Pitbull) [075679936356]"
            output = root / "Output"
            source.mkdir()
            (source / "01 Good Time.flac").write_bytes(b"test-copy")
            app = mlo.Organizer()
            app._current_identity = {
                "title": "Good Time", "physical": False,
                "feature_hint": "Pitbull", "metadata_verified": False}
            app._current_release_feature = "Pitbull"
            app._artist_root = "INNA"
            app.encoding_workers = 1
            observed = []
            with patch.object(mlo, "metadata", return_value=("1","Good Time","INNA")), \
                 patch.object(mlo, "probe", return_value={"codec_name":"flac"}), \
                 patch.object(mlo, "_update_copied_track_tags",
                              side_effect=lambda path,title,artist,num: observed.append(
                                  (title,artist,num))):
                app._export_release(source,output,"FLAC","ffmpeg","ffprobe")
            self.assertTrue((output / "01 Good Time (ft. Pitbull).flac").is_file())
            self.assertEqual(observed, [("Good Time (ft. Pitbull)","INNA",1)])
            self.assertEqual(mlo.release_title_normalize(
                "Good Time (feat. Pitbull)", "Single"), "Good Time - Single")

    def test_remix_and_extended_mix_release_case(self):
        self.assertEqual(mlo.release_title_normalize(
            "Déjà Vu (extended mix)", "Single"), "Déjà Vu (Extended Mix) - Single")
        self.assertEqual(mlo.release_title_normalize(
            "Sun Is Up (remix)", "Single"), "Sun Is Up (Remix) - Single")

    def test_legacy_cd_promo_title_cleaning(self):
        self.assertEqual(mlo.clean_release_title(
            "Inna - Déjá Vu (Maxi CD Single) (Promo)", "INNA"), "Déjá Vu")
        self.assertEqual(mlo.clean_release_title(
            "Inna - Party Never Ends (Part 2) (2013) [WPCR-15062]", "INNA"),
            "Party Never Ends (Part 2)")

    def test_diggy_down_filename_and_tag_title_are_same(self):
        title, artist = mlo.normalize_artist_title(
            "Diggy Down (Radio Edit) [ft. Marian Hill]", "INNA")
        self.assertEqual(title, "Diggy Down (ft. Marian Hill) [Radio Edit]")
        self.assertEqual(artist, "INNA")
        self.assertEqual(mlo.track_filename(1, title, ".m4a"),
                         "01 Diggy Down (ft. Marian Hill) [Radio Edit].m4a")
        title2, _ = mlo.normalize_artist_title(
            "Diggy Down (Extended Version) [ft. Marian Hill]", "INNA")
        self.assertEqual(title2,
                         "Diggy Down (ft. Marian Hill) [Extended Version]")

    def test_explicit_feature_removed_from_combined_artist_tag(self):
        title, artist = mlo.normalize_artist_title(
            "Diggy Down (Radio Edit) [ft. Marian Hill]",
            "INNA & Marian Hill", "INNA")
        self.assertEqual(title, "Diggy Down (ft. Marian Hill) [Radio Edit]")
        self.assertEqual(artist, "INNA")
        title2, artist2 = mlo.normalize_artist_title(
            "Diggy Down (Extended Version) [ft. Marian Hill]",
            "INNA, Marian Hill", "INNA")
        self.assertEqual(title2, "Diggy Down (ft. Marian Hill) [Extended Version]")
        self.assertEqual(artist2, "INNA")
        title3, artist3 = mlo.normalize_artist_title(
            "Song (ft. Artist B)", "Artist A & Artist B", "Unrelated")
        self.assertEqual(artist3, "Artist A & Artist B")

    def test_bob_taylor_reverse_credit_requires_consensus(self):
        value = "Deja Vu (Wideboy Stadium Radio Edit) [Bob Taylor ft. INNA]"
        original, artist = mlo.normalize_artist_title(value, "Bob Taylor")
        self.assertEqual(original, "Deja Vu (ft. INNA) [Wideboy Stadium Radio Edit]")
        self.assertEqual(artist, "Bob Taylor")
        consensus = {(
            mlo._song_key(value), frozenset(("inna", "bob taylor"))): "INNA"}
        expected, corrected = mlo.normalize_artist_title(
            value, "Bob Taylor", "INNA", consensus)
        self.assertEqual(corrected, "INNA")
        self.assertEqual(expected,
                         "Deja Vu (ft. Bob Taylor) [Wideboy Stadium Radio Edit]")
        # Different versions MUST retain their authentic titles.
        variant, _ = mlo.normalize_artist_title(
            "Deja Vu (ft. Bob Taylor) [Play & Win Club Mix]", "INNA")
        self.assertEqual(variant, "Deja Vu (ft. Bob Taylor) [Play & Win Club Mix]")

    def test_track_remix_added_credit_order_retained(self):
        t, _ = mlo.normalize_artist_title(
            "Reminder (Remix) [ft. A$AP Rocky & Young Thug]", "The Weeknd")
        self.assertEqual(t, "Reminder (Remix) [ft. A$AP Rocky & Young Thug]")

    def test_exact_discid_filters_two_cd_release(self):
        from unittest.mock import patch
        base = [{"discid": "valid-disc", "tracks": 16}]
        def fake_mb(path, **kwargs):
            if "double" in path:
                return {"media": [{"format":"CD","track-count":16},
                                  {"format":"CD","track-count":9}]}
            if "single" in path:
                return {"id":"single","media":[{"format":"CD","track-count":16,
                                                 "discs":[{"id":"valid-disc"}]}]}
            raise RuntimeError("Unknown release")
        with patch.object(mlo, "mb_json", side_effect=fake_mb):
            matching, rejected = mlo._disc_editions_by_medium(
                ["double", "single"], base)
        self.assertEqual([x["id"] for x in matching], ["single"])
        self.assertEqual(len(rejected), 1)

    def test_result_selection_and_log_folder_gui(self):
        self.assertIn('-webkit-user-select:text!important', mlo.HTML)
        self.assertIn('id="openLogFolder"', mlo.HTML)
        self.assertIn('id="copyResults"', mlo.HTML)
        self.assertNotIn('id="chooseChecker"', mlo.HTML)


class BuiltinCheckerRegression(unittest.TestCase):
    def test_no_external_analyzer_zip_or_browse(self):
        self.assertNotIn("AudioChecker ZIP", mlo.HTML)
        self.assertNotIn('id="chooseChecker"', mlo.HTML)
        self.assertFalse(hasattr(mlo, "setup_checker"))
        self.assertTrue(callable(mlo.integrated_check_web_release))

    def test_spectral_classifier_has_explicit_uncertainty(self):
        broad = [1e9] * (mlo._FFT_N // 2 + 1)
        self.assertEqual(mlo._spectral_summary([broad] * 8, 44100)[0], "NO_STRONG_CUTOFF")
        limited = list(broad)
        for index in range(len(limited)):
            if index * 44100 / mlo._FFT_N > 17500:
                limited[index] = 10
        self.assertEqual(mlo._spectral_summary([limited] * 8, 44100)[0], "BANDLIMIT_SIGNATURE")
        self.assertEqual(mlo._spectral_summary([broad] * 8, 32000)[0], "INCONCLUSIVE")

    def test_integrated_report_integrity_and_unicode(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            paths = [folder / "01 Déjà Vu.flac", folder / "02 Ночь.m4a"]
            items = [(paths[0], "NO_STRONG_CUTOFF", -2.0, 20, 44100, "No cutoff"),
                     (paths[1], "INCONCLUSIVE", None, 4, 44100, "No evidence")]
            log = folder / "audiochecker.log"
            log.write_bytes(mlo._integrated_checker_report(folder, items, mlo.VERSION).encode("utf-8"))
            self.assertTrue(mlo._validate_integrated_report(log, paths, mlo.VERSION))
            log.write_bytes(log.read_bytes() + b"tamper")
            self.assertFalse(mlo._validate_integrated_report(log, paths, mlo.VERSION))

    def test_missing_historical_cue_image_keeps_split_tracks_partial(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "physical"
            dest = Path(tmp) / "sorted"
            source.mkdir()
            for number in (1, 2):
                (source / f"{number:02d} Track {number}.flac").write_bytes(
                    b"source-must-survive-" + str(number).encode())
            (source / "INNA.cue").write_text(
                'FILE "Missing Album Image.wav" WAVE\n'
                '  TRACK 01 AUDIO\n'
                '    TITLE "Track 1"\n'
                '    INDEX 01 00:00:00\n'
                '  TRACK 02 AUDIO\n'
                '    TITLE "Track 2"\n'
                '    INDEX 01 03:00:00\n', encoding="utf-8")
            app = mlo.Organizer()
            app._current_identity = {"physical": True,
                                     "catalog": "", "barcode": "",
                                     "metadata_verified": False}
            app._artist_root = "INNA"
            app._credit_consensus = {}
            app._export_warnings = []
            app.encoding_workers = 1
            def fake_meta(path):
                num = int(Path(path).name[:2])
                return str(num), f"Track {num}", "INNA"
            with patch.object(mlo, "metadata", side_effect=fake_meta), \
                 patch.object(mlo, "probe", return_value={"codec_name": "flac"}), \
                 patch.object(mlo, "_update_copied_track_tags"):
                app._export_release(source, dest, "FLAC", "ffmpeg", "ffprobe")
            self.assertFalse((dest / "INNA.cue").exists())
            self.assertTrue((dest / "Unresolved CUE - INNA.cue").is_file())
            self.assertTrue((dest / "01 Track 1.flac").is_file())
            self.assertTrue((dest / "02 Track 2.flac").is_file())
            self.assertTrue(app._export_warnings)
            self.assertTrue((source / "INNA.cue").is_file())

    def test_result_selection_survives_status_poll_and_has_path_icon(self):
        self.assertIn("renderedResultMarkup", mlo.HTML)
        self.assertIn("selectingResults", mlo.HTML)
        self.assertIn("button.result-folder", mlo.HTML)
        self.assertNotIn("id=\"openCheckerZip\"", mlo.HTML)



class UserCorrections20261014(unittest.TestCase):
    def test_picard_english_small_words(self):
        cases = {
            "10 Minutes (Club Mix By Play & Win)": "10 Minutes (Club Mix by Play & Win)",
            "We're Going In The Club": "We're Going in the Club",
            "More Than Friends feat. Daddy Yanke": "More Than Friends (ft. Daddy Yanke)",
            "INNDiA feat. Play&Win": "INNDiA (ft. Play & Win)",
        }
        for supplied, expected in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(mlo.normalized_title(supplied), expected)
                self.assertEqual(mlo.track_filename(13, supplied, ".m4a"),
                                 "13 " + expected + ".m4a")

    def test_deluxe_edition_typo_and_feature_free_release(self):
        title = mlo.release_title_normalize(
            "Party Never Ends, Pt. 2 (Deluxe Editon)", "EP")
        self.assertEqual(title, "Party Never Ends, Pt. 2 (Deluxe Edition) - EP")
        self.assertNotIn("feat.", title.lower())

    def test_source_filename_feature_survives_short_tag(self):
        from unittest.mock import patch
        with patch.object(mlo, "metadata",
                          return_value=("3", "More Than Friends", "INNA")):
            num, title, artist = mlo.extract_number_title(
                Path("03 More Than Friends feat. Daddy Yanke.m4a"), "INNA")
        self.assertEqual((num, title, artist),
                         (3, "More Than Friends (ft. Daddy Yanke)", "INNA"))

    def test_legacy_maxi_cd_single_type_kept_without_online_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "Inna - Déjá Vu (Maxi CD Single) (Promo)"
            release.mkdir()
            (release / "original.cue").write_text(
                'FILE "missing.wav" WAVE\n'
                '  TRACK 01 AUDIO\n'
                '    TITLE "Déjá Vu"\n'
                '    INDEX 01 00:00:00\n', encoding="utf-8")
            info = mlo.release_identity(release, mlo.collect_files(release),
                                        default_artist="INNA", allow_network=False)
            self.assertEqual(info["release_type"], "Single")
            self.assertEqual(mlo.release_folder_target(info), "Déjá Vu - Single")

    def test_unknown_cd_legacy_names_are_cleaned_not_retained(self):
        example = {"folder": "Inna - Déjá Vu (Maxi CD Single) (Promo)",
                   "title": "Inna - Déjá Vu (Maxi CD Single) (Promo)",
                   "date": "", "physical": True, "catalog": "", "barcode": "",
                   "country": "", "root_artist": "INNA", "notes": [],
                   "release_type": ""}
        self.assertEqual(mlo.release_folder_target(example), "Déjá Vu")
        self.assertIn("unverified", " ".join(example["notes"]))
        example = {"folder": "Inna - Party Never Ends (Part 2) (2013) [WPCR-15062]",
                   "title": "Party Never Ends (Part 2): Japan",
                   "date": "2012-01-01", "physical": True,
                   "catalog": "WPCR-15062", "barcode": "",
                   "country": "JP", "root_artist": "INNA", "notes": [],
                   "release_type": "Album", "edition_date": "2013-01-01"}
        target = mlo.release_folder_target(example)
        self.assertIn("Party Never Ends (Part 2)", target)
        self.assertIn("WPCR-15062", target)
        self.assertNotIn(":", target)
        self.assertNotEqual(target, example["folder"])

    def test_verified_web_barcode_does_not_require_catalog(self):
        verified = {"physical": False, "metadata_verified": True,
                    "barcode": "5948204032791", "catalog": "", "notes": []}
        checker = [{"possible_bandlimited": 0, "inconclusive": 0}]
        self.assertEqual(mlo.completion_reasons(verified, [], checker), [])
        self.assertIn("WEB", " ".join(mlo.completion_reasons(
            dict(verified, barcode=""), [], checker)))
        self.assertIn("Integrated AudioChecker report missing",
                      mlo.completion_reasons(verified, [], []))
        self.assertTrue(mlo.completion_reasons(
            dict(verified, physical=True, country_provenance=
                 "MusicBrainz release-territory-fallback (not verified pressing)"),
            [], []))
        self.assertTrue(mlo.completion_reasons(
            dict(verified, metadata_verified=False), [], checker))


class RebuildMissingImageCue(unittest.TestCase):
    def test_exact_sixteen_track_image_cue_remaps_to_split_files(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            oldcue = root / "Party Never Ends.cue"
            starts = list(range(0, 16 * 150, 150))
            lines = []
            for n, stamp in enumerate(starts, 1):
                m, rem = divmod(stamp, 75 * 60)
                sec, frame = divmod(rem, 75)
                if n == 1:
                    lines.append('FILE "missing original.wav" WAVE')
                lines.extend([f"  TRACK {n:02d} AUDIO",
                              f'    TITLE "Track {n}"',
                              f"    INDEX 01 {m:02d}:{sec:02d}:{frame:02d}"])
            oldcue.write_text("\n".join(lines), encoding="utf-8")
            parsed = mlo.cue_read(oldcue)
            mapping = {}
            for n in range(1, 17):
                src = root / f"{n:02d} Track {n}.flac"
                dst = root / "New" / f"{n:02d} Track {n}.m4a"
                mapping[src] = dst
            with patch.object(mlo, "_exact_cd_sectors", return_value=150):
                rebuilt, problem = mlo.reconstruct_missing_image_cue(parsed, oldcue, mapping)
            self.assertEqual(problem, "")
            self.assertEqual(rebuilt.count('FILE "'), 16)
            self.assertEqual(rebuilt.count("INDEX 01 00:00:00"), 16)
            self.assertIn('FILE "16 Track 16.m4a" WAVE', rebuilt)
            self.assertTrue(oldcue.exists())

    def test_successful_cue_rebuild_is_reported_without_false_warning(self):
        import inspect
        source = inspect.getsource(mlo.Organizer._organize)
        export = inspect.getsource(mlo.Organizer._export_release)
        self.assertIn('result["message"] += " | " + " | ".join(self._export_notices)', source)
        self.assertIn('playable split-track CUE rebuilt after', export)

    def test_sector_mismatch_or_index00_remains_unresolved(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cue = root / "old.cue"
            cue.write_text('FILE "missing.wav" WAVE\n'
                           '  TRACK 01 AUDIO\n'
                           '    TITLE "A"\n'
                           '    INDEX 01 00:00:00\n'
                           '  TRACK 02 AUDIO\n'
                           '    TITLE "B"\n'
                           '    INDEX 01 00:02:00\n', encoding="utf-8")
            mapping = {root / "01 A.flac": root / "new" / "01 A.flac",
                       root / "02 B.flac": root / "new" / "02 B.flac"}
            with patch.object(mlo, "_exact_cd_sectors", return_value=149):
                rebuilt, problem = mlo.reconstruct_missing_image_cue(
                    mlo.cue_read(cue), cue, mapping)
            self.assertIsNone(rebuilt)
            self.assertIn("150", problem)
            cue.write_text(cue.read_text() + "    INDEX 00 00:01:70\n")
            with patch.object(mlo, "_exact_cd_sectors", return_value=150):
                rebuilt, problem = mlo.reconstruct_missing_image_cue(
                    mlo.cue_read(cue), cue, mapping)
            self.assertIsNone(rebuilt)
            self.assertIn("INDEX 00", problem)


class NamingRules(unittest.TestCase):
    def test_release_types_and_remix_exception(self):
        self.assertEqual(mlo.release_title_normalize("Good Time", "Single"), "Good Time - Single")
        self.assertEqual(mlo.release_title_normalize("Title - The Remixes EP", "EP"), "Title (The Remixes)")
        self.assertEqual(mlo.release_title_normalize("Plain Jane Remix", "Single"), "Plain Jane (Remix) - Single")
        x = {"folder":"Inna - YO 2019", "title":"YO", "date":"2019-05-01", "catalog":"CAT123", "barcode":"1234", "physical":False, "country":"", "release_type":"Album", "track_count":13, "notes":[]}
        self.assertEqual(mlo.release_folder_target(x), "2019-05-01 - YO [CAT123]")

    def test_yo_physical_edition_folder_and_sidecar_identity(self):
        info = {"folder": "Inna - YO 2019", "title": "YO",
                "date": "2019-05-31", "catalog": "GR18186",
                "barcode": "", "physical": True, "country": "RO",
                "release_type": "Album", "track_count": 11, "notes": []}
        self.assertEqual(mlo.release_folder_target(info),
                         "2019-05-31 - YO [RO - GR18186]")

    def test_release_type_suffix_relocation(self):
        self.assertEqual(mlo.release_title_normalize("Song (EP) (Extended Mix)", "EP"),
                         "Song (Extended Mix) - EP")
        self.assertEqual(mlo.release_title_normalize("Song (Remixes) (Single)", "Single"),
                         "Song (Remixes)")
        self.assertEqual(mlo.release_title_normalize("Song (Maxi CD Single) (Promo)", "Single"),
                         "Song (Maxi CD) (Promo) - Single")

    def test_checker_rejects_fake_report(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "audiochecker.log"
            log.write_text("AudioChecker fake output\\n100", encoding="utf-8")
            self.assertFalse(mlo._validate_integrated_report(log, [Path("01 Example.flac")], mlo.VERSION))

    def test_cd_reissue_year_suffix_from_concrete_edition(self):
        info = {"folder": "Legacy", "title": "I Am the Club Rocker",
                "date": "2011-09-19", "edition_date": "2012-07-11",
                "catalog": "WPCR-14516", "barcode": "", "physical": True,
                "country": "JP", "release_type": "Album", "notes": []}
        self.assertEqual(mlo.release_folder_target(info),
                         "2011-09-19 - I Am the Club Rocker [JP - WPCR-14516 - 2012]")
        info["edition_date"] = "2011-10-10"
        self.assertEqual(mlo.release_folder_target(info),
                         "2011-09-19 - I Am the Club Rocker [JP - WPCR-14516]")

    def test_cd_does_not_export_existing_web_audiochecker_log(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, dest = root / "CD", root / "Output"
            source.mkdir()
            (source / "01 Track.flac").write_bytes(b"placeholder-audio")
            (source / "original.cue").write_text(
                'FILE "01 Track.flac" WAVE\n  TRACK 01 AUDIO\n'
                '    TITLE "Track"\n    INDEX 01 00:00:00\n',
                encoding="utf-8")
            (source / "original.log").write_text("Exact Audio Copy rip log", encoding="utf-8")
            checker_source = source / "audiochecker.log"
            checker_source.write_text("historic WEB report", encoding="utf-8")
            app = mlo.Organizer()
            app._current_identity = {"physical": True, "metadata_verified": True,
                                     "catalog": "CAT001", "barcode": ""}
            app.encoding_workers = 1
            with patch.object(mlo, "probe", return_value={"codec_name": "flac"}), \
                 patch.object(mlo, "_update_copied_track_tags"):
                app._export_release(source, dest, "FLAC", "unused-ffmpeg", "unused-ffprobe")
            self.assertFalse((dest / "audiochecker.log").exists())
            self.assertTrue((dest / "CAT001.cue").exists())
            self.assertTrue((dest / "CAT001.log").exists())
            self.assertEqual(checker_source.read_text(encoding="utf-8"), "historic WEB report")
            self.assertEqual((dest / "01 Track.flac").read_bytes(), b"placeholder-audio")

    def test_cue_only_cd_does_not_become_web(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "Unknown"
            folder.mkdir()
            cue = folder / "one.cue"
            cue.write_text('FILE "01 Track.flac" WAVE\n'
                           '  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
            info = mlo.release_identity(folder, [cue], default_artist="Artist", allow_network=False)
            self.assertTrue(info["physical"])

    def test_zero_padding_exact_one_space(self):
        self.assertEqual(mlo.track_filename(1, "Red Lips (ft. Sam Bruno) [Skrillex Remix]", ".m4a"),
                         "01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a")
        self.assertEqual(mlo.track_filename(12, "Example", ".flac"), "12 Example.flac")
        self.assertEqual(mlo.track_filename(101, "Example", ".flac"), "101 Example.flac")

    def test_three_positional_brackets(self):
        original = "C'Mon (Catch 'Em By Surprise) (feat. Busta Rhymes) (Extended Club Mix)"
        self.assertEqual(mlo.normalized_title(original),
                         "C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}")

    def test_original_feature_and_remix_preserved(self):
        self.assertEqual(mlo.normalized_title("Red Lips (ft. Sam Bruno) [Skrillex Remix]"),
                         "Red Lips (ft. Sam Bruno) [Skrillex Remix]")
        self.assertEqual(mlo.normalized_title("Reminder (Remix) [ft. A$AP Rocky & Young Thug]"),
                         "Reminder (Remix) [ft. A$AP Rocky & Young Thug]")

    def test_physical_catalog_and_web_id(self):
        self.assertEqual(mlo.catalog("2007-07-09 - Our Love to Admire [JP - TOCP-66683]"),
                         ("TOCP-66683", True))
        self.assertEqual(mlo.catalog("2002-08-19 - Turn on the Bright Lights [US - OLE 993-2 - 2012]"),
                         ("OLE 993-2", True))
        self.assertEqual(mlo.catalog("2018-02-16 - El Pintor Bonus Tracks [OLE13186]"),
                         ("OLE13186", False))

    def test_source_and_destination_safety(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "Artist ALAC"
            src.mkdir()
            with self.assertRaises(ValueError):
                mlo.check_paths(str(src), str(src), "ALAC")
            with self.assertRaises(ValueError):
                mlo.check_paths(str(src), str(src / "child"), "FLAC")
            with self.assertRaises(ValueError):
                mlo.check_paths(str(src), str(Path(td) / "Other ALAC"), "FLAC")
            self.assertEqual(mlo.check_paths(str(src), str(Path(td) / "Artist FLAC"), "FLAC")[2], "FLAC")


class AutomaticBatch(unittest.TestCase):
    @staticmethod
    def identity(folder, files, default_artist=""):
        name = folder.name
        catalog = mlo.catalog(name)[0]
        title = re.sub(r"^\d{4}-\d\d-\d\d - ", "", name).split(" [")[0]
        return {"folder": name, "title": title, "date": "2020-01-01",
                "catalog": catalog, "barcode": "", "physical": False,
                "country": "", "country_provenance": "", "release_type": "Single",
                "selected_release": "test-only-id", "metadata_verified": True,
                "notes": [], "track_count": 1}

    def test_missing_audiochecker_does_not_block_and_second_run_skips(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "Input"
            output = base / "Output"
            folder = source / "2020-01-01 - Track [ABC123]"
            folder.mkdir(parents=True)
            (folder / "01 Track.flac").write_bytes(b"fixture")
            places = {"DATA": base / "Settings",
                      "SETTINGS": base / "Settings" / "settings.json",
                      "LOG": base / "Settings" / "activity.log",
                      "REPORT": base / "Settings" / "last-run.json",
                      "UNDO": base / "Settings" / "undo.json",
                      "HISTORY": base / "Settings" / "undo-history"}
            with patch.multiple(mlo, **places):
                app = mlo.Organizer()
                app.source, app.dest, app.fmt = str(source), str(output), "FLAC"
                app._export_release = lambda inp, out, fmt, ff, fp: shutil.copy2(
                    inp / "01 Track.flac", out / "01 Track.flac")
                with patch.object(mlo, "release_identity", side_effect=self.identity), \
                     patch.object(mlo, "ensure_ffmpeg", return_value=("ff", "ffprobe")), \
                     patch.object(mlo, "integrated_check_web_release", side_effect=RuntimeError("analyzer failed")):
                    app._organize(source, output, "FLAC")
                    self.assertEqual(app.counts["partial"], 1)
                    self.assertEqual(app.counts["done"], 0)
                    self.assertEqual(app.counts["failed"], 0)
                    self.assertIn("REQUIRED AudioChecker not completed", " | ".join(app.results[0]["warnings"]))
                    out_name = mlo.release_folder_target(self.identity(folder, []))
                    self.assertTrue((output / out_name / "01 Track.flac").is_file())
                    app._organize(source, output, "FLAC")
                    self.assertEqual(app.counts["skipped"], 1)
                    self.assertEqual(app.results[-1]["status"], "skipped")

    def test_bad_release_does_not_stop_good_release(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "Input"
            dest = base / "Output"
            folders = [source / "2020-01-01 - A [AAA]", source / "2020-01-01 - B [BBB]"]
            for folder in folders:
                folder.mkdir(parents=True)
                (folder / "01 Audio.flac").write_bytes(b"fixture")
            places = {key: base / "Data" / name for key, name in {
                "DATA":"", "SETTINGS":"settings.json","UNDO":"undo.json",
                "LOG":"activity.log","REPORT":"last-run.json","HISTORY":"undo-history"}.items()}
            with patch.multiple(mlo, **places):
                app = mlo.Organizer()
                app.source, app.dest, app.fmt = str(source), str(dest), "FLAC"
                def export(folder, output, fmt, ff, ffprobe):
                    if folder.name.endswith("[AAA]"):
                        raise ValueError("synthetic unsupported source")
                    shutil.copy2(folder / "01 Audio.flac", output / "01 Audio.flac")
                app._export_release = export
                with patch.object(mlo, "release_identity", side_effect=self.identity), \
                     patch.object(mlo, "ensure_ffmpeg", return_value=("ff", "ffprobe")), \
                     patch.object(mlo, "integrated_check_web_release", side_effect=RuntimeError("analyzer failed")):
                    app._organize(source, dest, "FLAC")
                self.assertEqual(app.counts["failed"], 1)
                self.assertEqual(app.counts["partial"], 1)
                out_name = mlo.release_folder_target(self.identity(folders[1], []))
                self.assertTrue((dest / out_name / "01 Audio.flac").is_file())

    def test_original_filename_version_survives_shorter_title_tag(self):
        from unittest.mock import patch
        with patch.object(mlo, "metadata", return_value=("1", "OK", "Artist")):
            number, title, _ = mlo.extract_number_title(Path("01 OK (Extended Version).flac"))
            self.assertEqual(number, 1)
            self.assertEqual(title, "OK (Extended Version)")




class DesktopStartupRecovery(unittest.TestCase):
    def test_bridge_exposes_only_safe_explicit_methods(self):
        class Engine:
            def __init__(self):
                self.window = object()  # Must not enter recursive pywebview scan.
                self.lock = threading.RLock()
                self.stop = threading.Event()
            def state(self):
                return {"alive": True}
            def browse(self, which):
                return which
            def open_folder(self, where):
                return {"opened": where}
            def open_log(self):
                return {"log": True}
            def organize(self, *args):
                return {"count": len(args)}
            def cancel(self):
                return {"cancelled": True}
            def undo(self):
                return {"undone": True}
            def info(self, text):
                self.last_log = text
        engine = Engine()
        bridge = mlo.DesktopBridge(engine)
        exported = [key for key in dir(bridge) if not key.startswith("_")]
        self.assertEqual(set(exported), {
            "state", "browse", "open_folder", "open_log", "open_log_folder",
            "organize", "cancel", "undo", "diagnostics"})
        self.assertEqual(bridge.state(), {"alive": True})
        self.assertEqual(bridge.browse("source"), "source")
        self.assertEqual(bridge.open_folder("C:\\Music"), {"opened":"C:\\Music"})
        self.assertEqual(bridge.organize("in","out","ALAC",2,4), {"count":5})
        self.assertEqual(bridge.diagnostics("bridge connected"), {"ok":True})
        self.assertIn("bridge connected", engine.last_log)
        self.assertNotIn("window", exported)
        self.assertNotIn("lock", exported)

    def test_each_startup_uses_distinct_app_private_webview2_profile(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            with patch.object(mlo, "DATA", Path(td)):
                first = mlo._session_profile()
                second = mlo._session_profile()
                self.assertNotEqual(first, second)
                self.assertTrue(second.is_dir())
                self.assertEqual(first.parent, second.parent)
                self.assertTrue(str(first).startswith(str(Path(td))))

    def test_main_exposes_bridge_not_organizer_and_isolates_webview_profile(self):
        from unittest.mock import patch
        class Event:
            def __iadd__(self, value):
                return self
        class FakeWindow:
            def __init__(self):
                self.events = type("Events", (), {
                    "shown":Event(),"loaded":Event(),"closed":Event()})()
        class Backend:
            def __init__(self):
                self.window = FakeWindow()
                self.args = None
            def create_window(self, title, **kwargs):
                self.args = kwargs
                return self.window
            def start(self, **kwargs):
                self.start_opts = kwargs
        class Log:
            def info(self, *args):
                pass
            def exception(self, *args):
                pass
        with tempfile.TemporaryDirectory() as td:
            backend = Backend()
            instance = type("FakeOrganizer", (), {})()
            root = Path(td)/"one-session"
            with patch.object(mlo, "ensure_data"), \
                 patch.object(mlo, "_startup_logger", return_value=Log()), \
                 patch.object(mlo, "_single_instance", return_value=None), \
                 patch.object(mlo, "_session_profile", return_value=root), \
                 patch.object(mlo, "load_ui", return_value=backend), \
                 patch.object(mlo, "Organizer", return_value=instance), \
                 patch.object(mlo.shutil, "rmtree"):
                mlo.main()
            self.assertIsInstance(backend.args["js_api"], mlo.DesktopBridge)
            self.assertIsNot(backend.args["js_api"], instance)
            self.assertEqual(backend.start_opts["gui"], "edgechromium")
            self.assertEqual(backend.start_opts["storage_path"], str(root))
            self.assertFalse(backend.start_opts["private_mode"])

    def test_html_uses_ready_event_and_single_flight_poll(self):
        self.assertIn('window.addEventListener("pywebviewready"', mlo.HTML)
        self.assertIn("if(pollBusy", mlo.HTML)
        self.assertIn("Promise.race", mlo.HTML)
        self.assertIn('id="reconnect"', mlo.HTML)
        self.assertNotIn('throw Error("Desktop connection failed.")', mlo.HTML)


class NativeEncoderCompliance(unittest.TestCase):
    def test_qaac_and_xiph_provenance(self):
        self.assertEqual(mlo._FLAC_VERSION, "1.5.0")
        self.assertTrue(mlo._FLAC_ZIP_URL.startswith(
            "https://ftp.osuosl.org/pub/xiph/releases/flac/"))
        from unittest.mock import patch
        from urllib.error import URLError
        with patch.object(mlo.urllib.request, "urlopen", side_effect=URLError("offline")):
            self.assertTrue(mlo._qaac_official_url().startswith(
                "https://github.com/nu774/qaac/releases/download/"))

    def test_native_encoder_only_no_ffmpeg_encode(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wav = root / "original.wav"
            wav.write_bytes(b"not a real decoder input: faked runner")
            calls = []
            def fake_cmd(argv, timeout=3600):
                calls.append(list(map(str, argv)))
                if argv[0] == "ffmpeg":
                    Path(argv[-1]).write_bytes(b"temporary PCM WAV")
                elif argv[0] in ("refalac64.exe", "flac.exe"):
                    target = argv[argv.index("-o")+1]
                    Path(target).write_bytes(b"native test bitstream")
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            for fmt, ext, encoder in (("ALAC", ".m4a", "refalac64.exe"),
                                      ("FLAC", ".flac", "flac.exe")):
                calls.clear()
                dst = root / ("generated" + ext)
                with patch.object(mlo, "cmd", side_effect=fake_cmd), \
                     patch.object(mlo, "ensure_encoder", return_value=encoder), \
                     patch.object(mlo, "_lossless_pcm_format",
                                  return_value=("pcm_s16le",16,44100)), \
                     patch.object(mlo, "probe",
                                  return_value={"codec_name":fmt.lower(),
                                                "sample_rate":"44100"}), \
                     patch.object(mlo, "pcm_hash", return_value="same"), \
                     patch.object(mlo, "_copy_audio_metadata") as tags, \
                     patch.object(mlo, "DATA", root):
                    mlo.convert("ffmpeg", wav, dst, fmt, ffprobe="ffprobe",
                                title="Test", number=1)
                encodes = [x for x in calls if x[0] in ("flac.exe", "refalac64.exe")]
                self.assertEqual(len(encodes), 1)
                self.assertEqual(encodes[0][0], encoder)
                self.assertTrue(all("-c:a" not in x for x in calls))
                tags.assert_called_once()

    def test_no_unsupported_codec_fallback(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "audio.wav"
            src.write_bytes(b"test")
            with patch.object(mlo, "_lossless_pcm_format",
                              return_value=("pcm_s16le",16,44100)), \
                 patch.object(mlo, "ensure_encoder", side_effect=RuntimeError("no refalac64")):
                with self.assertRaisesRegex(RuntimeError, "refalac64"):
                    mlo.convert("ffmpeg", src, Path(td)/"output.m4a", "ALAC",
                                ffprobe="ffprobe")
                self.assertFalse((Path(td)/"output.m4a").exists())

    def test_floating_pcm_and_lossy_sources_refused(self):
        from unittest.mock import patch
        with patch.object(mlo, "probe", return_value={
                "codec_name":"pcm_f32le", "sample_fmt":"flt", "sample_rate":"48000",
                "bits_per_sample":32}):
            with self.assertRaisesRegex(RuntimeError, "Floating-point"):
                mlo._lossless_pcm_format("ffprobe", Path("test.wav"))
        with patch.object(mlo, "probe", return_value={
                "codec_name":"mp3", "sample_fmt":"fltp", "sample_rate":"44100"}):
            with self.assertRaisesRegex(RuntimeError, "Lossy"):
                mlo._lossless_pcm_format("ffprobe", Path("test.mp3"))

    def test_zip_traversal_rejected(self):
        import io, zipfile
        with tempfile.TemporaryDirectory() as td:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("../unwanted.exe", b"unsafe")
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                mlo._safe_zip_extract(buf.getvalue(), Path(td)/"target")


class DiscIDAndWorkers(unittest.TestCase):
    def test_suggested_output_and_worker_defaults(self):
        self.assertEqual(mlo.suggested_output(r"C:\Music\INNA - UPDATED").replace("\\", "/"),
                         "C:/Music/INNA - UPDATED - Sorted")
        self.assertGreaterEqual(mlo.worker_limit(), 1)
        enc, checking = mlo.worker_defaults()
        self.assertTrue(1 <= enc <= mlo.worker_limit())
        self.assertTrue(1 <= checking <= mlo.worker_limit())

    def test_discid_uses_cue_corrector_sector_algorithm(self):
        import base64, hashlib
        start = [0, 150]
        leadout = 330
        result = mlo._disc_identity(start, leadout)
        payload = "01" + "02" + f"{leadout+150:08X}"
        payload += "".join(f"{n+150:08X}" for n in start) + ("0" * (97*8))
        expected = base64.b64encode(hashlib.sha1(payload.encode("ascii")).digest()).decode("ascii")
        expected = expected.replace("+", ".").replace("/", "_").replace("=", "-")
        self.assertEqual(result["discid"], expected)
        self.assertEqual(result["toc"], "1 2 480 150 300")

    def test_barcode_and_discid_must_agree_on_concrete_edition(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "2007-01-01 - Example [US - 1234567890123]"
            folder.mkdir()
            cue = folder / "rip.cue"
            cue.write_text('FILE "image.wav" WAVE\n  TRACK 01 AUDIO\n    TITLE "A"\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n    TITLE "B"\n    INDEX 01 00:02:00\n', encoding="utf8")
            log = folder / "rip.log"
            log.write_text('1 | 0:00.00 | 0:02.00 | 0 | 149\n'
                           '2 | 0:02.00 | 0:02.00 | 150 | 329\n')
            (folder / "image.wav").write_bytes(b"dummy CD audio")
            calls = []
            def fake_mb(path, **kwargs):
                calls.append(path)
                if path.startswith("discid/"):
                    return {"releases": [{"id": "disc-edition"}, {"id": "other-edition"}]}
                if path.startswith("release/?"):
                    return {"releases": [{"id": "disc-edition", "barcode": "1234567890123"}]}
                if path.startswith("release/other-edition"):
                    return {"id": "other-edition", "media": [
                        {"format": "CD", "track-count": 2},
                        {"format": "CD", "track-count": 2}]}
                if path.startswith("release/disc-edition"):
                    return {"id": "disc-edition", "title": "Example",
                            "barcode": "1234567890123", "country": "US",
                            "media": [{"format": "CD", "track-count": 2}],
                            "label-info": [{"catalog-number": "CAT-12"}],
                            "release-group": {"first-release-date": "2006-11-18", "primary-type": "Album"}}
                raise RuntimeError("Unexpected MusicBrainz request " + path)
            with patch.object(mlo, "mb_json", side_effect=fake_mb):
                info = mlo.release_identity(folder, mlo.collect_files(folder), default_artist="Artist")
            self.assertTrue(any(x.startswith("discid/") for x in calls))
            self.assertTrue(any("barcode%3A1234567890123" in x for x in calls))
            self.assertEqual(info["catalog"], "CAT-12")
            self.assertEqual(info["date"], "2006-11-18")
            self.assertEqual(info["selected_release"], "disc-edition")
            self.assertEqual(mlo.release_folder_target(info),
                             "2006-11-18 - Example [US - CAT-12]")

    def test_ambiguous_discid_preserves_original_name(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "2001 - Ambiguous CD [US - none]"
            folder.mkdir()
            (folder / "A.cue").write_text('FILE "album.wav" WAVE\n'
                             '  TRACK 01 AUDIO\n    TITLE "A"\n    INDEX 01 00:00:00\n'
                             '  TRACK 02 AUDIO\n    TITLE "B"\n    INDEX 01 00:01:00\n')
            (folder / "album.wav").write_bytes(b"test")
            (folder / "A.log").write_text('1 | 0:00.00 | 0:01.00 | 0 | 74\n'
                                          '2 | 0:01.00 | 0:01.00 | 75 | 149\n')
            with patch.object(mlo, "mb_json", return_value={
                    "releases": [{"id": "edition1"}, {"id": "edition2"}]}):
                info = mlo.release_identity(folder, mlo.collect_files(folder), default_artist="Artist")
            self.assertFalse(info["metadata_verified"])
            self.assertEqual(mlo.release_folder_target(info), folder.name)

    def test_worker_count_in_parallel_integrated_checker_log(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            tracks = [dest / "01 Track.flac", dest / "02 Song.flac"]
            for track in tracks:
                track.write_bytes(b"fixture PCM decoding mocked")
            def fake_job(track, ff, ffprobe, event):
                return track, "NO_STRONG_CUTOFF", -4.0, 24, 44100, "No cutoff"
            with patch.object(mlo.shutil, "which", return_value="ffprobe"), \
                 patch.object(mlo, "_integrated_checker_job", side_effect=fake_job):
                reports = mlo.integrated_check_web_release(
                    dest, "FLAC", "ffmpeg", threading.Event(), workers=2)
            self.assertEqual(reports[0]["workers"], 2)
            self.assertEqual(reports[0]["results"], 2)
            self.assertTrue(mlo._validate_integrated_report(
                dest / "audiochecker.log", tracks, mlo.VERSION))

    def test_encoding_worker_parallel_copy_and_title_fidelity(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "Source"
            dest = Path(td) / "Output"
            source.mkdir()
            for n in range(1, 4):
                (source / f"{n:02d} Track {n}.flac").write_bytes(bytes([n]*20))
            app = mlo.Organizer()
            app.encoding_workers = 3
            with patch.object(mlo, "probe", return_value={"codec_name": "flac"}), \
                 patch.object(mlo, "_update_copied_track_tags"):
                app._export_release(source, dest, "FLAC", "unused", "unused")
            for n in range(1, 4):
                self.assertTrue((dest / f"{n:02d} Track {n}.flac").exists())


class CueAndDiscovery(unittest.TestCase):
    def test_recursive_release_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "Artist ALAC"
            release = base / "Albums" / "2020-05-01 - Example [US - CAT001]"
            disc = release / "CD 1"
            disc.mkdir(parents=True)
            (disc / "01 Example.flac").write_bytes(b"not decoded by discovery")
            self.assertEqual(mlo.discover(base), [release])

    def _cue(self, path, index2="00:01:00", index0=False):
        parts = ['PERFORMER "Example"', 'TITLE "Album"', 'FILE "album.wav" WAVE',
                 '  TRACK 01 AUDIO', '    TITLE "First"', '    INDEX 01 00:00:00',
                 '  TRACK 02 AUDIO', '    TITLE "Second"']
        if index0:
            parts.append('    INDEX 00 00:00:70')
        parts.append("    INDEX 01 " + index2)
        path.write_text("\n".join(parts)+"\n", encoding="utf-8")

    def test_image_cue_track_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            (path / "album.wav").write_bytes(b"placeholder")
            cue_path = path / "CAT001.cue"
            self._cue(cue_path)
            cue = mlo.cue_read(cue_path)
            images = mlo.cue_image_plan(cue_path, cue)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0][2], [0, 75])
            names = {1: ("01 First.flac", 0, 75), 2: ("02 Second.flac", 75, None)}
            rewritten = mlo.image_cue_text(cue, names)
            self.assertIn('FILE "01 First.flac" WAVE', rewritten)
            self.assertIn('FILE "02 Second.flac" WAVE', rewritten)
            self.assertEqual(rewritten.count("INDEX 01 00:00:00"), 2)

    def test_index_00_preserved_during_split(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            (path / "album.wav").write_bytes(b"placeholder")
            cue_path = path / "CAT001.cue"
            self._cue(cue_path, index0=True)
            cue = mlo.cue_read(cue_path)
            plan = mlo.cue_image_plan(cue_path, cue)
            self.assertEqual(plan[0][2], [0, 70])
            names = {1: ("01 First.flac", 0, 70), 2: ("02 Second.flac", 70, None)}
            rewritten = mlo.image_cue_text(cue, names)
            self.assertIn("INDEX 00 00:00:00", rewritten)
            self.assertIn("INDEX 01 00:00:05", rewritten)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_real_pcm_roundtrip_for_both_output_formats(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "album.wav"
            with wave.open(str(source), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b"\x00\x00\x00\x00" * 88200)
            f = shutil.which("ffmpeg")
            # On non-Windows, exercise official Xiph flac directly.
            # refalac64.exe requires Windows and must not be substituted.
            from unittest.mock import patch
            encoders = [("FLAC", ".flac", shutil.which("flac"))]
            if mlo.IS_WIN:
                encoders.append(("ALAC", ".m4a", None))
            for mode, ext, available in encoders:
                if not mlo.IS_WIN and not available:
                    self.skipTest("The official FLAC CLI is not installed.")
                dst = Path(td) / ("01 Segment" + ext)
                with patch.object(mlo, "ensure_encoder",
                                  return_value=available or mlo.ensure_encoder(mode)), \
                     patch.object(mlo, "DATA", Path(td)):
                    mlo.convert(f, source, dst, mode, 0, 44100, "Segment", 1,
                                ffprobe=shutil.which("ffprobe"))
                self.assertTrue(dst.is_file())
                self.assertEqual(mlo.pcm_hash(f, source, 0, 44100), mlo.pcm_hash(f, dst))


if __name__ == "__main__":
    unittest.main(verbosity=2)
