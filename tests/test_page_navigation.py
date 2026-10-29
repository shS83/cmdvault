"""Exercise CmdVault's actual navigation bindings through a real fzf terminal."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('cmdvault_page_test', str(ROOT / 'cmdvault'))
spec = importlib.util.spec_from_loader(loader.name, loader)
vault = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = vault
loader.exec_module(vault)


@unittest.skipUnless(os.name == 'posix' and shutil.which('fzf'), 'requires POSIX and fzf')
class PageNavigationTests(unittest.TestCase):
    def navigation_bindings(self):
        with patch.object(vault.shutil, 'which', return_value='fzf'), \
                patch.object(vault.sys.stdin, 'isatty', return_value=False), \
                patch.object(vault.subprocess, 'run', return_value=subprocess.CompletedProcess([], 130, '', '')) as run:
            vault.pick([vault.Entry('row0')], '', False)
        return [arg for arg in run.call_args.args[0]
                if arg.startswith('--bind=') and 'preview-page-' in arg]

    def selection_after(self, keys):
        import fcntl
        import pty
        import termios
        bindings = self.navigation_bindings()
        pid, fd = pty.fork()
        if pid == 0:
            result = subprocess.run(
                [shutil.which('fzf'), '--sync', '--layout=reverse', '--no-sort',
                 '--height=100%', '--prompt=page-test> ', '--bind=start:first',
                 '--preview=echo preview', '--preview-window=hidden', *bindings],
                input=''.join(f'row{i}\n' for i in range(100)),
                capture_output=True, text=True,
                env={k: v for k, v in dict(os.environ, TERM='xterm-256color').items()
                     if k not in ('FZF_DEFAULT_OPTS', 'FZF_DEFAULT_OPTS_FILE')})
            os.write(2, ('SELECTED:' + result.stdout.strip() + '\n').encode())
            os._exit(result.returncode)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        output = b''
        exited = False

        def pump(duration):
            nonlocal output
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .02)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        return
                    output += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(fd, b'\x1b[1;1R')

        try:
            deadline = time.monotonic() + 5
            while b'page-test>' not in output and time.monotonic() < deadline:
                pump(.1)
            self.assertIn(b'page-test>', output)
            pump(.15)
            for key in keys:
                os.write(fd, key)
                pump(.15)
            os.write(fd, b'\r')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                pump(.05)
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    exited = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, output[-1000:])
                    break
            self.assertTrue(exited, 'fzf failed to exit')
            import re
            match = re.search(rb'SELECTED:row(\d+)', output)
            self.assertIsNotNone(match, output[-1000:])
            return int(match.group(1))
        finally:
            if not exited:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            os.close(fd)

    def test_page_down_moves_a_page_with_hidden_preview(self):
        self.assertGreater(self.selection_after([b'\x1b[6~']), 1)

    def test_page_up_returns_to_first_page_with_hidden_preview(self):
        self.assertEqual(self.selection_after([b'\x1b[6~', b'\x1b[5~']), 0)


if __name__ == '__main__':
    unittest.main()
