import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("cmdvault", str(ROOT / "cmdvault"))
spec = importlib.util.spec_from_loader(loader.name, loader)
vault = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = vault
loader.exec_module(vault)


class HistoryTests(unittest.TestCase):
    def test_plain_history_deduplication_and_order(self):
        entries = vault.entries_from("ls\nffmpeg -i 'a b'\nls\n", ["ssh host", "ls"])
        self.assertEqual([entry.command for entry in entries], ["ls", "ssh host", "ffmpeg -i 'a b'"])
        self.assertEqual([entry.count for entry in entries], [2, 0, 1])

    def test_timestamped_multiline_and_legacy_prefix(self):
        history = 'old command\n#1700000000\nprintf "%s\\n" \\\n  "hello"\n#1700000001\nls\n'
        self.assertEqual(list(vault.history_commands(history)),
                         ['old command', 'printf "%s\\n" \\\n  "hello"', 'ls'])

    def test_picker_display_escapes_control_characters(self):
        command = 'printf "hello"\n\t\x1b[2J'
        rendered = vault.display(command)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\n", rendered)
        self.assertEqual(json.loads('"' + rendered + '"'), command)


@unittest.skipUnless(os.name == "posix", "Linux integration tests require fcntl, executable scripts and Bash")
class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.history = self.base / "history"
        self.history.write_text("ls\nffmpeg -i video.mp4\nls\n", encoding="utf-8")
        self.favorite_file = self.base / "data/cmdvault/favorites.json"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "XDG_DATA_HOME": str(self.base / "data"),
                    "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}

    def run_cli(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(ROOT / "cmdvault"),
                               "--history", str(self.history), *args],
                              env=self.env, capture_output=True, text=True, **kwargs)

    def stub_picker(self, body):
        executable = self.bin / "fzf"
        executable.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        executable.chmod(0o755)

    def test_favorites_persist_and_are_private(self):
        command = "printf 'hello\nworld'"
        for _ in range(2):
            result = self.run_cli("--add", command)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.favorite_file.read_text()), [command])
        self.assertEqual(self.favorite_file.stat().st_mode & 0o777, 0o600)
        self.history.unlink()
        result = self.run_cli("--favorites", "--list")
        self.assertEqual(result.stdout, command + "\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.run_cli("--remove", command).returncode, 0)
        self.assertEqual(json.loads(self.favorite_file.read_text()), [])

    def test_invalid_favorites_are_not_overwritten(self):
        self.favorite_file.parent.mkdir(parents=True)
        self.favorite_file.write_text("broken", encoding="utf-8")
        self.assertNotEqual(self.run_cli("--add", "ls").returncode, 0)
        self.assertEqual(self.favorite_file.read_text(), "broken")

    def test_list_search(self):
        result = self.run_cli("--list", "FFMPEG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ffmpeg -i video.mp4\n")

    def test_picker_saves_exact_command_without_executing(self):
        target = self.base / "must-not-exist"
        command = f"echo $(touch '{target}')\nsecond line"
        self.history.write_text("#1700000000\n" + command + "\n", encoding="utf-8")
        self.env["FZF_DEFAULT_OPTS"] = "--bind=enter:execute(bad)"
        self.stub_picker(
            "import os, sys\n"
            "assert 'FZF_DEFAULT_OPTS' not in os.environ\n"
            "rows = sys.stdin.read().splitlines()\n"
            "print('')\nprint('' if '★' in rows[0] else 'ctrl-s')\nprint(rows[0])\n"
        )
        result = self.run_cli("--print")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, command + "\n")
        self.assertEqual(json.loads(self.favorite_file.read_text()), [command])
        self.assertFalse(target.exists())

    def test_cancel_has_no_output_or_side_effects(self):
        self.stub_picker("import sys\nsys.stdin.read()\nsys.exit(130)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.favorite_file.exists())

    def test_copy_passes_literal_stdin_to_user_function(self):
        functions = self.base / "functions"
        functions.write_text("copy() { printf '\\033]52;c;'; base64 -w0; printf '\\007'; }\n")
        terminal = self.base / "terminal"
        command = "printf '%s' \"$(touch never)\"\nsecond line"
        vault.copy_command(command, functions, terminal)
        import base64
        self.assertEqual(terminal.read_bytes(), b"\x1b]52;c;" + base64.b64encode(command.encode()) + b"\x07")

    def test_failed_copy_falls_back_to_original_command(self):
        with patch.dict(os.environ, self.env), \
                patch.object(vault, "pick", return_value=("ctrl-y", "ls")), \
                patch.object(vault, "copy_command", side_effect=ValueError("unavailable")), \
                patch("sys.stdout") as output:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                vault.main(["--history", str(self.history)])
            self.assertEqual(output.write.call_args_list[0].args, ("ls",))


class PickerRegressionTests(unittest.TestCase):
    def test_save_stays_open_colors_row_and_preserves_pipeline(self):
        command = "hcxwltool -i the_list.txt --straight | sort | uniq | hashcat -m 22000 hashfile.hc22000"
        entry = vault.Entry(command)
        responses = [subprocess.CompletedProcess([], 0, 'hashcat\nctrl-s\n0\trow\n', ''),
                     subprocess.CompletedProcess([], 130, '', '')]
        with patch.object(vault.shutil, 'which', return_value='fzf'), patch.object(vault.subprocess, 'run', side_effect=responses) as run, patch.object(vault, 'update_favorite') as save:
            self.assertIsNone(vault.pick([entry], '', False))
        save.assert_called_once_with(vault.favorites_path(), command)
        self.assertEqual(run.call_count, 2)
        self.assertIn('\x1b[33m', run.call_args.kwargs['input'])
        self.assertIn('--query=hashcat', run.call_args.args[0])
        self.assertTrue(entry.favorite)

    def test_save_failure_stays_open_without_marking_favorite(self):
        entry = vault.Entry('printf hello')
        responses = [subprocess.CompletedProcess([],0,'\nalt-f\n0\trow\n',''), subprocess.CompletedProcess([],130,'','')]
        with patch.object(vault.shutil,'which',return_value='fzf'), patch.object(vault.subprocess,'run',side_effect=responses) as run, patch.object(vault,'update_favorite',side_effect=OSError('disk full')):
            self.assertIsNone(vault.pick([entry],'',False))
        self.assertFalse(entry.favorite)
        self.assertTrue(any('Save failed' in arg for arg in run.call_args.args[0]))

    def test_enter_returns_original_not_ansi_display(self):
        command = 'printf "<hi>" | cat\nnext\tline'
        with patch.object(vault.shutil,'which',return_value='fzf'), patch.object(vault.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'\n\n0\tignored\n','')):
            self.assertEqual(vault.pick([vault.Entry(command,favorite=True)],'',True),('',command))

    def test_exact_pipeline_persistence_and_favorites_list(self):
        import contextlib
        import io
        from unittest.mock import MagicMock
        command = "hcxwltool -i the_list.txt --straight | sort | uniq | hashcat -m 22000 hashfile.hc22000"
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_DATA_HOME':folder}):
            # Windows can verify serialization; the Linux lock is tested by the POSIX suite.
            lock = contextlib.nullcontext() if os.name == 'posix' else patch.dict(sys.modules, {'fcntl':MagicMock()})
            with lock:
                vault.update_favorite(vault.favorites_path(),command)
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(vault.main(['--favorites','--list']),0)
            self.assertEqual(output.getvalue(),command+'\n')


class ExecutionTests(unittest.TestCase):
    def test_execution_preserves_exact_command_and_terminal(self):
        command = "printf '%s' 'a b' | cat > output.txt\nprintf done"
        with patch.object(vault.shutil, 'which', return_value='/usr/bin/bash'), patch.object(vault.os, 'execv') as execute:
            vault.execute_command(command)
        execute.assert_called_once_with('/usr/bin/bash', ['/usr/bin/bash', '--noprofile', '-ic', command])

    def test_missing_bash_is_reported(self):
        with patch.object(vault.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Bash is required'):
                vault.execute_command('echo test')

    def test_enter_executes_but_copy_print_and_cancel_do_not(self):
        import contextlib
        import io
        for args, selection, should_run, should_copy in [([], ('', 'echo hello'), True, False),
                (['--print'], ('', 'echo hello'), False, False),
                (['--copy'], ('', 'echo hello'), False, True),
                ([], ('ctrl-y', 'echo hello'), False, True), ([], None, False, False)]:
            with self.subTest(args=args, selection=selection), patch.object(vault, 'read_favorites', return_value=['echo hello']), patch.object(vault, 'pick', return_value=selection), patch.object(vault, 'execute_command') as execute, patch.object(vault, 'copy_command') as copy, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                vault.main(['--favorites', *args])
                self.assertEqual(execute.called, should_run)
                self.assertEqual(copy.called, should_copy)
                if should_run: execute.assert_called_once_with('echo hello')


class HistoryDeletionTests(unittest.TestCase):
    def test_exact_duplicates_only(self):
        text='keep\nsecret\nprefix secret\nsecret\nlast'
        self.assertEqual(vault.history_without(text,'secret'),('keep\nprefix secret\nlast',2))

    def test_multiline_timestamps_and_unrelated_bytes(self):
        text='legacy\r\n#1700000000\nfirst\nsecond\n#1700000001\nkeep\n#1700000002\nfirst\nsecond\n'
        cleaned,count=vault.history_without(text,'first\nsecond')
        self.assertEqual(count,2)
        self.assertEqual(cleaned,'legacy\r\n#1700000001\nkeep\n')

    def test_history_and_favorite_deleted_without_backup(self):
        import contextlib
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'XDG_DATA_HOME':folder}):
            path=Path(folder)/'history'
            path.write_text('keep\nsecret\nsecret\n',encoding='utf-8')
            lock=contextlib.nullcontext() if os.name=='posix' else patch.dict(sys.modules,{'fcntl':MagicMock()})
            with lock:
                vault.update_favorite(vault.favorites_path(),'secret')
                self.assertEqual(vault.delete_entry(path,'secret'),2)
            self.assertEqual(path.read_text(),'keep\n')
            self.assertEqual(vault.read_favorites(vault.favorites_path()),[])
            self.assertFalse(list(Path(folder).glob('.cmdvault-history-*')))
            self.assertFalse(list(Path(folder).glob('*.bak')))

    def test_failed_replace_preserves_original(self):
        import contextlib
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history';path.write_text('secret\n',encoding='utf-8')
            lock=contextlib.nullcontext() if os.name=='posix' else patch.dict(sys.modules,{'fcntl':MagicMock()})
            with lock,patch.object(vault.os,'replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):vault.delete_history_command(path,'secret')
            self.assertEqual(path.read_text(),'secret\n')
            self.assertFalse(list(Path(folder).glob('.cmdvault-history-*')))

    def test_ctrl_d_removes_row_clears_query_and_never_executes(self):
        entries=[vault.Entry('secret',favorite=True),vault.Entry('keep')]
        responses=[subprocess.CompletedProcess([],0,'secret\nctrl-d\n0\trow\n',''),subprocess.CompletedProcess([],130,'','')]
        with patch.object(vault.shutil,'which',return_value='fzf'),patch.object(vault.subprocess,'run',side_effect=responses) as run,patch.object(vault,'delete_entry',return_value=2) as delete,patch.object(vault,'execute_command') as execute:
            self.assertIsNone(vault.pick(entries,'secret',False,history_path=Path('/test/history')))
        delete.assert_called_once_with(Path('/test/history'),'secret')
        self.assertEqual([entry.command for entry in entries],['keep'])
        self.assertNotIn('secret',run.call_args.kwargs['input'])
        self.assertIn('--query=',run.call_args.args[0])
        execute.assert_not_called()



@unittest.skipUnless(os.name == 'posix', 'Requires Linux Bash and flock')
class BashIntegrationTests(unittest.TestCase):
    def test_sync_preserves_prompt_hooks_and_reloads_deletions(self):
        with tempfile.TemporaryDirectory() as folder:
            history=Path(folder)/'history'
            script = r'''HISTFILE=$1
PROMPT_COMMAND=('printf existing >/dev/null')
source "$2"
source "$2"
[[ ${#PROMPT_COMMAND[@]} == 2 ]] || exit 11
[[ ${PROMPT_COMMAND[0]} == _cmdvault_sync_history ]] || exit 12
[[ ${PROMPT_COMMAND[1]} == 'printf existing >/dev/null' ]] || exit 13
history -c
history -s 'secret-for-test'
_cmdvault_sync_history
grep -q 'secret-for-test' "$HISTFILE" || exit 14
printf 'keep\n' > "$HISTFILE"
_cmdvault_sync_history
history | grep -q 'secret-for-test' && exit 15
history | grep -q keep || exit 16
exit 0
'''
            result=subprocess.run(['bash','--noprofile','--norc','-ic',script,'test',str(history),str(ROOT/'cmdvault.bash')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    unittest.main()
