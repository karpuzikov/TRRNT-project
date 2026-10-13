# Music Library Organizer v0.1.2 - source-level regression tests.
# Run on Windows: py -3 tools\music-library-organizer\test_organizer.py
# These tests do not replace Windows GUI/interaction/real-disc acceptance.
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import wave

SOURCE = Path(__file__).resolve().parents[1] / "Music Library Organizer v0.2.0.pyw"
loader = importlib.machinery.SourceFileLoader("mlo_under_test", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
mlo = importlib.util.module_from_spec(spec)
loader.exec_module(mlo)


class NamingRules(unittest.TestCase):
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
    def test_missing_audiochecker_does_not_block_and_second_run_skips(self):
        from unittest.mock import patch
        import json
        import shutil
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            input_root = base / "Input"
            output_root = base / "Output"
            first = input_root / "2020-01-01 - Single [ABC123]"
            first.mkdir(parents=True)
            (first / "01 Track.flac").write_bytes(b"test source")
            paths = {
                "DATA": base / "Settings",
                "SETTINGS": base / "Settings" / "settings.json",
                "LOG": base / "Settings" / "activity.log",
                "REPORT": base / "Settings" / "last-run.json",
                "UNDO": base / "Settings" / "undo.json",
                "HISTORY": base / "Settings" / "undo-history",
            }
            with patch.multiple(mlo, **paths):
                app = mlo.Organizer()
                app.source, app.dest, app.fmt = str(input_root), str(output_root), "FLAC"
                def export(release, staging, fmt, ff, ffprobe):
                    shutil.copy2(release / "01 Track.flac", staging / "01 Track.flac")
                app._export_release = export
                with patch.object(mlo, "ensure_ffmpeg", return_value=("ff", "ffprobe")):
                    app._organize(input_root, output_root, "FLAC")
                    self.assertEqual(app.counts["done"], 1)
                    self.assertEqual(app.counts["failed"], 0)
                    self.assertGreaterEqual(app.counts["warnings"], 1)
                    self.assertTrue((output_root / first.name / "01 Track.flac").exists())
                    self.assertEqual(app.results[0]["status"], "done")
                    app._organize(input_root, output_root, "FLAC")
                    self.assertEqual(app.counts["skipped"], 1)
                    self.assertEqual(app.results[-1]["status"], "skipped")

    def test_bad_release_does_not_stop_good_release(self):
        from unittest.mock import patch
        import shutil
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "Input"
            dest = base / "Output"
            folders = [source / "2020-01-01 - A [AAA]", source / "2020-01-01 - B [BBB]"]
            for folder in folders:
                folder.mkdir(parents=True)
                (folder / "01 Audio.flac").write_bytes(b"temporary")
            paths = {key: base / "Data" / name for key, name in {
                "DATA":"", "SETTINGS":"settings.json","UNDO":"undo.json",
                "LOG":"activity.log","REPORT":"last-run.json","HISTORY":"undo-history"}.items()}
            with patch.multiple(mlo, **paths):
                app = mlo.Organizer()
                app.source, app.dest, app.fmt = str(source), str(dest), "FLAC"
                def export(folder, output, fmt, ff, ffprobe):
                    if folder.name.endswith("[AAA]"):
                        raise ValueError("synthetic unsupported source")
                    shutil.copy2(folder / "01 Audio.flac", output / "01 Audio.flac")
                app._export_release = export
                with patch.object(mlo, "ensure_ffmpeg", return_value=("ff", "ffprobe")):
                    app._organize(source, dest, "FLAC")
                self.assertEqual(app.counts["failed"], 1)
                self.assertEqual(app.counts["done"], 1)
                self.assertTrue((dest / folders[1].name / "01 Audio.flac").is_file())

    def test_original_filename_version_survives_shorter_title_tag(self):
        from unittest.mock import patch
        with patch.object(mlo, "metadata", return_value=("1", "OK", "Artist")):
            number, title, _ = mlo.extract_number_title(Path("01 OK (Extended Version).flac"))
            self.assertEqual(number, 1)
            self.assertEqual(title, "OK (Extended Version)")


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
            names = {1: "01 First.flac", 2: "02 Second.flac"}
            rewritten = mlo.image_cue_text(cue, names)
            self.assertIn('FILE "01 First.flac" WAVE', rewritten)
            self.assertIn('FILE "02 Second.flac" WAVE', rewritten)
            self.assertEqual(rewritten.count("INDEX 01 00:00:00"), 2)

    def test_index_00_requires_review(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            (path / "album.wav").write_bytes(b"placeholder")
            cue_path = path / "CAT001.cue"
            self._cue(cue_path, index0=True)
            with self.assertRaises(ValueError):
                mlo.cue_image_plan(cue_path, mlo.cue_read(cue_path))

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
            for mode, ext in (("FLAC", ".flac"), ("ALAC", ".m4a")):
                dst = Path(td) / ("01 Segment" + ext)
                mlo.convert(f, source, dst, mode, 0, 44100, "Segment", 1)
                self.assertTrue(dst.is_file())
                self.assertEqual(mlo.pcm_hash(f, source, 0, 44100), mlo.pcm_hash(f, dst))


if __name__ == "__main__":
    unittest.main(verbosity=2)
