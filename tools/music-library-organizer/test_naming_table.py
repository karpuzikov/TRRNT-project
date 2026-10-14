# Music Library Organizer v0.3.5 - table-driven naming rules.
# Every row is a rule or example recorded by the owner in RULES.md,
# MUSIC_GLOSSARY.md or CONTINUITY.md. To add a rule, add a row; do not write a new test.
# Run: py -3 tools\music-library-organizer\test_naming_table.py
import sys
sys.dont_write_bytecode = True  # loading the .pyw must not leave __pycache__ in tools/
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "Music Library Organizer v0.03.05.pyw"
loader = importlib.machinery.SourceFileLoader("mlo_naming_table", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
mlo = importlib.util.module_from_spec(spec)
loader.exec_module(mlo)

# (source release title, release type or None, root artist, expected folder title)
RELEASE_TITLES = [
    # Single/EP designation is a final " - Single" / " - EP" suffix.
    ("Song (EP) (Extended Mix)", "EP", "", "Song (Extended Mix) - EP"),
    ("Plain Jane REMIX - Single", "Single", "", "Plain Jane (Remix) - Single"),
    ("Some Album", None, "", "Some Album"),
    # Plural remix collections never get an EP/Single suffix.
    ("Title - The Remixes EP", "EP", "", "Title (The Remixes)"),
    ("Title (The Remixes) - Single", "Single", "", "Title (The Remixes)"),
    ("Song (Remixes) (Single)", "Single", "", "Song (Remixes)"),
    # Featured artists live on tracks, never in release/folder titles.
    ("Cola Song (ft. J Balvin)", "Single", "", "Cola Song - Single"),
    ("Good Time (feat. Pitbull)", "Single", "", "Good Time - Single"),
    # Version labels are case-normalized.
    ("Déjà Vu (extended mix)", "Single", "", "Déjà Vu (Extended Mix) - Single"),
    ("Sun Is Up (remix)", "Single", "", "Sun Is Up (Remix) - Single"),
    # Owner decision 2026-10-14: edition notes, year and catalog are not kept.
    ("Song (Maxi CD Single) (Promo)", "Single", "", "Song - Single"),
    ("Inna - Déjá Vu (Maxi CD Single) (Promo)", "Single", "INNA", "Déjá Vu - Single"),
    ("Inna - Party Never Ends (Part 2) (2013) [WPCR-15062]", None, "INNA",
     "Party Never Ends (Part 2)"),
]

# (source track title, expected title after normalization)
TRACK_TITLES = [
    # () first, [] second, {} third, regardless of role.
    ("Red Lips (feat. Sam Bruno) (Skrillex Remix)",
     "Red Lips (ft. Sam Bruno) [Skrillex Remix]"),
    ("C'Mon (Catch 'Em By Surprise) (feat. Busta Rhymes) (Extended Club Mix)",
     "C'Mon (Catch 'Em By Surprise) [ft. Busta Rhymes] {Extended Club Mix}"),
    # Feature of the original recording comes before the version.
    ("Diggy Down (Radio Edit) [ft. Marian Hill]",
     "Diggy Down (ft. Marian Hill) [Radio Edit]"),
    # A feature added by the remix keeps remix-before-feature order.
    ("Reminder (Remix) [ft. A$AP Rocky & Young Thug]",
     "Reminder (Remix) [ft. A$AP Rocky & Young Thug]"),
    ("Plain Jane (Remix) [ft. Nicki Minaj]",
     "Plain Jane (Remix) [ft. Nicki Minaj]"),
]

# (track number, title, extension, expected file name)
TRACK_FILENAMES = [
    # Zero-padded number, exactly one space, full title, no artist prefix.
    (1, "Red Lips (ft. Sam Bruno) [Skrillex Remix]", ".m4a",
     "01 Red Lips (ft. Sam Bruno) [Skrillex Remix].m4a"),
    (12, "Title", ".flac", "12 Title.flac"),
]


class NamingTable(unittest.TestCase):
    def test_release_titles(self):
        for source, kind, root, expected in RELEASE_TITLES:
            with self.subTest(source=source, kind=kind):
                cleaned = mlo.clean_release_title(source, root)
                self.assertEqual(mlo.release_title_normalize(cleaned, kind), expected)

    def test_track_titles(self):
        for source, expected in TRACK_TITLES:
            with self.subTest(source=source):
                title, artist = mlo.normalize_artist_title(source, "INNA")
                self.assertEqual(title, expected)
                self.assertEqual(artist, "INNA")

    def test_track_filenames(self):
        for number, title, ext, expected in TRACK_FILENAMES:
            with self.subTest(expected=expected):
                self.assertEqual(mlo.track_filename(number, title, ext), expected)


if __name__ == "__main__":
    unittest.main()
