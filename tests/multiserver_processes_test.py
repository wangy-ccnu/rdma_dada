"""B2 development tests; real /proc/process cases require Linux (no SSH)."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from scripts.multiserver import processes as p
except ImportError:
    p = None

OWNER = dict(run_id='run/../../雪', bundle_id='bundle', server_id='node', gpu_id=0,
             observation_sha256='a' * 64, generation='generation-1')
PROC = dict(pid=23456, start_ticks=456, boot_id='01234567-89ab-cdef-0123-456789abcdef',
            uid=os.getuid(), pgid=23456, sid=23456)


class Child:
    """No OS process: only the Popen boundary is replaced in portable cases."""
    pid = PROC['pid']
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('fixture', timeout)
        return self.returncode


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(p, 'B2 ProcessSupervisor is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'host'
        self.s = p.ProcessSupervisor(self.root)
        self.addCleanup(self.s.close)
        self.child = Child()
        self.addCleanup(lambda: setattr(self.child, 'returncode', 0))

    def launch(self, **changes):
        args = dict(owner=OWNER, role='worker', argv=['/usr/bin/python3', '-B', 'x;$(no)'],
                    cwd=str(self.root), env={'VALUE': '雪;$(no)'})
        args.update(changes)
        with mock.patch.object(p.sys, 'platform', 'linux'), \
                mock.patch.object(p.subprocess, 'Popen', return_value=self.child), \
                mock.patch.object(p, '_read_process', return_value=dict(PROC)):
            return self.s.launch(**args)

    def test_launch_persists_identity_and_uses_only_generated_log_paths(self):
        result = self.launch()
        self.assertEqual(result['state'], 'RUNNING')
        self.assertEqual(result['identity'], dict(owner=OWNER, role='worker', **PROC))
        self.assertEqual(result['cleanup'], 'UNKNOWN')
        for path in result['logs'].values():
            self.assertTrue(Path(path).is_relative_to(self.root) if hasattr(Path, 'is_relative_to')
                            else str(path).startswith(str(self.root) + '/'))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_duplicate_does_not_spawn_and_changed_content_is_rejected(self):
        result = self.launch()
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('duplicate spawn')), \
                mock.patch.object(p, '_read_process', return_value=dict(PROC)):
            again = self.s.launch(OWNER, 'worker', ['/usr/bin/python3', '-B', 'x;$(no)'],
                                  str(self.root), {'VALUE': '雪;$(no)'})
            self.assertEqual(again['identity'], result['identity'])
            with self.assertRaises(p.ProcessConflictError):
                self.s.launch(OWNER, 'worker', ['/bin/false'], str(self.root), {})

    def test_intent_is_durable_before_spawn_and_crash_never_replays(self):
        def crash(*args, **kwargs):
            records = list(self.root.glob('*/launch.json'))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text())['state'], 'INTENT')
            raise KeyboardInterrupt('crash before spawn')
        with mock.patch.object(p.sys, 'platform', 'linux'), \
                mock.patch.object(p.subprocess, 'Popen', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.s.launch(OWNER, 'worker', ['/bin/true'], str(self.root), {})
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('replay')):
            result = self.s.launch(OWNER, 'worker', ['/bin/true'], str(self.root), {})
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertIsNone(result['identity']['pid'])
        self.assertEqual(self.s.inspect(result['identity'])['state'], 'UNKNOWN')

    def test_spawn_before_identity_crash_retains_handle_but_no_signal_authority(self):
        with mock.patch.object(p.sys, 'platform', 'linux'), \
                mock.patch.object(p.subprocess, 'Popen', return_value=self.child), \
                mock.patch.object(p, '_read_process', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.s.launch(OWNER, 'worker', ['/bin/true'], str(self.root), {})
        with mock.patch.object(p.os, 'kill', side_effect=AssertionError('unsafe signal')):
            result = self.s.launch(OWNER, 'worker', ['/bin/true'], str(self.root), {})
            self.assertEqual(self.s.signal(result['identity'], signal.SIGTERM)['state'], 'UNKNOWN')

    def test_unknown_launch_can_reap_held_child_without_inventing_identity(self):
        with mock.patch.object(p.sys, 'platform', 'linux'), \
                mock.patch.object(p.subprocess, 'Popen', return_value=self.child), \
                mock.patch.object(p, '_read_process', side_effect=PermissionError):
            result = self.s.launch(OWNER, 'worker', ['/bin/true'], str(self.root), {})
        self.assertEqual(self.s.wait(result['identity'], 0)['state'], 'UNKNOWN')
        self.child.returncode = 0
        final = self.s.wait(result['identity'], 0)
        self.assertEqual((final['state'], final['returncode'], final['cleanup']),
                         ('UNKNOWN', 0, 'UNKNOWN'))
        self.assertIsNone(final['identity']['pid'])
        self.s.close()
        other = p.ProcessSupervisor(self.root)
        self.addCleanup(other.close)
        self.assertEqual(other.inspect(final['identity'])['returncode'], 0)

    def test_pid_reuse_boot_uid_group_session_changes_never_signal(self):
        identity = self.launch()['identity']
        for field, value in [('start_ticks', 999), ('boot_id', 'f' * 8 + PROC['boot_id'][8:]),
                             ('uid', PROC['uid'] + 1), ('pgid', 555), ('sid', 666)]:
            with self.subTest(field=field), \
                    mock.patch.object(p, '_read_process', return_value=dict(PROC, **{field: value})), \
                    mock.patch.object(p.os, 'kill', side_effect=AssertionError('foreign signal')):
                self.assertEqual(self.s.inspect(identity)['state'], 'UNKNOWN')
                self.assertFalse(self.s.signal(identity, signal.SIGKILL)['signal_sent'])

    def test_permission_missing_and_exit_during_inspection_are_unknown(self):
        identity = self.launch()['identity']
        for error in [PermissionError(), FileNotFoundError(), p.ProcessIdentityError('changed')]:
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(p, '_read_process', side_effect=error), \
                    mock.patch.object(p.os, 'kill', side_effect=AssertionError('unsafe signal')):
                self.assertEqual(self.s.inspect(identity)['state'], 'UNKNOWN')
                self.assertFalse(self.s.signal(identity, signal.SIGTERM)['signal_sent'])

    def test_reopened_matching_nonchild_is_inspectable_but_never_signalled(self):
        identity = self.launch()['identity']
        other = p.ProcessSupervisor(self.root)
        self.addCleanup(other.close)
        with mock.patch.object(p, '_read_process', return_value=dict(PROC)), \
                mock.patch.object(p.os, 'kill', side_effect=AssertionError('nonchild signal')):
            self.assertEqual(other.inspect(identity)['state'], 'RUNNING')
            result = other.signal(identity, signal.SIGTERM)
            self.assertEqual(result['state'], 'UNKNOWN')
            self.assertFalse(result['signal_sent'])
            self.assertEqual(other.wait(identity, 0)['state'], 'UNKNOWN')

    def test_known_exit_code_is_persistent_but_not_completion_or_cleanup(self):
        identity = self.launch()['identity']
        self.child.returncode = 0
        result = self.s.wait(identity, 0)
        self.assertEqual((result['state'], result['returncode'], result['cleanup']),
                         ('EXITED', 0, 'UNKNOWN'))
        other = p.ProcessSupervisor(self.root)
        self.addCleanup(other.close)
        self.assertEqual(other.inspect(identity)['returncode'], 0)

    def test_forged_identity_and_invalid_signals_timeouts_fail_before_effect(self):
        identity = self.launch()['identity']
        with mock.patch.object(p.os, 'kill', side_effect=AssertionError('unsafe signal')):
            for forged in [dict(identity, pid=1), dict(identity, owner=dict(OWNER, generation='other')),
                           dict(identity, role='../worker')]:
                with self.subTest(forged=forged), self.assertRaises(p.ProcessError):
                    self.s.signal(forged, signal.SIGTERM)
            for value in [True, -1, 0, 99999, 1.5]:
                with self.assertRaises(p.ProcessValidationError):
                    self.s.signal(identity, value)
            for value in [True, -1, float('nan'), float('inf'), 3601, 10 ** 500]:
                with self.assertRaises(p.ProcessValidationError):
                    self.s.wait(identity, value)

    def test_invalid_launch_inputs_create_no_intent(self):
        cases = [dict(owner=dict(OWNER, gpu_id=True)), dict(owner=dict(OWNER, extra=1)),
                 dict(owner=dict(OWNER, generation='\ud800')), dict(argv='echo bad'),
                 dict(argv=['relative']), dict(argv=['/bin/true', '\x00']),
                 dict(cwd='../bad'), dict(env={'A=B': 'x'}), dict(env={'A': 1}),
                 dict(role=''), dict(role='\ud800')]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(p.ProcessValidationError):
                self.launch(**changes)
        self.assertEqual(list(self.root.glob('*/launch.json')), [])

    def test_spawn_error_is_persistent_and_not_retried(self):
        with mock.patch.object(p.sys, 'platform', 'linux'), \
                mock.patch.object(p.subprocess, 'Popen', side_effect=OSError('bad executable')):
            result = self.s.launch(OWNER, 'worker', ['/bin/missing'], str(self.root), {})
        self.assertEqual((result['state'], result['cleanup']), ('NOT_STARTED', 'CONFIRMED'))
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('retry')):
            self.assertEqual(self.s.launch(OWNER, 'worker', ['/bin/missing'], str(self.root), {})['state'],
                             'NOT_STARTED')

    def test_identity_save_failure_retains_durable_unknown_without_replay(self):
        save = self.s._save
        calls = []
        def fail_after_intent(fd, record):
            calls.append(record['state'])
            if record['state'] != 'INTENT':
                raise OSError('disk failure')
            return save(fd, record)
        with mock.patch.object(self.s, '_save', side_effect=fail_after_intent):
            with self.assertRaises(p.ProcessStorageError):
                self.launch()
        self.assertEqual(calls, ['INTENT', 'RUNNING'])
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('replay')):
            result = self.s.launch(OWNER, 'worker', ['/usr/bin/python3', '-B', 'x;$(no)'],
                                   str(self.root), {'VALUE': '雪;$(no)'})
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertIsNone(result['identity']['pid'])

    def test_storage_read_permission_error_never_signals(self):
        identity = self.launch()['identity']
        with mock.patch.object(self.s, '_load', side_effect=PermissionError('denied')), \
                mock.patch.object(p.os, 'kill', side_effect=AssertionError('signal')):
            with self.assertRaises(p.ProcessStorageError):
                self.s.signal(identity, signal.SIGTERM)

    def test_empty_preexisting_slot_is_not_permission_to_spawn(self):
        self.launch()
        record = next(self.root.glob('*/launch.json'))
        record.unlink()
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            with self.assertRaises(p.ProcessStorageError):
                self.s.launch(OWNER, 'worker', ['/usr/bin/python3', '-B', 'x;$(no)'],
                              str(self.root), {'VALUE': '雪;$(no)'})

    def test_corrupt_or_inconsistent_records_are_refused_without_replacement(self):
        self.launch()
        record = next(self.root.glob('*/launch.json'))
        original = record.read_bytes()
        for bad in [b'{}', b'{"state":1,"state":2}', b'not json',
                    original.replace(b'"worker"', b'"foreign"')]:
            with self.subTest(bad=bad):
                record.write_bytes(bad)
                with self.assertRaises(p.ProcessStorageError):
                    self.launch()
                self.assertEqual(record.read_bytes(), bad)
        record.write_bytes(original)

    def test_symlink_record_log_slot_and_root_are_refused(self):
        self.launch()
        record = next(self.root.glob('*/launch.json'))
        for target in [record, record.parent / 'stdout.log']:
            original = target.read_bytes()
            target.unlink()
            foreign = self.root.parent / 'foreign'
            foreign.write_bytes(original)
            target.symlink_to(foreign)
            with self.assertRaises(p.ProcessStorageError):
                self.launch()
            self.assertEqual(foreign.read_bytes(), original)
            target.unlink()
            target.write_bytes(original)
            target.chmod(0o600)
        link = self.root.parent / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(p.ProcessStorageError):
            p.ProcessSupervisor(link)
        slot = record.parent
        moved = self.root.parent / 'moved'
        slot.rename(moved)
        slot.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(p.ProcessStorageError):
            self.launch()

    def test_unsafe_modes_hardlinks_and_traversal_root_are_refused(self):
        self.root.chmod(0o777)
        with self.assertRaises(p.ProcessStorageError):
            p.ProcessSupervisor(self.root)
        self.root.chmod(0o700)
        with self.assertRaises(p.ProcessValidationError):
            p.ProcessSupervisor(str(self.root) + '/../other')
        self.launch()
        record = next(self.root.glob('*/launch.json'))
        os.link(str(record), str(self.root.parent / 'hardlink'))
        with self.assertRaises(p.ProcessStorageError):
            self.launch()

    def test_close_never_signals_and_refuses_to_discard_live_child_handle(self):
        self.launch()
        with mock.patch.object(p.os, 'kill', side_effect=AssertionError('close kill')):
            with self.assertRaises(p.ProcessBusyError):
                self.s.close()
        self.child.returncode = 0
        self.s.close()
        self.s.close()
        with self.assertRaises(p.ProcessClosedError):
            self.launch()

    def test_close_does_not_reap_an_exit_without_recording_it(self):
        self.launch()
        # An unpolled exit still has returncode=None; close must require wait,
        # rather than reap and lose the result when descriptors are closed.
        with mock.patch.object(self.child, 'poll', return_value=0):
            with self.assertRaises(p.ProcessBusyError):
                self.s.close()


class ProcParsingTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(p, 'B2 ProcessSupervisor is not implemented')

    def read(self, states=('S', 'S'), ticks=(456, 456), boots=None, uid=None,
             allow_exited=False):
        # Literal Linux stat layout: starttime is field22, suffix index19.
        samples = []
        boots = boots or (PROC['boot_id'], PROC['boot_id'])
        uid = uid or '{} {} {} {}'.format(*([os.getuid()] * 4))
        for index in range(2):
            fields = [states[index], '1', '23456', '23456'] + ['0'] * 15 + [str(ticks[index])]
            samples.extend([boots[index] + '\n', '23456 (odd ) process) ' + ' '.join(fields),
                            'Name:\tfixture\nUid:\t' + uid + '\n'])
        streams = [mock.mock_open(read_data=value)() for value in samples]
        with mock.patch('builtins.open', side_effect=streams):
            return p._read_process(23456, allow_exited=allow_exited)

    def test_parentheses_in_comm_parse_and_two_samples_match(self):
        self.assertEqual(self.read(), PROC)

    def test_exit_between_samples_never_reports_running(self):
        with self.assertRaises(p.ProcessIdentityError):
            self.read(states=('S', 'Z'))
        with self.assertRaises(p.ProcessIdentityError):
            self.read(states=('X', 'X'))

    def test_launch_capture_can_identify_unreaped_zombie_before_poll(self):
        self.assertEqual(self.read(states=('S', 'Z'), allow_exited=True), PROC)

    def test_pid_reuse_boot_and_credentials_changing_inside_sample_are_refused(self):
        for changes in [dict(ticks=(456, 457)),
                        dict(boots=(PROC['boot_id'], 'ffffffff' + PROC['boot_id'][8:])),
                        dict(uid='1 2 1 1'), dict(uid='broken')]:
            with self.subTest(changes=changes), self.assertRaises(p.ProcessIdentityError):
                self.read(**changes)

    def test_disappearance_and_malformed_stat_never_create_identity(self):
        with mock.patch('builtins.open', side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                p._read_process(23456)
        with mock.patch('builtins.open', mock.mock_open(read_data='broken')):
            with self.assertRaises(p.ProcessIdentityError):
                p._read_process(23456)


@unittest.skipUnless(sys.platform == 'linux', 'requires Linux /proc; macOS is not Linux acceptance')
class LinuxProcessTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(p, 'B2 ProcessSupervisor is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'host'
        self.s = p.ProcessSupervisor(self.root)
        self.children = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Exact unreaped children only, never killpg/name/foreign PID cleanup.
        for child in self.children:
            if child.poll() is None:
                try:
                    # Bounded descendant fixtures get time to reap their children
                    # even if the assertion before their cooperative stop failed.
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.wait(timeout=5)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        self.s.close()

    def launch(self, code, role='worker'):
        real = subprocess.Popen
        def spawn(*args, **kwargs):
            child = real(*args, **kwargs)
            self.children.append(child)
            return child
        with mock.patch.object(p.subprocess, 'Popen', side_effect=spawn):
            return self.s.launch(OWNER, role, [sys.executable, '-B', '-c', code],
                                 str(self.root), {'PATH': os.environ.get('PATH', '')})

    def test_real_stdin_eof_separate_session_logs_and_wait_reap(self):
        code = ('import os,sys; print("stdin="+repr(sys.stdin.read())); '
                'print("out;$(literal)"); print("err",file=sys.stderr); '
                'print("sid="+str(os.getsid(0)))')
        result = self.launch(code)
        final = self.s.wait(result['identity'], 5)
        self.assertEqual((final['state'], final['returncode']), ('EXITED', 0))
        self.assertEqual(result['identity']['sid'], result['identity']['pid'])
        out = Path(result['logs']['stdout']).read_text()
        self.assertIn("stdin=''", out)
        self.assertIn('out;$(literal)', out)
        self.assertIn('sid=' + str(result['identity']['pid']), out)
        self.assertEqual(Path(result['logs']['stderr']).read_text(), 'err\n')
        self.assertTrue(all(c.stdin is None and c.stdout is None and c.stderr is None
                            for c in self.children))

    def test_real_timeout_duplicate_and_individual_signal(self):
        result = self.launch('import time; time.sleep(20)')
        start = time.monotonic()
        self.assertEqual(self.s.wait(result['identity'], .02)['state'], 'RUNNING')
        self.assertLess(time.monotonic() - start, 2)
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('duplicate')):
            again = self.s.launch(OWNER, 'worker', [sys.executable, '-B', '-c',
                                  'import time; time.sleep(20)'], str(self.root),
                                  {'PATH': os.environ.get('PATH', '')})
        self.assertEqual(again['identity'], result['identity'])
        self.assertTrue(self.s.signal(result['identity'], signal.SIGTERM)['signal_sent'])
        self.assertEqual(self.s.wait(result['identity'], 5)['returncode'], -signal.SIGTERM)

    def test_real_spawn_before_identity_crash_leaves_unknown_and_reaps_fixture(self):
        code = 'import time; time.sleep(.2)'
        with mock.patch.object(p, '_read_process', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.launch(code)
        with mock.patch.object(p.subprocess, 'Popen', side_effect=AssertionError('replay')), \
                mock.patch.object(p.os, 'kill', side_effect=AssertionError('unknown signal')):
            result = self.s.launch(OWNER, 'worker', [sys.executable, '-B', '-c', code],
                                  str(self.root), {'PATH': os.environ.get('PATH', '')})
            self.assertEqual(result['state'], 'UNKNOWN')
            self.assertFalse(self.s.signal(result['identity'], signal.SIGTERM)['signal_sent'])
        final = self.s.wait(result['identity'], 5)
        self.assertEqual((final['state'], final['returncode'], final['cleanup']),
                         ('UNKNOWN', 0, 'UNKNOWN'))

    def test_real_reopened_supervisor_has_no_signal_authority(self):
        result = self.launch('import time; time.sleep(.2)')
        other = p.ProcessSupervisor(self.root)
        try:
            with mock.patch.object(p.os, 'kill', side_effect=AssertionError('nonchild signal')):
                self.assertEqual(other.inspect(result['identity'])['state'], 'RUNNING')
                report = other.signal(result['identity'], signal.SIGKILL)
                self.assertEqual(report['state'], 'UNKNOWN')
                self.assertFalse(report['signal_sent'])
        finally:
            other.close()
        self.assertEqual(self.s.wait(result['identity'], 5)['returncode'], 0)

    def test_real_fast_exit_is_captured_before_reap(self):
        # /bin/true may already be a zombie by the first /proc sample.
        real = subprocess.Popen
        def spawn(*args, **kwargs):
            child = real(*args, **kwargs)
            self.children.append(child)
            time.sleep(.02)  # Do not poll: retain kernel PID ownership.
            return child
        with mock.patch.object(p.subprocess, 'Popen', side_effect=spawn):
            result = self.s.launch(OWNER, 'quick', ['/bin/true'], str(self.root), {})
        self.assertEqual((result['state'], result['returncode']), ('EXITED', 0))

    def test_real_concurrent_supervisors_spawn_once(self):
        other = p.ProcessSupervisor(self.root)
        self.addCleanup(other.close)
        results, errors = [], []
        code = 'import time; time.sleep(.1)'
        def run(supervisor):
            try:
                results.append(supervisor.launch(OWNER, 'concurrent',
                    [sys.executable, '-B', '-c', code], str(self.root), {}))
            except BaseException as error:
                errors.append(error)
        # Capture both potential launchers so a regression also reaps both.
        real = subprocess.Popen
        def spawn(*a, **kw):
            child = real(*a, **kw)
            self.children.append(child)
            return child
        with mock.patch.object(p.subprocess, 'Popen', side_effect=spawn):
            threads = [threading.Thread(target=run, args=(s,)) for s in (self.s, other)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.children), 1)
        self.assertEqual(results[0]['identity'], results[1]['identity'])
        self.children[0].wait(timeout=5)

    def test_cooperative_parent_reaps_descendant_no_group_signal_or_cleanup_guess(self):
        # Parent owns/reaps its child; supervisor has authority over parent only.
        code = ('import subprocess,signal,sys,time; '
                'c=subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"]); '
                'signal.signal(signal.SIGTERM,lambda *a:(c.terminate(),c.wait(),sys.exit(0))); '
                'print(c.pid,flush=True); time.sleep(2); c.terminate(); c.wait()')
        result = self.launch(code)
        log = Path(result['logs']['stdout'])
        deadline = time.monotonic() + 5
        while not log.read_text().strip() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(log.read_text().strip(), 'parent did not become ready')
        descendant = int(log.read_text().strip())
        with mock.patch.object(p.os, 'killpg', side_effect=AssertionError('blind group signal')):
            self.assertTrue(self.s.signal(result['identity'], signal.SIGTERM)['signal_sent'])
        final = self.s.wait(result['identity'], 5)
        self.assertEqual(final['returncode'], 0)
        self.assertEqual(final['cleanup'], 'UNKNOWN')
        self.assertFalse(Path('/proc/' + str(descendant)).exists())

    def test_unknown_descendant_stays_unknown_until_fixture_parent_reaps(self):
        code = ('import subprocess,sys; '
                'c=subprocess.Popen([sys.executable,"-c","import time; time.sleep(.2)"]); '
                'print(c.pid,flush=True); c.wait()')
        result = self.launch(code)
        self.assertEqual(self.s.inspect(result['identity'])['cleanup'], 'UNKNOWN')
        final = self.s.wait(result['identity'], 5)
        self.assertEqual((final['returncode'], final['cleanup']), (0, 'UNKNOWN'))
        descendant = int(Path(result['logs']['stdout']).read_text().strip())
        self.assertFalse(Path('/proc/' + str(descendant)).exists())


if __name__ == '__main__':
    unittest.main()
