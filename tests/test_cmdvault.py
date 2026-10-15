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
            "print('ctrl-s')\nprint(rows[0])\n"
        )
        result = self.run_cli()
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


if __name__ == "__main__":
    unittest.main()
