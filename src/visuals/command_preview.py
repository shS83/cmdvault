"""Show the actual command for a selected fzf row without executing it."""
import json
import sys
from pathlib import Path


def preview(commands_file: str, selected_row: str) -> str:
    # fzf --with-nth=2.. hides the stable numeric ID from the display,
    # but {} reliably passes the original untransformed selected line.
    index_text, separator, _ = selected_row.partition("\t")
    if not separator or not index_text.isascii() or not index_text.isdecimal():
        raise ValueError("Selected row has no valid index")
    commands = json.loads(Path(commands_file).read_text(encoding="utf-8"))
    index = int(index_text)
    if not isinstance(commands, list) or not 0 <= index < len(commands):
        raise ValueError("Selected row index out of range")
    command = commands[index]
    if not isinstance(command, str):
        raise ValueError("Command is not text")
    return command


def main() -> int:
    if len(sys.argv) != 3:
        print("Preview unavailable: invalid arguments", file=sys.stderr)
        return 2
    try:
        # Reserve two terminal rows above the preview; Kitty frame overlaps otherwise.
        print("\n\n" + preview(sys.argv[1], sys.argv[2]))
    except (OSError, ValueError, TypeError) as exc:
        print(f"Preview unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
