"""Exercise real fzf without needing a GUI or claiming GPU compatibility."""
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('fzf') and importlib.util.find_spec('PIL'),
                     'real fzf integration requires POSIX, fzf and optional Pillow')
class NeonPickerTests(unittest.TestCase):
    def test_running_sync_error_opens_modal(self):
        self.exercise_sync_error(graphics=True)

    def test_ansi_sync_status_and_error_update_without_keypress(self):
        self.exercise_sync_error(graphics=False)

    def test_sixel_sync_status_and_error_update_without_keypress(self):
        self.exercise_sync_error(graphics=False, sixel=True)

    def test_sixel_navigation_does_not_repaint_frame(self):
        import fcntl
        import pty
        import termios
        pid, fd = pty.fork()
        if pid == 0:
            from src.visuals.config import VisualConfig
            from src.visuals.picker import run_picker
            result = run_picker([shutil.which('fzf'), '--layout=reverse',
                                 '--height=100%', '--margin=1,2,0,2', '--sync', '--bind=start:first'],
                                input='row0\nrow1\nrow2\nrow3\n',
                                env=dict(os.environ, TERM='xterm-256color'),
                                config=VisualConfig(animation=False), graphics=False, sixel=True)
            os.write(2, ('SELECTED: ' + repr(result.stdout)).encode())
            os._exit(0 if result.stdout.strip() == 'row2' else 2)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 800, 480))
        data = b''
        def pump(duration):
            nonlocal data
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .02)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        return
                    data += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(fd, b'\x1b[1;1R')
                    if b'\x1b[16t' in chunk:
                        os.write(fd, b'\x1b[6;20;10t')
        exited = False
        try:
            deadline = time.monotonic() + 8
            while b'STATUS: READY' not in data and time.monotonic() < deadline:
                pump(.1)
            self.assertIn(b'STATUS: READY', data)
            pump(.2)
            start = len(data)
            for key in (b'\x1b[B', b'\x1b[A', b'\x1b[B', b'\x1b[B'):
                os.write(fd, key)
                pump(.2)
            # No full-width frame image may be sent just for selection movement.
            self.assertNotIn(b'"1;1;800;', data[start:])
            os.write(fd, b'\r')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                pump(.1)
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    exited = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, data[-1000:])
                    break
            self.assertTrue(exited)
        finally:
            if not exited:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            os.close(fd)

    def exercise_sync_error(self, graphics, sixel=False):
        import fcntl
        import pty
        import termios
        with tempfile.TemporaryDirectory() as folder:
            status_file = Path(folder) / 'status.json'
            pid, fd = pty.fork()
            if pid == 0:
                from src.visuals.config import VisualConfig
                from src.visuals.picker import run_picker
                env = dict(os.environ, TERM='xterm-kitty',
                           CMDVAULT_SYNC_STATUS_FILE=str(status_file))
                run_picker([shutil.which('fzf'), '--ansi', '--layout=reverse',
                            '--prompt=vault › '], input='0\trow\n', env=env,
                           config=VisualConfig(animation=False), graphics=graphics, sixel=sixel)
                os._exit(0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 800, 480))
            data = b''
            wrote_error = False
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([fd], [], [], .05)
                    if readable:
                        try:
                            chunk = os.read(fd, 65536)
                        except OSError:
                            break
                        data += chunk
                        if b'\x1b[6n' in chunk:
                            os.write(fd, b'\x1b[1;1R')
                        if sixel and b'\x1b[16t' in chunk:
                            os.write(fd, b'\x1b[6;24;12t')
                    if (not wrote_error and b'vault' in data
                            and (not sixel or b'"1;1;960;24' in data)):
                        status_file.write_text(json.dumps({
                            'state': 'error', 'message': 'Cloud Sync: error: test detail'}))
                        wrote_error = True
                    if (b'Cloud Sync: error: test detail' in data
                            and (graphics or b'STATUS: ERROR' in data)):
                        break
                self.assertTrue(wrote_error)
                self.assertIn(b'Cloud Sync: error: test detail', data)
                self.assertIn(b'ERROR', data)
                if not graphics:
                    self.assertIn(b'STATUS: READY', data)
                    self.assertIn(b'STATUS: ERROR', data)
                    self.assertNotIn(b'\x1b_G', data)
                    if sixel:
                        self.assertIn(b'\x1bP0;1;0q', data)
                        self.assertIn(b'"1;1;960;24', data)
                os.write(fd, b'\x1b')
                exited = False
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    finished, status = os.waitpid(pid, os.WNOHANG)
                    if finished:
                        exited = True
                        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                        break
                    time.sleep(.05)
                self.assertTrue(exited, 'ESC on error dialog did not exit picker')
            finally:
                if not locals().get('exited', False):
                    os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)
                os.close(fd)

    def exercise(self, keys, expected_status, expected_text, animation=False):
        import fcntl
        import pty
        import termios
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder)/'history'
            history.write_text('echo neon-first\necho neon-selected\n')
            pid,fd = pty.fork()
            if pid == 0:
                os.chdir(ROOT)
                os.environ.update(TERM='xterm-kitty',KITTY_WINDOW_ID='test',
                                  XDG_CONFIG_HOME=folder,XDG_DATA_HOME=folder)
                os.environ.pop('TMUX',None)
                os.environ.pop('STY',None)
                os.execv(sys.executable,[sys.executable,'cmdvault','--history',str(history),
                                        '--print','--graphics','kitty',*(['--fps','30'] if animation else ['--no-animation'])])
            original = termios.tcgetattr(fd)
            fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,800,480))
            data = b''
            deadline = time.monotonic()+15
            sent = False
            resized = False
            status = None
            try:
                while time.monotonic() < deadline:
                    readable,_,_ = select.select([fd],[],[],.05)
                    if readable:
                        try:
                            chunk = os.read(fd,65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        data += chunk
                        # Act as a terminal for fzf's cursor position query.
                        if b'\x1b[6n' in chunk:
                            os.write(fd,b'\x1b[1;1R')
                    if not resized and b'a=p,' in data and b'vault' in data:
                        fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack('HHHH',28,90,900,560))
                        os.kill(pid,signal.SIGWINCH)
                        resized = True
                    if resized and not sent and b'c=90,r=28' in data:
                        os.write(fd,keys)
                        sent = True
                child,status = os.waitpid(pid,os.WNOHANG)
                for _ in range(100):
                    if child:
                        break
                    time.sleep(.01)
                    child,status = os.waitpid(pid,os.WNOHANG)
                self.assertEqual(child,pid,'picker did not exit before timeout')
                self.assertEqual(os.waitstatus_to_exitcode(status),expected_status)
                self.assertIn(b'a=p,',data)
                self.assertIn(b'd=I,i=',data)
                self.assertIn(b'\x1b[?1049l',data[-2000:])
                self.assertIn(expected_text,data[-2000:])
                self.assertEqual(termios.tcgetattr(fd),original)
            finally:
                if status is None or child == 0:
                    os.kill(pid,signal.SIGKILL)
                    os.waitpid(pid,0)
                os.close(fd)

    def test_search_resize_and_enter(self):
        self.exercise(b'neon-selected\r',0,b'echo neon-selected')

    def test_escape_cleans_up(self):
        self.exercise(b'\x1b',1,b'\x1b[?25h')

    def test_keyboard_and_exit_during_animation_loading(self):
        self.exercise(b'neon-selected\r',0,b'echo neon-selected',animation=True)
