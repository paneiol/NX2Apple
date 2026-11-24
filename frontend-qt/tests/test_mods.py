"""The Mods page's per-game folders and iOS import, with the device command faked."""
import os
import subprocess
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
    def test_parent_or_game_folder_and_file_probe(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mods.game_mods_dir(d, TID), Path(d) / TID)
            self.assertEqual(mods.game_mods_dir(Path(d) / TID, TID), Path(d) / TID)
            self.assertFalse(mods.has_mod_files(d, TID))
            patch = Path(d) / TID / 'a mod' / 'exefs' / 'main.ips'
            patch.parent.mkdir(parents=True)
            patch.write_bytes(b'patch')
            self.assertTrue(mods.has_mod_files(d, TID))
            self.assertTrue(mods.has_mod_files(Path(d) / TID, TID))

    def test_title_id_is_required(self):
        for bad in ('', 'not a game', '1234'):
            with self.assertRaises(ValueError):
                mods.game_mods_dir(bad, bad)

    def test_bundle_ids_cover_one_game_and_the_custom_shell(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mods.bundle_id_for_game(TID, 'oneGame', d), f'org.nxnative.{TID.lower()}')
            self.assertEqual(mods.bundle_id_for_game(TID, 'shared', d), 'org.nxnative.shell')
            (Path(d) / 'shell.json').write_text('{"bundleID": "com.example.shell"}', encoding='utf-8')
            self.assertEqual(mods.bundle_id_for_game(TID, 'shared', d), 'com.example.shell')
            self.assertEqual(mods.bundle_id_for_game(TID, 'framework', d), 'com.example.shell')

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

    def test_device_support_uses_the_build_python(self):
        calls = []

        def runs(py, args):
            calls.append((py, args))
            return args[0] == '-m'

        self.assertTrue(mods.ensure_device_support('/venv/python', runs))
        self.assertEqual(calls[0][0], '/venv/python')
        self.assertEqual(calls[0][1][0], '-c')
        self.assertIn('11.63.1', calls[0][1][1])
        self.assertEqual(calls[1], ('/venv/python', ['-m', 'pip', 'install', mods.DEVICE_PACKAGE]))


class Import(unittest.TestCase):
    def test_pull_merges_and_preserves_unrelated_mods(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'mods'
            old = root / TID / 'old mod' / 'romfs' / 'keep.txt'
            old.parent.mkdir(parents=True)
            old.write_text('keep')
            replaced = root / TID / 'new mod' / 'romfs' / 'x.txt'
            replaced.parent.mkdir(parents=True)
            replaced.write_text('old')
            calls = []

            def fake(command, **kwargs):
                calls.append((command, kwargs))
                if command[3] == 'usbmux':
                    return subprocess.CompletedProcess(command, 0, stdout='["PHONE"]', stderr='')
                if command[3:5] == ['apps', 'list']:
                    installed = f'org.nxnative.{TID.lower()}.TEAMID'
                    return subprocess.CompletedProcess(command, 0, stdout='{"' + installed + '": {}}', stderr='')
                staging = Path(command[-1])
                new = staging / TID / 'new mod'
                (new / 'romfs').mkdir(parents=True)
                (new / 'romfs' / 'x.txt').write_text('new')
                (new / 'exefs').mkdir()
                (new / 'exefs' / 'main.ips').write_bytes(b'patch')
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

            ok, dest = mods.import_from_device(
                python='/venv/python', bundle_id=f'org.nxnative.{TID.lower()}', title=TID,
                mods_root=root, runner=fake)
            self.assertTrue(ok, dest)
            self.assertEqual(Path(dest), root / TID)
            self.assertEqual(old.read_text(), 'keep')
            self.assertEqual(replaced.read_text(), 'new')
            self.assertEqual((root / TID / 'new mod' / 'exefs' / 'main.ips').read_bytes(), b'patch')
            pull = next(c for c, _ in calls if c[3:5] == ['apps', 'pull'])
            self.assertEqual(pull[5], f'org.nxnative.{TID.lower()}.TEAMID')
            self.assertEqual(pull[6], f'/Documents/mods/{TID}')
            pull_kwargs = next(kw for c, kw in calls if c[3:5] == ['apps', 'pull'])
            self.assertEqual(pull_kwargs['env']['PYMOBILEDEVICE3_UDID'], 'PHONE')
            self.assertEqual(list(root.glob('.mods-import-*')), [])

    def test_failure_does_not_change_the_pc_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'mods'
            old = root / TID / 'mod' / 'romfs' / 'x.txt'
            old.parent.mkdir(parents=True)
            old.write_text('old')

            def fake(command, **kwargs):
                if command[3] == 'usbmux':
                    return subprocess.CompletedProcess(command, 0, stdout='["PHONE"]', stderr='')
                if command[3:5] == ['apps', 'list']:
                    return subprocess.CompletedProcess(command, 0, stdout='{}', stderr='')
                return subprocess.CompletedProcess(command, 1, stdout='device refused', stderr='')

            ok, detail = mods.import_from_device(
                python='/venv/python', bundle_id='org.nxnative.shell', title=TID, mods_root=root, runner=fake)
            self.assertFalse(ok)
            self.assertEqual(detail, 'device refused')
            self.assertEqual(old.read_text(), 'old')
            self.assertEqual(list(root.glob('.mods-import-*')), [])

    def test_local_merge_failure_leaves_the_old_tree_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'mods'
            old = root / TID / 'mod' / 'romfs' / 'x.txt'
            old.parent.mkdir(parents=True)
            old.write_text('old')

            def fake(command, **kwargs):
                if command[3] == 'usbmux':
                    return subprocess.CompletedProcess(command, 0, stdout='["PHONE"]', stderr='')
                if command[3:5] == ['apps', 'list']:
                    return subprocess.CompletedProcess(command, 0, stdout='{}', stderr='')
                new = Path(command[-1]) / TID / 'mod' / 'romfs'
                new.mkdir(parents=True)
                (new / 'x.txt').write_text('new')
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

            with mock.patch('nspb_qt.mods.shutil.copytree', side_effect=[None, OSError('disk full')]):
                ok, detail = mods.import_from_device(
                    python='/venv/python', bundle_id='org.nxnative.shell', title=TID,
                    mods_root=root, runner=fake)
            self.assertFalse(ok)
            self.assertIn('disk full', detail)
            self.assertEqual(old.read_text(), 'old')
            self.assertEqual(list(root.glob('.mods-import-*')), [])

    def test_shared_shell_uses_its_games_mods_folder(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def fake(command, **kwargs):
                calls.append(command)
                if command[3] == 'usbmux':
                    return subprocess.CompletedProcess(command, 0, stdout='["PHONE"]', stderr='')
                if command[3:5] == ['apps', 'list']:
                    return subprocess.CompletedProcess(command, 0, stdout='{}', stderr='')
                (Path(command[-1]) / 'mods').mkdir()
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

            ok, _ = mods.import_from_device(
                python='/venv/python', bundle_id='org.nxnative.shell', title=TID,
                mods_root=Path(d) / 'pc', shared=True, runner=fake)
            self.assertTrue(ok)
            pull = next(c for c in calls if c[3:5] == ['apps', 'pull'])
            self.assertEqual(pull[6], f'/Documents/games/{TID}/mods')


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

    def test_import_runs_in_background_with_the_selected_game_and_bundle(self):
        self.m.mods_path = str(self.root / 'mods')
        self.m.ipa_kind = 'oneGame'
        calls = []
        finished = []
        self.m.modsImportFinished.connect(lambda ok, note: finished.append((ok, note)))

        def inline_thread(*, target, daemon):
            return mock.MagicMock(start=target)

        with mock.patch('nspb_qt.mods.ensure_device_support', return_value=True), \
                mock.patch('nspb_qt.mods.import_from_device', return_value=(True, str(self.root / 'mods' / TID))) as pull, \
                mock.patch('threading.Thread', side_effect=inline_thread):
            self.m.start_mod_import(TID)
        self.assertEqual(calls, [])
        kwargs = pull.call_args.kwargs
        self.assertEqual(kwargs['title'], TID)
        self.assertEqual(kwargs['bundle_id'], f'org.nxnative.{TID.lower()}')
        self.assertEqual(kwargs['mods_root'], str(self.root / 'mods'))
        self.assertFalse(kwargs['shared'])
        self.assertFalse(self.m.mods_import_running)
        self.assertEqual(len(finished), 1)
        self.assertTrue(finished[0][0])

    def test_import_exception_clears_running_and_reports_failure(self):
        finished = []
        self.m.modsImportFinished.connect(lambda ok, note: finished.append((ok, note)))

        def inline_thread(*, target, daemon):
            return mock.MagicMock(start=target)

        with mock.patch('nspb_qt.mods.ensure_device_support', side_effect=RuntimeError('boom')), \
                mock.patch('threading.Thread', side_effect=inline_thread):
            self.m.start_mod_import(TID)
        self.assertFalse(self.m.mods_import_running)
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0][0])
        self.assertIn('boom', finished[0][1])

    def test_build_entries_reject_an_active_import(self):
        self.m.mods_import_running = True
        with mock.patch('nspb_qt.build_seed_cache.supports_build_seed', return_value=True) as supports:
            self.m.start_fast_refresh(TID)
        supports.assert_not_called()
        self.assertFalse(self.m.running)
        self.assertFalse(self.m.can_build)

    def test_mods_tab_compiles_with_fast_refresh_not_normal_build(self):
        from nspb_qt.library import LibraryGame
        from nspb_qt.ui import MainWindow
        game = LibraryGame(title_id=TID, name='Wonder', output_dir=str(self.root))
        self.m.library.upsert(game)
        self.m.library.upsert(LibraryGame(title_id='not-a-title-id', name='Fallback key'))
        self.m.mods_path = str(self.root / 'mods')
        patch = self.root / 'mods' / TID / 'mod' / 'exefs' / 'main.ips'
        patch.parent.mkdir(parents=True)
        patch.write_bytes(b'patch')
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
