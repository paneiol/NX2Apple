"""The Mods page's per-game folders: a mod folder picked on this PC, or a root of title folders."""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'frontend-qt'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from nspb_qt import mods   # noqa: E402

TID = '010015100B514000'


class Folders(unittest.TestCase):
    def test_title_root_and_game_folder(self):
        with tempfile.TemporaryDirectory() as d:
            patch = Path(d) / TID / 'a mod' / 'exefs' / 'main.ips'
            patch.parent.mkdir(parents=True)
            patch.write_bytes(b'patch')
            self.assertEqual(mods.game_mods_dir(d, TID), Path(d) / TID)
            self.assertEqual(mods.game_mods_dir(Path(d) / TID, TID), Path(d) / TID)
            self.assertTrue(mods.has_mod_files(d, TID))
            self.assertTrue(mods.has_mod_files(Path(d) / TID, TID))

    def test_a_mod_folder_picked_directly_needs_no_title_subfolder(self):
        # What a downloaded mod looks like: exefs/ with the patches of each game version.
        with tempfile.TemporaryDirectory() as d:
            picked = Path(d) / 'Zelda Hood Down'
            exefs = picked / 'exefs'
            exefs.mkdir(parents=True)
            for version in ('1.0.1', '1.0.2', '1.1.0'):
                (exefs / f'{version}.pchtxt').write_text('@nsobid-ABCDEF0123456789\n', encoding='utf-8')
            self.assertEqual(mods.game_mods_dir(picked, TID), picked)
            self.assertTrue(mods.has_mod_files(picked, TID))

    def test_an_atmosphere_contents_level_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            patch = Path(d) / 'contents' / TID / 'exefs' / 'main.ips'
            patch.parent.mkdir(parents=True)
            patch.write_bytes(b'patch')
            self.assertEqual(mods.game_mods_dir(d, TID), Path(d) / 'contents' / TID)
            self.assertTrue(mods.has_mod_files(d, TID))

    def test_an_empty_or_missing_folder_has_no_mods(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(mods.has_mod_files(d, TID))
            self.assertFalse(mods.has_mod_files(Path(d) / 'not there', TID))
            hidden = Path(d) / '.hidden'
            hidden.write_bytes(b'x')
            self.assertFalse(mods.has_mod_files(d, TID))

    def test_title_id_is_required(self):
        for bad in ('', 'not a game', '1234'):
            with self.assertRaises(ValueError):
                mods.game_mods_dir(bad, bad)

    def test_output_kind_comes_from_the_selected_game(self):
        with tempfile.TemporaryDirectory() as d:
            for app, expected in (('DmIOS', 'oneGame'), ('DmShell', 'shared')):
                path = Path(d) / f'{app}.ipa'
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr(f'Payload/{app}.app/Info.plist', b'plist')
                    z.writestr(f'Payload/{app}.app/{app}', b'exe')
                game = SimpleNamespace(ipa_path=str(path), ios_app_path='')
                self.assertEqual(mods.kind_for_game(game, 'shared'), expected)
            framework = SimpleNamespace(ipa_path='', ios_app_path=str(Path(d) / 'DmGameX.framework'))
            self.assertEqual(mods.kind_for_game(framework, 'oneGame'), 'framework')
            unknown = SimpleNamespace(ipa_path='', ios_app_path='')
            self.assertEqual(mods.kind_for_game(unknown, 'oneGame'), 'oneGame')


try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 is not installed')
class ModelAndPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from nspb_qt.model import Model
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = Model(queue_file=self.root / 'queue.json')
        self.m.repo_path = ''

    def tearDown(self):
        self.m.deleteLater()
        self.app.processEvents()

    def test_mods_tab_compiles_with_fast_refresh_not_normal_build(self):
        from nspb_qt.library import LibraryGame
        from nspb_qt.ui import MainWindow
        game = LibraryGame(title_id=TID, name='Wonder', output_dir=str(self.root))
        self.m.library.upsert(game)
        self.m.library.upsert(LibraryGame(title_id='not-a-title-id', name='Fallback key'))
        # The picked folder is the mod itself, as a download comes: no <title ID> level.
        picked = self.root / 'Hood Down'
        exefs = picked / 'exefs'
        exefs.mkdir(parents=True)
        (exefs / '1.1.0.pchtxt').write_bytes(b'patch')
        self.m.mods_path = str(picked)
        w = MainWindow(self.m, perform_startup_checks=False)
        self.addCleanup(w.deleteLater)
        w.show()
        self.app.processEvents()
        w._switch_page('mods')
        self.assertIn('mods', w.nav_btns)
        self.assertEqual(w.mods_game_combo.findData('not-a-title-id'), -1)
        self.assertTrue(w.mods_page.isVisible())
        self.assertFalse(w.builds_page.isVisible())
        with mock.patch.object(self.m, 'fast_refresh_status', return_value={'can_refresh': True}), \
                mock.patch.object(self.m, 'start_fast_refresh') as fast, \
                mock.patch.object(self.m, 'start_build') as normal:
            w._refresh_mods_page()
            self.assertTrue(w.mods_compile_btn.isEnabled())
            w._mods_compile_clicked()
        fast.assert_called_once_with(
            TID, out_dir=str(self.root), game_name='Wonder', target='ios', export_shell_at_end=True)
        normal.assert_not_called()
        w.close()


if __name__ == '__main__':
    unittest.main()
