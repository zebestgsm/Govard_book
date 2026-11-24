import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from sync_reader import ReaderSync


class SyncTests(unittest.TestCase):
    def test_backup_pushes_ignored_files_deletions_and_preserves_other_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'; root.mkdir()
            remote = Path(tmp) / 'remote.git'
            def git(*args, cwd=root):
                return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
            git('init', '--bare', str(remote), cwd=Path(tmp))
            git('init', '-b', 'test')
            git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com')
            (root / '.gitignore').write_text('translation_edits.json\nscan_overrides/\n')
            (root / 'other').write_text('base')
            git('add', '.'); git('commit', '-m', 'base'); git('remote', 'add', 'origin', str(remote))
            (root / 'other').write_text('staged'); git('add', 'other')
            (root / 'translation_edits.json').write_text('translation')
            (root / 'scan_overrides').mkdir(); (root / 'scan_overrides/001.bin').write_bytes(b'scan')
            sync = ReaderSync(root, (threading.RLock(), threading.RLock()))
            sync.sync_once()
            self.assertEqual(sync.status(), 'saved')
            self.assertEqual(git('show', 'origin/test:translation_edits.json'), 'translation')
            self.assertEqual(git('show', 'origin/test:scan_overrides/001.bin'), 'scan')
            self.assertEqual(git('diff', '--cached', '--name-only').strip(), 'other')
            (root / 'scan_overrides/001.bin').unlink()
            sync.changed(); sync.sync_once()
            self.assertEqual(sync.status(), 'saved')
            self.assertNotIn('scan_overrides/001.bin', git('ls-tree', '-r', '--name-only', 'origin/test'))

    def test_push_failure_reports_error_and_keeps_local_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True)
            git('init', '-b', 'test'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.com')
            (root / 'base').write_text('base'); git('add', 'base'); git('commit', '-m', 'base')
            (root / 'text_edits.json').write_text('edits')
            sync = ReaderSync(root, (threading.RLock(), threading.RLock()))
            sync.sync_once()
            self.assertEqual(sync.status(), 'error')
            self.assertEqual(git('show', 'HEAD:text_edits.json').stdout, 'edits')
