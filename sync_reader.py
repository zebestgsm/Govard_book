"""Background GitHub backup of reader data, without staging other project files."""
import os
from pathlib import Path
import subprocess
import threading

DATA_FILES = ('text_edits.json', 'translation_edits.json', 'scan_quality.json', 'corrections.json')


class ReaderSync:
    def __init__(self, root, locks):
        self.root = Path(root)
        self.locks = locks
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.generation = 0
        self.state = 'pending'
        self.branch = self.git('branch', '--show-current').stdout.strip()
        if not self.branch:
            raise RuntimeError('Автосохранение требует активной ветки Git.')

    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.root, text=True, capture_output=True,
                              check=check, timeout=60, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})

    def changed(self):
        with self.lock:
            self.generation += 1
            self.state = 'pending'
        self.event.set()

    def status(self):
        with self.lock:
            return self.state

    def sync_once(self):
        with self.lock:
            generation = self.generation
            self.state = 'syncing'
        try:
            # Take a consistent snapshot of both types of data while staging.
            with self.locks[0], self.locks[1]:
                if self.git('branch', '--show-current').stdout.strip() != self.branch:
                    raise RuntimeError('Ветка изменилась')
                tracked = self.git('ls-files', '-z', '--', *DATA_FILES, 'scan_overrides/').stdout.split('\0')
                paths = {p for p in tracked if p}
                paths.update(p for p in DATA_FILES if (self.root / p).is_file())
                folder = self.root / 'scan_overrides'
                if folder.is_dir():
                    paths.update(str(p.relative_to(self.root)) for p in folder.glob('[0-9][0-9][0-9].bin') if p.is_file())
                paths = sorted(paths)
                if paths:
                    # Never overwrite a user's already-staged versions of data files.
                    staged = self.git('diff', '--cached', '--name-only', '-z', '--', *paths).stdout
                    if staged:
                        raise RuntimeError('Файлы читалки уже подготовлены для другого коммита')
                    existing = [p for p in paths if (self.root / p).is_file()]
                    missing = [p for p in paths if not (self.root / p).exists()]
                    try:
                        if existing:
                            self.git('add', '-f', '--', *existing)
                        if missing:
                            self.git('add', '-u', '--', *missing)
                        diff = self.git('diff', '--cached', '--quiet', '--', *paths, check=False)
                        if diff.returncode == 1:
                            self.git('commit', '--only', '-m', 'Save reader changes and review decisions', '--', *paths)
                        elif diff.returncode != 0:
                            raise RuntimeError('Не удалось проверить изменения')
                    except Exception:
                        # Only our own staging is undone; the working files remain intact.
                        self.git('reset', '-q', 'HEAD', '--', *paths, check=False)
                        raise
            self.git('push', 'origin', f'HEAD:refs/heads/{self.branch}')
            with self.lock:
                self.state = 'saved' if generation == self.generation else 'pending'
        except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
            print(f'Reader GitHub backup failed: {exc}', flush=True)
            with self.lock:
                self.state = 'error'

    def run(self):
        while True:
            self.event.wait(10)
            self.event.clear()
            if self.status() != 'saved':
                self.sync_once()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name='reader-github-backup').start()
        self.event.set()
