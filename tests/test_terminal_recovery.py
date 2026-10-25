"""The shell must accept a complete line after normal and interrupted exits."""
import os
import select
import shutil
import signal
import struct
import time
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('fzf'), 'requires POSIX and fzf')
class TerminalRecoveryTests(unittest.TestCase):
    def test_exit_restores_keyboard_and_shell_input(self):
        for sixel in (False, True):
            for action in ('escape', 'ctrl-c', 'sigint', 'sigterm', 'sigquit', 'render-error'):
                if action == 'render-error' and not sixel:
                    continue
                with self.subTest(sixel=sixel, action=action):
                    self.exercise(sixel, action)

    def test_recovery_does_not_wait_for_full_output_queue(self):
        import fcntl
        import termios
        import tty
        from src.visuals.terminal import restore_terminal
        master, slave = os.openpty()
        try:
            original = termios.tcgetattr(slave)
            flags = fcntl.fcntl(slave, fcntl.F_GETFL)
            tty.setraw(slave)
            fcntl.fcntl(slave, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            try:
                while True:
                    os.write(slave, b'x' * 4096)
            except BlockingIOError:
                pass
            fcntl.fcntl(slave, fcntl.F_SETFL, flags)
            started = time.monotonic()
            restore_terminal(slave, original)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(termios.tcgetattr(slave), original)
            self.assertEqual(fcntl.fcntl(slave, fcntl.F_GETFL), flags)
        finally:
            os.close(master)
            os.close(slave)

    def exercise(self, sixel, action):
        import fcntl
        import pty
        import termios
        from unittest.mock import patch
        pid, fd = pty.fork()
        if pid == 0:
            try:
                from src.visuals.config import VisualConfig
                from src.visuals.picker import run_picker
                def fail_render(*args, **kwargs):
                    # Simulate an exception after a partially emitted SIXEL header.
                    os.write(2, b'\x1bP0;1;0q')
                    raise RuntimeError('interrupted image')
                try:
                    with patch('src.visuals.sixel.sixel_frame_packet',
                               side_effect=fail_render) if action == 'render-error' else patch.dict(os.environ, {}):
                        run_picker([shutil.which('fzf'), '--sync', '--layout=reverse', '--prompt=picker-ready'],
                                   input='row\n', env=dict(os.environ, TERM='xterm-256color'),
                                   config=VisualConfig(animation=False), graphics=False, sixel=sixel)
                except (KeyboardInterrupt, RuntimeError):
                    pass
                os.write(2, b'SHELL-READY\n')
                with open('/dev/tty') as source:
                    line = source.readline().rstrip('\n')
                os.write(2, ('SHELL-READ:' + line + '\n').encode())
                os._exit(0)
            except BaseException as exc:
                os.write(2, ('CHILD-ERROR: ' + repr(exc)).encode())
                os._exit(3)
        original = termios.tcgetattr(fd)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 800, 480))
        data = b''
        sent = action == 'render-error'
        line_sent = False
        exited = False
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .03)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    data += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(fd, b'\x1b[1;1R')
                    if b'\x1b[16t' in chunk:
                        os.write(fd, b'\x1b[6;20;10t')
                if not sent and b'picker-ready' in data and b'STATUS: READY' in data:
                    if action in ('escape', 'ctrl-c'):
                        os.write(fd, b'\x1b' if action == 'escape' else b'\x03')
                    else:
                        os.kill(pid, {'sigint': signal.SIGINT, 'sigterm': signal.SIGTERM,
                                      'sigquit': signal.SIGQUIT}[action])
                    sent = True
                if not line_sent and b'SHELL-READY' in data:
                    self.assertEqual(termios.tcgetattr(fd), original)
                    self.assertIn(b'\x18\x1b\\\x1b[?2026l', data)
                    os.write(fd, b'keyboard-works\n')
                    line_sent = True
                if b'SHELL-READ:keyboard-works' in data:
                    break
            self.assertTrue(sent, data[-400:])
            self.assertTrue(line_sent, data[-400:])
            self.assertIn(b'SHELL-READ:keyboard-works', data)
            end = time.monotonic() + 2
            while time.monotonic() < end:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    exited = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    break
                time.sleep(.02)
            self.assertTrue(exited)
        finally:
            if not exited:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(fd)
