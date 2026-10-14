# Duplicate Edition Analyzer v0.22.12 - core text/CUE logic tests.
# Run: py -3 tools\duplicate-edition-analyzer\test_dea_core.py
#
# The analyzer is a single .pyw that imports tkinter at module level. These tests
# load it with a stand-in tkinter so they run anywhere (CI, Linux, no display).
# They cover pure functions only; GUI, audio fingerprinting and Windows behavior
# still need real acceptance runs.
import sys
sys.dont_write_bytecode = True  # loading the .pyw must not leave __pycache__ in tools/
import importlib.machinery
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "Duplicate Edition Analyzer v0.22.12.pyw"


def _stand_in_module(name):
    module = types.ModuleType(name)

    class _Widget:
        def __init__(self, *args, **kwargs):
            pass

    def _attr(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return type(attr, (_Widget,), {})

    module.__getattr__ = _attr
    return module


def load_analyzer():
    try:
        import tkinter  # noqa: F401  (real tkinter is fine when installed)
    except ImportError:
        tk = _stand_in_module("tkinter")
        for sub in ("filedialog", "messagebox", "ttk"):
            child = _stand_in_module("tkinter." + sub)
            setattr(tk, sub, child)
            sys.modules["tkinter." + sub] = child
        sys.modules["tkinter"] = tk
    loader = importlib.machinery.SourceFileLoader("dea_under_test", str(SOURCE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses need the module registered
    loader.exec_module(module)
    return module


dea = load_analyzer()


class TrackTitleBasics(unittest.TestCase):
    def test_strip_track_number(self):
        for source, expected in [
            ("01 Red Lips", "Red Lips"),
            ("1. Song", "Song"),
            ("12 - Title", "Title"),
            ("Title", "Title"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(dea.strip_track_number(source), expected)

    def test_featured_credit_is_canonical(self):
        for source, expected in [
            ("Song (feat. A)", "Song (ft. A)"),
            ("Song [ft. A]", "Song (ft. A)"),
            ("Girlfriend (Dr. Luke Remix) [ft. Lil Mama]",
             "Girlfriend (Dr. Luke Remix) (ft. Lil Mama)"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(dea.canonical_featured_title(source), expected)

    def test_remaster_label_is_not_a_different_song(self):
        # Project rule: mastering labels are not separate songs for coverage.
        base = dea.identity_title("Song")
        for label in ("Song (2011 Remaster)", "Song (Remastered Version)"):
            with self.subTest(label=label):
                self.assertEqual(dea.identity_title(label), base)

    def test_remix_stays_a_different_song(self):
        self.assertNotEqual(dea.identity_title("Song (Remix)"), dea.identity_title("Song"))

    def test_censor_mask_matches_full_title(self):
        self.assertTrue(dea.masked_title_compatible("Sh**t", "Shit"))
        self.assertTrue(dea.masked_title_compatible("Shit", "Sh**t"))
        self.assertFalse(dea.masked_title_compatible("Song", "Other"))


class CallOutHookAliases(unittest.TestCase):
    """v0.22.12: three spellings are ONE 'Callout hook' choice, not three rows."""

    ALIASES = (
        "Suggested Callout Hook",
        "Call Out Hook",
        "Suggested Call Out Research Hook",
        "Callout Hook",
    )

    def test_aliases_share_one_canonical_pattern(self):
        keys = {dea.canonical_track_pattern(alias) for alias in self.ALIASES}
        self.assertEqual(len(keys), 1, keys)

    def test_extraction_reports_the_same_key_but_keeps_original_label(self):
        found = {}
        for alias in self.ALIASES[:3]:
            patterns = dea.extract_track_patterns("Song (" + alias + ")")
            self.assertEqual(len(patterns), 1, alias)
            key, label = patterns[0]
            found[alias] = (key, label)
            self.assertEqual(label, alias)
        self.assertEqual(len({key for key, _ in found.values()}), 1)

    def test_ordinary_version_is_not_a_pattern(self):
        self.assertEqual(dea.extract_track_patterns("Song (Radio Edit)"), [])


class SmallHelpers(unittest.TestCase):
    def test_version_key_orders_releases(self):
        self.assertEqual(dea._version_key("0.22.12"), (0, 22, 12))
        self.assertEqual(dea._version_key("v0.22.9"), (0, 22, 9))
        self.assertGreater(dea._version_key("0.22.12"), dea._version_key("0.22.9"))
        self.assertGreater(dea._version_key("0.22.9"), dea._version_key("0.4.1"))

    def test_cue_time_uses_75_frames_per_second(self):
        self.assertEqual(dea._cue_time_seconds("00:00:00"), 0.0)
        self.assertEqual(dea._cue_time_seconds("74:00:00"), 4440.0)
        self.assertAlmostEqual(dea._cue_time_seconds("03:25:37"), 205 + 37 / 75)

    def test_duration_display(self):
        self.assertEqual(dea._duration_display(0.0), "?:??")
        self.assertEqual(dea._duration_display(59.4), "0:59")
        self.assertEqual(dea._duration_display(61.0), "1:01")
        self.assertEqual(dea._duration_display(3725.0), "62:05")

    def test_parse_track_number(self):
        for value, fallback, expected in [("3", 1, 3), ("03/12", 1, 3), ("", 7, 7)]:
            with self.subTest(value=value):
                self.assertEqual(dea.parse_track_number(value, fallback), expected)

    def test_release_title_from_folder_drops_date_and_id(self):
        self.assertEqual(
            dea.release_title_from_folder("2014-07-15 - Good Time [075679936356]"), "Good Time")

    def test_album_family_ignores_edition_labels(self):
        base = dea.album_family("Album")
        for title in ("Album (Remastered)", "Album"):
            self.assertEqual(dea.album_family(title), base)
        self.assertEqual(dea.album_family("Party Never Ends (Deluxe Edition)"),
                         dea.album_family("Party Never Ends"))

    def test_release_type_inference(self):
        self.assertEqual(dea.infer_release_type("Song - Single", "2014 - Song - Single", 1, {})[0],
                         "single")
        self.assertEqual(dea.infer_release_type("Song (EP)", "EP", 5, {})[0], "ep")
        self.assertEqual(dea.infer_release_type("Album", "2014 - Album", 12, {})[0], "album")


class CueParsing(unittest.TestCase):
    def test_single_image_cue_splits_into_timed_tracks(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            audio = folder / "album.flac"
            audio.write_bytes(b"not real audio")
            cue = folder / "album.cue"
            cue.write_text(
                'PERFORMER "INNA"\nTITLE "Album"\nFILE "album.flac" WAVE\n'
                '  TRACK 01 AUDIO\n    TITLE "First"\n    INDEX 01 00:00:00\n'
                '  TRACK 02 AUDIO\n    TITLE "Second"\n    INDEX 01 03:22:37\n',
                encoding="utf-8")
            tracks = dea._parse_cue_tracks(cue, [audio])
            self.assertEqual([t["title"] for t in tracks], ["First", "Second"])
            self.assertEqual([t["track_number"] for t in tracks], [1, 2])
            self.assertEqual(tracks[0]["start"], 0.0)
            self.assertAlmostEqual(tracks[0]["end"], 202 + 37 / 75)
            self.assertAlmostEqual(tracks[1]["start"], 202 + 37 / 75)
            self.assertEqual(tracks[1]["end"], 0.0)  # 0.0 = until end of the file
            self.assertTrue(all(t["path"] == audio for t in tracks))
            self.assertTrue(all(t["album_performer"] == "INNA" for t in tracks))

    def test_missing_audio_file_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            audio = folder / "album.flac"
            audio.write_bytes(b"x")
            cue = folder / "album.cue"
            cue.write_text('FILE "album.flac" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n',
                           encoding="utf-8")
            self.assertEqual(dea._resolve_cue_audio_path(cue, "album.flac", [audio]), audio)
            self.assertIsNone(dea._resolve_cue_audio_path(cue, "missing.wav", [audio]))


class KnownGaps(unittest.TestCase):
    """Defects found by the 2026-10-14 audit. Marked expectedFailure so CI stays
    green; remove the decorator when the behavior is fixed (a surprise pass will
    then fail the run and remind you)."""

    @unittest.expectedFailure
    def test_accented_title_matches_unaccented_spelling(self):
        # identity_title drops accented letters instead of folding them, so the
        # same song spelled "Déjà Vu" and "Deja Vu" gets different identities.
        self.assertEqual(dea.identity_title("Déjà Vu"), dea.identity_title("Deja Vu"))

    @unittest.expectedFailure
    def test_non_latin_titles_have_an_identity(self):
        # Cyrillic/CJK titles normalize to "" and so never match by title.
        self.assertNotEqual(dea.identity_title("Дискотека"), "")

    @unittest.expectedFailure
    def test_trailing_remaster_year_is_ignored(self):
        # "(2011 Remaster)" is ignored but "- Remastered 2009" leaves "2009".
        self.assertEqual(dea.identity_title("Song - Remastered 2009"), dea.identity_title("Song"))


if __name__ == "__main__":
    unittest.main()
