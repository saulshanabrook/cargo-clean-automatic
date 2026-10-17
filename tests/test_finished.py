import contextlib
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('finish', ROOT / 'bin/finish_experiment.py')
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class FinishedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.0.0"\n')
        self.source = self.project / 'src/lib.rs'
        self.source.parent.mkdir()
        self.source.write_text('// dirty work, keep it\n')
        self.target = self.project / 'target'
        (self.target / 'debug/deps').mkdir(parents=True)
        (self.target / '.rustc_info.json').write_text('{}')
        (self.target / 'debug/.cargo-lock').touch()
        (self.target / 'debug/deps/output').write_bytes(b'generated')
        self.argv = ['--project', str(self.project), '--finished']

    def run_main(self, *extra, activity=True):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if activity:
                with patch.object(finish, 'inspect_activity'):
                    return finish.main(self.argv + list(extra))
            return finish.main(self.argv + list(extra))

    def cache(self, name='experiment'):
        root = self.base / name
        cache = root / 'testcase/.mypy_cache'
        (cache / '3.14/pkg').mkdir(parents=True)
        (cache / 'CACHEDIR.TAG').write_text('Signature: 8a477f597d28d172789f06886806bc55\n')
        (cache / '3.14/pkg/mod.data.json').write_text('{}')
        (cache / '3.14/pkg/mod.meta.json').write_text('{}')
        (root / 'testcase/source.pyi').write_text('class Keep: ...')
        (root / 'evidence.json').write_text('{"keep": true}')
        (root / '.venv').mkdir()
        (root / '.venv/runtime').write_text('prepared runtime')
        return root, cache

    def test_preview_then_clean_only_direct_target(self):
        sibling = self.project / 'nested/target'
        sibling.mkdir(parents=True)
        (sibling / 'evidence').write_text('keep')
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.run_main('--clean'), 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_text(), '// dirty work, keep it\n')
        self.assertTrue((sibling / 'evidence').exists())
        self.assertEqual(self.run_main('--clean'), 0)  # repeatable when absent

    def test_explicit_finish_required_and_conflicting_modes_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                finish.main(['--project', str(self.project), '--clean'])
            with self.assertRaises(SystemExit):
                finish.main(self.argv + ['--clean', '--dry-run'])
        self.assertTrue(self.target.exists())

    def test_custom_profile_or_evidence_protects_target(self):
        (self.target / 'benchmark-evidence').mkdir()
        self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue(self.target.exists())

    def test_nonstandard_profile_file_protects_target(self):
        (self.target / 'debug/notes.txt').write_text('keep')
        self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue(self.target.exists())

    def test_manifest_symlink_protects_target(self):
        manifest = self.project / 'Cargo.toml'
        manifest.rename(self.base / 'manifest')
        manifest.symlink_to(self.base / 'manifest')
        self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue(self.target.exists())

    def test_target_and_ancestor_symlinks_protect_external_data(self):
        real = self.base / 'outside'
        self.target.rename(real)
        self.target.symlink_to(real, target_is_directory=True)
        self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue((real / 'debug/deps/output').exists())
        alias = self.base / 'alias'
        alias.symlink_to(self.project, target_is_directory=True)
        self.argv = ['--project', str(alias), '--finished']
        self.assertEqual(self.run_main('--clean'), 1)

    def test_link_inside_target_preserves_source(self):
        (self.target / 'debug/deps/source').symlink_to(self.source)
        self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.target.exists())

    def test_cargo_lock_held_during_cleanup(self):
        original = finish.shutil.rmtree
        def remove(*args, **kwargs):
            with (self.target / 'debug/.cargo-lock').open('rb') as handle:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(*args, **kwargs)
        with patch.object(finish.shutil, 'rmtree', side_effect=remove) as remove_mock:
            remove_mock.avoids_symlink_attacks = True
            self.assertEqual(self.run_main('--clean'), 0)

    def test_busy_lock_protects_target(self):
        with (self.target / 'debug/.cargo-lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_main('--clean'), 1)
        self.assertTrue(self.target.exists())

    def test_activity_and_inspection_failure_protect(self):
        for error in [ValueError('active'), OSError('permission denied'), subprocess.TimeoutExpired('lsof', 20)]:
            with patch.object(finish, 'inspect_activity', side_effect=error):
                self.assertEqual(self.run_main('--clean', activity=False), 1)
            self.assertTrue(self.target.exists())

    def test_changed_output_protected(self):
        def change(*args):
            (self.target / 'debug/deps/new').write_text('new')
        with patch.object(finish, 'inspect_activity', side_effect=change):
            self.assertEqual(self.run_main('--clean', activity=False), 1)
        self.assertTrue(self.target.exists())

    def test_generated_cache_only_source_and_runtime_preserved(self):
        root, cache = self.cache()
        (cache / '3.14/cache.0.db').write_bytes(b'SQLite format 3\0' + b'fixture')
        self.assertEqual(self.run_main('--test-cache-root', str(root)), 0)
        self.assertTrue(cache.exists())
        self.assertEqual(self.run_main('--clean', '--test-cache-root', str(root)), 0)
        self.assertFalse(cache.exists())
        self.assertTrue((root / 'testcase/source.pyi').exists())
        self.assertTrue((root / 'evidence.json').exists())
        self.assertTrue((root / '.venv/runtime').exists())

    def test_unrecognized_cache_source_and_invalid_marker_protect_cache(self):
        root, cache = self.cache()
        source = cache / '3.14/keep.py'
        source.write_text('keep')
        self.assertEqual(self.run_main('--clean', '--test-cache-root', str(root)), 1)
        self.assertTrue(source.exists())
        source.unlink()
        (cache / 'CACHEDIR.TAG').write_text('not a cache')
        self.assertEqual(self.run_main('--clean', '--test-cache-root', str(root)), 1)
        self.assertTrue(cache.exists())

    def test_cache_link_and_unrelated_tree_preserved(self):
        root, cache = self.cache()
        other, other_cache = self.cache('unrelated')
        (root / 'other').symlink_to(other, target_is_directory=True)
        (cache / '3.14/pkg/alias.data.json').symlink_to(self.source)
        self.assertEqual(self.run_main('--clean', '--test-cache-root', str(root)), 1)
        self.assertTrue(self.source.exists())
        self.assertTrue(other_cache.exists())
        self.assertTrue(cache.exists())

    def test_scope_rejects_broad_root(self):
        for root in ['/', str(Path.home()), '/private/tmp']:
            self.assertEqual(self.run_main('--clean', '--test-cache-root', root), 1)
        self.assertTrue(self.target.exists())

    def test_shell_dispatch_invalid_project_never_legacy_scan(self):
        result = subprocess.run([str(ROOT / 'bin/cargo-clean-automatic'), '--project', str(self.base / 'missing'), '--finished', '--clean'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Cleanup refused', result.stderr)
        self.assertTrue(self.target.exists())

    def test_process_scoping_allows_unrelated_cargo_and_rejects_own(self):
        def results(cwd):
            return [subprocess.CompletedProcess([], 1, '', ''),
                    subprocess.CompletedProcess([], 0, f'98765 {os.getuid()} /bin/cargo\n', ''),
                    subprocess.CompletedProcess([], 0, f'p98765\nn{cwd}\n', ''),
                    subprocess.CompletedProcess([], 0, 'cargo check', '')]
        with patch.object(finish.subprocess, 'run', side_effect=results(self.base / 'unrelated')):
            finish.inspect_activity(self.project, self.target)
        with patch.object(finish.subprocess, 'run', side_effect=results(self.project)):
            with self.assertRaises(ValueError):
                finish.inspect_activity(self.project, self.target)
        values = results(self.base / 'unrelated')
        values[-1].stdout = f'cargo check --manifest-path {self.project}/Cargo.toml'
        with patch.object(finish.subprocess, 'run', side_effect=values):
            with self.assertRaises(ValueError):
                finish.inspect_activity(self.project, self.target)

    def test_real_project_python_process_protects_without_open_output(self):
        process = subprocess.Popen(['python3', '-c', 'import time; print("ready", flush=True); time.sleep(30)'], cwd=self.project, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(self.run_main('--clean', activity=False), 1)
            self.assertTrue(self.target.exists())
        finally:
            process.terminate()
            process.wait()
            process.stdout.close()

    def test_real_idle_cleanup_ignores_own_lock_and_unrelated_process(self):
        process = subprocess.Popen(['python3', '-c', 'import time; print("ready", flush=True); time.sleep(30)'], cwd=self.base, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(self.run_main('--clean', activity=False), 0)
            self.assertFalse(self.target.exists())
            self.assertTrue(self.source.exists())
        finally:
            process.terminate()
            process.wait()
            process.stdout.close()

    def test_cache_fifo_does_not_block_or_get_removed(self):
        root, cache = self.cache()
        os.mkfifo(cache / '3.14/cache.db')
        self.assertEqual(self.run_main('--clean', '--test-cache-root', str(root)), 1)
        self.assertTrue(cache.exists())

    def test_only_exact_installed_kache_text_alias_is_allowed(self):
        installed = self.base / 'kache'
        installed.write_bytes(b'\xcf\xfa\xed\xfe' + b'fixture')
        info = installed.stat()
        alias = f'p98765\nftxt\ntREG\nD{hex(info.st_dev)}\ni{info.st_ino}\n'
        results = [subprocess.CompletedProcess([], 0, alias, ''), subprocess.CompletedProcess([], 0, '', '')]
        with patch.object(finish.shutil, 'which', return_value=str(installed)), patch.object(finish.subprocess, 'run', side_effect=results):
            finish.inspect_activity(self.project, self.target)
        for changed in [alias.replace('ftxt', 'f3r'), alias.replace(f'i{info.st_ino}', f'i{info.st_ino+1}'), alias + 'f4r\ntREG\nD0x1\ni2\n']:
            with patch.object(finish.shutil, 'which', return_value=str(installed)), patch.object(finish.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, changed, '')):
                with self.assertRaises(ValueError):
                    finish.inspect_activity(self.project, self.target)

    def test_kache_alias_does_not_exempt_scoped_active_wrapper(self):
        installed = self.base / 'kache'
        installed.write_bytes(b'\xcf\xfa\xed\xfe' + b'fixture')
        info = installed.stat()
        alias = f'p98765\nftxt\ntREG\nD{hex(info.st_dev)}\ni{info.st_ino}\n'
        for cwd, active in [(self.base, False), (self.project, True)]:
            results = [subprocess.CompletedProcess([], 0, alias, ''),
                       subprocess.CompletedProcess([], 0, f'98765 {os.getuid()} /bin/kache\n', ''),
                       subprocess.CompletedProcess([], 0, f'p98765\nn{cwd}\n', ''),
                       subprocess.CompletedProcess([], 0, 'kache', '')]
            with patch.object(finish.shutil, 'which', return_value=str(installed)), patch.object(finish.subprocess, 'run', side_effect=results):
                if active:
                    with self.assertRaises(ValueError):
                        finish.inspect_activity(self.project, self.target)
                else:
                    finish.inspect_activity(self.project, self.target)

    def test_real_lsof_detects_open_artifact(self):
        process = subprocess.Popen(['python3', '-c', 'import sys,time; f=open(sys.argv[1]); print("ready", flush=True); time.sleep(30)', str(self.target / 'debug/deps/output')], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(self.run_main('--clean', activity=False), 1)
            self.assertTrue(self.target.exists())
        finally:
            process.terminate()
            process.wait()
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
